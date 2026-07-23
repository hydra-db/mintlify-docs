from __future__ import annotations

import unittest

from scripts.verify_openapi_public import validate_spec


def spec_with_description(description: str) -> dict:
    return {
        "openapi": "3.0.3",
        "paths": {
            "/query": {
                "post": {
                    "description": description,
                    "responses": {
                        "500": {"description": "Internal Server Error"}
                    },
                }
            }
        },
        "components": {"schemas": {"QueryRequest": {"type": "object"}}},
    }


class PublicOpenAPIContractTests(unittest.TestCase):
    def test_accepts_public_contract_language(self) -> None:
        spec = spec_with_description(
            "Queries knowledge, memories, or both within the selected database."
        )

        self.assertEqual(validate_spec(spec), [])

    def test_rejects_private_endpoint(self) -> None:
        spec = spec_with_description("Public description.")
        spec["paths"]["/connectors/{id}/credentials"] = {"patch": {}}

        violations = validate_spec(spec)

        self.assertTrue(
            any("private endpoint must not be published" in item for item in violations)
        )

    def test_rejects_source_derived_component_name(self) -> None:
        spec = spec_with_description("Public description.")
        spec["components"]["schemas"][
            "github_com_hydradb_internal_service.Result"
        ] = {"type": "object"}

        violations = validate_spec(spec)

        self.assertTrue(any("private source name" in item for item in violations))

    def test_rejects_private_description_details(self) -> None:
        private_descriptions = (
            "Serialized with MarshalJSON before returning.",
            "Validated by the TenantAliases middleware.",
            "Stored in DynamoDB before the Temporal workflow starts.",
            "Implemented by SearchService for PRO-1185.",
            "Requires the X-Cortex-Secret header.",
            "Controlled by a repo-level config.",
        )

        for description in private_descriptions:
            with self.subTest(description=description):
                violations = validate_spec(spec_with_description(description))
                self.assertEqual(len(violations), 1)
                self.assertIn("/paths/~1query/post/description", violations[0])


if __name__ == "__main__":
    unittest.main()
