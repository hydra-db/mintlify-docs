---
title: "Delete Raw Embeddings"
description: "Delete embeddings by `source_id` or `chunk_ids`."
---

## When to use it

- **Cleanup** – remove vectors for sources that no longer exist in your system of record
- **Reprocessing** – delete then re-insert after upgrading your embedding model
- **Right-to-deletion** – fulfill privacy requests for specific items

For deleting an entire embeddings tenant, use [`DELETE /tenants/delete`](/api-reference/endpoint/delete-tenant).

<Warning>
**This operation is irreversible.** Deleted embeddings cannot be recovered.
</Warning>

## Endpoint

```text
DELETE /embeddings/delete_raw_embeddings
```

- **Auth:** Bearer token
- **Idempotency:** Re-sending for already-deleted items returns `delete_count: 0`
- **Async:** No

## Example: Delete by source ID

<CodeGroup>

```bash cURL
curl -X DELETE 'https://api.hydradb.com/embeddings/delete_raw_embeddings?tenant_id=my_embeddings_tenant&source_id=doc_001' \
  -H "Authorization: Bearer <your_api_key>"
```

```typescript TypeScript
const response = await client.embeddings.delete({
  tenantId: "my_embeddings_tenant",
  sourceId: "doc_001"
});
```

```python Python (Sync)
response = client.embeddings.delete(
    tenant_id="my_embeddings_tenant",
    source_id="doc_001",
)
```

</CodeGroup>

## Example: Delete by chunk IDs

<CodeGroup>

```bash cURL
curl -X DELETE 'https://api.hydradb.com/embeddings/delete_raw_embeddings?tenant_id=my_embeddings_tenant&chunk_ids=doc_001_chunk_0&chunk_ids=doc_002_chunk_3' \
  -H "Authorization: Bearer <your_api_key>"
```

```typescript TypeScript
const response = await client.embeddings.delete({
  tenantId: "my_embeddings_tenant",
  chunkIds: ["doc_001_chunk_0", "doc_002_chunk_3"]
});
```

```python Python (Sync)
response = client.embeddings.delete(
    tenant_id="my_embeddings_tenant",
    chunk_ids=["doc_001_chunk_0", "doc_002_chunk_3"],
)
```

</CodeGroup>

## Query parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `tenant_id` | string | Yes | The embeddings tenant. |
| `source_id` | string | One of | Delete all chunks belonging to this source. **Mutually exclusive with `chunk_ids`.** |
| `chunk_ids` | string[] | One of | Delete specific chunks. **Mutually exclusive with `source_id`.** |
| `sub_tenant_id` | string | No | Sub-tenant scope. If omitted, the default sub-tenant is used. |

You must provide **exactly one** of `source_id` or `chunk_ids`.

## Response

```json
{
  "delete_count": 5,
  "success": true,
  "error": null
}
```

| Field | Description |
|---|---|
| `delete_count` | Number of chunks deleted. |
| `success` | `true` if the operation succeeded. |
| `error` | Error message if `success: false`. |

## Behavior notes

<Info>
**No partial parameter sets.** Both `source_id` and `chunk_ids` cannot be `null` – you must specify one.
</Info>

<Info>
**Deleting by source removes all its chunks.** When `source_id` is provided, every chunk associated with that source is deleted in a single operation.
</Info>

## Related endpoints

- **Insert vectors:** [Insert raw embeddings](/api-reference/endpoint/insert-raw-embeddings)
- **Search:** [Search raw embeddings](/api-reference/endpoint/search-raw-embeddings)
- **Look up by ID:** [Filter raw embeddings](/api-reference/endpoint/filter-raw-embeddings)
- **Delete an entire tenant:** [Delete tenant](/api-reference/endpoint/delete-tenant)

## Errors

Common codes: `400 INVALID_PARAMETERS` (mutual exclusion violation), `404 TENANT_NOT_FOUND`. See [Error Responses](/api-reference/error-responses) for the full list.
