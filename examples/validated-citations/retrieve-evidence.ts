export type HydraQueryChunk = {
  id?: string;
  chunk_uuid: string;
  chunk_content: string;
  source_title?: string;
  extra_context_ids?: string[];
};

export type HydraQueryResponse = {
  success: true;
  data: Record<string, unknown> & { chunks: HydraQueryChunk[] };
  error?: unknown;
  meta?: unknown;
};

export type RetrievalResult = {
  queryResult: HydraQueryResponse;
  earlyResult?: { status: "insufficient_evidence" };
};

export type RetrieveEvidenceOptions = {
  apiKey: string;
  database: string;
  collection?: string;
  fetchImpl?: typeof fetch;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function responseMessage(value: unknown): string {
  if (
    isRecord(value) &&
    isRecord(value.error) &&
    typeof value.error.message === "string"
  ) {
    return value.error.message;
  }
  return "HydraDB query failed";
}

function isQueryChunk(value: unknown): value is HydraQueryChunk {
  return (
    isRecord(value) &&
    typeof value.chunk_uuid === "string" &&
    value.chunk_uuid.length > 0 &&
    typeof value.chunk_content === "string" &&
    value.chunk_content.trim().length > 0
  );
}

/**
 * Retrieves one v2 query response for the current answer request.
 *
 * The complete successful response is preserved for `validateCitedAnswer`; do
 * not merge it with another request's response or use it as a cross-request
 * cache key.
 */
export async function retrieveEvidence(
  question: string,
  options: RetrieveEvidenceOptions,
): Promise<RetrievalResult> {
  if (options.apiKey.trim().length === 0) {
    throw new Error("HYDRA_DB_API_KEY is required to query HydraDB.");
  }
  if (options.database.trim().length === 0) {
    throw new Error("A HydraDB database is required to query evidence.");
  }

  const response = await (options.fetchImpl ?? fetch)(
    "https://api.hydradb.com/query",
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${options.apiKey}`,
        "API-Version": "2",
      },
      body: JSON.stringify({
        database: options.database,
        ...(options.collection ? { collection: options.collection } : {}),
        query: question,
        type: "knowledge",
        query_by: "hybrid",
        mode: "fast",
      }),
    },
  );

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new Error("HydraDB query returned invalid JSON.");
  }
  if (
    !response.ok ||
    !isRecord(payload) ||
    payload.success !== true ||
    !isRecord(payload.data) ||
    !Array.isArray(payload.data.chunks) ||
    !payload.data.chunks.every(isQueryChunk)
  ) {
    throw new Error(responseMessage(payload));
  }

  const queryResult: HydraQueryResponse = {
    ...payload,
    success: true,
    data: {
      ...payload.data,
      chunks: payload.data.chunks,
    },
  };

  if (queryResult.data.chunks.length === 0) {
    return {
      queryResult,
      earlyResult: { status: "insufficient_evidence" },
    };
  }

  return { queryResult };
}
