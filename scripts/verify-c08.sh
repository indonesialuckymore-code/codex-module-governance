#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

./scripts/verify-c07.sh
python3 scripts/verify-plugin-contract.py
python3 -m json.tool "$repo_root/schemas/codex-disconnection-incident.schema.json" >/dev/null
python3 -m json.tool "$repo_root/schemas/codex-central-takeover.schema.json" >/dev/null
python3 -m json.tool "$repo_root/schemas/codex-recovery-decision.schema.json" >/dev/null
python3 -m json.tool "$repo_root/schemas/codex-occupancy-release.schema.json" >/dev/null
python3 -m json.tool "$repo_root/schemas/codex-role-handover-request.schema.json" >/dev/null
python3 -m json.tool "$repo_root/schemas/codex-role-successor-activation.schema.json" >/dev/null
python3 -m json.tool "$repo_root/schemas/codex-task-event.schema.json" >/dev/null
python3 plugins/codex-module-governance/scripts/test_disconnection_recovery_controller.py
python3 plugins/codex-module-governance/scripts/test_role_continuity_controller.py
echo "C08 disconnection recovery and role continuity verification passed."
