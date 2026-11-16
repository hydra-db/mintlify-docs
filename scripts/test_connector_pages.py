import json
import re
import unittest

from generate_connector_pages import COPY, DIRECTORY, PURPOSES, ROOT, SNAPSHOT, SCOPED_PROVIDER_EXAMPLES, load_copy, pages, prose
from generate_llms_index import page_paths


PRODUCTION = json.loads((ROOT / "scripts/production_providers.json").read_text())["providers"]
PRODUCTION_SLUGS = {provider["provider"].replace("_", "-") for provider in PRODUCTION}
LOGOS = ROOT / "images/connectors"
INDEX = ROOT / "essentials/v2/connector-providers.mdx"
TITLE_LOGOS = ROOT / "connector-logos.css"
DASHBOARD_NAMES = json.loads((ROOT / "scripts/dashboard_provider_names.json").read_text())
FILTER_KEYS = json.loads((ROOT / "scripts/production_filter_keys.json").read_text())["filter_keys"]
PILOT = ("slack", "bigquery", "github", "notion", "stripe", "hubspot")
COPY_SECTIONS = ["Before you connect", "What gets synced", "How it fits into the graph", "Permissions", "Keeping in sync", "Not synced", "Questions this lets you answer", "Good to know"]
# How a connector is built is not a customer's concern. No connector page or the overview may name it.
INTERNAL_TERMS = (
    "moveit", "classic", "native", "golang", "go-native", "python", "tap", "taps", "singer", "lance", "lancedb",
    "normalizer", "normaliser", "renderer", "worker", "workers", "temporal", "kafka", "milvus", "falkordb", "turbolay",
    "dynamodb", "mongodb", "engine", "engines", "appkind", "app_kind", "connector_type", "app source", "app-source",
    "stream", "streams", "pipeline", "pipelines", "webhook", "webhooks", "feature flag", "environment variable",
    "kill switch", "claim check", "drain", "tombstone", "tombstones", "lake", "resolver", "fetcher", "adapter",
    "enricher", "linkref",
)
INTERNAL_PATTERNS = (
    r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]*[A-Z0-9]\b",
    r"\b[\w/-]+\.(?:go|py)\b",
    r"\b(?:AppKind|AppFields|AppProvider|AppExternalID|AppRelations|AppKnowledge\w*|AppMetadata|ACLOrigin\w*)\b",
)
# Words the provider itself shows the customer, each confirmed in that provider's reviewed fact file.
PROVIDER_VOCABULARY = {
    "airtable": {"LAST_MODIFIED_TIME"},  # Airtable's own formula function, named in the setup step the customer follows.
    "github": {"classic"},  # GitHub's "classic" personal access tokens, which the customer creates.
    "hubspot": {"pipeline", "pipelines"},  # HubSpot's deal pipeline, a field the customer sees on every deal.
    "supabase": {"webhook", "webhooks"},  # Supabase's Database Webhooks integration, which the customer installs.
}
OLD_TEMPLATE = (
    "to bring the following content into HydraDB", "The provider description lists these indexed object types",
    "does not enumerate fixed indexed object types", "## What syncs", "## Connect\n", "## Common mistakes", "## Related",
    "A provider page describes indexed content", "Synced content is not automatically private to each person",
    "supplies the credential fields for your deployment", "using that account's credentials where required",
    "Shared destinations are allowed",
)


def copy_pages():
    return {path.stem: (DIRECTORY / f"{path.stem.replace('_', '-')}.mdx").read_text() for path in sorted(COPY.glob("*.json"))}


def body(text):
    return text.split("\n---\n", 1)[1]


def title_logos(slug):
    css = TITLE_LOGOS.read_text() if TITLE_LOGOS.is_file() else ""
    rules = re.findall(rf'^(html(?:\.dark)?)\[data-current-path\$="/essentials/v2/connectors/{slug}"\] #page-title::before \{{[^}}]*url\("/images/connectors/([^"]+)"\)', css, re.M)
    return dict(rules)


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
                self.assertIn("own account ids", text)

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
        self.assertEqual(set(DIRECTORY.glob("*.mdx")), {path for path in generated if path.parent == DIRECTORY})

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
            if slug not in PRODUCTION_SLUGS or load_copy(provider["provider"]):
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
                card_images = sorted(re.findall(r'/images/connectors/([^"]+)"', card.group(1)))
                self.assertEqual(expected, card_images)
                self.assertEqual(f"/images/connectors/{slug}-light.svg" if len(expected) == 2 else expected and f"/images/connectors/{expected[0]}" or None, page_icon(page))
                logos = title_logos(slug)
                if len(expected) == 2:
                    self.assertEqual({"html": f"{slug}-light.svg", "html.dark": f"{slug}-dark.svg"}, logos)
                else:
                    self.assertEqual({"html": expected[0]} if expected else {}, logos)
                shown.update(expected)
        self.assertEqual(files, shown)

    def test_no_page_body_shows_a_standalone_logo(self):
        for slug in PRODUCTION_SLUGS:
            with self.subTest(provider=slug):
                self.assertNotIn("<img", body((DIRECTORY / f"{slug}.mdx").read_text()))

    def test_title_logo_rules_touch_only_connector_page_titles(self):
        css = TITLE_LOGOS.read_text()
        selectors = re.findall(r"^([^\s/{}][^{\n]*?)\s*\{", css, re.M)
        self.assertTrue(selectors)
        for selector in selectors:
            with self.subTest(selector=selector):
                self.assertRegex(selector, r'^html(\.dark)?\[data-current-path(\$|\*)="/essentials/v2/connectors/[a-z0-9-]*"\] #page-title::before$')

    def test_every_page_names_its_production_provider_slug(self):
        production = {provider["provider"] for provider in PRODUCTION}
        for slug in PRODUCTION_SLUGS:
            text = (DIRECTORY / f"{slug}.mdx").read_text()
            with self.subTest(provider=slug):
                named = re.findall(r"Use provider `([^`]+)`", text)
                self.assertLessEqual(len(named), 1)
                for value in named:
                    self.assertIn(value, production)
                    self.assertEqual(slug, value.replace("_", "-"))
        self.assertIn("Use provider `zoho_crm`", (DIRECTORY / "zoho-crm.mdx").read_text())

    def test_display_names_match_the_dashboard(self):
        names = DASHBOARD_NAMES["names"]
        self.assertNotIn("docs_overrides", DASHBOARD_NAMES)
        self.assertEqual({"coda": "Coda"}, DASHBOARD_NAMES["formerly"])
        self.assertEqual("Superhuman", names["coda"])
        self.assertIn('description: "What HydraDB syncs from Superhuman (formerly Coda), and how to connect it."', (DIRECTORY / "coda.mdx").read_text())
        self.assertEqual({provider["provider"] for provider in PRODUCTION}, set(names))
        index = INDEX.read_text()
        for provider, name in names.items():
            slug = provider.replace("_", "-")
            with self.subTest(provider=provider):
                self.assertIn(f'title: "{name}"\n', (DIRECTORY / f"{slug}.mdx").read_text())
                self.assertIn(f'<Card title="{name}"', index)
                self.assertRegex(index, rf'<Card title="{re.escape(name)}"[^\n]* href="/essentials/v2/connectors/{slug}" horizontal />')

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


class PageOpeningAndOrderTest(unittest.TestCase):
    def test_no_page_opens_with_filler(self):
        setup_steps = {step for provider in json.loads(SNAPSHOT.read_text())["providers"] for step in provider.get("setup_guide", [])}
        for path in sorted(DIRECTORY.glob("*.mdx")):
            text = body(path.read_text())
            with self.subTest(page=path.stem):
                for phrase in ("connector to bring", "the following content", "provider description lists"):
                    self.assertNotIn(phrase, text)
                for line in text.splitlines():
                    if "Use the" in line:
                        self.assertIn(re.sub(r"^\d+\. ", "", line), {prose(step) for step in setup_steps}, "only a production setup step may say 'Use the'")
                self.assertRegex(text, r"^\n*\S", "no blank gap under the title block")
                self.assertNotRegex(text, r"(?im)^(this section|the following|below are)")

    def test_groups_follow_the_order_customers_connect_most(self):
        order = ["Messaging & email", "Tickets & project management", "Docs & cloud storage", "Meetings & calendar", "Engineering", "Marketing & analytics", "HR & finance", "CRM & support"]
        config = json.loads((ROOT / "docs.json").read_text())
        group = next(item for item in config["navigation"]["versions"][0]["tabs"][0]["groups"] if item["group"] == "Connector providers")
        self.assertEqual(order, [category["group"] for category in group["pages"][1:]])
        self.assertEqual(order, [title for title in re.findall(r"^## (.+)$", INDEX.read_text(), re.M) if title in order])


class ConnectorCopyPagesTest(unittest.TestCase):
    def test_every_pilot_provider_has_a_valid_copy_file(self):
        for provider in PILOT:
            with self.subTest(provider=provider):
                copy = load_copy(provider)
                self.assertIsNotNone(copy, f"scripts/connector_pages/{provider}.json is missing")
                for document in copy["documents"]:
                    self.assertTrue(document["attributes"], document["name"])
        for path in COPY.glob("*.json"):
            with self.subTest(copy=path.name):
                self.assertIn(path.stem, {row["provider"] for row in PRODUCTION})
                self.assertIsNotNone(load_copy(path.stem))

    def test_a_copy_file_missing_a_section_or_label_is_rejected(self):
        broken = json.loads((COPY / "slack.json").read_text())
        del broken["not_synced"]
        broken["documents"][0]["attributes"][0]["purposes"] = ["Indexed"]
        path = COPY / "zz_broken_copy_probe.json"
        path.write_text(json.dumps(broken))
        try:
            with self.assertRaisesRegex(ValueError, r"missing not_synced.*purposes \['Indexed'\]"):
                load_copy("zz_broken_copy_probe")
        finally:
            path.unlink()

    def test_copy_pages_have_every_section_in_order(self):
        pages_by_provider = copy_pages()
        self.assertLessEqual(set(PILOT), set(pages_by_provider))
        for provider, text in pages_by_provider.items():
            with self.subTest(provider=provider):
                self.assertEqual(COPY_SECTIONS, re.findall(r"^## (.+)$", text, re.M))
                intro = body(text).split("\n## ", 1)[0].strip()
                self.assertTrue(intro, "the page opens with what the connector brings in")

    def test_no_page_names_how_a_connector_is_built(self):
        pages_to_check = {path.stem: body(path.read_text()) for path in DIRECTORY.glob("*.mdx")}
        pages_to_check["connector-providers"] = body(INDEX.read_text())
        for page, text in sorted(pages_to_check.items()):
            allowed = PROVIDER_VOCABULARY.get(page.replace("-", "_"), set())
            prose_only = re.sub(r"\]\([^)]*\)", "]", text)
            with self.subTest(page=page):
                found = [term for term in INTERNAL_TERMS if term not in allowed and re.search(rf"(?i)(?<![\w.-]){re.escape(term)}(?![\w-])", prose_only)]
                self.assertEqual([], found)
                for pattern in INTERNAL_PATTERNS:
                    found = set(re.findall(pattern, prose_only)) - allowed
                    self.assertFalse(found, pattern)

    def test_copy_pages_and_overview_use_no_long_dashes(self):
        for text in [*copy_pages().values(), INDEX.read_text()]:
            self.assertNotIn("\u2014", text)
            self.assertNotIn("\u2013", text)

    def test_every_filter_key_on_a_copy_page_is_published_by_production(self):
        for provider, text in copy_pages().items():
            published = set(FILTER_KEYS[provider])
            filters = re.findall(r'<ResponseField name="([^"]+)" type="Filter', text)
            keys = set(re.findall(r"\badditional_metadata\.\w+", text))
            with self.subTest(provider=provider):
                self.assertTrue(filters)
                self.assertLessEqual(set(filters), published)
                self.assertLessEqual(keys, published)

    def test_attribute_labels_are_the_plain_purpose_labels(self):
        for provider, text in copy_pages().items():
            section = text.split("## What gets synced\n", 1)[1].split("\n## ", 1)[0]
            with self.subTest(provider=provider):
                for kind in re.findall(r'<ResponseField name="[^"]+" type="([^"]+)"', section):
                    self.assertLessEqual(set(kind.split(", ")), set(PURPOSES), kind)

    def test_permissions_section_matches_production(self):
        acl = {row["provider"]: row["acl_supported"] for row in PRODUCTION}
        for provider, text in copy_pages().items():
            section = text.split("## Permissions\n", 1)[1].split("\n## ", 1)[0]
            with self.subTest(provider=provider):
                carries = "This connector can carry" in section
                self.assertEqual(acl[provider], carries)
                if not acl[provider]:
                    self.assertIn("Every synced item is visible to everyone who can query the collection", section)
                    self.assertIn("/essentials/v2/connectors#set-your-own-rule-on-a-resource", section)

    def test_copy_pages_keep_nothing_from_the_old_template(self):
        for provider, text in copy_pages().items():
            with self.subTest(provider=provider):
                for sentence in OLD_TEMPLATE:
                    self.assertNotIn(sentence, text)

    def test_overview_explains_how_to_read_a_page(self):
        index = INDEX.read_text()
        self.assertIn("## How to read a connector page", index)
        self.assertLess(index.index("## How to read a connector page"), index.index("<Columns"))
        self.assertIn("](/essentials/v2/connectors)", index.split("<Columns", 1)[0])


if __name__ == "__main__":
    unittest.main()
