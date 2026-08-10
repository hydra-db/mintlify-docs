#!/usr/bin/env python3
"""Score whether HydraDB docs expose the anchors coding agents need.

This is a deterministic docs-understanding guardrail. It does not call HydraDB,
an LLM, or any external service. Each fixture task names a realistic integration
question and the doc anchors an agent should be able to recover before writing
code.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = ROOT / "scripts" / "fixtures" / "agent_understanding_tasks.json"
DOCS_JSON = ROOT / "docs.json"


@dataclass
class AnchorResult:
    label: str
    pattern: str
    matched: bool


@dataclass
class TaskResult:
    task_id: str
    question: str
    docs: list[str]
    missing_docs: list[str]
    missing_nav: list[str]
    anchors: list[AnchorResult]

    @property
    def total(self) -> int:
        return len(self.anchors)

    @property
    def matched(self) -> int:
        return sum(1 for anchor in self.anchors if anchor.matched)

    @property
    def passed(self) -> bool:
        return not self.missing_docs and not self.missing_nav and self.matched == self.total


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def flatten_nav_pages(node: Any) -> set[str]:
    pages: set[str] = set()

    if isinstance(node, dict):
        for key, value in node.items():
            if key == "pages" and isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        pages.add(item)
                    else:
                        pages.update(flatten_nav_pages(item))
            else:
                pages.update(flatten_nav_pages(value))
    elif isinstance(node, list):
        for item in node:
            pages.update(flatten_nav_pages(item))

    return pages


def read_doc(path_text: str) -> tuple[str, str | None]:
    path = ROOT / path_text
    if not path.exists():
        return "", path_text
    return path.read_text(encoding="utf-8"), None


def score_task(task: dict[str, Any], nav_pages: set[str]) -> TaskResult:
    docs = list(task.get("docs", []))
    missing_docs: list[str] = []
    corpus_parts: list[str] = []

    for doc in docs:
        text, missing = read_doc(doc)
        if missing:
            missing_docs.append(missing)
        else:
            corpus_parts.append(f"\n\n<!-- {doc} -->\n{text}")

    corpus = "\n".join(corpus_parts)
    anchors: list[AnchorResult] = []
    for required in task.get("required", []):
        pattern = required["pattern"]
        flags = re.IGNORECASE | re.MULTILINE | re.DOTALL
        matched = re.search(pattern, corpus, flags) is not None
        anchors.append(
            AnchorResult(
                label=required["label"],
                pattern=pattern,
                matched=matched,
            )
        )

    missing_nav = [
        page for page in task.get("required_nav", []) if page not in nav_pages
    ]

    return TaskResult(
        task_id=task["id"],
        question=task["question"],
        docs=docs,
        missing_docs=missing_docs,
        missing_nav=missing_nav,
        anchors=anchors,
    )


def print_task(result: TaskResult, verbose: bool) -> None:
    status = "PASS" if result.passed else "FAIL"
    print(f"{status} {result.task_id}: {result.matched}/{result.total} anchors")

    if verbose or not result.passed:
        print(f"  question: {result.question}")
        if result.missing_docs:
            for doc in result.missing_docs:
                print(f"  missing doc: {doc}")
        if result.missing_nav:
            for page in result.missing_nav:
                print(f"  missing nav page: {page}")
        for anchor in result.anchors:
            marker = "OK" if anchor.matched else "MISSING"
            print(f"  {marker}: {anchor.label}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture",
        default=str(DEFAULT_FIXTURE),
        help="Path to the task fixture JSON",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if any task is missing a doc, nav entry, or anchor",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print every anchor result, not only failures",
    )
    args = parser.parse_args()

    fixture_path = Path(args.fixture).resolve()
    tasks = load_json(fixture_path)
    nav_pages = flatten_nav_pages(load_json(DOCS_JSON).get("navigation", {}))

    try:
        fixture_label = fixture_path.relative_to(ROOT)
    except ValueError:
        fixture_label = fixture_path

    print("=== HydraDB agent-understanding docs eval ===")
    print(f"Fixture: {fixture_label}")
    print(f"Tasks: {len(tasks)}")
    print()

    results = [score_task(task, nav_pages) for task in tasks]
    for result in results:
        print_task(result, args.verbose)

    total = sum(result.total for result in results)
    matched = sum(result.matched for result in results)
    failed = [result for result in results if not result.passed]
    if total == 0:
        print("ERROR: fixture contains no required checks", file=sys.stderr)
        return 1
    score = matched / total * 100

    print()
    print(
        f"Overall: {matched}/{total} anchors matched "
        f"({score:.1f}%), {len(results) - len(failed)}/{len(results)} tasks passed"
    )

    if failed:
        print("Failed tasks: " + ", ".join(result.task_id for result in failed))

    if args.strict and failed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
