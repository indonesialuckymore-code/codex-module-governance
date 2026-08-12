#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

./scripts/verify-c08.sh
python3 scripts/verify-plugin-contract.py
python3 -m json.tool "$repo_root/config/core-capability-registry.json" >/dev/null
python3 -m json.tool "$repo_root/schemas/codex-central-routing-request.schema.json" >/dev/null
python3 -m json.tool "$repo_root/schemas/codex-central-routing-decision.schema.json" >/dev/null
python3 plugins/codex-module-governance/scripts/test_central_construction_controller.py
echo "C09 central construction controller verification passed."
