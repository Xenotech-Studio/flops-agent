#!/usr/bin/env bash
# Root-package layout makes the checkout itself an import package.  Pytest would
# otherwise import its ``__init__.py`` as the anonymous module ``__init__``.
# Run through a temporary, correctly named package parent so this suite tests
# the installed distribution without importing anything from the Flops monorepo.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
test_root="$(mktemp -d "${TMPDIR:-/tmp}/flops-agent-tests.XXXXXX")"
trap 'rm -rf "$test_root"' EXIT
ln -s "$repo_root" "$test_root/flops_agent"

cd "$test_root"
python -m pytest flops_agent/tests "$@"
