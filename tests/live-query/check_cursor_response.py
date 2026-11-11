"""Run the cookbook's actual search_code function on a live response from stdin.

Print counts only. Neither the response contents nor environment values are logged.
"""
import ast
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace as NS

from hydra_db import types as sdk


def check():
    wire = json.load(sys.stdin)
    result = sdk.HandlerEnvelopeSearchV2RetrievalResult(**wire)
    if not result.success or result.data is None:
        raise ValueError("Expected a successful structured query response")
    expected = json.loads(result.data.json(exclude_none=True))
    page = Path(__file__).resolve().parents[2] / "cookbooks/v2/cursor.mdx"
    source = "\n".join(re.findall(r"^```python cursor.py\n(.*?)^```", page.read_text(), re.M | re.S))
    function = next(node for node in ast.parse(source).body
                    if isinstance(node, ast.FunctionDef) and node.name == "search_code")
    scope = {"json": json, "DATABASE": "unused", "COLLECTION": "unused",
             "client": NS(query=lambda **kwargs: result)}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(page), "exec"), scope)
    actual = json.loads(scope["search_code"]("Check the supplied live response"))
    if set(actual) != {"chunks", "graph_context", "additional_context"}:
        raise ValueError("The tool must return only the three public context fields")
    for field in ("graph_context", "additional_context"):
        if actual[field] != (expected.get(field) or {}):
            raise ValueError(f"The tool did not preserve {field}")
    expected_chunks = expected.get("chunks") or []
    if not expected_chunks or len(actual["chunks"]) != len(expected_chunks):
        raise ValueError("Expected every retrieved chunk to reach the tool response")
    for original, delivered in zip(expected_chunks, actual["chunks"]):
        path = (original.get("additional_metadata") or {}).get("path", original.get("source_title"))
        if delivered != {**original, "path": path}:
            raise ValueError("The tool did not preserve a chunk and its citation path")
    graph = actual["graph_context"]
    print(json.dumps({
        "chunks": len(actual["chunks"]),
        "graph_paths": len(graph.get("query_paths") or []) + len(graph.get("chunk_relations") or []),
        "linked_chunks": len(actual["additional_context"]),
    }))


if __name__ == "__main__":
    try:
        check()
    except Exception:
        # Validation failures can contain source text. Keep it out of test logs.
        print("Cursor response preservation check failed; no response contents logged.", file=sys.stderr)
        sys.exit(1)
