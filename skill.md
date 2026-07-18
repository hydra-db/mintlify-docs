---
name: hydradb
description: Build, review, or debug HydraDB API v2 integrations for agent memory, knowledge ingestion, GraphRAG, and grounded context retrieval.
license: Apache-2.0
compatibility: HydraDB API v2; Python hydradb-sdk 2.x; TypeScript @hydradb/sdk 2.x
metadata:
  product: HydraDB
  api-version: "2"
  documentation: https://docs.hydradb.com
---

# HydraDB

HydraDB stores knowledge and scoped memory for reliable hybrid and graph-enriched retrieval.

## Route the task

Load only the focused skill that matches the job:

| Task | Skill | Use it for |
|---|---|---|
| Choose a data model | `hydradb-model-context` | Knowledge vs memory, database vs collection, metadata, stable IDs |
| Write or repair ingestion | `hydradb-ingest-context` | Database readiness, multipart ingestion, status polling, upserts |
| Write or tune retrieval | `hydradb-query-context` | Query modes, filters, GraphRAG, `query_paths`, grounded LLM output |
| Diagnose an integration | `hydradb-debug-context` | Scope misses, SDK casing, envelopes, readiness, retries, error handling |

For a full feature, follow that order. Use the [agent quickstart](https://docs.hydradb.com/get-started/v2/agent-quickstart) to install them; consult the [full agent reference](https://docs.hydradb.com/AGENTS) only when a focused skill alone is insufficient.

## Non-negotiable v2 contract

1. Prefer `database` and `collection`. Do not introduce deprecated tenant fields or v1 routes.
2. Raw HTTP requires `Authorization: Bearer ...` and `API-Version: 2`.
3. Raw HTTP and Python use `snake_case`; TypeScript uses `camelCase` request and response fields.
4. Successful v2 calls whose response schema is an envelope expose the payload under `.data`; generated SDK methods return that envelope and raise typed exceptions for HTTP failures.
5. Database creation and ingestion are asynchronous. Poll readiness before ingesting and processing status before querying.
6. Reuse the same database and collection when writing and reading. A scope mismatch usually looks like an empty result, not an error.
7. Use `collection` for partitions such as a user, team, or workspace. Use `metadata_filters` for exact constraints inside that partition.
8. Never invent an endpoint, field, enum, or response shape. Verify REST details against the [v2 API reference](https://docs.hydradb.com/api-reference/v2) or the documentation MCP server at `https://docs.hydradb.com/mcp`.
9. Prefer the focused task skills over loading the full documentation dump into context.

## Minimal retrieval loop

```text
create database -> wait for database readiness -> ingest context
-> wait for indexing_status=completed -> query same scope
-> ground the model in data.chunks and graph_context
```

For production code, also handle terminal failures, timeouts, rate limits, and missing context. Never let the model present an unsupported answer as if HydraDB returned it.
