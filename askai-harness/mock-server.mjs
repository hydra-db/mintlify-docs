// Mock backend + demo host for the Ask-AI widget.
//
// Serves a docs-like page and the real root-level askai.js on one origin (so no
// CORS), plus a POST /docs/ask that streams the same newline-delimited JSON
// contract the Go endpoint produces: one `sources` event, a run of `delta`
// events, then `done`. Pure demo — no LLM, no network.
//
//   node askai-harness/mock-server.mjs   # then open http://localhost:4599
import http from "node:http";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..");
const PORT = process.env.PORT || 4599;

const SOURCES = [
  { index: 1, id: "quickstart", title: "Quickstart — Ingesting your first documents", url: "/quickstart" },
  { index: 2, id: "ingestion-api", title: "API Reference — POST /ingest", url: "/api-reference/ingest" },
  { index: 3, id: "collections", title: "Concepts — Databases & Collections", url: "/concepts/collections" },
];

const ANSWER = `You ingest documents into HydraDB by **POSTing them to the \`/ingest\` endpoint** with your API key. Each document is chunked, embedded, and indexed into the collection you specify [1][2].

A minimal request looks like:

\`\`\`bash
curl https://api.hydradb.com/ingest \\
  -H "Authorization: Bearer $HYDRA_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"database": "docs", "collection": "guides", "text": "..."}'
\`\`\`

If you omit \`collection\`, HydraDB writes to the database's default collection [3]. Ingestion is asynchronous — the response returns a source id you can poll for indexing status.`;

function streamAsk(res) {
  res.writeHead(200, {
    "Content-Type": "application/x-ndjson",
    "Cache-Control": "no-cache",
    "Access-Control-Allow-Origin": "*",
  });
  const send = (obj) => res.write(JSON.stringify(obj) + "\n");

  send({ type: "sources", sources: SOURCES });

  // Tokenize the answer into word-ish deltas and emit on a timer, like a stream.
  const tokens = ANSWER.match(/\s*\S+/g) || [];
  let i = 0;
  const tick = () => {
    if (i >= tokens.length) {
      send({ type: "done" });
      return res.end();
    }
    // Emit 1–3 tokens per frame for a natural cadence.
    const n = 1 + (i % 3);
    send({ type: "delta", text: tokens.slice(i, i + n).join("") });
    i += n;
    setTimeout(tick, 45);
  };
  setTimeout(tick, 250);
}

const server = http.createServer((req, res) => {
  if (req.method === "OPTIONS") {
    res.writeHead(204, {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
      "Access-Control-Allow-Headers": "*",
    });
    return res.end();
  }
  if (req.method === "POST" && req.url === "/docs/ask") {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => streamAsk(res));
    return;
  }
  if (req.url === "/askai.js") {
    res.writeHead(200, { "Content-Type": "application/javascript" });
    return res.end(readFileSync(join(ROOT, "askai.js")));
  }
  // Everything else → the demo docs page.
  res.writeHead(200, { "Content-Type": "text/html" });
  res.end(readFileSync(join(__dirname, "demo.html")));
});

server.listen(PORT, () => {
  console.log(`ask-ai mock running at http://localhost:${PORT}`);
});
