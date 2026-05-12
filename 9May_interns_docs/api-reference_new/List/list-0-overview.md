---
title: "List – Overview"
description: "Browse stored knowledge and memories, and inspect their graph relationships."
---

## Endpoint reference

| Endpoint | Method | Purpose |
|---|---|---|
| [`/list/data`](/api-reference/endpoint/list-data) | `POST` | Paginated listing of knowledge or memories with filters |
| [`/list/graph_relations_by_id`](/api-reference/endpoint/graph-relations) | `GET` | Inspect entity relationships for a single source |

## When to use which

| Goal | Endpoint |
|---|---|
| Browse all sources in a tenant | `POST /list/data` with `kind: "knowledge"` |
| Browse all memories for a user | `POST /list/data` with `kind: "memories"` and `sub_tenant_id` set |
| Filter by metadata | `POST /list/data` with `filters` |
| Look up specific items by ID | `POST /list/data` with `source_ids` |
| See entity relationships for one source | `GET /list/graph_relations_by_id` |

## Typical patterns

### Admin dashboard – paginate knowledge

```python
page = 1
while True:
    result = client.fetch.list_data(
        tenant_id="acme",
        kind="knowledge",
        page=page,
        page_size=50,
        include_fields=["title", "type", "url", "timestamp"],
    )
    for source in result.sources:
        render(source)
    if not result.pagination.has_next:
        break
    page += 1
```

### Verify metadata after ingestion

```python
result = client.fetch.list_data(
    tenant_id="acme",
    kind="knowledge",
    source_ids=["doc_12345"],
)
print(result.sources[0].tenant_metadata)
print(result.sources[0].document_metadata)
```

### Inspect graph relations for debugging

```python
relations = client.fetch.graph_relations_by_source_id(
    source_id="doc_12345",
    tenant_id="acme",
)
for triplet in relations.relations:
    print(f"{triplet.source.name} → {triplet.relations[0].canonical_predicate} → {triplet.target.name}")
```

## Related sections

- [API Reference → Fetch](/api-reference/endpoint/fetch-content) – retrieve original file content
- [Essentials → Metadata](/essentials/metadata) – tenant vs document metadata
- [Essentials → Context Graphs](/essentials/context-graphs) – how graph relationships are extracted
