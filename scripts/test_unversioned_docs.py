import json
import re
import unittest

from generate_llms_index import ROOT, page_paths
from sync_datalake import nav_pages
from sync_faq import docs_guides_pages

VERSIONED_PAGES = {
    "api-reference/endpoint/add-connector-resource": "/api-reference/endpoint/add-connector-resource",
    "api-reference/endpoint/configure-connector": "/api-reference/endpoint/configure-connector",
    "api-reference/endpoint/connector-resources": "/api-reference/endpoint/connector-resources",
    "api-reference/endpoint/connectors-overview": "/api-reference/endpoint/connectors-overview",
    "api-reference/endpoint/create-connector": "/api-reference/endpoint/create-connector",
    "api-reference/endpoint/create-tenant": "/api-reference/endpoint/create-database",
    "api-reference/endpoint/delete-collection": "/api-reference/endpoint/delete-collection",
    "api-reference/endpoint/delete-connector-resource": "/api-reference/endpoint/delete-connector-resource",
    "api-reference/endpoint/delete-connector": "/api-reference/endpoint/delete-connector",
    "api-reference/endpoint/delete-source": "/api-reference/endpoint/delete-source",
    "api-reference/endpoint/delete-tenant": "/api-reference/endpoint/delete-database",
    "api-reference/endpoint/delete-webhook": "/api-reference/endpoint/delete-webhook",
    "api-reference/endpoint/disable-webhook-signing": "/api-reference/endpoint/disable-webhook-signing",
    "api-reference/endpoint/discover-connector-resources": "/api-reference/endpoint/discover-connector-resources",
    "api-reference/endpoint/fetch-content": "/api-reference/endpoint/fetch-content",
    "api-reference/endpoint/get-connector-status": "/api-reference/endpoint/get-connector-status",
    "api-reference/endpoint/get-connector": "/api-reference/endpoint/get-connector",
    "api-reference/endpoint/get-metadata-schema": "/api-reference/endpoint/get-metadata-schema",
    "api-reference/endpoint/get-webhook-delivery": "/api-reference/endpoint/get-webhook-delivery",
    "api-reference/endpoint/get-webhook": "/api-reference/endpoint/get-webhook",
    "api-reference/endpoint/ingest-context": "/api-reference/endpoint/ingest-context",
    "api-reference/endpoint/list-connector-providers": "/api-reference/endpoint/list-connector-providers",
    "api-reference/endpoint/list-connectors": "/api-reference/endpoint/list-connectors",
    "api-reference/endpoint/list-documents": "/api-reference/endpoint/list-context",
    "api-reference/endpoint/list-sub-tenants": "/api-reference/endpoint/list-collections",
    "api-reference/endpoint/list-tenants": "/api-reference/endpoint/list-databases",
    "api-reference/endpoint/list-webhook-deliveries": "/api-reference/endpoint/list-webhook-deliveries",
    "api-reference/endpoint/pause-connector": "/api-reference/endpoint/pause-connector",
    "api-reference/endpoint/query-overview": "/api-reference/endpoint/query-overview",
    "api-reference/endpoint/query": "/api-reference/endpoint/query",
    "api-reference/endpoint/register-webhook": "/api-reference/endpoint/register-webhook",
    "api-reference/endpoint/resume-connector": "/api-reference/endpoint/resume-connector",
    "api-reference/endpoint/retry-webhook-delivery": "/api-reference/endpoint/retry-webhook-delivery",
    "api-reference/endpoint/set-webhook-signing-secret": "/api-reference/endpoint/set-webhook-signing-secret",
    "api-reference/endpoint/source-relations": "/api-reference/endpoint/source-relations",
    "api-reference/endpoint/source-status": "/api-reference/endpoint/source-status",
    "api-reference/endpoint/sources-overview": "/api-reference/endpoint/context-overview",
    "api-reference/endpoint/subgraph": "/api-reference/endpoint/subgraph",
    "api-reference/endpoint/submit-feedback": "/api-reference/endpoint/submit-feedback",
    "api-reference/endpoint/sync-connector": "/api-reference/endpoint/sync-connector",
    "api-reference/endpoint/tenant-stats": "/api-reference/endpoint/database-stats",
    "api-reference/endpoint/tenant-status": "/api-reference/endpoint/database-status",
    "api-reference/endpoint/tenants-overview": "/api-reference/endpoint/databases-overview",
    "api-reference/endpoint/test-webhook": "/api-reference/endpoint/test-webhook",
    "api-reference/endpoint/update-connector-resource": "/api-reference/endpoint/update-connector-resource",
    "api-reference/endpoint/update-connector": "/api-reference/endpoint/update-connector",
    "api-reference/endpoint/update-metadata-schema": "/api-reference/endpoint/update-metadata-schema",
    "api-reference/endpoint/update-source-metadata": "/api-reference/endpoint/update-source-metadata",
    "api-reference/endpoint/webhooks-overview": "/api-reference/endpoint/webhooks-overview",
    "api-reference/error-responses": "/api-reference/error-responses",
    "api-reference/index": "/api-reference/index",
    "api-reference/sdks": "/api-reference/sdks",
    "cookbooks/ai-chief-of-staff": "/cookbooks/chief-of-staff",
    "cookbooks/ai-linkedin-recruiter": "/cookbooks/index",
    "cookbooks/ai-onboarding-agent": "/cookbooks/glean",
    "cookbooks/ai-travel-planner": "/cookbooks/personal-assistant",
    "cookbooks/competitive-intelligence-agent": "/cookbooks/competitive-intelligence",
    "cookbooks/cookbook-01-build-cursor-for-docs": "/cookbooks/cursor",
    "cookbooks/cookbook-04-build-notion-ai": "/cookbooks/glean",
    "cookbooks/cookbook-10-ai-financial-analyst": "/cookbooks/index",
    "cookbooks/customer-support-agent": "/cookbooks/support-agent",
    "cookbooks/glean-clone": "/cookbooks/glean",
    "cookbooks/hydradb-cookbook-06": "/cookbooks/chief-of-staff",
    "cookbooks/index": "/cookbooks/index",
    "cookbooks/internal-search-perplexity": "/cookbooks/glean",
    "essentials/access-control": "/essentials/query#6-query-on-behalf-of-a-user",
    "essentials/api-results": "/essentials/query#7-use-the-results",
    "essentials/app-sources": "/essentials/context#send-app-sources",
    "essentials/architecture": "/get-started/introduction",
    "essentials/bring-your-own-graph": "/essentials/bring-your-own-graph",
    "essentials/connectors": "/essentials/connectors",
    "essentials/context-graphs": "/essentials/query#5-add-relationships",
    "essentials/glossary": "/get-started/introduction",
    "essentials/knowledge": "/essentials/context",
    "essentials/memories": "/essentials/context#2-save-memories",
    "essentials/metadata": "/essentials/metadata",
    "essentials/multi-tenant": "/essentials/databases-and-collections",
    "essentials/query": "/essentials/query",
    "essentials/semantic-search": "/essentials/query",
    "essentials/webhooks": "/api-reference/endpoint/webhooks-overview",
    "get-started/core-concepts": "/get-started/quickstart",
    "get-started/introduction": "/get-started/introduction",
    "get-started/quickstart": "/get-started/quickstart",
    "essentials/context": "/essentials/context",
    "essentials/databases-and-collections": "/essentials/databases-and-collections",
    "api-reference/endpoint/context-overview": "/api-reference/endpoint/context-overview",
    "api-reference/endpoint/create-database": "/api-reference/endpoint/create-database",
    "api-reference/endpoint/database-stats": "/api-reference/endpoint/database-stats",
    "api-reference/endpoint/database-status": "/api-reference/endpoint/database-status",
    "api-reference/endpoint/databases-overview": "/api-reference/endpoint/databases-overview",
    "api-reference/endpoint/delete-database": "/api-reference/endpoint/delete-database",
    "api-reference/endpoint/list-collections": "/api-reference/endpoint/list-collections",
    "api-reference/endpoint/list-context": "/api-reference/endpoint/list-context",
    "api-reference/endpoint/list-databases": "/api-reference/endpoint/list-databases",
    "cookbooks/chief-of-staff": "/cookbooks/chief-of-staff",
    "cookbooks/cursor": "/cookbooks/cursor",
    "cookbooks/competitive-intelligence": "/cookbooks/competitive-intelligence",
    "cookbooks/glean": "/cookbooks/glean",
    "cookbooks/personal-assistant": "/cookbooks/personal-assistant",
    "cookbooks/support-agent": "/cookbooks/support-agent"
}

class UnversionedDocsTest(unittest.TestCase):
    def test_navigation_has_no_version_picker_or_versioned_paths(self):
        config = json.loads((ROOT / "docs.json").read_text())
        self.assertNotIn("versions", config["navigation"])
        self.assertEqual(["Guides", "API Reference", "Cookbooks"], [tab["tab"] for tab in config["navigation"]["tabs"]])
        pages = page_paths(config["navigation"])
        self.assertIn("get-started/quickstart", pages)
        self.assertIn("api-reference/endpoint/query", pages)
        self.assertFalse(any("/v2/" in page for page in pages))

    def test_versioned_page_urls_redirect_directly_to_current_pages(self):
        config = json.loads((ROOT / "docs.json").read_text())
        redirects = {item["source"]: item for item in config["redirects"]}
        for slug, replacement in VERSIONED_PAGES.items():
            self.assertTrue((ROOT / (replacement.split("#")[0].lstrip("/") + ".mdx")).is_file())
            directory, rest = slug.split("/", 1)
            for suffix in ("", ".md"):
                source = f"/{directory}/v2/{rest}{suffix}"
                path, _, fragment = replacement.partition("#")
                target = path + suffix + ("#" + fragment if fragment else "")
                with self.subTest(source=source):
                    self.assertEqual(target, redirects[source]["destination"])
                    self.assertTrue(redirects[source]["permanent"])
                    self.assertNotIn(target, redirects)

    def test_published_content_and_schema_names_do_not_brand_the_api_as_v2(self):
        paths = [ROOT / "AGENTS.mdx", ROOT / "llms.txt"]
        for directory in ("get-started", "essentials", "api-reference", "cookbooks", "plugins"):
            paths.extend((ROOT / directory).rglob("*.mdx"))
        paths.append(ROOT / "api-reference/openapi.json")
        for path in paths:
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertIsNone(re.search(r"(?<![a-z])v2(?![a-z])", path.read_text(), re.I))

    def test_syncs_read_the_unversioned_navigation(self):
        config = json.loads((ROOT / "docs.json").read_text())
        lake_pages = {page for _, _, page in nav_pages(config)}
        guide_pages = set(docs_guides_pages(ROOT / "docs.json"))
        self.assertEqual(page_paths(config["navigation"]), lake_pages)
        self.assertIn("get-started/quickstart", guide_pages)
        self.assertTrue(guide_pages <= lake_pages)

    def test_docs_do_not_offer_a_previous_api_or_sdk_migration(self):
        self.assertNotIn("v1 API Reference", (ROOT / "api-reference/index.mdx").read_text())
        self.assertNotIn("Migrating from v1", (ROOT / "api-reference/sdks.mdx").read_text())


if __name__ == "__main__":
    unittest.main()
