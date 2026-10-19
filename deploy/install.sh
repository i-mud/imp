#!/usr/bin/env bash
set -euo pipefail

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'USAGE'
Usage: ./install.sh [options]

Install the Imp server runtime for the current user.

Options:
  --connection MODE  Choose ssh, direct, or none. Existing installs preserve
                     connection state when this option is omitted.
  --hostname HOST    Public DNS hostname for Direct WSS.
  --behind-nat       Direct behind a router forwarding TCP 443 to this host.
  --tf-startup FILE  Add the Imp capture hook to FILE instead of the
                     default ~/.tfrc.
  --no-start         Install files and systemd units without starting or
                     enabling services.
  -h, --help         Show this help.
USAGE
}

TF_STARTUP="$HOME/.tfrc"
TF_STARTUP_EXPLICIT=0
START_SERVICES=1
CONNECTION=""
PUBLIC_HOSTNAME=""
BEHIND_NAT=0
DIRECT_EDGE_OPTIONS=()
PUBLIC_VERIFY_OPTIONS=()

while (($#)); do
  case "$1" in
    --connection)
      (($# >= 2)) || die "--connection requires ssh, direct, or none"
      [[ -z "$CONNECTION" ]] || die "--connection may be supplied only once"
      case "$2" in
        ssh|direct|none) CONNECTION="$2" ;;
        *) die "--connection requires ssh|direct|none" ;;
      esac
      shift 2
      ;;
    --hostname)
      (($# >= 2)) || die "--hostname requires a public DNS hostname"
      [[ -z "$PUBLIC_HOSTNAME" ]] || die "--hostname may be supplied only once"
      PUBLIC_HOSTNAME="$2"
      [[ -n "$PUBLIC_HOSTNAME" ]] || die "--hostname requires a public DNS hostname"
      shift 2
      ;;
    --behind-nat)
      (( ! BEHIND_NAT )) || die "--behind-nat may be supplied only once"
      BEHIND_NAT=1
      shift
      ;;
    --tf-startup)
      (($# >= 2)) || die "--tf-startup requires a path"
      TF_STARTUP="$2"
      TF_STARTUP_EXPLICIT=1
      shift 2
      ;;
    --no-start)
      START_SERVICES=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

[[ "$(id -u)" -ne 0 ]] || die "run this installer as the target user, not root"
[[ "$(uname -s)" == "Linux" ]] || die "this Imp server bundle supports Linux only"
[[ "$(uname -m)" == "x86_64" ]] || die "this Imp server bundle supports x86_64 only"

command -v sha256sum >/dev/null 2>&1 ||
  die "sha256sum is required"

if ((START_SERVICES)); then
  command -v systemctl >/dev/null 2>&1 ||
    die "systemd user services are required"
fi

BUNDLE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VERSION_FILE="$BUNDLE_DIR/VERSION"

[[ -f "$VERSION_FILE" ]] || die "bundle is missing VERSION"
[[ -f "$BUNDLE_DIR/SHA256SUMS" ]] || die "bundle is missing SHA256SUMS"
[[ -f "$BUNDLE_DIR/capture.tf" ]] || die "bundle is missing capture.tf"
[[ -f "$BUNDLE_DIR/bin/imp-server" ]] || die "bundle is missing bin/imp-server"

VERSION="$(tr -d '\r\n' < "$VERSION_FILE")"

[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.+-][0-9A-Za-z.-]+)?$ ]] ||
  die "invalid bundle version: $VERSION"

printf 'Verifying Imp server bundle...\n'
(
  cd "$BUNDLE_DIR"
  sha256sum -c SHA256SUMS
)

"$BUNDLE_DIR/bin/imp-server" bundle-check "$BUNDLE_DIR"

BUNDLE_ID=""
read -r BUNDLE_ID _ < <(sha256sum "$BUNDLE_DIR/SHA256SUMS")

[[ "$BUNDLE_ID" =~ ^[0-9a-f]{64}$ ]] ||
  die "could not identify verified server bundle"

DATA_DIR="$HOME/.local/share/imp"
RELEASES_DIR="$DATA_DIR/releases"
RELEASE_DIR="$RELEASES_DIR/$VERSION"
CURRENT_LINK="$DATA_DIR/current"

CONFIG_DIR="$HOME/.config/imp"
STATE_DIR="$HOME/.local/state/imp"
BIN_DIR="$HOME/.local/bin"
SYSTEMD_DIR="$HOME/.config/systemd/user"

ACTION_LINK="$BIN_DIR/imp-action-consumer"
FEED_WATCH_LINK="$BIN_DIR/imp-feed-watch"
DIRECT_WSS_LINK="$BIN_DIR/imp-direct-wss"

# Missing one expected file is not evidence of a fresh installation.
EXISTING_INSTALL=0
SERVICE_STATE_UNKNOWN=0
for path in "$DATA_DIR" "$CONFIG_DIR" "$STATE_DIR" "$ACTION_LINK" "$FEED_WATCH_LINK" "$DIRECT_WSS_LINK" \
  "$SYSTEMD_DIR/imp-relay.service" "$SYSTEMD_DIR/imp-feed.service" \
  "$SYSTEMD_DIR/imp-gateway.service" /etc/imp/direct-edge.json; do
  if [[ -e "$path" || -L "$path" ]]; then
    EXISTING_INSTALL=1
  fi
done
if ((!EXISTING_INSTALL)) && command -v systemctl >/dev/null 2>&1; then
  for unit in imp-relay.service imp-feed.service imp-gateway.service; do
    if systemctl --user is-active --quiet "$unit" 2>/dev/null ||
       systemctl --user is-enabled --quiet "$unit" 2>/dev/null; then
      EXISTING_INSTALL=1
    elif loaded="$(systemctl --user show "$unit" --property=LoadState --value 2>/dev/null)"; then
      if [[ -n "$loaded" && "$loaded" != "not-found" ]]; then
        EXISTING_INSTALL=1
      fi
    else
      SERVICE_STATE_UNKNOWN=1
    fi
  done
fi

if [[ -z "$CONNECTION" ]] && ((!EXISTING_INSTALL)); then
  if ((SERVICE_STATE_UNKNOWN)) || [[ ! -t 0 || ! -t 1 ]]; then
    die "specify --connection ssh|direct|none; a fresh connection mode cannot be chosen non-interactively or with unknown user-service state"
  fi
  printf '\nHow will the Imp desktop connect?\n\n1. SSH\n2. Direct WSS\n3. Install only / configure later\n'
  read -r -p 'Choice [1-3]: ' choice || die "connection selection interrupted"
  case "$choice" in
    1) CONNECTION=ssh ;;
    2) CONNECTION=direct ;;
    3) CONNECTION=none ;;
    *) die "choose 1, 2, or 3, or rerun with --connection ssh|direct|none" ;;
  esac
fi
if [[ "$CONNECTION" == direct ]]; then
  ((START_SERVICES)) || die "--connection direct cannot be combined with --no-start"
  if [[ -z "$PUBLIC_HOSTNAME" && -t 0 && -t 1 ]]; then
    read -r -p 'Public hostname: ' PUBLIC_HOSTNAME || die "hostname selection interrupted"
  fi
  [[ -n "$PUBLIC_HOSTNAME" ]] || die "--connection direct requires --hostname HOST"
  PUBLIC_HOSTNAME="${PUBLIC_HOSTNAME,,}"
  "$BUNDLE_DIR/bin/imp-server" direct-edge hostname "$PUBLIC_HOSTNAME"
elif [[ -n "$PUBLIC_HOSTNAME" ]]; then
  die "--hostname is supported only with --connection direct"
fi
if ((BEHIND_NAT)) && [[ "$CONNECTION" != direct ]]; then
  die "--behind-nat is supported only with --connection direct"
fi

if [[ ! -e "$TF_STARTUP" && ! -L "$TF_STARTUP" ]] && ((!TF_STARTUP_EXPLICIT)); then
  if [[ -t 0 && -t 1 ]]; then
    printf '\nDefault TinyFugue startup file does not exist: %s\n' "$TF_STARTUP"
    read -r -p 'Enter an existing TinyFugue startup file: ' TF_STARTUP ||
      die "TinyFugue startup selection interrupted"
    case "$TF_STARTUP" in '~/'*) TF_STARTUP="$HOME/${TF_STARTUP:2}" ;; esac
  else
    die "default TinyFugue startup file does not exist: $TF_STARTUP; use --tf-startup FILE for an existing startup file"
  fi
fi
[[ -f "$TF_STARTUP" ]] || die "TinyFugue startup must be an existing regular file: $TF_STARTUP"
[[ -O "$TF_STARTUP" && -r "$TF_STARTUP" && -w "$TF_STARTUP" ]] ||
  die "TinyFugue startup must be owned by the current user, readable and writable: $TF_STARTUP"

LOAD_LINE='/load ~/.config/imp/capture.tf'
TF_DIRECT_HOOKS=0
TF_EXACT_HOOK=0
TF_NESTED_HOOKS=0
TF_UNCERTAIN=0
TF_BOM=0
TF_LEGACY=0
TF_INCLUDES=()
TF_NEWLINE=$'\n'
# shortcut: inspect only one level of literal /load paths; complex TF programs need operator review.
inspect_tf_startup() {
  local file="$1" depth="$2" line first=1 include
  while IFS= read -r line || [[ -n "$line" ]]; do
    if ((!depth)) && [[ "${line%$'\r'}" == "$LOAD_LINE" ]]; then TF_EXACT_HOOK=1; fi
    if ((first)); then
      if [[ "$line" == $'\xef\xbb\xbf'* ]]; then TF_BOM=1; TF_UNCERTAIN=1; fi
      if ((!depth)) && [[ "$line" == *$'\r' ]]; then TF_NEWLINE=$'\r\n'; fi
      line="${line#$'\xef\xbb\xbf'}"
      first=0
    fi
    line="${line%$'\r'}"
    case "$line" in ';'*|'#'*) continue ;; esac
    line="${line#"${line%%[![:space:]]*}"}"
    case "$line" in
      '') continue ;;
      /eval|/eval[[:space:]]*|/require|/require[[:space:]]*) TF_UNCERTAIN=1; continue ;;
      /*) ;;
      *) TF_LEGACY=1; TF_UNCERTAIN=1; continue ;;
    esac
    if [[ "$line" == /load[[:space:]]* ]]; then
      include="${line#/load}"
      include="${include#"${include%%[![:space:]]*}"}"
      case "$include" in
        '~/'*) include="$HOME/${include:2}" ;;
        /*) ;;
        *) TF_UNCERTAIN=1; continue ;;
      esac
      if [[ "$include" == "$CONFIG_DIR/capture.tf" || "$include" -ef "$CONFIG_DIR/capture.tf" ]]; then
        if ((depth)); then
          TF_NESTED_HOOKS=$((TF_NESTED_HOOKS + 1))
        else
          TF_DIRECT_HOOKS=$((TF_DIRECT_HOOKS + 1))
        fi
      elif ((depth)) || [[ ! -f "$include" || ! -r "$include" ]]; then
        TF_UNCERTAIN=1
      else
        TF_INCLUDES+=("$include")
      fi
    fi
  done < "$file"
}
inspect_tf_startup "$TF_STARTUP" 0
for include in "${TF_INCLUDES[@]}"; do inspect_tf_startup "$include" 1; done

DIRECT_HAS_CREDENTIAL=0
if [[ -e "$CONFIG_DIR/gateway.env" || -L "$CONFIG_DIR/gateway.env" ]]; then
  DIRECT_HAS_CREDENTIAL=1
fi
if [[ "$CONNECTION" == direct ]] && ((!DIRECT_HAS_CREDENTIAL)); then
  if ((BEHIND_NAT)); then
    DIRECT_EDGE_OPTIONS=(--behind-nat)
    "$BUNDLE_DIR/bin/imp-server" direct-edge dns "$PUBLIC_HOSTNAME" "${DIRECT_EDGE_OPTIONS[@]}"
  elif dns_error="$("$BUNDLE_DIR/bin/imp-server" direct-edge dns "$PUBLIC_HOSTNAME" 2>&1)"; then
    printf '%s\n' "$dns_error"
  elif [[ -t 0 && -t 1 ]] &&
       nat_address="$("$BUNDLE_DIR/bin/imp-server" direct-edge nat-candidate "$PUBLIC_HOSTNAME" 2>/dev/null)"; then
    printf '\n%s resolves to %s, but that address is not assigned\ndirectly to this machine.\n\n' "$PUBLIC_HOSTNAME" "$nat_address"
    printf 'Is this machine behind a router/NAT with TCP port 443 forwarded here?\n\n1. Yes, use NAT / port-forwarded Direct\n2. No, stop and fix DNS\n'
    read -r -p 'Choice [2]: ' nat_choice || die "NAT selection interrupted"
    [[ "$nat_choice" == 1 ]] || die "Direct stopped; fix DNS or forward TCP 443 and rerun with --behind-nat"
    BEHIND_NAT=1
    DIRECT_EDGE_OPTIONS=(--behind-nat)
    "$BUNDLE_DIR/bin/imp-server" direct-edge dns "$PUBLIC_HOSTNAME" "${DIRECT_EDGE_OPTIONS[@]}"
  else
    printf '%s\n' "$dns_error" >&2
    die "Direct DNS preflight failed; NAT requires explicit --behind-nat and one global A record with no AAAA"
  fi
  [[ -t 0 && -t 1 ]] || die "new Direct provisioning requires interactive stdin and stdout; rerun from a terminal"
  if ((BEHIND_NAT)); then
    PUBLIC_VERIFY_OPTIONS=(--verify-via-loopback)
  fi
fi

if [[ -e "$CURRENT_LINK" && ! -L "$CURRENT_LINK" ]]; then
  die "$CURRENT_LINK exists but is not a symlink"
fi

# Only this install's runtime, or its previous .venv layout, may be migrated.
for link in "$ACTION_LINK" "$FEED_WATCH_LINK" "$DIRECT_WSS_LINK"; do
  name="${link##*/}"
  if [[ -e "$link" && ! -L "$link" ]]; then
    die "$link exists but is not a symlink"
  fi
  if [[ -L "$link" ]]; then
    target="$(readlink "$link")"
    [[ "$target" == "$CURRENT_LINK/bin/$name" || "$target" == "$CURRENT_LINK/.venv/bin/$name" ]] ||
      die "$link points somewhere other than this install's runtime"
  fi
done

mkdir -p \
  "$RELEASES_DIR" \
  "$CONFIG_DIR" \
  "$STATE_DIR" \
  "$BIN_DIR" \
  "$SYSTEMD_DIR"

chmod 700 "$CONFIG_DIR" "$STATE_DIR"

FILES_BACKUP_DIR="$DATA_DIR/.install-backup.$$"
OLD_CURRENT=""
RELEASE_PLACED=0
CURRENT_SWITCHED=0
FILES_BACKUP_READY=0
TF_STARTUP_CHANGED=0
TF_STARTUP_BACKUP=""

RELAY_WAS_ACTIVE=0
FEED_WAS_ACTIVE=0
GATEWAY_WAS_ACTIVE=0
RELAY_WAS_ENABLED=0
FEED_WAS_ENABLED=0
GATEWAY_WAS_ENABLED=0
GATEWAY_CONNECTION_CHANGED=0
SSH_CONNECTION_COMMITTED=0
commit_status=0
EDGE_PENDING=0
DIRECT_PROVISIONING_STARTED=0
TARGET_UID="$(id -u)"

backup_path() {
  local source="$1"
  local key="$2"

  if [[ -e "$source" || -L "$source" ]]; then
    cp -a -- "$source" "$FILES_BACKUP_DIR/$key"
  fi
}

restore_path() {
  local target="$1"
  local key="$2"

  rm -f -- "$target" || return

  if [[ -e "$FILES_BACKUP_DIR/$key" ||
        -L "$FILES_BACKUP_DIR/$key" ]]; then
    cp -a -- "$FILES_BACKUP_DIR/$key" "$target"
  fi
}

verify_release_runtime() {
  local release="$1"
  local expected_bundle_id="$2"
  local actual_bundle_id=""

  [[ -f "$release/BUNDLE_SHA256" ]] ||
    die "existing Imp release is missing its bundle identity: $release"

  actual_bundle_id="$(tr -d '\r\n' < "$release/BUNDLE_SHA256")"

  [[ "$actual_bundle_id" == "$expected_bundle_id" ]] ||
    die "Imp release $VERSION already exists from a different bundle"

  [[ -x "$release/bin/imp-server" ]] ||
    die "Imp release is missing its runtime: $release"

  "$release/bin/imp-server" runtime-check "$VERSION"
}

if [[ -L "$CURRENT_LINK" ]]; then
  OLD_CURRENT="$(readlink "$CURRENT_LINK")"
fi

# --no-start still needs the incoming gateway state to select retained Direct migration.
if command -v systemctl >/dev/null 2>&1; then
  if systemctl --user is-active --quiet imp-relay.service; then
    RELAY_WAS_ACTIVE=1
  fi

  if systemctl --user is-active --quiet imp-feed.service; then
    FEED_WAS_ACTIVE=1
  fi

  if systemctl --user is-active --quiet imp-gateway.service; then
    GATEWAY_WAS_ACTIVE=1
  fi

  if systemctl --user is-enabled --quiet imp-relay.service; then
    RELAY_WAS_ENABLED=1
  fi

  if systemctl --user is-enabled --quiet imp-feed.service; then
    FEED_WAS_ENABLED=1
  fi

  if systemctl --user is-enabled --quiet imp-gateway.service; then
    GATEWAY_WAS_ENABLED=1
  fi
fi

cleanup() {
  status=$?
  set +e
  # A recorded SSH commit interruption remains authoritative through trap restoration.
  if ((commit_status != 0)); then status=$commit_status; fi
  trap - EXIT
  trap '' INT TERM HUP PIPE
  restoration_failed=0

  if ((status != 0)); then
    if ((EDGE_PENDING)); then
      "$BUNDLE_DIR/bin/imp-server" direct-edge rollback "$TARGET_UID" ||
        printf 'error: public-edge restoration failed; inspect imp-caddy.service before retrying\n' >&2 || true
    fi
    # The child may have handed off its token before a pending parent signal runs.
    # Presence also retains malformed/incomplete state for recovery; never inspect its contents here.
    if ((DIRECT_PROVISIONING_STARTED)) &&
       [[ -e "$CONFIG_DIR/gateway.env" || -L "$CONFIG_DIR/gateway.env" ]]; then
      printf 'error: Direct provisioning may have handed off a credential; local installation retained, public-edge recovery required\n' >&2 || true
      printf 'Inspect imp-direct-wss status and the public edge before retrying; do not rotate implicitly.\n' >&2 || true
      rm -rf -- "$FILES_BACKUP_DIR"
      exit "$status"
    fi
    if ((CURRENT_SWITCHED)); then
      if [[ -n "$OLD_CURRENT" ]]; then
        rollback_link="$DATA_DIR/.current.rollback.$$"
        rm -f -- "$rollback_link" &&
          ln -s "$OLD_CURRENT" "$rollback_link" &&
          mv -Tf -- "$rollback_link" "$CURRENT_LINK" || restoration_failed=1
      else
        rm -f -- "$CURRENT_LINK" || restoration_failed=1
      fi
    fi

    if ((RELEASE_PLACED && !restoration_failed)); then
      rm -rf -- "$RELEASE_DIR" || restoration_failed=1
    fi

    if ((FILES_BACKUP_READY)); then
      restore_path "$ACTION_LINK" action-consumer || restoration_failed=1
      restore_path "$FEED_WATCH_LINK" feed-watch || restoration_failed=1
      restore_path "$CONFIG_DIR/capture.tf" capture.tf || restoration_failed=1
      restore_path "$SYSTEMD_DIR/imp-relay.service" imp-relay.service || restoration_failed=1
      restore_path "$SYSTEMD_DIR/imp-feed.service" imp-feed.service || restoration_failed=1
      restore_path "$SYSTEMD_DIR/imp-gateway.service" imp-gateway.service || restoration_failed=1
      restore_path "$DIRECT_WSS_LINK" direct-wss || restoration_failed=1
    fi

    if ((TF_STARTUP_CHANGED)) &&
       [[ -n "$TF_STARTUP_BACKUP" &&
          -f "$TF_STARTUP_BACKUP" ]]; then
      cp -p -- "$TF_STARTUP_BACKUP" "$TF_STARTUP" &&
        rm -f -- "$TF_STARTUP_BACKUP" || restoration_failed=1
    fi

    if ((CURRENT_SWITCHED && START_SERVICES)); then
      printf 'Restoring previous Imp installation after failed activation...\n' >&2 || true

      systemctl --user daemon-reload || true
      systemctl --user reset-failed \
        imp-relay.service \
        imp-feed.service \
        imp-gateway.service || true

      if ((RELAY_WAS_ENABLED)); then
        systemctl --user enable imp-relay.service || true
      else
        systemctl --user disable imp-relay.service || true
      fi

      if ((FEED_WAS_ENABLED)); then
        systemctl --user enable imp-feed.service || true
      else
        systemctl --user disable imp-feed.service || true
      fi

      if ((RELAY_WAS_ACTIVE)); then
        systemctl --user restart imp-relay.service || true
      else
        systemctl --user stop imp-relay.service || true
      fi

      if ((FEED_WAS_ACTIVE)); then
        systemctl --user restart imp-feed.service || true
      else
        systemctl --user stop imp-feed.service || true
      fi

      if ((GATEWAY_CONNECTION_CHANGED && !SSH_CONNECTION_COMMITTED)); then
        if ((GATEWAY_WAS_ENABLED)); then
          systemctl --user enable imp-gateway.service || true
        else
          systemctl --user disable imp-gateway.service || true
        fi
        if ((GATEWAY_WAS_ACTIVE)); then
          systemctl --user restart imp-gateway.service || true
        else
          systemctl --user stop imp-gateway.service || true
        fi
      elif ((GATEWAY_WAS_ACTIVE && !SSH_CONNECTION_COMMITTED)); then
        systemctl --user restart imp-gateway.service || true
      fi
    fi
  fi

  if ((restoration_failed)); then
    printf 'error: filesystem restoration incomplete; rollback backups retained at %s\n' "$FILES_BACKUP_DIR" >&2 || true
  elif [[ -d "$FILES_BACKUP_DIR" ]]; then
    rm -rf -- "$FILES_BACKUP_DIR"
  fi

  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

if [[ -e "$RELEASE_DIR" ]]; then
  [[ -d "$RELEASE_DIR" ]] ||
    die "Imp release path exists but is not a directory: $RELEASE_DIR"

  printf 'Reusing existing Imp server runtime at %s...\n' "$RELEASE_DIR"
  verify_release_runtime "$RELEASE_DIR" "$BUNDLE_ID"
else
  printf 'Installing Imp server runtime at %s...\n' "$RELEASE_DIR"

  mkdir -p "$RELEASE_DIR"
  RELEASE_PLACED=1

  # cp -a preserves the hardlinked role aliases; validation runs on the installed copy.
  cp -a -- "$BUNDLE_DIR/bin" "$RELEASE_DIR/bin"
  cp "$VERSION_FILE" "$RELEASE_DIR/VERSION"
  printf '%s\n' "$BUNDLE_ID" > "$RELEASE_DIR/BUNDLE_SHA256"

  verify_release_runtime "$RELEASE_DIR" "$BUNDLE_ID"
  printf 'Imp runtime verified at %s\n' "$VERSION"
fi

NEXT_LINK="$DATA_DIR/.current.$$"
rm -f -- "$NEXT_LINK"
ln -s "$RELEASE_DIR" "$NEXT_LINK"
mv -Tf -- "$NEXT_LINK" "$CURRENT_LINK"
CURRENT_SWITCHED=1

rm -rf -- "$FILES_BACKUP_DIR"
mkdir -p "$FILES_BACKUP_DIR"

backup_path "$ACTION_LINK" action-consumer
backup_path "$FEED_WATCH_LINK" feed-watch
backup_path "$DIRECT_WSS_LINK" direct-wss

backup_path "$CONFIG_DIR/capture.tf" capture.tf
backup_path "$SYSTEMD_DIR/imp-relay.service" imp-relay.service
backup_path "$SYSTEMD_DIR/imp-feed.service" imp-feed.service
backup_path "$SYSTEMD_DIR/imp-gateway.service" imp-gateway.service

FILES_BACKUP_READY=1

ln -sfn \
  "$CURRENT_LINK/bin/imp-action-consumer" \
  "$ACTION_LINK"
ln -sfn \
  "$CURRENT_LINK/bin/imp-feed-watch" \
  "$FEED_WATCH_LINK"
ln -sfn \
  "$CURRENT_LINK/bin/imp-direct-wss" \
  "$DIRECT_WSS_LINK"

install -m 600 \
  "$BUNDLE_DIR/capture.tf" \
  "$CONFIG_DIR/capture.tf"

for unit in imp-relay.service imp-feed.service imp-gateway.service; do
  install -m 644 \
    "$BUNDLE_DIR/systemd/$unit" \
    "$SYSTEMD_DIR/$unit"
done

if ((TF_EXACT_HOOK)); then
  TF_STATUS='already exact; preserved without moving or rewriting'
elif ((TF_DIRECT_HOOKS)); then
  TF_STATUS='existing hook preserved; no duplicate added'
elif ((TF_NESTED_HOOKS)); then
  TF_STATUS='nested hook preserved; no duplicate added'
elif ((TF_UNCERTAIN)); then
  TF_STATUS='load order not established; manual integration required'
else
  TF_STARTUP_BACKUP="$(mktemp "${TF_STARTUP}.imp-backup.XXXXXXXX")"
  cp -p -- "$TF_STARTUP" "$TF_STARTUP_BACKUP"
  TF_STARTUP_CHANGED=1
  { printf '%s%s' "$LOAD_LINE" "$TF_NEWLINE"; cat -- "$TF_STARTUP_BACKUP"; } > "$TF_STARTUP"
  chmod --reference="$TF_STARTUP_BACKUP" -- "$TF_STARTUP"
  TF_STATUS='inserted before original startup commands'
  printf 'Backup: %s\n' "$TF_STARTUP_BACKUP"
fi

if ((START_SERVICES)); then
  printf 'Installing Imp systemd user services...\n'

  systemctl --user daemon-reload
  systemctl --user reset-failed \
    imp-relay.service \
    imp-feed.service \
    imp-gateway.service || true

  systemctl --user enable \
    imp-relay.service \
    imp-feed.service

  # restart also starts inactive units, and guarantees an upgrade actually
  # begins using the runtime selected by the new current symlink.
  systemctl --user restart imp-relay.service
  systemctl --user restart imp-feed.service

  if ((GATEWAY_WAS_ACTIVE)) && [[ "$CONNECTION" != ssh ]]; then
    systemctl --user restart imp-gateway.service
  fi

  systemctl --user is-active --quiet imp-relay.service ||
    die "imp-relay.service did not become active"

  systemctl --user is-active --quiet imp-feed.service ||
    die "imp-feed.service did not become active"

  if ((GATEWAY_WAS_ACTIVE)) && [[ "$CONNECTION" != ssh ]]; then
    systemctl --user is-active --quiet imp-gateway.service ||
      die "imp-gateway.service did not become active"
  fi
fi

if [[ "$CONNECTION" == ssh ]] && ((START_SERVICES)); then
  GATEWAY_CONNECTION_CHANGED=1
  systemctl --user stop imp-gateway.service
  systemctl --user disable imp-gateway.service
  edge_hostname="$("$BUNDLE_DIR/bin/imp-server" direct-edge owned-hostname "$TARGET_UID")"
  EDGE_PENDING=1
  "$BUNDLE_DIR/bin/imp-server" direct-edge disable "$TARGET_UID"
  # Defer interruptions through child-result reconciliation against durable owned state.
  commit_signal=0
  trap '((commit_signal != 0)) || commit_signal=$((commit_status != 0 ? commit_status : 130))' INT
  trap '((commit_signal != 0)) || commit_signal=$((commit_status != 0 ? commit_status : 143))' TERM
  trap '((commit_signal != 0)) || commit_signal=$((commit_status != 0 ? commit_status : 129))' HUP
  "$BUNDLE_DIR/bin/imp-server" direct-edge commit "$TARGET_UID" || commit_status=$?
  if ((commit_status != 0)); then
    # Establish inherited protection before creating any reconciliation process.
    trap '' INT TERM HUP
    ((commit_signal != 0)) || commit_signal=$commit_status
  fi
  if ((commit_status == 0)) ||
     { [[ -n "$edge_hostname" ]] &&
       "$BUNDLE_DIR/bin/imp-server" direct-edge ssh-committed "$edge_hostname" "$TARGET_UID"; }; then
    SSH_CONNECTION_COMMITTED=1
    EDGE_PENDING=0
  fi
  ((commit_signal == 0)) || commit_status=$commit_signal
  trap 'exit 130' INT
  trap 'exit 143' TERM
  trap 'exit 129' HUP
  ((commit_status == 0)) || exit "$commit_status"
elif [[ "$CONNECTION" == direct ]] && ((!DIRECT_HAS_CREDENTIAL)); then
  EDGE_PENDING=1
  "$BUNDLE_DIR/bin/imp-server" direct-edge enable "$PUBLIC_HOSTNAME" "$TARGET_UID" "${DIRECT_EDGE_OPTIONS[@]}"
  if [[ -e "$CONFIG_DIR/gateway.env" || -L "$CONFIG_DIR/gateway.env" ]]; then
    die "a Direct credential appeared during edge preparation; preserved without a verified token handoff"
  fi
  # Credentials and their one-time terminal output belong exclusively to this command.
  DIRECT_PROVISIONING_STARTED=1
  "$DIRECT_WSS_LINK" setup --public-url "wss://$PUBLIC_HOSTNAME/state" "${PUBLIC_VERIFY_OPTIONS[@]}"
  "$BUNDLE_DIR/bin/imp-server" direct-edge commit "$TARGET_UID"
  EDGE_PENDING=0
elif [[ "$CONNECTION" != ssh ]] && ((EXISTING_INSTALL)) &&
     { [[ "$CONNECTION" == direct ]] || ((GATEWAY_WAS_ENABLED || GATEWAY_WAS_ACTIVE)); }; then
  # Keep assignment outside a condition so ownership/query failures abort the install.
  edge_migration="$("$BUNDLE_DIR/bin/imp-server" direct-edge migration-needed "$TARGET_UID")"
  case "$edge_migration" in
    yes)
      EDGE_PENDING=1
      "$BUNDLE_DIR/bin/imp-server" direct-edge migrate "$TARGET_UID"
      "$BUNDLE_DIR/bin/imp-server" direct-edge commit "$TARGET_UID"
      EDGE_PENDING=0
      ;;
    no) ;;
    *) die "unexpected public-edge migration status" ;;
  esac
fi

printf '\nImp server %s installed.\n' "$VERSION"
printf 'Runtime: %s\n' "$CURRENT_LINK"
printf 'Relay:   ws://127.0.0.1:8787\n'
printf 'Adapter: TinyFugue\n'

if ((START_SERVICES)); then
  printf 'Services: imp-relay and imp-feed are enabled and active.\n'
else
  printf 'Services: installed but not started (--no-start).\n'
fi

printf 'TinyFugue startup: %s\n' "$TF_STARTUP"
printf 'TinyFugue integration: %s.\n' "$TF_STATUS"
if ((TF_DIRECT_HOOKS || TF_NESTED_HOOKS || TF_UNCERTAIN)); then
  printf 'warning: TinyFugue load order not established; inspect startup commands and nested loads.\n'
fi
if ((TF_BOM)); then
  printf 'warning: BOM-prefixed startup preserved; native TinyFugue BOM handling is not established. Review encoding and hook execution manually.\n'
fi
if ((TF_DIRECT_HOOKS + TF_NESTED_HOOKS > 1)); then
  printf 'warning: multiple Imp hook loads found; duplicate loading is possible; preserved unchanged.\n'
fi
if [[ "$TF_STATUS" == 'load order not established; manual integration required' ]]; then
  printf 'No new hook inserted: legacy syntax, unsupported loads, or a BOM require manual review; preserve existing file encoding when editing.\n'
  printf 'Manually arrange this before connection/login commands: %s\n' "$LOAD_LINE"
fi
if ((TF_LEGACY)); then
  printf 'Possible legacy world-list startup preserved: place the hook after legacy world definitions but before the first connection/login command, or convert world definitions to /addworld before using an early /load. Do not blindly prepend the hook.\n'
fi
printf 'Confirm your actual TinyFugue invocation (for example, tf -f FILE) loads this selected file and the hook before connection/login commands.\n'
printf 'For the current TF session, manually run: %s\n' "$LOAD_LINE"
printf 'A matching normalized checkpoint can recover prior authoritative identity without reconnecting; without that identity, recovery is not guaranteed.\n'

if command -v loginctl >/dev/null 2>&1; then
  LINGER="$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || true)"
  if [[ "$LINGER" != "yes" ]]; then
    printf '\nwarning: systemd user lingering is not enabled.\n' >&2
    printf 'Enable lingering so Imp survives logout and reboot:\n' >&2
    printf '  loginctl enable-linger %q\n' "$USER" >&2
  fi
fi

case "$CONNECTION" in
  ssh)
    if ((!START_SERVICES)); then
      printf '\nServices were not started; existing gateway and public-edge state is preserved.\n'
    fi
    printf '\nImp is ready for SSH connections.\n\nIn Settings -> Connection choose:\n'
    printf '  Managed SSH  - Imp manages the SSH tunnel\n  External SSH - you manage the SSH tunnel\n'
    ;;
  direct)
    if ((DIRECT_HAS_CREDENTIAL)); then
      printf '\nExisting Direct credential and public edge preserved; public authentication was not verified.\n'
      printf 'No token was generated or rotated. Keep using the existing URL/token.\n'
      printf 'For a deliberate new handoff, first configure/verify the public edge and ensure the gateway is active, then run:\n'
      printf '  %q rotate --public-url %q\n' "$DIRECT_WSS_LINK" "wss://$PUBLIC_HOSTNAME/state"
      printf 'For a router/NAT edge, add --verify-via-loopback to that command; leave the retained edge topology unchanged.\n'
    else
      printf '\nImp Direct WSS is ready.\n\nWSS URL:\n  wss://%s/state\n\n' "$PUBLIC_HOSTNAME"
      printf "The pairing token was displayed above by Imp's provisioning step.\n\n"
      printf 'Desktop:\n  Settings -> Connection -> Direct\n\n'
      printf 'Enter the WSS URL and pairing token, save, then restart Imp.\n'
    fi
    ;;
  *)
    printf '\nConnection configuration preserved; Direct remains unconfigured on a fresh install.\n'
    printf 'To configure later, rerun with --connection ssh or --connection direct --hostname HOST.\n'
    ;;
esac
