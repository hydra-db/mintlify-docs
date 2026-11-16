"""Regression tests for canonical names and copy used across the live docs."""

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
DEPRECATED_API_KEY = "HYDRA" + "_DB_API_KEY"


def hidden_pages():
    """Pages in a hidden navigation group: old copies kept for reference, not maintained."""
    def pages(value):
        if isinstance(value, str):
            return {value}
        if isinstance(value, list):
            return set().union(set(), *(pages(item) for item in value))
        if isinstance(value, dict):
            return set().union(set(), *(pages(item) for key, item in value.items() if key in {"pages", "groups", "tabs", "anchors", "dropdowns"}))
        return set()
    config = json.loads((ROOT / "docs.json").read_text())
    return {ROOT / f"{slug}.mdx" for version in config["navigation"]["versions"] if version.get("hidden") for slug in pages(version)}


def live_files(pattern: str):
    hidden = hidden_pages()
    for path in ROOT.rglob(pattern):
        if path in hidden:
            continue
        relative = path.relative_to(ROOT)
        if not set(relative.parts).intersection({
            ".git",
            "__pycache__",
            "archive",
            "bin",
            "node_modules",
            "plugins",
        }):
            yield path


class DocsConsistencyTest(unittest.TestCase):
    def test_ci_runs_docs_consistency_suite(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("python scripts/test_docs_consistency.py", workflow)

    def test_live_docs_use_canonical_api_key_name(self):
        stale_files = [
            str(path.relative_to(ROOT))
            for path in live_files("*")
            if path.is_file()
            and DEPRECATED_API_KEY
            in path.read_text(encoding="utf-8", errors="ignore")
        ]
        self.assertEqual([], stale_files)

    def test_plugin_deprecation_notes_keep_old_api_key_name(self):
        matches = []
        for path in (ROOT / "plugins").rglob("*"):
            if not path.is_file():
                continue
            count = path.read_text(encoding="utf-8", errors="ignore").count(
                DEPRECATED_API_KEY
            )
            matches.extend([str(path.relative_to(ROOT))] * count)

        # The CLI and MCP pages name only the current variable. The Claude Code
        # page keeps one note for people upgrading from the old name.
        self.assertCountEqual(["plugins/claude-code.mdx"], matches)



    def test_recall_pages_use_correct_spelling(self):
        for relative in (
            "api-reference/endpoint/full-recall.mdx",
            "api-reference/endpoint/recall-preferences.mdx",
        ):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn("knoledge", text, relative)
            self.assertNotIn("strucutre", text, relative)


# Facts stated on more than one page. Each entry names the pages that state the
# fact and a pattern whose captured groups must be identical on all of them, so
# the agent guide, the guides, and the API reference cannot drift apart.
SHARED_FACTS = {
    "max_results default and maximum": {
        "AGENTS.mdx": r"\| `max_results` \| [^|]*\| default (\d+); max (\d+) \|",
        "essentials/v2/query.mdx": r"`max_results`(?:</td>\s*<td>| \|)[^|<]*Default `(\d+)`, maximum `(\d+)`",
        "api-reference/v2/endpoint/query.mdx":
            r'name="max_results"[^|]*\|[^|]*to `(?P<max>\d+)`\. Defaults to `(?P<default>\d+)`',
    },
    "recency_bias default": {
        "essentials/v2/query.mdx": r"\*\*`recency_bias`:\*\*[^\n]*?Default `([\d.]+)`",
        "api-reference/v2/endpoint/query.mdx": r'name="recency_bias"[^\n]*\(default=`([\d.]+)`\)',
    },
    "metadata size limit": {
        "AGENTS.mdx": r"\| `metadata` \| [^|]*max (\d+ KiB)",
        "essentials/v2/context.mdx": r"\| `metadata` \| [^|]*Up to (\d+ KiB)",
        "essentials/v2/metadata.mdx": r"\| `metadata`(?: \(stored\))? \|(?: [^|]*\|)? (\d+ KiB)",
    },
    "additional_metadata size limit": {
        "AGENTS.mdx": r"\| `additional_metadata` \| [^|]*max (\d+ KiB)",
        "essentials/v2/context.mdx": r"\| `additional_metadata` \| [^|]*Up to (\d+ KiB)",
        "essentials/v2/metadata.mdx": r"\| `additional_metadata`(?: \(stored\))? \|(?: [^|]*\|)? (\d+ KiB)",
    },
    "collections per query": {
        "essentials/v2/query.mdx": r"\*\*Several collections:\*\*[^\n]*[Uu]p to (\d+)[; ]",
        "api-reference/v2/endpoint/query.mdx": r"at most (\d+) collections",
    },
}
INDEXING_STATUSES = ["queued", "processing", "graph_creation", "completed", "errored"]


class UnifiedNarrativeTest(unittest.TestCase):
    def read(self, relative):
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_cookbook_migration_requires_verified_identity(self):
        intro = self.read("snippets/cookbook-setup.mdx").split("```python hydra.py", 1)[0]
        self.assertIn("**Existing deployments:**", intro)
        migration = intro.split("**Existing deployments:**", 1)[1]
        for phrase in (
            "does not adopt legacy connectors", "remote syncs can continue",
            "Before switching", "client.connectors.list()", "`connector_id`",
            "`(provider, database, collection, account)`", "ownership",
            "trusted server configuration and credentials", "`provider_account_scope` where applicable",
            "connector names are not proof of ownership", "only under that verified identity",
            "without legacy-name adoption, automatic ACL reassignment, or blanket pause/delete",
        ):
            self.assertIn(phrase, migration)

    def test_inference_opt_out_does_not_promise_no_graph_processing(self):
        for relative in ("essentials/v2/context.mdx", "api-reference/v2/endpoint/ingest-context.mdx"):
            text = self.read(relative)
            with self.subTest(page=relative):
                self.assertIn("additional memory enrichment", text)
                self.assertIn("entity/graph extraction may still run", text)
                self.assertNotIn("Explicit `false` stores the text verbatim", text)
                self.assertNotIn("without preprocessing", text)

    def test_query_method_and_alpha_defaults_do_not_confuse_auto_mode(self):
        text = self.read("essentials/v2/query.mdx")
        method = re.search(r"<td>`query_by`</td>\s*<td>(.*?)</td>", text, re.S)
        self.assertIsNotNone(method)
        values = set(re.findall(r'`"([^"]+)"`', method.group(1)))
        spec = json.loads(self.read("api-reference/v2/openapi.json"))
        self.assertEqual({"hybrid", "text"}, values)
        self.assertEqual(set(spec["components"]["schemas"]["search.QueryBy"]["enum"]), values)
        self.assertIn('`"hybrid"` (the default)', method.group(1))
        self.assertRegex(self.read("api-reference/v2/endpoint/query.mdx"), r'name="query_by"[^\n]*default=`"hybrid"`')
        self.assertNotIn("auto", method.group(1))
        alpha = next(line for line in text.splitlines() if line.startswith("- `alpha`"))
        self.assertIn("Default `0.8`", alpha)
        self.assertIn('`"auto"` also resolves to `0.8`', alpha)
        self.assertNotIn('which `"auto"`', alpha)
        self.assertIn('`"auto"` (the default)', text.split('<td>`mode`</td>', 1)[1].split('</td>', 1)[0])
        self.assertIn("auto selector", self.read("plugins/cli.mdx"))

    def test_connector_enforcement_requires_a_human_session_even_for_false(self):
        text = self.read("essentials/v2/connectors.mdx")
        section = text.split("## 5. ACL", 1)[1].split("### Examples", 1)[0]
        for phrase in ("signed-in human", "owner or writer", "API-key clients must omit", "even `false`", "dashboard", "Enforce ACL", "`acl_supported: true`", "`false`", "persistent", "warning"):
            self.assertIn(phrase, section)
        self.assertIn("valid private ACLs", section)
        self.assertNotRegex(section, r"API.key[^.\n]*send[^.\n]*false")

    def test_memory_authorization_covers_mutations_and_adjacent_examples(self):
        text = self.read("essentials/v2/context.mdx")
        memory = text.split("## Ingesting memory", 1)[1].split("## Wait for indexing", 1)[0]
        self.assertIn("updates and deletes", memory)
        self.assertIn("authorize", memory)
        self.assertIn("source ID", memory)
        self.assertIn("verified session", memory)
        self.assertIn('collection=verified_session["memory_collection"]', memory)
        self.assertIn("collection: verifiedSession.memoryCollection", memory)
        self.assertIn('"collection=$SESSION_MEMORY_COLLECTION"', memory)

    def test_cli_shell_examples_do_not_recommend_api_keys_in_argv(self):
        text = self.read("plugins/cli.mdx")
        shell_blocks = re.findall(r"```(?:bash|sh|shell)[^\n]*\n(.*?)```", text, re.S)
        self.assertTrue(shell_blocks)
        for source in shell_blocks:
            commands = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
            commands = commands.replace("\\\n", " ")
            self.assertNotRegex(commands, r"\bhydradb\b[^\n]*--api-key(?:[=\s]|$)")
            self.assertNotRegex(commands, r"\bhydradb\s+config\s+set\s+api_key\b")
        self.assertIn("masked prompt", text)
        self.assertIn("`0600`", text)
        self.assertIn("inject `HYDRADB_API_KEY` through your environment or secret manager", text)

    def test_memory_scope_is_derived_from_the_signed_in_session(self):
        text = self.read("essentials/v2/context.mdx")
        memory = text.split("## Ingesting memory", 1)[1].split("## Wait for indexing", 1)[0]
        self.assertIn("signed-in session", memory)
        self.assertIn("does not authenticate", memory)
        self.assertIn("every ingest, status check, and query", memory)
        self.assertIn("`infer: true` (default)", memory)
        self.assertIn("`infer: false`", memory)

    def test_legacy_memory_instructions_are_not_public_guidance(self):
        for relative in (
            "essentials/v2/context.mdx",
            "api-reference/v2/endpoint/ingest-context.mdx",
            "cookbooks/v2/personal-assistant.mdx",
        ):
            self.assertNotIn("custom_instructions", self.read(relative), relative)
        guide = self.read("AGENTS.mdx")
        memories = guide.split("### Memories")[-1].split("## 7.", 1)[0]
        self.assertNotIn("custom_instructions", memories)
        self.assertIn("`custom_instructions` on the connector", guide)
        self.assertIn("custom_instructions", self.read("essentials/v2/connectors.mdx"))

    def test_query_names_and_defaults_match_the_canonical_contract(self):
        for relative in (
            "AGENTS.mdx",
            "essentials/v2/query.mdx",
            "api-reference/v2/endpoint/query.mdx",
        ):
            text = self.read(relative)
            with self.subTest(relative=relative):
                self.assertIn("query_explicit_relations", text)
                self.assertIn("additional_graph_context", text)
                self.assertIn("data.additional_graph_context", text)
                self.assertNotRegex(text, r'(?:Default|default|omitted means)[^\n|]*`0\.4`')
        reference = self.read("api-reference/v2/endpoint/query.mdx")
        self.assertRegex(reference, r'name="type"[^\n]*default=`"all"`')
        self.assertRegex(reference, r'name="recency_bias"[^\n]*default=`0\.6`')
        self.assertIn('name="query_explicit_relations"', reference)
        self.assertIn('name="additional_graph_context"', reference)
        self.assertIn("request-time hint", reference)
        self.assertIn("response map", reference)
        context = self.read("essentials/v2/context.mdx")
        self.assertIn("`relations.ids`", context)
        self.assertNotIn("`explicit_relations`", context)

    def test_cli_authentication_does_not_require_a_database_or_key_in_argv(self):
        text = self.read("plugins/cli.mdx")
        self.assertIn("hydradb login\n", text)
        self.assertNotIn("hydradb login --", text)
        self.assertNotIn("--api-key", text)
        self.assertIn("masked prompt", text)
        self.assertIn("`0600`", text)
        self.assertIn("hydradb database list", text)
        self.assertIn("hydradb config set database my-db", text)
        self.assertIn("hydradb database create my-db", text)
        self.assertIn("### Deprecated aliases", text)
        self.assertIn("### Persistent configuration", text)
        self.assertIn("`--kind`", text)
        self.assertIn("knowledge text", text.split("| `--markdown`", 1)[1].split("\n", 1)[0])
        self.assertIn("| `--additional-graph-context`", text)
        self.assertIn("`--additional_graph_context` is also accepted", text)
        self.assertIn("`response_warnings`", text)
        self.assertIn("effective `scope`", text)

    def test_client_auto_selection_is_not_documented_as_a_backend_wire_type(self):
        context = self.read("essentials/v2/context.mdx")
        self.assertIn("client-side selection", context)
        self.assertIn("defaults to `type=knowledge`", context)
        self.assertIn("`auto` is not an API wire value", context)
        self.assertNotIn("`type=traces`", context)
        self.assertIn("default is `\"knowledge\"`", self.read("AGENTS.mdx"))
        self.assertIn('default=`"knowledge"`', self.read("api-reference/v2/endpoint/ingest-context.mdx"))

    def test_mcp_api_key_connection_can_choose_its_scope_after_authentication(self):
        text = self.read("plugins/mcp.mdx")
        quickstart = text.split("## Configuration", 1)[0]
        self.assertNotIn('"X-HydraDB-Database"', quickstart)
        self.assertNotIn('"HYDRADB_DATABASE"', quickstart)
        self.assertIn("hydradb_databases", text)
        self.assertIn("hydradb_database_create", text)
        self.assertNotIn("names no database is refused", text)
        self.assertIn("`scope_notice`", text)
        self.assertIn("`warnings_omitted`", text)
        self.assertIn("rejected before an API call", text)

    def test_repaired_local_links_have_stable_targets(self):
        links = {
            "essentials/v2/context.mdx": ("link-sources-deterministically", "## Link sources deterministically"),
            "essentials/v2/connectors.mdx": ("6-connect-several-accounts-from-one-provider", "## 6. Connect several accounts from one provider"),
            "essentials/v2/metadata-2.mdx": ("metadata-and-additional-metadata", "## Metadata and additional metadata"),
            "plugins/cli.mdx": ("persistent-configuration", "### Persistent configuration"),
            "plugins/mcp.mdx": ("api-key-headers", "### API key headers"),
        }
        for relative, (anchor, target) in links.items():
            text = self.read(relative)
            with self.subTest(relative=relative):
                self.assertIn(f"](#{anchor})", text)
                self.assertIn(target, text)

    def test_byog_endpoints_have_navigable_reference_pages(self):
        routes = {
            "byog-query-managed": "POST /byog",
            "byog-query": "POST /byog/query",
            "byog-create-database": "POST /byog/databases",
            "byog-delete-database": "DELETE /byog/databases",
            "byog-list-collections": "GET /byog/collections",
            "byog-delete-collection": "DELETE /byog/collections",
        }
        config = self.read("docs.json")
        for page, operation in routes.items():
            relative = f"api-reference/v2/endpoint/{page}"
            text = self.read(f"{relative}.mdx")
            with self.subTest(operation=operation):
                self.assertIn(f'"{relative}"', config)
                self.assertIn(f'openapi: "api-reference/v2/openapi.json {operation}"', text)
                self.assertIn("<RequestExample>", text)
                self.assertIn("$HYDRADB_API_KEY", text)
                self.assertIn("## Related resources", text)


class SharedFactsTest(unittest.TestCase):
    def read(self, relative):
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_shared_facts_agree_across_pages(self):
        for fact, pages in SHARED_FACTS.items():
            values = {}
            for relative, pattern in pages.items():
                match = re.search(pattern, self.read(relative))
                self.assertIsNotNone(match, f"{fact}: not stated on {relative}")
                named = match.groupdict()
                values[relative] = (named["default"], named["max"]) if named else match.groups()
            with self.subTest(fact=fact):
                self.assertEqual(1, len(set(values.values())), values)

    def test_indexing_statuses_are_the_same_everywhere(self):
        # The reference page is the one place that must list every status.
        reference = self.read("api-reference/v2/endpoint/source-status.mdx")
        for status in INDEXING_STATUSES:
            self.assertIn(f"`{status}`", reference)
        sentence = re.search(r"moves forward through ([^:]*):", self.read("essentials/v2/context.mdx"))
        if sentence:  # The guide may spell the sequence out; if it does, it must match.
            self.assertEqual(INDEXING_STATUSES, re.findall(r"`(\w+)`", sentence.group(1)))
        used = set()
        for path in live_files("*.mdx"):
            if "/v2/" in str(path) or path.name == "AGENTS.mdx":
                used |= set(re.findall(
                    r'indexing_?[sS]tatus["\s]*(?:===?|:|in)\s*[(\[]?"(\w+)"', path.read_text(encoding="utf-8")
                ))
        self.assertTrue(used)
        self.assertLessEqual(used, set(INDEXING_STATUSES))

    def test_app_sources_are_documented_only_under_connectors(self):
        """App content reaches HydraDB through connectors, so no other page teaches app_knowledge."""
        pattern = re.compile(r"app[_ ]knowledge|appKnowledge|app_relations|app[ -]sources?", re.I)
        offenders = []
        pages = [p for p in live_files("*.mdx") if "/v2/" in str(p) or p.name == "AGENTS.mdx"]
        pages += sorted((ROOT / "snippets").glob("*.mdx"))
        for path in pages:
            relative = str(path.relative_to(ROOT))
            if relative == "essentials/v2/connectors.mdx" or "connector" in path.name:
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
                if pattern.search(line):
                    offenders.append(f"{relative}:{number}")
        self.assertEqual([], offenders)

    def test_supported_file_formats_match_the_api_reference(self):
        def extensions(relative, start, end):
            text = self.read(relative)
            section = text[text.index(start):text.index(end, text.index(start))]
            return set(re.findall(r"`(\.\w+)`", section))

        reference = extensions("api-reference/v2/endpoint/ingest-context.mdx",
                               "## Supported file formats", "### Unsupported formats")
        guide = extensions("AGENTS.mdx", "Supported extensions:", "- Any other extension")
        self.assertEqual(reference, guide)

    def test_absolute_docs_links_point_at_real_pages(self):
        config = json.loads(self.read("docs.json"))
        redirects = {redirect["source"] for redirect in config["redirects"]}
        for path in live_files("*.mdx"):
            if "/v2/" not in str(path) and path.name != "AGENTS.mdx":
                continue
            for url in re.findall(r"https://docs\.hydradb\.com(/[^\s)`\"'<>]*)", path.read_text(encoding="utf-8")):
                target = url.split("#")[0].rstrip("/.,")
                if not target or target.endswith((".json", ".txt")):
                    target = target.lstrip("/")
                    self.assertTrue(not target or (ROOT / target).exists(), f"{path.name}: {url}")
                    continue
                self.assertNotRegex(url, r"#[\w-]+\.md", f"{path.name}: {url}")
                page = target.removesuffix(".md").lstrip("/")
                exists = (
                    (ROOT / f"{page}.mdx").exists()
                    or (ROOT / page / "index.mdx").exists()
                    or f"/{page}" in redirects
                )
                self.assertTrue(exists, f"{path.name}: {url} does not exist")

    def test_anchor_links_point_at_real_headings(self):
        def prose(text):
            return re.sub(r"^[ \t]*```.*?^[ \t]*```", "", text, flags=re.M | re.S)

        def slug(heading):
            heading = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading)
            heading = re.sub(r"<[^>]+>", "", heading).replace("`", "").strip().lower()
            return re.sub(r"[^\w\- ]", "", heading).replace(" ", "-")

        # A same-page link must match a real heading (the hygiene check's rule).
        # A link from another page may also use an explicit anchor tag.
        headings, tagged = {}, {}
        for path in live_files("*.mdx"):
            text = prose(path.read_text(encoding="utf-8"))
            headings[path] = {slug(m.group(1)) for m in re.finditer(r"^#{1,6}\s+(.*)$", text, re.M)}
            tagged[path] = headings[path] | set(re.findall(r'<(?:a|span) id="([^"]+)"', text))
        missing = []
        for path, own in headings.items():
            relative = str(path.relative_to(ROOT))
            if "/v2/" not in f"/{relative}" and relative != "AGENTS.mdx":
                continue
            text = prose(path.read_text(encoding="utf-8"))
            for target, anchor in re.findall(r"\]\((/[^)#\s]*)?#([^)\s]+)\)", text):
                if not target:
                    known = own
                else:
                    known = tagged.get(ROOT / f"{target.strip('/')}.mdx")
                if known is None:
                    missing.append(f"{relative}: {target} is not a page")
                elif anchor not in known:
                    missing.append(f"{relative}: {target}#{anchor}")
        self.assertEqual([], missing, "\n".join(missing))

    def test_guides_do_not_show_internal_query_fields(self):
        for path in live_files("*.mdx"):
            if "/v2/" in str(path) or path.name == "AGENTS.mdx":
                text = path.read_text(encoding="utf-8")
                self.assertNotRegex(text, r"\bdata\.sources\b(?!`, which the public result does not return)",
                                    path.relative_to(ROOT))


if __name__ == "__main__":
    unittest.main()
