import json
import re
import unittest

from generate_connector_pages import DIRECTORY, ROOT, SNAPSHOT, SCOPED_PROVIDER_EXAMPLES, pages, prose
from generate_llms_index import page_paths


PRODUCTION = json.loads((ROOT / "scripts/production_providers.json").read_text())["providers"]
PRODUCTION_SLUGS = {provider["provider"].replace("_", "-") for provider in PRODUCTION}
LOGOS = ROOT / "images/connectors"
INDEX = ROOT / "essentials/v2/connector-providers.mdx"


def page_icon(text):
    match = re.search(r'^icon: "([^"]+)"$', text.split("\n---\n", 1)[0], re.M)
    return match.group(1) if match else None


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
        self.assertEqual(len(PRODUCTION), len(paths))
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
        self.assertEqual(set(DIRECTORY.glob("*.mdx")), set(generated) - {ROOT / "essentials/v2/connector-providers.mdx", ROOT / "docs.json"})

    def test_every_provider_page_is_registered_and_linked_from_the_catalog(self):
        config = json.loads((ROOT / "docs.json").read_text())
        registered = page_paths(config["navigation"]["versions"][0])
        index = (ROOT / "essentials/v2/connector-providers.mdx").read_text()
        for path in DIRECTORY.glob("*.mdx"):
            slug = str(path.relative_to(ROOT).with_suffix(""))
            self.assertIn(slug, registered)
            self.assertIn(f'href="/{slug}"', index)
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
            if slug not in PRODUCTION_SLUGS:
                continue
            text = (DIRECTORY / f"{slug}.mdx").read_text()
            section = text.split("## What syncs\n", 1)[1].split("## Connect", 1)[0]
            with self.subTest(provider=provider["provider"]):
                self.assertEqual(provider["indexed_object_types"], re.findall(r"^- `([^`]+)`$", section, re.M))
        monday = (DIRECTORY / "monday.mdx").read_text()
        self.assertIn("fails open with a persistent warning", monday)

    def test_pages_exist_exactly_for_the_production_providers(self):
        self.assertEqual(70, len(PRODUCTION_SLUGS))
        self.assertEqual(PRODUCTION_SLUGS, {path.stem for path in DIRECTORY.glob("*.mdx")})
        snapshot = {provider["provider"].replace("_", "-") for provider in json.loads(SNAPSHOT.read_text())["providers"]}
        self.assertLessEqual(PRODUCTION_SLUGS, snapshot)

    def test_every_logo_is_shown_and_every_shown_logo_exists(self):
        files = {path.name for path in LOGOS.glob("*.svg")}
        index = INDEX.read_text()
        shown = set()
        for slug in PRODUCTION_SLUGS:
            pair = {f"{slug}-light.svg", f"{slug}-dark.svg"}
            expected = sorted(pair) if pair <= files else [f"{slug}.svg"] if f"{slug}.svg" in files else []
            page = (DIRECTORY / f"{slug}.mdx").read_text()
            card = re.search(rf'<Card title="[^"]+"(.*?) href="/essentials/v2/connectors/{slug}" horizontal />', index)
            with self.subTest(provider=slug):
                self.assertIsNotNone(card)
                page_images = sorted(re.findall(r'<img src="/images/connectors/([^"]+)"', page.split("\n---\n", 1)[1].split("## What syncs", 1)[0]))
                card_images = sorted(re.findall(r'/images/connectors/([^"]+)"', card.group(1)))
                self.assertEqual(expected, page_images)
                self.assertEqual(expected, card_images)
                self.assertEqual(f"/images/connectors/{slug}-light.svg" if len(expected) == 2 else expected and f"/images/connectors/{expected[0]}" or None, page_icon(page))
                if len(expected) == 2:
                    self.assertIn(f'{slug}-light.svg" alt="{re.search(r"^title: \"([^\"]+)\"", page, re.M).group(1)} logo" className="connector-page-logo block dark:hidden"', page)
                    self.assertIn(f'{slug}-dark.svg" alt="', page)
                    self.assertRegex(page, rf'{slug}-dark.svg" alt="[^"]*" className="connector-page-logo hidden dark:block"')
                shown.update(expected)
        self.assertEqual(files, shown)

    def test_monochrome_marks_change_only_their_ink_between_themes(self):
        pairs = sorted(path.name[: -len("-light.svg")] for path in LOGOS.glob("*-light.svg"))
        self.assertEqual(["attio", "datadog", "github", "gong", "guru", "launchdarkly", "notion", "pipedrive", "sentry", "smartsheet", "twitter", "typeform", "zendesk"], pairs)
        for slug in pairs:
            light, dark = ((LOGOS / f"{slug}-{theme}.svg").read_text() for theme in ("light", "dark"))
            inks = lambda text: set(re.findall(r'(?:fill|stroke)="(#[0-9a-fA-F]+)"', text))
            with self.subTest(provider=slug):
                self.assertEqual({"#363636"}, inks(light))
                self.assertEqual({"#cccccc"}, inks(dark))
                self.assertEqual(light.replace("#363636", "#cccccc"), dark)
                self.assertFalse((LOGOS / f"{slug}.svg").exists())

    def test_logos_sit_on_the_dashboard_tile_in_both_themes(self):
        css = (ROOT / "style.css").read_text()
        self.assertRegex(css, r'\nimg\[src\*="/images/connectors/"\]\s*\{[^}]*background-color:\s*rgba\(0, 0, 0, 0\.04\)')
        self.assertRegex(css, r'\.dark img\[src\*="/images/connectors/"\]\s*\{[^}]*background-color:\s*rgba\(255, 255, 255, 0\.05\)')
        self.assertRegex(css, r'\.dark img\[src\*="/images/connectors/"\]\[src\*="-light\.svg"\]\s*\{[^}]*background-color:\s*rgba\(255, 255, 255, 0\.88\)')

    def test_navigation_groups_connectors_into_collapsed_categories(self):
        config = json.loads((ROOT / "docs.json").read_text())
        registered = {page for page in page_paths(config["navigation"]["versions"][0]) if page.startswith("essentials/v2/connectors/")}
        self.assertEqual(PRODUCTION_SLUGS, {page.rsplit("/", 1)[1] for page in registered})
        for page in registered:
            self.assertTrue((ROOT / f"{page}.mdx").is_file(), page)
        group = next(item for item in config["navigation"]["versions"][0]["tabs"][0]["groups"] if item["group"] == "Connector providers")
        self.assertEqual("essentials/v2/connector-providers", group["pages"][0])
        categories = group["pages"][1:]
        self.assertEqual(8, len(categories))
        index = INDEX.read_text()
        for category in categories:
            with self.subTest(category=category["group"]):
                self.assertNotIn("expanded", category)
                self.assertTrue(all(isinstance(page, str) for page in category["pages"]))
                titles = [re.search(r'^title: "([^"]+)"', (ROOT / f"{page}.mdx").read_text(), re.M).group(1) for page in category["pages"]]
                self.assertEqual(sorted(titles, key=str.casefold), titles)
                section = index.split(f"## {category['group']}\n", 1)[1].split("\n## ", 1)[0]
                self.assertEqual([f"/{page}" for page in category["pages"]], re.findall(r'href="([^"]+)"', section))
        self.assertEqual(len(PRODUCTION_SLUGS), sum(len(category["pages"]) for category in categories))

if __name__ == "__main__":
    unittest.main()
