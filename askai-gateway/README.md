# askai-gateway

A tiny, self-hostable **Ask-AI answer gateway** for [HydraDB](https://hydradb.com).

One static Rust binary that turns your HydraDB collection into the backend for
the drop-in docs widget (`askai.js`): retrieval-grounded answers, streamed
live, with numbered citations back to your pages.

```
browser widget ──POST /docs/ask──▶ askai-gateway ──/query──▶ HydraDB (retrieval)
                                        │
                                        └──/chat/completions──▶ any OpenAI-compatible LLM
                                             (OpenRouter, OpenAI, Groq, Ollama, vLLM, …)
```

The design goal is the same composable shape the hosted products (Kapa,
Inkeep) use — a **public identifier in the browser, everything sensitive
server-side** — except you can run this binary anywhere you like, forever.

## Quickstart

```bash
# 1. Sync your docs into HydraDB (see the docs-sync Action this ships with).
# 2. Run the gateway:
export HYDRA_API_KEY=...            # server-side HydraDB key (query scope)
export LLM_API_KEY=...              # OpenRouter / OpenAI-compatible key
cargo run --release                 # or: docker build -t askai-gateway . && docker run -p 8080:8080 --env-file .env askai-gateway

# 3. Point the widget at it (on your docs site, before askai.js loads):
window.HydraAskAI = { endpoint: "https://ask.yourdomain.com" };
```

## Environment variables

Everything is env-only — no config files, no database, no state. Binary +
env = a deployed Ask-AI backend.

| Variable | Default | Purpose |
|---|---|---|
| `HYDRA_API_KEY` | **required** | Server-side HydraDB key. Never in the browser. |
| `LLM_API_KEY` | **required** | LLM provider key (`OPENROUTER_API_KEY` also accepted). Never in the browser. |
| `HYDRA_BASE_URL` | `https://api.hydradb.com` | HydraDB API base (self-hosted friendly). |
| `HYDRA_DATABASE` | `hydra_docs` | Database to answer from. |
| `HYDRA_COLLECTION` | `docs` | Collection to answer from. |
| `LLM_BASE_URL` | `https://openrouter.ai/api/v1` | Any OpenAI-compatible API base. |
| `LLM_MODEL` | `openai/gpt-4o-mini` | Default synthesis model. |
| `LLM_MODEL_FAST` | — | Model override for the widget's *fast* mode. |
| `LLM_MODEL_THINKING` | — | Model override for *thinking* mode. |
| `LLM_TEMPERATURE` | `0.4` | Sampling temperature. |
| `LLM_MAX_TOKENS` | `700` | Answer budget. |
| `ASKAI_PORT` / `ASKAI_BIND` | `8080` / `0.0.0.0` | Listener. |
| `ASKAI_PUBLIC_KEY` | *(unset)* | If set, the widget must send it as `Authorization: Bearer …` — the browser-side public key, rate-limited + allowlisted here. |
| `ASKAI_ALLOWED_ORIGINS` | `*` | Comma-separated origin allowlist (e.g. `https://docs.hydradb.com`). |
| `ASKAI_RATE_LIMIT_RPM` | `30` | Per-IP requests/minute (0 disables). |
| `ASKAI_TOP_K` / `ASKAI_TOP_K_THINKING` | `8` / `12` | Retrieval depth per mode. |
| `ASKAI_MAX_CONTEXT_CHARS` | `12000` | Context budget fed to the model. |
| `ASKAI_SITE_URL` | — | Absolutize relative source URLs (e.g. `https://docs.hydradb.com`). |
| `ASKAI_SYSTEM_PROMPT` | — | Replace the built-in grounded system prompt (`{context}` is substituted). |
| `ASKAI_TIMEOUT_SECS` | `30` | Upstream connect/read budget. |
| `RUST_LOG` | `info` | Log level (`debug` for verbose). |

## The widget

The gateway speaks the exact contract of the drop-in `askai.js` widget
(Shadow-DOM, responsive, fast/auto/thinking modes, themable via CSS vars):

```html
<script>
  window.HydraAskAI = {
    endpoint: "https://ask.yourdomain.com", // gateway base; POST /docs/ask
    apiKey: "pk_docs_public…",             // only if ASKAI_PUBLIC_KEY is set
  };
</script>
<script src="/askai.js" defer></script>
```

The browser only ever holds the public key. Your HydraDB and LLM keys live in
the gateway's environment and are never exposed.

## API

`POST /docs/ask` (alias `POST /ask`)

```jsonc
// request
{ "query": "How do I ingest documents?", "mode": "fast | auto | thinking" }

// response: application/x-ndjson stream
{"type":"sources","sources":[{"index":1,"id":"…","title":"…","url":"…"}]}
{"type":"delta","text":"…"}   // repeated as the answer streams
{"type":"done"}
```

Terminal `{"type":"error","message":"…"}` events replace the tail if the
model provider fails mid-stream. Retrieval failures are proper HTTP errors
(400/401/403/429/502) before the stream opens.

Other endpoints: `GET /healthz` (liveness) and `GET /` (service info).

### Think-modes

| Mode | Retrieval | Model |
|---|---|---|
| `fast` | `top_k`, hybrid fast mode | `LLM_MODEL_FAST` → `LLM_MODEL` |
| `auto` | `top_k`, balanced | `LLM_MODEL` |
| `thinking` | `top_k_thinking` + graph context | `LLM_MODEL_THINKING` → `LLM_MODEL` |

## Deploy

**Docker:**

```bash
docker build -t askai-gateway .
docker run -p 8080:8080 --env-file .env askai-gateway
```

**Fly.io / Railway / Cloud Run:** deploy the same image; set the env vars in
the platform dashboard. Put it behind a proxy that forwards
`X-Forwarded-For` — the rate limiter keys on it when present.

## Security notes

- The widget key (`ASKAI_PUBLIC_KEY`) is *an identifier, not a secret* — like
  Kapa's `data-website-id`. Real protection comes from the per-IP rate limit,
  the origin allowlist, and keeping the HydraDB/LLM keys server-side.
- Prefer a **read-only, scoped HydraDB key**.
- Set `ASKAI_ALLOWED_ORIGINS` in production.

## Development

```bash
cargo test          # 31 tests: unit + end-to-end against mock upstreams
cargo build --release
```

Licensed under Apache-2.0.
