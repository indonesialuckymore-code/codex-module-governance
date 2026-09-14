#!/usr/bin/env python3
"""C03 Codex-module engineering ledger. Private data only; no business execution."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from initialize_project import C02Error, PROJECT_ID_PATTERN, REGISTRY_DIRECTORY, load_data_root
from outline_outcome_contract import OutlineContractError, validate_handoff


SCHEMA_VERSION = "0.3.0"
MODULE = "codex"
CENTRAL_WRITER = "codex-module-central"
MAX_TASKS_PER_WINDOW = 2
from runtime_model_policy import DEFAULT_TASK_MODEL, FULL_ACCESS_PROFILES, MANUAL_MODEL_METHOD, valid_model

TASK_RUNTIME_MODEL = DEFAULT_TASK_MODEL
MANUAL_WINDOW_MODEL_METHOD = "MANUAL_UI_TERRA_SELECTION_EVIDENCE"
WORKTREE_SCOPED_PERMISSION_PROFILES = {":workspace"}
FULL_ACCESS_PERMISSION_PROFILES = FULL_ACCESS_PROFILES
ALLOWED_TASK_PERMISSION_PROFILES = WORKTREE_SCOPED_PERMISSION_PROFILES | FULL_ACCESS_PERMISSION_PROFILES
MANUAL_WINDOW_PERMISSION_METHOD = "MANUAL_UI_PERMISSION_EVIDENCE"
LEDGERS_DIRECTORY = Path("module-ledgers")
LEDGER_FILENAME = "ledger.json"
RECEIPTS_DIRECTORY = "receipts"
ROLE_CONTINUITY_DIRECTORY = Path("role-continuity")
ROLE_ROUTING_FILENAME = "routing.json"
TASK_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{2,127}$")
REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
TASK_TRANSITIONS = {
    "PLANNED": {"READY", "BLOCKED", "CANCELLED"},
    "READY": {"IN_PROGRESS", "BLOCKED", "CANCELLED"},
    "IN_PROGRESS": {"NEEDS_REVIEW", "PARTIAL", "CONFLICT", "BLOCKED", "CANCEL_REQUESTED"},
    "NEEDS_REVIEW": {"IN_PROGRESS", "PARTIAL", "CONFLICT", "CANCEL_REQUESTED"},
    "PARTIAL": {"IN_PROGRESS", "BLOCKED", "CONFLICT", "CANCEL_REQUESTED"},
    "CONFLICT": {"BLOCKED", "CANCEL_REQUESTED"},
    "BLOCKED": {"READY", "CANCELLED"},
    "CANCEL_REQUESTED": {"CANCELLED"},
    "CANCELLED": set(),
    "DONE": set(),
}


class LedgerError(Exception):
    """A safe refusal. The caller must not treat it as a completed operation."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def print_result(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def canonical_digest(payload: Dict[str, Any]) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def require_pattern(value: str, pattern: re.Pattern, error: str) -> str:
    candidate = value.strip()
    if not pattern.fullmatch(candidate):
        raise LedgerError(error)
    return candidate


def require_opaque_reference(value: str, error: str) -> str:
    return require_pattern(value, REFERENCE_PATTERN, error)


def require_text(value: str, error: str, maximum: int = 1000) -> str:
    candidate = value.strip()
    if not candidate or len(candidate) > maximum:
        raise LedgerError(error)
    return candidate


def task_titles(task_id: str, raw_title: str) -> Tuple[str, str]:
    """Return the business title and the immutable cross-system title."""
    title = raw_title.strip()
    prefix = f"{task_id}｜"
    if title.startswith(prefix):
        title = title[len(prefix):].strip()
    title = re.sub(r"｜G[1-9][0-9]*$", "", title).strip()
    if not title:
        raise LedgerError("TASK_TITLE_INVALID")
    return title, f"{task_id}｜{title}"


def window_assignments(window: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return assignment history, including a safe view of pre-0.16 windows."""
    history = window.get("assignmentHistory")
    if isinstance(history, list) and history:
        return history
    task_id = window.get("taskId")
    if not isinstance(task_id, str):
        return []
    return [{
        "assignmentNumber": 1,
        "taskId": task_id,
        "canonicalTitle": window.get("canonicalTitle"),
        "runtimeTitle": window.get("runtimeTitle"),
        "generation": window.get("generation", 1),
        "contextMode": window.get("contextMode", "NEW"),
        "dispatchId": window.get("dispatchId"),
        "assignedAt": window.get("registeredAt"),
        "completedAt": None,
        "status": "ACTIVE",
    }]


def window_assignment_count(window: Dict[str, Any]) -> int:
    count = window.get("assignmentCount")
    declared = count if isinstance(count, int) and count >= 0 else 0
    return max(declared, len(window_assignments(window)))


def window_current_task_id(window: Dict[str, Any]) -> Optional[str]:
    if "currentTaskId" in window:
        value = window.get("currentTaskId")
        return value if isinstance(value, str) else None
    return window.get("taskId") if window.get("status") == "REGISTERED" else None


def require_object_key(value: str) -> str:
    candidate = value.strip()
    if (
        not 3 <= len(candidate) <= 256
        or candidate.startswith(("/", "~"))
        or ".." in candidate
        or "://" in candidate
        or "\\" in candidate
        or not re.fullmatch(r"[A-Za-z0-9._:/@#=-]+", candidate)
        or ":" not in candidate
    ):
        raise LedgerError("OBJECT_KEY_INVALID_OR_NOT_OPAQUE")
    return candidate


def ledger_directory(data_root: Path, project_id: str) -> Path:
    return data_root / LEDGERS_DIRECTORY / project_id


def ledger_path(data_root: Path, project_id: str) -> Path:
    return ledger_directory(data_root, project_id) / LEDGER_FILENAME


def receipt_directory(data_root: Path, project_id: str) -> Path:
    return ledger_directory(data_root, project_id) / RECEIPTS_DIRECTORY


def project_card_path(data_root: Path, project_id: str) -> Path:
    return data_root / REGISTRY_DIRECTORY / "project-cards" / project_id / "project-card.json"


def load_project_card(data_root: Path, project_id: str) -> Dict[str, Any]:
    card_path = project_card_path(data_root, project_id)
    if not card_path.is_file():
        raise LedgerError("C02_PROJECT_STARTUP_CARD_REQUIRED")
    try:
        card = json.loads(card_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise LedgerError("C02_PROJECT_STARTUP_CARD_INVALID")
    if (
        card.get("recordType") != "C02_PROJECT_STARTUP_CARD"
        or card.get("state") != "PROJECT_REGISTERED"
        or card.get("project", {}).get("id") != project_id
        or card.get("executionBoundary", {}).get("dispatchAllowed") is not False
        or card.get("executionBoundary", {}).get("testCreationAllowed") is not False
        or card.get("executionBoundary", {}).get("businessWriteAllowed") is not False
    ):
        raise LedgerError("C02_PROJECT_STARTUP_CARD_SCHEMA_UNSUPPORTED")
    return card


def write_json_exclusive(path: Path, payload: Dict[str, Any]) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        with path.open("x", encoding="utf-8") as output:
            output.write(serialized)
    except FileExistsError:
        raise LedgerError("IMMUTABLE_TARGET_ALREADY_EXISTS")


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
def ledger_lock(data_root: Path, project_id: str) -> Iterator[None]:
    lock_root = data_root / LEDGERS_DIRECTORY / ".locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    lock_path = lock_root / f"{project_id}.lock"
    from process_lock import process_lock
    with process_lock(lock_path, lambda: write_json_exclusive(lock_path, {
            "recordType": "C03_LEDGER_OPERATION_LOCK",
            "projectId": project_id,
            "createdAt": utc_now(),
            "pid": os.getpid(),
        }), LedgerError, "LEDGER_OPERATION_LOCK_PRESENT"):
        yield


def new_ledger(project_id: str, timestamp: str) -> Dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "recordType": "C03_CODEX_MODULE_ENGINEERING_LEDGER",
        "ledgerId": f"codex-module:{project_id}",
        "projectId": project_id,
        "module": MODULE,
        "createdAt": timestamp,
        "revision": 0,
        "status": "ACTIVE",
        "writerPolicy": {
            "soleWriter": CENTRAL_WRITER,
            "deniedDirectWriters": ["fable-5-system-router", "claude-module-central", "task-window", "sub-agent"],
        },
        "partitions": {
            "tasks": {"writer": CENTRAL_WRITER, "readers": ["boss", "fable-5-system-router", CENTRAL_WRITER]},
            "execution": {"writer": CENTRAL_WRITER, "readers": ["boss", CENTRAL_WRITER]},
            "occupancy": {"writer": CENTRAL_WRITER, "readers": ["boss", CENTRAL_WRITER]},
            "evidenceRefs": {"writer": CENTRAL_WRITER, "readers": ["boss", CENTRAL_WRITER]},
            "governanceRefs": {"writer": CENTRAL_WRITER, "readers": ["boss", "fable-5-system-router", CENTRAL_WRITER]},
        },
        "tasks": {},
        "windows": {},
        "subAgents": {},
        "objectOccupancies": {},
        "evidenceRefs": {},
        "adjudications": {},
        "skills": {},
        "recovery": {"state": "NORMAL", "recoveryEpoch": 0},
        "hardStops": [],
        "resolvedHardStops": [],
        "releasedOccupancies": [],
        "receiptIds": [],
        "latestReceiptId": None,
    }


def load_ledger(data_root: Path, project_id: str) -> Dict[str, Any]:
    path = ledger_path(data_root, project_id)
    if not path.is_file():
        raise LedgerError("C03_LEDGER_NOT_INITIALIZED")
    try:
        ledger = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise LedgerError("C03_LEDGER_INVALID_JSON")
    required = {"schemaVersion", "recordType", "ledgerId", "projectId", "module", "revision", "tasks", "windows", "subAgents", "objectOccupancies", "evidenceRefs", "adjudications", "skills", "recovery", "hardStops", "receiptIds", "latestReceiptId"}
    if (
        not isinstance(ledger, dict)
        or not required.issubset(ledger)
        or ledger.get("schemaVersion") != SCHEMA_VERSION
        or ledger.get("recordType") != "C03_CODEX_MODULE_ENGINEERING_LEDGER"
        or ledger.get("projectId") != project_id
        or ledger.get("module") != MODULE
        or ledger.get("writerPolicy", {}).get("soleWriter") != CENTRAL_WRITER
        or not isinstance(ledger.get("receiptIds"), list)
    ):
        raise LedgerError("C03_LEDGER_SCHEMA_UNSUPPORTED")
    return ledger


def require_writer(writer_id: Optional[str]) -> str:
    if writer_id != CENTRAL_WRITER:
        raise LedgerError("LEDGER_WRITER_NOT_AUTHORIZED")
    return writer_id


def require_current_central_thread(
    data_root: Path,
    project_id: str,
    caller_thread_ref: Optional[str],
) -> Optional[str]:
    """Bind every post-C08 ledger write to the one active central task.

    Before C08 routing exists, C02/C03 bootstrap remains backwards compatible.
    Once routing is active, the generic writer name is no longer sufficient:
    the native Codex task id must match CURRENT_CENTRAL.activeThreadRef.
    """
    path = data_root / ROLE_CONTINUITY_DIRECTORY / project_id / ROLE_ROUTING_FILENAME
    if not path.is_file():
        return None
    try:
        routing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise LedgerError("C08_CURRENT_CENTRAL_ROUTING_INVALID")
    central = routing.get("roles", {}).get("CURRENT_CENTRAL") if isinstance(routing, dict) else None
    active_thread_ref = central.get("activeThreadRef") if isinstance(central, dict) else None
    if (
        routing.get("recordType") != "C08_ROLE_ROUTING"
        or routing.get("projectId") != project_id
        or not isinstance(central, dict)
        or central.get("status") != "ACTIVE"
        or not isinstance(active_thread_ref, str)
        or not active_thread_ref.strip()
    ):
        raise LedgerError("C08_CURRENT_CENTRAL_ROUTING_INVALID")
    if not isinstance(caller_thread_ref, str) or not caller_thread_ref.strip():
        raise LedgerError("LEDGER_CALLER_THREAD_REQUIRED")
    caller = caller_thread_ref.strip()
    if caller != active_thread_ref:
        raise LedgerError("LEDGER_CALLER_NOT_CURRENT_CENTRAL")
    return caller


def receipt_id_for(ledger: Dict[str, Any]) -> str:
    return f"receipt-{ledger['revision'] + 1:06d}-{uuid.uuid4().hex[:12]}"


def commit_mutation(
    data_root: Path,
    project_id: str,
    ledger: Dict[str, Any],
    writer_id: str,
    operation: str,
    change_summary: Dict[str, Any],
    mutate,
    caller_thread_ref: Optional[str] = None,
) -> Dict[str, Any]:
    caller = require_current_central_thread(data_root, project_id, caller_thread_ref)
    recovery_state = ledger.get("recovery", {}).get("state", "UNKNOWN")
    if recovery_state not in {"NORMAL", "CLOSED"} and not operation.startswith("C08_"):
        raise LedgerError("LEDGER_RECOVERY_FREEZE_ACTIVE")
    before = copy.deepcopy(ledger)
    after = copy.deepcopy(ledger)
    if operation.startswith("C06_") and change_summary.get("taskId"):
        require_direction_ready(before, change_summary["taskId"])
    mutate(after)
    for task_id, task in before["tasks"].items():
        if (direction_hold(task) or active_external_waits(task) or any(item.get('status') == 'RECONCILIATION_REQUIRED'
                for item in task.get('outlineImpacts', []))) and after["tasks"][task_id]["status"] != task["status"]:
            if after["tasks"][task_id]["status"] in {"READY", "IN_PROGRESS", "DONE"}:
                require_direction_ready(before, task_id)
    for key, agent in after["subAgents"].items():
        if key not in before["subAgents"]:
            window = after["windows"].get(agent["windowId"], {})
            task_id = window_current_task_id(window)
            if task_id: require_direction_ready(before, task_id)
    receipt_id = receipt_id_for(before)
    after["revision"] = before["revision"] + 1
    after["receiptIds"] = [*before["receiptIds"], receipt_id]
    after["latestReceiptId"] = receipt_id
    after_digest = canonical_digest(after)
    receipt = {
        "schemaVersion": SCHEMA_VERSION,
        "recordType": "C03_IMMUTABLE_LEDGER_RECEIPT",
        "receiptId": receipt_id,
        "createdAt": utc_now(),
        "projectId": project_id,
        "module": MODULE,
        "writerId": writer_id,
        "callerThreadRef": caller,
        "operation": operation,
        "beforeRevision": before["revision"],
        "afterRevision": after["revision"],
        "beforeLedgerDigest": canonical_digest(before),
        "afterLedgerDigest": after_digest,
        "changeSummary": change_summary,
        "afterLedger": after,
    }
    receipt_path = receipt_directory(data_root, project_id) / f"{receipt_id}.json"
    write_json_exclusive(receipt_path, receipt)
    replace_json_atomically(ledger_path(data_root, project_id), after)
    return {
        "status": "RECORDED",
        "operation": operation,
        "receiptId": receipt_id,
        "revision": after["revision"],
        "hardStops": after["hardStops"],
        "writePerformed": True,
    }


def execution_arrangement_waits(ledger: Dict[str, Any], task_id: str) -> List[str]:
    """Read current plan constraints from C03; old projects have no added gate."""
    unmet = set()
    for record in ledger.get("executionPlanRevisions", {}).values():
        version = next((item for item in record["versions"] if item["mapDigest"] == record["currentDigest"]), None)
        if version is None or canonical_digest(version["map"]) != record["currentDigest"]:
            raise LedgerError("EXECUTION_ARRANGEMENT_INTEGRITY_INVALID")
        for item in version["map"]["executionPlan"]["tasks"]:
            if item["taskId"] == task_id:
                for dependency in item["dependencies"] + item.get("schedulingDependencies", []):
                    if ledger["tasks"].get(dependency, {}).get("status") != "DONE":
                        unmet.add(dependency)
    return sorted(unmet)


def ensure_task(ledger: Dict[str, Any], task_id: str) -> Dict[str, Any]:
    task = ledger["tasks"].get(task_id)
    if not isinstance(task, dict):
        raise LedgerError("TASK_NOT_FOUND")
    return task


def correction_snapshot(task: Dict[str, Any], project_id: str) -> Dict[str, Any]:
    history = task.get("directionCorrections", [])
    current = history[-1] if history else None
    requests = [{"requestSchemaVersion": "0.19.0", "recordType": "C14_CENTRAL_TO_TASK_REQUEST",
        "messageId": "direction-" + canonical_digest({"projectId": project_id, "taskId": task["taskId"],
            "requestDigest": current["requestDigest"], "window": window})[:40],
        "projectId": project_id, "taskIdentity": {"taskId": task["taskId"], "canonicalTitle": task["canonicalTitle"]},
        "windowId": window["windowId"], "commandId": current["correctionId"], "commandType": "DIRECTION_CORRECTION",
        "commandRef": current["instructionRef"], "commandDigest": current["requestDigest"],
        "summary": "Read the current task correction; pause affected execution and report synchronization evidence."}
        for window in current["windowBindings"]] if current else []
    return {"taskId": task["taskId"], "taskStatus": task["status"], "messageRequests": requests,
            "correctionRevision": len(history), "current": current,
            "synchronizationState": ("PAUSE_REQUESTED" if direction_hold(task) else
                ("VERIFIED_WAITING_EXTERNAL" if active_external_waits(task) else "VERIFIED_FOR_RESUMPTION")) if current else "NOT_REQUIRED",
            "resumptionEligible": not direction_hold(task) and not active_external_waits(task)
                and task["status"] not in {"DONE", "CANCELLED", "CANCEL_REQUESTED"},
            "governanceHold": direction_hold(task), "runtimeInterrupted": False,
            "writePerformed": False}


def direction_hold(task: Dict[str, Any]) -> bool:
    corrections = task.get("directionCorrections", [])
    if not corrections: return False
    current = corrections[-1]
    return not any(item.get("revision") == current["revision"] and item.get("correctionDigest") == current["requestDigest"]
        for item in task.get("directionVerifications", []))


def require_direction_ready(ledger: Dict[str, Any], task_id: str) -> None:
    if any(item.get("status") == "RECONCILIATION_REQUIRED" for item in ensure_task(ledger, task_id).get("outlineImpacts", [])):
        raise LedgerError("TASK_OUTLINE_RECONCILIATION_REQUIRED")
    if direction_hold(ensure_task(ledger, task_id)):
        raise LedgerError("TASK_DIRECTION_CORRECTION_PENDING")
    if active_external_waits(ensure_task(ledger, task_id)):
        raise LedgerError("TASK_EXTERNAL_WAIT_PENDING")


def active_external_waits(task: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [item for item in task.get("externalWaits", {}).values() if item["status"] == "WAITING"]


def feedback_repair_task(entry: Dict[str, Any]) -> Optional[str]:
    if entry.get('repairRetargets'):
        return entry['repairRetargets'][-1]['repairTaskId']
    return entry.get("repairLink", {}).get("repairTaskId") or entry.get("repairTaskId")


def feedback_binding(entry: Dict[str, Any]) -> str:
    # Status/closure evolve; the issue, original acceptance and repair link do not.
    return canonical_digest({key: value for key, value in entry.items() if key not in {"status", "closure"}})


def link_feedback_repair(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    root = load_data_root(args)
    project_id = require_pattern(args.project_id, PROJECT_ID_PATTERN, "PROJECT_ID_INVALID")
    task_id = require_pattern(args.task_id, TASK_ID_PATTERN, "TASK_ID_INVALID")
    repair_id = require_pattern(args.repair_task_id, TASK_ID_PATTERN, "TASK_ID_INVALID")
    feedback_id = require_opaque_reference(args.feedback_id, "DELIVERY_FEEDBACK_REFERENCE_INVALID")
    writer = require_writer(args.writer_id)
    with ledger_lock(root, project_id):
        require_current_central_thread(root, project_id, args.caller_thread_ref)
        _, code = verify_ledger(args)
        if code: raise LedgerError("DELIVERY_FEEDBACK_LEDGER_UNVERIFIED")
        before = load_ledger(root, project_id)
        entry = ensure_task(before, task_id).get("deliveryFeedback", {}).get(feedback_id)
        if not entry: raise LedgerError("DELIVERY_FEEDBACK_NOT_FOUND")
        existing = feedback_repair_task(entry)
        if existing:
            if existing != repair_id: raise LedgerError("DELIVERY_FEEDBACK_REPAIR_ALREADY_LINKED")
            return {"status": "IDEMPOTENT_FEEDBACK_REPAIR_LINK", "writePerformed": False}, 0
        repair = ensure_task(before, repair_id)
        if repair_id == task_id or repair["status"] in {"DONE", "CANCELLED", "CANCEL_REQUESTED"}:
            raise LedgerError("DELIVERY_FEEDBACK_ACTIVE_DISTINCT_REPAIR_REQUIRED")
        def mutate(after):
            after["tasks"][task_id]["deliveryFeedback"][feedback_id]["repairLink"] = {
                "repairTaskId": repair_id, "linkedAt": utc_now()}
        return commit_mutation(root, project_id, before, writer, "LINK_DELIVERY_FEEDBACK_REPAIR",
            {"taskId": task_id, "feedbackId": feedback_id, "repairTaskId": repair_id}, mutate, args.caller_thread_ref), 0


def close_feedback(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    import independent_handover_validator as validator
    root = load_data_root(args)
    project_id = require_pattern(args.project_id, PROJECT_ID_PATTERN, "PROJECT_ID_INVALID")
    task_id = require_pattern(args.task_id, TASK_ID_PATTERN, "TASK_ID_INVALID")
    feedback_id = require_opaque_reference(args.feedback_id, "DELIVERY_FEEDBACK_REFERENCE_INVALID")
    validation_id = require_opaque_reference(args.validation_id, "DELIVERY_FEEDBACK_REFERENCE_INVALID")
    writer = require_writer(args.writer_id)
    with ledger_lock(root, project_id):
        require_current_central_thread(root, project_id, args.caller_thread_ref)
        _, code = verify_ledger(args)
        if code: raise LedgerError("DELIVERY_FEEDBACK_LEDGER_UNVERIFIED")
        before = load_ledger(root, project_id)
        entry = ensure_task(before, task_id).get("deliveryFeedback", {}).get(feedback_id)
        if not entry: raise LedgerError("DELIVERY_FEEDBACK_NOT_FOUND")
        try:
            decision = validator.verify_decision_data(root, project_id, validation_id)
            finalization = validator.verify_finalization_data(root, project_id, validation_id)
            validator.verify_feedback_assessments(root, project_id, before, decision["taskId"], decision)
        except (validator.HandoverValidationError, validator.LedgerError) as error:
            raise LedgerError("DELIVERY_FEEDBACK_REPAIR_ACCEPTANCE_UNVERIFIED") from error
        repair_id = feedback_repair_task(entry)
        matches = [item for item in decision.get("feedbackAssessments", [])
                   if item["originalTaskId"] == task_id and item["feedbackId"] == feedback_id
                   and item["feedbackDigest"] == feedback_binding(entry)
                   and item["issueResolved"] is True and item["independentlyReadBack"] is True]
        if (not repair_id or decision["taskId"] != repair_id or repair_id == task_id or len(matches) != 1
                or decision["outcome"] != "PASS_PENDING_BOSS_APPROVAL" or finalization["bossDecision"] != "APPROVED"
                or ensure_task(before, repair_id)["status"] != "DONE"):
            raise LedgerError("DELIVERY_FEEDBACK_ISSUE_SPECIFIC_ACCEPTANCE_REQUIRED")
        require_current_direction(before, repair_id, decision)
        if entry.get("closure"):
            if entry["closure"]["validationId"] != validation_id:
                raise LedgerError("DELIVERY_FEEDBACK_ALREADY_CLOSED")
            return {"status": "IDEMPOTENT_FEEDBACK_CLOSURE", "writePerformed": False}, 0
        closure = {"validationId": validation_id, "decisionDigest": canonical_digest(decision),
                   "finalizationDigest": canonical_digest(finalization), "feedbackDigest": feedback_binding(entry),
                   "evidenceRef": matches[0]["evidenceRef"], "closedAt": utc_now()}
        def mutate(after):
            after["tasks"][task_id]["deliveryFeedback"][feedback_id].update(status="CLOSED_VERIFIED", closure=closure)
        return commit_mutation(root, project_id, before, writer, "CLOSE_DELIVERY_FEEDBACK",
            {"taskId": task_id, "feedbackId": feedback_id, "validationId": validation_id}, mutate, args.caller_thread_ref), 0


def record_feedback(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    import independent_handover_validator as validator
    root = load_data_root(args)
    project_id = require_pattern(args.project_id, PROJECT_ID_PATTERN, "PROJECT_ID_INVALID")
    task_id = require_pattern(args.task_id, TASK_ID_PATTERN, "TASK_ID_INVALID")
    writer = require_writer(args.writer_id)
    fields = {"feedbackId": args.feedback_id, "originalValidationId": args.validation_id,
              "issueRef": args.issue_ref, "affectedRef": args.affected_ref}
    value = {key: require_opaque_reference(val, "DELIVERY_FEEDBACK_REFERENCE_INVALID") for key, val in fields.items()}
    value["repairTaskId"] = require_pattern(args.repair_task_id, TASK_ID_PATTERN, "TASK_ID_INVALID") if args.repair_task_id else None
    with ledger_lock(root, project_id):
        require_current_central_thread(root, project_id, args.caller_thread_ref)
        _, code = verify_ledger(args)
        if code: raise LedgerError("DELIVERY_FEEDBACK_LEDGER_UNVERIFIED")
        before = load_ledger(root, project_id)
        task = ensure_task(before, task_id)
        try:
            decision = validator.verify_decision_data(root, project_id, value["originalValidationId"])
            finalization = validator.verify_finalization_data(root, project_id, value["originalValidationId"])
        except (validator.HandoverValidationError, validator.LedgerError) as error:
            raise LedgerError("DELIVERY_FEEDBACK_ORIGINAL_ACCEPTANCE_UNVERIFIED") from error
        if task["status"] != "DONE" or decision["taskId"] != task_id or finalization["bossDecision"] != "APPROVED":
            raise LedgerError("DELIVERY_FEEDBACK_REQUIRES_ACCEPTED_ORIGINAL_TASK")
        existing = task.get("deliveryFeedback", {}).get(value["feedbackId"])
        if existing:
            if any(existing[key] != val for key, val in value.items()):
                raise LedgerError("DELIVERY_FEEDBACK_ID_REUSED")
            return {"status": "IDEMPOTENT_DELIVERY_FEEDBACK", "feedback": existing, "writePerformed": False}, 0
        if value["repairTaskId"]:
            ensure_task(before, value["repairTaskId"])
            if value["repairTaskId"] == task_id:
                raise LedgerError("DELIVERY_FEEDBACK_REPAIR_MUST_BE_DISTINCT")
        entry = {**value, "status": "REPORTED_REQUIRES_REVIEW", "recordedAt": utc_now(),
                 "originalDecisionDigest": canonical_digest(decision),
                 "originalFinalizationDigest": canonical_digest(finalization),
                 "originalPackageDigest": decision["source"]["packageDigest"],
                 "originalOutcomeIds": task.get("outcomeIds", [])}
        def mutate(after):
            after["tasks"][task_id].setdefault("deliveryFeedback", {})[value["feedbackId"]] = entry
        result = commit_mutation(root, project_id, before, writer, "RECORD_DELIVERY_FEEDBACK",
            {"taskId": task_id, "feedbackId": value["feedbackId"]}, mutate, args.caller_thread_ref)
        return {**result, "feedback": entry, "repairTaskCreated": False}, 0


def resolve_wait(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    from initialize_project import is_within
    root = load_data_root(args)
    project_id = require_pattern(args.project_id, PROJECT_ID_PATTERN, "PROJECT_ID_INVALID")
    task_id = require_pattern(args.task_id, TASK_ID_PATTERN, "TASK_ID_INVALID")
    writer = require_writer(args.writer_id)
    try:
        path = Path(args.resolution).resolve()
        if not is_within(path, root): raise LedgerError("EXTERNAL_WAIT_PRIVATE_PROOF_REQUIRED")
        value = json.loads(path.read_text())
        if (not isinstance(value, dict) or set(value) != {"waitId", "taskId", "conditionRef", "directionContext",
                "conditionSatisfied", "executionSafeToResume", "evidence"}
                or value["taskId"] != task_id or value["conditionSatisfied"] is not True
                or value["executionSafeToResume"] is not True):
            raise LedgerError("EXTERNAL_WAIT_RESOLUTION_INVALID")
        wait_id = require_opaque_reference(value["waitId"], "EXTERNAL_WAIT_REFERENCE_INVALID")
        evidence = value["evidence"]
        if not isinstance(evidence, dict) or set(evidence) != {"file", "sha256", "reference"}:
            raise LedgerError("EXTERNAL_WAIT_EVIDENCE_INVALID")
        reference = require_opaque_reference(evidence["reference"], "EXTERNAL_WAIT_REFERENCE_INVALID")
        evidence_path = Path(evidence["file"]).resolve()
        if not is_within(evidence_path, root) or hashlib.sha256(evidence_path.read_bytes()).hexdigest() != evidence["sha256"]:
            raise LedgerError("EXTERNAL_WAIT_EVIDENCE_MISMATCH")
    except (OSError, ValueError, TypeError) as error:
        raise LedgerError("EXTERNAL_WAIT_PRIVATE_PROOF_INVALID") from error
    source_digest = canonical_digest(value)
    with ledger_lock(root, project_id):
        caller = require_current_central_thread(root, project_id, args.caller_thread_ref)
        _, code = verify_ledger(args)
        if code: raise LedgerError("EXTERNAL_WAIT_LEDGER_UNVERIFIED")
        before = load_ledger(root, project_id)
        task = ensure_task(before, task_id)
        entry = task.get("externalWaits", {}).get(wait_id)
        if not entry or entry["conditionRef"] != value["conditionRef"]:
            raise LedgerError("EXTERNAL_WAIT_CONDITION_MISMATCH")
        if entry["status"] == "RESOLVED":
            if entry["resolution"]["sourceDigest"] != source_digest:
                raise LedgerError("EXTERNAL_WAIT_RESOLUTION_REUSED")
            return {"status": "IDEMPOTENT_WAIT_RESOLUTION", "writePerformed": False,
                    "resumptionEligible": not active_external_waits(task) and not direction_hold(task)
                        and task["status"] not in {"DONE", "CANCELLED", "CANCEL_REQUESTED"}}, 0
        context = value["directionContext"]
        if (not isinstance(context, dict) or type(context.get("revision")) is not int
                or context != direction_context(task)):
            raise LedgerError("EXTERNAL_WAIT_DIRECTION_MISMATCH")
        if task["status"] in {"DONE", "CANCELLED", "CANCEL_REQUESTED"}:
            raise LedgerError("EXTERNAL_WAIT_TASK_TERMINAL")
        sealed = {"sourceDigest": source_digest, "directionContext": context, "verifiedByThreadRef": caller,
                  "evidenceRef": reference, "sha256": evidence["sha256"], "resolvedAt": utc_now()}
        def mutate(after):
            after["tasks"][task_id]["externalWaits"][wait_id].update({"status": "RESOLVED", "resolution": sealed})
        result = commit_mutation(root, project_id, before, writer, "RESOLVE_EXTERNAL_WAIT",
            {"taskId": task_id, "waitId": wait_id}, mutate, args.caller_thread_ref)
        current = load_ledger(root, project_id)["tasks"][task_id]
        return {**result, "resumptionEligible": not active_external_waits(current) and not direction_hold(current),
                "runtimeResumed": False}, 0


def record_wait(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    root = load_data_root(args)
    project_id = require_pattern(args.project_id, PROJECT_ID_PATTERN, "PROJECT_ID_INVALID")
    task_id = require_pattern(args.task_id, TASK_ID_PATTERN, "TASK_ID_INVALID")
    writer = require_writer(args.writer_id)
    fields = {"waitId": args.wait_id, "ownerRef": args.owner_ref,
              "conditionRef": args.condition_ref, "reasonRef": args.reason_ref}
    value = {key: require_opaque_reference(val, "EXTERNAL_WAIT_REFERENCE_INVALID") for key, val in fields.items()}
    with ledger_lock(root, project_id):
        require_current_central_thread(root, project_id, args.caller_thread_ref)
        _, code = verify_ledger(args)
        if code: raise LedgerError("EXTERNAL_WAIT_LEDGER_UNVERIFIED")
        before = load_ledger(root, project_id)
        task = ensure_task(before, task_id)
        existing = task.get("externalWaits", {}).get(value["waitId"])
        if existing:
            if any(existing[key] != val for key, val in value.items()):
                raise LedgerError("EXTERNAL_WAIT_ID_REUSED")
            return {"status": "IDEMPOTENT_EXTERNAL_WAIT", "wait": existing, "writePerformed": False}, 0
        if task["status"] in {"DONE", "CANCELLED", "CANCEL_REQUESTED"}:
            raise LedgerError("EXTERNAL_WAIT_TASK_TERMINAL")
        entry = {**value, "status": "WAITING", "recordedAt": utc_now(),
                 "followupStatus": "NOT_SCHEDULED", "runtimeInterrupted": False}
        def mutate(after):
            after["tasks"][task_id].setdefault("externalWaits", {})[value["waitId"]] = entry
        result = commit_mutation(root, project_id, before, writer, "RECORD_EXTERNAL_WAIT",
            {"taskId": task_id, "waitId": value["waitId"]}, mutate, args.caller_thread_ref)
        return {**result, "wait": entry}, 0


def direction_context(task: Dict[str, Any]) -> Dict[str, Any]:
    history = task.get("directionCorrections", [])
    return {"revision": len(history), "digest": history[-1]["requestDigest"] if history else "NONE"}


def validate_direction_context(value: Any) -> Dict[str, Any]:
    if (not isinstance(value, dict) or set(value) != {"revision", "digest"}
        or type(value["revision"]) is not int or value["revision"] < 1
        or not isinstance(value["digest"], str) or re.fullmatch(r"[a-f0-9]{64}", value["digest"]) is None):
        raise LedgerError("TASK_DIRECTION_CONTEXT_INVALID")
    return dict(value)


def require_current_direction(ledger: Dict[str, Any], task_id: str, artifact: Dict[str, Any]) -> None:
    require_direction_ready(ledger, task_id)
    supplied = artifact.get("directionContext", {"revision": 0, "digest": "NONE"})
    if not isinstance(supplied, dict) or type(supplied.get("revision")) is not int or supplied != direction_context(ensure_task(ledger, task_id)):
        raise LedgerError("TASK_DIRECTION_VERSION_MISMATCH")


def verify_correction(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    from direction_correction_protocol import load_verification, communication_snapshot, CorrectionError
    root = load_data_root(args)
    project_id = require_pattern(args.project_id, PROJECT_ID_PATTERN, "PROJECT_ID_INVALID")
    task_id = require_pattern(args.task_id, TASK_ID_PATTERN, "TASK_ID_INVALID")
    writer = require_writer(args.writer_id)
    try:
        with ledger_lock(root, project_id):
            caller = require_current_central_thread(root, project_id, args.caller_thread_ref)
            if not caller: raise LedgerError("CORRECTION_CURRENT_CENTRAL_REQUIRED")
            _, code = verify_ledger(args)
            if code: raise LedgerError("CORRECTION_LEDGER_INTEGRITY_UNVERIFIED")
            before = load_ledger(root, project_id)
            task = ensure_task(before, task_id)
            verified = load_verification(root, args.verification, project_id, task, caller)
            existing = next((item for item in task.get("directionVerifications", [])
                if item["revision"] == verified["revision"]), None)
            if existing:
                if existing["sourceDigest"] != verified["sourceDigest"]: raise LedgerError("CORRECTION_VERIFICATION_ALREADY_RECORDED")
                return {**correction_snapshot(task, project_id), "status": "IDEMPOTENT_CORRECTION_VERIFICATION"}, 0
            if task["status"] in {"DONE", "CANCELLED", "CANCEL_REQUESTED", "CONFLICT"}:
                raise LedgerError("CORRECTION_TASK_NOT_RESUMABLE")
            communication = communication_snapshot(root, project_id, before, task_id)
            if not communication["readyForVerification"]: raise LedgerError("CORRECTION_COMMUNICATION_INCOMPLETE")
            record = {**verified, "communication": communication["communication"], "verifiedAt": utc_now()}
            def mutate(after):
                target = after["tasks"][task_id]
                target.setdefault("directionVerifications", []).append(record)
                target["history"].append({"at": record["verifiedAt"], "event": "DIRECTION_APPLICATION_VERIFIED",
                    "revision": verified["revision"], "verificationId": verified["verificationId"], "by": writer})
            result = commit_mutation(root, project_id, before, writer, "VERIFY_DIRECTION_APPLICATION",
                {"taskId": task_id, "revision": verified["revision"]}, mutate, caller)
            return {**correction_snapshot(load_ledger(root, project_id)["tasks"][task_id], project_id), **result}, 0
    except CorrectionError as error:
        raise LedgerError(str(error)) from error


def read_correction(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    _, exit_code = verify_ledger(args)
    if exit_code: raise LedgerError("CORRECTION_LEDGER_INTEGRITY_UNVERIFIED")
    ledger = load_ledger(load_data_root(args), args.project_id)
    ensure_task(ledger, args.task_id)
    from direction_correction_protocol import communication_snapshot, CorrectionError
    try:
        communication = communication_snapshot(load_data_root(args), args.project_id, ledger, args.task_id)
    except CorrectionError as error:
        raise LedgerError(str(error)) from error
    return {**correction_snapshot(ensure_task(ledger, args.task_id), args.project_id), **communication}, 0


def record_correction(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    """Record a task-local execution hold; never claim to interrupt the runtime."""
    root = load_data_root(args)
    project_id = require_pattern(args.project_id, PROJECT_ID_PATTERN, "PROJECT_ID_INVALID")
    task_id = require_pattern(args.task_id, TASK_ID_PATTERN, "TASK_ID_INVALID")
    writer = require_writer(args.writer_id)
    reference_fields = {"correctionId": args.correction_id, "instructionRef": args.instruction_ref,
                        "reasonRef": args.reason_ref, "bossDecisionRef": args.boss_decision_ref}
    value = {key: require_opaque_reference(val, "CORRECTION_REFERENCE_INVALID") for key, val in reference_fields.items()}
    for key, refs in [("retainedRequirementRefs", args.retain_ref), ("supersededRequirementRefs", args.supersede_ref)]:
        value[key] = [require_opaque_reference(ref, "CORRECTION_REFERENCE_INVALID") for ref in refs]
        if len(set(value[key])) != len(value[key]): raise LedgerError("CORRECTION_DUPLICATE_REFERENCE")
    if set(value["retainedRequirementRefs"]) & set(value["supersededRequirementRefs"]):
        raise LedgerError("CORRECTION_REQUIREMENTS_CONFLICT")
    value["expectedRevision"] = args.expected_revision
    if args.expected_revision < 0: raise LedgerError("CORRECTION_REVISION_INVALID")
    request_digest = canonical_digest(value)
    with ledger_lock(root, project_id):
        require_current_central_thread(root, project_id, args.caller_thread_ref)
        _, exit_code = verify_ledger(args)
        if exit_code: raise LedgerError("CORRECTION_LEDGER_INTEGRITY_UNVERIFIED")
        before = load_ledger(root, project_id)
        task = ensure_task(before, task_id)
        history = task.get("directionCorrections", [])
        existing = next((item for item in history if item["correctionId"] == value["correctionId"]), None)
        if existing:
            if existing["requestDigest"] != request_digest: raise LedgerError("CORRECTION_ID_REUSED")
            return {**correction_snapshot(task, project_id), "status": "IDEMPOTENT_CORRECTION", "recordedRevision": existing["revision"]}, 0
        if len(history) != args.expected_revision: raise LedgerError("CORRECTION_STALE_REVISION")
        if task["status"] in {"DONE", "CANCELLED", "CANCEL_REQUESTED"}:
            raise LedgerError("CORRECTION_REQUIRES_ACTIVE_TASK")
        entry = {**value, "revision": len(history) + 1, "requestDigest": request_digest,
                 "createdAt": utc_now(), "scope": "EXECUTION_WITHIN_APPROVED_CONTRACT",
                 "windowBindings": [{"windowId": key, "runtimeThreadRef": window.get("runtimeThreadRef", key),
                     "generation": window.get("generation", 1)} for key, window in before["windows"].items()
                     if window_current_task_id(window) == task_id]}

        def mutate(after: Dict[str, Any]) -> None:
            target = after["tasks"][task_id]
            target.setdefault("directionCorrections", []).append(entry)
            target["history"].append({"at": entry["createdAt"], "event": "DIRECTION_CORRECTION_RECORDED",
                "correctionId": entry["correctionId"], "revision": entry["revision"], "by": CENTRAL_WRITER})

        result = commit_mutation(root, project_id, before, writer, "RECORD_DIRECTION_CORRECTION",
            {"taskId": task_id, "correctionId": entry["correctionId"]}, mutate, args.caller_thread_ref)
        return {**correction_snapshot(load_ledger(root, project_id)["tasks"][task_id], project_id), **result}, 0


def ensure_owner(ledger: Dict[str, Any], owner_type: str, owner_id: str) -> None:
    containers = {"task": "tasks", "window": "windows", "sub-agent": "subAgents"}
    if owner_type not in containers or owner_id not in ledger[containers[owner_type]]:
        raise LedgerError("OBJECT_CLAIM_OWNER_NOT_FOUND")


def initialize_ledger(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args)
    project_id = require_pattern(args.project_id, PROJECT_ID_PATTERN, "PROJECT_ID_INVALID")
    load_project_card(data_root, project_id)
    target = ledger_path(data_root, project_id)
    if target.exists() or ledger_directory(data_root, project_id).exists():
        raise LedgerError("C03_LEDGER_ALREADY_EXISTS_OR_NEEDS_RECOVERY")
    if args.dry_run:
        return {
            "status": "READY_FOR_BOSS_APPROVAL",
            "action": "MIGRATE_C02_STARTUP_CARD_TO_C03_LEDGER",
            "projectId": project_id,
            "writePerformed": False,
            "message": "C02 项目启动卡有效。批准后将创建仅属于 Codex 模块的详细工程总账。",
        }, 0

    writer_id = require_writer(args.writer_id)
    with ledger_lock(data_root, project_id):
        directory = ledger_directory(data_root, project_id)
        if directory.exists():
            raise LedgerError("C03_LEDGER_ALREADY_EXISTS_OR_NEEDS_RECOVERY")
        directory.mkdir(parents=True, exist_ok=False)
        receipt_directory(data_root, project_id).mkdir(exist_ok=False)
        ledger = new_ledger(project_id, utc_now())
        receipt_id = "receipt-000000-initialize"
        ledger["receiptIds"] = [receipt_id]
        ledger["latestReceiptId"] = receipt_id
        receipt = {
            "schemaVersion": SCHEMA_VERSION,
            "recordType": "C03_IMMUTABLE_LEDGER_RECEIPT",
            "receiptId": receipt_id,
            "createdAt": utc_now(),
            "projectId": project_id,
            "module": MODULE,
            "writerId": writer_id,
            "operation": "MIGRATE_C02_STARTUP_CARD_TO_C03_LEDGER",
            "beforeRevision": None,
            "afterRevision": 0,
            "beforeLedgerDigest": "NONE",
            "afterLedgerDigest": canonical_digest(ledger),
            "changeSummary": {"source": "C02_PROJECT_STARTUP_CARD", "createdPartitions": sorted(ledger["partitions"])},
            "afterLedger": ledger,
        }
        write_json_exclusive(receipt_directory(data_root, project_id) / f"{receipt_id}.json", receipt)
        write_json_exclusive(ledger_path(data_root, project_id), ledger)
    return {
        "status": "C03_LEDGER_INITIALIZED",
        "projectId": project_id,
        "ledgerId": ledger["ledgerId"],
        "writer": CENTRAL_WRITER,
        "receiptId": receipt_id,
        "writePerformed": True,
        "message": "已从 C02 项目启动卡创建 Codex 模块详细总账；未创建任务、窗口、TEST 或业务对象。",
    }, 0


def mutate_ledger(args: argparse.Namespace, operation: str, change_summary: Dict[str, Any], mutate) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args)
    project_id = require_pattern(args.project_id, PROJECT_ID_PATTERN, "PROJECT_ID_INVALID")
    writer_id = require_writer(args.writer_id)
    with ledger_lock(data_root, project_id):
        ledger = load_ledger(data_root, project_id)
        return commit_mutation(
            data_root,
            project_id,
            ledger,
            writer_id,
            operation,
            change_summary,
            mutate,
            caller_thread_ref=args.caller_thread_ref,
        ), 0


def import_outline(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args).resolve()
    try:
        handoff_path = Path(args.handoff).resolve(strict=True)
        outline_path = Path(args.outline_file).resolve(strict=True)
        if not handoff_path.is_relative_to(data_root) or not outline_path.is_relative_to(data_root):
            raise LedgerError("OUTLINE_REQUIRES_PRIVATE_INPUTS")
        contract = validate_handoff(json.loads(handoff_path.read_text(encoding="utf-8")), args.project_id)
        if hashlib.sha256(outline_path.read_bytes()).hexdigest() != contract["outline"]["sha256"]:
            raise LedgerError("OUTLINE_CONTENT_DIGEST_MISMATCH")
        if contract["bossApproval"]["reference"] != args.boss_approval_ref:
            raise LedgerError("OUTLINE_APPROVAL_REFERENCE_MISMATCH")
    except (OSError, UnicodeError, json.JSONDecodeError, OutlineContractError) as error:
        raise LedgerError(f"OUTLINE_IMPORT_INVALID: {error}") from error
    digest = canonical_digest(contract)

    def mutate(ledger: Dict[str, Any]) -> None:
        previous = ledger.get("outlineContract")
        if previous and previous.get("digest") != digest:
            raise LedgerError("OUTLINE_UPDATE_REQUIRES_RECONCILIATION")
        ledger["outlineContract"] = {"digest": digest, "handoff": contract}

    project_id = require_pattern(args.project_id, PROJECT_ID_PATTERN, "PROJECT_ID_INVALID")
    writer_id = require_writer(args.writer_id)
    with ledger_lock(data_root, project_id):
        ledger = load_ledger(data_root, project_id)
        require_current_central_thread(data_root, project_id, args.caller_thread_ref)
        if args.command == "revise-outline":
            change_id = require_opaque_reference(args.change_id, "OUTLINE_CHANGE_ID_INVALID")
            request = {"changeId": change_id, "previousDigest": args.expected_digest, "nextDigest": digest}
            existing = next((item for item in ledger.get("outlineRevisions", []) if item["changeId"] == change_id), None)
            if existing:
                if any(existing[key] != value for key, value in request.items()):
                    raise LedgerError("OUTLINE_CHANGE_ID_REUSED")
                return {"status": "IDEMPOTENT_OUTLINE_REVISION", "writePerformed": False, "revision": existing}, 0
            previous = ledger.get("outlineContract")
            if not previous or previous["digest"] != args.expected_digest:
                raise LedgerError("OUTLINE_REVISION_STALE")
            if (previous["handoff"]["outline"]["version"] == contract["outline"]["version"]
                    or previous["handoff"]["bossApproval"]["reference"] == args.boss_approval_ref):
                raise LedgerError("OUTLINE_NEW_VERSION_AND_APPROVAL_REQUIRED")
            from outline_outcome_contract import changed_outcomes
            changed = changed_outcomes(previous["handoff"], contract)
            affected = [key for key, task in ledger["tasks"].items() if set(task.get("outcomeIds", [])) & changed]
            entry = {**request, "approvalRef": args.boss_approval_ref, "affectedTaskIds": affected,
                     "changedOutcomeIds": sorted(changed), "recordedAt": utc_now()}
            def revise(after):
                after.setdefault("outlineHistory", {})[previous["digest"]] = previous
                after["outlineContract"] = {"digest": digest, "handoff": contract}
                after.setdefault("outlineRevisions", []).append(entry)
                for key in affected:
                    after["tasks"][key].setdefault("outlineImpacts", []).append({"changeId": change_id,
                        "outlineDigest": digest, "status": "RECONCILIATION_REQUIRED",
                        "runtimeInterrupted": False})
            result = commit_mutation(data_root, project_id, ledger, writer_id, "REVISE_OUTLINE", request, revise,
                                     caller_thread_ref=args.caller_thread_ref)
            return {**result, "revision": entry, "runtimeInterrupted": False}, 0
        if ledger.get("outlineContract", {}).get("digest") == digest:
            return {"status": "OUTLINE_ALREADY_REGISTERED", "outlineDigest": digest, "writePerformed": False}, 0
        return commit_mutation(data_root, project_id, ledger, writer_id, "IMPORT_OUTLINE",
            {"outlineDigest": digest}, mutate, caller_thread_ref=args.caller_thread_ref), 0


def add_task(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    task_id = require_pattern(args.task_id, TASK_ID_PATTERN, "TASK_ID_INVALID")
    title, canonical_title = task_titles(task_id, require_text(args.title, "TASK_TITLE_INVALID", 160))
    business_goal = require_text(args.business_goal, "TASK_BUSINESS_GOAL_INVALID", 1200)
    plan_ref = require_opaque_reference(args.plan_ref, "PLAN_REFERENCE_INVALID")

    def mutate(ledger: Dict[str, Any]) -> None:
        if task_id in ledger["tasks"]:
            raise LedgerError("TASK_ID_ALREADY_EXISTS")
        outcome_ids = getattr(args, "outcome_id", None) or []
        contract = ledger.get("outlineContract")
        outcome_link = {}
        if contract:
            allowed = {ref for candidate in contract["handoff"]["candidateTasks"]
                       if candidate["codexCentralRegistration"] for ref in candidate["outcomeIds"]}
            if not outcome_ids or len(outcome_ids) != len(set(outcome_ids)) or not set(outcome_ids) <= allowed:
                raise LedgerError("TASK_APPROVED_OUTCOME_REQUIRED")
            outcome_link = {"outcomeIds": outcome_ids, "outlineContractDigest": contract["digest"]}
        elif outcome_ids:
            raise LedgerError("TASK_OUTLINE_CONTRACT_NOT_REGISTERED")
        ledger["tasks"][task_id] = {
            "taskId": task_id,
            "title": title,
            "canonicalTitle": canonical_title,
            "businessGoal": business_goal,
            "planRef": plan_ref,
            **outcome_link,
            "status": "PLANNED",
            "createdAt": utc_now(),
            "completionSignalIds": [],
            "history": [{"at": utc_now(), "event": "TASK_REGISTERED", "by": CENTRAL_WRITER}],
        }

    return mutate_ledger(args, "REGISTER_TASK", {"taskId": task_id, "planRef": plan_ref}, mutate)


def transition_task(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    task_id = require_pattern(args.task_id, TASK_ID_PATTERN, "TASK_ID_INVALID")
    target_status = require_text(args.to_status, "TASK_STATUS_INVALID", 40).upper()
    reason = require_text(args.reason, "TASK_TRANSITION_REASON_INVALID", 500)
    if target_status == "DONE":
        raise LedgerError("DONE_REQUIRES_C06_INDEPENDENT_VALIDATION")
    if target_status not in TASK_TRANSITIONS:
        raise LedgerError("TASK_STATUS_INVALID")

    def mutate(ledger: Dict[str, Any]) -> None:
        task = ensure_task(ledger, task_id)
        source_status = task["status"]
        if target_status not in TASK_TRANSITIONS.get(source_status, set()):
            raise LedgerError("TASK_STATE_TRANSITION_NOT_ALLOWED")
        task["status"] = target_status
        task["history"].append({"at": utc_now(), "event": "STATUS_CHANGED", "from": source_status, "to": target_status, "reason": reason, "by": CENTRAL_WRITER})

    return mutate_ledger(args, "TRANSITION_TASK", {"taskId": task_id, "toStatus": target_status}, mutate)


def cancel_unstarted_task(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    """Cancel a pre-runtime task and atomically release only its own claims.

    This is intentionally narrower than a general occupancy-release operation:
    it accepts only READY/PLANNED tasks with no completion signal, no active
    registered window and no C10 runtime confirmation.
    """
    data_root = load_data_root(args)
    project_id = require_pattern(args.project_id, PROJECT_ID_PATTERN, "PROJECT_ID_INVALID")
    writer_id = require_writer(args.writer_id)
    task_id = require_pattern(args.task_id, TASK_ID_PATTERN, "TASK_ID_INVALID")
    boss_decision_ref = require_opaque_reference(
        args.boss_decision_ref, "CANCEL_TASK_BOSS_DECISION_REFERENCE_INVALID"
    )
    reason = require_text(args.reason, "TASK_CANCELLATION_REASON_INVALID", 500)

    with ledger_lock(data_root, project_id):
        before = load_ledger(data_root, project_id)
        task = ensure_task(before, task_id)
        prior_releases = [
            item for item in before.get("releasedOccupancies", [])
            if item.get("taskId") == task_id
            and item.get("releaseType") == "CANCELLED_PRE_RUNTIME"
        ]
        matching = [
            {"objectKey": key, "claim": claim}
            for key, occupancy in before["objectOccupancies"].items()
            for claim in occupancy.get("claims", [])
            if claim.get("ownerType") == "task" and claim.get("ownerId") == task_id
        ]
        if task.get("status") == "CANCELLED" and not matching and len(prior_releases) == 1:
            return {
                "status": "IDEMPOTENT_UNSTARTED_TASK_ALREADY_CANCELLED",
                "taskId": task_id,
                "releasedClaimCount": prior_releases[0]["releasedClaimCount"],
                "occupancyReleased": True,
                "writePerformed": False,
            }, 0
        if task.get("status") not in {"PLANNED", "READY"}:
            raise LedgerError("CANCEL_UNSTARTED_TASK_REQUIRES_PLANNED_OR_READY")
        if task.get("completionSignalIds"):
            raise LedgerError("CANCEL_UNSTARTED_TASK_HAS_COMPLETION_SIGNAL")
        if any(stop.get("taskId") == task_id for stop in before["hardStops"]):
            raise LedgerError("CANCEL_UNSTARTED_TASK_HAS_ACTIVE_HARD_STOP")
        active_window_statuses = {
            "REGISTERED", "ACTIVE", "IN_PROGRESS", "READY", "DISPATCHED", "BLOCKED", "DISCONNECTED"
        }
        if any(
            window.get("currentTaskId", window.get("taskId")) == task_id
            and window.get("status") in active_window_statuses
            for window in before.get("windows", {}).values()
        ):
            raise LedgerError("CANCEL_UNSTARTED_TASK_HAS_ACTIVE_WINDOW")
        confirmations_root = data_root / "dispatches" / project_id
        if confirmations_root.is_dir():
            for confirmation_path in confirmations_root.glob("*/dispatch-confirmation.json"):
                try:
                    confirmation = json.loads(confirmation_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    raise LedgerError("CANCEL_UNSTARTED_TASK_CONFIRMATION_UNREADABLE")
                if confirmation.get("taskId") == task_id:
                    raise LedgerError("CANCEL_UNSTARTED_TASK_HAS_RUNTIME_CONFIRMATION")
        if prior_releases:
            raise LedgerError("CANCEL_UNSTARTED_TASK_RELEASE_HISTORY_CONFLICT")

        def mutate(after: Dict[str, Any]) -> None:
            cancelled_at = utc_now()
            for key in list(after["objectOccupancies"]):
                occupancy = after["objectOccupancies"][key]
                occupancy["claims"] = [
                    claim for claim in occupancy.get("claims", [])
                    if not (claim.get("ownerType") == "task" and claim.get("ownerId") == task_id)
                ]
                if not occupancy["claims"]:
                    del after["objectOccupancies"][key]
                else:
                    occupancy["status"] = "CONFLICT" if len(occupancy["claims"]) > 1 else "CLAIMED"
            target = after["tasks"][task_id]
            source_status = target["status"]
            target["status"] = "CANCELLED"
            target["history"].append({
                "at": cancelled_at,
                "event": "UNSTARTED_TASK_CANCELLED_AND_OCCUPANCY_RELEASED",
                "from": source_status,
                "to": "CANCELLED",
                "reason": reason,
                "bossDecisionRef": boss_decision_ref,
                "releasedClaimCount": len(matching),
                "by": CENTRAL_WRITER,
            })
            after.setdefault("releasedOccupancies", []).append({
                "releaseId": f"released-occupancy-{uuid.uuid4().hex[:12]}",
                "releaseType": "CANCELLED_PRE_RUNTIME",
                "releasedAt": cancelled_at,
                "releasedBy": CENTRAL_WRITER,
                "taskId": task_id,
                "bossDecisionRef": boss_decision_ref,
                "reason": reason,
                "releasedClaimCount": len(matching),
                "releasedClaims": matching,
            })

        result = commit_mutation(
            data_root,
            project_id,
            before,
            writer_id,
            "CANCEL_UNSTARTED_TASK_AND_RELEASE_OCCUPANCY",
            {"taskId": task_id, "bossDecisionRef": boss_decision_ref},
            mutate,
            caller_thread_ref=args.caller_thread_ref,
        )
        result.update({
            "status": "UNSTARTED_TASK_CANCELLED",
            "taskId": task_id,
            "releasedClaimCount": len(matching),
            "occupancyReleased": True,
        })
        return result, 0


def record_completion_signal(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args)
    project_id = require_pattern(args.project_id, PROJECT_ID_PATTERN, "PROJECT_ID_INVALID")
    writer_id = require_writer(args.writer_id)
    task_id = require_pattern(args.task_id, TASK_ID_PATTERN, "TASK_ID_INVALID")
    signal_id = require_opaque_reference(args.signal_id, "COMPLETION_SIGNAL_ID_INVALID")
    with ledger_lock(data_root, project_id):
        ledger = load_ledger(data_root, project_id)
        task = ensure_task(ledger, task_id)
        if signal_id in task["completionSignalIds"]:
            return {
                "status": "IDEMPOTENT_DUPLICATE_SIGNAL",
                "taskId": task_id,
                "signalId": signal_id,
                "currentTaskStatus": task["status"],
                "writePerformed": False,
                "message": "该完成信号已接收，任务状态没有被重复推进。",
            }, 0
        if task["status"] != "IN_PROGRESS":
            raise LedgerError("COMPLETION_SIGNAL_REQUIRES_IN_PROGRESS_TASK")

        def mutate(after: Dict[str, Any]) -> None:
            target = ensure_task(after, task_id)
            target["completionSignalIds"].append(signal_id)
            target["status"] = "NEEDS_REVIEW"
            target["history"].append({"at": utc_now(), "event": "COMPLETION_SIGNAL_RECEIVED", "signalId": signal_id, "to": "NEEDS_REVIEW", "by": CENTRAL_WRITER})

        return commit_mutation(
            data_root,
            project_id,
            ledger,
            writer_id,
            "RECORD_COMPLETION_SIGNAL",
            {"taskId": task_id, "signalId": signal_id, "toStatus": "NEEDS_REVIEW"},
            mutate,
            caller_thread_ref=args.caller_thread_ref,
        ), 0


def register_window(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    window_id = require_pattern(args.window_id, TASK_ID_PATTERN, "WINDOW_ID_INVALID")
    runtime_thread_ref = getattr(args, "runtime_thread_ref", None)
    thread_evidence_ref = getattr(args, "runtime_thread_evidence_ref", None)
    if (runtime_thread_ref is None) != (thread_evidence_ref is None):
        raise LedgerError("WINDOW_RUNTIME_THREAD_EVIDENCE_INCOMPLETE")
    if runtime_thread_ref is not None:
        runtime_thread_ref = require_opaque_reference(runtime_thread_ref, "WINDOW_RUNTIME_THREAD_REF_INVALID")
        thread_evidence_ref = require_opaque_reference(thread_evidence_ref, "WINDOW_RUNTIME_THREAD_EVIDENCE_INVALID")
    task_id = require_pattern(args.task_id, TASK_ID_PATTERN, "TASK_ID_INVALID")
    context_mode = require_text(args.context_mode, "WINDOW_CONTEXT_MODE_INVALID", 20).upper()
    if context_mode not in {"NEW", "REUSED"}:
        raise LedgerError("WINDOW_CONTEXT_MODE_INVALID")
    model_evidence_ref = None
    runtime_model = getattr(args, "runtime_model", None) or TASK_RUNTIME_MODEL
    if not valid_model(runtime_model):
        raise LedgerError("WINDOW_RUNTIME_MODEL_INVALID")
    if getattr(args, "runtime_model", None) and args.runtime_model_evidence_ref is None:
        raise LedgerError("WINDOW_RUNTIME_MODEL_EVIDENCE_INVALID")
    if args.runtime_model_evidence_ref is not None:
        model_evidence_ref = require_opaque_reference(args.runtime_model_evidence_ref, "WINDOW_RUNTIME_MODEL_EVIDENCE_INVALID")
    permission_profile = args.runtime_permission_profile
    permission_evidence_ref = args.runtime_permission_evidence_ref
    writable_roots = args.runtime_writable_root or []
    governance_access = args.governance_data_root_access
    permission_class = None
    if (permission_profile is None) != (permission_evidence_ref is None):
        raise LedgerError("WINDOW_RUNTIME_PERMISSION_EVIDENCE_INCOMPLETE")
    if permission_profile is not None:
        permission_profile = require_text(permission_profile, "WINDOW_RUNTIME_PERMISSION_PROFILE_INVALID", 80)
        if permission_profile not in ALLOWED_TASK_PERMISSION_PROFILES:
            raise LedgerError("WINDOW_RUNTIME_PERMISSION_PROFILE_INVALID")
        permission_evidence_ref = require_opaque_reference(permission_evidence_ref, "WINDOW_RUNTIME_PERMISSION_EVIDENCE_INVALID")
        if permission_profile in FULL_ACCESS_PERMISSION_PROFILES:
            permission_class = "FULL_ACCESS"
            if writable_roots or (governance_access is not None and str(governance_access).strip().upper() != "NOT_RESTRICTED"):
                raise LedgerError("WINDOW_FULL_ACCESS_EVIDENCE_INVALID")
            governance_access = "NOT_RESTRICTED"
        else:
            permission_class = "WORKTREE_SCOPED"
            if not writable_roots or str(governance_access).strip().upper() != "DENIED":
                raise LedgerError("WINDOW_RUNTIME_PERMISSION_EVIDENCE_INCOMPLETE")
            normalized_roots = []
            for raw_root in writable_roots:
                candidate = Path(raw_root).expanduser()
                if not candidate.is_absolute() or ".." in candidate.parts:
                    raise LedgerError("WINDOW_RUNTIME_WRITABLE_ROOT_INVALID")
                normalized_roots.append(str(candidate.resolve()))
            writable_roots = normalized_roots
            governance_access = "DENIED"
    elif writable_roots or governance_access is not None:
        raise LedgerError("WINDOW_RUNTIME_PERMISSION_EVIDENCE_INCOMPLETE")

    def mutate(ledger: Dict[str, Any]) -> None:
        if window_id in ledger["windows"]:
            raise LedgerError("WINDOW_ID_ALREADY_EXISTS")
        if any(item.get("runtimeThreadRef") == (runtime_thread_ref or window_id) for item in ledger["windows"].values()):
            raise LedgerError("WINDOW_RUNTIME_THREAD_ALREADY_REGISTERED")
        task = ensure_task(ledger, task_id)
        require_direction_ready(ledger, task_id)
        if task["status"] in {"CANCELLED", "DONE"}:
            raise LedgerError("WINDOW_CANNOT_ATTACH_TO_TERMINAL_TASK")
        existing_generations = [
            item.get("generation", 1)
            for item in ledger["windows"].values()
            if item.get("taskId") == task_id and isinstance(item.get("generation", 1), int)
        ]
        generation = max(existing_generations, default=0) + 1
        canonical_title = task.get("canonicalTitle", f"{task_id}｜{task['title']}")
        window = {
            "windowId": window_id,
            "taskId": task_id,
            "currentTaskId": task_id,
            "canonicalTitle": canonical_title,
            "generation": generation,
            "runtimeTitle": f"{canonical_title}｜G{generation}",
            "runtimeThreadRef": runtime_thread_ref or window_id,
            "model": "UNVERIFIED",
            "contextMode": context_mode,
            "status": "REGISTERED",
            "maxAssignments": MAX_TASKS_PER_WINDOW,
            "assignmentCount": 1,
            "assignmentHistory": [{
                "assignmentNumber": 1,
                "taskId": task_id,
                "canonicalTitle": canonical_title,
                "generation": generation,
                "runtimeTitle": f"{canonical_title}｜G{generation}",
                "contextMode": context_mode,
                "dispatchId": None,
                "assignedAt": utc_now(),
                "completedAt": None,
                "status": "ACTIVE",
            }],
            "registeredAt": utc_now(),
        }
        if thread_evidence_ref is not None:
            window["runtimeThreadEvidenceRef"] = thread_evidence_ref
        if model_evidence_ref is not None:
            window.update({
                "model": runtime_model,
                "runtimeModel": runtime_model,
                "modelEnforcement": {
                    "model": runtime_model,
                    "method": MANUAL_MODEL_METHOD if getattr(args, "runtime_model", None) else MANUAL_WINDOW_MODEL_METHOD,
                    "evidenceRef": model_evidence_ref,
                    "confirmedAt": utc_now(),
                },
            })
        if permission_profile is not None:
            window["permissionEnforcement"] = {
                "permissionClass": permission_class,
                "profile": permission_profile,
                "method": MANUAL_WINDOW_PERMISSION_METHOD,
                "evidenceRef": permission_evidence_ref,
                "writableRoots": writable_roots,
                "governanceDataRootAccess": governance_access,
                "confirmedAt": utc_now(),
            }
        ledger["windows"][window_id] = window

    return mutate_ledger(args, "REGISTER_TASK_WINDOW", {"windowId": window_id, "taskId": task_id, "model": runtime_model if model_evidence_ref is not None else "UNVERIFIED", "modelEvidenceRecorded": model_evidence_ref is not None, "permissionClass": permission_class or "UNVERIFIED", "permissionEvidenceRecorded": permission_profile is not None}, mutate)


def register_sub_agent(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    sub_agent_id = require_pattern(args.sub_agent_id, TASK_ID_PATTERN, "SUB_AGENT_ID_INVALID")
    window_id = require_pattern(args.window_id, TASK_ID_PATTERN, "WINDOW_ID_INVALID")
    role = require_text(args.role, "SUB_AGENT_ROLE_INVALID", 160)

    def mutate(ledger: Dict[str, Any]) -> None:
        if sub_agent_id in ledger["subAgents"]:
            raise LedgerError("SUB_AGENT_ID_ALREADY_EXISTS")
        if window_id not in ledger["windows"]:
            raise LedgerError("WINDOW_NOT_FOUND")
        active = [agent for agent in ledger["subAgents"].values() if agent["windowId"] == window_id and agent["status"] in {"REGISTERED", "FROZEN", "DISCONNECTED"}]
        if len(active) >= 3:
            raise LedgerError("FIRST_LEVEL_SUB_AGENT_LIMIT_REACHED")
        ledger["subAgents"][sub_agent_id] = {
            "subAgentId": sub_agent_id,
            "windowId": window_id,
            "role": role,
            "model": "UNVERIFIED",
            "defaultModel": TASK_RUNTIME_MODEL,
            "level": 1,
            "status": "REGISTERED",
            "registeredAt": utc_now(),
        }

    return mutate_ledger(args, "REGISTER_FIRST_LEVEL_SUB_AGENT", {"subAgentId": sub_agent_id, "windowId": window_id}, mutate)


def claim_object(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    object_key = require_object_key(args.object_key)
    owner_type = require_text(args.owner_type, "OBJECT_CLAIM_OWNER_TYPE_INVALID", 20)
    owner_id = require_pattern(args.owner_id, TASK_ID_PATTERN, "OBJECT_CLAIM_OWNER_ID_INVALID")
    intent = require_text(args.intent, "OBJECT_CLAIM_INTENT_INVALID", 20).upper()
    if intent not in {"READ", "WRITE"}:
        raise LedgerError("OBJECT_CLAIM_INTENT_INVALID")

    def mutate(ledger: Dict[str, Any]) -> None:
        ensure_owner(ledger, owner_type, owner_id)
        if intent == "WRITE":
            if owner_type == "task":
                require_direction_ready(ledger, owner_id)
            else:
                window_id = owner_id if owner_type == "window" else ledger["subAgents"][owner_id]["windowId"]
                task_id = window_current_task_id(ledger["windows"][window_id])
                if task_id: require_direction_ready(ledger, task_id)
        existing = ledger["objectOccupancies"].get(object_key)
        claim = {
            "claimId": f"claim-{uuid.uuid4().hex[:12]}",
            "ownerType": owner_type,
            "ownerId": owner_id,
            "intent": intent,
            "registeredAt": utc_now(),
        }
        if existing is None:
            ledger["objectOccupancies"][object_key] = {"objectKey": object_key, "status": "CLAIMED", "claims": [claim]}
            return
        if any(item["ownerType"] == owner_type and item["ownerId"] == owner_id and item["intent"] == intent for item in existing["claims"]):
            raise LedgerError("OBJECT_CLAIM_ALREADY_RECORDED")
        existing["claims"].append(claim)
        existing["status"] = "CONFLICT"
        hard_stop = {"code": "OBJECT_OCCUPANCY_CONFLICT", "objectKey": object_key, "detectedAt": utc_now(), "claimCount": len(existing["claims"])}
        if not any(item.get("code") == hard_stop["code"] and item.get("objectKey") == object_key for item in ledger["hardStops"]):
            ledger["hardStops"].append(hard_stop)

    result, exit_code = mutate_ledger(args, "CLAIM_OBJECT", {"objectKey": object_key, "ownerType": owner_type, "ownerId": owner_id, "intent": intent}, mutate)
    result["conflictCheckRequired"] = True
    return result, exit_code


def record_evidence(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    evidence_id = require_opaque_reference(args.evidence_id, "EVIDENCE_ID_INVALID")
    reference = require_opaque_reference(args.reference, "EVIDENCE_REFERENCE_INVALID")
    category = require_text(args.category, "EVIDENCE_CATEGORY_INVALID", 80)
    summary = require_text(args.summary, "EVIDENCE_SUMMARY_INVALID", 1000)

    def mutate(ledger: Dict[str, Any]) -> None:
        if evidence_id in ledger["evidenceRefs"]:
            raise LedgerError("EVIDENCE_ID_ALREADY_EXISTS")
        ledger["evidenceRefs"][evidence_id] = {"evidenceId": evidence_id, "reference": reference, "category": category, "summary": summary, "recordedAt": utc_now()}

    return mutate_ledger(args, "RECORD_EVIDENCE_REFERENCE", {"evidenceId": evidence_id, "category": category}, mutate)


def record_adjudication(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    adjudication_id = require_opaque_reference(args.adjudication_id, "ADJUDICATION_ID_INVALID")
    request_ref = require_opaque_reference(args.request_ref, "ADJUDICATION_REQUEST_REFERENCE_INVALID")
    outcome = require_text(args.outcome, "ADJUDICATION_OUTCOME_INVALID", 40).upper()
    if outcome not in {"ADVICE_RECEIVED", "BOSS_DECISION"}:
        raise LedgerError("ADJUDICATION_OUTCOME_INVALID")

    def mutate(ledger: Dict[str, Any]) -> None:
        if adjudication_id in ledger["adjudications"]:
            raise LedgerError("ADJUDICATION_ID_ALREADY_EXISTS")
        ledger["adjudications"][adjudication_id] = {"adjudicationId": adjudication_id, "requestRef": request_ref, "outcome": outcome, "recordedAt": utc_now()}

    return mutate_ledger(args, "RECORD_ADJUDICATION_REFERENCE", {"adjudicationId": adjudication_id, "outcome": outcome}, mutate)


def resolve_c06_validation_conflict(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    """Move one Boss-adjudicated historical C06 conflict out of the active stop set.

    The immutable receipt chain continues to preserve the original hard stop. The
    current ledger additionally archives it under resolvedHardStops with the
    superseding PASS decision and Boss decision that authorized resolution.
    """
    data_root = load_data_root(args)
    project_id = require_pattern(args.project_id, PROJECT_ID_PATTERN, "PROJECT_ID_INVALID")
    writer_id = require_writer(args.writer_id)
    task_id = require_pattern(args.task_id, TASK_ID_PATTERN, "TASK_ID_INVALID")
    conflict_validation_id = require_opaque_reference(
        args.conflict_validation_id, "C06_CONFLICT_VALIDATION_ID_INVALID"
    )
    superseding_validation_id = require_opaque_reference(
        args.superseding_validation_id, "C06_SUPERSEDING_VALIDATION_ID_INVALID"
    )
    boss_decision_ref = require_opaque_reference(
        args.boss_decision_ref, "C06_BOSS_DECISION_REFERENCE_INVALID"
    )
    if conflict_validation_id == superseding_validation_id:
        raise LedgerError("C06_SUPERSEDING_VALIDATION_MUST_DIFFER")

    ledger = load_ledger(data_root, project_id)
    ensure_task(ledger, task_id)
    adjudication = ledger["adjudications"].get(boss_decision_ref)
    if (
        not isinstance(adjudication, dict)
        or adjudication.get("outcome") != "BOSS_DECISION"
        or adjudication.get("requestRef") != superseding_validation_id
    ):
        raise LedgerError("C06_HARD_STOP_BOSS_DECISION_UNVERIFIED")

    decision_path = (
        data_root
        / "handover-validations"
        / project_id
        / superseding_validation_id
        / "validation-decision.json"
    )
    try:
        decision = json.loads(decision_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise LedgerError("C06_SUPERSEDING_VALIDATION_DECISION_UNREADABLE")
    exception_finalization_path = (
        data_root / "handover-validations" / project_id / superseding_validation_id / "boss-finalization.json"
    )
    try:
        exception_finalization = json.loads(exception_finalization_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        exception_finalization = {}
    accepted_historical_before_snapshot = (
        decision.get("outcome") == "NEEDS_REVIEW"
        and exception_finalization.get("bossDecision") == "APPROVED"
        and exception_finalization.get("bossDecisionRef") == boss_decision_ref
        and exception_finalization.get("bossException") == {
            "kind": "HISTORICAL_BEFORE_SNAPSHOT_UNRECOVERABLE", "accepted": True
        }
    )
    if (
        not isinstance(decision, dict)
        or decision.get("recordType") != "C06_VALIDATION_DECISION"
        or decision.get("validationId") != superseding_validation_id
        or decision.get("taskId") != task_id
        or (decision.get("outcome") != "PASS_PENDING_BOSS_APPROVAL" and not accepted_historical_before_snapshot)
    ):
        raise LedgerError("C06_SUPERSEDING_VALIDATION_NOT_PASSED")

    matches = [
        stop for stop in ledger["hardStops"]
        if stop.get("code") == "C06_VALIDATION_CONFLICT"
        and stop.get("taskId") == task_id
        and stop.get("validationId") == conflict_validation_id
    ]
    if len(matches) != 1:
        raise LedgerError("C06_ACTIVE_CONFLICT_HARD_STOP_NOT_UNIQUE")

    def mutate(after: Dict[str, Any]) -> None:
        active = after["hardStops"]
        indexes = [
            index for index, stop in enumerate(active)
            if stop.get("code") == "C06_VALIDATION_CONFLICT"
            and stop.get("taskId") == task_id
            and stop.get("validationId") == conflict_validation_id
        ]
        if len(indexes) != 1:
            raise LedgerError("C06_ACTIVE_CONFLICT_HARD_STOP_NOT_UNIQUE")
        original = active.pop(indexes[0])
        resolved = {
            "resolutionId": f"resolved-{uuid.uuid4().hex[:12]}",
            "resolutionKind": "BOSS_ACCEPTED_AS_HISTORICAL_AFTER_SUPERSEDING_PASS",
            "resolvedAt": utc_now(),
            "resolvedBy": CENTRAL_WRITER,
            "bossDecisionRef": boss_decision_ref,
            "supersedingValidationId": superseding_validation_id,
            "originalHardStop": original,
        }
        after.setdefault("resolvedHardStops", []).append(resolved)
        after["tasks"][task_id]["history"].append({
            "at": resolved["resolvedAt"],
            "event": "C06_HARD_STOP_RESOLVED_AS_HISTORICAL",
            "conflictValidationId": conflict_validation_id,
            "supersedingValidationId": superseding_validation_id,
            "bossDecisionRef": boss_decision_ref,
            "by": CENTRAL_WRITER,
        })

    result = commit_mutation(
        data_root,
        project_id,
        ledger,
        writer_id,
        "RESOLVE_C06_VALIDATION_CONFLICT_AS_HISTORICAL",
        {
            "taskId": task_id,
            "conflictValidationId": conflict_validation_id,
            "supersedingValidationId": superseding_validation_id,
            "bossDecisionRef": boss_decision_ref,
        },
        mutate,
        caller_thread_ref=args.caller_thread_ref,
    )
    result["resolvedHardStopCount"] = len(
        load_ledger(data_root, project_id).get("resolvedHardStops", [])
    )
    return result, 0


def release_done_task_occupancy(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    """Release only claims owned by a DONE task with an intact approved C06 finalization."""
    data_root = load_data_root(args)
    project_id = require_pattern(args.project_id, PROJECT_ID_PATTERN, "PROJECT_ID_INVALID")
    writer_id = require_writer(args.writer_id)
    task_id = require_pattern(args.task_id, TASK_ID_PATTERN, "TASK_ID_INVALID")
    validation_id = require_opaque_reference(args.validation_id, "C06_VALIDATION_ID_INVALID")
    boss_decision_ref = require_opaque_reference(
        args.boss_decision_ref, "C06_BOSS_DECISION_REFERENCE_INVALID"
    )
    ledger = load_ledger(data_root, project_id)
    task = ensure_task(ledger, task_id)
    if task.get("status") != "DONE":
        raise LedgerError("OCCUPANCY_RELEASE_REQUIRES_DONE_TASK")
    if any(stop.get("taskId") == task_id for stop in ledger["hardStops"]):
        raise LedgerError("OCCUPANCY_RELEASE_TASK_HAS_ACTIVE_HARD_STOP")

    validation_directory = data_root / "handover-validations" / project_id / validation_id
    finalization_path = validation_directory / "boss-finalization.json"
    finalization_receipt_path = validation_directory / "receipts" / "receipt-000001-finalize.json"
    try:
        finalization = json.loads(finalization_path.read_text(encoding="utf-8"))
        finalization_receipt = json.loads(finalization_receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise LedgerError("OCCUPANCY_RELEASE_C06_FINALIZATION_UNREADABLE")
    if (
        not isinstance(finalization, dict)
        or finalization.get("recordType") != "C06_BOSS_FINALIZATION"
        or finalization.get("validationId") != validation_id
        or finalization.get("taskId") != task_id
        or finalization.get("bossDecision") != "APPROVED"
        or finalization.get("bossDecisionRef") != boss_decision_ref
        or finalization.get("executionBoundary", {}).get("doneRecorded") is not True
        or finalization.get("ledgerMutation", {}).get("taskStatusAfter") != "DONE"
    ):
        raise LedgerError("OCCUPANCY_RELEASE_C06_FINALIZATION_INVALID")
    if (
        not isinstance(finalization_receipt, dict)
        or finalization_receipt.get("recordType") != "C06_IMMUTABLE_FINALIZATION_RECEIPT"
        or finalization_receipt.get("validationId") != validation_id
        or finalization_receipt.get("afterFinalization") != finalization
        or finalization_receipt.get("afterFinalizationDigest") != canonical_digest(finalization)
    ):
        raise LedgerError("OCCUPANCY_RELEASE_C06_FINALIZATION_RECEIPT_INVALID")

    matching = [
        {"objectKey": key, "claim": claim}
        for key, occupancy in ledger["objectOccupancies"].items()
        for claim in occupancy.get("claims", [])
        if claim.get("ownerType") == "task" and claim.get("ownerId") == task_id
    ]
    prior_releases = [
        item for item in ledger.get("releasedOccupancies", [])
        if item.get("taskId") == task_id and item.get("validationId") == validation_id
    ]
    if not matching:
        if len(prior_releases) == 1:
            return {
                "status": "IDEMPOTENT_DONE_TASK_OCCUPANCY_ALREADY_RELEASED",
                "taskId": task_id,
                "validationId": validation_id,
                "releasedClaimCount": prior_releases[0]["releasedClaimCount"],
                "occupancyReleased": True,
                "writePerformed": False,
            }, 0
        raise LedgerError("DONE_TASK_HAS_NO_RELEASABLE_OCCUPANCY")
    if prior_releases:
        raise LedgerError("DONE_TASK_OCCUPANCY_RELEASE_HISTORY_CONFLICT")

    def mutate(after: Dict[str, Any]) -> None:
        released_at = utc_now()
        for key in list(after["objectOccupancies"]):
            occupancy = after["objectOccupancies"][key]
            occupancy["claims"] = [
                claim for claim in occupancy.get("claims", [])
                if not (claim.get("ownerType") == "task" and claim.get("ownerId") == task_id)
            ]
            if not occupancy["claims"]:
                del after["objectOccupancies"][key]
            else:
                occupancy["status"] = "CONFLICT" if len(occupancy["claims"]) > 1 else "CLAIMED"
        after.setdefault("releasedOccupancies", []).append({
            "releaseId": f"released-occupancy-{uuid.uuid4().hex[:12]}",
            "releasedAt": released_at,
            "releasedBy": CENTRAL_WRITER,
            "taskId": task_id,
            "validationId": validation_id,
            "bossDecisionRef": boss_decision_ref,
            "releasedClaimCount": len(matching),
            "releasedClaims": matching,
        })
        after["tasks"][task_id]["history"].append({
            "at": released_at,
            "event": "DONE_TASK_OCCUPANCY_RELEASED",
            "validationId": validation_id,
            "bossDecisionRef": boss_decision_ref,
            "releasedClaimCount": len(matching),
            "by": CENTRAL_WRITER,
        })

    result = commit_mutation(
        data_root,
        project_id,
        ledger,
        writer_id,
        "RELEASE_DONE_TASK_OCCUPANCY",
        {
            "taskId": task_id,
            "validationId": validation_id,
            "bossDecisionRef": boss_decision_ref,
            "releasedClaimCount": len(matching),
        },
        mutate,
        caller_thread_ref=args.caller_thread_ref,
    )
    result.update({
        "taskId": task_id,
        "validationId": validation_id,
        "releasedClaimCount": len(matching),
        "occupancyReleased": True,
    })
    return result, 0


def register_skill(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    skill_id = require_opaque_reference(args.skill_id, "SKILL_ID_INVALID")
    source_version = require_opaque_reference(args.source_version, "SKILL_SOURCE_VERSION_INVALID")
    classification = require_text(args.classification, "SKILL_CLASSIFICATION_INVALID", 20).upper()
    if classification not in {"CORE", "OPTIONAL"}:
        raise LedgerError("SKILL_CLASSIFICATION_INVALID")

    def mutate(ledger: Dict[str, Any]) -> None:
        if skill_id in ledger["skills"]:
            raise LedgerError("SKILL_ID_ALREADY_EXISTS")
        ledger["skills"][skill_id] = {"skillId": skill_id, "sourceVersion": source_version, "classification": classification, "status": "REGISTERED", "registeredAt": utc_now()}

    return mutate_ledger(args, "REGISTER_SKILL", {"skillId": skill_id, "classification": classification}, mutate)


def verify_ledger(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args)
    project_id = require_pattern(args.project_id, PROJECT_ID_PATTERN, "PROJECT_ID_INVALID")
    ledger = load_ledger(data_root, project_id)
    receipt_ids = ledger["receiptIds"]
    previous_digest = "NONE"
    for position, receipt_id in enumerate(receipt_ids):
        receipt_path = receipt_directory(data_root, project_id) / f"{receipt_id}.json"
        if not receipt_path.is_file():
            raise LedgerError("IMMUTABLE_RECEIPT_MISSING")
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise LedgerError("IMMUTABLE_RECEIPT_INVALID_JSON")
        snapshot = receipt.get("afterLedger")
        if (
            receipt.get("recordType") != "C03_IMMUTABLE_LEDGER_RECEIPT"
            or receipt.get("receiptId") != receipt_id
            or receipt.get("beforeLedgerDigest") != previous_digest
            or not isinstance(snapshot, dict)
            or receipt.get("afterLedgerDigest") != canonical_digest(snapshot)
            or snapshot.get("revision") != position
            or snapshot.get("receiptIds") != receipt_ids[: position + 1]
            or snapshot.get("latestReceiptId") != receipt_id
        ):
            raise LedgerError("IMMUTABLE_RECEIPT_CHAIN_INVALID")
        previous_digest = receipt["afterLedgerDigest"]
    if not receipt_ids or canonical_digest(ledger) != previous_digest:
        raise LedgerError("LEDGER_AND_RECEIPT_CHAIN_MISMATCH")
    return {
        "status": "LEDGER_INTEGRITY_VERIFIED",
        "projectId": project_id,
        "revision": ledger["revision"],
        "receiptCount": len(receipt_ids),
        "taskCount": len(ledger["tasks"]),
        "windowCount": len(ledger["windows"]),
        "subAgentCount": len(ledger["subAgents"]),
        "hardStops": ledger["hardStops"],
        "resolvedHardStopCount": len(ledger.get("resolvedHardStops", [])),
        "releasedOccupancyCount": len(ledger.get("releasedOccupancies", [])),
        "writePerformed": False,
    }, 0


def read_summary(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args)
    project_id = require_pattern(args.project_id, PROJECT_ID_PATTERN, "PROJECT_ID_INVALID")
    ledger = load_ledger(data_root, project_id)
    from project_progress import business_progress
    return {
        "businessProgress": business_progress(data_root, project_id, ledger),
        "status": "LEDGER_READ",
        "projectId": project_id,
        "revision": ledger["revision"],
        "tasks": {task_id: task["status"] for task_id, task in ledger["tasks"].items()},
        "externalWaits": {task_id: active_external_waits(task) for task_id, task in ledger["tasks"].items()
                          if active_external_waits(task)},
        "deliveryFeedback": {task_id: list(task["deliveryFeedback"].values()) for task_id, task in ledger["tasks"].items()
                             if task.get("deliveryFeedback")},
        "currentDeliveryState": {task_id: ("REPORTED_ISSUE_REQUIRES_REVIEW" if any(
            item["status"] != "CLOSED_VERIFIED" for item in task.get("deliveryFeedback", {}).values())
            else "REPORTED_ISSUES_CLOSED") for task_id, task in ledger["tasks"].items()
                                 if task.get("deliveryFeedback")},
        "windowCount": len(ledger["windows"]),
        "subAgentCount": len(ledger["subAgents"]),
        "hardStops": ledger["hardStops"],
        "resolvedHardStopCount": len(ledger.get("resolvedHardStops", [])),
        "releasedOccupancyCount": len(ledger.get("releasedOccupancies", [])),
        "latestReceiptId": ledger["latestReceiptId"],
        "writePerformed": False,
    }, 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="C03 Codex-module engineering ledger manager")
    root_source = parser.add_mutually_exclusive_group(required=True)
    root_source.add_argument("--data-root", help="Private data directory outside every Git worktree")
    root_source.add_argument("--config", help="Private module config JSON containing storage.userDataRoot")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--writer-id", help=f"The only accepted writer is {CENTRAL_WRITER}")
    parser.add_argument(
        "--caller-thread-ref",
        help="Native Codex task id; required for writes after C08 CURRENT_CENTRAL routing is active",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    wait = commands.add_parser("record-wait")
    for field in ("task-id", "wait-id", "owner-ref", "condition-ref", "reason-ref"):
        wait.add_argument("--" + field, required=True)
    resolve = commands.add_parser("resolve-wait")
    resolve.add_argument("--task-id", required=True)
    resolve.add_argument("--resolution", required=True)
    feedback = commands.add_parser("record-feedback")
    for field in ("task-id", "feedback-id", "validation-id", "issue-ref", "affected-ref"):
        feedback.add_argument("--" + field, required=True)
    feedback.add_argument("--repair-task-id")
    repair_link = commands.add_parser("link-feedback-repair")
    for field in ("task-id", "feedback-id", "repair-task-id"):
        repair_link.add_argument("--" + field, required=True)
    feedback_close = commands.add_parser("close-feedback")
    for field in ("task-id", "feedback-id", "validation-id"):
        feedback_close.add_argument("--" + field, required=True)

    initialize = commands.add_parser("initialize")
    action = initialize.add_mutually_exclusive_group(required=True)
    action.add_argument("--dry-run", action="store_true")
    action.add_argument("--apply", action="store_true")

    add = commands.add_parser("add-task")
    add.add_argument("--task-id", required=True)
    add.add_argument("--title", required=True)
    add.add_argument("--business-goal", required=True)
    add.add_argument("--plan-ref", required=True)
    add.add_argument("--outcome-id", action="append")

    for name in ("import-outline", "revise-outline"):
        outline = commands.add_parser(name)
        outline.add_argument("--handoff", required=True)
        outline.add_argument("--outline-file", required=True)
        outline.add_argument("--boss-approval-ref", required=True)
        if name == "revise-outline":
            outline.add_argument("--expected-digest", required=True)
            outline.add_argument("--change-id", required=True)

    for name in ('reconcile-outline', 'bind-wait-followup', 'retarget-feedback-repair'):
        lifecycle = commands.add_parser(name)
        lifecycle.add_argument('--task-id', required=True)
        lifecycle.add_argument('--proof', required=True)

    transition = commands.add_parser("transition-task")
    transition.add_argument("--task-id", required=True)
    transition.add_argument("--to-status", required=True)
    transition.add_argument("--reason", required=True)

    correction = commands.add_parser("record-correction")
    correction.add_argument("--task-id", required=True)
    correction.add_argument("--correction-id", required=True)
    correction.add_argument("--expected-revision", type=int, required=True)
    correction.add_argument("--instruction-ref", required=True)
    correction.add_argument("--reason-ref", required=True)
    correction.add_argument("--boss-decision-ref", required=True)
    correction.add_argument("--retain-ref", action="append", required=True)
    correction.add_argument("--supersede-ref", action="append", required=True)
    correction_read = commands.add_parser("read-correction")
    correction_read.add_argument("--task-id", required=True)
    correction_verify = commands.add_parser("verify-correction")
    correction_verify.add_argument("--task-id", required=True)
    correction_verify.add_argument("--verification", required=True)

    completion = commands.add_parser("record-completion-signal")
    completion.add_argument("--task-id", required=True)
    completion.add_argument("--signal-id", required=True)

    window = commands.add_parser("register-window")
    window.add_argument("--window-id", required=True)
    window.add_argument("--task-id", required=True)
    window.add_argument("--context-mode", required=True)
    window.add_argument("--runtime-thread-ref")
    window.add_argument("--runtime-thread-evidence-ref")
    window.add_argument("--runtime-model")
    window.add_argument("--runtime-model-evidence-ref")
    window.add_argument("--runtime-permission-profile")
    window.add_argument("--runtime-permission-evidence-ref")
    window.add_argument("--runtime-writable-root", action="append")
    window.add_argument("--governance-data-root-access")

    sub_agent = commands.add_parser("register-sub-agent")
    sub_agent.add_argument("--sub-agent-id", required=True)
    sub_agent.add_argument("--window-id", required=True)
    sub_agent.add_argument("--role", required=True)

    claim = commands.add_parser("claim-object")
    claim.add_argument("--object-key", required=True)
    claim.add_argument("--owner-type", required=True, choices=["task", "window", "sub-agent"])
    claim.add_argument("--owner-id", required=True)
    claim.add_argument("--intent", required=True, choices=["READ", "WRITE", "read", "write"])

    evidence = commands.add_parser("record-evidence")
    evidence.add_argument("--evidence-id", required=True)
    evidence.add_argument("--reference", required=True)
    evidence.add_argument("--category", required=True)
    evidence.add_argument("--summary", required=True)

    adjudication = commands.add_parser("record-adjudication")
    adjudication.add_argument("--adjudication-id", required=True)
    adjudication.add_argument("--request-ref", required=True)
    adjudication.add_argument("--outcome", required=True)

    resolve_c06 = commands.add_parser("resolve-c06-validation-conflict")
    resolve_c06.add_argument("--task-id", required=True)
    resolve_c06.add_argument("--conflict-validation-id", required=True)
    resolve_c06.add_argument("--superseding-validation-id", required=True)
    resolve_c06.add_argument("--boss-decision-ref", required=True)

    release_done = commands.add_parser("release-done-task-occupancy")
    release_done.add_argument("--task-id", required=True)
    release_done.add_argument("--validation-id", required=True)
    release_done.add_argument("--boss-decision-ref", required=True)

    cancel_unstarted = commands.add_parser("cancel-unstarted-task")
    cancel_unstarted.add_argument("--task-id", required=True)
    cancel_unstarted.add_argument("--boss-decision-ref", required=True)
    cancel_unstarted.add_argument("--reason", required=True)

    skill = commands.add_parser("register-skill")
    skill.add_argument("--skill-id", required=True)
    skill.add_argument("--source-version", required=True)
    skill.add_argument("--classification", required=True)

    commands.add_parser("verify")
    commands.add_parser("read-summary")
    return parser.parse_args()


def dispatch(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    if args.command in {'reconcile-outline', 'bind-wait-followup', 'retarget-feedback-repair'}:
        from task_lifecycle import operate
        return operate(args)
    handlers = {
        "initialize": initialize_ledger,
        "record-wait": record_wait,
        "resolve-wait": resolve_wait,
        "record-feedback": record_feedback,
        "link-feedback-repair": link_feedback_repair,
        "close-feedback": close_feedback,
        "add-task": add_task,
        "import-outline": import_outline,
        "revise-outline": import_outline,
        "transition-task": transition_task,
        "record-correction": record_correction,
        "read-correction": read_correction,
        "verify-correction": verify_correction,
        "record-completion-signal": record_completion_signal,
        "register-window": register_window,
        "register-sub-agent": register_sub_agent,
        "claim-object": claim_object,
        "record-evidence": record_evidence,
        "record-adjudication": record_adjudication,
        "resolve-c06-validation-conflict": resolve_c06_validation_conflict,
        "release-done-task-occupancy": release_done_task_occupancy,
        "cancel-unstarted-task": cancel_unstarted_task,
        "register-skill": register_skill,
        "verify": verify_ledger,
        "read-summary": read_summary,
    }
    return handlers[args.command](args)


def main() -> int:
    args = parse_args()
    try:
        result, exit_code = dispatch(args)
        print_result(result)
        return exit_code
    except (LedgerError, C02Error) as error:
        print_result({
            "status": "REFUSED",
            "reason": str(error),
            "writePerformed": False,
            "message": "工程总账操作已停止，没有推进任务或修改业务对象。",
        })
        return 2


if __name__ == "__main__":
    sys.modules['ledger_manager'] = sys.modules[__name__]
    sys.exit(main())
