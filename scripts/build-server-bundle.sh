#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

for command in python3 uv tar sha256sum; do
  command -v "$command" >/dev/null 2>&1 ||
    {
      printf 'error: required command not found: %s\n' "$command" >&2
      exit 1
    }
done

python3 -m pip --version >/dev/null 2>&1 ||
  {
    printf 'error: python3 pip is required to assemble the server wheelhouse\n' >&2
    exit 1
  }

read -r VERSION WEBSOCKETS_VERSION WEBSOCKETS_SHA256 < <(
  python3 - "$ROOT" <<'PY_METADATA'
import json
from pathlib import Path
import sys
import tomllib

root = Path(sys.argv[1])

with (root / "package.json").open() as f:
    root_version = json.load(f)["version"]

package_versions = []

for relative in (
    "services/relay/pyproject.toml",
    "integrations/tinyfugue/pyproject.toml",
):
    with (root / relative).open("rb") as f:
        package_versions.append(tomllib.load(f)["project"]["version"])

if any(version != root_version for version in package_versions):
    raise SystemExit(
        f"version mismatch: root={root_version}, packages={package_versions}"
    )

with (root / "services/relay/uv.lock").open("rb") as f:
    lock = tomllib.load(f)

websockets_packages = [
    package
    for package in lock["package"]
    if package["name"] == "websockets"
]

if len(websockets_packages) != 1:
    raise SystemExit(
        f"expected one locked websockets package, got {len(websockets_packages)}"
    )

websockets = websockets_packages[0]

candidates = [
    wheel
    for wheel in websockets.get("wheels", [])
    if "cp312-cp312-" in wheel["url"]
    and "manylinux" in wheel["url"]
    and "x86_64" in wheel["url"]
]

if len(candidates) != 1:
    raise SystemExit(
        "expected exactly one CPython 3.12 manylinux x86_64 websockets wheel"
    )

hash_value = candidates[0]["hash"]

if not hash_value.startswith("sha256:"):
    raise SystemExit(f"unexpected websockets wheel hash: {hash_value}")

print(
    root_version,
    websockets["version"],
    hash_value.removeprefix("sha256:"),
)
PY_METADATA
)

TARGET="linux-x86_64"
OUTPUT_DIR="${IMP_SERVER_DIST:-$ROOT/dist/server}"
ARCHIVE="$OUTPUT_DIR/imp-server-$VERSION-$TARGET.tar.gz"

WORK_DIR="$(mktemp -d)"
trap 'rm -rf -- "$WORK_DIR"' EXIT

BUNDLE_NAME="imp-server-$VERSION"
BUNDLE_DIR="$WORK_DIR/$BUNDLE_NAME"

mkdir -p \
  "$BUNDLE_DIR/wheels" \
  "$BUNDLE_DIR/systemd" \
  "$OUTPUT_DIR"

uv build \
  --project "$ROOT/services/relay" \
  --wheel \
  --out-dir "$BUNDLE_DIR/wheels"

uv build \
  --project "$ROOT/integrations/tinyfugue" \
  --wheel \
  --out-dir "$BUNDLE_DIR/wheels"

python3 -m pip download \
  --dest "$BUNDLE_DIR/wheels" \
  --only-binary=:all: \
  --platform manylinux_2_28_x86_64 \
  --python-version 312 \
  --implementation cp \
  --abi cp312 \
  "websockets==$WEBSOCKETS_VERSION"

# uv may leave bookkeeping files in an output directory. The release
# wheelhouse contains wheels only.
find "$BUNDLE_DIR/wheels" \
  -maxdepth 1 \
  -type f \
  ! -name '*.whl' \
  -delete

python3 - \
  "$BUNDLE_DIR/wheels" \
  "$WEBSOCKETS_VERSION" \
  "$WEBSOCKETS_SHA256" <<'PY_HASH'
from hashlib import sha256
from pathlib import Path
import sys

wheel_dir = Path(sys.argv[1])
version = sys.argv[2]
expected = sys.argv[3]

matches = list(
    wheel_dir.glob(
        f"websockets-{version}-cp312-cp312-*manylinux*x86_64*.whl"
    )
)

if len(matches) != 1:
    raise SystemExit(
        f"expected one downloaded websockets target wheel, found {matches}"
    )

actual = sha256(matches[0].read_bytes()).hexdigest()

if actual != expected:
    raise SystemExit(
        f"websockets wheel hash mismatch: expected {expected}, got {actual}"
    )

print(f"Verified locked websockets wheel: {matches[0].name}")
PY_HASH

cp "$ROOT/deploy/install.sh" "$BUNDLE_DIR/install.sh"
cp "$ROOT/integrations/tinyfugue/imp.tf" "$BUNDLE_DIR/capture.tf"

cp \
  "$ROOT/deploy/systemd/imp-relay.service" \
  "$ROOT/deploy/systemd/imp-feed.service" \
  "$ROOT/deploy/systemd/imp-gateway.service" \
  "$BUNDLE_DIR/systemd/"

chmod +x "$BUNDLE_DIR/install.sh"

printf '%s\n' "$VERSION" > "$BUNDLE_DIR/VERSION"

(
  cd "$BUNDLE_DIR"
  sha256sum \
    VERSION \
    install.sh \
    capture.tf \
    systemd/*.service \
    wheels/*.whl \
    > SHA256SUMS
)

rm -f -- "$ARCHIVE" "$ARCHIVE.sha256"

tar \
  -C "$WORK_DIR" \
  -czf "$ARCHIVE" \
  "$BUNDLE_NAME"

(
  cd "$OUTPUT_DIR"
  sha256sum "$(basename "$ARCHIVE")" \
    > "$(basename "$ARCHIVE").sha256"
)

printf '\nBuilt Imp server bundle:\n'
printf '  %s\n' "$ARCHIVE"
printf '  %s.sha256\n' "$ARCHIVE"
