//! Runtime configuration for the desktop transport.
//!
//! The historical file remains `tunnel.json` for compatibility. External and
//! managed modes retain the existing SSH behaviour. Direct mode adds a WSS
//! state URL plus one pairing token owned by native application config.

use std::fmt;
use std::path::Path;

use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use serde::{Deserialize, Serialize};
use url::Url;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum TunnelMode {
    #[default]
    External,
    Managed,
    Direct,
}

#[derive(Clone, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct TunnelConfig {
    #[serde(default)]
    pub mode: TunnelMode,
    /// An existing `Host` alias from the user's `~/.ssh/config`. Required
    /// when `mode` is `managed`.
    #[serde(default)]
    pub ssh_target: String,
    /// Direct-WSS state endpoint. Required when `mode` is `direct`.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub remote_url: String,
    /// One canonical 256-bit unpadded base64url pairing token. Required when
    /// `mode` is `direct`.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub pairing_token: String,
}

impl fmt::Debug for TunnelConfig {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("TunnelConfig")
            .field("mode", &self.mode)
            .field("ssh_target", &self.ssh_target)
            .field("remote_url", &self.remote_url)
            .field(
                "pairing_token",
                &if self.pairing_token.is_empty() {
                    ""
                } else {
                    "<redacted>"
                },
            )
            .finish()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RuntimeConnectionMode {
    Local,
    Direct,
}

/// The only transport configuration exposed to the renderer.
///
/// SSH target/process information never crosses this boundary. The plaintext
/// pairing token crosses only for Direct WSS because the WebView must send the
/// first authentication frame.
#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeConnectionConfig {
    pub mode: RuntimeConnectionMode,
    pub state_url: Option<String>,
    pub authentication_token: Option<String>,
}

impl TunnelConfig {
    pub fn runtime_connection_config(&self) -> RuntimeConnectionConfig {
        match self.mode {
            TunnelMode::External | TunnelMode::Managed => RuntimeConnectionConfig {
                mode: RuntimeConnectionMode::Local,
                state_url: None,
                authentication_token: None,
            },
            TunnelMode::Direct => RuntimeConnectionConfig {
                mode: RuntimeConnectionMode::Direct,
                state_url: Some(self.remote_url.clone()),
                authentication_token: Some(self.pairing_token.clone()),
            },
        }
    }
}

fn validate(config: &TunnelConfig) -> Result<(), &'static str> {
    if config.mode != TunnelMode::Direct {
        return Ok(());
    }

    let url = Url::parse(&config.remote_url).map_err(|_| "direct remote URL is invalid")?;

    if url.scheme() != "wss" {
        return Err("direct remote URL must use wss");
    }
    if url.host_str().is_none() {
        return Err("direct remote URL must include a host");
    }
    if !url.username().is_empty() || url.password().is_some() {
        return Err("direct remote URL must not contain credentials");
    }
    if url.query().is_some() || url.fragment().is_some() {
        return Err("direct remote URL must not contain query or fragment data");
    }
    if url
        .path_segments()
        .and_then(|mut segments| segments.next_back())
        != Some("state")
    {
        return Err("direct remote URL must end at /state");
    }

    let decoded = URL_SAFE_NO_PAD
        .decode(config.pairing_token.as_bytes())
        .map_err(|_| "direct pairing token is invalid")?;

    if decoded.len() != 32 || URL_SAFE_NO_PAD.encode(&decoded) != config.pairing_token {
        return Err("direct pairing token must be canonical 256-bit base64url");
    }

    Ok(())
}

/// Reads `path`, or writes and returns the external-mode default the first
/// time so the file is discoverable and documents its own shape. A malformed
/// or invalid file falls back to the safe external default rather than
/// blocking startup or starting a partially configured direct transport.
pub fn load_or_init(path: &Path) -> TunnelConfig {
    if let Ok(text) = std::fs::read_to_string(path) {
        return match serde_json::from_str::<TunnelConfig>(&text) {
            Ok(config) => match validate(&config) {
                Ok(()) => config,
                Err(reason) => {
                    eprintln!(
                        "tinyscry: invalid tunnel config at {}: {reason}; using external mode",
                        path.display()
                    );
                    TunnelConfig::default()
                }
            },
            Err(error) => {
                eprintln!(
                    "tinyscry: invalid tunnel config at {}: {error}; using external mode",
                    path.display()
                );
                TunnelConfig::default()
            }
        };
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

    const VALID_TOKEN: &str = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";

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
                ssh_target: "avatar".into(),
                remote_url: String::new(),
                pairing_token: String::new(),
            }
        );
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn valid_direct_mode_round_trips_and_builds_runtime_config() {
        let dir = tempdir();
        let path = dir.join("tunnel.json");
        std::fs::write(
            &path,
            format!(
                r#"{{"mode":"direct","remoteUrl":"wss://tinyscry.example/state","pairingToken":"{VALID_TOKEN}"}}"#
            ),
        )
        .unwrap();

        let config = load_or_init(&path);

        assert_eq!(config.mode, TunnelMode::Direct);
        assert_eq!(config.remote_url, "wss://tinyscry.example/state");
        assert_eq!(config.pairing_token, VALID_TOKEN);

        let runtime = config.runtime_connection_config();
        assert_eq!(runtime.mode, RuntimeConnectionMode::Direct);
        assert_eq!(
            runtime.state_url.as_deref(),
            Some("wss://tinyscry.example/state")
        );
        assert_eq!(runtime.authentication_token.as_deref(), Some(VALID_TOKEN));

        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn direct_mode_accepts_reverse_proxy_path_prefix() {
        let dir = tempdir();
        let path = dir.join("tunnel.json");
        std::fs::write(
            &path,
            format!(
                r#"{{"mode":"direct","remoteUrl":"wss://tinyscry.example/tinyscry/state","pairingToken":"{VALID_TOKEN}"}}"#
            ),
        )
        .unwrap();

        let config = load_or_init(&path);

        assert_eq!(config.mode, TunnelMode::Direct);
        assert_eq!(config.remote_url, "wss://tinyscry.example/tinyscry/state");

        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn direct_mode_rejects_insecure_ws() {
        let dir = tempdir();
        let path = dir.join("tunnel.json");
        std::fs::write(
            &path,
            format!(
                r#"{{"mode":"direct","remoteUrl":"ws://tinyscry.example/state","pairingToken":"{VALID_TOKEN}"}}"#
            ),
        )
        .unwrap();

        assert_eq!(load_or_init(&path), TunnelConfig::default());
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn direct_mode_rejects_privileged_or_ambiguous_url_shapes() {
        let dir = tempdir();

        for remote_url in [
            "wss://tinyscry.example/ingest",
            "wss://user@tinyscry.example/state",
            "wss://tinyscry.example/state?token=nope",
            "wss://tinyscry.example/state#fragment",
        ] {
            let path = dir.join("tunnel.json");
            std::fs::write(
                &path,
                format!(
                    r#"{{"mode":"direct","remoteUrl":"{remote_url}","pairingToken":"{VALID_TOKEN}"}}"#
                ),
            )
            .unwrap();

            assert_eq!(load_or_init(&path), TunnelConfig::default());
        }

        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn direct_mode_rejects_noncanonical_token() {
        let dir = tempdir();
        let path = dir.join("tunnel.json");
        std::fs::write(
            &path,
            r#"{"mode":"direct","remoteUrl":"wss://tinyscry.example/state","pairingToken":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAB"}"#,
        )
        .unwrap();

        assert_eq!(load_or_init(&path), TunnelConfig::default());
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn local_runtime_config_exposes_no_ssh_or_auth_material() {
        let config = TunnelConfig {
            mode: TunnelMode::Managed,
            ssh_target: "avatar".into(),
            remote_url: String::new(),
            pairing_token: String::new(),
        };

        let runtime = config.runtime_connection_config();
        let json = serde_json::to_value(runtime).unwrap();

        assert_eq!(json["mode"], "local");
        assert_eq!(json["stateUrl"], serde_json::Value::Null);
        assert_eq!(json["authenticationToken"], serde_json::Value::Null);
        assert!(!json.to_string().contains("avatar"));
    }

    #[test]
    fn debug_output_redacts_pairing_token() {
        let config = TunnelConfig {
            mode: TunnelMode::Direct,
            ssh_target: String::new(),
            remote_url: "wss://tinyscry.example/state".into(),
            pairing_token: VALID_TOKEN.into(),
        };

        let debug = format!("{config:?}");

        assert!(!debug.contains(VALID_TOKEN));
        assert!(debug.contains("<redacted>"));
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
