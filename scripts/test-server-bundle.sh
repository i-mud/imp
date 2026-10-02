#!/usr/bin/env bash
set -euo pipefail

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

[[ $# -eq 1 ]] || die "usage: $0 ARCHIVE"

ARCHIVE="$(realpath "$1")"

[[ -f "$ARCHIVE" ]] || die "archive not found: $ARCHIVE"

for command in python3.12 tar sha256sum readlink stat; do
  command -v "$command" >/dev/null 2>&1 ||
    die "required command not found: $command"
done

if [[ -f "$ARCHIVE.sha256" ]]; then
  printf '=== outer checksum ===\n'
  (
    cd "$(dirname "$ARCHIVE")"
    sha256sum -c "$(basename "$ARCHIVE").sha256"
  )
fi

WORK_DIR="$(mktemp -d)"
trap 'rm -rf -- "$WORK_DIR"' EXIT

EXTRACT_DIR="$WORK_DIR/extract"
mkdir -p "$EXTRACT_DIR"

tar -xzf "$ARCHIVE" -C "$EXTRACT_DIR"

mapfile -t TOP_LEVEL < <(
  find "$EXTRACT_DIR" \
    -mindepth 1 \
    -maxdepth 1 \
    -type d \
    -print
)

[[ ${#TOP_LEVEL[@]} -eq 1 ]] ||
  die "expected exactly one top-level bundle directory"

BUNDLE_DIR="${TOP_LEVEL[0]}"
VERSION="$(tr -d '\r\n' < "$BUNDLE_DIR/VERSION")"

printf '\n=== bundle ===\n'
printf 'version=%s\n' "$VERSION"
printf 'directory=%s\n' "$BUNDLE_DIR"

(
  cd "$BUNDLE_DIR"
  sha256sum -c SHA256SUMS
)

assert_no_debris() {
  local home="$1"
  local -a debris=()

  mapfile -t debris < <(
    find "$home" \
      -name '.*.tmp.*' \
      -o -name '.*.previous.*' \
      -o -name '.install-backup.*'
  )

  if ((${#debris[@]} != 0)); then
    printf 'unexpected installer debris:\n' >&2
    printf '  %s\n' "${debris[@]}" >&2
    exit 1
  fi
}

printf '\n=== clean install + idempotency ===\n'

HOME_DIR="$WORK_DIR/home"
mkdir -p "$HOME_DIR"

printf '/echo existing TinyFugue startup\n' > "$HOME_DIR/.tfrc"

HOME="$HOME_DIR" \
  "$BUNDLE_DIR/install.sh" \
  --no-start

HOME="$HOME_DIR" \
  "$BUNDLE_DIR/install.sh" \
  --no-start

EXPECTED_RELEASE="$HOME_DIR/.local/share/imp/releases/$VERSION"

[[ "$(readlink "$HOME_DIR/.local/share/imp/current")" == "$EXPECTED_RELEASE" ]] ||
  die "current symlink does not select $VERSION"

[[ "$(
  readlink "$HOME_DIR/.local/bin/imp-action-consumer"
)" == "$HOME_DIR/.local/share/imp/current/.venv/bin/imp-action-consumer" ]] ||
  die "action-consumer symlink is incorrect"

mapfile -t WEBSOCKETS_WHEELS < <(
  find "$BUNDLE_DIR/wheels" \
    -maxdepth 1 \
    -type f \
    -name 'websockets-*.whl' \
    -print
)

[[ ${#WEBSOCKETS_WHEELS[@]} -eq 1 ]] ||
  die "expected exactly one websockets wheel"

WEBSOCKETS_WHEEL="$(basename "${WEBSOCKETS_WHEELS[0]}")"
EXPECTED_WEBSOCKETS="${WEBSOCKETS_WHEEL#websockets-}"
EXPECTED_WEBSOCKETS="${EXPECTED_WEBSOCKETS%%-*}"

"$HOME_DIR/.local/share/imp/current/.venv/bin/python" \
  - "$VERSION" "$EXPECTED_WEBSOCKETS" <<'PY_VERIFY'
from importlib.metadata import version
import sys

expected_imp = sys.argv[1]
expected_websockets = sys.argv[2]

assert version("imp-relay") == expected_imp
assert version("imp-adapter") == expected_imp
assert version("imp-tinyfugue") == expected_imp
assert version("websockets") == expected_websockets

print("package versions: OK")
PY_VERIFY

for entry in imp-relay imp-gateway imp-feed imp-action-consumer; do
  script="$EXPECTED_RELEASE/.venv/bin/$entry"

  [[ -x "$script" ]] ||
    die "console script is not executable: $entry"

  IFS= read -r shebang < "$script" ||
    die "could not read console script: $entry"

  [[ "$shebang" == '#!'* ]] ||
    die "console script has no shebang: $entry"

  interpreter="${shebang#\#!}"

  [[ "$interpreter" == "$EXPECTED_RELEASE/.venv/bin/"* ]] ||
    die "console script points outside final release: $entry -> $interpreter"

  [[ -x "$interpreter" ]] ||
    die "console script interpreter does not exist: $entry -> $interpreter"
done

printf 'console script interpreters: OK\n'

[[ "$(
  grep -Fxc '/load ~/.config/imp/capture.tf' "$HOME_DIR/.tfrc"
)" -eq 1 ]] ||
  die "TinyFugue load line is not idempotent"

mapfile -t TF_BACKUPS < <(
  find "$HOME_DIR" \
    -maxdepth 1 \
    -type f \
    -name '.tfrc.imp-backup.*' \
    -print
)

[[ ${#TF_BACKUPS[@]} -eq 1 ]] ||
  die "expected exactly one TinyFugue startup backup"

[[ "$(stat -c '%a' "$HOME_DIR/.config/imp")" == "700" ]] ||
  die "Imp config directory mode is not 700"

[[ "$(stat -c '%a' "$HOME_DIR/.local/state/imp")" == "700" ]] ||
  die "Imp state directory mode is not 700"

[[ "$(stat -c '%a' "$HOME_DIR/.config/imp/capture.tf")" == "600" ]] ||
  die "capture.tf mode is not 600"

grep -Fxq \
  'ExecStart=%h/.local/share/imp/current/.venv/bin/imp-relay --host 127.0.0.1 --port 8787' \
  "$HOME_DIR/.config/systemd/user/imp-relay.service"

grep -Fxq \
  'ExecStart=%h/.local/share/imp/current/.venv/bin/imp-feed' \
  "$HOME_DIR/.config/systemd/user/imp-feed.service"

grep -Fxq \
  'ExecStart=%h/.local/share/imp/current/.venv/bin/imp-gateway --host 127.0.0.1 --port 8788 --relay-url ws://127.0.0.1:8787' \
  "$HOME_DIR/.config/systemd/user/imp-gateway.service"

assert_no_debris "$HOME_DIR"

printf 'clean install: OK\n'
printf 'idempotent reinstall: OK\n'

printf '\n=== custom TinyFugue startup override ===\n'

CUSTOM_HOME="$WORK_DIR/custom-home"
mkdir -p "$CUSTOM_HOME"

printf '/echo default startup untouched\n' > "$CUSTOM_HOME/.tfrc"
printf '/echo custom startup\n' > "$CUSTOM_HOME/custom.tf"

HOME="$CUSTOM_HOME" \
  "$BUNDLE_DIR/install.sh" \
  --no-start \
  --tf-startup "$CUSTOM_HOME/custom.tf"

grep -Fxq \
  '/load ~/.config/imp/capture.tf' \
  "$CUSTOM_HOME/custom.tf" ||
  die "custom TinyFugue startup did not receive load line"

[[ "$(grep -Fxc '/load ~/.config/imp/capture.tf' "$CUSTOM_HOME/.tfrc" || true)" -eq 0 ]] ||
  die "custom startup override unexpectedly modified ~/.tfrc"

printf 'custom TinyFugue startup override: OK\n'

printf '\n=== missing default TinyFugue startup ===\n'

MISSING_HOME="$WORK_DIR/missing-home"
mkdir -p "$MISSING_HOME"

if HOME="$MISSING_HOME" \
   "$BUNDLE_DIR/install.sh" \
   --no-start \
   >"$WORK_DIR/missing-startup.out" 2>&1; then
  cat "$WORK_DIR/missing-startup.out" >&2
  die "missing default TinyFugue startup unexpectedly succeeded"
fi

grep -Fq \
  'default TinyFugue startup file does not exist:' \
  "$WORK_DIR/missing-startup.out" ||
  {
    cat "$WORK_DIR/missing-startup.out" >&2
    die "missing default startup produced the wrong failure"
  }

[[ ! -e "$MISSING_HOME/.tfrc" ]] ||
  die "installer unexpectedly created ~/.tfrc"

[[ ! -e "$MISSING_HOME/.local/share/imp" ]] ||
  die "missing default startup changed the installation home"

printf 'missing default TinyFugue startup rejection: OK\n'

printf '\n=== tamper rejection ===\n'

TAMPER_BUNDLE="$WORK_DIR/tampered"
TAMPER_HOME="$WORK_DIR/tamper-home"

cp -a "$BUNDLE_DIR" "$TAMPER_BUNDLE"
mkdir -p "$TAMPER_HOME"

printf '\n; tampered\n' >> "$TAMPER_BUNDLE/capture.tf"

if HOME="$TAMPER_HOME" \
   "$TAMPER_BUNDLE/install.sh" \
   --no-start \
   >"$WORK_DIR/tamper.out" 2>&1; then
  cat "$WORK_DIR/tamper.out" >&2
  die "tampered bundle unexpectedly installed"
fi

grep -Fq 'capture.tf: FAILED' "$WORK_DIR/tamper.out" ||
  {
    cat "$WORK_DIR/tamper.out" >&2
    die "tamper failure did not come from checksum verification"
  }

[[ ! -e "$TAMPER_HOME/.local/share/imp" ]] ||
  die "tampered bundle changed the installation home"

printf 'tamper rejection: OK\n'

printf '\n=== unlisted bundle content rejection ===\n'

EXTRA_BUNDLE="$WORK_DIR/extra-content"
EXTRA_HOME="$WORK_DIR/extra-home"

cp -a "$BUNDLE_DIR" "$EXTRA_BUNDLE"
mkdir -p "$EXTRA_HOME"

printf 'unlisted\n' > "$EXTRA_BUNDLE/wheels/unlisted.whl"

if HOME="$EXTRA_HOME" \
   "$EXTRA_BUNDLE/install.sh" \
   --no-start \
   >"$WORK_DIR/extra-content.out" 2>&1; then
  cat "$WORK_DIR/extra-content.out" >&2
  die "bundle with unlisted content unexpectedly installed"
fi

grep -Fq \
  'bundle contents do not match SHA256SUMS:' \
  "$WORK_DIR/extra-content.out" ||
  {
    cat "$WORK_DIR/extra-content.out" >&2
    die "unlisted content produced the wrong failure"
  }

[[ ! -e "$EXTRA_HOME/.local/share/imp" ]] ||
  die "unlisted bundle content changed the installation home"

printf 'unlisted bundle content rejection: OK\n'

printf '\n=== failed activation rollback ===\n'

ROLLBACK_HOME="$WORK_DIR/rollback-home"
FAKE_BIN="$WORK_DIR/fake-bin"

mkdir -p "$ROLLBACK_HOME" "$FAKE_BIN"

printf '/load ~/.config/imp/capture.tf\n' > "$ROLLBACK_HOME/.tfrc"

HOME="$ROLLBACK_HOME" \
  "$BUNDLE_DIR/install.sh" \
  --no-start

printf 'known-good\n' \
  > "$ROLLBACK_HOME/.local/share/imp/releases/$VERSION/rollback-marker"

printf 'old-capture\n' \
  > "$ROLLBACK_HOME/.config/imp/capture.tf"

printf 'old-relay-unit\n' \
  > "$ROLLBACK_HOME/.config/systemd/user/imp-relay.service"

printf 'old-feed-unit\n' \
  > "$ROLLBACK_HOME/.config/systemd/user/imp-feed.service"

printf 'old-gateway-unit\n' \
  > "$ROLLBACK_HOME/.config/systemd/user/imp-gateway.service"

ln -sfn /tmp/old-action-consumer \
  "$ROLLBACK_HOME/.local/bin/imp-action-consumer"

printf '/echo original startup\n' > "$ROLLBACK_HOME/.tfrc"

ROLLBACK_SYSTEMCTL_LOG="$WORK_DIR/rollback-systemctl.log"
ROLLBACK_RELAY_FAIL_MARKER="$WORK_DIR/rollback-relay-failed-once"

: > "$ROLLBACK_SYSTEMCTL_LOG"

cat > "$FAKE_BIN/systemctl" <<'SH_SYSTEMCTL'
#!/usr/bin/env bash
set -eu

printf '%s\n' "$*" >> "${IMP_TEST_SYSTEMCTL_LOG:?}"

case "$*" in
  *"is-active"*)
    exit 0
    ;;
  *"is-enabled"*)
    exit 0
    ;;
  *"restart imp-relay.service"*)
    if [[ ! -e "${IMP_TEST_RELAY_FAIL_MARKER:?}" ]]; then
      : > "$IMP_TEST_RELAY_FAIL_MARKER"
      exit 1
    fi
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
SH_SYSTEMCTL

chmod +x "$FAKE_BIN/systemctl"

if HOME="$ROLLBACK_HOME" \
   PATH="$FAKE_BIN:$PATH" \
   IMP_TEST_SYSTEMCTL_LOG="$ROLLBACK_SYSTEMCTL_LOG" \
   IMP_TEST_RELAY_FAIL_MARKER="$ROLLBACK_RELAY_FAIL_MARKER" \
   "$BUNDLE_DIR/install.sh" \
   >"$WORK_DIR/rollback.out" 2>&1; then
  cat "$WORK_DIR/rollback.out" >&2
  die "failed-activation test unexpectedly succeeded"
fi

grep -Fq \
  'Restoring previous Imp installation after failed activation...' \
  "$WORK_DIR/rollback.out" ||
  {
    cat "$WORK_DIR/rollback.out" >&2
    die "rollback path did not execute"
  }

for expected_call in \
  '--user enable imp-relay.service' \
  '--user enable imp-feed.service' \
  '--user restart imp-relay.service' \
  '--user restart imp-feed.service' \
  '--user restart imp-gateway.service'; do
  grep -Fxq -- "$expected_call" "$ROLLBACK_SYSTEMCTL_LOG" ||
    die "rollback did not restore service state: $expected_call"
done

for unexpected_call in \
  '--user disable imp-relay.service' \
  '--user disable imp-feed.service' \
  '--user stop imp-relay.service' \
  '--user stop imp-feed.service'; do
  if grep -Fxq -- "$unexpected_call" "$ROLLBACK_SYSTEMCTL_LOG"; then
    die "rollback restored the wrong service state: $unexpected_call"
  fi
done

printf 'service-state rollback: OK\n'

grep -Fxq \
  known-good \
  "$ROLLBACK_HOME/.local/share/imp/releases/$VERSION/rollback-marker"

[[ "$(
  readlink "$ROLLBACK_HOME/.local/share/imp/current"
)" == "$ROLLBACK_HOME/.local/share/imp/releases/$VERSION" ]] ||
  die "runtime rollback failed"

grep -Fxq old-capture \
  "$ROLLBACK_HOME/.config/imp/capture.tf"

grep -Fxq old-relay-unit \
  "$ROLLBACK_HOME/.config/systemd/user/imp-relay.service"

grep -Fxq old-feed-unit \
  "$ROLLBACK_HOME/.config/systemd/user/imp-feed.service"

grep -Fxq old-gateway-unit \
  "$ROLLBACK_HOME/.config/systemd/user/imp-gateway.service"

[[ "$(
  readlink "$ROLLBACK_HOME/.local/bin/imp-action-consumer"
)" == "/tmp/old-action-consumer" ]] ||
  die "action-consumer link rollback failed"

grep -Fxq \
  '/echo original startup' \
  "$ROLLBACK_HOME/.tfrc"

[[ "$(
  grep -Fxc '/load ~/.config/imp/capture.tf' "$ROLLBACK_HOME/.tfrc" ||
    true
)" -eq 0 ]] ||
  die "TinyFugue startup rollback failed"

mapfile -t ROLLBACK_BACKUPS < <(
  find "$ROLLBACK_HOME" \
    -maxdepth 1 \
    -name '.tfrc.imp-backup.*' \
    -print
)

[[ ${#ROLLBACK_BACKUPS[@]} -eq 0 ]] ||
  die "failed install left a TinyFugue startup backup"

assert_no_debris "$ROLLBACK_HOME"

printf 'runtime rollback: OK\n'
printf 'integration-file rollback: OK\n'
printf 'TinyFugue startup rollback: OK\n'

printf '\nserver bundle acceptance: PASS\n'
