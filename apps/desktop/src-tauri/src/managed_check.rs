//! Owns the explicit one-shot Managed SSH diagnostic, not the gameplay tunnel.

#[path = "managed_check/process.rs"]
pub(crate) mod process;
#[path = "managed_check/protocol.rs"]
mod protocol;

use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use parking_lot::{Condvar, Mutex};
use serde::Serialize;
use serde_json::Value;

use crate::tunnel_config::ManagedDestination;

const CHECK_BUDGET: Duration = Duration::from_secs(5);
const SHUTDOWN_BUDGET: Duration = CHECK_BUDGET.saturating_add(crate::runtime::DIAGNOSTIC_CLEANUP);
const MAX_REQUEST_ID: u64 = (1 << 53) - 1;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum CheckResult {
    InvalidDestination,
    SshUnavailable,
    Live,
    Stale,
    Down,
    NoUsableState,
    HostVerification,
    Authentication,
    ForwardingUnavailable,
    InvalidProtocol,
    Timeout,
    Cancelled,
    ConnectionFailed,
    Busy,
    RetirementUnconfirmed,
}

pub(crate) type CheckOutcome = Result<CheckResult, RetirementEvidence>;

// Retain only the original ownership handles until app exit. They are never used
// to find, signal, or adopt a replacement process.
pub(crate) enum RetirementEvidence {
    Child {
        _owner: crate::runtime::RuntimeOwner,
        _child: crate::runtime::OwnedChild,
    },
    #[cfg(unix)]
    Startup {
        _guardian: crate::runtime::StartupRetirementUnconfirmed,
    },
    WorkerLost,
}

#[derive(Default)]
pub(crate) struct ManagedCheck {
    state: Mutex<State>,
    cancelled: AtomicBool,
    retired: Condvar,
}

#[derive(Default)]
struct State {
    // The renderer allocates increasing IDs. Observing a newer start or cancel
    // retires any still-undelivered older starts without a set of tombstones.
    high_water: u64,
    active: Option<u64>,
    saves: usize,
    retirement_fault: Option<RetirementEvidence>,
    shutdown_deadline: Option<Instant>,
    shutdown_outcome: Option<ShutdownOutcome>,
}

impl State {
    fn is_idle(&self) -> bool {
        self.active.is_none() && self.saves == 0
    }

    fn shutdown_readiness(&self) -> Option<ShutdownOutcome> {
        if self.retirement_fault.is_some() {
            Some(ShutdownOutcome::RetirementUnconfirmed)
        } else if self.is_idle() {
            Some(ShutdownOutcome::Retired)
        } else {
            None
        }
    }
}

#[derive(Debug, PartialEq, Eq)]
pub(crate) enum ExitReadiness {
    Ready(ShutdownOutcome),
    NeedsWait,
    Waiting,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum ShutdownOutcome {
    Retired,
    RetirementUnconfirmed,
    TimedOut,
}

impl ShutdownOutcome {
    pub(crate) fn report(self) {
        match self {
            Self::Retired => {}
            Self::RetirementUnconfirmed => {
                eprintln!("imp: managed connection check retirement was not confirmed")
            }
            Self::TimedOut => eprintln!("imp: connection settings shutdown wait timed out"),
        }
    }
}

impl ManagedCheck {
    fn admit(self: &Arc<Self>, id: u64) -> Result<ActiveCheck, CheckResult> {
        let mut state = self.state.lock();
        if state.retirement_fault.is_some() {
            return Err(CheckResult::RetirementUnconfirmed);
        }
        if !(1..=MAX_REQUEST_ID).contains(&id) {
            return Err(CheckResult::InvalidDestination);
        }
        if state.shutdown_deadline.is_some() || id <= state.high_water {
            return Err(CheckResult::Cancelled);
        }
        state.high_water = id;
        if !state.is_idle() {
            return Err(CheckResult::Busy);
        }
        self.cancelled.store(false, Ordering::Release);
        state.active = Some(id);
        Ok(ActiveCheck(Some(Arc::clone(self))))
    }

    pub(crate) fn reserve_save(self: &Arc<Self>) -> Result<SaveReservation, &'static str> {
        let mut state = self.state.lock();
        if state.retirement_fault.is_some() {
            return Err("retirement_unconfirmed");
        }
        if state.shutdown_deadline.is_some() {
            return Err("Imp is closing. Reopen it before saving connection settings.");
        }
        if state.active.is_some() {
            return Err("Wait for the connection check to finish, then try saving again.");
        }
        // Admission under this lock orders saves and checks; disk I/O runs outside it.
        state.saves += 1;
        Ok(SaveReservation(Arc::clone(self)))
    }

    pub(crate) fn cancel(&self, id: u64) {
        if !(1..=MAX_REQUEST_ID).contains(&id) {
            return;
        }
        let mut state = self.state.lock();
        state.high_water = state.high_water.max(id);
        if state.active == Some(id) {
            self.cancelled.store(true, Ordering::Release);
        }
    }

    pub(crate) fn begin_shutdown(&self) -> ExitReadiness {
        let mut state = self.state.lock();
        let was_closing = state.shutdown_deadline.is_some();
        if !was_closing {
            state.shutdown_deadline = Some(Instant::now() + SHUTDOWN_BUDGET);
            self.cancelled.store(true, Ordering::Release);
        }
        if let Some(outcome) = state
            .shutdown_outcome
            .or_else(|| state.shutdown_readiness())
        {
            ExitReadiness::Ready(outcome)
        } else if was_closing {
            ExitReadiness::Waiting
        } else {
            ExitReadiness::NeedsWait
        }
    }

    pub(crate) fn shutdown(&self) -> ShutdownOutcome {
        self.begin_shutdown();
        let deadline = self.state.lock().shutdown_deadline.unwrap();
        self.shutdown_until(deadline)
    }

    fn shutdown_until(&self, deadline: Instant) -> ShutdownOutcome {
        let mut state = self.state.lock();
        let outcome = loop {
            if let Some(outcome) = state
                .shutdown_outcome
                .or_else(|| state.shutdown_readiness())
            {
                break outcome;
            }
            if self.retired.wait_until(&mut state, deadline).timed_out() {
                break state
                    .shutdown_readiness()
                    .unwrap_or(ShutdownOutcome::TimedOut);
            }
        };
        state.shutdown_outcome = Some(outcome);
        outcome
    }

    #[cfg(any(test, feature = "runtime-acceptance"))]
    #[allow(dead_code)] // Used by the acceptance harness's copy of this module.
    pub(crate) fn run_with(
        self: &Arc<Self>,
        id: u64,
        destination: Value,
        run: impl FnOnce(&ManagedDestination, Instant, &AtomicBool) -> CheckOutcome,
    ) -> CheckResult {
        let deadline = Instant::now() + CHECK_BUDGET;
        match self.admit(id) {
            Ok(active) => active.run(destination, deadline, run),
            Err(result) => result,
        }
    }
}

pub(crate) struct SaveReservation(Arc<ManagedCheck>);

impl Drop for SaveReservation {
    fn drop(&mut self) {
        let mut state = self.0.state.lock();
        state.saves -= 1;
        if state.is_idle() {
            self.0.retired.notify_all();
        }
    }
}

struct ActiveCheck(Option<Arc<ManagedCheck>>);

impl ActiveCheck {
    fn run(
        mut self,
        destination: Value,
        deadline: Instant,
        run: impl FnOnce(&ManagedDestination, Instant, &AtomicBool) -> CheckOutcome,
    ) -> CheckResult {
        let result = catch_unwind(AssertUnwindSafe(|| {
            let cancelled = &self.0.as_ref().unwrap().cancelled;
            if cancelled.load(Ordering::Acquire) {
                Ok(CheckResult::Cancelled)
            } else if let Some(destination) = ManagedDestination::from_check_input(destination) {
                run(&destination, deadline, cancelled)
            } else {
                Ok(CheckResult::InvalidDestination)
            }
        }))
        .unwrap_or(Err(RetirementEvidence::WorkerLost));
        self.finish(result)
    }

    fn finish(&mut self, result: CheckOutcome) -> CheckResult {
        let owner = self.0.take().expect("active check");
        let mut state = owner.state.lock();
        let response = match result {
            Err(evidence) => {
                state.retirement_fault = Some(evidence);
                CheckResult::RetirementUnconfirmed
            }
            Ok(_) if owner.cancelled.load(Ordering::Acquire) => CheckResult::Cancelled,
            Ok(result) => result,
        };
        state.active = None;
        owner.retired.notify_all();
        response
    }
}

impl Drop for ActiveCheck {
    fn drop(&mut self) {
        if self.0.is_some() {
            self.finish(Err(RetirementEvidence::WorkerLost));
        }
    }
}

#[tauri::command]
pub(crate) async fn check_managed_connection(
    request_id: Value,
    destination: Value,
    check: tauri::State<'_, Arc<ManagedCheck>>,
) -> Result<CheckResult, ()> {
    let deadline = Instant::now() + CHECK_BUDGET;
    let active = match check.inner().admit(request_id.as_u64().unwrap_or(0)) {
        Ok(active) => active,
        Err(result) => return Ok(result),
    };
    Ok(tauri::async_runtime::spawn_blocking(move || {
        active.run(destination, deadline, process::run)
    })
    .await
    .unwrap_or(CheckResult::RetirementUnconfirmed))
}

#[tauri::command]
pub(crate) fn cancel_managed_connection(
    request_id: Value,
    check: tauri::State<'_, Arc<ManagedCheck>>,
) {
    if let Some(id) = request_id.as_u64() {
        check.cancel(id);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::sync::mpsc;
    use std::thread;

    fn alias() -> Value {
        json!({"sshTarget": "  synthetic-alias  "})
    }

    #[test]
    fn check_admits_only_valid_destinations_and_safe_ids() {
        let check = Arc::new(ManagedCheck::default());
        let invalid = [
            json!({}),
            json!("host"),
            json!(["host"]),
            json!({"sshTarget": null}),
            json!({"sshTarget": " "}),
            json!({"sshTarget": "-oProxyCommand=x"}),
            json!({"sshTarget": "ssh://transit.invalid"}),
            json!({"sshTarget": "imp@extra@example.invalid"}),
            json!({"sshTarget": "imp@-oProxyCommand=x"}),
            json!({"sshTarget": "server *"}),
            json!({"sshTarget": "x", "sshDestination": {"host": "example.invalid", "username": "imp"}}),
            json!({"sshTarget": "x", "remoteUrl": "ws://example.invalid/action"}),
            json!({"sshDestination": {"host": "example.invalid:22", "username": "imp"}}),
            json!({"sshDestination": {"host": "example.invalid", "username": "imp", "port": 0}}),
            json!({"sshDestination": {"host": "example.invalid", "username": "imp", "port": null}}),
        ];
        let mut id = 1;
        for destination in invalid {
            assert_eq!(
                check.run_with(id, destination, |_, _, _| panic!(
                    "invalid input reached SSH"
                )),
                CheckResult::InvalidDestination
            );
            id += 1;
        }
        for invalid_id in [0, MAX_REQUEST_ID + 1] {
            check.cancel(invalid_id);
            assert_eq!(
                check.run_with(invalid_id, alias(), |_, _, _| panic!(
                    "invalid ID reached SSH"
                )),
                CheckResult::InvalidDestination
            );
        }
        assert_eq!(
            check.run_with(id, alias(), |destination, _, _| {
                assert_eq!(
                    destination,
                    &ManagedDestination::Alias("synthetic-alias".into())
                );
                Ok(CheckResult::Live)
            }),
            CheckResult::Live
        );
        for (id, target) in [(id + 1, "imp@example.invalid"), (id + 2, "imp@2001:db8::1")] {
            assert_eq!(
                check.run_with(id, json!({"sshTarget": target}), |destination, _, _| {
                    assert_eq!(destination, &ManagedDestination::Alias(target.into()));
                    Ok(CheckResult::Stale)
                }),
                CheckResult::Stale
            );
        }
        assert_eq!(
            check.run_with(
                id + 3,
                json!({"sshDestination": {"host": "2001:db8::1", "username": "imp", "port": 2222}}),
                |destination, _, _| {
                    let ManagedDestination::Server(server) = destination else {
                        panic!("not a server login");
                    };
                    assert_eq!(server.host, "2001:db8::1");
                    assert_eq!(server.username, "imp");
                    assert_eq!(server.port, Some(2222));
                    Ok(CheckResult::Down)
                },
            ),
            CheckResult::Down
        );
    }

    #[test]
    fn early_or_queued_cancellation_never_invokes_process() {
        let check = Arc::new(ManagedCheck::default());
        check.cancel(1);
        let unexpected = |_: &ManagedDestination, _: Instant, _: &AtomicBool| {
            panic!("cancelled check reached SSH")
        };
        assert_eq!(
            check.run_with(1, alias(), unexpected),
            CheckResult::Cancelled
        );
        let queued = check.admit(2).unwrap();
        check.cancel(2);
        assert_eq!(
            queued.run(alias(), Instant::now(), unexpected),
            CheckResult::Cancelled
        );
        assert_eq!(
            check.run_with(3, alias(), |_, _, _| Ok(CheckResult::Live)),
            CheckResult::Live
        );
    }

    #[test]
    fn cancellation_keeps_reservation_until_worker_exits() {
        let check = Arc::new(ManagedCheck::default());
        let other = Arc::clone(&check);
        let (entered, started) = mpsc::channel();
        let (release, proceed) = mpsc::channel();
        let worker = thread::spawn(move || {
            other.run_with(1, alias(), |_, _, cancelled| {
                entered.send(()).unwrap();
                proceed.recv().unwrap();
                assert!(cancelled.load(Ordering::Acquire));
                Ok(CheckResult::Live)
            })
        });
        started.recv().unwrap();
        check.cancel(1);
        assert_eq!(
            check.run_with(2, alias(), |_, _, _| panic!(
                "overlapping check reached SSH"
            )),
            CheckResult::Busy
        );
        assert!(check.reserve_save().is_err());
        release.send(()).unwrap();
        assert_eq!(worker.join().unwrap(), CheckResult::Cancelled);
        drop(check.reserve_save().unwrap());
        assert_eq!(
            check.run_with(2, alias(), |_, _, _| panic!("retired ID reached SSH")),
            CheckResult::Cancelled
        );
        assert_eq!(
            check.run_with(3, alias(), |_, _, cancelled| {
                check.cancel(1);
                check.cancel(4);
                assert!(!cancelled.load(Ordering::Acquire));
                Ok(CheckResult::Live)
            }),
            CheckResult::Live
        );
        assert_eq!(
            check.run_with(4, alias(), |_, _, _| panic!("future cancellation was lost")),
            CheckResult::Cancelled
        );
    }

    #[test]
    fn admitted_saves_exclude_a_check_until_all_saves_finish() {
        let check = Arc::new(ManagedCheck::default());
        let first = check.reserve_save().unwrap();
        let second = check.reserve_save().unwrap();
        assert_eq!(check.admit(1).err(), Some(CheckResult::Busy));
        drop(first);
        assert_eq!(check.admit(2).err(), Some(CheckResult::Busy));
        drop(second);
        let mut active = check.admit(3).unwrap();
        assert!(check.reserve_save().is_err());
        assert_eq!(active.finish(Ok(CheckResult::Live)), CheckResult::Live);
        assert!(check.reserve_save().is_ok());
    }

    #[test]
    fn shutdown_rejects_new_saves_and_waits_for_admitted_saves() {
        let check = Arc::new(ManagedCheck::default());
        let saving = check.reserve_save().unwrap();
        let other = Arc::clone(&check);
        let (closing, started) = mpsc::channel();
        let waiter = thread::spawn(move || {
            closing.send(other.begin_shutdown()).unwrap();
            assert_eq!(other.shutdown(), ShutdownOutcome::Retired);
        });
        assert_eq!(started.recv().unwrap(), ExitReadiness::NeedsWait);
        assert!(check.reserve_save().is_err());
        drop(saving);
        waiter.join().unwrap();
        assert!(check.reserve_save().is_err());
    }

    #[test]
    fn shutdown_signals_active_check_and_waits_for_retirement() {
        let check = Arc::new(ManagedCheck::default());
        let mut active = check.admit(1).unwrap();
        let other = Arc::clone(&check);
        let (closing, started) = mpsc::channel();
        let waiter = thread::spawn(move || {
            closing.send(other.begin_shutdown()).unwrap();
            assert_eq!(other.shutdown(), ShutdownOutcome::Retired);
        });
        assert_eq!(started.recv().unwrap(), ExitReadiness::NeedsWait);
        assert!(check.cancelled.load(Ordering::Acquire));
        assert_eq!(check.begin_shutdown(), ExitReadiness::Waiting);
        assert_eq!(
            check.run_with(2, alias(), |_, _, _| panic!(
                "check reached SSH during shutdown"
            )),
            CheckResult::Cancelled
        );
        assert_eq!(active.finish(Ok(CheckResult::Live)), CheckResult::Cancelled);
        waiter.join().unwrap();
        assert_eq!(
            check.begin_shutdown(),
            ExitReadiness::Ready(ShutdownOutcome::Retired)
        );
    }

    #[test]
    fn shutdown_timeout_does_not_claim_an_admitted_save_retired() {
        let check = Arc::new(ManagedCheck::default());
        let saving = check.reserve_save().unwrap();
        assert_eq!(check.begin_shutdown(), ExitReadiness::NeedsWait);
        assert_eq!(
            check.shutdown_until(Instant::now()),
            ShutdownOutcome::TimedOut
        );
        assert_eq!(
            check.begin_shutdown(),
            ExitReadiness::Ready(ShutdownOutcome::TimedOut)
        );
        drop(saving);
    }

    #[test]
    fn shutdown_timeout_does_not_claim_an_active_check_retired() {
        let check = Arc::new(ManagedCheck::default());
        let active = check.admit(1).unwrap();
        assert_eq!(check.begin_shutdown(), ExitReadiness::NeedsWait);
        assert_eq!(
            check.shutdown_until(Instant::now()),
            ShutdownOutcome::TimedOut
        );
        assert_eq!(
            check.begin_shutdown(),
            ExitReadiness::Ready(ShutdownOutcome::TimedOut)
        );
        drop(active);
    }

    #[test]
    fn worker_panic_or_drop_without_settlement_blocks_checks_and_saves() {
        for panic in [false, true] {
            let check = Arc::new(ManagedCheck::default());
            if panic {
                let result = check.run_with(1, alias(), |_, _, _| {
                    check.cancel(1);
                    panic!("worker lost before settlement");
                });
                assert_eq!(result, CheckResult::RetirementUnconfirmed);
            } else {
                let active = check.admit(1).unwrap();
                check.cancel(1);
                drop(active);
            }
            assert_eq!(
                check.admit(2).err(),
                Some(CheckResult::RetirementUnconfirmed)
            );
            assert_eq!(check.reserve_save().err(), Some("retirement_unconfirmed"));
            assert_eq!(check.shutdown(), ShutdownOutcome::RetirementUnconfirmed);
        }
    }
}
