"""Execute every cookbook's Python against the released SDK's method signatures.

No network and no credentials: each SDK method is replaced by a stub that first
binds the call to the real method's signature (so an unknown or misspelled
argument fails), checks the JSON payloads against the documented shapes, and
returns the SDK's own response model for that method, filled with public fields
only. A cookbook that reads a field the public API does not return fails here.
"""

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

APP_SOURCE_KEYS = {
    "id", "database", "collection", "title", "kind", "provider", "external_id", "url",
    "timestamp", "fields", "metadata", "additional_metadata", "relations",
    "app_relations", "acl", "attachments", "comments",
}
KINDS = {"email", "message", "ticket", "knowledge_base", "comment", "meeting", "custom"}
DOCUMENT_METADATA_KEYS = {"id", "metadata", "additional_metadata", "relations"}
MEMORY_KEYS = {
    "id", "title", "text", "user_assistant_pairs", "is_markdown", "infer",
    "custom_instructions", "user_name", "metadata", "additional_metadata",
}
OPERATORS = {"equals", "contains", "contains_any"}
# fields.* per kind, from the field reference in essentials/v2/context.mdx.
KIND_FIELDS = {
    "email": {"subject", "body", "from", "to", "cc", "bcc", "thread_id", "reply_to_id",
              "forwarded_from_id", "created_at", "url"},
    "message": {"body", "author", "thread_id", "parent_id", "mentions", "created_at",
                "edited_at", "url"},
    "ticket": {"title", "description", "status", "priority", "assignee", "reporter",
               "parent_id", "linked_issue_ids", "created_at", "updated_at", "url"},
    "knowledge_base": {"title", "body", "parent_id", "created_by", "updated_by",
                       "created_at", "updated_at", "url"},
    "comment": {"body", "author", "parent_id", "thread_id", "created_at", "updated_at",
                "url", "properties"},
    "meeting": {"title", "body", "host", "host_email", "participants", "started_at",
                "updated_at", "labels", "url"},
    "custom": {"body", "parent_id", "thread_id", "data"},
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
        self.restricted = set()  # Collections holding a source that carries an acl.
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

    def connector_create(self, **kwargs):
        self.test.assertNotIn("enforce_acl", kwargs)  # Needs a dashboard session.
        return sdk.HandlerConnectorCreateResponse(connector_id="conn_1")

    def connector_discover(self, id):
        return sdk.HandlerDiscoverResponseBody(resources=[
            sdk.HandlerDiscoveredResourceDto(id="C1", resource_type="channel", name="general")
        ])

    def connector_configure(self, id, resources):
        for resource in resources:
            sdk.HandlerResourceMapping(**resource)
            self.test.assertLessEqual(set(resource), {"resource_id", "resource_type", "name"})
        return sdk.HandlerConfigureResponse()

    def ingest(self, type, database, collection, documents=None, document_metadata=None,
               app_knowledge=None, memories=None):
        test = self.test
        self.collections.add(collection)
        ids = []
        if type == "memory":
            test.assertIsNone(app_knowledge)
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
                    test.assertIn(Path(filename).suffix, UPLOAD_EXTENSIONS, filename)
                    test.assertTrue(handle.read(), f"{filename} is empty")
                    ids.append(item["id"])
            for item in json.loads(app_knowledge or "[]"):
                test.assertLessEqual(set(item), APP_SOURCE_KEYS)
                test.assertIn(item["kind"], KINDS)
                test.assertEqual(item["kind"], item["fields"]["kind"])
                test.assertLessEqual(
                    set(item["fields"]) - {"kind"}, KIND_FIELDS[item["kind"]], item["id"]
                )
                test.assertLessEqual(set(item.get("metadata", {})), self.declared)
                if item.get("acl"):
                    self.restricted.add(collection)
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
            test.assertIn("acl", kwargs, "a query over restricted sources must send the caller's acl")
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
    def run_cookbook(self, path):
        setup = python_blocks(SETUP)["hydra.py"]
        files = python_blocks(path)
        self.assertTrue(files, f"{path.name} has no labelled Python blocks")
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ, {"HYDRADB_API_KEY": "test", "OPENAI_API_KEY": "test"}
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
                for step in ("DatabasesClient.create", "DatabasesClient.status",
                             "ContextClient.ingest", "ContextClient.status", "query"):
                    self.assertIn(step, stubs.calls)

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

    def test_setup_waits_for_completed_when_asked(self):
        hydra, stubs = self.load_setup()
        stubs.collections.add("docs")
        stubs.status = "graph_creation"
        with mock.patch("time.sleep"):
            hydra.wait_indexed("db", "docs", ["source_1"])  # Searchable is enough by default.
        with mock.patch("time.sleep", side_effect=TimeoutError), self.assertRaises(TimeoutError):
            hydra.wait_indexed("db", "docs", ["source_1"], until="completed")

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
