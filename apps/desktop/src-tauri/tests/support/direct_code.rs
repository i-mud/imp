//! Native phases of the authenticated Direct connection-code fixture
//! (`scripts/test-direct-code-e2e.py`): `direct-code PHASE CONFIG ENDPOINT`, CONFIG being the fixture's
//! disposable `tunnel.json`. Secrets arrive on stdin and leave only on stdout; failures print a fixed
//! category, never a value. Refusal cases stay in the `tunnel_config` unit tests.

use std::ffi::OsString;
use std::io::Read;
use std::path::Path;

use serde_json::json;

use crate::tunnel_config::{
    load, ConnectionConfigStore, ConnectionSettingsUpdate, LoadedConfig, RuntimeConnectionMode,
};

fn check(condition: bool, category: &str) {
    if !condition {
        eprintln!("E2E-FAIL {category}");
        std::process::exit(1);
    }
}

/// Starts unconfigured, saves, and reports the startup runtime, which must not change before a restart.
fn save(path: &Path, endpoint: &str, by_code: bool) {
    let mut input = String::new();
    check(std::io::stdin().read_to_string(&mut input).is_ok(), "stdin");
    let lines: Vec<&str> = input.lines().collect();
    check(lines.len() == if by_code { 1 } else { 2 }, "stdin shape");

    let running = load(path);
    check(
        matches!(running, LoadedConfig::Unconfigured),
        "not unconfigured at start",
    );
    let startup = running.runtime_connection_config();
    let store = ConnectionConfigStore::new(path.to_path_buf(), running);

    let update = if by_code {
        json!({"mode": "direct", "connectionCode": lines[0]})
    } else {
        json!({"mode": "direct", "remoteUrl": lines[0], "pairingToken": lines[1]})
    };
    let update = serde_json::from_value::<ConnectionSettingsUpdate>(update);
    check(update.is_ok(), "update shape");
    let saved = store.save(update.unwrap_or_else(|_| unreachable!()));
    check(saved.is_ok(), "native save refused");
    let saved = saved.unwrap_or_else(|_| unreachable!());

    check(
        saved.pending_restart && saved.running.is_none(),
        "save did not wait for restart",
    );
    check(
        saved
            .saved
            .is_some_and(|s| s.remote_url == endpoint && s.has_pairing_token),
        "saved settings differ from the issued endpoint",
    );
    println!(
        "E2E-RUNNING {}",
        serde_json::to_string(&startup).unwrap_or_default()
    );
}

/// A new process stands for the restart: only the saved file decides the runtime transport. The runtime
/// config is released only when it is exactly the endpoint argument and the token expected on stdin.
fn restart(path: &Path, endpoint: &str) {
    let mut expected = String::new();
    check(
        std::io::stdin().read_to_string(&mut expected).is_ok(),
        "stdin",
    );
    let runtime = load(path).runtime_connection_config();
    check(
        matches!(runtime.mode, RuntimeConnectionMode::Direct)
            && runtime.state_url.as_deref() == Some(endpoint)
            && runtime.authentication_token.as_deref() == Some(expected.trim_end_matches('\n')),
        "restarted runtime differs from the expected endpoint and token",
    );
    println!(
        "E2E-RUNTIME {}",
        serde_json::to_string(&runtime).unwrap_or_default()
    );
}

pub fn run(args: &[OsString]) {
    check(args.len() == 3, "arguments");
    let (path, endpoint) = (Path::new(&args[1]), args[2].to_str().unwrap_or_default());
    match args[0].to_str().unwrap_or_default() {
        "save-code" => save(path, endpoint, true),
        "save-manual" => save(path, endpoint, false),
        "restart" => restart(path, endpoint),
        _ => check(false, "unknown phase"),
    }
}
