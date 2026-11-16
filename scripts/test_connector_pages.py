import json
import re
import unittest

from generate_connector_pages import DIRECTORY, ROOT, SNAPSHOT, SCOPED_PROVIDER_EXAMPLES, pages, prose
from generate_llms_index import page_paths


class ConnectorPagesTest(unittest.TestCase):
    def test_scoped_provider_examples_are_distinct_and_match_the_guide(self):
        guide = (ROOT / "essentials/v2/connectors.mdx").read_text()
        rows = re.findall(r"^\| `(slack|github|linear|notion)` \| `([^`]+)` \| `([^`]+)` \|$", guide, re.M)
        mapping = {provider: (first, second) for provider, first, second in rows}
        self.assertEqual({"slack", "github", "linear", "notion"}, set(mapping))
        self.assertEqual(SCOPED_PROVIDER_EXAMPLES, mapping)
        self.assertIn("does not derive or validate the native account identity or reject duplicate scopes", guide)
        self.assertIn("Filter on `connector_id`", guide)
        self.assertIn("same database and collection", guide)
        for provider, pair in mapping.items():
            self.assertTrue(all(value.strip() for value in pair))
            self.assertNotEqual(pair[0], pair[1])
            text = (DIRECTORY / f"{provider}.mdx").read_text()
            with self.subTest(provider=provider):
                self.assertIn("`provider_account_scope`", text)
                for value in pair:
                    self.assertIn(f"`{value}`", text)
                self.assertIn("caller-provided example identifiers", text)
                self.assertIn("not validated native account-ID formats", text)

    def test_all_provider_pages_keep_one_account_per_connector_and_human_enforcement(self):
        paths = list(DIRECTORY.glob("*.mdx"))
        self.assertEqual(98, len(paths))
        for path in paths:
            text = path.read_text()
            with self.subTest(provider=path.stem):
                self.assertIn("one connector per account", text)
                self.assertIn("collection names do not authorize access", text)
                self.assertIn("API-key requests must omit `enforce_acl`", text)
                self.assertIn("including `false`", text)
                self.assertNotRegex(text, r"(?i)(?:multiple|several|two) accounts per connector")
                self.assertNotIn("With acl_supported on for the provider", text)

    def test_setup_placeholders_and_json_are_safe_mdx(self):
        self.assertEqual("Use `<client_id>`", prose("Use <client_id>"))
        self.assertEqual("`https://<org>.okta.com/api/v1/`", prose("https://<org>.okta.com/api/v1/"))
        self.assertEqual('&#123;"table": "a.b"&#125;', prose('{"table": "a.b"}'))

    def test_provider_pages_match_the_source_snapshot(self):
        generated = pages()
        for path, content in generated.items():
            with self.subTest(page=str(path.relative_to(ROOT))):
                self.assertEqual(content, path.read_text())
        self.assertEqual(set(DIRECTORY.glob("*.mdx")), set(generated) - {ROOT / "essentials/v2/connector-providers.mdx"})

    def test_every_provider_page_is_registered_and_linked_from_the_catalog(self):
        config = json.loads((ROOT / "docs.json").read_text())
        registered = page_paths(config["navigation"]["versions"][0])
        index = (ROOT / "essentials/v2/connector-providers.mdx").read_text()
        for path in DIRECTORY.glob("*.mdx"):
            slug = str(path.relative_to(ROOT).with_suffix(""))
            self.assertIn(slug, registered)
            self.assertIn(f"](/{slug})", index)
        self.assertIn("/essentials/v2/connector-providers", (ROOT / "essentials/v2/connectors.mdx").read_text())

    def test_snapshot_records_source_commits_and_preserves_unknown_object_types(self):
        snapshot = json.loads(SNAPSHOT.read_text())
        provenance = snapshot["provenance"]
        self.assertTrue(provenance["application"]["commit"].startswith("d76958b5"))
        self.assertTrue(provenance["dashboard"]["commit"].startswith("f611226e"))
        self.assertRegex(provenance["export_sha256"], r"^[a-f0-9]{64}$")
        providers = {provider["provider"]: provider for provider in snapshot["providers"]}
        self.assertEqual(98, len(providers))
        for identifier in ("airtable", "bigquery", "smartsheet", "supabase"):
            self.assertEqual([], providers[identifier]["indexed_object_types"])
        self.assertNotIn("zoho", providers)
        self.assertNotIn("googleanalytics", providers)
        self.assertEqual("Zoho CRM", providers["zoho-crm"]["name"])
        self.assertEqual("Google Analytics", providers["ga4"]["name"])
        self.assertIn("no history backfill", providers["supabase"]["summary"].lower())

    def test_provider_pages_list_only_the_exact_indexed_object_types(self):
        snapshot = json.loads(SNAPSHOT.read_text())
        for provider in snapshot["providers"]:
            slug = provider["provider"].replace("_", "-")
            text = (DIRECTORY / f"{slug}.mdx").read_text()
            section = text.split("## What syncs\n", 1)[1].split("## Connect", 1)[0]
            with self.subTest(provider=provider["provider"]):
                self.assertEqual(provider["indexed_object_types"], re.findall(r"^- `([^`]+)`$", section, re.M))
        monday = (DIRECTORY / "monday.mdx").read_text()
        self.assertIn("fails open with a persistent warning", monday)


if __name__ == "__main__":
    unittest.main()
