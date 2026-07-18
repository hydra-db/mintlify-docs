---
name: hydradb-ingest-context
description: Implement or repair HydraDB v2 ingestion. Use for database creation, readiness polling, knowledge or memory ingestion, stable IDs, upserts, status polling, and webhooks.
license: Apache-2.0
compatibility: HydraDB API v2; Python hydradb-sdk 2.x; TypeScript @hydradb/sdk 2.x
metadata:
  product: HydraDB
  task: ingestion
---

# Ingest HydraDB context

Preserve the asynchronous lifecycle:

```text
create database -> poll database readiness -> ingest -> capture returned IDs
-> poll each ID until completed -> make content queryable
```

Searching before either readiness boundary is a race.

## Preserve the language contract

| Client | Request and response fields | Version |
|---|---|---|
| Raw HTTP | `snake_case` | Send `API-Version: 2` |
| Python SDK | `snake_case` | SDK sends v2 |
| TypeScript SDK | `camelCase` | SDK sends v2 and serializes wire fields |

Successful envelope responses expose the payload under `.data`. In TypeScript, write `documentMetadata`, not `document_metadata`; in Python, write `document_metadata`.

Fields inside JSON-stringified multipart payloads remain raw snake_case even when the outer TypeScript request uses camelCase.

Memory-item `metadata` is currently JSON-encoded; `additional_metadata` remains an object. Example: `metadata: JSON.stringify({ department: "support" })`.

## Python: complete memory path

```python
import json
import os
import time
from hydra_db import HydraDB

client = HydraDB(token=os.environ["HYDRA_DB_API_KEY"])
database = "agent_production"
collection = "user_42"

client.databases.create(database=database)

deadline = time.monotonic() + 300
while time.monotonic() < deadline:
    status = client.databases.status(database=database).data
    infra = status.infra if status else None
    vectorstore = infra.vectorstore_status if infra else None
    if (
        infra
        and infra.scheduler_status is True
        and infra.graph_status is True
        and vectorstore
        and vectorstore.knowledge is True
        and vectorstore.memories is True
    ):
        break
    time.sleep(5)
else:
    raise TimeoutError("database did not become ready for ingestion")

ingest = client.context.ingest(
    database=database,
    collection=collection,
    type="memory",
    upsert="true",
    memories=json.dumps([
        {
            "id": "user:42:preference:answer-style",
            "text": "The user prefers concise answers with runnable examples.",
            "infer": True,
            "additional_metadata": {"source": "settings"},
        }
    ]),
)
results = ingest.data.results if ingest.data else []
context_id = next((item.id for item in results if item.id and not item.error), None)
if not context_id:
    raise RuntimeError("ingestion returned no context ID")

deadline = time.monotonic() + 300
while time.monotonic() < deadline:
    status = client.context.status(
        database=database,
        collection=collection,
        ids=[context_id],
    ).data
    if not status or not status.statuses:
        raise RuntimeError("status response contained no item")
    item = status.statuses[0]
    if item.indexing_status == "completed":
        break
    if item.indexing_status in {"failed", "errored"}:
        raise RuntimeError(item.error_message or "ingestion failed")
    time.sleep(2)
else:
    raise TimeoutError("context did not finish indexing")
```

Docs say `errored`; OpenAPI says `failed`. Treat both as terminal.

## TypeScript: the same contract

```typescript
import { HydraDBClient } from "@hydradb/sdk";

const client = new HydraDBClient({
  token: process.env.HYDRA_DB_API_KEY!,
});
const database = "agent_production";
const collection = "user_42";

await client.databases.create({ database });

const readyDeadline = Date.now() + 300_000;
for (;;) {
  if (Date.now() >= readyDeadline) {
    throw new Error("database did not become ready for ingestion");
  }
  const envelope = await client.databases.status({ database });
  const infra = envelope.data?.infra;
  if (
    infra?.schedulerStatus === true &&
    infra.graphStatus === true &&
    infra.vectorstoreStatus?.knowledge === true &&
    infra.vectorstoreStatus.memories === true
  ) break;
  await new Promise((resolve) => setTimeout(resolve, 5_000));
}

const ingest = await client.context.ingest({
  database,
  collection,
  type: "memory",
  upsert: "true",
  memories: JSON.stringify([
    {
      id: "user:42:preference:answer-style",
      text: "The user prefers concise answers with runnable examples.",
      infer: true,
      additional_metadata: { source: "settings" },
    },
  ]),
});
const contextId = ingest.data?.results?.find(
  (item) => item.id && !item.error,
)?.id;
if (!contextId) {
  throw new Error(ingest.data?.results?.[0]?.error ?? "ingestion returned no context ID");
}

const indexDeadline = Date.now() + 300_000;
for (;;) {
  if (Date.now() >= indexDeadline) {
    throw new Error("context did not finish indexing");
  }
  const statusEnvelope = await client.context.status({
    database,
    collection,
    ids: [contextId],
  });
  const item = statusEnvelope.data?.statuses?.[0];
  if (!item) throw new Error("status response contained no item");
  if (item.indexingStatus === "completed") break;
  if (["failed", "errored"].includes(item.indexingStatus ?? "")) {
    throw new Error(item.errorMessage ?? "ingestion failed");
  }
  await new Promise((resolve) => setTimeout(resolve, 2_000));
}
```

## Choose the ingestion shape

| Content | `type` | Input |
|---|---|---|
| Files such as PDF, DOCX, Markdown, or text | `knowledge` | `documents` plus optional `document_metadata` / `documentMetadata` |
| Structured app records | `knowledge` | `app_knowledge` / `appKnowledge` |
| Preferences and observations | `memory` | JSON-stringified `memories` array |
| Caller-supplied graph | `knowledge` | `graph_payload` / `graphPayload` |

The ingest endpoint is multipart. Raw HTTP field names are snake_case:

```bash
curl -sS -X POST 'https://api.hydradb.com/context/ingest' \
  -H "Authorization: Bearer $HYDRA_DB_API_KEY" \
  -H 'API-Version: 2' \
  -F 'database=agent_production' \
  -F 'collection=user_42' \
  -F 'type=knowledge' \
  -F 'documents=@handbook.pdf' \
  -F 'document_metadata=[{"id":"handbook:v3","additional_metadata":{"source":"handbook"}}]'
```

## Production rules

- Keep database and collection in one typed scope object reused by ingestion and query code.
- Persist returned IDs with the source record; status polling needs them.
- Use bounded exponential backoff with jitter for rate limits, transport failures, and transient 5xx responses.
- Bound every polling loop by attempts or a deadline.
- Do not retry unchanged validation or authorization failures.
- Use stable IDs and `upsert="true"` for replayable ingestion.
- Prefer webhooks over dense polling at scale, and verify webhook signatures.
- Log safe scope, status, duration, and correlation data; never log secrets or private content.

## Completion check

An ingest 2xx is not completion. Every required item must reach `completed` or surface a terminal failure.

Verify uncommon payloads against the [ingestion reference](https://docs.hydradb.com/api-reference/v2/endpoint/ingest-context) and [processing status reference](https://docs.hydradb.com/api-reference/v2/endpoint/source-status).
