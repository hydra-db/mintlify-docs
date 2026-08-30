//! The NDJSON stream contract spoken to the widget (see `askai.js` /
//! `askai-harness/mock-server.mjs`):
//!
//! ```text
//! {"type":"sources","sources":[{"index":1,"id":"…","title":"…","url":"…"}]}
//! {"type":"delta","text":"…"}   // repeated
//! {"type":"done"}
//! ```
//!
//! One `error` event may replace the tail when synthesis fails mid-stream.

use serde::{Deserialize, Serialize};

/// A citation emitted in the `sources` event.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Source {
    pub index: usize,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub id: Option<String>,
    pub title: String,
    pub url: String,
}

#[derive(Debug, Serialize)]
#[serde(tag = "type", rename_all = "lowercase")]
pub enum Event {
    Sources { sources: Vec<Source> },
    Delta { text: String },
    Done,
    Error { message: String },
}

impl Event {
    /// Serialize as one NDJSON line (including the trailing newline).
    pub fn to_line(&self) -> Vec<u8> {
        let mut v = serde_json::to_vec(self).expect("event serialization is infallible");
        v.push(b'\n');
        v
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sources_event_shape() {
        let ev = Event::Sources {
            sources: vec![Source {
                index: 1,
                id: Some("quickstart".into()),
                title: "Quickstart".into(),
                url: "/quickstart".into(),
            }],
        };
        assert_eq!(
            String::from_utf8(ev.to_line()).unwrap(),
            r#"{"type":"sources","sources":[{"index":1,"id":"quickstart","title":"Quickstart","url":"/quickstart"}]}"#
                .to_string()
                + "\n"
        );
    }

    #[test]
    fn delta_and_done_shapes() {
        assert_eq!(
            String::from_utf8(Event::Delta { text: "héllo".into() }.to_line()).unwrap(),
            "{\"type\":\"delta\",\"text\":\"héllo\"}\n"
        );
        assert_eq!(
            String::from_utf8(Event::Done.to_line()).unwrap(),
            "{\"type\":\"done\"}\n"
        );
    }

    #[test]
    fn error_shape() {
        assert_eq!(
            String::from_utf8(Event::Error { message: "upstream".into() }.to_line()).unwrap(),
            "{\"type\":\"error\",\"message\":\"upstream\"}\n"
        );
    }
}
