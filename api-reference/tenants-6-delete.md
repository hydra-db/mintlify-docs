---
title: "Delete Tenant"
description: "Permanently delete a tenant and all its data."
---

## When to use it

- **Offboarding** – a customer has churned and you need to remove their data
- **Environment teardown** – delete test or staging tenants
- **Compliance** – right-to-deletion requests under GDPR or similar

<Warning>
**This operation is irreversible.** Deleting a tenant permanently removes:

- Graph database storage
- Vector store collections (knowledge and memory)
- Source documents
- Chunk data
- Tenant metadata schema
- Tenant ID mapping

This data cannot be recovered.
</Warning>

## Endpoint

```
DELETE /tenants/delete
```

- **Auth:** Bearer token
- **Idempotency:** Re-sending for a deleted tenant returns `404 TENANT_NOT_FOUND`
- **Async:** Yes – returns `deletion_scheduled`. Cleanup runs in the background.

## Example

<Tabs>
  <Tab title="cURL">
    ```bash
    curl -X DELETE 'https://api.hydradb.com/tenants/delete?tenant_id=my_first_tenant' \
      -H "Authorization: Bearer <your_api_key>"
    ```
  </Tab>
  <Tab title="TypeScript">
    ```ts
    const response = await client.tenant.deleteTenant({
      tenantId: "my_first_tenant"
    });
    ```
  </Tab>
  <Tab title="Python (Sync)">
    ```python
    response = client.tenant.delete_tenant(tenant_id="my_first_tenant")
    ```
  </Tab>
</Tabs>

## Query parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `tenant_id` | string | Yes | The tenant to delete. |

## Response

```json
{
  "tenant_id": "my_first_tenant",
  "status": "deletion_scheduled",
  "message": "Tenant deregistered. Background cleanup is in progress."
}
```

| Field | Description |
|---|---|
| `tenant_id` | The tenant scheduled for deletion. |
| `status` | Always `deletion_scheduled`. Cleanup is asynchronous. |
| `message` | Human-readable status. |

## Behavior notes

<Info>
**Cleanup is asynchronous.** Deletion runs in the background and is typically complete within a few minutes. Treat deletion as eventually consistent.
</Info>

<Info>
**Re-creation.** Once deletion is scheduled, re-creating a tenant with the same `tenant_id` may fail until cleanup completes. If you need to re-create immediately, use a different `tenant_id`.
</Info>

## Related endpoints

- **Before this:** [List data](/api-reference/endpoint/list-data) – confirm what's stored before deleting
- **Alternative:** [Delete knowledge](/api-reference/endpoint/delete-knowledge) · [Delete memory](/api-reference/endpoint/delete-memory) – remove individual items instead of the whole tenant

## Errors

Common codes: `400 INVALID_PARAMETERS`, `404 TENANT_NOT_FOUND`. See [Error Responses](/api-reference/error-responses) for the full list.
