---
title: "Full Recall"
description: "Hybrid semantic + graph + metadata recall over knowledge sources."
---

## When to use it

`/recall/full_recall` is the primary endpoint for retrieving knowledge (documents, app sources). It runs a multi-stage pipeline that combines:

1. Metadata filtering (deterministic scoping)
2. Hybrid retrieval (semantic + keyword)
3. Graph traversal (entity relationships)
4. Personalized ranking (user, agent, task)

Use this for document-grounded queries: "What's in our Q4 contract?", "Find pricing decisions from the last sprint."

For user-specific memories (preferences, conversation history), use [`POST /recall/recall_preferences`](/api-reference/endpoint/recall-preferences) instead.

## Endpoint

```
POST /recall/full_recall
```

- **Auth:** Bearer token
- **Idempotency:** Read-only
- **Async:** No

## Example

<Tabs>
  <Tab title="cURL">
    ```bash
    curl -X POST 'https://api.hydradb.com/recall/full_recall' \
      -H "Authorization: Bearer <your_api_key>" \
      -H "Content-Type: application/json" \
      -d '{
        "tenant_id": "my_first_tenant",
        "query": "What are the pricing tiers?",
        "max_results": 5,
        "mode": "thinking",
        "graph_context": true,
        "metadata_filters": { "category": "pricing" }
      }'
    ```
  </Tab>
  <Tab title="TypeScript">
    ```ts
    const result = await client.recall.fullRecall({
      tenantId: "my_first_tenant",
      query: "What are the pricing tiers?",
      maxResults: 5,
      mode: "thinking",
      graphContext: true,
      metadataFilters: { category: "pricing" }
    });
    ```
  </Tab>
  <Tab title="Python (Sync)">
    ```python
    result = client.recall.full_recall(
        tenant_id="my_first_tenant",
        query="What are the pricing tiers?",
        max_results=5,
        mode="thinking",
        graph_context=True,
        metadata_filters={"category": "pricing"},
    )
    ```
  </Tab>
</Tabs>

## Request parameters

| Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `tenant_id` | string | Yes | – | The tenant to search. |
| `query` | string | Yes | – | Search terms or natural-language question. |
| `sub_tenant_id` | string | No | default | Sub-tenant scope. |
| `max_results` | integer | No | – | Maximum number of chunks to return. |
| `mode` | enum | No | `fast` | `fast` (single query, lower latency) or `thinking` (multi-query with reranking, higher quality). |
| `alpha` | number\|string | No | `0.8` | Hybrid weight: `0.0` = keyword, `1.0` = semantic, `"auto"` lets the system pick. |
| `recency_bias` | number | No | `0.0` | Preference for newer content (`0.0`–`1.0`). |
| `graph_context` | boolean | No | `false` | If `true`, include entity relationships in the response. |
| `search_forceful_relations` | boolean | No | `true` | In `thinking` mode, augment context with forcefully-related sources. |
| `additional_context` | string | No | – | Extra context to guide retrieval. |
| `metadata_filters` | object | No | – | Filter by metadata. See [Metadata filters](#metadata-filters). |

## Metadata filters

`metadata_filters` supports two filter scopes:

**Tenant-level filters** (top-level keys, matched against fields defined in `tenant_metadata_schema`):

```json
{ "metadata_filters": { "category": "engineering", "department": "R&D" } }
```

**Document-level filters** (nested under `document_metadata`, matched against per-document metadata):

```json
{ "metadata_filters": { "document_metadata": { "source": "account_plan" } } }
```

Both filter scopes can be combined in the same request. All filters use exact-match AND logic – every key must match for a chunk to be returned.

## Modes

| Mode | Latency | Quality | When to use |
|---|---|---|---|
| `fast` (default) | Lower | Standard | Real-time chat, autocomplete, simple lookups |
| `thinking` | Higher | Higher | Complex queries, multi-step reasoning, anything customer-facing where quality matters more than ms |

In `thinking` mode, HydraDB runs multiple sub-queries against the index and reranks results before returning.

## Response

```json
{
  "chunks": [
    {
      "chunk_uuid": "a1b2c3d4-e5f6-7890-1234-567890abcdef",
      "source_id": "doc_12345",
      "chunk_content": "Pricing tiers: $29/month Starter, $79/month Pro, $199/month Enterprise...",
      "source_title": "Q4 Pricing Strategy",
      "source_type": "file",
      "source_upload_time": "2025-09-15T10:00:00Z",
      "relevancy_score": 0.92,
      "document_metadata": { "author": "Product Team" },
      "tenant_metadata": { "category": "pricing" }
    }
  ],
  "sources": [
    {
      "id": "doc_12345",
      "title": "Q4 Pricing Strategy",
      "type": "file",
      "url": "https://...",
      "timestamp": "2025-09-15T10:00:00Z",
      "tenant_metadata": { "category": "pricing" },
      "document_metadata": { "author": "Product Team" }
    }
  ],
  "graph_context": {
    "query_paths": [
      {
        "triplets": [
          {
            "source": { "name": "Pricing Strategy", "type": "PROJECT" },
            "relation": { "canonical_predicate": "OWNED_BY" },
            "target": { "name": "Product Team", "type": "ORGANIZATION" }
          }
        ],
        "relevancy_score": 0.85
      }
    ],
    "chunk_relations": [],
    "chunk_id_to_group_ids": {}
  },
  "additional_context": {}
}
```

| Field | Description |
|---|---|
| `chunks` | Retrieved content ranked by relevance. |
| `chunks[].chunk_uuid` | Unique identifier for the chunk. |
| `chunks[].source_id` | The document this chunk came from. |
| `chunks[].chunk_content` | The actual text content. |
| `chunks[].relevancy_score` | Higher means more relevant. |
| `sources` | Deduplicated source documents corresponding to the returned chunks. |
| `graph_context` | Entity relationships extracted from your data. Populated only when `graph_context: true` in the request. |
| `graph_context.query_paths` | Relationship paths relevant to the query. |
| `graph_context.chunk_relations` | Relationships between returned chunks. |
| `additional_context` | Map of related chunks from forcefully-connected sources (only in `thinking` mode). |

<Info>
**`graph_context` populates only when:**
- You set `graph_context: true` in the request, **and**
- Your ingested content has linked relationships in the graph.

If either condition is missing, the fields return as empty arrays. See [Essentials → Context Graphs](/essentials/context-graphs).
</Info>

## Behavior notes

<Info>
**`thinking` mode is the personalized path.** The default `fast` mode uses a single retrieval pass. `thinking` mode adds query expansion, reranking, and forceful-relation context. For B2C apps where personalized recall matters, prefer `thinking`.
</Info>

## Related endpoints

- **For user memories instead:** [Recall preferences](/api-reference/endpoint/recall-preferences) – same parameters, targets the memory collection
- **For exact-match search:** [Boolean recall](/api-reference/endpoint/boolean-recall) – full-text search with AND/OR/PHRASE operators
- **For pre-computed vectors:** [Search raw embeddings](/api-reference/endpoint/search-raw-embeddings) – embeddings tenants only

## Errors

Common codes: `400 INVALID_PARAMETERS`, `404 TENANT_NOT_FOUND`, `422 VALIDATION_ERROR`, `429 RATE_LIMITED`. See [Error Responses](/api-reference/error-responses) for the full list.

Read more: [Essentials → Recall](/essentials/recall) · [Essentials → Context Graphs](/essentials/context-graphs)
