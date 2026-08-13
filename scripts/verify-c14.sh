#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

./scripts/verify-c08.sh
python3 scripts/verify-plugin-contract.py
python3 -m py_compile plugins/codex-module-governance/scripts/task_communication_bridge.py
python3 plugins/codex-module-governance/scripts/test_task_communication_bridge.py
echo "C14 bidirectional task communication verification passed."
