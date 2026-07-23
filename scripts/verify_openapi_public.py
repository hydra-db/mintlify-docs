#!/usr/bin/env python3
"""Reject private implementation details in the public OpenAPI contract."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "api-reference" / "v2" / "openapi.json"

PRIVATE_PATHS = {
    "/connectors/{id}/credentials",
}

PRIVATE_COMPONENT_PATTERNS = (
    re.compile(r"github_com_", re.IGNORECASE),
    re.compile(r"internal_service", re.IGNORECASE),
)

PRIVATE_DESCRIPTION_PATTERNS = {
    "source-code serialization detail": re.compile(
        r"\b(?:MarshalJSON|UnmarshalJSON|omitempty|default_factory|populate_by_name)\b",
        re.IGNORECASE,
    ),
    "backend framework or middleware": re.compile(
        r"\b(?:Pydantic|FastAPI|TenantAliases|reqmeta)\b",
        re.IGNORECASE,
    ),
    "backend storage or orchestration": re.compile(
        r"\b(?:DynamoDB|dynamodbav|Temporal(?:'s)?|MongoDB|GSI)\b",
        re.IGNORECASE,
    ),
    "private source identifier": re.compile(
        r"\b(?:PRO-\d+|SearchService|graphVectorPruneEnabled|resolveTenant|"
        r"toConnector|connectorFromItem|toResource)\b",
        re.IGNORECASE,
    ),
    "private authentication mechanism": re.compile(
        r"\bX-Cortex-Secret\b",
        re.IGNORECASE,
    ),
    "private deployment behavior": re.compile(
        r"\b(?:repo-level config|server-level flag|configured at startup)\b",
        re.IGNORECASE,
    ),
}


def pointer(parts: tuple[str, ...]) -> str:
    if not parts:
        return "/"
    return "/" + "/".join(
        part.replace("~", "~0").replace("/", "~1") for part in parts
    )


def iter_descriptions(
    value: Any, path: tuple[str, ...] = ()
) -> list[tuple[tuple[str, ...], str]]:
    descriptions: list[tuple[tuple[str, ...], str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = (*path, key)
            if key == "description" and isinstance(child, str):
                descriptions.append((child_path, child))
            else:
                descriptions.extend(iter_descriptions(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            descriptions.extend(iter_descriptions(child, (*path, str(index))))
    return descriptions


def validate_spec(spec: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    paths = spec.get("paths", {})
    for private_path in sorted(PRIVATE_PATHS & paths.keys()):
        violations.append(
            f"/paths/{private_path}: private endpoint must not be published"
        )

    schemas = spec.get("components", {}).get("schemas", {})
    for schema_name in schemas:
        for pattern in PRIVATE_COMPONENT_PATTERNS:
            if pattern.search(schema_name):
                violations.append(
                    f"/components/schemas/{schema_name}: private source name"
                )
                break

    for path, description in iter_descriptions(spec):
        if description == "Internal Server Error":
            continue
        for category, pattern in PRIVATE_DESCRIPTION_PATTERNS.items():
            match = pattern.search(description)
            if match:
                violations.append(
                    f"{pointer(path)}: {category} ({match.group(0)!r})"
                )

    return violations


def main() -> int:
    with SPEC_PATH.open(encoding="utf-8") as spec_file:
        spec = json.load(spec_file)

    violations = validate_spec(spec)
    if violations:
        print("Public OpenAPI contract check failed:", file=sys.stderr)
        for violation in violations:
            print(f"  - {violation}", file=sys.stderr)
        return 1

    print(
        "Public OpenAPI contract check passed: "
        f"{len(spec.get('paths', {}))} paths and "
        f"{len(spec.get('components', {}).get('schemas', {}))} schemas scanned."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
