import argparse
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
    """llms.txt carries the Agent Integration Guide, so an agent that fetches either reads the same text."""
    source = (ROOT / "AGENTS.mdx").read_text()
    front, body = re.match(r"---\n(.*?)\n---\n(.*)", source, re.S).groups()
    field = lambda name: re.search(rf'^{name}:\s*[\'"]?(.*?)[\'"]?\s*$', front, re.M).group(1)
    return f"# {field('title')}\n\n> {field('description')}\n\n{body.strip()}\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    destination = ROOT / "llms.txt"
    content = generate()
    if args.check:
        if not destination.exists() or destination.read_text() != content:
            raise SystemExit("llms.txt is stale; run python scripts/generate_llms_index.py")
        print("llms.txt matches the Agent Integration Guide")
    else:
        destination.write_text(content)
