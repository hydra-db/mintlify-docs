"""Regression tests for canonical names and copy used across the live docs."""

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
DEPRECATED_API_KEY = "HYDRA" + "_DB_API_KEY"


def live_files(pattern: str):
    for path in ROOT.rglob(pattern):
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

        self.assertCountEqual(
            ["plugins/claude-code.mdx", "plugins/cli.mdx", "plugins/mcp.mdx"],
            matches,
        )



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
        "essentials/v2/query.mdx": r"\| `max_results` \|[^|]*Default `(\d+)`, maximum `(\d+)`",
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
        "essentials/v2/metadata.mdx": r"\| `metadata` \| [^|]*\| (\d+ KiB)",
    },
    "additional_metadata size limit": {
        "AGENTS.mdx": r"\| `additional_metadata` \| [^|]*max (\d+ KiB)",
        "essentials/v2/context.mdx": r"\| `additional_metadata` \| [^|]*Up to (\d+ KiB)",
        "essentials/v2/metadata.mdx": r"\| `additional_metadata` \| [^|]*\| (\d+ KiB)",
    },
    "collections per query": {
        "essentials/v2/query.mdx": r"\*\*Several collections:\*\*[^\n]*Up to (\d+);",
        "api-reference/v2/endpoint/query.mdx": r"at most (\d+) collections",
    },
}
INDEXING_STATUSES = ["queued", "processing", "graph_creation", "completed", "errored"]
APP_SOURCE_KINDS = ["email", "message", "ticket", "knowledge_base", "comment", "meeting", "custom"]


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
        sentence = re.search(r"moves forward through ([^:]*):", self.read("essentials/v2/context.mdx"))
        self.assertEqual(INDEXING_STATUSES, re.findall(r"`(\w+)`", sentence.group(1)))
        used = set()
        for path in live_files("*.mdx"):
            if "/v2/" in str(path) or path.name == "AGENTS.mdx":
                used |= set(re.findall(
                    r'indexing_?[sS]tatus["\s]*(?:===?|:|in)\s*[(\[]?"(\w+)"', path.read_text(encoding="utf-8")
                ))
        self.assertTrue(used)
        self.assertLessEqual(used, set(INDEXING_STATUSES))

    def test_app_source_kinds_are_the_same_everywhere(self):
        listed = ", ".join(f"`{kind}`" for kind in APP_SOURCE_KINDS[:-1]) + f", or `{APP_SOURCE_KINDS[-1]}`"
        for relative in ("essentials/v2/context.mdx", "api-reference/v2/endpoint/ingest-context.mdx"):
            self.assertIn(listed, self.read(relative), relative)
        table = re.findall(r"^\| `(\w+)` \| [^|]+ \| `", self.read("essentials/v2/context.mdx"), re.M)
        self.assertEqual(APP_SOURCE_KINDS, table)
        guide = re.search(r"Kinds in OpenAPI[^.]*\.", self.read("AGENTS.mdx")).group(0)
        self.assertEqual(set(APP_SOURCE_KINDS), set(re.findall(r"`(\w+)`", guide)))

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

    def test_guides_do_not_show_internal_query_fields(self):
        for path in live_files("*.mdx"):
            if "/v2/" in str(path) or path.name == "AGENTS.mdx":
                text = path.read_text(encoding="utf-8")
                self.assertNotRegex(text, r"\bdata\.sources\b(?!`, which the public result does not return)",
                                    path.relative_to(ROOT))


if __name__ == "__main__":
    unittest.main()
