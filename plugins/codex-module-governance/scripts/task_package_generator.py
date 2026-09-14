#!/usr/bin/env python3
"""C04 draft task-package generator. Private data only; it never dispatches work."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from initialize_project import C02Error, PROJECT_ID_PATTERN, is_within, load_data_root
from ledger_manager import (
    CENTRAL_WRITER,
    LedgerError,
    REFERENCE_PATTERN,
    TASK_ID_PATTERN,
    canonical_digest,
    load_ledger,
    verify_ledger,
)


SCHEMA_VERSION = "0.4.0"
PACKAGES_DIRECTORY = Path("task-packages")
PACKAGE_FILENAME = "task-package.json"
RECEIPTS_DIRECTORY = "receipts"
INITIAL_RECEIPT_ID = "receipt-000000-generate"
SENSITIVE_VALUE_PATTERN = re.compile(
    r"(?:-----BEGIN [A-Z ]+PRIVATE KEY-----|\b(?:sk|ghp|github_pat)_[A-Za-z0-9_-]{12,}|"
    r"(?:cookie|authorization|password)\s*[:=]\s*\S+)",
    re.IGNORECASE,
)
ABSOLUTE_REFERENCE_PATTERN = re.compile(r"(?:^|\s)(?:~?/|/[A-Za-z]|[A-Za-z]:\\|https?://)")


class TaskPackageError(Exception):
    """A safe refusal. It never authorizes dispatch or business execution."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def print_result(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def write_json_exclusive(path: Path, payload: Dict[str, Any]) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        with path.open("x", encoding="utf-8") as output:
            output.write(serialized)
    except FileExistsError:
        raise TaskPackageError("IMMUTABLE_TARGET_ALREADY_EXISTS")


def task_package_directory(data_root: Path, project_id: str, package_id: str) -> Path:
    return data_root / PACKAGES_DIRECTORY / project_id / "drafts" / package_id


def task_package_path(data_root: Path, project_id: str, package_id: str) -> Path:
    return task_package_directory(data_root, project_id, package_id) / PACKAGE_FILENAME


def receipt_directory(data_root: Path, project_id: str, package_id: str) -> Path:
    return task_package_directory(data_root, project_id, package_id) / RECEIPTS_DIRECTORY


def require_pattern(value: str, pattern: re.Pattern, error: str) -> str:
    candidate = value.strip()
    if not pattern.fullmatch(candidate):
        raise TaskPackageError(error)
    return candidate


def require_safe_text(value: Any, error: str, maximum: int = 1000) -> str:
    if not isinstance(value, str):
        raise TaskPackageError(error)
    candidate = value.strip()
    if not candidate or len(candidate) > maximum:
        raise TaskPackageError(error)
    if SENSITIVE_VALUE_PATTERN.search(candidate) or ABSOLUTE_REFERENCE_PATTERN.search(candidate):
        raise TaskPackageError("TASK_PACKAGE_BRIEF_CONTAINS_SENSITIVE_OR_LOCATION_DATA")
    return candidate


def require_reference(value: Any, error: str) -> str:
    if not isinstance(value, str):
        raise TaskPackageError(error)
    return require_pattern(value, REFERENCE_PATTERN, error)


def require_exact_object(value: Any, required: set[str], error: str) -> Dict[str, Any]:
    if not isinstance(value, dict) or set(value) != required:
        raise TaskPackageError(error)
    return value


def require_text_list(value: Any, error: str, maximum_items: int = 20) -> List[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= maximum_items:
        raise TaskPackageError(error)
    return [require_safe_text(item, error) for item in value]


def require_reference_list(value: Any, error: str, maximum_items: int = 20) -> List[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= maximum_items:
        raise TaskPackageError(error)
    return [require_reference(item, error) for item in value]


def require_reference_records(value: Any, error: str, include_object_type: bool = False) -> List[Dict[str, str]]:
    if not isinstance(value, list) or not 1 <= len(value) <= 20:
        raise TaskPackageError(error)
    required = {"reference", "purpose"}
    if include_object_type:
        required.add("objectType")
    records: List[Dict[str, str]] = []
    for item in value:
        record = require_exact_object(item, required, error)
        normalized = {
            "reference": require_reference(record["reference"], error),
            "purpose": require_safe_text(record["purpose"], error),
        }
        if include_object_type:
            normalized["objectType"] = require_safe_text(record["objectType"], error, 80)
        records.append(normalized)
    return records


def validate_brief(payload: Any, task_id: str) -> Dict[str, Any]:
    required = {
        "briefSchemaVersion", "recordType", "taskId", "windowRecommendation", "dependencies",
        "requiredReading", "realTimeChecks", "allowedActions", "forbiddenActions", "preflightSnapshot",
        "executionSequence", "acceptance", "hardStops", "rollbackPlan", "deliverables", "handbackRule",
    }
    if isinstance(payload, dict) and "acceptancePolicy" in payload:
        required.add("acceptancePolicy")
    brief = require_exact_object(payload, required, "TASK_PACKAGE_BRIEF_SCHEMA_UNSUPPORTED")
    if brief["briefSchemaVersion"] != SCHEMA_VERSION or brief["recordType"] != "C04_TASK_PACKAGE_BRIEF":
        raise TaskPackageError("TASK_PACKAGE_BRIEF_SCHEMA_UNSUPPORTED")
    if require_pattern(brief["taskId"], TASK_ID_PATTERN, "TASK_ID_INVALID") != task_id:
        raise TaskPackageError("TASK_PACKAGE_BRIEF_TASK_ID_MISMATCH")

    window = require_exact_object(brief["windowRecommendation"], {"mode", "reason"}, "WINDOW_RECOMMENDATION_INVALID")
    mode = require_safe_text(window["mode"], "WINDOW_RECOMMENDATION_INVALID", 12).upper()
    if mode not in {"NEW", "REUSE"}:
        raise TaskPackageError("WINDOW_RECOMMENDATION_INVALID")

    acceptance = require_exact_object(
        brief["acceptance"],
        {"positiveCases", "negativeCases", "idempotencyChecks", "rollbackChecks", "logAndHistoryChecks", "readbackChecks"},
        "TASK_PACKAGE_ACCEPTANCE_INVALID",
    )
    from acceptance_policy import validate_policy, group_is_not_applicable, AcceptancePolicyError
    policy = None
    if "acceptancePolicy" in brief:
        try:
            policy = validate_policy(brief["acceptancePolicy"])
        except AcceptancePolicyError as error:
            raise TaskPackageError(str(error))
        for reason in policy["notApplicable"].values():
            require_safe_text(reason, "TASK_PACKAGE_ACCEPTANCE_REASON_INVALID")
    if any(group_is_not_applicable(policy, key) and value != [] for key, value in acceptance.items()):
        raise TaskPackageError("ACCEPTANCE_CONTRADICTORY_REQUIREMENT")
    normalized_acceptance = {
        key: [] if value == [] and group_is_not_applicable(policy, key)
        else require_text_list(value, "TASK_PACKAGE_ACCEPTANCE_INVALID")
        for key, value in acceptance.items()
    }
    return {
        **({"acceptancePolicy": policy} if policy is not None else {}),
        "windowRecommendation": {"mode": mode, "reason": require_safe_text(window["reason"], "WINDOW_RECOMMENDATION_INVALID")},
        "dependencies": require_reference_list(brief["dependencies"], "TASK_PACKAGE_DEPENDENCIES_INVALID"),
        "requiredReading": require_reference_records(brief["requiredReading"], "TASK_PACKAGE_REQUIRED_READING_INVALID"),
        "realTimeChecks": require_reference_records(brief["realTimeChecks"], "TASK_PACKAGE_REALTIME_CHECKS_INVALID", include_object_type=True),
        "allowedActions": require_text_list(brief["allowedActions"], "TASK_PACKAGE_ALLOWED_ACTIONS_INVALID"),
        "forbiddenActions": require_text_list(brief["forbiddenActions"], "TASK_PACKAGE_FORBIDDEN_ACTIONS_INVALID"),
        "preflightSnapshot": require_text_list(brief["preflightSnapshot"], "TASK_PACKAGE_SNAPSHOT_INVALID"),
        "executionSequence": require_text_list(brief["executionSequence"], "TASK_PACKAGE_EXECUTION_SEQUENCE_INVALID"),
        "acceptance": normalized_acceptance,
        "hardStops": require_text_list(brief["hardStops"], "TASK_PACKAGE_HARD_STOPS_INVALID"),
        "rollbackPlan": require_text_list(brief["rollbackPlan"], "TASK_PACKAGE_ROLLBACK_INVALID"),
        "deliverables": require_text_list(brief["deliverables"], "TASK_PACKAGE_DELIVERABLES_INVALID"),
        "handbackRule": require_safe_text(brief["handbackRule"], "TASK_PACKAGE_HANDBACK_RULE_INVALID"),
    }


def load_private_brief(raw_path: str, data_root: Path, task_id: str) -> Tuple[Dict[str, Any], str]:
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file() or not is_within(path, data_root):
        raise TaskPackageError("PRIVATE_TASK_PACKAGE_BRIEF_REQUIRED_INSIDE_DATA_ROOT")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise TaskPackageError("TASK_PACKAGE_BRIEF_INVALID_JSON")
    return validate_brief(raw, task_id), canonical_digest(raw)


def require_writer(writer_id: Optional[str]) -> str:
    if writer_id != CENTRAL_WRITER:
        raise TaskPackageError("TASK_PACKAGE_WRITER_NOT_AUTHORIZED")
    return writer_id


def verified_planned_task(data_root: Path, project_id: str, task_id: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    try:
        _, verification_exit_code = verify_ledger(argparse.Namespace(data_root=str(data_root), config=None, project_id=project_id))
        if verification_exit_code != 0:
            raise TaskPackageError("SOURCE_LEDGER_INTEGRITY_UNVERIFIED")
        ledger = load_ledger(data_root, project_id)
    except (LedgerError, C02Error) as error:
        raise TaskPackageError(f"SOURCE_LEDGER_{error}")
    if ledger["hardStops"]:
        raise TaskPackageError("SOURCE_LEDGER_HAS_UNRESOLVED_HARD_STOP")
    task = ledger["tasks"].get(task_id)
    if not isinstance(task, dict):
        raise TaskPackageError("SOURCE_LEDGER_TASK_NOT_FOUND")
    if task.get("status") != "PLANNED":
        raise TaskPackageError("TASK_PACKAGE_REQUIRES_PLANNED_TASK")
    return ledger, task


@contextmanager
def package_lock(data_root: Path, project_id: str) -> Iterator[None]:
    root = data_root / PACKAGES_DIRECTORY / project_id
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".task-package-generator.lock"
    try:
        write_json_exclusive(lock_path, {"recordType": "C04_TASK_PACKAGE_OPERATION_LOCK", "projectId": project_id, "createdAt": utc_now(), "pid": os.getpid()})
    except TaskPackageError:
        raise TaskPackageError("TASK_PACKAGE_OPERATION_LOCK_PRESENT")
    try:
        yield
    finally:
        if lock_path.exists():
            lock_path.unlink()


def package_from(ledger: Dict[str, Any], task: Dict[str, Any], project_id: str, package_id: str, brief: Dict[str, Any], brief_digest: str, writer_id: str) -> Dict[str, Any]:
    timestamp = utc_now()
    canonical_title = task.get("canonicalTitle", f"{task['taskId']}｜{task['title']}")
    package_display_name = f"{canonical_title}｜任务包"
    package = {
        "schemaVersion": SCHEMA_VERSION,
        "recordType": "C04_DRAFT_TASK_PACKAGE",
        "packageId": package_id,
        "projectId": project_id,
        "taskId": task["taskId"],
        "displayName": package_display_name,
        "taskIdentity": {
            "taskId": task["taskId"],
            "canonicalTitle": canonical_title,
            "packageDisplayName": package_display_name,
        },
        "generatedAt": timestamp,
        "writerId": writer_id,
        "status": "DRAFT_REQUIRES_BOSS_REVIEW",
        "sourceLedger": {
            "ledgerId": ledger["ledgerId"],
            "revision": ledger["revision"],
            "latestReceiptId": ledger["latestReceiptId"],
            "digest": canonical_digest(ledger),
            "taskPlanRef": task["planRef"],
        },
        "inputBriefDigest": brief_digest,
        "task": {"title": task["title"], "businessGoal": task["businessGoal"]},
        **brief,
        "approvalAndDispatchBoundary": {
            "bossApprovalStatus": "REQUIRED",
            "c05OccupancyCheckStatus": "REQUIRED_BEFORE_DISPATCH",
            "dispatchAllowed": False,
            "taskWindowCreated": False,
            "subAgentCreated": False,
            "objectClaimCreated": False,
            "testCreationAllowed": False,
            "businessWriteAllowed": False,
        },
        "receiptIds": [INITIAL_RECEIPT_ID],
        "latestReceiptId": INITIAL_RECEIPT_ID,
    }
    if task.get("outlineContractDigest"):
        contract = ledger.get("outlineContract", {})
        if contract.get("digest") != task["outlineContractDigest"]:
            contract = ledger.get("outlineHistory", {}).get(task["outlineContractDigest"], {})
        handoff = contract.get("handoff", {})
        if (contract.get("digest") != task["outlineContractDigest"]
                or canonical_digest(handoff) != contract.get("digest")):
            raise TaskPackageError("TASK_OUTLINE_CONTRACT_DIGEST_MISMATCH")
        outcome_ids = task.get("outcomeIds", [])
        outcomes = [item for item in handoff["outcomes"] if item["outcomeId"] in outcome_ids]
        if not outcome_ids or len(outcomes) != len(outcome_ids):
            raise TaskPackageError("TASK_OUTCOME_REFERENCE_INVALID")
        goal_ids = {item["goalId"] for item in outcomes}
        package["task"]["outcomeContract"] = {
            "digest": contract["digest"], "outline": handoff["outline"],
            "goals": [item for item in handoff["goals"] if item["goalId"] in goal_ids],
            "outcomes": outcomes,
        }
    return package


def draft_result(package: Dict[str, Any], write_performed: bool) -> Dict[str, Any]:
    return {
        "status": "DRAFT_REQUIRES_BOSS_REVIEW" if write_performed else "READY_FOR_BOSS_APPROVAL",
        "action": "GENERATE_DRAFT_TASK_PACKAGE",
        "packageId": package["packageId"],
        "taskId": package["taskId"],
        "displayName": package["displayName"],
        "sourceLedgerRevision": package["sourceLedger"]["revision"],
        "windowRecommendation": package["windowRecommendation"],
        "dispatchAllowed": False,
        "taskWindowCreated": False,
        "testCreationAllowed": False,
        "businessWriteAllowed": False,
        "writePerformed": write_performed,
        "message": "任务包仅供 Boss 审阅；未派发任务、创建窗口、占用对象、创建 TEST 或执行任何业务写入。",
    }


def generate(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args)
    project_id = require_pattern(args.project_id, PROJECT_ID_PATTERN, "PROJECT_ID_INVALID")
    package_id = require_pattern(args.package_id, PROJECT_ID_PATTERN, "TASK_PACKAGE_ID_INVALID")
    task_id = require_pattern(args.task_id, TASK_ID_PATTERN, "TASK_ID_INVALID")
    brief, brief_digest = load_private_brief(args.brief, data_root, task_id)
    ledger, task = verified_planned_task(data_root, project_id, task_id)
    package = package_from(ledger, task, project_id, package_id, brief, brief_digest, CENTRAL_WRITER)
    if args.dry_run:
        return draft_result(package, False), 0

    writer_id = require_writer(args.writer_id)
    with package_lock(data_root, project_id):
        target_directory = task_package_directory(data_root, project_id, package_id)
        if target_directory.exists():
            raise TaskPackageError("DRAFT_TASK_PACKAGE_ALREADY_EXISTS")
        ledger, task = verified_planned_task(data_root, project_id, task_id)
        package = package_from(ledger, task, project_id, package_id, brief, brief_digest, writer_id)
        target_directory.mkdir(parents=True, exist_ok=False)
        receipt_directory(data_root, project_id, package_id).mkdir(exist_ok=False)
        receipt = {
            "schemaVersion": SCHEMA_VERSION,
            "recordType": "C04_IMMUTABLE_TASK_PACKAGE_RECEIPT",
            "receiptId": INITIAL_RECEIPT_ID,
            "createdAt": utc_now(),
            "projectId": project_id,
            "packageId": package_id,
            "taskId": task_id,
            "writerId": writer_id,
            "operation": "GENERATE_DRAFT_TASK_PACKAGE",
            "beforePackageDigest": "NONE",
            "afterPackageDigest": canonical_digest(package),
            "sourceLedgerDigest": package["sourceLedger"]["digest"],
            "inputBriefDigest": brief_digest,
            "afterPackage": package,
        }
        write_json_exclusive(receipt_directory(data_root, project_id, package_id) / f"{INITIAL_RECEIPT_ID}.json", receipt)
        write_json_exclusive(task_package_path(data_root, project_id, package_id), package)
    result = draft_result(package, True)
    result["receiptId"] = INITIAL_RECEIPT_ID
    return result, 0


def load_package(data_root: Path, project_id: str, package_id: str) -> Dict[str, Any]:
    path = task_package_path(data_root, project_id, package_id)
    if not path.is_file():
        raise TaskPackageError("DRAFT_TASK_PACKAGE_NOT_FOUND")
    try:
        package = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise TaskPackageError("DRAFT_TASK_PACKAGE_INVALID_JSON")
    required = {
        "schemaVersion", "recordType", "packageId", "projectId", "taskId", "generatedAt", "writerId", "status",
        "sourceLedger", "inputBriefDigest", "displayName", "taskIdentity", "task", "windowRecommendation", "dependencies", "requiredReading",
        "realTimeChecks", "allowedActions", "forbiddenActions", "preflightSnapshot", "executionSequence", "acceptance",
        "hardStops", "rollbackPlan", "deliverables", "handbackRule", "approvalAndDispatchBoundary", "receiptIds", "latestReceiptId",
    }
    if (
        not isinstance(package, dict)
        or not required.issubset(package)
        or package.get("schemaVersion") != SCHEMA_VERSION
        or package.get("recordType") != "C04_DRAFT_TASK_PACKAGE"
        or package.get("packageId") != package_id
        or package.get("projectId") != project_id
        or package.get("taskId") != package.get("taskIdentity", {}).get("taskId")
        or package.get("displayName") != package.get("taskIdentity", {}).get("packageDisplayName")
        or package.get("displayName") != f"{package.get('taskIdentity', {}).get('canonicalTitle')}｜任务包"
        or package.get("status") != "DRAFT_REQUIRES_BOSS_REVIEW"
        or package.get("writerId") != CENTRAL_WRITER
        or package.get("receiptIds") != [INITIAL_RECEIPT_ID]
        or package.get("latestReceiptId") != INITIAL_RECEIPT_ID
    ):
        raise TaskPackageError("DRAFT_TASK_PACKAGE_SCHEMA_UNSUPPORTED")
    if "acceptancePolicy" in package:
        from acceptance_policy import validate_policy, AcceptancePolicyError
        try:
            validate_policy(package["acceptancePolicy"])
        except AcceptancePolicyError as error:
            raise TaskPackageError(str(error))
    return package


def verify_package(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args)
    project_id = require_pattern(args.project_id, PROJECT_ID_PATTERN, "PROJECT_ID_INVALID")
    package_id = require_pattern(args.package_id, PROJECT_ID_PATTERN, "TASK_PACKAGE_ID_INVALID")
    package = load_package(data_root, project_id, package_id)
    receipt_path = receipt_directory(data_root, project_id, package_id) / f"{INITIAL_RECEIPT_ID}.json"
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise TaskPackageError("IMMUTABLE_TASK_PACKAGE_RECEIPT_MISSING_OR_INVALID")
    boundary = package["approvalAndDispatchBoundary"]
    if (
        receipt.get("recordType") != "C04_IMMUTABLE_TASK_PACKAGE_RECEIPT"
        or receipt.get("receiptId") != INITIAL_RECEIPT_ID
        or receipt.get("beforePackageDigest") != "NONE"
        or receipt.get("afterPackageDigest") != canonical_digest(receipt.get("afterPackage", {}))
        or canonical_digest(package) != receipt.get("afterPackageDigest")
        or receipt.get("afterPackage") != package
        or any(boundary.get(key) is not False for key in ("dispatchAllowed", "taskWindowCreated", "subAgentCreated", "objectClaimCreated", "testCreationAllowed", "businessWriteAllowed"))
        or boundary.get("bossApprovalStatus") != "REQUIRED"
        or boundary.get("c05OccupancyCheckStatus") != "REQUIRED_BEFORE_DISPATCH"
    ):
        raise TaskPackageError("IMMUTABLE_TASK_PACKAGE_RECEIPT_CHAIN_INVALID")
    return {
        "status": "DRAFT_TASK_PACKAGE_INTEGRITY_VERIFIED",
        "projectId": project_id,
        "packageId": package_id,
        "taskId": package["taskId"],
        "sourceLedgerRevision": package["sourceLedger"]["revision"],
        "dispatchAllowed": False,
        "taskWindowCreated": False,
        "testCreationAllowed": False,
        "businessWriteAllowed": False,
        "writePerformed": False,
    }, 0


def read_summary(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args)
    project_id = require_pattern(args.project_id, PROJECT_ID_PATTERN, "PROJECT_ID_INVALID")
    package_id = require_pattern(args.package_id, PROJECT_ID_PATTERN, "TASK_PACKAGE_ID_INVALID")
    package = load_package(data_root, project_id, package_id)
    return {
        "status": package["status"],
        "projectId": project_id,
        "packageId": package_id,
        "taskId": package["taskId"],
        "windowRecommendation": package["windowRecommendation"],
        "sourceLedgerRevision": package["sourceLedger"]["revision"],
        "nextRequiredGate": package["approvalAndDispatchBoundary"]["c05OccupancyCheckStatus"],
        "writePerformed": False,
    }, 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="C04 non-dispatching Codex task-package generator")
    root_source = parser.add_mutually_exclusive_group(required=True)
    root_source.add_argument("--data-root", help="Private data directory outside every Git worktree")
    root_source.add_argument("--config", help="Private module config JSON containing storage.userDataRoot")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--writer-id", help=f"The only accepted writer is {CENTRAL_WRITER}")
    commands = parser.add_subparsers(dest="command", required=True)

    generate_command = commands.add_parser("generate")
    generate_command.add_argument("--package-id", required=True)
    generate_command.add_argument("--task-id", required=True)
    generate_command.add_argument("--brief", required=True)
    action = generate_command.add_mutually_exclusive_group(required=True)
    action.add_argument("--dry-run", action="store_true")
    action.add_argument("--apply", action="store_true")

    verify_command = commands.add_parser("verify")
    verify_command.add_argument("--package-id", required=True)

    summary_command = commands.add_parser("read-summary")
    summary_command.add_argument("--package-id", required=True)
    return parser.parse_args()


def dispatch(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    handlers = {"generate": generate, "verify": verify_package, "read-summary": read_summary}
    return handlers[args.command](args)


def main() -> int:
    args = parse_args()
    try:
        result, exit_code = dispatch(args)
        print_result(result)
        return exit_code
    except (TaskPackageError, LedgerError, C02Error) as error:
        print_result({
            "status": "REFUSED",
            "reason": str(error),
            "writePerformed": False,
            "message": "任务包操作已停止；没有派发任务、创建窗口、占用对象、创建 TEST 或修改业务对象。",
        })
        return 2


if __name__ == "__main__":
    sys.exit(main())
