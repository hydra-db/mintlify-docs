---
title: "Recall Preferences"
description: "Hybrid recall over user memories – preferences, conversation history, inline notes."
---

## When to use it

`/recall/recall_preferences` retrieves user-specific context that was ingested via [`POST /memories/add_memory`](/api-reference/endpoint/add-memory). Use it when your agent needs to:

- Personalize responses based on user preferences
- Recall past conversations with the user
- Surface user notes or settings relevant to the current task

The request and response shapes are **identical to [`POST /recall/full_recall`](/api-reference/endpoint/full-recall)** – the only difference is which collection is searched. Use `full_recall` for documents and knowledge, `recall_preferences` for user memories.

## Endpoint

```
POST /recall/recall_preferences
```

- **Auth:** Bearer token
- **Idempotency:** Read-only
- **Async:** No

## Example

<Tabs>
  <Tab title="cURL">
    ```bash
    curl -X POST 'https://api.hydradb.com/recall/recall_preferences' \
      -H "Authorization: Bearer <your_api_key>" \
      -H "Content-Type: application/json" \
      -d '{
        "tenant_id": "my_first_tenant",
        "sub_tenant_id": "user_alex",
        "query": "What are my display preferences?",
        "max_results": 3,
        "mode": "thinking"
      }'
    ```
  </Tab>
  <Tab title="TypeScript">
    ```ts
    const result = await client.recall.recallPreferences({
      tenantId: "my_first_tenant",
      subTenantId: "user_alex",
      query: "What are my display preferences?",
      maxResults: 3,
      mode: "thinking"
    });
    ```
  </Tab>
  <Tab title="Python (Sync)">
    ```python
    result = client.recall.recall_preferences(
        tenant_id="my_first_tenant",
        sub_tenant_id="user_alex",
        query="What are my display preferences?",
        max_results=3,
        mode="thinking",
    )
    ```
  </Tab>
</Tabs>

## Request parameters

Identical to [`POST /recall/full_recall`](/api-reference/endpoint/full-recall#request-parameters). The most important ones for memory recall:

| Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `tenant_id` | string | Yes | – | The tenant to search. |
| `query` | string | Yes | – | Search terms or natural-language question. |
| `sub_tenant_id` | string | No | default | **Critical for B2C apps** – pass the end user's sub-tenant to scope the search to that user's memories. |
| `max_results` | integer | No | – | Maximum number of memory chunks to return. |
| `mode` | enum | No | `fast` | `fast` or `thinking`. Use `thinking` for richer personalization. |
| `alpha` | number\|string | No | `0.8` | Hybrid weight: `0.0` = keyword, `1.0` = semantic, `"auto"`. |
| `recency_bias` | number | No | `0.0` | Recency preference. Often more useful on memories than knowledge. |
| `graph_context` | boolean | No | `false` | Include entity relationships in the response. |
| `metadata_filters` | object | No | – | Filter by metadata. See note below. |

## Sub-tenant scoping

For B2C apps, sub-tenants typically map to end users:

```python
# When user "alex" interacts with your agent:
result = client.recall.recall_preferences(
    tenant_id="my_first_tenant",
    sub_tenant_id="user_alex",     # scope to alex's memories only
    query="what are my preferences for technical depth?",
)
```

Without `sub_tenant_id`, the search runs against the default sub-tenant – which is rarely what you want for personalized recall.

## Metadata filters on memories

The `metadata_filters` parameter targets the `metadata` and `additional_metadata` fields that were attached to memories at ingestion time. Same filter syntax as `full_recall`:

```json
{
  "metadata_filters": {
    "team": "engineering",
    "additional_metadata": { "source": "onboarding" }
  }
}
```

## Response

Same shape as [`POST /recall/full_recall`](/api-reference/endpoint/full-recall#response). The `chunks[]` array contains memory chunks; `sources[]` contains the originating memory items.

```json
{
  "chunks": [
    {
      "chunk_uuid": "...",
      "source_id": "1d50e5cd7c196a2bbcc1a59b037b3a44",
      "chunk_content": "User prefers detailed technical explanations and dark mode",
      "source_title": "User Preferences",
      "relevancy_score": 0.94,
      "document_metadata": { "source": "onboarding" },
      "tenant_metadata": { "team": "engineering" }
    }
  ],
  "sources": [...],
  "graph_context": {...},
  "additional_context": {}
}
```

## Behavior notes

<Info>
**Pass `sub_tenant_id` explicitly for B2C apps.** Personalized recall only works when memories are scoped to the right user. Omitting `sub_tenant_id` falls back to the default sub-tenant.
</Info>

<Info>
**`thinking` mode is especially valuable for memories.** Memory recall benefits from query expansion because user preferences are often phrased indirectly ("I like X" → recall "user prefers X").
</Info>

## Related endpoints

- **For documents and knowledge:** [Full recall](/api-reference/endpoint/full-recall) – same shape, targets the knowledge collection
- **Ingest memories:** [Add memory](/api-reference/endpoint/add-memory) – store memories before they can be recalled
- **For exact-match memory search:** [Boolean recall](/api-reference/endpoint/boolean-recall) – set `search_mode: "memories"`

## Errors

Common codes: `400 INVALID_PARAMETERS`, `404 TENANT_NOT_FOUND`, `422 VALIDATION_ERROR`, `429 RATE_LIMITED`. See [Error Responses](/api-reference/error-responses) for the full list.

Read more: [Essentials → Memories](/essentials/memories) · [Essentials → Recall](/essentials/recall)
