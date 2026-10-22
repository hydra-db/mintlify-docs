#!/usr/bin/env node
// One-off: ingest a handful of real docs pages into HydraDB so the local
// Ask-AI gateway has something to retrieve. Uses the same /context/ingest
// contract as _ci-proposed/scripts/sync-docs.mjs, but built-in fetch only.
import fs from "node:fs";
import path from "node:path";

const KEY = process.env.HYDRA_DB_API_KEY;
const DATABASE = process.env.HYDRA_DB_DATABASE || "default";
const COLLECTION = process.env.HYDRA_DB_DOCS_COLLECTION || "docs";
const BASE = process.env.HYDRA_BASE_URL || "https://api.hydradb.com";
if (!KEY) { console.error("HYDRA_DB_API_KEY required"); process.exit(1); }

const PAGES = [
  "get-started/v2/quickstart",
  "get-started/v2/introduction",
  "get-started/v2/core-concepts",
  "essentials/v2/knowledge",
  "essentials/v2/memories",
  "essentials/v2/recall",
  "essentials/v2/semantic-search",
  "essentials/v2/multi-tenant",
];

function parseFrontmatter(raw) {
  const m = raw.match(/^---\n([\s\S]*?)\n---\n?/);
  if (!m) return { data: {}, body: raw };
  const data = {};
  for (const line of m[1].split("\n")) {
    const kv = line.match(/^([A-Za-z0-9_-]+):\s*(.*)$/);
    if (kv) data[kv[1]] = kv[2].trim().replace(/^["']|["']$/g, "");
  }
  return { data, body: raw.slice(m[0].length) };
}

const items = [];
for (const route of PAGES) {
  const rel = fs.existsSync(route + ".mdx") ? route + ".mdx" : route + ".md";
  if (!fs.existsSync(rel)) { console.warn("skip (missing):", route); continue; }
  const { data, body } = parseFrontmatter(fs.readFileSync(rel, "utf-8"));
  const title = data.title || route.split("/").pop();
  const md = data.description ? `# ${title}\n\n${data.description}\n\n${body}` : `# ${title}\n\n${body}`;
  items.push({
    id: "doc--" + route.replace(/\//g, "--"),
    database: DATABASE,
    collection: COLLECTION,
    title,
    type: "knowledge_base",
    url: "/" + route,
    timestamp: new Date().toISOString(),
    content: { markdown: md.slice(0, 20000) },
    tenant_metadata: { src_kind: "docs", section: route.split("/")[0], page: route, repo: "mintlify-docs" },
    additional_metadata: { repo_path: rel },
  });
}
console.log(`ingesting ${items.length} pages → ${DATABASE}/${COLLECTION}`);

const form = new FormData();
form.set("database", DATABASE);
form.set("collection", COLLECTION);
form.set("type", "knowledge");
form.set("upsert", "true");
form.set("app_knowledge", JSON.stringify(items));

const res = await fetch(`${BASE}/context/ingest`, {
  method: "POST",
  headers: { Authorization: `Bearer ${KEY}`, "API-Version": "2" },
  body: form,
});
const text = await res.text();
console.log("status", res.status);
console.log(text.slice(0, 1500));
