import json
import re
import unittest

from generate_llms_index import generate, page_paths, ROOT


class DocsDiscoveryTest(unittest.TestCase):
    def test_index_is_current_and_does_not_redirect_back_to_the_guide(self):
        self.assertEqual(generate(), (ROOT / "llms.txt").read_text())
        config = json.loads((ROOT / "docs.json").read_text())
        self.assertFalse({"/llms.txt", "/llms-full.txt"} & {redirect["source"] for redirect in config["redirects"]})
        links = re.findall(r'https://docs\.hydradb\.com/([^\s)]+)\.md\)', generate())
        expected = {path.relative_to(ROOT).with_suffix("").as_posix() for path in ROOT.rglob("*.mdx") if not any(part.startswith(".") or part in {"node_modules", "archive", "snippets"} for part in path.relative_to(ROOT).parts)}
        self.assertEqual(expected, set(links))
        self.assertEqual(len(links), len(set(links)))
        self.assertIn("AGENTS", links)
        self.assertIn("plugins/cursor", links)
        self.assertIn("api-reference/v2/endpoint/sync-connector", links)
        for slug in links:
            self.assertTrue((ROOT / f"{slug}.mdx").is_file(), slug)
            self.assertFalse(slug.startswith(("archive/", "snippets/")), slug)

    def test_each_legacy_api_page_has_the_shared_rendered_warning(self):
        config = json.loads((ROOT / "docs.json").read_text())
        versions = {version["version"]: page_paths(version) for version in config["navigation"]["versions"]}
        for slug in versions["v1"] - versions["v2"] - {"continuity-assurance"}:
            with self.subTest(slug=slug):
                text = (ROOT / f"{slug}.mdx").read_text()
                self.assertIn('import V1Legacy from "/snippets/v1-legacy.mdx";', text)
                self.assertIn("<V1Legacy />", text)
        warning = (ROOT / "snippets/v1-legacy.mdx").read_text()
        self.assertIn("/get-started/v2/introduction", warning)
        self.assertIn("not compatible", warning)

    def test_changed_pages_have_no_extra_prose_h1_or_zero_width_artifacts(self):
        for relative in ("AGENTS.mdx", "continuity-assurance.mdx", "cookbooks/hydradb-cookbook-06.mdx", "cookbooks/v2/hydradb-cookbook-06.mdx"):
            with self.subTest(relative=relative):
                text = (ROOT / relative).read_text()
                prose = re.sub(r'^```[^\n]*\n.*?^```', "", text, flags=re.M | re.S)
                self.assertIsNone(re.search(r'^#\s+', prose, re.M))
                self.assertFalse(any(character in prose for character in "\u200b\u200c\u200d\ufeff"))


if __name__ == "__main__":
    unittest.main()
