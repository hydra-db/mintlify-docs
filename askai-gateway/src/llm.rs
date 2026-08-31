//! Answer synthesis: streamed chat completions against any OpenAI-compatible
//! API (OpenRouter, OpenAI, Groq, Together, Ollama, vLLM, …).
//!
//! The SSE parsing is factored into a pure incremental parser so it can be
//! unit-tested against real provider fixtures.

use serde_json::{json, Value};

use crate::config::Config;
use crate::mode::AskMode;

/// One chat message.
#[derive(Debug, Clone)]
pub struct Message {
    pub role: &'static str,
    pub content: String,
}

impl Message {
    pub fn system(content: impl Into<String>) -> Self {
        Self { role: "system", content: content.into() }
    }
    pub fn user(content: impl Into<String>) -> Self {
        Self { role: "user", content: content.into() }
    }
}

/// The grounded system prompt (docs-assistant persona, mirrored from the
/// web app's Ask flow). Override wholesale via `ASKAI_SYSTEM_PROMPT`.
pub fn system_prompt(cfg: &Config, context: &str, mode: AskMode) -> String {
    if let Some(custom) = &cfg.system_prompt {
        return custom.replace("{context}", context);
    }
    let context_block = if context.trim().is_empty() {
        "(No context was retrieved for this question.)".to_string()
    } else {
        format!("=== RETRIEVED CONTEXT ===\n{context}")
    };
    let depth = match mode {
        AskMode::Fast => "Answer concisely — the user picked the fast mode.",
        AskMode::Auto => "",
        AskMode::Thinking => "Reason carefully step by step before answering, but show only the final answer.",
    };
    format!(
        "You are the AI documentation assistant for this site. Answer using ONLY the retrieved context below.\n\
         Context entries are numbered — cite them inline with [1], [2], … exactly as shown.\n\
         If the context cannot answer the question, say so plainly and suggest what to search for instead.\n\
         Use short paragraphs and light markdown (code fences for code, bold sparingly). {depth}\n\n\
         {context_block}"
    )
}

/// Build the upstream `POST {base}/chat/completions` request body.
pub fn chat_body(model: &str, messages: &[Message], cfg: &Config) -> Value {
    json!({
        "model": model,
        "messages": messages.iter().map(|m| json!({ "role": m.role, "content": m.content })).collect::<Vec<_>>(),
        "stream": true,
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_tokens,
    })
}

// ── incremental SSE parsing ─────────────────────────────────────────────────

/// Accumulates the raw upstream byte stream and yields content deltas.
///
/// Feed it raw bytes (`data:`-prefixed SSE frames split on newlines arrive in
/// arbitrary chunks); it returns the text deltas as they parse out.
#[derive(Debug, Default)]
pub struct SseParser {
    buf: String,
}

impl SseParser {
    pub fn new() -> Self {
        Self::default()
    }

    /// Feed raw bytes, get the decoded content deltas they completed.
    pub fn feed(&mut self, bytes: &[u8]) -> Vec<String> {
        self.buf.push_str(&String::from_utf8_lossy(bytes));
        let mut deltas = Vec::new();
        // Only consume up to the last complete newline (keep the remainder).
        while let Some(pos) = self.buf.find('\n') {
            let line: String = self.buf.drain(..=pos).collect();
            if let Some(text) = Self::parse_line(line.trim_end_matches('\r').trim()) {
                deltas.push(text);
            }
        }
        deltas
    }

    /// Parse one SSE frame line into a content delta.
    fn parse_line(line: &str) -> Option<String> {
        let payload = line.strip_prefix("data:")?.trim();
        if payload == "[DONE]" || payload.is_empty() {
            return None;
        }
        let frame: Value = serde_json::from_str(payload).ok()?;
        let choices = frame.get("choices")?.as_array()?;
        let first = choices.first()?;
        // Streaming shape: choices[0].delta.content — non-streaming replies
        // use choices[0].message.content (some proxies do mid-stream).
        let content = first
            .get("delta")
            .and_then(|d| d.get("content"))
            .or_else(|| first.get("message").and_then(|m| m.get("content")));
        content.and_then(Value::as_str).filter(|s| !s.is_empty()).map(str::to_string)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::Config;

    fn test_cfg() -> Config {
        Config::from_lookup(|k| match k {
            "HYDRA_API_KEY" => Some("hk".into()),
            "LLM_API_KEY" => Some("lk".into()),
            _ => None,
        })
        .unwrap()
    }

    #[test]
    fn parses_openrouter_stream_frames() {
        let mut p = SseParser::new();
        // Byte chunks deliberately split mid-frame.
        let first = p.feed(b"data: {\"choices\":[{\"delta\":{\"content\":\"He");
        assert!(first.is_empty(), "incomplete frame must not emit");
        let second = p.feed(b"llo\"}}]}\n\n");
        assert_eq!(second, vec!["Hello".to_string()]);
        let third = p.feed(b"data: [DONE]\n");
        assert!(third.is_empty());
    }

    #[test]
    fn skips_empty_content_and_non_json() {
        let mut p = SseParser::new();
        let out = p.feed(
            b"data: {\"choices\":[{\"delta\":{\"content\":\"a\"}}]}\ndata: {\"choices\":[{\"delta\":{}}]}\ndata: {\"choices\":[{\"delta\":{\"content\":\"b\"}}]}\n",
        );
        assert_eq!(out, vec!["a".to_string(), "b".to_string()]);
    }

    #[test]
    fn handles_crlf_and_message_shape() {
        let mut p = SseParser::new();
        let out = p.feed(b"data: {\"choices\":[{\"message\":{\"content\":\"hi\"}}]}\r\n");
        assert_eq!(out, vec!["hi".to_string()]);
    }

    #[test]
    fn system_prompt_grounds_and_numbers() {
        let cfg = test_cfg();
        let prompt = system_prompt(&cfg, "[1] Quickstart\nIngest things.", AskMode::Auto);
        assert!(prompt.contains("ONLY the retrieved context"));
        assert!(prompt.contains("=== RETRIEVED CONTEXT ==="));
        assert!(prompt.contains("[1] Quickstart"));
    }

    #[test]
    fn chat_body_shape() {
        let cfg = test_cfg();
        let msgs = vec![Message::system("s"), Message::user("q")];
        let body = chat_body("m/x", &msgs, &cfg);
        assert_eq!(body["model"], "m/x");
        assert_eq!(body["stream"], true);
        assert_eq!(body["messages"][1]["content"], "q");
    }
}
