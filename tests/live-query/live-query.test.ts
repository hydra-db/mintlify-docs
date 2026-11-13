import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import { HydraDBClient, type HydraDB } from "@hydradb/sdk";

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`Set ${name} before running the live tests`);
  return value;
}

async function request<T>(label: string, run: () => PromiseLike<T>): Promise<T> {
  try {
    return await run();
  } catch (error) {
    const status = typeof error === "object" && error !== null && "statusCode" in error
      && typeof error.statusCode === "number" ? error.statusCode : "unavailable";
    throw new Error(`${label} failed; HTTP status ${status}. Response contents are not logged.`);
  }
}

function hasDeclaredLinks(source: HydraDB.ListV2SourceItem): boolean {
  const relations = source.relations;
  return typeof relations === "object" && relations !== null && "ids" in relations
    && Array.isArray(relations.ids) && relations.ids.length > 0;
}

const database = required("HYDRADB_DATABASE");
let queryWire: unknown;
const client = new HydraDBClient({
  token: required("HYDRADB_API_KEY"),
  timeoutInSeconds: 90,
  maxRetries: 0,
  fetch: async (input, init) => {
    const response = await fetch(input, init);
    const url = input instanceof Request ? input.url : String(input);
    if (new URL(url).pathname === "/query" && response.ok) {
      queryWire = await response.clone().json();
    }
    return response;
  },
});

test("live TypeScript client preserves context through the Cursor cookbook", { timeout: 240_000 }, async (t) => {
  const available = await request("List collections", () => client.databases.collections({ database }));
  assert.equal(available.success, true, "The collection lookup must succeed");
  const requestedCollection = process.env.HYDRADB_COLLECTION;
  const collections = requestedCollection !== undefined
    ? [requestedCollection] : available.data?.collections ?? [];
  assert.ok(collections.length > 0, "The database needs an indexed collection");

  let source: HydraDB.ListV2SourceItem | undefined;
  for (const collection of collections) {
    const listed = await request("List existing knowledge", () => client.context.list({
      database, collection, type: "knowledge", pageSize: 20,
      includeFields: ["title", "relations"],
    }));
    assert.equal(listed.success, true, "The context lookup must succeed");
    const candidates = listed.data?.sources ?? [];
    source ??= candidates[0];
    const linked = candidates.find(hasDeclaredLinks);
    if (linked) {
      source = linked;
      break;
    }
  }
  assert.ok(source, "The selected collections need at least one existing knowledge source");
  const selected = source;
  const query = process.env.HYDRADB_TEST_QUERY
    ?? `Describe ${selected.title || selected.id} and its relationships.`;
  const result = await request("Query existing knowledge", () => client.query({
    database, collection: selected.collection, query, type: "knowledge",
    queryBy: "hybrid", mode: "thinking", graphContext: true,
    queryForcefulRelations: true, maxResults: 8, responseFormat: "structured",
  }));
  assert.equal(result.success, true, "The query must succeed");
  const data = result.data;
  assert.ok(data && typeof data !== "string", "The SDK must parse a structured response");

  await t.test("returns searchable source chunks with citation fields", () => {
    const chunks = data.chunks ?? [];
    assert.ok(chunks.length > 0 && chunks.length <= 8, "The query must return one to eight chunks");
    assert.ok(chunks.every((chunk) => typeof chunk.id === "string" && chunk.id.length > 0
      && typeof chunk.chunkContent === "string" && chunk.chunkContent.length > 0),
    "Each chunk must contain a source ID and source text");
    assert.equal(data.llmPrompt, undefined, "Structured format must omit the generated prompt");
  });

  await t.test("passes the live HTTP response through the actual Python cookbook formatter", () => {
    assert.ok(queryWire !== undefined, "The SDK request must yield a captured HTTP response");
    const childEnv = { ...process.env };
    delete childEnv.HYDRADB_API_KEY;
    const checked = spawnSync("uv", [
      "run", "--python", "3.12", "--with", "hydradb-sdk==2.1.7", "python",
      fileURLToPath(new URL("check_cursor_response.py", import.meta.url)),
    ], { input: JSON.stringify(queryWire), encoding: "utf8", env: childEnv, timeout: 60_000 });
    assert.equal(checked.status, 0, "The cookbook must preserve chunks, graph context, linked context, and citation paths");
    t.diagnostic(`Cookbook context counts: ${checked.stdout.trim()}`);
  });

  await t.test("includes live graph relationships", (graphTest) => {
    const paths = [...data.graphContext?.queryPaths ?? [], ...data.graphContext?.chunkRelations ?? []];
    if (paths.length === 0) {
      graphTest.skip("This query returned no graph paths; nonempty graph coverage needs different indexed data or a query");
      return;
    }
    assert.ok(paths.every((path) => (path.triplets?.length ?? 0) > 0
      || (typeof path.combinedContext === "string" && path.combinedContext.length > 0)),
    "Every graph path must contain relationship evidence or a textual summary");
  });

  await t.test("includes live linked-source context", (linkedTest) => {
    const linked = Object.values(data.additionalContext ?? {});
    if (linked.length === 0) {
      linkedTest.skip("This query returned no linked chunks; nonempty linked-context coverage needs declared source relations");
      return;
    }
    assert.ok(linked.every((chunk) => typeof chunk.chunkContent === "string" && chunk.chunkContent.length > 0),
      "Every linked source must carry its text");
  });

  t.diagnostic(`Read-only live counts: collections=${collections.length}, chunks=${data.chunks?.length ?? 0}, graph_paths=${(data.graphContext?.queryPaths?.length ?? 0) + (data.graphContext?.chunkRelations?.length ?? 0)}, linked_chunks=${Object.keys(data.additionalContext ?? {}).length}`);
});
