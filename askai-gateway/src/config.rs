//! Environment-driven configuration (Kapa-style: everything sensitive lives
//! server-side; the browser only ever holds a public, optional key).
//!
//! Every knob is an env var so the gateway deploys as one static binary, one
//! Docker image, or one Fly/Railway/Cloud Run service — no config files.

use std::net::IpAddr;
use std::time::Duration;

use crate::mode::AskMode;

/// The full runtime configuration, resolved once at startup.
#[derive(Debug, Clone)]
pub struct Config {
    // ── server ─────────────────────────────────────────────────────────────
    pub bind: IpAddr,
    pub port: u16,
    pub timeout: Duration,

    // ── HydraDB retrieval (server-side only) ───────────────────────────────
    pub hydra_base_url: String,
    pub hydra_api_key: String,
    pub hydra_database: String,
    pub hydra_collection: String,
    /// Chunks to retrieve for fast/auto mode.
    pub top_k: usize,
    /// Chunks to retrieve for thinking mode (deeper retrieval).
    pub top_k_thinking: usize,

    // ── LLM synthesis (server-side only, any OpenAI-compatible API) ────────
    pub llm_base_url: String,
    pub llm_api_key: String,
    /// Default model (auto mode, and the fallback for fast/thinking).
    pub llm_default_model: String,
    pub llm_fast_model: Option<String>,
    pub llm_thinking_model: Option<String>,
    pub temperature: f32,
    pub max_tokens: u32,
    /// Hard cap on the retrieved-context block fed to the model (chars).
    pub max_context_chars: usize,

    // ── widget access control ──────────────────────────────────────────────
    /// Optional public key the widget sends as `Authorization: Bearer …`.
    pub public_key: Option<String>,
    /// Comma-separated allowlist; `*` (or unset) allows every origin.
    pub allowed_origins: Vec<String>,
    /// Per-IP requests per minute (0 disables rate limiting).
    pub rate_limit_rpm: u32,

    // ── answer shaping ──────────────────────────────────────────────────────
    /// Optional base URL used to absolutize relative source URLs.
    pub site_url: Option<String>,
    /// Optional system-prompt override.
    pub system_prompt: Option<String>,
}

impl Config {
    /// Resolve the model to synthesize with for a given ask-mode.
    pub fn model_for(&self, mode: AskMode) -> &str {
        match mode {
            AskMode::Fast => self
                .llm_fast_model
                .as_deref()
                .unwrap_or(&self.llm_default_model),
            AskMode::Auto => &self.llm_default_model,
            AskMode::Thinking => self
                .llm_thinking_model
                .as_deref()
                .unwrap_or(&self.llm_default_model),
        }
    }

    /// Chunks to retrieve for a mode.
    pub fn top_k_for(&self, mode: AskMode) -> usize {
        match mode {
            AskMode::Fast | AskMode::Auto => self.top_k,
            AskMode::Thinking => self.top_k_thinking,
        }
    }

    /// True when the origin allowlist accepts every origin.
    pub fn allows_all_origins(&self) -> bool {
        self.allowed_origins.iter().any(|o| o == "*")
    }

    pub fn origin_allowed(&self, origin: &str) -> bool {
        self.allows_all_origins()
            || self.allowed_origins.iter().any(|o| o == origin)
    }

    /// Read configuration from the process environment.
    pub fn from_env() -> Result<Self, String> {
        Self::from_lookup(|k| std::env::var(k).ok())
    }

    /// Read configuration from a lookup function (tests inject maps).
    pub fn from_lookup(lookup: impl Fn(&str) -> Option<String>) -> Result<Self, String> {
        let get = |k: &str| lookup(k).filter(|v| !v.trim().is_empty());
        let parse_num = |k: &str, default: &str| -> Result<String, String> {
            // Helper used by the typed parsers below; keep errors consistent.
            let raw = get(k).unwrap_or_else(|| default.to_string());
            if raw.trim().is_empty() {
                Err(format!("{k} must not be blank"))
            } else {
                Ok(raw)
            }
        };

        let bind = get("ASKAI_BIND").unwrap_or_else(|| "0.0.0.0".into());
        let bind: IpAddr = bind
            .trim()
            .parse()
            .map_err(|_| format!("ASKAI_BIND is not a valid IP: {bind}"))?;

        let port: u16 = parse_num("ASKAI_PORT", "8080")?
            .trim()
            .parse()
            .map_err(|_| "ASKAI_PORT must be a port number".to_string())?;

        let timeout_secs: u64 = parse_num("ASKAI_TIMEOUT_SECS", "30")?
            .trim()
            .parse()
            .map_err(|_| "ASKAI_TIMEOUT_SECS must be a number of seconds".to_string())?;

        let hydra_base_url = get("HYDRA_BASE_URL").unwrap_or_else(|| "https://api.hydradb.com".into());
        let hydra_api_key = get("HYDRA_API_KEY")
            .or_else(|| get("ASKAI_HYDRA_API_KEY"))
            .ok_or("HYDRA_API_KEY is required (a server-side HydraDB key with query scope)")?;

        let hydra_database = get("HYDRA_DATABASE").unwrap_or_else(|| "hydra_docs".into());
        let hydra_collection = get("HYDRA_COLLECTION").unwrap_or_else(|| "docs".into());

        let top_k: usize = parse_num("ASKAI_TOP_K", "8")?
            .trim()
            .parse()
            .map_err(|_| "ASKAI_TOP_K must be a number".to_string())?;
        let top_k_thinking: usize = parse_num("ASKAI_TOP_K_THINKING", "12")?
            .trim()
            .parse()
            .map_err(|_| "ASKAI_TOP_K_THINKING must be a number".to_string())?;

        let llm_base_url = get("LLM_BASE_URL").unwrap_or_else(|| "https://openrouter.ai/api/v1".into());
        // OPENROUTER_API_KEY is accepted as an alias so existing OpenRouter
        // deployments work with zero extra setup.
        let llm_api_key = get("LLM_API_KEY")
            .or_else(|| get("OPENROUTER_API_KEY"))
            .ok_or("LLM_API_KEY (or OPENROUTER_API_KEY) is required — the browser never sees it")?;
        let llm_default_model = get("LLM_MODEL").unwrap_or_else(|| "openai/gpt-4o-mini".into());
        let llm_fast_model = get("LLM_MODEL_FAST");
        let llm_thinking_model = get("LLM_MODEL_THINKING");

        let temperature: f32 = parse_num("LLM_TEMPERATURE", "0.4")?
            .trim()
            .parse()
            .map_err(|_| "LLM_TEMPERATURE must be a number".to_string())?;
        let max_tokens: u32 = parse_num("LLM_MAX_TOKENS", "700")?
            .trim()
            .parse()
            .map_err(|_| "LLM_MAX_TOKENS must be a number".to_string())?;
        let max_context_chars: usize = parse_num("ASKAI_MAX_CONTEXT_CHARS", "12000")?
            .trim()
            .parse()
            .map_err(|_| "ASKAI_MAX_CONTEXT_CHARS must be a number".to_string())?;

        let public_key = get("ASKAI_PUBLIC_KEY");
        let allowed_origins = get("ASKAI_ALLOWED_ORIGINS")
            .map(|v| {
                v.split(',')
                    .map(|s| s.trim().to_string())
                    .filter(|s| !s.is_empty())
                    .collect::<Vec<_>>()
            })
            .unwrap_or_else(|| vec!["*".into()]);
        let rate_limit_rpm: u32 = parse_num("ASKAI_RATE_LIMIT_RPM", "30")?
            .trim()
            .parse()
            .map_err(|_| "ASKAI_RATE_LIMIT_RPM must be a number (0 disables limiting)".to_string())?;

        let site_url = get("ASKAI_SITE_URL").map(|s| s.trim().trim_end_matches('/').to_string());
        let system_prompt = get("ASKAI_SYSTEM_PROMPT");

        Ok(Self {
            bind,
            port,
            timeout: Duration::from_secs(timeout_secs),
            hydra_base_url: hydra_base_url.trim().trim_end_matches('/').to_string(),
            hydra_api_key: hydra_api_key.trim().to_string(),
            hydra_database,
            hydra_collection,
            top_k,
            top_k_thinking,
            llm_base_url: llm_base_url.trim().trim_end_matches('/').to_string(),
            llm_api_key: llm_api_key.trim().to_string(),
            llm_default_model,
            llm_fast_model,
            llm_thinking_model,
            temperature,
            max_tokens,
            max_context_chars,
            public_key,
            allowed_origins,
            rate_limit_rpm,
            site_url,
            system_prompt,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn from_pairs(pairs: &[(&str, &str)]) -> Result<Config, String> {
        Config::from_lookup(|k| {
            pairs
                .iter()
                .find(|(key, _)| *key == k)
                .map(|(_, v)| v.to_string())
        })
    }

    #[test]
    fn defaults_fill_completely() {
        let cfg = from_pairs(&[
            ("HYDRA_API_KEY", "hk"),
            ("LLM_API_KEY", "lk"),
        ])
        .unwrap();
        assert_eq!(cfg.hydra_base_url, "https://api.hydradb.com");
        assert_eq!(cfg.hydra_database, "hydra_docs");
        assert_eq!(cfg.hydra_collection, "docs");
        assert_eq!(cfg.llm_base_url, "https://openrouter.ai/api/v1");
        assert_eq!(cfg.llm_default_model, "openai/gpt-4o-mini");
        assert_eq!(cfg.top_k, 8);
        assert_eq!(cfg.top_k_thinking, 12);
        assert_eq!(cfg.rate_limit_rpm, 30);
        assert!(cfg.allows_all_origins());
        assert!(cfg.public_key.is_none());
    }

    #[test]
    fn openrouter_alias_and_trailing_slashes() {
        let cfg = from_pairs(&[
            ("HYDRA_API_KEY", "hk"),
            ("OPENROUTER_API_KEY", "ok"),
            ("HYDRA_BASE_URL", "https://hydra.internal/"),
            ("LLM_BASE_URL", "https://openrouter.ai/api/v1/"),
        ])
        .unwrap();
        assert_eq!(cfg.llm_api_key, "ok");
        assert_eq!(cfg.hydra_base_url, "https://hydra.internal");
        assert_eq!(cfg.llm_base_url, "https://openrouter.ai/api/v1");
    }

    #[test]
    fn missing_keys_are_clear_errors() {
        assert_eq!(
            from_pairs(&[("LLM_API_KEY", "lk")]).unwrap_err(),
            "HYDRA_API_KEY is required (a server-side HydraDB key with query scope)"
        );
        assert!(from_pairs(&[("HYDRA_API_KEY", "hk")])
            .unwrap_err()
            .starts_with("LLM_API_KEY"));
    }

    #[test]
    fn model_resolution_per_mode() {
        let cfg = from_pairs(&[
            ("HYDRA_API_KEY", "hk"),
            ("LLM_API_KEY", "lk"),
            ("LLM_MODEL", "m/default"),
            ("LLM_MODEL_FAST", "m/fast"),
            ("LLM_MODEL_THINKING", "m/think"),
        ])
        .unwrap();
        assert_eq!(cfg.model_for(AskMode::Fast), "m/fast");
        assert_eq!(cfg.model_for(AskMode::Auto), "m/default");
        assert_eq!(cfg.model_for(AskMode::Thinking), "m/think");
        assert_eq!(cfg.top_k_for(AskMode::Fast), 8);
        assert_eq!(cfg.top_k_for(AskMode::Thinking), 12);
    }

    #[test]
    fn origin_allowlist() {
        let cfg = from_pairs(&[
            ("HYDRA_API_KEY", "hk"),
            ("LLM_API_KEY", "lk"),
            ("ASKAI_ALLOWED_ORIGINS", "https://docs.hydradb.com, https://hydradb.com"),
        ])
        .unwrap();
        assert!(!cfg.allows_all_origins());
        assert!(cfg.origin_allowed("https://docs.hydradb.com"));
        assert!(!cfg.origin_allowed("https://evil.example"));
    }
}
