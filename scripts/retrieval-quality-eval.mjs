#!/usr/bin/env node

import fs from "node:fs/promises";
import path from "node:path";
import { performance } from "node:perf_hooks";
import { pathToFileURL } from "node:url";

export const EXIT_CODES = Object.freeze({
  SUCCESS: 0,
  ERROR: 1,
  GATE_FAILURE: 2,
});

export const DEFAULTS = Object.freeze({
  baseUrl: "https://api.hydradb.com",
  fixturePath: "scripts/fixtures/retrieval-quality-v1.json",
  format: "markdown",
  maxRetries: 3,
  pollIntervalMs: 5_000,
  requestTimeoutMs: 30_000,
  seedTimeoutMs: 15 * 60_000,
});

const MAX_TIMER_MS = 2_147_483_647;

const SOURCE_KEYS = new Set([
  "additional_metadata",
  "attachments",
  "comments",
  "external_id",
  "fields",
  "id",
  "kind",
  "metadata",
  "provider",
  "relations",
  "timestamp",
  "title",
  "type",
  "url",
]);
const QUERY_KEYS = new Set(["expected_source_ids", "id", "kind", "query"]);
const PROFILE_KEYS = new Set([
  "alpha",
  "graph_context",
  "id",
  "max_results",
  "metadata_filters",
  "mode",
  "operator",
  "query_apps",
  "query_forceful_relations",
  "query_by",
  "recency_bias",
]);
const GATE_KEYS = new Set([
  "max_p95_latency_ms",
  "min_hit_at_k",
  "min_mrr_at_k",
  "min_recall_at_k",
]);
const TOP_LEVEL_KEYS = new Set([
  "collection",
  "gates",
  "k",
  "name",
  "profiles",
  "queries",
  "schema_version",
  "sources",
]);
const APP_KINDS = new Set([
  "comment",
  "custom",
  "email",
  "knowledge_base",
  "message",
  "ticket",
]);
const QUERY_KINDS = new Set(["literal", "multi-source", "semantic"]);
const INDEXING_STATUSES = new Set([
  "completed",
  "errored",
  "failed",
  "graph_creation",
  "processing",
  "queued",
]);
const TRANSIENT_HTTP_STATUSES = new Set([429, 500, 503]);

export class FixtureValidationError extends Error {
  constructor(issues) {
    const normalized = Array.isArray(issues) ? issues : [String(issues)];
    super(`Fixture validation failed:\n${normalized.map((issue) => `- ${issue}`).join("\n")}`);
    this.name = "FixtureValidationError";
    this.issues = normalized;
  }
}

export class EvaluationError extends Error {
  constructor(message, options = {}) {
    super(message, options);
    this.name = "EvaluationError";
  }
}

export class HydraApiError extends Error {
  constructor(message, { status = 0, code = "HYDRA_API_ERROR", requestId, cause } = {}) {
    super(message, { cause });
    this.name = "HydraApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

function isPlainObject(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function hasOwn(value, key) {
  return Object.prototype.hasOwnProperty.call(value, key);
}

function isNonEmptyString(value) {
  return typeof value === "string" && value.trim().length > 0;
}

function isFiniteNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function addUnknownKeyIssues(value, allowedKeys, objectPath, issues) {
  if (!isPlainObject(value)) return;
  for (const key of Object.keys(value)) {
    if (!allowedKeys.has(key)) issues.push(`${objectPath}.${key} is not a recognized field`);
  }
}

function requireNonEmptyString(value, valuePath, issues) {
  if (!isNonEmptyString(value)) issues.push(`${valuePath} must be a non-empty string`);
}

function validateAppFields(source, sourcePath, issues) {
  if (!isPlainObject(source.fields)) {
    issues.push(`${sourcePath}.fields must be an object`);
    return;
  }

  if (source.fields.kind !== source.kind) {
    issues.push(`${sourcePath}.fields.kind must match ${sourcePath}.kind`);
  }

  switch (source.kind) {
    case "email":
      if (!isNonEmptyString(source.fields.subject) && !isNonEmptyString(source.fields.body)) {
        issues.push(`${sourcePath}.fields must include a non-empty subject or body`);
      }
      break;
    case "message":
    case "comment":
    case "knowledge_base":
      requireNonEmptyString(source.fields.body, `${sourcePath}.fields.body`, issues);
      break;
    case "ticket":
      if (!isNonEmptyString(source.fields.title) && !isNonEmptyString(source.fields.description)) {
        issues.push(`${sourcePath}.fields must include a non-empty title or description`);
      }
      break;
    case "custom":
      if (!isPlainObject(source.fields.data) && !isNonEmptyString(source.fields.data)) {
        issues.push(`${sourcePath}.fields.data must be a non-empty string or object`);
      } else if (isPlainObject(source.fields.data) && Object.keys(source.fields.data).length === 0) {
        issues.push(`${sourcePath}.fields.data must not be empty`);
      }
      break;
    default:
      break;
  }
}

/** Validate and return a versioned retrieval fixture. All errors are reported together. */
export function validateFixture(fixture) {
  const issues = [];
  if (!isPlainObject(fixture)) throw new FixtureValidationError(["fixture must be a JSON object"]);

  addUnknownKeyIssues(fixture, TOP_LEVEL_KEYS, "fixture", issues);
  if (fixture.schema_version !== 1) issues.push("fixture.schema_version must equal 1");
  requireNonEmptyString(fixture.name, "fixture.name", issues);
  requireNonEmptyString(fixture.collection, "fixture.collection", issues);
  if (!Number.isInteger(fixture.k) || fixture.k < 1 || fixture.k > 50) {
    issues.push("fixture.k must be an integer from 1 through 50");
  }

  const sourceIds = new Set();
  const sourceExternalIds = new Set();
  if (!Array.isArray(fixture.sources) || fixture.sources.length === 0) {
    issues.push("fixture.sources must be a non-empty array");
  } else {
    fixture.sources.forEach((source, index) => {
      const sourcePath = `fixture.sources[${index}]`;
      if (!isPlainObject(source)) {
        issues.push(`${sourcePath} must be an object`);
        return;
      }
      addUnknownKeyIssues(source, SOURCE_KEYS, sourcePath, issues);
      for (const key of ["id", "title", "type", "kind", "provider", "external_id"]) {
        requireNonEmptyString(source[key], `${sourcePath}.${key}`, issues);
      }
      if (isNonEmptyString(source.id)) {
        if (sourceIds.has(source.id)) issues.push(`${sourcePath}.id duplicates source ID ${source.id}`);
        sourceIds.add(source.id);
      }
      if (isNonEmptyString(source.external_id)) {
        const identity = `${source.provider ?? ""}\u0000${source.external_id}`;
        if (sourceExternalIds.has(identity)) {
          issues.push(`${sourcePath}.external_id duplicates provider identity ${source.external_id}`);
        }
        sourceExternalIds.add(identity);
      }
      if (!APP_KINDS.has(source.kind)) {
        issues.push(`${sourcePath}.kind must be one of ${[...APP_KINDS].join(", ")}`);
      }
      validateAppFields(source, sourcePath, issues);
      for (const key of ["metadata", "additional_metadata"]) {
        if (hasOwn(source, key) && !isPlainObject(source[key])) {
          issues.push(`${sourcePath}.${key} must be an object`);
        }
      }
      for (const key of ["relations", "attachments", "comments"]) {
        if (hasOwn(source, key) && !Array.isArray(source[key])) {
          issues.push(`${sourcePath}.${key} must be an array`);
        }
      }
      if (
        hasOwn(source, "timestamp") &&
        (!isNonEmptyString(source.timestamp) || Number.isNaN(Date.parse(source.timestamp)))
      ) {
        issues.push(`${sourcePath}.timestamp must be an ISO-8601 timestamp`);
      }
      if (hasOwn(source, "url")) {
        try {
          const parsedUrl = new URL(source.url);
          if (!new Set(["http:", "https:"]).has(parsedUrl.protocol)) throw new Error("unsupported protocol");
        } catch {
          issues.push(`${sourcePath}.url must be an absolute HTTP(S) URL`);
        }
      }
    });
  }

  const queryIds = new Set();
  if (!Array.isArray(fixture.queries) || fixture.queries.length === 0) {
    issues.push("fixture.queries must be a non-empty array");
  } else {
    fixture.queries.forEach((query, index) => {
      const queryPath = `fixture.queries[${index}]`;
      if (!isPlainObject(query)) {
        issues.push(`${queryPath} must be an object`);
        return;
      }
      addUnknownKeyIssues(query, QUERY_KEYS, queryPath, issues);
      requireNonEmptyString(query.id, `${queryPath}.id`, issues);
      requireNonEmptyString(query.query, `${queryPath}.query`, issues);
      if (!QUERY_KINDS.has(query.kind)) {
        issues.push(`${queryPath}.kind must be one of ${[...QUERY_KINDS].join(", ")}`);
      }
      if (isNonEmptyString(query.id)) {
        if (queryIds.has(query.id)) issues.push(`${queryPath}.id duplicates query ID ${query.id}`);
        queryIds.add(query.id);
      }
      if (!Array.isArray(query.expected_source_ids) || query.expected_source_ids.length === 0) {
        issues.push(`${queryPath}.expected_source_ids must be a non-empty array`);
      } else {
        const expectedIds = new Set();
        for (const [expectedIndex, expectedId] of query.expected_source_ids.entries()) {
          const expectedPath = `${queryPath}.expected_source_ids[${expectedIndex}]`;
          requireNonEmptyString(expectedId, expectedPath, issues);
          if (expectedIds.has(expectedId)) issues.push(`${expectedPath} duplicates ${expectedId}`);
          expectedIds.add(expectedId);
          if (isNonEmptyString(expectedId) && !sourceIds.has(expectedId)) {
            issues.push(`${expectedPath} references unknown source ID ${expectedId}`);
          }
        }
        if (query.kind === "multi-source" && query.expected_source_ids.length < 2) {
          issues.push(`${queryPath} is multi-source and must expect at least two sources`);
        }
        if (query.kind !== "multi-source" && query.expected_source_ids.length !== 1) {
          issues.push(`${queryPath} must expect exactly one source unless kind is multi-source`);
        }
      }
    });
  }

  const profileIds = new Set();
  if (!Array.isArray(fixture.profiles) || fixture.profiles.length === 0) {
    issues.push("fixture.profiles must be a non-empty array");
  } else {
    fixture.profiles.forEach((profile, index) => {
      const profilePath = `fixture.profiles[${index}]`;
      if (!isPlainObject(profile)) {
        issues.push(`${profilePath} must be an object`);
        return;
      }
      addUnknownKeyIssues(profile, PROFILE_KEYS, profilePath, issues);
      requireNonEmptyString(profile.id, `${profilePath}.id`, issues);
      if (isNonEmptyString(profile.id)) {
        if (profileIds.has(profile.id)) issues.push(`${profilePath}.id duplicates profile ID ${profile.id}`);
        profileIds.add(profile.id);
      }
      if (!new Set(["hybrid", "text"]).has(profile.query_by)) {
        issues.push(`${profilePath}.query_by must be hybrid or text`);
      }
      if (!Number.isInteger(profile.max_results) || profile.max_results < 1 || profile.max_results > 50) {
        issues.push(`${profilePath}.max_results must be an integer from 1 through 50`);
      } else if (Number.isInteger(fixture.k) && profile.max_results < fixture.k) {
        issues.push(`${profilePath}.max_results must be at least fixture.k (${fixture.k})`);
      }
      if (typeof profile.graph_context !== "boolean") {
        issues.push(`${profilePath}.graph_context must be a boolean`);
      }
      if (typeof profile.query_apps !== "boolean") {
        issues.push(`${profilePath}.query_apps must be a boolean`);
      }
      if (hasOwn(profile, "query_forceful_relations") && typeof profile.query_forceful_relations !== "boolean") {
        issues.push(`${profilePath}.query_forceful_relations must be a boolean`);
      }
      if (hasOwn(profile, "metadata_filters") && !isPlainObject(profile.metadata_filters)) {
        issues.push(`${profilePath}.metadata_filters must be an object`);
      }
      if (
        hasOwn(profile, "recency_bias") &&
        (!isFiniteNumber(profile.recency_bias) || profile.recency_bias < 0 || profile.recency_bias > 1)
      ) {
        issues.push(`${profilePath}.recency_bias must be a number from 0 through 1`);
      }

      if (profile.query_by === "text") {
        if (!new Set(["and", "or", "phrase"]).has(profile.operator)) {
          issues.push(`${profilePath}.operator must be and, or, or phrase for text retrieval`);
        }
        if (hasOwn(profile, "alpha")) issues.push(`${profilePath}.alpha is only valid for hybrid retrieval`);
        if (hasOwn(profile, "mode")) {
          issues.push(`${profilePath}.mode applies only to hybrid retrieval and must be omitted for text`);
        }
      }
      if (profile.query_by === "hybrid") {
        if (!new Set(["fast", "thinking"]).has(profile.mode)) {
          issues.push(`${profilePath}.mode must be fast or thinking for hybrid retrieval`);
        }
        if (hasOwn(profile, "operator")) issues.push(`${profilePath}.operator is only valid for text retrieval`);
        if (
          profile.alpha !== "auto" &&
          (!isFiniteNumber(profile.alpha) || profile.alpha < 0 || profile.alpha > 1)
        ) {
          issues.push(`${profilePath}.alpha must be auto or a number from 0 through 1`);
        }
      }
    });
  }

  if (hasOwn(fixture, "gates")) {
    if (!isPlainObject(fixture.gates)) {
      issues.push("fixture.gates must be an object keyed by profile ID");
    } else {
      for (const [profileId, gate] of Object.entries(fixture.gates)) {
        const gatePath = `fixture.gates.${profileId}`;
        if (!profileIds.has(profileId)) issues.push(`${gatePath} references unknown profile ID ${profileId}`);
        if (!isPlainObject(gate)) {
          issues.push(`${gatePath} must be an object`);
          continue;
        }
        addUnknownKeyIssues(gate, GATE_KEYS, gatePath, issues);
        if (Object.keys(gate).length === 0) issues.push(`${gatePath} must include at least one threshold`);
        for (const key of ["min_hit_at_k", "min_recall_at_k", "min_mrr_at_k"]) {
          if (hasOwn(gate, key) && (!isFiniteNumber(gate[key]) || gate[key] < 0 || gate[key] > 1)) {
            issues.push(`${gatePath}.${key} must be a number from 0 through 1`);
          }
        }
        if (
          hasOwn(gate, "max_p95_latency_ms") &&
          (!isFiniteNumber(gate.max_p95_latency_ms) || gate.max_p95_latency_ms < 0)
        ) {
          issues.push(`${gatePath}.max_p95_latency_ms must be a non-negative number`);
        }
      }
    }
  }

  if (issues.length > 0) throw new FixtureValidationError(issues);
  return fixture;
}

export async function loadFixture(fixturePath) {
  let raw;
  try {
    raw = await fs.readFile(fixturePath, "utf8");
  } catch (error) {
    throw new FixtureValidationError([`could not read ${fixturePath}: ${error.message}`]);
  }

  let fixture;
  try {
    fixture = JSON.parse(raw);
  } catch (error) {
    throw new FixtureValidationError([`could not parse ${fixturePath} as JSON: ${error.message}`]);
  }
  return validateFixture(fixture);
}

export function extractApiError(payload, status = 0) {
  const envelopeError = isPlainObject(payload?.error) ? payload.error : undefined;
  const detail = isPlainObject(payload?.detail) ? payload.detail : undefined;
  const code =
    (isNonEmptyString(envelopeError?.code) && envelopeError.code) ||
    (isNonEmptyString(detail?.error_code) && detail.error_code) ||
    (isNonEmptyString(detail?.code) && detail.code) ||
    `HTTP_${status || "ERROR"}`;
  const message =
    (isNonEmptyString(envelopeError?.message) && envelopeError.message) ||
    (isNonEmptyString(detail?.message) && detail.message) ||
    `HydraDB request failed with HTTP ${status || "error"}`;
  const requestId = isNonEmptyString(payload?.meta?.request_id) ? payload.meta.request_id : undefined;
  return { code, message, requestId };
}

/** Parse the documented HydraDB v2 success envelope and return its data object. */
export function parseEnvelope(payload, { status = 200, operation = "HydraDB request" } = {}) {
  if (!isPlainObject(payload)) {
    throw new HydraApiError(`${operation} returned a malformed response envelope`, {
      status,
      code: "MALFORMED_ENVELOPE",
    });
  }
  for (const key of ["success", "data", "error", "meta"]) {
    if (!hasOwn(payload, key)) {
      throw new HydraApiError(`${operation} returned a malformed response envelope (missing ${key})`, {
        status,
        code: "MALFORMED_ENVELOPE",
      });
    }
  }
  if (payload.success !== true || payload.error !== null || !isPlainObject(payload.data) || !isPlainObject(payload.meta)) {
    const extracted = extractApiError(payload, status);
    throw new HydraApiError(
      payload.success === false && extracted.message
        ? `${operation} failed: ${extracted.message}`
        : `${operation} returned a malformed response envelope`,
      {
        status,
        code: payload.success === false ? extracted.code : "MALFORMED_ENVELOPE",
        requestId: extracted.requestId,
      },
    );
  }
  return payload.data;
}

function isTransientNetworkError(error) {
  return (
    error?.name === "AbortError" ||
    error?.name === "TimeoutError" ||
    error?.name === "TypeError" ||
    ["ECONNRESET", "ETIMEDOUT", "UND_ERR_CONNECT_TIMEOUT"].includes(error?.code)
  );
}

async function readResponsePayload(response) {
  let raw;
  try {
    raw = await response.text();
  } catch (error) {
    throw new HydraApiError("HydraDB response body could not be read", {
      status: response.status,
      code: "MALFORMED_RESPONSE",
      cause: error,
    });
  }
  if (!raw.trim()) return undefined;
  try {
    return JSON.parse(raw);
  } catch (error) {
    throw new HydraApiError("HydraDB returned a non-JSON response", {
      status: response.status,
      code: "MALFORMED_RESPONSE",
      cause: error,
    });
  }
}

function retryDelayMs(retryNumber, random) {
  return 2 ** retryNumber * 1_000 + Math.floor(random() * 251);
}

/**
 * Perform one HydraDB request. `maxRetries` is the number of retries after the
 * first attempt. Only callers that explicitly mark an operation read-only can
 * retry, and only for bounded network/429/500/503 failures.
 */
export async function requestJson(
  url,
  init = {},
  {
    fetchImpl = globalThis.fetch,
    maxRetries = DEFAULTS.maxRetries,
    random = Math.random,
    readOnly = false,
    requestTimeoutMs = DEFAULTS.requestTimeoutMs,
    sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)),
  } = {},
) {
  if (typeof fetchImpl !== "function") throw new EvaluationError("A Fetch API implementation is required");
  if (!Number.isInteger(maxRetries) || maxRetries < 0) throw new EvaluationError("maxRetries must be a non-negative integer");
  if (!isFiniteNumber(requestTimeoutMs) || requestTimeoutMs <= 0) {
    throw new EvaluationError("requestTimeoutMs must be greater than zero");
  }

  for (let attempt = 0; attempt <= maxRetries; attempt += 1) {
    const controller = new AbortController();
    let timedOut = false;
    const timeout = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, requestTimeoutMs);
    const callerSignal = init.signal;
    const abortFromCaller = () => controller.abort(callerSignal?.reason);
    if (callerSignal) {
      if (callerSignal.aborted) abortFromCaller();
      else callerSignal.addEventListener("abort", abortFromCaller, { once: true });
    }
    const cleanup = () => {
      clearTimeout(timeout);
      callerSignal?.removeEventListener?.("abort", abortFromCaller);
    };

    try {
      let response;
      try {
        response = await fetchImpl(url, { ...init, signal: controller.signal });
      } catch (error) {
        const canRetry =
          readOnly &&
          attempt < maxRetries &&
          !callerSignal?.aborted &&
          (timedOut || isTransientNetworkError(error));
        if (canRetry) {
          cleanup();
          await sleep(retryDelayMs(attempt, random));
          continue;
        }
        throw new HydraApiError(
          timedOut ? "HydraDB request timed out" : "HydraDB request could not be completed",
          {
            code: timedOut ? "REQUEST_TIMEOUT" : "NETWORK_ERROR",
            cause: error,
          },
        );
      }

      let payload;
      try {
        payload = await readResponsePayload(response);
      } catch (error) {
        const bodyError = error?.cause ?? error;
        const canRetry =
          readOnly &&
          attempt < maxRetries &&
          !callerSignal?.aborted &&
          (timedOut || TRANSIENT_HTTP_STATUSES.has(response.status) || isTransientNetworkError(bodyError));
        if (canRetry) {
          cleanup();
          await sleep(retryDelayMs(attempt, random));
          continue;
        }
        if (timedOut) {
          throw new HydraApiError("HydraDB request timed out", {
            code: "REQUEST_TIMEOUT",
            cause: bodyError,
          });
        }
        throw error;
      }

      if (!response.ok) {
        if (readOnly && attempt < maxRetries && TRANSIENT_HTTP_STATUSES.has(response.status)) {
          cleanup();
          await sleep(retryDelayMs(attempt, random));
          continue;
        }
        const extracted = extractApiError(payload, response.status);
        const headerRequestId = response.headers?.get?.("x-request-id");
        throw new HydraApiError(extracted.message, {
          status: response.status,
          code: extracted.code,
          requestId: extracted.requestId ?? headerRequestId ?? undefined,
        });
      }

      return parseEnvelope(payload, {
        status: response.status,
        operation: `${init.method ?? "GET"} ${new URL(url).pathname}`,
      });
    } finally {
      cleanup();
    }
  }

  throw new HydraApiError("HydraDB request exhausted its retry budget", { code: "RETRY_EXHAUSTED" });
}

function normalizeBaseUrl(baseUrl) {
  let parsed;
  try {
    parsed = new URL(baseUrl);
  } catch {
    throw new EvaluationError("HYDRA_DB_BASE_URL must be an absolute HTTP(S) URL");
  }
  if (!new Set(["http:", "https:"]).has(parsed.protocol) || parsed.username || parsed.password) {
    throw new EvaluationError("HYDRA_DB_BASE_URL must be an HTTP(S) URL without embedded credentials");
  }
  const loopbackHosts = new Set(["localhost", "127.0.0.1", "::1", "[::1]"]);
  if (parsed.protocol === "http:" && !loopbackHosts.has(parsed.hostname)) {
    throw new EvaluationError("HYDRA_DB_BASE_URL must use HTTPS unless it targets a loopback host");
  }
  parsed.hash = "";
  parsed.search = "";
  return parsed.toString().replace(/\/$/, "");
}

export function createApiClient({
  apiKey,
  baseUrl = DEFAULTS.baseUrl,
  fetchImpl = globalThis.fetch,
  maxRetries = DEFAULTS.maxRetries,
  random = Math.random,
  requestTimeoutMs = DEFAULTS.requestTimeoutMs,
  sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)),
} = {}) {
  if (!isNonEmptyString(apiKey)) throw new EvaluationError("HYDRA_DB_API_KEY is required");
  const normalizedBaseUrl = normalizeBaseUrl(baseUrl);
  const commonHeaders = Object.freeze({
    Accept: "application/json",
    Authorization: `Bearer ${apiKey}`,
    "API-Version": "2",
  });
  const call = (pathname, init, readOnly) =>
    requestJson(`${normalizedBaseUrl}${pathname}`, init, {
      fetchImpl,
      maxRetries,
      random,
      readOnly,
      requestTimeoutMs,
      sleep,
    });

  return Object.freeze({
    createDatabase(database) {
      return call(
        "/databases",
        {
          method: "POST",
          headers: { ...commonHeaders, "Content-Type": "application/json" },
          body: JSON.stringify({ database }),
        },
        false,
      );
    },

    databaseStatus(database) {
      const parameters = new URLSearchParams({ database });
      return call(`/databases/status?${parameters}`, { method: "GET", headers: commonHeaders }, true);
    },

    ingestKnowledge({ database, collection, sources }) {
      const appKnowledge = sources.map((source) => ({ ...source, database, collection }));
      const form = new FormData();
      form.append("type", "knowledge");
      form.append("database", database);
      form.append("collection", collection);
      form.append("upsert", "true");
      form.append("app_knowledge", JSON.stringify(appKnowledge));
      return call("/context/ingest", { method: "POST", headers: commonHeaders, body: form }, false);
    },

    contextStatus({ database, collection, ids }) {
      const parameters = new URLSearchParams({ database, collection });
      for (const id of ids) parameters.append("ids", id);
      return call(`/context/status?${parameters}`, { method: "GET", headers: commonHeaders }, true);
    },

    query(request) {
      return call(
        "/query",
        {
          method: "POST",
          headers: { ...commonHeaders, "Content-Type": "application/json" },
          body: JSON.stringify(request),
        },
        true,
      );
    },
  });
}

export function databaseIsReady(data) {
  if (!isPlainObject(data?.infra)) throw new EvaluationError("Database status response is missing data.infra");
  const { infra } = data;
  if (typeof infra.scheduler_status !== "boolean" || typeof infra.graph_status !== "boolean") {
    throw new EvaluationError("Database status response has malformed scheduler_status or graph_status");
  }
  if (
    !isPlainObject(infra.vectorstore_status) ||
    typeof infra.vectorstore_status.knowledge !== "boolean" ||
    typeof infra.vectorstore_status.memories !== "boolean"
  ) {
    throw new EvaluationError("Database status response has malformed vectorstore_status");
  }
  if (hasOwn(infra, "ready_for_ingestion") && typeof infra.ready_for_ingestion !== "boolean") {
    throw new EvaluationError("Database status response has malformed ready_for_ingestion");
  }
  return (
    infra.scheduler_status &&
    infra.graph_status &&
    infra.vectorstore_status.knowledge &&
    infra.vectorstore_status.memories
  );
}

function assertPollOptions(timeoutMs, pollIntervalMs) {
  if (!isFiniteNumber(timeoutMs) || timeoutMs <= 0) throw new EvaluationError("timeoutMs must be greater than zero");
  if (!isFiniteNumber(pollIntervalMs) || pollIntervalMs < 0) {
    throw new EvaluationError("pollIntervalMs must be non-negative");
  }
}

export async function waitForDatabaseReady(
  client,
  {
    database,
    timeoutMs = DEFAULTS.seedTimeoutMs,
    pollIntervalMs = DEFAULTS.pollIntervalMs,
    onProgress = () => {},
    now = Date.now,
    sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)),
  },
) {
  assertPollOptions(timeoutMs, pollIntervalMs);
  const startedAt = now();
  while (true) {
    const status = await client.databaseStatus(database);
    const ready = databaseIsReady(status);
    const elapsed = now() - startedAt;
    if (elapsed >= timeoutMs) {
      throw new EvaluationError(`Database ${database} was not ready before the ${timeoutMs}ms timeout`);
    }
    if (ready) return status;
    onProgress(`Database ${database} is still provisioning`);
    await sleep(Math.min(pollIntervalMs, timeoutMs - elapsed, 60_000));
  }
}

export function validateIngestionResults(expectedIds, data) {
  if (!Array.isArray(expectedIds) || expectedIds.length === 0) {
    throw new EvaluationError("Expected ingestion IDs must be a non-empty array");
  }
  if (!isPlainObject(data) || !Array.isArray(data.results)) {
    throw new EvaluationError("Ingestion response is missing data.results");
  }
  if (data.success !== true || data.failed_count !== 0 || data.success_count !== expectedIds.length) {
    throw new EvaluationError(
      `Ingestion did not queue every fixture source (success_count=${String(data.success_count)}, failed_count=${String(data.failed_count)})`,
    );
  }

  const expected = new Set(expectedIds);
  const byId = new Map();
  for (const [index, result] of data.results.entries()) {
    if (!isPlainObject(result) || !isNonEmptyString(result.id)) {
      throw new EvaluationError(`Ingestion result at index ${index} is missing a valid ID`);
    }
    if (!expected.has(result.id)) throw new EvaluationError(`Ingestion returned unexpected source ID ${result.id}`);
    if (byId.has(result.id)) throw new EvaluationError(`Ingestion returned duplicate source ID ${result.id}`);
    if (["errored", "failed"].includes(result.status) || isNonEmptyString(result.error) || isNonEmptyString(result.error_code)) {
      throw new EvaluationError(`Ingestion failed for source ${result.id}`);
    }
    byId.set(result.id, result);
  }
  for (const id of expectedIds) {
    if (!byId.has(id)) throw new EvaluationError(`Ingestion response is missing source ID ${id}`);
  }
  return expectedIds.map((id) => byId.get(id));
}

/** Ensure a batch status response contains exactly one well-formed entry per requested ID. */
export function validateStatusEntries(expectedIds, statuses) {
  if (!Array.isArray(expectedIds) || expectedIds.length === 0) {
    throw new EvaluationError("Expected status IDs must be a non-empty array");
  }
  if (new Set(expectedIds).size !== expectedIds.length) {
    throw new EvaluationError("Expected status IDs must be unique");
  }
  if (!Array.isArray(statuses)) throw new EvaluationError("Status response is missing data.statuses");

  const expected = new Set(expectedIds);
  const byId = new Map();
  for (const [index, status] of statuses.entries()) {
    if (!isPlainObject(status) || !isNonEmptyString(status.id)) {
      throw new EvaluationError(`Status entry at index ${index} is missing a valid ID`);
    }
    if (!expected.has(status.id)) throw new EvaluationError(`Status response returned unexpected source ID ${status.id}`);
    if (byId.has(status.id)) throw new EvaluationError(`Status response returned duplicate source ID ${status.id}`);
    if (!INDEXING_STATUSES.has(status.indexing_status)) {
      throw new EvaluationError(`Status response returned unknown indexing status for ${status.id}: ${String(status.indexing_status)}`);
    }
    byId.set(status.id, status);
  }
  for (const id of expectedIds) {
    if (!byId.has(id)) throw new EvaluationError(`Status response is missing source ID ${id}`);
  }
  return expectedIds.map((id) => byId.get(id));
}

function throwOnFailedStatuses(statuses) {
  const failed = statuses.find(
    (status) => ["errored", "failed"].includes(status.indexing_status) || status.success === false,
  );
  if (failed) {
    const detail = failed.error_code || failed.error_message || failed.message || failed.indexing_status;
    throw new EvaluationError(`Indexing failed for ${failed.id}: ${detail}`);
  }
}

export async function waitForIndexing(
  client,
  {
    database,
    collection,
    ids,
    timeoutMs = DEFAULTS.seedTimeoutMs,
    pollIntervalMs = DEFAULTS.pollIntervalMs,
    onProgress = () => {},
    now = Date.now,
    sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)),
  },
) {
  assertPollOptions(timeoutMs, pollIntervalMs);
  const startedAt = now();
  while (true) {
    const data = await client.contextStatus({ database, collection, ids });
    const statuses = validateStatusEntries(ids, data?.statuses);
    throwOnFailedStatuses(statuses);
    const elapsed = now() - startedAt;
    if (elapsed >= timeoutMs) {
      const pending = statuses
        .filter((status) => status.indexing_status !== "completed")
        .map((status) => `${status.id}=${status.indexing_status}`)
        .join(", ");
      const detail = pending ? ` (${pending})` : "";
      throw new EvaluationError(`Indexing did not complete before the ${timeoutMs}ms timeout${detail}`);
    }
    if (statuses.every((status) => status.indexing_status === "completed")) return statuses;
    onProgress(
      `Waiting for indexing: ${statuses.filter((status) => status.indexing_status === "completed").length}/${ids.length} completed`,
    );
    await sleep(Math.min(pollIntervalMs, timeoutMs - elapsed, 60_000));
  }
}

/** Rank parent sources by the first occurrence of each chunk.id. */
export function rankSources(chunks, k = Number.POSITIVE_INFINITY) {
  if (!Array.isArray(chunks)) throw new EvaluationError("Query response is missing data.chunks");
  if (k !== Number.POSITIVE_INFINITY && (!Number.isInteger(k) || k < 1)) {
    throw new EvaluationError("k must be a positive integer");
  }
  const ranked = [];
  const seen = new Set();
  for (const [index, chunk] of chunks.entries()) {
    if (!isPlainObject(chunk) || !isNonEmptyString(chunk.id)) {
      throw new EvaluationError(`Query chunk at index ${index} is missing a valid parent source ID in chunk.id`);
    }
    if (seen.has(chunk.id)) continue;
    seen.add(chunk.id);
    ranked.push(chunk.id);
    if (ranked.length === k) break;
  }
  return ranked;
}

export function calculateQueryMetrics(expectedSourceIds, rankedSourceIds, k) {
  if (!Array.isArray(expectedSourceIds) || expectedSourceIds.length === 0) {
    throw new EvaluationError("expectedSourceIds must be a non-empty array");
  }
  if (!Array.isArray(rankedSourceIds)) throw new EvaluationError("rankedSourceIds must be an array");
  if (!Number.isInteger(k) || k < 1) throw new EvaluationError("k must be a positive integer");
  const expected = new Set(expectedSourceIds);
  const topK = [];
  const seen = new Set();
  for (const id of rankedSourceIds) {
    if (!seen.has(id)) topK.push(id);
    seen.add(id);
    if (topK.length === k) break;
  }
  const hits = topK.filter((id) => expected.has(id));
  const firstRelevantIndex = topK.findIndex((id) => expected.has(id));
  return {
    hit_at_k: hits.length > 0 ? 1 : 0,
    recall_at_k: hits.length / expected.size,
    mrr_at_k: firstRelevantIndex === -1 ? 0 : 1 / (firstRelevantIndex + 1),
  };
}

export function nearestRankPercentile(values, percentile) {
  if (!Array.isArray(values) || values.length === 0 || values.some((value) => !isFiniteNumber(value))) {
    throw new EvaluationError("percentile values must be a non-empty array of finite numbers");
  }
  if (!isFiniteNumber(percentile) || percentile < 0 || percentile > 1) {
    throw new EvaluationError("percentile must be from 0 through 1");
  }
  const sorted = [...values].sort((left, right) => left - right);
  const index = Math.max(0, Math.ceil(percentile * sorted.length) - 1);
  return sorted[index];
}

export const percentile = nearestRankPercentile;

function round(value, places = 6) {
  const multiplier = 10 ** places;
  return Math.round((value + Number.EPSILON) * multiplier) / multiplier;
}

function mean(values) {
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

export function aggregateProfileResults(profileId, queryResults, k) {
  if (!isNonEmptyString(profileId)) throw new EvaluationError("profileId must be a non-empty string");
  if (!Array.isArray(queryResults) || queryResults.length === 0) {
    throw new EvaluationError(`Profile ${profileId} has no query results`);
  }
  const requiredMetrics = ["hit_at_k", "recall_at_k", "mrr_at_k", "latency_ms"];
  for (const [index, result] of queryResults.entries()) {
    if (!isPlainObject(result) || requiredMetrics.some((key) => !isFiniteNumber(result[key]))) {
      throw new EvaluationError(`Profile ${profileId} query result ${index} has malformed metrics`);
    }
  }
  const latencies = queryResults.map((result) => result.latency_ms);
  return {
    id: profileId,
    k,
    queries: queryResults,
    summary: {
      query_count: queryResults.length,
      hit_at_k: round(mean(queryResults.map((result) => result.hit_at_k))),
      recall_at_k: round(mean(queryResults.map((result) => result.recall_at_k))),
      mrr_at_k: round(mean(queryResults.map((result) => result.mrr_at_k))),
      p50_latency_ms: round(nearestRankPercentile(latencies, 0.5), 2),
      p95_latency_ms: round(nearestRankPercentile(latencies, 0.95), 2),
    },
  };
}

function effectiveGate(fixtureGate = {}, cliThresholds = {}) {
  const result = { ...fixtureGate };
  for (const key of ["min_hit_at_k", "min_recall_at_k", "min_mrr_at_k"]) {
    if (hasOwn(cliThresholds, key)) result[key] = Math.max(result[key] ?? 0, cliThresholds[key]);
  }
  if (hasOwn(cliThresholds, "max_p95_latency_ms")) {
    result.max_p95_latency_ms = Math.min(
      result.max_p95_latency_ms ?? Number.POSITIVE_INFINITY,
      cliThresholds.max_p95_latency_ms,
    );
  }
  return result;
}

export function evaluateGates(report, fixtureGates = {}, cliThresholds = {}) {
  if (!isPlainObject(report) || !Array.isArray(report.profiles)) {
    throw new EvaluationError("Report is missing profiles");
  }
  const failures = [];
  const comparisons = [
    ["min_hit_at_k", "hit_at_k", ">="],
    ["min_recall_at_k", "recall_at_k", ">="],
    ["min_mrr_at_k", "mrr_at_k", ">="],
    ["max_p95_latency_ms", "p95_latency_ms", "<="],
  ];
  for (const profile of report.profiles) {
    const gate = effectiveGate(fixtureGates?.[profile.id], cliThresholds);
    for (const [thresholdKey, metric, operator] of comparisons) {
      if (!hasOwn(gate, thresholdKey)) continue;
      const actual = profile.summary?.[metric];
      if (!isFiniteNumber(actual)) throw new EvaluationError(`Profile ${profile.id} is missing ${metric}`);
      const threshold = gate[thresholdKey];
      const passed = operator === ">=" ? actual >= threshold : actual <= threshold;
      if (!passed) failures.push({ profile: profile.id, metric, operator, threshold, actual });
    }
  }
  return failures;
}

export function buildQueryRequest(fixture, profile, query, database) {
  const request = {
    database,
    collection: fixture.collection,
    query: query.query,
    type: "knowledge",
    query_by: profile.query_by,
    max_results: profile.max_results,
    graph_context: profile.graph_context,
    query_apps: profile.query_apps,
  };

  // `mode` and `alpha` belong only to hybrid retrieval. Text retrieval uses
  // BM25 plus `operator`, so deliberately omit hybrid routing controls.
  const retrievalKeys = profile.query_by === "text" ? ["operator"] : ["alpha", "mode"];
  for (const key of [
    ...retrievalKeys,
    "metadata_filters",
    "query_forceful_relations",
    "recency_bias",
  ]) {
    if (hasOwn(profile, key)) request[key] = profile[key];
  }
  return request;
}

function requireCompletedStatuses(ids, data) {
  const statuses = validateStatusEntries(ids, data?.statuses);
  throwOnFailedStatuses(statuses);
  const pending = statuses.filter((status) => status.indexing_status !== "completed");
  if (pending.length > 0) {
    throw new EvaluationError(
      `Fixture sources are not fully indexed: ${pending.map((status) => `${status.id}=${status.indexing_status}`).join(", ")}`,
    );
  }
  return statuses;
}

export async function seedFixture(
  fixture,
  {
    client,
    database,
    timeoutMs = DEFAULTS.seedTimeoutMs,
    pollIntervalMs = DEFAULTS.pollIntervalMs,
    onProgress = () => {},
    now = Date.now,
    sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)),
  },
) {
  validateFixture(fixture);
  validateDatabaseName(database);
  let reusedDatabase = false;
  onProgress(`Creating or reusing sandbox database ${database}`);
  try {
    await client.createDatabase(database);
  } catch (error) {
    if (error instanceof HydraApiError && error.status === 409 && error.code === "DATABASE_ALREADY_EXISTS") {
      reusedDatabase = true;
    } else {
      throw error;
    }
  }

  await waitForDatabaseReady(client, {
    database,
    timeoutMs,
    pollIntervalMs,
    onProgress,
    now,
    sleep,
  });

  onProgress(`Ingesting ${fixture.sources.length} fixed-ID fixture sources`);
  const ingestion = await client.ingestKnowledge({
    database,
    collection: fixture.collection,
    sources: fixture.sources,
  });
  const expectedIds = fixture.sources.map((source) => source.id);
  const results = validateIngestionResults(expectedIds, ingestion);
  const returnedIds = results.map((result) => result.id);

  await waitForIndexing(client, {
    database,
    collection: fixture.collection,
    ids: returnedIds,
    timeoutMs,
    pollIntervalMs,
    onProgress,
    now,
    sleep,
  });

  return {
    collection: fixture.collection,
    database,
    fixture: fixture.name,
    reused_database: reusedDatabase,
    source_count: fixture.sources.length,
    status: "completed",
  };
}

export async function runEvaluation(
  fixture,
  {
    client,
    database,
    profiles: selectedProfileIds = [],
    cliThresholds = {},
    onProgress = () => {},
    now = () => performance.now(),
    requestTimeoutMs = DEFAULTS.requestTimeoutMs,
  },
) {
  validateFixture(fixture);
  validateDatabaseName(database);
  const selected =
    selectedProfileIds.length === 0
      ? fixture.profiles
      : fixture.profiles.filter((profile) => selectedProfileIds.includes(profile.id));
  if (selected.length !== (selectedProfileIds.length || fixture.profiles.length)) {
    const known = new Set(fixture.profiles.map((profile) => profile.id));
    const unknown = selectedProfileIds.filter((id) => !known.has(id));
    throw new EvaluationError(`Unknown profile ID${unknown.length === 1 ? "" : "s"}: ${unknown.join(", ")}`);
  }

  onProgress(`Checking database and fixture indexing readiness`);
  const databaseStatus = await client.databaseStatus(database);
  if (!databaseIsReady(databaseStatus)) throw new EvaluationError(`Database ${database} is not ready for evaluation`);
  const sourceIds = fixture.sources.map((source) => source.id);
  const indexing = await client.contextStatus({ database, collection: fixture.collection, ids: sourceIds });
  requireCompletedStatuses(sourceIds, indexing);

  const profileReports = [];
  for (const profile of selected) {
    onProgress(`Running profile ${profile.id}`);
    const queryResults = [];
    for (const query of fixture.queries) {
      const request = buildQueryRequest(fixture, profile, query, database);
      const startedAt = now();
      const response = await client.query(request);
      const latencyMs = Math.max(0, now() - startedAt);
      if (!Array.isArray(response?.chunks)) {
        throw new EvaluationError(`Profile ${profile.id}, query ${query.id}: response is missing data.chunks`);
      }
      if (response.chunks.length === 0) {
        throw new EvaluationError(`Profile ${profile.id}, query ${query.id}: HydraDB returned no chunks`);
      }
      const rankedSourceIds = rankSources(response.chunks);
      if (rankedSourceIds.length === 0) {
        throw new EvaluationError(`Profile ${profile.id}, query ${query.id}: HydraDB returned no rankable sources`);
      }
      const metrics = calculateQueryMetrics(query.expected_source_ids, rankedSourceIds, fixture.k);
      queryResults.push({
        id: query.id,
        kind: query.kind,
        query: query.query,
        expected_source_ids: [...query.expected_source_ids],
        ranked_source_ids: rankedSourceIds,
        latency_ms: round(latencyMs, 2),
        ...metrics,
      });
    }
    profileReports.push(aggregateProfileResults(profile.id, queryResults, fixture.k));
  }

  const report = {
    schema_version: 1,
    fixture: {
      name: fixture.name,
      schema_version: fixture.schema_version,
      k: fixture.k,
    },
    database,
    collection: fixture.collection,
    execution: {
      request_timeout_ms: requestTimeoutMs,
    },
    profiles: profileReports,
  };
  const effectiveThresholds = Object.fromEntries(
    profileReports.map((profile) => [
      profile.id,
      effectiveGate(fixture.gates?.[profile.id], cliThresholds),
    ]),
  );
  const failures = evaluateGates(report, fixture.gates ?? {}, cliThresholds);
  return {
    ...report,
    gates: {
      passed: failures.length === 0,
      effective_thresholds: effectiveThresholds,
      failures,
    },
  };
}

function sortedJsonValue(value) {
  if (Array.isArray(value)) return value.map(sortedJsonValue);
  if (!isPlainObject(value)) return value;
  return Object.fromEntries(
    Object.keys(value)
      .sort()
      .map((key) => [key, sortedJsonValue(value[key])]),
  );
}

export function stableStringify(value, spaces = 2) {
  return JSON.stringify(sortedJsonValue(value), null, spaces);
}

export function formatJsonReport(report) {
  return `${stableStringify(report, 2)}\n`;
}

function escapeMarkdown(value) {
  return String(value).replaceAll("\\", "\\\\").replaceAll("|", "\\|").replaceAll("\n", " ");
}

function metric(value) {
  return Number(value).toFixed(3);
}

export function formatMarkdownReport(report) {
  const lines = [
    "# HydraDB retrieval quality report",
    "",
    "| Fixture | Database | Collection | K |",
    "| --- | --- | --- | ---: |",
    `| ${escapeMarkdown(report.fixture.name)} | ${escapeMarkdown(report.database)} | ${escapeMarkdown(report.collection)} | ${report.fixture.k} |`,
    "",
    "## Profile comparison",
    "",
    "| Profile | Hit@K | Source Recall@K | MRR@K | p50 latency | p95 latency |",
    "| --- | ---: | ---: | ---: | ---: | ---: |",
  ];
  for (const profile of report.profiles) {
    lines.push(
      `| ${escapeMarkdown(profile.id)} | ${metric(profile.summary.hit_at_k)} | ${metric(profile.summary.recall_at_k)} | ${metric(profile.summary.mrr_at_k)} | ${profile.summary.p50_latency_ms.toFixed(2)} ms | ${profile.summary.p95_latency_ms.toFixed(2)} ms |`,
    );
  }

  lines.push(
    "",
    "## Per-query results",
    "",
    "| Profile | Query | Kind | Expected sources | Ranked sources | Hit@K | Recall@K | MRR@K | Latency |",
    "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: |",
  );
  for (const profile of report.profiles) {
    for (const query of profile.queries) {
      lines.push(
        `| ${escapeMarkdown(profile.id)} | ${escapeMarkdown(query.id)} | ${escapeMarkdown(query.kind)} | ${escapeMarkdown(query.expected_source_ids.join(", "))} | ${escapeMarkdown(query.ranked_source_ids.join(", "))} | ${metric(query.hit_at_k)} | ${metric(query.recall_at_k)} | ${metric(query.mrr_at_k)} | ${query.latency_ms.toFixed(2)} ms |`,
      );
    }
  }

  lines.push(
    "",
    "## Regression gates",
    "",
    "| Profile | Min Hit@K | Min Recall@K | Min MRR@K | Max p95 latency |",
    "| --- | ---: | ---: | ---: | ---: |",
  );
  for (const profile of report.profiles) {
    const gate = report.gates.effective_thresholds?.[profile.id] ?? {};
    const value = (key) => (hasOwn(gate, key) ? String(gate[key]) : "-");
    lines.push(
      `| ${escapeMarkdown(profile.id)} | ${value("min_hit_at_k")} | ${value("min_recall_at_k")} | ${value("min_mrr_at_k")} | ${value("max_p95_latency_ms")} |`,
    );
  }
  lines.push("");
  if (report.gates.passed) {
    lines.push("PASS — all configured gates were met.");
  } else {
    lines.push("FAIL — one or more configured gates were not met.", "");
    for (const failure of report.gates.failures) {
      lines.push(
        `- ${escapeMarkdown(failure.profile)}: ${failure.metric} ${failure.operator} ${failure.threshold} (actual ${failure.actual})`,
      );
    }
  }
  return `${lines.join("\n")}\n`;
}

function parseFiniteOption(raw, flag, { minimum = Number.NEGATIVE_INFINITY, maximum = Number.POSITIVE_INFINITY } = {}) {
  const value = Number(raw);
  if (!Number.isFinite(value) || value < minimum || value > maximum) {
    throw new EvaluationError(`${flag} must be a number from ${minimum} through ${maximum}`);
  }
  return value;
}

function parseRequestTimeoutOption(raw, flag) {
  const value = Number(raw);
  if (!Number.isInteger(value) || value < 1 || value > MAX_TIMER_MS) {
    throw new EvaluationError(`${flag} must be an integer from 1 through ${MAX_TIMER_MS}`);
  }
  return value;
}

function readFlagValue(argv, index, flag) {
  const argument = argv[index];
  const equalsIndex = argument.indexOf("=");
  if (equalsIndex !== -1) {
    const value = argument.slice(equalsIndex + 1);
    if (!value) throw new EvaluationError(`${flag} requires a value`);
    return { value, consumed: 0 };
  }
  const value = argv[index + 1];
  if (!value || value.startsWith("--")) throw new EvaluationError(`${flag} requires a value`);
  return { value, consumed: 1 };
}

export function parseArgs(argv) {
  if (!Array.isArray(argv)) throw new EvaluationError("argv must be an array");
  const command = argv[0];
  if (!command || new Set(["--help", "-h", "help"]).has(command)) return { command: "help", options: {} };
  if (!new Set(["run", "seed", "validate"]).has(command)) {
    throw new EvaluationError(`Unknown command ${command}; expected validate, seed, or run`);
  }
  const options = {
    fixture: DEFAULTS.fixturePath,
    format: DEFAULTS.format,
    profiles: [],
    thresholds: {},
  };
  const allowedByCommand = {
    validate: new Set(["--fixture"]),
    seed: new Set([
      "--database",
      "--fixture",
      "--poll-interval-ms",
      "--request-timeout-ms",
      "--timeout-ms",
    ]),
    run: new Set([
      "--database",
      "--fixture",
      "--format",
      "--max-p95-latency-ms",
      "--min-hit-at-k",
      "--min-mrr-at-k",
      "--min-recall-at-k",
      "--profile",
      "--request-timeout-ms",
    ]),
  };

  for (let index = 1; index < argv.length; index += 1) {
    const argument = argv[index];
    if (new Set(["--help", "-h"]).has(argument)) return { command: "help", options: {} };
    if (!argument.startsWith("--")) throw new EvaluationError(`Unexpected positional argument ${argument}`);
    const flag = argument.includes("=") ? argument.slice(0, argument.indexOf("=")) : argument;
    if (!allowedByCommand[command].has(flag)) throw new EvaluationError(`${flag} is not valid for ${command}`);
    const { value, consumed } = readFlagValue(argv, index, flag);
    index += consumed;
    switch (flag) {
      case "--fixture":
        options.fixture = value;
        break;
      case "--database":
        options.database = value;
        break;
      case "--format":
        if (!new Set(["json", "markdown"]).has(value)) throw new EvaluationError("--format must be markdown or json");
        options.format = value;
        break;
      case "--profile":
        if (options.profiles.includes(value)) throw new EvaluationError(`--profile ${value} was provided more than once`);
        options.profiles.push(value);
        break;
      case "--timeout-ms":
        options.timeoutMs = parseFiniteOption(value, flag, { minimum: 1 });
        break;
      case "--poll-interval-ms":
        options.pollIntervalMs = parseFiniteOption(value, flag, { minimum: 0 });
        break;
      case "--request-timeout-ms":
        options.requestTimeoutMs = parseRequestTimeoutOption(value, flag);
        break;
      case "--min-hit-at-k":
      case "--min-recall-at-k":
      case "--min-mrr-at-k":
        options.thresholds[flag.slice(2).replaceAll("-", "_")] = parseFiniteOption(value, flag, {
          minimum: 0,
          maximum: 1,
        });
        break;
      case "--max-p95-latency-ms":
        options.thresholds.max_p95_latency_ms = parseFiniteOption(value, flag, { minimum: 0 });
        break;
      default:
        throw new EvaluationError(`Unsupported option ${flag}`);
    }
  }

  if (new Set(["run", "seed"]).has(command) && !isNonEmptyString(options.database)) {
    throw new EvaluationError(`${command} requires an explicit --database`);
  }
  if (options.database) validateDatabaseName(options.database);
  return { command, options };
}

function validateDatabaseName(database) {
  if (!isNonEmptyString(database) || database.length > 25 || !/^[A-Za-z0-9_-]+$/.test(database)) {
    throw new EvaluationError("--database must be 1-25 letters, numbers, underscores, or hyphens");
  }
  return database;
}

function usage() {
  return `HydraDB retrieval quality evaluator (Node.js 20+)

Usage:
  node scripts/retrieval-quality-eval.mjs validate [--fixture <path>]
  node scripts/retrieval-quality-eval.mjs seed --database <sandbox> [--fixture <path>] [--timeout-ms <ms>] [--poll-interval-ms <ms>]
       [--request-timeout-ms <ms>]
  node scripts/retrieval-quality-eval.mjs run --database <sandbox> [--fixture <path>] [--format markdown|json] [--profile <id> ...]
       [--request-timeout-ms <ms>]
       [--min-hit-at-k <0..1>] [--min-recall-at-k <0..1>] [--min-mrr-at-k <0..1>]
       [--max-p95-latency-ms <ms>]

Timeouts:
  --request-timeout-ms bounds each HTTP attempt (default 30000). For seed, --timeout-ms
  is the phase polling budget checked between bounded status requests.

Safety:
  validate is completely offline. run performs only status reads and retrieval queries.
  seed is the only command that creates or ingests data. No command deletes data.
`;
}

function writeTo(destination, text) {
  if (typeof destination === "function") destination(text);
  else destination.write(text);
}

function redactSecrets(text, secrets) {
  let redacted = String(text);
  for (const secret of secrets.filter(isNonEmptyString)) redacted = redacted.replaceAll(secret, "[REDACTED]");
  return redacted;
}

function safeErrorText(error, secrets = []) {
  let result;
  if (error instanceof FixtureValidationError) result = error.message;
  if (error instanceof HydraApiError) {
    const request = error.requestId ? ` (request ${error.requestId})` : "";
    result = `${error.message} [${error.code}]${request}`;
  }
  result ??= isNonEmptyString(error?.message) ? error.message : "Unknown evaluator error";
  return redactSecrets(result, secrets);
}

export async function runCli(
  argv,
  {
    cwd = process.cwd(),
    env = process.env,
    fetchImpl = globalThis.fetch,
    now,
    random = Math.random,
    sleep,
    stderr = process.stderr,
    stdout = process.stdout,
  } = {},
) {
  let credential;
  try {
    const { command, options } = parseArgs(argv);
    if (command === "help") {
      writeTo(stdout, usage());
      return EXIT_CODES.SUCCESS;
    }
    const fixturePath = path.resolve(cwd, options.fixture);
    const fixture = await loadFixture(fixturePath);
    if (command === "validate") {
      writeTo(
        stdout,
        `Valid fixture ${fixture.name}: ${fixture.sources.length} sources, ${fixture.queries.length} queries, ${fixture.profiles.length} profiles.\n`,
      );
      return EXIT_CODES.SUCCESS;
    }

    credential = env.HYDRA_DB_API_KEY;
    if (!isNonEmptyString(credential)) throw new EvaluationError("HYDRA_DB_API_KEY is required for seed and run");
    const requestTimeoutMs = options.requestTimeoutMs ?? DEFAULTS.requestTimeoutMs;
    const client = createApiClient({
      apiKey: credential,
      baseUrl: env.HYDRA_DB_BASE_URL || DEFAULTS.baseUrl,
      fetchImpl,
      random,
      requestTimeoutMs,
      sleep,
    });
    const onProgress = (message) => writeTo(stderr, `${message}\n`);

    if (command === "seed") {
      const result = await seedFixture(fixture, {
        client,
        database: options.database,
        timeoutMs: options.timeoutMs ?? DEFAULTS.seedTimeoutMs,
        pollIntervalMs: options.pollIntervalMs ?? DEFAULTS.pollIntervalMs,
        onProgress,
        now: now ?? Date.now,
        sleep,
      });
      writeTo(
        stdout,
        `${stableStringify({
          ...result,
          execution: { request_timeout_ms: requestTimeoutMs },
        }, 2)}\n`,
      );
      return EXIT_CODES.SUCCESS;
    }

    const report = await runEvaluation(fixture, {
      client,
      database: options.database,
      profiles: options.profiles,
      cliThresholds: options.thresholds,
      onProgress,
      now: now ?? (() => performance.now()),
      requestTimeoutMs,
    });
    writeTo(stdout, options.format === "json" ? formatJsonReport(report) : formatMarkdownReport(report));
    return report.gates.passed ? EXIT_CODES.SUCCESS : EXIT_CODES.GATE_FAILURE;
  } catch (error) {
    writeTo(stderr, `Error: ${safeErrorText(error, [credential])}\n`);
    return EXIT_CODES.ERROR;
  }
}

export async function main(argv = process.argv.slice(2)) {
  return runCli(argv);
}

const isMain = process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url;
if (isMain) {
  process.exitCode = await main();
}
