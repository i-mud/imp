#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] ||
  {
    printf 'error: the server bundle must be built on Linux x86_64\n' >&2
    exit 1
  }

for command in uv tar sha256sum readelf; do
  command -v "$command" >/dev/null 2>&1 ||
    {
      printf 'error: required command not found: %s\n' "$command" >&2
      exit 1
    }
done

# Exact uv-managed CPython from the central pin; no ambient or system Python is ever consulted.
PYTHON_PIN="$(tr -d '\r\n' < "$ROOT/services/relay/.python-version")"

WORK_DIR="$(mktemp -d)"
trap 'rm -rf -- "$WORK_DIR"' EXIT

# Build-time only: the wheels and the locked virtualenv never enter the bundle.
WHEELS_DIR="$WORK_DIR/wheels"
VENV="$WORK_DIR/venv"
PYI_DIR="$WORK_DIR/pyinstaller"

uv venv --managed-python --python "$PYTHON_PIN" "$VENV"

"$VENV/bin/python" - "$PYTHON_PIN" "$(uv python dir)" <<'PY_INTERPRETER'
import platform
from pathlib import Path
import sys

pin, managed_dir = sys.argv[1:]
base = Path(sys._base_executable).resolve()

if (
    sys.implementation.name != "cpython"
    or platform.python_version() != pin
    or platform.machine() != "x86_64"
    or not base.is_relative_to(Path(managed_dir).resolve())
):
    raise SystemExit(f"build interpreter is not uv-managed CPython {pin} x86_64: {base}")

print(f"Build interpreter: CPython {pin} (uv-managed, pinned by services/relay/.python-version): {base}")
PY_INTERPRETER

read -r VERSION WEBSOCKETS_VERSION < <(
  "$VENV/bin/python" - "$ROOT" <<'PY_METADATA'
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
    "integrations/common/pyproject.toml",
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

print(root_version, websockets["version"])
PY_METADATA
)

TARGET="linux-x86_64"
OUTPUT_DIR="${IMP_SERVER_DIST:-$ROOT/dist/server}"
ARCHIVE="$OUTPUT_DIR/imp-server-$VERSION-$TARGET.tar.gz"

BUNDLE_NAME="imp-server-$VERSION"
BUNDLE_DIR="$WORK_DIR/$BUNDLE_NAME"

mkdir -p "$BUNDLE_DIR/bin" "$BUNDLE_DIR/systemd" "$OUTPUT_DIR"

for project in services/relay integrations/common integrations/tinyfugue; do
  uv build --python "$VENV/bin/python" --project "$ROOT/$project" --wheel --out-dir "$WHEELS_DIR"
done

# The relay lock pins every runtime dependency (with hashes) and the PyInstaller dev group.
uv export \
  --python "$VENV/bin/python" \
  --project "$ROOT/services/relay" \
  --frozen \
  --no-emit-project \
  --no-header \
  --output-file "$WORK_DIR/requirements.txt"

uv pip install --python "$VENV/bin/python" --require-hashes --no-deps -r "$WORK_DIR/requirements.txt"
uv pip install --python "$VENV/bin/python" --no-deps --no-index "$WHEELS_DIR"/*.whl

"$VENV/bin/python" - "$WEBSOCKETS_VERSION" <<'PY_WEBSOCKETS'
from importlib.metadata import version
import sys

if version("websockets") != sys.argv[1]:
    raise SystemExit(f"locked websockets {sys.argv[1]} was not installed: {version('websockets')}")
PY_WEBSOCKETS

"$VENV/bin/pyinstaller" \
  --noconfirm \
  --clean \
  --onedir \
  --contents-directory imp-server-runtime \
  --name imp-server \
  --distpath "$PYI_DIR/dist" \
  --workpath "$PYI_DIR/work" \
  --specpath "$PYI_DIR/spec" \
  --copy-metadata imp-relay \
  --copy-metadata imp-adapter \
  --copy-metadata imp-tinyfugue \
  --copy-metadata websockets \
  "$ROOT/services/relay/server_entry.py"

# PyInstaller links shared-library versions inside the onedir tree. Release bundles accept no
# symlinks, so replace each link that resolves inside the tree with a regular copy.
"$VENV/bin/python" - "$PYI_DIR/dist/imp-server" "$VERSION" "$WEBSOCKETS_VERSION" <<'PY_REGULARIZE'
from pathlib import Path
import shutil
import sys

root = Path(sys.argv[1]).resolve()

while links := sorted(path for path in root.rglob("*") if path.is_symlink()):
    link = links[0]
    target = link.resolve(strict=True) if link.exists() else None
    if target is None or not target.is_relative_to(root):
        raise SystemExit(f"PyInstaller link is dangling or leaves the build tree: {link}")
    link.unlink()
    if target.is_dir():
        shutil.copytree(target, link, symlinks=True)
    else:
        shutil.copy2(target, link)

for path in root.rglob("*"):
    name = path.relative_to(root).as_posix()
    if not path.is_dir() and not path.is_file():
        raise SystemExit(f"unsupported PyInstaller path type: {name}")
    if not name.isprintable() or any(char in name for char in " \t\\"):
        raise SystemExit(f"path is not representable in SHA256SUMS: {name!r}")

runtime = root / "imp-server-runtime"
for package, expected in (
    ("imp_relay", sys.argv[2]),
    ("imp_adapter", sys.argv[2]),
    ("imp_tinyfugue", sys.argv[2]),
    ("websockets", sys.argv[3]),
):
    if not (runtime / f"{package}-{expected}.dist-info").is_dir():
        raise SystemExit(f"frozen runtime lacks {package}-{expected}.dist-info")
PY_REGULARIZE

cp -a "$PYI_DIR/dist/imp-server/." "$BUNDLE_DIR/bin/"

for role in relay gateway feed action-consumer direct-wss direct-edge public-verify; do
  ln "$BUNDLE_DIR/bin/imp-server" "$BUNDLE_DIR/bin/imp-$role"
done

cp "$ROOT/deploy/install.sh" "$BUNDLE_DIR/install.sh"
cp "$ROOT/integrations/tinyfugue/imp.tf" "$BUNDLE_DIR/capture.tf"

cp \
  "$ROOT/deploy/systemd/imp-relay.service" \
  "$ROOT/deploy/systemd/imp-feed.service" \
  "$ROOT/deploy/systemd/imp-gateway.service" \
  "$BUNDLE_DIR/systemd/"

chmod +x "$BUNDLE_DIR/install.sh"

printf '%s\n' "$VERSION" > "$BUNDLE_DIR/VERSION"

# Gate the final regularized tree: every unique ELF must fit the documented runtime baseline.
"$VENV/bin/python" "$SCRIPT_DIR/check-server-abi.py" "$BUNDLE_DIR" > "$WORK_DIR/abi.json"

"$VENV/bin/python" - "$BUNDLE_DIR/BUILD_INFO.json" "$WORK_DIR/abi.json" "$PYTHON_PIN" "$VERSION" "$(uv --version)" <<'PY_BUILD_INFO'
from importlib.metadata import version
import json
from pathlib import Path
import platform
import sys

output, abi, pin, bundle_version, uv = sys.argv[1:]
info = {
    "version": bundle_version,
    "python_pin": pin,
    "python_pin_file": "services/relay/.python-version",
    "python": {
        "implementation": sys.implementation.name,
        "version": platform.python_version(),
        "build": " ".join(platform.python_build()),
        "compiler": platform.python_compiler(),
        "machine": platform.machine(),
        "managed_by": "uv",
        "base_executable": str(Path(sys._base_executable).resolve()),
    },
    "uv": uv,
    "pyinstaller": version("pyinstaller"),
    "abi": json.loads(Path(abi).read_text()),
}
Path(output).write_text(json.dumps(info, indent=2, sort_keys=True) + "\n")
PY_BUILD_INFO

(
  cd "$BUNDLE_DIR"
  {
    printf '%s\n' VERSION BUILD_INFO.json install.sh capture.tf systemd/*.service
    find bin -type f | LC_ALL=C sort
  } | xargs -d '\n' sha256sum > SHA256SUMS
)

# The release inventory and embedded metadata must pass the same checks the installer runs.
"$BUNDLE_DIR/bin/imp-server" bundle-check "$BUNDLE_DIR"
"$BUNDLE_DIR/bin/imp-server" runtime-check "$VERSION"

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
