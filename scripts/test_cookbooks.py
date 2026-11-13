"""Execute every cookbook's Python against the released SDK's method signatures.

No network and no credentials: each SDK method is replaced by a stub that first
binds the call to the real method's signature (so an unknown or misspelled
argument fails), checks the JSON payloads against the documented shapes, and
returns the SDK's own response model for that method, filled with public fields
only. A cookbook that reads a field the public API does not return fails here.
"""

import ast
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
MEMORY_KEYS = {
    "id", "title", "text", "user_assistant_pairs", "is_markdown", "infer",
    "custom_instructions", "user_name", "metadata", "additional_metadata",
}
OPERATORS = {"equals", "contains", "contains_any"}
# Cookbooks that bring app content in through connectors, and the variables they read.
CONNECTOR_COOKBOOKS = {"glean", "chief-of-staff", "personal-assistant"}
APP_CREDENTIALS = {
    "SLACK_TOKEN": "xoxp-test", "SLACK_WORKSPACE_ID": "T12345ACME",
    "GMAIL_CREDENTIALS": '{"refresh_token": "test"}',
    "EXECUTIVE_EMAIL": "dana@acme.com", "USER_EMAIL": "maya@example.com",
}
# Plain-text and document extensions POST /context/ingest accepts
# (api-reference/v2/endpoint/ingest-context.mdx#supported-file-formats).
UPLOAD_EXTENSIONS = {".txt", ".md", ".markdown", ".json", ".pdf", ".docx", ".csv"}


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
        self.connector_status = ("active", "healthy")
        self.status = "completed"
        self.calls = []
        for owner, name, handler in [
            (client.databases, "create", self.create),
            (client.databases, "status", self.database_status),
            (client.context, "ingest", self.ingest),
            (client.context, "status", self.context_status),
            (client, "query", self.query),
            (client.connectors, "create", self.connector_create),
            (client.connectors, "discover", self.connector_discover),
            (client.connectors, "configure", self.connector_configure),
            (client.connectors, "status", self.connector_status_call),
            (client.connectors, "list", self.connector_list),
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
                infra=sdk.TenantsInfraV2(ready_for_ingestion=True)
            )
        )

    def connector_create(self, provider, database, collection, credentials, provider_account_scope=None):
        self.test.assertTrue(credentials and all(credentials.values()), "credentials must not be empty")
        connector_id = f"conn_{len(self.connectors) + 1}"
        self.connectors.append(sdk.HandlerConnectorApiView(
            connector_id=connector_id, provider=provider, database=database, collection=collection,
        ))
        self.collections.add(collection)
        self.restricted.add(collection)
        return sdk.HandlerConnectorCreateResponse(connector_id=connector_id)

    def connector_list(self, provider=None):
        return sdk.HandlerConnectorListResponse(
            connectors=[c for c in self.connectors if provider in (None, c.provider)]
        )

    def connector_status_call(self, id):
        self.test.assertIn(id, [c.connector_id for c in self.connectors])
        lifecycle, status = self.connector_status
        return sdk.HandlerConnectorStatusResponse(connector_id=id, lifecycle=lifecycle, status=status)

    def connector_discover(self, id):
        self.test.assertIn(id, [c.connector_id for c in self.connectors])
        return sdk.HandlerDiscoverResponseBody(resources=[
            sdk.HandlerDiscoveredResourceDto(id="C1", resource_type="channel", name="general")
        ])

    def connector_configure(self, id, resources):
        for resource in resources:
            sdk.HandlerResourceMapping(**resource)
            self.test.assertLessEqual(set(resource), {"resource_id", "resource_type", "name"})
        return sdk.HandlerConfigureResponse()

    def ingest(self, type, database, collection, documents=None, document_metadata=None,
               memories=None):  # No app_knowledge: app content comes through connectors.
        test = self.test
        self.collections.add(collection)
        ids = []
        if type == "memory":
            for item in json.loads(memories):
                test.assertLessEqual(set(item), MEMORY_KEYS)
                test.assertEqual(1, ("text" in item) + ("user_assistant_pairs" in item))
                for pair in item.get("user_assistant_pairs", []):
                    test.assertEqual({"user", "assistant"}, set(pair))
                ids.append(item.get("id", f"mem_{len(self.calls)}"))
        else:
            test.assertEqual("knowledge", type)
            test.assertIsNone(memories)
            if documents is not None:
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

    def query(self, **kwargs):
        test = self.test
        scopes = kwargs.get("collections") or [kwargs["collection"]]
        test.assertFalse("collection" in kwargs and "collections" in kwargs)
        test.assertLessEqual(set(scopes), self.collections)
        test.assertIn(kwargs["type"], {"knowledge", "memory", "all"})
        test.assertIn(kwargs.get("mode", "auto"), {"fast", "thinking", "auto"})
        if "max_results" in kwargs:
            test.assertLessEqual(kwargs["max_results"], 250)
        for key, value in kwargs.get("metadata_filters", {}).items():
            test.assertIn(key, self.declared)
            test.assertEqual(1, len(value))
            test.assertLessEqual(set(value), OPERATORS)
        if self.restricted & set(scopes):
            test.assertIn("acl", kwargs, "a query over connector-synced sources must send the caller's acl")
        if "acl" in kwargs:
            test.assertTrue(kwargs["acl"] and all(isinstance(p, str) for p in kwargs["acl"]))
        # Public fields only: `sources` and other internal fields stay unset,
        # as .github/verify_v2_public_contract.py requires of the public result.
        chunk = sdk.SearchV2Chunk(
            id="source_1", chunk_uuid="source_1_chunk_0", chunk_content="text",
            source_title="title", relevancy_score=0.9,
        )
        return sdk.HandlerEnvelopeSearchV2RetrievalResult(
            data=sdk.SearchV2RetrievalResult(llm_prompt="context", chunks=[chunk]),
        )


class CookbookTest(unittest.TestCase):
    def test_cursor_search_preserves_graph_linked_context_and_file_paths(self):
        source = python_blocks(ROOT / "cookbooks/v2/cursor.mdx")["cursor.py"]
        search = next(node for node in ast.parse(source).body
                      if isinstance(node, ast.FunctionDef) and node.name == "search_code")
        result = sdk.HandlerEnvelopeSearchV2RetrievalResult(
            data=sdk.SearchV2RetrievalResult(
                chunks=[sdk.SearchV2Chunk(
                    id="auth", source_title="auth.py.txt", chunk_content="Validate the token.",
                    additional_metadata={"path": "src/auth.py"},
                )],
                graph_context=sdk.SearchGraphContext(query_paths=[
                    sdk.SearchScoredPathResponse(combined_context="Router calls validate_token."),
                ]),
                additional_context={"policy": sdk.SearchV2Chunk(
                    id="policy", chunk_content="Service tokens require the admin scope.",
                )},
                sources=[],  # Internal SDK fields must not leak into the tool response.
            ),
        )
        scope = {"json": json, "DATABASE": "code", "COLLECTION": "repo",
                 "client": NS(query=lambda **kwargs: result)}
        exec(compile(ast.Module(body=[search], type_ignores=[]), "cursor.py", "exec"), scope)

        context = json.loads(scope["search_code"]("How is authentication enforced?"))

        self.assertEqual({
            "chunks": [{
                "id": "auth", "source_title": "auth.py.txt",
                "chunk_content": "Validate the token.",
                "additional_metadata": {"path": "src/auth.py"}, "path": "src/auth.py",
            }],
            "graph_context": {"query_paths": [{"combined_context": "Router calls validate_token."}]},
            "additional_context": {"policy": {
                "id": "policy", "chunk_content": "Service tokens require the admin scope.",
            }},
        }, context)

    def run_cookbook(self, path):
        setup = python_blocks(SETUP)["hydra.py"]
        files = python_blocks(path)
        self.assertTrue(files, f"{path.name} has no labelled Python blocks")
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ, {"HYDRADB_API_KEY": "test", "OPENAI_API_KEY": "test", **APP_CREDENTIALS}
        ), mock.patch.dict(sys.modules, {"openai": fake_openai()}), mock.patch("time.sleep"), \
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
                if path.stem in CONNECTOR_COOKBOOKS:
                    steps += ["ConnectorsClient.list", "ConnectorsClient.create", "ConnectorsClient.discover",
                              "ConnectorsClient.configure", "ConnectorsClient.status"]
                else:
                    steps += ["ContextClient.ingest", "ContextClient.status"]
                    self.assertNotIn("ConnectorsClient.create", stubs.calls)
                for step in steps:
                    self.assertIn(step, stubs.calls)

    def test_connect_reuses_an_existing_connector(self):
        hydra, stubs = self.load_setup()
        with mock.patch("time.sleep"):
            first = hydra.connect("slack", "db", "docs", {"access_token": "x"}, account_scope="T1")
            second = hydra.connect("slack", "db", "docs", {"access_token": "x"}, account_scope="T1")
            other = hydra.connect("slack", "db", "other", {"access_token": "x"}, account_scope="T1")
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        self.assertEqual(2, stubs.calls.count("ConnectorsClient.create"))

    def test_connect_waits_for_the_first_sync_and_raises_on_failure(self):
        hydra, stubs = self.load_setup()
        stubs.connector_status = ("ingesting", "checking")
        with mock.patch("time.sleep", side_effect=TimeoutError), self.assertRaises(TimeoutError):
            hydra.connect("slack", "db", "docs", {"access_token": "x"})
        stubs.connector_status = ("ingesting", "failed")
        gave_up = mock.patch("time.sleep", side_effect=AssertionError("connect kept polling a failed connector"))
        with gave_up, self.assertRaisesRegex(RuntimeError, "slack connector failed"):
            hydra.connect("slack", "db", "docs", {"access_token": "x"})

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
