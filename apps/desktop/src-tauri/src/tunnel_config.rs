//! Runtime configuration for the desktop tunnel supervisor.
//!
//! Deliberately small: an existing SSH `Host` alias plus a mode selector, not
//! a reimplementation of hostname/user/identity/proxy that `~/.ssh/config`
//! already expresses. Defaults to `external` so an unconfigured install keeps
//! today's manual-tunnel behaviour rather than spawning an SSH child.

use std::path::Path;

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum TunnelMode {
    #[default]
    External,
    Managed,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct TunnelConfig {
    #[serde(default)]
    pub mode: TunnelMode,
    /// An existing `Host` alias from the user's `~/.ssh/config`. Required
    /// when `mode` is `managed`.
    #[serde(default)]
    pub ssh_target: String,
}

/// Reads `path`, or writes and returns the external-mode default the first
/// time so the file is discoverable and documents its own shape. A malformed
/// file falls back to the safe default rather than blocking startup.
pub fn load_or_init(path: &Path) -> TunnelConfig {
    if let Ok(text) = std::fs::read_to_string(path) {
        return serde_json::from_str(&text).unwrap_or_else(|error| {
            eprintln!(
                "tinyscry: invalid tunnel config at {}: {error}; using external mode",
                path.display()
            );
            TunnelConfig::default()
        });
    }
    let config = TunnelConfig::default();
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if let Ok(text) = serde_json::to_string_pretty(&config) {
        let _ = std::fs::write(path, text);
    }
    config
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_file_defaults_to_external_and_is_written() {
        let dir = tempdir();
        let path = dir.join("tunnel.json");

        let config = load_or_init(&path);

        assert_eq!(config.mode, TunnelMode::External);
        let written = std::fs::read_to_string(&path).unwrap();
        assert!(written.contains("\"external\""));
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn malformed_file_falls_back_to_default_without_panicking() {
        let dir = tempdir();
        let path = dir.join("tunnel.json");
        std::fs::write(&path, "{not json").unwrap();

        let config = load_or_init(&path);

        assert_eq!(config, TunnelConfig::default());
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn managed_mode_round_trips() {
        let dir = tempdir();
        let path = dir.join("tunnel.json");
        std::fs::write(&path, r#"{"mode":"managed","sshTarget":"avatar"}"#).unwrap();

        let config = load_or_init(&path);

        assert_eq!(
            config,
            TunnelConfig {
                mode: TunnelMode::Managed,
                ssh_target: "avatar".into()
            }
        );
        std::fs::remove_dir_all(&dir).ok();
    }

    fn tempdir() -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "tinyscry-tunnel-config-test-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }
}
