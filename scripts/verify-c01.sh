#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"

required_files=(
  ".gitignore"
  "LICENSE"
  "README.md"
  "AGENTS.md"
  ".agents/plugins/marketplace.json"
  "config/module-config.example.json"
  "schemas/module-config.schema.json"
  "docs/PRIVATE_DATA_BOUNDARY.md"
  "docs/VERSIONING_AND_RELEASE.md"
  "docs/INSTALLATION.md"
  "docs/ARCHITECTURE_DECISIONS.md"
  "plugins/codex-module-governance/.codex-plugin/plugin.json"
)

for item in "${required_files[@]}"; do
  test -f "$repo_root/$item" || { echo "MISSING: $item" >&2; exit 1; }
done

forbidden_tracked_patterns=(
  '(^|/)\.env($|\.)'
  '(^|/)(private|runtime|projects|evidence|backups|logs)/'
  '\.(sqlite|sqlite3|db|pem|key|p12|pfx)$'
)

for pattern in "${forbidden_tracked_patterns[@]}"; do
  if { git -C "$repo_root" ls-files; git -C "$repo_root" ls-files --others --exclude-standard; } | grep -E "$pattern" >/dev/null; then
    echo "FORBIDDEN REPOSITORY PATH matches: $pattern" >&2
    exit 1
  fi
done

python3 -m json.tool "$repo_root/.agents/plugins/marketplace.json" >/dev/null
python3 -m json.tool "$repo_root/config/module-config.example.json" >/dev/null
python3 -m json.tool "$repo_root/schemas/module-config.schema.json" >/dev/null
python3 -m json.tool "$repo_root/plugins/codex-module-governance/.codex-plugin/plugin.json" >/dev/null

grep -q '"name": "codex-module-governance"' "$repo_root/plugins/codex-module-governance/.codex-plugin/plugin.json"
grep -q '"mode": "private"' "$repo_root/config/module-config.example.json"
grep -q '"central": "gpt-5.6-sol"' "$repo_root/config/module-config.example.json"
grep -q '"taskWindow": "gpt-5.6-terra"' "$repo_root/config/module-config.example.json"
grep -q '"maxConcurrentFirstLevel": 3' "$repo_root/config/module-config.example.json"

echo "C01 skeleton verification passed."
