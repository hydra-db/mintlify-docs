---
title: "Graph Relations By Source ID"
description: "Retrieve all entity relationships extracted from a single source."
---

## When to use it

- **Debugging graph construction** – verify HydraDB extracted the relationships you expected
- **Visualizations** – build entity graphs in admin UIs
- **Audit** – confirm what entities and connections exist for a specific document

This endpoint returns the raw triplets (subject → predicate → object) that HydraDB extracted from a single source.

## Endpoint

```
GET /list/graph_relations_by_id
```

- **Auth:** Bearer token
- **Idempotency:** Read-only
- **Async:** No

## Example

<Tabs>
  <Tab title="cURL">
    ```bash
    curl 'https://api.hydradb.com/list/graph_relations_by_id?source_id=doc_12345&tenant_id=my_first_tenant&limit=100' \
      -H "Authorization: Bearer <your_api_key>"
    ```
  </Tab>
  <Tab title="TypeScript">
    ```ts
    const response = await client.fetch.graphRelationsBySourceId({
      sourceId: "doc_12345",
      tenantId: "my_first_tenant",
      limit: 100
    });
    ```
  </Tab>
  <Tab title="Python (Sync)">
    ```python
    response = client.fetch.graph_relations_by_source_id(
        source_id="doc_12345",
        tenant_id="my_first_tenant",
        limit=100,
    )
    ```
  </Tab>
</Tabs>

## Query parameters

| Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `source_id` | string | Yes | – | The source to fetch relations for. |
| `tenant_id` | string | Yes | – | The tenant the source belongs to. |
| `sub_tenant_id` | string | No | default | Sub-tenant scope. |
| `is_memory` | boolean | No | `false` | Set `true` to fetch relations for a memory; `false` for knowledge. |
| `limit` | integer | No | `250` | Maximum number of relations to return. |

## Response

```json
{
  "success": true,
  "message": "Relations retrieved successfully",
  "relations": [
    {
      "source": {
        "name": "Pricing Strategy",
        "type": "PROJECT",
        "entity_id": "ent_abc123",
        "namespace": "default"
      },
      "target": {
        "name": "Product Team",
        "type": "ORGANIZATION",
        "entity_id": "ent_def456",
        "namespace": "default"
      },
      "relations": [
        {
          "canonical_predicate": "OWNED_BY",
          "raw_predicate": "is owned by",
          "context": "The Q4 pricing strategy was developed and is owned by the Product Team...",
          "confidence": 0.92,
          "relationship_id": "rel_xyz789",
          "chunk_id": "chunk_001"
        }
      ],
      "chunk_id": "chunk_001"
    }
  ]
}
```

| Field | Description |
|---|---|
| `relations[]` | Array of triplets extracted from this source. |
| `relations[].source` | Subject entity. |
| `relations[].target` | Object entity. |
| `relations[].relations[]` | Evidence for the relationship. Multiple entries mean the same connection appears in multiple chunks. |
| `relations[].relations[].canonical_predicate` | Normalized relationship type (e.g., `OWNED_BY`, `REPORTS_TO`, `WORKS_FOR`). |
| `relations[].relations[].raw_predicate` | The original phrasing extracted from the text. |
| `relations[].relations[].context` | Surrounding context for the relationship. |
| `relations[].relations[].confidence` | Extraction confidence (0.0–1.0). |
| `relations[].chunk_id` | The chunk this triplet came from. |

## Entity types

The `type` field on each entity is normalized at extraction. Common types:

| Type | Examples |
|---|---|
| `PERSON` | Named individuals |
| `ORGANIZATION` | Companies, teams, departments |
| `PROJECT` | Initiatives, products, releases |
| `PRODUCT` | Specific products or services |
| `ERROR_CODE` | Error identifiers |
| `LOCATION` | Geographic entities |

The full list depends on what HydraDB extracted from your specific content.

## Behavior notes

<Info>
**No relations doesn't mean no graph.** A source might be successfully indexed without producing extractable triplets (very short content, structured data, or content without clear entity-relationship language). Use this endpoint to confirm what was actually extracted.
</Info>

<Info>
**Memories vs knowledge.** Set `is_memory: true` to look up relations for a memory item. The default (`false`) targets knowledge sources.
</Info>

## Related endpoints

- **Browse sources first:** [List data](/api-reference/endpoint/list-data) – find the `source_id` you want to inspect
- **Retrieve content:** [Fetch content](/api-reference/endpoint/fetch-content) – get the original file or text
- **Use in recall:** [Full recall](/api-reference/endpoint/full-recall) with `graph_context: true` – see graph context applied to a query

## Errors

Common codes: `400 INVALID_PARAMETERS`, `404 SOURCE_NOT_FOUND`, `422 VALIDATION_ERROR`. See [Error Responses](/api-reference/error-responses) for the full list.

Read more: [Essentials → Context Graphs](/essentials/context-graphs)
