#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

./scripts/verify-c06.sh
python3 scripts/verify-plugin-contract.py
python3 -m json.tool "$repo_root/schemas/codex-adjudication-request.schema.json" >/dev/null
python3 -m json.tool "$repo_root/schemas/codex-advisory-opinion.schema.json" >/dev/null
python3 -m json.tool "$repo_root/schemas/codex-adjudication-boss-decision.schema.json" >/dev/null
python3 -m json.tool "$repo_root/schemas/codex-adjudication-window-handoff.schema.json" >/dev/null
python3 plugins/codex-module-governance/scripts/test_adjudication_request_organizer.py
echo "C07 adjudication request organizer verification passed."
