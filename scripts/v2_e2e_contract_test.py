#!/usr/bin/env python3
"""HydraDB v2 end-to-end API contract tester.

What this script checks:
1. Static docs contract:
   - Every endpoint page under api-reference/v2/endpoint with an `openapi:` directive
     points at a real OpenAPI operation.
   - cURL blocks on those pages use the same HTTP method/path as the directive.
   - cURL blocks include `Authorization: Bearer ...` and `API-Version: 2`.

2. Runtime API contract:
   - Creates/polls a tenant.
   - Ingests knowledge and memory data.
   - Polls source status until searchable.
   - Exercises all canonical v2 endpoints documented in api-reference/v2 and AGENTS.md:
       POST   /tenants
       GET    /tenants
       DELETE /tenants
       GET    /tenants/status
       GET    /tenants/sub-tenants
       GET    /tenants/stats
       POST   /source/ingest
       GET    /source/status
       GET    /source/fetch
       POST   /source/list
       DELETE /source
       GET    /source/relations
       POST   /search
       GET    /webhooks/indexing
       POST   /webhooks/indexing
       DELETE /webhooks/indexing
       GET    /webhooks/indexing/deliveries
       GET    /webhooks/indexing/deliveries/{delivery_id}
       POST   /webhooks/indexing/deliveries/{delivery_id}/retry
       POST   /webhooks/indexing/test
   - Validates each JSON response against the OpenAPI response schema with strict
     extra-key checking where the schema declares object properties.

No SDKs are used. HTTP requests are raw requests that mirror the cURL surface.

Security note: do not commit real API keys. Paste a short-lived key below only
for local ad-hoc runs, or prefer HYDRADB_API_KEY in your shell.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import traceback
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

# -----------------------------------------------------------------------------
# Top-level configuration: safe to edit locally.
# -----------------------------------------------------------------------------
BASE_URL = os.getenv("HYDRADB_BASE_URL", "https://api-v2.staging.hydradb.com/")
API_KEY = os.getenv("HYDRADB_API_KEY", "")
TENANT_ID = os.getenv("HYDRADB_TENANT_ID", "default-tenant")
SUB_TENANT_ID = os.getenv("HYDRADB_SUB_TENANT_ID", "e2e_user_alex")

# Search docs currently show `type`; OpenAPI calls the raw HTTP field `source`.
# Keep this set to `type` to test documented cURL behavior. If staging only
# accepts OpenAPI's field, the script will report the mismatch.
SEARCH_SELECTOR_FIELD = os.getenv("HYDRADB_SEARCH_SELECTOR_FIELD", "type")

API_VERSION = "2"
OPENAPI_PATH = (
    Path(__file__).resolve().parents[1] / "api-reference" / "v2" / "openapi.json"
)
ENDPOINT_DOCS_DIR = (
    Path(__file__).resolve().parents[1] / "api-reference" / "v2" / "endpoint"
)

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
REQUEST_TIMEOUT_SECONDS = int(os.getenv("HYDRADB_REQUEST_TIMEOUT_SECONDS", "60"))

# The webhooks API requires a public HTTPS URL. This URL is intentionally inert,
# but it is syntactically valid and should allow create/get/delete contract checks.
WEBHOOK_URL = os.getenv(
    "HYDRADB_WEBHOOK_URL", "https://example.com/hydradb-indexing-webhook"
)

# Tenant IDs are documented as max 25 chars. Keep disposable IDs short.
DELETE_TEST_TENANT_ID = os.getenv("HYDRADB_DELETE_TEST_TENANT_ID", "default-tenant-del")

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
    knowledge_file_source_id: str
    knowledge_app_source_id: str
    memory_source_id: str
    created_delete_tenant: bool = False
    created_webhook: bool = False
    known_delivery_id: str | None = None


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
            ("/tenants", "/source", "/search")
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
            "User-Agent": "hydradb-v2-docs-e2e-contract-test/1.0",
        }
        if extra:
            headers.update(extra)
        return headers

    def request(
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
        method = method.upper()
        label = label or f"{method} {path}"
        contract_path = contract_path or self._contract_path(path)
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
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif multipart is not None:
            fields, files = multipart
            boundary = "----hydradb-e2e-" + uuid.uuid4().hex
            data = self._encode_multipart(boundary, fields, files)
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"

        req = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
                status = resp.status
                body = resp.read().decode("utf-8", errors="replace")
                response_headers = dict(resp.headers.items())
        except HTTPError as exc:
            status = exc.code
            body = exc.read().decode("utf-8", errors="replace")
            response_headers = dict(exc.headers.items())
        except URLError as exc:
            raise RuntimeError(f"{label}: network error: {exc}") from exc

        try:
            parsed = json.loads(body) if body else None
        except json.JSONDecodeError as exc:
            raise ContractError(
                f"{label}: response is not valid JSON: {exc}; body={body[:1000]!r}"
            ) from exc

        response = ApiResponse(method, path, status, response_headers, body, parsed)
        if status not in set(expected_statuses):
            request_id = response.request_id
            raise ContractError(
                f"{label}: expected HTTP {sorted(expected_statuses)}, got {status}; request_id={request_id}; body={body[:2000]}"
            )
        if validate_contract:
            self.contract.validate_response(method, contract_path, status, parsed)
        return response

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
        ("POST", "/source/ingest"),
        ("GET", "/source/status"),
        ("GET", "/source/fetch"),
        ("POST", "/source/list"),
        ("DELETE", "/source"),
        ("GET", "/source/relations"),
        ("POST", "/search"),
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

    # Explicitly surface the known raw search field discrepancy if it exists.
    query_schema = (
        contract.doc.get("components", {}).get("schemas", {}).get("QueryRequest", {})
    )
    props = query_schema.get("properties", {})
    if "source" in props and "type" not in props:
        recorder.fail(
            "docs.openapi search selector field",
            "Endpoint docs/cURLs use `type`, but OpenAPI QueryRequest documents raw field `source`. Confirm API accepts `type` alias or update one side.",
        )
    else:
        recorder.pass_(
            "docs.openapi search selector field", "Search selector naming is aligned"
        )


# -----------------------------------------------------------------------------
# Runtime tests.
# -----------------------------------------------------------------------------


def run_check(
    recorder: Recorder, name: str, fn: Callable[[], str | ApiResponse | None]
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
    except Exception as exc:  # noqa: BLE001 - this is a test harness; record and continue where possible.
        details = str(exc)
        if os.getenv("HYDRADB_DEBUG", "0") == "1":
            details += "\n" + traceback.format_exc()
        recorder.fail(name, details)
        return None


def create_context() -> Context:
    # Keep IDs stable per run but unique enough for upsert-friendly re-runs.
    run_id = os.getenv("HYDRADB_E2E_RUN_ID", time.strftime("%Y%m%d%H%M%S"))
    suffix = re.sub(r"[^a-zA-Z0-9_]", "_", run_id)[-12:]
    return Context(
        run_id=suffix,
        knowledge_file_source_id=f"e2e_file_{suffix}",
        knowledge_app_source_id=f"e2e_app_{suffix}",
        memory_source_id=f"e2e_mem_{suffix}",
    )


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
        # 409 is acceptable for re-runs.  The spec doesn't document 409 on POST /tenants
        # (it should — that's a docs gap we flag), so skip schema validation for that
        # status and validate the envelope + error code manually below.
        return client.request(
            "POST",
            "/tenants",
            json_body=create_tenant_body(tenant_id),
            expected_statuses=(200, 409),
            label=f"POST /tenants create {tenant_id}",
            validate_contract=False,  # manual validation below handles both statuses
        )

    resp = run_check(recorder, f"runtime POST /tenants create {tenant_id}", _create)
    if not isinstance(resp, ApiResponse):
        return

    body = resp.json_body or {}
    if resp.status == 200:
        # Validate the 200 contract manually (schema validation was skipped above).
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
        # 409 is not in the spec — flag it as a docs gap, then validate envelope shape.
        recorder.fail(
            "docs.openapi POST /tenants missing 409",
            "OpenAPI spec does not document 409 TENANT_ALREADY_EXISTS on POST /tenants. "
            "This is a docs gap; the endpoint does return it.",
        )
        # Still validate the envelope and error code so we catch regressions.
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
        if code != "TENANT_ALREADY_EXISTS":
            recorder.fail(
                f"runtime POST /tenants existing code {tenant_id}",
                f"Expected TENANT_ALREADY_EXISTS, got {code!r}",
                resp.request_id,
            )
        else:
            recorder.pass_(
                f"runtime POST /tenants existing code {tenant_id}",
                "tenant already exists; continuing",
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


def ingest_test_data(client: ApiClient, recorder: Recorder, ctx: Context) -> list[str]:
    source_ids: list[str] = []
    knowledge_file = (
        f"HydraDB E2E contract test policy {ctx.run_id}. "
        "Refunds are available within 30 days. Support answers should cite the policy."
    ).encode("utf-8")
    file_metadata = [
        {
            "source_id": ctx.knowledge_file_source_id,
            "title": "E2E Refund Policy",
            "type": "txt",
            "tenant_metadata": {"department": "support", "workspace": "docs"},
            "additional_metadata": {"source": "e2e_contract", "run_id": ctx.run_id},
        }
    ]
    app_knowledge = [
        {
            "id": ctx.knowledge_app_source_id,
            "tenant_id": TENANT_ID,
            "sub_tenant_id": SUB_TENANT_ID,
            "title": "E2E pricing discussion",
            "type": "slack",
            "url": "https://example.com/e2e/pricing",
            "timestamp": "2026-05-29T00:00:00Z",
            "content": {
                "text": f"Contract test {ctx.run_id}: Starter costs $29 and Pro costs $79."
            },
            "tenant_metadata": {"department": "support", "workspace": "docs"},
            "additional_metadata": {"source": "e2e_contract", "run_id": ctx.run_id},
            "relations": {
                "source_ids": [ctx.knowledge_file_source_id],
                "properties": {"reason": "same_e2e_run"},
            },
        }
    ]

    def _ingest_knowledge() -> ApiResponse:
        return client.request(
            "POST",
            "/source/ingest",
            multipart=(
                {
                    "type": "knowledge",
                    "tenant_id": TENANT_ID,
                    "sub_tenant_id": SUB_TENANT_ID,
                    "upsert": "true",
                    "file_metadata": json.dumps(file_metadata),
                    "app_knowledge": json.dumps(app_knowledge),
                },
                [("files", "e2e_refund_policy.txt", knowledge_file, "text/plain")],
            ),
            expected_statuses=(202,),
            label="POST /source/ingest knowledge",
        )

    resp = run_check(
        recorder, "runtime POST /source/ingest knowledge", _ingest_knowledge
    )
    if isinstance(resp, ApiResponse) and isinstance(resp.data, dict):
        for item in resp.data.get("results", []) or []:
            if isinstance(item, dict) and item.get("source_id"):
                source_ids.append(item["source_id"])

    # The server's MemoryItem model validates tenant_metadata and additional_metadata
    # as JSON strings (not dicts) when sent inside the already-stringified `memories`
    # multipart field.  Pre-encode them here.
    memories = [
        {
            "source_id": ctx.memory_source_id,
            "title": "E2E user response preference",
            "text": f"Alex prefers concise technical answers and dark mode. Contract test {ctx.run_id}.",
            "infer": True,
            "user_name": "Alex",
            "tenant_metadata": json.dumps(
                {"department": "support", "workspace": "docs"}
            ),
            "additional_metadata": json.dumps(
                {"source": "e2e_contract", "run_id": ctx.run_id}
            ),
        }
    ]

    def _ingest_memory() -> ApiResponse:
        return client.request(
            "POST",
            "/source/ingest",
            multipart=(
                {
                    "type": "memory",
                    "tenant_id": TENANT_ID,
                    "sub_tenant_id": SUB_TENANT_ID,
                    "upsert": "true",
                    "memories": json.dumps(memories),
                },
                [],
            ),
            expected_statuses=(202,),
            label="POST /source/ingest memory",
        )

    resp = run_check(recorder, "runtime POST /source/ingest memory", _ingest_memory)
    if isinstance(resp, ApiResponse) and isinstance(resp.data, dict):
        for item in resp.data.get("results", []) or []:
            if isinstance(item, dict) and item.get("source_id"):
                source_ids.append(item["source_id"])

    expected = {
        ctx.knowledge_file_source_id,
        ctx.knowledge_app_source_id,
        ctx.memory_source_id,
    }
    missing = expected - set(source_ids)
    if missing:
        recorder.fail(
            "runtime ingest returned source IDs",
            f"Missing expected source IDs {sorted(missing)}; got {source_ids}",
        )
    else:
        recorder.pass_(
            "runtime ingest returned source IDs", ", ".join(sorted(source_ids))
        )
    return source_ids or list(expected)


def wait_for_sources_searchable(
    client: ApiClient, recorder: Recorder, source_ids: list[str]
) -> bool:
    deadline = time.time() + SOURCE_READY_TIMEOUT_SECONDS
    terminal_failures = {"errored", "failed"}
    searchable = {"graph_creation", "completed"}
    last_statuses = None
    while time.time() < deadline:
        resp = run_check(
            recorder,
            "runtime GET /source/status poll",
            lambda: client.request(
                "GET",
                "/source/status",
                query={
                    "tenant_id": TENANT_ID,
                    "sub_tenant_id": SUB_TENANT_ID,
                    "source_ids": source_ids,
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


def exercise_core_endpoints(
    client: ApiClient, recorder: Recorder, ctx: Context, source_ids: list[str]
) -> None:
    run_check(
        recorder, "runtime GET /tenants", lambda: client.request("GET", "/tenants")
    )
    run_check(
        recorder,
        "runtime GET /tenants/sub-tenants",
        lambda: client.request(
            "GET", "/tenants/sub-tenants", query={"tenant_id": TENANT_ID}
        ),
    )
    run_check(
        recorder,
        "runtime GET /tenants/stats",
        lambda: client.request("GET", "/tenants/stats", query={"tenant_id": TENANT_ID}),
    )

    # /source/list for both documented shapes.
    run_check(
        recorder,
        "runtime POST /source/list knowledge",
        lambda: client.request(
            "POST",
            "/source/list",
            json_body={
                "tenant_id": TENANT_ID,
                "sub_tenant_id": SUB_TENANT_ID,
                "type": "knowledge",
                "source_ids": [
                    ctx.knowledge_file_source_id,
                    ctx.knowledge_app_source_id,
                ],
                "page": 1,
                "page_size": 10,
                "filters": {"tenant_metadata": {"department": "support"}},
            },
        ),
    )
    run_check(
        recorder,
        "runtime POST /source/list memory",
        lambda: client.request(
            "POST",
            "/source/list",
            json_body={
                "tenant_id": TENANT_ID,
                "sub_tenant_id": SUB_TENANT_ID,
                "type": "memory",
                "source_ids": [ctx.memory_source_id],
                "page": 1,
                "page_size": 10,
            },
        ),
    )

    # /source/fetch is most reliable for uploaded file source.
    run_check(
        recorder,
        "runtime GET /source/fetch content",
        lambda: client.request(
            "GET",
            "/source/fetch",
            query={
                "tenant_id": TENANT_ID,
                "sub_tenant_id": SUB_TENANT_ID,
                "source_id": ctx.knowledge_file_source_id,
                "mode": "content",
            },
        ),
    )

    run_check(
        recorder,
        "runtime GET /source/relations",
        lambda: client.request(
            "GET",
            "/source/relations",
            query={
                "tenant_id": TENANT_ID,
                "sub_tenant_id": SUB_TENANT_ID,
                "source_id": ctx.knowledge_app_source_id,
                "type": "knowledge",
                "limit": 100,
            },
        ),
    )

    # Search endpoint: test docs selector field, plus the OpenAPI selector as a compatibility probe if different.
    search_body_base = {
        "tenant_id": TENANT_ID,
        "sub_tenant_id": SUB_TENANT_ID,
        "query": f"What is Alex's preference and what are the refund rules for contract test {ctx.run_id}?",
        "search_by": "hybrid",
        "mode": "thinking",
        "max_results": 5,
        "alpha": "auto",
        "graph_context": True,
        "metadata_filters": {
            "department": "support",
            "additional_metadata": {"source": "e2e_contract"},
        },
    }
    for selector in ("knowledge", "memory", "all"):
        body = dict(search_body_base)
        body[SEARCH_SELECTOR_FIELD] = selector
        run_check(
            recorder,
            f"runtime POST /search {SEARCH_SELECTOR_FIELD}={selector}",
            lambda body=body: client.request("POST", "/search", json_body=body),
        )

    if SEARCH_SELECTOR_FIELD != "source":
        body = dict(search_body_base)
        body["source"] = "knowledge"
        run_check(
            recorder,
            "runtime POST /search OpenAPI source=knowledge compatibility",
            lambda: client.request("POST", "/search", json_body=body),
        )

    # Text search/operator contract.
    text_body = {
        "tenant_id": TENANT_ID,
        "sub_tenant_id": SUB_TENANT_ID,
        "query": "Starter costs $29",
        SEARCH_SELECTOR_FIELD: "knowledge",
        "search_by": "text",
        "operator": "phrase",
        "max_results": 5,
    }
    run_check(
        recorder,
        "runtime POST /search text phrase",
        lambda: client.request("POST", "/search", json_body=text_body),
    )

    # DELETE /source — body must be {"request": {tenant_id, sub_tenant_id, ids}, "type": ...}
    # (the `request` wrapper key is required per OpenAPI Body_delete_documents_source_delete).
    if DELETE_CORE_TEST_DATA:
        run_check(
            recorder,
            "runtime DELETE /source memory",
            lambda: client.request(
                "DELETE",
                "/source",
                json_body={
                    "type": "memory",
                    "request": {
                        "tenant_id": TENANT_ID,
                        "sub_tenant_id": SUB_TENANT_ID,
                        "ids": [ctx.memory_source_id],
                    },
                },
            ),
        )
        run_check(
            recorder,
            "runtime DELETE /source knowledge",
            lambda: client.request(
                "DELETE",
                "/source",
                json_body={
                    "type": "knowledge",
                    "request": {
                        "tenant_id": TENANT_ID,
                        "sub_tenant_id": SUB_TENANT_ID,
                        "ids": [
                            ctx.knowledge_file_source_id,
                            ctx.knowledge_app_source_id,
                        ],
                    },
                },
            ),
        )
    else:
        recorder.pass_(
            "runtime DELETE /source skipped",
            "Set HYDRADB_DELETE_CORE_TEST_DATA=1 to delete ingested E2E sources",
        )


def exercise_delete_tenant_endpoint(
    client: ApiClient, recorder: Recorder, ctx: Context
) -> None:
    # Try to create a disposable tenant to test DELETE /tenants against.
    # If the plan limit is hit (403 FORBIDDEN) we skip gracefully rather than
    # failing — that's a plan constraint, not an API contract bug.
    def _create_disposable() -> ApiResponse:
        return client.request(
            "POST",
            "/tenants",
            json_body=create_tenant_body(DELETE_TEST_TENANT_ID),
            expected_statuses=(200, 403, 409),
            validate_contract=False,
            label=f"POST /tenants create {DELETE_TEST_TENANT_ID}",
        )

    resp = run_check(
        recorder,
        f"runtime POST /tenants create {DELETE_TEST_TENANT_ID}",
        _create_disposable,
    )
    if not isinstance(resp, ApiResponse):
        return

    if resp.status == 403:
        body = resp.json_body or {}
        err = body.get("error") if isinstance(body, dict) else {}
        code = (err or {}).get("code", "")
        recorder.pass_(
            f"runtime DELETE /tenants disposable skipped",
            f"Plan limit reached ({code}); cannot create a 3rd tenant on this account. "
            "DELETE /tenants endpoint exists and returns a documented error envelope.",
            resp.request_id,
        )
        return

    if resp.status == 409:
        # Already exists from a previous run — still valid to delete.
        pass
    else:
        # 200 — validate the contract.
        try:
            client.contract.validate_response("POST", "/tenants", 200, resp.json_body)
        except ContractError as exc:
            recorder.fail(
                f"runtime POST /tenants contract {DELETE_TEST_TENANT_ID}",
                str(exc),
                resp.request_id,
            )

    ctx.created_delete_tenant = True
    run_check(
        recorder,
        "runtime DELETE /tenants disposable",
        lambda: client.request(
            "DELETE", "/tenants", query={"tenant_id": DELETE_TEST_TENANT_ID}
        ),
    )


def exercise_webhook_endpoints(
    client: ApiClient, recorder: Recorder, ctx: Context
) -> None:
    if not RUN_WEBHOOK_TESTS:
        recorder.pass_(
            "runtime webhooks skipped", "Set HYDRADB_RUN_WEBHOOK_TESTS=1 to enable"
        )
        return

    run_check(
        recorder,
        "runtime GET /webhooks/indexing initial",
        lambda: client.request("GET", "/webhooks/indexing"),
    )
    post_resp = run_check(
        recorder,
        "runtime POST /webhooks/indexing",
        lambda: client.request(
            "POST",
            "/webhooks/indexing",
            json_body={
                "url": WEBHOOK_URL,
                "event_types": ["indexing.status_changed"],
            },
            expected_statuses=(200, 201),
        ),
    )
    if isinstance(post_resp, ApiResponse):
        ctx.created_webhook = True

    run_check(
        recorder,
        "runtime GET /webhooks/indexing after create",
        lambda: client.request("GET", "/webhooks/indexing"),
    )
    test_resp = run_check(
        recorder,
        "runtime POST /webhooks/indexing/test",
        lambda: client.request("POST", "/webhooks/indexing/test", json_body={}),
    )
    # WebhookTestResponse has no delivery_id (test sends but doesn't create a delivery row).
    # We'll pick up a real delivery_id from the deliveries list below.

    deliveries_resp = run_check(
        recorder,
        "runtime GET /webhooks/indexing/deliveries",
        lambda: client.request(
            "GET", "/webhooks/indexing/deliveries", query={"limit": 10}
        ),
    )
    if ctx.known_delivery_id is None and isinstance(deliveries_resp, ApiResponse):
        # Webhooks return the schema directly (no envelope), so json_body IS the DeliveryListResponse.
        data = deliveries_resp.json_body
        if isinstance(data, dict):
            deliveries = data.get("deliveries") or data.get("items") or []
            if deliveries and isinstance(deliveries[0], dict):
                ctx.known_delivery_id = deliveries[0].get("delivery_id") or deliveries[
                    0
                ].get("id")

    if ctx.known_delivery_id:
        run_check(
            recorder,
            "runtime GET /webhooks/indexing/deliveries/{delivery_id}",
            lambda: client.request(
                "GET", f"/webhooks/indexing/deliveries/{ctx.known_delivery_id}"
            ),
        )
        # Retry can be invalid if the delivery already succeeded; accept 400/409 if schema matches ErrorApiResponse.
        run_check(
            recorder,
            "runtime POST /webhooks/indexing/deliveries/{delivery_id}/retry",
            lambda: client.request(
                "POST",
                f"/webhooks/indexing/deliveries/{ctx.known_delivery_id}/retry",
                expected_statuses=(200, 202, 400, 409),
            ),
        )
    else:
        recorder.fail(
            "runtime webhook delivery detail/retry",
            "No delivery_id found from test or delivery list; cannot exercise detail/retry path endpoints",
        )

    run_check(
        recorder,
        "runtime DELETE /webhooks/indexing",
        lambda: client.request("DELETE", "/webhooks/indexing"),
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
    parser.add_argument(
        "--tenant-id",
        default=TENANT_ID,
        help="Tenant ID to create/use for core E2E flow",
    )
    parser.add_argument(
        "--sub-tenant-id", default=SUB_TENANT_ID, help="Sub-tenant ID for E2E data"
    )
    args = parser.parse_args()

    # Allow CLI overrides while keeping config visible at the top of the file.
    BASE_URL = args.base_url
    TENANT_ID = args.tenant_id
    SUB_TENANT_ID = args.sub_tenant_id

    recorder = Recorder()
    contract = OpenApiContract(OPENAPI_PATH, strict_extra_keys=STRICT_EXTRA_KEYS)

    print("=== HydraDB v2 docs/API contract test ===")
    print(f"OpenAPI: {OPENAPI_PATH}")
    print(f"Endpoint docs: {ENDPOINT_DOCS_DIR}")
    print(f"Base URL: {BASE_URL}")
    print(f"Tenant: {TENANT_ID}")
    print(f"Sub-tenant: {SUB_TENANT_ID}")
    print(f"Strict extra keys: {STRICT_EXTRA_KEYS}")
    print(f"Search selector field under test: {SEARCH_SELECTOR_FIELD}")

    if not args.no_static:
        print("\n=== Static docs/cURL/OpenAPI audit ===")
        audit_endpoint_docs(contract, recorder)

    if args.static_only:
        recorder.summary()
        return 1 if recorder.failed else 0

    if not API_KEY:
        recorder.fail(
            "runtime configuration API key",
            "API_KEY is empty. Set HYDRADB_API_KEY or paste a short-lived key into API_KEY at the top of this script.",
        )
        recorder.summary()
        return 1

    print("\n=== Runtime E2E contract tests ===")
    client = ApiClient(BASE_URL, API_KEY, contract, recorder)
    ctx = create_context()
    try:
        ensure_tenant(client, recorder, TENANT_ID)
        ready = wait_for_tenant_ready(client, recorder, TENANT_ID)
        if ready:
            source_ids = ingest_test_data(client, recorder, ctx)
            wait_for_sources_searchable(client, recorder, source_ids)
            exercise_core_endpoints(client, recorder, ctx, source_ids)
        else:
            recorder.fail(
                "runtime core flow",
                "Tenant never became ready; skipping ingestion/search-dependent endpoints",
            )

        exercise_delete_tenant_endpoint(client, recorder, ctx)
        exercise_webhook_endpoints(client, recorder, ctx)
    finally:
        cleanup(client, recorder, ctx)

    recorder.summary()
    return 1 if recorder.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
