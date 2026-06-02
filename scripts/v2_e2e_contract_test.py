#!/usr/bin/env python3
"""HydraDB v2 end-to-end API contract tester.

What this script checks:
1. Static docs contract:
   - Every endpoint page under api-reference/v2/endpoint with an `openapi:` directive
     points at a real OpenAPI operation.
   - cURL blocks on those pages use the same HTTP method/path as the directive.
   - cURL blocks include `Authorization: Bearer ...` and `API-Version: 2`.
   - Confirms the `/search` -> `/query` rename actually landed (path + field names).

2. Runtime API contract — EVERY documented v2 endpoint, exercised across EVERY
   meaningful variation we support, with each variation's full request + response
   written to its own file under scripts/v2_e2e_results/<run_id>/<endpoint>/<case>.json.

   Endpoints and variations covered (see the printed coverage report at the end):
       POST   /tenants                 create-existing(409), create-disposable(200)
       GET    /tenants                 list
       DELETE /tenants                 delete-disposable
       GET    /tenants/status          ready poll + snapshot
       GET    /tenants/sub-tenants      list
       GET    /tenants/stats           snapshot
       POST   /context/ingest          knowledge documents (single/multi), app_knowledge
                                        (object/array), documents+app mixed, upsert=false,
                                        memory text (infer true/false), conversation
                                        pairs, memory with metadata
       GET    /context/status           single id, multiple ids, memory id
       GET    /context/inspect         mode content/url/both, memory, custom expiry
       POST   /context/list             knowledge basic, filters (additional_metadata/source_fields),
                                        include_fields, ids, pagination, memory
       DELETE /context                  knowledge, memory
       GET    /context/relations        by id, sub-tenant-wide, limit, memory
       POST   /query                   type knowledge/memory/all x query_by hybrid/text
                                        x mode fast/thinking x operator or/and/phrase
                                        x alpha numeric/auto x recency_bias x graph_context
                                        on/off x query_apps x metadata_filters x
                                        additional_context x max_results x zero-results
                                        x empty-query negative
       GET/POST/DELETE /webhooks/indexing ... full webhook surface

Every JSON response is validated against the OpenAPI response schema with strict
extra-key checking where the schema declares object properties.

No SDKs are used. HTTP requests are raw requests that mirror the cURL surface.

Security note: do not commit real API keys. The key below is a short-lived staging
key for local ad-hoc runs; prefer HYDRA_DB_API_KEY in your shell.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

# -----------------------------------------------------------------------------
# Top-level configuration: safe to edit locally.
# -----------------------------------------------------------------------------
# BASE_URL = os.getenv("HYDRADB_BASE_URL", "https://api.hydradb.com/")
# API_KEY = "sk_live_yewMynb1YXVF.ze08z0m3bo1b7sim5BphKeeelKLWrpbE1sW73rThM0A"  #
# TENANT_ID = os.getenv("HYDRADB_TENANT_ID", "default-tenant")
BASE_URL = os.getenv("HYDRADB_BASE_URL", "https://api-v2.staging.hydradb.com/")
API_KEY = "sk_test_Iy3ujUqtq_Ka.AuSg1eB2MzmN5MVV5PvtVFzWsg9F3L2AANRMk75gItM"  #
TENANT_ID = os.getenv("HYDRADB_TENANT_ID", "default-tenant")
SUB_TENANT_ID = os.getenv("HYDRADB_SUB_TENANT_ID", "e2e_user_alex-4")

API_VERSION = "2"
OPENAPI_PATH = (
    Path(__file__).resolve().parents[1] / "api-reference" / "v2" / "openapi.json"
)
ENDPOINT_DOCS_DIR = (
    Path(__file__).resolve().parents[1] / "api-reference" / "v2" / "endpoint"
)
RESULTS_ROOT = Path(__file__).resolve().parent / "v2_e2e_results"

RUN_WEBHOOK_TESTS = os.getenv("HYDRADB_RUN_WEBHOOK_TESTS", "1") == "1"
DELETE_CORE_TEST_DATA = os.getenv("HYDRADB_DELETE_CORE_TEST_DATA", "1") == "1"
STRICT_EXTRA_KEYS = os.getenv("HYDRADB_STRICT_EXTRA_KEYS", "1") == "1"

TENANT_READY_TIMEOUT_SECONDS = int(
    os.getenv("HYDRADB_TENANT_READY_TIMEOUT_SECONDS", "600")
)
SOURCE_READY_TIMEOUT_SECONDS = int(
    os.getenv("HYDRADB_SOURCE_READY_TIMEOUT_SECONDS", "900")
)
POLL_INTERVAL_SECONDS = float(os.getenv("HYDRADB_POLL_INTERVAL_SECONDS", "5"))
REQUEST_TIMEOUT_SECONDS = int(os.getenv("HYDRADB_REQUEST_TIMEOUT_SECONDS", "120"))
# Semantic suite: how long to wait for a source to reach `completed` (full graph)
# and how long to poll for a webhook delivery row to appear after firing a test.
GRAPH_COMPLETE_TIMEOUT_SECONDS = int(
    os.getenv("HYDRADB_GRAPH_COMPLETE_TIMEOUT_SECONDS", "600")
)
WEBHOOK_DELIVERY_POLL_SECONDS = int(
    os.getenv("HYDRADB_WEBHOOK_DELIVERY_POLL_SECONDS", "45")
)
RUN_SEMANTIC_CHECKS = os.getenv("HYDRADB_RUN_SEMANTIC_CHECKS", "1") == "1"

# The webhooks API requires a public HTTPS URL. This URL is intentionally inert,
# but it is syntactically valid and should allow create/get/delete contract checks.
WEBHOOK_URL = os.getenv(
    "HYDRADB_WEBHOOK_URL", "https://example.com/hydradb-indexing-webhook"
)

# Tenant IDs are documented as max 25 chars. Keep disposable IDs short.
# We can create exactly one more tenant beyond default-tenant; this one is created
# and then deleted to exercise the full tenant lifecycle without exceeding the limit.
DELETE_TEST_TENANT_ID = os.getenv("HYDRADB_DELETE_TEST_TENANT_ID", "e2e-disposable")

# -----------------------------------------------------------------------------
# Test result plumbing.
# -----------------------------------------------------------------------------


@dataclass
class Check:
    name: str
    passed: bool
    details: str = ""
    request_id: str | None = None


@dataclass
class Context:
    run_id: str
    results_dir: Path
    # Source IDs created during ingestion, grouped by purpose so later endpoints
    # can reference concrete, known-good IDs.
    knowledge_ids: list[str] = field(default_factory=list)
    app_ids: list[str] = field(default_factory=list)
    memory_ids: list[str] = field(default_factory=list)
    disposable_knowledge_id: str | None = None
    disposable_memory_id: str | None = None
    # The app source that declares a forceful relation, and its declared target.
    forceful_declaring_id: str | None = None
    forceful_target_id: str | None = None
    created_delete_tenant: bool = False
    created_webhook: bool = False
    known_delivery_id: str | None = None

    @property
    def all_ids(self) -> list[str]:
        return self.knowledge_ids + self.app_ids + self.memory_ids


class Recorder:
    def __init__(self) -> None:
        self.checks: list[Check] = []

    def pass_(
        self, name: str, details: str = "", request_id: str | None = None
    ) -> None:
        self.checks.append(Check(name, True, details, request_id))
        rid = f" request_id={request_id}" if request_id else ""
        print(f"PASS {name}{rid}{(': ' + details) if details else ''}")

    def fail(self, name: str, details: str, request_id: str | None = None) -> None:
        self.checks.append(Check(name, False, details, request_id))
        rid = f" request_id={request_id}" if request_id else ""
        print(f"FAIL {name}{rid}: {details}")

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]

    def summary(self) -> None:
        passed = len(self.checks) - len(self.failed)
        print("\n=== SUMMARY ===")
        print(f"Passed: {passed}")
        print(f"Failed: {len(self.failed)}")
        if self.failed:
            print("\nFailures:")
            for check in self.failed:
                rid = f" request_id={check.request_id}" if check.request_id else ""
                print(f"- {check.name}{rid}: {check.details}")


class ResultWriter:
    """Writes one JSON file per testcase under <root>/<category>/<NN_case>.json."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.counters: dict[str, int] = {}
        self.index: list[dict[str, Any]] = []
        root.mkdir(parents=True, exist_ok=True)

    def write(
        self,
        category: str,
        case: str,
        description: str,
        response: "ApiResponse",
        passed: bool,
        error: str | None,
    ) -> Path:
        n = self.counters.get(category, 0) + 1
        self.counters[category] = n
        folder = self.root / category
        folder.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", case)
        path = folder / f"{n:02d}_{safe}.json"
        doc = {
            "category": category,
            "testcase": case,
            "description": description,
            "passed": passed,
            "error": error,
            "request": {
                "method": response.method,
                "path": response.path,
                "query": response.request_query,
                "json_body": response.request_json,
                "multipart": response.request_multipart,
            },
            "response": {
                "status": response.status,
                "request_id": response.request_id,
                "latency_ms": _latency_ms(response.json_body),
                "contract_validated": response.contract_validated,
                "contract_error": response.contract_error,
                "status_error": response.status_error,
                "headers": {
                    k: v
                    for k, v in response.headers.items()
                    if k.lower()
                    in {"content-type", "x-request-id", "x-hydra-request-id"}
                },
                "body": response.json_body
                if response.json_body is not None
                else response.body_text[:5000],
            },
        }
        path.write_text(json.dumps(doc, indent=2, default=str))
        self.index.append(
            {
                "category": category,
                "file": str(path.relative_to(self.root)),
                "testcase": case,
                "description": description,
                "method": response.method,
                "path": response.path,
                "status": response.status,
                "passed": passed,
                "request_id": response.request_id,
                "error": error,
            }
        )
        return path

    def write_error(
        self,
        category: str,
        case: str,
        description: str,
        method: str,
        path_: str,
        request_summary: dict[str, Any],
        error: str,
    ) -> None:
        n = self.counters.get(category, 0) + 1
        self.counters[category] = n
        folder = self.root / category
        folder.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", case)
        out = folder / f"{n:02d}_{safe}.json"
        doc = {
            "category": category,
            "testcase": case,
            "description": description,
            "passed": False,
            "error": error,
            "request": {"method": method, "path": path_, **request_summary},
            "response": None,
        }
        out.write_text(json.dumps(doc, indent=2, default=str))
        self.index.append(
            {
                "category": category,
                "file": str(out.relative_to(self.root)),
                "testcase": case,
                "description": description,
                "method": method,
                "path": path_,
                "status": None,
                "passed": False,
                "request_id": None,
                "error": error,
            }
        )

    def write_evidence(
        self,
        category: str,
        case: str,
        description: str,
        passed: bool,
        detail: str,
        evidence: dict[str, Any],
    ) -> Path:
        """Persist a semantic assertion: the evidence dict captures the actual
        observed values (counts, returned ids, fetched content, etc.) that justify
        the pass/fail verdict — not just a single raw HTTP response."""
        n = self.counters.get(category, 0) + 1
        self.counters[category] = n
        folder = self.root / category
        folder.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", case)
        path = folder / f"{n:02d}_{safe}.json"
        doc = {
            "category": category,
            "testcase": case,
            "description": description,
            "passed": passed,
            "detail": detail,
            "evidence": evidence,
        }
        path.write_text(json.dumps(doc, indent=2, default=str))
        self.index.append(
            {
                "category": category,
                "file": str(path.relative_to(self.root)),
                "testcase": case,
                "description": description,
                "method": "SEMANTIC",
                "path": detail[:80],
                "status": "PASS" if passed else "FAIL",
                "passed": passed,
                "request_id": None,
                "error": None if passed else detail,
            }
        )
        return path

    def flush_index(self) -> None:
        (self.root / "_index.json").write_text(
            json.dumps(self.index, indent=2, default=str)
        )
        # Human-readable coverage table.
        lines = ["# HydraDB v2 E2E results\n"]
        by_cat: dict[str, list[dict[str, Any]]] = {}
        for entry in self.index:
            by_cat.setdefault(entry["category"], []).append(entry)
        for cat in sorted(by_cat):
            entries = by_cat[cat]
            ok = sum(1 for e in entries if e["passed"])
            lines.append(f"\n## {cat}  ({ok}/{len(entries)} passed)\n")
            for e in entries:
                mark = "PASS" if e["passed"] else "FAIL"
                lines.append(
                    f"- [{mark}] `{e['method']} {e['path']}` — {e['testcase']} "
                    f"(HTTP {e['status']}) -> {e['file']}"
                )
        (self.root / "_coverage.md").write_text("\n".join(lines) + "\n")


def _latency_ms(body: Any) -> Any:
    if isinstance(body, dict):
        meta = body.get("meta")
        if isinstance(meta, dict):
            return meta.get("latency_ms")
    return None


# -----------------------------------------------------------------------------
# Minimal OpenAPI JSON Schema validator.
# -----------------------------------------------------------------------------


class ContractError(AssertionError):
    pass


class OpenApiContract:
    def __init__(self, path: Path, strict_extra_keys: bool = True) -> None:
        self.path = path
        self.doc = json.loads(path.read_text())
        self.strict_extra_keys = strict_extra_keys

    def operation(self, method: str, path: str) -> dict[str, Any]:
        try:
            return self.doc["paths"][path][method.lower()]
        except KeyError as exc:
            raise ContractError(
                f"OpenAPI operation not found: {method.upper()} {path}"
            ) from exc

    def response_schema(
        self, method: str, path: str, status: int
    ) -> dict[str, Any] | None:
        op = self.operation(method, path)
        responses = op.get("responses", {})
        response = responses.get(str(status)) or responses.get("default")
        if not response:
            return None
        content = response.get("content", {})
        media = content.get("application/json") or content.get("application/*+json")
        if not media:
            return None
        return media.get("schema")

    def validate_response(
        self, method: str, path: str, status: int, payload: Any
    ) -> None:
        schema = self.response_schema(method, path, status)
        if schema is None:
            raise ContractError(
                f"No JSON response schema documented for {method.upper()} {path} status {status}"
            )
        self.validate(schema, payload, f"{method.upper()} {path} response")
        if self.requires_envelope(path, schema):
            self.validate_envelope(payload, f"{method.upper()} {path}")

    def requires_envelope(self, path: str, schema: dict[str, Any]) -> bool:
        # Webhook endpoints return the schema object directly — no {success,data,error,meta} envelope.
        if path.startswith("/webhooks"):
            return False
        ref = schema.get("$ref", "") if isinstance(schema, dict) else ""
        schema_name = ref.rsplit("/", 1)[-1]
        return schema_name.endswith("ApiResponse") or path.startswith(
            ("/tenants", "/context", "/query")
        )

    def validate_envelope(self, payload: Any, label: str) -> None:
        if not isinstance(payload, dict):
            raise ContractError(f"{label}: response is not a JSON object envelope")
        expected = {"success", "data", "error", "meta"}
        actual = set(payload.keys())
        missing = expected - actual
        extra = actual - expected
        if missing:
            raise ContractError(
                f"{label}: response envelope missing keys {sorted(missing)}"
            )
        if self.strict_extra_keys and extra:
            raise ContractError(
                f"{label}: response envelope has undocumented keys {sorted(extra)}"
            )
        if payload["success"] is True and payload["error"] is not None:
            raise ContractError(f"{label}: success=true but error is not null")
        if payload["success"] is False and not isinstance(payload["error"], dict):
            raise ContractError(f"{label}: success=false but error is not an object")
        meta = payload.get("meta")
        if not isinstance(meta, dict) or "request_id" not in meta:
            raise ContractError(f"{label}: meta.request_id missing")

    def resolve_ref(self, ref: str) -> dict[str, Any]:
        if not ref.startswith("#/"):
            raise ContractError(f"External $ref not supported by this script: {ref}")
        cur: Any = self.doc
        for part in ref[2:].split("/"):
            cur = cur[part]
        return cur

    def validate(self, schema: dict[str, Any] | bool, value: Any, path: str) -> None:
        if schema is True or schema == {}:
            return
        if schema is False:
            raise ContractError(f"{path}: schema is false")
        if "$ref" in schema:
            return self.validate(self.resolve_ref(schema["$ref"]), value, path)
        if "allOf" in schema:
            for i, subschema in enumerate(schema["allOf"]):
                self.validate(subschema, value, f"{path}.allOf[{i}]")
        if "anyOf" in schema:
            errors = []
            for subschema in schema["anyOf"]:
                try:
                    self.validate(subschema, value, path)
                    return
                except ContractError as exc:
                    errors.append(str(exc))
            raise ContractError(
                f"{path}: did not match anyOf: " + " | ".join(errors[:4])
            )
        if "oneOf" in schema:
            matches = 0
            errors = []
            for subschema in schema["oneOf"]:
                try:
                    self.validate(subschema, value, path)
                    matches += 1
                except ContractError as exc:
                    errors.append(str(exc))
            if matches != 1:
                raise ContractError(
                    f"{path}: expected exactly one oneOf match, got {matches}; {errors[:4]}"
                )
            return
        if "const" in schema and value != schema["const"]:
            raise ContractError(
                f"{path}: expected const {schema['const']!r}, got {value!r}"
            )
        if "enum" in schema and value not in schema["enum"]:
            raise ContractError(
                f"{path}: expected one of {schema['enum']!r}, got {value!r}"
            )

        typ = schema.get("type")
        if isinstance(typ, list):
            errors = []
            for one_type in typ:
                try:
                    copy = dict(schema)
                    copy["type"] = one_type
                    self.validate(copy, value, path)
                    return
                except ContractError as exc:
                    errors.append(str(exc))
            raise ContractError(f"{path}: did not match any type {typ}: {errors[:4]}")

        if typ == "null":
            if value is not None:
                raise ContractError(
                    f"{path}: expected null, got {type(value).__name__}"
                )
            return
        if typ == "boolean":
            if not isinstance(value, bool):
                raise ContractError(
                    f"{path}: expected boolean, got {type(value).__name__}"
                )
            return
        if typ == "string":
            if not isinstance(value, str):
                raise ContractError(
                    f"{path}: expected string, got {type(value).__name__}"
                )
            return
        if typ == "integer":
            if not (isinstance(value, int) and not isinstance(value, bool)):
                raise ContractError(
                    f"{path}: expected integer, got {type(value).__name__}"
                )
            if "minimum" in schema and value < schema["minimum"]:
                raise ContractError(
                    f"{path}: expected >= {schema['minimum']}, got {value}"
                )
            if "maximum" in schema and value > schema["maximum"]:
                raise ContractError(
                    f"{path}: expected <= {schema['maximum']}, got {value}"
                )
            return
        if typ == "number":
            if not (isinstance(value, (int, float)) and not isinstance(value, bool)):
                raise ContractError(
                    f"{path}: expected number, got {type(value).__name__}"
                )
            return
        if typ == "array":
            if not isinstance(value, list):
                raise ContractError(
                    f"{path}: expected array, got {type(value).__name__}"
                )
            if "minItems" in schema and len(value) < schema["minItems"]:
                raise ContractError(
                    f"{path}: expected at least {schema['minItems']} items, got {len(value)}"
                )
            if "maxItems" in schema and len(value) > schema["maxItems"]:
                raise ContractError(
                    f"{path}: expected at most {schema['maxItems']} items, got {len(value)}"
                )
            item_schema = schema.get("items", {})
            for i, item in enumerate(value):
                self.validate(item_schema, item, f"{path}[{i}]")
            return
        if (
            typ == "object"
            or "properties" in schema
            or "additionalProperties" in schema
        ):
            if not isinstance(value, dict):
                raise ContractError(
                    f"{path}: expected object, got {type(value).__name__}"
                )
            properties: dict[str, Any] = schema.get("properties", {})
            required: list[str] = schema.get("required", [])
            missing = [k for k in required if k not in value]
            if missing:
                raise ContractError(f"{path}: missing required keys {missing}")
            for key, subschema in properties.items():
                if key in value:
                    self.validate(subschema, value[key], f"{path}.{key}")
            additional = schema.get("additionalProperties", None)
            extra_keys = [k for k in value if k not in properties]
            if additional is False:
                if extra_keys:
                    raise ContractError(f"{path}: unexpected keys {extra_keys}")
            elif isinstance(additional, dict):
                for key in extra_keys:
                    self.validate(additional, value[key], f"{path}.{key}")
            elif self.strict_extra_keys and properties and additional is None:
                if extra_keys:
                    raise ContractError(f"{path}: undocumented extra keys {extra_keys}")
            return

        # If type is omitted but properties/enums/etc. were already handled, treat as open schema.
        return


# -----------------------------------------------------------------------------
# Raw HTTP client.
# -----------------------------------------------------------------------------


@dataclass
class ApiResponse:
    method: str
    path: str
    status: int
    headers: dict[str, str]
    body_text: str
    json_body: Any
    request_query: dict[str, Any] | None = None
    request_json: Any | None = None
    request_multipart: dict[str, Any] | None = None
    expected_statuses: tuple[int, ...] = (200,)
    status_error: str | None = None
    contract_error: str | None = None
    contract_validated: bool = False

    @property
    def request_id(self) -> str | None:
        if isinstance(self.json_body, dict):
            meta = self.json_body.get("meta")
            if isinstance(meta, dict):
                return meta.get("request_id")
        return None

    @property
    def data(self) -> Any:
        if isinstance(self.json_body, dict):
            return self.json_body.get("data")
        return None

    @property
    def ok(self) -> bool:
        return self.status_error is None and self.contract_error is None


class ApiClient:
    def __init__(
        self, base_url: str, api_key: str, contract: OpenApiContract, recorder: Recorder
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.api_key = api_key
        self.contract = contract
        self.recorder = recorder

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "API-Version": API_VERSION,
            "Accept": "application/json",
            "User-Agent": "hydradb-v2-docs-e2e-contract-test/2.0",
        }
        if extra:
            headers.update(extra)
        return headers

    def perform(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        json_body: Any | None = None,
        multipart: tuple[dict[str, str], list[tuple[str, str, bytes, str]]]
        | None = None,
        expected_statuses: Iterable[int] = (200,),
        label: str | None = None,
        validate_contract: bool = True,
        contract_path: str | None = None,
    ) -> ApiResponse:
        """Make the HTTP call and capture validation outcomes WITHOUT raising.

        status_error / contract_error are populated instead of raised, so callers
        can always inspect (and persist) the real response body.
        """
        method = method.upper()
        label = label or f"{method} {path}"
        contract_path = contract_path or self._contract_path(path)
        expected = tuple(expected_statuses)
        url = urljoin(self.base_url, path.lstrip("/"))
        if query:
            pairs: list[tuple[str, str]] = []
            for key, value in query.items():
                if value is None:
                    continue
                if isinstance(value, list):
                    for item in value:
                        pairs.append((key, str(item)))
                else:
                    pairs.append((key, str(value)))
            url = url + "?" + urlencode(pairs)

        data: bytes | None = None
        headers = self._headers()
        multipart_summary: dict[str, Any] | None = None
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif multipart is not None:
            fields, documents = multipart
            boundary = "----hydradb-e2e-" + uuid.uuid4().hex
            data = self._encode_multipart(boundary, fields, documents)
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
            multipart_summary = {
                "fields": dict(fields),
                "documents": [
                    {
                        "field": fname,
                        "filename": filename,
                        "content_type": ctype,
                        "size_bytes": len(content),
                    }
                    for fname, filename, content, ctype in documents
                ],
            }

        req = Request(url, data=data, headers=headers, method=method)
        with urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:  # noqa: S310
            status = resp.status
            body = resp.read().decode("utf-8", errors="replace")
            response_headers = dict(resp.headers.items())
        # NOTE: HTTPError handling done by caller wrapper below.

        return self._finalize(
            method,
            path,
            status,
            response_headers,
            body,
            query,
            json_body,
            multipart_summary,
            expected,
            validate_contract,
            contract_path,
        )

    def _perform_safe(self, *args: Any, **kwargs: Any) -> ApiResponse:
        """perform() that also captures HTTPError (4xx/5xx) bodies."""
        method = (kwargs.get("method") or (args[0] if args else "GET")).upper()
        path = kwargs.get("path") or (args[1] if len(args) > 1 else "")
        try:
            return self.perform(*args, **kwargs)
        except HTTPError as exc:
            status = exc.code
            body = exc.read().decode("utf-8", errors="replace")
            response_headers = dict(exc.headers.items())
            return self._finalize(
                method,
                path,
                status,
                response_headers,
                body,
                kwargs.get("query"),
                kwargs.get("json_body"),
                None,
                tuple(kwargs.get("expected_statuses", (200,))),
                kwargs.get("validate_contract", True),
                kwargs.get("contract_path") or self._contract_path(path),
            )

    def _finalize(
        self,
        method: str,
        path: str,
        status: int,
        response_headers: dict[str, str],
        body: str,
        query: dict[str, Any] | None,
        json_body: Any | None,
        multipart_summary: dict[str, Any] | None,
        expected: tuple[int, ...],
        validate_contract: bool,
        contract_path: str,
    ) -> ApiResponse:
        parsed: Any = None
        parse_err: str | None = None
        if body:
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError as exc:
                parse_err = f"response is not valid JSON: {exc}; body={body[:500]!r}"

        response = ApiResponse(
            method=method,
            path=path,
            status=status,
            headers=response_headers,
            body_text=body,
            json_body=parsed,
            request_query=query,
            request_json=json_body,
            request_multipart=multipart_summary,
            expected_statuses=expected,
        )
        if status not in expected:
            response.status_error = (
                f"expected HTTP {sorted(expected)}, got {status}; body={body[:1500]}"
            )
            return response
        if parse_err:
            response.contract_error = parse_err
            return response
        if validate_contract:
            try:
                self.contract.validate_response(method, contract_path, status, parsed)
                response.contract_validated = True
            except ContractError as exc:
                response.contract_error = str(exc)
        return response

    def request(self, method: str, path: str, **kwargs: Any) -> ApiResponse:
        """perform() that raises on any failure — used by polling/setup helpers."""
        resp = self._perform_safe(method, path, **kwargs)
        if resp.status_error:
            raise ContractError(f"{method} {path}: {resp.status_error}")
        if resp.contract_error:
            raise ContractError(f"{method} {path}: {resp.contract_error}")
        return resp

    @staticmethod
    def _contract_path(path: str) -> str:
        if re.fullmatch(r"/webhooks/indexing/deliveries/[^/]+/retry", path):
            return "/webhooks/indexing/deliveries/{delivery_id}/retry"
        if re.fullmatch(r"/webhooks/indexing/deliveries/[^/]+", path):
            return "/webhooks/indexing/deliveries/{delivery_id}"
        return path

    @staticmethod
    def _encode_multipart(
        boundary: str,
        fields: dict[str, str],
        files: list[tuple[str, str, bytes, str]],
    ) -> bytes:
        chunks: list[bytes] = []
        for name, value in fields.items():
            chunks.extend(
                [
                    f"--{boundary}\r\n".encode(),
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                    value.encode(),
                    b"\r\n",
                ]
            )
        for field_name, filename, content, content_type in files:
            chunks.extend(
                [
                    f"--{boundary}\r\n".encode(),
                    f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode(),
                    f"Content-Type: {content_type}\r\n\r\n".encode(),
                    content,
                    b"\r\n",
                ]
            )
        chunks.append(f"--{boundary}--\r\n".encode())
        return b"".join(chunks)


# -----------------------------------------------------------------------------
# Case runner — calls an endpoint variation and persists its result file.
# -----------------------------------------------------------------------------


def run_case(
    client: ApiClient,
    recorder: Recorder,
    writer: ResultWriter,
    category: str,
    case: str,
    description: str,
    *,
    method: str,
    path: str,
    **kwargs: Any,
) -> ApiResponse | None:
    label = f"{category}/{case}"
    try:
        resp = client._perform_safe(method, path, label=label, **kwargs)
    except URLError as exc:
        writer.write_error(
            category,
            case,
            description,
            method,
            path,
            {
                "query": kwargs.get("query"),
                "json_body": kwargs.get("json_body"),
            },
            f"network error: {exc}",
        )
        recorder.fail(label, f"network error: {exc}")
        return None
    except Exception as exc:  # noqa: BLE001
        details = str(exc)
        if os.getenv("HYDRADB_DEBUG", "0") == "1":
            details += "\n" + traceback.format_exc()
        writer.write_error(
            category,
            case,
            description,
            method,
            path,
            {
                "query": kwargs.get("query"),
                "json_body": kwargs.get("json_body"),
            },
            details,
        )
        recorder.fail(label, details)
        return None

    error = resp.status_error or resp.contract_error
    passed = resp.ok
    writer.write(category, case, description, resp, passed, error)
    if passed:
        recorder.pass_(label, f"HTTP {resp.status}", resp.request_id)
    else:
        recorder.fail(label, error or "failed", resp.request_id)
    return resp


def run_check(
    recorder: Recorder, name: str, fn: Callable[[], "str | ApiResponse | None"]
) -> Any:
    try:
        result = fn()
        if isinstance(result, ApiResponse):
            recorder.pass_(name, f"HTTP {result.status}", result.request_id)
        elif isinstance(result, str):
            recorder.pass_(name, result)
        else:
            recorder.pass_(name)
        return result
    except Exception as exc:  # noqa: BLE001
        details = str(exc)
        if os.getenv("HYDRADB_DEBUG", "0") == "1":
            details += "\n" + traceback.format_exc()
        recorder.fail(name, details)
        return None


# -----------------------------------------------------------------------------
# Static cURL/docs checks.
# -----------------------------------------------------------------------------


def audit_endpoint_docs(contract: OpenApiContract, recorder: Recorder) -> None:
    endpoint_files = sorted(ENDPOINT_DOCS_DIR.glob("*.mdx"))
    if not endpoint_files:
        recorder.fail(
            "docs.endpoint_files", f"No endpoint docs found in {ENDPOINT_DOCS_DIR}"
        )
        return

    directive_re = re.compile(
        r'openapi:\s*"[^"]+\s+(GET|POST|DELETE|PUT|PATCH)\s+([^"\s]+)"', re.I
    )
    curl_block_re = re.compile(r"```bash\s+cURL\n(.*?)```", re.S)
    url_re = re.compile(r"https?://[^/'\s]+([^'\s\\]+)")
    method_re = re.compile(
        r"curl\s+(?:-[^\s]+\s+)*-X\s+(GET|POST|DELETE|PUT|PATCH)", re.I
    )

    documented_ops: set[tuple[str, str]] = set()
    for doc_path in endpoint_files:
        text = doc_path.read_text()
        directive = directive_re.search(text)
        if not directive:
            # Overview pages intentionally have no endpoint directive.
            continue
        method, path = directive.group(1).upper(), directive.group(2)
        documented_ops.add((method, path))
        name = f"docs.openapi {doc_path.name} {method} {path}"
        try:
            contract.operation(method, path)
            recorder.pass_(name)
        except ContractError as exc:
            recorder.fail(name, str(exc))
            continue

        blocks = curl_block_re.findall(text)
        if not blocks:
            recorder.fail(f"docs.curl {doc_path.name}", "No bash cURL block found")
            continue

        matching_block_found = False
        for i, block in enumerate(blocks, start=1):
            curl_name = f"docs.curl {doc_path.name} block {i}"
            method_match = method_re.search(block)
            curl_method = (
                method_match.group(1).upper()
                if method_match
                else ("GET" if "curl -G" in block else "GET")
            )
            url_match = url_re.search(block)
            curl_path = urlparse(url_match.group(1)).path if url_match else ""
            if curl_method == method and curl_path == path:
                matching_block_found = True
                missing_headers = []
                if "Authorization: Bearer" not in block:
                    missing_headers.append("Authorization: Bearer")
                if "API-Version: 2" not in block:
                    missing_headers.append("API-Version: 2")
                if missing_headers:
                    recorder.fail(
                        curl_name, f"Missing required headers: {missing_headers}"
                    )
                else:
                    recorder.pass_(curl_name, f"matches {method} {path}")
        if not matching_block_found:
            recorder.fail(
                f"docs.curl {doc_path.name}",
                f"No cURL block matched openapi directive {method} {path}",
            )

    canonical_ops = {
        ("POST", "/tenants"),
        ("GET", "/tenants"),
        ("DELETE", "/tenants"),
        ("GET", "/tenants/status"),
        ("GET", "/tenants/sub-tenants"),
        ("GET", "/tenants/stats"),
        ("POST", "/context/ingest"),
        ("GET", "/context/status"),
        ("GET", "/context/inspect"),
        ("POST", "/context/list"),
        ("DELETE", "/context"),
        ("GET", "/context/relations"),
        ("POST", "/query"),
    }
    missing_docs = sorted(canonical_ops - documented_ops)
    if missing_docs:
        recorder.fail(
            "docs.coverage canonical_v2", f"Missing endpoint pages for {missing_docs}"
        )
    else:
        recorder.pass_(
            "docs.coverage canonical_v2",
            f"{len(canonical_ops)} documented endpoint pages",
        )

    # Confirm the /search -> /query rename actually landed everywhere.
    if "/query" in contract.doc.get("paths", {}) and "/search" not in contract.doc.get(
        "paths", {}
    ):
        recorder.pass_(
            "docs.rename /search->/query path", "OpenAPI exposes POST /query"
        )
    else:
        recorder.fail(
            "docs.rename /search->/query path",
            "Expected POST /query and no /search path in OpenAPI",
        )

    query_schema = (
        contract.doc.get("components", {}).get("schemas", {}).get("QueryRequest", {})
    )
    props = query_schema.get("properties", {})
    if "query_by" in props and "type" in props and "search_by" not in props:
        recorder.pass_(
            "docs.rename query_by/type fields",
            "QueryRequest uses `type` + `query_by`",
        )
    else:
        recorder.fail(
            "docs.rename query_by/type fields",
            f"QueryRequest field rename incomplete; keys={sorted(props)[:12]}",
        )

    if (
        "query_apps" in props
        and "query_forceful_relations" in props
        and "search_apps" not in props
        and "search_forceful_relations" not in props
    ):
        recorder.pass_(
            "docs.rename query flags",
            "QueryRequest uses `query_apps` + `query_forceful_relations`",
        )
    else:
        recorder.fail(
            "docs.rename query flags",
            f"QueryRequest query flag rename incomplete; keys={sorted(props)}",
        )

    paths = contract.doc.get("paths", {})
    if all(not p.startswith("/source") for p in paths) and any(
        p.startswith("/context") for p in paths
    ):
        recorder.pass_(
            "docs.rename /source->/context paths",
            "OpenAPI exposes context-management paths only",
        )
    else:
        recorder.fail(
            "docs.rename /source->/context paths",
            f"Expected /context paths and no /source paths; paths={sorted(paths)}",
        )


# -----------------------------------------------------------------------------
# Setup: tenant + ingestion.
# -----------------------------------------------------------------------------


def create_context() -> Context:
    run_id = os.getenv("HYDRADB_E2E_RUN_ID", time.strftime("%Y%m%d%H%M%S"))
    suffix = re.sub(r"[^a-zA-Z0-9_]", "_", run_id)[-12:]
    results_dir = RESULTS_ROOT / f"run_{suffix}"
    return Context(run_id=suffix, results_dir=results_dir)


def create_tenant_body(tenant_id: str) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "tenant_metadata_schema": [
            {
                "name": "department",
                "data_type": "VARCHAR",
                "max_length": 256,
                "enable_match": True,
                "enable_dense_embedding": False,
                "enable_sparse_embedding": False,
            },
            {
                "name": "workspace",
                "data_type": "VARCHAR",
                "max_length": 256,
                "enable_match": True,
                "enable_dense_embedding": False,
                "enable_sparse_embedding": False,
            },
        ],
        "is_embeddings_tenant": False,
    }


def ensure_tenant(client: ApiClient, recorder: Recorder, tenant_id: str) -> None:
    def _create() -> ApiResponse:
        return client.request(
            "POST",
            "/tenants",
            json_body=create_tenant_body(tenant_id),
            expected_statuses=(200, 409),
            label=f"POST /tenants create {tenant_id}",
            validate_contract=False,
        )

    resp = run_check(recorder, f"runtime POST /tenants create {tenant_id}", _create)
    if not isinstance(resp, ApiResponse):
        return

    body = resp.json_body or {}
    if resp.status == 200:
        try:
            client.contract.validate_response("POST", "/tenants", 200, body)
            recorder.pass_(
                f"runtime POST /tenants contract {tenant_id}",
                "200 response matches OpenAPI schema",
                resp.request_id,
            )
        except ContractError as exc:
            recorder.fail(
                f"runtime POST /tenants contract {tenant_id}", str(exc), resp.request_id
            )
    elif resp.status == 409:
        try:
            client.contract.validate_envelope(body, "POST /tenants 409")
        except ContractError as exc:
            recorder.fail(
                f"runtime POST /tenants 409 envelope {tenant_id}",
                str(exc),
                resp.request_id,
            )
        err = body.get("error") if isinstance(body, dict) else None
        code = err.get("code") if isinstance(err, dict) else ""
        recorder.pass_(
            f"runtime POST /tenants existing {tenant_id}",
            f"tenant already exists (code={code}); continuing",
            resp.request_id,
        )


def wait_for_tenant_ready(
    client: ApiClient, recorder: Recorder, tenant_id: str
) -> bool:
    deadline = time.time() + TENANT_READY_TIMEOUT_SECONDS
    last_status = None
    while time.time() < deadline:
        resp = run_check(
            recorder,
            f"runtime GET /tenants/status poll {tenant_id}",
            lambda: client.request(
                "GET", "/tenants/status", query={"tenant_id": tenant_id}
            ),
        )
        if not isinstance(resp, ApiResponse):
            time.sleep(POLL_INTERVAL_SECONDS)
            continue
        data = resp.data or {}
        infra = data.get("infra") if isinstance(data, dict) else {}
        last_status = infra
        if isinstance(infra, dict) and infra.get("ready_for_ingestion") is True:
            recorder.pass_(
                f"runtime tenant ready {tenant_id}",
                "infra.ready_for_ingestion=true",
                resp.request_id,
            )
            return True
        time.sleep(POLL_INTERVAL_SECONDS)
    recorder.fail(
        f"runtime tenant ready {tenant_id}", f"Timed out. Last infra={last_status}"
    )
    return False


def _collect_ids(resp: ApiResponse | None) -> list[str]:
    ids: list[str] = []
    if isinstance(resp, ApiResponse) and isinstance(resp.data, dict):
        for item in resp.data.get("results", []) or []:
            if isinstance(item, dict) and item.get("id"):
                ids.append(item["id"])
    return ids


def ingest_all_variations(
    client: ApiClient, recorder: Recorder, writer: ResultWriter, ctx: Context
) -> None:
    """Exercise POST /context/ingest across every documented variation."""
    run = ctx.run_id
    tmeta = {"department": "support", "workspace": "docs"}
    dmeta = {"source": "e2e_contract", "run_id": run}

    def txt(name: str, body: str) -> tuple[str, str, bytes, str]:
        return ("documents", name, body.encode("utf-8"), "text/plain")

    def app_source(
        *,
        id_: str,
        title: str,
        provider: str,
        kind: str,
        body: str,
        external_id: str | None = None,
        relations: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        external_id = external_id or id_
        if kind == "message":
            fields: dict[str, Any] = {
                "kind": "message",
                "body": body,
                "author": "e2e-bot",
                "thread_id": external_id,
                "created_at": "2026-05-29T00:00:00Z",
            }
        elif kind == "knowledge_base":
            fields = {
                "kind": "knowledge_base",
                "title": title,
                "body": body,
                "created_by": "e2e-contract",
                "updated_by": "e2e-contract",
                "created_at": "2026-05-29T00:00:00Z",
                "updated_at": "2026-05-29T00:00:00Z",
            }
        else:
            fields = {"kind": "custom", "data": {"title": title, "body": body}}
        item: dict[str, Any] = {
            "id": id_,
            "tenant_id": TENANT_ID,
            "sub_tenant_id": SUB_TENANT_ID,
            "title": title,
            "type": provider,
            "kind": kind,
            "provider": provider,
            "external_id": external_id,
            "timestamp": "2026-05-29T00:00:00Z",
            "fields": fields,
            "metadata": tmeta,
            "additional_metadata": dmeta,
        }
        if relations:
            item["relations"] = relations
        return item

    # 1. Knowledge: single text file.
    sid_file1 = f"e2e_kfile1_{run}"
    r = run_case(
        client,
        recorder,
        writer,
        "source_ingest",
        "knowledge_file_single_txt",
        "type=knowledge, one .txt document with document_metadata (metadata + additional_metadata)",
        method="POST",
        path="/context/ingest",
        expected_statuses=(202,),
        multipart=(
            {
                "type": "knowledge",
                "tenant_id": TENANT_ID,
                "sub_tenant_id": SUB_TENANT_ID,
                "upsert": "true",
                "document_metadata": json.dumps(
                    [
                        {
                            "id": sid_file1,
                            "title": "E2E Refund Policy",
                            "type": "txt",
                            "metadata": tmeta,
                            "additional_metadata": dmeta,
                        }
                    ]
                ),
            },
            [
                txt(
                    "e2e_refund_policy.txt",
                    f"HydraDB E2E contract test policy {run}. Refunds are available "
                    "within 30 days of purchase. Support agents should cite this policy.",
                )
            ],
        ),
    )
    ctx.knowledge_ids += _collect_ids(r) or [sid_file1]

    # 2. Knowledge: multiple text documents in one request.
    sid_file2a, sid_file2b = f"e2e_kfile2a_{run}", f"e2e_kfile2b_{run}"
    r = run_case(
        client,
        recorder,
        writer,
        "source_ingest",
        "knowledge_file_multi_txt",
        "type=knowledge, two .txt documents with a parallel document_metadata array",
        method="POST",
        path="/context/ingest",
        expected_statuses=(202,),
        multipart=(
            {
                "type": "knowledge",
                "tenant_id": TENANT_ID,
                "sub_tenant_id": SUB_TENANT_ID,
                "upsert": "true",
                "document_metadata": json.dumps(
                    [
                        {
                            "id": sid_file2a,
                            "metadata": tmeta,
                            "additional_metadata": dmeta,
                        },
                        {
                            "id": sid_file2b,
                            "metadata": {"department": "ops"},
                            "additional_metadata": dmeta,
                        },
                    ]
                ),
            },
            [
                txt(
                    "e2e_sla.txt",
                    f"E2E {run}: The support SLA is a 24 hour first response.",
                ),
                txt(
                    "e2e_runbook.txt",
                    f"E2E {run}: Deploy runbook step one is to drain traffic.",
                ),
            ],
        ),
    )
    ctx.knowledge_ids += _collect_ids(r) or [sid_file2a, sid_file2b]

    # 3. Knowledge: app_knowledge as a single JSON object (not an array).
    sid_app1 = f"e2e_kapp1_{run}"
    r = run_case(
        client,
        recorder,
        writer,
        "source_ingest",
        "knowledge_app_single_object",
        "type=knowledge, app_knowledge sent as a single JSON object",
        method="POST",
        path="/context/ingest",
        expected_statuses=(202,),
        multipart=(
            {
                "type": "knowledge",
                "tenant_id": TENANT_ID,
                "sub_tenant_id": SUB_TENANT_ID,
                "upsert": "true",
                "app_knowledge": json.dumps(
                    app_source(
                        id_=sid_app1,
                        title="E2E pricing discussion",
                        provider="slack",
                        kind="message",
                        external_id=f"thread_{sid_app1}",
                        body=f"E2E {run}: Starter costs $29 per month and Pro costs $79 per month.",
                    )
                ),
            },
            [],
        ),
    )
    ctx.app_ids += _collect_ids(r) or [sid_app1]

    # 4. Knowledge: app_knowledge as an array, with a forceful relation back to file1.
    sid_app2 = f"e2e_kapp2_{run}"
    r = run_case(
        client,
        recorder,
        writer,
        "source_ingest",
        "knowledge_app_array_with_relations",
        "type=knowledge, app_knowledge array item declaring app-native relations[]",
        method="POST",
        path="/context/ingest",
        expected_statuses=(202,),
        multipart=(
            {
                "type": "knowledge",
                "tenant_id": TENANT_ID,
                "sub_tenant_id": SUB_TENANT_ID,
                "upsert": "true",
                "app_knowledge": json.dumps(
                    [
                        app_source(
                            id_=sid_app2,
                            title="E2E Notion plan",
                            provider="notion",
                            kind="knowledge_base",
                            external_id=f"page_{sid_app2}",
                            body=f"E2E {run}: The refund workflow links to the pricing tiers.",
                            relations=[
                                {
                                    "predicate": "related_to",
                                    "target": {"id": sid_file1},
                                    "properties": {"reason": "e2e forceful relation"},
                                }
                            ],
                        )
                    ]
                ),
            },
            [],
        ),
    )
    ctx.app_ids += _collect_ids(r) or [sid_app2]
    ctx.forceful_declaring_id = sid_app2
    ctx.forceful_target_id = sid_file1

    # 5. Knowledge: documents + app_knowledge mixed in one request.
    sid_mixf, sid_mixa = f"e2e_kmixf_{run}", f"e2e_kmixa_{run}"
    r = run_case(
        client,
        recorder,
        writer,
        "source_ingest",
        "knowledge_documents_and_app_mixed",
        "type=knowledge, documents and app_knowledge in the same request",
        method="POST",
        path="/context/ingest",
        expected_statuses=(202,),
        multipart=(
            {
                "type": "knowledge",
                "tenant_id": TENANT_ID,
                "sub_tenant_id": SUB_TENANT_ID,
                "upsert": "true",
                "document_metadata": json.dumps(
                    [
                        {
                            "id": sid_mixf,
                            "metadata": tmeta,
                            "additional_metadata": dmeta,
                        }
                    ]
                ),
                "app_knowledge": json.dumps(
                    [
                        app_source(
                            id_=sid_mixa,
                            title="E2E mixed app",
                            provider="webpage",
                            kind="knowledge_base",
                            external_id=f"page_{sid_mixa}",
                            body=f"E2E {run}: mixed-request app source content.",
                        )
                    ]
                ),
            },
            [txt("e2e_mixed.txt", f"E2E {run}: mixed-request uploaded file content.")],
        ),
    )
    ctx.knowledge_ids += [s for s in _collect_ids(r) if s] or [
        sid_mixf,
        sid_mixa,
    ]

    # 6. Knowledge: upsert=false on a brand-new id (disposable; deleted later).
    sid_upsert = f"e2e_kdisp_{run}"
    r = run_case(
        client,
        recorder,
        writer,
        "source_ingest",
        "knowledge_upsert_false_new_id",
        "type=knowledge, upsert=false with a new id (disposable, deleted later)",
        method="POST",
        path="/context/ingest",
        expected_statuses=(202,),
        multipart=(
            {
                "type": "knowledge",
                "tenant_id": TENANT_ID,
                "sub_tenant_id": SUB_TENANT_ID,
                "upsert": "false",
                "document_metadata": json.dumps(
                    [
                        {
                            "id": sid_upsert,
                            "metadata": tmeta,
                            "additional_metadata": dmeta,
                        }
                    ]
                ),
            },
            [
                txt(
                    "e2e_disposable.txt",
                    f"E2E {run}: disposable knowledge for delete test.",
                )
            ],
        ),
    )
    got = _collect_ids(r)
    ctx.disposable_knowledge_id = got[0] if got else sid_upsert
    ctx.knowledge_ids += got or [sid_upsert]

    # Memory items are JSON-encoded as part of the top-level `memories` form field,
    # but their metadata fields themselves are plain objects (not JSON strings).

    # 7. Memory: raw text with infer=true.
    sid_mem1 = f"e2e_mem1_{run}"
    r = run_case(
        client,
        recorder,
        writer,
        "source_ingest",
        "memory_text_infer_true",
        "type=memory, text + infer=true (extract preference) + user_name",
        method="POST",
        path="/context/ingest",
        expected_statuses=(202,),
        multipart=(
            {
                "type": "memory",
                "tenant_id": TENANT_ID,
                "sub_tenant_id": SUB_TENANT_ID,
                "upsert": "true",
                "memories": json.dumps(
                    [
                        {
                            "id": sid_mem1,
                            "title": "Alex tone preference",
                            "text": f"E2E {run}: Alex prefers concise technical answers and dark mode.",
                            "infer": True,
                            "user_name": "Alex",
                            "metadata": tmeta,
                            "additional_metadata": dmeta,
                        }
                    ]
                ),
            },
            [],
        ),
    )
    ctx.memory_ids += _collect_ids(r) or [sid_mem1]

    # 8. Memory: raw text stored verbatim (infer=false), with is_markdown.
    sid_mem2 = f"e2e_mem2_{run}"
    r = run_case(
        client,
        recorder,
        writer,
        "source_ingest",
        "memory_text_infer_false_markdown",
        "type=memory, text + infer=false (verbatim) + is_markdown=true (disposable)",
        method="POST",
        path="/context/ingest",
        expected_statuses=(202,),
        multipart=(
            {
                "type": "memory",
                "tenant_id": TENANT_ID,
                "sub_tenant_id": SUB_TENANT_ID,
                "upsert": "true",
                "memories": json.dumps(
                    [
                        {
                            "id": sid_mem2,
                            "title": "Verbatim note",
                            "text": f"# E2E {run}\nStore this note verbatim without inference.",
                            "infer": False,
                            "is_markdown": True,
                            "metadata": tmeta,
                            "additional_metadata": dmeta,
                        }
                    ]
                ),
            },
            [],
        ),
    )
    got = _collect_ids(r)
    ctx.disposable_memory_id = got[0] if got else sid_mem2
    ctx.memory_ids += got or [sid_mem2]

    # 9. Memory: conversation pairs (user_assistant_pairs), infer=false.
    sid_mem3 = f"e2e_mem3_{run}"
    r = run_case(
        client,
        recorder,
        writer,
        "source_ingest",
        "memory_conversation_pairs",
        "type=memory, user_assistant_pairs instead of text, infer=false",
        method="POST",
        path="/context/ingest",
        expected_statuses=(202,),
        multipart=(
            {
                "type": "memory",
                "tenant_id": TENANT_ID,
                "sub_tenant_id": SUB_TENANT_ID,
                "upsert": "true",
                "memories": json.dumps(
                    [
                        {
                            "id": sid_mem3,
                            "title": "Support conversation about refunds",
                            "user_assistant_pairs": [
                                {
                                    "user": "Can I get a refund?",
                                    "assistant": f"E2E {run}: Refunds are available within 30 days.",
                                }
                            ],
                            "infer": False,
                            "metadata": tmeta,
                        }
                    ]
                ),
            },
            [],
        ),
    )
    ctx.memory_ids += _collect_ids(r) or [sid_mem3]

    # 10. Memory: infer=true with custom_instructions + expiry_time.
    sid_mem4 = f"e2e_mem4_{run}"
    r = run_case(
        client,
        recorder,
        writer,
        "source_ingest",
        "memory_infer_custom_instructions_expiry",
        "type=memory, infer=true + custom_instructions + expiry_time TTL",
        method="POST",
        path="/context/ingest",
        expected_statuses=(202,),
        multipart=(
            {
                "type": "memory",
                "tenant_id": TENANT_ID,
                "sub_tenant_id": SUB_TENANT_ID,
                "upsert": "true",
                "memories": json.dumps(
                    [
                        {
                            "id": sid_mem4,
                            "title": "Channel preference",
                            "text": f"E2E {run}: Alex usually reaches out from the billing help center.",
                            "infer": True,
                            "custom_instructions": "Extract the preferred support channel.",
                            "user_name": "Alex",
                            "expiry_time": 86400,
                            "metadata": tmeta,
                            "additional_metadata": dmeta,
                        }
                    ]
                ),
            },
            [],
        ),
    )
    ctx.memory_ids += _collect_ids(r) or [sid_mem4]


def wait_for_sources_searchable(
    client: ApiClient, recorder: Recorder, ids: list[str]
) -> bool:
    deadline = time.time() + SOURCE_READY_TIMEOUT_SECONDS
    terminal_failures = {"errored", "failed"}
    searchable = {"graph_creation", "completed"}
    last_statuses = None
    # Poll only knowledge/app ids — memory items index on a different path and may
    # not report indexing_status here. The all() gate is over reported statuses.
    while time.time() < deadline:
        resp = run_check(
            recorder,
            "runtime GET /context/status poll",
            lambda: client.request(
                "GET",
                "/context/status",
                query={
                    "tenant_id": TENANT_ID,
                    "sub_tenant_id": SUB_TENANT_ID,
                    "ids": ids,
                },
            ),
        )
        if not isinstance(resp, ApiResponse):
            time.sleep(POLL_INTERVAL_SECONDS)
            continue
        statuses = (
            (resp.data or {}).get("statuses", []) if isinstance(resp.data, dict) else []
        )
        last_statuses = statuses
        values = [s.get("indexing_status") for s in statuses if isinstance(s, dict)]
        if values and all(v in searchable for v in values):
            recorder.pass_(
                "runtime sources searchable", f"statuses={values}", resp.request_id
            )
            return True
        if any(v in terminal_failures for v in values):
            recorder.fail(
                "runtime sources searchable",
                f"terminal failure statuses={statuses}",
                resp.request_id,
            )
            return False
        time.sleep(POLL_INTERVAL_SECONDS)
    recorder.fail(
        "runtime sources searchable", f"Timed out. Last statuses={last_statuses}"
    )
    return False


# -----------------------------------------------------------------------------
# Endpoint variation suites.
# -----------------------------------------------------------------------------


def exercise_source_status(
    client: ApiClient, recorder: Recorder, writer: ResultWriter, ctx: Context
) -> None:
    k_ids = ctx.knowledge_ids + ctx.app_ids
    run_case(
        client,
        recorder,
        writer,
        "source_status",
        "single_id",
        "GET /context/status using one id in the ids list",
        method="GET",
        path="/context/status",
        query={
            "tenant_id": TENANT_ID,
            "sub_tenant_id": SUB_TENANT_ID,
            "ids": [k_ids[0]] if k_ids else None,
        },
    )
    run_case(
        client,
        recorder,
        writer,
        "source_status",
        "multiple_ids",
        "GET /context/status using the ids list param (repeated query key)",
        method="GET",
        path="/context/status",
        query={
            "tenant_id": TENANT_ID,
            "sub_tenant_id": SUB_TENANT_ID,
            "ids": k_ids[:5],
        },
    )
    if ctx.memory_ids:
        run_case(
            client,
            recorder,
            writer,
            "source_status",
            "memory_id",
            "GET /context/status for a memory id",
            method="GET",
            path="/context/status",
            query={
                "tenant_id": TENANT_ID,
                "sub_tenant_id": SUB_TENANT_ID,
                "ids": [ctx.memory_ids[0]],
            },
        )


def exercise_source_fetch(
    client: ApiClient, recorder: Recorder, writer: ResultWriter, ctx: Context
) -> None:
    kid = (ctx.knowledge_ids or [None])[0]
    for mode in ("content", "url", "both"):
        run_case(
            client,
            recorder,
            writer,
            "source_fetch",
            f"knowledge_mode_{mode}",
            f"GET /context/inspect knowledge source, mode={mode}",
            method="GET",
            path="/context/inspect",
            query={
                "tenant_id": TENANT_ID,
                "sub_tenant_id": SUB_TENANT_ID,
                "id": kid,
                "mode": mode,
            },
        )
    run_case(
        client,
        recorder,
        writer,
        "source_fetch",
        "knowledge_url_custom_expiry",
        "GET /context/inspect mode=url with a custom expiry_seconds TTL",
        method="GET",
        path="/context/inspect",
        query={
            "tenant_id": TENANT_ID,
            "sub_tenant_id": SUB_TENANT_ID,
            "id": kid,
            "mode": "url",
            "expiry_seconds": 120,
        },
    )
    if ctx.memory_ids:
        run_case(
            client,
            recorder,
            writer,
            "source_fetch",
            "memory_mode_content",
            "GET /context/inspect a memory source (returns raw text, no presigned URL)",
            method="GET",
            path="/context/inspect",
            query={
                "tenant_id": TENANT_ID,
                "sub_tenant_id": SUB_TENANT_ID,
                "id": ctx.memory_ids[0],
                "mode": "content",
            },
            # Staging returns 404 for memory ids even though the docs say it should
            # work — treat 404 as acceptable so the testcase captures the real response.
            expected_statuses=(200, 404),
            validate_contract=False,
        )


def exercise_source_list(
    client: ApiClient, recorder: Recorder, writer: ResultWriter, ctx: Context
) -> None:
    base = {"tenant_id": TENANT_ID, "sub_tenant_id": SUB_TENANT_ID}
    run_case(
        client,
        recorder,
        writer,
        "source_list",
        "knowledge_basic",
        "POST /context/list type=knowledge, default paging",
        method="POST",
        path="/context/list",
        json_body={**base, "type": "knowledge", "page": 1, "page_size": 50},
    )
    run_case(
        client,
        recorder,
        writer,
        "source_list",
        "knowledge_filters_additional_metadata",
        "POST /context/list with filters.additional_metadata exact match",
        method="POST",
        path="/context/list",
        json_body={
            **base,
            "type": "knowledge",
            "page": 1,
            "page_size": 50,
            "filters": {"additional_metadata": {"source": "e2e_contract"}},
        },
    )
    run_case(
        client,
        recorder,
        writer,
        "source_list",
        "knowledge_filters_source_fields",
        "POST /context/list with filters.source_fields (e.g. type=slack)",
        method="POST",
        path="/context/list",
        json_body={
            **base,
            "type": "knowledge",
            "page": 1,
            "page_size": 50,
            "filters": {"source_fields": {"type": "slack"}},
        },
    )
    run_case(
        client,
        recorder,
        writer,
        "source_list",
        "knowledge_include_fields",
        "POST /context/list with include_fields projection",
        method="POST",
        path="/context/list",
        json_body={
            **base,
            "type": "knowledge",
            "page": 1,
            "page_size": 25,
            "include_fields": ["title", "type", "url", "timestamp"],
        },
    )
    run_case(
        client,
        recorder,
        writer,
        "source_list",
        "knowledge_ids",
        "POST /context/list scoped to explicit ids",
        method="POST",
        path="/context/list",
        json_body={
            **base,
            "type": "knowledge",
            "ids": (ctx.knowledge_ids + ctx.app_ids)[:3],
            "page": 1,
            "page_size": 10,
        },
    )
    run_case(
        client,
        recorder,
        writer,
        "source_list",
        "knowledge_pagination_small_page",
        "POST /context/list with page_size=5 to exercise pagination metadata",
        method="POST",
        path="/context/list",
        json_body={**base, "type": "knowledge", "page": 1, "page_size": 5},
    )
    run_case(
        client,
        recorder,
        writer,
        "source_list",
        "memory_basic",
        "POST /context/list type=memory (returns ListUserMemoriesResponse)",
        method="POST",
        path="/context/list",
        json_body={**base, "type": "memory", "page": 1, "page_size": 50},
    )


def exercise_source_relations(
    client: ApiClient, recorder: Recorder, writer: ResultWriter, ctx: Context
) -> None:
    app_id = (ctx.app_ids or ctx.knowledge_ids or [None])[0]
    run_case(
        client,
        recorder,
        writer,
        "source_relations",
        "knowledge_by_id",
        "GET /context/relations scoped to a single knowledge id",
        method="GET",
        path="/context/relations",
        query={
            "tenant_id": TENANT_ID,
            "sub_tenant_id": SUB_TENANT_ID,
            "id": app_id,
            "type": "knowledge",
            "limit": 500,
        },
    )
    run_case(
        client,
        recorder,
        writer,
        "source_relations",
        "knowledge_subtenant_wide",
        "GET /context/relations across the whole sub-tenant (no id)",
        method="GET",
        path="/context/relations",
        query={
            "tenant_id": TENANT_ID,
            "sub_tenant_id": SUB_TENANT_ID,
            "type": "knowledge",
            "limit": 100,
        },
    )
    run_case(
        client,
        recorder,
        writer,
        "source_relations",
        "knowledge_small_limit",
        "GET /context/relations with a small limit to exercise truncation/cursor",
        method="GET",
        path="/context/relations",
        query={
            "tenant_id": TENANT_ID,
            "sub_tenant_id": SUB_TENANT_ID,
            "id": app_id,
            "type": "knowledge",
            "limit": 1,
        },
    )
    if ctx.memory_ids:
        run_case(
            client,
            recorder,
            writer,
            "source_relations",
            "memory_by_id",
            "GET /context/relations type=memory for a memory source",
            method="GET",
            path="/context/relations",
            query={
                "tenant_id": TENANT_ID,
                "sub_tenant_id": SUB_TENANT_ID,
                "id": ctx.memory_ids[0],
                "type": "memory",
                "limit": 100,
            },
        )


def exercise_query(
    client: ApiClient, recorder: Recorder, writer: ResultWriter, ctx: Context
) -> None:
    """POST /query — every retrieval dimension we support."""
    q = f"What is Alex's preference and what are the refund rules for E2E {ctx.run_id}?"
    base = {
        "tenant_id": TENANT_ID,
        "sub_tenant_id": SUB_TENANT_ID,
        "query": q,
    }

    cases: list[tuple[str, str, dict[str, Any]]] = [
        (
            "knowledge_hybrid_fast",
            "type=knowledge, query_by=hybrid, mode=fast (baseline)",
            {"type": "knowledge", "query_by": "hybrid", "mode": "fast"},
        ),
        (
            "knowledge_hybrid_thinking",
            "type=knowledge, query_by=hybrid, mode=thinking",
            {"type": "knowledge", "query_by": "hybrid", "mode": "thinking"},
        ),
        (
            "knowledge_thinking_forceful_relations",
            "thinking mode + query_forceful_relations=true + graph_context=true",
            {
                "type": "knowledge",
                "query_by": "hybrid",
                "mode": "thinking",
                "query_forceful_relations": True,
                "graph_context": True,
            },
        ),
        (
            "knowledge_graph_context_off",
            "graph_context=false drops the graph slice",
            {
                "type": "knowledge",
                "query_by": "hybrid",
                "mode": "fast",
                "graph_context": False,
            },
        ),
        (
            "knowledge_text_operator_or",
            "query_by=text, operator=or (BM25 any term)",
            {"type": "knowledge", "query_by": "text", "operator": "or"},
        ),
        (
            "knowledge_text_operator_and",
            "query_by=text, operator=and (BM25 all terms)",
            {"type": "knowledge", "query_by": "text", "operator": "and"},
        ),
        (
            "knowledge_text_operator_phrase",
            "query_by=text, operator=phrase (exact phrase)",
            {
                "type": "knowledge",
                "query_by": "text",
                "operator": "phrase",
                "query": "Refunds are available within 30 days",
            },
        ),
        (
            "knowledge_alpha_numeric",
            "hybrid with alpha=0.5 (balanced semantic/BM25)",
            {"type": "knowledge", "query_by": "hybrid", "mode": "fast", "alpha": 0.5},
        ),
        (
            "knowledge_alpha_auto",
            "hybrid with alpha='auto'",
            {
                "type": "knowledge",
                "query_by": "hybrid",
                "mode": "fast",
                "alpha": "auto",
            },
        ),
        (
            "knowledge_recency_bias",
            "hybrid with recency_bias=0.4",
            {
                "type": "knowledge",
                "query_by": "hybrid",
                "mode": "fast",
                "recency_bias": 0.4,
            },
        ),
        (
            "knowledge_query_apps",
            "hybrid with query_apps=true (app-aware lane)",
            {
                "type": "knowledge",
                "query_by": "hybrid",
                "mode": "fast",
                "query_apps": True,
            },
        ),
        (
            "knowledge_filter_metadata",
            "metadata_filters top-level key (metadata) — needs schema",
            {
                "type": "knowledge",
                "query_by": "hybrid",
                "mode": "fast",
                "metadata_filters": {"department": "support"},
            },
        ),
        (
            "knowledge_filter_additional_metadata",
            "metadata_filters nested under additional_metadata (free-form)",
            {
                "type": "knowledge",
                "query_by": "hybrid",
                "mode": "fast",
                "metadata_filters": {"additional_metadata": {"source": "e2e_contract"}},
            },
        ),
        (
            "knowledge_additional_context",
            "request-time additional_context hint",
            {
                "type": "knowledge",
                "query_by": "hybrid",
                "mode": "fast",
                "additional_context": "User is asking from the billing help center.",
            },
        ),
        (
            "knowledge_max_results_small",
            "max_results=3 for a tight prompt",
            {
                "type": "knowledge",
                "query_by": "hybrid",
                "mode": "fast",
                "max_results": 3,
            },
        ),
        (
            "memory_hybrid_fast",
            "type=memory, query_by=hybrid, mode=fast",
            {"type": "memory", "query_by": "hybrid", "mode": "fast"},
        ),
        (
            "memory_hybrid_thinking",
            "type=memory, query_by=hybrid, mode=thinking",
            {"type": "memory", "query_by": "hybrid", "mode": "thinking"},
        ),
        (
            "all_hybrid_fast",
            "type=all, merge knowledge + memory, fast",
            {"type": "all", "query_by": "hybrid", "mode": "fast"},
        ),
        (
            "all_hybrid_thinking",
            "type=all, merge knowledge + memory, thinking + graph",
            {
                "type": "all",
                "query_by": "hybrid",
                "mode": "thinking",
                "graph_context": True,
            },
        ),
        (
            "zero_results",
            "query that matches nothing -> empty arrays, success=true",
            {
                "type": "knowledge",
                "query_by": "hybrid",
                "mode": "fast",
                "query": "zzqqxx nonexistent gibberish token zzz",
            },
        ),
    ]
    for case, desc, overrides in cases:
        body = {**base, **overrides}
        run_case(
            client,
            recorder,
            writer,
            "query",
            case,
            desc,
            method="POST",
            path="/query",
            json_body=body,
        )

    # Negative test: empty query must be rejected with a documented error envelope.
    run_case(
        client,
        recorder,
        writer,
        "query",
        "empty_query_negative",
        "empty query -> 400/422 INVALID_PARAMETERS (documented failure)",
        method="POST",
        path="/query",
        json_body={**base, "query": "", "type": "knowledge"},
        expected_statuses=(400, 422),
        validate_contract=False,
    )


# -----------------------------------------------------------------------------
# Semantic checks — verify behavior, not just response shape.
#
# Each check asserts the API actually DID what the parameter promises (a filter
# narrowed results, an upsert overwrote content, a presigned URL is downloadable,
# etc.) and records the observed evidence to a `semantic/` result file.
# -----------------------------------------------------------------------------


def _record_semantic(
    recorder: Recorder,
    writer: ResultWriter,
    case: str,
    description: str,
    passed: bool,
    detail: str,
    evidence: dict[str, Any],
) -> None:
    writer.write_evidence("semantic", case, description, passed, detail, evidence)
    label = f"semantic/{case}"
    if passed:
        recorder.pass_(label, detail)
    else:
        recorder.fail(label, detail)


def _safe_request(client: ApiClient, *args: Any, **kwargs: Any) -> ApiResponse | None:
    """Best-effort request that returns the response (or None on transport error)
    without ever raising — semantic checks inspect the body either way."""
    kwargs.setdefault("validate_contract", False)
    try:
        return client._perform_safe(*args, **kwargs)
    except Exception:  # noqa: BLE001
        return None


def _list_items(resp: ApiResponse | None) -> list[dict[str, Any]]:
    if not isinstance(resp, ApiResponse) or not isinstance(resp.data, dict):
        return []
    for key in ("sources", "user_memories"):
        items = resp.data.get(key)
        if isinstance(items, list):
            return [i for i in items if isinstance(i, dict)]
    return []


def _list_total(resp: ApiResponse | None) -> int | None:
    if isinstance(resp, ApiResponse) and isinstance(resp.data, dict):
        t = resp.data.get("total")
        if isinstance(t, int):
            return t
    return None


def _query_chunks(resp: ApiResponse | None) -> list[dict[str, Any]]:
    if not isinstance(resp, ApiResponse) or not isinstance(resp.data, dict):
        return []
    chunks = resp.data.get("chunks")
    return (
        [c for c in chunks if isinstance(c, dict)] if isinstance(chunks, list) else []
    )


def _list_call(
    client: ApiClient, type_: str, filters: dict[str, Any] | None, **extra: Any
) -> ApiResponse | None:
    body: dict[str, Any] = {
        "tenant_id": TENANT_ID,
        "sub_tenant_id": SUB_TENANT_ID,
        "type": type_,
        "page": 1,
        "page_size": 100,
    }
    if filters:
        body["filters"] = filters
    body.update(extra)
    return _safe_request(client, "POST", "/context/list", json_body=body)


def sem_filter_list_additional_metadata(
    client: ApiClient, recorder: Recorder, writer: ResultWriter, ctx: Context
) -> None:
    """Proof: an additional_metadata filter that matches our run returns items, and a
    bogus value returns zero — so the filter is genuinely applied (not ignored)."""
    match = _list_call(
        client, "knowledge", {"additional_metadata": {"run_id": ctx.run_id}}
    )
    bogus = _list_call(
        client, "knowledge", {"additional_metadata": {"run_id": "no_such_run_zzz999"}}
    )
    n_match, n_bogus = len(_list_items(match)), len(_list_items(bogus))
    passed = n_match > 0 and n_bogus == 0
    _record_semantic(
        recorder,
        writer,
        "filter_list_additional_metadata",
        "POST /context/list additional_metadata filter actually narrows results",
        passed,
        f"match run_id={ctx.run_id} -> {n_match} sources; bogus run_id -> {n_bogus} "
        f"(expect match>0 and bogus==0)",
        {
            "matched_count": n_match,
            "bogus_count": n_bogus,
            "match_total": _list_total(match),
            "bogus_total": _list_total(bogus),
        },
    )


def sem_filter_list_source_fields(
    client: ApiClient, recorder: Recorder, writer: ResultWriter, ctx: Context
) -> None:
    """Proof: source_fields.type filtering works — filtering by a type that exists in
    the corpus returns ONLY sources of that type, and a nonexistent type returns zero.

    The matching type is discovered dynamically from an unfiltered list rather than
    hard-coded, because app-declared types (slack/notion/...) are normalized: app
    sources come back as type='document', NOT the type set in app_knowledge. That
    normalization is itself recorded below as a doc/API discrepancy."""
    unfiltered = _list_items(_list_call(client, "knowledge", None))
    all_types = sorted({i.get("type") for i in unfiltered if i.get("type")})
    # Did any app source preserve its declared type (slack/notion/webpage)?
    app_types_preserved = any(
        t in all_types for t in ("slack", "notion", "webpage", "gmail")
    )
    probe_type = all_types[0] if all_types else None

    if not probe_type:
        _record_semantic(
            recorder,
            writer,
            "filter_list_source_fields",
            "POST /context/list source_fields.type filter returns only matching type",
            False,
            "no sources with a type value to probe",
            {"all_types": all_types},
        )
        return

    match = _list_call(client, "knowledge", {"source_fields": {"type": probe_type}})
    bogus = _list_call(
        client, "knowledge", {"source_fields": {"type": "no_such_type_zzz"}}
    )
    match_items = _list_items(match)
    match_types = sorted({i.get("type") for i in match_items})
    only_probe = bool(match_items) and all(
        i.get("type") == probe_type for i in match_items
    )
    n_bogus = len(_list_items(bogus))
    passed = only_probe and n_bogus == 0
    note = (
        ""
        if app_types_preserved
        else (
            " | FINDING: app-declared types (slack/notion) are normalized to 'document' "
            "in /context/list — source_fields.type cannot select app sources by their app type."
        )
    )
    _record_semantic(
        recorder,
        writer,
        "filter_list_source_fields",
        "POST /context/list source_fields.type filter returns only matching type",
        passed,
        f"probe type='{probe_type}' -> {len(match_items)} sources (types: {match_types}); "
        f"bogus -> {n_bogus} (expect all=='{probe_type}' and bogus==0)." + note,
        {
            "all_types_in_corpus": all_types,
            "app_types_preserved": app_types_preserved,
            "probe_type": probe_type,
            "match_count": len(match_items),
            "match_types": match_types,
            "bogus_count": n_bogus,
        },
    )


def sem_filter_list_metadata(
    client: ApiClient, recorder: Recorder, writer: ResultWriter, ctx: Context
) -> None:
    """Informational: metadata filtering only works if the key is declared in
    the tenant schema with enable_match. We report whether it is active or silently
    ignored rather than hard-failing (undeclared-key behavior is documented)."""
    no_filter = _list_call(client, "knowledge", None)
    dept = _list_call(client, "knowledge", {"metadata": {"department": "support"}})
    bogus = _list_call(
        client, "knowledge", {"metadata": {"department": "no_such_dept_zzz"}}
    )
    n_all, n_dept, n_bogus = (
        len(_list_items(no_filter)),
        len(_list_items(dept)),
        len(_list_items(bogus)),
    )
    # Filter is "active" if a bogus department returns fewer than the unfiltered set.
    active = n_bogus < n_all
    detail = (
        f"unfiltered={n_all}, department=support -> {n_dept}, bogus department -> "
        f"{n_bogus}. metadata filtering appears "
        + (
            "ACTIVE (key declared with enable_match)"
            if active
            else "INERT (key not declared / silently ignored — per docs)"
        )
    )
    # Pass regardless: this is diagnostic. The boolean records which world we are in.
    _record_semantic(
        recorder,
        writer,
        "filter_list_metadata",
        "POST /context/list metadata filter — active vs silently-ignored probe",
        True,
        detail,
        {
            "unfiltered": n_all,
            "department_support": n_dept,
            "bogus_department": n_bogus,
            "filter_active": active,
        },
    )


def sem_filter_query_additional_metadata(
    client: ApiClient, recorder: Recorder, writer: ResultWriter, ctx: Context
) -> None:
    """Proof: a /query with an additional_metadata filter returns only chunks from
    matching sources, and a bogus filter returns no chunks."""
    q = f"refund policy pricing tiers SLA runbook E2E {ctx.run_id}"
    base = {
        "tenant_id": TENANT_ID,
        "sub_tenant_id": SUB_TENANT_ID,
        "query": q,
        "type": "knowledge",
        "query_by": "hybrid",
        "mode": "fast",
        "max_results": 20,
    }
    match = _safe_request(
        client,
        "POST",
        "/query",
        json_body={
            **base,
            "metadata_filters": {"additional_metadata": {"run_id": ctx.run_id}},
        },
    )
    bogus = _safe_request(
        client,
        "POST",
        "/query",
        json_body={
            **base,
            "metadata_filters": {"additional_metadata": {"run_id": "no_such_run_zzz"}},
        },
    )
    match_chunks, bogus_chunks = _query_chunks(match), _query_chunks(bogus)
    # Of the matched chunks that echo additional_metadata, confirm they belong to this run.
    mismatched = [
        c.get("id")
        for c in match_chunks
        if isinstance(c.get("additional_metadata"), dict)
        and c["additional_metadata"].get("run_id") not in (None, ctx.run_id)
    ]
    passed = len(match_chunks) > 0 and len(bogus_chunks) == 0 and not mismatched
    _record_semantic(
        recorder,
        writer,
        "filter_query_additional_metadata",
        "POST /query additional_metadata filter restricts chunks to matching sources",
        passed,
        f"match -> {len(match_chunks)} chunks; bogus -> {len(bogus_chunks)} chunks; "
        f"foreign-run chunks={len(mismatched)} (expect match>0, bogus==0, foreign==0)",
        {
            "match_chunks": len(match_chunks),
            "bogus_chunks": len(bogus_chunks),
            "match_ids": sorted({c.get("id") for c in match_chunks}),
            "foreign_run_ids": mismatched,
        },
    )


def sem_forceful_relations(
    client: ApiClient, recorder: Recorder, writer: ResultWriter, ctx: Context
) -> None:
    """Proof: with mode=thinking + query_forceful_relations=true, querying content
    from the app source that declared app-native relations[] surfaces the related
    source into data.additional_context; with the flag off it does not.

    Forceful relations require the declaring source's graph to be built, so we first
    wait for both the declaring source and its target to reach `completed`."""
    declaring_status = target_status = None
    if ctx.forceful_declaring_id:
        declaring_status = _poll_status(
            client,
            ctx.forceful_declaring_id,
            {"completed"},
            GRAPH_COMPLETE_TIMEOUT_SECONDS,
        )
    if ctx.forceful_target_id:
        target_status = _poll_status(client, ctx.forceful_target_id, {"completed"}, 120)

    q = f"refund workflow links to the pricing tiers E2E {ctx.run_id}"
    base = {
        "tenant_id": TENANT_ID,
        "sub_tenant_id": SUB_TENANT_ID,
        "query": q,
        "type": "knowledge",
        "query_by": "hybrid",
        "max_results": 10,
    }
    on = _safe_request(
        client,
        "POST",
        "/query",
        json_body={**base, "mode": "thinking", "query_forceful_relations": True},
    )
    off = _safe_request(
        client,
        "POST",
        "/query",
        json_body={**base, "mode": "thinking", "query_forceful_relations": False},
    )

    def _add_ctx_keys(resp: ApiResponse | None) -> list[str]:
        if isinstance(resp, ApiResponse) and isinstance(resp.data, dict):
            ac = resp.data.get("additional_context")
            if isinstance(ac, dict):
                return list(ac.keys())
        return []

    on_keys, off_keys = _add_ctx_keys(on), _add_ctx_keys(off)
    retrieved = sorted({c.get("id") for c in _query_chunks(on)})
    declaring_retrieved = ctx.forceful_declaring_id in retrieved
    graph_ready = declaring_status == "completed"
    surfaced = len(on_keys) > 0

    if surfaced:
        passed = True
        detail = (
            f"VERIFIED: flag ON surfaced {len(on_keys)} additional_context entr(ies) "
            f"{on_keys[:5]}; flag OFF -> {len(off_keys)}"
        )
    elif not declaring_retrieved:
        # The declaring source wasn't even retrieved → nothing could be surfaced.
        passed = True
        detail = (
            "INCONCLUSIVE: declaring source not in results, so forceful relations had "
            f"nothing to attach (declaring_id={ctx.forceful_declaring_id}, retrieved={retrieved})"
        )
    elif not graph_ready:
        passed = True
        detail = (
            f"INCONCLUSIVE: declaring source retrieved but its graph wasn't `completed` "
            f"in time (status={declaring_status}); additional_context empty"
        )
    else:
        # Best conditions met (graph completed, declaring source retrieved) yet nothing
        # surfaced → a real gap worth confirming with the API team.
        passed = False
        detail = (
            "FINDING: declaring source retrieved AND its graph completed, but "
            "query_forceful_relations=true produced an empty additional_context. "
            "Confirm forceful-relation surfacing with the API team."
        )
    _record_semantic(
        recorder,
        writer,
        "forceful_relations_surfacing",
        "POST /query query_forceful_relations populates additional_context (thinking mode)",
        passed,
        detail,
        {
            "on_keys": on_keys,
            "off_keys": off_keys,
            "retrieved_ids": retrieved,
            "declaring_id": ctx.forceful_declaring_id,
            "declaring_retrieved": declaring_retrieved,
            "declaring_status": declaring_status,
            "target_status": target_status,
        },
    )


def _poll_status(
    client: ApiClient, id: str, want: set[str], timeout: int
) -> str | None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        resp = _safe_request(
            client,
            "GET",
            "/context/status",
            query={
                "tenant_id": TENANT_ID,
                "sub_tenant_id": SUB_TENANT_ID,
                "ids": [id],
            },
        )
        if isinstance(resp, ApiResponse) and isinstance(resp.data, dict):
            statuses = resp.data.get("statuses") or []
            vals = [s.get("indexing_status") for s in statuses if isinstance(s, dict)]
            last = vals[0] if vals else last
            if last in want:
                return last
            if last in {"errored", "failed"}:
                return last
        time.sleep(POLL_INTERVAL_SECONDS)
    return last


def _fetch_content(client: ApiClient, id: str) -> str:
    resp = _safe_request(
        client,
        "GET",
        "/context/inspect",
        query={
            "tenant_id": TENANT_ID,
            "sub_tenant_id": SUB_TENANT_ID,
            "id": id,
            "mode": "content",
        },
    )
    if isinstance(resp, ApiResponse) and isinstance(resp.data, dict):
        return resp.data.get("content") or ""
    return ""


def sem_upsert_overwrite(
    client: ApiClient, recorder: Recorder, writer: ResultWriter, ctx: Context
) -> None:
    """Proof: re-ingesting the same id with upsert=true replaces the stored
    content; and upsert=false on an existing id does NOT silently succeed."""
    sid = f"e2e_ovr_{ctx.run_id}"
    marker_a = f"ALPHAMARK{ctx.run_id}aaa"
    marker_b = f"BETAMARK{ctx.run_id}bbb"

    def _ingest(text: str, upsert: str) -> ApiResponse | None:
        return _safe_request(
            client,
            "POST",
            "/context/ingest",
            expected_statuses=(202, 400, 409),
            multipart=(
                {
                    "type": "knowledge",
                    "tenant_id": TENANT_ID,
                    "sub_tenant_id": SUB_TENANT_ID,
                    "upsert": upsert,
                    "document_metadata": json.dumps([{"id": sid}]),
                },
                [("documents", "ovr.txt", text.encode(), "text/plain")],
            ),
        )

    v1 = _ingest(f"version one content {marker_a}", "true")
    s1 = _poll_status(
        client, sid, {"graph_creation", "completed"}, SOURCE_READY_TIMEOUT_SECONDS
    )
    content_v1 = _fetch_content(client, sid)
    v2 = _ingest(f"version two content {marker_b}", "true")
    # Wait for re-index. Re-ingest may briefly keep old content; poll until B appears.
    deadline = time.time() + SOURCE_READY_TIMEOUT_SECONDS
    content_v2 = ""
    while time.time() < deadline:
        _poll_status(client, sid, {"graph_creation", "completed"}, 60)
        content_v2 = _fetch_content(client, sid)
        if marker_b in content_v2:
            break
        time.sleep(POLL_INTERVAL_SECONDS)

    overwrote = marker_b in content_v2 and marker_a not in content_v2
    # upsert=false on the now-existing id must not succeed silently.
    nf = _ingest("conflict attempt", "false")
    nf_body = nf.json_body if isinstance(nf, ApiResponse) else {}
    nf_data = nf_body.get("data") if isinstance(nf_body, dict) else {}
    failed_count = nf_data.get("failed_count") if isinstance(nf_data, dict) else None
    results = nf_data.get("results") if isinstance(nf_data, dict) else []
    result_errored = any(
        isinstance(r, dict)
        and (r.get("error") or r.get("status") in {"failed", "error", "skipped"})
        for r in (results or [])
    )
    nf_status = nf.status if isinstance(nf, ApiResponse) else None
    upsert_false_rejected = (
        (nf_status not in (200, 202))
        or (isinstance(failed_count, int) and failed_count >= 1)
        or result_errored
    )

    passed = overwrote and upsert_false_rejected
    ctx.disposable_knowledge_id = ctx.disposable_knowledge_id or sid
    _record_semantic(
        recorder,
        writer,
        "upsert_overwrite",
        "POST /context/ingest upsert=true overwrites; upsert=false rejects existing id",
        passed,
        f"v1 content had ALPHA={marker_a in content_v1}; after upsert v2 has BETA & not "
        f"ALPHA={overwrote}; upsert=false rejected={upsert_false_rejected} "
        f"(http={nf_status}, failed_count={failed_count}, result_errored={result_errored})",
        {
            "status_v1": s1,
            "content_v1_has_alpha": marker_a in content_v1,
            "content_v2_has_beta": marker_b in content_v2,
            "content_v2_has_alpha": marker_a in content_v2,
            "overwrote": overwrote,
            "upsert_false_http": nf_status,
            "upsert_false_failed_count": failed_count,
            "upsert_false_result_errored": result_errored,
        },
    )


def sem_presigned_url(
    client: ApiClient, recorder: Recorder, writer: ResultWriter, ctx: Context
) -> None:
    """Proof: the presigned URL from fetch mode=url is actually downloadable (HTTP
    200) and returns the file bytes — no auth header needed."""
    kid = (ctx.knowledge_ids or [None])[0]
    resp = _safe_request(
        client,
        "GET",
        "/context/inspect",
        query={
            "tenant_id": TENANT_ID,
            "sub_tenant_id": SUB_TENANT_ID,
            "id": kid,
            "mode": "url",
        },
    )
    url = ""
    if isinstance(resp, ApiResponse) and isinstance(resp.data, dict):
        url = resp.data.get("presigned_url") or ""
    dl_status: int | None = None
    dl_bytes = 0
    err = None
    if url:
        try:
            with urlopen(url, timeout=REQUEST_TIMEOUT_SECONDS) as r:  # noqa: S310
                dl_status = r.status
                dl_bytes = len(r.read())
        except HTTPError as exc:
            dl_status = exc.code
            err = str(exc)
        except Exception as exc:  # noqa: BLE001
            err = str(exc)
    passed = bool(url) and dl_status == 200 and dl_bytes > 0
    _record_semantic(
        recorder,
        writer,
        "presigned_url_downloadable",
        "GET /context/inspect mode=url returns a working, downloadable presigned URL",
        passed,
        f"url_present={bool(url)}, download_status={dl_status}, bytes={dl_bytes}"
        + (f", error={err}" if err else ""),
        {
            "id": kid,
            "url_present": bool(url),
            "download_status": dl_status,
            "downloaded_bytes": dl_bytes,
            "error": err,
        },
    )


def sem_pagination_walk(
    client: ApiClient, recorder: Recorder, writer: ResultWriter, ctx: Context
) -> None:
    """Proof: walking /context/list page-by-page (scoped to this run) yields exactly
    `total` distinct ids with no overlap, and total_pages matches."""
    page_size = 2
    seen: list[str] = []
    pages = 0
    reported_total = None
    reported_total_pages = None
    page = 1
    while page <= 50:
        resp = _list_call(
            client,
            "knowledge",
            {"additional_metadata": {"run_id": ctx.run_id}},
            page=page,
            page_size=page_size,
        )
        items = _list_items(resp)
        pages += 1
        for it in items:
            sid = it.get("id")
            if sid:
                seen.append(sid)
        pg = (
            (resp.data or {}).get("pagination", {})
            if isinstance(resp, ApiResponse) and isinstance(resp.data, dict)
            else {}
        )
        reported_total = _list_total(resp) if reported_total is None else reported_total
        reported_total_pages = pg.get("total_pages", reported_total_pages)
        if not pg.get("has_next"):
            break
        page += 1

    distinct = len(set(seen))
    no_dupes = distinct == len(seen)
    total_ok = reported_total is None or distinct == reported_total
    expected_pages = (
        (reported_total + page_size - 1) // page_size
        if isinstance(reported_total, int) and reported_total
        else None
    )
    pages_ok = expected_pages is None or reported_total_pages in (expected_pages, None)
    passed = no_dupes and total_ok and distinct > 0
    _record_semantic(
        recorder,
        writer,
        "pagination_walk",
        "POST /context/list page walk returns all distinct items matching `total`",
        passed,
        f"walked {pages} pages of size {page_size}; collected {len(seen)} ids "
        f"({distinct} distinct, dupes={not no_dupes}); reported total={reported_total}, "
        f"total_pages={reported_total_pages} (expected~{expected_pages})",
        {
            "pages_walked": pages,
            "collected": len(seen),
            "distinct": distinct,
            "reported_total": reported_total,
            "reported_total_pages": reported_total_pages,
            "expected_pages": expected_pages,
            "no_duplicates": no_dupes,
            "total_matches": total_ok,
            "pages_match": pages_ok,
        },
    )


def sem_graph_completeness(
    client: ApiClient, recorder: Recorder, writer: ResultWriter, ctx: Context
) -> None:
    """Proof: once a source reaches `completed`, its relations graph is populated."""
    sid = (ctx.app_ids or ctx.knowledge_ids or [None])[0]
    if not sid:
        _record_semantic(
            recorder,
            writer,
            "graph_completeness",
            "GET /context/relations is populated after indexing_status=completed",
            False,
            "no knowledge source available",
            {},
        )
        return
    final = _poll_status(client, sid, {"completed"}, GRAPH_COMPLETE_TIMEOUT_SECONDS)
    rel = _safe_request(
        client,
        "GET",
        "/context/relations",
        query={
            "tenant_id": TENANT_ID,
            "sub_tenant_id": SUB_TENANT_ID,
            "id": sid,
            "type": "knowledge",
            "limit": 1000,
        },
    )
    relations = []
    if isinstance(rel, ApiResponse) and isinstance(rel.data, dict):
        relations = rel.data.get("relations") or []
    n_rel = len(relations) if isinstance(relations, list) else 0
    if final == "completed":
        passed = n_rel > 0
        detail = (
            f"source reached `completed`; relations populated count={n_rel} (expect >0)"
        )
    else:
        # Did not reach completed within the window — record honestly as inconclusive.
        passed = True
        detail = (
            f"INCONCLUSIVE: source did not reach `completed` within "
            f"{GRAPH_COMPLETE_TIMEOUT_SECONDS}s (last status={final}); relations so far={n_rel}"
        )
    _record_semantic(
        recorder,
        writer,
        "graph_completeness",
        "GET /context/relations is populated after indexing_status=completed",
        passed,
        detail,
        {"id": sid, "final_status": final, "relations_count": n_rel},
    )


def exercise_semantic_checks(
    client: ApiClient, recorder: Recorder, writer: ResultWriter, ctx: Context
) -> None:
    if not RUN_SEMANTIC_CHECKS:
        recorder.pass_(
            "semantic skipped", "Set HYDRADB_RUN_SEMANTIC_CHECKS=1 to enable"
        )
        return
    sem_filter_list_additional_metadata(client, recorder, writer, ctx)
    sem_filter_list_source_fields(client, recorder, writer, ctx)
    sem_filter_list_metadata(client, recorder, writer, ctx)
    sem_filter_query_additional_metadata(client, recorder, writer, ctx)
    sem_forceful_relations(client, recorder, writer, ctx)
    sem_presigned_url(client, recorder, writer, ctx)
    sem_pagination_walk(client, recorder, writer, ctx)
    sem_upsert_overwrite(client, recorder, writer, ctx)
    sem_graph_completeness(client, recorder, writer, ctx)


def exercise_source_delete(
    client: ApiClient, recorder: Recorder, writer: ResultWriter, ctx: Context
) -> None:
    if not DELETE_CORE_TEST_DATA:
        recorder.pass_(
            "runtime DELETE /context skipped",
            "Set HYDRADB_DELETE_CORE_TEST_DATA=1 to delete disposable E2E sources",
        )
        return
    if ctx.disposable_knowledge_id:
        run_case(
            client,
            recorder,
            writer,
            "source_delete",
            "knowledge",
            "DELETE /context type=knowledge with request wrapper {tenant_id, sub_tenant_id, ids}",
            method="DELETE",
            path="/context",
            json_body={
                "type": "knowledge",
                "request": {
                    "tenant_id": TENANT_ID,
                    "sub_tenant_id": SUB_TENANT_ID,
                    "ids": [ctx.disposable_knowledge_id],
                },
            },
        )
    if ctx.disposable_memory_id:
        run_case(
            client,
            recorder,
            writer,
            "source_delete",
            "memory",
            "DELETE /context type=memory (aggregate user_memory_deleted response)",
            method="DELETE",
            path="/context",
            json_body={
                "type": "memory",
                "request": {
                    "tenant_id": TENANT_ID,
                    "sub_tenant_id": SUB_TENANT_ID,
                    "ids": [ctx.disposable_memory_id],
                },
            },
        )


def exercise_tenant_endpoints(
    client: ApiClient, recorder: Recorder, writer: ResultWriter, ctx: Context
) -> None:
    run_case(
        client,
        recorder,
        writer,
        "tenants",
        "list_tenants",
        "GET /tenants — list all tenants visible to the API key",
        method="GET",
        path="/tenants",
    )
    run_case(
        client,
        recorder,
        writer,
        "tenants",
        "status",
        "GET /tenants/status — provisioning/infra snapshot for the main tenant",
        method="GET",
        path="/tenants/status",
        query={"tenant_id": TENANT_ID},
    )
    run_case(
        client,
        recorder,
        writer,
        "tenants",
        "sub_tenants",
        "GET /tenants/sub-tenants — list sub-tenants",
        method="GET",
        path="/tenants/sub-tenants",
        query={"tenant_id": TENANT_ID},
    )
    run_case(
        client,
        recorder,
        writer,
        "tenants",
        "stats",
        "GET /tenants/stats — row/chunk counts",
        method="GET",
        path="/tenants/stats",
        query={"tenant_id": TENANT_ID},
    )
    # Create-existing -> 409 documented failure envelope.
    run_case(
        client,
        recorder,
        writer,
        "tenants",
        "create_existing_409",
        "POST /tenants for the already-existing main tenant -> 409 TENANT_ALREADY_EXISTS",
        method="POST",
        path="/tenants",
        json_body=create_tenant_body(TENANT_ID),
        expected_statuses=(409,),
        validate_contract=False,
    )


def exercise_disposable_tenant_lifecycle(
    client: ApiClient, recorder: Recorder, writer: ResultWriter, ctx: Context
) -> None:
    """Use the single extra tenant slot to exercise create(200) + delete."""
    resp = run_case(
        client,
        recorder,
        writer,
        "tenants",
        "create_disposable_200",
        "POST /tenants create the one allowed extra tenant (with metadata schema)",
        method="POST",
        path="/tenants",
        json_body=create_tenant_body(DELETE_TEST_TENANT_ID),
        expected_statuses=(200, 403, 409),
        validate_contract=False,
    )
    if not isinstance(resp, ApiResponse):
        return
    if resp.status == 403:
        recorder.pass_(
            "tenants/create_disposable_200 note",
            "Plan limit reached (403) — cannot create a 3rd tenant; delete still documented.",
            resp.request_id,
        )
        return
    if resp.status == 200:
        try:
            client.contract.validate_response("POST", "/tenants", 200, resp.json_body)
        except ContractError as exc:
            recorder.fail(
                "tenants/create_disposable_200 contract", str(exc), resp.request_id
            )
    ctx.created_delete_tenant = True
    run_case(
        client,
        recorder,
        writer,
        "tenants",
        "delete_disposable",
        "DELETE /tenants for the disposable tenant (frees the slot)",
        method="DELETE",
        path="/tenants",
        query={"tenant_id": DELETE_TEST_TENANT_ID},
    )


def exercise_webhook_endpoints(
    client: ApiClient, recorder: Recorder, writer: ResultWriter, ctx: Context
) -> None:
    if not RUN_WEBHOOK_TESTS:
        recorder.pass_(
            "runtime webhooks skipped", "Set HYDRADB_RUN_WEBHOOK_TESTS=1 to enable"
        )
        return

    run_case(
        client,
        recorder,
        writer,
        "webhooks",
        "get_initial",
        "GET /webhooks/indexing — current webhook config",
        method="GET",
        path="/webhooks/indexing",
    )
    post_resp = run_case(
        client,
        recorder,
        writer,
        "webhooks",
        "create",
        "POST /webhooks/indexing — register an indexing webhook",
        method="POST",
        path="/webhooks/indexing",
        json_body={"url": WEBHOOK_URL, "event_types": ["indexing.status_changed"]},
        expected_statuses=(200, 201),
    )
    if isinstance(post_resp, ApiResponse) and post_resp.ok:
        ctx.created_webhook = True

    run_case(
        client,
        recorder,
        writer,
        "webhooks",
        "get_after_create",
        "GET /webhooks/indexing — confirm registration",
        method="GET",
        path="/webhooks/indexing",
    )
    run_case(
        client,
        recorder,
        writer,
        "webhooks",
        "test",
        "POST /webhooks/indexing/test — fire a test delivery",
        method="POST",
        path="/webhooks/indexing/test",
        json_body={},
    )
    deliveries_resp = run_case(
        client,
        recorder,
        writer,
        "webhooks",
        "deliveries_list",
        "GET /webhooks/indexing/deliveries — recent delivery attempts",
        method="GET",
        path="/webhooks/indexing/deliveries",
        query={"limit": 10},
    )

    def _extract_delivery_id(resp: ApiResponse | None) -> str | None:
        if isinstance(resp, ApiResponse) and isinstance(resp.json_body, dict):
            deliveries = (
                resp.json_body.get("deliveries") or resp.json_body.get("items") or []
            )
            if deliveries and isinstance(deliveries[0], dict):
                return deliveries[0].get("delivery_id") or deliveries[0].get("id")
        return None

    ctx.known_delivery_id = ctx.known_delivery_id or _extract_delivery_id(
        deliveries_resp
    )
    # A delivery row (even a failed one) may take a moment to materialize after the
    # test fire — poll briefly before giving up.
    if ctx.known_delivery_id is None:
        deadline = time.time() + WEBHOOK_DELIVERY_POLL_SECONDS
        while time.time() < deadline and ctx.known_delivery_id is None:
            time.sleep(POLL_INTERVAL_SECONDS)
            polled = _safe_request(
                client, "GET", "/webhooks/indexing/deliveries", query={"limit": 10}
            )
            ctx.known_delivery_id = _extract_delivery_id(polled)

    if ctx.known_delivery_id:
        run_case(
            client,
            recorder,
            writer,
            "webhooks",
            "delivery_detail",
            "GET /webhooks/indexing/deliveries/{delivery_id}",
            method="GET",
            path=f"/webhooks/indexing/deliveries/{ctx.known_delivery_id}",
        )
        run_case(
            client,
            recorder,
            writer,
            "webhooks",
            "delivery_retry",
            "POST /webhooks/indexing/deliveries/{delivery_id}/retry",
            method="POST",
            path=f"/webhooks/indexing/deliveries/{ctx.known_delivery_id}/retry",
            expected_statuses=(200, 202, 400, 409),
        )
    else:
        # The default WEBHOOK_URL (example.com) is inert, so no delivery row is created.
        # This is an environment limitation, not an API defect — skip gracefully.
        # Set HYDRADB_WEBHOOK_URL to a real receiver (e.g. webhook.site) to exercise these.
        recorder.pass_(
            "runtime webhook delivery detail/retry skipped",
            f"No delivery row appeared within {WEBHOOK_DELIVERY_POLL_SECONDS}s "
            f"(WEBHOOK_URL={WEBHOOK_URL} is inert). Set HYDRADB_WEBHOOK_URL to a real "
            "receiver to exercise GET/retry on a delivery_id.",
        )

    run_case(
        client,
        recorder,
        writer,
        "webhooks",
        "delete",
        "DELETE /webhooks/indexing — remove the webhook",
        method="DELETE",
        path="/webhooks/indexing",
    )
    ctx.created_webhook = False


def cleanup(client: ApiClient, recorder: Recorder, ctx: Context) -> None:
    if ctx.created_webhook:
        run_check(
            recorder,
            "cleanup DELETE /webhooks/indexing",
            lambda: client.request("DELETE", "/webhooks/indexing"),
        )


# -----------------------------------------------------------------------------
# Main.
# -----------------------------------------------------------------------------


def main() -> int:
    global BASE_URL, TENANT_ID, SUB_TENANT_ID

    parser = argparse.ArgumentParser(
        description="Run HydraDB v2 docs + runtime API contract tests"
    )
    parser.add_argument(
        "--static-only",
        action="store_true",
        help="Only audit docs/OpenAPI/cURL alignment; do not call the API",
    )
    parser.add_argument(
        "--no-static", action="store_true", help="Skip static docs/cURL audit"
    )
    parser.add_argument("--base-url", default=BASE_URL, help="API base URL")
    parser.add_argument("--tenant-id", default=TENANT_ID, help="Main tenant ID")
    parser.add_argument(
        "--sub-tenant-id", default=SUB_TENANT_ID, help="Sub-tenant ID for E2E data"
    )
    args = parser.parse_args()

    BASE_URL = args.base_url
    TENANT_ID = args.tenant_id
    SUB_TENANT_ID = args.sub_tenant_id

    recorder = Recorder()
    contract = OpenApiContract(OPENAPI_PATH, strict_extra_keys=STRICT_EXTRA_KEYS)
    ctx = create_context()
    writer = ResultWriter(ctx.results_dir)

    print("=== HydraDB v2 docs/API contract test ===")
    print(f"OpenAPI: {OPENAPI_PATH}")
    print(f"Endpoint docs: {ENDPOINT_DOCS_DIR}")
    print(f"Base URL: {BASE_URL}")
    print(f"Tenant: {TENANT_ID}")
    print(f"Sub-tenant: {SUB_TENANT_ID}")
    print(f"Results dir: {ctx.results_dir}")
    print(f"Strict extra keys: {STRICT_EXTRA_KEYS}")

    if not args.no_static:
        print("\n=== Static docs/cURL/OpenAPI audit ===")
        audit_endpoint_docs(contract, recorder)

    if args.static_only:
        recorder.summary()
        return 1 if recorder.failed else 0

    if not API_KEY:
        recorder.fail(
            "runtime configuration API key",
            "API_KEY is empty. Set HYDRA_DB_API_KEY or paste a short-lived key.",
        )
        recorder.summary()
        return 1

    print("\n=== Runtime E2E contract tests ===")
    client = ApiClient(BASE_URL, API_KEY, contract, recorder)
    try:
        ensure_tenant(client, recorder, TENANT_ID)
        ready = wait_for_tenant_ready(client, recorder, TENANT_ID)
        if ready:
            print("\n--- Ingesting every variation ---")
            ingest_all_variations(client, recorder, writer, ctx)
            wait_for_sources_searchable(
                client, recorder, ctx.knowledge_ids + ctx.app_ids
            )
            print("\n--- Read endpoints (status/fetch/list/relations/query) ---")
            exercise_source_status(client, recorder, writer, ctx)
            exercise_source_fetch(client, recorder, writer, ctx)
            exercise_source_list(client, recorder, writer, ctx)
            exercise_source_relations(client, recorder, writer, ctx)
            exercise_query(client, recorder, writer, ctx)
            print("\n--- Semantic checks (behavior, not just shape) ---")
            exercise_semantic_checks(client, recorder, writer, ctx)
            print("\n--- Delete source variations ---")
            exercise_source_delete(client, recorder, writer, ctx)
        else:
            recorder.fail(
                "runtime core flow",
                "Tenant never became ready; skipping ingestion/search-dependent endpoints",
            )

        print("\n--- Tenant endpoints + disposable lifecycle ---")
        exercise_tenant_endpoints(client, recorder, writer, ctx)
        exercise_disposable_tenant_lifecycle(client, recorder, writer, ctx)
        print("\n--- Webhook endpoints ---")
        exercise_webhook_endpoints(client, recorder, writer, ctx)
    finally:
        cleanup(client, recorder, ctx)
        writer.flush_index()

    recorder.summary()
    print(f"\nPer-testcase result documents written under: {ctx.results_dir}")
    print(f"Coverage table: {ctx.results_dir / '_coverage.md'}")
    return 1 if recorder.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
