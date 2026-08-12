#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

./scripts/verify-c05.sh
python3 scripts/verify-plugin-contract.py
python3 -m json.tool "$repo_root/schemas/codex-task-handback.schema.json" >/dev/null
python3 -m json.tool "$repo_root/schemas/codex-independent-validation-review.schema.json" >/dev/null
python3 -m json.tool "$repo_root/schemas/codex-validation-decision.schema.json" >/dev/null
python3 -m json.tool "$repo_root/schemas/codex-boss-finalization.schema.json" >/dev/null
python3 plugins/codex-module-governance/scripts/test_independent_handover_validator.py
echo "C06 independent handover validation verification passed."
