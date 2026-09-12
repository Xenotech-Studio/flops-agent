#!/usr/bin/env bash
# Runs the suite against the installed distribution. Install this checkout
# first (pip install -e ".[test,providers]") so ``import flops_agent`` resolves
# through the package, not through this script's own path juggling.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
python -m pytest tests "$@"
