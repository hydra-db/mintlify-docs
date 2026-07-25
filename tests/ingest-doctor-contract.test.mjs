import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { LIMITS } from "../examples/ingest-doctor/validate.mjs";
import { MULTIPART_FIELD_ORDER } from "../examples/ingest-doctor/prepare-multipart.mjs";

const openApiPath = fileURLToPath(
  new URL("../api-reference/v2/openapi.json", import.meta.url),
);
const bringYourOwnGraphPath = fileURLToPath(
  new URL("../essentials/v2/bring-your-own-graph.mdx", import.meta.url),
);

function normalizeTableCell(value) {
  return value
    .replaceAll("`", "")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

function parseLimitValue(value) {
  const match = value.match(/(?:≤|<=)\s*([\d,]+)/u);
  assert.ok(match, `Expected an upper-bound value, received: ${value}`);
  return Number(match[1].replaceAll(",", ""));
}

function readLimitTable(markdown) {
  const limitsSection = markdown.match(
    /^##\s+\d+\.\s+Limits\s*$([\s\S]*?)(?=^##\s+|^---\s*$|(?![\s\S]))/mu,
  );
  assert.ok(limitsSection, "Expected the BYOG Limits section");

  const rows = new Map();
  for (const line of limitsSection[1].split("\n")) {
    if (!line.trim().startsWith("|")) {
      continue;
    }

    const cells = line
      .split("|")
      .slice(1, -1)
      .map((cell) => cell.trim());
    if (
      cells.length !== 2 ||
      normalizeTableCell(cells[0]) === "limit" ||
      /^-+$/.test(cells[0])
    ) {
      continue;
    }

    rows.set(normalizeTableCell(cells[0]), parseLimitValue(cells[1]));
  }

  return rows;
}

test("Ingest Doctor stays aligned with the /context/ingest OpenAPI contract", async () => {
  const openApi = JSON.parse(await readFile(openApiPath, "utf8"));
  const ingest = openApi.paths?.["/context/ingest"]?.post;

  assert.ok(ingest, "Expected POST /context/ingest in the OpenAPI document");
  assert.equal(ingest.requestBody?.required, true);

  const multipart =
    ingest.requestBody?.content?.["multipart/form-data"]?.schema;
  assert.ok(multipart, "Expected a multipart/form-data request schema");
  assert.equal(multipart.type, "object");
  assert.deepEqual(multipart.required, ["database"]);

  const fields = multipart.properties;
  assert.ok(fields, "Expected multipart form properties");

  for (const name of MULTIPART_FIELD_ORDER) {
    assert.ok(
      fields[name],
      `Expected multipart helper field ${name} in the OpenAPI document`,
    );
  }

  assert.equal(fields.type?.type, "string");
  assert.equal(fields.type?.default, "knowledge");
  assert.deepEqual(fields.type?.enum, ["knowledge", "memory"]);
  assert.equal(fields.database?.type, "string");
  assert.equal(fields.collection?.type, "string");
  assert.equal(fields.upsert?.type, "string");
  assert.equal(fields.upsert?.default, "true");

  for (const name of [
    "document_metadata",
    "app_knowledge",
    "memories",
    "graph_payload",
  ]) {
    assert.equal(
      fields[name]?.type,
      "string",
      `Expected ${name} to remain a JSON-encoded string form field`,
    );
  }

  assert.equal(fields.documents?.type, "string");
  assert.equal(fields.documents?.format, "binary");

  for (const [alias, replacement] of [
    ["tenant_id", "database"],
    ["sub_tenant_id", "collection"],
  ]) {
    assert.equal(fields[alias]?.type, "string");
    assert.equal(
      fields[alias]?.deprecated,
      true,
      `Expected ${alias} to remain a deprecated alias for ${replacement}`,
    );
    assert.equal(fields[alias]?.["x-deprecated"], "true");
    assert.equal(
      fields[replacement]?.deprecated,
      undefined,
      `Expected ${replacement} to remain the canonical field`,
    );
  }
});

test("Ingest Doctor graph limits match the documented BYOG limits table", async () => {
  const markdown = await readFile(bringYourOwnGraphPath, "utf8");
  const documented = readLimitTable(markdown);

  assert.equal(documented.size, 5, "Expected all five documented BYOG limits");
  assert.equal(LIMITS.entitiesPerGraph, documented.get("entities"));
  assert.equal(LIMITS.relationsPerGraph, documented.get("relations"));
  assert.equal(
    LIMITS.relationsPerEntity,
    documented.get("relations per entity (degree)"),
  );
  assert.equal(
    LIMITS.relationContextCharacters,
    documented.get("context length"),
  );

  const sharedNamePredicateLimit = documented.get("name / predicate length");
  assert.equal(LIMITS.entityNameCharacters, sharedNamePredicateLimit);
  assert.equal(LIMITS.predicateCharacters, sharedNamePredicateLimit);
});
