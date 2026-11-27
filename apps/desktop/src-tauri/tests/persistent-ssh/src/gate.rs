use std::future::poll_fn;

use anyhow::{ensure, Result};
use futures_util::{Sink, SinkExt};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tokio::sync::watch;
use tokio_tungstenite::tungstenite::Message;

use crate::wire::{Identity, RelayContext};

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct Ticket {
    pub generation: u64,
    pub revision: u64,
    #[serde(flatten)]
    pub context: RelayContext,
}

#[derive(Default)]
pub struct Gate {
    pub generation: u64,
    pub active: bool,
    revision: u64,
    identity: Option<Identity>,
    live: bool,
}

impl Gate {
    pub fn new(generation: u64) -> Self {
        Self {
            generation,
            active: true,
            ..Self::default()
        }
    }

    pub fn snapshot(&mut self, identity: Option<Identity>) {
        if self.identity != identity {
            self.revision = self
                .revision
                .checked_add(1)
                .expect("authority revision overflow");
            self.identity = identity;
        }
    }

    pub fn status(&mut self, feed: &str) {
        self.live = feed == "live";
        if !self.live {
            // A retained snapshot is not evidence of an actionable foreground after feed loss.
            self.snapshot(None);
        }
    }

    pub fn ticket(&self) -> Option<Ticket> {
        self.identity.as_ref().map(|identity| Ticket {
            generation: self.generation,
            revision: self.revision,
            context: identity.context.clone(),
        })
    }

    pub fn is_current(&self, generation: u64) -> bool {
        self.active && self.generation == generation
    }

    pub fn permits(&self, ticket: &Ticket, session_closed: bool) -> bool {
        !session_closed
            && self.is_current(ticket.generation)
            && self.live
            && self.revision == ticket.revision
            && self
                .identity
                .as_ref()
                .is_some_and(|identity| identity.context == ticket.context)
    }
}

pub async fn dispatch<S>(
    gate: &mut watch::Receiver<Gate>,
    ticket: &Ticket,
    closed: impl Fn() -> bool,
    sink: &mut S,
    frame: String,
    issued: &mut bool,
) -> Result<()>
where
    S: Sink<Message> + Unpin,
    S::Error: std::error::Error + Send + Sync + 'static,
{
    ensure!(
        gate.borrow().permits(ticket, closed()),
        "action context is no longer current"
    );
    let current = gate.clone();
    tokio::select! {
        biased;
        _ = gate.wait_for(|g| !g.permits(ticket, closed())) => anyhow::bail!("action context is no longer current"),
        result = async {
            poll_fn(|cx| Pin::new(&mut *sink).poll_ready(cx)).await?;
            {
                let current = current.borrow();
                ensure!(current.permits(ticket, closed()), "action context is no longer current");
                // Once start_send is attempted, cancellation cannot prove non-delivery.
                *issued = true;
                Pin::new(&mut *sink).start_send(Message::Text(frame.into()))?;
            }
            sink.flush().await?;
            Ok::<_, anyhow::Error>(())
        } => result,
    }
}

pub fn finish(
    gate: &Gate,
    ticket: &Ticket,
    closed: bool,
    id: u64,
    issued: bool,
    result: Result<Value>,
) -> Value {
    let result = if gate.permits(ticket, closed) {
        result
    } else {
        Err(anyhow::anyhow!("action context is no longer current"))
    };
    match result {
        Ok(result) => json!({"id":id,"status":"ok","generation":ticket.generation,"result":result}),
        Err(error) => action_error(id, issued, &error.to_string()),
    }
}

pub fn action_error(id: u64, issued: bool, error: &str) -> Value {
    json!({"id":id,"status":"error","error":error,"delivery":if issued {"unknown"} else {"not-dispatched"}})
}

#[cfg(test)]
mod tests {
    use std::sync::{
        atomic::{AtomicUsize, Ordering},
        Arc,
    };

    use futures_util::sink;

    use super::*;

    fn identity(character: &str) -> Identity {
        Identity {
            context: RelayContext {
                session: "tf_1".into(),
                foreground: 1,
                connection: 1,
            },
            character: character.into(),
        }
    }

    fn active(generation: u64) -> Gate {
        let mut gate = Gate::new(generation);
        gate.snapshot(Some(identity("Carmen")));
        gate.status("live");
        gate
    }

    #[tokio::test]
    async fn queued_action_cannot_dispatch_after_replacement_or_foreground_aba() {
        for replace in [false, true] {
            let (tx, mut rx) = watch::channel(active(1));
            let ticket = tx.borrow().ticket().unwrap();
            let (release, queued) = tokio::sync::oneshot::channel();
            let writes = Arc::new(AtomicUsize::new(0));
            let count = writes.clone();
            let task = tokio::spawn(async move {
                let mut sink = sink::unfold(count, |count, _: Message| {
                    Box::pin(async move {
                        count.fetch_add(1, Ordering::Relaxed);
                        Ok::<_, std::io::Error>(count)
                    })
                });
                let mut issued = false;
                queued.await.unwrap();
                let result = dispatch(
                    &mut rx,
                    &ticket,
                    || false,
                    &mut sink,
                    "action".into(),
                    &mut issued,
                )
                .await;
                assert!(result.is_err());
                assert!(!issued);
            });
            if replace {
                tx.send_replace(active(2));
            } else {
                tx.send_modify(|gate| {
                    gate.snapshot(Some(identity("Inigo")));
                    gate.snapshot(Some(identity("Carmen")));
                });
            }
            release.send(()).unwrap();
            task.await.unwrap();
            assert_eq!(writes.load(Ordering::Relaxed), 0);
        }
    }

    #[test]
    fn delayed_result_cannot_commit_after_feed_loss_or_session_loss() {
        let mut gate = active(1);
        let ticket = gate.ticket().unwrap();
        let result =
            || Ok(json!({"type":"action-result","protocol":2,"status":"forwarded","detail":null}));
        assert_eq!(
            finish(&gate, &ticket, false, 1, true, result())["status"],
            "ok"
        );
        assert_eq!(
            finish(&gate, &ticket, true, 1, true, result())["delivery"],
            "unknown"
        );
        assert_eq!(
            finish(&active(2), &ticket, false, 1, true, result())["delivery"],
            "unknown"
        );
        gate.status("stale");
        gate.status("live");
        assert!(gate.ticket().is_none());
        gate.snapshot(Some(identity("Carmen")));
        let stale = finish(&gate, &ticket, false, 1, true, result());
        assert_eq!(stale["delivery"], "unknown");
        assert!(stale.get("result").is_none());
        assert!(gate.permits(&gate.ticket().unwrap(), false));
    }
}
