#!/usr/bin/env python3
"""C13 release, clean-install, upgrade, and rollback validator."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


PLUGIN = "codex-module-governance"
MARKETPLACE = "qianyi-codex-governance"
CURRENT_VERSION = "0.20.0"
REQUIRED_SKILLS = {
    "construction-outline-planner",
    "new-project-initializer",
    "engineering-ledger-manager",
    "task-package-generator",
    "occupancy-conflict-checker",
    "independent-handover-validator",
    "adjudication-request-organizer",
    "disconnection-recovery-controller",
    "central-workbench",
    "task-window-dispatch-controller",
    "external-skill-adapter-controller",
    "task-communication-bridge",
}
EXCLUDED_PARTS = {".git", "__pycache__", ".DS_Store"}


class C13Error(Exception):
    pass


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise C13Error(f"C13_INVALID_JSON:{path}:{error}")
    if not isinstance(value, dict):
        raise C13Error(f"C13_JSON_OBJECT_REQUIRED:{path}")
    return value


def plugin_root(repo: Path) -> Path:
    return repo / "plugins" / PLUGIN


def manifest(repo: Path) -> dict[str, Any]:
    return load_json(plugin_root(repo) / ".codex-plugin" / "plugin.json")


def marketplace(repo: Path) -> dict[str, Any]:
    return load_json(repo / ".agents" / "plugins" / "marketplace.json")


def assert_repo(repo: Path) -> None:
    if not repo.is_dir():
        raise C13Error("C13_REPOSITORY_NOT_FOUND")
    market = marketplace(repo)
    if market.get("name") != MARKETPLACE:
        raise C13Error("C13_MARKETPLACE_NAME_MISMATCH")
    entries = market.get("plugins")
    if not isinstance(entries, list) or len(entries) != 1 or entries[0].get("name") != PLUGIN:
        raise C13Error("C13_SINGLE_PLUGIN_ENTRY_REQUIRED")
    source = entries[0].get("source")
    if source != {"source": "local", "path": f"./plugins/{PLUGIN}"}:
        raise C13Error("C13_MARKETPLACE_SOURCE_INVALID")
    metadata = manifest(repo)
    if metadata.get("name") != PLUGIN or metadata.get("version") != CURRENT_VERSION:
        raise C13Error("C13_PLUGIN_VERSION_MISMATCH")
    if metadata.get("repository") != "https://github.com/indonesialuckymore-code/codex-module-governance":
        raise C13Error("C13_REPOSITORY_METADATA_MISMATCH")
    source_registry = load_json(repo / "config" / "core-capability-registry.json")
    bundled_registry = load_json(plugin_root(repo) / "config" / "core-capability-registry.json")
    if bundled_registry != source_registry:
        raise C13Error("C13_BUNDLED_CAPABILITY_REGISTRY_MISMATCH")
    skill_root = plugin_root(repo) / "skills"
    present = {path.parent.name for path in skill_root.glob("*/SKILL.md")}
    if present != REQUIRED_SKILLS:
        raise C13Error(f"C13_SKILL_SET_MISMATCH:{sorted(present ^ REQUIRED_SKILLS)}")


def ignored(relative: Path) -> bool:
    return any(part in EXCLUDED_PARTS for part in relative.parts) or relative.suffix in {".pyc", ".pyo"}


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root)
        if ignored(relative):
            continue
        digest.update(relative.as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def copy_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise C13Error("C13_SOURCE_PLUGIN_NOT_FOUND")
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".DS_Store"),
    )


def install(source_repo: Path, install_root: Path) -> dict[str, Any]:
    assert_repo(source_repo)
    install_root.mkdir(parents=True, exist_ok=True)
    target = install_root / PLUGIN
    if target.exists():
        raise C13Error("C13_CLEAN_INSTALL_TARGET_ALREADY_EXISTS")
    copy_tree(plugin_root(source_repo), target)
    expected = tree_digest(plugin_root(source_repo))
    actual = tree_digest(target)
    if expected != actual:
        shutil.rmtree(target, ignore_errors=True)
        raise C13Error("C13_CLEAN_INSTALL_DIGEST_MISMATCH")
    return {"status": "CLEAN_INSTALL_VERIFIED", "version": manifest(source_repo)["version"], "installRoot": str(install_root), "pluginPath": str(target), "digest": actual}


def upgrade(installed_plugin: Path, source_repo: Path, backup_root: Path, simulate_failure: bool) -> dict[str, Any]:
    assert_repo(source_repo)
    if not installed_plugin.is_dir():
        raise C13Error("C13_INSTALLED_PLUGIN_NOT_FOUND")
    before_manifest = load_json(installed_plugin / ".codex-plugin" / "plugin.json")
    backup_root.mkdir(parents=True, exist_ok=True)
    backup = backup_root / f"{PLUGIN}-{before_manifest.get('version', 'unknown')}"
    if backup.exists():
        raise C13Error("C13_BACKUP_TARGET_ALREADY_EXISTS")
    copy_tree(installed_plugin, backup)
    parent = installed_plugin.parent
    with tempfile.TemporaryDirectory(prefix="c13-stage-", dir=parent) as temp:
        staged = Path(temp) / PLUGIN
        copy_tree(plugin_root(source_repo), staged)
        if simulate_failure:
            (staged / ".codex-plugin" / "plugin.json").write_text("{}\n", encoding="utf-8")
        try:
            staged_manifest = load_json(staged / ".codex-plugin" / "plugin.json")
            if staged_manifest.get("name") != PLUGIN or staged_manifest.get("version") != CURRENT_VERSION:
                raise C13Error("C13_STAGED_UPGRADE_INVALID")
            displaced = parent / f".{PLUGIN}.previous"
            if displaced.exists():
                raise C13Error("C13_PREVIOUS_TARGET_PRESENT")
            installed_plugin.rename(displaced)
            try:
                staged.rename(installed_plugin)
            except Exception:
                displaced.rename(installed_plugin)
                raise
            shutil.rmtree(displaced)
        except Exception as error:
            if not installed_plugin.exists() and backup.exists():
                copy_tree(backup, installed_plugin)
            raise C13Error(f"C13_UPGRADE_ABORTED_PREVIOUS_VERSION_PRESERVED:{error}")
    return {"status": "UPGRADE_VERIFIED", "fromVersion": before_manifest.get("version"), "toVersion": CURRENT_VERSION, "backupPath": str(backup), "pluginPath": str(installed_plugin), "digest": tree_digest(installed_plugin)}


def rollback(installed_plugin: Path, backup: Path) -> dict[str, Any]:
    if not installed_plugin.is_dir() or not backup.is_dir():
        raise C13Error("C13_ROLLBACK_SOURCE_NOT_FOUND")
    current = load_json(installed_plugin / ".codex-plugin" / "plugin.json").get("version")
    restored = load_json(backup / ".codex-plugin" / "plugin.json").get("version")
    parent = installed_plugin.parent
    with tempfile.TemporaryDirectory(prefix="c13-rollback-", dir=parent) as temp:
        staged = Path(temp) / PLUGIN
        copy_tree(backup, staged)
        displaced = parent / f".{PLUGIN}.upgrade"
        if displaced.exists():
            raise C13Error("C13_UPGRADE_TARGET_PRESENT")
        installed_plugin.rename(displaced)
        try:
            staged.rename(installed_plugin)
        except Exception:
            displaced.rename(installed_plugin)
            raise
        shutil.rmtree(displaced)
    return {"status": "ROLLBACK_VERIFIED", "fromVersion": current, "toVersion": restored, "pluginPath": str(installed_plugin), "digest": tree_digest(installed_plugin)}


def git_clone(source: str, target: Path) -> None:
    result = subprocess.run(["git", "clone", "--depth", "1", source, str(target)], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise C13Error(f"C13_GIT_CLONE_FAILED:{result.stderr.strip()}")


def smoke(repo: Path, previous_source: str | None) -> dict[str, Any]:
    assert_repo(repo)
    with tempfile.TemporaryDirectory(prefix="c13-clean-") as temp, tempfile.TemporaryDirectory(prefix="c13-private-") as private_temp:
        root = Path(temp)
        clone = root / "clone"
        shutil.copytree(
            repo,
            clone,
            ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "*.pyo", ".DS_Store"),
        )
        assert_repo(clone)
        installed_root = root / "cache" / MARKETPLACE / PLUGIN
        installed = Path(install(clone, installed_root)["pluginPath"])
        natural_language = installed / "scripts" / "natural_language_gateway.py"
        result = subprocess.run([sys.executable, str(natural_language), "--data-root", private_temp, "interpret", "--text", "启动新项目"], capture_output=True, text=True, check=False)
        output = json.loads(result.stdout)
        boundaries = output.get("boundaries", {})
        if result.returncode != 0 or boundaries.get("ledgerWritePerformed") is not False or boundaries.get("businessWritePerformed") is not False:
            raise C13Error("C13_INSTALLED_GATEWAY_SMOKE_FAILED")
        if previous_source:
            previous = root / "previous"
            previous_path = Path(previous_source).expanduser()
            if previous_path.is_dir():
                shutil.copytree(previous_path, previous, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "*.pyo", ".DS_Store"))
            else:
                git_clone(previous_source, previous)
            old_manifest = manifest(previous)
            shutil.rmtree(installed)
            copy_tree(plugin_root(previous), installed)
            upgrade_result = upgrade(installed, clone, root / "backups", False)
            rollback_result = rollback(installed, Path(upgrade_result["backupPath"]))
            if rollback_result["toVersion"] != old_manifest.get("version"):
                raise C13Error("C13_ROLLBACK_VERSION_MISMATCH")
            shutil.rmtree(installed)
            copy_tree(plugin_root(previous), installed)
            before = tree_digest(installed)
            try:
                upgrade(installed, clone, root / "failure-backups", True)
            except C13Error as error:
                if "PREVIOUS_VERSION_PRESERVED" not in str(error):
                    raise
            else:
                raise C13Error("C13_SIMULATED_FAILURE_NOT_DETECTED")
            if tree_digest(installed) != before:
                raise C13Error("C13_FAILED_UPGRADE_CHANGED_INSTALLED_PLUGIN")
        return {"status": "C13_SMOKE_VERIFIED", "version": manifest(clone)["version"], "cloneDigest": tree_digest(plugin_root(clone)), "installedSkillCount": len(REQUIRED_SKILLS), "gatewayReadOnly": True, "upgradeRollbackVerified": bool(previous_source), "failedUpgradePreservesOldVersion": bool(previous_source)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="C13 release validator")
    commands = parser.add_subparsers(dest="command", required=True)
    install_parser = commands.add_parser("install")
    install_parser.add_argument("--source-repo", required=True)
    install_parser.add_argument("--install-root", required=True)
    upgrade_parser = commands.add_parser("upgrade")
    upgrade_parser.add_argument("--installed-plugin", required=True)
    upgrade_parser.add_argument("--source-repo", required=True)
    upgrade_parser.add_argument("--backup-root", required=True)
    upgrade_parser.add_argument("--simulate-failure", action="store_true")
    rollback_parser = commands.add_parser("rollback")
    rollback_parser.add_argument("--installed-plugin", required=True)
    rollback_parser.add_argument("--backup", required=True)
    smoke_parser = commands.add_parser("smoke")
    smoke_parser.add_argument("--repo", required=True)
    smoke_parser.add_argument("--previous-source")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "install":
            output = install(Path(args.source_repo).resolve(), Path(args.install_root).resolve())
        elif args.command == "upgrade":
            output = upgrade(Path(args.installed_plugin).resolve(), Path(args.source_repo).resolve(), Path(args.backup_root).resolve(), args.simulate_failure)
        elif args.command == "rollback":
            output = rollback(Path(args.installed_plugin).resolve(), Path(args.backup).resolve())
        else:
            output = smoke(Path(args.repo).resolve(), args.previous_source)
        print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (C13Error, OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "REFUSED", "reason": str(error), "writePerformed": False}, ensure_ascii=False, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
