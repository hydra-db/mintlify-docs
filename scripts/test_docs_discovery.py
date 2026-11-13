import json
import re
import unittest

from generate_llms_index import generate, ROOT


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
        self.assertIn("api-reference/endpoint/sync-connector", links)
        for slug in links:
            self.assertTrue((ROOT / f"{slug}.mdx").is_file(), slug)
            self.assertFalse(slug.startswith(("archive/", "snippets/")), slug)

    def test_legacy_pages_are_replaced_by_redirects_not_a_rendered_warning(self):
        config = json.loads((ROOT / "docs.json").read_text())
        self.assertFalse((ROOT / "snippets/v1-legacy.mdx").exists())
        for redirect in config["redirects"]:
            source = redirect["source"].lstrip("/")
            if source.startswith(("get-started/", "essentials/", "api-reference/", "cookbooks/")):
                with self.subTest(source=source):
                    self.assertFalse((ROOT / f"{source}.mdx").exists())
                    self.assertNotIn("/v2/", redirect["destination"])

    def test_changed_pages_have_no_extra_prose_h1_or_zero_width_artifacts(self):
        for relative in ("AGENTS.mdx", "continuity-assurance.mdx"):
            with self.subTest(relative=relative):
                text = (ROOT / relative).read_text()
                prose = re.sub(r'^```[^\n]*\n.*?^```', "", text, flags=re.M | re.S)
                self.assertIsNone(re.search(r'^#\s+', prose, re.M))
                self.assertFalse(any(character in prose for character in "\u200b\u200c\u200d\ufeff"))


if __name__ == "__main__":
    unittest.main()
