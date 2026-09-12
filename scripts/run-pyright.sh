#!/usr/bin/env bash
# The temporary root-package layout needs a parent directory named
# ``flops_agent`` for static import resolution. Copying into that shape keeps
# the check independent of the Flops monorepo; phase 4 will replace this shim
# with the final source layout.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
pyright_bin="$repo_root/node_modules/.bin/pyright"
if [[ ! -x "$pyright_bin" ]]; then
    echo "pyright is missing; run npm ci first." >&2
    exit 1
fi

type_root="$(mktemp -d "${TMPDIR:-/tmp}/flops-agent-pyright.XXXXXX")"
trap 'rm -rf "$type_root"' EXIT
mkdir "$type_root/flops_agent"
rsync -a \
    --exclude='.git/' \
    --exclude='node_modules/' \
    --exclude='build/' \
    --exclude='dist/' \
    "$repo_root/" "$type_root/flops_agent/"

cd "$type_root"
"$pyright_bin" --project flops_agent/pyrightconfig.json
