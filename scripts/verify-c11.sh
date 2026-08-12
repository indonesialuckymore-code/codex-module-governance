#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$repo_root"
./scripts/verify-c10.sh
python3 scripts/verify-plugin-contract.py
python3 -m json.tool schemas/codex-external-skill-request.schema.json >/dev/null
python3 -m json.tool schemas/codex-external-skill-registry.schema.json >/dev/null
python3 plugins/codex-module-governance/scripts/test_external_skill_adapter_controller.py
echo "C11 external Skill adapter verification passed."
