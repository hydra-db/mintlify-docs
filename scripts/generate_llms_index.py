import argparse
import json
import os
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def page_paths(value):
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        return set().union(*(page_paths(item) for item in value))
    if isinstance(value, dict):
        return set().union(*(page_paths(item) for key, item in value.items() if key in {"pages", "groups", "tabs", "anchors", "dropdowns"}))
    return set()


def generate():
    config = json.loads((ROOT / "docs.json").read_text())
    versions = {version["version"]: page_paths(version) for version in config["navigation"]["versions"]}
    legacy = versions["v1"] - versions["v2"] - {"continuity-assurance"}
    groups = {"Current v2 documentation": [], "Integration and connector details": [], "Site information": [], "Legacy v1 reference (not maintained)": []}
    for directory, subdirs, files in os.walk(ROOT):
        subdirs[:] = sorted(name for name in subdirs if not name.startswith(".") and name not in {"node_modules", "archive", "snippets"})
        for filename in sorted(files):
            if not filename.endswith(".mdx"):
                continue
            path = Path(directory) / filename
            slug = path.relative_to(ROOT).with_suffix("").as_posix()
            title = re.search(r'^title:\s*[\'\"]?(.*?)[\'\"]?\s*$', path.read_text(), re.M)
            title = title.group(1) if title else slug
            title = title.replace("[", "\\[").replace("]", "\\]")
            if slug in legacy:
                group = "Legacy v1 reference (not maintained)"
            elif slug in versions["v2"] or "/v2/" in slug:
                group = "Current v2 documentation"
            elif slug.startswith(("plugins/", "connectors/")):
                group = "Integration and connector details"
            else:
                group = "Site information"
            groups[group].append((slug, f"- [{title}](https://docs.hydradb.com/{slug}.md)"))
    lines = ["# HydraDB Documentation", "", "> Documentation for HydraDB's supported v2 API, SDKs, integrations, and cookbooks. Legacy v1 pages are listed separately and aren't compatible with current SDK installs.", "", "Start with the v2 quickstart. The Agent Integration Guide explains the API contract; this index lists every non-archive documentation page, including linked detail pages outside the navigation."]
    for heading, pages in groups.items():
        lines.extend(["", f"## {heading}", ""])
        lines.extend(line for _, line in sorted(pages))
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    destination = ROOT / "llms.txt"
    content = generate()
    if args.check:
        if not destination.exists() or destination.read_text() != content:
            raise SystemExit("llms.txt is stale; run python scripts/generate_llms_index.py")
        print("llms.txt matches all non-archive documentation pages")
    else:
        destination.write_text(content)
