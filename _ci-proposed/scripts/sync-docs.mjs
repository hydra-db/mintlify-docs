#!/usr/bin/env node
/**
 * HydraDB docs sync (Mintlify edition).
 *
 * Ingests every Mintlify page (.mdx/.md) of this repo into a HydraDB collection
 * so the "Ask AI" widget can retrieve and cite them. Adapted from the internal
 * docs' scripts/sync-docs.mjs; same SDK ingest contract, adjusted for Mintlify
 * (MDX + YAML frontmatter, path-based URLs, no fixed section list).
 *
 * Re-runnable at any time:
 *   - Every page maps to a stable source id from its repo path, so HydraDB's
 *     upsert (same id -> replace) keeps content fresh without duplicates.
 *   - A local manifest records the sha256 of each ingested page; re-runs only
 *     push pages whose hash changed.
 *   - Pages deleted from the repo are pruned from HydraDB (--no-prune to skip).
 *
 * Usage:
 *   node scripts/sync-docs.mjs                 # incremental sync
 *   node scripts/sync-docs.mjs --full          # re-ingest every page
 *   node scripts/sync-docs.mjs --since-ref=HEAD^   # CI: only files in that git range
 *   node scripts/sync-docs.mjs --dry-run       # print what would change
 *
 * Env:
 *   HYDRA_DB_API_KEY          (required) Bearer token for HydraDB
 *   HYDRA_DB_DATABASE         target database   (default: hydra_docs)
 *   HYDRA_DB_DOCS_COLLECTION  target collection (default: docs)
 *   HYDRA_DB_SYNC_BATCH       batch size        (default: 15)
 *   HYDRA_DB_SYNC_TIMEOUT_MS  index-wait timeout(default: 600000)
 */

import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { execSync } from "node:child_process";
import { HydraDBClient } from "@hydradb/sdk";

const ROOT = process.cwd();
const DATABASE = process.env.HYDRA_DB_DATABASE || "hydra_docs";
const COLLECTION = process.env.HYDRA_DB_DOCS_COLLECTION || "docs";
const REPO = process.env.HYDRA_DB_SYNC_REPO || "mintlify-docs";
const MANIFEST_PATH = path.join(ROOT, `.hydradb-sync-manifest.${DATABASE}.${COLLECTION}.json`);
const ITEM_SHAPE_VERSION = 1;

// Directories that are NOT documentation pages. Everything else under ROOT that
// ends in .mdx/.md is treated as a page. `snippets` holds reusable fragments
// (not standalone pages); `archive` is retired content; the rest are tooling.
const EXCLUDE_DIRS = new Set([
  "node_modules", ".git", ".github", "_ci-proposed",
  "archive", "snippets", "footer", "footer-harness", "askai-harness",
  "scripts", "logo", "images", "assets", ".cursor",
]);

const SCHEMA_FIELDS = [
  { name: "src_kind", data_type: "VARCHAR", enable_match: true, max_length: 64 },
  { name: "section", data_type: "VARCHAR", enable_match: true, max_length: 128 },
  { name: "folder", data_type: "VARCHAR", enable_match: true, max_length: 256 },
  { name: "page", data_type: "VARCHAR", enable_match: true, max_length: 512 },
  { name: "repo", data_type: "VARCHAR", enable_match: true, max_length: 256 },
];

const args = new Set(process.argv.slice(2));
const FULL = args.has("--full");
const DRY_RUN = args.has("--dry-run");
let PRUNE = !args.has("--no-prune");
const sinceArg = process.argv.find((a) => a.startsWith("--since-ref="));
const SINCE_REF = sinceArg ? sinceArg.slice("--since-ref=".length) : null;
const BATCH_SIZE = Number(process.env.HYDRA_DB_SYNC_BATCH || 15);
const WAIT_TIMEOUT_MS = Number(process.env.HYDRA_DB_SYNC_TIMEOUT_MS || 10 * 60_000);

const isDoc = (name) => name.endsWith(".mdx") || name.endsWith(".md");
const stripExt = (p) => p.replace(/\.mdx?$/, "");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function die(msg) {
  console.error(`✗ ${msg}`);
  process.exit(1);
}
if (!process.env.HYDRA_DB_API_KEY) die("HYDRA_DB_API_KEY is required (set it as a secret / in .env.local)");

const client = new HydraDBClient({ token: process.env.HYDRA_DB_API_KEY });

// Parse a minimal YAML frontmatter block: returns { data, body }. Only the
// scalar keys we care about (title, description) are read — no YAML dep needed.
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

function titleFromFilename(filename) {
  return stripExt(filename)
    .replace(/^\d+-/, "")
    .split(/[-_]/)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}
function titleFrom(data, body, filename) {
  if (data.title) return data.title;
  const m = body.match(/^#\s+(.+)$/m);
  return m ? m[1].trim() : titleFromFilename(filename);
}

// collectPagePaths pulls every published page route out of docs.json. Pages are
// exactly the strings inside a "pages": [...] array; label strings elsewhere
// (tab/group/dropdown names) are ignored, so we never ingest non-doc files.
function collectPagePaths(node, out = []) {
  if (Array.isArray(node)) {
    for (const x of node) collectPagePaths(x, out);
  } else if (node && typeof node === "object") {
    for (const [k, v] of Object.entries(node)) {
      if (k === "pages" && Array.isArray(v)) {
        for (const p of v) {
          if (typeof p === "string") out.push(p);
          else collectPagePaths(p, out); // nested group object
        }
      } else {
        collectPagePaths(v, out);
      }
    }
  }
  return out;
}

function makeDoc(relRoute) {
  // relRoute is a Mintlify route like "essentials/v2/knowledge" (no extension).
  const rel = fs.existsSync(path.join(ROOT, relRoute + ".mdx"))
    ? relRoute + ".mdx"
    : fs.existsSync(path.join(ROOT, relRoute + ".md"))
    ? relRoute + ".md"
    : null;
  if (!rel) return null;
  const abs = path.join(ROOT, rel);
  const raw = fs.readFileSync(abs, "utf-8");
  const { data, body } = parseFrontmatter(raw);
  const slug = relRoute.split("/");
  return {
    slug,
    relPath: rel,
    title: titleFrom(data, body, slug[slug.length - 1]),
    description: data.description || "",
    content: body,
    mtime: fs.statSync(abs).mtime,
  };
}

function collectDocs() {
  // Preferred: drive off docs.json so we ingest exactly the published routes.
  const cfgPath = path.join(ROOT, "docs.json");
  if (fs.existsSync(cfgPath)) {
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

  // Fallback (no docs.json): walk content dirs, skipping tooling/meta dirs.
  const docs = [];
  const walk = (dir, slugPrefix) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (entry.name.startsWith(".")) continue;
      if (entry.isDirectory()) {
        if (EXCLUDE_DIRS.has(entry.name)) continue;
        walk(path.join(dir, entry.name), [...slugPrefix, entry.name]);
      } else if (isDoc(entry.name)) {
        const abs = path.join(dir, entry.name);
        const rel = path.relative(ROOT, abs).split(path.sep).join("/");
        const { data, body } = parseFrontmatter(fs.readFileSync(abs, "utf-8"));
        docs.push({
          slug: [...slugPrefix, stripExt(entry.name)],
          relPath: rel,
          title: titleFrom(data, body, entry.name),
          description: data.description || "",
          content: body,
          mtime: fs.statSync(abs).mtime,
        });
      }
    }
  };
  for (const entry of fs.readdirSync(ROOT, { withFileTypes: true })) {
    if (entry.isDirectory() && !entry.name.startsWith(".") && !EXCLUDE_DIRS.has(entry.name)) {
      walk(path.join(ROOT, entry.name), [entry.name]);
    }
  }
  return docs;
}

function sourceId(slug) {
  return `doc--${slug.join("--").toLowerCase()}`;
}

function loadManifest() {
  try { return JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf-8")); }
  catch { return { sources: {} }; }
}
function saveManifest(manifest) {
  const tmp = MANIFEST_PATH + ".tmp";
  fs.writeFileSync(tmp, JSON.stringify(manifest, null, 2));
  fs.renameSync(tmp, MANIFEST_PATH);
}

function authHeaders() {
  return {
    Authorization: `Bearer ${process.env.HYDRA_DB_API_KEY}`,
    "API-Version": "2",
    "Content-Type": "application/json",
  };
}

async function ensureDatabase() {
  try {
    await client.databases.create({ database: DATABASE });
    console.log(`✓ created database "${DATABASE}"`);
  } catch (err) {
    const msg = String(err?.message || err);
    if (!/already exists/i.test(msg)) {
      try { await client.databases.status({ database: DATABASE }); }
      catch { die(`cannot create or read database "${DATABASE}": ${msg}`); }
    }
  }
  for (;;) {
    const s = await client.databases.status({ database: DATABASE });
    if (s.data?.infra?.readyForIngestion ?? s.data?.infra?.ready_for_ingestion) break;
    console.log("… waiting for database infrastructure …");
    await sleep(5_000);
  }
  for (const field of SCHEMA_FIELDS) {
    try {
      const res = await fetch(
        `https://api.hydradb.com/databases/${encodeURIComponent(DATABASE)}/metadata-schema`,
        { method: "PATCH", headers: authHeaders(), body: JSON.stringify({ add_fields: [field] }) }
      );
      const body = await res.text();
      if (res.ok) console.log(`✓ schema field "${field.name}" ready`);
      else if (/exists|duplicate/i.test(body)) console.log(`• schema field "${field.name}" already present`);
      else console.warn(`! schema field "${field.name}" skipped: ${body.slice(0, 160)}`);
    } catch (err) {
      console.warn(`! schema field "${field.name}" skipped: ${err}`);
    }
  }
}

function gitChangedDocs(ref) {
  const changed = execSync(`git diff --name-only --diff-filter=ACMR ${ref}...HEAD`, { encoding: "utf-8" });
  const deleted = execSync(`git diff --name-only --diff-filter=D ${ref}...HEAD`, { encoding: "utf-8" });
  const pick = (s) => s.split("\n").map((l) => l.trim()).filter(isDoc);
  const notExcluded = (p) => !EXCLUDE_DIRS.has(p.split("/")[0]);
  return {
    changed: new Set(pick(changed).filter(notExcluded)),
    deleted: new Set(pick(deleted).filter(notExcluded)),
  };
}

function toItem(doc) {
  return {
    id: sourceId(doc.slug),
    database: DATABASE,
    collection: COLLECTION,
    title: doc.title,
    type: "knowledge_base",
    url: "/" + doc.slug.join("/"), // Mintlify serves pages at their path, sans extension
    timestamp: doc.mtime.toISOString(),
    content: { markdown: doc.description ? `# ${doc.title}\n\n${doc.description}\n\n${doc.content}` : doc.content },
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

async function ingestBatch(items) {
  const res = await client.context.ingest({
    type: "knowledge",
    database: DATABASE,
    collection: COLLECTION,
    upsert: "true",
    appKnowledge: JSON.stringify(items),
  });
  if (!res.data?.success) throw new Error(res.error?.message || "ingest failed");
  return res.data.results.map((r) => r.id);
}

async function waitIndexed(ids) {
  const deadline = Date.now() + WAIT_TIMEOUT_MS;
  const pending = new Set(ids);
  while (pending.size && Date.now() < deadline) {
    const res = await client.context.status({ database: DATABASE, collection: COLLECTION, ids: [...pending] });
    for (const st of res.data?.statuses || []) {
      const status = st.indexingStatus || st.indexing_status;
      if (status === "completed" || status === "graph_creation") pending.delete(st.id);
      else if (status === "errored" || status === "failed") {
        const msg = st.errorMessage || st.error_message || st.message || "indexing failed";
        throw new Error(`source ${st.id}: ${msg}`);
      }
    }
    if (pending.size) await sleep(3_000);
  }
  if (pending.size) throw new Error(`timed out waiting for ${pending.size} source(s): ${[...pending].join(", ")}`);
}

async function deleteSources(ids, label) {
  if (!ids.length) return;
  if (DRY_RUN) { console.log(`[dry-run] would DELETE /context ${ids.length} ${label}`); return; }
  for (let i = 0; i < ids.length; i += BATCH_SIZE) {
    await client.context.delete({
      type: "knowledge", database: DATABASE, collection: COLLECTION,
      ids: ids.slice(i, i + BATCH_SIZE),
    });
  }
  console.log(`✓ DELETE /context ${ids.length} ${label}`);
}

async function main() {
  console.log(`HydraDB sync → database=${DATABASE} collection=${COLLECTION}${FULL ? " (full)" : ""}${DRY_RUN ? " (dry-run)" : ""}${SINCE_REF ? ` (since ${SINCE_REF})` : ""}`);

  let docs = collectDocs();
  const extraDeleteIds = [];
  if (SINCE_REF) {
    const { changed, deleted } = gitChangedDocs(SINCE_REF);
    docs = docs.filter((d) => changed.has(d.relPath));
    for (const rel of deleted) extraDeleteIds.push(sourceId(stripExt(rel).split("/")));
    PRUNE = false;
    console.log(`delta: ${changed.size} changed, ${deleted.size} deleted`);
    if (!docs.length && !extraDeleteIds.length) { console.log("✓ nothing to ingest"); return; }
  }
  if (!docs.length && extraDeleteIds.length === 0) die("no doc pages found");

  const manifest = loadManifest();
  if (!DRY_RUN) await ensureDatabase();

  const currentIds = new Set();
  const toIngest = [];
  for (const doc of docs) {
    const item = toItem(doc);
    currentIds.add(item.id);
    const hash = crypto.createHash("sha256")
      .update(item.content.markdown).update("\n")
      .update(JSON.stringify(item.tenant_metadata))
      .update(String(ITEM_SHAPE_VERSION))
      .digest("hex");
    const prev = manifest.sources[item.id];
    if (FULL || !prev || prev.hash !== hash) toIngest.push({ doc, id: item.id, hash });
  }

  console.log(`${toIngest.length}/${docs.length} page(s) need ingestion`);
  if (DRY_RUN) {
    for (const { id, doc } of toIngest) console.log(`  • ${id}  (${doc.relPath})`);
  } else {
    let done = 0;
    for (let i = 0; i < toIngest.length; i += BATCH_SIZE) {
      const batch = toIngest.slice(i, i + BATCH_SIZE);
      const ids = await ingestBatch(batch.map(({ doc }) => toItem(doc)));
      await waitIndexed(ids);
      for (const { id, hash, doc } of batch) {
        manifest.sources[id] = { hash, synced_at: new Date().toISOString(), path: doc.relPath };
      }
      done += batch.length;
      console.log(`  ✓ ${done}/${toIngest.length} indexed`);
    }
  }

  if (PRUNE) {
    const stale = Object.keys(manifest.sources).filter((id) => !currentIds.has(id));
    await deleteSources(stale, "stale source(s)");
    for (const id of stale) delete manifest.sources[id];
  }
  if (extraDeleteIds.length) {
    await deleteSources(extraDeleteIds, "deleted page(s)");
    for (const id of extraDeleteIds) delete manifest.sources[id];
  }

  if (!DRY_RUN) saveManifest(manifest);
  const stats = await client.databases.stats({ database: DATABASE }).catch(() => null);
  console.log("✓ sync complete");
  if (stats?.data) console.log(JSON.stringify(stats.data).slice(0, 400));
}

main().catch((err) => {
  console.error("✗ sync failed:", err?.message || err);
  process.exit(1);
});
