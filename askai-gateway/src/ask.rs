//! `POST /docs/ask` (alias `POST /ask`) — the widget's single endpoint.
//!
//! Pipeline: rate-limit → auth → origin gate → validate → retrieve (HydraDB)
//! → return an NDJSON stream: `sources`, `delta`*, `done` (or a terminal
//! `error` event if synthesis fails mid-stream).

use std::io;
use std::net::{IpAddr, SocketAddr};

use axum::body::Body;
use axum::extract::rejection::JsonRejection;
use axum::extract::{ConnectInfo, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::Json;
use futures_util::StreamExt;
use bytes::Bytes;
use serde::Deserialize;
use tokio::sync::mpsc;
use tokio_stream::wrappers::ReceiverStream;

use crate::llm::{chat_body, system_prompt, Message, SseParser};
use crate::mode::AskMode;
use crate::ndjson::Event;
use crate::state::AppState;

const MAX_QUERY_CHARS: usize = 2_000;

#[derive(Debug, Deserialize)]
pub struct AskRequest {
    pub query: String,
    pub mode: Option<String>,
}

fn err_json(status: StatusCode, message: impl Into<String>) -> Response {
    (status, Json(serde_json::json!({ "error": message.into() }))).into_response()
}

fn client_ip(headers: &HeaderMap, addr: SocketAddr) -> IpAddr {
    // Behind a proxy the socket address is the proxy — prefer X-Forwarded-For
    // (first hop, added by the load balancer).
    headers
        .get("x-forwarded-for")
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.split(',').next())
        .and_then(|s| s.trim().parse().ok())
        .unwrap_or(addr.ip())
}

pub async fn ask(
    State(app): State<AppState>,
    ConnectInfo(addr): ConnectInfo<SocketAddr>,
    headers: HeaderMap,
    body: Result<Json<AskRequest>, JsonRejection>,
) -> Response {
    let cfg = app.cfg();

    // 1. Rate limit (per-IP, before any upstream work).
    if !app.0.limiter.allow(client_ip(&headers, addr)) {
        return err_json(StatusCode::TOO_MANY_REQUESTS, "Rate limit exceeded — try again in a minute.");
    }

    // 2. Public-key gate (the browser-side widget key; the real HydraDB key
    //    never leaves the server).
    if let Some(public) = &cfg.public_key {
        let bearer = headers
            .get("authorization")
            .and_then(|v| v.to_str().ok())
            .map(|v| v.trim().to_string())
            .unwrap_or_default();
        let expected = format!("Bearer {public}");
        if !bearer.eq_ignore_ascii_case(&expected) {
            return err_json(StatusCode::UNAUTHORIZED, "Invalid or missing widget API key.");
        }
    }

    // 3. Origin allowlist (defence in depth alongside CORS).
    if let Some(origin) = headers.get("origin").and_then(|v| v.to_str().ok()) {
        if !cfg.origin_allowed(origin) {
            return err_json(StatusCode::FORBIDDEN, "Origin not allowed.");
        }
    }

    // 4. Validate the request.
    let Json(req) = match body {
        Ok(Json(req)) => Json(req),
        Err(rej) => {
            return err_json(StatusCode::BAD_REQUEST, format!("Invalid request body: {rej}"));
        }
    };
    let query = req.query.trim().to_string();
    if query.is_empty() {
        return err_json(StatusCode::BAD_REQUEST, "Missing query.");
    }
    if query.chars().count() > MAX_QUERY_CHARS {
        return err_json(StatusCode::BAD_REQUEST, format!("Query too long (max {MAX_QUERY_CHARS} characters)."));
    }
    let mode = match req.mode.as_deref() {
        None | Some("") | Some("auto") => AskMode::Auto,
        Some(s) => match AskMode::parse(s) {
            Some(m) => m,
            None => {
                return err_json(StatusCode::BAD_REQUEST, format!("Unknown mode {s:?} — expected fast, auto, or thinking."));
            }
        },
    };

    // 5. Retrieve from HydraDB (before the stream starts, so failures map to
    //    real HTTP status codes).
    let retrieval = match crate::hydra::retrieve(&app.0.http, cfg, &query, mode).await {
        Ok(r) => r,
        Err(msg) => {
            tracing::warn!(mode = ?mode, "retrieval failed: {msg}");
            return err_json(StatusCode::BAD_GATEWAY, msg);
        }
    };
    tracing::info!(mode = ?mode, sources = retrieval.sources.len(), "answering");

    // 6. Stream the synthesis as NDJSON.
    let messages = vec![
        Message::system(system_prompt(cfg, &retrieval.context, mode)),
        Message::user(query),
    ];
    let sources_event = Event::Sources { sources: retrieval.sources };
    let model = cfg.model_for(mode).to_string();
    let body_cfg = cfg.clone();
    let http = app.0.http.clone();

    let (tx, rx) = mpsc::channel::<Result<Bytes, io::Error>>(16);
    tokio::spawn(async move {
        // Sources land immediately — the widget renders them while the
        // answer streams in.
        if tx.send(Ok(Bytes::from(sources_event.to_line()))).await.is_err() {
            return; // client went away before we even started
        }

        let url = format!("{}/chat/completions", body_cfg.llm_base_url);
        let send_result = http
            .post(&url)
            .bearer_auth(&body_cfg.llm_api_key)
            .json(&chat_body(&model, &messages, &body_cfg))
            .send()
            .await;

        let resp = match send_result {
            Ok(resp) if resp.status().is_success() => resp,
            Ok(resp) => {
                let status = resp.status();
                let detail: String = resp.text().await.unwrap_or_default().chars().take(200).collect();
                tracing::warn!("LLM provider error ({status}): {detail}");
                let _ = tx.send(Ok(Bytes::from(Event::Error {
                    message: format!("The model provider returned an error ({status})."),
                }.to_line()))).await;
                return;
            }
            Err(e) => {
                tracing::warn!("LLM provider unreachable: {e}");
                let _ = tx.send(Ok(Bytes::from(Event::Error {
                    message: "The model provider is unreachable.".to_string(),
                }.to_line()))).await;
                return;
            }
        };

        let mut parser = SseParser::new();
        let mut stream = resp.bytes_stream();
        let mut client_gone = false;
        while let Some(chunk) = stream.next().await {
            let bytes = match chunk {
                Ok(b) => b,
                Err(e) => {
                    tracing::warn!("LLM stream error: {e}");
                    let _ = tx.send(Ok(Bytes::from(Event::Error {
                        message: "The answer stream was interrupted.".to_string(),
                    }.to_line()))).await;
                    client_gone = true; // stop relaying; fall through to end
                    break;
                }
            };
            for delta in parser.feed(&bytes) {
                if tx.send(Ok(Bytes::from(Event::Delta { text: delta }.to_line()))).await.is_err() {
                    client_gone = true; // widget closed — drop the upstream
                    break;
                }
            }
            if client_gone {
                break;
            }
        }
        if !client_gone {
            let _ = tx.send(Ok(Bytes::from(Event::Done.to_line()))).await;
        }
    });

    let body = Body::from_stream(ReceiverStream::new(rx));
    (
        StatusCode::OK,
        [
            ("content-type", "application/x-ndjson; charset=utf-8"),
            ("cache-control", "no-store"),
            ("x-accel-buffering", "no"),
        ],
        body,
    )
        .into_response()
}
