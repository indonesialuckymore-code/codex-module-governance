#!/usr/bin/env python3
"""C05 atomic occupancy review. It never dispatches or touches business systems."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from initialize_project import C02Error, PROJECT_ID_PATTERN, is_within, load_data_root
from ledger_manager import (
    CENTRAL_WRITER,
    LedgerError,
    MAX_TASKS_PER_WINDOW,
    TASK_ID_PATTERN,
    canonical_digest,
    commit_mutation,
    ledger_lock,
    load_ledger,
    receipt_directory as ledger_receipt_directory,
    require_object_key,
    utc_now,
    verify_ledger,
    window_assignment_count,
    window_current_task_id,
)
from task_package_generator import (
    TaskPackageError,
    load_package,
    verify_package,
)


SCHEMA_VERSION = "0.5.0"
REVIEWS_DIRECTORY = Path("occupancy-reviews")
DECISION_FILENAME = "occupancy-decision.json"
RECEIPTS_DIRECTORY = "receipts"
INITIAL_RECEIPT_ID = "receipt-000000-review"
REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
RESOURCE_CLASSES = {
    "FILE", "TABLE", "FIELD", "VIEW", "WORKFLOW", "SERVICE", "CONFIG",
    "PRODUCTION_SWITCH", "FACT_SOURCE", "ACCOUNT", "EXTERNAL_ENTRY",
    "TEST_SCOPE", "LOG_SCOPE", "ROLLBACK_SCOPE",
}
TASK_RUNTIME_MODEL = "gpt-5.6-terra"
TASK_PERMISSION_CLASS = "WORKTREE_SCOPED"
ALLOWED_TASK_PERMISSION_PROFILES = {":workspace", "qianyi-task-terra", "workspace-write"}
WINDOW_PERMISSION_METHODS = {
    "PERMISSION_PROFILE_READBACK",
    "LEGACY_WORKSPACE_SANDBOX_READBACK",
    "MANUAL_UI_PERMISSION_EVIDENCE",
}
TERRA_MODEL_ENFORCEMENT_METHODS = {
    "NATIVE_CREATE_THREAD_MODEL_PARAMETER",
    "NATIVE_SEND_MESSAGE_MODEL_OVERRIDE",
    "MANUAL_UI_TERRA_SELECTION_EVIDENCE",
}


class OccupancyError(Exception):
    """A safe refusal that cannot authorize dispatch."""


def print_result(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def require_reference(value: Any, error: str) -> str:
    if not isinstance(value, str):
        raise OccupancyError(error)
    candidate = value.strip()
    if not REFERENCE_PATTERN.fullmatch(candidate):
        raise OccupancyError(error)
    return candidate


def require_exact_object(value: Any, required: set[str], error: str) -> Dict[str, Any]:
    if not isinstance(value, dict) or set(value) != required:
        raise OccupancyError(error)
    return value


def require_reference_list(value: Any, error: str, allow_empty: bool = False) -> List[str]:
    if not isinstance(value, list) or len(value) > 40 or (not allow_empty and not value):
        raise OccupancyError(error)
    normalized = [require_reference(item, error) for item in value]
    if len(normalized) != len(set(normalized)):
        raise OccupancyError(error)
    return normalized


def terra_model_is_enforced(window: Dict[str, Any]) -> bool:
    """A label in the ledger is insufficient; reuse needs recorded model-selection evidence."""
    control = window.get("modelEnforcement")
    return (
        window.get("model") == TASK_RUNTIME_MODEL
        and window.get("runtimeModel") == TASK_RUNTIME_MODEL
        and isinstance(control, dict)
        and control.get("model") == TASK_RUNTIME_MODEL
        and control.get("method") in TERRA_MODEL_ENFORCEMENT_METHODS
        and isinstance(control.get("evidenceRef"), str)
        and bool(control["evidenceRef"].strip())
    )


def bounded_permission_is_enforced(window: Dict[str, Any]) -> bool:
    control = window.get("permissionEnforcement")
    return (
        isinstance(control, dict)
        and control.get("permissionClass") == TASK_PERMISSION_CLASS
        and control.get("profile") in ALLOWED_TASK_PERMISSION_PROFILES
        and control.get("method") in WINDOW_PERMISSION_METHODS
        and isinstance(control.get("evidenceRef"), str)
        and bool(control["evidenceRef"].strip())
    )


def decision_directory(data_root: Path, project_id: str, review_id: str) -> Path:
    return data_root / REVIEWS_DIRECTORY / project_id / review_id


def decision_path(data_root: Path, project_id: str, review_id: str) -> Path:
    return decision_directory(data_root, project_id, review_id) / DECISION_FILENAME


def receipt_directory(data_root: Path, project_id: str, review_id: str) -> Path:
    return decision_directory(data_root, project_id, review_id) / RECEIPTS_DIRECTORY


def write_json_exclusive(path: Path, payload: Dict[str, Any]) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        with path.open("x", encoding="utf-8") as output:
            output.write(serialized)
    except FileExistsError:
        raise OccupancyError("IMMUTABLE_TARGET_ALREADY_EXISTS")


@contextmanager
def review_lock(data_root: Path, project_id: str) -> Iterator[None]:
    root = data_root / REVIEWS_DIRECTORY / ".locks"
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / f"{project_id}.lock"
    try:
        write_json_exclusive(lock_path, {
            "recordType": "C05_OCCUPANCY_REVIEW_OPERATION_LOCK",
            "projectId": project_id,
            "createdAt": utc_now(),
            "pid": os.getpid(),
        })
    except OccupancyError:
        raise OccupancyError("OCCUPANCY_REVIEW_OPERATION_LOCK_PRESENT")
    try:
        yield
    finally:
        if lock_path.exists():
            lock_path.unlink()


def validate_review(payload: Any, project_id: str, package_id: str) -> Dict[str, Any]:
    required = {
        "reviewSchemaVersion", "recordType", "reviewId", "projectId", "packageId", "taskId",
        "bossReview", "dependencyGate", "crossModuleGate", "windowReview", "occupancyRequests",
    }
    review = require_exact_object(payload, required, "C05_REVIEW_SCHEMA_UNSUPPORTED")
    if review["reviewSchemaVersion"] != SCHEMA_VERSION or review["recordType"] != "C05_OCCUPANCY_REVIEW_REQUEST":
        raise OccupancyError("C05_REVIEW_SCHEMA_UNSUPPORTED")
    if not isinstance(review["projectId"], str) or not PROJECT_ID_PATTERN.fullmatch(review["projectId"]):
        raise OccupancyError("PROJECT_ID_INVALID")
    if review["projectId"] != project_id:
        raise OccupancyError("C05_REVIEW_PROJECT_MISMATCH")
    if not isinstance(review["packageId"], str) or not PROJECT_ID_PATTERN.fullmatch(review["packageId"]):
        raise OccupancyError("TASK_PACKAGE_ID_INVALID")
    if review["packageId"] != package_id:
        raise OccupancyError("C05_REVIEW_PACKAGE_MISMATCH")
    review_id = require_reference(review["reviewId"], "C05_REVIEW_ID_INVALID")
    task_id = require_reference(review["taskId"], "TASK_ID_INVALID")
    if not TASK_ID_PATTERN.fullmatch(task_id):
        raise OccupancyError("TASK_ID_INVALID")

    boss = require_exact_object(review["bossReview"], {"status", "reference"}, "C05_BOSS_REVIEW_INVALID")
    boss_status = str(boss["status"]).strip().upper()
    if boss_status not in {"REQUIRED", "APPROVED", "REJECTED"}:
        raise OccupancyError("C05_BOSS_REVIEW_INVALID")
    boss_reference = require_reference(boss["reference"], "C05_BOSS_REVIEW_REFERENCE_INVALID")

    dependencies = require_exact_object(review["dependencyGate"], {"status", "references"}, "C05_DEPENDENCY_GATE_INVALID")
    dependency_status = str(dependencies["status"]).strip().upper()
    if dependency_status not in {"SATISFIED", "WAITING", "UNKNOWN"}:
        raise OccupancyError("C05_DEPENDENCY_GATE_INVALID")
    dependency_references = require_reference_list(
        dependencies["references"], "C05_DEPENDENCY_REFERENCES_INVALID", allow_empty=dependency_status != "SATISFIED"
    )

    cross_module = require_exact_object(review["crossModuleGate"], {"status", "reference"}, "C05_CROSS_MODULE_GATE_INVALID")
    cross_module_status = str(cross_module["status"]).strip().upper()
    if cross_module_status not in {"NOT_APPLICABLE", "CLEAR", "CONFLICT", "UNKNOWN"}:
        raise OccupancyError("C05_CROSS_MODULE_GATE_INVALID")
    cross_module_reference = require_reference(cross_module["reference"], "C05_CROSS_MODULE_REFERENCE_INVALID")

    raw_window = review["windowReview"]
    if not isinstance(raw_window, dict) or not {"mode", "candidateWindowId"}.issubset(raw_window) or not set(raw_window).issubset({"mode", "candidateWindowId", "contextCompatibility", "compatibilityEvidenceRefs"}):
        raise OccupancyError("C05_WINDOW_REVIEW_INVALID")
    window = raw_window
    window_mode = str(window["mode"]).strip().upper()
    if window_mode not in {"AUTO", "NEW", "REUSE"}:
        raise OccupancyError("C05_WINDOW_REVIEW_INVALID")
    candidate_window_id: Optional[str] = None
    if window["candidateWindowId"] is not None:
        candidate_window_id = require_reference(window["candidateWindowId"], "C05_WINDOW_ID_INVALID")
        if not TASK_ID_PATTERN.fullmatch(candidate_window_id):
            raise OccupancyError("C05_WINDOW_ID_INVALID")
    if window_mode == "NEW" and candidate_window_id is not None:
        raise OccupancyError("C05_NEW_WINDOW_MUST_NOT_HAVE_WINDOW_ID")
    if window_mode == "REUSE" and candidate_window_id is None:
        raise OccupancyError("C05_REUSE_WINDOW_ID_REQUIRED")
    compatibility = str(window.get("contextCompatibility", "NOT_ASSESSED")).strip().upper()
    if compatibility not in {"COMPATIBLE", "INCOMPATIBLE", "UNKNOWN", "NOT_ASSESSED"}:
        raise OccupancyError("C05_WINDOW_CONTEXT_COMPATIBILITY_INVALID")
    compatibility_refs = require_reference_list(
        window.get("compatibilityEvidenceRefs", []),
        "C05_WINDOW_COMPATIBILITY_EVIDENCE_INVALID",
        allow_empty=compatibility != "COMPATIBLE",
    )
    if compatibility == "COMPATIBLE" and not compatibility_refs:
        raise OccupancyError("C05_WINDOW_COMPATIBILITY_EVIDENCE_REQUIRED")
    if window_mode == "REUSE" and compatibility != "COMPATIBLE":
        raise OccupancyError("C05_REUSE_REQUIRES_COMPATIBLE_CONTEXT")

    raw_requests = review["occupancyRequests"]
    if not isinstance(raw_requests, list) or not 1 <= len(raw_requests) <= 80:
        raise OccupancyError("C05_OCCUPANCY_REQUESTS_INVALID")
    requests: List[Dict[str, Any]] = []
    seen_objects = set()
    for raw in raw_requests:
        item = require_exact_object(
            raw, {"objectKey", "conflictKey", "resourceClass", "intent", "exclusive"},
            "C05_OCCUPANCY_REQUEST_INVALID",
        )
        if not isinstance(item["objectKey"], str) or not isinstance(item["conflictKey"], str):
            raise OccupancyError("C05_OCCUPANCY_REQUEST_INVALID")
        try:
            object_key = require_object_key(item["objectKey"])
            conflict_key = require_object_key(item["conflictKey"])
        except LedgerError as error:
            raise OccupancyError(str(error))
        resource_class = str(item["resourceClass"]).strip().upper()
        intent = str(item["intent"]).strip().upper()
        if resource_class not in RESOURCE_CLASSES or intent not in {"READ", "WRITE"} or type(item["exclusive"]) is not bool:
            raise OccupancyError("C05_OCCUPANCY_REQUEST_INVALID")
        if object_key in seen_objects:
            raise OccupancyError("C05_DUPLICATE_OBJECT_REQUEST")
        seen_objects.add(object_key)
        requests.append({
            "objectKey": object_key,
            "conflictKey": conflict_key,
            "resourceClass": resource_class,
            "intent": intent,
            "exclusive": item["exclusive"],
        })
    return {
        "reviewId": review_id,
        "projectId": project_id,
        "packageId": package_id,
        "taskId": task_id,
        "bossReview": {"status": boss_status, "reference": boss_reference},
        "dependencyGate": {"status": dependency_status, "references": dependency_references},
        "crossModuleGate": {"status": cross_module_status, "reference": cross_module_reference},
        "windowReview": {
            "mode": window_mode,
            "candidateWindowId": candidate_window_id,
            "contextCompatibility": compatibility,
            "compatibilityEvidenceRefs": compatibility_refs,
        },
        "occupancyRequests": requests,
    }


def load_private_review(raw_path: str, data_root: Path, project_id: str, package_id: str) -> Tuple[Dict[str, Any], str]:
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file() or not is_within(path, data_root):
        raise OccupancyError("PRIVATE_C05_REVIEW_REQUIRED_INSIDE_DATA_ROOT")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise OccupancyError("C05_REVIEW_INVALID_JSON")
    return validate_review(raw, project_id, package_id), canonical_digest(raw)


def require_writer(writer_id: Optional[str]) -> str:
    if writer_id != CENTRAL_WRITER:
        raise OccupancyError("C05_WRITER_NOT_AUTHORIZED")
    return writer_id


def verified_sources(data_root: Path, project_id: str, package_id: str, review: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    try:
        _, package_exit = verify_package(argparse.Namespace(
            data_root=str(data_root), config=None, project_id=project_id, package_id=package_id
        ))
        _, ledger_exit = verify_ledger(argparse.Namespace(data_root=str(data_root), config=None, project_id=project_id))
        if package_exit != 0 or ledger_exit != 0:
            raise OccupancyError("C05_SOURCE_INTEGRITY_UNVERIFIED")
        package = load_package(data_root, project_id, package_id)
        ledger = load_ledger(data_root, project_id)
    except (TaskPackageError, LedgerError, C02Error) as error:
        raise OccupancyError(f"C05_SOURCE_{error}")
    if package["taskId"] != review["taskId"]:
        raise OccupancyError("C05_REVIEW_TASK_MISMATCH")
    task = ledger["tasks"].get(review["taskId"])
    if not isinstance(task, dict):
        raise OccupancyError("C05_TASK_NOT_FOUND")
    if task.get("status") != "PLANNED":
        raise OccupancyError("C05_REQUIRES_PLANNED_TASK")
    return package, ledger


def resolve_window(package: Dict[str, Any], ledger: Dict[str, Any], review: Dict[str, Any]) -> Dict[str, Any]:
    task_id = review["taskId"]
    current_matching = sorted(
        window_id for window_id, window in ledger["windows"].items()
        if window_current_task_id(window) == task_id
        and window.get("status") == "REGISTERED"
        and terra_model_is_enforced(window)
        and bounded_permission_is_enforced(window)
    )
    reusable = []
    for window_id, window in ledger["windows"].items():
        count = window_assignment_count(window)
        current_task_id = window_current_task_id(window)
        last_task_id = window.get("taskId")
        last_task = ledger["tasks"].get(last_task_id, {})
        explicitly_available = window.get("status") == "AVAILABLE_FOR_REUSE" and current_task_id is None
        legacy_available = (
            "assignmentHistory" not in window
            and window.get("status") == "REGISTERED"
            and last_task.get("status") == "DONE"
        )
        if (
            count == 1
            and terra_model_is_enforced(window)
            and bounded_permission_is_enforced(window)
            and last_task.get("status") == "DONE"
            and (explicitly_available or legacy_available)
        ):
            reusable.append(window_id)
    reusable.sort()
    requested_mode = review["windowReview"]["mode"]
    candidate = review["windowReview"]["candidateWindowId"]
    compatibility = review["windowReview"]["contextCompatibility"]
    if requested_mode == "AUTO":
        if len(current_matching) == 1:
            return {"status": "REUSE_EXISTING_WINDOW", "windowId": current_matching[0], "model": "gpt-5.6-terra", "assignmentNumber": 1, "maxAssignments": MAX_TASKS_PER_WINDOW, "reuseType": "CURRENT_ASSIGNMENT"}
        if len(current_matching) > 1:
            return {"status": "CONFLICT_DUPLICATE_ACTIVE_WINDOWS", "windowIds": current_matching, "model": "gpt-5.6-terra"}
        if reusable and compatibility == "UNKNOWN":
            return {"status": "WAITING_FOR_WINDOW_CONTEXT_COMPATIBILITY", "windowIds": reusable, "model": "gpt-5.6-terra"}
        if len(reusable) == 1 and compatibility == "COMPATIBLE":
            previous_task_id = ledger["windows"][reusable[0]].get("taskId")
            return {"status": "REUSE_EXISTING_WINDOW", "windowId": reusable[0], "model": "gpt-5.6-terra", "assignmentNumber": 2, "maxAssignments": MAX_TASKS_PER_WINDOW, "reuseType": "SECOND_AND_FINAL_ASSIGNMENT", "previousTaskId": previous_task_id, "reuseReservationId": f"window-slot:{review['reviewId']}"}
        if len(reusable) > 1 and compatibility == "COMPATIBLE":
            return {"status": "WAITING_FOR_CENTRAL_WINDOW_SELECTION", "windowIds": reusable, "model": "gpt-5.6-terra"}
        return {"status": "OPEN_NEW_WINDOW", "windowId": None, "model": "gpt-5.6-terra", "assignmentNumber": 1, "maxAssignments": MAX_TASKS_PER_WINDOW}
    if requested_mode == "NEW":
        if current_matching:
            return {"status": "CONFLICT_DUPLICATE_ACTIVE_WINDOW", "windowIds": current_matching, "model": "gpt-5.6-terra"}
        return {"status": "OPEN_NEW_WINDOW", "windowId": None, "model": "gpt-5.6-terra", "assignmentNumber": 1, "maxAssignments": MAX_TASKS_PER_WINDOW}
    if candidate is None:
        return {"status": "WAITING_FOR_REUSABLE_WINDOW", "windowId": None, "model": "gpt-5.6-terra"}
    window = ledger["windows"].get(candidate)
    if not isinstance(window, dict):
        return {"status": "WAITING_FOR_REUSABLE_WINDOW", "windowId": candidate, "model": "gpt-5.6-terra"}
    if candidate in current_matching:
        return {"status": "REUSE_EXISTING_WINDOW", "windowId": candidate, "model": "gpt-5.6-terra", "assignmentNumber": 1, "maxAssignments": MAX_TASKS_PER_WINDOW, "reuseType": "CURRENT_ASSIGNMENT"}
    if window_assignment_count(window) >= MAX_TASKS_PER_WINDOW or window.get("status") == "RETIRED":
        return {"status": "WAITING_WINDOW_REUSE_CAP_REACHED", "windowId": candidate, "model": "gpt-5.6-terra", "assignmentCount": window_assignment_count(window), "maxAssignments": MAX_TASKS_PER_WINDOW}
    if candidate not in reusable:
        return {"status": "WAITING_FOR_REUSABLE_WINDOW", "windowId": candidate, "model": "gpt-5.6-terra"}
    return {"status": "REUSE_EXISTING_WINDOW", "windowId": candidate, "model": "gpt-5.6-terra", "assignmentNumber": 2, "maxAssignments": MAX_TASKS_PER_WINDOW, "reuseType": "SECOND_AND_FINAL_ASSIGNMENT", "previousTaskId": window.get("taskId"), "reuseReservationId": f"window-slot:{review['reviewId']}"}


def claim_conflicts(candidate: Dict[str, Any], object_key: str, claim: Dict[str, Any]) -> bool:
    existing_conflict_key = claim.get("conflictKey", object_key)
    overlaps = (
        candidate["objectKey"] == object_key
        or candidate["conflictKey"] == existing_conflict_key
        or candidate["objectKey"] == existing_conflict_key
        or candidate["conflictKey"] == object_key
    )
    if not overlaps:
        return False
    return bool(candidate["exclusive"] or claim.get("exclusive", False) or candidate["intent"] == "WRITE" or claim.get("intent") == "WRITE")


def analyse(package: Dict[str, Any], ledger: Dict[str, Any], review: Dict[str, Any]) -> Dict[str, Any]:
    if review["bossReview"]["status"] != "APPROVED":
        return {"status": "WAITING_FOR_BOSS_APPROVAL", "windowDecision": None, "conflicts": []}
    if review["dependencyGate"]["status"] != "SATISFIED":
        return {"status": "WAITING_FOR_DEPENDENCIES", "windowDecision": None, "conflicts": []}
    if review["crossModuleGate"]["status"] == "UNKNOWN":
        return {"status": "WAITING_FOR_CROSS_MODULE_CLEARANCE", "windowDecision": None, "conflicts": []}
    if review["crossModuleGate"]["status"] == "CONFLICT":
        return {
            "status": "CONFLICT_HARD_STOP",
            "windowDecision": None,
            "conflicts": [{
                "type": "CROSS_MODULE_HANDOFF_CONFLICT",
                "reference": review["crossModuleGate"]["reference"],
            }],
        }
    if ledger["hardStops"]:
        return {
            "status": "CONFLICT_HARD_STOP",
            "windowDecision": None,
            "conflicts": [{"type": "EXISTING_LEDGER_HARD_STOP", "detail": item} for item in ledger["hardStops"]],
        }
    window_decision = resolve_window(package, ledger, review)
    if window_decision["status"].startswith("WAITING"):
        return {"status": window_decision["status"], "windowDecision": window_decision, "conflicts": []}
    if window_decision["status"].startswith("CONFLICT"):
        return {
            "status": "CONFLICT_HARD_STOP",
            "windowDecision": window_decision,
            "conflicts": [{"type": window_decision["status"], "windowIds": window_decision.get("windowIds", [])}],
        }

    conflicts: List[Dict[str, Any]] = []
    for candidate in review["occupancyRequests"]:
        for object_key, occupancy in ledger["objectOccupancies"].items():
            for claim in occupancy.get("claims", []):
                if claim.get("ownerType") == "task" and claim.get("ownerId") == review["taskId"]:
                    conflicts.append({
                        "type": "CANDIDATE_TASK_ALREADY_HAS_OCCUPANCY",
                        "requestedObjectKey": candidate["objectKey"],
                        "existingObjectKey": object_key,
                        "existingOwnerType": claim.get("ownerType"),
                        "existingOwnerId": claim.get("ownerId"),
                        "existingIntent": claim.get("intent"),
                    })
                    continue
                if claim_conflicts(candidate, object_key, claim):
                    conflicts.append({
                        "type": "OBJECT_OCCUPANCY_CONFLICT",
                        "requestedObjectKey": candidate["objectKey"],
                        "requestedConflictKey": candidate["conflictKey"],
                        "existingObjectKey": object_key,
                        "existingOwnerType": claim.get("ownerType"),
                        "existingOwnerId": claim.get("ownerId"),
                        "existingIntent": claim.get("intent"),
                    })
    return {
        "status": "CONFLICT_HARD_STOP" if conflicts else "ELIGIBLE_FOR_DISPATCH_APPROVAL",
        "windowDecision": window_decision,
        "conflicts": conflicts,
    }


def public_result(review: Dict[str, Any], analysis: Dict[str, Any], write_performed: bool) -> Dict[str, Any]:
    eligible = analysis["status"] == "ELIGIBLE_FOR_DISPATCH_APPROVAL"
    return {
        "status": analysis["status"],
        "reviewId": review["reviewId"],
        "packageId": review["packageId"],
        "taskId": review["taskId"],
        "windowDecision": analysis["windowDecision"],
        "conflictCount": len(analysis["conflicts"]),
        "occupancyReserved": bool(write_performed and eligible),
        "windowReuseReserved": bool(
            write_performed and eligible
            and isinstance(analysis.get("windowDecision"), dict)
            and analysis["windowDecision"].get("assignmentNumber") == 2
        ),
        "dispatchEligibility": "ELIGIBLE" if write_performed and eligible else "NOT_GRANTED",
        "dispatchExecuted": False,
        "taskWindowCreated": False,
        "subAgentCreated": False,
        "testCreationAllowed": False,
        "businessWriteAllowed": False,
        "writePerformed": write_performed,
        "message": "C05 只检查并预留治理对象；未派发窗口、创建 Agent、创建 TEST 或执行任何业务写入。",
    }


def build_decision(
    project_id: str,
    package: Dict[str, Any],
    review: Dict[str, Any],
    review_digest: str,
    analysis: Dict[str, Any],
    before_ledger: Dict[str, Any],
    after_ledger: Dict[str, Any],
    ledger_result: Dict[str, Any],
    claims: List[Dict[str, Any]],
) -> Dict[str, Any]:
    eligible = analysis["status"] == "ELIGIBLE_FOR_DISPATCH_APPROVAL"
    return {
        "schemaVersion": SCHEMA_VERSION,
        "recordType": "C05_OCCUPANCY_DECISION",
        "reviewId": review["reviewId"],
        "projectId": project_id,
        "packageId": review["packageId"],
        "taskId": review["taskId"],
        "createdAt": utc_now(),
        "writerId": CENTRAL_WRITER,
        "status": analysis["status"],
        "source": {
            "packageDigest": canonical_digest(package),
            "reviewDigest": review_digest,
            "beforeLedgerRevision": before_ledger["revision"],
            "beforeLedgerDigest": canonical_digest(before_ledger),
        },
        "bossApprovalRef": review["bossReview"]["reference"],
        "dependencyRefs": review["dependencyGate"]["references"],
        "crossModuleGate": review["crossModuleGate"],
        "windowDecision": analysis["windowDecision"],
        "requestedOccupancies": review["occupancyRequests"],
        "conflicts": analysis["conflicts"],
        "reservedClaimIds": [claim["claimId"] for claim in claims] if eligible else [],
        "ledgerMutation": {
            "receiptId": ledger_result["receiptId"],
            "afterRevision": after_ledger["revision"],
            "afterLedgerDigest": canonical_digest(after_ledger),
            "taskStatusAfter": after_ledger["tasks"][review["taskId"]]["status"],
        },
        "executionBoundary": {
            "occupancyReserved": eligible,
            "dispatchEligibility": "ELIGIBLE" if eligible else "NOT_GRANTED",
            "dispatchExecuted": False,
            "taskWindowCreated": False,
            "subAgentCreated": False,
            "testCreationAllowed": False,
            "businessWriteAllowed": False,
        },
        "receiptIds": [INITIAL_RECEIPT_ID],
        "latestReceiptId": INITIAL_RECEIPT_ID,
    }


def write_decision(data_root: Path, decision: Dict[str, Any]) -> None:
    project_id = decision["projectId"]
    review_id = decision["reviewId"]
    target = decision_directory(data_root, project_id, review_id)
    target.mkdir(parents=True, exist_ok=False)
    receipt_directory(data_root, project_id, review_id).mkdir(exist_ok=False)
    receipt = {
        "schemaVersion": SCHEMA_VERSION,
        "recordType": "C05_IMMUTABLE_OCCUPANCY_DECISION_RECEIPT",
        "receiptId": INITIAL_RECEIPT_ID,
        "createdAt": utc_now(),
        "projectId": project_id,
        "reviewId": review_id,
        "writerId": CENTRAL_WRITER,
        "operation": "C05_REVIEW_AND_RESERVE_OCCUPANCY",
        "beforeDecisionDigest": "NONE",
        "afterDecisionDigest": canonical_digest(decision),
        "afterDecision": decision,
    }
    write_json_exclusive(receipt_directory(data_root, project_id, review_id) / f"{INITIAL_RECEIPT_ID}.json", receipt)
    write_json_exclusive(decision_path(data_root, project_id, review_id), decision)


def load_decision(data_root: Path, project_id: str, review_id: str) -> Dict[str, Any]:
    path = decision_path(data_root, project_id, review_id)
    if not path.is_file():
        raise OccupancyError("C05_DECISION_NOT_FOUND")
    try:
        decision = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise OccupancyError("C05_DECISION_INVALID_JSON")
    if (
        not isinstance(decision, dict)
        or decision.get("schemaVersion") != SCHEMA_VERSION
        or decision.get("recordType") != "C05_OCCUPANCY_DECISION"
        or decision.get("projectId") != project_id
        or decision.get("reviewId") != review_id
        or decision.get("writerId") != CENTRAL_WRITER
    ):
        raise OccupancyError("C05_DECISION_SCHEMA_UNSUPPORTED")
    return decision


def verify_decision_data(data_root: Path, project_id: str, review_id: str) -> Dict[str, Any]:
    decision = load_decision(data_root, project_id, review_id)
    receipt_path = receipt_directory(data_root, project_id, review_id) / f"{INITIAL_RECEIPT_ID}.json"
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise OccupancyError("C05_IMMUTABLE_RECEIPT_MISSING_OR_INVALID")
    ledger_mutation = decision.get("ledgerMutation", {})
    ledger_receipt_id = ledger_mutation.get("receiptId")
    try:
        ledger_receipt = json.loads(
            (ledger_receipt_directory(data_root, project_id) / f"{ledger_receipt_id}.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError, TypeError):
        raise OccupancyError("C05_LINKED_LEDGER_RECEIPT_MISSING_OR_INVALID")
    boundary = decision.get("executionBoundary", {})
    expected_task_status = "READY" if decision.get("status") == "ELIGIBLE_FOR_DISPATCH_APPROVAL" else "BLOCKED"
    expected_operation = "C05_RESERVE_TASK_OCCUPANCIES" if expected_task_status == "READY" else "C05_BLOCK_CONFLICTING_TASK"
    linked_snapshot = ledger_receipt.get("afterLedger", {})
    window_decision = decision.get("windowDecision") or {}
    reuse_reservation_valid = True
    if expected_task_status == "READY" and window_decision.get("assignmentNumber") == 2:
        linked_window = linked_snapshot.get("windows", {}).get(window_decision.get("windowId"), {})
        reuse_reservation_valid = (
            linked_window.get("status") == "RESERVED_FOR_REUSE"
            and linked_window.get("reservedForTaskId") == decision.get("taskId")
            and linked_window.get("reuseReservationId") == window_decision.get("reuseReservationId")
            and window_assignment_count(linked_window) == 1
        )
    if (
        receipt.get("recordType") != "C05_IMMUTABLE_OCCUPANCY_DECISION_RECEIPT"
        or receipt.get("receiptId") != INITIAL_RECEIPT_ID
        or receipt.get("beforeDecisionDigest") != "NONE"
        or receipt.get("afterDecisionDigest") != canonical_digest(receipt.get("afterDecision", {}))
        or receipt.get("afterDecision") != decision
        or canonical_digest(decision) != receipt.get("afterDecisionDigest")
        or ledger_receipt.get("recordType") != "C03_IMMUTABLE_LEDGER_RECEIPT"
        or ledger_receipt.get("receiptId") != ledger_receipt_id
        or ledger_receipt.get("operation") != expected_operation
        or ledger_receipt.get("afterLedgerDigest") != ledger_mutation.get("afterLedgerDigest")
        or ledger_receipt.get("afterRevision") != ledger_mutation.get("afterRevision")
        or not isinstance(linked_snapshot, dict)
        or canonical_digest(linked_snapshot) != ledger_mutation.get("afterLedgerDigest")
        or linked_snapshot.get("tasks", {}).get(decision.get("taskId"), {}).get("status") != expected_task_status
        or not reuse_reservation_valid
        or ledger_mutation.get("taskStatusAfter") != expected_task_status
        or any(boundary.get(key) is not False for key in (
            "dispatchExecuted", "taskWindowCreated", "subAgentCreated", "testCreationAllowed", "businessWriteAllowed"
        ))
    ):
        raise OccupancyError("C05_IMMUTABLE_RECEIPT_CHAIN_INVALID")
    return decision


def evaluate(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args)
    project_id = require_reference(args.project_id, "PROJECT_ID_INVALID")
    package_id = require_reference(args.package_id, "TASK_PACKAGE_ID_INVALID")
    if not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise OccupancyError("PROJECT_ID_INVALID")
    if not PROJECT_ID_PATTERN.fullmatch(package_id):
        raise OccupancyError("TASK_PACKAGE_ID_INVALID")
    review, review_digest = load_private_review(args.review, data_root, project_id, package_id)
    if args.apply:
        require_writer(args.writer_id)
        if decision_path(data_root, project_id, review["reviewId"]).exists():
            decision = verify_decision_data(data_root, project_id, review["reviewId"])
            if (
                decision["packageId"] != package_id
                or decision["taskId"] != review["taskId"]
                or decision["source"]["reviewDigest"] != review_digest
            ):
                raise OccupancyError("C05_REVIEW_ID_REUSE_WITH_DIFFERENT_INPUT")
            result = public_result(
                review,
                {"status": decision["status"], "windowDecision": decision["windowDecision"], "conflicts": decision["conflicts"]},
                False,
            )
            result["status"] = "IDEMPOTENT_EXISTING_C05_DECISION"
            result["decisionStatus"] = decision["status"]
            return result, 0
    package, ledger = verified_sources(data_root, project_id, package_id, review)
    analysis = analyse(package, ledger, review)
    if args.dry_run:
        result = public_result(review, analysis, False)
        result["conflicts"] = analysis["conflicts"]
        return result, 0

    writer_id = require_writer(args.writer_id)
    if review["bossReview"]["status"] != "APPROVED" or review["dependencyGate"]["status"] != "SATISFIED":
        raise OccupancyError("C05_REVIEW_NOT_READY_FOR_APPLY")
    if analysis["status"].startswith("WAITING"):
        raise OccupancyError(f"C05_GATE_NOT_READY:{analysis['status']}")
    if any(item.get("type") == "EXISTING_LEDGER_HARD_STOP" for item in analysis["conflicts"]):
        raise OccupancyError("C05_SOURCE_LEDGER_HAS_UNRESOLVED_HARD_STOP")

    with review_lock(data_root, project_id):
        if decision_path(data_root, project_id, review["reviewId"]).exists():
            decision = verify_decision_data(data_root, project_id, review["reviewId"])
            result = public_result(review, {"status": decision["status"], "windowDecision": decision["windowDecision"], "conflicts": decision["conflicts"]}, False)
            result["status"] = "IDEMPOTENT_EXISTING_C05_DECISION"
            result["decisionStatus"] = decision["status"]
            return result, 0
        with ledger_lock(data_root, project_id):
            package, before_ledger = verified_sources(data_root, project_id, package_id, review)
            analysis = analyse(package, before_ledger, review)
            if analysis["status"].startswith("WAITING"):
                raise OccupancyError(f"C05_GATE_NOT_READY:{analysis['status']}")
            if any(item.get("type") == "EXISTING_LEDGER_HARD_STOP" for item in analysis["conflicts"]):
                raise OccupancyError("C05_SOURCE_LEDGER_HAS_UNRESOLVED_HARD_STOP")

            new_claims: List[Dict[str, Any]] = []
            if analysis["status"] == "ELIGIBLE_FOR_DISPATCH_APPROVAL":
                for request in review["occupancyRequests"]:
                    new_claims.append({
                        "claimId": f"claim-{uuid.uuid4().hex[:12]}",
                        "ownerType": "task",
                        "ownerId": review["taskId"],
                        "intent": request["intent"],
                        "conflictKey": request["conflictKey"],
                        "resourceClass": request["resourceClass"],
                        "exclusive": request["exclusive"],
                        "c05ReviewId": review["reviewId"],
                        "registeredAt": utc_now(),
                    })

            def mutate(after: Dict[str, Any]) -> None:
                task = after["tasks"][review["taskId"]]
                if task["status"] != "PLANNED":
                    raise OccupancyError("C05_REQUIRES_PLANNED_TASK")
                if analysis["status"] == "CONFLICT_HARD_STOP":
                    task["status"] = "BLOCKED"
                    task["history"].append({
                        "at": utc_now(), "event": "C05_OCCUPANCY_CONFLICT", "reviewId": review["reviewId"],
                        "conflictCount": len(analysis["conflicts"]), "to": "BLOCKED", "by": CENTRAL_WRITER,
                    })
                    after["hardStops"].append({
                        "code": "C05_OCCUPANCY_CONFLICT",
                        "reviewId": review["reviewId"],
                        "taskId": review["taskId"],
                        "conflictCount": len(analysis["conflicts"]),
                        "detectedAt": utc_now(),
                    })
                    return
                for request, claim in zip(review["occupancyRequests"], new_claims):
                    occupancy = after["objectOccupancies"].setdefault(
                        request["objectKey"], {"objectKey": request["objectKey"], "status": "CLAIMED", "claims": []}
                    )
                    occupancy["claims"].append(claim)
                window_decision = analysis.get("windowDecision") or {}
                if window_decision.get("assignmentNumber") == 2:
                    window = after["windows"].get(window_decision.get("windowId"))
                    if not isinstance(window, dict):
                        raise OccupancyError("C05_REUSABLE_WINDOW_DISAPPEARED")
                    explicit_available = window.get("status") == "AVAILABLE_FOR_REUSE" and window_current_task_id(window) is None
                    legacy_available = "assignmentHistory" not in window and window.get("status") == "REGISTERED" and after["tasks"].get(window.get("taskId"), {}).get("status") == "DONE"
                    if window_assignment_count(window) != 1 or not (explicit_available or legacy_available) or not terra_model_is_enforced(window) or not bounded_permission_is_enforced(window):
                        raise OccupancyError("C05_WINDOW_REUSE_SLOT_NO_LONGER_AVAILABLE")
                    window["status"] = "RESERVED_FOR_REUSE"
                    window["currentTaskId"] = None
                    window["reservedForTaskId"] = review["taskId"]
                    window["reuseReservationId"] = window_decision["reuseReservationId"]
                    window["reuseReservedAt"] = utc_now()
                task["status"] = "READY"
                task["history"].append({
                    "at": utc_now(), "event": "C05_OCCUPANCY_RESERVED", "reviewId": review["reviewId"],
                    "claimCount": len(new_claims), "to": "READY", "by": CENTRAL_WRITER,
                })

            operation = "C05_RESERVE_TASK_OCCUPANCIES" if analysis["status"] == "ELIGIBLE_FOR_DISPATCH_APPROVAL" else "C05_BLOCK_CONFLICTING_TASK"
            ledger_result = commit_mutation(
                data_root, project_id, before_ledger, writer_id, operation,
                {"reviewId": review["reviewId"], "taskId": review["taskId"], "status": analysis["status"], "conflictCount": len(analysis["conflicts"])},
                mutate,
            )
            after_ledger = load_ledger(data_root, project_id)
        decision = build_decision(
            project_id, package, review, review_digest, analysis, before_ledger, after_ledger, ledger_result, new_claims
        )
        write_decision(data_root, decision)
    result = public_result(review, analysis, True)
    result["decisionReceiptId"] = INITIAL_RECEIPT_ID
    result["ledgerReceiptId"] = ledger_result["receiptId"]
    result["taskStatusAfter"] = after_ledger["tasks"][review["taskId"]]["status"]
    return result, 0


def verify_decision(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args)
    project_id = require_reference(args.project_id, "PROJECT_ID_INVALID")
    review_id = require_reference(args.review_id, "C05_REVIEW_ID_INVALID")
    decision = verify_decision_data(data_root, project_id, review_id)
    return {
        "status": "C05_DECISION_INTEGRITY_VERIFIED",
        "projectId": project_id,
        "reviewId": review_id,
        "decisionStatus": decision["status"],
        "taskId": decision["taskId"],
        "dispatchExecuted": False,
        "taskWindowCreated": False,
        "businessWriteAllowed": False,
        "writePerformed": False,
    }, 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="C05 atomic occupancy and conflict checker")
    root_source = parser.add_mutually_exclusive_group(required=True)
    root_source.add_argument("--data-root", help="Private data directory outside every Git worktree")
    root_source.add_argument("--config", help="Private module config JSON containing storage.userDataRoot")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--writer-id", help=f"The only accepted writer is {CENTRAL_WRITER}")
    commands = parser.add_subparsers(dest="command", required=True)

    evaluate_command = commands.add_parser("evaluate")
    evaluate_command.add_argument("--package-id", required=True)
    evaluate_command.add_argument("--review", required=True)
    action = evaluate_command.add_mutually_exclusive_group(required=True)
    action.add_argument("--dry-run", action="store_true")
    action.add_argument("--apply", action="store_true")

    verify_command = commands.add_parser("verify")
    verify_command.add_argument("--review-id", required=True)
    return parser.parse_args()


def dispatch(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    return {"evaluate": evaluate, "verify": verify_decision}[args.command](args)


def main() -> int:
    args = parse_args()
    try:
        result, exit_code = dispatch(args)
        print_result(result)
        return exit_code
    except (OccupancyError, TaskPackageError, LedgerError, C02Error) as error:
        print_result({
            "status": "REFUSED",
            "reason": str(error),
            "dispatchExecuted": False,
            "taskWindowCreated": False,
            "subAgentCreated": False,
            "testCreationAllowed": False,
            "businessWriteAllowed": False,
            "writePerformed": False,
            "message": "C05 已停止；未派发窗口、创建 Agent、创建 TEST 或修改业务对象。",
        })
        return 2


if __name__ == "__main__":
    sys.exit(main())
