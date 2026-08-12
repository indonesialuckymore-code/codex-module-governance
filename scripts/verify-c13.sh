#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
./scripts/verify-c12.sh
python3 scripts/test_c13_release_validator.py
python3 scripts/c13-release-validator.py smoke --repo "$repo_root"
if grep -R "/Users/" README.md AGENTS.md config docs plugins schemas scripts --exclude-dir=__pycache__ --exclude='test_*.py' --exclude='verify-c13.sh' >/dev/null; then
  echo "C13 author absolute path leak detected." >&2
  exit 1
fi
echo "C13 release and rollback verification passed."
