//! Runtime configuration for the desktop transport.
//!
//! The historical file remains `tunnel.json` for compatibility. External and
//! managed modes retain the existing SSH behaviour. Direct mode adds a WSS
//! state URL plus one pairing token owned by native application config.

use std::fmt;
use std::io::{ErrorKind, Write};
use std::path::{Path, PathBuf};

use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use parking_lot::Mutex;
use serde::{Deserialize, Serialize};
use tempfile::NamedTempFile;
use url::Url;

use crate::tunnel::SSH_FORWARD_PORT;

const UNAVAILABLE_ERROR: &str =
    "Imp could not read tunnel.json. Restore a valid file or move it aside, then restart Imp.";
const CHANGED_ERROR: &str = "Connection settings changed on disk. Restart Imp before saving again.";

#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum TunnelMode {
    #[default]
    External,
    Local,
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
    Unconfigured,
    Unavailable,
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

#[derive(Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ConnectionSettingsStatus {
    pub running: Option<ConnectionSettings>,
    pub saved: Option<ConnectionSettings>,
    pub pending_restart: bool,
}

/// One requested settings update.
///
/// `pairing_token: None` means "preserve the existing Direct token" and is
/// valid only for the same saved Direct endpoint. A supplied string is always
/// validated as a replacement; an empty string is not a preserve sentinel.
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

type PersistConfig = fn(&Path, &TunnelConfig, bool) -> Result<(), String>;

#[derive(Debug, PartialEq, Eq)]
pub enum LoadedConfig {
    Configured(TunnelConfig),
    Unconfigured,
    Unavailable,
}

impl LoadedConfig {
    fn configured(&self) -> Option<&TunnelConfig> {
        match self {
            Self::Configured(config) => Some(config),
            Self::Unconfigured | Self::Unavailable => None,
        }
    }

    pub fn runtime_connection_config(&self) -> RuntimeConnectionConfig {
        let mode = match self {
            Self::Configured(config) => return config.runtime_connection_config(),
            Self::Unconfigured => RuntimeConnectionMode::Unconfigured,
            Self::Unavailable => RuntimeConnectionMode::Unavailable,
        };
        RuntimeConnectionConfig {
            mode,
            state_url: None,
            authentication_token: None,
        }
    }
}

/// Owns the persisted desktop transport configuration.
///
/// Runtime transport selection remains immutable for the current application
/// process. Saving through this store changes only the next-start
/// configuration.
pub struct ConnectionConfigStore {
    path: PathBuf,
    running: LoadedConfig,
    // Until the first successful save, running also describes the saved file.
    next: Mutex<Option<TunnelConfig>>,
    persist: PersistConfig,
}

impl ConnectionConfigStore {
    pub fn new(path: PathBuf, running: LoadedConfig) -> Self {
        Self {
            path,
            running,
            next: Mutex::new(None),
            persist: persist_config,
        }
    }

    pub fn settings(&self) -> Result<ConnectionSettingsStatus, String> {
        let next = self.next.lock();
        let saved = next.as_ref().or_else(|| self.running.configured());
        self.check_saved(saved)?;
        Ok(self.summarize(saved))
    }

    pub fn save(
        &self,
        update: ConnectionSettingsUpdate,
    ) -> Result<ConnectionSettingsStatus, String> {
        let mut next = self.next.lock();
        let saved = next.as_ref().or_else(|| self.running.configured());
        self.check_saved(saved)?;
        let updated = saved.unwrap_or(&TunnelConfig::default()).updated(update)?;
        (self.persist)(&self.path, &updated, saved.is_none())?;
        let status = self.summarize(Some(&updated));
        *next = Some(updated);
        Ok(status)
    }

    fn check_saved(&self, saved: Option<&TunnelConfig>) -> Result<(), String> {
        if self.running == LoadedConfig::Unavailable {
            return Err(UNAVAILABLE_ERROR.into());
        }
        match load(&self.path) {
            LoadedConfig::Unavailable => Err(UNAVAILABLE_ERROR.into()),
            disk if disk.configured() == saved => Ok(()),
            _ => Err(CHANGED_ERROR.into()),
        }
    }

    fn summarize(&self, saved: Option<&TunnelConfig>) -> ConnectionSettingsStatus {
        let running = self.running.configured();
        ConnectionSettingsStatus {
            running: running.map(TunnelConfig::connection_settings),
            saved: saved.map(TunnelConfig::connection_settings),
            pending_restart: match (running, saved) {
                (Some(running), Some(saved)) => !running.same_route(saved),
                (None, None) => false,
                _ => true,
            },
        }
    }
}

impl TunnelConfig {
    pub fn runtime_connection_config(&self) -> RuntimeConnectionConfig {
        match self.mode {
            TunnelMode::Local => RuntimeConnectionConfig {
                mode: RuntimeConnectionMode::Local,
                state_url: None,
                authentication_token: None,
            },
            TunnelMode::External | TunnelMode::Managed => RuntimeConnectionConfig {
                mode: RuntimeConnectionMode::Local,
                state_url: Some(format!("ws://127.0.0.1:{SSH_FORWARD_PORT}/state")),
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
            TunnelMode::Local => ConnectionSettings {
                mode: TunnelMode::Local,
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

    fn same_route(&self, other: &Self) -> bool {
        if self.mode != other.mode {
            return false;
        }
        match self.mode {
            TunnelMode::External | TunnelMode::Local => true,
            TunnelMode::Managed => self.ssh_target.trim() == other.ssh_target.trim(),
            TunnelMode::Direct => {
                self.pairing_token == other.pairing_token
                    && (self.remote_url == other.remote_url
                        || Url::parse(&other.remote_url).is_ok_and(|url| {
                            same_direct_endpoint(&self.remote_url, &other.remote_url, &url)
                        }))
            }
        }
    }

    fn updated(&self, update: ConnectionSettingsUpdate) -> Result<Self, &'static str> {
        let candidate = match update.mode {
            TunnelMode::External => TunnelConfig::default(),
            TunnelMode::Local => TunnelConfig {
                mode: TunnelMode::Local,
                ssh_target: String::new(),
                remote_url: String::new(),
                pairing_token: String::new(),
            },
            TunnelMode::Managed => TunnelConfig {
                mode: TunnelMode::Managed,
                ssh_target: update.ssh_target.trim().to_owned(),
                remote_url: String::new(),
                pairing_token: String::new(),
            },
            TunnelMode::Direct => {
                let token = match update.pairing_token.as_deref() {
                    Some(token) => token,
                    None if self.mode == TunnelMode::Direct && !self.pairing_token.is_empty() => {
                        &self.pairing_token
                    }
                    None => return Err("direct pairing token is required"),
                };

                let url = validate_direct_url(&update.remote_url)?;
                validate_direct_token(token)?;
                if update.pairing_token.is_none()
                    && !same_direct_endpoint(&self.remote_url, &update.remote_url, &url)
                {
                    return Err("direct pairing token is required when changing the remote URL");
                }

                return Ok(TunnelConfig {
                    mode: TunnelMode::Direct,
                    ssh_target: String::new(),
                    remote_url: update.remote_url,
                    pairing_token: update
                        .pairing_token
                        .unwrap_or_else(|| self.pairing_token.clone()),
                });
            }
        };

        validate(&candidate)?;
        Ok(candidate)
    }
}

fn validate(config: &TunnelConfig) -> Result<(), &'static str> {
    match config.mode {
        TunnelMode::External | TunnelMode::Local => Ok(()),
        TunnelMode::Managed => {
            if config.ssh_target.trim().is_empty() {
                return Err("managed mode requires a non-empty sshTarget");
            }
            Ok(())
        }
        TunnelMode::Direct => {
            validate_direct_url(&config.remote_url)?;
            validate_direct_token(&config.pairing_token)
        }
    }
}

fn validate_direct_url(value: &str) -> Result<Url, &'static str> {
    let url = Url::parse(value).map_err(|_| "direct remote URL is invalid")?;

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

    Ok(url)
}

fn validate_direct_token(token: &str) -> Result<(), &'static str> {
    let decoded = URL_SAFE_NO_PAD
        .decode(token.as_bytes())
        .map_err(|_| "direct pairing token is invalid")?;

    if decoded.len() != 32 || URL_SAFE_NO_PAD.encode(&decoded) != token {
        return Err("direct pairing token must be canonical 256-bit base64url");
    }

    Ok(())
}

fn same_direct_endpoint(saved: &str, requested: &str, requested_url: &Url) -> bool {
    // Outside simple ASCII spellings, do not assume native and WebView URL normalization agree.
    let simple_ascii = |value: &str| {
        value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b":/.-_[]".contains(&byte))
    };
    saved == requested
        || (simple_ascii(saved)
            && simple_ascii(requested)
            && Url::parse(saved).is_ok_and(|saved_url| &saved_url == requested_url))
}

fn persist_config(path: &Path, config: &TunnelConfig, create: bool) -> Result<(), String> {
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

    // Publish atomically after syncing. A first save must not clobber a file
    // that appeared after the absence check (including another Imp instance).
    if create {
        staged.persist_noclobber(path).map_err(|error| {
            if error.error.kind() == ErrorKind::AlreadyExists {
                CHANGED_ERROR.to_owned()
            } else {
                format!("failed to create connection settings: {}", error.error)
            }
        })?;
    } else {
        staged
            .persist(path)
            .map_err(|error| format!("failed to replace connection settings: {}", error.error))?;
    }

    Ok(())
}

/// Reads only. Only a genuinely absent path can enter first-time setup.
pub fn load(path: &Path) -> LoadedConfig {
    match std::fs::symlink_metadata(path) {
        Ok(metadata) if metadata.is_file() => {}
        Ok(metadata)
            if metadata.file_type().is_symlink()
                && std::fs::metadata(path).is_ok_and(|target| target.is_file()) => {}
        Err(error) if error.kind() == ErrorKind::NotFound && has_existing_directory(path) => {
            return LoadedConfig::Unconfigured;
        }
        _ => return LoadedConfig::Unavailable,
    }

    // serde's derived struct deserializer also accepts positional arrays.
    let Ok(text) = std::fs::read_to_string(path) else {
        return LoadedConfig::Unavailable;
    };
    if text.trim_start().starts_with('{') {
        if let Ok(config) = serde_json::from_str(&text) {
            if validate(&config).is_ok() {
                return LoadedConfig::Configured(config);
            }
        }
    }
    LoadedConfig::Unavailable
}

fn has_existing_directory(path: &Path) -> bool {
    // A dangling parent link can also make the leaf return NotFound.
    for parent in path
        .ancestors()
        .skip(1)
        .filter(|path| !path.as_os_str().is_empty())
    {
        match std::fs::symlink_metadata(parent) {
            Ok(metadata) if metadata.file_type().is_symlink() => {
                return std::fs::metadata(parent).is_ok_and(|target| target.is_dir());
            }
            Ok(metadata) => return metadata.is_dir(),
            Err(error) if error.kind() == ErrorKind::NotFound => continue,
            Err(_) => return false,
        }
    }
    true
}

#[cfg(test)]
mod tests {
    use super::*;

    const VALID_TOKEN: &str = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";

    #[test]
    fn missing_file_stays_absent_until_explicit_save_and_relaunch() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("missing-parent/tunnel.json");
        let loaded = load(&path);

        assert_inactive(&loaded, RuntimeConnectionMode::Unconfigured, "unconfigured");
        assert!(!path.parent().unwrap().exists());
        let store = ConnectionConfigStore::new(path.clone(), loaded);
        let status = store.settings().unwrap();
        assert_eq!(
            serde_json::to_value(status).unwrap(),
            serde_json::json!({ "running": null, "saved": null, "pendingRestart": false })
        );
        assert!(!path.parent().unwrap().exists());
        assert_eq!(
            store
                .save(direct_update("wss://imp.example/state", None))
                .unwrap_err(),
            "direct pairing token is required"
        );
        assert!(!path.parent().unwrap().exists());

        let status = store.save(local_update()).unwrap();
        assert!(status.running.is_none());
        assert_eq!(status.saved.unwrap().mode, TunnelMode::Local);
        assert!(status.pending_restart);
        assert_inactive(
            &store.running,
            RuntimeConnectionMode::Unconfigured,
            "unconfigured",
        );

        let restarted = ConnectionConfigStore::new(path.clone(), load(&path));
        let status = restarted.settings().unwrap();
        assert!(!status.pending_restart);
        assert_eq!(status.running, status.saved);
        assert_eq!(status.running.unwrap().mode, TunnelMode::Local);
    }

    #[test]
    fn unavailable_file_is_preserved_and_stays_unavailable_until_restart() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("tunnel.json");
        for original in [
            b"{not json pairing-token".as_slice(),
            b"[]",
            b"[\"external\"]",
            b"null",
            b"{\"mode\":\"unknown\"}",
            b"\xff",
        ] {
            std::fs::write(&path, original).unwrap();
            let loaded = load(&path);
            assert_inactive(&loaded, RuntimeConnectionMode::Unavailable, "unavailable");
            let store = ConnectionConfigStore::new(path.clone(), loaded);
            assert_eq!(store.settings().unwrap_err(), UNAVAILABLE_ERROR);
            assert_eq!(store.save(local_update()).unwrap_err(), UNAVAILABLE_ERROR);
            assert_eq!(std::fs::read(&path).unwrap(), original);

            std::fs::remove_file(&path).unwrap();
            assert_eq!(store.save(local_update()).unwrap_err(), UNAVAILABLE_ERROR);
            assert!(!path.exists());
        }
    }

    #[test]
    fn directory_at_config_path_is_not_absence() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("tunnel.json");
        std::fs::create_dir(&path).unwrap();
        let store = ConnectionConfigStore::new(path.clone(), load(&path));
        assert_eq!(store.settings().unwrap_err(), UNAVAILABLE_ERROR);
        assert_eq!(store.save(local_update()).unwrap_err(), UNAVAILABLE_ERROR);
        assert!(path.is_dir());
    }

    #[cfg(unix)]
    #[test]
    fn dangling_link_is_not_absence() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("tunnel.json");
        let target = dir.path().join("missing.json");
        std::os::unix::fs::symlink(&target, &path).unwrap();
        let store = ConnectionConfigStore::new(path.clone(), load(&path));
        assert_eq!(store.settings().unwrap_err(), UNAVAILABLE_ERROR);
        assert_eq!(store.save(local_update()).unwrap_err(), UNAVAILABLE_ERROR);
        assert_eq!(std::fs::read_link(path).unwrap(), target);
        assert!(!target.exists());
    }

    #[cfg(unix)]
    #[test]
    fn dangling_parent_link_is_not_absence() {
        let dir = tempfile::tempdir().unwrap();
        let parent = dir.path().join("config");
        let target = dir.path().join("missing");
        std::os::unix::fs::symlink(&target, &parent).unwrap();
        let path = parent.join("nested/tunnel.json");
        let store = ConnectionConfigStore::new(path.clone(), load(&path));
        assert_eq!(store.save(local_update()).unwrap_err(), UNAVAILABLE_ERROR);
        assert_eq!(std::fs::read_link(parent).unwrap(), target);
        assert!(!target.exists());
    }

    #[test]
    fn existing_external_and_legacy_omitted_mode_keep_the_forward() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("tunnel.json");
        for text in [r#"{"mode":"external"}"#, "{}"] {
            std::fs::write(&path, text).unwrap();
            let loaded = load(&path);
            let config = loaded.configured().unwrap();
            assert_eq!(config.mode, TunnelMode::External);
            let runtime = loaded.runtime_connection_config();
            assert_eq!(runtime.mode, RuntimeConnectionMode::Local);
            assert_eq!(
                runtime.state_url,
                Some(format!("ws://127.0.0.1:{SSH_FORWARD_PORT}/state"))
            );
            assert!(runtime.authentication_token.is_none());
            assert_eq!(std::fs::read_to_string(&path).unwrap(), text);
        }
    }

    fn assert_inactive(loaded: &LoadedConfig, mode: RuntimeConnectionMode, wire_mode: &str) {
        assert!(loaded.configured().is_none());
        let runtime = loaded.runtime_connection_config();
        assert_eq!(runtime.mode, mode);
        assert_eq!(
            serde_json::to_value(runtime).unwrap(),
            serde_json::json!({ "mode": wire_mode, "stateUrl": null, "authenticationToken": null })
        );
    }

    fn local_update() -> ConnectionSettingsUpdate {
        ConnectionSettingsUpdate {
            mode: TunnelMode::Local,
            ssh_target: String::new(),
            remote_url: String::new(),
            pairing_token: None,
        }
    }

    #[test]
    fn local_mode_round_trips_and_uses_the_local_runtime_projection() {
        let dir = tempdir();
        let path = dir.join("tunnel.json");
        std::fs::write(&path, r#"{"mode":"local"}"#).unwrap();

        let LoadedConfig::Configured(config) = load(&path) else {
            panic!("expected configured Local mode");
        };

        assert_eq!(
            config,
            TunnelConfig {
                mode: TunnelMode::Local,
                ssh_target: String::new(),
                remote_url: String::new(),
                pairing_token: String::new(),
            }
        );

        let runtime = config.runtime_connection_config();
        assert_eq!(runtime.mode, RuntimeConnectionMode::Local);
        assert!(runtime.state_url.is_none());
        assert!(runtime.authentication_token.is_none());

        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn local_update_clears_remote_transport_material() {
        let existing = TunnelConfig {
            mode: TunnelMode::Direct,
            ssh_target: "ignored".into(),
            remote_url: "wss://imp.example/state".into(),
            pairing_token: VALID_TOKEN.into(),
        };

        let updated = existing
            .updated(ConnectionSettingsUpdate {
                mode: TunnelMode::Local,
                ssh_target: "ignored".into(),
                remote_url: "wss://ignored.example/state".into(),
                pairing_token: Some(VALID_TOKEN.into()),
            })
            .unwrap();

        assert_eq!(
            updated,
            TunnelConfig {
                mode: TunnelMode::Local,
                ssh_target: String::new(),
                remote_url: String::new(),
                pairing_token: String::new(),
            }
        );
    }

    #[test]
    fn managed_mode_round_trips() {
        let dir = tempdir();
        let path = dir.join("tunnel.json");
        std::fs::write(&path, r#"{"mode":"managed","sshTarget":"avatar"}"#).unwrap();

        let LoadedConfig::Configured(config) = load(&path) else {
            panic!("expected configured Managed SSH mode");
        };

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

        let LoadedConfig::Configured(config) = load(&path) else {
            panic!("expected configured Direct WSS mode");
        };

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

        let LoadedConfig::Configured(config) = load(&path) else {
            panic!("expected configured Direct WSS mode");
        };

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

        assert_eq!(load(&path), LoadedConfig::Unavailable);
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

            assert_eq!(load(&path), LoadedConfig::Unavailable);
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

        assert_eq!(load(&path), LoadedConfig::Unavailable);
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn managed_runtime_config_exposes_forward_without_ssh_or_auth_material() {
        let config = TunnelConfig {
            mode: TunnelMode::Managed,
            ssh_target: "avatar".into(),
            remote_url: String::new(),
            pairing_token: String::new(),
        };

        let runtime = config.runtime_connection_config();
        let json = serde_json::to_value(runtime).unwrap();

        assert_eq!(json["mode"], "local");
        assert_eq!(
            json["stateUrl"],
            format!("ws://127.0.0.1:{SSH_FORWARD_PORT}/state")
        );
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

        assert_eq!(load(&path), LoadedConfig::Unavailable);

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
        assert_eq!(
            empty.unwrap_err(),
            "managed mode requires a non-empty sshTarget"
        );

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
    fn direct_update_requires_valid_token_when_switching_from_non_direct_mode() {
        for mode in [TunnelMode::External, TunnelMode::Local, TunnelMode::Managed] {
            let existing = TunnelConfig {
                mode,
                ssh_target: "avatar".into(),
                ..TunnelConfig::default()
            };
            for (token, error) in [
                (None, "direct pairing token is required"),
                (Some("!"), "direct pairing token is invalid"),
                (
                    Some(""),
                    "direct pairing token must be canonical 256-bit base64url",
                ),
            ] {
                let result = existing.updated(direct_update("wss://imp.example/state", token));
                assert_eq!(result.unwrap_err(), error, "{mode:?}");
            }

            let updated = existing
                .updated(direct_update("wss://imp.example/state", Some(VALID_TOKEN)))
                .unwrap();
            assert_eq!(updated.mode, TunnelMode::Direct);
            assert_eq!(updated.pairing_token, VALID_TOKEN);
            assert!(updated.ssh_target.is_empty());
        }
    }

    #[test]
    fn direct_update_retains_token_for_the_same_validated_endpoint() {
        for (saved, requested) in [
            (
                "wss://imp.example/state",
                "WSS://IMP.example:443/cache/../state",
            ),
            (
                "wss://imp.example:8443/imp/state",
                "wss://IMP.example:8443/imp/./state",
            ),
            ("wss://127.1/state", "wss://127.0.0.1/state"),
            (
                "wss://[2001:0DB8::1]/state",
                "wss://[2001:db8::1]:443/state",
            ),
            ("wss://bücher.example/state", "wss://bücher.example/state"),
            (
                "wss://imp.example/%69mp/state",
                "wss://imp.example/%69mp/state",
            ),
        ] {
            let existing = direct_config(saved, VALID_TOKEN);
            let updated = existing.updated(direct_update(requested, None)).unwrap();
            assert_eq!(updated, direct_config(requested, VALID_TOKEN));
        }
    }

    fn direct_config(remote_url: &str, pairing_token: &str) -> TunnelConfig {
        TunnelConfig {
            mode: TunnelMode::Direct,
            ssh_target: String::new(),
            remote_url: remote_url.into(),
            pairing_token: pairing_token.into(),
        }
    }

    fn direct_update(remote_url: &str, pairing_token: Option<&str>) -> ConnectionSettingsUpdate {
        ConnectionSettingsUpdate {
            mode: TunnelMode::Direct,
            ssh_target: String::new(),
            remote_url: remote_url.into(),
            pairing_token: pairing_token.map(str::to_owned),
        }
    }

    fn failing_persist(_path: &Path, _config: &TunnelConfig, _create: bool) -> Result<(), String> {
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
        persist_config(&path, &existing, true).unwrap();

        let store = ConnectionConfigStore::new(path.clone(), load(&path));
        let settings = store
            .save(ConnectionSettingsUpdate {
                mode: TunnelMode::Managed,
                ssh_target: "  avatar  ".into(),
                remote_url: "wss://ignored.example/state".into(),
                pairing_token: Some(VALID_TOKEN.into()),
            })
            .unwrap();
        assert_eq!(settings.running.unwrap().mode, TunnelMode::Direct);
        assert!(settings.pending_restart);
        let settings = settings.saved.unwrap();

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
        persist_config(&path, &existing, true).unwrap();

        let store = ConnectionConfigStore::new(path.clone(), load(&path));
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
        persist_config(&path, &existing, true).unwrap();

        let store = ConnectionConfigStore::new(path.clone(), load(&path));
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
    fn store_retains_direct_token_without_returning_it() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("tunnel.json");
        let existing = direct_config("wss://imp.example/state", VALID_TOKEN);
        persist_config(&path, &existing, true).unwrap();
        let store = ConnectionConfigStore::new(path.clone(), load(&path));

        for url in [
            "wss://imp.example/state",
            "wss://IMP.example:443/proxy/../state",
        ] {
            let settings = store.save(direct_update(url, None)).unwrap();
            assert_eq!(settings.saved.as_ref().unwrap().remote_url, url);
            assert!(settings.saved.as_ref().unwrap().has_pairing_token);
            assert!(!settings.pending_restart);
            assert_eq!(store.settings().unwrap(), settings);
            let json = serde_json::to_value(settings).unwrap();
            assert!(json["running"].get("pairingToken").is_none());
            assert!(json["saved"].get("pairingToken").is_none());
            assert!(!json.to_string().contains(VALID_TOKEN));
            assert_eq!(
                load(&path),
                LoadedConfig::Configured(direct_config(url, VALID_TOKEN)),
            );
        }
    }

    #[test]
    fn store_rejects_implicit_transfers_and_invalid_updates_without_changes() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("tunnel.json");
        let existing = direct_config("wss://xn--bcher-kva.example/imp/state", VALID_TOKEN);
        persist_config(&path, &existing, true).unwrap();
        let before = std::fs::read(&path).unwrap();
        let store = ConnectionConfigStore::new(path.clone(), load(&path));
        let changed = "direct pairing token is required when changing the remote URL";

        for (url, token, error) in [
            ("wss://other.example/imp/state", None, changed),
            ("wss://xn--bcher-kva.example:8443/imp/state", None, changed),
            ("wss://xn--bcher-kva.example/other/state", None, changed),
            ("wss://bücher.example/imp/state", None, changed),
            ("wss://xn--bcher-kva.example/%2e/imp/state", None, changed),
            ("wss://xn--bcher-kva.example\\imp/state", None, changed),
            ("not a URL", None, "direct remote URL is invalid"),
            (
                "ws://other.example/state",
                Some(VALID_TOKEN),
                "direct remote URL must use wss",
            ),
            (
                "wss://user@other.example/state",
                None,
                "direct remote URL must not contain credentials",
            ),
            (
                "wss://other.example/state?foo=bar",
                None,
                "direct remote URL must not contain query or fragment data",
            ),
            (
                "wss://other.example/ingest",
                None,
                "direct remote URL must end at /state",
            ),
            (
                "wss://other.example/state",
                Some("!"),
                "direct pairing token is invalid",
            ),
            (
                "wss://other.example/state",
                Some(""),
                "direct pairing token must be canonical 256-bit base64url",
            ),
        ] {
            assert_eq!(
                store.save(direct_update(url, token)).unwrap_err(),
                error,
                "{url}"
            );
            assert_eq!(std::fs::read(&path).unwrap(), before);
            assert!(store.next.lock().is_none());
        }
    }

    #[test]
    fn store_binds_retention_to_the_last_saved_endpoint() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("tunnel.json");
        let original = "wss://old.example/state";
        let new = "wss://new.example/state";
        let existing = direct_config(original, VALID_TOKEN);
        persist_config(&path, &existing, true).unwrap();
        let store = ConnectionConfigStore::new(path.clone(), load(&path));
        let stale = store.settings().unwrap().saved.unwrap();

        let replacement = URL_SAFE_NO_PAD.encode([1; 32]);
        store.save(direct_update(new, Some(&replacement))).unwrap();
        assert_eq!(
            load(&path),
            LoadedConfig::Configured(direct_config(new, &replacement)),
        );
        let before = std::fs::read(&path).unwrap();

        assert_eq!(
            store
                .save(direct_update(&stale.remote_url, None))
                .unwrap_err(),
            "direct pairing token is required when changing the remote URL"
        );
        assert_eq!(std::fs::read(&path).unwrap(), before);
        assert_eq!(
            load(&path),
            LoadedConfig::Configured(direct_config(new, &replacement)),
        );

        store.save(direct_update(new, None)).unwrap();
        assert_eq!(
            load(&path),
            LoadedConfig::Configured(direct_config(new, &replacement)),
        );

        store.save(direct_update(new, Some(VALID_TOKEN))).unwrap();
        assert_eq!(
            load(&path),
            LoadedConfig::Configured(direct_config(new, VALID_TOKEN)),
        );
    }

    #[test]
    fn store_persistence_failure_keeps_previous_file_and_memory() {
        let dir = tempdir();
        let path = dir.join("tunnel.json");

        let existing = direct_config("wss://old.example/state", VALID_TOKEN);
        persist_config(&path, &existing, true).unwrap();

        let before = std::fs::read_to_string(&path).unwrap();
        let mut store = ConnectionConfigStore::new(path.clone(), load(&path));
        store.persist = failing_persist;
        let status = store.settings().unwrap();
        let replacement = URL_SAFE_NO_PAD.encode([1; 32]);
        let error = store
            .save(direct_update("wss://new.example/state", Some(&replacement)))
            .unwrap_err();

        assert_eq!(error, "simulated persistence failure");
        assert_eq!(std::fs::read_to_string(&path).unwrap(), before);
        assert_eq!(store.settings().unwrap(), status);
        assert!(store.next.lock().is_none());

        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn pending_restart_detects_token_only_and_endpoint_only_changes() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("tunnel.json");
        let original = "wss://imp.example/state";
        let existing = direct_config(original, VALID_TOKEN);
        persist_config(&path, &existing, true).unwrap();
        let store = ConnectionConfigStore::new(path.clone(), load(&path));
        let token = URL_SAFE_NO_PAD.encode([1; 32]);

        let status = store.save(direct_update(original, Some(&token))).unwrap();
        assert_eq!(status.running, status.saved);
        assert!(status.pending_restart);
        let json = serde_json::to_string(&status).unwrap();
        assert!(!json.contains(VALID_TOKEN));
        assert!(!json.contains(&token));
        assert!(!json.contains("pairingToken"));

        let status = store
            .save(direct_update(
                "wss://imp.example/prefix/state",
                Some(VALID_TOKEN),
            ))
            .unwrap();
        assert!(status.pending_restart);
        assert_eq!(status.running.unwrap().remote_url, original);

        let status = store
            .save(direct_update(original, Some(VALID_TOKEN)))
            .unwrap();
        assert!(!status.pending_restart);
    }

    #[test]
    fn store_never_replaces_an_unexpected_file_before_save() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("tunnel.json");
        let store = ConnectionConfigStore::new(path.clone(), load(&path));
        let original = r#"{"mode":"external"}"#;
        std::fs::write(&path, original).unwrap();
        assert_eq!(store.save(local_update()).unwrap_err(), CHANGED_ERROR);
        assert_eq!(std::fs::read_to_string(&path).unwrap(), original);

        let store = ConnectionConfigStore::new(path.clone(), load(&path));
        std::fs::write(&path, "{unreadable").unwrap();
        assert_eq!(store.settings().unwrap_err(), UNAVAILABLE_ERROR);
        assert_eq!(store.save(local_update()).unwrap_err(), UNAVAILABLE_ERROR);
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "{unreadable");

        let changed = r#"{"mode":"managed","sshTarget":"avatar"}"#;
        std::fs::write(&path, changed).unwrap();
        assert_eq!(store.settings().unwrap_err(), CHANGED_ERROR);
        assert_eq!(store.save(local_update()).unwrap_err(), CHANGED_ERROR);
        assert_eq!(std::fs::read_to_string(&path).unwrap(), changed);
    }

    #[test]
    fn first_persist_preserves_a_file_created_after_the_absence_check() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("tunnel.json");
        let mut store = ConnectionConfigStore::new(path.clone(), load(&path));
        store.persist = |path, config, create| {
            std::fs::write(path, r#"{"mode":"external"}"#).unwrap();
            persist_config(path, config, create)
        };
        assert_eq!(store.save(local_update()).unwrap_err(), CHANGED_ERROR);
        assert!(store.next.lock().is_none());
        assert_eq!(
            std::fs::read_to_string(&path).unwrap(),
            r#"{"mode":"external"}"#,
        );
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
        persist_config(&path, &old, true).unwrap();

        let replacement = TunnelConfig {
            mode: TunnelMode::Managed,
            ssh_target: "new".into(),
            remote_url: String::new(),
            pairing_token: String::new(),
        };
        persist_config(&path, &replacement, false).unwrap();

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
        persist_config(&path, &config, true).unwrap();

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
