"""Execute the context guide's Python polling example without network calls."""
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

DOCUMENT = Path(__file__).resolve().parents[1] / "essentials/v2/context.mdx"


class IndexingPollTest(unittest.TestCase):
    def run_example(self, pages):
        text = DOCUMENT.read_text().split('title="Poll until indexed:', 1)[1]
        source = text.split("```python Python SDK\n", 1)[1].split("```", 1)[0]
        status = Mock(side_effect=[NS(data=NS(statuses=[NS(id=str(i), indexing_status=s) for i, s in enumerate(page)])) for page in pages])
        with patch("time.sleep"):
            exec(compile(source, "context-poll.py", "exec"), {"client": NS(context=NS(status=status))})
        return status.call_count

    def test_failed_indexing_stops_the_example(self):
        with self.assertRaisesRegex(RuntimeError, "Indexing failed"):
            self.run_example([["completed", "errored"]])

    def test_empty_or_partial_results_do_not_mean_success(self):
        self.assertEqual(3, self.run_example([[], ["completed"], ["completed", "completed"]]))

    def test_completed_sources_allow_the_example_to_continue(self):
        self.assertEqual(1, self.run_example([["completed", "completed"]]))
