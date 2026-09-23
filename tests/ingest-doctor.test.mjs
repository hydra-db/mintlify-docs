import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, truncate, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  LIMITS,
  LOCAL_SAFETY_LIMITS,
  formatHuman,
  runCli,
  validateIngestManifest,
} from "../examples/ingest-doctor/validate.mjs";

const examplesDirectory = fileURLToPath(
  new URL("../examples/ingest-doctor/", import.meta.url),
);

function encoded(value) {
  return JSON.stringify(value);
}

function knowledge(overrides = {}) {
  return {
    type: "knowledge",
    database: "acme",
    collection: "",
    app_knowledge: encoded([
      {
        id: "app-1",
        database: "acme",
        collection: "",
        kind: "custom",
        provider: "example",
        external_id: "app-1",
        fields: { kind: "custom", data: { text: "hello" } },
      },
    ]),
    ...overrides,
  };
}

function memory(overrides = {}) {
  return {
    type: "memory",
    database: "acme",
    collection: "user-1",
    memories: encoded([{ id: "memory-1", text: "Prefers dark mode." }]),
    ...overrides,
  };
}

function codes(result) {
  return result.diagnostics.map(({ code }) => code);
}

function diagnostic(result, code) {
  return result.diagnostics.find((item) => item.code === code);
}

function graphManifest(graph, overrides = {}) {
  return knowledge({
    graph_payload: encoded({ "app-1": graph }),
    ...overrides,
  });
}

function simpleGraph(overrides = {}) {
  return {
    entities: {
      left: { name: "Left" },
      right: { name: "Right" },
    },
    relations: [
      { source: "left", target: "right", predicate: "RELATES_TO" },
    ],
    ...overrides,
  };
}

async function fixture(name) {
  return JSON.parse(await readFile(join(examplesDirectory, name), "utf8"));
}

function captureIo() {
  let stdout = "";
  let stderr = "";
  return {
    io: {
      stdout: (text) => {
        stdout += text;
      },
      stderr: (text) => {
        stderr += text;
      },
    },
    output: () => ({ stdout, stderr }),
  };
}

test("valid knowledge fixture passes", async () => {
  const result = validateIngestManifest(await fixture("valid-knowledge.json"));
  assert.equal(result.status, "pass");
  assert.equal(result.summary.errors, 0);
  assert.deepEqual(result.summary, {
    sources: 2,
    graphs: 2,
    entities: 4,
    relations: 2,
    errors: 0,
    warnings: 0,
    diagnosticsShown: 0,
    diagnosticsTruncated: false,
  });
});

test("valid memory fixture passes", async () => {
  const result = validateIngestManifest(await fixture("valid-memory.json"));
  assert.equal(result.status, "pass");
  assert.equal(result.summary.sources, 1);
  assert.equal(result.summary.errors, 0);
});

test("type defaults to knowledge", () => {
  const manifest = knowledge();
  delete manifest.type;
  assert.equal(validateIngestManifest(manifest).status, "pass");
});

test("app_knowledge accepts a single object", () => {
  const item = JSON.parse(knowledge().app_knowledge)[0];
  const result = validateIngestManifest(
    knowledge({ app_knowledge: encoded(item) }),
  );
  assert.equal(result.status, "pass");
});

test("modern app fields object is accepted", () => {
  assert.equal(validateIngestManifest(knowledge()).status, "pass");
});

test("legacy app content object is accepted", () => {
  const result = validateIngestManifest(
    knowledge({
      app_knowledge: encoded([
        {
          id: "app-1",
          database: "acme",
          collection: "",
          content: { text: "hello" },
        },
      ]),
    }),
  );
  assert.equal(result.status, "pass");
  assert.ok(codes(result).includes("W_APP_LEGACY_CONTENT"));
});

test("app source without fields or content fails", () => {
  const result = validateIngestManifest(
    knowledge({
      app_knowledge: encoded([
        { id: "app-1", database: "acme", collection: "" },
      ]),
    }),
  );
  assert.ok(codes(result).includes("E_APP_PAYLOAD_REQUIRED"));
});

test("malformed modern fields fails", () => {
  const result = validateIngestManifest(
    knowledge({
      app_knowledge: encoded([
        {
          id: "app-1",
          database: "acme",
          collection: "",
          fields: "not-an-object",
        },
      ]),
    }),
  );
  assert.ok(codes(result).includes("E_APP_FIELDS_SHAPE"));
});

test("malformed legacy content fails", () => {
  const result = validateIngestManifest(
    knowledge({
      app_knowledge: encoded([
        {
          id: "app-1",
          database: "acme",
          collection: "",
          content: [],
        },
      ]),
    }),
  );
  assert.ok(codes(result).includes("E_APP_CONTENT_SHAPE"));
});

test("omitted app item scopes are warnings rather than errors", () => {
  const result = validateIngestManifest(
    knowledge({
      app_knowledge: encoded([
        {
          id: "app-1",
          kind: "custom",
          provider: "example",
          external_id: "app-1",
          fields: { kind: "custom", data: { text: "hello" } },
        },
      ]),
    }),
  );
  assert.equal(result.status, "pass");
  assert.deepEqual(codes(result), [
    "W_APP_COLLECTION_OMITTED",
    "W_APP_DATABASE_OMITTED",
  ]);
});

test("canonical app-native relations array is accepted", () => {
  const [item] = JSON.parse(knowledge().app_knowledge);
  item.relations = [
    {
      predicate: "linked_to",
      target: { external_id: "other-1", provider: "example" },
      properties: { reason: "same workflow" },
    },
  ];
  const result = validateIngestManifest(
    knowledge({ app_knowledge: encoded([item]) }),
  );
  assert.equal(result.status, "pass");
  assert.equal(result.summary.errors, 0);
});

test("app relation external_id requires a provider", () => {
  const [item] = JSON.parse(knowledge().app_knowledge);
  item.relations = [
    { predicate: "linked_to", target: { external_id: "other-1" } },
  ];
  const result = validateIngestManifest(
    knowledge({ app_knowledge: encoded([item]) }),
  );
  assert.ok(codes(result).includes("E_APP_RELATION_PROVIDER_REQUIRED"));
});

test("app fields kind must match the top-level kind", () => {
  const [item] = JSON.parse(knowledge().app_knowledge);
  item.fields.kind = "ticket";
  const result = validateIngestManifest(
    knowledge({ app_knowledge: encoded([item]) }),
  );
  assert.ok(codes(result).includes("E_APP_KIND_CONFLICT"));
});

test("missing app fields kind is a compatibility warning", () => {
  const [item] = JSON.parse(knowledge().app_knowledge);
  delete item.fields.kind;
  const result = validateIngestManifest(
    knowledge({ app_knowledge: encoded([item]) }),
  );
  assert.equal(result.status, "pass");
  assert.ok(codes(result).includes("W_APP_FIELDS_KIND_OMITTED"));
});

test("present app fields kind must be a non-empty string", () => {
  const [item] = JSON.parse(knowledge().app_knowledge);
  item.fields.kind = 42;
  const result = validateIngestManifest(
    knowledge({ app_knowledge: encoded([item]) }),
  );
  assert.ok(codes(result).includes("E_APP_FIELDS_KIND_TYPE"));
});

test("unsupported app kind fails with a custom-kind repair path", () => {
  const [item] = JSON.parse(knowledge().app_knowledge);
  item.kind = "unknown_kind";
  item.fields.kind = "unknown_kind";
  const result = validateIngestManifest(
    knowledge({ app_knowledge: encoded([item]) }),
  );
  assert.ok(codes(result).includes("E_APP_KIND_UNSUPPORTED"));
});

test("supplied app database mismatch fails", () => {
  const result = validateIngestManifest(
    knowledge({
      app_knowledge: encoded([
        {
          id: "app-1",
          database: "other",
          collection: "",
          fields: {},
        },
      ]),
    }),
  );
  assert.ok(codes(result).includes("E_APP_DATABASE_MISMATCH"));
});

test("supplied app collection mismatch fails", () => {
  const result = validateIngestManifest(
    knowledge({
      app_knowledge: encoded([
        {
          id: "app-1",
          database: "acme",
          collection: "other",
          fields: {},
        },
      ]),
    }),
  );
  assert.ok(codes(result).includes("E_APP_COLLECTION_MISMATCH"));
});

test("empty app id is accepted for server generation", () => {
  const result = validateIngestManifest(
    knowledge({
      app_knowledge: encoded([
        { id: "", database: "acme", collection: "", fields: {} },
      ]),
    }),
  );
  assert.equal(result.status, "pass");
  assert.equal(result.summary.sources, 1);
});

test("deprecated database alias is accepted with warning", () => {
  const result = validateIngestManifest(
    knowledge({
      database: undefined,
      tenant_id: "acme",
    }),
  );
  assert.equal(result.status, "pass");
  assert.ok(codes(result).includes("W_DATABASE_ALIAS"));
});

test("conflicting database alias fails", () => {
  const result = validateIngestManifest(
    knowledge({ tenant_id: "different" }),
  );
  assert.ok(codes(result).includes("E_DATABASE_ALIAS_CONFLICT"));
});

test("deprecated collection alias is accepted with warning", () => {
  const manifest = knowledge({ sub_tenant_id: "" });
  delete manifest.collection;
  const result = validateIngestManifest(manifest);
  assert.equal(result.status, "pass");
  assert.ok(codes(result).includes("W_COLLECTION_ALIAS"));
});

test("conflicting collection alias fails", () => {
  const result = validateIngestManifest(
    knowledge({ collection: "one", sub_tenant_id: "two" }),
  );
  assert.ok(codes(result).includes("E_COLLECTION_ALIAS_CONFLICT"));
});

test("missing database fails", () => {
  const manifest = knowledge();
  delete manifest.database;
  const result = validateIngestManifest(manifest);
  assert.ok(codes(result).includes("E_DATABASE_REQUIRED"));
});

test("unknown type fails", () => {
  const result = validateIngestManifest(knowledge({ type: "all" }));
  assert.ok(codes(result).includes("E_TYPE"));
});

test("invalid collection type fails", () => {
  const result = validateIngestManifest(knowledge({ collection: 42 }));
  assert.ok(codes(result).includes("E_COLLECTION_TYPE"));
});

test("invalid upsert representation fails", () => {
  const result = validateIngestManifest(knowledge({ upsert: "yes" }));
  assert.ok(codes(result).includes("E_UPSERT_TYPE"));
});

test("knowledge request requires a source", () => {
  const result = validateIngestManifest(
    knowledge({ app_knowledge: undefined }),
  );
  assert.ok(codes(result).includes("E_KNOWLEDGE_SOURCE_REQUIRED"));
});

test("knowledge request rejects memories field", () => {
  const result = validateIngestManifest(
    knowledge({ memories: encoded([{ text: "hello" }]) }),
  );
  assert.ok(codes(result).includes("E_KNOWLEDGE_MEMORIES_CONFLICT"));
});

test("memory request rejects documents", () => {
  const result = validateIngestManifest(memory({ documents: ["note.txt"] }));
  assert.ok(codes(result).includes("E_MEMORY_DOCUMENTS_CONFLICT"));
});

test("memory request rejects document_metadata", () => {
  const result = validateIngestManifest(
    memory({ document_metadata: encoded([]) }),
  );
  assert.ok(codes(result).includes("E_MEMORY_DOCUMENT_METADATA_CONFLICT"));
});

test("memory request rejects app_knowledge", () => {
  const result = validateIngestManifest(
    memory({ app_knowledge: encoded({}) }),
  );
  assert.ok(codes(result).includes("E_MEMORY_APP_KNOWLEDGE_CONFLICT"));
});

test("memory request requires a non-empty memories array", () => {
  const result = validateIngestManifest(memory({ memories: encoded([]) }));
  assert.ok(codes(result).includes("E_MEMORIES_REQUIRED"));
});

for (const field of [
  "document_metadata",
  "app_knowledge",
  "memories",
  "graph_payload",
]) {
  test(`${field} must remain a JSON string`, () => {
    const result = validateIngestManifest(knowledge({ [field]: [] }));
    assert.ok(codes(result).includes("E_JSON_STRING_REQUIRED"));
  });

  test(`${field} rejects malformed nested JSON`, () => {
    const result = validateIngestManifest(knowledge({ [field]: "{" }));
    assert.ok(codes(result).includes("E_JSON_INVALID"));
  });
}

test("document_metadata must be an array", () => {
  const result = validateIngestManifest(
    knowledge({
      documents: ["a.pdf"],
      document_metadata: encoded({ id: "a" }),
    }),
  );
  assert.ok(codes(result).includes("E_DOCUMENT_METADATA_SHAPE"));
});

test("document and metadata counts must align", () => {
  const result = validateIngestManifest(
    knowledge({
      documents: ["a.pdf", "b.pdf"],
      document_metadata: encoded([{ id: "a" }]),
    }),
  );
  assert.ok(codes(result).includes("E_DOCUMENT_METADATA_COUNT"));
});

test("document_metadata may be omitted for inferred defaults", () => {
  const manifest = knowledge({ documents: ["a.pdf"] });
  delete manifest.app_knowledge;
  const result = validateIngestManifest(manifest);
  assert.equal(result.status, "pass");
});

test("invalid local document descriptor fails", () => {
  const result = validateIngestManifest(
    knowledge({ documents: [{}], document_metadata: encoded([{}]) }),
  );
  assert.ok(codes(result).includes("E_DOCUMENT_DESCRIPTOR"));
});

test("empty local document filename fails", () => {
  const result = validateIngestManifest(
    knowledge({ documents: [""], document_metadata: encoded([{}]) }),
  );
  assert.ok(codes(result).includes("E_DOCUMENT_DESCRIPTOR"));
});

test("comma-containing document id fails", () => {
  const result = validateIngestManifest(
    knowledge({
      documents: ["a.pdf"],
      document_metadata: encoded([{ id: "a,b" }]),
    }),
  );
  assert.ok(codes(result).includes("E_ID_COMMA"));
});

test("comma-containing memory id fails", () => {
  const result = validateIngestManifest(
    memory({ memories: encoded([{ id: "a,b", text: "hello" }]) }),
  );
  assert.ok(codes(result).includes("E_ID_COMMA"));
});

test("duplicate explicit source ids fail", () => {
  const result = validateIngestManifest(
    knowledge({
      documents: ["a.pdf"],
      document_metadata: encoded([{ id: "same" }]),
      app_knowledge: encoded([
        {
          id: "same",
          database: "acme",
          collection: "",
          fields: {},
        },
      ]),
    }),
  );
  assert.ok(codes(result).includes("E_ID_DUPLICATE"));
});

test("memory accepts non-empty text", () => {
  assert.equal(validateIngestManifest(memory()).status, "pass");
});

test("memory accepts conversation pairs", () => {
  const result = validateIngestManifest(
    memory({
      memories: encoded([
        {
          user_assistant_pairs: [
            { user: "Question", assistant: "Answer" },
          ],
        },
      ]),
    }),
  );
  assert.equal(result.status, "pass");
});

test("memory with both content representations produces a warning", () => {
  const result = validateIngestManifest(
    memory({
      memories: encoded([
        {
          text: "Question and answer",
          user_assistant_pairs: [
            { user: "Question", assistant: "Answer" },
          ],
        },
      ]),
    }),
  );
  assert.equal(result.status, "pass");
  assert.ok(codes(result).includes("W_MEMORY_MULTIPLE_CONTENT"));
});

test("memory instructions with infer false produce a warning", () => {
  const result = validateIngestManifest(
    memory({
      memories: encoded([
        {
          text: "Already structured",
          infer: false,
          custom_instructions: "Extract a preference",
        },
      ]),
    }),
  );
  assert.equal(result.status, "pass");
  assert.ok(codes(result).includes("W_MEMORY_INSTRUCTIONS_IGNORED"));
});

test("memory instructions with default infer false produce a warning", () => {
  const result = validateIngestManifest(
    memory({
      memories: encoded([
        {
          text: "Already structured",
          custom_instructions: "Extract a preference",
        },
      ]),
    }),
  );
  assert.equal(result.status, "pass");
  assert.ok(codes(result).includes("W_MEMORY_INSTRUCTIONS_IGNORED"));
});

test("is_markdown without text produces a warning", () => {
  const result = validateIngestManifest(
    memory({
      memories: encoded([
        {
          is_markdown: true,
          user_assistant_pairs: [
            { user: "Question", assistant: "Answer" },
          ],
        },
      ]),
    }),
  );
  assert.equal(result.status, "pass");
  assert.ok(codes(result).includes("W_MEMORY_MARKDOWN_WITHOUT_TEXT"));
});

test("memory without text or pairs fails", () => {
  const result = validateIngestManifest(
    memory({ memories: encoded([{ id: "memory-1" }]) }),
  );
  assert.ok(codes(result).includes("E_MEMORY_CONTENT_REQUIRED"));
});

test("malformed conversation pair fails", () => {
  const result = validateIngestManifest(
    memory({
      memories: encoded([
        { user_assistant_pairs: [{ user: "Question" }] },
      ]),
    }),
  );
  assert.ok(codes(result).includes("E_MEMORY_PAIR_CONTENT"));
});

test("memory metadata object fails because metadata is encoded again", () => {
  const result = validateIngestManifest(
    memory({
      memories: encoded([{ text: "hello", metadata: { team: "support" } }]),
    }),
  );
  assert.ok(codes(result).includes("E_MEMORY_METADATA_STRING_REQUIRED"));
});

test("memory metadata accepts an encoded object", () => {
  const result = validateIngestManifest(
    memory({
      memories: encoded([
        { text: "hello", metadata: encoded({ team: "support" }) },
      ]),
    }),
  );
  assert.equal(result.status, "pass");
});

test("memory metadata rejects malformed encoded JSON", () => {
  const result = validateIngestManifest(
    memory({ memories: encoded([{ text: "hello", metadata: "{" }]) }),
  );
  assert.ok(codes(result).includes("E_MEMORY_METADATA_JSON_INVALID"));
});

test("memory additional_metadata accepts an object", () => {
  const result = validateIngestManifest(
    memory({
      memories: encoded([
        { text: "hello", additional_metadata: { source: "settings" } },
      ]),
    }),
  );
  assert.equal(result.status, "pass");
});

test("memory additional_metadata also accepts encoded object form", () => {
  const result = validateIngestManifest(
    memory({
      memories: encoded([
        {
          text: "hello",
          additional_metadata: encoded({ source: "settings" }),
        },
      ]),
    }),
  );
  assert.equal(result.status, "pass");
  assert.ok(
    codes(result).includes("W_MEMORY_ADDITIONAL_METADATA_ENCODED"),
  );
});

test("shared acyclic document descriptors are not reported as cycles", () => {
  const sharedDocument = { filename: "shared.pdf" };
  const result = validateIngestManifest(
    knowledge({ documents: [sharedDocument, sharedDocument] }),
  );

  assert.equal(result.status, "pass");
  assert.equal(codes(result).includes("E_CYCLIC_VALUE"), false);
});

test("cyclic programmatic manifests still fail safely", () => {
  const manifest = knowledge();
  manifest.self = manifest;

  const result = validateIngestManifest(manifest);
  assert.equal(result.status, "fail");
  assert.ok(codes(result).includes("E_CYCLIC_VALUE"));
});

test("shared subtrees count against the depth limit at every path", () => {
  const chain = (depth, tail = {}) => {
    let value = tail;
    for (let index = 0; index < depth; index += 1) {
      value = { child: value };
    }
    return value;
  };

  const shared = chain(80);
  const result = validateIngestManifest(
    knowledge({
      a_shallow: shared,
      z_deep: chain(80, shared),
    }),
  );

  assert.equal(result.status, "fail");
  assert.ok(codes(result).includes("E_STRUCTURE_DEPTH_LIMIT"));
});

test("shared DAG expansion counts against the node limit", () => {
  let shared = { leaf: true };
  for (let depth = 0; depth < 18; depth += 1) {
    shared = { left: shared, right: shared };
  }

  const result = validateIngestManifest(knowledge({ shared }));
  assert.equal(result.status, "fail");
  assert.ok(codes(result).includes("E_STRUCTURE_NODE_LIMIT"));
});

test("deep structures fail safely instead of overflowing the stack", () => {
  const manifest = knowledge();
  let cursor = manifest;
  for (let depth = 0; depth < 2_000; depth += 1) {
    cursor.extra = {};
    cursor = cursor.extra;
  }
  const result = validateIngestManifest(manifest);
  assert.equal(result.status, "fail");
  assert.ok(codes(result).includes("E_STRUCTURE_DEPTH_LIMIT"));
});

test("oversized flat structures fail before full traversal", () => {
  const result = validateIngestManifest(
    knowledge({ documents: Array(200_001).fill("x") }),
  );
  assert.equal(result.status, "fail");
  assert.ok(codes(result).includes("E_STRUCTURE_NODE_LIMIT"));
});

test("graph_payload must be a map", () => {
  const result = validateIngestManifest(
    knowledge({ graph_payload: encoded([]) }),
  );
  assert.ok(codes(result).includes("E_GRAPH_PAYLOAD_SHAPE"));
});

test("graph key must match an explicit same-request source id", () => {
  const result = validateIngestManifest(
    knowledge({
      graph_payload: encoded({ missing: simpleGraph() }),
    }),
  );
  assert.ok(codes(result).includes("E_GRAPH_UNKNOWN_SOURCE"));
  assert.equal(
    diagnostic(result, "E_GRAPH_UNKNOWN_SOURCE").path,
    "graph_payload.*",
  );
});

test("empty generated app id cannot bind graph_payload", () => {
  const result = validateIngestManifest(
    knowledge({
      app_knowledge: encoded([
        { id: "", database: "acme", collection: "", fields: {} },
      ]),
      graph_payload: encoded({ "": simpleGraph() }),
    }),
  );
  assert.ok(codes(result).includes("E_GRAPH_UNKNOWN_SOURCE"));
});

test("graph entities must be a map", () => {
  const result = validateIngestManifest(
    graphManifest({ entities: [], relations: [] }),
  );
  assert.ok(codes(result).includes("E_GRAPH_ENTITIES_SHAPE"));
});

test("graph relations must be an array", () => {
  const result = validateIngestManifest(
    graphManifest({ entities: {}, relations: {} }),
  );
  assert.ok(codes(result).includes("E_GRAPH_RELATIONS_SHAPE"));
});

test("graph entity requires a name", () => {
  const result = validateIngestManifest(
    graphManifest({
      entities: { left: {} },
      relations: [],
    }),
  );
  assert.ok(codes(result).includes("E_GRAPH_ENTITY_NAME_REQUIRED"));
  assert.equal(
    diagnostic(result, "E_GRAPH_ENTITY_NAME_REQUIRED").path,
    "graph_payload.*.entities.*.name",
  );
});

test("relation source must reference an entity key", () => {
  const result = validateIngestManifest(
    graphManifest(
      simpleGraph({
        relations: [
          { source: "missing", target: "right", predicate: "RELATES_TO" },
        ],
      }),
    ),
  );
  assert.ok(codes(result).includes("E_GRAPH_SOURCE_UNKNOWN_ENTITY"));
  assert.equal(
    diagnostic(result, "E_GRAPH_SOURCE_UNKNOWN_ENTITY").path,
    "graph_payload.*.relations[0].source",
  );
});

test("relation target must reference an entity key", () => {
  const result = validateIngestManifest(
    graphManifest(
      simpleGraph({
        relations: [
          { source: "left", target: "missing", predicate: "RELATES_TO" },
        ],
      }),
    ),
  );
  assert.ok(codes(result).includes("E_GRAPH_TARGET_UNKNOWN_ENTITY"));
});

test("entity name at 256 characters passes", () => {
  const graph = simpleGraph();
  graph.entities.left.name = "n".repeat(LIMITS.entityNameCharacters);
  assert.equal(validateIngestManifest(graphManifest(graph)).status, "pass");
});

test("entity name above 256 characters fails", () => {
  const graph = simpleGraph();
  graph.entities.left.name = "n".repeat(LIMITS.entityNameCharacters + 1);
  assert.ok(
    codes(validateIngestManifest(graphManifest(graph))).includes(
      "E_GRAPH_ENTITY_NAME_LIMIT",
    ),
  );
});

test("Unicode entity limit counts characters rather than UTF-16 units", () => {
  const graph = simpleGraph();
  graph.entities.left.name = "😀".repeat(LIMITS.entityNameCharacters);
  assert.equal(validateIngestManifest(graphManifest(graph)).status, "pass");
});

test("predicate at 256 characters passes", () => {
  const graph = simpleGraph();
  graph.relations[0].predicate = "p".repeat(LIMITS.predicateCharacters);
  assert.equal(validateIngestManifest(graphManifest(graph)).status, "pass");
});

test("predicate above 256 characters fails", () => {
  const graph = simpleGraph();
  graph.relations[0].predicate = "p".repeat(
    LIMITS.predicateCharacters + 1,
  );
  assert.ok(
    codes(validateIngestManifest(graphManifest(graph))).includes(
      "E_GRAPH_PREDICATE_LIMIT",
    ),
  );
});

test("relation context at 2,000 characters passes", () => {
  const graph = simpleGraph();
  graph.relations[0].context = "c".repeat(
    LIMITS.relationContextCharacters,
  );
  assert.equal(validateIngestManifest(graphManifest(graph)).status, "pass");
});

test("relation context above 2,000 characters fails", () => {
  const graph = simpleGraph();
  graph.relations[0].context = "c".repeat(
    LIMITS.relationContextCharacters + 1,
  );
  assert.ok(
    codes(validateIngestManifest(graphManifest(graph))).includes(
      "E_GRAPH_CONTEXT_LIMIT",
    ),
  );
});

function entityBoundaryGraph(count) {
  const entities = {};
  const relations = [];
  for (let index = 0; index < count; index += 1) {
    entities[`entity-${index}`] = { name: `Entity ${index}` };
    if (index > 0) {
      relations.push({
        source: `entity-${index - 1}`,
        target: `entity-${index}`,
        predicate: "NEXT",
      });
    }
  }
  return { entities, relations };
}

test("exact 5,000-entity boundary passes", () => {
  const result = validateIngestManifest(
    graphManifest(entityBoundaryGraph(LIMITS.entitiesPerGraph)),
  );
  assert.equal(result.status, "pass");
  assert.equal(result.summary.entities, LIMITS.entitiesPerGraph);
});

test("5,001 entities fail", () => {
  const result = validateIngestManifest(
    graphManifest(entityBoundaryGraph(LIMITS.entitiesPerGraph + 1)),
  );
  assert.ok(codes(result).includes("E_GRAPH_ENTITY_LIMIT"));
});

function relationBoundaryGraph(count) {
  const entities = {};
  for (let index = 0; index < 40; index += 1) {
    entities[`entity-${index}`] = { name: `Entity ${index}` };
  }
  const relations = [];
  for (let index = 0; index < count; index += 1) {
    relations.push({
      source: `entity-${index % 40}`,
      target: `entity-${(index + 1) % 40}`,
      predicate: `RELATION_${index}`,
    });
  }
  return { entities, relations };
}

test("exact 10,000-relation and 500-degree boundaries pass", () => {
  const result = validateIngestManifest(
    graphManifest(relationBoundaryGraph(LIMITS.relationsPerGraph)),
  );
  assert.equal(result.status, "pass");
  assert.equal(result.summary.relations, LIMITS.relationsPerGraph);
});

test("combined relation and context boundaries fit the local safety ceiling", () => {
  const graph = relationBoundaryGraph(LIMITS.relationsPerGraph);
  for (const relation of graph.relations) {
    relation.context = "c".repeat(LIMITS.relationContextCharacters);
  }
  const manifest = graphManifest(graph);
  const encodedBytes = Buffer.byteLength(JSON.stringify(manifest), "utf8");

  assert.ok(encodedBytes > 16 * 1024 * 1024);
  assert.ok(encodedBytes < LOCAL_SAFETY_LIMITS.manifestBytes);

  const result = validateIngestManifest(manifest);
  assert.equal(result.status, "pass");
  assert.equal(result.summary.relations, LIMITS.relationsPerGraph);
});

test("10,001 relations fail", () => {
  const result = validateIngestManifest(
    graphManifest(relationBoundaryGraph(LIMITS.relationsPerGraph + 1)),
  );
  assert.ok(codes(result).includes("E_GRAPH_RELATION_LIMIT"));
});

function degreeGraph(count) {
  const entities = { center: { name: "Center" } };
  const relations = [];
  for (let index = 0; index < count; index += 1) {
    const key = `leaf-${index}`;
    entities[key] = { name: `Leaf ${index}` };
    relations.push({
      source: "center",
      target: key,
      predicate: `RELATION_${index}`,
    });
  }
  return { entities, relations };
}

test("exact 500 incident relations per entity pass", () => {
  const result = validateIngestManifest(
    graphManifest(degreeGraph(LIMITS.relationsPerEntity)),
  );
  assert.equal(result.status, "pass");
});

test("501 incident relations per entity fail", () => {
  const result = validateIngestManifest(
    graphManifest(degreeGraph(LIMITS.relationsPerEntity + 1)),
  );
  assert.ok(codes(result).includes("E_GRAPH_DEGREE_LIMIT"));
});

test("a self-loop counts as one incident relation", () => {
  const entities = { center: { name: "Center" } };
  const relations = Array.from(
    { length: LIMITS.relationsPerEntity },
    (_, index) => ({
      source: "center",
      target: "center",
      predicate: `SELF_${index}`,
    }),
  );
  const result = validateIngestManifest(
    graphManifest({ entities, relations }),
  );
  assert.equal(result.status, "pass");
});

test("orphan entity produces a warning without failing", () => {
  const result = validateIngestManifest(
    graphManifest({
      entities: { orphan: { name: "Orphan" } },
      relations: [],
    }),
  );
  assert.equal(result.status, "pass");
  assert.ok(codes(result).includes("W_GRAPH_ORPHAN_ENTITY"));
});

test("lowercase-normalized name collision produces a warning", () => {
  const result = validateIngestManifest(
    graphManifest({
      entities: {
        one: { name: "ALICE" },
        two: { name: "alice" },
      },
      relations: [{ source: "one", target: "two", predicate: "KNOWS" }],
    }),
  );
  assert.equal(result.status, "pass");
  assert.ok(codes(result).includes("W_GRAPH_NORMALIZED_NAME_COLLISION"));
});

test("duplicate relation produces a warning", () => {
  const graph = simpleGraph();
  graph.relations.push({ ...graph.relations[0] });
  const result = validateIngestManifest(graphManifest(graph));
  assert.equal(result.status, "pass");
  assert.ok(codes(result).includes("W_GRAPH_DUPLICATE_RELATION"));
});

test("prototype-affecting graph key is rejected", () => {
  const result = validateIngestManifest(
    knowledge({
      graph_payload:
        '{"app-1":{"entities":{"__proto__":{"name":"hidden"}},"relations":[]}}',
    }),
  );
  assert.ok(codes(result).includes("E_UNSAFE_OBJECT_KEY"));
});

test("prototype-affecting nested memory metadata key is rejected", () => {
  const result = validateIngestManifest(
    memory({
      memories: encoded([
        { text: "hello", metadata: '{"constructor":"hidden"}' },
      ]),
    }),
  );
  assert.ok(codes(result).includes("E_UNSAFE_OBJECT_KEY"));
});

test("diagnostic ordering is deterministic across object insertion order", () => {
  const first = {
    z: simpleGraph({
      relations: [
        { source: "missing", target: "missing", predicate: "RELATES_TO" },
      ],
    }),
    a: simpleGraph({
      relations: [
        { source: "missing", target: "missing", predicate: "RELATES_TO" },
      ],
    }),
  };
  const second = { a: first.a, z: first.z };
  const left = validateIngestManifest(
    knowledge({ graph_payload: encoded(first) }),
  );
  const right = validateIngestManifest(
    knowledge({ graph_payload: encoded(second) }),
  );
  assert.deepEqual(left, right);
});

test("human and JSON diagnostics never echo untrusted values", () => {
  const sentinels = [
    "SECRET_SOURCE_ID",
    "SECRET_ENTITY_KEY",
    "SECRET_FILENAME",
    "SECRET_MEMORY_TEXT",
    "https://secret.invalid/private",
    "SECRET_RAW_VALUE",
  ];
  const manifest = {
    type: "knowledge",
    database: "",
    documents: ["SECRET_FILENAME"],
    document_metadata: encoded([
      {
        id: "SECRET_SOURCE_ID,invalid",
        url: "https://secret.invalid/private",
        metadata: { raw: "SECRET_RAW_VALUE" },
      },
    ]),
    graph_payload: encoded({
      SECRET_SOURCE_ID: {
        entities: {
          SECRET_ENTITY_KEY: { name: "" },
        },
        relations: [
          {
            source: "SECRET_ENTITY_KEY",
            target: "missing",
            predicate: "",
            context: "SECRET_MEMORY_TEXT",
          },
        ],
      },
    }),
  };
  const result = validateIngestManifest(manifest);
  const output = `${formatHuman(result)}\n${JSON.stringify(result)}`;
  for (const sentinel of sentinels) {
    assert.equal(output.includes(sentinel), false);
  }
});

test("dynamic graph and entity paths use wildcards", () => {
  const result = validateIngestManifest(
    knowledge({
      graph_payload: encoded({
        "private-source": {
          entities: { "private-entity": {} },
          relations: [],
        },
      }),
    }),
  );
  for (const item of result.diagnostics) {
    assert.equal(item.path.includes("private-source"), false);
    assert.equal(item.path.includes("private-entity"), false);
  }
  assert.ok(
    result.diagnostics.some((item) =>
      item.path.startsWith("graph_payload.*.entities.*"),
    ),
  );
});

test("invalid fixture reports the three intended validation errors", async () => {
  const result = validateIngestManifest(await fixture("invalid.json"));
  assert.equal(result.status, "fail");
  assert.deepEqual(codes(result), [
    "E_DOCUMENT_METADATA_COUNT",
    "E_GRAPH_TARGET_UNKNOWN_ENTITY",
    "E_GRAPH_UNKNOWN_SOURCE",
  ]);
});

test("repaired fixture passes with the intended summary", async () => {
  const result = validateIngestManifest(await fixture("repaired.json"));
  assert.equal(result.status, "pass");
  assert.deepEqual(result.summary, {
    sources: 3,
    graphs: 1,
    entities: 2,
    relations: 1,
    errors: 0,
    warnings: 0,
    diagnosticsShown: 0,
    diagnosticsTruncated: false,
  });
});

test("human formatter starts with PASS for a valid manifest", () => {
  assert.match(
    formatHuman(validateIngestManifest(knowledge())),
    /^PASS — ingest manifest is internally consistent/,
  );
});

test("human formatter starts with FAIL for an invalid manifest", () => {
  assert.match(
    formatHuman(validateIngestManifest({})),
    /^FAIL — ingest manifest has validation errors/,
  );
});

test("the manifest byte ceiling is an explicit local safety policy", () => {
  assert.equal(LOCAL_SAFETY_LIMITS.manifestBytes, 128 * 1024 * 1024);
});

test("CLI returns 0 and human output for valid fixture", async () => {
  const capture = captureIo();
  const exitCode = await runCli(
    [join(examplesDirectory, "valid-knowledge.json")],
    capture.io,
  );
  const output = capture.output();
  assert.equal(exitCode, 0);
  assert.match(output.stdout, /^PASS/);
  assert.equal(output.stderr, "");
});

test("CLI returns 1 for validation errors", async () => {
  const capture = captureIo();
  const exitCode = await runCli(
    [join(examplesDirectory, "invalid.json")],
    capture.io,
  );
  assert.equal(exitCode, 1);
  assert.match(capture.output().stdout, /^FAIL/);
});

test("CLI returns 0 for the repaired fixture", async () => {
  const capture = captureIo();
  const exitCode = await runCli(
    [join(examplesDirectory, "repaired.json")],
    capture.io,
  );
  assert.equal(exitCode, 0);
  assert.match(capture.output().stdout, /^PASS/);
  assert.equal(capture.output().stderr, "");
});

test("CLI --json returns machine-readable diagnostics", async () => {
  const capture = captureIo();
  const exitCode = await runCli(
    [join(examplesDirectory, "invalid.json"), "--json"],
    capture.io,
  );
  assert.equal(exitCode, 1);
  const parsed = JSON.parse(capture.output().stdout);
  assert.equal(parsed.status, "fail");
  assert.ok(Array.isArray(parsed.diagnostics));
  assert.deepEqual(Object.keys(parsed.diagnostics[0]), [
    "severity",
    "code",
    "path",
    "message",
  ]);
});

test("CLI accepts --json before the fixture path", async () => {
  const capture = captureIo();
  const exitCode = await runCli(
    ["--json", join(examplesDirectory, "valid-memory.json")],
    capture.io,
  );
  assert.equal(exitCode, 0);
  assert.equal(JSON.parse(capture.output().stdout).status, "pass");
});

test("CLI returns 2 when the file cannot be read", async () => {
  const capture = captureIo();
  const exitCode = await runCli(["does-not-exist.json"], capture.io);
  assert.equal(exitCode, 2);
  assert.equal(capture.output().stdout, "");
  assert.match(capture.output().stderr, /Could not read/);
});

test("CLI returns 2 for malformed top-level JSON", async () => {
  const directory = await mkdtemp(join(tmpdir(), "ingest-doctor-"));
  const path = join(directory, "malformed.json");
  await writeFile(path, "{", "utf8");
  const capture = captureIo();
  const exitCode = await runCli([path], capture.io);
  assert.equal(exitCode, 2);
  assert.match(capture.output().stderr, /not valid JSON/);
});

test("CLI rejects files above the local safety ceiling before reading", async () => {
  const directory = await mkdtemp(join(tmpdir(), "ingest-doctor-"));
  const path = join(directory, "oversized.json");
  await writeFile(path, "", "utf8");
  await truncate(path, LOCAL_SAFETY_LIMITS.manifestBytes + 1);

  const capture = captureIo();
  const exitCode = await runCli([path], capture.io);
  assert.equal(exitCode, 2);
  assert.equal(capture.output().stdout, "");
  assert.match(capture.output().stderr, /128 MiB local safety limit/);
});

test("CLI returns 2 when no path is provided", async () => {
  const capture = captureIo();
  assert.equal(await runCli([], capture.io), 2);
  assert.match(capture.output().stderr, /^Usage:/);
});

test("CLI help returns 0", async () => {
  const capture = captureIo();
  assert.equal(await runCli(["--help"], capture.io), 0);
  assert.match(capture.output().stdout, /^Usage:/);
});
