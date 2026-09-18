#!/usr/bin/env bash
set -euo pipefail

SOURCE="$HOME/src/tinyscry/"
DEST="/mnt/c/src/tinyscry-native/"

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
  "$SOURCE" "$DEST"