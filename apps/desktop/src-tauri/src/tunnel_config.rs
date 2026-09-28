//! Runtime configuration for the desktop transport.
//!
//! The historical file remains `tunnel.json` for compatibility. External and
//! managed modes retain the existing SSH behaviour. Direct mode adds a WSS
//! state URL plus one pairing token owned by native application config.

use std::fmt;
use std::io::Write;
use std::path::{Path, PathBuf};

use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use parking_lot::Mutex;
use serde::{Deserialize, Serialize};
use tempfile::NamedTempFile;
use url::Url;

#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;

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
    #[serde(default, skip_serializing_if = "String::is_empty")]
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

/// Native connection settings safe to show in the settings UI.
///
/// The persisted Direct-WSS pairing token is deliberately projected to a
/// boolean. Reading settings must never return the existing plaintext token
/// merely to populate a form.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ConnectionSettings {
    pub mode: TunnelMode,
    pub ssh_target: String,
    pub remote_url: String,
    pub has_pairing_token: bool,
}

/// One requested settings update.
///
/// `pairing_token: None` means "preserve the existing Direct token" and is
/// valid only when the currently persisted mode already has one. A supplied
/// string is always validated as a replacement; an empty string is not a
/// preserve sentinel.
#[derive(Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ConnectionSettingsUpdate {
    pub mode: TunnelMode,
    #[serde(default)]
    pub ssh_target: String,
    #[serde(default)]
    pub remote_url: String,
    #[serde(default)]
    pub pairing_token: Option<String>,
}

type PersistConfig = fn(&Path, &TunnelConfig) -> Result<(), String>;

/// Owns the persisted desktop transport configuration.
///
/// Runtime transport selection remains immutable for the current application
/// process. Saving through this store changes only the next-start
/// configuration.
pub struct ConnectionConfigStore {
    path: PathBuf,
    config: Mutex<TunnelConfig>,
    persist: PersistConfig,
}

impl ConnectionConfigStore {
    pub fn new(path: PathBuf, config: TunnelConfig) -> Self {
        Self {
            path,
            config: Mutex::new(config),
            persist: persist_config,
        }
    }

    #[cfg(test)]
    fn with_persist(path: PathBuf, config: TunnelConfig, persist: PersistConfig) -> Self {
        Self {
            path,
            config: Mutex::new(config),
            persist,
        }
    }

    pub fn settings(&self) -> ConnectionSettings {
        self.config.lock().connection_settings()
    }

    pub fn save(&self, update: ConnectionSettingsUpdate) -> Result<ConnectionSettings, String> {
        let mut current = self.config.lock();
        let updated = current.updated(update).map_err(str::to_owned)?;

        // Do not replace the in-memory configuration unless the durable write
        // succeeds. A failed save therefore leaves both views on the previous
        // usable configuration.
        (self.persist)(&self.path, &updated)?;
        *current = updated;

        Ok(current.connection_settings())
    }
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

    pub fn connection_settings(&self) -> ConnectionSettings {
        match self.mode {
            TunnelMode::External => ConnectionSettings {
                mode: TunnelMode::External,
                ssh_target: String::new(),
                remote_url: String::new(),
                has_pairing_token: false,
            },
            TunnelMode::Managed => ConnectionSettings {
                mode: TunnelMode::Managed,
                ssh_target: self.ssh_target.clone(),
                remote_url: String::new(),
                has_pairing_token: false,
            },
            TunnelMode::Direct => ConnectionSettings {
                mode: TunnelMode::Direct,
                ssh_target: String::new(),
                remote_url: self.remote_url.clone(),
                has_pairing_token: !self.pairing_token.is_empty(),
            },
        }
    }

    fn updated(&self, update: ConnectionSettingsUpdate) -> Result<Self, &'static str> {
        let candidate = match update.mode {
            TunnelMode::External => TunnelConfig::default(),
            TunnelMode::Managed => TunnelConfig {
                mode: TunnelMode::Managed,
                ssh_target: update.ssh_target.trim().to_owned(),
                remote_url: String::new(),
                pairing_token: String::new(),
            },
            TunnelMode::Direct => {
                let pairing_token = match update.pairing_token {
                    Some(token) => token,
                    None if self.mode == TunnelMode::Direct && !self.pairing_token.is_empty() => {
                        self.pairing_token.clone()
                    }
                    None => return Err("direct pairing token is required"),
                };

                TunnelConfig {
                    mode: TunnelMode::Direct,
                    ssh_target: String::new(),
                    remote_url: update.remote_url,
                    pairing_token,
                }
            }
        };

        validate(&candidate)?;
        Ok(candidate)
    }
}

fn validate(config: &TunnelConfig) -> Result<(), &'static str> {
    match config.mode {
        TunnelMode::External => Ok(()),
        TunnelMode::Managed => {
            if config.ssh_target.trim().is_empty() {
                return Err("managed mode requires a non-empty sshTarget");
            }
            Ok(())
        }
        TunnelMode::Direct => {
            let url =
                Url::parse(&config.remote_url).map_err(|_| "direct remote URL is invalid")?;

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
    }
}

fn persist_config(path: &Path, config: &TunnelConfig) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| "connection settings path has no parent directory".to_owned())?;

    std::fs::create_dir_all(parent)
        .map_err(|error| format!("failed to create connection settings directory: {error}"))?;

    let mut staged = NamedTempFile::new_in(parent)
        .map_err(|error| format!("failed to stage connection settings: {error}"))?;

    #[cfg(unix)]
    staged
        .as_file()
        .set_permissions(std::fs::Permissions::from_mode(0o600))
        .map_err(|error| format!("failed to secure staged connection settings: {error}"))?;

    let mut payload = serde_json::to_vec_pretty(config)
        .map_err(|error| format!("failed to serialize connection settings: {error}"))?;
    payload.push(b'\n');

    staged
        .write_all(&payload)
        .map_err(|error| format!("failed to write staged connection settings: {error}"))?;
    staged
        .flush()
        .map_err(|error| format!("failed to flush staged connection settings: {error}"))?;
    staged
        .as_file()
        .sync_all()
        .map_err(|error| format!("failed to sync staged connection settings: {error}"))?;

    // The staged file has already been synced. Replacement is the commit
    // point: after it succeeds, do not perform another fallible operation and
    // risk reporting failure after the previous configuration is gone.
    staged
        .persist(path)
        .map_err(|error| format!("failed to replace connection settings: {}", error.error))?;

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
                        "imp: invalid tunnel config at {}: {reason}; using external mode",
                        path.display()
                    );
                    TunnelConfig::default()
                }
            },
            Err(error) => {
                eprintln!(
                    "imp: invalid tunnel config at {}: {error}; using external mode",
                    path.display()
                );
                TunnelConfig::default()
            }
        };
    }

    let config = TunnelConfig::default();
    let _ = persist_config(path, &config);
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
                r#"{{"mode":"direct","remoteUrl":"wss://imp.example/state","pairingToken":"{VALID_TOKEN}"}}"#
            ),
        )
        .unwrap();

        let config = load_or_init(&path);

        assert_eq!(config.mode, TunnelMode::Direct);
        assert_eq!(config.remote_url, "wss://imp.example/state");
        assert_eq!(config.pairing_token, VALID_TOKEN);

        let runtime = config.runtime_connection_config();
        assert_eq!(runtime.mode, RuntimeConnectionMode::Direct);
        assert_eq!(
            runtime.state_url.as_deref(),
            Some("wss://imp.example/state")
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
                r#"{{"mode":"direct","remoteUrl":"wss://imp.example/imp/state","pairingToken":"{VALID_TOKEN}"}}"#
            ),
        )
        .unwrap();

        let config = load_or_init(&path);

        assert_eq!(config.mode, TunnelMode::Direct);
        assert_eq!(config.remote_url, "wss://imp.example/imp/state");

        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn direct_mode_rejects_insecure_ws() {
        let dir = tempdir();
        let path = dir.join("tunnel.json");
        std::fs::write(
            &path,
            format!(
                r#"{{"mode":"direct","remoteUrl":"ws://imp.example/state","pairingToken":"{VALID_TOKEN}"}}"#
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
            "wss://imp.example/ingest",
            "wss://user@imp.example/state",
            "wss://imp.example/state?token=nope",
            "wss://imp.example/state#fragment",
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
            r#"{"mode":"direct","remoteUrl":"wss://imp.example/state","pairingToken":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAB"}"#,
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
            remote_url: "wss://imp.example/state".into(),
            pairing_token: VALID_TOKEN.into(),
        };

        let debug = format!("{config:?}");

        assert!(!debug.contains(VALID_TOKEN));
        assert!(debug.contains("<redacted>"));
    }

    #[test]
    fn managed_mode_rejects_empty_target_on_load() {
        let dir = tempdir();
        let path = dir.join("tunnel.json");
        std::fs::write(&path, r#"{"mode":"managed","sshTarget":"   "}"#).unwrap();

        assert_eq!(load_or_init(&path), TunnelConfig::default());

        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn settings_projection_never_exposes_plaintext_pairing_token() {
        let config = TunnelConfig {
            mode: TunnelMode::Direct,
            ssh_target: String::new(),
            remote_url: "wss://imp.example/state".into(),
            pairing_token: VALID_TOKEN.into(),
        };

        let settings = config.connection_settings();
        let json = serde_json::to_value(settings).unwrap();
        let serialized = json.to_string();

        assert_eq!(json["mode"], "direct");
        assert_eq!(json["remoteUrl"], "wss://imp.example/state");
        assert_eq!(json["hasPairingToken"], true);
        assert!(json.get("pairingToken").is_none());
        assert!(!serialized.contains(VALID_TOKEN));
    }

    #[test]
    fn external_update_clears_transport_specific_fields() {
        let existing = TunnelConfig {
            mode: TunnelMode::Direct,
            ssh_target: "should-not-survive".into(),
            remote_url: "wss://imp.example/state".into(),
            pairing_token: VALID_TOKEN.into(),
        };

        let updated = existing
            .updated(ConnectionSettingsUpdate {
                mode: TunnelMode::External,
                ssh_target: "ignored".into(),
                remote_url: "wss://ignored.example/state".into(),
                pairing_token: Some(VALID_TOKEN.into()),
            })
            .unwrap();

        assert_eq!(updated, TunnelConfig::default());
    }

    #[test]
    fn managed_update_requires_and_trims_target_and_clears_direct_fields() {
        let existing = TunnelConfig {
            mode: TunnelMode::Direct,
            ssh_target: String::new(),
            remote_url: "wss://imp.example/state".into(),
            pairing_token: VALID_TOKEN.into(),
        };

        let empty = existing.updated(ConnectionSettingsUpdate {
            mode: TunnelMode::Managed,
            ssh_target: "   ".into(),
            remote_url: String::new(),
            pairing_token: None,
        });
        assert_eq!(empty.unwrap_err(), "managed mode requires a non-empty sshTarget");

        let updated = existing
            .updated(ConnectionSettingsUpdate {
                mode: TunnelMode::Managed,
                ssh_target: "  avatar  ".into(),
                remote_url: "wss://ignored.example/state".into(),
                pairing_token: Some(VALID_TOKEN.into()),
            })
            .unwrap();

        assert_eq!(
            updated,
            TunnelConfig {
                mode: TunnelMode::Managed,
                ssh_target: "avatar".into(),
                remote_url: String::new(),
                pairing_token: String::new(),
            }
        );
    }

    #[test]
    fn direct_update_requires_token_when_switching_from_non_direct_mode() {
        let result = TunnelConfig::default().updated(ConnectionSettingsUpdate {
            mode: TunnelMode::Direct,
            ssh_target: String::new(),
            remote_url: "wss://imp.example/state".into(),
            pairing_token: None,
        });

        assert_eq!(result.unwrap_err(), "direct pairing token is required");
    }

    #[test]
    fn direct_update_can_preserve_existing_token_without_exposing_it() {
        let existing = TunnelConfig {
            mode: TunnelMode::Direct,
            ssh_target: String::new(),
            remote_url: "wss://old.example/state".into(),
            pairing_token: VALID_TOKEN.into(),
        };

        let updated = existing
            .updated(ConnectionSettingsUpdate {
                mode: TunnelMode::Direct,
                ssh_target: "ignored".into(),
                remote_url: "wss://new.example/state".into(),
                pairing_token: None,
            })
            .unwrap();

        assert_eq!(updated.remote_url, "wss://new.example/state");
        assert_eq!(updated.pairing_token, VALID_TOKEN);
        assert!(updated.ssh_target.is_empty());
    }

    #[test]
    fn direct_update_validates_explicit_replacement_token() {
        let existing = TunnelConfig {
            mode: TunnelMode::Direct,
            ssh_target: String::new(),
            remote_url: "wss://old.example/state".into(),
            pairing_token: VALID_TOKEN.into(),
        };

        let result = existing.updated(ConnectionSettingsUpdate {
            mode: TunnelMode::Direct,
            ssh_target: String::new(),
            remote_url: "wss://new.example/state".into(),
            pairing_token: Some(String::new()),
        });

        assert_eq!(
            result.unwrap_err(),
            "direct pairing token must be canonical 256-bit base64url"
        );
    }

    fn failing_persist(_path: &Path, _config: &TunnelConfig) -> Result<(), String> {
        Err("simulated persistence failure".into())
    }

    #[test]
    fn store_save_persists_canonical_managed_config() {
        let dir = tempdir();
        let path = dir.join("tunnel.json");

        let existing = TunnelConfig {
            mode: TunnelMode::Direct,
            ssh_target: String::new(),
            remote_url: "wss://imp.example/state".into(),
            pairing_token: VALID_TOKEN.into(),
        };
        persist_config(&path, &existing).unwrap();

        let store = ConnectionConfigStore::new(path.clone(), existing);
        let settings = store
            .save(ConnectionSettingsUpdate {
                mode: TunnelMode::Managed,
                ssh_target: "  avatar  ".into(),
                remote_url: "wss://ignored.example/state".into(),
                pairing_token: Some(VALID_TOKEN.into()),
            })
            .unwrap();

        assert_eq!(settings.mode, TunnelMode::Managed);
        assert_eq!(settings.ssh_target, "avatar");
        assert!(settings.remote_url.is_empty());
        assert!(!settings.has_pairing_token);

        let persisted: TunnelConfig =
            serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();

        assert_eq!(
            persisted,
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
    fn store_save_persists_canonical_external_config() {
        let dir = tempdir();
        let path = dir.join("tunnel.json");

        let existing = TunnelConfig {
            mode: TunnelMode::Managed,
            ssh_target: "avatar".into(),
            remote_url: String::new(),
            pairing_token: String::new(),
        };
        persist_config(&path, &existing).unwrap();

        let store = ConnectionConfigStore::new(path.clone(), existing);
        store
            .save(ConnectionSettingsUpdate {
                mode: TunnelMode::External,
                ssh_target: "ignored".into(),
                remote_url: "wss://ignored.example/state".into(),
                pairing_token: Some(VALID_TOKEN.into()),
            })
            .unwrap();

        let persisted: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
        let object = persisted.as_object().unwrap();

        assert_eq!(object.len(), 1);
        assert_eq!(object["mode"], "external");
        assert!(!object.contains_key("sshTarget"));
        assert!(!object.contains_key("remoteUrl"));
        assert!(!object.contains_key("pairingToken"));

        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn store_save_persists_direct_without_ssh_target() {
        let dir = tempdir();
        let path = dir.join("tunnel.json");

        let existing = TunnelConfig::default();
        persist_config(&path, &existing).unwrap();

        let store = ConnectionConfigStore::new(path.clone(), existing);
        store
            .save(ConnectionSettingsUpdate {
                mode: TunnelMode::Direct,
                ssh_target: "ignored".into(),
                remote_url: "wss://imp.example/state".into(),
                pairing_token: Some(VALID_TOKEN.into()),
            })
            .unwrap();

        let persisted: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
        let object = persisted.as_object().unwrap();

        assert_eq!(object["mode"], "direct");
        assert_eq!(object["remoteUrl"], "wss://imp.example/state");
        assert_eq!(object["pairingToken"], VALID_TOKEN);
        assert!(!object.contains_key("sshTarget"));

        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn store_can_preserve_direct_token_without_returning_it() {
        let dir = tempdir();
        let path = dir.join("tunnel.json");

        let existing = TunnelConfig {
            mode: TunnelMode::Direct,
            ssh_target: String::new(),
            remote_url: "wss://old.example/state".into(),
            pairing_token: VALID_TOKEN.into(),
        };
        persist_config(&path, &existing).unwrap();

        let store = ConnectionConfigStore::new(path.clone(), existing);
        let settings = store
            .save(ConnectionSettingsUpdate {
                mode: TunnelMode::Direct,
                ssh_target: String::new(),
                remote_url: "wss://new.example/state".into(),
                pairing_token: None,
            })
            .unwrap();

        assert_eq!(settings.mode, TunnelMode::Direct);
        assert_eq!(settings.remote_url, "wss://new.example/state");
        assert!(settings.has_pairing_token);

        let serialized_settings = serde_json::to_string(&settings).unwrap();
        assert!(!serialized_settings.contains(VALID_TOKEN));

        let persisted: TunnelConfig =
            serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();

        assert_eq!(persisted.pairing_token, VALID_TOKEN);
        assert_eq!(persisted.remote_url, "wss://new.example/state");

        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn store_persistence_failure_keeps_previous_file_and_memory() {
        let dir = tempdir();
        let path = dir.join("tunnel.json");

        let existing = TunnelConfig {
            mode: TunnelMode::Managed,
            ssh_target: "avatar".into(),
            remote_url: String::new(),
            pairing_token: String::new(),
        };
        persist_config(&path, &existing).unwrap();

        let before = std::fs::read_to_string(&path).unwrap();
        let store =
            ConnectionConfigStore::with_persist(path.clone(), existing, failing_persist);

        let error = store
            .save(ConnectionSettingsUpdate {
                mode: TunnelMode::External,
                ssh_target: String::new(),
                remote_url: String::new(),
                pairing_token: None,
            })
            .unwrap_err();

        assert_eq!(error, "simulated persistence failure");
        assert_eq!(std::fs::read_to_string(&path).unwrap(), before);

        let settings = store.settings();
        assert_eq!(settings.mode, TunnelMode::Managed);
        assert_eq!(settings.ssh_target, "avatar");

        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn persist_config_atomically_replaces_an_existing_file() {
        let dir = tempdir();
        let path = dir.join("tunnel.json");

        let old = TunnelConfig {
            mode: TunnelMode::Managed,
            ssh_target: "old".into(),
            remote_url: String::new(),
            pairing_token: String::new(),
        };
        persist_config(&path, &old).unwrap();

        let replacement = TunnelConfig {
            mode: TunnelMode::Managed,
            ssh_target: "new".into(),
            remote_url: String::new(),
            pairing_token: String::new(),
        };
        persist_config(&path, &replacement).unwrap();

        let persisted: TunnelConfig =
            serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();

        assert_eq!(persisted, replacement);

        std::fs::remove_dir_all(&dir).ok();
    }

    #[cfg(unix)]
    #[test]
    fn persisted_connection_settings_are_owner_only() {
        let dir = tempdir();
        let path = dir.join("tunnel.json");

        let config = TunnelConfig {
            mode: TunnelMode::Direct,
            ssh_target: String::new(),
            remote_url: "wss://imp.example/state".into(),
            pairing_token: VALID_TOKEN.into(),
        };
        persist_config(&path, &config).unwrap();

        let mode = std::fs::metadata(&path).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o600);

        std::fs::remove_dir_all(&dir).ok();
    }

    fn tempdir() -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "imp-tunnel-config-test-{}-{}",
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
