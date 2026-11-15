import json
import re
import unittest

from generate_llms_index import generate, page_paths, ROOT


class DocsDiscoveryTest(unittest.TestCase):
    def test_llms_txt_is_the_agent_guide(self):
        """llms.txt and the Agent Integration Guide page must say the same thing."""
        text = (ROOT / "llms.txt").read_text()
        self.assertEqual(generate(), text)
        guide = (ROOT / "AGENTS.mdx").read_text().split("---\n", 2)[2].strip()
        self.assertTrue(text.endswith(guide + "\n"))
        self.assertTrue(text.startswith("# Complete Agent Integration Guide\n\n> "))
        config = json.loads((ROOT / "docs.json").read_text())
        self.assertFalse({"/llms.txt", "/llms-full.txt"} & {redirect["source"] for redirect in config["redirects"]})

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
        for relative in ("AGENTS.mdx", "continuity-assurance.mdx"):
            with self.subTest(relative=relative):
                text = (ROOT / relative).read_text()
                prose = re.sub(r'^```[^\n]*\n.*?^```', "", text, flags=re.M | re.S)
                self.assertIsNone(re.search(r'^#\s+', prose, re.M))
                self.assertFalse(any(character in prose for character in "\u200b\u200c\u200d\ufeff"))


if __name__ == "__main__":
    unittest.main()
