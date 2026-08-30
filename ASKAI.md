# HydraDB "Ask AI" widget (`askai.js`)

A drop-in, framework-agnostic docs assistant: a launcher + slide-out chat panel
that answers questions from your docs with streamed, cited answers. Built for
Mintlify (which auto-loads root-level `.js`, like `footer.js`/`reo.js`) but works
in any HTML site. The whole widget renders inside a **Shadow DOM** host, so the
page's CSS can't leak in and the widget's can't leak out.

## Install (Mintlify)

`askai.js` at the repo root is picked up automatically — Mintlify injects every
root `.js` on every page. Configure it with a tiny inline snippet in `docs.json`
(or a second root script) that sets a global **before** `askai.js` runs.

## Configure

```html
<script>
  window.HydraAskAI = {
    endpoint: "https://ask.yourdomain.com",  // ask API base (POST /docs/ask)
    apiKey:   "pk_docs_readonly_xxx",        // optional — see "API key" below
    logo:     "",                            // optional custom logo URL (defaults to HydraDB mark)
    theme:    { accent: "#FF571A" },          // optional brand override
    modes:    ["fast", "auto", "thinking"],  // think-modes to show (or one)
  };
</script>
```

Everything is optional except `endpoint`. All values here are **public** — never
put a privileged/secret key in the browser (see below).

Attribution (**"Powered by HydraDB"** hyperlinked to `hydradb.com`) is always
displayed in the footer (Kapa-style), alongside the original HydraDB logo mark in
the launcher button and panel header.

## API key (composable — no prop required)

Resolution order: `config.apiKey` → `window.HYDRA_ASKAI_KEY` → none.

- **Mintlify / static hosts:** there is no build step you control and no
  server-side env, so set the value in the runtime config object above. Use a
  **public, read-only, docs-scoped** key (safe to expose — it's rate-limited and
  domain-allowlisted server-side, exactly like Kapa's `data-website-id` or
  Inkeep's `NEXT_PUBLIC_INKEEP_API_KEY`).
- **Framework hosts (Next.js/Vite/Docusaurus):** inject a public env var into the
  global so the key isn't hard-coded:
  ```js
  window.HYDRA_ASKAI_KEY = process.env.NEXT_PUBLIC_HYDRA_ASKAI_KEY; // Next.js
  window.HYDRA_ASKAI_KEY = import.meta.env.VITE_HYDRA_ASKAI_KEY;    // Vite
  ```
- **If your ask endpoint injects the key server-side, omit it entirely.**

> A real secret (a write key, an LLM provider key) must **never** ship to the
> browser. Mintlify-hosted sites have no server, so those live only in your
> backend / the docs-sync CI secret — see `_ci-proposed/README.md`.

## Theme (inherits the host's colors)

Token precedence: `config.theme.<token>` → host CSS var `--askai-<token>` on
`:root` → built-in default. A host that sets `--askai-accent: #7C3AED` themes the
widget with zero JS. Tokens: `accent`, `panel`, `bg`, `text`, `muted`, `line`.

## Backend contract

`POST {endpoint}/docs/ask`, body `{ "query": string, "mode": "fast|auto|thinking" }`,
optional `Authorization: Bearer <key>`. Response is newline-delimited JSON:

```
{"type":"sources","sources":[{"index":1,"title":"…","url":"/…"}]}
{"type":"delta","text":"…"}          // repeated
{"type":"done"}
```

The docs corpus is populated by the sync Action in `_ci-proposed/`.

## Backend: Option A (hosted) or Option B (open-source Rust binary `askai-gateway`)

1. **Option A (Hosted):** Set `endpoint` to a hosted HydraDB Ask endpoint (`https://agents.hydradb.com`).
2. **Option B (Self-hosted Rust binary):** Run [`askai-gateway`](https://github.com/hydra-db/mintlify-docs/tree/main/askai-gateway) — a tiny, zero-dependency Axum binary built for this widget:
   ```bash
   export HYDRA_API_KEY=...
   export LLM_API_KEY=... # OpenRouter or any OpenAI-compatible provider
   ./askai-gateway        # listens on :8080, serves POST /docs/ask
   ```

## Run locally, end to end (verified)

The widget is fully composable: **the page only has to pass `endpoint`.** Point it
at a running `askai-gateway` and everything else (retrieval, streaming, citations)
just works. Two ways to run the backend:

### A. Pure mock (no keys, instant)

```bash
node askai-harness/mock-server.mjs                 # serves widget + fake /docs/ask on :4599
# open http://localhost:4599/askai-harness/demo.html
```

### B. Real end-to-end — HydraDB retrieval + a real LLM (what the screenshots show)

```bash
# 1. Build the gateway (lives in this repo now, under askai-gateway/).
cd askai-gateway && cargo build && cd ..

# 2. (first run only) Put a few docs pages into a collection so there's something to answer.
export HYDRA_DB_API_KEY=sk_live_...           # a HydraDB key with query + ingest scope
node askai-harness/ingest-sample.mjs           # ingests into database=default, collection=docs

# 3. Run the gateway. Secrets stay server-side; the browser never sees them.
export HYDRA_API_KEY=$HYDRA_DB_API_KEY         # HydraDB key (query scope)
export HYDRA_DATABASE=default HYDRA_COLLECTION=docs
export ASKAI_SITE_URL=https://docs.hydradb.com # absolutizes citation links
export OPENROUTER_API_KEY=sk-or-...            # or LLM_API_KEY for any OpenAI-compatible provider
export LLM_MODEL=openai/gpt-4o-mini
ASKAI_PORT=8080 ./askai-gateway/target/debug/askai-gateway

# 4. Serve the docs + widget and point it at the gateway — this is the ONLY wiring the page needs.
python3 -m http.server 4599            # from the repo root, in another shell
# open http://localhost:4599/askai-harness/demo.html?endpoint=http://localhost:8080
```

`demo.html` sets `window.HydraAskAI = { endpoint }` from the `?endpoint=` query param —
that single value is the whole integration surface.

See [`askai-gateway/README.md`](askai-gateway/README.md) for the full environment-variable
reference (models per mode, rate limiting, origin allowlist, public widget key, Docker).
