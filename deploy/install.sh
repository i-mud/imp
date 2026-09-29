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

while (($#)); do
  case "$1" in
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

command -v python3.12 >/dev/null 2>&1 ||
  die "Python 3.12 is required"
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

VERSION="$(tr -d '\r\n' < "$VERSION_FILE")"

[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.+-][0-9A-Za-z.-]+)?$ ]] ||
  die "invalid bundle version: $VERSION"

printf 'Verifying Imp server bundle...\n'
(
  cd "$BUNDLE_DIR"
  sha256sum -c SHA256SUMS
)

python3.12 - "$BUNDLE_DIR" <<'PY_BUNDLE_INVENTORY'
from pathlib import Path
import string
import sys

root = Path(sys.argv[1])
manifest = root / "SHA256SUMS"

expected: list[str] = []

for raw in manifest.read_text(encoding="utf-8").splitlines():
    parts = raw.split(maxsplit=1)
    if len(parts) != 2:
        raise SystemExit("invalid SHA256SUMS entry")

    digest, name = parts
    name = name.removeprefix("*")
    relative = Path(name)

    if (
        len(digest) != 64
        or any(char not in string.hexdigits for char in digest)
        or not name
        or relative.is_absolute()
        or ".." in relative.parts
        or name == "SHA256SUMS"
    ):
        raise SystemExit(f"invalid SHA256SUMS entry: {raw!r}")

    expected.append(relative.as_posix())

if len(expected) != len(set(expected)):
    raise SystemExit("SHA256SUMS contains duplicate paths")

actual: list[str] = []

for path in root.rglob("*"):
    relative = path.relative_to(root).as_posix()

    if path.is_symlink():
        raise SystemExit(f"bundle contains an unexpected symlink: {relative}")

    if path.is_file():
        if relative != "SHA256SUMS":
            actual.append(relative)
    elif not path.is_dir():
        raise SystemExit(f"bundle contains an unsupported path type: {relative}")

expected_set = set(expected)
actual_set = set(actual)

if actual_set != expected_set:
    missing = sorted(expected_set - actual_set)
    extra = sorted(actual_set - expected_set)
    raise SystemExit(
        "bundle contents do not match SHA256SUMS: "
        f"missing={missing!r} extra={extra!r}"
    )
PY_BUNDLE_INVENTORY

BUNDLE_ID=""
read -r BUNDLE_ID _ < <(sha256sum "$BUNDLE_DIR/SHA256SUMS")

[[ "$BUNDLE_ID" =~ ^[0-9a-f]{64}$ ]] ||
  die "could not identify verified server bundle"

if [[ ! -f "$TF_STARTUP" ]]; then
  if ((TF_STARTUP_EXPLICIT)); then
    die "TinyFugue startup file does not exist: $TF_STARTUP"
  fi

  die "default TinyFugue startup file does not exist: $TF_STARTUP; use --tf-startup FILE for a different startup file"
fi

DATA_DIR="$HOME/.local/share/imp"
RELEASES_DIR="$DATA_DIR/releases"
RELEASE_DIR="$RELEASES_DIR/$VERSION"
CURRENT_LINK="$DATA_DIR/current"

CONFIG_DIR="$HOME/.config/imp"
STATE_DIR="$HOME/.local/state/imp"
BIN_DIR="$HOME/.local/bin"
SYSTEMD_DIR="$HOME/.config/systemd/user"

ACTION_LINK="$BIN_DIR/imp-action-consumer"

if [[ -e "$CURRENT_LINK" && ! -L "$CURRENT_LINK" ]]; then
  die "$CURRENT_LINK exists but is not a symlink"
fi

if [[ -e "$ACTION_LINK" && ! -L "$ACTION_LINK" ]]; then
  die "$ACTION_LINK exists but is not a symlink"
fi

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

  rm -f -- "$target"

  if [[ -e "$FILES_BACKUP_DIR/$key" ||
        -L "$FILES_BACKUP_DIR/$key" ]]; then
    cp -a -- "$FILES_BACKUP_DIR/$key" "$target"
  fi
}

verify_release_runtime() {
  local release="$1"
  local expected_bundle_id="$2"
  local actual_bundle_id=""
  local entry=""
  local script=""
  local shebang=""
  local interpreter=""

  [[ -f "$release/BUNDLE_SHA256" ]] ||
    die "existing Imp release is missing its bundle identity: $release"

  actual_bundle_id="$(tr -d '\r\n' < "$release/BUNDLE_SHA256")"

  [[ "$actual_bundle_id" == "$expected_bundle_id" ]] ||
    die "Imp release $VERSION already exists from a different bundle"

  [[ -x "$release/.venv/bin/python" ]] ||
    die "Imp release is missing its Python runtime: $release"

  "$release/.venv/bin/python" - "$VERSION" <<'PY_VERIFY_RUNTIME'
from importlib.metadata import version
import sys

expected = sys.argv[1]

for package in ("imp-relay", "imp-tinyfugue"):
    actual = version(package)
    if actual != expected:
        raise SystemExit(
            f"{package}: expected version {expected}, installed {actual}"
        )
PY_VERIFY_RUNTIME

  for entry in imp-relay imp-gateway imp-feed imp-action-consumer; do
    script="$release/.venv/bin/$entry"

    [[ -x "$script" ]] ||
      die "Imp release is missing console script: $script"

    IFS= read -r shebang < "$script" ||
      die "could not read console script: $script"

    [[ "$shebang" == '#!'* ]] ||
      die "Imp console script has no interpreter: $script"

    interpreter="${shebang#\#!}"

    [[ "$interpreter" == "$release/.venv/bin/"* ]] ||
      die "Imp console script points outside its release: $entry -> $interpreter"

    [[ -x "$interpreter" ]] ||
      die "Imp console script has missing interpreter: $entry -> $interpreter"
  done
}

if [[ -L "$CURRENT_LINK" ]]; then
  OLD_CURRENT="$(readlink "$CURRENT_LINK")"
fi

if ((START_SERVICES)); then
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
fi

cleanup() {
  status=$?
  trap - EXIT

  if ((status != 0)); then
    if ((CURRENT_SWITCHED)); then
      if [[ -n "$OLD_CURRENT" ]]; then
        rollback_link="$DATA_DIR/.current.rollback.$$"
        rm -f -- "$rollback_link"
        ln -s "$OLD_CURRENT" "$rollback_link"
        mv -Tf -- "$rollback_link" "$CURRENT_LINK"
      else
        rm -f -- "$CURRENT_LINK"
      fi
    fi

    if ((RELEASE_PLACED)); then
      rm -rf -- "$RELEASE_DIR"
    fi

    if ((FILES_BACKUP_READY)); then
      restore_path "$ACTION_LINK" action-consumer
      restore_path "$CONFIG_DIR/capture.tf" capture.tf
      restore_path "$SYSTEMD_DIR/imp-relay.service" imp-relay.service
      restore_path "$SYSTEMD_DIR/imp-feed.service" imp-feed.service
      restore_path "$SYSTEMD_DIR/imp-gateway.service" imp-gateway.service
    fi

    if ((TF_STARTUP_CHANGED)) &&
       [[ -n "$TF_STARTUP_BACKUP" &&
          -f "$TF_STARTUP_BACKUP" ]]; then
      cp -p -- "$TF_STARTUP_BACKUP" "$TF_STARTUP"
      rm -f -- "$TF_STARTUP_BACKUP"
    fi

    if ((CURRENT_SWITCHED && START_SERVICES)); then
      printf 'Restoring previous Imp installation after failed activation...\n' >&2

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

      if ((GATEWAY_WAS_ACTIVE)); then
        systemctl --user restart imp-gateway.service || true
      fi
    fi
  fi

  if [[ -d "$FILES_BACKUP_DIR" ]]; then
    rm -rf -- "$FILES_BACKUP_DIR"
  fi

  exit "$status"
}
trap cleanup EXIT

if [[ -e "$RELEASE_DIR" ]]; then
  [[ -d "$RELEASE_DIR" ]] ||
    die "Imp release path exists but is not a directory: $RELEASE_DIR"

  printf 'Reusing existing Imp server runtime at %s...\n' "$RELEASE_DIR"
  verify_release_runtime "$RELEASE_DIR" "$BUNDLE_ID"
else
  printf 'Creating Python 3.12 runtime at %s...\n' "$RELEASE_DIR"

  mkdir -p "$RELEASE_DIR"
  RELEASE_PLACED=1

  if ! python3.12 -m venv "$RELEASE_DIR/.venv"; then
    die "could not create a Python 3.12 venv; install the Python 3.12 venv support package"
  fi

  "$RELEASE_DIR/.venv/bin/python" -m pip install \
    --no-index \
    --no-cache-dir \
    --find-links "$BUNDLE_DIR/wheels" \
    "imp-relay==$VERSION" \
    "imp-tinyfugue==$VERSION"

  "$RELEASE_DIR/.venv/bin/python" -m pip check

  cp "$VERSION_FILE" "$RELEASE_DIR/VERSION"
  printf '%s\n' "$BUNDLE_ID" > "$RELEASE_DIR/BUNDLE_SHA256"

  verify_release_runtime "$RELEASE_DIR" "$BUNDLE_ID"
  printf 'Imp Python packages and console scripts verified at %s\n' "$VERSION"
fi

NEXT_LINK="$DATA_DIR/.current.$$"
rm -f -- "$NEXT_LINK"
ln -s "$RELEASE_DIR" "$NEXT_LINK"
mv -Tf -- "$NEXT_LINK" "$CURRENT_LINK"
CURRENT_SWITCHED=1

rm -rf -- "$FILES_BACKUP_DIR"
mkdir -p "$FILES_BACKUP_DIR"

backup_path "$ACTION_LINK" action-consumer
backup_path "$CONFIG_DIR/capture.tf" capture.tf
backup_path "$SYSTEMD_DIR/imp-relay.service" imp-relay.service
backup_path "$SYSTEMD_DIR/imp-feed.service" imp-feed.service
backup_path "$SYSTEMD_DIR/imp-gateway.service" imp-gateway.service

FILES_BACKUP_READY=1

ln -sfn \
  "$CURRENT_LINK/.venv/bin/imp-action-consumer" \
  "$ACTION_LINK"

install -m 600 \
  "$BUNDLE_DIR/capture.tf" \
  "$CONFIG_DIR/capture.tf"

for unit in imp-relay.service imp-feed.service imp-gateway.service; do
  install -m 644 \
    "$BUNDLE_DIR/systemd/$unit" \
    "$SYSTEMD_DIR/$unit"
done

LOAD_LINE='/load ~/.config/imp/capture.tf'
TF_CONFIGURED=0

if grep -Fxq "$LOAD_LINE" "$TF_STARTUP"; then
  printf 'TinyFugue startup already contains the Imp hook: %s\n' "$TF_STARTUP"
else
  BACKUP="${TF_STARTUP}.imp-backup.$(date +%Y%m%d%H%M%S)"
  cp -p -- "$TF_STARTUP" "$BACKUP"
  TF_STARTUP_BACKUP="$BACKUP"
  TF_STARTUP_CHANGED=1
  printf '\n%s\n' "$LOAD_LINE" >> "$TF_STARTUP"
  printf 'Updated TinyFugue startup file: %s\n' "$TF_STARTUP"
  printf 'Backup: %s\n' "$BACKUP"
fi

TF_CONFIGURED=1

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

  if ((GATEWAY_WAS_ACTIVE)); then
    systemctl --user restart imp-gateway.service
  fi

  systemctl --user is-active --quiet imp-relay.service ||
    die "imp-relay.service did not become active"

  systemctl --user is-active --quiet imp-feed.service ||
    die "imp-feed.service did not become active"

  if ((GATEWAY_WAS_ACTIVE)); then
    systemctl --user is-active --quiet imp-gateway.service ||
      die "imp-gateway.service did not become active"
  fi
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

if ((TF_CONFIGURED)); then
  printf 'TinyFugue startup integration: configured.\n'
else
  printf '\nTinyFugue startup integration still requires this line:\n'
  printf '  %s\n' "$LOAD_LINE"
  printf 'Re-run with --tf-startup FILE to install it automatically.\n'
fi

if command -v loginctl >/dev/null 2>&1; then
  LINGER="$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || true)"
  if [[ "$LINGER" != "yes" ]]; then
    printf '\nwarning: systemd user lingering is not enabled.\n' >&2
    printf 'Enable lingering so Imp survives logout and reboot:\n' >&2
    printf '  loginctl enable-linger %q\n' "$USER" >&2
  fi
fi

printf '\nDirect WSS gateway enablement was not changed.\n'
