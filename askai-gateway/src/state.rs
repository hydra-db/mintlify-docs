//! Shared application state, one `Arc` per server.

use std::sync::Arc;

use reqwest::Client;

use crate::config::Config;
use crate::ratelimit::RateLimiter;

pub struct AppStateInner {
    pub cfg: Config,
    pub http: Client,
    pub limiter: RateLimiter,
}

/// Cheaply cloneable handle passed to every route.
#[derive(Clone)]
pub struct AppState(pub Arc<AppStateInner>);

impl AppState {
    pub fn new(cfg: Config, http: Client) -> Self {
        let limiter = RateLimiter::new(cfg.rate_limit_rpm);
        Self(Arc::new(AppStateInner { cfg, http, limiter }))
    }

    pub fn cfg(&self) -> &Config {
        &self.0.cfg
    }
}
