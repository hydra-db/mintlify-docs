"""Run the examples in AGENTS.mdx and the v2 docs against the released SDKs and the OpenAPI spec.

Python blocks, here and on every other v2 page, run through the real
`hydradb-sdk` client. Only the HTTP layer is
replaced: every request the SDK sends is checked against
api-reference/openapi.json (the path and method exist, and every query
parameter, JSON key, and form field is declared), and every canned response is
checked against the operation's response schema before the SDK parses it. An
example that sends an unknown field, or reads one the public API does not
return, fails here.

TypeScript blocks, here and on every other v2 page, are type-checked against
the installed `@hydradb/sdk`. JSON and cURL blocks are checked against the spec.
"""

import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import types
from types import SimpleNamespace as NS
import unittest
from unittest import mock
from urllib.parse import parse_qs

import httpx
import hydra_db

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_cookbooks import (  # noqa: E402
    APP_SOURCE_KEYS, DOCUMENT_METADATA_KEYS, KIND_FIELDS, KINDS, MEMORY_KEYS,
)

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "AGENTS.mdx"
SPEC = json.loads((ROOT / "api-reference/openapi.json").read_text())
SCHEMAS = SPEC["components"]["schemas"]
SPEC_TEXT = json.dumps(SPEC)
FULL_TEXT = {}
BASE = "https://api.hydradb.com"

# Fields the spec still lists but .github/verify_v2_public_contract.py keeps out
# of the public docs. An example must not send or read them.
INTERNAL_RESPONSE = {"sources", "source_facts", "source_filter", "app_search_fusion", "temporal_filter"}
INTERNAL_REQUEST = {"attributes", "graph_vector_prune", "graph_vector_prune_spacy_entities",
                    "temporal_intent", "temporal_now"}
LEGACY = {"tenant_id", "sub_tenant_id", "sub_tenant_ids"}
INDEXING_STATUSES = {"queued", "processing", "graph_creation", "completed", "errored"}


def blocks(language, page=GUIDE):
    """Return [(first line number, source)] for a page's fenced blocks in one language."""
    found, current, start = [], None, 0
    for number, line in enumerate(page.read_text().split("\n"), 1):
        fence = re.match(r"\s*```(\w*)", line)
        if fence and current is None:
            current, start, body = fence.group(1), number + 1, []
        elif fence:
            if current == language:
                found.append((start, "\n".join(body)))
            current = None
        elif current is not None:
            body.append(line)
    return found


def resolve(schema):
    while "$ref" in schema:
        schema = SCHEMAS[schema["$ref"].rsplit("/", 1)[1]]
    for part in schema.get("allOf", []):
        merged = dict(resolve(part))
        merged.setdefault("properties", {})
        return merged
    return schema


def unknown_keys(value, schema, path=""):
    """List keys in value that the schema does not declare, recursively."""
    schema = resolve(schema)
    if isinstance(value, list):
        items = schema.get("items", {})
        return [k for i, v in enumerate(value) for k in unknown_keys(v, items, f"{path}[{i}]")]
    if not isinstance(value, dict):
        return []
    properties = schema.get("properties")
    if properties is None or "additionalProperties" in schema:  # Free-form object, such as metadata.
        return []
    unknown = []
    for key, child in value.items():
        if key not in properties:
            unknown.append(f"{path}.{key}")
        else:
            unknown += unknown_keys(child, properties[key], f"{path}.{key}")
    return unknown


def operation(method, path):
    """Find the spec operation for a concrete request path."""
    for template, item in SPEC["paths"].items():
        pattern = "^" + re.sub(r"\{[^}]+\}", "[^/]+", template) + "$"
        if re.match(pattern, path) and method.lower() in item:
            return template, item[method.lower()]
    raise AssertionError(f"{method} {path} is not in the OpenAPI spec")


def request_schema(op):
    content = op.get("requestBody", {}).get("content", {})
    for media in content.values():
        return resolve(media["schema"])
    return {"properties": {}}


# Two routes for one operation: the SDKs call the first, the page documents the second.
SAME_ROUTE = {("GET", "/context/{id}/subgraph"): ("GET", "/context/subgraph")}


def binding(page):
    """Return (METHOD, path template) for an endpoint page, or None."""
    match = re.search(r'^openapi: "api-reference/openapi.json (\w+) ([^"]+)"', page.read_text(), re.M)
    return match.groups() if match else None


def section(page, tag):
    return "\n".join(re.findall(rf"<{tag}>(.*?)</{tag}>", page.read_text(), re.S))


def success_example(page):
    """The first 2xx JSON body in a page's <ResponseExample>, with its status code."""
    method, template = binding(page)
    responses = SPEC["paths"][template][method.lower()]["responses"]
    declared = next(code for code in responses if code.startswith("2"))
    for label, body in re.findall(r"```json([^\n]*)\n(.*?)\n\s*```", section(page, "ResponseExample"), re.S):
        try:
            sample = json.loads(body)
        except json.JSONDecodeError:
            continue
        failed = isinstance(sample, dict) and sample.get("success") is False
        if failed or re.match(r"\s*[45]\d\d|\s*Failure", label):
            continue
        code = re.match(r"\s*(2\d\d)", label)
        return int(code.group(1) if code else declared), sample
    return None


def envelope(data):
    return {"success": True, "data": data, "error": None,
            "meta": {"request_id": "req_test", "latency_ms": 1.0}}


class FakeHydraDB:
    """An HTTP layer that validates requests and answers with spec-checked bodies."""

    def __init__(self, test):
        self.test = test
        self.calls = []
        self.failures = []  # Kept here too: an example may swallow the exception.
        self.examples = {}  # (method, path template) -> (status, body) documented on the page under test.

    def respond(self, template, method, query, body):
        ids = query.get("ids") or query.get("id") or ["source_1"]
        key = (method, template)
        if key in self.examples:
            return self.examples[key]
        if key == ("POST", "/databases"):
            return 202, envelope({"database": body.get("database"), "status": "accepted"})
        if key == ("GET", "/databases/status"):
            return 200, envelope({"database": query["database"][0],
                                  "infra": {"ready_for_ingestion": True}})
        if key == ("POST", "/context/ingest"):
            items = json.loads(body.get("memories") or body.get("app_knowledge")
                               or body.get("document_metadata") or "[{}]")
            return 202, envelope({"success": True, "results": [
                {"id": item.get("id", f"source_{n}"), "status": "queued"}
                for n, item in enumerate(items)
            ], "success_count": len(items), "failed_count": 0})
        if key == ("GET", "/context/status"):
            self.test.assertIn("collection", query, "context.status must send the collection it ingested into")
            return 200, envelope({"statuses": [
                {"id": i, "indexing_status": "completed", "success": True} for i in ids
            ]})
        if key == ("POST", "/query"):
            return 200, envelope({
                "llm_prompt": "Question and context.",
                "chunks": [{"id": "source_1", "chunk_uuid": "source_1_chunk_0",
                            "chunk_content": "text", "source_title": "title",
                            "relevancy_score": 0.9}],
            })
        if key == ("POST", "/feedback"):
            return 200, envelope({"feedback_id": "fb_1"})
        if key == ("POST", "/connectors"):
            return 201, {"connector_id": "conn_1", "provider": body.get("provider"), "status": "active"}
        if key == ("GET", "/connectors/{id}/discover"):
            return 200, {"resources": [{"id": "C1", "resource_type": "channel", "name": "general"}]}
        if key == ("POST", "/connectors/{id}/configure"):
            return 200, {}
        if key == ("GET", "/connectors/{id}/status"):
            return 200, {"connector_id": "conn_1", "status": "active"}
        if key == ("GET", "/context/inspect"):
            return 200, envelope({"id": ids[0], "success": True})
        if key == ("GET", "/context/relations"):
            return 200, envelope({"relations": [], "is_truncated": False})
        # Every other endpoint: an empty success body in the shape the spec declares.
        _, op = operation(method, template.replace("{", "").replace("}", ""))
        success = next(s for s in op["responses"] if s.startswith("2"))
        media = op["responses"][success].get("content", {}).get("application/json", {})
        enveloped = "data" in resolve(media.get("schema", {})).get("properties", {})
        return int(success), envelope({}) if enveloped else {}

    def check_ingest_items(self, body, where):
        """Check the JSON-string form fields, which the spec types only as strings."""
        test = self.test
        test.assertIn(body.get("type"), {"knowledge", "memory"}, where)
        for item in json.loads(body.get("memories") or "[]"):
            test.assertEqual("memory", body["type"], where)
            test.assertLessEqual(set(item), MEMORY_KEYS, where)
            test.assertEqual(1, ("text" in item) + ("user_assistant_pairs" in item), where)
        for item in json.loads(body.get("document_metadata") or "[]"):
            test.assertLessEqual(set(item), DOCUMENT_METADATA_KEYS, where)
        for item in json.loads(body.get("app_knowledge") or "[]"):
            test.assertEqual("knowledge", body["type"], where)
            test.assertLessEqual(set(item), APP_SOURCE_KEYS, where)
            test.assertIn(item.get("kind"), KINDS, where)
            fields = item.get("fields", {})
            test.assertEqual(item["kind"], fields.get("kind"), where)
            test.assertLessEqual(set(fields) - {"kind"}, KIND_FIELDS[item["kind"]], f"{where}: {item.get('id')}")

    def handle(self, request):
        try:
            return self.checked(request)
        except AssertionError as error:
            self.failures.append(f"{request.method} {request.url.path}: {error}")
            raise

    def checked(self, request):
        test = self.test
        template, op = operation(request.method, request.url.path)
        where = f"{request.method} {template}"
        test.assertEqual("2", request.headers.get("API-Version"), where)
        test.assertTrue(request.headers.get("Authorization", "").startswith("Bearer "), where)

        query = parse_qs(request.url.query.decode())
        declared = {p["name"] for p in op.get("parameters", []) if p["in"] == "query"}
        test.assertLessEqual(set(query), declared, f"{where}: undeclared query parameter")
        test.assertFalse(set(query) & LEGACY, f"{where}: deprecated parameter")

        body = {}
        content_type = request.headers.get("Content-Type", "")
        if content_type.startswith("application/json") and request.content:
            body = json.loads(request.content)
        elif content_type.startswith("multipart/form-data"):
            for name in set(re.findall(rb'; name="([^"]+)"', request.content)):
                field = name.decode()
                match = re.search(rb'; name="' + name + rb'"\r\n\r\n(.*?)\r\n--', request.content, re.S)
                body[field] = match.group(1).decode() if match else "<file>"
        schema = request_schema(op)
        test.assertEqual([], unknown_keys(body, schema), f"{where}: undeclared request field")
        test.assertFalse(set(body) & (LEGACY | INTERNAL_REQUEST), f"{where}: deprecated or internal field")
        for required in schema.get("required", []):
            test.assertIn(required, body, f"{where}: missing required field")

        if template == "/context/ingest":
            self.check_ingest_items(body, where)

        status, payload = self.respond(template, request.method, query, body)
        responses = op["responses"]
        declared_status = str(status) if str(status) in responses else next(s for s in responses if s.startswith("2"))
        media = responses[declared_status].get("content", {}).get("application/json")
        if media:
            test.assertEqual([], unknown_keys(payload, media["schema"]), f"{where}: canned response is off-spec")
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        if template == "/query":
            test.assertFalse(set(data) & INTERNAL_RESPONSE, f"{where}: internal response field")
        self.calls.append(where)
        return httpx.Response(status, json=payload)


def fake_openai():
    module = types.ModuleType("openai")

    class OpenAI:
        def __init__(self, *args, **kwargs):
            create = lambda **k: NS(choices=[NS(message=NS(content="stub answer", tool_calls=None))])
            self.chat = NS(completions=NS(create=create))

    module.OpenAI = OpenAI
    return module


V2_PAGES = [GUIDE] + sorted(
    page
    for folder in ("get-started", "essentials", "api-reference", "plugins")
    for page in (ROOT / folder).rglob("*.mdx")
)


class PythonExamples(unittest.TestCase):
    """Run every Python example in the v2 docs through the real SDK."""

    # Placeholders an example may use without defining them.
    AMBIENT = {
        "expected_answer": "30 days", "connector_id": "conn_1", "secret": "whsec_test",
        "raw_body": b"{}", "signature": "sha256=00", "eval_set": [],
    }
    # Pages whose examples need a third-party package or a helper defined in
    # prose. The plugin pages are skipped too: they call agent frameworks.
    SKIP = {
        "essentials/bring-your-own-graph.mdx": "raw HTTP client built on requests and neo4j",
    }
    OPTIONAL_MODULES = {"fastapi", "flask", "requests", "neo4j"}

    def run_page(self, page, fake, make_client):
        namespace = {"__name__": "__main__", **self.AMBIENT}
        ran = 0
        bound = binding(page)
        request_example = section(page, "RequestExample") if bound else ""
        documented = success_example(page) if bound else None
        before = fake.calls.count("POST /feedback")
        expected = sum(source.count("feedback.submit(") for _, source in blocks("python", page)
                       if "for " not in source)
        for line, source in blocks("python", page):
            namespace["client"] = namespace["hydra"] = make_client()
            seen = len(fake.calls)
            in_request_example = bool(bound) and source.strip() and source.strip() in request_example
            # The page's request example gets the page's own documented response.
            fake.examples = {}
            if in_request_example and documented:
                fake.examples = {route: documented for route in (bound, SAME_ROUTE.get(bound)) if route}
            with self.subTest(example=f"{page.relative_to(ROOT)}:{line}"):
                try:
                    exec(compile(textwrap.dedent(source), f"{page.name}:{line}", "exec"), namespace)
                except ModuleNotFoundError as error:
                    self.assertIn(error.name, self.OPTIONAL_MODULES)
                    continue
                ran += 1
                if in_request_example:
                    accepted = {" ".join(route) for route in (bound, SAME_ROUTE.get(bound)) if route}
                    self.assertTrue(accepted & set(fake.calls[seen:]),
                                    "the request example does not call the endpoint this page documents")
                    self.bound_examples += 1
                    self.served_examples += bool(documented)
        fake.examples = {}
        # Feedback calls sit inside try/except, so count the ones that arrived.
        with self.subTest(page=str(page.relative_to(ROOT)), check="feedback calls reached the API"):
            self.assertGreaterEqual(fake.calls.count("POST /feedback") - before, expected)
        return ran

    def test_every_python_block_runs_through_the_sdk(self):
        fake = FakeHydraDB(self)
        self.bound_examples = self.served_examples = 0
        real_sync, real_async = hydra_db.HydraDB, hydra_db.AsyncHydraDB
        transport = httpx.MockTransport(fake.handle)

        def sync_client(**kwargs):
            self.assertLessEqual(set(kwargs), {"token", "timeout", "base_url"})
            return real_sync(token="test", httpx_client=httpx.Client(transport=transport))

        def async_client(**kwargs):
            self.assertLessEqual(set(kwargs), {"token", "timeout", "base_url"})
            return real_async(token="test", httpx_client=httpx.AsyncClient(transport=transport))

        def no_network(*args, **kwargs):
            raise AssertionError("an example reached the network")

        real_open = open

        def open_or_stub(path, mode="r", *args, **kwargs):
            if "b" in mode and "r" in mode and not Path(path).exists():
                return io.BytesIO(b"%PDF-1.4 test")
            return real_open(path, mode, *args, **kwargs)

        ran = 0
        previous = os.getcwd()
        environment = {"HYDRADB_API_KEY": "test", "OPENAI_API_KEY": "test",
                       "HYDRADB_WEBHOOK_SECRET": "whsec_test",
                       "HYDRADB_WEBHOOK_SECRET_PREVIOUS": "whsec_old"}
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.dict(os.environ, environment), \
                mock.patch.dict(sys.modules, {"openai": fake_openai()}), \
                mock.patch.object(hydra_db, "HydraDB", sync_client), \
                mock.patch.object(hydra_db, "AsyncHydraDB", async_client), \
                mock.patch.object(httpx.HTTPTransport, "handle_request", no_network), \
                mock.patch("builtins.open", open_or_stub), \
                mock.patch("time.sleep"), mock.patch("builtins.print"):
            os.chdir(directory)
            try:
                for page in V2_PAGES:
                    relative = str(page.relative_to(ROOT))
                    if relative not in self.SKIP and not relative.startswith("plugins/"):
                        ran += self.run_page(page, fake, sync_client)
            finally:
                os.chdir(previous)
        self.assertEqual([], fake.failures)
        self.assertGreaterEqual(ran, 90)
        # Every endpoint page has a Python request example that calls its endpoint,
        # and nearly all of them were answered with the page's documented response.
        endpoints = [page for page in V2_PAGES if binding(page)]
        self.assertEqual(len(endpoints), self.bound_examples)
        # Pages with no <ResponseExample> show the response Mintlify renders from the spec.
        without_example = sum(1 for page in endpoints if "<ResponseExample>" not in page.read_text())
        self.assertEqual(len(endpoints) - without_example, self.served_examples)
        for expected in ("POST /databases", "GET /databases/status", "POST /context/ingest",
                         "GET /context/status", "POST /query", "POST /feedback",
                         "POST /connectors", "GET /context/inspect"):
            self.assertIn(expected, fake.calls)


class TypeScriptExamples(unittest.TestCase):
    """Type-check every TypeScript example in the v2 docs against @hydradb/sdk."""

    PAGES = V2_PAGES + sorted((ROOT / "cookbooks").glob("*.mdx"))
    # Names an example may use without declaring: each is set up in an earlier
    # block on the same page, or is a placeholder the reader supplies.
    AMBIENT = {
        "client": 'declare const client: import("@hydradb/sdk").HydraDBClient;',
        "hydra": 'declare const hydra: import("@hydradb/sdk").HydraDBClient;',
        "buildString": 'import { buildString } from "@hydradb/sdk/helpers";',
        "connectorId": "declare const connectorId: string;",
        "secret": "declare const secret: string;",
        "rawBody": "declare const rawBody: string;",
        "signature": "declare const signature: string;",
        "BASE": "declare const BASE: string;",
        "KEY": "declare const KEY: string;",
    }
    SHIMS = (
        'declare module "openai" {\n  export default class OpenAI {\n'
        "    constructor(options?: { apiKey?: string });\n    chat: any;\n  }\n}\n"
        'declare module "express" {\n  const express: any;\n  export default express;\n'
        "  export type Request = any;\n  export type Response = any;\n}\n"
    )

    def test_every_typescript_block_type_checks(self):
        tsc = ROOT / "node_modules/.bin/tsc"
        if not tsc.exists():
            self.skipTest("node_modules is not installed")
        work = ROOT / "node_modules/.docs-typecheck"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir()
        count = 0
        try:
            (work / "shims.d.ts").write_text(self.SHIMS)
            for page in self.PAGES:
                name = str(page.relative_to(ROOT)).removesuffix(".mdx").replace("/", "__")
                for line, source in blocks("ts", page) + blocks("typescript", page):
                    preamble = ["export {};"]
                    for identifier, declaration in self.AMBIENT.items():
                        used = re.search(rf"(?<![.\w]){identifier}\b", source)
                        declared = re.search(
                            rf"^\s*(?:const|let|var|function|async function)\s+{identifier}\b"
                            rf"|^import\b[^;]*\b{identifier}\b", source, re.M)
                        if used and not declared:
                            preamble.append(declaration)
                    (work / f"{name}__line_{line}.ts").write_text("\n".join(preamble) + "\n" + source + "\n")
                    count += 1
            (work / "package.json").write_text('{"type": "module"}')
            (work / "tsconfig.json").write_text(json.dumps({
                "compilerOptions": {
                    "target": "es2022", "module": "nodenext", "moduleResolution": "nodenext",
                    "strict": True, "noImplicitAny": False, "noEmit": True, "skipLibCheck": True,
                    "types": ["node"],
                },
                "include": ["*.ts"],
            }))
            result = subprocess.run([str(tsc), "-p", str(work)], capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        self.assertGreaterEqual(count, 100)


class EndpointPages(unittest.TestCase):
    """Checks on the pages bound to one operation with `openapi:` front matter."""

    PAGES = [page for page in V2_PAGES if binding(page)]

    def test_every_operation_has_a_page(self):
        documented = {binding(page) for page in self.PAGES} | set(SAME_ROUTE.values())
        published = {(method.upper(), path) for path, item in SPEC["paths"].items()
                     for method in item if method in {"get", "post", "put", "patch", "delete"}}
        self.assertEqual(published, documented)

    def test_python_and_typescript_call_the_same_method(self):
        def snake(name):
            return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()

        for page in self.PAGES:
            example = section(page, "RequestExample")
            python = re.search(r"```python[^\n]*\n(.*?)```", example, re.S)
            typescript = re.search(r"```typescript[^\n]*\n(.*?)```", example, re.S)
            with self.subTest(page=page.name):
                self.assertTrue(python and typescript, "request example needs Python and TypeScript")
                called = re.search(r"\b(?:client|hydra)\.((?:\w+\.)?\w+)\(", python.group(1)).group(1)
                called_ts = re.search(r"\b(?:client|hydra)\.((?:\w+\.)?\w+)\(", typescript.group(1)).group(1)
                self.assertEqual(called, ".".join(snake(part) for part in called_ts.split(".")))

    def test_documented_fields_exist_in_the_spec(self):
        """A `<Field name>` in a request table must be a real request field."""
        checked = 0
        for page in self.PAGES:
            method, template = binding(page)
            op = SPEC["paths"][template][method.lower()]
            schema = request_schema(op)
            known = set(schema.get("properties", {})) | {p["name"] for p in op.get("parameters", [])}
            text = page.read_text()
            request_part = text.split("<ResponseExample>")[0] + text.split("</ResponseExample>")[-1]
            for name in re.findall(r'^\| <Field name="([^"]+)"', request_part, re.M):
                checked += 1
                self.assertFalse({name} & INTERNAL_REQUEST, f"{page.name}: internal field {name}")
                if "." not in name and name in known:
                    continue
                # A nested or item-level name: it must at least appear in the spec's text
                # for this operation's request body.
                leaf = name.split(".")[-1].split("[")[0]
                self.assertIn(f'"{leaf}"', FULL_TEXT.get(template, SPEC_TEXT), f"{page.name}: {name}")
        self.assertGreaterEqual(checked, 60)


class JsonAndCurlExamples(unittest.TestCase):
    REFERENCE = [page for page in V2_PAGES if "plugins" not in page.parts]

    def test_json_blocks_parse_and_use_public_fields(self):
        count = 0
        for page in self.REFERENCE:
            for line, source in blocks("json", page):
                with self.subTest(example=f"{page.relative_to(ROOT)}:{line}"):
                    try:
                        sample = json.loads(source)
                    except json.JSONDecodeError:
                        self.assertRegex(source, r"\.\.\.|//", "invalid JSON that is not an elided excerpt")
                        continue
                    count += 1
                    text = json.dumps(sample)
                    for name in INTERNAL_REQUEST:
                        self.assertNotIn(f'"{name}"', text)
                    if page == GUIDE:
                        for name in LEGACY:
                            self.assertNotIn(f'"{name}"', text)
                    data = sample.get("data", sample) if isinstance(sample, dict) else {}
                    if isinstance(data, dict) and "chunks" in data:
                        self.assertFalse(set(data) & INTERNAL_RESPONSE)
                        self.assertEqual([], unknown_keys(data, SCHEMAS["search.RetrievalResult"]))
                    if isinstance(sample, dict) and "query" in sample and "database" in sample \
                            and "params" not in sample:
                        self.assertEqual([], unknown_keys(sample, request_schema(operation("POST", "/query")[1])))
        self.assertGreaterEqual(count, 120)

    @staticmethod
    def curl_calls(source):
        """Yield (method, path, command) for each curl call to the API in a bash block."""
        for command in re.split(r"\n(?=\s*(?:\w+=\$\()?curl\b)", source):
            url = re.search(r"https://api\.hydradb\.com(/[^\s'\"?]*)", command)
            if not url:
                continue
            explicit = re.search(r"(?:-X ?|--request )(\w+)", command)
            sends_body = re.search(r"\s(?:-d|--data|--data-raw|-F|--form)\b", command) and " -G" not in command
            yield (explicit.group(1) if explicit else "POST" if sends_body else "GET"), url.group(1), command

    def check_curl(self, method, path, command):
        _, op = operation(method, path)
        if "AUTH[@]" not in command:  # The quickstart keeps both headers in a shell array.
            self.assertIn("API-Version: 2", command)
            self.assertIn("Authorization: Bearer", command)
        schema = request_schema(op)
        properties = schema.get("properties", {})
        for field in re.findall(r"(?:-F|--form) ['\"]?(\w+)=", command):
            self.assertIn(field, properties, "form field is not in the spec")
        declared = {p["name"] for p in op.get("parameters", [])}
        inline = re.search(re.escape(path) + r"\?([^\s'\"]+)", command)
        for field in re.findall(r"--data-urlencode ['\"]?(\w+)=", command) + \
                (re.findall(r"(\w+)=", inline.group(1)) if inline else []):
            self.assertIn(field, declared, "query parameter is not in the spec")
        body = re.search(r"(?:-d|--data|--data-raw) '(\{.*\})'", command, re.S)
        if body:
            sent = json.loads(body.group(1))
            self.assertEqual([], unknown_keys(sent, schema), "JSON body has a field the spec does not declare")
            self.assertFalse(set(sent) & INTERNAL_REQUEST)
            for required in schema.get("required", []):
                self.assertIn(required, sent, "JSON body lacks a required field")
        form = dict(re.findall(r"-F '(\w+)=(\[.*?\])'", command, re.S))
        if form:
            form["type"] = (re.search(r'-F "?type=(\w+)', command) or [None, None])[1]
            FakeHydraDB(self).check_ingest_items(form, f"{method} {path}")

    def test_curl_blocks_match_the_spec(self):
        calls = 0
        for page in self.REFERENCE:
            bound = binding(page)
            request_example = section(page, "RequestExample") if bound else ""
            for line, source in blocks("bash", page):
                for method, path, command in self.curl_calls(source):
                    calls += 1
                    with self.subTest(example=f"{page.relative_to(ROOT)}:{line}", call=f"{method} {path}"):
                        self.check_curl(method, path, command)
                        if bound and source.strip() in request_example:
                            self.assertEqual(bound, (method, operation(method, path)[0]),
                                             "the request example does not call the endpoint this page documents")
        self.assertGreaterEqual(calls, 80)

    def test_status_values_are_the_documented_ones(self):
        pages = V2_PAGES + sorted((ROOT / "cookbooks").glob("*.mdx")) + sorted((ROOT / "snippets").glob("*.mdx"))
        used = {}
        for page in pages:
            for line in page.read_text().split("\n"):
                tests_status = re.search(r"indexing_?[sS]tatus\b", line) and \
                    re.search(r"==|\bin\b|includes\(|has\(", line)
                names_statuses = re.match(r"\s*(?:const )?(?:terminal|done)\b.*=", line)
                if not (tests_status or names_statuses):
                    continue
                for literal in re.findall(r'"([a-z_]+)"', line):
                    used.setdefault(literal, page.name)
        self.assertGreaterEqual(len(used), 2)
        self.assertEqual({}, {k: v for k, v in used.items() if k not in INDEXING_STATUSES})


if __name__ == "__main__":
    unittest.main()
