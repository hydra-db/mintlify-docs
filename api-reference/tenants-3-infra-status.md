---
title: "Check Infra Status"
description: "Check whether a tenant's infrastructure is fully provisioned."
---

## When to use it

- **After tenant creation** – poll until provisioning completes
- **Diagnostics** – confirm the graph and vectorstore are healthy

## Endpoint

```
GET /tenants/infra/status
```

- **Auth:** Bearer token
- **Idempotency:** Read-only
- **Async:** No

## Example

<Tabs>
  <Tab title="cURL">
    ```bash
    curl 'https://api.hydradb.com/tenants/infra/status?tenant_id=my_first_tenant' \
      -H "Authorization: Bearer <your_api_key>"
    ```
  </Tab>
  <Tab title="TypeScript">
    ```ts
    const status = await client.tenant.getInfraStatus({
      tenantId: "my_first_tenant"
    });
    ```
  </Tab>
  <Tab title="Python (Sync)">
    ```python
    status = client.tenant.get_infra_status(tenant_id="my_first_tenant")
    ```
  </Tab>
</Tabs>

## Query parameters

| Name | Type | Required | Description |
|---|---|---|---|
| `tenant_id` | string | Yes | The tenant to inspect. |

## Response

```json
{
  "tenant_id": "my_first_tenant",
  "org_id": "free",
  "infra": {
    "scheduler_status": true,
    "graph_status": true,
    "vectorstore_status": [true, true]
  },
  "message": "Deployed infrastructure status"
}
```

| Field | Description |
|---|---|
| `tenant_id` | The tenant that was queried. |
| `org_id` | Organization identifier. |
| `infra.scheduler_status` | `true` when the ingestion scheduler is ready. |
| `infra.graph_status` | `true` when the graph database is ready. |
| `infra.vectorstore_status` | Array of two booleans. **Both must be `true`** before ingesting. |

## Polling pattern

After creating a tenant, poll this endpoint every few seconds until all statuses are `true`:

```python
import time

while True:
    status = client.tenant.get_infra_status(tenant_id="my_first_tenant")
    infra = status.infra
    if (infra.scheduler_status
        and infra.graph_status
        and all(infra.vectorstore_status)):
        break
    time.sleep(2)
```

Typical provisioning time: 10–30 seconds.

## Related endpoints

- **Before this:** [Create tenant](/api-reference/endpoint/create-tenant) – creation is asynchronous
- **After this:** [Upload knowledge](/api-reference/endpoint/upload-knowledge) · [Add memory](/api-reference/endpoint/add-memory)

## Errors

Common codes: `400 INVALID_PARAMETERS`, `404 TENANT_NOT_FOUND`. See [Error Responses](/api-reference/error-responses) for the full list.
