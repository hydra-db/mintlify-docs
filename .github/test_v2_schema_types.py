import pathlib
import runpy
import unittest


CONTRACT = runpy.run_path(str(pathlib.Path(__file__).with_name("verify_v2_public_contract.py")))


class SchemaTypesTest(unittest.TestCase):
    def check_schema(self, schema):
        CONTRACT["ERRORS"].clear()
        CONTRACT["check_schema_types"](schema, "test.Response")
        return CONTRACT["ERRORS"]

    def test_redundant_object_titles_are_rejected(self):
        for title in ("Result object", "Result object.object", "Alias expansion.object", "object[]"):
            with self.subTest(title=title):
                errors = self.check_schema({"type": "object", "additionalProperties": True, "title": title})
                self.assertTrue(any("native object label" in error for error in errors), errors)

    def test_nested_redundant_titles_are_rejected(self):
        errors = self.check_schema({"type": "array", "items": {"type": "object", "additionalProperties": True, "title": "Alias expansion.object"}})
        self.assertTrue(any("native object label" in error for error in errors), errors)

    def test_fixed_values_cannot_become_opaque(self):
        for schema in ({}, {"type": "object"}, {"type": ["object", "null"]}, {"type": "array"}, {"type": "array", "items": {}}):
            with self.subTest(schema=schema):
                self.assertTrue(self.check_schema(schema))

    def test_maps_model_names_and_examples_remain_valid(self):
        for schema in (
            {"type": "object", "additionalProperties": True},
            {"type": "object", "additionalProperties": {}},
            {"type": "object", "additionalProperties": {"type": "integer"}},
            {"type": "object", "properties": {"id": {"type": "string"}}, "title": "SearchAliasExpansionNote"},
            {"type": "string", "example": "Result object.object"},
        ):
            with self.subTest(schema=schema):
                self.assertFalse(self.check_schema(schema))


if __name__ == "__main__":
    unittest.main()
