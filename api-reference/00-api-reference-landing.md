---
title: "API Reference"
description: "Complete reference for every HydraDB endpoint, organized by group."
---

## Quick links

- **New to HydraDB?** Start with the [Quickstart](/quickstart)
- **Authentication:** Every endpoint requires `Authorization: Bearer <your_api_key>`
- **Base URL:** `https://api.hydradb.com`
- **Errors:** See [Error Responses](/api-reference/error-responses)

## Endpoint groups

| Group | What it does | Pages |
|---|---|---|
| [Tenants](/api-reference/endpoint/tenants-overview) | Create, monitor, and manage isolated workspaces | 6 endpoints |
| [Ingestion](/api-reference/endpoint/ingestion-overview) | Upload knowledge sources (files, app content) | 2 endpoints |
| [Memories](/api-reference/endpoint/memories-overview) | Add and remove user-specific context | 2 endpoints |
| [Recall](/api-reference/endpoint/recall-overview) | Retrieve context with semantic or keyword search | 3 endpoints |
| [List](/api-reference/endpoint/list-overview) | Browse stored content and inspect graph relationships | 2 endpoints |
| [Fetch](/api-reference/endpoint/fetch-content) | Retrieve original file content | 1 endpoint |
| [Knowledge Deletion](/api-reference/endpoint/delete-knowledge) | Remove knowledge sources | 1 endpoint |
| [Embeddings](/api-reference/endpoint/embeddings-overview) | Bring-your-own-vector workflows | 4 endpoints |

## End-to-end lifecycle

```mermaid
flowchart LR
 A[Create Tenant] --> B[Wait for Provisioning]
 B --> C[Ingest Knowledge / Memories]
 C --> D[Verify Processing]
 D --> E[Recall Context]
 E --> F[Pass to LLM]
 D --> G[List / Fetch / Inspect]

 style A fill:#e8f4f8
 style C fill:#e8f4f8
 style E fill:#e8f8ea
 style F fill:#e8f8ea
 style G fill:#fff4e8
```

## Full endpoint inventory

### Tenants

| Endpoint | Method | Purpose |
|---|---|---|
| [`/tenants/create`](/api-reference/endpoint/create-tenant) | `POST` | Create a new isolated workspace |
| [`/tenants/infra/status`](/api-reference/endpoint/infra-status) | `GET` | Check provisioning readiness |
| [`/tenants/monitor`](/api-reference/endpoint/monitor-tenant) | `GET` | Get object counts and vector dimensions |
| [`/tenants/sub_tenant_ids`](/api-reference/endpoint/list-sub-tenant-ids) | `GET` | List active sub-tenants |
| [`/tenants/tenant_ids`](/api-reference/endpoint/list-tenant-ids) | `GET` | List all tenants for the organization |
| [`/tenants/delete`](/api-reference/endpoint/delete-tenant) | `DELETE` | Permanently delete a tenant |

### Ingestion

| Endpoint | Method | Purpose |
|---|---|---|
| [`/ingestion/upload_knowledge`](/api-reference/endpoint/upload-knowledge) | `POST` | Ingest files and/or app sources |
| [`/ingestion/verify_processing`](/api-reference/endpoint/verify-processing) | `POST` | Check processing status |

### Memories

| Endpoint | Method | Purpose |
|---|---|---|
| [`/memories/add_memory`](/api-reference/endpoint/add-memory) | `POST` | Ingest user memories |
| [`/memories/delete_memory`](/api-reference/endpoint/delete-memory) | `DELETE` | Delete a single memory |

### Recall

| Endpoint | Method | Purpose |
|---|---|---|
| [`/recall/full_recall`](/api-reference/endpoint/full-recall) | `POST` | Hybrid recall over knowledge |
| [`/recall/recall_preferences`](/api-reference/endpoint/recall-preferences) | `POST` | Hybrid recall over user memories |
| [`/recall/boolean_recall`](/api-reference/endpoint/boolean-recall) | `POST` | Exact-match full-text search |

### List

| Endpoint | Method | Purpose |
|---|---|---|
| [`/list/data`](/api-reference/endpoint/list-data) | `POST` | Paginated browse of knowledge or memories |
| [`/list/graph_relations_by_id`](/api-reference/endpoint/graph-relations) | `GET` | Inspect entity relationships for a source |

### Fetch

| Endpoint | Method | Purpose |
|---|---|---|
| [`/fetch/content`](/api-reference/endpoint/fetch-content) | `POST` | Retrieve original file content or presigned URL |

### Knowledge Deletion

| Endpoint | Method | Purpose |
|---|---|---|
| [`/knowledge/delete_knowledge`](/api-reference/endpoint/delete-knowledge) | `POST` | Bulk delete sources by IDs |

### Embeddings

| Endpoint | Method | Purpose |
|---|---|---|
| [`/embeddings/insert_raw_embeddings`](/api-reference/endpoint/insert-raw-embeddings) | `POST` | Insert pre-computed vectors |
| [`/embeddings/search_raw_embeddings`](/api-reference/endpoint/search-raw-embeddings) | `POST` | Vector similarity search |
| [`/embeddings/filter_raw_embeddings`](/api-reference/endpoint/filter-raw-embeddings) | `POST` | Retrieve embeddings by ID |
| [`/embeddings/delete_raw_embeddings`](/api-reference/endpoint/delete-raw-embeddings) | `DELETE` | Delete embeddings |

## Conventions

**Authentication.** Every endpoint requires `Authorization: Bearer <your_api_key>` in the request header. Get your key at [app.hydradb.com](https://app.hydradb.com).

**Tenant scoping.** Every endpoint requires a `tenant_id`. Most endpoints also accept an optional `sub_tenant_id` for finer-grained scoping. If omitted, the default sub-tenant is used.

**Async operations.** Tenant creation, deletion, and content ingestion are asynchronous. They return immediately after queuing. Use the relevant status endpoint to confirm completion before downstream operations.

**Pagination.** Listing endpoints (`/list/data`) return a `pagination` object with `page`, `page_size`, `total`, `total_pages`, `has_next`, and `has_previous`.

**Status codes.** Successful responses return `200` (or `202` for async accepts). Errors follow standard HTTP semantics:

| Code | Meaning |
|---|---|
| `200` | Success |
| `202` | Accepted (async operation queued) |
| `400` | Invalid parameters |
| `401` | Authentication required |
| `403` | Forbidden |
| `404` | Resource not found |
| `409` | Conflict (e.g., tenant already exists) |
| `422` | Validation error |
| `429` | Rate limit exceeded |
| `500` | Internal server error |
| `503` | Service unavailable |

See [Error Responses](/api-reference/error-responses) for response shapes and error codes.

## Next steps

- **Build something:** [Quickstart](/quickstart) walks through your first integration in five minutes
- **Understand the model:** [Core Concepts](/core-concepts) explains tenants, memories, recall, and metadata
- **Go deeper:** [Essentials](/essentials) covers each primitive in depth
- **Continue Building:** [Tenants](/tenants) for some of the most important endpoints
