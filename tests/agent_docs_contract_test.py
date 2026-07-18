#!/usr/bin/env python3
"""Static contract checks for HydraDB's agent-facing documentation."""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
OPENAPI_PATH = ROOT / "api-reference/v2/openapi.json"
V1_OPENAPI_PATH = ROOT / "api-reference/openapi.json"

EXPECTED_SKILLS = {
    ROOT / "skill.md": "hydradb",
    ROOT / ".mintlify/skills/hydradb-model-context/SKILL.md": "hydradb-model-context",
    ROOT / ".mintlify/skills/hydradb-ingest-context/SKILL.md": "hydradb-ingest-context",
    ROOT / ".mintlify/skills/hydradb-query-context/SKILL.md": "hydradb-query-context",
    ROOT / ".mintlify/skills/hydradb-debug-context/SKILL.md": "hydradb-debug-context",
}

KNOWN_CONTRADICTIONS = (
    "Use snake_case request fields in TypeScript",
    "Request parameters stay snake_case in both SDKs",
    "SDKs unwrap `data`",
    "ready_for_ingestion",
    "readyForIngestion",
    "tenant_metadata",
    "TooManyRequestsError",
    "body?.error?.code",
)


errors: list[str] = []


def require(condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def read(path: Path) -> str:
    if not path.is_file():
        errors.append(f"missing required file: {path.relative_to(ROOT)}")
        return ""
    return path.read_text(encoding="utf-8")


def frontmatter_value(text: str, field: str) -> str | None:
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end == -1:
        return None
    match = re.search(rf"^{re.escape(field)}:\s*[\"']?([^\n\"']+)", text[4:end], re.MULTILINE)
    return match.group(1).strip() if match else None


def docs_route_exists(route: str) -> bool:
    route = route.rstrip("/") or "/"
    if route in {"/mcp", "/llms.txt", "/llms-full.txt", "/skill.md"}:
        return True

    relative = route.lstrip("/")
    if relative.endswith(".md"):
        relative = relative[:-3]

    candidates = [
        ROOT / f"{relative}.mdx",
        ROOT / relative / "index.mdx",
    ]
    return any(candidate.is_file() for candidate in candidates)


def check_skills() -> list[str]:
    texts: list[str] = []
    v1_paths = set(json.loads(read(V1_OPENAPI_PATH)).get("paths", {}))
    v2_paths = set(json.loads(read(OPENAPI_PATH)).get("paths", {}))
    v1_only_paths = v1_paths - v2_paths

    for path, expected_name in EXPECTED_SKILLS.items():
        text = read(path)
        if not text:
            continue
        texts.append(text)
        relative = path.relative_to(ROOT)

        require(
            frontmatter_value(text, "name") == expected_name,
            f"{relative}: frontmatter name must be {expected_name!r}",
        )
        require(
            bool(frontmatter_value(text, "description")),
            f"{relative}: frontmatter description is required",
        )
        require(
            frontmatter_value(text, "license") == "Apache-2.0",
            f"{relative}: license must be Apache-2.0",
        )
        limit = 900 if path.name == "SKILL.md" else 600
        require(
            len(text.split()) <= limit,
            f"{relative}: {len(text.split())} words exceeds the {limit}-word context budget",
        )
        require("Cortex" not in text, f"{relative}: stale Cortex branding found")
        for route in v1_only_paths:
            require(route not in text, f"{relative}: v1-only active route found: {route}")

    combined = "\n".join(texts)
    require("TypeScript uses `camelCase`" in combined, "skills must state TypeScript camelCase")
    require("Python SDK" in combined and "`snake_case`" in combined, "skills must state Python snake_case")
    require("API-Version: 2" in combined, "skills must state the raw HTTP version header")
    require("under `.data`" in combined, "skills must state the response envelope contract")
    return texts


def check_endpoint_mentions(texts: list[str]) -> None:
    spec = json.loads(read(OPENAPI_PATH))
    spec_paths = set(spec.get("paths", {}))
    route_pattern = re.compile(r"\b(?:GET|POST|PUT|PATCH|DELETE)\s+(`?)(/[a-z0-9_/{}/-]+)\1", re.IGNORECASE)

    for path, text in zip(EXPECTED_SKILLS, texts):
        for match in route_pattern.finditer(text):
            route = match.group(2).rstrip("/") or "/"
            require(
                route in spec_paths,
                f"{path.relative_to(ROOT)}: endpoint {route!r} is absent from the v2 OpenAPI spec",
            )


def check_links() -> None:
    paths = [
        *EXPECTED_SKILLS,
        ROOT / "get-started/v2/agent-quickstart.mdx",
        ROOT / "get-started/v2/quickstart.mdx",
        ROOT / "api-reference/v2/error-responses.mdx",
    ]
    link_pattern = re.compile(r"\[[^\]]+\]\((https://docs\.hydradb\.com)?(/[^)#\s]+)(?:#[^)]+)?\)")

    for path in paths:
        text = read(path)
        for match in link_pattern.finditer(text):
            route = urlparse((match.group(1) or "") + match.group(2)).path
            require(
                docs_route_exists(route),
                f"{path.relative_to(ROOT)}: documentation link does not resolve locally: {route}",
            )


def check_mintlify_config() -> None:
    config = json.loads(read(ROOT / "docs.json"))
    require(bool(config.get("description")), "docs.json: site description is required for agent indexes")

    instructions = config.get("markdown", {}).get("instructions", [])
    if isinstance(instructions, str):
        instructions = [instructions]
    joined = " ".join(instructions)
    for phrase in ("API v2", "snake_case", "camelCase", "API-Version: 2", "payload from data"):
        require(phrase in joined, f"docs.json: markdown instructions must include {phrase!r}")
    require(
        "For an explicit v1 request" in joined,
        "docs.json: global agent instructions must preserve the explicit v1 contract",
    )

    v2 = next(
        (version for version in config.get("navigation", {}).get("versions", []) if version.get("version") == "v2"),
        {},
    )
    serialized_v2 = json.dumps(v2)
    require(
        "get-started/v2/agent-quickstart" in serialized_v2,
        "docs.json: agent quickstart must be present in v2 navigation",
    )


def check_agent_quickstart() -> None:
    text = read(ROOT / "get-started/v2/agent-quickstart.mdx")
    require(
        "npx skills add https://docs.hydradb.com" in text,
        "agent quickstart must include the exact skill installation command",
    )
    require(
        'actions={["copy", "cursor"]}' in text,
        "agent quickstart Prompt must use Mintlify's supported copy and cursor actions",
    )
    require(
        '<Visibility for="agents">' in text,
        "agent quickstart must include a Markdown-only completion contract for agents",
    )
    for prompt_body in re.findall(r"<Prompt\b[^>]*>(.*?)</Prompt>", text, re.DOTALL):
        require(
            not re.search(r"https?://", prompt_body),
            "agent quickstart Prompt children must not contain a bare URL; Mintlify autolinks it and corrupts action payloads",
        )


def check_known_contradictions() -> None:
    targets = [ROOT / "AGENTS.mdx", ROOT / "api-reference/v2/sdks.mdx", *EXPECTED_SKILLS]
    for path in targets:
        text = read(path)
        for contradiction in KNOWN_CONTRADICTIONS:
            require(
                contradiction not in text,
                f"{path.relative_to(ROOT)}: known contract contradiction found: {contradiction!r}",
            )

    sdk_text = read(ROOT / "api-reference/v2/sdks.mdx")
    require(
        "TypeScript SDK exposes **camelCase** fields" in sdk_text,
        "SDK page must describe TypeScript camelCase fields",
    )
    require(
        "SDKs return the full success envelope" in sdk_text,
        "SDK page must preserve the .data response envelope contract",
    )

    package = json.loads(read(ROOT / "package.json"))
    sdk_version = package.get("dependencies", {}).get("@hydradb/sdk", "")
    require(
        sdk_version.startswith("^2."),
        "package.json must pin @hydradb/sdk 2.x so agent snippets use the documented type surface",
    )

    metadata_page = read(ROOT / "api-reference/v2/endpoint/update-source-metadata.mdx")
    require(
        "PATCH /context/{id}/metadata" in metadata_page,
        "source metadata page must bind to the v2 OpenAPI path",
    )
    require(
        "/context/sources/" not in metadata_page,
        "source metadata page contains the stale non-OpenAPI route",
    )


def fenced_blocks(text: str, language: str) -> list[str]:
    pattern = rf"```{re.escape(language)}(?:[^\n]*)\n(.*?)```"
    return re.findall(pattern, text, re.DOTALL | re.IGNORECASE)


def check_code_blocks() -> None:
    paths = [*EXPECTED_SKILLS, ROOT / "get-started/v2/agent-quickstart.mdx"]

    for path in paths:
        text = read(path)
        for index, block in enumerate(fenced_blocks(text, "python"), 1):
            try:
                ast.parse(block)
            except SyntaxError as exc:
                errors.append(f"{path.relative_to(ROOT)}: Python block {index} is invalid: {exc}")

        for index, block in enumerate(fenced_blocks(text, "json"), 1):
            try:
                json.loads(block)
            except json.JSONDecodeError as exc:
                errors.append(f"{path.relative_to(ROOT)}: JSON block {index} is invalid: {exc}")

        for index, block in enumerate(fenced_blocks(text, "bash"), 1):
            result = subprocess.run(
                [os.environ.get("BASH", "bash"), "-n"],
                input=block,
                text=True,
                capture_output=True,
                check=False,
            )
            require(
                result.returncode == 0,
                f"{path.relative_to(ROOT)}: Bash block {index} is invalid: {result.stderr.strip()}",
            )

    typecheck = subprocess.run(
        ["node", "tests/typecheck_agent_snippets.mjs"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    require(
        typecheck.returncode == 0,
        "TypeScript agent snippets fail SDK 2.x strict type-check:\n"
        f"{typecheck.stdout}{typecheck.stderr}",
    )


def main() -> int:
    skill_texts = check_skills()
    check_endpoint_mentions(skill_texts)
    check_links()
    check_mintlify_config()
    check_agent_quickstart()
    check_known_contradictions()
    check_code_blocks()

    if errors:
        print(f"Agent documentation contract failed ({len(errors)} issue(s)):")
        for error in errors:
            print(f"  - {error}")
        return 1

    print(
        "Agent documentation contract passed: "
        f"{len(EXPECTED_SKILLS)} skills, v2 endpoints, links, naming, envelopes, SDK-typed snippets, and navigation verified."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
