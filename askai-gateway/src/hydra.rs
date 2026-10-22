//! HydraDB retrieval: `POST {base}/query` → normalized, citation-ready sources.
//!
//! The response envelope has been seen in several shapes on the live API
//! (`{success, data:{chunks…}}`, `data.md`, bare arrays under `data`/`sources`/
//! `results`, camelCase and snake_case chunk fields) — normalization here is
//! deliberately tolerant, mirroring the web app's `lib/qa.ts` logic.

use std::collections::HashMap;

use reqwest::Client;
use serde_json::{json, Value};

use crate::config::Config;
use crate::mode::AskMode;
use crate::ndjson::Source;

/// A normalized retrieval hit.
#[derive(Debug, Clone)]
pub struct Chunk {
    pub content: String,
    pub title: Option<String>,
    pub url: Option<String>,
    pub source_id: Option<String>,
    pub score: Option<f64>,
}

/// Retrieval output: deduped, ranked sources plus the numbered context blocks
/// fed to the LLM (indices line up with the `sources` event).
#[derive(Debug, Default)]
pub struct Retrieval {
    pub sources: Vec<Source>,
    pub context: String,
}

/// Query HydraDB for a user question in the given mode.
pub async fn retrieve(
    client: &Client,
    cfg: &Config,
    query: &str,
    mode: AskMode,
) -> Result<Retrieval, String> {
    // v2 /query wants snake_case fields (see api-reference/v2/openapi.json).
    let payload = json!({
        "query": query,
        "database": cfg.hydra_database,
        "collection": cfg.hydra_collection,
        "type": "knowledge",
        "max_results": cfg.top_k_for(mode),
        "mode": mode.hydra_mode(),
        "graph_context": mode.wants_graph_context(),
    });

    let url = format!("{}/query", cfg.hydra_base_url);
    let resp = client
        .post(&url)
        .bearer_auth(&cfg.hydra_api_key)
        .json(&payload)
        .timeout(cfg.timeout)
        .send()
        .await
        .map_err(|e| format!("HydraDB unreachable: {e}"))?;

    if !resp.status().is_success() {
        let status = resp.status();
        let detail = resp.text().await.unwrap_or_default();
        let detail: String = detail.chars().take(200).collect();
        return Err(format!("HydraDB /query failed ({status}): {detail}"));
    }

    let body: Value = resp
        .json()
        .await
        .map_err(|e| format!("HydraDB /query returned invalid JSON: {e}"))?;

    let chunks = normalize_chunks(&body);
    // In v2, chunk objects carry content + title but no URL — the URL lives in
    // the sibling `data.sources` array. Build an id→(title,url) map to enrich.
    let source_meta = parse_source_meta(&body);
    Ok(build_retrieval(chunks, &source_meta, cfg))
}

/// Map source id → (title, url) from the `data.sources` array (v2 `SourceInfo`).
///
/// The stored `url` is often an internal storage location (e.g. `s3://…`) rather
/// than the page's web URL. When it isn't an http(s) link we fall back to the
/// `metadata.page` route (the convention the docs sync writes), yielding a
/// relative `/route` that `site_url` then absolutizes into a real doc link.
fn parse_source_meta(body: &Value) -> HashMap<String, (Option<String>, Option<String>)> {
    let mut map = HashMap::new();
    let arrays = [
        body.get("data").and_then(|d| d.get("sources")).and_then(Value::as_array),
        body.get("sources").and_then(Value::as_array),
    ];
    for arr in arrays.into_iter().flatten() {
        for s in arr {
            let id = as_str(field(s, &["source_id", "sourceId", "id"]));
            let title = as_str(field(s, &["source_title", "sourceTitle", "title"]));
            let raw_url = as_str(field(s, &["source_url", "sourceUrl", "url"]));
            let page = s
                .get("metadata")
                .and_then(|m| as_str(field(m, &["page", "route", "path"])));
            let url = match raw_url {
                Some(u) if u.starts_with("http") || u.starts_with('/') => Some(u),
                _ => page.map(|p| format!("/{}", p.trim_start_matches('/'))),
            };
            if let Some(id) = id {
                map.entry(id).or_insert((title, url));
            }
        }
    }
    map
}

// ── normalization ───────────────────────────────────────────────────────────

fn field<'a>(obj: &'a Value, names: &[&str]) -> Option<&'a Value> {
    names.iter().find_map(|n| obj.get(*n).filter(|v| !v.is_null()))
}

fn as_str(v: Option<&Value>) -> Option<String> {
    v.and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
}

fn as_f64(v: Option<&Value>) -> Option<f64> {
    v.and_then(Value::as_f64)
}

fn normalize_chunk(raw: &Value) -> Option<Chunk> {
    let c = raw.as_object()?;
    // Tolerate `{ chunk: {...} }` / `{ chunks: [{...}] }` wrappers.
    if let Some(inner) = c.get("chunk").filter(|v| v.is_object()) {
        return normalize_chunk(inner);
    }
    if let Some(first) = c
        .get("chunks")
        .and_then(Value::as_array)
        .and_then(|a| a.first())
    {
        return normalize_chunk(first);
    }

    let content = as_str(field(raw, &["chunk_content", "chunkContent", "content", "text"]));
    let title = as_str(field(raw, &["source_title", "sourceTitle"]));
    let url = as_str(field(raw, &["source_url", "sourceUrl"]));
    let source_id = as_str(field(raw, &["source_id", "sourceId", "id"]));
    let chunk_uuid = as_str(field(raw, &["chunk_uuid", "chunkUuid", "chunk_id", "chunkId"]));
    // Chunk ids derive from the source id (`{source}_chunk_{n}`) — recover it.
    let derived_source_id = source_id.or_else(|| {
        chunk_uuid.and_then(|u| u.split("_chunk_").next().map(str::to_string))
    });
    if content.is_none() && title.is_none() && url.is_none() && derived_source_id.is_none() {
        return None;
    }

    let score = as_f64(field(raw, &["relevancy_score", "relevancyScore", "score"]));
    Some(Chunk {
        content: content.unwrap_or_default(),
        title,
        url,
        source_id: derived_source_id,
        score,
    })
}

/// Unwrap the `/query` envelope and produce a flat chunk list.
fn normalize_chunks(body: &Value) -> Vec<Chunk> {
    let data = body.get("data").filter(|d| d.is_object());
    let md = data.and_then(|d| d.get("md")).filter(|m| m.is_object());

    let candidates: Vec<&Vec<Value>> = [
        data.and_then(|d| d.get("chunks")).and_then(Value::as_array),
        data.and_then(|d| d.get("sources")).and_then(Value::as_array),
        md.and_then(|m| m.get("chunks")).and_then(Value::as_array),
        md.and_then(|m| m.get("sources")).and_then(Value::as_array),
        body.get("chunks").and_then(Value::as_array),
        body.get("sources").and_then(Value::as_array),
        body.get("results").and_then(Value::as_array),
        body.get("data").and_then(Value::as_array),
    ]
    .into_iter()
    .flatten()
    .collect();

    candidates
        .first()
        .map(|list| list.iter().filter_map(normalize_chunk).collect())
        .unwrap_or_default()
}

/// Group chunks into per-source groups ranked by best score, number them,
/// build the `[n] title\ncontent` context blocks within the char budget.
fn build_retrieval(
    mut chunks: Vec<Chunk>,
    source_meta: &HashMap<String, (Option<String>, Option<String>)>,
    cfg: &Config,
) -> Retrieval {
    chunks.sort_by(|a, b| {
        b.score
            .unwrap_or(f64::MIN)
            .partial_cmp(&a.score.unwrap_or(f64::MIN))
            .unwrap_or(std::cmp::Ordering::Equal)
    });

    // Group by source (id → title, url, chunks, best score).
    let mut order: Vec<String> = Vec::new();
    let mut groups: HashMap<String, (Option<String>, Option<String>, Vec<String>, Option<f64>)> =
        HashMap::new();
    for chunk in chunks {
        let key = chunk
            .source_id
            .clone()
            .or_else(|| chunk.title.clone())
            .unwrap_or_else(|| format!("chunk-{}", order.len()));
        let entry = groups.entry(key.clone()).or_insert_with(|| {
            order.push(key.clone());
            (chunk.title.clone(), chunk.url.clone(), Vec::new(), chunk.score)
        });
        if entry.1.is_none() {
            entry.1 = chunk.url.clone();
        }
        if chunk.content.trim().is_empty() {
            // keep title/url provenance even for empty chunks
            continue;
        }
        entry.2.push(chunk.content.trim().to_string());
        if chunk.score.unwrap_or(f64::MIN) > entry.3.unwrap_or(f64::MIN) {
            entry.3 = chunk.score;
        }
    }

    // Rank groups by best score (groups were created in score order already,
    // so `order` is the ranking).
    let mut sources = Vec::new();
    let mut blocks: Vec<String> = Vec::new();
    let mut total = 0usize;
    for (i, key) in order.iter().enumerate() {
        let (title, url, contents, _) = &groups[key];
        let meta = source_meta.get(key);
        let index = i + 1;
        let title = title
            .clone()
            .or_else(|| meta.and_then(|m| m.0.clone()))
            .unwrap_or_else(|| "Untitled source".into());
        let mut url = url
            .clone()
            .or_else(|| meta.and_then(|m| m.1.clone()))
            .unwrap_or_default();
        if let Some(base) = &cfg.site_url {
            if url.starts_with('/') && !url.starts_with("//") {
                url = format!("{base}{url}");
            }
        }
        let body = contents.join("\n\n");
        if body.is_empty() {
            continue;
        }
        let block = format!("[{index}] {title}\n{body}");
        // Keep the context within budget, dropping whole trailing sources.
        if total + block.len() > cfg.max_context_chars {
            if sources.is_empty() {
                // Always keep at least one source, truncated.
                let keep = block.char_indices().take(cfg.max_context_chars).last().map(|(i, c)| i + c.len_utf8()).unwrap_or(block.len());
                let truncated = format!("{}…", &block[..keep.saturating_sub(1)]);
                blocks.push(truncated);
                sources.push(Source {
                    index,
                    id: Some(key.clone()),
                    title,
                    url,
                });
            }
            break;
        }
        total += block.len();
        blocks.push(block);
        sources.push(Source {
            index,
            id: Some(key.clone()),
            title,
            url,
        });
    }

    Retrieval {
        sources,
        context: blocks.join("\n\n"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::Config;
    use serde_json::json;

    fn test_cfg() -> Config {
        Config::from_lookup(|k| match k {
            "HYDRA_API_KEY" => Some("hk".into()),
            "LLM_API_KEY" => Some("lk".into()),
            "ASKAI_SITE_URL" => Some("https://docs.hydradb.com".into()),
            _ => None,
        })
        .unwrap()
    }

    #[test]
    fn normalizes_live_envelope_with_snake_case() {
        let body = json!({
            "success": true,
            "data": {
                "chunks": [
                    {
                        "chunk_uuid": "quickstart_chunk_0",
                        "chunk_content": "Ingest via POST /ingest.",
                        "source_title": "Quickstart",
                        "source_url": "/quickstart",
                        "relevancy_score": 0.9
                    },
                    {
                        "chunk_uuid": "quickstart_chunk_1",
                        "chunk_content": "More ingest detail.",
                        "source_title": "Quickstart",
                        "source_url": "/quickstart",
                        "relevancy_score": 0.7
                    }
                ]
            }
        });
        let got = normalize_chunks(&body);
        assert_eq!(got.len(), 2);
        assert_eq!(got[0].source_id.as_deref(), Some("quickstart"));
    }

    #[test]
    fn normalizes_md_wrapper_and_camel_case() {
        let body = json!({
            "success": true,
            "data": { "md": { "chunks": [
                { "chunkContent": "hello", "sourceTitle": "T", "sourceUrl": "/t", "relevancyScore": 0.5 }
            ]}}
        });
        let got = normalize_chunks(&body);
        assert_eq!(got.len(), 1);
        assert_eq!(got[0].content, "hello");
    }

    #[test]
    fn normalizes_bare_array_envelope() {
        let body = json!({ "results": [ { "content": "x", "source_title": "Y", "source_url": "/y" } ] });
        assert_eq!(normalize_chunks(&body).len(), 1);
    }

    #[test]
    fn groups_dedupes_ranks_and_absolutizes() {
        let body = json!({
            "data": { "chunks": [
                { "chunk_content": "b-body", "source_id": "b", "source_title": "B", "source_url": "/b", "relevancy_score": 0.5 },
                { "chunk_content": "a-body-1", "source_id": "a", "source_title": "A", "source_url": "/a", "relevancy_score": 0.95 },
                { "chunk_content": "a-body-2", "source_id": "a", "source_title": "A", "source_url": "/a", "relevancy_score": 0.8 }
            ]}
        });
        let cfg = test_cfg();
        let r = build_retrieval(normalize_chunks(&body), &parse_source_meta(&body), &cfg);
        assert_eq!(r.sources.len(), 2);
        assert_eq!(r.sources[0].index, 1);
        assert_eq!(r.sources[0].id.as_deref(), Some("a"));
        assert_eq!(r.sources[0].url, "https://docs.hydradb.com/a");
        assert_eq!(r.sources[1].id.as_deref(), Some("b"));
        assert!(r.context.starts_with("[1] A\na-body-1"));
        assert!(r.context.contains("a-body-2"));
        assert!(r.context.contains("[2] B"));
    }

    #[test]
    fn context_budget_drops_whole_trailing_sources() {
        let mut chunks = Vec::new();
        for i in 0..5 {
            chunks.push(Chunk {
                content: "x".repeat(5_000),
                title: Some(format!("S{i}")),
                url: Some(format!("/s{i}")),
                source_id: Some(format!("s{i}")),
                score: Some(1.0 - i as f64 * 0.1),
            });
        }
        let cfg = test_cfg(); // 12_000 budget → 2 sources fit (10k+10k) > budget? 2*5000=10000+titles ok
        let r = build_retrieval(chunks, &HashMap::new(), &cfg);
        assert_eq!(r.sources.len(), 2);
        assert!(r.context.len() <= cfg.max_context_chars);
    }

    #[test]
    fn enriches_chunk_url_from_sources_array() {
        // v2 shape: chunks have content+title (no url); url lives in data.sources.
        let body = json!({
            "success": true,
            "data": {
                "chunks": [
                    { "chunk_uuid": "quickstart_chunk_0", "chunk_content": "Ingest via POST /ingest.",
                      "source_title": "Quickstart", "relevancy_score": 0.9 }
                ],
                "sources": [
                    { "id": "quickstart", "title": "Quickstart", "url": "/get-started/quickstart" }
                ]
            }
        });
        let cfg = test_cfg();
        let r = build_retrieval(normalize_chunks(&body), &parse_source_meta(&body), &cfg);
        assert_eq!(r.sources.len(), 1);
        assert_eq!(r.sources[0].url, "https://docs.hydradb.com/get-started/quickstart");
    }
}
