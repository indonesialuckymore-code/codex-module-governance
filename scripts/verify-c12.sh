#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$repo_root"
./scripts/verify-c11.sh
python3 scripts/verify-plugin-contract.py
python3 -m json.tool schemas/codex-natural-language-gateway-decision.schema.json >/dev/null
python3 -m json.tool schemas/module-config.schema.json >/dev/null
python3 -m json.tool config/module-config.example.json >/dev/null
python3 plugins/codex-module-governance/scripts/test_natural_language_gateway.py
echo "C12 natural-language gateway verification passed."
