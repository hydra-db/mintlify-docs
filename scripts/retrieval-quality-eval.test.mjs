import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import {
  FixtureValidationError,
  aggregateProfileResults,
  buildQueryRequest,
  calculateQueryMetrics,
  createApiClient,
  evaluateGates,
  nearestRankPercentile,
  rankSources,
  requestJson,
  runCli,
  stableStringify,
  validateFixture,
  validateStatusEntries,
  waitForDatabaseReady,
  waitForIndexing,
} from "./retrieval-quality-eval.mjs";

const scriptsDirectory = dirname(fileURLToPath(import.meta.url));
const repositoryRoot = resolve(scriptsDirectory, "..");
const fixturePath = resolve(
  scriptsDirectory,
  "fixtures/retrieval-quality-v1.json",
);
const fixture = JSON.parse(await readFile(fixturePath, "utf8"));

function clone(value) {
  return structuredClone(value);
}

function issueText(error) {
  return JSON.stringify(error?.issues ?? [error?.message ?? String(error)]);
}

function successResponse(data, status = 200) {
  return new Response(
    JSON.stringify({
      success: true,
      data,
      error: null,
      meta: { request_id: "req_unit_test", latency_ms: 1 },
    }),
    {
      status,
      headers: { "content-type": "application/json" },
    },
  );
}

function errorResponse(status, code = "TRANSIENT", message = "try again") {
  return new Response(
    JSON.stringify({
      success: false,
      data: null,
      error: { code, message },
      meta: { request_id: "req_unit_test", latency_ms: 1 },
    }),
    {
      status,
      headers: { "content-type": "application/json" },
    },
  );
}

function statusEntry(id, indexingStatus) {
  return {
    id,
    indexing_status: indexingStatus,
    error_code: "",
    success: indexingStatus !== "failed" && indexingStatus !== "errored",
    message: "unit test status",
  };
}

function outputBuffer() {
  let value = "";
  return {
    stream: {
      write(chunk) {
        value += String(chunk);
        return true;
      },
    },
    read() {
      return value;
    },
  };
}

function deterministicNow(step = 7) {
  let time = 0;
  return () => {
    time += step;
    return time;
  };
}

function makeRunFetch({ result = "expected" } = {}) {
  const calls = [];
  const expectedByQuery = new Map(
    fixture.queries.map((query) => [query.query, query.expected_source_ids[0]]),
  );

  const fetchImpl = async (input, init = {}) => {
    const url = new URL(
      typeof input === "string" || input instanceof URL ? input : input.url,
    );
    const method = init.method ?? input?.method ?? "GET";
    const call = { method, pathname: url.pathname };
    calls.push(call);

    if (url.pathname === "/databases/status") {
      return successResponse({
        database: "rql_unit_test",
        infra: {
          scheduler_status: true,
          graph_status: true,
          vectorstore_status: { knowledge: true, memories: true },
          ready_for_ingestion: true,
        },
      });
    }

    if (url.pathname === "/context/status") {
      return successResponse({
        statuses: fixture.sources.map((source) =>
          statusEntry(source.id, "completed"),
        ),
      });
    }

    if (url.pathname === "/query") {
      const request = JSON.parse(String(init.body));
      call.request = request;
      if (result === "empty") {
        return successResponse({ chunks: [] });
      }
      if (result === "credential-error") {
        return errorResponse(
          400,
          "BAD_QUERY",
          "unit-test-secret-do-not-print was rejected",
        );
      }

      const id =
        result === "expected"
          ? expectedByQuery.get(request.query)
          : "rql-v1-calibration-register";
      return successResponse({
        chunks: [
          {
            id,
            chunk_uuid: `${id}:chunk-1`,
            text: "Synthetic unit-test result.",
          },
        ],
      });
    }

    throw new Error(`Unexpected test request: ${method} ${url.pathname}`);
  };

  return { calls, fetchImpl };
}

async function invokeRunCli({ result = "expected", extraArgs = [] } = {}) {
  const stdout = outputBuffer();
  const stderr = outputBuffer();
  const api = makeRunFetch({ result });
  const exitCode = await runCli(
    [
      "run",
      "--fixture",
      fixturePath,
      "--database",
      "rql_unit_test",
      "--profile",
      "text",
      "--format",
      "json",
      ...extraArgs,
    ],
    {
      cwd: repositoryRoot,
      env: {
        HYDRA_DB_API_KEY: "unit-test-secret-do-not-print",
        HYDRA_DB_BASE_URL: "https://hydradb.invalid",
      },
      fetchImpl: api.fetchImpl,
      now: deterministicNow(),
      random: () => 0,
      sleep: async () => {},
      stdout: stdout.stream,
      stderr: stderr.stream,
    },
  );

  return {
    calls: api.calls,
    exitCode,
    stderr: stderr.read(),
    stdout: stdout.read(),
  };
}

async function invokeSeedCli({ reuseDatabase = false, omitIngestionId } = {}) {
  const stdout = outputBuffer();
  const stderr = outputBuffer();
  const calls = [];
  let ingestedSources = [];

  const fetchImpl = async (input, init = {}) => {
    const url = new URL(
      typeof input === "string" || input instanceof URL ? input : input.url,
    );
    const method = init.method ?? input?.method ?? "GET";
    calls.push({ method, pathname: url.pathname });

    if (url.pathname === "/databases" && method === "POST") {
      return reuseDatabase
        ? errorResponse(
            409,
            "DATABASE_ALREADY_EXISTS",
            "Database already exists",
          )
        : successResponse({ database: "rql_seed_test", status: "accepted" });
    }

    if (url.pathname === "/databases/status") {
      return successResponse({
        database: "rql_seed_test",
        infra: {
          scheduler_status: true,
          graph_status: true,
          vectorstore_status: { knowledge: true, memories: true },
        },
      });
    }

    if (url.pathname === "/context/ingest" && method === "POST") {
      ingestedSources = JSON.parse(String(init.body.get("app_knowledge")));
      const results = fixture.sources
        .filter((source) => source.id !== omitIngestionId)
        .map((source) => ({
          id: source.id,
          status: "queued",
          error: null,
        }));
      return successResponse(
        {
          success: true,
          results,
          success_count: fixture.sources.length,
          failed_count: 0,
        },
        202,
      );
    }

    if (url.pathname === "/context/status") {
      return successResponse({
        statuses: url.searchParams
          .getAll("ids")
          .map((id) => statusEntry(id, "completed")),
      });
    }

    throw new Error(`Unexpected test request: ${method} ${url.pathname}`);
  };

  const exitCode = await runCli(
    [
      "seed",
      "--fixture",
      fixturePath,
      "--database",
      "rql_seed_test",
      "--timeout-ms",
      "100",
      "--poll-interval-ms",
      "0",
    ],
    {
      cwd: repositoryRoot,
      env: {
        HYDRA_DB_API_KEY: "unit-test-secret-do-not-print",
        HYDRA_DB_BASE_URL: "https://hydradb.invalid",
      },
      fetchImpl,
      random: () => 0,
      sleep: async () => {},
      stdout: stdout.stream,
      stderr: stderr.stream,
    },
  );

  return {
    calls,
    exitCode,
    ingestedSources,
    stderr: stderr.read(),
    stdout: stdout.read(),
  };
}

test("the versioned fixture is valid and uses app-native searchable fields", () => {
  assert.doesNotThrow(() => validateFixture(fixture));
  assert.equal(fixture.schema_version, 1);
  assert.match(fixture.collection, /(?:^|_)v1$/);
  assert.deepEqual(
    fixture.profiles.map((profile) => profile.id),
    ["text", "hybrid-fast", "hybrid-thinking"],
  );
  assert.deepEqual(new Set(fixture.queries.map((query) => query.kind)), new Set([
    "literal",
    "semantic",
    "multi-source",
  ]));

  for (const source of fixture.sources) {
    assert.equal(source.kind, "knowledge_base");
    assert.equal(source.fields.kind, "knowledge_base");
    assert.equal(typeof source.fields.body, "string");
    assert.ok(source.fields.body.length > 20);
    assert.equal("content" in source, false);
  }
});

test("profiles serialize the intended text, fast, and thinking query contracts", () => {
  const query = fixture.queries[0];
  const requests = Object.fromEntries(
    fixture.profiles.map((profile) => [
      profile.id,
      buildQueryRequest(fixture, profile, query, "rql_unit_test"),
    ]),
  );

  assert.deepEqual(requests.text, {
    database: "rql_unit_test",
    collection: fixture.collection,
    query: query.query,
    type: "knowledge",
    query_by: "text",
    max_results: 8,
    graph_context: false,
    query_apps: true,
    operator: "or",
  });
  assert.deepEqual(requests["hybrid-fast"], {
    database: "rql_unit_test",
    collection: fixture.collection,
    query: query.query,
    type: "knowledge",
    query_by: "hybrid",
    max_results: 8,
    graph_context: false,
    query_apps: true,
    alpha: 0.8,
    mode: "fast",
  });
  assert.deepEqual(requests["hybrid-thinking"], {
    database: "rql_unit_test",
    collection: fixture.collection,
    query: query.query,
    type: "knowledge",
    query_by: "hybrid",
    max_results: 8,
    graph_context: true,
    query_apps: true,
    alpha: 0.8,
    mode: "thinking",
  });
});

test("fixture validation is strict and reports all discovered issues", () => {
  const invalid = clone(fixture);
  invalid.unknown_root_key = true;
  invalid.sources[1].id = invalid.sources[0].id;
  invalid.queries[0].expected_source_ids = ["source-that-does-not-exist"];
  invalid.queries.find((query) => query.kind === "multi-source").expected_source_ids = [
    invalid.sources[0].id,
  ];
  invalid.profiles[0].mode = "thinking";
  invalid.profiles[1].max_results = invalid.k - 1;
  invalid.gates["missing-profile"] = { min_hit_at_k: 0.5 };

  assert.throws(
    () => validateFixture(invalid),
    (error) => {
      assert.ok(error instanceof FixtureValidationError);
      assert.ok(Array.isArray(error.issues));
      assert.ok(error.issues.length >= 7, issueText(error));
      const issues = issueText(error);
      assert.match(issues, /unknown_root_key/i);
      assert.match(issues, /duplicate/i);
      assert.match(issues, /source-that-does-not-exist/i);
      assert.match(issues, /multi-source/i);
      assert.match(issues, /mode/i);
      assert.match(issues, /max_results/i);
      assert.match(issues, /missing-profile/i);
      return true;
    },
  );
});

test("fixture validation rejects invalid profile ranges and query kinds", () => {
  const invalid = clone(fixture);
  invalid.queries[0].kind = "keyword-ish";
  invalid.profiles[1].alpha = 1.1;
  invalid.profiles[1].recency_bias = 1.1;
  delete invalid.profiles[2].query_apps;
  invalid.gates["hybrid-fast"].min_recall_at_k = -0.1;

  assert.throws(
    () => validateFixture(invalid),
    (error) => {
      assert.ok(error instanceof FixtureValidationError);
      const issues = issueText(error);
      assert.match(issues, /keyword-ish|kind/i);
      assert.match(issues, /alpha/i);
      assert.match(issues, /recency_bias/i);
      assert.match(issues, /query_apps/i);
      assert.match(issues, /min_recall_at_k/i);
      return true;
    },
  );

  const phraseProfile = clone(fixture);
  phraseProfile.profiles[0].operator = "phrase";
  assert.doesNotThrow(() => validateFixture(phraseProfile));
});

test("rankSources uses the first chunk per source and never lets duplicates inflate rank", () => {
  const chunks = [
    { id: "source-a", chunk_uuid: "a-1" },
    { id: "source-a", chunk_uuid: "a-2" },
    { id: "source-b", chunk_uuid: "b-1" },
    { id: "source-c", chunk_uuid: "c-1" },
  ];

  assert.deepEqual(rankSources(chunks), ["source-a", "source-b", "source-c"]);
  assert.deepEqual(rankSources(chunks, 2), ["source-a", "source-b"]);
  assert.deepEqual(rankSources([]), []);
  assert.throws(() => rankSources([{ chunk_uuid: "missing-source-id" }]), /id/i);
});

test("query metrics calculate Hit@K, source Recall@K, and MRR@K", () => {
  assert.deepEqual(
    calculateQueryMetrics(["source-a", "source-b"], ["noise", "source-a", "source-b"], 2),
    {
      hit_at_k: 1,
      recall_at_k: 0.5,
      mrr_at_k: 0.5,
    },
  );
  assert.deepEqual(
    calculateQueryMetrics(["source-a", "source-b"], ["noise", "source-a", "source-b"], 3),
    {
      hit_at_k: 1,
      recall_at_k: 1,
      mrr_at_k: 0.5,
    },
  );
  assert.deepEqual(calculateQueryMetrics(["source-a"], ["noise"], 5), {
    hit_at_k: 0,
    recall_at_k: 0,
    mrr_at_k: 0,
  });
});

test("nearest-rank percentiles are deterministic and do not mutate input", () => {
  const values = [40, 10, 30, 20];
  assert.equal(nearestRankPercentile(values, 0.5), 20);
  assert.equal(nearestRankPercentile(values, 0.95), 40);
  assert.deepEqual(values, [40, 10, 30, 20]);
});

test("profile aggregation macro-averages metrics and computes p50/p95 latency", () => {
  const result = aggregateProfileResults(
    "hybrid-fast",
    [
      {
        id: "q1",
        kind: "literal",
        query: "one",
        expected_source_ids: ["a"],
        ranked_source_ids: ["a"],
        hit_at_k: 1,
        recall_at_k: 1,
        mrr_at_k: 1,
        latency_ms: 10,
      },
      {
        id: "q2",
        kind: "multi-source",
        query: "two",
        expected_source_ids: ["b", "c"],
        ranked_source_ids: ["x", "b"],
        hit_at_k: 1,
        recall_at_k: 0.5,
        mrr_at_k: 0.5,
        latency_ms: 30,
      },
      {
        id: "q3",
        kind: "semantic",
        query: "three",
        expected_source_ids: ["d"],
        ranked_source_ids: ["x"],
        hit_at_k: 0,
        recall_at_k: 0,
        mrr_at_k: 0,
        latency_ms: 20,
      },
    ],
    5,
  );

  assert.deepEqual(result.summary, {
    query_count: 3,
    hit_at_k: 0.666667,
    recall_at_k: 0.5,
    mrr_at_k: 0.5,
    p50_latency_ms: 20,
    p95_latency_ms: 30,
  });
});

test("gate evaluation reports profile thresholds without throwing", () => {
  const report = {
    profiles: [
      {
        id: "hybrid-fast",
        summary: {
          query_count: 2,
          hit_at_k: 0.5,
          recall_at_k: 0.4,
          mrr_at_k: 0.3,
          p50_latency_ms: 20,
          p95_latency_ms: 80,
        },
        queries: [],
      },
    ],
  };
  const failures = evaluateGates(
    report,
    {
      "hybrid-fast": {
        min_hit_at_k: 0.75,
        min_recall_at_k: 0.4,
        max_p95_latency_ms: 50,
      },
    },
    {},
  );

  assert.deepEqual(
    failures.map(({ metric, operator }) => [metric, operator]),
    [
      ["hit_at_k", ">="],
      ["p95_latency_ms", "<="],
    ],
  );
});

test("requestJson rejects malformed success envelopes", async () => {
  const malformedPayloads = [
    { data: {} },
    { success: true, data: {}, error: null },
    { success: false, data: {}, error: null, meta: {} },
    { success: true, data: null, error: null, meta: {} },
    { success: true, data: {}, error: { code: "NOPE" }, meta: {} },
  ];

  for (const payload of malformedPayloads) {
    await assert.rejects(
      requestJson(
        "https://hydradb.invalid/query",
        { method: "POST" },
        {
          fetchImpl: async () =>
            new Response(JSON.stringify(payload), {
              status: 200,
              headers: { "content-type": "application/json" },
            }),
          maxRetries: 0,
          readOnly: true,
          sleep: async () => {},
        },
      ),
      /envelope|success|data|error|meta/i,
    );
  }
});

test("custom API origins require HTTPS except on loopback", () => {
  const options = {
    apiKey: "unit-test-secret-do-not-print",
    fetchImpl: async () => successResponse({}),
  };
  assert.doesNotThrow(() =>
    createApiClient({ ...options, baseUrl: "http://127.0.0.1:8787" }),
  );
  assert.doesNotThrow(() =>
    createApiClient({ ...options, baseUrl: "http://localhost:8787" }),
  );
  assert.doesNotThrow(() =>
    createApiClient({ ...options, baseUrl: "https://mock.example.test" }),
  );
  assert.throws(
    () => createApiClient({ ...options, baseUrl: "http://mock.example.test" }),
    /HTTPS|loopback/i,
  );
});

test("read-only requests retry bounded transient failures", async () => {
  let calls = 0;
  const delays = [];
  const data = await requestJson(
    "https://hydradb.invalid/query",
    { method: "POST" },
    {
      fetchImpl: async () => {
        calls += 1;
        return calls < 3
          ? errorResponse(calls === 1 ? 429 : 503)
          : successResponse({ chunks: [{ id: "source-a" }] });
      },
      maxRetries: 2,
      random: () => 0,
      readOnly: true,
      sleep: async (delay) => delays.push(delay),
    },
  );

  assert.equal(calls, 3);
  assert.equal(delays.length, 2);
  assert.deepEqual(data, { chunks: [{ id: "source-a" }] });
});

test("request timeout covers a stalled response body", async () => {
  let signalAborted = false;
  await assert.rejects(
    requestJson(
      "https://hydradb.invalid/query",
      { method: "POST" },
      {
        fetchImpl: async (_url, init) => ({
          ok: true,
          status: 200,
          headers: new Headers(),
          text: () =>
            new Promise((resolve, reject) => {
              init.signal.addEventListener(
                "abort",
                () => {
                  signalAborted = true;
                  const error = new Error("body read aborted");
                  error.name = "AbortError";
                  reject(error);
                },
                { once: true },
              );
            }),
        }),
        maxRetries: 0,
        readOnly: true,
        requestTimeoutMs: 5,
      },
    ),
    /timed out|REQUEST_TIMEOUT/i,
  );
  assert.equal(signalAborted, true);
});

test("read-only requests retry a transient response-body failure", async () => {
  let calls = 0;
  const data = await requestJson(
    "https://hydradb.invalid/query",
    { method: "POST" },
    {
      fetchImpl: async () => {
        calls += 1;
        if (calls === 1) {
          return {
            ok: true,
            status: 200,
            headers: new Headers(),
            async text() {
              throw new TypeError("connection reset while reading body");
            },
          };
        }
        return successResponse({ chunks: [{ id: "source-a" }] });
      },
      maxRetries: 1,
      random: () => 0,
      readOnly: true,
      sleep: async () => {},
    },
  );

  assert.equal(calls, 2);
  assert.deepEqual(data, { chunks: [{ id: "source-a" }] });
});

test("mutating requests are never retried", async () => {
  let calls = 0;
  await assert.rejects(
    requestJson(
      "https://hydradb.invalid/context/ingest",
      { method: "POST" },
      {
        fetchImpl: async () => {
          calls += 1;
          return errorResponse(503);
        },
        maxRetries: 3,
        readOnly: false,
        sleep: async () => {},
      },
    ),
    /try again|TRANSIENT|503/i,
  );
  assert.equal(calls, 1);
});

test("non-transient read failures are not retried", async () => {
  let calls = 0;
  await assert.rejects(
    requestJson(
      "https://hydradb.invalid/query",
      { method: "POST" },
      {
        fetchImpl: async () => {
          calls += 1;
          return errorResponse(400, "BAD_QUERY", "bad query");
        },
        maxRetries: 3,
        readOnly: true,
        sleep: async () => {},
      },
    ),
    /bad query|BAD_QUERY|400/i,
  );
  assert.equal(calls, 1);
});

test("status validation restores requested order and rejects missing or duplicate entries", () => {
  assert.deepEqual(
    validateStatusEntries(
      ["source-a", "source-b"],
      [statusEntry("source-b", "completed"), statusEntry("source-a", "processing")],
    ).map((entry) => entry.id),
    ["source-a", "source-b"],
  );

  assert.throws(
    () =>
      validateStatusEntries(
        ["source-a", "source-b"],
        [statusEntry("source-a", "completed")],
      ),
    /missing|source-b/i,
  );
  assert.throws(
    () =>
      validateStatusEntries(
        ["source-a", "source-b"],
        [
          statusEntry("source-a", "completed"),
          statusEntry("source-a", "completed"),
        ],
      ),
    /duplicate|source-a/i,
  );
  assert.throws(
    () =>
      validateStatusEntries(
        ["source-a"],
        [statusEntry("source-a", "completed"), statusEntry("source-z", "completed")],
      ),
    /unexpected|unknown|source-z/i,
  );
});

test("indexing polling waits for every ID to complete", async () => {
  let calls = 0;
  const client = {
    async contextStatus() {
      calls += 1;
      return {
        statuses: [
          statusEntry("source-a", calls === 1 ? "processing" : "completed"),
          statusEntry("source-b", "completed"),
        ],
      };
    },
  };
  let clock = 0;
  const statuses = await waitForIndexing(client, {
    database: "rql_unit_test",
    collection: "retrieval_quality_lab",
    ids: ["source-a", "source-b"],
    timeoutMs: 100,
    pollIntervalMs: 1,
    now: () => clock,
    sleep: async (delay) => {
      clock += delay;
    },
  });

  assert.equal(calls, 2);
  assert.deepEqual(statuses.map((entry) => entry.indexing_status), [
    "completed",
    "completed",
  ]);
});

for (const failedStatus of ["failed", "errored"]) {
  test(`indexing polling fails immediately on ${failedStatus}`, async () => {
    const client = {
      async contextStatus() {
        return { statuses: [statusEntry("source-a", failedStatus)] };
      },
    };
    await assert.rejects(
      waitForIndexing(client, {
        database: "rql_unit_test",
        collection: "retrieval_quality_lab",
        ids: ["source-a"],
        timeoutMs: 100,
        pollIntervalMs: 1,
        now: () => 0,
        sleep: async () => {},
      }),
      new RegExp(`${failedStatus}|source-a`, "i"),
    );
  });
}

test("indexing polling rejects unknown states and times out deterministically", async () => {
  await assert.rejects(
    waitForIndexing(
      {
        async contextStatus() {
          return { statuses: [statusEntry("source-a", "mysterious")] };
        },
      },
      {
        database: "rql_unit_test",
        collection: "retrieval_quality_lab",
        ids: ["source-a"],
        timeoutMs: 100,
        pollIntervalMs: 1,
        now: () => 0,
        sleep: async () => {},
      },
    ),
    /unknown|mysterious/i,
  );

  let clock = 0;
  await assert.rejects(
    waitForIndexing(
      {
        async contextStatus() {
          return { statuses: [statusEntry("source-a", "processing")] };
        },
      },
      {
        database: "rql_unit_test",
        collection: "retrieval_quality_lab",
        ids: ["source-a"],
        timeoutMs: 2,
        pollIntervalMs: 1,
        now: () => clock,
        sleep: async (delay) => {
          clock += delay;
        },
      },
    ),
    /timed out|timeout/i,
  );
});

test("poll deadlines include time spent inside the final status request", async () => {
  let databaseClock = 0;
  await assert.rejects(
    waitForDatabaseReady(
      {
        async databaseStatus() {
          databaseClock = 6;
          return {
            infra: {
              scheduler_status: true,
              graph_status: true,
              vectorstore_status: { knowledge: true, memories: true },
            },
          };
        },
      },
      {
        database: "rql_unit_test",
        timeoutMs: 5,
        pollIntervalMs: 0,
        now: () => databaseClock,
        sleep: async () => {},
      },
    ),
    /timeout/i,
  );

  let indexingClock = 0;
  await assert.rejects(
    waitForIndexing(
      {
        async contextStatus() {
          indexingClock = 6;
          return { statuses: [statusEntry("source-a", "completed")] };
        },
      },
      {
        database: "rql_unit_test",
        collection: "retrieval_quality_lab",
        ids: ["source-a"],
        timeoutMs: 5,
        pollIntervalMs: 0,
        now: () => indexingClock,
        sleep: async () => {},
      },
    ),
    /timeout/i,
  );
});

test("validate is completely offline", async () => {
  const stdout = outputBuffer();
  const stderr = outputBuffer();
  let fetchCalls = 0;
  const exitCode = await runCli(
    ["validate", "--fixture", fixturePath],
    {
      cwd: repositoryRoot,
      env: {},
      fetchImpl: async () => {
        fetchCalls += 1;
        throw new Error("validate must not use the network");
      },
      stdout: stdout.stream,
      stderr: stderr.stream,
    },
  );

  assert.equal(exitCode, 0);
  assert.equal(fetchCalls, 0);
  assert.doesNotMatch(`${stdout.read()}${stderr.read()}`, /api[_ -]?key/i);
});

test("seed creates or explicitly reuses one sandbox and ingests fixed app-source IDs", async () => {
  for (const reuseDatabase of [false, true]) {
    const result = await invokeSeedCli({ reuseDatabase });
    assert.equal(result.exitCode, 0, result.stderr);
    const output = JSON.parse(result.stdout);
    assert.equal(output.reused_database, reuseDatabase);
    assert.equal(output.database, "rql_seed_test");
    assert.equal(output.status, "completed");
    assert.deepEqual(
      result.ingestedSources.map((source) => source.id),
      fixture.sources.map((source) => source.id),
    );
    assert.ok(
      result.ingestedSources.every(
        (source) =>
          source.database === "rql_seed_test" &&
          source.collection === fixture.collection &&
          typeof source.fields?.body === "string" &&
          !("content" in source),
      ),
    );
    assert.equal(
      result.calls.filter(
        (call) => call.pathname === "/databases" && call.method === "POST",
      ).length,
      1,
    );
    assert.equal(
      result.calls.filter(
        (call) =>
          call.pathname === "/context/ingest" && call.method === "POST",
      ).length,
      1,
    );
    assert.equal(result.calls.some((call) => call.method === "DELETE"), false);
    assert.doesNotMatch(
      `${result.stdout}${result.stderr}`,
      /unit-test-secret-do-not-print/,
    );
  }
});

test("seed fails when ingestion omits a fixture ID", async () => {
  const missingId = fixture.sources[0].id;
  const result = await invokeSeedCli({ omitIngestionId: missingId });
  assert.equal(result.exitCode, 1);
  assert.match(result.stderr, new RegExp(`missing source ID ${missingId}`, "i"));
  assert.equal(
    result.calls.some((call) => call.pathname === "/context/status"),
    false,
  );
});

test("run returns the threshold exit code and performs no mutations", async () => {
  const result = await invokeRunCli({
    result: "wrong",
    extraArgs: ["--min-hit-at-k", "1"],
  });

  assert.equal(result.exitCode, 2);
  assert.equal(
    JSON.parse(result.stdout).gates.effective_thresholds.text.min_hit_at_k,
    1,
  );
  assert.ok(result.calls.some((call) => call.pathname === "/query"));
  assert.ok(
    result.calls
      .filter((call) => call.pathname === "/query")
      .every((call) => call.request.query_apps === true),
  );
  assert.equal(
    result.calls.some(
      (call) =>
        call.pathname === "/context/ingest" ||
        (call.pathname === "/databases" && call.method === "POST") ||
        call.method === "DELETE",
    ),
    false,
  );
  assert.doesNotMatch(
    `${result.stdout}${result.stderr}`,
    /unit-test-secret-do-not-print/,
  );
});

test("run treats an empty chunk list as an error", async () => {
  const result = await invokeRunCli({ result: "empty" });
  assert.equal(result.exitCode, 1);
  assert.match(result.stderr, /empty|no (chunks|results)/i);
});

test("API error messages cannot echo the credential", async () => {
  const result = await invokeRunCli({ result: "credential-error" });
  assert.equal(result.exitCode, 1);
  assert.doesNotMatch(
    `${result.stdout}${result.stderr}`,
    /unit-test-secret-do-not-print/,
  );
  assert.match(result.stderr, /\[REDACTED\]/);
});

test("JSON output is byte-stable for identical deterministic runs", async () => {
  const first = await invokeRunCli();
  const second = await invokeRunCli();

  assert.equal(first.exitCode, 0, first.stderr);
  assert.equal(second.exitCode, 0, second.stderr);
  assert.equal(first.stdout, second.stdout);
  assert.deepEqual(JSON.parse(first.stdout), JSON.parse(second.stdout));
  assert.equal(
    stableStringify(JSON.parse(first.stdout)).trim(),
    first.stdout.trim(),
  );
});
