import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = json.loads((ROOT / "api-reference/v2/openapi.json").read_text())
VERIFIER = ROOT / ".github/verify_v2_public_contract.py"


class PublicContractVerifierTests(unittest.TestCase):
    def run_verifier(self, mutate=None):
        spec = copy.deepcopy(SPEC)
        if mutate is not None:
            mutate(spec)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as fixture:
            json.dump(spec, fixture)
            fixture.flush()
            return subprocess.run(
                [sys.executable, str(VERIFIER)],
                cwd=ROOT,
                env=dict(
                    os.environ,
                    HYDRADB_V2_SPEC_PATH=fixture.name,
                    HYDRADB_V2_SKIP_MUTATION_TESTS="1",
                ),
                capture_output=True,
                text=True,
            )

    def test_current_contract_passes(self):
        result = self.run_verifier()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_update_metadata_sdk_method_metadata(self):
        operation = SPEC["paths"]["/context/{id}/metadata"]["patch"]
        self.assertEqual(operation.get("x-fern-sdk-group-name"), "context")
        self.assertEqual(operation.get("x-fern-sdk-method-name"), "update_metadata")

    def test_inline_success_response_fails(self):
        def mutate(spec):
            spec["paths"]["/query"]["post"]["responses"]["200"]["content"]["application/json"]["schema"] = {
                "type": "object",
                "properties": {"success": {"type": "boolean"}},
            }

        result = self.run_verifier(mutate)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("successful responses need named component schemas", result.stderr)

    def test_inline_array_success_response_fails(self):
        def mutate(spec):
            spec["paths"]["/connectors/providers"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] = {
                "type": "array",
                "items": {"type": "string"},
            }

        result = self.run_verifier(mutate)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("successful responses need named component schemas", result.stderr)

    def test_mixed_success_response_union_fails(self):
        def mutate(spec):
            schema = spec["paths"]["/query"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]
            schema.clear()
            schema["oneOf"] = [
                {"$ref": "#/components/schemas/handler.Envelope-search_V2RetrievalResult"},
                {"type": "object", "properties": {"success": {"type": "boolean"}}},
            ]

        result = self.run_verifier(mutate)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("successful responses need named component schemas", result.stderr)

    def test_all_named_success_response_union_passes(self):
        def mutate(spec):
            response = spec["paths"]["/connectors"]["get"]["responses"]["200"]
            reference = response["content"]["application/json"]["schema"]["$ref"]
            alternate = copy.deepcopy(response)
            alternate["content"]["application/json"]["schema"] = {
                "oneOf": [{"$ref": reference}, {"$ref": reference}],
            }
            spec["paths"]["/connectors"]["get"]["responses"]["201"] = alternate

        result = self.run_verifier(mutate)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_non_component_response_reference_fails(self):
        def mutate(spec):
            spec["paths"]["/query"]["post"]["responses"]["200"]["content"]["application/json"]["schema"] = {
                "$ref": "#/paths/~1query/post/requestBody",
            }

        result = self.run_verifier(mutate)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("successful responses need named component schemas", result.stderr)

    def test_security_scheme_response_reference_reports_contract_error(self):
        def mutate(spec):
            spec["paths"]["/query"]["post"]["responses"]["200"]["content"]["application/json"]["schema"] = {
                "$ref": "#/components/securitySchemes/BearerAuth",
            }

        result = self.run_verifier(mutate)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("successful responses need named component schemas", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_missing_success_response_schema_fails(self):
        def mutate(spec):
            del spec["paths"]["/query"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]

        result = self.run_verifier(mutate)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("response content needs a schema", result.stderr)

    def test_missing_success_response_content_fails(self):
        def mutate(spec):
            del spec["paths"]["/query"]["post"]["responses"]["200"]["content"]

        result = self.run_verifier(mutate)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("successful body responses need content", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_generic_object_display_title_fails(self):
        def mutate(spec):
            spec["components"]["schemas"]["search.V2Chunk"]["properties"]["id"]["title"] = "Result object"

        result = self.run_verifier(mutate)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("generic object display title", result.stderr)

    def test_array_item_generic_object_display_title_fails(self):
        def mutate(spec):
            spec["components"]["schemas"]["search.V2RetrievalResult"]["properties"]["chunks"]["items"]["title"] = "Object"

        result = self.run_verifier(mutate)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("generic object display title", result.stderr)

    def test_union_branch_generic_object_display_title_fails(self):
        def mutate(spec):
            data = spec["components"]["schemas"]["handler.Envelope-search_V2RetrievalResult"]["properties"]["data"]
            data["oneOf"][0]["title"] = "Search result object"

        result = self.run_verifier(mutate)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("generic object display title", result.stderr)

    def test_non_generic_object_word_in_display_title_passes(self):
        def mutate(spec):
            spec["components"]["schemas"]["search.V2Chunk"]["properties"]["id"]["title"] = "Objectification identifier"

        result = self.run_verifier(mutate)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_component_display_title_fails(self):
        def mutate(spec):
            spec["components"]["schemas"]["search.V2Chunk"]["title"] = "Search chunk"

        result = self.run_verifier(mutate)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("component display titles obscure", result.stderr)

    def test_inline_query_nested_model_fails(self):
        def mutate(spec):
            spec["components"]["schemas"]["search.V2RetrievalResult"]["properties"]["alias_expansions"] = {
                "type": "array",
                "items": copy.deepcopy(spec["components"]["schemas"]["search.AliasExpansionNote"]),
            }

        result = self.run_verifier(mutate)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("alias_expansions must use only the named search.AliasExpansionNote model", result.stderr)

    def assert_inline_query_model_fails(self, field, target):
        def mutate(spec):
            value = spec["components"]["schemas"]["search.V2RetrievalResult"]["properties"][field]
            reference = f"#/components/schemas/{target}"

            def inline_reference(node):
                if isinstance(node, dict):
                    if node.get("$ref") == reference:
                        node.clear()
                        node.update(copy.deepcopy(spec["components"]["schemas"][target]))
                        return True
                    return any(inline_reference(child) for child in node.values())
                if isinstance(node, list):
                    return any(inline_reference(child) for child in node)
                return False

            self.assertTrue(inline_reference(value), f"{field} does not reference {target}")

        result = self.run_verifier(mutate)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"Query result {field} must use only the named {target} model", result.stderr)

    def test_chunks_named_model_fails_when_inlined(self):
        self.assert_inline_query_model_fails("chunks", "search.V2Chunk")

    def test_code_search_named_model_fails_when_inlined(self):
        self.assert_inline_query_model_fails("code_search", "search.CodeSearchResult")

    def test_graph_context_named_model_fails_when_inlined(self):
        self.assert_inline_query_model_fails("graph_context", "search.GraphContext")

    def test_temporal_duration_named_model_fails_when_inlined(self):
        self.assert_inline_query_model_fails("temporal_duration", "search.TemporalDuration")

    def test_temporal_facts_named_model_fails_when_inlined(self):
        self.assert_inline_query_model_fails("temporal_facts", "search.TemporalFact")

    def test_query_envelope_anonymous_result_fails(self):
        def mutate(spec):
            envelope = spec["components"]["schemas"]["handler.Envelope-search_V2RetrievalResult"]
            envelope["properties"]["data"]["oneOf"][0] = {
                "type": "object",
                "properties": {"result": {"type": "string"}},
            }

        result = self.run_verifier(mutate)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Query envelope data must contain exactly the named", result.stderr)

    def test_additional_context_value_model_fails(self):
        def mutate(spec):
            field = spec["components"]["schemas"]["search.V2RetrievalResult"]["properties"]["additional_context"]
            field["additionalProperties"] = {"type": "object", "properties": {"id": {"type": "string"}}}

        result = self.run_verifier(mutate)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("additional_context must use only the named search.V2Chunk model", result.stderr)

    def test_query_envelope_extra_anonymous_branch_fails(self):
        def mutate(spec):
            envelope = spec["components"]["schemas"]["handler.Envelope-search_V2RetrievalResult"]
            envelope["properties"]["data"]["oneOf"].append({"type": "object", "properties": {"raw": {"type": "string"}}})

        result = self.run_verifier(mutate)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must contain exactly", result.stderr)

    def test_query_envelope_missing_markdown_branch_fails(self):
        def mutate(spec):
            data = spec["components"]["schemas"]["handler.Envelope-search_V2RetrievalResult"]["properties"]["data"]
            data["oneOf"] = [copy.deepcopy(data["oneOf"][0]), copy.deepcopy(data["oneOf"][0])]

        result = self.run_verifier(mutate)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must contain exactly", result.stderr)

    def test_nested_model_extra_anonymous_branch_fails(self):
        def mutate(spec):
            field = spec["components"]["schemas"]["search.V2RetrievalResult"]["properties"]["alias_expansions"]
            field["items"] = {"oneOf": [field["items"], {"type": "object", "properties": {"raw": {"type": "string"}}}]}

        result = self.run_verifier(mutate)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("alias_expansions must use only", result.stderr)


if __name__ == "__main__":
    unittest.main()
