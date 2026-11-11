"""Regression tests for canonical names and copy used across the live docs."""

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


if __name__ == "__main__":
    unittest.main()
