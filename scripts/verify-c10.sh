#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

./scripts/verify-c09.sh
python3 scripts/verify-plugin-contract.py
python3 -m json.tool schemas/codex-dispatch-request.schema.json >/dev/null
python3 -m json.tool schemas/codex-runtime-dispatch-confirmation.schema.json >/dev/null
python3 -m json.tool schemas/codex-sub-agent-return.schema.json >/dev/null
python3 -m json.tool schemas/codex-sub-agent-append-request.schema.json >/dev/null
python3 -m json.tool schemas/codex-parent-quality-review.schema.json >/dev/null
python3 plugins/codex-module-governance/scripts/test_task_window_dispatch_controller.py
echo "C10 task-window dispatch controller verification passed."
