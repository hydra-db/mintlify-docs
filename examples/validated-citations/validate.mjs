#!/usr/bin/env node

import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const ANSWERED_KEYS = new Set(["status", "claims"]);
const ABSTENTION_KEYS = new Set(["status"]);
const CLAIM_KEYS = new Set(["text", "evidence_chunk_ids"]);
const EVIDENCE_EXCERPT_LIMIT = 240;

function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasExactKeys(value, expected) {
  const keys = Object.keys(value);
  return keys.length === expected.size && keys.every((key) => expected.has(key));
}

function diagnostic(code, path, message) {
  return {
    ok: false,
    errors: [{ code, path, message }],
  };
}

function queryError(path, message) {
  return diagnostic("INVALID_QUERY_RESULT", path, message);
}

function optionalText(value) {
  if (typeof value !== "string") {
    return undefined;
  }
  return value.trim().length > 0 ? value : undefined;
}

function optionalIdentifier(value) {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function additionalContextPath() {
  return "$.additional_context[*]";
}

function normalizeChunk(chunk, path, canonicalId) {
  if (!isRecord(chunk)) {
    return {
      error: queryError(path, "A retrieval chunk must be a JSON object."),
    };
  }

  const chunkUuid = canonicalId ?? chunk.chunk_uuid;
  if (typeof chunkUuid !== "string" || chunkUuid.length === 0) {
    return {
      error: queryError(
        `${path}.chunk_uuid`,
        "A retrieval chunk must have a nonempty string chunk_uuid.",
      ),
    };
  }

  if (
    chunk.id !== undefined &&
    chunk.id !== null &&
    typeof chunk.id !== "string"
  ) {
    return {
      error: queryError(
        `${path}.id`,
        "A retrieval chunk id must be a string when present.",
      ),
    };
  }
  if (
    chunk.source_title !== undefined &&
    chunk.source_title !== null &&
    typeof chunk.source_title !== "string"
  ) {
    return {
      error: queryError(
        `${path}.source_title`,
        "A retrieval source_title must be a string when present.",
      ),
    };
  }
  if (
    typeof chunk.chunk_content !== "string" ||
    chunk.chunk_content.trim().length === 0
  ) {
    return {
      error: queryError(
        `${path}.chunk_content`,
        "A citable retrieval chunk must contain nonempty string content.",
      ),
    };
  }

  const extraContextIds = chunk.extra_context_ids ?? [];
  if (!Array.isArray(extraContextIds)) {
    return {
      error: queryError(
        `${path}.extra_context_ids`,
        "extra_context_ids must be an array when present.",
      ),
    };
  }
  const uniqueExtraContextIds = [];
  const seenExtraContextIds = new Set();
  for (let index = 0; index < extraContextIds.length; index += 1) {
    const extraId = extraContextIds[index];
    if (typeof extraId !== "string" || extraId.length === 0) {
      return {
        error: queryError(
          `${path}.extra_context_ids[${index}]`,
          "Each additional-context reference must be a nonempty string.",
        ),
      };
    }
    if (!seenExtraContextIds.has(extraId)) {
      seenExtraContextIds.add(extraId);
      uniqueExtraContextIds.push(extraId);
    }
  }

  return {
    value: {
      chunkUuid,
      sourceId: optionalIdentifier(chunk.id),
      sourceTitle: optionalText(chunk.source_title),
      chunkContent: chunk.chunk_content,
      extraContextIds: uniqueExtraContextIds,
    },
  };
}

function sameStringSet(left, right) {
  if (left.length !== right.length) {
    return false;
  }
  const rightValues = new Set(right);
  return left.every((value) => rightValues.has(value));
}

function sameEvidence(left, right) {
  return (
    left.sourceId === right.sourceId &&
    left.sourceTitle === right.sourceTitle &&
    left.chunkContent === right.chunkContent &&
    sameStringSet(left.extraContextIds, right.extraContextIds)
  );
}

function registerEvidence(registry, evidence, path) {
  const existing = registry.get(evidence.chunkUuid);
  if (!existing) {
    registry.set(evidence.chunkUuid, evidence);
    return undefined;
  }
  if (!sameEvidence(existing, evidence)) {
    return diagnostic(
      "DUPLICATE_RETRIEVAL_ID",
      path,
      "The same evidence ID maps to conflicting retrieval data.",
    );
  }
  return undefined;
}

function unwrapQueryResult(queryResult) {
  if (!isRecord(queryResult)) {
    return {
      error: queryError("$", "The query result must be a JSON object."),
    };
  }

  if (Object.hasOwn(queryResult, "success")) {
    if (queryResult.success !== true) {
      return {
        error: diagnostic(
          "QUERY_UNSUCCESSFUL",
          "$.success",
          "The HydraDB query response was not successful.",
        ),
      };
    }
    if (!isRecord(queryResult.data)) {
      return {
        error: queryError(
          "$.data",
          "A successful query envelope must contain an object data payload.",
        ),
      };
    }
    return { value: queryResult.data };
  }

  return { value: queryResult };
}

function buildResponseRegistry(queryResult) {
  const unwrapped = unwrapQueryResult(queryResult);
  if (unwrapped.error) {
    return unwrapped;
  }
  const payload = unwrapped.value;

  const chunks = payload.chunks ?? [];
  if (!Array.isArray(chunks)) {
    return {
      error: queryError("$.chunks", "Query result chunks must be an array."),
    };
  }

  const sources = payload.sources ?? [];
  if (!Array.isArray(sources)) {
    return {
      error: queryError("$.sources", "Query result sources must be an array."),
    };
  }

  const additionalContext = payload.additional_context ?? {};
  if (!isRecord(additionalContext)) {
    return {
      error: diagnostic(
        "INVALID_ADDITIONAL_CONTEXT",
        "$.additional_context",
        "Query result additional_context must be an object map when present.",
      ),
    };
  }

  const evidence = new Map();
  const referencedAdditionalIds = [];
  const seenAdditionalIds = new Set();

  for (let index = 0; index < chunks.length; index += 1) {
    const path = `$.chunks[${index}]`;
    const normalized = normalizeChunk(chunks[index], path);
    if (normalized.error) {
      return normalized;
    }
    const registrationError = registerEvidence(
      evidence,
      normalized.value,
      `${path}.chunk_uuid`,
    );
    if (registrationError) {
      return { error: registrationError };
    }

    for (const extraId of normalized.value.extraContextIds) {
      if (!seenAdditionalIds.has(extraId)) {
        seenAdditionalIds.add(extraId);
        referencedAdditionalIds.push(extraId);
      }
    }
  }

  for (const extraId of referencedAdditionalIds) {
    if (!Object.hasOwn(additionalContext, extraId)) {
      continue;
    }
    const path = additionalContextPath();
    const chunk = additionalContext[extraId];
    if (!isRecord(chunk)) {
      return {
        error: diagnostic(
          "INVALID_ADDITIONAL_CONTEXT",
          path,
          "A referenced additional_context value must be a chunk object.",
        ),
      };
    }
    if (
      Object.hasOwn(chunk, "chunk_uuid") &&
      chunk.chunk_uuid !== extraId
    ) {
      return {
        error: diagnostic(
          "ADDITIONAL_CONTEXT_ID_MISMATCH",
          `${path}.chunk_uuid`,
          "An additional_context chunk_uuid must exactly match its map key.",
        ),
      };
    }

    const normalized = normalizeChunk(chunk, path, extraId);
    if (normalized.error) {
      return normalized;
    }
    const registrationError = registerEvidence(
      evidence,
      normalized.value,
      path,
    );
    if (registrationError) {
      return { error: registrationError };
    }
  }

  const sourceEntries = new Map();
  for (let index = 0; index < sources.length; index += 1) {
    const source = sources[index];
    if (!isRecord(source)) {
      return {
        error: queryError(
          `$.sources[${index}]`,
          "Each source entry must be a JSON object.",
        ),
      };
    }
    if (
      source.id !== undefined &&
      source.id !== null &&
      typeof source.id !== "string"
    ) {
      return {
        error: queryError(
          `$.sources[${index}].id`,
          "A source id must be a string when present.",
        ),
      };
    }
    const sourceId = optionalIdentifier(source.id);
    if (!sourceId) {
      continue;
    }
    const entries = sourceEntries.get(sourceId) ?? [];
    entries.push({ source, index });
    sourceEntries.set(sourceId, entries);
  }

  return {
    value: {
      evidence,
      sourceEntries,
    },
  };
}

function parseModelOutput(modelOutput) {
  if (typeof modelOutput === "string") {
    try {
      return { value: JSON.parse(modelOutput) };
    } catch {
      return {
        error: diagnostic(
          "MALFORMED_MODEL_OUTPUT",
          "$",
          "Model output must be one strict JSON object without code fences.",
        ),
      };
    }
  }
  return { value: modelOutput };
}

function validateAnswerShape(modelOutput) {
  const parsed = parseModelOutput(modelOutput);
  if (parsed.error) {
    return parsed;
  }
  const answer = parsed.value;
  if (!isRecord(answer)) {
    return {
      error: diagnostic(
        "INVALID_ANSWER_SHAPE",
        "$",
        "Model output must be a JSON object.",
      ),
    };
  }

  if (
    answer.status !== "answered" &&
    answer.status !== "insufficient_evidence"
  ) {
    return {
      error: diagnostic(
        "INVALID_ANSWER_STATUS",
        "$.status",
        "Status must be answered or insufficient_evidence.",
      ),
    };
  }

  if (answer.status === "insufficient_evidence") {
    if (!hasExactKeys(answer, ABSTENTION_KEYS)) {
      return {
        error: diagnostic(
          "INVALID_ANSWER_SHAPE",
          "$",
          "An insufficient_evidence result may contain only status.",
        ),
      };
    }
    return { value: answer };
  }

  if (!hasExactKeys(answer, ANSWERED_KEYS)) {
    return {
      error: diagnostic(
        "INVALID_ANSWER_SHAPE",
        "$",
        "An answered result must contain exactly status and claims.",
      ),
    };
  }
  if (!Array.isArray(answer.claims) || answer.claims.length === 0) {
    return {
      error: diagnostic(
        "INVALID_ANSWER_SHAPE",
        "$.claims",
        "An answered result must contain at least one claim.",
      ),
    };
  }

  for (let claimIndex = 0; claimIndex < answer.claims.length; claimIndex += 1) {
    const claim = answer.claims[claimIndex];
    const claimPath = `$.claims[${claimIndex}]`;
    if (!isRecord(claim) || !hasExactKeys(claim, CLAIM_KEYS)) {
      return {
        error: diagnostic(
          "INVALID_ANSWER_SHAPE",
          claimPath,
          "Each claim must contain exactly text and evidence_chunk_ids.",
        ),
      };
    }
    if (typeof claim.text !== "string" || claim.text.trim().length === 0) {
      return {
        error: diagnostic(
          "EMPTY_CLAIM",
          `${claimPath}.text`,
          "Each claim must contain nonempty text.",
        ),
      };
    }
    if (
      !Array.isArray(claim.evidence_chunk_ids) ||
      claim.evidence_chunk_ids.length === 0
    ) {
      return {
        error: diagnostic(
          "UNCITED_CLAIM",
          `${claimPath}.evidence_chunk_ids`,
          "Each answered claim must cite at least one evidence ID.",
        ),
      };
    }

    const seenIds = new Set();
    for (
      let evidenceIndex = 0;
      evidenceIndex < claim.evidence_chunk_ids.length;
      evidenceIndex += 1
    ) {
      const evidenceId = claim.evidence_chunk_ids[evidenceIndex];
      const evidencePath =
        `${claimPath}.evidence_chunk_ids[${evidenceIndex}]`;
      if (typeof evidenceId !== "string" || evidenceId.length === 0) {
        return {
          error: diagnostic(
            "INVALID_EVIDENCE_ID",
            evidencePath,
            "Evidence IDs must be nonempty opaque strings.",
          ),
        };
      }
      if (seenIds.has(evidenceId)) {
        return {
          error: diagnostic(
            "DUPLICATE_EVIDENCE_ID",
            evidencePath,
            "A claim must not repeat the same evidence ID.",
          ),
        };
      }
      seenIds.add(evidenceId);
    }
  }

  return { value: answer };
}

function validateSourceUrl(rawUrl, path) {
  if (rawUrl === undefined || rawUrl === null) {
    return { value: undefined };
  }
  if (typeof rawUrl !== "string") {
    return {
      error: diagnostic(
        "UNSAFE_SOURCE_URL",
        path,
        "A cited source URL must be a credential-free HTTP(S) URL.",
      ),
    };
  }
  const candidate = rawUrl.trim();
  if (candidate.length === 0) {
    return { value: undefined };
  }

  try {
    const parsed = new URL(candidate);
    if (
      (parsed.protocol !== "http:" && parsed.protocol !== "https:") ||
      parsed.username.length > 0 ||
      parsed.password.length > 0
    ) {
      throw new TypeError("Disallowed citation URL");
    }
    return { value: parsed.href };
  } catch {
    return {
      error: diagnostic(
        "UNSAFE_SOURCE_URL",
        path,
        "A cited source URL must be a credential-free HTTP(S) URL.",
      ),
    };
  }
}

function resolveCitationLink(sourceUrl, citationLinkPolicy) {
  if (!sourceUrl || typeof citationLinkPolicy !== "function") {
    return undefined;
  }

  const parsed = new URL(sourceUrl);
  let decision;
  try {
    decision = citationLinkPolicy(
      Object.freeze({
        href: parsed.href,
        origin: parsed.origin,
        pathname: parsed.pathname,
        search: parsed.search,
        hash: parsed.hash,
      }),
    );
  } catch {
    return undefined;
  }

  if (!isRecord(decision) || decision.allow !== true) {
    return undefined;
  }
  if (parsed.search.length > 0 && decision.allowQuery !== true) {
    return undefined;
  }
  if (parsed.hash.length > 0 && decision.allowFragment !== true) {
    return undefined;
  }

  return { href: parsed.href };
}

function evidenceExcerpt(chunkContent) {
  if (chunkContent.length <= EVIDENCE_EXCERPT_LIMIT) {
    return {
      text: chunkContent,
      truncated: false,
    };
  }

  return {
    text: `${chunkContent.slice(0, EVIDENCE_EXCERPT_LIMIT)}…`,
    truncated: true,
  };
}

function resolveCitation(
  evidence,
  sourceEntries,
  citationLinkPolicy,
  includeEvidenceExcerpt,
) {
  const citation = {
    chunk_uuid: evidence.chunkUuid,
  };
  if (evidence.sourceId) {
    citation.source_id = evidence.sourceId;
  }

  let sourceTitle;
  let sourceUrl;
  let hasSourceMetadata = false;
  const matches = evidence.sourceId
    ? sourceEntries.get(evidence.sourceId) ?? []
    : [];

  for (const { source, index } of matches) {
    if (
      source.title !== undefined &&
      source.title !== null &&
      typeof source.title !== "string"
    ) {
      return {
        error: queryError(
          `$.sources[${index}].title`,
          "A cited source title must be a string when present.",
        ),
      };
    }
    const candidateTitle = optionalText(source.title);
    const candidateUrl = validateSourceUrl(
      source.url,
      `$.sources[${index}].url`,
    );
    if (candidateUrl.error) {
      return candidateUrl;
    }

    if (!hasSourceMetadata) {
      sourceTitle = candidateTitle;
      sourceUrl = candidateUrl.value;
      hasSourceMetadata = true;
    } else {
      if (candidateTitle !== sourceTitle) {
        return {
          error: diagnostic(
            "AMBIGUOUS_SOURCE_ID",
            `$.sources[${index}].title`,
            "Duplicate source IDs contain conflicting citation metadata.",
          ),
        };
      }
      if (candidateUrl.value !== sourceUrl) {
        return {
          error: diagnostic(
            "AMBIGUOUS_SOURCE_ID",
            `$.sources[${index}].url`,
            "Duplicate source IDs contain conflicting citation metadata.",
          ),
        };
      }
    }
  }

  citation.title = sourceTitle ?? evidence.sourceTitle ?? evidence.chunkUuid;
  const link = resolveCitationLink(sourceUrl, citationLinkPolicy);
  if (link) {
    citation.link = link;
  }
  if (includeEvidenceExcerpt) {
    citation.evidence_excerpt = evidenceExcerpt(evidence.chunkContent);
  }
  return { value: citation };
}

/**
 * Bind model-produced claim IDs to evidence from the associated HydraDB query.
 *
 * This verifies response membership and response-derived citation metadata. It
 * does not verify that the cited text semantically entails the claim.
 */
export function validateCitedAnswer({
  queryResult,
  modelOutput,
  citationLinkPolicy,
  includeEvidenceExcerpt = false,
} = {}) {
  const responseRegistry = buildResponseRegistry(queryResult);
  if (responseRegistry.error) {
    return responseRegistry.error;
  }

  const answerShape = validateAnswerShape(modelOutput);
  if (answerShape.error) {
    return answerShape.error;
  }
  const answer = answerShape.value;

  if (answer.status === "insufficient_evidence") {
    return {
      ok: true,
      value: {
        status: "insufficient_evidence",
      },
    };
  }

  const { evidence, sourceEntries } = responseRegistry.value;
  if (evidence.size === 0) {
    return diagnostic(
      "NO_EVIDENCE_AVAILABLE",
      "$",
      "An answered result cannot be validated without returned evidence.",
    );
  }

  const claims = [];
  for (let claimIndex = 0; claimIndex < answer.claims.length; claimIndex += 1) {
    const claim = answer.claims[claimIndex];
    const citations = [];
    for (
      let evidenceIndex = 0;
      evidenceIndex < claim.evidence_chunk_ids.length;
      evidenceIndex += 1
    ) {
      const evidenceId = claim.evidence_chunk_ids[evidenceIndex];
      const evidencePath =
        `$.claims[${claimIndex}].evidence_chunk_ids[${evidenceIndex}]`;
      const evidenceEntry = evidence.get(evidenceId);
      if (!evidenceEntry) {
        return diagnostic(
          "UNKNOWN_EVIDENCE_ID",
          evidencePath,
          "Evidence ID was not returned by the associated query response.",
        );
      }
      const resolved = resolveCitation(
        evidenceEntry,
        sourceEntries,
        citationLinkPolicy,
        includeEvidenceExcerpt === true,
      );
      if (resolved.error) {
        return resolved.error;
      }
      citations.push(resolved.value);
    }
    claims.push({
      text: claim.text,
      citations,
    });
  }

  return {
    ok: true,
    value: {
      status: "answered",
      claims,
    },
  };
}

function usage() {
  return [
    "Usage:",
    "  node examples/validated-citations/validate.mjs --demo <cases.json>",
    "  node examples/validated-citations/validate.mjs --query <query.json> --answer <answer.json>",
  ].join("\n");
}

function optionValue(args, name) {
  const index = args.indexOf(name);
  if (index === -1) {
    return undefined;
  }
  return args[index + 1];
}

async function runDemo(path) {
  const fixture = JSON.parse(await readFile(path, "utf8"));
  if (
    !isRecord(fixture.queryResults) ||
    !isRecord(fixture.modelOutputs) ||
    !Array.isArray(fixture.demoCases)
  ) {
    throw new TypeError("Demo fixture has an invalid shape.");
  }

  let matchesExpectations = true;
  for (const demoCase of fixture.demoCases) {
    const result = validateCitedAnswer({
      queryResult: fixture.queryResults[demoCase.queryResult],
      modelOutput: fixture.modelOutputs[demoCase.modelOutput],
    });
    const expected = demoCase.expect;

    if (result.ok) {
      const status = result.value.status;
      if (
        expected?.kind !== "pass" ||
        expected.status !== status
      ) {
        matchesExpectations = false;
        console.log(`FAIL ${demoCase.name} unexpected validation result`);
        continue;
      }
      if (status === "answered") {
        const claimCount = result.value.claims.length;
        const citationCount = result.value.claims.reduce(
          (total, claim) => total + claim.citations.length,
          0,
        );
        const linkCount = result.value.claims.reduce(
          (total, claim) =>
            total + claim.citations.filter((citation) => citation.link).length,
          0,
        );
        if (
          expected?.linkCount !== undefined &&
          expected.linkCount !== linkCount
        ) {
          matchesExpectations = false;
          console.log(`FAIL ${demoCase.name} unexpected link count`);
          continue;
        }
        const claimLabel = claimCount === 1 ? "claim" : "claims";
        const citationLabel = citationCount === 1 ? "citation" : "citations";
        const linkLabel = linkCount === 1 ? "link" : "links";
        const linkSummary =
          expected?.linkCount === undefined ? "" : `, ${linkCount} ${linkLabel}`;
        console.log(
          `PASS ${demoCase.name} (${claimCount} ${claimLabel}, ` +
            `${citationCount} ${citationLabel}${linkSummary})`,
        );
      } else {
        console.log(`PASS ${demoCase.name} (insufficient_evidence)`);
      }
      continue;
    }

    const error = result.errors[0];
    if (expected?.kind !== "reject" || expected.code !== error.code) {
      matchesExpectations = false;
      console.log(`FAIL ${demoCase.name} unexpected validation result`);
      continue;
    }
    console.log(
      `REJECT ${demoCase.name} ${error.code} ${error.path}`,
    );
  }

  return matchesExpectations ? 0 : 1;
}

async function runCli(args) {
  if (args.includes("--help") || args.includes("-h")) {
    console.log(usage());
    return 0;
  }

  const demoPath = optionValue(args, "--demo");
  if (demoPath && args.length === 2) {
    return runDemo(demoPath);
  }

  const queryPath = optionValue(args, "--query");
  const answerPath = optionValue(args, "--answer");
  if (queryPath && answerPath && args.length === 4) {
    const queryResult = JSON.parse(await readFile(queryPath, "utf8"));
    const modelOutput = await readFile(answerPath, "utf8");
    const result = validateCitedAnswer({ queryResult, modelOutput });
    console.log(JSON.stringify(result, null, 2));
    return result.ok ? 0 : 1;
  }

  console.error(usage());
  return 2;
}

const invokedPath = process.argv[1] ? pathToFileURL(resolve(process.argv[1])).href : "";
if (import.meta.url === invokedPath) {
  try {
    process.exitCode = await runCli(process.argv.slice(2));
  } catch (error) {
    console.error(`ERROR ${error instanceof Error ? error.message : "Unknown error"}`);
    process.exitCode = 2;
  }
}
