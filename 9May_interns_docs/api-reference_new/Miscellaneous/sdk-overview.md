---
title: "SDKs – Node and Python"
description: "Official TypeScript/Node.js and Python SDKs for the HydraDB platform."
---

## Installation

<Tabs>
  <Tab title="TypeScript / Node.js">
    ```bash
    npm i @hydra_db/node
    ```
  </Tab>
  <Tab title="Python">
    ```bash
    pip install hydra-db-python
    ```
  </Tab>
</Tabs>

## Client setup

<Tabs>
  <Tab title="TypeScript">
    ```ts
    import { HydraDBClient } from "@hydra_db/node";

    const client = new HydraDBClient({
      token: process.env.HYDRADB_API_KEY,
    });
    ```
  </Tab>
  <Tab title="Python (Sync)">
    ```python
    import os
    from hydra_db import HydraDB

    client = HydraDB(token=os.environ["HYDRADB_API_KEY"])
    ```
  </Tab>
  <Tab title="Python (Async)">
    ```python
    import os
    from hydra_db import AsyncHydraDB

    async_client = AsyncHydraDB(token=os.environ["HYDRADB_API_KEY"])
    ```
  </Tab>
</Tabs>

<Info>
**Python:** Both synchronous (`HydraDB`) and asynchronous (`AsyncHydraDB`) clients are available. They share an identical surface – choose based on your application's concurrency model.
</Info>

## Naming conventions

The REST API uses **snake_case** for all request and response fields. The SDKs adapt this to native idioms:

| | Field naming | Example |
|---|---|---|
| **Raw HTTP / cURL** | snake_case | `tenant_id`, `sub_tenant_id` |
| **TypeScript SDK** | camelCase | `tenantId`, `subTenantId` |
| **Python SDK** | snake_case | `tenant_id`, `sub_tenant_id` |

The TypeScript SDK transparently maps camelCase parameters to snake_case on the wire. You can rely on your IDE's autocomplete and type checking – if a field is optional in the API, it's optional in the SDK.

## SDK method structure

SDK methods are grouped by capability, not by URL path. The mapping is mostly intuitive but **a few groups span multiple URL prefixes**:

| URL prefix | SDK group | Why |
|---|---|---|
| `/tenants/*` | `client.tenant` | Tenant lifecycle |
| `/ingestion/*` | `client.upload` | Content upload + verification |
| `/memories/*` | `client.upload` | Memories share the upload group |
| `/recall/*` | `client.recall` | All retrieval modes |
| `/list/*`, `/fetch/*` | `client.fetch` | Browse and retrieve |
| `/knowledge/*` | `client.data` | Bulk delete |
| `/embeddings/*` | `client.embeddings` | Bring-your-own-vector workflows |

## Method reference

Method names map to the URL by removing the leading slash and replacing path separators:

### Tenants

| Method | Endpoint |
|---|---|
| `client.tenant.create()` | `POST /tenants/create` |
| `client.tenant.get_infra_status()` | `GET /tenants/infra/status` |
| `client.tenant.monitor()` | `GET /tenants/monitor` |
| `client.tenant.get_sub_tenant_ids()` | `GET /tenants/sub_tenant_ids` |
| `client.tenant.get_tenant_ids()` | `GET /tenants/tenant_ids` |
| `client.tenant.delete_tenant()` | `DELETE /tenants/delete` |

### Ingestion + Memories (under `upload`)

| Method | Endpoint |
|---|---|
| `client.upload.knowledge()` | `POST /ingestion/upload_knowledge` |
| `client.upload.verify_processing()` | `POST /ingestion/verify_processing` |
| `client.upload.add_memory()` | `POST /memories/add_memory` |
| `client.upload.delete_memory()` | `DELETE /memories/delete_memory` |

### Recall

| Method | Endpoint |
|---|---|
| `client.recall.full_recall()` | `POST /recall/full_recall` |
| `client.recall.recall_preferences()` | `POST /recall/recall_preferences` |
| `client.recall.boolean_recall()` | `POST /recall/boolean_recall` |

### List + Fetch (under `fetch`)

| Method | Endpoint |
|---|---|
| `client.fetch.list_data()` | `POST /list/data` |
| `client.fetch.graph_relations_by_source_id()` | `GET /list/graph_relations_by_id` |
| `client.fetch.content()` | `POST /fetch/content` |

### Knowledge Deletion

| Method | Endpoint |
|---|---|
| `client.data.delete()` | `POST /knowledge/delete_knowledge` |

### Embeddings

| Method | Endpoint |
|---|---|
| `client.embeddings.insert()` | `POST /embeddings/insert_raw_embeddings` |
| `client.embeddings.search()` | `POST /embeddings/search_raw_embeddings` |
| `client.embeddings.filter()` | `POST /embeddings/filter_raw_embeddings` |
| `client.embeddings.delete()` | `DELETE /embeddings/delete_raw_embeddings` |

In TypeScript, snake_case method names use camelCase: `client.tenant.getInfraStatus()`, `client.recall.fullRecall()`, etc.

## Getting started

### Create a tenant

A tenant is an isolated workspace. Most organizations create one tenant total, with sub-tenants for users or teams. See [Multi-Tenant](/essentials/multi-tenant) for the full model.

<Tabs>
  <Tab title="TypeScript">
    ```ts
    const response = await client.tenant.create({
      tenantId: "my_first_tenant"
    });
    ```
  </Tab>
  <Tab title="Python (Sync)">
    ```python
    response = client.tenant.create(tenant_id="my_first_tenant")
    ```
  </Tab>
  <Tab title="Python (Async)">
    ```python
    response = await async_client.tenant.create(tenant_id="my_first_tenant")
    ```
  </Tab>
</Tabs>

Tenant creation is asynchronous. Poll [`get_infra_status`](/api-reference/endpoint/infra-status) until provisioning completes:

<Tabs>
  <Tab title="TypeScript">
    ```ts
    while (true) {
      const status = await client.tenant.getInfraStatus({
        tenantId: "my_first_tenant"
      });
      const { graphStatus, vectorstoreStatus } = status.infra;
      if (graphStatus && vectorstoreStatus.every(Boolean)) break;
      await new Promise(r => setTimeout(r, 2000));
    }
    ```
  </Tab>
  <Tab title="Python (Sync)">
    ```python
    import time

    while True:
        status = client.tenant.get_infra_status(tenant_id="my_first_tenant")
        if (status.infra.graph_status
            and all(status.infra.vectorstore_status)):
            break
        time.sleep(2)
    ```
  </Tab>
</Tabs>

### Ingest knowledge

Upload documents, app sources, or both via [`upload.knowledge`](/api-reference/endpoint/upload-knowledge):

<Tabs>
  <Tab title="TypeScript">
    ```ts
    import { createReadStream } from "fs";

    const result = await client.upload.knowledge({
      tenantId: "my_first_tenant",
      files: [
        createReadStream("contract.pdf"),
        createReadStream("policy.pdf"),
      ],
      fileMetadata: JSON.stringify([
        {
          file_id: "contract_q4",
          metadata: { category: "legal" },
          additional_metadata: { author: "Alice" }
        },
        {
          file_id: "policy_2025",
          metadata: { category: "legal" },
          additional_metadata: { author: "Bob" }
        }
      ])
    });
    ```
  </Tab>
  <Tab title="Python (Sync)">
    ```python
    import json

    with open("contract.pdf", "rb") as f1, open("policy.pdf", "rb") as f2:
        result = client.upload.knowledge(
            tenant_id="my_first_tenant",
            files=[f1, f2],
            file_metadata=json.dumps([
                {
                    "file_id": "contract_q4",
                    "metadata": {"category": "legal"},
                    "additional_metadata": {"author": "Alice"},
                },
                {
                    "file_id": "policy_2025",
                    "metadata": {"category": "legal"},
                    "additional_metadata": {"author": "Bob"},
                },
            ]),
        )
    ```
  </Tab>
</Tabs>

For full options including app sources and forceful relations, see [Upload knowledge](/api-reference/endpoint/upload-knowledge).

### Add user memories

Store user preferences and conversation history via [`upload.add_memory`](/api-reference/endpoint/add-memory):

<Tabs>
  <Tab title="TypeScript">
    ```ts
    // Plain text memory
    const result = await client.upload.addMemory({
      tenantId: "my_first_tenant",
      memories: [
        {
          text: "User prefers detailed technical explanations and dark mode",
          infer: true,
          userName: "Alex",
          metadata: { team: "engineering" },
          additionalMetadata: { source: "onboarding" }
        }
      ]
    });

    // Conversation pairs
    const conversation = await client.upload.addMemory({
      tenantId: "my_first_tenant",
      memories: [
        {
          userAssistantPairs: [
            { user: "What are my preferences?", assistant: "You prefer dark mode." },
            { user: "How do I like reports?", assistant: "Weekly summaries." }
          ],
          infer: true,
          userName: "Alex"
        }
      ]
    });
    ```
  </Tab>
  <Tab title="Python (Sync)">
    ```python
    # Plain text memory
    result = client.upload.add_memory(
        tenant_id="my_first_tenant",
        memories=[
            {
                "text": "User prefers detailed technical explanations and dark mode",
                "infer": True,
                "user_name": "Alex",
                "metadata": {"team": "engineering"},
                "additional_metadata": {"source": "onboarding"},
            }
        ],
    )

    # Conversation pairs
    conversation = client.upload.add_memory(
        tenant_id="my_first_tenant",
        memories=[
            {
                "user_assistant_pairs": [
                    {"user": "What are my preferences?", "assistant": "You prefer dark mode."},
                    {"user": "How do I like reports?", "assistant": "Weekly summaries."},
                ],
                "infer": True,
                "user_name": "Alex",
            }
        ],
    )
    ```
  </Tab>
</Tabs>

<Info>
**`infer` defaults to `false`.** Set `infer: true` for conversational content where you want HydraDB to extract implicit preferences. Use `infer: false` for content that should be stored verbatim.
</Info>

### Recall

Three retrieval modes for different use cases:

<Tabs>
  <Tab title="TypeScript">
    ```ts
    // Hybrid recall over knowledge
    const knowledge = await client.recall.fullRecall({
      tenantId: "my_first_tenant",
      query: "What are the pricing tiers?",
      mode: "thinking",
      graphContext: true
    });

    // Hybrid recall over user memories
    const prefs = await client.recall.recallPreferences({
      tenantId: "my_first_tenant",
      subTenantId: "user_alex",
      query: "What are this user's display preferences?",
      mode: "thinking"
    });

    // Exact-match search
    const exact = await client.recall.booleanRecall({
      tenantId: "my_first_tenant",
      query: "ERROR_429 rate limit",
      operator: "and"
    });
    ```
  </Tab>
  <Tab title="Python (Sync)">
    ```python
    # Hybrid recall over knowledge
    knowledge = client.recall.full_recall(
        tenant_id="my_first_tenant",
        query="What are the pricing tiers?",
        mode="thinking",
        graph_context=True,
    )

    # Hybrid recall over user memories
    prefs = client.recall.recall_preferences(
        tenant_id="my_first_tenant",
        sub_tenant_id="user_alex",
        query="What are this user's display preferences?",
        mode="thinking",
    )

    # Exact-match search
    exact = client.recall.boolean_recall(
        tenant_id="my_first_tenant",
        query="ERROR_429 rate limit",
        operator="and",
    )
    ```
  </Tab>
</Tabs>

For full parameter documentation, see [Recall](/api-reference/endpoint/recall-overview).

### Browse and inspect

<Tabs>
  <Tab title="TypeScript">
    ```ts
    // List knowledge sources with pagination
    const sources = await client.fetch.listData({
      tenantId: "my_first_tenant",
      kind: "knowledge",
      page: 1,
      pageSize: 50
    });

    // Inspect graph relationships for a specific source
    const relations = await client.fetch.graphRelationsBySourceId({
      tenantId: "my_first_tenant",
      sourceId: "doc_12345"
    });

    // Fetch original file content or get a presigned URL
    const file = await client.fetch.content({
      tenantId: "my_first_tenant",
      sourceId: "doc_12345",
      mode: "url"
    });
    ```
  </Tab>
  <Tab title="Python (Sync)">
    ```python
    # List knowledge sources with pagination
    sources = client.fetch.list_data(
        tenant_id="my_first_tenant",
        kind="knowledge",
        page=1,
        page_size=50,
    )

    # Inspect graph relationships for a specific source
    relations = client.fetch.graph_relations_by_source_id(
        tenant_id="my_first_tenant",
        source_id="doc_12345",
    )

    # Fetch original file content or get a presigned URL
    file = client.fetch.content(
        tenant_id="my_first_tenant",
        source_id="doc_12345",
        mode="url",
    )
    ```
  </Tab>
</Tabs>

## Type safety

<Check>
Both SDKs are fully typed:

- **Autocomplete** for all method names and parameters
- **Type checking** for request and response objects
- **Inline documentation** for each parameter
- **Compile-time validation** for required vs optional fields
</Check>

The SDKs provide exact type parity with the API specification:

- **Request parameters** – every field documented in the API reference is reflected in method signatures
- **Response objects** – return types match the JSON schema for each endpoint
- **Error types** – exception structures mirror error response formats
- **Nested objects** – complex parameters and responses keep their full structure

## Error handling

Both SDKs throw exceptions for non-2xx responses. Error objects expose the API's `detail` payload:

<Tabs>
  <Tab title="TypeScript">
    ```ts
    try {
      const result = await client.recall.fullRecall({
        tenantId: "my_first_tenant",
        query: "..."
      });
    } catch (error: any) {
      const code = error.body?.detail?.error_code;
      const status = error.statusCode;

      if (code === "TENANT_NOT_FOUND") {
        // Handle missing tenant
      } else if (status === 429) {
        // Rate limit - retry with backoff
      } else {
        throw error;
      }
    }
    ```
  </Tab>
  <Tab title="Python (Sync)">
    ```python
    try:
        result = client.recall.full_recall(
            tenant_id="my_first_tenant",
            query="...",
        )
    except Exception as e:
        body = getattr(e, "body", {}) or {}
        code = body.get("detail", {}).get("error_code")
        status = getattr(e, "status_code", None)

        if code == "TENANT_NOT_FOUND":
            # Handle missing tenant
            pass
        elif status == 429:
            # Rate limit - retry with backoff
            pass
        else:
            raise
    ```
  </Tab>
</Tabs>

For the full list of error codes and retry patterns, see [Error Responses](/api-reference/error-responses).

## IDE-driven discovery

Whether you're using TypeScript, Python, VS Code, PyCharm, or any modern IDE, the workflow is the same:

1. Type the method name → see all available methods
2. Open the parentheses → see all required and optional parameters
3. Press `Cmd+Space` (macOS) or `Ctrl+Space` (Windows/Linux) → get inline documentation

This works because the SDKs are fully typed with comprehensive parameter docs.

## Related sections

- [API Reference](/api-reference) – complete endpoint documentation
- [Error Responses](/api-reference/error-responses) – HTTP codes, error codes, retry patterns
- [Quickstart](/quickstart) – build your first integration in five minutes
