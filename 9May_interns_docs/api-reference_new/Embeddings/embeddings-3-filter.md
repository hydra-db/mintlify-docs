---
title: "Filter Raw Embeddings"
description: "Retrieve specific embeddings by `source_id` or `chunk_ids`."
---

## When to use it

Use this when you already know which embeddings you want – not similarity search, but direct lookup:

- **Inspect specific chunks** for debugging or validation
- **Reconstruct a full source** by fetching all its chunks
- **Cache management** – pull stored vectors back for downstream use

For similarity search by query vector, use [`POST /embeddings/search_raw_embeddings`](/api-reference/endpoint/search-raw-embeddings) instead.

## Endpoint

```text
POST /embeddings/filter_raw_embeddings
```

- **Auth:** Bearer token
- **Idempotency:** Read-only
- **Async:** No

## Example: Filter by source ID

<CodeGroup>

```bash cURL
curl -X POST 'https://api.hydradb.com/embeddings/filter_raw_embeddings' \
  -H "Authorization: Bearer <your_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "my_embeddings_tenant",
    "sub_tenant_id": "default",
    "source_id": "doc_001",
    "limit": 100
  }'
```

```typescript TypeScript
const results = await client.embeddings.filter({
  tenantId: "my_embeddings_tenant",
  subTenantId: "default",
  sourceId: "doc_001",
  limit: 100
});
```

```python Python (Sync)
results = client.embeddings.filter(
    tenant_id="my_embeddings_tenant",
    sub_tenant_id="default",
    source_id="doc_001",
    limit=100,
)
```

</CodeGroup>

## Example: Filter by chunk IDs

<CodeGroup>

```bash cURL
curl -X POST 'https://api.hydradb.com/embeddings/filter_raw_embeddings' \
  -H "Authorization: Bearer <your_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "my_embeddings_tenant",
    "sub_tenant_id": "default",
    "chunk_ids": ["doc_001_chunk_0", "doc_002_chunk_3"]
  }'
```

```typescript TypeScript
const results = await client.embeddings.filter({
  tenantId: "my_embeddings_tenant",
  subTenantId: "default",
  chunkIds: ["doc_001_chunk_0", "doc_002_chunk_3"]
});
```

```python Python (Sync)
results = client.embeddings.filter(
    tenant_id="my_embeddings_tenant",
    sub_tenant_id="default",
    chunk_ids=["doc_001_chunk_0", "doc_002_chunk_3"],
)
```

</CodeGroup>

## Request parameters

| Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `tenant_id` | string | Yes | – | The embeddings tenant. |
| `sub_tenant_id` | string | Yes | – | Sub-tenant scope. |
| `source_id` | string | One of | – | Filter all chunks belonging to this source. **Mutually exclusive with `chunk_ids`.** |
| `chunk_ids` | string[] | One of | – | Filter specific chunks by ID. **Mutually exclusive with `source_id`.** |
| `limit` | integer | No | `100` | Maximum results (1–10,000). |
| `output_fields` | string[] | No | – | Fields to include in results. Defaults to `chunk_id`, `source_id`, `metadata`. |

You must provide **exactly one** of `source_id` or `chunk_ids`. Sending both returns `400 INVALID_PARAMETERS`.

## Response

```json
[
  {
    "source_id": "doc_001",
    "embedding": {
      "chunk_id": "doc_001_chunk_0",
      "embedding": [0.123, -0.045, 0.789]
    },
    "score": 0.0,
    "distance": 0.0,
    "metadata": { "category": "engineering" }
  }
]
```

The shape matches [`/embeddings/search_raw_embeddings`](/api-reference/endpoint/search-raw-embeddings#response). For filter operations, `score` and `distance` are `0.0` (no ranking applied).

## Behavior notes

<Info>
**This is not similarity search.** This endpoint returns exact matches by ID. Use [`POST /embeddings/search_raw_embeddings`](/api-reference/endpoint/search-raw-embeddings) when you want vector similarity ranking.
</Info>

<Info>
**No partial parameter sets.** Both `source_id` and `chunk_ids` cannot be `null` – you must specify one.
</Info>

## Related endpoints

- **Similarity search:** [Search raw embeddings](/api-reference/endpoint/search-raw-embeddings)
- **Insert vectors:** [Insert raw embeddings](/api-reference/endpoint/insert-raw-embeddings)
- **Delete:** [Delete raw embeddings](/api-reference/endpoint/delete-raw-embeddings)

## Errors

Common codes: `400 INVALID_PARAMETERS` (mutual exclusion violation), `404 TENANT_NOT_FOUND`, `422 VALIDATION_ERROR`. See [Error Responses](/api-reference/error-responses) for the full list.
