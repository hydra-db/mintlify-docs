import unittest

from generate_connector_pages import DIRECTORY, load_copy, pages


class ZoomChatDocsTests(unittest.TestCase):
    def test_zoom_documents_team_chat_files_and_scoped_parent(self):
        copy = load_copy("zoom")
        documents = {document["name"]: document for document in copy["documents"]}
        self.assertIn("Team Chat messages", documents)
        self.assertIn("Team Chat files", documents)
        files = documents["Team Chat files"]
        self.assertEqual(files["kind"], "knowledge_base")
        attributes = {item["name"]: item for item in files["attributes"]}
        self.assertIn("Graph connection", attributes["parent_id"]["uses"])
        self.assertIn("same user", attributes["parent_id"]["text"])
        page = pages()[DIRECTORY / "zoom.mdx"]
        for text in (
            "Team Chat messages", "Team Chat files", "PDF", "DOCX", "six months",
            "team_chat:read:list_user_channels:admin",
            "team_chat:read:list_user_messages:admin", "team_chat:read:file:admin",
            "Chat Messages Edit", "50 MiB",
        ):
            with self.subTest(text=text):
                self.assertIn(text, page)


if __name__ == "__main__":
    unittest.main()
