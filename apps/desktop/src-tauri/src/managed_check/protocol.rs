use super::CheckResult;
use serde_json::Value;
use std::{
    cell::Cell,
    io::{self, Read, Write},
    time::Instant,
};
use tungstenite::{
    client::client_with_config, protocol::WebSocketConfig, Error, HandshakeError, Message,
};

const STATE_URL: &str = "ws://127.0.0.1:8787/state";
const MAX_HEADER_BYTES: usize = 8 * 1024;
const MAX_FRAME_CHARS: usize = 16_384;
// The canonical limit counts UTF-16 units; one unit occupies at most three UTF-8 bytes.
const MAX_MESSAGE_BYTES: usize = 3 * MAX_FRAME_CHARS;
const MAX_WIRE_BYTES: usize = MAX_HEADER_BYTES + 3 * MAX_MESSAGE_BYTES + 4 * 1024;
const MAX_CONTROL_FRAMES: usize = 32;
const MAX_SAFE_INTEGER: f64 = 9_007_199_254_740_991.0;
const MAX_VITAL: f64 = 1_000_000_000.0;

pub(super) fn exchange(stream: impl Read + Write, deadline: Instant) -> CheckResult {
    exchange_until(stream, || Instant::now() >= deadline)
}

fn exchange_until(stream: impl Read + Write, expired: impl Fn() -> bool) -> CheckResult {
    let remaining = Cell::new(MAX_HEADER_BYTES);
    let stream = BoundedStream {
        stream,
        remaining: &remaining,
        expired: &expired,
    };
    let config = WebSocketConfig::default()
        .read_buffer_size(4096)
        .write_buffer_size(0)
        .max_write_buffer_size(1024)
        .max_frame_size(Some(MAX_MESSAGE_BYTES))
        .max_message_size(Some(MAX_MESSAGE_BYTES));
    // Unlike connect(), client_with_config neither reconnects nor follows redirects.
    let handshake = client_with_config(STATE_URL, stream, Some(config));
    if expired() {
        return CheckResult::Timeout;
    }
    let (mut websocket, response) = match handshake {
        Ok(upgrade) => upgrade,
        Err(HandshakeError::Failure(Error::Io(error)))
            if error.kind() == io::ErrorKind::TimedOut =>
        {
            return CheckResult::Timeout;
        }
        Err(_) if remaining.get() == MAX_HEADER_BYTES => return CheckResult::ConnectionFailed,
        Err(_) => return CheckResult::InvalidProtocol,
    };
    // Tungstenite doesn't reject unsolicited extensions itself.
    if response.headers().contains_key("sec-websocket-extensions") {
        return CheckResult::InvalidProtocol;
    }
    remaining.set(remaining.get() + MAX_WIRE_BYTES - MAX_HEADER_BYTES);

    let mut startup = Startup::Hello;
    let mut controls = 0;
    loop {
        if expired() {
            return CheckResult::Timeout;
        }
        let message = websocket.read();
        if expired() {
            return CheckResult::Timeout;
        }
        match message {
            Ok(Message::Text(raw)) => {
                let observation = startup.observe(raw.as_str());
                if expired() {
                    return CheckResult::Timeout;
                }
                match observation {
                    Ok(Some(result)) => return result,
                    Ok(None) => {}
                    Err(()) => return CheckResult::InvalidProtocol,
                }
            }
            Ok(message @ (Message::Ping(_) | Message::Pong(_) | Message::Close(_))) => {
                controls += 1;
                if controls > MAX_CONTROL_FRAMES {
                    return CheckResult::InvalidProtocol;
                }
                // read() queues required control replies, which must reach the peer.
                match websocket.flush() {
                    Ok(()) => {}
                    Err(Error::Io(error)) if error.kind() == io::ErrorKind::TimedOut => {
                        return CheckResult::Timeout;
                    }
                    Err(_) => return CheckResult::InvalidProtocol,
                }
                if message.is_close() {
                    return CheckResult::InvalidProtocol;
                }
            }
            Err(Error::Io(error)) if error.kind() == io::ErrorKind::TimedOut => {
                return CheckResult::Timeout;
            }
            _ => return CheckResult::InvalidProtocol,
        }
    }
}

// Bound all incoming bytes, including bytes read ahead during upgrade and fragments
// that aren't surfaced as messages. The process owner interrupts blocked pipe I/O.
struct BoundedStream<'a, S, F> {
    stream: S,
    remaining: &'a Cell<usize>,
    expired: &'a F,
}

impl<S: Read, F: Fn() -> bool> Read for BoundedStream<'_, S, F> {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        if (self.expired)() {
            return Err(io::ErrorKind::TimedOut.into());
        }
        let remaining = self.remaining.get();
        if remaining == 0 && !buf.is_empty() {
            return Err(io::ErrorKind::InvalidData.into());
        }
        let len = buf.len().min(remaining);
        let read = self.stream.read(&mut buf[..len])?;
        self.remaining.set(remaining - read);
        Ok(read)
    }
}

impl<S: Write, F: Fn() -> bool> Write for BoundedStream<'_, S, F> {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        if (self.expired)() {
            return Err(io::ErrorKind::TimedOut.into());
        }
        self.stream.write(buf)
    }

    fn flush(&mut self) -> io::Result<()> {
        if (self.expired)() {
            return Err(io::ErrorKind::TimedOut.into());
        }
        self.stream.flush()
    }
}

enum Startup {
    Hello,
    SnapshotOrStatus,
    Status { usable_snapshot: bool },
}

impl Startup {
    fn observe(&mut self, raw: &str) -> Result<Option<CheckResult>, ()> {
        if raw.encode_utf16().count() > MAX_FRAME_CHARS {
            return Err(());
        }
        let body: Value = serde_json::from_str(raw).map_err(|_| ())?;
        if !integer(&body["protocol"], 2.0, 2.0) || !integer(&body["at"], 0.0, MAX_SAFE_INTEGER) {
            return Err(());
        }
        match (&*self, body["type"].as_str()) {
            (Self::Hello, Some("hello"))
                if text(&body["relay"]["name"], 64) && text(&body["relay"]["version"], 64) =>
            {
                *self = Self::SnapshotOrStatus;
                Ok(None)
            }
            (Self::SnapshotOrStatus, Some("snapshot")) => {
                let context = body.get("context").ok_or(())?;
                if !integer(&body["seq"], 0.0, MAX_SAFE_INTEGER)
                    || !valid_state(&body["state"])
                    || (!context.is_null() && !valid_context(context))
                {
                    return Err(());
                }
                *self = Self::Status {
                    usable_snapshot: !context.is_null(),
                };
                Ok(None)
            }
            (Self::SnapshotOrStatus | Self::Status { .. }, Some("status"))
                if body["detail"].is_null() || text(&body["detail"], 256) =>
            {
                let result = match body["feed"].as_str() {
                    Some("live")
                        if matches!(
                            self,
                            Self::Status {
                                usable_snapshot: true
                            }
                        ) =>
                    {
                        CheckResult::Live
                    }
                    Some("live") => CheckResult::NoUsableState,
                    Some("stale") => CheckResult::Stale,
                    Some("down") => CheckResult::Down,
                    _ => return Err(()),
                };
                Ok(Some(result))
            }
            _ => Err(()),
        }
    }
}

fn text(field: &Value, max: usize) -> bool {
    field.as_str().is_some_and(|text| {
        !text.is_empty()
            && text.encode_utf16().count() <= max
            && !text.chars().any(char::is_control)
    })
}

fn integer(field: &Value, min: f64, max: f64) -> bool {
    field
        .as_f64()
        .is_some_and(|n| (min..=max).contains(&n) && n.fract() == 0.0)
}

fn valid_context(context: &Value) -> bool {
    context["session"].as_str().is_some_and(|session| {
        !session.is_empty()
            && session.len() <= 128
            && session
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || c == b'_')
    }) && integer(&context["foreground"], 1.0, MAX_SAFE_INTEGER)
        && integer(&context["connection"], 1.0, MAX_SAFE_INTEGER)
}

fn valid_state(state: &Value) -> bool {
    let character = &state["character"];
    let target = &state["target"];
    state.is_object()
        && (character.is_null()
            || (text(&character["name"], 64)
                && ["hp", "mana", "moves"].into_iter().all(|key| {
                    let vital = &character[key];
                    vital.is_null()
                        || (integer(&vital["current"], 0.0, MAX_VITAL)
                            && integer(&vital["max"], 0.0, MAX_VITAL))
                })))
        && (target.is_null()
            || (text(&target["name"], 64)
                && (target["healthPercent"].is_null()
                    || target["healthPercent"]
                        .as_f64()
                        .is_some_and(|n| (0.0..=100.0).contains(&n)))))
}

#[cfg(test)]
mod tests {
    use super::*;
    use tungstenite::handshake::derive_accept_key;

    macro_rules! fixture {
        ($path:literal) => {
            serde_json::from_str::<Value>(include_str!(concat!(
                "../../../../../packages/protocol/fixtures/",
                $path,
                ".json"
            )))
            .unwrap()["frame"]
                .as_str()
                .unwrap()
                .to_owned()
        };
    }

    #[derive(Default)]
    struct Relay {
        output: Vec<u8>,
        frames: Vec<u8>,
        response: Option<Vec<u8>>,
        headers: &'static str,
        eof: Option<io::ErrorKind>,
        offset: usize,
    }

    impl Read for Relay {
        fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
            let response = self.response.get_or_insert_with(|| {
                let request = std::str::from_utf8(&self.output).unwrap();
                assert!(request.starts_with("GET /state HTTP/1.1\r\n"));
                assert!(request.contains("Host: 127.0.0.1:8787\r\n"));
                let key = request
                    .lines()
                    .find_map(|line| line.strip_prefix("Sec-WebSocket-Key: "))
                    .unwrap();
                let accept = derive_accept_key(key.as_bytes());
                format!(
                    "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n{}\r\n",
                    self.headers
                )
                .into_bytes()
            });
            let (data, offset) = if self.offset < response.len() {
                (response.as_slice(), self.offset)
            } else {
                (self.frames.as_slice(), self.offset - response.len())
            };
            let n = (&data[offset..]).read(buf)?;
            if n == 0 {
                if let Some(error) = self.eof {
                    return Err(error.into());
                }
            }
            self.offset += n;
            Ok(n)
        }
    }

    impl Write for Relay {
        fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
            self.output.write(buf)
        }

        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }

    impl Relay {
        fn frame(&mut self, opcode: u8, payload: &[u8]) {
            self.frames.push(opcode);
            if payload.len() <= 125 {
                self.frames.push(payload.len() as u8);
            } else {
                self.frames.push(126);
                self.frames
                    .extend_from_slice(&u16::try_from(payload.len()).unwrap().to_be_bytes());
            }
            self.frames.extend_from_slice(payload);
        }

        fn text(&mut self, raw: &str) {
            self.frame(0x81, raw.as_bytes());
        }

        fn result(&mut self) -> CheckResult {
            exchange_until(self, || false)
        }
    }

    fn result(frames: &[&str]) -> CheckResult {
        let mut relay = Relay::default();
        for raw in frames {
            relay.text(raw);
        }
        relay.result()
    }

    #[test]
    fn canonical_startup_classifies_feed_separately_from_retained_state() {
        let hello = fixture!("accept/hello");
        let live = fixture!("accept/status-feed-live-no-detail");
        let down = fixture!("accept/status-feed-down");
        let stale = live.replace("\"live\"", "\"stale\"");
        for snapshot in [
            fixture!("accept/snapshot-full"),
            fixture!("accept/snapshot-overheal"),
            fixture!("accept/snapshot-partial-vitals"),
            fixture!("accept/snapshot-target-unknown-health"),
            fixture!("accept/snapshot-forward-compatible-fields"),
        ] {
            assert_eq!(result(&[&hello, &snapshot, &live]), CheckResult::Live);
            assert_eq!(result(&[&hello, &snapshot, &stale]), CheckResult::Stale);
            assert_eq!(result(&[&hello, &snapshot, &down]), CheckResult::Down);
        }
        assert_eq!(result(&[&hello, &live]), CheckResult::NoUsableState);
        assert_eq!(result(&[&hello, &stale]), CheckResult::Stale);
        assert_eq!(result(&[&hello, &down]), CheckResult::Down);
        assert_eq!(
            result(&[&hello, &fixture!("accept/snapshot-empty-state"), &live]),
            CheckResult::NoUsableState
        );
    }

    #[test]
    fn rejects_noncanonical_application_sequence_and_incomplete_startup() {
        let hello = fixture!("accept/hello");
        let snapshot = fixture!("accept/snapshot-full");
        let live = fixture!("accept/status-feed-live-no-detail");
        let text = fixture!("accept/text-server");
        for frames in [
            &[][..],
            &[&hello][..],
            &[&hello, &snapshot][..],
            &[&snapshot, &hello, &live][..],
            &[&live, &hello, &snapshot][..],
            &[&hello, &hello, &live][..],
            &[&hello, &snapshot, &snapshot, &live][..],
            &[&hello, &text, &live][..],
        ] {
            let mut relay = Relay::default();
            for frame in frames {
                relay.text(frame);
            }
            assert_eq!(relay.result(), CheckResult::InvalidProtocol);
        }
    }

    #[test]
    fn rejects_invalid_canonical_fields() {
        let hello = fixture!("accept/hello");
        let snapshot = fixture!("accept/snapshot-full");
        let live = fixture!("accept/status-feed-live-no-detail");
        for raw in [
            fixture!("reject/invalid-json"),
            fixture!("reject/array-frame"),
            fixture!("reject/null-frame"),
            fixture!("reject/missing-protocol"),
            fixture!("reject/hello-missing-relay"),
            hello.replace("\"0.1.0\"", "null"),
        ] {
            assert_eq!(
                result(&[&raw, &snapshot, &live]),
                CheckResult::InvalidProtocol
            );
        }
        for raw in [
            fixture!("reject/future-protocol"),
            fixture!("reject/missing-at"),
            snapshot_with_context_from(&fixture!("reject/context-invalid-session")),
            snapshot_with_context_from(&fixture!("reject/context-integer-overflow")),
            snapshot_with_context_from(
                r#"{"context":{"session":"s1","foreground":1,"connection":9007199254740992}}"#,
            ),
            fixture!("reject/character-missing-name"),
            fixture!("reject/name-control-characters"),
            fixture!("reject/name-too-long"),
            fixture!("reject/state-not-object"),
            fixture!("reject/target-percent-integer-overflow"),
            fixture!("reject/target-percent-not-a-number"),
            fixture!("reject/target-percent-out-of-range"),
            fixture!("reject/vital-absurd-value"),
            fixture!("reject/vital-missing-max"),
            fixture!("reject/vital-negative"),
            fixture!("reject/vital-non-integer"),
            r#"{"type":"snapshot","protocol":2,"at":0,"seq":0,"state":{}}"#.to_owned(),
            r#"{"type":"snapshot","protocol":2,"at":0,"seq":0,"context":null}"#.to_owned(),
            r#"{"type":"snapshot","protocol":2,"at":0,"context":null,"state":{}}"#.to_owned(),
            r#"{"type":"snapshot","protocol":2,"at":0,"seq":9007199254740992,"context":null,"state":{}}"#
                .to_owned(),
        ] {
            assert_eq!(result(&[&hello, &raw, &live]), CheckResult::InvalidProtocol);
        }
        for raw in [
            fixture!("reject/status-unknown-feed"),
            live.replace("\"live\"", "null"),
            live.replace("1758000000800", "9007199254740992"),
            format!("{{\"detail\":\"{}\",{}", "x".repeat(257), &live[1..]),
        ] {
            assert_eq!(
                result(&[&hello, &snapshot, &raw]),
                CheckResult::InvalidProtocol
            );
        }
    }

    fn snapshot_with_context_from(frame: &str) -> String {
        // Copy the flat context verbatim: the overflow fixture cannot be parsed as Value.
        let (_, context) = frame.split_once(r#""context":{"#).unwrap();
        let (fields, _) = context.split_once('}').unwrap();
        format!(
            r#"{{"type":"snapshot","protocol":2,"at":0,"seq":1,"context":{{{fields}}},"state":{{}}}}"#
        )
    }

    #[test]
    fn rejects_non_websocket_http_without_reading_a_body_or_following_a_redirect() {
        const REDIRECT: &[u8] =
            b"HTTP/1.1 302 Found\r\nLocation: /action\r\nContent-Length: 9223372036854775807\r\n\r\n";
        for response in [
            b"HTTP/1.1 101 Switching Protocols\r\n\
              Upgrade: websocket\r\nConnection: Upgrade\r\n\
              Sec-WebSocket-Accept: invalid\r\n\r\n"
                .as_slice(),
            b"HTTP/1.1 101 Switching Protocols\r\nUpgrade:",
        ] {
            let mut relay = Relay {
                response: Some(response.to_vec()),
                ..Relay::default()
            };
            assert_eq!(relay.result(), CheckResult::InvalidProtocol);
        }
        let mut relay = Relay {
            response: Some(REDIRECT.to_vec()),
            eof: Some(io::ErrorKind::TimedOut),
            ..Relay::default()
        };
        assert_eq!(relay.result(), CheckResult::InvalidProtocol);
        assert!(relay.output.starts_with(b"GET /state "));
        assert_eq!(
            relay
                .output
                .windows(4)
                .filter(|bytes| *bytes == b"GET ")
                .count(),
            1
        );
        let mut relay = Relay {
            headers: "Sec-WebSocket-Extensions: permessage-deflate\r\n",
            ..Relay::default()
        };
        assert_eq!(relay.result(), CheckResult::InvalidProtocol);
    }

    #[test]
    fn bounds_header_bytes_before_upgrade() {
        let mut response = b"HTTP/1.1 101 Switching Protocols\r\nPad: ".to_vec();
        response.resize(MAX_HEADER_BYTES + 1, b'x');
        let mut relay = Relay {
            response: Some(response),
            ..Relay::default()
        };
        assert_eq!(relay.result(), CheckResult::InvalidProtocol);
        assert_eq!(relay.offset, MAX_HEADER_BYTES);
    }

    #[test]
    fn handles_fragmented_text_and_replies_to_ping_and_close() {
        let hello = fixture!("accept/hello");
        let mut relay = Relay::default();
        let (first, last) = hello.as_bytes().split_at(20);
        relay.frame(0x01, first);
        relay.frame(0x89, b"p");
        relay.frame(0x80, last);
        relay.text(&fixture!("accept/status-feed-down"));
        assert_eq!(relay.result(), CheckResult::Down);
        let request_end = relay
            .output
            .windows(4)
            .position(|b| b == b"\r\n\r\n")
            .unwrap()
            + 4;
        let pong = &relay.output[request_end..];
        assert_eq!(pong.len(), 7);
        assert_eq!(&pong[..2], &[0x8A, 0x81]);
        assert_eq!(pong[2] ^ pong[6], b'p');

        let mut relay = Relay::default();
        relay.text(&hello);
        relay.frame(0x88, &1000_u16.to_be_bytes());
        assert_eq!(relay.result(), CheckResult::InvalidProtocol);
        let request_end = relay
            .output
            .windows(4)
            .position(|b| b == b"\r\n\r\n")
            .unwrap()
            + 4;
        assert_eq!(&relay.output[request_end..request_end + 2], &[0x88, 0x82]);
    }

    #[test]
    fn rejects_binary_bad_control_and_invalid_utf8() {
        for (opcode, payload) in [(0x82, b"{}".as_slice()), (0x09, b"p"), (0x81, b"\xff")] {
            let mut relay = Relay::default();
            relay.frame(opcode, payload);
            assert_eq!(relay.result(), CheckResult::InvalidProtocol);
        }
    }

    #[test]
    fn bounds_text_bytes_utf16_units_and_fragmented_message_size() {
        let hello = fixture!("accept/hello");
        let down = fixture!("accept/status-feed-down");
        let max_text = format!("{hello}{}", " ".repeat(MAX_FRAME_CHARS - hello.len()));
        assert_eq!(result(&[&max_text, &down]), CheckResult::Down);
        assert_eq!(
            result(&[&(max_text + " "), &down]),
            CheckResult::InvalidProtocol
        );
        assert_eq!(
            result(&[&fixture!("reject/frame-too-large")]),
            CheckResult::InvalidProtocol
        );

        let mut relay = Relay {
            eof: Some(io::ErrorKind::TimedOut),
            ..Relay::default()
        };
        relay.frames.extend_from_slice(&[0x81, 127]);
        relay.frames.extend_from_slice(&(1_u64 << 40).to_be_bytes());
        assert_eq!(relay.result(), CheckResult::InvalidProtocol);

        let mut relay = Relay::default();
        let fragment = vec![b' '; MAX_MESSAGE_BYTES / 2];
        relay.frame(0x01, &fragment);
        relay.frame(0x00, &fragment);
        relay.frame(0x80, b" ");
        assert_eq!(relay.result(), CheckResult::InvalidProtocol);
    }

    #[test]
    fn bounds_control_flood_and_wire_bytes_even_for_empty_continuations() {
        let mut relay = Relay {
            eof: Some(io::ErrorKind::TimedOut),
            ..Relay::default()
        };
        for _ in 0..=MAX_CONTROL_FRAMES {
            relay.frame(0x8a, b"");
        }
        assert_eq!(relay.result(), CheckResult::InvalidProtocol);

        let mut relay = Relay::default();
        relay.frame(0x01, b"");
        for _ in 0..=MAX_WIRE_BYTES / 2 {
            relay.frame(0x00, b"");
        }
        assert_eq!(relay.result(), CheckResult::InvalidProtocol);
        assert_eq!(relay.offset, MAX_WIRE_BYTES);
    }

    #[test]
    fn distinguishes_no_response_from_timeout_without_a_wall_clock() {
        let mut relay = Relay {
            response: Some(Vec::new()),
            ..Relay::default()
        };
        assert_eq!(relay.result(), CheckResult::ConnectionFailed);
        assert_eq!(
            exchange_until(Relay::default(), || true),
            CheckResult::Timeout
        );
        let mut relay = Relay {
            eof: Some(io::ErrorKind::TimedOut),
            ..Relay::default()
        };
        relay.text(&fixture!("accept/hello"));
        assert_eq!(relay.result(), CheckResult::Timeout);
    }
}
