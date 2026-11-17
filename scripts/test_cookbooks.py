"""Execute every cookbook's Python against the released SDK's method signatures.

No network and no credentials: each SDK method is replaced by a stub that first
binds the call to the real method's signature (so an unknown or misspelled
argument fails), checks the JSON payloads against the documented shapes of a
unified database, and returns a response filled with public fields only. A
cookbook that reads a field the public API does not return fails here.

The query stub returns the unified response body (chunks, graph,
forceful_relations, llm_prompt, tabular_data). The released SDK has no model
for it, so it is built from plain objects that raise on any other attribute.
"""

import ast
import copy
import inspect
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import types
from types import SimpleNamespace as NS
import unittest
from unittest import mock

from hydra_db import types as sdk

ROOT = Path(__file__).resolve().parents[1]
COOKBOOKS = sorted(p for p in (ROOT / "cookbooks/v2").glob("*.mdx") if p.name != "index.mdx")
SETUP = ROOT / "snippets/cookbook-setup.mdx"

DOCUMENT_METADATA_KEYS = {"id", "metadata", "additional_metadata", "relations"}
# The accepted item fields of a unified ingest (POST /context/ingest, `context`).
# `instructions` is accepted by the API but is not taught on ingest pages.
CONTEXT_ITEM_KEYS = {
    "context_id", "title", "text", "conversation", "enrich", "upsert", "happened_at",
    "attributes", "custom_attributes", "forceful_relations", "acl", "user_name",
}
OPERATORS = {"equals", "contains", "contains_any"}
ATTRIBUTE_OPERATORS = {"$eq", "$ne", "$gt", "$gte", "$lt", "$lte", "$in", "$nin", "$exists", "$not"}
# Optional connector examples use these environment variables.
CONNECTOR_COOKBOOKS = {"glean"}
APP_CREDENTIALS = {
    "SLACK_TOKEN": "xoxp-test", "SLACK_WORKSPACE_ID": "T12345ACME",
    "GMAIL_CREDENTIALS": '{"refresh_token": "test"}',
    "EXECUTIVE_EMAIL": "dana@acme.com", "USER_EMAIL": "maya@example.com",
}
# Plain-text and document extensions POST /context/ingest accepts
# (api-reference/v2/endpoint/ingest-context.mdx#supported-file-formats).
UPLOAD_EXTENSIONS = {".txt", ".md", ".markdown", ".json", ".pdf", ".docx", ".csv"}


def bounded_sleep(limit=200):
    """Patch time.sleep so a polling loop that never ends fails instead of hanging the suite."""
    calls = iter(range(limit))

    def sleep(seconds):
        if next(calls, None) is None:
            raise AssertionError("a polling loop did not finish")

    return mock.patch("time.sleep", side_effect=sleep)


def python_blocks(path):
    """Return {filename: source} for the labelled Python blocks on a page, in order."""
    files = {}
    for name, body in re.findall(r"^```python ([\w.]+)\n(.*?)^```", path.read_text(), re.M | re.S):
        files[name] = files.get(name, "") + body + "\n"
    return files


def fake_openai():
    module = types.ModuleType("openai")

    class OpenAI:
        def __init__(self, *args, **kwargs):
            self.calls = 0
            self.chat = NS(completions=NS(create=self.create))

        def create(self, model, messages, tools=None):
            self.calls += 1
            if tools and self.calls == 1:
                call = NS(id="call_1", function=NS(arguments=json.dumps({"question": "auth"})))
                return NS(choices=[NS(message=NS(content=None, tool_calls=[call]))])
            return NS(choices=[NS(message=NS(content="stub answer", tool_calls=None))])

    module.OpenAI = OpenAI
    return module


class Stubs:
    """Signature-checked replacements for the SDK methods the cookbooks call."""

    def __init__(self, test, client):
        self.test = test
        self.declared = set()
        self.collections = set()
        self.restricted = set()  # Collections a connector syncs into: their sources carry ACLs.
        self.connectors = []
        self.configured = {}  # connector_id -> resources, or None before configure.
        self.discovered = [["C1", "C2"], ["C3"]]  # Two pages, so pagination is exercised.
        self.connector_status = ("active", "healthy")
        self.failing = set()  # Resource IDs whose last sync failed.
        self.retryable = True  # Whether waiting can fix those failures.
        self.quiet = "ok"  # Status of the resources that did not fail.
        self.acl_warnings = {}  # resource_id -> why its permissions could not be read.
        self.page_acl_warnings = {}
        self.status = "completed"
        self.calls = []
        for owner, name, handler in [
            (client.databases, "create", self.create),
            (client.databases, "status", self.database_status),
            (client.context, "ingest", self.ingest),
            (client.context, "status", self.context_status),
            (client.context, "list", self.context_list),
            (client, "query", self.query),
            (client.connectors, "create", self.connector_create),
            (client.connectors, "discover", self.connector_discover),
            (client.connectors, "configure", self.connector_configure),
            (client.connectors, "status", self.connector_status_call),
            (client.connectors, "list", self.connector_list),
            (client, "list_providers", self.provider_catalog),
            (client.connectors, "list_resources", self.connector_resources),
            (client.connectors, "update_resource_acl", self.connector_update_resource),
        ]:
            label = name if owner is client else f"{type(owner).__name__}.{name}"
            setattr(owner, name, self.checked(label, getattr(owner, name), handler))

    def checked(self, name, real, handler):
        signature = inspect.signature(real)

        def call(*args, **kwargs):
            signature.bind(*args, **kwargs)
            self.calls.append(name)
            return handler(*args, **kwargs)

        return call

    def create(self, database, database_metadata_schema=None):
        for field in database_metadata_schema or []:
            self.test.assertLessEqual(
                set(field),
                {"name", "data_type", "max_length", "enable_dense_embedding", "enable_sparse_embedding"},
            )
            self.declared.add(field["name"])
        return sdk.HandlerEnvelopeTenantsTenantCreateAcceptedResponse(success=True)

    def database_status(self, database):
        return sdk.HandlerEnvelopeTenantsInfraStatusResponseV2(
            data=sdk.TenantsInfraStatusResponseV2(
                database=database,
                infra=sdk.TenantsInfraV2(ready_for_ingestion=True)
            )
        )

    # Providers that copy the app's permissions onto each source, per
    # "What each provider captures" in essentials/v2/connectors.mdx.
    CAPTURES_ACL = {"slack", "google_drive", "github", "confluence", "jira"}

    def connector_create(self, provider, name, database, collection, credentials,
                         provider_account_scope=None):
        self.test.assertTrue(credentials and all(credentials.values()), "credentials must not be empty")
        if provider in {"slack", "github", "linear", "notion"}:
            self.test.assertTrue(provider_account_scope, f"{provider} needs provider_account_scope")
        else:
            self.test.assertIsNone(provider_account_scope, f"{provider} has no account scope")
        self.test.assertNotIn(name, [c.name for c in self.connectors if c.database == database],
                              "one connector per account")
        connector_id = f"conn_{len(self.connectors) + 1}"
        self.connectors.append(sdk.HandlerConnectorApiView(
            connector_id=connector_id, provider=provider, name=name, database=database,
            collection=collection, provider_account_scope=provider_account_scope,
            lifecycle="pending_setup",
        ))
        self.configured[connector_id] = None
        self.collections.add(collection)
        self.restricted.add(collection)
        return sdk.HandlerConnectorCreateResponse(connector_id=connector_id)

    def provider_catalog(self, id=None):
        self.test.assertIsNone(id, "the catalog, not one provider, reports acl_supported")
        return sdk.HandlerProviderListResponse(providers=[
            sdk.HandlerCatalogConnector.model_validate(
                {"provider": p, "acl_supported": p in self.CAPTURES_ACL})
            for p in sorted(self.CAPTURES_ACL | {"gmail", "notion"})
        ])

    def connector_list(self, provider=None):
        return sdk.HandlerConnectorListResponse(
            connectors=[c for c in self.connectors if provider in (None, c.provider)]
        )

    def connector(self, connector_id):
        found = [c for c in self.connectors if c.connector_id == connector_id]
        self.test.assertTrue(found, f"unknown connector {connector_id}")
        return found[0]

    def connector_status_call(self, id):
        self.connector(id)
        if self.configured[id] is None:
            return sdk.HandlerConnectorStatusResponse(
                connector_id=id, lifecycle="pending_setup", status="degraded")
        lifecycle, status = self.connector_status
        return sdk.HandlerConnectorStatusResponse(
            connector_id=id, lifecycle=lifecycle, status=status,
            resources=[
                sdk.HandlerConnectorResourceStatus(
                    resource_id=r["resource_id"], display_name=r["name"],
                    status="failed" if r["resource_id"] in self.failing else self.quiet,
                    message="permission denied" if r["resource_id"] in self.failing else None,
                    retryable=self.retryable if r["resource_id"] in self.failing else None,
                    acl_warning=self.acl_warnings.get(r["resource_id"]),
                    page_acl_warning=self.page_acl_warnings.get(r["resource_id"]))
                for r in self.configured[id]
            ])

    def connector_resources(self, id):
        self.connector(id)
        return sdk.HandlerConnectorResourcesResponse(resources=[
            sdk.ConnectorsResource(connector_id=id, resource_id=r["resource_id"], acl=r.get("acl"))
            for r in self.configured[id] or []
        ])

    def connector_update_resource(self, id, resource_id, request):
        self.test.assertLessEqual(set(request), {"acl", "custom_instructions"})
        found = [r for r in self.configured[id] if r["resource_id"] == resource_id]
        self.test.assertTrue(found, f"unknown resource {resource_id}")
        self.test.assertIsInstance(request.get("acl", []), list)
        found[0].update(request)
        return {"connector_id": id, "resource_id": resource_id, **request}

    def connector_discover(self, id, cursor=None, limit=None):
        connector = self.connector(id)
        self.test.assertLessEqual(limit or 100, 100)
        pages = self.discovered
        index = 0 if cursor is None else int(cursor)
        return sdk.HandlerDiscoverResponseBody(
            provider=connector.provider,
            resources=[sdk.HandlerDiscoveredResourceDto(id=r, resource_type="channel", name=r.lower())
                       for r in pages[index]],
            has_more=index + 1 < len(pages),
            next_cursor=str(index + 1) if index + 1 < len(pages) else None,
        )

    def connector_configure(self, id, resources, lookback_days=None):
        test = self.test
        connector = self.connector(id)
        test.assertTrue(resources, "configure rejects an empty resources list")
        wanted = {r for page in self.discovered for r in page}
        test.assertEqual(wanted, {r["resource_id"] for r in resources}, "configure every discovered resource")
        for resource in resources:
            sdk.HandlerResourceMapping(**resource)
            test.assertLessEqual(set(resource), {"resource_id", "resource_type", "name", "acl"})
            if connector.provider not in self.CAPTURES_ACL:
                test.assertIsNotNone(resource.get("acl"),
                                f"{connector.provider} captures no permissions: set acl on its resources")
        self.configured[id] = resources
        return sdk.HandlerConfigureResponse(connector_id=id)

    # A unified database takes no `type` and no split upload fields (`memories`):
    # passing either fails the call here.
    def ingest(self, database, collection, context=None, documents=None, document_metadata=None,
               upsert=None):  # No app_knowledge: app content comes through connectors.
        test = self.test
        self.collections.add(collection)
        ids = []
        test.assertNotEqual(context is None, documents is None, "send context items or files, not both")
        if context is not None:
            items = json.loads(context)
            test.assertTrue(0 < len(items) <= 100)
            for item in items:
                test.assertLessEqual(set(item), CONTEXT_ITEM_KEYS)
                test.assertEqual(1, ("text" in item) + ("conversation" in item))
                for turn in item.get("conversation", []):
                    test.assertEqual({"role", "content"}, set(turn))
                    test.assertIn(turn["role"], {"user", "assistant", "system"})
                    test.assertTrue(turn["content"].strip())
                if "conversation" in item:
                    test.assertTrue({"user", "assistant"} & {t["role"] for t in item["conversation"]})
                test.assertLessEqual(set(item.get("attributes", {})), self.declared)
                if "happened_at" in item:
                    test.assertRegex(item["happened_at"], r"^\d{4}-\d{2}-\d{2}$")
                ids.append(item.get("context_id", f"ctx_{len(self.calls)}_{len(ids)}"))
        else:
            items = json.loads(document_metadata)
            test.assertEqual(len(documents), len(items))
            for (filename, handle, content_type), item in zip(documents, items):
                test.assertLessEqual(set(item), DOCUMENT_METADATA_KEYS)
                test.assertLessEqual(set(item.get("metadata", {})), self.declared)
                test.assertIn(Path(filename).suffix, UPLOAD_EXTENSIONS, filename)
                test.assertTrue(handle.read(), f"{filename} is empty")
                ids.append(item["id"])
        test.assertTrue(ids)
        test.assertTrue(all(re.fullmatch(r"[^,\s]+", i) for i in ids), ids)
        return sdk.HandlerEnvelopeIngestionV2IngestResponse(
            data=sdk.IngestionV2IngestResponse(
                results=[sdk.IngestionV2IngestResultItem(id=i, status="queued") for i in ids]
            )
        )

    def context_status(self, database, collection, ids):
        self.test.assertIn(collection, self.collections)
        self.test.assertTrue(ids)
        return sdk.HandlerEnvelopeIngestionV2BatchProcessingStatus(
            data=sdk.IngestionV2BatchProcessingStatus(statuses=[
                sdk.IngestionV2ProcessingStatus(
                    id=i, indexing_status=self.status,
                    error_code="E2002" if self.status == "errored" else None,
                    error_message="No readable text" if self.status == "errored" else None,
                ) for i in ids
            ])
        )

    def context_list(self, database, collection, ids, acl=None):
        self.test.assertIn(collection, self.collections)
        self.test.assertTrue(ids)
        if collection in self.restricted:
            self.test.assertTrue(acl, "a list over connector-synced sources must send the caller's acl")
        return sdk.HandlerEnvelopeListV2ListResponse(
            data=sdk.ListV2ListResponse(sources=[
                sdk.ListV2SourceItem(id=i, title=f"title of {i}", database=database, collection=collection,
                                     tenant_id=database, sub_tenant_id=collection)
                for i in ids
            ])
        )

    def query(self, **kwargs):
        test = self.test
        scopes = kwargs.get("collections") or [kwargs["collection"]]
        test.assertFalse("collection" in kwargs and "collections" in kwargs)
        test.assertLessEqual(set(scopes), self.collections)
        test.assertNotIn("type", kwargs, "a unified database takes no type")
        test.assertIn(kwargs.get("mode", "auto"), {"fast", "thinking", "auto"})
        if "max_results" in kwargs:
            test.assertLessEqual(kwargs["max_results"], 250)
        for key, value in kwargs.get("metadata_filters", {}).items():
            test.assertIn(key, self.declared)
            test.assertEqual(1, len(value))
            test.assertLessEqual(set(value), OPERATORS)
        for key, value in kwargs.get("attributes", {}).items():
            test.assertIn(key, self.declared)
            if isinstance(value, dict):
                test.assertLessEqual(set(value), ATTRIBUTE_OPERATORS)
        if self.restricted & set(scopes):
            test.assertIn("acl", kwargs, "a query over connector-synced sources must send the caller's acl")
        if "acl" in kwargs:
            test.assertTrue(kwargs["acl"])
            for principal in kwargs["acl"]:
                test.assertRegex(principal, r"^(?:[^@\s]+@[^@\s]+\.\w+|domain:\S+|group:\S+:\S+)$")
        return unified_result(
            chunks=[unified_chunk("source_1", "text")],
            forceful_relations=[NS(via=NS(**{"from": "source_1", "to": "source_2"}),
                                   chunk=unified_chunk("source_2", "linked text"))],
        )


def unified_chunk(context_id, content):
    """One chunk of a unified /query response, with its public fields only."""
    return NS(chunk_id=f"{context_id}_chunk_0", context_id=context_id, score=0.9, content=content)


def plain(value):
    if isinstance(value, NS):
        return {k: plain(v) for k, v in vars(value).items() if not callable(v)}
    if isinstance(value, list):
        return [plain(v) for v in value]
    return value


def unified_result(chunks=(), graph=(), forceful_relations=()):
    """The unified /query envelope: data carries exactly the five public keys."""
    data = NS(chunks=list(chunks), graph=list(graph), forceful_relations=list(forceful_relations),
              llm_prompt="context", tabular_data=None)
    data.json = lambda **kwargs: json.dumps(plain(data))
    return NS(success=True, data=data, meta=NS(request_id="req_1"))


class CookbookTest(unittest.TestCase):
    def test_cursor_search_preserves_graph_linked_context_and_file_paths(self):
        source = python_blocks(ROOT / "cookbooks/v2/cursor.mdx")["cursor.py"]
        search = next(node for node in ast.parse(source).body
                      if isinstance(node, ast.FunctionDef) and node.name == "search_code")
        body = {
            "chunks": [{"chunk_id": "auth_chunk_0", "context_id": "auth", "score": 0.9, "content": "Validate the token."}],
            "graph": [{"origin": "query_path", "triplets": [], "path_summary": "Router calls validate_token."}],
            "forceful_relations": [{"via": {"from": "auth", "to": "policy"},
                                    "chunk": {"chunk_id": "policy_chunk_0", "context_id": "policy", "content": "Admin scope."}}],
            "llm_prompt": "context",
            "tabular_data": None,
        }
        result = NS(data=NS(json=lambda **kwargs: json.dumps(body)))
        scope = {"json": json, "DATABASE": "code", "COLLECTION": "repo",
                 "PATHS": {"auth": "src/auth.py"},
                 "client": NS(query=lambda **kwargs: result)}
        exec(compile(ast.Module(body=[search], type_ignores=[]), "cursor.py", "exec"), scope)

        context = json.loads(scope["search_code"]("How is authentication enforced?"))

        self.assertEqual("src/auth.py", context["chunks"][0]["path"])
        self.assertEqual(body["graph"], context["graph"])
        self.assertEqual(body["forceful_relations"], context["forceful_relations"])

    @staticmethod
    def optional_credentials(source):
        """Environment variables for providers a cookbook connects only when they are set."""
        names = re.findall(r'for provider in \(([^)]*)\)', source)
        providers = re.findall(r'"(\w+)"', " ".join(names))
        variables = {}
        for p in providers:
            variables[f"{p.upper()}_CREDENTIALS"] = '{"api_token": "test"}'
            variables[f"{p.upper()}_SITE"] = f"acme-{p}.example.com"
        return variables

    def run_cookbook(self, path):
        setup = python_blocks(SETUP)["hydra.py"]
        files = python_blocks(path)
        self.assertTrue(files, f"{path.name} has no labelled Python blocks")
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ, {"HYDRADB_API_KEY": "test", "OPENAI_API_KEY": "test", **APP_CREDENTIALS,
                         **self.optional_credentials(path.read_text())}
        ), mock.patch.dict(sys.modules, {"openai": fake_openai()}), bounded_sleep(), \
                mock.patch("builtins.print"):
            previous = os.getcwd()
            os.chdir(directory)
            try:
                repo = Path("path/to/your/repo")
                repo.mkdir(parents=True)
                (repo / "auth.py").write_text("def check(token):\n    return bool(token)\n")
                hydra = types.ModuleType("hydra")
                exec(compile(setup, "hydra.py", "exec"), hydra.__dict__)
                stubs = Stubs(self, hydra.client)
                with mock.patch.dict(sys.modules, {"hydra": hydra}):
                    for name, source in files.items():
                        exec(compile(source, name, "exec"), {"__name__": "__main__"})
            finally:
                os.chdir(previous)
        return stubs

    def test_every_cookbook_runs_against_the_sdk_signatures(self):
        self.assertEqual(6, len(COOKBOOKS))
        for path in COOKBOOKS:
            with self.subTest(cookbook=path.name):
                stubs = self.run_cookbook(path)
                steps = ["DatabasesClient.create", "DatabasesClient.status", "query"]
                steps += ["ContextClient.ingest", "ContextClient.status"]
                if path.stem in CONNECTOR_COOKBOOKS:
                    steps += ["ConnectorsClient.list", "ConnectorsClient.create", "ConnectorsClient.discover",
                              "ConnectorsClient.configure", "ConnectorsClient.status"]
                else:
                    self.assertNotIn("ConnectorsClient.create", stubs.calls)
                for step in steps:
                    self.assertIn(step, stubs.calls)

    def test_glean_connection_example_discovers_every_page(self):
        hydra, stubs = self.load_setup()
        source = python_blocks(ROOT / "cookbooks/v2/glean.mdx")["connect.py"]
        with mock.patch.dict(sys.modules, {"hydra": hydra}), bounded_sleep():
            exec(compile(source, "connect.py", "exec"), {})
        self.assertEqual(2, stubs.calls.count("ConnectorsClient.discover"))
        self.assertEqual(1, stubs.calls.count("ConnectorsClient.configure"))

    def test_executive_brief_preserves_acl_on_reads_and_writes(self):
        source = python_blocks(ROOT / "cookbooks/v2/chief-of-staff.mdx")["chief_of_staff.py"]
        tree = ast.parse(source)
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in {"brief", "remember"}]
        client = NS(query=mock.Mock(return_value=NS()), context=NS(ingest=mock.Mock(return_value=NS())))
        scope = {"json": json, "client": client, "DATABASE": "db", "RECORDS": "work", "EXECUTIVE": "exec",
                 "EXECUTIVE_EMAIL": "dana@acme.com", "answer": mock.Mock(),
                 "wait_indexed": mock.Mock(), "source_ids": lambda result: ["item"]}
        records = next(node for node in tree.body if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == "records" for target in node.targets))
        exec(compile(ast.Module(body=[records], type_ignores=[]), "chief-records.py", "exec"), scope)
        self.assertTrue(all(item.get("acl") == ["dana@acme.com"] for item in scope["records"]))
        exec(compile(ast.Module(body=functions, type_ignores=[]), "chief-functions.py", "exec"), scope)
        scope["brief"]("What is current?")
        self.assertEqual(["dana@acme.com"], client.query.call_args.kwargs.get("acl"))
        scope["remember"]("A private commitment")
        items = json.loads(client.context.ingest.call_args.kwargs["context"])
        self.assertTrue(items)
        self.assertTrue(all(item.get("acl") == ["dana@acme.com"] for item in items))

    def test_connect_reuses_only_the_same_account(self):
        hydra, stubs = self.load_setup()
        token = {"access_token": "x"}
        with bounded_sleep():
            first = hydra.connect("slack", "T1", "db", "docs", token)
            again = hydra.connect("slack", "T1", "db", "docs", token)
            workspace = hydra.connect("slack", "T2", "db", "docs", token)
            collection = hydra.connect("slack", "T1", "db", "other", token)
        self.assertEqual(first, again)
        self.assertEqual(3, len({first, workspace, collection}))
        self.assertEqual(3, stubs.calls.count("ConnectorsClient.create"))
        self.assertEqual(3, stubs.calls.count("ConnectorsClient.configure"))
        self.assertEqual({"T1", "T2"}, {c.provider_account_scope for c in stubs.connectors})

    def test_connect_gives_each_mailbox_its_own_connector(self):
        hydra, stubs = self.load_setup()
        with bounded_sleep():
            work = hydra.connect("gmail", "maya@acme.com", "db", "docs", {"refresh_token": "a"},
                                 acl=["maya@acme.com"])
            home = hydra.connect("gmail", "maya@home.org", "db", "docs", {"refresh_token": "b"},
                                 acl=["maya@acme.com"])
        self.assertNotEqual(work, home)
        self.assertEqual(2, stubs.calls.count("ConnectorsClient.create"))
        self.assertEqual(2, stubs.calls.count("ConnectorsClient.configure"))

    def test_connect_keeps_ambiguous_collection_account_pairs_separate(self):
        for provider, credential in (("gmail", "refresh_token"), ("slack", "access_token")):
            with self.subTest(provider=provider):
                hydra, stubs = self.load_setup()
                first_args = (provider, "carol@example.com", "db", "alice-bob", {credential: "first"})
                second_args = (provider, "bob-carol@example.com", "db", "alice", {credential: "second"})
                with bounded_sleep():
                    first = hydra.connect(*first_args, acl=["carol@example.com"])
                    original = copy.deepcopy(stubs.configured[first])
                    second = hydra.connect(*second_args, acl=["bob-carol@example.com"])
                    self.assertEqual(original, stubs.configured[first], "the second account changed the first account's resources or ACLs")
                    self.assertNotEqual(first, second)
                    self.assertEqual(first, hydra.connect(*first_args, acl=["carol@example.com"]))
                    self.assertEqual(second, hydra.connect(*second_args, acl=["bob-carol@example.com"]))
                self.assertEqual(2, stubs.calls.count("ConnectorsClient.create"))
                self.assertEqual(2, stubs.calls.count("ConnectorsClient.configure"))
                self.assertNotEqual(stubs.connector(first).name, stubs.connector(second).name)
                if provider == "gmail":
                    stubs.connectors = [connector.model_copy(update={"provider_account_scope": ""})
                                        for connector in stubs.connectors]
                    with bounded_sleep():
                        self.assertEqual(first, hydra.connect(*first_args, acl=["carol@example.com"]))
                        self.assertEqual(second, hydra.connect(*second_args, acl=["bob-carol@example.com"]))
                    self.assertEqual(2, stubs.calls.count("ConnectorsClient.create"))
                for connector_id, args, acl in ((first, first_args, ["carol@example.com"]),
                                                (second, second_args, ["bob-carol@example.com"])):
                    connector = stubs.connector(connector_id)
                    self.assertEqual((args[0], args[2], args[3]),
                                     (connector.provider, connector.database, connector.collection))
                    self.assertEqual(args[1] if provider == "slack" else "", connector.provider_account_scope)
                    self.assertTrue(all(resource["acl"] == acl for resource in stubs.configured[connector_id]))

    def test_connect_name_includes_the_database_identity(self):
        hydra, stubs = self.load_setup()
        with bounded_sleep():
            first = hydra.connect("slack", "caller-chosen-account", "db-a", "docs", {"access_token": "x"})
            second = hydra.connect("slack", "caller-chosen-account", "db-b", "docs", {"access_token": "x"})
        self.assertNotEqual(first, second)
        self.assertNotEqual(stubs.connector(first).name, stubs.connector(second).name)
        self.assertEqual({"db-a", "db-b"}, {connector.database for connector in stubs.connectors})

    def test_connect_rejects_same_name_with_missing_or_mismatched_identity_before_writes(self):
        cases = [("slack", field, value) for field, value in (
            ("provider", "github"), ("database", "other-db"), ("collection", "other-collection"),
            ("provider_account_scope", "other-caller-chosen-account"),
            ("provider_account_scope", ""),
            ("provider", None), ("database", None), ("collection", None),
            ("provider_account_scope", None), ("connector_id", None),
        )] + [("gmail", "provider_account_scope", "unexpected-account-scope")]
        for configured in (False, True):
            for provider, field, value in cases:
                with self.subTest(configured=configured, provider=provider, field=field, value=value):
                    hydra, stubs = self.load_setup()
                    credential = "refresh_token" if provider == "gmail" else "access_token"
                    args = (provider, "caller-chosen-account", "db", "docs", {credential: "x"})
                    with bounded_sleep():
                        connector_id = hydra.connect(*args, acl=["first@example.com"])
                    stubs.connectors[0] = stubs.connectors[0].model_copy(update={field: value})
                    if not configured:
                        stubs.configured[connector_id] = None
                    before = copy.deepcopy(stubs.configured)
                    start = len(stubs.calls)
                    with bounded_sleep(), self.assertRaisesRegex(RuntimeError, "identity"):
                        hydra.connect(*args, acl=["second@example.com"])
                    self.assertEqual(before, stubs.configured)
                    self.assertEqual(["ConnectorsClient.list"], stubs.calls[start:])

    def test_connect_does_not_reuse_legacy_ambiguous_names(self):
        hydra, stubs = self.load_setup()
        args = ("gmail", "carol@example.com", "db", "alice-bob", {"refresh_token": "x"})
        with bounded_sleep():
            first = hydra.connect(*args, acl=["first@example.com"])
            original = copy.deepcopy(stubs.configured[first])
            old_name = "cookbook-gmail-alice-bob-carol@example.com"
            stubs.connectors[0] = stubs.connectors[0].model_copy(update={"name": old_name})
            second = hydra.connect(*args, acl=["second@example.com"])
        self.assertNotEqual(first, second)
        self.assertEqual(original, stubs.configured[first])
        self.assertNotEqual(old_name, stubs.connector(second).name)

    def test_connect_rejects_multiple_same_name_connectors_before_acl_writes(self):
        hydra, stubs = self.load_setup()
        args = ("slack", "caller-chosen-account", "db", "docs", {"access_token": "x"})
        with bounded_sleep():
            first = hydra.connect(*args, acl=["first@example.com"])
            hydra.connect("slack", "another-account", "other-db", "docs", {"access_token": "x"}, acl=["other@example.com"])
        stubs.connectors[1] = stubs.connectors[1].model_copy(update={"name": stubs.connector(first).name})
        before = copy.deepcopy(stubs.configured)
        start = len(stubs.calls)
        with bounded_sleep(), self.assertRaisesRegex(RuntimeError, "identity"):
            hydra.connect(*args, acl=["second@example.com"])
        self.assertEqual(before, stubs.configured)
        self.assertEqual(["ConnectorsClient.list"], stubs.calls[start:])

    def test_connect_configures_every_page_of_resources(self):
        hydra, stubs = self.load_setup()
        with bounded_sleep():
            connector_id = hydra.connect("slack", "T1", "db", "docs", {"access_token": "x"}, lookback_days=90)
        self.assertEqual(["C1", "C2", "C3"], [r["resource_id"] for r in stubs.configured[connector_id]])
        self.assertEqual(2, stubs.calls.count("ConnectorsClient.discover"))

    def test_connect_finishes_a_connector_left_unconfigured(self):
        hydra, stubs = self.load_setup()
        stubs.discovered = [[]]  # The first run finds nothing and stops before configure.
        with bounded_sleep(), self.assertRaisesRegex(RuntimeError, "can reach nothing to sync"):
            hydra.connect("slack", "T1", "db", "docs", {"access_token": "x"})
        stubs.discovered = [["C1"]]
        with bounded_sleep():
            connector_id = hydra.connect("slack", "T1", "db", "docs", {"access_token": "x"})
        self.assertEqual(1, stubs.calls.count("ConnectorsClient.create"))
        self.assertEqual(["C1"], [r["resource_id"] for r in stubs.configured[connector_id]])

    def test_connect_sets_acl_for_a_provider_that_captures_none(self):
        hydra, stubs = self.load_setup()
        with bounded_sleep():
            connector_id = hydra.connect("gmail", "a@acme.com", "db", "docs", {"refresh_token": "x"},
                                         acl=["a@acme.com"])
        self.assertTrue(all(r["acl"] == ["a@acme.com"] for r in stubs.configured[connector_id]))
        with bounded_sleep(), self.assertRaisesRegex(ValueError, "notion captures no permissions"):
            hydra.connect("notion", "ws1", "db", "docs", {"token": "x"})
        self.assertNotIn("ConnectorsClient.create", stubs.calls[stubs.calls.index("list_providers"):])

    def test_connect_stops_when_permissions_could_not_be_read(self):
        for field in ("acl_warnings", "page_acl_warnings"):
            with self.subTest(field=field):
                hydra, stubs = self.load_setup()
                setattr(stubs, field, {"C2": "provider returned 403"})
                gave_up = mock.patch("time.sleep", side_effect=AssertionError("connect kept polling"))
                with gave_up, self.assertRaisesRegex(RuntimeError, "could not read the permissions of c2. Pass"):
                    hydra.connect("slack", "T1", "db", "docs", {"access_token": "x"})

    def test_connect_accepts_unread_permissions_when_an_acl_is_set(self):
        hydra, stubs = self.load_setup()
        stubs.acl_warnings = {"C2": "provider returned 403"}
        with bounded_sleep():
            connector_id = hydra.connect("slack", "T1", "db", "docs", {"access_token": "x"},
                                         acl=["domain:acme.com"])
        self.assertTrue(all(r["acl"] == ["domain:acme.com"] for r in stubs.configured[connector_id]))

    def test_connect_replaces_the_acl_of_a_connector_it_reuses(self):
        hydra, stubs = self.load_setup()
        args = ("gmail", "a@acme.com", "db", "docs", {"refresh_token": "x"})
        with bounded_sleep():
            connector_id = hydra.connect(*args, acl=["domain:acme.com"])
            self.assertEqual(connector_id, hydra.connect(*args, acl=["a@acme.com"]))
        self.assertTrue(all(r["acl"] == ["a@acme.com"] for r in stubs.configured[connector_id]))
        self.assertEqual(3, stubs.calls.count("ConnectorsClient.update_resource_acl"))
        self.assertEqual(1, stubs.calls.count("ConnectorsClient.configure"))

    def test_connect_leaves_a_stored_acl_alone_when_none_is_passed(self):
        hydra, stubs = self.load_setup()
        args = ("slack", "T1", "db", "docs", {"access_token": "x"})
        with bounded_sleep():
            connector_id = hydra.connect(*args, acl=["domain:acme.com"])
            hydra.connect(*args)
        self.assertTrue(all(r["acl"] == ["domain:acme.com"] for r in stubs.configured[connector_id]))
        self.assertNotIn("ConnectorsClient.update_resource_acl", stubs.calls)

    def test_connect_treats_an_empty_acl_as_nobody_not_as_unset(self):
        hydra, stubs = self.load_setup()
        args = ("gmail", "a@acme.com", "db", "docs", {"refresh_token": "x"})
        with bounded_sleep():
            connector_id = hydra.connect(*args, acl=[])
        self.assertTrue(all(r["acl"] == [] for r in stubs.configured[connector_id]))
        with bounded_sleep():
            hydra.connect(*args, acl=["a@acme.com"])
            hydra.connect(*args, acl=[])
        self.assertTrue(all(r["acl"] == [] for r in stubs.configured[connector_id]))

    def test_connect_reports_a_connector_that_synced_only_part(self):
        hydra, stubs = self.load_setup()
        stubs.connector_status = ("active", "degraded")
        stubs.failing = {"C2"}
        stubs.quiet = "empty"  # A resource with nothing new is not a failure.
        gave_up = mock.patch("time.sleep", side_effect=AssertionError("connect kept polling"))
        with gave_up, self.assertRaisesRegex(RuntimeError, "incomplete: c2: permission denied$"):
            hydra.connect("slack", "T1", "db", "docs", {"access_token": "x"})

    def test_connect_stops_when_a_first_sync_fails_for_good(self):
        hydra, stubs = self.load_setup()
        stubs.connector_status = ("ingesting", "degraded")
        stubs.failing = {"C1"}
        stubs.retryable = False
        gave_up = mock.patch("time.sleep", side_effect=AssertionError("connect kept polling"))
        with gave_up, self.assertRaisesRegex(RuntimeError, "cannot sync c1: permission denied$"):
            hydra.connect("slack", "T1", "db", "docs", {"access_token": "x"})

    def test_connect_names_the_failing_resources_when_it_times_out(self):
        hydra, stubs = self.load_setup()
        stubs.connector_status = ("ingesting", "degraded")
        stubs.failing = {"C1"}  # Retryable, so connect keeps waiting.
        with bounded_sleep(), mock.patch("time.time", side_effect=[0, 0, 10, 10_000, 10_000]), \
                self.assertRaisesRegex(TimeoutError, "c1: permission denied"):
            hydra.connect("slack", "T1", "db", "docs", {"access_token": "x"}, timeout=60)

    def test_connect_stops_on_every_state_that_cannot_recover(self):
        for lifecycle, status in (("ingesting", "failed"), ("paused", "paused"), ("active", "failed"),
                                  ("ingesting", "capped"), ("reconnect", "degraded")):
            with self.subTest(lifecycle=lifecycle, status=status):
                hydra, stubs = self.load_setup()
                stubs.connector_status = (lifecycle, status)
                gave_up = mock.patch("time.sleep", side_effect=AssertionError("connect kept polling"))
                with gave_up, self.assertRaisesRegex(RuntimeError, "connector is"):
                    hydra.connect("slack", "T1", "db", "docs", {"access_token": "x"})

    def test_connect_gives_up_after_its_timeout(self):
        hydra, stubs = self.load_setup()
        stubs.connector_status = ("ingesting", "checking")  # An account with nothing to sync never goes active.
        with bounded_sleep(), mock.patch("time.time", side_effect=[0, 0, 10, 10_000, 10_000]), \
                self.assertRaisesRegex(TimeoutError, "synced nothing"):
            hydra.connect("slack", "T1", "db", "docs", {"access_token": "x"}, timeout=60)

    def test_connect_reports_zero_and_immediate_timeouts_without_polling(self):
        for timeout, times in ((0, [0, 0]), (60, [0, 100])):
            with self.subTest(timeout=timeout):
                hydra, stubs = self.load_setup()
                with bounded_sleep(), mock.patch("time.time", side_effect=times), \
                        self.assertRaises(TimeoutError) as raised:
                    hydra.connect("slack", "caller-chosen-account", "db", "docs", {"access_token": "x"}, timeout=timeout)
                message = str(raised.exception)
                self.assertIn(f"synced nothing within {timeout} seconds", message)
                self.assertIn("first-sync polling did not run for conn_1", message)
                self.assertEqual(1, stubs.calls.count("ConnectorsClient.status"))
                self.assertEqual(["C1", "C2", "C3"], [r["resource_id"] for r in stubs.configured["conn_1"]])

    def load_setup(self):
        """Import hydra.py with stubbed SDK methods, outside any cookbook."""
        hydra = types.ModuleType("hydra")
        with mock.patch.dict(os.environ, {"HYDRADB_API_KEY": "test", "OPENAI_API_KEY": "test"}), \
                mock.patch.dict(sys.modules, {"openai": fake_openai()}):
            exec(compile(python_blocks(SETUP)["hydra.py"], "hydra.py", "exec"), hydra.__dict__)
        return hydra, Stubs(self, hydra.client)

    def test_setup_raises_when_indexing_errors(self):
        hydra, stubs = self.load_setup()
        stubs.collections.add("docs")
        stubs.status = "errored"
        # A loop that misses the errored status must fail here, not spin forever.
        gave_up = mock.patch("time.sleep", side_effect=AssertionError("wait_indexed kept polling an errored source"))
        with gave_up, self.assertRaisesRegex(RuntimeError, "E2002: No readable text"):
            hydra.wait_indexed("db", "docs", ["source_1"])

    def test_setup_waits_for_completed(self):
        # On the live API a source at graph_creation can still be missed by a
        # query for a few seconds, so the helper must not return early.
        hydra, stubs = self.load_setup()
        stubs.collections.add("docs")
        stubs.status = "graph_creation"
        with mock.patch("time.sleep", side_effect=TimeoutError), self.assertRaises(TimeoutError):
            hydra.wait_indexed("db", "docs", ["source_1"])
        stubs.status = "completed"
        with mock.patch("time.sleep", side_effect=TimeoutError):
            hydra.wait_indexed("db", "docs", ["source_1"])

    def test_setup_raises_when_an_item_is_rejected(self):
        hydra, _ = self.load_setup()
        rejected = sdk.HandlerEnvelopeIngestionV2IngestResponse(
            data=sdk.IngestionV2IngestResponse(results=[
                sdk.IngestionV2IngestResultItem(id="a", filename="auth.py", status="failed",
                                                error="Unsupported format", error_code="E1002"),
            ])
        )
        with self.assertRaisesRegex(RuntimeError, "auth.py: E1002 Unsupported format"):
            hydra.source_ids(rejected)

    def test_index_links_every_cookbook(self):
        index = (ROOT / "cookbooks/v2/index.mdx").read_text()
        for path in COOKBOOKS:
            self.assertIn(f'href="/cookbooks/v2/{path.stem}"', index)


if __name__ == "__main__":
    unittest.main()
