import json
import unittest

from generate_llms_index import ROOT, page_paths


LEGACY_REDIRECTS = {
    "/get-started/introduction": "/get-started/introduction",
    "/get-started/quickstart": "/get-started/quickstart",
    "/get-started/core-concepts": "/get-started/quickstart",
    "/essentials/api-results": "/essentials/query#7-use-the-results",
    "/essentials/app-sources": "/essentials/context#send-app-sources",
    "/essentials/architecture": "/get-started/introduction",
    "/essentials/context-graphs": "/essentials/query#5-add-relationships",
    "/essentials/knowledge": "/essentials/context",
    "/essentials/memories": "/essentials/context#2-save-memories",
    "/essentials/metadata": "/essentials/metadata",
    "/essentials/multi-tenant": "/essentials/databases-and-collections",
    "/essentials/semantic-search": "/essentials/query",
    "/essentials/webhooks": "/api-reference/endpoint/webhooks-overview",
    "/cookbooks/index": "/cookbooks/index",
    "/cookbooks/glean-clone": "/cookbooks/glean",
    "/cookbooks/ai-chief-of-staff": "/cookbooks/chief-of-staff",
    "/cookbooks/ai-linkedin-recruiter": "/cookbooks/index",
    "/cookbooks/ai-travel-planner": "/cookbooks/personal-assistant",
    "/cookbooks/cookbook-01-build-cursor-for-docs": "/cookbooks/cursor",
    "/cookbooks/customer-support-agent": "/cookbooks/support-agent",
    "/cookbooks/cookbook-04-build-notion-ai": "/cookbooks/glean",
    "/cookbooks/competitive-intelligence-agent": "/cookbooks/competitive-intelligence",
    "/cookbooks/hydradb-cookbook-06": "/cookbooks/chief-of-staff",
    "/cookbooks/internal-search-perplexity": "/cookbooks/glean",
    "/cookbooks/ai-onboarding-agent": "/cookbooks/glean",
    "/cookbooks/cookbook-10-ai-financial-analyst": "/cookbooks/index",
    "/api-reference/index": "/api-reference/index",
    "/api-reference/sdks": "/api-reference/sdks",
    "/api-reference/error-responses": "/api-reference/error-responses",
    "/essentials/recall": "/essentials/query",
    "/api-reference/endpoint/add-memory": "/api-reference/endpoint/ingest-context",
    "/api-reference/endpoint/boolean-recall": "/api-reference/endpoint/query",
    "/api-reference/endpoint/create-tenant": "/api-reference/endpoint/create-database",
    "/api-reference/endpoint/delete-knowledge": "/api-reference/endpoint/delete-source",
    "/api-reference/endpoint/delete-memory": "/api-reference/endpoint/delete-source",
    "/api-reference/endpoint/delete-tenant": "/api-reference/endpoint/delete-database",
    "/api-reference/endpoint/fetch-content": "/api-reference/endpoint/fetch-content",
    "/api-reference/endpoint/full-recall": "/api-reference/endpoint/query",
    "/api-reference/endpoint/graph-relations": "/api-reference/endpoint/source-relations",
    "/api-reference/endpoint/infra-status": "/api-reference/endpoint/database-status",
    "/api-reference/endpoint/ingestion-overview": "/api-reference/endpoint/context-overview",
    "/api-reference/endpoint/list-data": "/api-reference/endpoint/list-context",
    "/api-reference/endpoint/list-overview": "/api-reference/endpoint/list-context",
    "/api-reference/endpoint/list-sub-tenant-ids": "/api-reference/endpoint/list-collections",
    "/api-reference/endpoint/list-tenant-ids": "/api-reference/endpoint/list-databases",
    "/api-reference/endpoint/memories-overview": "/api-reference/endpoint/context-overview",
    "/api-reference/endpoint/monitor-tenant": "/api-reference/endpoint/database-stats",
    "/api-reference/endpoint/recall-overview": "/api-reference/endpoint/query-overview",
    "/api-reference/endpoint/recall-preferences": "/api-reference/endpoint/query",
    "/api-reference/endpoint/tenants-overview": "/api-reference/endpoint/databases-overview",
    "/api-reference/endpoint/upload-knowledge": "/api-reference/endpoint/ingest-context",
    "/api-reference/endpoint/verify-processing": "/api-reference/endpoint/source-status"
}


class LegacyRedirectTest(unittest.TestCase):
    def test_only_current_api_pages_are_available_in_navigation(self):
        config = json.loads((ROOT / "docs.json").read_text())
        self.assertNotIn("versions", config["navigation"])
        pages = page_paths(config["navigation"])
        self.assertFalse(pages & {source.lstrip("/") for source, target in LEGACY_REDIRECTS.items() if source != target})
        self.assertTrue({"AGENTS", "plugins/cli", "plugins/mcp", "plugins/ecosystem"} <= pages)

    def test_every_retired_page_reaches_its_current_replacement(self):
        config = json.loads((ROOT / "docs.json").read_text())
        redirects = {redirect["source"]: redirect for redirect in config["redirects"]}
        self.assertEqual(len(config["redirects"]), len(redirects))
        for source, target in LEGACY_REDIRECTS.items():
            with self.subTest(source=source):
                if source == target:
                    self.assertNotIn(source, redirects)
                    self.assertTrue((ROOT / (target.split("#")[0].lstrip("/") + ".mdx")).is_file())
                    continue
                self.assertIn(source, redirects)
                self.assertEqual(target, redirects[source]["destination"])
                self.assertTrue(redirects[source]["permanent"])
                self.assertNotIn(target, redirects)
                self.assertTrue((ROOT / (target.split("#")[0].lstrip("/") + ".mdx")).is_file())
                self.assertFalse((ROOT / (source.lstrip("/") + ".mdx")).exists())

    def test_agent_index_does_not_advertise_retired_pages(self):
        index = (ROOT / "llms.txt").read_text()
        self.assertNotIn("Legacy v1", index)
        for source, target in LEGACY_REDIRECTS.items():
            with self.subTest(source=source):
                link = f"https://docs.hydradb.com{source}.md)"
                if source == target:
                    self.assertIn(link, index)
                else:
                    self.assertNotIn(link, index)

    def test_retired_markdown_urls_reach_the_current_markdown(self):
        config = json.loads((ROOT / "docs.json").read_text())
        redirects = {redirect["source"]: redirect for redirect in config["redirects"]}
        for source, target in LEGACY_REDIRECTS.items():
            with self.subTest(source=source):
                if source == target:
                    self.assertNotIn(source + ".md", redirects)
                    continue
                self.assertIn(source + ".md", redirects)
                self.assertEqual(target.split("#")[0] + ".md" + ("#" + target.split("#", 1)[1] if "#" in target else ""), redirects[source + ".md"]["destination"])
                self.assertTrue(redirects[source + ".md"]["permanent"])

    def test_legacy_openapi_url_serves_the_current_spec(self):
        self.assertEqual(
            (ROOT / "api-reference/v2/openapi.json").read_bytes(),
            (ROOT / "api-reference/openapi.json").read_bytes(),
        )


if __name__ == "__main__":
    unittest.main()
