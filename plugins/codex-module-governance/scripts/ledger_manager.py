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


SCHEMA_VERSION = "0.3.0"
MODULE = "codex"
CENTRAL_WRITER = "codex-module-central"
LEDGERS_DIRECTORY = Path("module-ledgers")
LEDGER_FILENAME = "ledger.json"
RECEIPTS_DIRECTORY = "receipts"
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
    try:
        write_json_exclusive(lock_path, {
            "recordType": "C03_LEDGER_OPERATION_LOCK",
            "projectId": project_id,
            "createdAt": utc_now(),
            "pid": os.getpid(),
        })
    except LedgerError:
        raise LedgerError("LEDGER_OPERATION_LOCK_PRESENT")
    try:
        yield
    finally:
        if lock_path.exists():
            lock_path.unlink()


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
) -> Dict[str, Any]:
    recovery_state = ledger.get("recovery", {}).get("state", "UNKNOWN")
    if recovery_state not in {"NORMAL", "CLOSED"} and not operation.startswith("C08_"):
        raise LedgerError("LEDGER_RECOVERY_FREEZE_ACTIVE")
    before = copy.deepcopy(ledger)
    after = copy.deepcopy(ledger)
    mutate(after)
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


def ensure_task(ledger: Dict[str, Any], task_id: str) -> Dict[str, Any]:
    task = ledger["tasks"].get(task_id)
    if not isinstance(task, dict):
        raise LedgerError("TASK_NOT_FOUND")
    return task


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
        return commit_mutation(data_root, project_id, ledger, writer_id, operation, change_summary, mutate), 0


def add_task(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    task_id = require_pattern(args.task_id, TASK_ID_PATTERN, "TASK_ID_INVALID")
    title = require_text(args.title, "TASK_TITLE_INVALID", 160)
    business_goal = require_text(args.business_goal, "TASK_BUSINESS_GOAL_INVALID", 1200)
    plan_ref = require_opaque_reference(args.plan_ref, "PLAN_REFERENCE_INVALID")

    def mutate(ledger: Dict[str, Any]) -> None:
        if task_id in ledger["tasks"]:
            raise LedgerError("TASK_ID_ALREADY_EXISTS")
        ledger["tasks"][task_id] = {
            "taskId": task_id,
            "title": title,
            "businessGoal": business_goal,
            "planRef": plan_ref,
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

        return commit_mutation(data_root, project_id, ledger, writer_id, "RECORD_COMPLETION_SIGNAL", {"taskId": task_id, "signalId": signal_id, "toStatus": "NEEDS_REVIEW"}, mutate), 0


def register_window(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    window_id = require_pattern(args.window_id, TASK_ID_PATTERN, "WINDOW_ID_INVALID")
    task_id = require_pattern(args.task_id, TASK_ID_PATTERN, "TASK_ID_INVALID")
    context_mode = require_text(args.context_mode, "WINDOW_CONTEXT_MODE_INVALID", 20).upper()
    if context_mode not in {"NEW", "REUSED"}:
        raise LedgerError("WINDOW_CONTEXT_MODE_INVALID")

    def mutate(ledger: Dict[str, Any]) -> None:
        if window_id in ledger["windows"]:
            raise LedgerError("WINDOW_ID_ALREADY_EXISTS")
        task = ensure_task(ledger, task_id)
        if task["status"] in {"CANCELLED", "DONE"}:
            raise LedgerError("WINDOW_CANNOT_ATTACH_TO_TERMINAL_TASK")
        ledger["windows"][window_id] = {
            "windowId": window_id,
            "taskId": task_id,
            "model": "gpt-5.6-terra",
            "contextMode": context_mode,
            "status": "REGISTERED",
            "registeredAt": utc_now(),
        }

    return mutate_ledger(args, "REGISTER_TASK_WINDOW", {"windowId": window_id, "taskId": task_id, "model": "gpt-5.6-terra"}, mutate)


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
            "model": "gpt-5.6-terra",
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
        "writePerformed": False,
    }, 0


def read_summary(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args)
    project_id = require_pattern(args.project_id, PROJECT_ID_PATTERN, "PROJECT_ID_INVALID")
    ledger = load_ledger(data_root, project_id)
    return {
        "status": "LEDGER_READ",
        "projectId": project_id,
        "revision": ledger["revision"],
        "tasks": {task_id: task["status"] for task_id, task in ledger["tasks"].items()},
        "windowCount": len(ledger["windows"]),
        "subAgentCount": len(ledger["subAgents"]),
        "hardStops": ledger["hardStops"],
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
    commands = parser.add_subparsers(dest="command", required=True)

    initialize = commands.add_parser("initialize")
    action = initialize.add_mutually_exclusive_group(required=True)
    action.add_argument("--dry-run", action="store_true")
    action.add_argument("--apply", action="store_true")

    add = commands.add_parser("add-task")
    add.add_argument("--task-id", required=True)
    add.add_argument("--title", required=True)
    add.add_argument("--business-goal", required=True)
    add.add_argument("--plan-ref", required=True)

    transition = commands.add_parser("transition-task")
    transition.add_argument("--task-id", required=True)
    transition.add_argument("--to-status", required=True)
    transition.add_argument("--reason", required=True)

    completion = commands.add_parser("record-completion-signal")
    completion.add_argument("--task-id", required=True)
    completion.add_argument("--signal-id", required=True)

    window = commands.add_parser("register-window")
    window.add_argument("--window-id", required=True)
    window.add_argument("--task-id", required=True)
    window.add_argument("--context-mode", required=True)

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

    skill = commands.add_parser("register-skill")
    skill.add_argument("--skill-id", required=True)
    skill.add_argument("--source-version", required=True)
    skill.add_argument("--classification", required=True)

    commands.add_parser("verify")
    commands.add_parser("read-summary")
    return parser.parse_args()


def dispatch(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    handlers = {
        "initialize": initialize_ledger,
        "add-task": add_task,
        "transition-task": transition_task,
        "record-completion-signal": record_completion_signal,
        "register-window": register_window,
        "register-sub-agent": register_sub_agent,
        "claim-object": claim_object,
        "record-evidence": record_evidence,
        "record-adjudication": record_adjudication,
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
    sys.exit(main())
