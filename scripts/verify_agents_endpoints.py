#!/usr/bin/env python3
"""Verify API endpoints mentioned in AGENTS.mdx exist in the OpenAPI paths."""

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}


def clean_path(raw: str) -> str:
    path = raw.strip().rstrip(".,);]")
    if path.startswith("https://api.hydradb.com"):
        path = urlparse(path).path
    path = path.split("?", 1)[0]
    return path.rstrip("/") or "/"


def iter_doc_endpoints(text: str):
    seen = set()

    # Method-qualified mentions: `POST /query`, curl comments, tables, etc.
    method_re = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\s+((?:https://api\.hydradb\.com)?/[A-Za-z0-9_./{}*?=&:-]+)")
    for method, raw_path in method_re.findall(text):
        path = clean_path(raw_path)
        item = (method.lower(), path)
        if item not in seen:
            seen.add(item)
            yield item

    # Backticked path-only mentions: `/context/ingest`, `/context/*`, etc.
    tick_re = re.compile(r"`((?:https://api\.hydradb\.com)?/[A-Za-z0-9_./{}*?=&:-]+)`")
    for raw_path in tick_re.findall(text):
        path = clean_path(raw_path)
        item = (None, path)
        if item not in seen:
            seen.add(item)
            yield item

    # Full API URLs in examples, even when not backticked.
    url_re = re.compile(r"https://api\.hydradb\.com/[A-Za-z0-9_./{}*?=&:-]+")
    for raw_url in url_re.findall(text):
        path = clean_path(raw_url)
        item = (None, path)
        if item not in seen:
            seen.add(item)
            yield item


def path_exists(path: str, openapi_paths: set[str]) -> bool:
    if path.endswith("*"):
        return any(p.startswith(path[:-1]) for p in openapi_paths)
    return path in openapi_paths


# Legacy aliases that still appear in deprecation guidance.
DEPRECATED_PATH_PREFIXES = (
    "/tenants",
)


def is_allowed_missing_path(path: str) -> bool:
    return any(
        path == prefix or path.startswith(prefix + "/")
        for prefix in DEPRECATED_PATH_PREFIXES
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs", default="AGENTS.mdx", help="Path to the agents MDX file")
    parser.add_argument("--openapi", default="api-reference/v2/openapi.json", help="Path to OpenAPI JSON")
    args = parser.parse_args()

    docs_path = Path(args.docs)
    openapi_path = Path(args.openapi)

    text = docs_path.read_text(encoding="utf-8")
    spec = json.loads(openapi_path.read_text(encoding="utf-8"))
    paths = spec.get("paths", {})
    openapi_paths = set(paths)

    missing = []
    checked = []
    for method, path in sorted(iter_doc_endpoints(text), key=lambda x: (x[1], x[0] or "")):
        checked.append((method, path))
        if not path_exists(path, openapi_paths):
            if is_allowed_missing_path(path):
                continue
            missing.append((method, path, "path not found"))
            continue
        if method and not path.endswith("*"):
            allowed = {m.lower() for m in paths[path] if m.lower() in HTTP_METHODS}
            if method not in allowed:
                missing.append((method, path, f"method not found; allowed: {', '.join(sorted(allowed)) or 'none'}"))

    if missing:
        print(f"Missing endpoints in {openapi_path} referenced by {docs_path}:")
        for method, path, reason in missing:
            label = f"{method.upper()} {path}" if method else path
            print(f"- {label} ({reason})")
        print(f"\nChecked {len(checked)} endpoint mention(s).")
        return 1

    print(f"OK: checked {len(checked)} endpoint mention(s) from {docs_path} against {openapi_path}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
