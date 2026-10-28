"""Regression tests for canonical names and copy used across the live docs."""

from pathlib import Path
import re
from types import SimpleNamespace
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

    def test_cookbook_context_block_uses_each_source(self):
        cookbook = (
            ROOT / "cookbooks/v2/cookbook-01-build-cursor-for-docs.mdx"
        ).read_text(encoding="utf-8")
        match = re.search(
            r"```python\n(?:from backend\.hydra_client import search_docs\n\n)?"
            r".*?(def build_context_block\(recall_payload\) -> str:\n.*?)\n```",
            cookbook,
            re.DOTALL,
        )
        self.assertIsNotNone(match)

        namespace = {}
        exec(match.group(1), namespace)
        payload = SimpleNamespace(
            data=SimpleNamespace(
                chunks=[
                    SimpleNamespace(chunk_content=" First chunk "),
                    SimpleNamespace(chunk_content="Second chunk"),
                ],
                sources=[
                    SimpleNamespace(title="Guide", id="source-1"),
                    SimpleNamespace(title=None, id=None),
                ],
            )
        )

        self.assertEqual(
            "Context:\nFirst chunk\n\nSecond chunk\n\n"
            "Sources:\n- Guide (source-1)\n- Untitled ()",
            namespace["build_context_block"](payload),
        )

    def test_cookbook_04_uses_notion_workspace_title(self):
        expected_title = "Build Notion AI for Your Workspace"
        expected_files = {
            "cookbooks/cookbook-04-build-notion-ai.mdx": 1,
            "cookbooks/v2/cookbook-04-build-notion-ai.mdx": 1,
            "cookbooks/index.mdx": 1,
            "cookbooks/v2/index.mdx": 1,
        }
        for relative, count in expected_files.items():
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertEqual(count, text.count(expected_title), relative)

        agents = (ROOT / "AGENTS.mdx").read_text(encoding="utf-8")
        self.assertIn("| **Notion AI for Your Workspace** |", agents)

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
