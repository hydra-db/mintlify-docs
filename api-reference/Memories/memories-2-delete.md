---
title: "Delete Memory"
description: "Permanently delete a single memory by its ID."
---

## When to use it

- **Right-to-deletion** – remove a specific user's memory upon request
- **Cleanup** – delete outdated or incorrect memories
- **Privacy controls** – let end users delete their own data through your app

To remove documents (knowledge) instead of user memories, use [`POST /knowledge/delete_knowledge`](/api-reference/endpoint/delete-knowledge).

<Warning>
**This operation is irreversible.** Once deleted, the memory cannot be recovered.
</Warning>

## Endpoint

```text
DELETE /memories/delete_memory
```

- **Auth:** Bearer token
- **Idempotency:** Re-sending for an already-deleted memory returns `404`
- **Async:** No

## Example

<CodeGroup>

```bash cURL
curl -X DELETE 'https://api.hydradb.com/memories/delete_memory?tenant_id=my_first_tenant&memory_id=1d50e5cd7c196a2bbcc1a59b037b3a44' \
  -H "Authorization: Bearer <your_api_key>"
```

```typescript TypeScript
const response = await client.memories.delete({
  tenantId: "my_first_tenant",
  memoryId: "1d50e5cd7c196a2bbcc1a59b037b3a44"
});
```

```python Python (Sync)
response = client.memories.delete(
    tenant_id="my_first_tenant",
    memory_id="1d50e5cd7c196a2bbcc1a59b037b3a44",
)
```

</CodeGroup>

## Query parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `tenant_id` | string | Yes | The tenant the memory belongs to. |
| `memory_id` | string | Yes | The unique identifier of the memory to delete. This is the `source_id` returned when the memory was added. |
| `sub_tenant_id` | string | No | Sub-tenant scope. If omitted, the default sub-tenant is used. |

## Response

```json
{
  "success": true,
  "user_memory_deleted": true
}
```

| Field | Description |
|---|---|
| `success` | `true` if the deletion request was processed. |
| `user_memory_deleted` | `true` if the specific memory was found and deleted. |

## Related endpoints

- **Before this:** [List data](/api-reference/endpoint/list-data) (`kind: "memories"`) – browse memories before deleting
- **Alternative:** [Delete tenant](/api-reference/endpoint/delete-tenant) – delete all memories in a tenant at once

## Errors

Common codes: `400 INVALID_PARAMETERS`, `404 MEMORY_NOT_FOUND`. See [Error Responses](/api-reference/error-responses) for the full list.
