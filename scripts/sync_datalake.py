#!/usr/bin/env python3
"""
Sync the public docs into the team's internal knowledge data lake.

Every page in the v2 navigation of docs.json (Guides, Cookbooks, API Reference)
becomes one `knowledge_base` app source in HydraDB, tagged with the shared
data-lake metadata from scripts/datalake_taxonomy.json so the team's MCP can
narrow a search to, say, public API reference pages about connectors:

  origin=mintlify_docs  audience=public  doc_kind  product_area  lifecycle=shipped
  api_version=v2  section  path  endpoint (API reference only)  tickets

API reference pages carry little prose in their MDX (Mintlify renders the
operation from openapi.json), so the operation's parameters, body fields and
responses are rendered from openapi.json into the body.

This is a separate database from the public website FAQ (scripts/sync_faq.py);
the two syncs never touch each other's content.

Runs:
  --since-ref=<sha>  upsert only pages whose MDX changed since <sha>. A change to
                     docs.json, openapi.json, the taxonomy or the sync scripts
                     re-ingests every page, because it can change any page's
                     metadata or body.
  --full             upsert every page.
  --dry-run          build the item set and print it; no API calls.

Every run then deletes the sources this sync owns (origin=mintlify_docs, id
prefix mdoc-) that are no longer in the navigation. If any page cannot be read
the run aborts before ingesting or deleting anything.

Config (env):
  HYDRADB_DATALAKE_API_KEY     secret, required unless --dry-run
  HYDRADB_DATALAKE_DATABASE    defaults to internal-knowledge-data-lake
  HYDRADB_DATALAKE_COLLECTION  defaults to common-collection
  HYDRADB_API_BASE             defaults to https://api.hydradb.com
  DOCS_BASE                    defaults to https://docs.hydradb.com
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from sync_faq import (
    ApiError,
    api_request,
    collect_pages,
    encode_multipart,
    info,
    mdx_frontmatter_title,
    mdx_to_text,
    step,
)

ORIGIN = "mintlify_docs"
ID_PREFIX = "mdoc-"
INGEST_BATCH = 20
STATUS_BATCH = 50
STATUS_POLL_SECONDS = 10
STATUS_TIMEOUT_SECONDS = 20 * 60
FULL_SYNC_TRIGGERS = {
    "docs.json",
    "api-reference/v2/openapi.json",
    "scripts/datalake_taxonomy.json",
    "scripts/sync_datalake.py",
    "scripts/sync_faq.py",
}


def nav_pages(docs_json: dict) -> list[tuple[str, str, str]]:
    """(tab, group, page) for every page in the v2 navigation, first occurrence wins."""
    v2 = next((v for v in docs_json.get("navigation", {}).get("versions", [])
               if v.get("version") == "v2"), None)
    if not v2:
        raise RuntimeError("docs.json has no v2 version section")
    seen: set[str] = set()
    out: list[tuple[str, str, str]] = []
    for tab in v2.get("tabs", []):
        for group in tab.get("groups", []):
            for page in collect_pages(group):
                if page not in seen:
                    seen.add(page)
                    out.append((tab["tab"], group.get("group", ""), page))
    return out


def frontmatter(text: str, key: str) -> str | None:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        return None
    km = re.search(rf"^{key}:\s*[\"']?(.*?)[\"']?\s*$", m.group(1), re.MULTILINE)
    return km.group(1).strip() if km else None


def deref(spec: dict, node):
    for _ in range(20):
        if not (isinstance(node, dict) and "$ref" in node):
            break
        target = spec
        for part in node["$ref"].lstrip("#/").split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        node = target
    return node


def schema_type(spec: dict, schema: dict) -> str:
    schema = deref(spec, schema)
    if "type" in schema:
        if schema["type"] == "array":
            return f"{schema_type(spec, schema.get('items', {}))}[]"
        return str(schema["type"])
    for key in ("oneOf", "anyOf"):
        if key in schema:
            return " or ".join(schema_type(spec, s) for s in schema[key])
    return "object"


def render_operation(spec: dict, method: str, api_path: str) -> str:
    op = spec.get("paths", {}).get(api_path, {}).get(method.lower())
    if not op:
        raise RuntimeError(f"openapi.json has no {method} {api_path}")
    lines = [f"{method.upper()} {api_path}"]
    for key in ("summary", "description"):
        if op.get(key):
            lines.append(op[key])
    params = [deref(spec, p) for p in op.get("parameters", [])]
    if params:
        lines.append("\nParameters:")
        for p in params:
            req = ", required" if p.get("required") else ""
            lines.append(f"- {p.get('name')} ({p.get('in')}, "
                         f"{schema_type(spec, p.get('schema', {}))}{req}): "
                         f"{(p.get('description') or '').strip()}")
    for media, body in deref(spec, op.get("requestBody", {})).get("content", {}).items():
        schema = deref(spec, body.get("schema", {}))
        props = schema.get("properties", {})
        if not props:
            continue
        required = set(schema.get("required", []))
        lines.append(f"\nRequest body ({media}):")
        for name, prop in props.items():
            desc = (deref(spec, prop).get("description") or prop.get("description") or "").strip()
            req = ", required" if name in required else ""
            lines.append(f"- {name} ({schema_type(spec, prop)}{req}): {desc}")
    responses = op.get("responses", {})
    if responses:
        lines.append("\nResponses:")
        for code, resp in responses.items():
            lines.append(f"- {code}: {(deref(spec, resp).get('description') or '').strip()}")
    return "\n".join(lines)


def first_match(rules: list, key: str) -> list[str] | None:
    for prefix, areas in rules:
        if key.startswith(prefix):
            return areas
    return None


def git(repo_root: Path, *args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=repo_root, capture_output=True,
                              text=True, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def page_tickets(repo_root: Path, rel_path: str) -> str:
    """Linear tickets (PRO-123) named by the commits that touched a page, newest first."""
    seen: list[str] = []
    for t in re.findall(r"\b(PRO-\d+)\b", git(repo_root, "log", "--format=%B", "--", rel_path)):
        if t not in seen:
            seen.append(t)
    out = ""
    for t in seen:
        nxt = f"{out},{t}" if out else t
        if len(nxt) > 4000:
            break
        out = nxt
    return out


def build_items(repo_root: Path, taxonomy: dict, docs_base: str,
                database: str, collection: str) -> list[dict]:
    docs_json = json.loads((repo_root / "docs.json").read_text(encoding="utf-8"))
    spec = json.loads((repo_root / "api-reference/v2/openapi.json").read_text(encoding="utf-8"))
    vocab = taxonomy["vocabulary"]
    commit = git(repo_root, "rev-parse", "HEAD").strip()
    synced_at = datetime.now(timezone.utc).isoformat()
    items: list[dict] = []
    for tab, group, page in nav_pages(docs_json):
        mdx_path = next((c for c in (repo_root / f"{page}.mdx", repo_root / f"{page}.md",
                                     repo_root / page / "index.mdx") if c.exists()), None)
        if mdx_path is None:
            raise RuntimeError(f"docs page {page}: no MDX/MD file found")
        rel_path = mdx_path.relative_to(repo_root).as_posix()
        raw = mdx_path.read_text(encoding="utf-8", errors="replace")
        title = mdx_frontmatter_title(mdx_path) or page
        body = mdx_to_text(raw)

        endpoint = None
        areas = first_match(taxonomy["pages"], page)
        openapi = frontmatter(raw, "openapi")
        if openapi:
            m = re.match(r"^\S+\s+(GET|POST|PUT|PATCH|DELETE)\s+(\S+)$", openapi, re.IGNORECASE)
            if not m:
                raise RuntimeError(f"docs page {page}: unreadable openapi frontmatter {openapi!r}")
            endpoint = f"{m.group(1).upper()} {m.group(2)}"
            body = f"{render_operation(spec, m.group(1), m.group(2))}\n\n{body}".strip()
            areas = first_match(taxonomy["endpoints"], m.group(2)) or areas
        description = frontmatter(raw, "description")
        if description:
            body = f"{description}\n\n{body}"
        if not body.strip():
            raise RuntimeError(f"docs page {page}: extracted empty body from {rel_path}")

        doc_kind = "plugin" if group == "Plugins" else taxonomy["tabs"].get(tab)
        areas = areas or ["general"]
        for field, values in (("doc_kind", [doc_kind]), ("product_area", areas)):
            bad = [v for v in values if v not in vocab[field]]
            if bad:
                raise RuntimeError(f"docs page {page}: {field} {bad} not in the taxonomy vocabulary")

        url = f"{docs_base.rstrip('/')}/{page}"
        metadata = {
            "origin": ORIGIN,
            "audience": "public",
            "doc_kind": doc_kind,
            "product_area": ",".join(areas),
            "lifecycle": "shipped",
            "api_version": "v2",
            "section": f"{tab} / {group}" if group else tab,
            "path": rel_path,
        }
        if endpoint:
            metadata["endpoint"] = endpoint
        tickets = page_tickets(repo_root, rel_path)
        if tickets:
            metadata["tickets"] = tickets
        items.append({
            "id": ID_PREFIX + page.lower().replace("/", "--"),
            "database": database,
            "collection": collection,
            "title": title,
            "kind": "knowledge_base",
            "provider": ORIGIN,
            "external_id": page,
            "url": url,
            "fields": {"kind": "knowledge_base", "title": title, "body": body, "url": url},
            "metadata": metadata,
            "additional_metadata": {
                "repo_path": rel_path,
                "commit_sha": commit,
                "synced_at": synced_at,
            },
        })
    return items


class Api:
    def __init__(self, key: str, base: str, database: str, collection: str) -> None:
        self.key, self.base, self.database, self.collection = key, base, database, collection

    def call(self, method: str, path: str, payload: dict | None = None,
             body: bytes | None = None, content_type: str = "application/json") -> dict:
        if payload is not None:
            body = json.dumps(payload).encode()
        status, text = api_request(method, path, self.key, self.base, body=body,
                                   content_type=content_type)
        if status not in (200, 202):
            raise ApiError(status, text)
        return json.loads(text) if text else {}

    def ensure_schema(self, fields: list[dict]) -> None:
        path = f"/databases/{urllib.parse.quote(self.database)}/metadata-schema"
        added = self.call("PATCH", path, {"add_fields": fields}).get("added_fields") or []
        info(f"metadata schema ready ({len(added)} field(s) added)")

    def ingest(self, items: list[dict]) -> None:
        body, content_type = encode_multipart({
            "database": self.database,
            "collection": self.collection,
            "type": "knowledge",
            "upsert": "true",
            "app_knowledge": json.dumps(items, separators=(",", ":")),
        })
        results = (self.call("POST", "/context/ingest", body=body,
                             content_type=content_type).get("data") or {}).get("results") or []
        rejected = [f"{r.get('id')}: {r.get('error')}" for r in results if r.get("error")]
        if rejected:
            raise RuntimeError(f"ingest rejected {len(rejected)} items: {rejected}")

    def wait_indexed(self, ids: list[str]) -> None:
        pending = list(ids)
        deadline = time.monotonic() + STATUS_TIMEOUT_SECONDS
        while pending:
            still, errored = [], []
            for i in range(0, len(pending), STATUS_BATCH):
                batch = pending[i:i + STATUS_BATCH]
                query = urllib.parse.urlencode({"database": self.database,
                                                "collection": self.collection,
                                                "ids": ",".join(batch)})
                data = self.call("GET", f"/context/status?{query}").get("data") or {}
                statuses = {s.get("id"): s for s in data.get("statuses") or []}
                for sid in batch:
                    s = statuses.get(sid) or {}
                    state = s.get("indexing_status")
                    if state in ("errored", "failed"):
                        errored.append(f"{sid}: {s.get('error_code') or s.get('error_message')}")
                    elif state not in ("completed", "graph_creation"):
                        still.append(sid)
            if errored:
                raise RuntimeError(f"{len(errored)} items failed to index: {errored}")
            pending = still
            if pending:
                if time.monotonic() > deadline:
                    raise RuntimeError(f"{len(pending)} items not indexed after "
                                       f"{STATUS_TIMEOUT_SECONDS // 60} min: {pending}")
                time.sleep(STATUS_POLL_SECONDS)

    def owned_ids(self) -> set[str]:
        """Ids this sync owns, scoped by origin and id prefix so a prune never
        touches internal docs or connector content in the same collection."""
        owned: set[str] = set()
        for page in range(1, 1001):
            data = self.call("POST", "/context/list", {
                "database": self.database,
                "collection": self.collection,
                "type": "knowledge",
                "filters": {"metadata": {"origin": ORIGIN}},
                "page": page,
                "page_size": 100,
            }).get("data") or {}
            sources = data.get("sources") or []
            owned.update(s["id"] for s in sources if str(s.get("id", "")).startswith(ID_PREFIX))
            pagination = data.get("pagination") or {}
            has_next = pagination.get("has_next")
            if has_next is None:
                has_next = page < (pagination.get("total_pages") or 1)
            if not sources or not has_next:
                break
        return owned

    def delete(self, ids: list[str]) -> None:
        for i in range(0, len(ids), 50):
            status, text = api_request("DELETE", "/context", self.key, self.base, body=json.dumps({
                "database": self.database,
                "collection": self.collection,
                "type": "knowledge",
                "ids": ids[i:i + 50],
            }).encode())
            if status not in (200, 404):
                raise ApiError(status, text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--since-ref")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parent.parent))
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    api_key = os.environ.get("HYDRADB_DATALAKE_API_KEY", "").strip()
    database = os.environ.get("HYDRADB_DATALAKE_DATABASE", "").strip() or "internal-knowledge-data-lake"
    collection = os.environ.get("HYDRADB_DATALAKE_COLLECTION", "").strip() or "common-collection"
    api_base = os.environ.get("HYDRADB_API_BASE", "").strip() or "https://api.hydradb.com"
    docs_base = os.environ.get("DOCS_BASE", "").strip() or "https://docs.hydradb.com"
    if not args.dry_run and not api_key:
        raise RuntimeError("HYDRADB_DATALAKE_API_KEY is not set")
    taxonomy = json.loads((repo_root / "scripts/datalake_taxonomy.json").read_text(encoding="utf-8"))

    print(f"HydraDB data lake sync\n  database:   {database}\n  collection: {collection}")
    step("Building item set")
    items = build_items(repo_root, taxonomy, docs_base, database, collection)
    expected = {it["id"] for it in items}
    info(f"{len(items)} pages in the v2 navigation")

    to_ingest = items
    if args.since_ref and not args.full:
        changed = set(git(repo_root, "diff", "--name-only", f"{args.since_ref}...HEAD").split())
        if changed & FULL_SYNC_TRIGGERS:
            info("navigation, OpenAPI spec, taxonomy or sync changed: re-ingesting every page")
        else:
            to_ingest = [it for it in items if it["additional_metadata"]["repo_path"] in changed]
    info(f"{len(to_ingest)} page(s) to ingest")

    if args.dry_run:
        for it in to_ingest:
            m = it["metadata"]
            print(f"  {it['id']:<58} {m['doc_kind']:<14} {m['product_area']:<26} "
                  f"{m.get('endpoint', '')}")
        return 0

    api = Api(api_key, api_base, database, collection)
    step("Ensuring metadata schema")
    api.ensure_schema(taxonomy["schema"])

    step(f"Ingesting {len(to_ingest)} pages")
    for i in range(0, len(to_ingest), INGEST_BATCH):
        batch = to_ingest[i:i + INGEST_BATCH]
        api.ingest(batch)
        api.wait_indexed([it["id"] for it in batch])
        info(f"{min(i + INGEST_BATCH, len(to_ingest))}/{len(to_ingest)} indexed")

    step("Removing pages no longer in the navigation")
    stale = sorted(api.owned_ids() - expected)
    if stale:
        info(f"deleting {len(stale)}: {stale}")
        api.delete(stale)
    else:
        info("nothing to delete")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"### Data lake sync\n\n- database: {database} / {collection}\n"
                     f"- ingested: {len(to_ingest)} of {len(items)} pages\n"
                     f"- deleted: {len(stale)}\n")
    step("Done")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ApiError as exc:
        print(f"::error title=Data lake sync API error::{exc}", file=sys.stderr)
        raise SystemExit(1)
    except RuntimeError as exc:
        print(f"::error title=Data lake sync aborted::{exc}", file=sys.stderr)
        raise SystemExit(1)
