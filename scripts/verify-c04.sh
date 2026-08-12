#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

./scripts/verify-c03.sh
python3 scripts/verify-plugin-contract.py
python3 -m json.tool "$repo_root/schemas/codex-draft-task-package.schema.json" >/dev/null
python3 -m json.tool "$repo_root/schemas/codex-task-package-brief.schema.json" >/dev/null
python3 plugins/codex-module-governance/scripts/test_task_package_generator.py
echo "C04 task-package generator verification passed."
