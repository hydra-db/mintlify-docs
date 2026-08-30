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

## Configure — one line

The **only** thing the widget needs is the gateway URL. Two equivalent ways:

**A. Hardcode it in the component (simplest for Mintlify).** Open `askai.js` and
change the single `DEFAULT_ENDPOINT` constant at the top:

```js
var DEFAULT_ENDPOINT = "https://ask.yourdomain.com"; // or http://localhost:8080 for local dev
```

That's it — no config file, no build step, no key. `mintlify dev` (and the hosted
platform) auto-load root `askai.js`, so the widget just appears.

**B. Or set a runtime global** (handy if you can't edit the file), before `askai.js`:

```html
<script>
  window.HydraAskAI = {
    endpoint: "https://ask.yourdomain.com",  // ask API base (POST /docs/ask) — the only required field
    logo:     "",                            // optional custom logo URL (defaults to HydraDB mark)
    theme:    { accent: "#FF571A" },          // optional brand override
    modes:    ["fast", "auto", "thinking"],  // think-modes to show (or one)
  };
</script>
```

No API key goes here — the gateway holds all secrets (see below).

Attribution (**"Powered by HydraDB"** hyperlinked to `hydradb.com`) is always
displayed in the footer (Kapa-style), alongside the original HydraDB logo mark in
the launcher button and panel header.

## API key: you don't need one in the browser

**The browser holds no key.** The `askai-gateway` holds the real HydraDB key and
the LLM provider key **server-side** (as env vars on the binary), so the page only
ever sends a question to `endpoint`. This is the safest setup and the default.

The one optional case: if you turn on the gateway's `ASKAI_PUBLIC_KEY` (a *public*,
rate-limited, origin-allowlisted widget token — think Kapa's `data-website-id`, not
a secret), the widget forwards it. Resolution order: `config.apiKey` →
`window.HYDRA_ASKAI_KEY` → none.

> A real secret (a HydraDB write/query key, an LLM provider key) must **never**
> ship to the browser — and with this design it never does. Those live only on the
> gateway and in the docs-sync CI secret (see `_ci-proposed/README.md`).

## Keyboard shortcut (⌘I / Ctrl+I)

Press **⌘I** (macOS) or **Ctrl+I** (Windows/Linux) anywhere to open or close Ask
AI. The widget claims this shortcut in the capture phase, so on Mintlify it takes
over from the built-in "Ask Assistant" — and it hides that navbar button so there
is a single Ask AI entry point. The shortcut is surfaced on the launcher (a `⌘I`
badge + tooltip) and in the greeting message.

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

# 4. Run the REAL Mintlify docs site — it auto-loads askai.js, no extra server needed.
npm install
npm run dev                             # → mintlify dev on http://localhost:3000
# The Ask AI launcher appears on every page. To point it at your local gateway,
# set DEFAULT_ENDPOINT = "http://localhost:8080" in askai.js (mintlify dev hot-reloads it).
```

`mintlify dev` serves root `askai.js` exactly like the hosted platform does, so the
widget is live on the real docs — no `python -m http.server` and no `demo.html`
needed. (`askai-harness/demo.html` remains only as a standalone, framework-free
sandbox.)

See [`askai-gateway/README.md`](askai-gateway/README.md) for the full environment-variable
reference (models per mode, rate limiting, origin allowlist, public widget key, Docker).
