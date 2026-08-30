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
    endpoint: "https://agents.hydradb.com", // ask API base (POST /docs/ask)
    apiKey:   "pk_docs_readonly_xxx",        // optional — see "API key" below
    theme:    { accent: "#FF571A" },          // optional brand override
    modes:    ["fast", "auto", "thinking"],  // think-modes to show (or one)
    brand:    true,                            // "Powered by HydraDB" footer
  };
</script>
```

Everything is optional except `endpoint`. All values here are **public** — never
put a privileged/secret key in the browser (see below).

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

## Local demo

```bash
node askai-harness/mock-server.mjs   # http://localhost:4599 (mock NDJSON backend)
```
