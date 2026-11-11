"""Execute every cookbook's Python against the released SDK's method signatures.

No network and no credentials: each SDK method is replaced by a stub that first
binds the call to the real method's signature (so an unknown or misspelled
argument fails), checks the JSON payloads against the documented shapes, and
returns a minimal response.
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
        self.calls = []
        for owner, name, handler in [
            (client.databases, "create", self.create),
            (client.databases, "status", self.database_status),
            (client.context, "ingest", self.ingest),
            (client.context, "status", self.context_status),
            (client, "query", self.query),
            (client.connectors, "create", lambda **k: NS(connector_id="conn_1")),
            (client.connectors, "discover", lambda *a, **k: NS(resources=[NS(id="C1", resource_type="channel", name="general")])),
            (client.connectors, "configure", lambda *a, **k: NS()),
        ]:
            setattr(owner, name, self.checked(name, getattr(owner, name), handler))

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
        return NS(data=NS())

    def database_status(self, database):
        return NS(data=NS(infra=NS(ready_for_ingestion=True)))

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
                    handle.read()
                    ids.append(item["id"])
            for item in json.loads(app_knowledge or "[]"):
                test.assertLessEqual(set(item), APP_SOURCE_KEYS)
                test.assertIn(item["kind"], KINDS)
                test.assertEqual(item["kind"], item["fields"]["kind"])
                test.assertLessEqual(set(item.get("metadata", {})), self.declared)
                ids.append(item["id"])
        return NS(data=NS(results=[NS(id=i) for i in ids]))

    def context_status(self, database, collection, ids):
        self.test.assertIn(collection, self.collections)
        self.test.assertTrue(ids)
        return NS(data=NS(statuses=[NS(indexing_status="completed") for _ in ids]))

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
        chunk = NS(chunk_content="text", source_title="title", additional_metadata=None)
        source = NS(id="source_1", title="title", url=None)
        return NS(
            data=NS(llm_prompt="context", chunks=[chunk], sources=[source]),
            meta=NS(request_id="req_1"),
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
                for step in ("create", "ingest", "status", "query"):
                    self.assertIn(step, stubs.calls)

    def test_index_links_every_cookbook(self):
        index = (ROOT / "cookbooks/v2/index.mdx").read_text()
        for path in COOKBOOKS:
            self.assertIn(f'href="/cookbooks/v2/{path.stem}"', index)


if __name__ == "__main__":
    unittest.main()
