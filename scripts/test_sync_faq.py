"""Tests for sync_faq.py. Run: python -m unittest discover -s scripts -p 'test_sync_faq.py'"""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sync_faq  # noqa: E402


class HtmlToTextTest(unittest.TestCase):
    def test_void_elements_do_not_drop_the_text_after_them(self):
        self.assertEqual(sync_faq.html_to_text('<main><input type="hidden"><p>Answer</p></main>'), "Answer")
        self.assertIn("After", sync_faq.html_to_text('<video><source src="a.mp4"></video><p>After</p>'))

    def test_nested_wrappers_keep_all_their_content(self):
        for html in ('<main><div><div>Intro</div><p>Answer</p></div></main>',
                     '<div id="content"><div>Intro</div><p>Answer</p></div>'):
            text = sync_faq.html_to_text(html)
            self.assertIn("Intro", text)
            self.assertIn("Answer", text)

    def test_main_is_preferred_over_page_chrome(self):
        html = "<header>Nav</header><div>Promo</div><main>Body</main><footer>Foot</footer>"
        self.assertEqual(sync_faq.html_to_text(html), "Body")


class MdxToTextTest(unittest.TestCase):
    def test_component_attributes_keep_their_visible_text(self):
        text = sync_faq.mdx_to_text('| <Field name="ids" type="string[]" required /> | One or more ids |\n'
                                    '<Tab title="Python">body</Tab>')
        self.assertIn("ids (string[], required)", text)
        self.assertIn("Python", text)

    def test_code_blocks_keep_their_imports(self):
        text = sync_faq.mdx_to_text('import { Card } from "x"\nIntro\n```python\nimport hydradb\n<div>kept</div>\n```\n')
        self.assertNotIn("import { Card }", text)
        self.assertIn("import hydradb", text)
        self.assertIn("<div>kept</div>", text)


class QaTest(unittest.TestCase):
    def test_id_comes_from_the_question_not_its_position(self):
        self.assertEqual(sync_faq.qa_id("What is HydraDB?"), sync_faq.qa_id("What is HydraDB?"))
        self.assertNotEqual(sync_faq.qa_id("What is HydraDB?"), sync_faq.qa_id("Is HydraDB open source?"))

    def test_a_missing_qa_file_aborts_instead_of_returning_nothing(self):
        with self.assertRaises(RuntimeError):
            sync_faq.build_faq_items(Path("missing-qa.yaml"), "db")


class WaitIndexedTest(unittest.TestCase):
    def _run(self, *rounds):
        replies = iter(rounds)

        def fake_api(method, path, *args, **kwargs):
            states = next(replies)
            statuses = [{"id": i, "indexing_status": s, "error_code": "parse_failed"} for i, s in states.items()]
            return 200, json.dumps({"data": {"statuses": statuses}})

        with mock.patch.object(sync_faq, "api_request", fake_api), \
                mock.patch.object(sync_faq, "STATUS_POLL_SECONDS", 0):
            sync_faq.wait_indexed(["a", "b"], "key", "https://api", "db")

    def test_waits_until_every_item_is_completed(self):
        self._run({"a": "processing", "b": "completed"}, {"a": "completed"})

    def test_an_errored_item_fails_the_run(self):
        with self.assertRaises(RuntimeError):
            self._run({"a": "errored", "b": "completed"})


if __name__ == "__main__":
    unittest.main()
