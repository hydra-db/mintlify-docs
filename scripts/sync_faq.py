#!/usr/bin/env python3
"""
Sync the website FAQ knowledge base in HydraDB.

Builds one `knowledge_base` app source per page from three sources:

  1. Docs    -- the v2 "Guides" tab pages listed in docs.json
                (url = https://docs.hydradb.com/<page path>)
  2. Website -- hydradb.com/, /ai, /contact, /alternatives, every
                /alternatives/* page, and /privacy-policy. HTML is fetched and
                the readable text is extracted. /blog/* and /terms-of-service
                are never ingested.
  3. faq/qa.yaml -- a hand-written Q&A list (question, answer, optional url).

Each item is ingested with `POST /context/ingest` (multipart, type=knowledge,
upsert=true) so it is a `knowledge_base` app source carrying its own `url`.
The `url` must be present on the item itself: without it HydraDB replaces the
url with the internal s3 pointer and the FAQ box shows no source links.

After ingest, any source already in the database that is no longer in the set
is deleted (`POST /context/list` then `DELETE /context`) so removed pages stop
being answered.

Config (env):
  HYDRADB_FAQ_API_KEY   secret, required to actually ingest. When missing the
                        run exits 0 with a notice (see --skip-if-no-key).
  HYDRADB_FAQ_DATABASE  repo variable, defaults to `website-faq`.
  HYDRADB_API_BASE      defaults to https://api.hydradb.com
  DOCS_BASE             defaults to https://docs.hydradb.com
  SITE_BASE             defaults to https://hydradb.com
  FAQ_QA_PATH           defaults to faq/qa.yaml

Dry run: pass --dry-run to build the item set and print a summary without
calling the API. Useful for local validation and CI without a key.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - pyyaml is installed in the workflow
    yaml = None


API_BASE_DEFAULT = "https://api.hydradb.com"
DOCS_BASE_DEFAULT = "https://docs.hydradb.com"
SITE_BASE_DEFAULT = "https://hydradb.com"
DATABASE_DEFAULT = "website-faq"
COLLECTION_DEFAULT = "default"
HTTP_TIMEOUT = 30
MAX_FETCH_RETRIES = 2
USER_AGENT = "hydradb-faq-sync/1.0 (+https://github.com/hydra-db/mintlify-docs)"

# Pages on hydradb.com to always ingest, independent of sitemap discovery.
SITE_BASE_PATHS = ["/", "/ai", "/contact", "/alternatives", "/privacy-policy"]

# Pages on hydradb.com to never ingest, even if discovered.
SITE_EXCLUDE_PATHS = {"/terms-of-service"}

# Tags whose contents are not readable text and should be dropped wholesale.
SKIP_TAGS = {
    "script", "style", "noscript", "svg", "math", "template",
    "nav", "header", "footer", "aside",
    "button", "form", "input", "select", "textarea", "label",
    "iframe", "canvas", "video", "audio", "picture", "source",
}


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def info(msg: str) -> None:
    print(f"  -> {msg}", flush=True)

def warn(msg: str) -> None:
    print(f"  !  {msg}", flush=True)

def step(msg: str) -> None:
    print(f"\n== {msg} ==", flush=True)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class ApiError(Exception):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body[:500]}")
        self.status = status
        self.body = body


def http_get(url: str) -> str:
    """GET a URL with a small retry loop and return the body as text."""
    last_err: Exception | None = None
    for attempt in range(MAX_FETCH_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 - retry on any network error
            last_err = exc
            if attempt < MAX_FETCH_RETRIES:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"fetch failed for {url}: {last_err}")


def api_request(method: str, path: str, api_key: str, api_base: str,
                 body: bytes | None = None, content_type: str = "application/json",
                 extra_headers: dict[str, str] | None = None) -> tuple[int, str]:
    url = f"{api_base}{path}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "API-Version": "2",
        "User-Agent": USER_AGENT,
        "Content-Type": content_type,
    }
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)


# ---------------------------------------------------------------------------
# Multipart form encoding (stdlib only)
# ---------------------------------------------------------------------------

def encode_multipart(fields: dict[str, str]) -> tuple[bytes, str]:
    boundary = "----hydradbfaq" + uuid.uuid4().hex
    buf = io.BytesIO()
    for name, value in fields.items():
        buf.write(f"--{boundary}\r\n".encode())
        buf.write(
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        )
        buf.write(f"{value}\r\n".encode())
    buf.write(f"--{boundary}--\r\n".encode())
    return buf.getvalue(), f"multipart/form-data; boundary={boundary}"


# ---------------------------------------------------------------------------
# HTML readable-text extraction
# ---------------------------------------------------------------------------

class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self._skip_depth += 1

    def handle_startendtag(self, tag, attrs):
        # self-closing tags contribute no text; nothing to do.
        pass

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            text = data.strip()
            if text:
                self._parts.append(text)

    def text(self) -> str:
        return re.sub(r"\s+\n", "\n", "\n".join(self._parts)).strip()


def _slice_main(html: str) -> str:
    """Prefer <main> or <article> content so nav/footer noise is dropped."""
    for pattern in (
        r"<main\b[^>]*>(.*?)</main>",
        r"<article\b[^>]*>(.*?)</article>",
        r"<div[^>]*id=[\"']?content[\"']?[^>]*>(.*?)</div>",
    ):
        m = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
        if m:
            return m.group(1)
    return html


def html_to_text(html: str) -> str:
    html = _slice_main(html)
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:
        # Fall back to a regex strip if the parser chokes on malformed HTML.
        text = re.sub(r"<[^>]+>", " ", html)
        return re.sub(r"\s+", " ", text).strip()
    return parser.text()


def html_title(html: str) -> str | None:
    m = re.search(r"<title\b[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    title = re.sub(r"\s+", " ", m.group(1)).strip()
    # Drop common site suffixes/prefixes.
    for sep in (" — ", " | ", " - "):
        if sep in title:
            title = title.split(sep)[0].strip()
    return title or None


# ---------------------------------------------------------------------------
# docs.json -> v2 Guides tab pages
# ---------------------------------------------------------------------------

def collect_pages(nodes) -> list[str]:
    """Recursively collect page strings from a groups/pages tree."""
    pages: list[str] = []
    if isinstance(nodes, list):
        for node in nodes:
            pages.extend(collect_pages(node))
    elif isinstance(nodes, dict):
        if "pages" in nodes:
            pages.extend(collect_pages(nodes["pages"]))
        # A group entry may itself be {"group": "...", "pages": [...]}
        for key in ("groups",):
            if key in nodes:
                pages.extend(collect_pages(nodes[key]))
    elif isinstance(nodes, str):
        pages.append(nodes)
    return pages


def docs_guides_pages(docs_json_path: Path) -> list[str]:
    data = json.loads(docs_json_path.read_text(encoding="utf-8"))
    versions = data.get("navigation", {}).get("versions", [])
    v2 = next((v for v in versions if v.get("version") == "v2"), None)
    if not v2:
        raise RuntimeError("docs.json has no v2 version section")
    guides_tab = next((t for t in v2.get("tabs", []) if t.get("tab") == "Guides"), None)
    if not guides_tab:
        raise RuntimeError("docs.json v2 has no 'Guides' tab")
    pages = collect_pages(guides_tab.get("groups", []))
    # De-duplicate while preserving order (AGENTS appears once; some pages
    # could be referenced from multiple groups).
    seen: set[str] = set()
    ordered: list[str] = []
    for p in pages:
        if p not in seen:
            seen.add(p)
            ordered.append(p)
    return ordered


def mdx_frontmatter_title(mdx_path: Path) -> str | None:
    if not mdx_path.exists():
        return None
    text = mdx_path.read_text(encoding="utf-8", errors="replace")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        return None
    fm = m.group(1)
    tm = re.search(r"^title:\s*[\"']?(.*?)[\"']?\s*$", fm, re.MULTILINE)
    return tm.group(1).strip() if tm else None


# ---------------------------------------------------------------------------
# Website sitemap -> /alternatives/* discovery
# ---------------------------------------------------------------------------

def sitemap_urls(site_base: str) -> list[str]:
    """Return all URLs from sitemap.xml at site_base/sitemap.xml, or []."""
    candidates = [
        f"{site_base.rstrip('/')}/sitemap.xml",
        f"{site_base.rstrip('/')}/sitemap-index.xml",
    ]
    for url in candidates:
        try:
            xml = http_get(url)
        except Exception:
            continue
        urls: list[str] = []
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return []
        # Sitemaps use XML namespaces; match <loc> ignoring the prefix.
        for el in root.iter():
            tag = el.tag.split("}")[-1]
            if tag == "loc" and el.text:
                urls.append(el.text.strip())
        # Handle sitemap index files: follow nested <sitemap><loc>.
        if urls and all(u.endswith(".xml") for u in urls):
            nested: list[str] = []
            for u in urls:
                try:
                    sub = http_get(u)
                    sroot = ET.fromstring(sub)
                    for el in sroot.iter():
                        if el.tag.split("}")[-1] == "loc" and el.text:
                            nested.append(el.text.strip())
                except Exception:
                    continue
            return nested
        return urls
    return []


def discover_alternative_pages(site_base: str) -> list[str]:
    """Return all /alternatives/* paths (with a sub-path, excluding the index)."""
    urls = sitemap_urls(site_base)
    base_host = urllib.parse.urlparse(site_base).netloc
    paths: list[str] = []
    seen: set[str] = set()
    for u in urls:
        parsed = urllib.parse.urlparse(u)
        if parsed.netloc and parsed.netloc != base_host:
            continue
        path = parsed.path or "/"
        # /alternatives/* means a sub-page of /alternatives, not the index.
        if path.startswith("/alternatives/") and path != "/alternatives/":
            normalized = path.rstrip("/") or "/alternatives"
            if normalized not in seen:
                seen.add(normalized)
                paths.append(normalized)
    # Fall back to scraping links from /alternatives if the sitemap had none.
    if not paths:
        try:
            html = http_get(f"{site_base.rstrip('/')}/alternatives")
            for m in re.finditer(r'href=["\']([^"\']+)["\']', html):
                href = m.group(1)
                if href.startswith("/alternatives/") and href != "/alternatives/":
                    p = href.split("#", 1)[0].split("?", 1)[0].rstrip("/")
                    if p and p not in seen:
                        seen.add(p)
                        paths.append(p)
        except Exception:
            pass
    return sorted(paths)


def site_pages(site_base: str) -> list[str]:
    """Return the ordered list of site paths to ingest."""
    paths: list[str] = []
    seen: set[str] = set()
    for p in SITE_BASE_PATHS:
        if p in seen:
            continue
        seen.add(p)
        paths.append(p)
    for p in discover_alternative_pages(site_base):
        if p in seen:
            continue
        seen.add(p)
        paths.append(p)
    return paths


def site_slug(path: str) -> str:
    if path == "/" or path == "":
        return "home"
    slug = path.strip("/").replace("/", "-")
    return slug or "home"


# ---------------------------------------------------------------------------
# Item construction
# ---------------------------------------------------------------------------

def make_item(item_id: str, title: str, provider: str, external_id: str,
              url: str, body: str, database: str) -> dict:
    body = (body or "").strip()
    if not body:
        body = title
    return {
        "id": item_id,
        "database": database,
        "collection": COLLECTION_DEFAULT,
        "title": title,
        "kind": "knowledge_base",
        "provider": provider,
        "external_id": external_id,
        "url": url,
        "fields": {
            "kind": "knowledge_base",
            "title": title,
            "body": body,
            "url": url,
        },
    }


def build_docs_items(docs_pages: list[str], repo_root: Path, docs_base: str,
                     database: str) -> list[dict]:
    items: list[dict] = []
    for page in docs_pages:
        url = f"{docs_base.rstrip('/')}/{page.lstrip('/')}"
        # Prefer the local frontmatter title (stable, no network).
        mdx_candidates = [
            repo_root / f"{page}.mdx",
            repo_root / f"{page}.md",
            repo_root / page / "index.mdx",
        ]
        title = None
        for cand in mdx_candidates:
            title = mdx_frontmatter_title(cand)
            if title:
                break
        body = ""
        try:
            html = http_get(url)
            body = html_to_text(html)
            if not title:
                title = html_title(html)
        except Exception as exc:
            warn(f"docs page {page}: fetch failed ({exc}); ingesting title only")
        if not title:
            title = page
        item_id = "docs-" + page.replace("/", "-")
        items.append(make_item(item_id, title, "docs", page, url, body, database))
        info(f"docs  {page}  ({len(body)} chars)")
    return items


def build_site_items(site_base: str, database: str) -> list[dict]:
    items: list[dict] = []
    for path in site_pages(site_base):
        if path in SITE_EXCLUDE_PATHS:
            continue
        if path.startswith("/blog/"):
            continue
        url = f"{site_base.rstrip('/')}{path}"
        title = site_slug(path)
        body = ""
        try:
            html = http_get(url)
            body = html_to_text(html)
            t = html_title(html)
            if t:
                title = t
        except Exception as exc:
            warn(f"site {path}: fetch failed ({exc}); ingesting title only")
        item_id = "site-" + site_slug(path)
        items.append(
            make_item(item_id, title, "website", path, url, body, database)
        )
        info(f"site  {path}  ({len(body)} chars)")
    return items


def build_faq_items(qa_path: Path, database: str) -> list[dict]:
    if not qa_path.exists():
        warn(f"{qa_path} not found; no Q&A items ingested")
        return []
    if yaml is None:
        warn("PyYAML not installed; skipping faq/qa.yaml")
        return []
    data = yaml.safe_load(qa_path.read_text(encoding="utf-8")) or {}
    entries = data.get("items") or []
    items: list[dict] = []
    for i, entry in enumerate(entries, start=1):
        question = (entry.get("question") or "").strip()
        answer = (entry.get("answer") or "").strip()
        if not question or not answer:
            warn(f"qa.yaml entry {i}: missing question or answer; skipping")
            continue
        url = (entry.get("url") or "").strip()
        body = f"Q: {question}\n\nA: {answer}"
        item_id = f"qa-{i}"
        items.append(
            make_item(item_id, question, "faq", item_id, url, body, database)
        )
        info(f"faq   {item_id}  ({question[:60]!r})")
    return items


# ---------------------------------------------------------------------------
# HydraDB API: ingest, list, delete
# ---------------------------------------------------------------------------

def ingest(items: list[dict], api_key: str, api_base: str, database: str) -> None:
    if not items:
        info("no items to ingest")
        return
    app_knowledge = json.dumps(items, separators=(",", ":"))
    fields = {
        "database": database,
        "collection": COLLECTION_DEFAULT,
        "type": "knowledge",
        "upsert": "true",
        "app_knowledge": app_knowledge,
    }
    body, content_type = encode_multipart(fields)
    status, text = api_request(
        "POST", "/context/ingest", api_key, api_base,
        body=body, content_type=content_type,
    )
    if status not in (200, 202):
        raise ApiError(status, text)
    try:
        envelope = json.loads(text)
    except json.JSONDecodeError:
        envelope = {}
    results = (envelope.get("data") or {}).get("results") or []
    info(f"ingested {len(items)} items (api accepted {len(results)} ids)")
    # Surface per-item accept errors if any.
    for r in results:
        if r.get("error"):
            warn(f"  item {r.get('id')}: {r.get('error')}")


def list_existing_ids(api_key: str, api_base: str, database: str) -> set[str]:
    existing: set[str] = set()
    page = 1
    while True:
        payload = json.dumps({
            "database": database,
            "collection": COLLECTION_DEFAULT,
            "type": "knowledge",
            "page": page,
            "page_size": 100,
        })
        status, text = api_request(
            "POST", "/context/list", api_key, api_base, body=payload.encode(),
        )
        if status != 200:
            raise ApiError(status, text)
        envelope = json.loads(text)
        data = envelope.get("data") or {}
        sources = data.get("sources") or []
        for s in sources:
            sid = s.get("id")
            if sid:
                existing.add(sid)
        pagination = data.get("pagination") or {}
        total_pages = pagination.get("total_pages") or 1
        has_next = pagination.get("has_next") or page < total_pages
        if not has_next or not sources:
            break
        page += 1
        if page > 1000:  # hard safety cap
            warn("list pagination exceeded 1000 pages; stopping")
            break
    return existing


def delete_ids(ids: list[str], api_key: str, api_base: str, database: str) -> None:
    if not ids:
        info("no stale sources to delete")
        return
    payload = json.dumps({
        "database": database,
        "collection": COLLECTION_DEFAULT,
        "type": "knowledge",
        "ids": ids,
    })
    status, text = api_request(
        "DELETE", "/context", api_key, api_base, body=payload.encode(),
        extra_headers={"X-HydraDB-Delete-Status": "strict"},
    )
    if status not in (200, 404):
        raise ApiError(status, text)
    try:
        envelope = json.loads(text)
    except json.JSONDecodeError:
        envelope = {}
    deleted = (envelope.get("data") or {}).get("deleted_count")
    info(f"deleted {deleted if deleted is not None else '?'} stale sources "
         f"(requested {len(ids)})")
    # 404 means none matched; that's fine for our purposes (already gone).
    if status == 404:
        info("delete returned 404 (nothing matched; already gone)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Build the item set and print a summary; do not "
                             "call the HydraDB API.")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parent.parent),
                        help="Repo root containing docs.json and faq/.")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    api_key = os.environ.get("HYDRADB_FAQ_API_KEY", "").strip()
    database = os.environ.get("HYDRADB_FAQ_DATABASE", "").strip() or DATABASE_DEFAULT
    api_base = os.environ.get("HYDRADB_API_BASE", "").strip() or API_BASE_DEFAULT
    docs_base = os.environ.get("DOCS_BASE", "").strip() or DOCS_BASE_DEFAULT
    site_base = os.environ.get("SITE_BASE", "").strip() or SITE_BASE_DEFAULT
    qa_path = Path(os.environ.get("FAQ_QA_PATH", "faq/qa.yaml"))
    if not qa_path.is_absolute():
        qa_path = repo_root / qa_path

    print(f"HydraDB FAQ sync\n  database: {database}\n  api base: {api_base}\n"
          f"  docs base: {docs_base}\n  site base: {site_base}\n"
          f"  qa file:   {qa_path}")

    if args.dry_run:
        info("DRY RUN: no API calls will be made")
    elif not api_key:
        notice = (
            "HYDRADB_FAQ_API_KEY is not set; skipping FAQ sync. "
            "Add the repository secret and re-run to ingest."
        )
        print(f"\n== SKIP ==\n{notice}")
        # Print a GitHub Actions notice so it shows up in the run summary.
        print(f"::notice title=FAQ sync skipped::{notice}")
        return 0

    # 1. Build the item set.
    step("Building item set")
    docs_pages = docs_guides_pages(repo_root / "docs.json")
    info(f"docs.json v2 Guides tab: {len(docs_pages)} pages")
    items: list[dict] = []
    items.extend(build_docs_items(docs_pages, repo_root, docs_base, database))
    items.extend(build_site_items(site_base, database))
    items.extend(build_faq_items(qa_path, database))

    new_ids = {it["id"] for it in items}
    info(f"total items: {len(items)} ({len(new_ids)} unique ids)")

    if args.dry_run:
        step("Dry-run summary")
        for it in items:
            print(f"  {it['id']:<40}  {it['provider']:<8}  {it['title'][:70]}")
        print(f"\nDry run complete. {len(items)} items would be ingested into "
              f"database '{database}'.")
        return 0

    # 2. Ingest (upsert) the full set.
    step(f"Ingesting {len(items)} items into '{database}'")
    ingest(items, api_key, api_base, database)

    # 3. Delete sources no longer in the set.
    step("Reconciling stale sources")
    existing = list_existing_ids(api_key, api_base, database)
    info(f"database currently has {len(existing)} knowledge sources")
    stale = sorted(existing - new_ids)
    if stale:
        info(f"{len(stale)} stale sources to delete: {stale}")
    delete_ids(stale, api_key, api_base, database)

    step("Done")
    print(f"  ingested: {len(items)}\n  deleted:  {len(stale)}\n"
          f"  total in database: {len(existing) - len(stale)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ApiError as exc:
        print(f"\n== API ERROR ==\n{exc}", file=sys.stderr)
        print(f"::error title=FAQ sync API error::{exc}", file=sys.stderr)
        raise SystemExit(1)
