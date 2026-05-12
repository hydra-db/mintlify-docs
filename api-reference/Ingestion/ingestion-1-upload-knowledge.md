---
title: "Upload Knowledge"
description: "Ingest documents (files) and/or app-generated content (app sources) into a tenant."
---

## When to use it

Use this endpoint to add knowledge that your agents will recall later:

- **Files** – PDFs, DOCX, Markdown, CSVs, and other documents you want HydraDB to parse and chunk
- **App sources** – structured content from connected apps (Slack messages, Notion pages, Gmail threads, etc.) where you already have the text and metadata

For user-specific context (preferences, conversations), use [`POST /memories/add_memory`](/api-reference/endpoint/add-memory) instead.

## Endpoint

```text
POST /ingestion/upload_knowledge
```

- **Auth:** Bearer token
- **Content type:** `multipart/form-data` (always – even when sending only `app_sources`)
- **Idempotency:** Controlled by `upsert` (default `true` – overwrites existing items with the same ID)
- **Async:** Yes – returns `202 Accepted` with one `source_id` per item. Use [`POST /ingestion/verify_processing`](/api-reference/endpoint/verify-processing) to check status.

## Two ingestion paths

This endpoint accepts two content formats in one request. Send either or both:

| Path | Form field | Content type |
|---|---|---|
| **Files** | `files` (binary) + optional `file_metadata` (JSON string) | Documents to be parsed |
| **App sources** | `app_sources` (JSON string) | Pre-parsed app content with metadata |

## Example: Upload files

<CodeGroup>

```bash cURL
curl -X POST 'https://api.hydradb.com/ingestion/upload_knowledge' \
  -H "Authorization: Bearer <your_api_key>" \
  -F "tenant_id=my_first_tenant" \
  -F "files=@/path/to/contract.pdf" \
  -F "files=@/path/to/policy.pdf" \
  -F 'file_metadata=[
    { "file_id": "contract_q4", "metadata": { "category": "legal" } },
    { "file_id": "policy_2025", "metadata": { "category": "legal" } }
  ]'
```

```typescript TypeScript
import { createReadStream } from "fs";

const response = await client.upload.knowledge({
  tenantId: "my_first_tenant",
  files: [
    createReadStream("/path/to/contract.pdf"),
    createReadStream("/path/to/policy.pdf"),
  ],
  fileMetadata: JSON.stringify([
    { file_id: "contract_q4", metadata: { category: "legal" } },
    { file_id: "policy_2025", metadata: { category: "legal" } },
  ]),
});
```

```python Python (Sync)
import json

with open("/path/to/contract.pdf", "rb") as f1, \
     open("/path/to/policy.pdf", "rb") as f2:
    response = client.upload.knowledge(
        tenant_id="my_first_tenant",
        files=[f1, f2],
        file_metadata=json.dumps([
            {"file_id": "contract_q4", "metadata": {"category": "legal"}},
            {"file_id": "policy_2025", "metadata": {"category": "legal"}},
        ]),
    )
```

</CodeGroup>

## Example: Upload app sources

<CodeGroup>

```bash cURL
curl -X POST 'https://api.hydradb.com/ingestion/upload_knowledge' \
  -H "Authorization: Bearer <your_api_key>" \
  -F "tenant_id=my_first_tenant" \
  -F 'app_sources=[
    {
      "id": "slack_msg_12345",
      "title": "Q4 planning standup notes",
      "type": "slack",
      "url": "https://acme.slack.com/archives/C01/p1234567890",
      "timestamp": "2025-10-15T14:30:00Z",
      "content": { "text": "Today we discussed the Q4 roadmap..." },
      "tenant_metadata": { "category": "engineering" },
      "document_metadata": { "channel": "#planning", "author": "alex" }
    }
  ]'
```

```typescript TypeScript
const response = await client.upload.knowledge({
  tenantId: "my_first_tenant",
  appSources: JSON.stringify([
    {
      id: "slack_msg_12345",
      title: "Q4 planning standup notes",
      type: "slack",
      url: "https://acme.slack.com/archives/C01/p1234567890",
      timestamp: "2025-10-15T14:30:00Z",
      content: { text: "Today we discussed the Q4 roadmap..." },
      tenant_metadata: { category: "engineering" },
      document_metadata: { channel: "#planning", author: "alex" },
    },
  ]),
});
```

```python Python (Sync)
import json

response = client.upload.knowledge(
    tenant_id="my_first_tenant",
    app_sources=json.dumps([
        {
            "id": "slack_msg_12345",
            "title": "Q4 planning standup notes",
            "type": "slack",
            "url": "https://acme.slack.com/archives/C01/p1234567890",
            "timestamp": "2025-10-15T14:30:00Z",
            "content": {"text": "Today we discussed the Q4 roadmap..."},
            "tenant_metadata": {"category": "engineering"},
            "document_metadata": {"channel": "#planning", "author": "alex"},
        }
    ]),
)
```

</CodeGroup>

## Form fields

| Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `tenant_id` | string | Yes | – | The tenant to ingest into. |
| `files` | binary[] | One of | `[]` | One or more files to upload. |
| `file_metadata` | JSON string | No | – | Array of metadata objects, one per file. Length must match `files`. See [File metadata](#file-metadata). |
| `app_sources` | JSON string | One of | – | A single app source object or an array. See [App sources](#app-sources). |
| `sub_tenant_id` | string | No | default | Sub-tenant scope. |
| `upsert` | boolean | No | `true` | If `true`, existing sources with the same ID are overwritten. |

You must provide at least one of `files` or `app_sources`. You can send both in the same request.

## File metadata

Each entry in `file_metadata` corresponds to the file at the same index in `files`:

| Field | Type | Description |
|---|---|---|
| `file_id` | string | Optional. Server-generated if omitted. Use this to reference the file later (e.g., for `verify_processing`). |
| `metadata` | object | Tenant-level metadata. Filterable in recall queries. |
| `additional_metadata` | object | Document-level metadata. Filterable in recall queries. |
| `relations` | object | Forcefully connect this file to other sources. See [Forceful relations](/essentials/forceful-relations). |

## App sources

Each app source represents a single piece of pre-parsed content (a Slack message, Notion page, Gmail thread, etc.):

| Field | Type | Description |
|---|---|---|
| `id` | string | Unique identifier for the source. |
| `title` | string | Short title or subject. |
| `type` | string | Source category (e.g., `slack`, `notion`, `gmail`, `webpage`). |
| `description` | string | Optional long-form description. |
| `url` | string | Canonical URL or reference link. |
| `timestamp` | string | ISO-8601 timestamp (creation or last-updated). |
| `content` | object | Content payload. See [Content formats](#content-formats). |
| `tenant_metadata` | object | Tenant-level metadata. |
| `document_metadata` | object | Document-level metadata. |
| `attachments` | array | Optional related attachments (images, PDFs, etc.). |
| `relations` | object | Forcefully connect this source to others. |

### Content formats

The `content` object supports multiple content types. Use the field that matches your source — only one needs to be set:

| Field | Use for |
|---|---|
| `text` | Plain text content |
| `markdown` | Markdown-formatted content |
| `html_base64` | Base64-encoded HTML |
| `csv_base64` | Base64-encoded CSV |

```json
{ "content": { "text": "Plain text content." } }
```

## Response

```json
{
  "success": true,
  "message": "Upload initiated successfully",
  "results": [
    {
      "source_id": "ef3ea754019855e2b39e9ab5c2d26096",
      "filename": "contract.pdf",
      "status": "queued",
      "error": null
    },
    {
      "source_id": "9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d",
      "filename": "policy.pdf",
      "status": "queued",
      "error": null
    }
  ],
  "success_count": 2,
  "failed_count": 0
}
```

| Field | Description |
|---|---|
| `success` | `true` if at least one item was queued. |
| `message` | Human-readable status. |
| `results[]` | Per-item upload result. |
| `results[].source_id` | The ID assigned to this source. Use this for `verify_processing` and downstream operations. |
| `results[].filename` | Original filename (for files) or `null` (for app sources). |
| `results[].status` | Initial status. Always `queued` on a successful response. |
| `success_count` / `failed_count` | Aggregate counts across the request. |

## Behavior notes

<Info>
**Processing is asynchronous.** This endpoint returns once items are queued. Use [`POST /ingestion/verify_processing`](/api-reference/endpoint/verify-processing) with the returned `source_id`s to check when content is fully indexed.
</Info>

<Info>
**Always use multipart form data.** Even if you're sending only `app_sources` (no files), the endpoint expects `multipart/form-data`. Sending JSON directly will return `422 VALIDATION_ERROR`.
</Info>

<Info>
**`file_metadata` and `app_sources` are JSON strings.** Both must be serialized before being sent as form fields. Most HTTP libraries don't auto-stringify nested JSON in multipart bodies.
</Info>

## Related endpoints

- **Next:** [Verify processing](/api-reference/endpoint/verify-processing) – poll for ingestion status
- **Next:** [Full recall](/api-reference/endpoint/full-recall) – retrieve ingested content
- **Alternative:** [Add memory](/api-reference/endpoint/add-memory) – for user memories instead of knowledge
- **Related:** [Delete knowledge](/api-reference/endpoint/delete-knowledge) – remove sources

## Errors

Common codes: `400 INVALID_PARAMETERS`, `404 TENANT_NOT_FOUND`, `422 VALIDATION_ERROR`. See [Error Responses](/api-reference/error-responses) for the full list.

Read more: [Essentials → Memories](/essentials/memories) · [Essentials → Metadata](/essentials/metadata)
