use anyhow::{ensure, Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

pub const MAX_FRAME_CHARS: usize = 16_384;
const MAX_SAFE_INTEGER: u64 = 9_007_199_254_740_991;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct RelayContext {
    pub session: String,
    pub foreground: u64,
    pub connection: u64,
}

impl RelayContext {
    pub fn valid(&self) -> bool {
        (1..=128).contains(&self.session.len())
            && self
                .session
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'_')
            && (1..=MAX_SAFE_INTEGER).contains(&self.foreground)
            && (1..=MAX_SAFE_INTEGER).contains(&self.connection)
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Identity {
    pub context: RelayContext,
    pub character: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Action {
    #[serde(rename = "type")]
    kind: String,
    protocol: u64,
    pub context: RelayContext,
    command: String,
}

impl Action {
    pub fn encode(&self, expected: &RelayContext) -> Result<String> {
        ensure!(
            self.kind == "action" && self.protocol == 2,
            "invalid action envelope"
        );
        ensure!(
            self.context == *expected && self.context.valid(),
            "action context mismatch"
        );
        ensure!(
            (1..=512).contains(&self.command.len())
                && self.command.bytes().all(|b| (b' '..=b'~').contains(&b)),
            "invalid action command"
        );
        Ok(
            json!({"type":"action", "protocol":2, "context":self.context, "command":self.command})
                .to_string(),
        )
    }
}

pub enum StateFrame {
    Hello,
    Snapshot {
        seq: u64,
        identity: Option<Identity>,
        frame: Value,
    },
    Status {
        feed: String,
    },
    Text,
}

pub fn state_frame(raw: &str) -> Result<StateFrame> {
    let frame = envelope(raw)?;
    integer(&frame["at"], MAX_SAFE_INTEGER).context("invalid state frame timestamp")?;
    match frame["type"].as_str() {
        Some("hello") => {
            ensure!(
                text(&frame["relay"]["name"], 64) && text(&frame["relay"]["version"], 64),
                "invalid relay hello"
            );
            Ok(StateFrame::Hello)
        }
        Some("snapshot") => {
            let seq =
                integer(&frame["seq"], MAX_SAFE_INTEGER).context("invalid snapshot sequence")?;
            let context = frame.get("context").context("missing snapshot context")?;
            let context = if context.is_null() {
                None
            } else {
                Some(relay_context(context)?)
            };
            let state = frame["state"]
                .as_object()
                .context("invalid snapshot state")?;
            let character = state
                .get("character")
                .context("missing snapshot character")?;
            let target = state.get("target").context("missing snapshot target")?;
            if !character.is_null() {
                ensure!(text(&character["name"], 64), "invalid character name");
                for vital in ["hp", "mana", "moves"] {
                    optional(character.get(vital), |v| {
                        integer(&v["current"], 1_000_000_000).is_some()
                            && integer(&v["max"], 1_000_000_000).is_some()
                    })?;
                }
            }
            if !target.is_null() {
                ensure!(text(&target["name"], 64), "invalid target name");
                optional(target.get("healthPercent"), |v| {
                    v.as_f64().is_some_and(|n| (0.0..=100.0).contains(&n))
                })?;
            }
            let identity = context
                .zip(character["name"].as_str())
                .map(|(context, name)| Identity {
                    context,
                    character: name.to_owned(),
                });
            Ok(StateFrame::Snapshot {
                seq,
                identity,
                frame,
            })
        }
        Some("status") => {
            let feed = frame["feed"]
                .as_str()
                .filter(|s| matches!(*s, "live" | "stale" | "down"))
                .context("invalid feed status")?;
            optional(frame.get("detail"), |v| text(v, 256))?;
            Ok(StateFrame::Status {
                feed: feed.to_owned(),
            })
        }
        Some("text") => {
            relay_context(&frame["context"])?;
            ensure!(text(&frame["text"], 1024), "invalid text event");
            Ok(StateFrame::Text)
        }
        _ => anyhow::bail!("unexpected state frame"),
    }
}

pub fn action_result(raw: &str) -> Result<Value> {
    let frame = envelope(raw)?;
    ensure!(
        frame["type"] == "action-result"
            && matches!(
                frame["status"].as_str(),
                Some("forwarded" | "rejected" | "unknown")
            ),
        "invalid relay action result"
    );
    optional(frame.get("detail"), |v| text(v, 256))?;
    Ok(frame)
}

fn envelope(raw: &str) -> Result<Value> {
    ensure!(
        raw.encode_utf16().count() <= MAX_FRAME_CHARS,
        "relay frame too large"
    );
    let frame: Value = serde_json::from_str(raw).context("invalid relay JSON")?;
    ensure!(
        frame["protocol"].as_u64() == Some(2),
        "unsupported relay protocol"
    );
    Ok(frame)
}

fn relay_context(value: &Value) -> Result<RelayContext> {
    let context = RelayContext::deserialize(value).context("invalid relay context")?;
    ensure!(context.valid(), "invalid relay context");
    Ok(context)
}

fn integer(value: &Value, max: u64) -> Option<u64> {
    value.as_u64().filter(|n| *n <= max)
}

fn text(value: &Value, max: usize) -> bool {
    value.as_str().is_some_and(|s| {
        !s.is_empty() && s.encode_utf16().count() <= max && !s.chars().any(char::is_control)
    })
}

fn optional(value: Option<&Value>, valid: impl FnOnce(&Value) -> bool) -> Result<()> {
    ensure!(
        value.is_some_and(|v| v.is_null() || valid(v)),
        "invalid optional relay field"
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn contextual_snapshot_requires_real_relay_fields() {
        let mut snapshot = json!({
            "type":"snapshot","protocol":2,"seq":1,"at":1,
            "context":{"session":"tf_1","foreground":1,"connection":2},
            "state":{"character":{"name":"Carmen","hp":null,"mana":null,"moves":null},"target":null}
        });
        assert!(matches!(
            state_frame(&snapshot.to_string()).unwrap(),
            StateFrame::Snapshot {
                identity: Some(_),
                ..
            }
        ));
        snapshot["state"]["character"] = Value::Null;
        assert!(matches!(
            state_frame(&snapshot.to_string()).unwrap(),
            StateFrame::Snapshot { identity: None, .. }
        ));
        snapshot["context"]["foreground"] = json!(0);
        assert!(state_frame(&snapshot.to_string()).is_err());
    }
}
