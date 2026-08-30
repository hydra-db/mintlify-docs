#!/usr/bin/env node
/**
 * HydraDB docs ingestion — Cortex-style, SDK-free (built-in fetch only).
 *
 * Replicates the internal Cortex docs-sync approach against the public HydraDB
 * REST API (v2), so the "Ask AI" widget's gateway has a real, filterable corpus
 * to retrieve and cite:
 *
 *   1. ensure the target database exists and its infra is ready for ingestion
 *   2. register match-enabled metadata fields (so /query metadata_filters work —
 *      this is what makes "search this page" and "search all docs" both correct)
 *   3. ingest every published page from docs.json into one collection, each row
 *      carrying tenant_metadata (src_kind/section/folder/page/repo) + a stable id
 *   4. poll /context/status until every source finishes indexing
 *
 * Re-runnable: ids are derived from the repo path, and upsert=true replaces in
 * place, so running again just refreshes changed pages (no duplicates).
 *
 * Env:
 *   HYDRA_DB_API_KEY          (required) Bearer token
 *   HYDRA_DB_DATABASE         target database   (default: hydra_docs)
 *   HYDRA_DB_DOCS_COLLECTION  target collection (default: docs)
 *   HYDRA_BASE_URL            API base          (default: https://api.hydradb.com)
 */
import fs from "node:fs";
import path from "node:path";

const ROOT = process.cwd();
const KEY = process.env.HYDRA_DB_API_KEY;
const DATABASE = process.env.HYDRA_DB_DATABASE || "hydra_docs";
const COLLECTION = process.env.HYDRA_DB_DOCS_COLLECTION || "docs";
const REPO = process.env.HYDRA_DB_SYNC_REPO || "mintlify-docs";
const BASE = (process.env.HYDRA_BASE_URL || "https://api.hydradb.com").replace(/\/$/, "");
const BATCH_SIZE = Number(process.env.HYDRA_DB_SYNC_BATCH || 15);
const WAIT_TIMEOUT_MS = Number(process.env.HYDRA_DB_SYNC_TIMEOUT_MS || 10 * 60_000);

if (!KEY) die("HYDRA_DB_API_KEY is required");

// Match-enabled string fields → usable as /query metadata_filters. `page` lets
// the widget scope a search to the current page; `section`/`folder`/`repo` scope
// to a docs area; `src_kind` separates docs from any other ingested content.
const SCHEMA_FIELDS = [
  { name: "src_kind", data_type: "VARCHAR", enable_match: true, max_length: 64 },
  { name: "section", data_type: "VARCHAR", enable_match: true, max_length: 128 },
  { name: "folder", data_type: "VARCHAR", enable_match: true, max_length: 256 },
  { name: "page", data_type: "VARCHAR", enable_match: true, max_length: 512 },
  { name: "repo", data_type: "VARCHAR", enable_match: true, max_length: 256 },
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const isDoc = (n) => n.endsWith(".mdx") || n.endsWith(".md");
const stripExt = (p) => p.replace(/\.mdx?$/, "");
function die(msg) { console.error(`✗ ${msg}`); process.exit(1); }

function jsonHeaders() {
  return { Authorization: `Bearer ${KEY}`, "API-Version": "2", "Content-Type": "application/json" };
}
async function api(method, pathname, body) {
  const res = await fetch(`${BASE}${pathname}`, {
    method,
    headers: jsonHeaders(),
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { /* non-JSON */ }
  return { ok: res.ok, status: res.status, data, text };
}

// ── docs collection (drive off docs.json, exactly the published routes) ───────
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
function titleFrom(data, body, filename) {
  if (data.title) return data.title;
  const m = body.match(/^#\s+(.+)$/m);
  if (m) return m[1].trim();
  return stripExt(filename).replace(/^\d+-/, "").split(/[-_]/)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
}
function collectPagePaths(node, out = []) {
  if (Array.isArray(node)) { for (const x of node) collectPagePaths(x, out); }
  else if (node && typeof node === "object") {
    for (const [k, v] of Object.entries(node)) {
      if (k === "pages" && Array.isArray(v)) {
        for (const p of v) (typeof p === "string" ? out.push(p) : collectPagePaths(p, out));
      } else collectPagePaths(v, out);
    }
  }
  return out;
}
function makeDoc(route) {
  const rel = fs.existsSync(path.join(ROOT, route + ".mdx")) ? route + ".mdx"
    : fs.existsSync(path.join(ROOT, route + ".md")) ? route + ".md" : null;
  if (!rel) return null;
  const abs = path.join(ROOT, rel);
  const { data, body } = parseFrontmatter(fs.readFileSync(abs, "utf-8"));
  const slug = route.split("/");
  return {
    slug, relPath: rel,
    title: titleFrom(data, body, slug[slug.length - 1]),
    description: data.description || "",
    content: body,
    mtime: fs.statSync(abs).mtime,
  };
}
function collectDocs() {
  const cfgPath = path.join(ROOT, "docs.json");
  if (!fs.existsSync(cfgPath)) die("docs.json not found (run from repo root)");
  const cfg = JSON.parse(fs.readFileSync(cfgPath, "utf-8"));
  const routes = [...new Set(collectPagePaths(cfg.navigation || cfg))];
  const docs = [];
  for (const route of routes) {
    const doc = makeDoc(route);
    if (doc) docs.push(doc);
    else console.warn(`! docs.json references "${route}" but no .mdx/.md file was found`);
  }
  return docs;
}
function toItem(doc) {
  const md = doc.description
    ? `# ${doc.title}\n\n${doc.description}\n\n${doc.content}`
    : doc.content;
  return {
    id: `doc--${doc.slug.join("--").toLowerCase()}`,
    database: DATABASE,
    collection: COLLECTION,
    title: doc.title,
    type: "knowledge_base",
    url: "/" + doc.slug.join("/"),
    timestamp: doc.mtime.toISOString(),
    content: { markdown: md },
    tenant_metadata: {
      src_kind: "docs",
      section: doc.slug[0],
      folder: doc.relPath.replace(/\/[^/]+\.mdx?$/, "") || doc.slug[0],
      page: doc.slug.join("/"),
      repo: REPO,
    },
    additional_metadata: { repo_path: doc.relPath, synced_at: new Date().toISOString() },
  };
}

// ── infra steps ───────────────────────────────────────────────────────────────
async function ensureDatabase() {
  const list = await api("GET", "/databases");
  const names = list.data?.data?.databases || list.data?.data?.tenant_ids || [];
  if (names.includes(DATABASE)) {
    console.log(`• database "${DATABASE}" already exists`);
  } else {
    const res = await api("POST", "/databases", { database: DATABASE });
    if (res.ok || /already exists/i.test(res.text)) console.log(`✓ created database "${DATABASE}"`);
    else die(`create database failed (${res.status}): ${res.text.slice(0, 300)}`);
  }
  // Wait for infra readiness.
  const deadline = Date.now() + WAIT_TIMEOUT_MS;
  for (;;) {
    const s = await api("GET", `/databases/status?database=${encodeURIComponent(DATABASE)}`);
    const infra = s.data?.data?.infra || {};
    if (infra.ready_for_ingestion ?? infra.readyForIngestion) break;
    if (Date.now() > deadline) die("timed out waiting for database infrastructure");
    console.log("… waiting for database infrastructure …");
    await sleep(5_000);
  }
  console.log(`✓ database "${DATABASE}" ready for ingestion`);
}

async function ensureSchema() {
  for (const field of SCHEMA_FIELDS) {
    const res = await api(
      "PATCH",
      `/databases/${encodeURIComponent(DATABASE)}/metadata-schema`,
      { add_fields: [field] }
    );
    if (res.ok) console.log(`✓ metadata field "${field.name}" ready`);
    else if (/exist|duplicate/i.test(res.text)) console.log(`• metadata field "${field.name}" already present`);
    else console.warn(`! metadata field "${field.name}" skipped (${res.status}): ${res.text.slice(0, 160)}`);
  }
}

async function ingestBatch(items) {
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
  if (!res.ok) throw new Error(`ingest failed (${res.status}): ${text.slice(0, 300)}`);
  let data = null; try { data = JSON.parse(text); } catch {}
  const results = data?.data?.results || data?.results || [];
  return results.map((r) => r.id).filter(Boolean);
}

async function waitIndexed(ids) {
  if (!ids.length) return;
  const deadline = Date.now() + WAIT_TIMEOUT_MS;
  const pending = new Set(ids);
  while (pending.size && Date.now() < deadline) {
    const qs = `database=${encodeURIComponent(DATABASE)}&collection=${encodeURIComponent(COLLECTION)}` +
      `&ids=${[...pending].map(encodeURIComponent).join(",")}`;
    const res = await api("GET", `/context/status?${qs}`);
    const statuses = res.data?.data?.statuses || res.data?.statuses || [];
    for (const st of statuses) {
      const status = st.indexing_status || st.indexingStatus || st.status;
      if (status === "completed" || status === "graph_creation") pending.delete(st.id);
      else if (status === "errored" || status === "failed") {
        throw new Error(`source ${st.id}: ${st.error_message || st.errorMessage || "indexing failed"}`);
      }
    }
    if (pending.size) await sleep(3_000);
  }
  if (pending.size) throw new Error(`timed out indexing ${pending.size} source(s): ${[...pending].join(", ")}`);
}

async function main() {
  console.log(`HydraDB ingest → ${BASE}  database=${DATABASE}  collection=${COLLECTION}`);
  const docs = collectDocs();
  if (!docs.length) die("no doc pages found");
  console.log(`found ${docs.length} published page(s) in docs.json`);

  await ensureDatabase();
  await ensureSchema();

  const items = docs.map(toItem);
  let done = 0;
  for (let i = 0; i < items.length; i += BATCH_SIZE) {
    const batch = items.slice(i, i + BATCH_SIZE);
    const ids = await ingestBatch(batch);
    await waitIndexed(ids);
    done += batch.length;
    console.log(`  ✓ ${done}/${items.length} indexed`);
  }
  const stats = await api("GET", `/databases/stats?database=${encodeURIComponent(DATABASE)}`);
  console.log("✓ ingestion complete");
  if (stats.data?.data) console.log(JSON.stringify(stats.data.data).slice(0, 400));
}

main().catch((err) => { console.error("✗ ingest failed:", err?.message || err); process.exit(1); });
