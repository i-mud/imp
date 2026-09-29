#!/usr/bin/env bash
set -euo pipefail

message_file="${1:?git did not provide a tag message file}"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

version="$(
  node -e '
    const fs = require("fs");
    process.stdout.write(
      JSON.parse(fs.readFileSync(process.argv[1], "utf8")).version
    );
  ' "$repo_root/package.json"
)"

printf 'Imp v%s\n' "$version" > "$message_file"
