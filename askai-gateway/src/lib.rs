//! askai-gateway — self-hosted Ask-AI answer gateway for HydraDB.
//!
//! One static binary: `POST /docs/ask` → retrieval from HydraDB + streamed,
//! cited answers from any OpenAI-compatible LLM. Speaks the exact NDJSON
//! contract of the `askai.js` docs widget, so the widget needs zero changes
//! beyond `window.HydraAskAI.endpoint`.

pub mod ask;
pub mod config;
pub mod hydra;
pub mod llm;
pub mod mode;
pub mod ndjson;
pub mod ratelimit;
pub mod state;

use axum::http::HeaderValue;
use axum::routing::{get, post};
use axum::Json;
use axum::Router;
use tower_http::cors::{AllowOrigin, CorsLayer};

use crate::state::AppState;

async fn healthz() -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "ok": true,
        "service": "askai-gateway",
        "version": env!("CARGO_PKG_VERSION"),
    }))
}

async fn index() -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "service": "askai-gateway",
        "version": env!("CARGO_PKG_VERSION"),
        "description": "Self-hosted Ask-AI answer gateway for HydraDB.",
        "endpoints": {
            "ask": "POST /docs/ask  { \"query\": string, \"mode\": \"fast|auto|thinking\" } → NDJSON stream",
            "alias": "POST /ask",
            "health": "GET /healthz",
        },
        "source": "https://github.com/hydra-db/mintlify-docs/tree/main/askai-gateway",
    }))
}

/// CORS: permissive when every origin is allowed; an exact allowlist
/// otherwise (the ask handler additionally enforces the origin gate).
fn cors_layer(state: &AppState) -> CorsLayer {
    if state.cfg().allows_all_origins() {
        CorsLayer::permissive()
    } else {
        let origins: Vec<HeaderValue> = state
            .cfg()
            .allowed_origins
            .iter()
            .filter_map(|o| HeaderValue::from_str(o).ok())
            .collect();
        CorsLayer::new()
            .allow_origin(AllowOrigin::list(origins))
            .allow_methods(tower_http::cors::Any)
            .allow_headers(tower_http::cors::Any)
    }
}

pub fn router(state: AppState) -> Router {
    let cors = cors_layer(&state);
    Router::new()
        .route("/", get(index))
        .route("/healthz", get(healthz))
        .route("/docs/ask", post(ask::ask))
        .route("/ask", post(ask::ask))
        .layer(cors)
        .with_state(state)
}
