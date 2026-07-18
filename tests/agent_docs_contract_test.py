#!/usr/bin/env python3
"""Static contract checks for HydraDB's agent-facing documentation."""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
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
    "App-source object kinds in OpenAPI include:",
    "`metadata` and `additional_metadata` are both plain objects",
    "SDKs return the full success envelope",
    "Errors use the same envelope with `success: false`",
    "SDK methods are grouped under three top-level namespaces",
    "The TypeScript SDK accepts the same snake_case keys",
    "They wrap every endpoint in this reference",
    "Both SDKs return a `{ success, data, error, meta }` envelope",
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
    if relative.endswith(".json"):
        return (ROOT / relative).is_file()
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
    spec_paths = spec.get("paths", {})
    route_pattern = re.compile(
        r"\b(GET|POST|PUT|PATCH|DELETE)\s+(`?)(/[a-z0-9_/{}/-]+)\2",
        re.IGNORECASE,
    )
    table_route_pattern = re.compile(
        r"\|\s*\[\s*`?(/[a-z0-9_/{}/-]+)`?\s*\]\([^)]+\)\s*\|\s*`(GET|POST|PUT|PATCH|DELETE)`",
        re.IGNORECASE,
    )

    documents = list(zip(EXPECTED_SKILLS, texts))
    for extra in (
        ROOT / "get-started/v2/agent-quickstart.mdx",
        ROOT / "get-started/v2/quickstart.mdx",
        ROOT / "api-reference/v2/index.mdx",
    ):
        documents.append((extra, read(extra)))

    for path, text in documents:
        mentions = [
            (match.group(1).lower(), match.group(3).rstrip("/") or "/")
            for match in route_pattern.finditer(text)
        ]
        mentions.extend(
            (match.group(2).lower(), match.group(1).rstrip("/") or "/")
            for match in table_route_pattern.finditer(text)
        )
        for method, route in mentions:
            require(
                route in spec_paths,
                f"{path.relative_to(ROOT)}: endpoint {route!r} is absent from the v2 OpenAPI spec",
            )
            if route in spec_paths:
                require(
                    method in spec_paths[route],
                    f"{path.relative_to(ROOT)}: {method.upper()} is not defined for v2 endpoint {route!r}",
                )


def check_links() -> None:
    paths = [
        *EXPECTED_SKILLS,
        ROOT / "get-started/v2/agent-quickstart.mdx",
        ROOT / "get-started/v2/quickstart.mdx",
        ROOT / "api-reference/v2/error-responses.mdx",
        ROOT / "api-reference/v2/index.mdx",
        ROOT / "api-reference/v2/sdks.mdx",
        ROOT / "api-reference/v2/endpoint/tenant-status.mdx",
        ROOT / "api-reference/v2/endpoint/ingest-context.mdx",
        ROOT / "api-reference/v2/endpoint/update-source-metadata.mdx",
        ROOT / "essentials/v2/metadata.mdx",
        ROOT / "AGENTS.mdx",
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
    targets = [
        ROOT / "AGENTS.mdx",
        ROOT / "api-reference/v2/index.mdx",
        ROOT / "api-reference/v2/sdks.mdx",
        *EXPECTED_SKILLS,
    ]
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
        "Only SDK methods whose return type is an envelope" in sdk_text,
        "SDK page must qualify which methods expose payloads under .data",
    )
    require(
        "can return direct objects instead" in sdk_text,
        "SDK page must document direct-return operations",
    )
    require(
        '"metadata": json.dumps({"team": "engineering"})' in sdk_text,
        "Python memory metadata must remain JSON-encoded inside the memories field",
    )
    require(
        'metadata: JSON.stringify({ team: "engineering" })' in sdk_text,
        "TypeScript memory metadata must remain JSON-encoded inside the memories field",
    )

    agents_text = read(ROOT / "AGENTS.mdx")
    require(
        "`app_kind`: free-form string; the OpenAPI does not define an enum." in agents_text,
        "AGENTS.mdx must not invent a closed app_kind enum",
    )
    agents_app_marker = "### Knowledge from app sources"
    require(agents_app_marker in agents_text, "AGENTS.mdx must preserve its app-source section")
    agents_app_section = agents_text.split(agents_app_marker, 1)[1].split("### Memories", 1)[0] if agents_app_marker in agents_text else ""
    require(
        all(field in agents_app_section for field in ("kind", "provider", "external_id", "fields"))
        and "content.text" not in agents_app_section
        and not re.search(r'(?m)^\s+(?:"content"|content):\s*\{', agents_app_section),
        "AGENTS.mdx must use the canonical app-native ingestion shape",
    )
    require(
        "`fast`, `thinking`, `auto`" in agents_text,
        "AGENTS.mdx must preserve every documented query mode",
    )

    package = json.loads(read(ROOT / "package.json"))
    sdk_version = package.get("dependencies", {}).get("@hydradb/sdk", "")
    require(
        sdk_version == "^2.1.1",
        "package.json must use the SDK 2.1.1 range validated by agent snippets",
    )
    npm_lock = json.loads(read(ROOT / "package-lock.json"))
    npm_root_version = npm_lock.get("packages", {}).get("", {}).get("dependencies", {}).get("@hydradb/sdk")
    npm_resolved_version = npm_lock.get("packages", {}).get("node_modules/@hydradb/sdk", {}).get("version")
    require(
        npm_root_version == "^2.1.1" and npm_resolved_version == "2.1.1",
        "package-lock.json must resolve the same @hydradb/sdk 2.1.1 contract as package.json",
    )
    pnpm_lock = read(ROOT / "pnpm-lock.yaml")
    require(
        "specifier: ^2.1.1" in pnpm_lock and "'@hydradb/sdk@2.1.1':" in pnpm_lock,
        "pnpm-lock.yaml must resolve @hydradb/sdk 2.1.1",
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
    require(
        '<Field name="collection" type="string" required />' in metadata_page,
        "source metadata page must require explicit collection scope",
    )
    require(
        "accepted deprecated alias for `additional_metadata`" in metadata_page,
        "source metadata page must preserve the document_metadata compatibility alias",
    )

    ingest_page = read(ROOT / "api-reference/v2/endpoint/ingest-context.mdx")
    ingest_app_marker = '<Accordion title="App sources'
    require(ingest_app_marker in ingest_page, "ingest endpoint must preserve its app-source field section")
    ingest_app_section = ingest_page.split(ingest_app_marker, 1)[1].split("</Accordion>", 1)[0] if ingest_app_marker in ingest_page else ""
    require(
        all(f'<Field name="{field}"' in ingest_app_section for field in ("kind", "provider", "external_id", "fields"))
        and '<Field name="content"' not in ingest_app_section
        and not re.search(r'(?m)^\s+(?:"content"|content):\s*\{', ingest_page),
        "ingest endpoint must match the canonical app-native item schema",
    )
    memory_marker = '<Accordion title="3. User memories'
    require(memory_marker in ingest_page, "ingest endpoint must preserve its memory field section")
    memory_section = ingest_page.split(memory_marker, 1)[1].split("</Accordion>", 1)[0] if memory_marker in ingest_page else ""
    require(
        '<Field name="metadata" type="string (JSON object)" />' in memory_section,
        "memory metadata must remain a JSON-encoded string inside each memories item",
    )
    require(
        '<Field name="additional_metadata" type="object" />' in memory_section
        and '<Field name="additional_metadata" type="string' not in memory_section,
        "memory additional_metadata must remain an object inside each memories item",
    )
    require(
        "SDK 2.1.1 accepts one Uploadable per call" in ingest_page,
        "ingest endpoint must document the TypeScript SDK's single-Uploadable contract",
    )
    require(
        'upsert="true"' in ingest_page and "upsert=True" not in ingest_page,
        "Python ingest examples must use the SDK 2.1.1 string type for upsert",
    )
    for path in (
        ROOT / "AGENTS.mdx",
        ROOT / "api-reference/v2/endpoint/ingest-context.mdx",
        ROOT / "api-reference/v2/error-responses.mdx",
        ROOT / "api-reference/v2/sdks.mdx",
    ):
        require(
            not re.search(r"documents\s*=\s*\[", read(path)),
            f"{path.relative_to(ROOT)}: Python SDK 2.1.1 accepts one typed File per ingest call",
        )

    api_index = read(ROOT / "api-reference/v2/index.mdx")
    require(
        "/context/sources/" not in api_index
        and "`/context/{id}/metadata`" in api_index
        and "context.updateSourceMetadata" in api_index,
        "API index must preserve the v2 source-metadata route and SDK method",
    )

    error_page = read(ROOT / "api-reference/v2/error-responses.mdx")
    require(
        "`502`" in error_page and "statusCode >= 500" in error_page and "status_code >= 500" in error_page,
        "retry guidance must cover transient 502 and other 5xx responses",
    )
    require(
        "`502`" in agents_text and "exc.status_code < 500" in agents_text,
        "AGENTS.mdx retry guidance must cover transient 502 and other 5xx responses",
    )

    quickstart = read(ROOT / "get-started/v2/quickstart.mdx")
    require(
        'status.indexing_status in ("errored", "failed")' in quickstart,
        "Python quickstart must stop on both documented indexing failure states",
    )
    require(
        '[ "$INDEXING_STATUS" = "errored" ] || [ "$INDEXING_STATUS" = "failed" ]' in quickstart,
        "cURL quickstart must stop on both documented indexing failure states",
    )


def fenced_blocks(text: str, language: str) -> list[str]:
    pattern = rf"```{re.escape(language)}(?:[^\n]*)\n(.*?)```"
    return re.findall(pattern, text, re.DOTALL | re.IGNORECASE)


def resolve_bash() -> str | None:
    candidates = [os.environ.get("BASH"), shutil.which("bash")]
    if os.name == "nt":
        candidates.extend(
            [
                r"C:\Program Files\Git\bin\bash.exe",
                r"C:\Program Files\Git\usr\bin\bash.exe",
            ]
        )

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            probe = subprocess.run(
                [candidate, "--version"],
                text=True,
                capture_output=True,
                check=False,
            )
        except OSError:
            continue
        if probe.returncode == 0:
            return candidate
    return None


def check_code_blocks() -> None:
    paths = [
        *EXPECTED_SKILLS,
        ROOT / "get-started/v2/agent-quickstart.mdx",
        ROOT / "get-started/v2/quickstart.mdx",
        ROOT / "api-reference/v2/endpoint/ingest-context.mdx",
    ]
    bash = resolve_bash()

    for path in paths:
        text = read(path)
        for index, block in enumerate(fenced_blocks(text, "python"), 1):
            try:
                ast.parse(textwrap.dedent(block))
            except SyntaxError as exc:
                errors.append(f"{path.relative_to(ROOT)}: Python block {index} is invalid: {exc}")

        for index, block in enumerate(fenced_blocks(text, "json"), 1):
            try:
                json.loads(textwrap.dedent(block))
            except json.JSONDecodeError as exc:
                errors.append(f"{path.relative_to(ROOT)}: JSON block {index} is invalid: {exc}")

        for index, block in enumerate(fenced_blocks(text, "bash"), 1):
            if bash is None:
                require(False, f"{path.relative_to(ROOT)}: no working Bash parser is available")
                break
            result = subprocess.run(
                [bash, "-n"],
                input=textwrap.dedent(block),
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
