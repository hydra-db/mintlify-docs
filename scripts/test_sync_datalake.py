import json
import unittest
from pathlib import Path
from unittest.mock import patch

import sync_datalake as sync


class DataLakeTests(unittest.TestCase):
    def test_moved_pages_keep_their_product_area_tags(self):
        root = Path(__file__).resolve().parents[1]
        taxonomy = json.loads((root / "scripts/datalake_taxonomy.json").read_text())
        with patch.object(sync, "git", return_value="test-commit"):
            items = sync.build_items(root, taxonomy, "https://docs.hydradb.com", "db", "collection")
        areas = {item["external_id"]: item["metadata"]["product_area"] for item in items}
        expected = {
            "essentials/context": "ingestion",
            "essentials/connectors": "connectors",
            "essentials/query": "retrieval",
            "essentials/bring-your-own-graph": "graph,ingestion",
            "essentials/databases-and-collections": "multi_tenancy",
            "essentials/metadata": "metadata",
            "api-reference/sdks": "api,sdk",
            "api-reference/endpoint/databases-overview": "api,multi_tenancy",
            "api-reference/endpoint/context-overview": "api,ingestion",
            "api-reference/endpoint/query-overview": "api,retrieval",
            "api-reference/endpoint/connectors-overview": "api,connectors",
            "api-reference/endpoint/webhooks-overview": "api,webhooks"
        }
        for page, expected_areas in expected.items():
            with self.subTest(page=page):
                self.assertEqual(expected_areas, areas[page])
        for prefix, _ in taxonomy["pages"]:
            with self.subTest(prefix=prefix):
                self.assertTrue(any(page.startswith(prefix) for page in areas))

    def test_invalid_comparison_aborts(self):
        with self.assertRaises((RuntimeError, sync.subprocess.CalledProcessError)):
            sync.git(Path.cwd(), "diff", "--name-only", "nonexistent-ref...HEAD")

    def test_workflow_reconciles_all_pages_after_failed_runs(self):
        workflow = (Path(__file__).parents[1] / ".github/workflows/sync-datalake.yml").read_text()
        self.assertIn("python scripts/sync_datalake.py --full", workflow)
        self.assertNotIn("--since-ref", workflow)

    def test_delete_uses_strict_status_and_checks_each_result(self):
        api = sync.Api("test", "unused", "db", "collection")
        failures = [
            {"success": False, "data": {"deleted_count": 0}},
            {"success": True, "data": {"results": [{"id": "one", "status": "failed"}]}},
            {"success": True, "data": {"results": []}},
        ]
        for payload in failures:
            with self.subTest(payload=payload), patch.object(sync, "api_request", return_value=(200, json.dumps(payload))) as request:
                with self.assertRaises(RuntimeError):
                    api.delete(["one"])
                self.assertEqual(request.call_args.kwargs["extra_headers"], {"X-HydraDB-Delete-Status": "strict"})

    def test_response_schemas_include_nested_fields_and_bound_cycles(self):
        spec = {
            "paths": {"/test": {"get": {"responses": {"200": {
                "description": "OK", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Node"}}}
            }}}}},
            "components": {"schemas": {"Node": {"type": "object", "properties": {
                "answer": {"type": "string", "description": "The answer text"},
                "next": {"$ref": "#/components/schemas/Node"},
                "items": {"type": "array", "items": {"$ref": "#/components/schemas/Node"}},
            }}}},
        }
        body = sync.render_operation(spec, "GET", "/test")
        self.assertIn("answer", body)
        self.assertIn("The answer text", body)
        self.assertIn("items", body)
        self.assertLess(len(body), 20000)

    def test_delete_accepts_confirmed_success_and_already_absent(self):
        api = sync.Api("test", "unused", "db", "collection")
        success = {"success": True, "data": {"results": [{"id": "one", "deleted": True}]}}
        for response in [(200, json.dumps(success)), (404, "{}")]:
            with self.subTest(response=response), patch.object(sync, "api_request", return_value=response):
                api.delete(["one"])

    def test_delete_propagates_indexing_and_store_failures(self):
        api = sync.Api("test", "unused", "db", "collection")
        for status in (409, 500):
            with self.subTest(status=status), patch.object(sync, "api_request", return_value=(status, "failed")):
                with self.assertRaises(sync.ApiError):
                    api.delete(["one"])


if __name__ == "__main__":
    unittest.main()
