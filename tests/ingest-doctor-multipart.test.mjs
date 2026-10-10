import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import {
  MULTIPART_FIELD_ORDER,
  prepareValidatedMultipart,
} from "../examples/ingest-doctor/prepare-multipart.mjs";
import { runDemo } from "../examples/ingest-doctor/prepare-demo.mjs";

const examplesDirectory = fileURLToPath(
  new URL("../examples/ingest-doctor/", import.meta.url),
);

async function fixture(name) {
  return JSON.parse(
    await readFile(new URL(name, `file://${examplesDirectory}/`), "utf8"),
  );
}

function documentLoader(contents = ["first", "second"]) {
  return async (_descriptor, index) =>
    new Blob([contents[index] ?? `document-${index}`], {
      type: "application/octet-stream",
    });
}

function preparationCodes(result) {
  return result.preparationDiagnostics.map(({ code }) => code);
}

test("multipart demo prints only the safe part summary", async () => {
  let stdout = "";
  let stderr = "";
  const exitCode = await runDemo({
    stdout: (text) => {
      stdout += text;
    },
    stderr: (text) => {
      stderr += text;
    },
  });

  assert.equal(exitCode, 0);
  assert.equal(stderr, "");
  assert.deepEqual(JSON.parse(stdout), {
    status: "ready",
    fieldNames: [
      "type",
      "database",
      "collection",
      "documents",
      "documents",
      "document_metadata",
      "app_knowledge",
      "graph_payload",
    ],
    documentCount: 2,
    partCount: 8,
  });
  assert.equal(stdout.includes("synthetic document bytes"), false);
  assert.equal(stdout.includes("billing-policy.pdf"), false);
});

test("multipart field order is explicit and stable", () => {
  assert.deepEqual(MULTIPART_FIELD_ORDER, [
    "type",
    "database",
    "collection",
    "upsert",
    "documents",
    "document_metadata",
    "app_knowledge",
    "memories",
    "graph_payload",
  ]);
});

test("knowledge manifest becomes ordered multipart with unchanged opaque strings", async () => {
  const manifest = await fixture("valid-knowledge.json");
  const result = await prepareValidatedMultipart(manifest, {
    loadDocument: documentLoader(),
  });

  assert.equal(result.status, "ready");
  assert.deepEqual(result.partSummary, {
    fieldNames: [
      "type",
      "database",
      "collection",
      "upsert",
      "documents",
      "document_metadata",
      "app_knowledge",
      "graph_payload",
    ],
    documentCount: 1,
    partCount: 8,
  });

  const entries = [...result.formData.entries()];
  assert.deepEqual(
    entries.map(([name]) => name),
    result.partSummary.fieldNames,
  );
  assert.equal(entries[5][1], manifest.document_metadata);
  assert.equal(entries[6][1], manifest.app_knowledge);
  assert.equal(entries[7][1], manifest.graph_payload);
  assert.equal(entries[4][1] instanceof Blob, true);
  assert.equal(entries[4][1].name, "billing-policy.pdf");
  assert.equal(await entries[4][1].text(), "first");
});

test("memory manifest needs no loader and contains no document parts", async () => {
  const manifest = await fixture("valid-memory.json");
  const result = await prepareValidatedMultipart(manifest);

  assert.equal(result.status, "ready");
  assert.deepEqual(result.partSummary.fieldNames, [
    "type",
    "database",
    "collection",
    "memories",
    "graph_payload",
  ]);
  assert.equal(result.partSummary.documentCount, 0);
  assert.equal(result.formData.has("documents"), false);
  assert.equal(result.formData.get("memories"), manifest.memories);
});

test("invalid manifest blocks before invoking the document loader", async () => {
  const manifest = await fixture("invalid.json");
  let calls = 0;
  const result = await prepareValidatedMultipart(manifest, {
    loadDocument: async () => {
      calls += 1;
      return new Blob([]);
    },
  });

  assert.equal(result.status, "blocked");
  assert.equal(result.validation.status, "fail");
  assert.equal(calls, 0);
  assert.equal(result.formData, null);
  assert.deepEqual(result.preparationDiagnostics, []);
});

test("validation warnings still permit canonical multipart preparation", async () => {
  const manifest = await fixture("valid-knowledge.json");
  manifest.tenant_id = manifest.database;
  manifest.sub_tenant_id = manifest.collection;
  delete manifest.database;
  delete manifest.collection;

  const result = await prepareValidatedMultipart(manifest, {
    loadDocument: documentLoader(),
  });

  assert.equal(result.status, "ready");
  assert.equal(result.validation.summary.warnings, 2);
  assert.equal(result.formData.get("database"), "acme_corp");
  assert.equal(result.formData.get("collection"), "team_docs");
  assert.equal(result.formData.has("tenant_id"), false);
  assert.equal(result.formData.has("sub_tenant_id"), false);
});

test("unknown top-level fields block instead of being silently dropped", async () => {
  const manifest = await fixture("valid-knowledge.json");
  manifest.future_private_field = "do-not-echo";
  let calls = 0;
  const result = await prepareValidatedMultipart(manifest, {
    loadDocument: async () => {
      calls += 1;
      return new Blob([]);
    },
  });

  assert.equal(result.status, "blocked");
  assert.deepEqual(preparationCodes(result), [
    "E_MULTIPART_FIELD_UNSUPPORTED",
  ]);
  assert.equal(calls, 0);
  assert.equal(JSON.stringify(result).includes("future_private_field"), false);
  assert.equal(JSON.stringify(result).includes("do-not-echo"), false);
});

for (const [label, malformed] of [
  ["unpaired high surrogate", "\ud800"],
  ["unpaired low surrogate", "\udc00"],
]) {
  test(`${label} blocks before FormData can normalize it`, async () => {
    const manifest = await fixture("valid-memory.json");
    manifest.memories = `[{"id":"memory-1","text":"${malformed}"}]`;
    const result = await prepareValidatedMultipart(manifest);

    assert.equal(result.validation.status, "pass");
    assert.equal(result.status, "blocked");
    assert.deepEqual(preparationCodes(result), [
      "E_MULTIPART_STRING_ENCODING",
    ]);
    assert.equal(result.formData, null);
  });
}

test("distinct malformed source ids cannot collapse during FormData conversion", async () => {
  const manifest = await fixture("valid-memory.json");
  manifest.memories =
    '[{"id":"\ud800","text":"first"},{"id":"\ud801","text":"second"}]';
  delete manifest.graph_payload;
  const result = await prepareValidatedMultipart(manifest);

  assert.equal(result.validation.status, "pass");
  assert.equal(result.validation.summary.sources, 2);
  assert.equal(result.status, "blocked");
  assert.deepEqual(preparationCodes(result), [
    "E_MULTIPART_STRING_ENCODING",
  ]);
});

test("missing document loader blocks with a stable diagnostic", async () => {
  const result = await prepareValidatedMultipart(
    await fixture("valid-knowledge.json"),
  );
  assert.equal(result.status, "blocked");
  assert.deepEqual(preparationCodes(result), ["E_DOCUMENT_LOADER_REQUIRED"]);
});

test("non-Blob loader output blocks safely", async () => {
  const result = await prepareValidatedMultipart(
    await fixture("valid-knowledge.json"),
    { loadDocument: async () => Buffer.from("not a Blob") },
  );
  assert.equal(result.status, "blocked");
  assert.deepEqual(preparationCodes(result), ["E_DOCUMENT_BLOB_REQUIRED"]);
  assert.equal(result.formData, null);
});

test("loader failures do not expose raw error text or filenames", async () => {
  const manifest = await fixture("valid-knowledge.json");
  const result = await prepareValidatedMultipart(manifest, {
    loadDocument: async () => {
      throw new Error("private-loader-detail");
    },
  });
  const encoded = JSON.stringify(result);

  assert.equal(result.status, "blocked");
  assert.deepEqual(preparationCodes(result), ["E_DOCUMENT_LOAD"]);
  assert.equal(encoded.includes("private-loader-detail"), false);
  assert.equal(encoded.includes("billing-policy.pdf"), false);
});

test("multipart upload names strip POSIX and Windows local paths", async () => {
  const manifest = await fixture("repaired.json");
  manifest.documents = [
    "/private/customer/billing-policy.pdf",
    String.raw`C:\private\customer\deploy-runbook.pdf`,
  ];
  const loaderDescriptors = [];
  const result = await prepareValidatedMultipart(manifest, {
    loadDocument: async (descriptor, index) => {
      loaderDescriptors.push(descriptor);
      assert.equal(Object.isFrozen(descriptor), true);
      return documentLoader()(descriptor, index);
    },
  });

  assert.equal(result.status, "ready");
  assert.deepEqual(
    result.formData.getAll("documents").map((file) => file.name),
    ["billing-policy.pdf", "deploy-runbook.pdf"],
  );
  assert.deepEqual(loaderDescriptors, [
    { filename: "/private/customer/billing-policy.pdf" },
    { filename: String.raw`C:\private\customer\deploy-runbook.pdf` },
  ]);
});

test("unsafe upload names block before any document read", async () => {
  const manifest = await fixture("repaired.json");
  manifest.documents[1] = "private\nname.pdf";
  let calls = 0;
  const result = await prepareValidatedMultipart(manifest, {
    loadDocument: async () => {
      calls += 1;
      return new Blob([]);
    },
  });

  assert.equal(result.status, "blocked");
  assert.deepEqual(preparationCodes(result), ["E_DOCUMENT_UPLOAD_NAME"]);
  assert.equal(calls, 0);
});

test("malformed Unicode upload names block before any document read", async () => {
  const manifest = await fixture("repaired.json");
  manifest.documents[1] = "private-\ud800.pdf";
  let calls = 0;
  const result = await prepareValidatedMultipart(manifest, {
    loadDocument: async () => {
      calls += 1;
      return new Blob([]);
    },
  });

  assert.equal(result.validation.status, "pass");
  assert.equal(result.status, "blocked");
  assert.deepEqual(preparationCodes(result), ["E_DOCUMENT_UPLOAD_NAME"]);
  assert.equal(calls, 0);
});

test("boolean and string upsert values have deterministic wire encoding", async () => {
  const manifest = await fixture("valid-memory.json");
  manifest.upsert = false;
  const booleanResult = await prepareValidatedMultipart(manifest);
  assert.equal(booleanResult.formData.get("upsert"), "false");

  manifest.upsert = "1";
  const stringResult = await prepareValidatedMultipart(manifest);
  assert.equal(stringResult.formData.get("upsert"), "1");
});

test("manifest mutation during loading cannot alter the prepared request", async () => {
  const manifest = await fixture("repaired.json");
  const originalGraph = manifest.graph_payload;
  const result = await prepareValidatedMultipart(manifest, {
    loadDocument: async (_descriptor, index) => {
      if (index === 0) {
        manifest.database = "mutated";
        manifest.graph_payload = "{}";
        manifest.documents[1] = "mutated.txt";
      }
      return new Blob([`document-${index}`]);
    },
  });

  assert.equal(result.status, "ready");
  assert.equal(result.formData.get("database"), "acme_corp");
  assert.equal(result.formData.get("graph_payload"), originalGraph);
  assert.deepEqual(
    result.formData.getAll("documents").map((file) => file.name),
    ["billing-policy.pdf", "deploy-runbook.pdf"],
  );
});

test("memory preparation invokes no loader or transport hook", async () => {
  const manifest = await fixture("valid-memory.json");
  const originalFetch = globalThis.fetch;
  let fetchCalls = 0;
  let loaderCalls = 0;
  globalThis.fetch = async () => {
    fetchCalls += 1;
    throw new Error("network should not be used");
  };

  try {
    const result = await prepareValidatedMultipart(manifest, {
      loadDocument: async () => {
        loaderCalls += 1;
        return new Blob([]);
      },
    });
    assert.equal(result.status, "ready");
    assert.equal(fetchCalls, 0);
    assert.equal(loaderCalls, 0);
    assert.equal(result.formData.has("authorization"), false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
