---
title: "Embeddings – Overview"
description: "A parallel endpoint family for teams that supply their own pre-computed vectors."
---

## When to use the Embeddings track

Use the `/embeddings/*` endpoints when:

- You generate embeddings with a **fine-tuned or proprietary model** and don't want HydraDB to re-embed
- You're **migrating** from an existing vector database and need to bring vectors with you
- You need **dimension control** that doesn't match HydraDB's defaults

For most use cases, use the standard ingestion path (`/ingestion/upload_knowledge` or `/memories/add_memory`). HydraDB handles parsing, chunking, and embedding for you.

## Embeddings tenants

The `/embeddings/*` endpoints operate on **embeddings tenants** – tenants created with `is_embeddings_tenant: true`. To set one up:

```bash
curl -X POST 'https://api.hydradb.com/tenants/create' \
  -H "Authorization: Bearer <your_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "my_embeddings_tenant",
    "is_embeddings_tenant": true,
    "embeddings_dimension": 1536
  }'
```

The `embeddings_dimension` must match your model's output dimension.

See [Create tenant](/api-reference/endpoint/create-tenant) for full options.

## Endpoint reference

| Endpoint | Method | Purpose |
|---|---|---|
| [`/embeddings/insert_raw_embeddings`](/api-reference/endpoint/insert-raw-embeddings) | `POST` | Insert pre-computed embedding vectors |
| [`/embeddings/search_raw_embeddings`](/api-reference/endpoint/search-raw-embeddings) | `POST` | Vector similarity search |
| [`/embeddings/filter_raw_embeddings`](/api-reference/endpoint/filter-raw-embeddings) | `POST` | Retrieve specific embeddings by ID |
| [`/embeddings/delete_raw_embeddings`](/api-reference/endpoint/delete-raw-embeddings) | `DELETE` | Delete embeddings by source ID or chunk IDs |

## Data model

The embeddings track uses a different structure from the standard ingestion path:

```
Source (source_id)
└── Embeddings
    ├── Chunk (chunk_id) → embedding vector
    ├── Chunk (chunk_id) → embedding vector
    └── ...
```

Each **source** groups one or more **chunks**. Each chunk has its own `chunk_id` and embedding vector. Metadata is attached at the source level.

## Typical lifecycle

```
1. POST /tenants/create with is_embeddings_tenant: true     → create embeddings tenant
2. POST /embeddings/insert_raw_embeddings                   → upload vectors
3. POST /embeddings/search_raw_embeddings                   → similarity search
4. POST /embeddings/filter_raw_embeddings                   → look up specific chunks
5. DELETE /embeddings/delete_raw_embeddings                 → remove embeddings
```

## Embeddings vs standard ingestion

| | Standard ingestion | Embeddings track |
|---|---|---|
| **Tenant type** | Default | `is_embeddings_tenant: true` |
| **Input** | Files, text, app sources | Pre-computed vectors |
| **HydraDB embeds** | Yes | No |
| **HydraDB parses** | Yes | No |
| **Graph extraction** | Yes | No |
| **Recall** | `/recall/*` | `/embeddings/search_raw_embeddings` |
| **Best for** | Most use cases | Fine-tuned models, migrations |

The two tracks do not interoperate – an embeddings tenant cannot use `/recall/full_recall`, and a standard tenant cannot use `/embeddings/search_raw_embeddings`.

## Related sections

- [Essentials → Custom Embeddings](/essentials/custom-embeddings) – conceptual overview
- [API Reference → Tenants](/api-reference/endpoint/create-tenant) – create an embeddings tenant
