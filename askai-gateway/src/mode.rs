//! The widget's think-modes, mirrored from `askai.js`.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum AskMode {
    Fast,
    Auto,
    Thinking,
}

impl AskMode {
    pub fn parse(s: &str) -> Option<Self> {
        match s.trim().to_ascii_lowercase().as_str() {
            "fast" => Some(Self::Fast),
            "auto" => Some(Self::Auto),
            "thinking" | "think" => Some(Self::Thinking),
            _ => None,
        }
    }

    /// The retrieval mode forwarded to HydraDB `/query`.
    pub fn hydra_mode(self) -> &'static str {
        match self {
            Self::Fast => "fast",
            Self::Auto => "auto",
            Self::Thinking => "thinking",
        }
    }

    /// Whether to ask HydraDB for graph-augmented context (deeper, slower).
    pub fn wants_graph_context(self) -> bool {
        matches!(self, Self::Thinking)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_widget_modes() {
        assert_eq!(AskMode::parse("fast"), Some(AskMode::Fast));
        assert_eq!(AskMode::parse("Auto"), Some(AskMode::Auto));
        assert_eq!(AskMode::parse(" thinking "), Some(AskMode::Thinking));
        assert_eq!(AskMode::parse("deep"), None);
    }

    #[test]
    fn hydra_mapping() {
        assert_eq!(AskMode::Thinking.hydra_mode(), "thinking");
        assert!(AskMode::Thinking.wants_graph_context());
        assert!(!AskMode::Fast.wants_graph_context());
    }
}
