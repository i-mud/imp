//! Producer-side remote-access configuration for a desktop-local Imp node.
//!
//! This is intentionally separate from `tunnel.json`:
//! - `tunnel.json` selects the node this desktop consumes.
//! - `remote-access.json` controls whether other Imp clients may consume this
//!   desktop's local node through the authenticated gateway.

use std::fmt;
use std::io::Write;
use std::path::Path;

use serde::{Deserialize, Serialize};
use tempfile::NamedTempFile;

#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;

use crate::tunnel::{NODE_PORT, SSH_FORWARD_PORT};

pub const DEFAULT_GATEWAY_PORT: u16 = 8788;

#[derive(Clone, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct RemoteAccessConfig {
    #[serde(default)]
    pub wss_enabled: bool,
    #[serde(default = "default_gateway_port")]
    pub gateway_port: u16,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub pairing_token_sha256: String,
}

impl Default for RemoteAccessConfig {
    fn default() -> Self {
        Self {
            wss_enabled: false,
            gateway_port: DEFAULT_GATEWAY_PORT,
            pairing_token_sha256: String::new(),
        }
    }
}

impl fmt::Debug for RemoteAccessConfig {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("RemoteAccessConfig")
            .field("wss_enabled", &self.wss_enabled)
            .field("gateway_port", &self.gateway_port)
            .field(
                "pairing_token_sha256",
                &if self.pairing_token_sha256.is_empty() {
                    ""
                } else {
                    "<redacted>"
                },
            )
            .finish()
    }
}

const fn default_gateway_port() -> u16 {
    DEFAULT_GATEWAY_PORT
}

impl RemoteAccessConfig {
    fn normalized(&self) -> Result<Self, &'static str> {
        if self.gateway_port == 0 {
            return Err("remote gateway port must be in 1..65535");
        }
        if self.gateway_port == NODE_PORT || self.gateway_port == SSH_FORWARD_PORT {
            return Err(
                "remote gateway port must differ from the local node and SSH forward ports",
            );
        }

        let pairing_token_sha256 = if self.pairing_token_sha256.is_empty() {
            if self.wss_enabled {
                return Err("enabled remote WSS requires a pairing-token digest");
            }
            String::new()
        } else {
            normalize_digest(&self.pairing_token_sha256)
                .ok_or("pairing-token digest must be exactly 64 hexadecimal characters")?
        };

        Ok(Self {
            wss_enabled: self.wss_enabled,
            gateway_port: self.gateway_port,
            pairing_token_sha256,
        })
    }
}

fn normalize_digest(value: &str) -> Option<String> {
    if value.len() != 64 || !value.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return None;
    }

    Some(value.to_ascii_lowercase())
}

fn persist_config(path: &Path, config: &RemoteAccessConfig) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| "remote-access settings path has no parent directory".to_owned())?;

    std::fs::create_dir_all(parent)
        .map_err(|error| format!("failed to create remote-access settings directory: {error}"))?;

    let mut staged = NamedTempFile::new_in(parent)
        .map_err(|error| format!("failed to stage remote-access settings: {error}"))?;

    #[cfg(unix)]
    staged
        .as_file()
        .set_permissions(std::fs::Permissions::from_mode(0o600))
        .map_err(|error| format!("failed to secure staged remote-access settings: {error}"))?;

    let mut payload = serde_json::to_vec_pretty(config)
        .map_err(|error| format!("failed to serialize remote-access settings: {error}"))?;
    payload.push(b'\n');

    staged
        .write_all(&payload)
        .map_err(|error| format!("failed to write remote-access settings: {error}"))?;
    staged
        .flush()
        .map_err(|error| format!("failed to flush remote-access settings: {error}"))?;
    staged
        .as_file()
        .sync_all()
        .map_err(|error| format!("failed to sync remote-access settings: {error}"))?;

    staged
        .persist(path)
        .map_err(|error| format!("failed to replace remote-access settings: {}", error.error))?;

    Ok(())
}

/// Reads the producer-side remote-access configuration.
///
/// Missing configuration is initialized to the safe disabled default. Invalid
/// existing configuration is left untouched on disk but ignored for this
/// process, which therefore starts with remote WSS disabled.
pub fn load_or_init(path: &Path) -> RemoteAccessConfig {
    match std::fs::read_to_string(path) {
        Ok(text) => match serde_json::from_str::<RemoteAccessConfig>(&text) {
            Ok(config) => match config.normalized() {
                Ok(config) => config,
                Err(reason) => {
                    eprintln!(
                        "imp: invalid remote-access config at {}: {reason}; remote WSS disabled",
                        path.display()
                    );
                    RemoteAccessConfig::default()
                }
            },
            Err(error) => {
                eprintln!(
                    "imp: invalid remote-access config at {}: {error}; remote WSS disabled",
                    path.display()
                );
                RemoteAccessConfig::default()
            }
        },
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            let config = RemoteAccessConfig::default();
            if let Err(error) = persist_config(path, &config) {
                eprintln!(
                    "imp: could not initialize remote-access config at {}: {error}",
                    path.display()
                );
            }
            config
        }
        Err(error) => {
            eprintln!(
                "imp: could not read remote-access config at {}: {error}; remote WSS disabled",
                path.display()
            );
            RemoteAccessConfig::default()
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    use tempfile::tempdir;

    const DIGEST_UPPER: &str = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";
    const DIGEST_LOWER: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

    #[test]
    fn default_remote_access_is_disabled() {
        assert_eq!(
            RemoteAccessConfig::default(),
            RemoteAccessConfig {
                wss_enabled: false,
                gateway_port: 8788,
                pairing_token_sha256: String::new(),
            }
        );
    }

    #[test]
    fn enabled_wss_requires_pairing_token_digest() {
        let config = RemoteAccessConfig {
            wss_enabled: true,
            gateway_port: 8788,
            pairing_token_sha256: String::new(),
        };

        assert_eq!(
            config.normalized().unwrap_err(),
            "enabled remote WSS requires a pairing-token digest"
        );
    }

    #[test]
    fn digest_is_validated_and_canonicalized_to_lowercase() {
        let config = RemoteAccessConfig {
            wss_enabled: true,
            gateway_port: 8788,
            pairing_token_sha256: DIGEST_UPPER.into(),
        };

        let normalized = config.normalized().unwrap();

        assert_eq!(normalized.pairing_token_sha256, DIGEST_LOWER);
    }

    #[test]
    fn invalid_digest_is_rejected_even_while_disabled() {
        let config = RemoteAccessConfig {
            wss_enabled: false,
            gateway_port: 8788,
            pairing_token_sha256: "not-a-digest".into(),
        };

        assert_eq!(
            config.normalized().unwrap_err(),
            "pairing-token digest must be exactly 64 hexadecimal characters"
        );
    }

    #[test]
    fn gateway_port_must_be_nonzero_and_distinct_from_reserved_ports() {
        let zero = RemoteAccessConfig {
            gateway_port: 0,
            ..RemoteAccessConfig::default()
        };
        assert_eq!(
            zero.normalized().unwrap_err(),
            "remote gateway port must be in 1..65535"
        );

        let node_port = RemoteAccessConfig {
            gateway_port: NODE_PORT,
            ..RemoteAccessConfig::default()
        };
        assert_eq!(
            node_port.normalized().unwrap_err(),
            "remote gateway port must differ from the local node and SSH forward ports"
        );

        let ssh_forward_port = RemoteAccessConfig {
            gateway_port: SSH_FORWARD_PORT,
            ..RemoteAccessConfig::default()
        };
        assert_eq!(
            ssh_forward_port.normalized().unwrap_err(),
            "remote gateway port must differ from the local node and SSH forward ports"
        );
    }

    #[test]
    fn missing_config_is_initialized_disabled() {
        let directory = tempdir().unwrap();
        let path = directory.path().join("remote-access.json");

        let config = load_or_init(&path);

        assert_eq!(config, RemoteAccessConfig::default());
        let stored: RemoteAccessConfig =
            serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
        assert_eq!(stored, RemoteAccessConfig::default());
    }

    #[test]
    fn invalid_existing_config_falls_back_disabled_without_replacing_file() {
        let directory = tempdir().unwrap();
        let path = directory.path().join("remote-access.json");
        let invalid = r#"{"wssEnabled":true,"gatewayPort":8788}"#;
        std::fs::write(&path, invalid).unwrap();

        let config = load_or_init(&path);

        assert_eq!(config, RemoteAccessConfig::default());
        assert_eq!(std::fs::read_to_string(path).unwrap(), invalid);
    }

    #[test]
    fn persistence_round_trips_enabled_config() {
        let directory = tempdir().unwrap();
        let path = directory.path().join("remote-access.json");
        let config = RemoteAccessConfig {
            wss_enabled: true,
            gateway_port: 8788,
            pairing_token_sha256: DIGEST_LOWER.into(),
        };

        persist_config(&path, &config).unwrap();

        let stored: RemoteAccessConfig =
            serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
        assert_eq!(stored, config);
    }
}
