#!/usr/bin/env bash
set -euo pipefail

root=$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)
release=3a8b0c77f88a03fb41ebe908b402476c4e5dc0ee # v0.6.0
native=apps/desktop/src-tauri
fixture="$root/$native/tests/fixtures/structured-managed.json"

if ! git -C "$root" cat-file -e "$release^{commit}"; then
    echo 'Missing the v0.6.0 commit; fetch the v0.6.0 tag before running this check.' >&2
    exit 1
fi

scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
mkdir "$scratch/src"
# Compile the released decoder verbatim without bringing up Tauri or OpenSSH.
git -C "$root" show "$release:$native/src/tunnel_config.rs" > "$scratch/src/tunnel_config.rs"
git -C "$root" show "$release:$native/src/tunnel.rs" |
    grep '^pub const SSH_FORWARD_PORT:' > "$scratch/src/tunnel.rs"
git -C "$root" show "$release:$native/Cargo.lock" > "$scratch/Cargo.lock"

cat > "$scratch/Cargo.toml" <<'TOML'
[package]
name = "managed-ssh-downgrade-check"
version = "0.0.0"
edition = "2021"

[dependencies]
base64 = "0.22"
parking_lot = "0.12"
serde = { version = "1", features = ["derive"] }
serde_json = "1"
tempfile = "3"
url = "2"
TOML

cat > "$scratch/src/main.rs" <<'RUST'
mod tunnel;
#[allow(dead_code)]
mod tunnel_config;

use std::path::PathBuf;
use tunnel_config::{load, LoadedConfig, RuntimeConnectionMode, TunnelMode};

fn main() {
    let mut paths = std::env::args_os().skip(1).map(PathBuf::from);
    let legacy = paths.next().unwrap();
    let structured = paths.next().unwrap();
    let LoadedConfig::Configured(legacy) = load(&legacy) else {
        panic!("v0.6.0 must still accept a legacy Managed SSH config");
    };
    assert_eq!(legacy.mode, TunnelMode::Managed);
    assert_eq!(legacy.ssh_target, "avatar");
    assert!(legacy.runtime_connection_config().state_url.is_some());

    let before = std::fs::read(&structured).unwrap();
    let loaded = load(&structured);
    assert_eq!(loaded, LoadedConfig::Unavailable);
    let runtime = loaded.runtime_connection_config();
    assert_eq!(runtime.mode, RuntimeConnectionMode::Unavailable);
    assert!(runtime.state_url.is_none());
    assert!(runtime.authentication_token.is_none());
    assert_eq!(std::fs::read(&structured).unwrap(), before);
    println!("v0.6.0 loads the legacy alias and refuses the canonical structured configuration");
}
RUST

printf '%s\n' '{"mode":"managed","sshTarget":"avatar"}' > "$scratch/legacy.json"
cargo run --quiet --manifest-path "$scratch/Cargo.toml" -- "$scratch/legacy.json" "$fixture"
