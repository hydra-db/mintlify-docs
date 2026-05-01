---
title: "Error Handling Playbook"
description: "A practical guide to detecting, interpreting, and recovering from failures across ingestion, recall, and async processing in HydraDB."
---

The existing [Error Responses](/api-reference/error-responses) page documents the *shape* of HydraDB errors — HTTP codes, error codes, and response format. This page is the **playbook**: what to actually do when something breaks, how to tell a transient failure from a permanent one, and how to build clients that degrade gracefully instead of silently losing data.

If you're building production agents on HydraDB, read this end-to-end. Most incidents in memory systems come from three places, ingestion that half-succeeded, recall that returned nothing useful, and async jobs that never finished, and every section below maps to one of those.

## How HydraDB errors are structured

Every error from the API returns a `detail` object with three fields you should rely on:

```json
{
  "detail": {
    "success": false,
    "message": "Human-readable description",
    "error_code": "MACHINE_READABLE_CODE"
  }
}
```

**Always branch on `error_code`, never on `message`.** Messages change; codes are stable. The HTTP status tells you the *class* of failure (client vs server, auth vs validation); the `error_code` tells you the *specific* reason. Both matter for recovery.

<Note>
Some edge-case errors (especially network-level or proxy-level failures) may not include the `detail` wrapper. Always defensively check `response.ok` and wrap your parse in a `try/catch`, assume the body may not be JSON.
</Note>

## The three failure domains

HydraDB operations fall into three domains, and each has a different recovery model. Knowing which domain you're in is the first thing to figure out when something fails.

| Domain | Example operations | Typical failures | Recovery model |
|---|---|---|---|
| **Ingestion** | `upload_knowledge`, `add_memories`, `add_embeddings` | File too large, invalid format, partial batch failure, processing pipeline error | Retry the specific item; check `verify_processing` for async state |
| **Recall** | `full_recall`, `recall_preferences`, `keyword` (lexical) | Empty results, timeouts, invalid filters, tenant not ready | Widen the query, fall back to lexical, check tenant state |
| **Management** | `tenants/create`, `delete_memory`, `list`, `relations` | Conflict on create, not found on delete, permission denied | Idempotent retry or hard fail to the user |

The rest of this page is organized around these domains.

---

## Ingestion errors

Ingestion is the riskiest surface because it's **partially asynchronous**: the HTTP call returns once the file is accepted, but parsing, chunking, embedding, and graph construction happen afterward. A 200 response does not mean your memory is queryable.

### Synchronous ingestion failures

These fail at the HTTP layer and are easy to handle:

| Error code | HTTP | What it actually means | What to do |
|---|---|---|---|
| `INVALID_FILE_FORMAT` | 400 | File extension or MIME type isn't one HydraDB supports | Check the supported-formats list; convert client-side before retry |
| `FILE_TOO_LARGE` | 400 | File exceeds per-upload size limit | Split the file (chapter-by-chapter for books, sheet-by-sheet for spreadsheets) and upload as separate memories with shared `document_metadata` |
| `INVALID_PARAMETERS` | 400 | `tenant_id` missing, malformed `file_metadata`, or an unknown field | Validate the request body against the SDK types before sending; don't send empty strings for optional IDs  omit them |
| `TENANT_NOT_FOUND` | 404 | Tenant doesn't exist yet | Create the tenant first. In B2B flows, create-on-first-use at your application layer |
| `MEMORY_LIMIT_EXCEEDED` | 422 | User memory quota hit for this tenant/sub-tenant | Decide per-product: either delete oldest memories, surface a UI prompt, or upgrade the plan |
| `CONFLICT` | 409 | Duplicate file upload with same ID | Treat as success if you're using deterministic IDs; otherwise generate a new one |

**Pattern for a robust upload:**

```typescript
async function uploadWithRecovery(file: Buffer, fileMeta: FileMetadata) {
  try {
    return await client.upload.knowledge({
      files: [file],
      tenant_id: tenantId,
      file_metadata: [fileMeta]
    });
  } catch (err) {
    const code = err?.detail?.error_code;

    if (code === "TENANT_NOT_FOUND") {
      await client.tenant.create({ tenant_id: tenantId });
      return uploadWithRecovery(file, fileMeta); // retry once
    }
    if (code === "FILE_TOO_LARGE") {
      const chunks = await splitFile(file);
      return Promise.all(chunks.map((c, i) =>
        uploadWithRecovery(c, { ...fileMeta, id: `${fileMeta.id}_part${i}` })
      ));
    }
    if (code === "CONFLICT") {
      return { status: "already_ingested", id: fileMeta.id };
    }
    throw err; // unknown — let it bubble
  }
}
```

### Asynchronous ingestion failures (the hard ones)

The upload endpoint returns quickly. The actual processing: parse → chunk → embed → extract entities → link into graph runs in the background. Failures here don't come back as HTTP errors; they show up when you check `verify_processing` or when a recall returns nothing.

Three things can go wrong in this phase:

1. **Parse failure** — corrupted PDF, password-protected file, or a format the parser mishandles. Shows up as a `PROCESSING_FAILED` status on `verify_processing`.
2. **Partial success in a batch** — you uploaded 10 files, 7 processed, 3 failed. The batch HTTP call returned 200. Only `verify_processing` surfaces which ones died.
3. **Silent indexing lag** — processing is still running but taking longer than expected. Recalls against the tenant will return incomplete results until it finishes.

**The rule**: never treat an upload as "done" until `verify_processing` confirms it. For interactive UIs, show an "indexing" state per document. For batch jobs, poll before triggering any downstream recall.

```python
def wait_until_indexed(file_ids: list[str], tenant_id: str, timeout_s: int = 300):
    deadline = time.time() + timeout_s
    pending = set(file_ids)
    backoff = 2

    while pending and time.time() < deadline:
        status = client.ingestion.verify_processing(
            file_ids=list(pending),
            tenant_id=tenant_id
        )
        for file_id, state in status.items():
            if state == "processed":
                pending.discard(file_id)
            elif state == "failed":
                # Don't keep polling a failed file: log and move on
                log.error(f"Ingestion failed for {file_id}")
                pending.discard(file_id)
        if pending:
            time.sleep(backoff)
            backoff = min(backoff * 1.5, 30)  # cap at 30s

    if pending:
        raise TimeoutError(f"Still indexing after {timeout_s}s: {pending}")
```

<Warning>
Do not fire recall queries immediately after an upload and assume complete coverage. Even when `verify_processing` reports success for the *document*, graph enrichment (entity extraction, cross-source linking) may still be catching up for a few more seconds on very large documents. For most workloads this is invisible; for high-stakes immediate recall, add a short guard delay or a targeted verify.
</Warning>

---

## Recall errors

Recall failures are subtle because most of them aren't technically "errors", the HTTP call succeeds, you just got back something you can't use. The playbook here is more about *degradation* than *recovery*.

### Hard recall errors

| Error code | HTTP | Cause | Recovery |
|---|---|---|---|
| `INVALID_SEARCH_PARAMETERS` | 400 | Bad `alpha`, out-of-range `recency_bias`, or malformed metadata filter | Validate ranges client-side; `alpha` should be 0–1, `recency_bias` non-negative |
| `SEARCH_TIMEOUT` | 500 | Query exceeded internal deadline: usually very large tenants + complex graph traversal | Retry with a narrower metadata filter or a tighter `max_results` |
| `TENANT_NOT_FOUND` | 404 | Typo in `tenant_id`, or tenant was deleted | Hard fail: don't auto-create on recall |
| `NO_RESULTS_FOUND` | 404 | Truly no matching memories | Not always an error see below |

### The "empty result" problem

`NO_RESULTS_FOUND` and an empty `chunks` array are different things, and both need handling:

- **Empty `chunks: []`** with a 200 response the query executed, nothing matched under your current filters and scoring
- **`NO_RESULTS_FOUND`** the query was rejected because no recall path could even be attempted (e.g., every filter excluded all data)

For
For interactive agents, empty results are often worse than errors the agent silently answers with no context. Wrap every recall with a fallback chain:

```typescript
async function recallWithFallback(query: string, tenantId: string) {
  // 1. Try full hybrid recall first
  let result = await client.recall.fullRecall({
    query,
    tenantId,
    alpha: 0.7,       // balanced semantic/lexical
    recencyBias: 0.2
  });

  if (result.chunks?.length) return { source: "full_recall", ...result };

  // 2. Widen: drop recency preference, lean more semantic
  result = await client.recall.fullRecall({
    query,
    tenantId,
    alpha: 0.9,
    recencyBias: 0
  });

  if (result.chunks?.length) return { source: "widened", ...result };

  // 3. Last resort: pure lexical catches exact-match queries
  //    that hybrid missed (e.g. product names, error codes)
  const lexical = await client.recall.keyword({ query, tenantId });

  if (lexical.chunks?.length) return { source: "lexical", ...lexical };

  return { source: "empty", chunks: [] };
}
```

The fallback isn't just for reliability it gives you **observability**. Log the `source` field and you'll quickly see which queries consistently need widening, which is a signal to either re-ingest missing content or adjust your default `alpha`.

### Timeouts and slow recalls

`SEARCH_TIMEOUT` is almost always caused by one of:

- Very broad queries on a tenant with millions of memories and no metadata filter
- Graph traversal exploding because a highly-connected entity matched the query
- A cold tenant that hasn't been queried recently (first query after idle can be slower)

The fix is almost never "retry immediately", retrying the exact same query will just time out again. Instead:

1. **Add a metadata filter**. Even a coarse one (`source: "notion"` or `project: "phoenix"`) dramatically narrows the search space.
2. **Lower `max_results`**. The cost of ranking scales with candidate count.
3. **Reduce `recency_bias` if set high**. Strong recency preference forces a larger initial candidate set.

If you've tightened all three and still hit timeouts, that's a signal to contact support, it likely indicates a tenant shape (sharding, graph density) that warrants attention.

---

## Async and long-running job errors

Three HydraDB operations are meaningfully asynchronous: **file ingestion**, **memory inference** (`infer: true`), and **graph enrichment**. The first is the one you explicitly check with `verify_processing`; the other two happen transparently and only surface if something goes wrong downstream.

### Stuck or lost ingestion jobs

If `verify_processing` returns `processing` indefinitely (beyond, say, 5 minutes for a reasonably-sized document):

1. **Re-verify with the file ID explicitly** — batch verify calls can occasionally return stale status.
2. **Check the monitor endpoint** — `GET /tenants/monitor` surfaces tenant-level processing health.
3. **Do not re-upload blindly.** A second upload with the same content but new ID doubles your storage and creates duplicate graph entities that disambiguation has to resolve later. Delete the stuck one first.

```python
def safe_reingest(file_id: str, tenant_id: str, file_path: str):
    status = client.ingestion.verify_processing(
        file_ids=[file_id], tenant_id=tenant_id
    )
    if status[file_id] == "failed":
        client.knowledge.delete(file_id=file_id, tenant_id=tenant_id)
    elif status[file_id] == "processing":
        raise RuntimeError(f"{file_id} still processing — don't reingest yet")
    # Only re-upload if failed and deleted, or if not present at all
    with open(file_path, "rb") as f:
        return client.upload.knowledge(
            tenant_id=tenant_id,
            files=[(file_path, f)],
            file_metadata=[{"id": file_id}]
        )
```

### Inference failures on `infer: true`

When you submit memories with `infer: true`, HydraDB extracts preferences and signals from the raw text. This step can silently produce weaker results if:

- The source text is very short (one-line messages give the inference model nothing to work with)
- The text contains mostly numbers, code, or structured data (try `is_markdown: true` or `infer: false`)
- The `custom_instructions` are contradictory or overly narrow

There's no error code for "inference was weak", you find out by recalling and noticing the memory isn't surfacing. Mitigation: for critical user preferences, set `infer: false` and provide pre-structured text. Reserve `infer: true` for conversation transcripts and free-form content.

---

## Retry semantics: what's safe to retry

Not every failure should be retried. Retrying a non-idempotent operation can double-create resources, blow out quotas, or corrupt state. Use this table:

| Error class | Retryable? | Notes |
|---|---|---|
| Network error (no response) | ✅ Yes | Use exponential backoff |
| 5xx `INTERNAL_ERROR` | ✅ Yes | Backoff + jitter; cap at 3–5 attempts |
| `SEARCH_TIMEOUT` | ⚠️ Only after narrowing | Same query will time out again |
| 429 rate limit | ✅ Yes | Respect `Retry-After` if present |
| 4xx validation (`INVALID_PARAMETERS`, `INVALID_FILE_FORMAT`) | ❌ No | Fix the request |
| `TENANT_NOT_FOUND` on recall | ❌ No | Hard fail to user |
| `CONFLICT` on create | ❌ No | Treat as success if idempotent ID; otherwise change ID |
| `MEMORY_LIMIT_EXCEEDED` | ❌ No | Requires quota action |

**Exponential backoff with jitter** — use this, not naive doubling:

```typescript
async function retry<T>(fn: () => Promise<T>, maxAttempts = 4): Promise<T> {
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      return await fn();
    } catch (err) {
      const status = err?.status ?? err?.response?.status;
      const code = err?.detail?.error_code;

      const retryable =
        !status ||                                // network error
        status >= 500 ||                          // server error
        status === 429 ||                         // rate limit
        (status === 404 && code === "PROCESSING"); // in-flight

      if (!retryable || attempt === maxAttempts) throw err;

      const base = Math.min(1000 * 2 ** (attempt - 1), 30_000);
      const jitter = Math.random() * 500;
      await new Promise(r => setTimeout(r, base + jitter));
    }
  }
  throw new Error("unreachable");
}
```

---

## Building observability around failures

Debugging HydraDB issues in production is dramatically easier if you log the right things up front. At minimum, capture for every failed call:

- `tenant_id` and `sub_tenant_id`
- HTTP status + `error_code` (not just the message)
- Operation type (`upload_knowledge`, `full_recall`, etc.)
- For recall: the `query`, `alpha`, `recency_bias`, and any metadata filters
- For ingestion: the `file_metadata.id` and file size
- Request ID if the response includes one

Structured logs matter more than verbose ones. A single line per failure with those fields lets you answer "which tenant sees the most timeouts" or "are 413s concentrated in one file type" in seconds.

For user-facing errors, translate HydraDB codes into your own product vocabulary before surfacing them. A user should never see `MEMORY_LIMIT_EXCEEDED` they should see whatever your product calls that condition.

---

## Quick reference: failure mode → first action

When something fails in production and you have ten seconds to decide what to do:

- **Upload returned 200 but recall is empty** → poll `verify_processing`
- **Recall returns empty but data exists** → widen `alpha`, drop `recency_bias`, fall back to lexical
- **Recall times out** → add a metadata filter, lower `max_results`
- **`TENANT_NOT_FOUND` during ingestion** → create the tenant, retry once
- **`FILE_TOO_LARGE`** → split and upload as linked parts
- **`CONFLICT` on create** → treat as success if you use deterministic IDs
- **5xx on any endpoint** → exponential backoff, max 3 retries
- **Inference output looks weak** → switch to `infer: false` with structured text

Everything past this list is in the per-domain sections above.
