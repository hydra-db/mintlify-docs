//! askai-gateway entry point: env config → HTTP server with graceful shutdown.

use std::time::Duration;

use askai_gateway::config::Config;
use askai_gateway::state::AppState;
use askai_gateway::router;
use tokio::net::TcpListener;

fn mask(secret: &str) -> String {
    let prefix: String = secret.chars().take(4).collect();
    format!("{prefix}…{}", secret.len())
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info".into()),
        )
        .init();

    let cfg = match Config::from_env() {
        Ok(cfg) => cfg,
        Err(msg) => {
            eprintln!("askai-gateway: configuration error\n  {msg}\n\nSee the README for the full env-var reference.");
            std::process::exit(1);
        }
    };

    tracing::info!(
        version = env!("CARGO_PKG_VERSION"),
        bind = %cfg.bind,
        port = cfg.port,
        hydra = %cfg.hydra_base_url,
        database = %cfg.hydra_database,
        collection = %cfg.hydra_collection,
        hydra_key = %mask(&cfg.hydra_api_key),
        llm = %cfg.llm_base_url,
        model = %cfg.llm_default_model,
        llm_key = %mask(&cfg.llm_api_key),
        rate_limit_rpm = cfg.rate_limit_rpm,
        public_key = cfg.public_key.as_deref().map(mask).unwrap_or_else(|| "(none)".into()),
        origins = cfg.allowed_origins.join(","),
        "askai-gateway starting"
    );

    let http = reqwest::Client::builder()
        .connect_timeout(Duration::from_secs(5))
        // Read-idle timeout (not a total timeout) so long streamed answers
        // never get cut off, but stalled upstream connections do.
        .read_timeout(cfg.timeout)
        .user_agent(format!("askai-gateway/{}", env!("CARGO_PKG_VERSION")))
        .build()
        .expect("failed to build HTTP client");

    let listener = TcpListener::bind((cfg.bind, cfg.port))
        .await
        .expect("failed to bind");
    let app = router(AppState::new(cfg, http));
    tracing::info!("listening on http://{}", listener.local_addr().expect("local addr"));

    axum::serve(
        listener,
        app.into_make_service_with_connect_info::<std::net::SocketAddr>(),
    )
    .with_graceful_shutdown(shutdown_signal())
    .await
    .expect("server error");
}

async fn shutdown_signal() {
    let ctrl_c = async {
        let _ = tokio::signal::ctrl_c().await;
    };
    #[cfg(unix)]
    let terminate = async {
        match tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()) {
            Ok(mut sig) => {
                sig.recv().await;
            }
            Err(_) => std::future::pending::<()>().await,
        }
    };
    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c => {},
        _ = terminate => {},
    }
    tracing::info!("shutdown signal received — draining connections");
}
