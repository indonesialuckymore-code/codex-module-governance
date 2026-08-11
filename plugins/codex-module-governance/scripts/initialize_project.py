#!/usr/bin/env python3
"""C02 private project registration. It never creates tasks or business data."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple


SCHEMA_VERSION = "0.2.0"
PROJECT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
REGISTRY_DIRECTORY = Path("project-registry")
INDEX_FILENAME = "projects-index.json"
LOCK_FILENAME = ".initializer.lock"


class C02Error(Exception):
    """A refusal that must not cause a write."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def print_result(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def is_within(candidate: Path, container: Path) -> bool:
    try:
        candidate.relative_to(container)
        return True
    except ValueError:
        return False


def product_repo_root() -> Path:
    # <repo>/plugins/codex-module-governance/scripts/initialize_project.py
    return Path(__file__).resolve().parents[3]


def has_git_ancestor(path: Path) -> bool:
    probe = path if path.exists() else path.parent
    for ancestor in (probe, *probe.parents):
        if (ancestor / ".git").exists():
            return True
    return False


def validate_data_root(raw_path: str) -> Path:
    candidate = Path(raw_path).expanduser().resolve()
    repo_root = product_repo_root()

    if is_within(candidate, repo_root) or is_within(repo_root, candidate):
        raise C02Error("PRIVATE_DATA_ROOT_OVERLAPS_PRODUCT_REPOSITORY")
    if has_git_ancestor(candidate):
        raise C02Error("PRIVATE_DATA_ROOT_IS_INSIDE_GIT_WORKTREE")
    return candidate


def load_data_root(args: argparse.Namespace) -> Path:
    if args.data_root:
        return validate_data_root(args.data_root)

    if not args.config:
        raise C02Error("PRIVATE_DATA_ROOT_OR_CONFIG_REQUIRED")

    config_path = Path(args.config).expanduser().resolve()
    if not config_path.is_file():
        raise C02Error("PRIVATE_CONFIG_NOT_FOUND")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        raw_root = config["storage"]["userDataRoot"]
    except (json.JSONDecodeError, KeyError, TypeError):
        raise C02Error("PRIVATE_CONFIG_INVALID")

    if not isinstance(raw_root, str) or not raw_root.strip() or raw_root.startswith("replace-with-"):
        raise C02Error("PRIVATE_CONFIG_DATA_ROOT_INVALID")
    return validate_data_root(raw_root)


def normalize_display_name(value: str) -> str:
    return "".join(char for char in value.casefold() if char.isalnum())


def validate_input(args: argparse.Namespace) -> Tuple[str, str, str]:
    project_id = args.project_id.strip()
    display_name = args.display_name.strip()
    scope_summary = args.scope_summary.strip()

    if not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise C02Error("PROJECT_ID_INVALID")
    if not display_name or len(display_name) > 120:
        raise C02Error("PROJECT_DISPLAY_NAME_INVALID")
    if not scope_summary or len(scope_summary) > 2000:
        raise C02Error("PROJECT_SCOPE_SUMMARY_INVALID")
    return project_id, display_name, scope_summary


def empty_index(timestamp: str) -> Dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "recordType": "C02_PROJECT_STARTUP_INDEX",
        "createdAt": timestamp,
        "updatedAt": timestamp,
        "projects": [],
    }


def load_index(index_path: Path, timestamp: str) -> Dict[str, Any]:
    if not index_path.exists():
        return empty_index(timestamp)
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise C02Error("PROJECT_INDEX_INVALID_JSON")

    if (
        not isinstance(index, dict)
        or index.get("schemaVersion") != SCHEMA_VERSION
        or index.get("recordType") != "C02_PROJECT_STARTUP_INDEX"
        or not isinstance(index.get("projects"), list)
    ):
        raise C02Error("PROJECT_INDEX_SCHEMA_UNSUPPORTED")
    for entry in index["projects"]:
        if not isinstance(entry, dict) or not all(isinstance(entry.get(key), str) for key in ("projectId", "displayName", "normalizedDisplayName", "createdAt", "state")):
            raise C02Error("PROJECT_INDEX_ENTRY_INVALID")
    return index


def duplicates(index: Dict[str, Any], project_id: str, display_name: str) -> List[Dict[str, str]]:
    normalized_name = normalize_display_name(display_name)
    findings: List[Dict[str, str]] = []
    for entry in index["projects"]:
        reason: Optional[str] = None
        if entry["projectId"] == project_id:
            reason = "EXACT_PROJECT_ID"
        elif entry["normalizedDisplayName"] == normalized_name:
            reason = "EXACT_DISPLAY_NAME"
        else:
            score = SequenceMatcher(None, entry["normalizedDisplayName"], normalized_name).ratio()
            if score >= 0.82:
                reason = "SIMILAR_DISPLAY_NAME"
        if reason:
            findings.append({
                "projectId": entry["projectId"],
                "displayName": entry["displayName"],
                "state": entry["state"],
                "reason": reason,
            })
    return findings


def duplicate_action(findings: List[Dict[str, str]]) -> str:
    reasons = {finding["reason"] for finding in findings}
    if "EXACT_PROJECT_ID" in reasons:
        return "REUSE_EXISTING_PROJECT_CARD"
    return "REVIEW_REUSE_OR_MIGRATE"


def validate_existing_project_paths(data_root: Path, entries: List[Dict[str, Any]]) -> None:
    for entry in entries:
        expected_card = data_root / REGISTRY_DIRECTORY / "project-cards" / entry["projectId"] / "project-card.json"
        if not expected_card.is_file():
            raise C02Error("PROJECT_INDEX_AND_CARD_MISMATCH")


def write_json_exclusive(path: Path, payload: Dict[str, Any]) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        with path.open("x", encoding="utf-8") as output:
            output.write(serialized)
    except FileExistsError:
        raise C02Error("TARGET_FILE_ALREADY_EXISTS")


def replace_json_atomically(path: Path, payload: Dict[str, Any]) -> None:
    temporary_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        with temporary_path.open("x", encoding="utf-8") as output:
            output.write(serialized)
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


@contextmanager
def registry_lock(registry_root: Path) -> Iterator[None]:
    registry_root.mkdir(parents=True, exist_ok=True)
    lock_path = registry_root / LOCK_FILENAME
    lock_payload = {"recordType": "C02_INITIALIZER_LOCK", "createdAt": utc_now(), "pid": os.getpid()}
    try:
        write_json_exclusive(lock_path, lock_payload)
    except C02Error:
        raise C02Error("PROJECT_INITIALIZATION_LOCK_PRESENT")
    try:
        yield
    finally:
        if lock_path.exists():
            lock_path.unlink()


def make_card(project_id: str, display_name: str, scope_summary: str, timestamp: str) -> Dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "recordType": "C02_PROJECT_STARTUP_CARD",
        "createdAt": timestamp,
        "project": {
            "id": project_id,
            "displayName": display_name,
            "scopeSummary": scope_summary,
        },
        "state": "PROJECT_REGISTERED",
        "executionBoundary": {
            "dispatchAllowed": False,
            "testCreationAllowed": False,
            "businessWriteAllowed": False,
        },
        "nextRequiredDecision": "C03_ENGINEERING_LEDGER_GOVERNANCE_REQUIRED",
    }


def make_receipt(project_id: str, timestamp: str) -> Dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "recordType": "C02_INITIALIZATION_RECEIPT",
        "createdAt": timestamp,
        "projectId": project_id,
        "result": "PROJECT_REGISTERED",
        "createdObjects": ["project-card.json", "initialization-receipt.json", "projects-index.json"],
        "notCreated": ["engineering ledger", "task", "task window", "sub-agent", "TEST data", "business object"],
        "remainingGate": "C03_ENGINEERING_LEDGER_GOVERNANCE_REQUIRED",
    }


def inspection_result(data_root: Path, project_id: str, display_name: str, scope_summary: str, index: Dict[str, Any], findings: List[Dict[str, str]]) -> Dict[str, Any]:
    if findings:
        return {
            "status": "REQUIRES_REVIEW",
            "action": duplicate_action(findings),
            "projectId": project_id,
            "displayName": display_name,
            "candidateProjects": findings,
            "writePerformed": False,
            "message": "发现同名、近似或同编号项目。请先决定复用、迁移评估或人工裁定。",
        }
    return {
        "status": "READY_FOR_BOSS_APPROVAL",
        "action": "CREATE_PROJECT_STARTUP_CARD",
        "projectId": project_id,
        "displayName": display_name,
        "scopeSummary": scope_summary,
        "privateDataRoot": str(data_root),
        "registeredProjectCount": len(index["projects"]),
        "writePerformed": False,
        "message": "未发现编号、名称或近似名称冲突。批准后仅创建私有项目启动卡，不派发、不施工。",
    }


def initialize(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args)
    project_id, display_name, scope_summary = validate_input(args)
    registry_root = data_root / REGISTRY_DIRECTORY
    index_path = registry_root / INDEX_FILENAME
    timestamp = utc_now()

    if args.dry_run:
        index = load_index(index_path, timestamp)
        validate_existing_project_paths(data_root, index["projects"])
        result = inspection_result(data_root, project_id, display_name, scope_summary, index, duplicates(index, project_id, display_name))
        return result, 3 if result["status"] == "REQUIRES_REVIEW" else 0

    with registry_lock(registry_root):
        index = load_index(index_path, timestamp)
        validate_existing_project_paths(data_root, index["projects"])
        findings = duplicates(index, project_id, display_name)
        if findings:
            return inspection_result(data_root, project_id, display_name, scope_summary, index, findings), 3

        project_directory = registry_root / "project-cards" / project_id
        if project_directory.exists():
            raise C02Error("PROJECT_CARD_DIRECTORY_ALREADY_EXISTS")
        project_directory.mkdir(parents=True, exist_ok=False)

        card = make_card(project_id, display_name, scope_summary, timestamp)
        receipt = make_receipt(project_id, timestamp)
        write_json_exclusive(project_directory / "project-card.json", card)
        write_json_exclusive(project_directory / "initialization-receipt.json", receipt)

        index["projects"].append({
            "projectId": project_id,
            "displayName": display_name,
            "normalizedDisplayName": normalize_display_name(display_name),
            "createdAt": timestamp,
            "state": "PROJECT_REGISTERED",
        })
        index["updatedAt"] = timestamp
        if index_path.exists():
            replace_json_atomically(index_path, index)
        else:
            write_json_exclusive(index_path, index)

    return {
        "status": "PROJECT_REGISTERED",
        "action": "PROJECT_STARTUP_CARD_CREATED",
        "projectId": project_id,
        "displayName": display_name,
        "privateDataRoot": str(data_root),
        "created": ["project-card.json", "initialization-receipt.json", "projects-index.json"],
        "notCreated": ["engineering ledger", "task", "task window", "sub-agent", "TEST data", "business object"],
        "nextRequiredDecision": "C03_ENGINEERING_LEDGER_GOVERNANCE_REQUIRED",
    }, 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="C02 safe private project registration")
    root_source = parser.add_mutually_exclusive_group(required=True)
    root_source.add_argument("--data-root", help="Private data directory outside every Git worktree")
    root_source.add_argument("--config", help="Private module config JSON containing storage.userDataRoot")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--scope-summary", required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--dry-run", action="store_true", help="Inspect only; write nothing")
    action.add_argument("--apply", action="store_true", help="Create a project startup card after Boss approval")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result, exit_code = initialize(args)
        print_result(result)
        return exit_code
    except C02Error as error:
        print_result({
            "status": "REFUSED",
            "reason": str(error),
            "writePerformed": False,
            "message": "初始化器已停止，未创建或覆盖项目数据。",
        })
        return 2


if __name__ == "__main__":
    sys.exit(main())
