#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"

"$repo_root/scripts/verify-c02.sh"

required_files=(
  "docs/C03_CONSTRUCTION_REPORT.md"
  "docs/C03_ENGINEERING_LEDGER.md"
  "schemas/codex-module-ledger.schema.json"
  "schemas/immutable-ledger-receipt.schema.json"
  "plugins/codex-module-governance/skills/engineering-ledger-manager/SKILL.md"
  "plugins/codex-module-governance/skills/engineering-ledger-manager/agents/openai.yaml"
  "plugins/codex-module-governance/scripts/ledger_manager.py"
  "plugins/codex-module-governance/scripts/test_ledger_manager.py"
)

for item in "${required_files[@]}"; do
  test -f "$repo_root/$item" || { echo "MISSING: $item" >&2; exit 1; }
done

python3 -m json.tool "$repo_root/schemas/codex-module-ledger.schema.json" >/dev/null
python3 -m json.tool "$repo_root/schemas/immutable-ledger-receipt.schema.json" >/dev/null
python3 "$repo_root/scripts/verify-plugin-contract.py"
python3 -m unittest "$repo_root/plugins/codex-module-governance/scripts/test_ledger_manager.py"

grep -q 'LEDGER_WRITER_NOT_AUTHORIZED' "$repo_root/plugins/codex-module-governance/scripts/ledger_manager.py"
grep -q 'DONE_REQUIRES_C06_INDEPENDENT_VALIDATION' "$repo_root/plugins/codex-module-governance/scripts/ledger_manager.py"
grep -q 'OBJECT_OCCUPANCY_CONFLICT' "$repo_root/plugins/codex-module-governance/scripts/ledger_manager.py"

echo "C03 engineering ledger verification passed."
