---
name: hydradb-model-context
description: Design HydraDB v2 context before writing code. Use for choosing knowledge versus memory, database and collection boundaries, metadata schemas, IDs, and retrieval scope.
license: Apache-2.0
compatibility: HydraDB API v2
metadata:
  product: HydraDB
  task: context-modeling
---

# Model HydraDB context

Design the retrieval boundary first. Most HydraDB failures that look like search problems are actually modeling or scope problems.

## Decide in this order

1. What evidence should the agent retrieve?
2. Is that evidence shared knowledge, scoped memory, or both?
3. Which database owns it?
4. Which collection partitions it?
5. Which exact constraints belong in schema-backed `metadata`?
6. Which display or bookkeeping fields belong in `additional_metadata`?
7. Which stable ID makes retries safe?

State these choices before generating integration code.

## Choose the content type

| Need | Store as | Query with |
|---|---|---|
| Policies, manuals, tickets, code, shared app content | `knowledge` | `type="knowledge"` |
| Preferences, observations, conversation-derived facts | `memory` | `type="memory"` |
| A grounded answer personalized to a user | Both | `type="all"` |

Knowledge answers “what does the source say?” Memory answers “what should this agent remember?” Do not turn every chat message into shared knowledge, and do not copy authoritative documents into memory.

## Choose the scope

| Layer | Meaning | Good boundary |
|---|---|---|
| `database` | Isolated workspace and metadata schema | Product, environment, or security boundary |
| `collection` | Retrieval partition inside a database | User, team, workspace, or project |
| `metadata_filters` | Exact constraints inside selected partitions | Status, region, department, language |

Use a database for a hard isolation boundary. Use a collection for the unit you commonly write and query together. Do not use metadata filters as a substitute for user or workspace partitioning.

For shared knowledge plus personal memory, a common model is:

```text
database: product_production
knowledge collection: shared
memory collection: user_<stable_user_id>
```

Query the relevant collection or use the `collections` field when intentionally searching more than one. Never silently fan out across all users.

## Put metadata in the correct lane

| Metadata | Shape | Filter path | Use it for |
|---|---|---|---|
| `metadata` | Object for knowledge; JSON-encoded string for a memory item | Top-level keys in `metadata_filters` | Hot, repeated exact filters |
| `additional_metadata` | Free-form object | `metadata_filters.additional_metadata` | Citations, source IDs, UI fields, occasional filters |

Declare hot fields when creating the database:

```python
client.databases.create(
    database="support_production",
    database_metadata_schema=[
        {
            "name": "department",
            "data_type": "VARCHAR",
            "enable_match": True,
            "max_length": 128,
        },
        {
            "name": "published",
            "data_type": "BOOL",
            "enable_match": True,
        },
    ],
)
```

The TypeScript equivalent uses `databaseMetadataSchema`, `dataType`, `enableMatch`, and `maxLength`.

For `type="memory"`, the outer `memories` multipart field is JSON-stringified and each item's schema-backed `metadata` is currently another JSON-encoded string. Keep its `additional_metadata` as an object. Knowledge `document_metadata` and `app_knowledge` keep `metadata` as an object.

Use exact values in `metadata_filters`. Range, contains, and fuzzy intent belong in the natural-language query, a semantic metadata field, a reranker, or application code.

## Make ingestion idempotent

Assign stable, deterministic IDs from the source system:

```text
document: github:<repo>:blob:<commit>:<path>
message:  slack:<workspace>:<channel>:<timestamp>
memory:   user:<user_id>:preference:<preference_key>
```

Reusing an ID with `upsert=true` turns retry into update instead of duplication. Do not use a random ID when a source already has a durable identity.

## Design the query before the schema is final

Write at least three retrieval tests:

1. A query that should return a known item.
2. A nearby query that should not cross the collection boundary.
3. A filtered query that should exclude a known item.

For GraphRAG, add a multi-hop question whose answer needs a relationship path, then inspect `graph_context.query_paths`. If no test needs a field, reconsider storing or indexing it.

## Reject these designs

- One database per end user when collections provide the needed partition.
- One global collection containing private memories from every user.
- Random IDs on every retry.
- User identity stored only in free-form metadata.
- Undeclared top-level metadata filters.
- High-cardinality UI bookkeeping fields added to the hot schema.
- An answer pipeline that cannot say “no supporting context.”

## Handoff

Once the model is explicit, use `hydradb-ingest-context`, then `hydradb-query-context`. Verify uncommon schema options against the [metadata guide](https://docs.hydradb.com/essentials/v2/metadata) and [create database reference](https://docs.hydradb.com/api-reference/v2/endpoint/create-tenant).
