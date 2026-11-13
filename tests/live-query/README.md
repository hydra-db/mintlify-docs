# Live query tests

These tests use the official `@hydradb/sdk` TypeScript client against an existing database. They list collections and knowledge sources, then run one query. They do not ingest, update, or delete context, and they do not call OpenAI.

The tests check that the SDK returns source chunks, then feed the actual HTTP response through `search_code` extracted from the Cursor cookbook. That check verifies that the tool preserves graph context, linked context, and every chunk's citation path. The Python bridge prints counts only.

## Run

Use Node 24 and `uv`. Install the repository's locked dependencies first with `pnpm install --frozen-lockfile`. The Python bridge uses `hydradb-sdk==2.1.7`; `uv` installs it into its cache.

Keep the API key in a local environment file outside Git:

```dotenv
HYDRADB_API_KEY=your-local-key
```

From the repository root:

```bash
HYDRADB_DATABASE=your-database mise exec node@24.11.0 -- \
  node --env-file=/absolute/path/to/your/local.env \
  --test tests/live-query/live-query.test.ts
```

Optional environment variables:

| Variable | Purpose |
| --- | --- |
| `HYDRADB_COLLECTION` | Query this collection. An empty value selects the default collection. If omitted, inspect the database's collections and prefer a source with declared relations. |
| `HYDRADB_TEST_QUERY` | Use this question. If omitted, build a question from an existing source's title. |

The key is loaded by Node and passed directly to the SDK. The tests never print environment values, authorization headers, source text, or full API errors. The Python subprocess does not receive the key. Do not run the tests with HTTP debug logging enabled.

Graph and linked-context checks report an explicit skip when the chosen query returns no such evidence. That is a limit of the selected data, not proof that nonempty context survived. The offline regression in `scripts/test_cookbooks.py` covers both with concrete expected values. To exercise them live, select a collection and query with graph relationships and sources linked by `relations.ids`.

Type-check the suite without contacting the API:

```bash
mise exec node@24.11.0 -- node_modules/.bin/tsc -p tests/live-query/tsconfig.json
```

The benchmark wording fix is checked against the published benchmark reports. An API query cannot verify a benchmark's scoring protocol.
