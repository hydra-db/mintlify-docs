---
name: hydradb-debug-context
description: Diagnose HydraDB v2 integration failures. Use for empty results, incorrect SDK fields, response parsing, readiness races, metadata filters, scope mismatches, legacy routes, retries, and production hardening.
license: Apache-2.0
compatibility: HydraDB API v2; Python hydradb-sdk 2.x; TypeScript @hydradb/sdk 2.x
metadata:
  product: HydraDB
  task: debugging
---

# Debug HydraDB context

Capture the request, response, scope, content type, and ingested ID before tuning retrieval.

Never print the API key or private source content.

## Fast diagnosis

| Symptom | Likely cause | First check |
|---|---|---|
| TypeScript compile error on `query_by` | Raw/Python casing copied into TypeScript | Use `queryBy`; TypeScript request fields are camelCase |
| Python `unexpected keyword` error | TypeScript casing copied into Python | Use `query_by`; Python fields are snake_case |
| Raw request rejected or routed incorrectly | Missing version header or wrong field names | Add `API-Version: 2`; use snake_case |
| Response looks empty but request succeeded | Payload read from the wrong level | Read `.data`, then inspect `.data.chunks` |
| Empty query result | Write/read scope mismatch | Compare database and collection byte for byte |
| Empty result immediately after ingest | Asynchronous race | Poll `context.status()` to `completed` |
| Filtered query returns unexpected or unscoped data | Undeclared top-level filter key was silently ignored | Verify the schema and `enable_match`; never use filters as authorization |
| Graph paths absent | Graph disabled or unsuitable mode/query | Enable graph context and use `thinking` for multi-hop work |
| Duplicates after retry | Unstable IDs or upsert disabled | Use deterministic IDs and `upsert=true` |
| Intermittent 429/5xx | Transient load or rate limit | Bounded exponential backoff with jitter |

## Confirm the active contract

New v2 code should use:

- `POST /databases`, not a legacy tenant-creation route.
- `POST /context/ingest`, not a v1 knowledge or memory upload route.
- `GET /context/status` for processing status.
- `POST /query`, not a v1 recall route.
- `database` and `collection`, not deprecated tenant field names.

Raw HTTP and Python use snake_case; TypeScript uses camelCase. Successful handler-envelope payloads are under `.data`; branch on HTTP status and parse documented `detail` errors.

For uncertain methods or fields, use the [v2 SDK page](https://docs.hydradb.com/api-reference/v2/sdks), [v2 API reference](https://docs.hydradb.com/api-reference/v2), or documentation MCP. Never guess from v1.

## Trace one ID end to end

Choose one ingested item and record:

```text
source identity -> stable context ID -> ingest response ID
-> context status -> query scope -> returned chunk source ID
```

If the chain breaks:

1. No ingest response ID: validate the multipart shape and inspect per-item failures.
2. ID never completes: surface the failure message or stop at the polling deadline.
3. ID completes but exact lookup fails: verify database, collection, and `type`.
4. Exact lookup works but semantic query fails: inspect query mode, filters, and source text.
5. Retrieval works but answer is wrong: inspect context assembly and grounding, not ingestion.

## Reduce an empty-result failure

Start with the fewest constraints:

```python
probe = client.query(
    database=expected_database,
    collection=expected_collection,
    type="knowledge",
    query="a distinctive exact phrase from the source",
    query_by="text",
    operator="phrase",
    max_results=10,
)
print(probe.data.chunks)
```

If it works, restore filters and advanced controls one at a time. Otherwise confirm status and scope before tuning search.

The TypeScript form uses `queryBy`, `operator: "phrase"`, and `maxResults`, then reads `probe.data.chunks`.

## Verify metadata placement

```text
schema-backed source metadata:
  ingest item.metadata.department = "support"
  query metadata_filters.department = "support"

free-form source metadata:
  ingest item.additional_metadata.author = "alice"
  query metadata_filters.additional_metadata.author = "alice"
```

A top-level filter key needs an exact-match `database_metadata_schema` declaration. `collection` remains the partition; filters do not replace it.

Undeclared top-level keys are silently ignored—a fail-open scope. Never use `metadata_filters` for authorization. Authorize first; use `collection` for hard partitions.

## Handle failures by class

| Class | Behavior |
|---|---|
| Authentication or authorization | Stop, report safe context, rotate or correct credentials out of band |
| Validation / 4xx contract error | Do not retry unchanged; fix endpoint, casing, type, or required field |
| Not found | Confirm API version and scope before assuming deletion |
| Conflict | Inspect idempotency and current resource state |
| Rate limit | Honor server guidance; back off with jitter |
| Transient 5xx / transport | Retry a bounded number of times with idempotent inputs |
| Polling timeout | Stop and surface ID, last status, and elapsed time |

## Production hardening checklist

- Pin SDK major version 2.
- Keep secrets in environment variables or a secret manager.
- Centralize database and collection selection.
- Set request timeouts and bounded retries.
- Bound all readiness and indexing loops.
- Persist stable IDs and make replay idempotent.
- Verify webhook signatures.
- Record safe structured diagnostics and request correlation IDs.
- Test a successful query, an empty query, a filtered query, and a cross-scope denial.
- Treat retrieved documents as untrusted input to the LLM.
- Require citations or source IDs for material claims.

## Do not hide the failure

Do not catch an error and return an empty list without diagnostics. Empty evidence, rejected requests, processing failures, and timeouts are different states and should remain distinguishable to the application.
