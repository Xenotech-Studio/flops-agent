#!/usr/bin/env bash
# Copy only tracked docs/ files from a source checkout, including local edits.
# Usage: bash scripts/mirror-docs.sh SOURCE_CHECKOUT DOCS_CHECKOUT
set -euo pipefail
source_root=$(realpath "${1:?source checkout required}")
target_root=$(realpath "${2:?docs checkout required}")
[[ "$source_root" != "$target_root" ]] || { echo 'Source and target must differ' >&2; exit 1; }
[[ -f "$source_root/docs/README.md" ]] || { echo 'Missing source docs/README.md' >&2; exit 1; }
[[ ! -L "$target_root/docs" && ! -L "$target_root/docs/en" ]] || { echo 'Refusing symlink destination' >&2; exit 1; }
staging=$(mktemp -d)
trap 'rm -rf "$staging"' EXIT
git -C "$source_root" ls-files -z -- docs/ > "$staging/files"
[[ -s "$staging/files" ]] || { echo 'No tracked docs' >&2; exit 1; }
tar -C "$source_root" --null -T "$staging/files" -cf "$staging/docs.tar"
tar -C "$staging" -xf "$staging/docs.tar"
mkdir -p "$target_root/docs/en"
rsync -rcl --delete --itemize-changes "$staging/docs/" "$target_root/docs/en/"
