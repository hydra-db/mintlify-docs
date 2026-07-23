#!/usr/bin/env python3
"""Verify AGENTS.mdx, OpenAPI, and SDK docs stay agent-safe."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "AGENTS.mdx"
OPENAPI = ROOT / "api-reference" / "v2" / "openapi.json"
SDKS = ROOT / "api-reference" / "v2" / "sdks.mdx"
QUICKSTART = ROOT / "get-started" / "v2" / "quickstart.mdx"

REQUIRED_INDEXING_STATUSES = {
    "queued",
    "processing",
    "graph_creation",
    "completed",
    "errored",
}


def main() -> int:
    violations: list[str] = []
    spec = json.loads(OPENAPI.read_text(encoding="utf-8"))
    schemas = spec["components"]["schemas"]

    indexing = (
        schemas["ingestion.V2ProcessingStatus"]["properties"]["indexing_status"]
    )
    enum_values = set(indexing.get("enum") or [])
    missing = REQUIRED_INDEXING_STATUSES - enum_values
    if missing:
        violations.append(
            f"openapi indexing_status missing enum values: {sorted(missing)}"
        )

    query_required = set(schemas["search.QueryRequest"].get("required") or [])
    for field in ("database", "query", "type"):
        if field not in query_required:
            violations.append(f"search.QueryRequest missing required field: {field}")

    sdks = SDKS.read_text(encoding="utf-8")
    if re.search(
        r"TypeScript SDK.*snake_case.*params|params: `database`, `query_by`",
        sdks,
        re.IGNORECASE | re.DOTALL,
    ) and "camelCase for multi-word fields" not in sdks:
        violations.append(
            "sdks.mdx still teaches TypeScript request fields as snake_case"
        )
    if "queryBy" not in sdks or "pageSize" not in sdks:
        violations.append("sdks.mdx is missing canonical TypeScript camelCase examples")

    agents = AGENTS.read_text(encoding="utf-8")
    if "SDKs unwrap `data`" in agents:
        violations.append("AGENTS.mdx checklist still claims SDKs unwrap data")
    if 'mode: "auto"' not in agents and "`auto`" not in agents:
        violations.append("AGENTS.mdx does not document mode auto")

    quickstart = QUICKSTART.read_text(encoding="utf-8")
    if "graph_creation" not in quickstart:
        violations.append("quickstart.mdx does not treat graph_creation as searchable")

    if "/context/sources/" in (
        (ROOT / "api-reference/v2/endpoint/update-source-metadata.mdx").read_text(
            encoding="utf-8"
        )
    ):
        violations.append(
            "update-source-metadata.mdx still documents /context/sources/... path"
        )

    if violations:
        print("Agent contract check failed:", file=sys.stderr)
        for item in violations:
            print(f"  - {item}", file=sys.stderr)
        return 1

    print(
        "Agent contract check passed: indexing statuses, required query fields, "
        "SDK naming, quickstart readiness, and metadata path are aligned."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
