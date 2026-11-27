use std::io;
use std::net::{IpAddr, SocketAddr};
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use anyhow::{ensure, Context, Result};
use futures_util::{SinkExt, StreamExt};
use serde::Deserialize;
use serde_json::{json, Value};
use tokio::sync::{mpsc, watch};
use tokio::task::{AbortHandle, JoinSet};
use tokio::time::timeout;
use tokio_tungstenite::{
    client_async_with_config,
    tungstenite::{protocol::WebSocketConfig, Message, Utf8Bytes},
    WebSocketStream,
};

use crate::gate::{self, Gate, Ticket};
use crate::session::{Session, Stream};
use crate::wire::{self, Action, StateFrame};

const DEADLINE: Duration = Duration::from_secs(5);
const SHUTDOWN: Duration = Duration::from_secs(2);
const MAX_ACTIONS: usize = 8;
const MAX_JOBS: usize = MAX_ACTIONS + 8;
const MAX_WS_BYTES: usize = wire::MAX_FRAME_CHARS * 3;

type Output = mpsc::Sender<Value>;
type WebSocket = WebSocketStream<Stream>;

#[derive(Deserialize)]
struct Request {
    id: u64,
    #[serde(flatten)]
    operation: Operation,
}

#[derive(Deserialize)]
#[serde(tag = "op", rename_all = "kebab-case")]
enum Operation {
    Establish(Connection),
    Replace(Connection),
    State { generation: u64 },
    Action { context: Ticket, action: Action },
    Shutdown,
}

#[derive(Deserialize)]
struct Connection {
    host: IpAddr,
    port: u16,
    username: String,
    server_key: PathBuf,
    private_key: PathBuf,
    relay_port: u16,
}

enum Completed {
    Connected { id: u64, result: Result<Session> },
    Lost(u64),
    Action,
    Retired,
    Disconnected(Result<()>),
}

struct Authority {
    gate: watch::Sender<Gate>,
    session: Option<Arc<Session>>,
    jobs: JoinSet<Result<Completed>>,
    connecting: Option<(u64, AbortHandle)>,
    state_started: bool,
    actions: usize,
    output: Output,
}

pub async fn run(input: mpsc::Receiver<io::Result<Vec<u8>>>, output: Output) -> Result<()> {
    let mut authority = Authority {
        gate: watch::channel(Gate::default()).0,
        session: None,
        jobs: JoinSet::new(),
        connecting: None,
        state_started: false,
        actions: 0,
        output,
    };
    let result = authority.serve(input).await;
    let cleanup = authority.stop().await;
    let shutdown_id = result?;
    if let Some(id) = shutdown_id {
        match cleanup {
            Ok(()) => emit(&authority.output, json!({"id":id,"status":"ok"}))?,
            Err(error) => reply_error(&authority.output, id, &error.to_string())?,
        }
        Ok(())
    } else {
        cleanup
    }
}

impl Authority {
    async fn serve(
        &mut self,
        mut input: mpsc::Receiver<io::Result<Vec<u8>>>,
    ) -> Result<Option<u64>> {
        loop {
            tokio::select! {
                biased;
                completed = self.jobs.join_next(), if !self.jobs.is_empty() => {
                    if let Some(completed) = completed { self.completed(completed??)?; }
                }
                line = input.recv() => {
                    let Some(line) = line else { return Ok(None); };
                    let line = line?;
                    let request = match serde_json::from_slice::<Request>(&line) {
                        Ok(request) => request,
                        Err(_) => {
                            let value = serde_json::from_slice::<Value>(&line).unwrap_or(Value::Null);
                            let id = value["id"].as_u64();
                            if let Some(id) = id.filter(|_| value["op"] == "action") {
                                emit(&self.output, gate::action_error(id, false, "invalid fixture action"))?;
                            } else {
                                emit(&self.output, json!({"id":id,"status":"error","error":"invalid fixture request"}))?;
                            }
                            continue;
                        }
                    };
                    let Request { id, operation } = request;
                    match operation {
                        Operation::Shutdown => return Ok(Some(id)),
                        Operation::Establish(connection) if self.gate.borrow().generation == 0 => self.connect(id, connection)?,
                        Operation::Establish(_) => reply_error(&self.output, id, "use replace after establishing a session")?,
                        Operation::Replace(connection) => self.connect(id, connection)?,
                        Operation::State { generation } => self.start_state(id, generation)?,
                        Operation::Action { context, action } => self.start_action(id, context, action)?,
                    }
                }
            }
        }
    }

    fn connect(&mut self, id: u64, config: Connection) -> Result<()> {
        if self.connecting.is_some() || self.jobs.len() >= MAX_JOBS - 2 {
            return reply_error(&self.output, id, "native session work is busy");
        }
        self.retire_current()?;
        self.connecting = Some((
            id,
            self.jobs.spawn(async move {
                let result = timeout(
                    DEADLINE,
                    Session::connect(
                        SocketAddr::new(config.host, config.port),
                        &config.username,
                        &config.private_key,
                        &config.server_key,
                        config.relay_port,
                    ),
                )
                .await
                .context("SSH establish timed out")
                .and_then(|result| result);
                Ok(Completed::Connected { id, result })
            }),
        ));
        Ok(())
    }

    fn completed(&mut self, completed: Completed) -> Result<()> {
        match completed {
            Completed::Connected { id, result } => {
                self.connecting = None;
                let session = match result {
                    Ok(session) if !session.is_closed() => Arc::new(session),
                    Ok(_) => return reply_error(&self.output, id, "SSH session already closed"),
                    Err(error) => return reply_error(&self.output, id, &error.to_string()),
                };
                let generation = self
                    .gate
                    .borrow()
                    .generation
                    .checked_add(1)
                    .context("authority generation overflow")?;
                self.gate.send_replace(Gate::new(generation));
                self.session = Some(Arc::clone(&session));
                self.state_started = false;
                let mut gate = self.gate.subscribe();
                let output = self.output.clone();
                let notifier = self.gate.clone();
                self.jobs.spawn(async move {
                    tokio::select! {
                        biased;
                        _ = generation_ended(&mut gate, generation) => Ok(Completed::Retired),
                        _ = session.lost() => {
                            retire_gate(&notifier, &output, generation)?;
                            Ok(Completed::Lost(generation))
                        }
                    }
                });
                emit(
                    &self.output,
                    json!({"id":id,"status":"ok","generation":generation}),
                )
            }
            Completed::Disconnected(result) => {
                if let Err(error) = result {
                    eprintln!("retiring fixture SSH session: {error}");
                }
                Ok(())
            }
            Completed::Lost(generation) if self.gate.borrow().generation == generation => {
                self.retire_current()
            }
            Completed::Lost(_) | Completed::Retired => Ok(()),
            Completed::Action => {
                self.actions -= 1;
                Ok(())
            }
        }
    }

    fn start_state(&mut self, id: u64, generation: u64) -> Result<()> {
        if !self.gate.borrow().is_current(generation) {
            return reply_error(&self.output, id, "state generation is no longer current");
        }
        let Some(session) = self.session.as_ref().filter(|session| !session.is_closed()) else {
            return reply_error(&self.output, id, "no active SSH generation");
        };
        if self.state_started || self.jobs.len() >= MAX_JOBS {
            return reply_error(
                &self.output,
                id,
                "state subscription is already installed or native work is busy",
            );
        }
        self.state_started = true;
        let session = Arc::clone(session);
        let gate = self.gate.clone();
        let output = self.output.clone();
        self.jobs.spawn(async move {
            let mut rx = gate.subscribe();
            let mut opened = false;
            let result = tokio::select! {
                biased;
                _ = generation_ended(&mut rx, generation) => {
                    if !opened { reply_error(&output, id, "state generation ended")?; }
                    return Ok(Completed::Retired);
                }
                _ = session.lost() => Err(anyhow::anyhow!("SSH session closed")),
                result = read_state(id, generation, &session, &gate, &output, &mut opened) => result,
            };
            if let Err(error) = result {
                if !opened { reply_error(&output, id, &error.to_string())?; }
                retire_gate(&gate, &output, generation)?;
            }
            Ok(Completed::Lost(generation))
        });
        Ok(())
    }

    fn start_action(&mut self, id: u64, ticket: Ticket, action: Action) -> Result<()> {
        let Some(session) = self
            .session
            .as_ref()
            .filter(|session| self.gate.borrow().permits(&ticket, session.is_closed()))
        else {
            return emit(
                &self.output,
                gate::action_error(id, false, "action context is no longer current"),
            );
        };
        let frame = match action.encode(&ticket.context) {
            Ok(frame) => frame,
            Err(error) => {
                return emit(
                    &self.output,
                    gate::action_error(id, false, &error.to_string()),
                )
            }
        };
        if self.actions >= MAX_ACTIONS || self.jobs.len() >= MAX_JOBS {
            return emit(
                &self.output,
                gate::action_error(id, false, "native action work is busy"),
            );
        }
        self.actions += 1;
        let session = Arc::clone(session);
        let gate = self.gate.subscribe();
        let output = self.output.clone();
        self.jobs.spawn(async move {
            let mut cancel = gate.clone();
            let mut issued = false;
            let result = tokio::select! {
                biased;
                _ = cancel.wait_for(|g| !g.permits(&ticket, session.is_closed())) => Err(anyhow::anyhow!("action context is no longer current")),
                _ = session.lost() => Err(anyhow::anyhow!("SSH session closed")),
                result = timeout(DEADLINE, send_action(&session, gate.clone(), &ticket, frame, &mut issued)) => {
                    result.context("relay action timed out").and_then(|result| result)
                }
            };
            emit(&output, gate::finish(&gate.borrow(), &ticket, session.is_closed(), id, issued, result))?;
            Ok(Completed::Action)
        });
        Ok(())
    }

    fn retire_current(&mut self) -> Result<()> {
        let generation = self.gate.borrow().generation;
        let invalidated = retire_gate(&self.gate, &self.output, generation);
        if let Some(session) = self.session.take() {
            self.jobs
                .spawn(async move { Ok(Completed::Disconnected(session.disconnect().await)) });
        }
        invalidated
    }

    async fn stop(&mut self) -> Result<()> {
        let retired = self.retire_current();
        let canceled = if let Some((id, connecting)) = self.connecting.take() {
            connecting.abort();
            reply_error(&self.output, id, "fixture shutdown canceled SSH establish")
        } else {
            Ok(())
        };
        let drained = timeout(SHUTDOWN, async {
            while let Some(result) = self.jobs.join_next().await {
                match result {
                    Ok(Ok(Completed::Disconnected(result))) => result?,
                    Ok(result) => {
                        result?;
                    }
                    Err(error) if error.is_cancelled() => {}
                    Err(error) => return Err(error.into()),
                }
            }
            Ok::<_, anyhow::Error>(())
        })
        .await
        .context("native shutdown timed out");
        self.jobs.abort_all();
        retired?;
        canceled?;
        drained?
    }
}

async fn generation_ended(gate: &mut watch::Receiver<Gate>, generation: u64) {
    let _ = gate.wait_for(|gate| !gate.is_current(generation)).await;
}

fn retire_gate(gate: &watch::Sender<Gate>, output: &Output, generation: u64) -> Result<()> {
    if gate.send_if_modified(|gate| {
        if !gate.is_current(generation) {
            return false;
        }
        gate.active = false;
        gate.status("down");
        true
    }) {
        emit(
            output,
            json!({"event":"invalidated","generation":generation}),
        )?;
    }
    Ok(())
}

async fn open_websocket(session: &Session, path: &str) -> Result<WebSocket> {
    let stream = session.open().await?;
    let config = WebSocketConfig::default()
        .max_message_size(Some(MAX_WS_BYTES))
        .max_frame_size(Some(MAX_WS_BYTES));
    let (socket, _) =
        client_async_with_config(format!("ws://127.0.0.1{path}"), stream, Some(config)).await?;
    Ok(socket)
}

async fn read_text(socket: &mut WebSocket) -> Result<Utf8Bytes> {
    loop {
        match socket.next().await.context("relay WebSocket ended")?? {
            Message::Text(text) => return Ok(text),
            Message::Ping(_) => socket.flush().await?,
            Message::Pong(_) => {}
            Message::Close(_) => anyhow::bail!("relay WebSocket closed"),
            _ => anyhow::bail!("unexpected binary relay frame"),
        }
    }
}

async fn read_state(
    id: u64,
    generation: u64,
    session: &Session,
    gate: &watch::Sender<Gate>,
    output: &Output,
    opened: &mut bool,
) -> Result<()> {
    let mut socket = timeout(DEADLINE, async {
        let mut socket = open_websocket(session, "/state").await?;
        ensure!(
            matches!(
                wire::state_frame(&read_text(&mut socket).await?)?,
                StateFrame::Hello
            ),
            "expected relay hello"
        );
        Ok::<_, anyhow::Error>(socket)
    })
    .await
    .context("state channel setup timed out")??;
    ensure!(
        !session.is_closed() && gate.borrow().is_current(generation),
        "state generation ended"
    );
    *opened = true;
    emit(
        output,
        json!({"id":id,"status":"ok","generation":generation}),
    )?;
    let mut last_seq = None;
    loop {
        let frame = wire::state_frame(&read_text(&mut socket).await?)?;
        publish_state(
            gate,
            output,
            id,
            generation,
            session.is_closed(),
            &mut last_seq,
            frame,
        )?;
        // Bound one task's work when the SSH channel has several ready frames.
        tokio::task::yield_now().await;
    }
}

fn publish_state(
    gate: &watch::Sender<Gate>,
    output: &Output,
    id: u64,
    generation: u64,
    session_closed: bool,
    last_seq: &mut Option<u64>,
    frame: StateFrame,
) -> Result<()> {
    // This check, mutation, and publication do not yield on the authority runtime.
    ensure!(
        !session_closed && gate.borrow().is_current(generation),
        "state generation ended"
    );
    match frame {
        StateFrame::Hello => anyhow::bail!("duplicate relay hello"),
        StateFrame::Snapshot {
            seq,
            identity,
            frame,
        } => {
            ensure!(
                last_seq.is_none_or(|last| seq > last),
                "relay snapshot sequence did not increase"
            );
            *last_seq = Some(seq);
            gate.send_modify(|gate| gate.snapshot(identity));
            emit(
                output,
                json!({"id":id,"event":"state","generation":generation,"context":gate.borrow().ticket(),"snapshot":frame}),
            )
        }
        StateFrame::Status { feed } => {
            gate.send_modify(|gate| gate.status(&feed));
            emit(
                output,
                json!({"id":id,"event":"status","generation":generation,"feed":feed}),
            )
        }
        StateFrame::Text => Ok(()),
    }
}

async fn send_action(
    session: &Session,
    mut gate: watch::Receiver<Gate>,
    ticket: &Ticket,
    frame: String,
    issued: &mut bool,
) -> Result<Value> {
    let mut socket = open_websocket(session, "/action").await?;
    gate::dispatch(
        &mut gate,
        ticket,
        || session.is_closed(),
        &mut socket,
        frame,
        issued,
    )
    .await?;
    let result = read_text(&mut socket).await?;
    wire::action_result(&result)
}

fn emit(output: &Output, frame: Value) -> Result<()> {
    output
        .try_send(frame)
        .map_err(|_| anyhow::anyhow!("fixture output unavailable or backed up"))
}

fn reply_error(output: &Output, id: u64, error: &str) -> Result<()> {
    emit(output, json!({"id":id,"status":"error","error":error}))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn delayed_state_request_is_rejected_at_admission() {
        let (output, mut events) = mpsc::channel(2);
        let mut authority = Authority {
            gate: watch::channel(Gate::new(2)).0,
            session: None,
            jobs: JoinSet::new(),
            connecting: None,
            state_started: false,
            actions: 0,
            output,
        };
        let request: Request =
            serde_json::from_str(r#"{"id":1,"op":"state","generation":1}"#).unwrap();
        let Operation::State { generation } = request.operation else {
            panic!("expected state request");
        };
        authority.start_state(request.id, generation).unwrap();
        assert_eq!(
            events.try_recv().unwrap(),
            json!({"id":1,"status":"error","error":"state generation is no longer current"})
        );
        assert!(!authority.state_started);
        assert!(authority.jobs.is_empty());
    }

    #[test]
    fn retired_state_completion_cannot_publish_or_mutate_replacement() {
        let (output, mut events) = mpsc::channel(2);
        let (gate, _) = watch::channel(Gate::new(2));
        let mut seq = None;
        let frame = wire::state_frame(
            r#"{
            "type":"snapshot","protocol":2,"seq":1,"at":1,
            "context":{"session":"tf_1","foreground":1,"connection":2},
            "state":{"character":{"name":"Carmen","hp":null,"mana":null,"moves":null},"target":null}
        }"#,
        )
        .unwrap();
        assert!(publish_state(&gate, &output, 1, 1, false, &mut seq, frame).is_err());
        retire_gate(&gate, &output, 1).unwrap();
        assert!(gate.borrow().is_current(2));
        assert!(gate.borrow().ticket().is_none());
        assert_eq!(seq, None);
        assert!(events.try_recv().is_err());
    }
}
