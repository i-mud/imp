#[cfg(target_os = "linux")]
mod linux;
#[allow(dead_code)]
#[path = "../../../src/runtime.rs"]
mod runtime;
mod ssh;
#[cfg(windows)]
mod windows;

#[cfg(target_os = "linux")]
use linux::Identity;
#[cfg(windows)]
use windows::Identity;

use runtime::{OwnedChild, RuntimeOwner};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::ffi::{OsStr, OsString};
use std::io::{self, BufRead, Write};
use std::net::{Ipv4Addr, SocketAddr, TcpStream};
use std::thread;
use std::time::{Duration, Instant};
use tungstenite::{client::client_with_config, protocol::WebSocketConfig, Message, WebSocket};

const PROOF_BUDGET: Duration = Duration::from_millis(100);
const IO_BUDGET: Duration = Duration::from_secs(3);
const FRAME_LIMIT: usize = 16_384;

#[derive(Clone, Copy, Deserialize)]
#[serde(rename_all = "lowercase")]
enum Slot {
    State,
    Action,
}

impl Slot {
    fn path(self) -> &'static str {
        match self {
            Self::State => "/state",
            Self::Action => "/action",
        }
    }
}

#[derive(Deserialize)]
#[serde(tag = "cmd", rename_all = "lowercase", deny_unknown_fields)]
enum Command {
    Begin {
        slot: Slot,
        #[serde(default)]
        pause_before_connect: bool,
    },
    Prove {
        slot: Slot,
    },
    Upgrade {
        slot: Slot,
    },
    Read {
        slot: Slot,
    },
    Send {
        slot: Slot,
        action: Action,
    },
    Close {
        slot: Slot,
    },
    Bench {
        slot: Slot,
        #[serde(default)]
        warm: bool,
        action: Option<Action>,
    },
    Warm {
        slot: Slot,
    },
    Retire,
    Stopchild,
    Quit,
}

#[derive(Deserialize)]
#[serde(tag = "cmd", rename_all = "lowercase", deny_unknown_fields)]
enum BarrierCommand {
    Stopchild,
    Resume,
    Cancel,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Action {
    #[serde(rename = "type")]
    kind: String,
    protocol: u8,
    context: Context,
    command: String,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Context {
    session: String,
    foreground: u64,
    connection: u64,
}

impl Action {
    fn valid(&self) -> bool {
        const SAFE: std::ops::RangeInclusive<u64> = 1..=9_007_199_254_740_991;
        self.kind == "action"
            && self.protocol == 2
            && self.command == "owned-forward-fixture"
            && self.context.session == "guided_fixture"
            && SAFE.contains(&self.context.foreground)
            && SAFE.contains(&self.context.connection)
    }
}

struct Pending {
    stream: TcpStream,
    dialed_at: Instant,
    connected_at: Instant,
}

// Two fixed slots per owner. Boxing the open state would allocate on every measured upgrade.
#[allow(clippy::large_enum_variant)]
enum Connection {
    Pending(Pending),
    Proven(TcpStream),
    Open(WebSocket<TcpStream>),
}

#[derive(Clone, Copy, Default, Serialize)]
struct Timing {
    connect_us: u128,
    proof_us: u128,
    search_us: u128,
    attempts: u32,
}

impl Timing {
    fn record(&mut self, pending: &Pending, start: Instant, now: Instant) {
        self.proof_us = (now - pending.connected_at).as_micros();
        self.search_us = (now - start).as_micros();
    }

    fn into_response(self) -> Value {
        let mut value = serde_json::to_value(self).unwrap();
        value["status"] = json!("ok");
        value
    }
}

struct Failure {
    code: &'static str,
    os_error: Option<i32>,
    timing: Option<Timing>,
}

type Result<T, E = Failure> = std::result::Result<T, E>;

impl Failure {
    fn io(code: &'static str, error: io::Error) -> Self {
        Self {
            os_error: error.raw_os_error(),
            ..code.into()
        }
    }

    fn response(self) -> Value {
        let mut response = self.timing.map_or_else(|| json!({}), Timing::into_response);
        response["status"] = json!("error");
        response["code"] = json!(self.code);
        if let Some(code) = self.os_error {
            response["os_error"] = json!(code);
        }
        response
    }
}

impl From<&'static str> for Failure {
    fn from(code: &'static str) -> Self {
        Self {
            code,
            os_error: None,
            timing: None,
        }
    }
}

struct Driver {
    child: OwnedChild,
    _owner: RuntimeOwner,
    identity: Identity,
    slots: [Option<Connection>; 2],
    addr: SocketAddr,
    retired: bool,
    stopped: bool,
    control: bool,
    strict_upgrade: bool,
}

impl Driver {
    fn spawn(args: ssh::Args) -> Result<Self> {
        let owner = RuntimeOwner::new().map_err(|error| Failure::io("spawn_failed", error))?;
        let program = OsStr::new("ssh");
        let argv = args.ssh.iter().map(OsString::as_os_str);
        #[cfg(target_os = "linux")]
        let child = linux::spawn(&owner, program, argv);
        #[cfg(windows)]
        let child = owner.spawn(program, argv, runtime::RuntimeStdout::Null);
        let mut child = child.map_err(|error| Failure::io("spawn_failed", error))?;
        let identity = match Identity::new(&mut child) {
            Ok(identity) => identity,
            Err(error) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(Failure::io("identity_failed", error));
            }
        };
        // Quietly drain SSH output, which can name fixture keys and hosts.
        for mut pipe in [child.stdout.take(), child.stderr.take()]
            .into_iter()
            .flatten()
        {
            thread::spawn(move || {
                let _ = io::copy(&mut pipe, &mut io::sink());
            });
        }
        Ok(Self {
            child,
            _owner: owner,
            identity,
            slots: [None, None],
            addr: (Ipv4Addr::LOCALHOST, args.local_port).into(),
            retired: false,
            stopped: false,
            control: args.control,
            strict_upgrade: false,
        })
    }

    fn live(&mut self) -> Result<()> {
        if self.retired {
            return Err("retired".into());
        }
        if self.stopped {
            return Err("child_exited".into());
        }
        #[cfg(target_os = "linux")]
        if !self
            .identity
            .alive()
            .map_err(|error| Failure::io("child_wait_failed", error))?
        {
            return Err("child_exited".into());
        }
        match self.child.try_wait() {
            Ok(None) => Ok(()),
            Ok(Some(_)) => Err("child_exited".into()),
            Err(error) => Err(Failure::io("child_wait_failed", error)),
        }
    }

    fn stop_child(&mut self) -> Result<()> {
        self.stopped = true;
        self.child
            .kill()
            .and_then(|_| self.child.wait())
            .map_err(|error| Failure::io("stop_failed", error))?;
        Ok(())
    }

    fn retire(&mut self) -> Result<()> {
        self.retired = true;
        self.slots = [None, None];
        self.stop_child()
    }

    fn begin(
        &mut self,
        slot: Slot,
        before_connect: impl FnOnce(&mut Self) -> Result<()>,
    ) -> Result<Instant> {
        self.live()?;
        if self.slots[slot as usize].is_some() {
            return Err("slot_busy".into());
        }
        before_connect(self)?;
        let dialed_at = Instant::now();
        let stream = TcpStream::connect_timeout(&self.addr, IO_BUDGET);
        let connected_at = Instant::now();
        self.live()?;
        let stream = stream.map_err(|error| Failure::io("connect_failed", error))?;
        self.slots[slot as usize] = Some(Connection::Pending(Pending {
            stream,
            dialed_at,
            connected_at,
        }));
        Ok(dialed_at)
    }

    fn prove(&mut self, slot: Slot) -> Result<Timing> {
        let entry = &mut self.slots[slot as usize];
        if !matches!(entry, Some(Connection::Pending(_))) {
            self.live()?;
            return Err("not_pending".into());
        }
        let Some(Connection::Pending(pending)) = entry.take() else {
            unreachable!();
        };
        let start = Instant::now();
        let deadline = pending.connected_at + PROOF_BUDGET;
        let mut timing = Timing {
            connect_us: (pending.connected_at - pending.dialed_at).as_micros(),
            ..Timing::default()
        };
        if let Err(mut failure) = self.live() {
            timing.record(&pending, start, Instant::now());
            failure.timing = Some(timing);
            return Err(failure);
        }
        let mut failure = loop {
            if Instant::now() >= deadline {
                break Failure::from("proof_timeout");
            }
            timing.attempts += 1;
            let observed = self.identity.observes(&mut self.child, &pending.stream);
            let live = self.live();
            let now = Instant::now();
            if let Err(error) = live {
                break error;
            }
            if now >= deadline {
                break Failure::from("proof_timeout");
            }
            match observed {
                Ok(true) => {
                    timing.record(&pending, start, now);
                    self.slots[slot as usize] = Some(Connection::Proven(pending.stream));
                    return Ok(timing);
                }
                Ok(false) => thread::sleep(Duration::from_millis(1).min(deadline - now)),
                Err(error) => break Failure::io("proof_failed", error),
            }
        };
        timing.record(&pending, start, Instant::now());
        failure.timing = Some(timing);
        Err(failure)
    }

    fn allow_warm(&self) -> Result<()> {
        if !self.control {
            return Err("control_required".into());
        }
        if !self.strict_upgrade {
            return Err("control_not_ready".into());
        }
        Ok(())
    }

    fn upgrade(&mut self, slot: Slot, warm: bool) -> Result<()> {
        self.live()?;
        let entry = &mut self.slots[slot as usize];
        let stream = match (warm, entry.as_ref()) {
            (false, Some(Connection::Proven(_))) | (true, Some(Connection::Pending(_))) => {
                match entry.take().unwrap() {
                    Connection::Proven(stream) | Connection::Pending(Pending { stream, .. }) => {
                        stream
                    }
                    Connection::Open(_) => unreachable!(),
                }
            }
            _ => return Err("not_proven".into()),
        };
        stream
            .set_read_timeout(Some(IO_BUDGET))
            .and_then(|_| stream.set_write_timeout(Some(IO_BUDGET)))
            .map_err(|error| Failure::io("upgrade_failed", error))?;
        self.live()?;
        // Unlike URL dialing, this performs one handshake, only on this stream, without redirects.
        let ws = client_with_config(
            format!("ws://{}{}", self.addr, slot.path()),
            stream,
            Some(
                WebSocketConfig::default()
                    .max_message_size(Some(FRAME_LIMIT))
                    .max_frame_size(Some(FRAME_LIMIT)),
            ),
        );
        self.live()?;
        let (ws, _) = ws.map_err(|_| Failure::from("upgrade_failed"))?;
        self.slots[slot as usize] = Some(Connection::Open(ws));
        self.strict_upgrade |= !warm;
        Ok(())
    }

    fn opened(&mut self, slot: Slot) -> Result<&mut WebSocket<TcpStream>> {
        self.live()?;
        match &mut self.slots[slot as usize] {
            Some(Connection::Open(ws)) => Ok(ws),
            _ => Err("not_open".into()),
        }
    }

    fn read(&mut self, slot: Slot) -> Result<Value> {
        let deadline = Instant::now() + IO_BUDGET;
        loop {
            let ws = self.opened(slot)?;
            let left = deadline.saturating_duration_since(Instant::now());
            if left.is_zero() {
                return Err("read_failed".into());
            }
            ws.get_ref()
                .set_read_timeout(Some(left))
                .map_err(|error| Failure::io("read_failed", error))?;
            let message = ws.read();
            self.live()?;
            match message {
                Ok(Message::Text(text)) => {
                    let frame: Value = serde_json::from_str(text.as_str())
                        .map_err(|_| Failure::from("invalid_frame"))?;
                    if frame["protocol"] != 2 || Instant::now() >= deadline {
                        return Err("invalid_frame".into());
                    }
                    return Ok(frame);
                }
                Ok(Message::Ping(_) | Message::Pong(_)) => continue,
                _ => return Err("read_failed".into()),
            }
        }
    }

    fn send(&mut self, slot: Slot, action: &Action) -> Result<()> {
        if !matches!(slot, Slot::Action) || !action.valid() {
            return Err("invalid_action".into());
        }
        let text = serde_json::to_string(action).map_err(|_| Failure::from("invalid_action"))?;
        let sent = self.opened(slot)?.send(Message::text(text));
        self.live()?;
        sent.map_err(|_| Failure::from("send_failed"))
    }

    fn warm(&mut self, slot: Slot) -> Result<()> {
        self.live()?;
        self.allow_warm()?;
        self.begin(slot, |_| Ok(()))?;
        self.upgrade(slot, true)
    }

    fn bench(&mut self, slot: Slot, warm: bool, action: Option<Action>) -> Result<Value> {
        self.live()?;
        if warm {
            self.allow_warm()?;
        }
        match (slot, action.as_ref()) {
            (Slot::State, None) => {}
            (Slot::Action, Some(action)) if action.valid() => {}
            _ => return Err("invalid_action".into()),
        }
        let start = self.begin(slot, |_| Ok(()))?;
        let mut observed_timing = None;
        let result = (|| {
            let timing = if warm {
                let Some(Connection::Pending(pending)) = &self.slots[slot as usize] else {
                    unreachable!();
                };
                Timing {
                    connect_us: (pending.connected_at - start).as_micros(),
                    ..Timing::default()
                }
            } else {
                self.prove(slot)?
            };
            observed_timing = Some(timing);
            self.upgrade(slot, warm)?;
            let mut response = timing.into_response();
            let messages = if let Some(action) = action {
                let action_start = Instant::now();
                self.send(slot, &action)?;
                let reply = self.read(slot)?;
                if reply["type"] != "action-result" {
                    return Err("unexpected_frame".into());
                }
                response["action_us"] = json!(action_start.elapsed().as_micros());
                vec![reply]
            } else {
                let hello = self.read(slot)?;
                let snapshot = self.read(slot)?;
                if hello["type"] != "hello" || snapshot["type"] != "snapshot" {
                    return Err("unexpected_frame".into());
                }
                vec![hello, snapshot]
            };
            response["elapsed_us"] = json!(start.elapsed().as_micros());
            response["messages"] = json!(messages);
            Ok(response)
        })();
        let result = result.map_err(|mut failure: Failure| {
            failure.timing = failure.timing.or(observed_timing);
            failure
        });
        if result.is_err() {
            self.slots[slot as usize] = None;
        }
        result
    }

    fn command(
        &mut self,
        command: Command,
        pause: impl FnOnce(&mut Self) -> Result<()>,
    ) -> Result<Value> {
        match command {
            Command::Begin {
                slot,
                pause_before_connect,
            } => {
                self.begin(slot, |driver| {
                    if pause_before_connect {
                        pause(driver)?;
                    }
                    Ok(())
                })?;
            }
            Command::Prove { slot } => return Ok(self.prove(slot)?.into_response()),
            Command::Upgrade { slot } => self.upgrade(slot, false)?,
            Command::Read { slot } => {
                return Ok(json!({"status": "ok", "message": self.read(slot)?}))
            }
            Command::Send { slot, action } => self.send(slot, &action)?,
            Command::Close { slot } => self.slots[slot as usize] = None,
            Command::Bench { slot, warm, action } => return self.bench(slot, warm, action),
            Command::Warm { slot } => self.warm(slot)?,
            Command::Stopchild => self.stop_child()?,
            Command::Retire | Command::Quit => self.retire()?,
        }
        Ok(json!({"status": "ok"}))
    }
}

fn reply(stdout: &mut impl Write, value: Value) -> io::Result<()> {
    serde_json::to_writer(&mut *stdout, &value)?;
    writeln!(stdout)?;
    stdout.flush()
}

fn pause_before_connect(
    driver: &mut Driver,
    lines: &mut impl Iterator<Item = io::Result<String>>,
    stdout: &mut impl Write,
) -> Result<()> {
    reply(stdout, json!({"status": "before_connect"}))
        .map_err(|error| Failure::io("barrier_failed", error))?;
    let mut next = || {
        let line = lines
            .next()
            .transpose()
            .map_err(|error| Failure::io("barrier_failed", error))?
            .ok_or_else(|| Failure::from("connect_cancelled"))?;
        serde_json::from_str::<BarrierCommand>(&line).map_err(|_| Failure::from("invalid_barrier"))
    };
    match next()? {
        BarrierCommand::Stopchild => driver.stop_child()?,
        BarrierCommand::Cancel => return Err("connect_cancelled".into()),
        BarrierCommand::Resume => return Err("invalid_barrier".into()),
    }
    reply(stdout, json!({"status": "ok"})).map_err(|error| Failure::io("barrier_failed", error))?;
    match next()? {
        BarrierCommand::Resume => Ok(()),
        BarrierCommand::Cancel => Err("connect_cancelled".into()),
        BarrierCommand::Stopchild => Err("invalid_barrier".into()),
    }
}

fn run(stdin: impl BufRead, mut stdout: impl Write) -> io::Result<()> {
    let driver = ssh::Args::parse(std::env::args_os().skip(1).collect())
        .ok_or_else(|| Failure::from("invalid_args"))
        .and_then(Driver::spawn);
    let mut driver = match driver {
        Ok(driver) => driver,
        Err(error) => return reply(&mut stdout, error.response()),
    };
    reply(
        &mut stdout,
        json!({"status": "ready", "pid": driver.child.id(), "control": driver.control}),
    )?;
    let mut lines = stdin.lines();
    while let Some(line) = lines.next() {
        let (response, quit) = match serde_json::from_str::<Command>(&line?) {
            Ok(command) => {
                let quit = matches!(command, Command::Quit);
                let response = driver
                    .command(command, |driver| {
                        pause_before_connect(driver, &mut lines, &mut stdout)
                    })
                    .unwrap_or_else(Failure::response);
                (response, quit)
            }
            Err(_) => (Failure::from("invalid_command").response(), false),
        };
        reply(&mut stdout, response)?;
        if quit {
            return Ok(());
        }
    }
    Ok(())
}

fn main() {
    runtime::run_helper_if_requested();
    #[cfg(target_os = "linux")]
    linux::run_helper_if_requested();
    if run(io::stdin().lock(), io::stdout().lock()).is_err() {
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn action_is_fixture_scoped() {
        let mut action: Action = serde_json::from_value(json!({
            "type": "action", "protocol": 2, "command": "owned-forward-fixture",
            "context": {"session": "guided_fixture", "foreground": 7, "connection": 11}
        }))
        .unwrap();
        assert!(action.valid());
        action.command.push('!');
        assert!(!action.valid());
    }
}
