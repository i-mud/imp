#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
SYNC_SCRIPT="$SCRIPT_DIR/sync-native.sh"

echo "Initial native sync..."
"$SYNC_SCRIPT"

echo "Watching $REPO_ROOT for changes..."

inotifywait \
  --monitor \
  --recursive \
  --quiet \
  --event close_write,create,delete,move \
  --exclude '(\.git|node_modules|dist|target|\.venv|venv|__pycache__|\.pytest_cache|\.mypy_cache|\.ruff_cache|\.vite|\.idea)' \
  "$REPO_ROOT" |
while read -r _directory _events _filename; do
  "$SYNC_SCRIPT"
done