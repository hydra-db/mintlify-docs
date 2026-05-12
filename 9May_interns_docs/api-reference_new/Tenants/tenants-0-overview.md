---
title: "Tenants – Overview"
description: "Quick reference for all Tenants endpoints, their lifecycle, and when to call each."
---

## Lifecycle

```mermaid
flowchart LR
 A[Create Tenant] --> B[Poll Infra Status]
 B --> C{Ready?}
 C -- no --> B
 C -- yes --> D[Ingest Knowledge<br/>or Memories]
 D --> E[Recall]
 E --> D

 A -.observability.-> M[Monitor /<br/>List Tenants /<br/>List Sub-Tenants]
 D -.observability.-> M
 E -.observability.-> M

 A -.teardown.-> F[Delete Tenant]
 D -.teardown.-> F
 E -.teardown.-> F

 style A fill:#e8f4f8
 style B fill:#fff4e8
 style D fill:#e8f8ea
 style E fill:#e8f8ea
 style M fill:#f4f0ff
 style F fill:#ffe8e8
```

Blue = setup. Orange = async wait. Green = operational. Purple = observability. Red = destructive.

## Endpoint reference

| Endpoint | Method | Purpose | Async? |
|---|---|---|---|
| [`/tenants/create`](/api-reference/endpoint/create-tenant) | `POST` | Create a new isolated workspace | Yes |
| [`/tenants/infra/status`](/api-reference/endpoint/infra-status) | `GET` | Check provisioning readiness | No |
| [`/tenants/monitor`](/api-reference/endpoint/monitor-tenant) | `GET` | Get object counts and vector dimensions | No |
| [`/tenants/sub_tenant_ids`](/api-reference/endpoint/list-sub-tenant-ids) | `GET` | List active sub-tenants | No |
| [`/tenants/tenant_ids`](/api-reference/endpoint/list-tenant-ids) | `GET` | List all tenants for the organization | No |
| [`/tenants/delete`](/api-reference/endpoint/delete-tenant) | `DELETE` | Permanently remove a tenant | Yes |

## Typical call sequence

For a new tenant from scratch:

```
1. POST   /tenants/create              → returns "accepted"
2. GET    /tenants/infra/status (poll) → wait for all statuses true
3. POST   /ingestion/upload_knowledge  → start ingesting data
4. POST   /recall/full_recall          → query your data
5. GET    /tenants/monitor             → verify storage
```

For routine operations on an existing tenant:

```
GET /tenants/sub_tenant_ids → list active sub-tenants
GET /tenants/tenant_ids     → list all tenants in the org
GET /tenants/monitor        → check storage growth
```

For teardown:

```
DELETE /tenants/delete → schedule deletion (irreversible)
```

## Key concepts

**Tenant** – Top-level isolated workspace. One per organization in most cases.

**Sub-tenant** – Subdivision inside a tenant. Created automatically the first time a `sub_tenant_id` is used. No explicit creation step, no confirmation.

**Default sub-tenant** – Every tenant gets one at creation. API calls that omit `sub_tenant_id` target this default.

**Tenant metadata schema** – Immutable field definitions set at creation. Cannot be changed later.

**Embeddings tenants** – A specialized tenant variant (`is_embeddings_tenant: true`) for teams uploading pre-computed vectors. Uses a separate endpoint family at `/embeddings/*`.

## Related sections

- [Essentials → Multi-Tenant Support](/essentials/multi-tenant) – concepts, isolation guarantees, use cases
- [Essentials → Metadata](/essentials/metadata) – tenant and document metadata
- [API Reference → Ingestion](/api-reference/endpoint/upload-knowledge) – after tenant setup, start ingesting
- [Create Tenant](/api-reference/endpoint/tenants-1-create) – Setup your first tenant