import json
import re
import unittest

from generate_connector_pages import COPY, DIRECTORY, LOGOS as LOGO_DIRECTORY, PLACEHOLDER_MARK, ROOT, SNAPSHOT, SCOPED_PROVIDER_EXAMPLES, PROVIDER_ORDER, artwork, load_copy, logo, mdx, pages, placeholder_logos
from generate_llms_index import page_paths


PRODUCTION = json.loads((ROOT / "scripts/production_providers.json").read_text())["providers"]
PRODUCTION_SLUGS = {provider["provider"].replace("_", "-") for provider in PRODUCTION}
LOGOS = ROOT / "images/connectors"
INDEX = ROOT / "essentials/v2/connector-providers.mdx"
TITLE_LOGOS = ROOT / "connector-logos.css"
DASHBOARD_NAMES = json.loads((ROOT / "scripts/dashboard_provider_names.json").read_text())
FILTER_KEYS = json.loads((ROOT / "scripts/production_filter_keys.json").read_text())["filter_keys"]
COPY_SECTIONS = ["Permissions", "Before you connect", "Keeping in sync", "What gets synced", "How it fits into the graph", "Not synced"]
# The badge vocabulary a customer reads on every attribute. The overview defines each one; provider pages do not repeat it.
BADGES = ("Searchable text", "Query filter", "Result date", "Graph connection", "Graph identity", "Access rule", "Stored only")
GRAPH_CLASSES = ("Email", "Message", "Ticket", "Page", "Contact", "Comment", "Meeting", "Record", "Container", "Iteration")
# Wording that describes HydraDB's machinery or hedges instead of stating the outcome. Checked on pages rendered from copy files and the overview.
COPY_PAGE_TERMS = (
    "declared", "declares", "shared model", "second pass", "walks", "walked", "acl_supported", "entities", "backfill",
    "depending on your deployment", "matched to messages through", "fails open", "fail-open",
)
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
    "copper": {"pipeline", "pipelines"},  # Copper's opportunity pipelines, which the customer sets up in Copper.
    "datadog": {"declared"},  # Datadog's "declared incident" and its declaration time, shown on every incident.
    "github": {"classic"},  # GitHub's "classic" personal access tokens, which the customer creates.
    "gitlab": {"pipelines"},  # GitLab's CI/CD pipelines, named in Not synced so customers know they are left out.
    "hubspot": {"pipeline", "pipelines"},  # HubSpot's deal pipeline, a field the customer sees on every deal.
    "launchdarkly": {"feature flag"},  # LaunchDarkly's own name for the flags it syncs.
    "mailchimp": {"classic"},  # Mailchimp's "classic automations", told apart from Customer Journeys.
    "okta": {"SAML_2_0"},  # An Okta sign-on mode value, shown as Okta returns it.
    "pipedrive": {"pipeline", "pipelines"},  # Pipedrive's deal pipelines, a field the customer sees on every deal.
    "posthog": {"feature flag"},  # PostHog's product name for the flags it syncs.
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


def section(text, title):
    return text.split(f"\n## {title}\n", 1)[1].split("\n## ", 1)[0]


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
                self.assertIn("labels you choose", text)
                self.assertIn("own account ids", text)

    def test_all_provider_pages_keep_one_account_per_connector_and_human_enforcement(self):
        paths = list(DIRECTORY.glob("*.mdx"))
        self.assertEqual(len(PRODUCTION), len(paths))
        for path in paths:
            text = path.read_text()
            with self.subTest(provider=path.stem):
                self.assertRegex(text, r"\nOne (connector|token) (reads|covers) ")
                self.assertIn("API-key requests must omit `enforce_acl`", text)
                self.assertIn("including `false`", text)
                self.assertNotRegex(text, r"(?i)(?:multiple|several|two) accounts per connector")
                self.assertNotIn("With acl_supported on for the provider", text)

    def test_copy_text_is_safe_mdx_outside_inline_code(self):
        self.assertEqual("Use &lt;client_id&gt; or `<client_id>`", mdx("Use <client_id> or `<client_id>`"))
        self.assertEqual('&#123;"table": "a.b"&#125; and `{"table": "a.b"}`', mdx('{"table": "a.b"} and `{"table": "a.b"}`'))

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

    def test_every_production_provider_renders_from_its_copy_file(self):
        for row in PRODUCTION:
            with self.subTest(provider=row["provider"]):
                copy = load_copy(row["provider"])
                text = (DIRECTORY / f"{row['provider'].replace('_', '-')}.mdx").read_text()
                self.assertIn(f"description: {json.dumps(copy['description'])}\n", text)
        self.assertEqual({row["provider"] for row in PRODUCTION}, {path.stem for path in COPY.glob("*.json")})

    def test_a_provider_without_a_copy_file_stops_the_run(self):
        with self.assertRaisesRegex(ValueError, r"zz_missing_probe\.json: every production provider needs a copy file"):
            load_copy("zz_missing_probe")

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
            expected = sorted(pair) if pair <= files else [f"{slug}.svg"]
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
        coda = (DIRECTORY / "coda.mdx").read_text()
        self.assertIn('title: "Superhuman"\n', coda)
        self.assertIn("formerly Coda", body(coda))
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
        placeholders = ["copper", "klaviyo", "lever", "pardot", "recurly"]
        self.assertEqual(sorted(["attio", "datadog", "github", "gong", "guru", "launchdarkly", "notion", "pipedrive", "sentry", "smartsheet", "twitter", "typeform", "zendesk", *placeholders]), pairs)
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
        group = next(item for item in config["navigation"]["versions"][0]["tabs"][0]["groups"] if item["group"] == "Connector catalog")
        self.assertEqual("essentials/v2/connector-providers", group["pages"][0])
        categories = group["pages"][1:]
        self.assertEqual(8, len(categories))
        index = INDEX.read_text()
        for category in categories:
            with self.subTest(category=category["group"]):
                self.assertNotIn("expanded", category)
                self.assertTrue(all(isinstance(page, str) for page in category["pages"]))
                slugs = [page.rsplit("/", 1)[1] for page in category["pages"]]
                self.assertEqual([name.replace("_", "-") for name in PROVIDER_ORDER[category["group"]]], slugs)
                section = index.split(f"## {category['group']}\n", 1)[1].split("\n## ", 1)[0]
                self.assertEqual([f"/{page}" for page in category["pages"]], re.findall(r'href="([^"]+)"', section))
        self.assertEqual(len(PRODUCTION_SLUGS), sum(len(category["pages"]) for category in categories))

    def test_each_group_opens_with_the_tools_companies_run_most(self):
        self.assertEqual(["slack", "gmail", "outlook", "microsoft_teams", "discord", "sendgrid", "twilio"], PROVIDER_ORDER["Messaging & email"][:7])
        self.assertEqual(["jira", "linear", "asana", "clickup", "monday", "smartsheet"], PROVIDER_ORDER["Tickets & project management"][:6])
        self.assertEqual(["confluence", "notion", "google_drive", "dropbox", "sharepoint", "bookstack"], PROVIDER_ORDER["Docs & cloud storage"][:6])


class PageOpeningAndOrderTest(unittest.TestCase):
    def test_no_page_opens_with_filler(self):
        for path in sorted(DIRECTORY.glob("*.mdx")):
            text = body(path.read_text())
            with self.subTest(page=path.stem):
                for phrase in ("connector to bring", "the following content", "provider description lists"):
                    self.assertNotIn(phrase, text)
                self.assertNotRegex(text, r"Use the [^.\n]* connector to")
                self.assertRegex(text, r"^\n*\S", "no blank gap under the title block")
                self.assertNotRegex(text, r"(?im)^(this section|the following|below are)")

    def test_groups_follow_the_order_customers_connect_most(self):
        order = ["Messaging & email", "Tickets & project management", "Docs & cloud storage", "Meetings & calendar", "Engineering", "Marketing & analytics", "HR & finance", "CRM & support"]
        config = json.loads((ROOT / "docs.json").read_text())
        group = next(item for item in config["navigation"]["versions"][0]["tabs"][0]["groups"] if item["group"] == "Connector catalog")
        self.assertEqual(order, [category["group"] for category in group["pages"][1:]])
        self.assertEqual(order, [title for title in re.findall(r"^## (.+)$", INDEX.read_text(), re.M) if title in order])


class PlaceholderLogoTest(unittest.TestCase):
    def test_every_production_provider_has_a_logo_file(self):
        for row in PRODUCTION:
            found = logo(row["provider"])
            with self.subTest(provider=row["provider"]):
                for path in filter(None, found):
                    self.assertTrue((ROOT / path.lstrip("/")).is_file(), path)
                text = (DIRECTORY / f"{row['provider'].replace('_', '-')}.mdx").read_text()
                self.assertIn(f'icon: "{found.light}"', text)
                self.assertIn(f'url("{found.light}")', TITLE_LOGOS.read_text())
                self.assertIn(found.light, INDEX.read_text())

    def test_placeholders_are_neutral_monograms_for_providers_without_artwork(self):
        names = DASHBOARD_NAMES["names"]
        placeholder_files = {path.name for path in LOGO_DIRECTORY.glob("*.svg") if PLACEHOLDER_MARK in path.read_text()}
        missing = sorted(row["provider"] for row in PRODUCTION if artwork(row["provider"]) is None)
        self.assertEqual(["copper", "klaviyo", "lever", "pardot", "recurly"], missing)
        self.assertEqual({f"{slug}-{theme}.svg" for slug in missing for theme in ("light", "dark")}, placeholder_files)
        for provider in missing:
            light = (LOGO_DIRECTORY / f"{provider}-light.svg").read_text()
            with self.subTest(provider=provider):
                self.assertEqual([names[provider][0].upper()], re.findall(r">([^<]+)</text>", light))
                self.assertNotRegex(light, r"<path|<image|href=")
                self.assertEqual({"#363636"}, set(re.findall(r'fill="(#[0-9a-fA-F]+)"', light)))

    def test_real_artwork_replaces_a_placeholder_on_the_next_run(self):
        providers = [{"provider": "copper", "display_name": "Copper"}]
        self.assertEqual({LOGO_DIRECTORY / "copper-light.svg", LOGO_DIRECTORY / "copper-dark.svg"}, set(placeholder_logos(providers)))
        real = LOGO_DIRECTORY / "copper.svg"
        real.write_text('<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><circle cx="12" cy="12" r="10"/></svg>')
        try:
            self.assertEqual({}, placeholder_logos(providers))
            self.assertEqual("/images/connectors/copper.svg", logo("copper").light)
            generated = pages()
            self.assertNotIn(LOGO_DIRECTORY / "copper-light.svg", generated)
            self.assertIn('url("/images/connectors/copper.svg")', generated[TITLE_LOGOS])
        finally:
            real.unlink()


class ConnectorCopyPagesTest(unittest.TestCase):
    def test_every_provider_has_a_valid_copy_file(self):
        for row in PRODUCTION:
            with self.subTest(provider=row["provider"]):
                for document in load_copy(row["provider"])["documents"]:
                    self.assertTrue(document["attributes"], document["name"])

    def test_a_copy_file_missing_a_section_or_label_is_rejected(self):
        broken = json.loads((COPY / "slack.json").read_text())
        del broken["not_synced"]
        broken["documents"][0]["attributes"][0]["uses"] = ["Indexed"]
        broken["connect"]["credentials"][0]["type"] = "integer"
        broken["sync"]["scheduled"] = "no"
        path = COPY / "zz_broken_copy_probe.json"
        path.write_text(json.dumps(broken))
        try:
            with self.assertRaisesRegex(ValueError, r"missing not_synced.*: type integer.*sync.scheduled must be true or false.*uses \['Indexed'\]"):
                load_copy("zz_broken_copy_probe")
        finally:
            path.unlink()

    def test_copy_pages_have_every_section_in_order(self):
        pages_by_provider = copy_pages()
        self.assertEqual({row["provider"] for row in PRODUCTION}, set(pages_by_provider))
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
            filters = re.findall(r'<ResponseField name="([^"]+)" type="[^"]*Query filter', text)
            keys = set(re.findall(r"\badditional_metadata\.\w+", text))
            with self.subTest(provider=provider):
                self.assertEqual(bool(published), bool(filters), "a page shows filters exactly when production publishes some")
                self.assertLessEqual(set(filters), published)
                self.assertLessEqual(keys, published)

    def test_every_attribute_shows_each_use_as_a_customer_badge(self):
        for provider, text in copy_pages().items():
            fields = re.findall(r'<ResponseField name="([^"]+)" type="([^"]*)">\n(.*?)\n</ResponseField>', section(text, "What gets synced"), re.S)
            with self.subTest(provider=provider):
                self.assertTrue(fields)
                for name, kind, description in fields:
                    uses = kind.split(", ")
                    self.assertTrue(kind, name)
                    self.assertLessEqual(set(uses), set(BADGES), name)
                    self.assertEqual([badge for badge in BADGES if badge in uses], uses, f"{name}: badges in legend order")
                    if "Stored only" in uses:
                        self.assertEqual(["Stored only"], uses, name)
                    elif "Searchable text" not in uses:
                        self.assertIn("not searchable", description.lower(), f"{name} has no search use, so it must say so")

    def test_permissions_section_matches_production(self):
        acl = {row["provider"]: row["acl_supported"] for row in PRODUCTION}
        for provider, text in copy_pages().items():
            permissions = section(text, "Permissions")
            with self.subTest(provider=provider):
                carries = permissions.lstrip().startswith("Turn on **Enforce ACL** for this connector")
                self.assertEqual(acl[provider], carries)
                if not acl[provider]:
                    self.assertIn("Every synced item is visible to everyone who can query the collection", permissions)
                    self.assertIn("/essentials/v2/connectors#set-your-own-rule-on-a-resource", permissions)

    def test_copy_pages_keep_nothing_from_the_old_template(self):
        for provider, text in copy_pages().items():
            with self.subTest(provider=provider):
                for sentence in OLD_TEMPLATE:
                    self.assertNotIn(sentence, text)

    def test_overview_explains_how_to_read_a_page(self):
        index = INDEX.read_text()
        self.assertIn("## How to read a connector page", index)
        self.assertGreater(index.index("## How to read a connector page"), index.rindex("</Columns>"))
        opening = index.split("---", 2)[2].split("## ", 1)[0]
        self.assertIn("](/essentials/v2/connectors)", opening)
        self.assertEqual(1, len([line for line in opening.splitlines() if line.strip()]), "the catalog starts right under one line")
        self.assertIn("#6-connect-several-accounts-from-one-provider", index.split("## Related", 1)[1])


class ConnectorGraphLinksMergeTest(unittest.TestCase):
    def test_sentry_copy_and_page_keep_project_team_links_and_limits(self):
        copy = load_copy("sentry")
        for text in (json.dumps(copy), (DIRECTORY / "sentry.mdx").read_text()):
            with self.subTest(source="copy" if text.startswith("{") else "page"):
                self.assertIn("Issues link to their project through `part_of`", text)
                self.assertIn("assigned to a team, to that team through `related_to`", text)
                self.assertIn("Projects link to each owning team through `part_of`", text)
                self.assertIn("Sentry's project and team IDs, not display names", text)
                self.assertIn("target is indexed in the same database and collection", text)
                self.assertIn("An event's numeric group ID does not identify the issue's short ID", text)
                self.assertIn("a user ID is distinct from an organization member ID", text)
                self.assertNotIn("an issue is not linked to its project", text)

    def test_klaviyo_copy_and_page_keep_audience_configuration_links(self):
        copy = load_copy("klaviyo")
        for text in (json.dumps(copy), (DIRECTORY / "klaviyo.mdx").read_text()):
            with self.subTest(source="copy" if text.startswith("{") else "page"):
                self.assertIn("included and excluded list or segment audiences through `targets` and `excludes`, respectively", text)
                self.assertIn("audience configuration, not message delivery", text)
                self.assertIn("Segments link to lists or segments referenced in their definitions through `related_to`", text)
                self.assertIn("a reference in a negative rule does not assert positive membership", text)
                self.assertIn("profiles are not linked to their lists or segments", text)
                self.assertNotIn("records are not linked to each other", text)


class RevisedPageDesignTest(unittest.TestCase):
    def test_permissions_come_first_and_setup_has_real_subheadings(self):
        for provider, text in copy_pages().items():
            connect = section(text, "Before you connect")
            with self.subTest(provider=provider):
                self.assertEqual(COPY_SECTIONS, re.findall(r"^## (.+)$", text, re.M))
                self.assertEqual(["Credentials", "Set it up"], re.findall(r"^### (.+)$", connect, re.M))

    def test_provider_pages_leave_the_label_legend_to_the_overview(self):
        legend = ("part of the text a query matches", "What the labels mean", "kept on the item and shown when you inspect it")
        for provider, text in copy_pages().items():
            with self.subTest(provider=provider):
                for phrase in legend:
                    self.assertNotIn(phrase, text)

    def test_no_good_to_know_and_at_most_three_example_searches(self):
        for provider, text in copy_pages().items():
            with self.subTest(provider=provider):
                self.assertNotIn("## Good to know", text)
                self.assertNotIn("## Questions this lets you answer", text)
                if "**Example searches**" in text:
                    examples = text.split("**Example searches**\n\n", 1)[1].split("\n\n", 1)[0]
                    self.assertLessEqual(len(examples.splitlines()), 3)

    def test_not_synced_stays_visible_and_accordions_hold_only_stored_or_optional_detail(self):
        for provider, text in copy_pages().items():
            with self.subTest(provider=provider):
                self.assertNotIn("<Accordion", section(text, "Not synced"))
                self.assertTrue(re.findall(r"^- ", section(text, "Not synced"), re.M))
                for title, content in re.findall(r'<Accordion title="([^"]+)">(.*?)</Accordion>', text, re.S):
                    self.assertNotIn("not kept", title.lower())
                    kinds = set(re.findall(r'<ResponseField name="[^"]+" type="([^"]+)"', content))
                    if title.startswith("Stored only"):
                        self.assertEqual({"Stored only"}, kinds, title)
                    else:
                        self.assertFalse(kinds & set(BADGES), f"{title} folds attributes that are not stored-only")

    def test_json_examples_are_multi_line_and_never_wider_than_80_columns(self):
        for provider, text in copy_pages().items():
            with self.subTest(provider=provider):
                for block in re.findall(r"^```json[^\n]*\n(.*?)^```$", text, re.S | re.M):
                    self.assertGreater(len(block.splitlines()), 1)
                    self.assertLessEqual(max(len(line) for line in block.splitlines()), 80, block)

    def test_copy_pages_and_overview_describe_outcomes_not_machinery(self):
        pages_to_check = {provider: body(text) for provider, text in copy_pages().items()}
        pages_to_check["connector-providers"] = body(INDEX.read_text())
        for page, text in pages_to_check.items():
            allowed = PROVIDER_VOCABULARY.get(page, set())
            with self.subTest(page=page):
                found = [term for term in COPY_PAGE_TERMS if term not in allowed and re.search(rf"(?i)(?<![\w-]){re.escape(term)}(?![\w-])", text)]
                self.assertEqual([], found)

    def test_permissions_state_the_cloud_outcome_with_enforce_acl_first(self):
        acl = {row["provider"]: row["acl_supported"] for row in PRODUCTION}
        for provider, text in copy_pages().items():
            permissions = section(text, "Permissions").strip()
            with self.subTest(provider=provider):
                if acl[provider]:
                    self.assertTrue(permissions.startswith("Turn on **Enforce ACL**"))
                    cloud = "On HydraDB Cloud, with Enforce ACL on, such an item follows the access rule you set on its resource. With no rule, it is hidden from search."
                    if load_copy(provider)["permissions"].get("cloud_outcome", True):
                        self.assertIn(cloud, permissions)
                        self.assertIn("Self-hosted deployments can be configured differently.", permissions)
                    else:
                        self.assertNotIn(cloud, permissions)
                else:
                    self.assertTrue(permissions.startswith("<Warning>"))
                    self.assertIn("**Enforce ACL** is unavailable for this connector", permissions.split("</Warning>", 1)[0])

    def test_the_hourly_schedule_shows_only_for_scheduled_connectors(self):
        hourly = "HydraDB syncs every hour by default"
        for provider, text in copy_pages().items():
            with self.subTest(provider=provider):
                self.assertEqual(load_copy(provider)["sync"].get("scheduled", True), hourly in section(text, "Keeping in sync"))
        self.assertNotIn(hourly, (DIRECTORY / "supabase.mdx").read_text())

    def test_numeric_settings_use_the_number_type(self):
        for provider, text in copy_pages().items():
            with self.subTest(provider=provider):
                self.assertNotIn("A whole number", text)
        self.assertIn('<ResponseField name="max_backfill_days" type="number">', (DIRECTORY / "twitter.mdx").read_text())

    def test_graph_section_names_each_item_class_without_generic_promises(self):
        for provider, text in copy_pages().items():
            graph = section(text, "How it fits into the graph")
            documents = re.findall(r"^### (.+)$", section(text, "What gets synced"), re.M)
            with self.subTest(provider=provider):
                for document in documents:
                    if document.startswith("On every "):
                        continue
                    self.assertRegex(graph, rf"(?m)^- {re.escape(document)} use the ({'|'.join(GRAPH_CLASSES)}) class\.$")
                self.assertNotIn("kept with its author and its thread", graph)
                self.assertNotIn("There is no per-connector setup", graph)

    def test_overview_collapses_the_labels_and_graph_classes_below_the_grid(self):
        index = INDEX.read_text()
        end = index.split("</Columns>")[-1]
        labels = end.split("### Attribute labels", 1)[1].split("### Graph classes", 1)[0]
        classes = end.split("### Graph classes", 1)[1].split("## Related", 1)[0]
        for part in (labels, classes):
            self.assertEqual(1, part.count("<Accordion "))
            self.assertEqual(1, part.count("</Accordion>"))
        for badge in BADGES:
            self.assertIn(f"- **{badge}:** ", labels.split("<Accordion ", 1)[1])
        for title in GRAPH_CLASSES:
            self.assertIn(f"- **{title}:** ", classes.split("<Accordion ", 1)[1])

    def test_an_attribute_must_say_when_it_is_not_searchable(self):
        broken = json.loads((COPY / "slack.json").read_text())
        broken["documents"][0]["attributes"].append({"name": "probe", "uses": ["Query filter"], "text": "A filter."})
        broken["documents"][0]["attributes"].append({"name": "mixed", "uses": ["Stored only", "Query filter"], "text": "Not searchable."})
        path = COPY / "zz_unsaid_probe.json"
        path.write_text(json.dumps(broken))
        try:
            with self.assertRaisesRegex(ValueError, r"probe: an attribute with no search use must say it is not searchable.*mixed: Stored only cannot be combined"):
                load_copy("zz_unsaid_probe")
        finally:
            path.unlink()


if __name__ == "__main__":
    unittest.main()
