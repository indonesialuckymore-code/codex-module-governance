#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

./scripts/verify-c04.sh
python3 scripts/verify-plugin-contract.py
python3 -m json.tool "$repo_root/schemas/codex-occupancy-review.schema.json" >/dev/null
python3 -m json.tool "$repo_root/schemas/codex-occupancy-decision.schema.json" >/dev/null
python3 plugins/codex-module-governance/scripts/test_occupancy_conflict_checker.py
echo "C05 occupancy and conflict checker verification passed."
