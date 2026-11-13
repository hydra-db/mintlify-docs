//! End-to-end integration tests: the real router wired to mock HydraDB and
//! LLM upstreams, exercised over real HTTP sockets.

use std::sync::Arc;

use askai_gateway::config::Config;
use askai_gateway::state::AppState;
use askai_gateway::router;
use axum::extract::State;
use axum::http::StatusCode;
use axum::response::IntoResponse;
use axum::Json;
use serde_json::{json, Value};
use tokio::net::TcpListener;

// ── mock upstreams ─────────────────────────────────────────────────────────

#[derive(Clone, Default)]
struct MockHydra {
    fail: bool,
}

async fn hydra_query(State(mock): State<MockHydra>, body: Json<Value>) -> impl IntoResponse {
    if mock.fail {
        return (StatusCode::BAD_GATEWAY, Json(json!({ "error": "boom" })));
    }
    // Sanity: the gateway must scope the query server-side.
    assert_eq!(body["database"], "test-db");
    assert_eq!(body["collection"], "docs");
    assert_eq!(body["type"], "knowledge");
    assert_eq!(body["query"], "How do I ingest?");
    match body["mode"].as_str() {
        Some("fast") | Some("auto") | Some("thinking") => {}
        other => panic!("unexpected retrieval mode: {other:?}"),
    }
    // The live envelope shape: { success, data: { chunks: [...] } }.
    let resp = json!({
        "success": true,
        "data": { "chunks": [
            {
                "chunk_uuid": "quickstart_chunk_0",
                "chunk_content": "Ingest documents via POST /ingest with your API key.",
                "source_id": "quickstart",
                "source_title": "Quickstart",
                "source_url": "/quickstart",
                "relevancy_score": 0.95
            },
            {
                "chunk_uuid": "ingest_chunk_1",
                "chunk_content": "The /ingest endpoint is asynchronous and returns a source id.",
                "source_id": "api-ingest",
                "source_title": "API — POST /ingest",
                "source_url": "/api-reference/ingest",
                "relevancy_score": 0.8
            }
        ]}
    });
    (StatusCode::OK, Json(resp))
}

async fn llm_chat(body: Json<Value>) -> impl IntoResponse {
    assert_eq!(body["stream"], true, "the gateway must request streaming");
    let content = body["messages"][1]["content"].as_str().unwrap().to_string();
    let system = body["messages"][0]["content"].as_str().unwrap().to_string();
    assert!(system.contains("[1] Quickstart"), "context must be grounded");
    assert_eq!(content, "How do I ingest?");
    let sse = concat!(
        "data: {\"choices\":[{\"delta\":{\"content\":\"You ingest\"}}]}\n",
        "data: {\"choices\":[{\"delta\":{\"content\":\" via POST /ingest\"}}]}\n",
        "data: {\"choices\":[{\"delta\":{}}]}\n",
        "data: {\"choices\":[{\"delta\":{\"content\":\" [1].\"}}]}\n",
        "data: [DONE]\n"
    );
    (
        StatusCode::OK,
        [("content-type", "text/event-stream")],
        sse.to_string(),
    )
}

async fn spawn_uds_router() -> (String /*base url*/, String /*hydra url*/, String /*llm url*/) {
    let hydra = axum::Router::new()
        .route("/query", axum::routing::post(hydra_query))
        .with_state(MockHydra::default());
    let llm = axum::Router::new().route("/chat/completions", axum::routing::post(llm_chat));

    let hydra_base = spawn(hydra).await;
    let llm_base = spawn(llm).await;

    let cfg = test_config(|pairs| {
        pairs.push(("HYDRA_BASE_URL", hydra_base.clone()));
        pairs.push(("LLM_BASE_URL", llm_base.clone()));
    });
    let http = reqwest::Client::builder().build().unwrap();
    let state = AppState::new(cfg, http);
    let gw_base = spawn(router(state)).await;
    (gw_base, hydra_base, llm_base)
}

async fn spawn(app: axum::Router) -> String {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        axum::serve(
            listener,
            app.into_make_service_with_connect_info::<std::net::SocketAddr>(),
        )
        .await
        .unwrap();
    });
    format!("http://{addr}")
}

fn test_config(mutate: impl FnOnce(&mut Vec<(&'static str, String)>)) -> Config {
    let mut pairs: Vec<(&str, String)> = vec![
        ("HYDRA_API_KEY", "hydra-key".into()),
        ("LLM_API_KEY", "llm-key".into()),
        ("HYDRA_DATABASE", "test-db".into()),
        ("HYDRA_COLLECTION", "docs".into()),
    ];
    mutate(&mut pairs);
    Config::from_lookup(|k| {
        pairs
            .iter()
            .find(|(key, _)| *key == k)
            .map(|(_, v)| v.clone())
    })
    .unwrap()
}

/// POST a question and return (status, headers, full NDJSON text).
async fn ask(
    gw: &str,
    path: &str,
    key: Option<&str>,
    origin: Option<&str>,
    body: Value,
) -> (StatusCode, reqwest::header::HeaderMap, String) {
    let client = reqwest::Client::builder().build().unwrap();
    let mut req = client
        .post(format!("{gw}{path}"))
        .json(&body);
    if let Some(k) = key {
        req = req.bearer_auth(k);
    }
    if let Some(o) = origin {
        req = req.header("origin", o);
    }
    let resp = req.send().await.unwrap();
    let status = resp.status();
    let headers = resp.headers().clone();
    let text = resp.text().await.unwrap();
    (status, headers, text)
}

fn lines(text: &str) -> Vec<Value> {
    text.lines()
        .filter(|l| !l.trim().is_empty())
        .map(|l| serde_json::from_str(l).expect("each line is valid NDJSON"))
        .collect()
}

fn ask_body(query: &str, mode: Option<&str>) -> Value {
    json!({ "query": query, "mode": mode })
}

// ── tests ──────────────────────────────────────────────────────────────────

#[tokio::test]
async fn end_to_end_streams_sources_deltas_done() {
    let (gw, _, _) = spawn_uds_router().await;
    let (status, headers, text) = ask(&gw, "/docs/ask", None, None, ask_body("How do I ingest?", Some("auto"))).await;
    assert_eq!(status, StatusCode::OK);

    assert_eq!(
        headers.get("content-type").unwrap().to_str().unwrap(),
        "application/x-ndjson; charset=utf-8"
    );

    let events = lines(&text);
    assert_eq!(events[0]["type"], "sources");
    let sources = events[0]["sources"].as_array().unwrap();
    assert_eq!(sources.len(), 2);
    assert_eq!(sources[0]["index"], 1);
    assert_eq!(sources[0]["id"], "quickstart");
    assert_eq!(sources[0]["url"], "/quickstart");
    assert_eq!(sources[1]["id"], "api-ingest");

    let answer: String = events[1..]
        .iter()
        .filter(|e| e["type"] == "delta")
        .filter_map(|e| e["text"].as_str())
        .collect();
    assert_eq!(answer, "You ingest via POST /ingest [1].");
    assert_eq!(events.last().unwrap()["type"], "done");
}

#[tokio::test]
async fn alias_ask_path_matches_docs_ask() {
    let (gw, _, _) = spawn_uds_router().await;
    let (status, _, text) = ask(&gw, "/ask", None, None, ask_body("How do I ingest?", None)).await;
    assert_eq!(status, StatusCode::OK);
    assert!(text.contains("\"type\":\"done\""));
}

#[tokio::test]
async fn public_key_gate_rejects_missing_and_wrong_keys() {
    // Reconfigure with a public key requirement.
    let hydra = axum::Router::new()
        .route("/query", axum::routing::post(hydra_query))
        .with_state(MockHydra::default());
    let llm = axum::Router::new().route("/chat/completions", axum::routing::post(llm_chat));
    let hydra_base = spawn(hydra).await;
    let llm_base = spawn(llm).await;
    let cfg = test_config(|p| {
        p.push(("HYDRA_BASE_URL", hydra_base.clone()));
        p.push(("LLM_BASE_URL", llm_base.clone()));
        p.push(("ASKAI_PUBLIC_KEY", "pk_docs_test".into()));
    });
    let http = reqwest::Client::builder().build().unwrap();
    let gw = spawn(router(AppState::new(cfg, http))).await;

    let (status, _, _) = ask(&gw, "/docs/ask", None, None, ask_body("q", None)).await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    let (status, _, _) = ask(&gw, "/docs/ask", Some("pk_wrong"), None, ask_body("q", None)).await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    let (status, _, text) = ask(&gw, "/docs/ask", Some("pk_docs_test"), None, ask_body("How do I ingest?", None)).await;
    assert_eq!(status, StatusCode::OK);
    assert!(text.contains("\"type\":\"done\""));
}

#[tokio::test]
async fn origin_allowlist_blocks_disallowed_origins() {
    let (gw, _, _) = spawn_uds_router().await;
    let (status, _, _) = ask(&gw, "/docs/ask", None, Some("https://docs.hydradb.com"), ask_body("How do I ingest?", None)).await;
    assert_eq!(status, StatusCode::OK, "allow-all default accepts any origin");

    let hydra = axum::Router::new()
        .route("/query", axum::routing::post(hydra_query))
        .with_state(MockHydra::default());
    let llm = axum::Router::new().route("/chat/completions", axum::routing::post(llm_chat));
    let hydra_base = spawn(hydra).await;
    let llm_base = spawn(llm).await;
    let cfg = test_config(|p| {
        p.push(("HYDRA_BASE_URL", hydra_base.clone()));
        p.push(("LLM_BASE_URL", llm_base.clone()));
        p.push(("ASKAI_ALLOWED_ORIGINS", "https://docs.hydradb.com".into()));
    });
    let http = reqwest::Client::builder().build().unwrap();
    let gw = spawn(router(AppState::new(cfg, http))).await;

    let (status, _, _) = ask(&gw, "/docs/ask", None, Some("https://evil.example"), ask_body("How do I ingest?", None)).await;
    assert_eq!(status, StatusCode::FORBIDDEN);
    let (status, _, _) = ask(&gw, "/docs/ask", None, Some("https://docs.hydradb.com"), ask_body("How do I ingest?", None)).await;
    assert_eq!(status, StatusCode::OK);
}

#[tokio::test]
async fn rate_limit_blocks_bursts_per_ip() {
    let hydra = axum::Router::new()
        .route("/query", axum::routing::post(hydra_query))
        .with_state(MockHydra::default());
    let llm = axum::Router::new().route("/chat/completions", axum::routing::post(llm_chat));
    let hydra_base = spawn(hydra).await;
    let llm_base = spawn(llm).await;
    let cfg = test_config(|p| {
        p.push(("HYDRA_BASE_URL", hydra_base.clone()));
        p.push(("LLM_BASE_URL", llm_base.clone()));
        p.push(("ASKAI_RATE_LIMIT_RPM", "2".into()));
    });
    let http = reqwest::Client::builder().build().unwrap();
    let gw = spawn(router(AppState::new(cfg, http))).await;

    let s1 = ask(&gw, "/docs/ask", None, None, ask_body("How do I ingest?", None)).await.0;
    let s2 = ask(&gw, "/docs/ask", None, None, ask_body("How do I ingest?", None)).await.0;
    let s3 = ask(&gw, "/docs/ask", None, None, ask_body("How do I ingest?", None)).await.0;
    assert_eq!(s1, StatusCode::OK);
    assert_eq!(s2, StatusCode::OK);
    assert_eq!(s3, StatusCode::TOO_MANY_REQUESTS, "3rd request in the same minute is blocked");
}

#[tokio::test]
async fn validation_rejects_bad_input() {
    let (gw, _, _) = spawn_uds_router().await;
    let (status, _, _) = ask(&gw, "/docs/ask", None, None, ask_body("", None)).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    let (status, _, _) = ask(&gw, "/docs/ask", None, None, ask_body("q", Some("turbo"))).await;
    assert_eq!(status, StatusCode::BAD_REQUEST, "unknown mode rejected");

    let client = reqwest::Client::builder().build().unwrap();
    let resp = client
        .post(format!("{gw}/docs/ask"))
        .header("content-type", "application/json")
        .body("not json")
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
}

#[tokio::test]
async fn retrieval_failure_maps_to_502() {
    // Point Hydra at a closed port — the gateway must surface a 502, not hang.
    let llm = axum::Router::new().route("/chat/completions", axum::routing::post(llm_chat));
    let llm_base = spawn(llm).await;
    let cfg = test_config(|p| {
        p.push(("HYDRA_BASE_URL", "http://127.0.0.1:9".into()));
        p.push(("LLM_BASE_URL", llm_base.clone()));
        p.push(("ASKAI_TIMEOUT_SECS", "2".into()));
    });
    let http = reqwest::Client::builder()
        .connect_timeout(std::time::Duration::from_millis(500))
        .read_timeout(std::time::Duration::from_secs(2))
        .build()
        .unwrap();
    let gw = spawn(router(AppState::new(cfg, http))).await;
    let (status, _, body) = ask(&gw, "/docs/ask", None, None, ask_body("How do I ingest?", None)).await;
    assert_eq!(status, StatusCode::BAD_GATEWAY);
    assert!(body.contains("error"));
}

#[tokio::test]
async fn healthz_and_index_respond() {
    let (gw, _, _) = spawn_uds_router().await;
    let client = reqwest::Client::builder().build().unwrap();
    let health = client.get(format!("{gw}/healthz")).send().await.unwrap();
    assert_eq!(health.status(), StatusCode::OK);
    let v: Value = health.json().await.unwrap();
    assert_eq!(v["ok"], true);
    let index = client.get(format!("{gw}/")).send().await.unwrap();
    assert_eq!(index.status(), StatusCode::OK);
    let v: Value = index.json().await.unwrap();
    assert_eq!(v["service"], "askai-gateway");
}

// Keep the compiler honest about unused helpers in single-threaded contexts.
#[allow(dead_code)]
fn _assert_arc_send() {
    fn is_send<T: Send>() {}
    is_send::<Arc<AppState>>();
}
