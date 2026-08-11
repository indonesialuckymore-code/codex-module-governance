#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"

"$repo_root/scripts/verify-c01.sh"

required_files=(
  "docs/C02_NEW_PROJECT_INITIALIZER.md"
  "schemas/project-card.schema.json"
  "schemas/projects-index.schema.json"
  "plugins/codex-module-governance/skills/new-project-initializer/SKILL.md"
  "plugins/codex-module-governance/scripts/initialize_project.py"
  "plugins/codex-module-governance/scripts/test_initialize_project.py"
)

for item in "${required_files[@]}"; do
  test -f "$repo_root/$item" || { echo "MISSING: $item" >&2; exit 1; }
done

python3 -m json.tool "$repo_root/schemas/project-card.schema.json" >/dev/null
python3 -m json.tool "$repo_root/schemas/projects-index.schema.json" >/dev/null
python3 "$repo_root/scripts/verify-plugin-contract.py"
python3 -m unittest "$repo_root/plugins/codex-module-governance/scripts/test_initialize_project.py"

grep -q 'C03_ENGINEERING_LEDGER_GOVERNANCE_REQUIRED' "$repo_root/plugins/codex-module-governance/scripts/initialize_project.py"
grep -q 'PRIVATE_DATA_ROOT_IS_INSIDE_GIT_WORKTREE' "$repo_root/plugins/codex-module-governance/scripts/initialize_project.py"

echo "C02 initializer verification passed."
