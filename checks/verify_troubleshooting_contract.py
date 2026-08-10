#!/usr/bin/env python3
"""Contract check for essentials/v2/troubleshooting-retrieval.mdx.

The troubleshooting guide only helps if its causes and fixes stay true to the
API. This script fails (non-zero exit) if the guide references an endpoint,
field, or default that no longer exists in the OpenAPI spec or the canonical
pages. Standard library only - no install, no network, no API key.

Run from the repo root:

    python checks/verify_troubleshooting_contract.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OPENAPI = REPO / "api-reference" / "v2" / "openapi.json"
GUIDE = REPO / "essentials" / "v2" / "troubleshooting-retrieval.mdx"
MEMORIES = REPO / "essentials" / "v2" / "memories.mdx"
MULTITENANT = REPO / "essentials" / "v2" / "multi-tenant.mdx"

failures: list[str] = []
checks = 0


def check(condition: bool, label: str) -> None:
    global checks
    checks += 1
    if not condition:
        failures.append(label)


def read(path: Path) -> str:
    if not path.exists():
        failures.append(f"missing file: {path.relative_to(REPO)}")
        return ""
    return path.read_text(encoding="utf-8")


def main() -> int:
    spec_text = read(OPENAPI)
    guide = read(GUIDE)
    memories = read(MEMORIES)
    multitenant = read(MULTITENANT)

    # OpenAPI must be valid JSON with the endpoints the guide sends readers to.
    paths = {}
    if spec_text:
        try:
            paths = json.loads(spec_text).get("paths", {})
        except json.JSONDecodeError as exc:
            failures.append(f"openapi.json is not valid JSON: {exc}")

    for endpoint in ("/query", "/context/ingest", "/context/status"):
        check(endpoint in spec_text, f"OpenAPI spec references {endpoint}")

    # Every request field / knob the guide's fixes depend on must exist in the spec.
    for field in (
        "collections",
        "infer",
        "query_apps",
        "graph_context",
        "metadata_filters",
        "additional_metadata",
        "signing_secret",
    ):
        check(field in spec_text, f"OpenAPI spec defines '{field}'")

    # The response envelope the guide teaches readers to unwrap.
    for key in ("request_id", "deprecation"):
        check(key in spec_text, f"OpenAPI spec exposes envelope '{key}'")

    # Behavioral defaults the guide asserts must match the canonical pages.
    check(
        "infer" in memories and "false" in memories,
        "memories.mdx documents the infer default (false)",
    )
    check(
        "collection" in multitenant and "should not be expected" in multitenant,
        "multi-tenant.mdx states the same-collection read/write rule the guide quotes",
    )

    # The guide must not have drifted away from its own core symptoms.
    for anchor in (
        "type-all-scoping-trap",
        "#empty-results",
        "#data-isolation",
        "#ignored-settings",
    ):
        check(anchor in guide, f"guide keeps the '{anchor}' section")

    print(f"ran {checks} checks against the live spec and canonical pages")
    if failures:
        print(f"\nFAILED ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS - troubleshooting guide matches the API contract")
    return 0


if __name__ == "__main__":
    sys.exit(main())
