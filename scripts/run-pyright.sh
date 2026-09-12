#!/usr/bin/env bash
# The src layout makes ``src/flops_agent`` resolvable in place, so pyright runs
# directly against the checkout: no temporary copy or parent-directory shim.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
pyright_bin="$repo_root/node_modules/.bin/pyright"
if [[ ! -x "$pyright_bin" ]]; then
    echo "pyright is missing; run npm ci first." >&2
    exit 1
fi

cd "$repo_root"
"$pyright_bin" --project pyrightconfig.json
