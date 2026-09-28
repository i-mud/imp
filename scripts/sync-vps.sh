#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="$(cd -- "$SCRIPT_DIR/.." && pwd)/"
HOST="${IMP_VPS_HOST:-avatar}"
DEST="${IMP_VPS_DEST:-~/imp/}"

rsync -a --delete \
  --exclude='.git/' \
  --exclude='node_modules/' \
  --exclude='dist/' \
  --exclude='target/' \
  --exclude='.venv/' \
  --exclude='venv/' \
  --exclude='__pycache__/' \
  --exclude='.pytest_cache/' \
  --exclude='.mypy_cache/' \
  --exclude='.ruff_cache/' \
  --exclude='.vite/' \
  --exclude='.idea/' \
  --exclude='.env' \
  --exclude='.env.*' \
  --exclude='*.pem' \
  --exclude='*.key' \
  --exclude='id_rsa*' \
  --exclude='id_ed25519*' \
  --exclude='known_hosts' \
  --exclude='.DS_Store' \
  --exclude='Thumbs.db' \
  "$SOURCE" "$HOST:$DEST"
