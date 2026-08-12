#!/usr/bin/env python3
"""Dependency-free preflight for the manifest and bundled governance Skills."""

import json
import re
import sys
from pathlib import Path


SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


def fail(message):
    print(f"Plugin contract preflight failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def main():
    repo_root = Path(__file__).resolve().parents[1]
    plugin_root = repo_root / "plugins" / "codex-module-governance"
    manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    required = {"name", "version", "description", "author", "skills", "interface"}
    allowed = {
        "id", "name", "version", "description", "skills", "apps", "mcpServers",
        "interface", "author", "homepage", "repository", "license", "keywords",
    }
    missing = required - set(manifest)
    unsupported = set(manifest) - allowed
    if missing:
        fail(f"missing manifest keys: {sorted(missing)}")
    if unsupported:
        fail(f"unsupported manifest keys: {sorted(unsupported)}")
    if not SEMVER.fullmatch(manifest["version"]):
        fail("version is not strict semantic versioning")
    if manifest["skills"].rstrip("/").removeprefix("./") != "skills":
        fail("skills path does not resolve to the skills directory")
    if not isinstance(manifest["author"], dict) or not str(manifest["author"].get("name", "")).strip():
        fail("author.name is missing")

    interface = manifest["interface"]
    required_interface = {"displayName", "shortDescription", "longDescription", "developerName", "category"}
    missing_interface = required_interface - set(interface)
    if missing_interface:
        fail(f"missing interface keys: {sorted(missing_interface)}")
    if not any(str(interface.get(key, "")).strip() for key in ("defaultPrompt", "default_prompt")):
        fail("a default prompt is required")
    if not isinstance(interface.get("capabilities"), list) or not all(str(item).strip() for item in interface["capabilities"]):
        fail("capabilities must be a non-empty string list")

    skills = {
        "new-project-initializer": "C02",
        "engineering-ledger-manager": "C03",
        "task-package-generator": "C04",
        "occupancy-conflict-checker": "C05",
        "independent-handover-validator": "C06",
        "adjudication-request-organizer": "C07",
        "disconnection-recovery-controller": "C08",
        "central-construction-controller": "C09",
    }
    for skill_name, stage in skills.items():
        skill_root = plugin_root / "skills" / skill_name
        skill_path = skill_root / "SKILL.md"
        skill_contents = skill_path.read_text(encoding="utf-8")
        if not skill_contents.startswith("---\n") or "\n---" not in skill_contents[4:]:
            fail(f"{stage} Skill frontmatter is missing or not closed")
        frontmatter = skill_contents[4:skill_contents.find("\n---", 4)]
        if not re.search(rf"^name:\s*{re.escape(skill_name)}\s*$", frontmatter, re.MULTILINE):
            fail(f"{stage} Skill name is missing")
        if not re.search(r"^description:\s*\S", frontmatter, re.MULTILINE):
            fail(f"{stage} Skill description is missing")

    for skill_name, stage in {"engineering-ledger-manager": "C03", "task-package-generator": "C04", "occupancy-conflict-checker": "C05", "independent-handover-validator": "C06", "adjudication-request-organizer": "C07", "disconnection-recovery-controller": "C08", "central-construction-controller": "C09"}.items():
        agent_manifest = plugin_root / "skills" / skill_name / "agents" / "openai.yaml"
        agent_contents = agent_manifest.read_text(encoding="utf-8")
        for required_line in ("interface:", "display_name:", "short_description:", "default_prompt:"):
            if required_line not in agent_contents:
                fail(f"{stage} Skill agent metadata is missing {required_line}")

    for path in plugin_root.rglob("*"):
        if path.is_file() and "[TODO:" in path.read_text(encoding="utf-8", errors="ignore"):
            fail(f"unresolved placeholder in {path.relative_to(plugin_root)}")
    print("Plugin manifest and C02/C03/C04/C05/C06/C07/C08/C09 Skill contract preflight passed.")


if __name__ == "__main__":
    main()
