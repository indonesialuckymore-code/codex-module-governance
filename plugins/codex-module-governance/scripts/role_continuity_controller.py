#!/usr/bin/env python3
"""C08 role continuity and durable task-event inbox.

This controller keeps logical roles stable while Codex tasks are replaced. It does
not execute business work and it never treats chat delivery as the source of truth.
"""

from __future__ import annotations

import argparse
import copy
import inspect
from runtime_model_policy import valid_model
import json
import os
import re
import sys
import uuid
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
    commit_mutation,
    ledger_lock,
    load_ledger,
    record_completion_signal,
    verify_ledger,
    window_current_task_id,
)


SCHEMA_VERSION = "0.15.0"
RETURN_SCHEMA_VERSION = "0.18.0"
ROLES = {"CURRENT_CENTRAL", "CURRENT_ADJUDICATION"}
ROLE_MODELS = {"CURRENT_CENTRAL": "gpt-5.6-sol", "CURRENT_ADJUDICATION": "gpt-5.6-sol"}
EVENT_TYPES = {"IN_PROGRESS", "BLOCKED_FOR_DECISION", "DECISION_APPLIED", "READY_FOR_VALIDATION", "SUB_AGENT_APPEND_REQUEST", "TASK_HANDBACK_QUEUED"}
ROOT = Path("role-continuity")
EVENT_ROOT = Path("task-events")
RETURN_ROOT = Path("return-inbox")
RETURN_VALIDATOR_MODEL = "gpt-5.6-sol"
SAFE_TEXT = re.compile(r"^[^\r\n]{1,500}$")


class ContinuityError(Exception):
    """Safe refusal that does not change business state."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def print_result(value: Dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def exact(value: Any, keys: set[str], error: str) -> Dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ContinuityError(error)
    return value


def ref(value: Any, error: str) -> str:
    if not isinstance(value, str) or not REFERENCE_PATTERN.fullmatch(value.strip()):
        raise ContinuityError(error)
    return value.strip()


def task_ref(value: Any, error: str) -> str:
    if not isinstance(value, str) or not TASK_ID_PATTERN.fullmatch(value.strip()):
        raise ContinuityError(error)
    return value.strip()


def project_ref(value: Any) -> str:
    if not isinstance(value, str) or not PROJECT_ID_PATTERN.fullmatch(value.strip()):
        raise ContinuityError("C08C_PROJECT_ID_INVALID")
    return value.strip()


def role_ref(value: Any) -> str:
    candidate = str(value).strip().upper()
    if candidate not in ROLES:
        raise ContinuityError("C08C_ROLE_INVALID")
    return candidate


def refs(value: Any, error: str, *, allow_empty: bool = False, maximum: int = 80) -> List[str]:
    if not isinstance(value, list) or len(value) > maximum or (not allow_empty and not value):
        raise ContinuityError(error)
    normalized = [ref(item, error) for item in value]
    if len(normalized) != len(set(normalized)):
        raise ContinuityError(error)
    return normalized


def safe_text(value: Any, error: str, maximum: int = 500) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum or not SAFE_TEXT.fullmatch(value.strip()):
        raise ContinuityError(error)
    return value.strip()


def private_json(raw_path: str, data_root: Path, error: str) -> Tuple[Dict[str, Any], str]:
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file() or not is_within(path, data_root):
        raise ContinuityError("C08C_PRIVATE_INPUT_REQUIRED")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ContinuityError(error)
    if not isinstance(value, dict):
        raise ContinuityError(error)
    return value, canonical_digest(value)


def project_root(data_root: Path, project_id: str) -> Path:
    return data_root / ROOT / project_id


def routing_path(data_root: Path, project_id: str) -> Path:
    return project_root(data_root, project_id) / "routing.json"


def routing_receipts(data_root: Path, project_id: str) -> Path:
    return project_root(data_root, project_id) / "receipts"


def handover_root(data_root: Path, project_id: str, handover_id: str) -> Path:
    return project_root(data_root, project_id) / "handovers" / handover_id


def event_path(data_root: Path, project_id: str, event_id: str) -> Path:
    return data_root / EVENT_ROOT / project_id / "inbox" / f"{event_id}.json"


def event_receipt_path(data_root: Path, project_id: str, event_id: str) -> Path:
    return data_root / EVENT_ROOT / project_id / "receipts" / f"{event_id}.json"


def acknowledgement_path(data_root: Path, project_id: str, event_id: str) -> Path:
    return data_root / EVENT_ROOT / project_id / "acknowledged" / f"{event_id}.json"


def return_root(data_root: Path, project_id: str) -> Path:
    return data_root / RETURN_ROOT / project_id


def return_ticket_root(data_root: Path, project_id: str, ticket_id: str) -> Path:
    return return_root(data_root, project_id) / "tickets" / ticket_id


def return_submission_path(data_root: Path, project_id: str, ticket_id: str) -> Path:
    return return_ticket_root(data_root, project_id, ticket_id) / "submission.json"


def return_submission_receipt_path(data_root: Path, project_id: str, ticket_id: str) -> Path:
    return return_ticket_root(data_root, project_id, ticket_id) / "receipt-000000-submission.json"


def return_delivery_path(data_root: Path, project_id: str, ticket_id: str) -> Path:
    return return_ticket_root(data_root, project_id, ticket_id) / "receipt-000001-delivery.json"


def return_admission_path(data_root: Path, project_id: str, ticket_id: str) -> Path:
    return return_ticket_root(data_root, project_id, ticket_id) / "receipt-000002-admission.json"


def return_reservation_path(data_root: Path, project_id: str, ticket_id: str) -> Path:
    return return_ticket_root(data_root, project_id, ticket_id) / "receipt-000003-validation-reservation.json"


def return_validation_path(data_root: Path, project_id: str, ticket_id: str) -> Path:
    return return_ticket_root(data_root, project_id, ticket_id) / "receipt-000004-validation-result.json"


def return_validation_abort_path(data_root: Path, project_id: str, ticket_id: str) -> Path:
    return return_ticket_root(data_root, project_id, ticket_id) / "receipt-000004-validation-aborted.json"


def return_policy_path(data_root: Path, project_id: str, policy_id: str) -> Path:
    return return_root(data_root, project_id) / "policies" / f"{policy_id}.json"


def return_policy_receipt_path(data_root: Path, project_id: str, policy_id: str) -> Path:
    return return_root(data_root, project_id) / "policies" / f"{policy_id}.receipt.json"


def return_ticket_ref(value: Any, error: str) -> str:
    candidate = ref(value, error)
    if len(candidate) > 96:
        raise ContinuityError(error)
    return candidate


def return_event_id(ticket_id: str) -> str:
    return return_ticket_ref(f"return-event-{ticket_id}", "C08C_RETURN_EVENT_ID_INVALID")


def read_json(path: Path, error: str) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ContinuityError(error)
    if not isinstance(value, dict):
        raise ContinuityError(error)
    return value


def write_exclusive(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as output:
            output.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    except FileExistsError:
        raise ContinuityError("C08C_IMMUTABLE_TARGET_ALREADY_EXISTS")


def replace_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("x", encoding="utf-8") as output:
            output.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def continuity_lock(data_root: Path, project_id: str) -> Iterator[None]:
    path = project_root(data_root, project_id) / ".continuity.lock"
    from process_lock import process_lock
    with process_lock(path, lambda: write_exclusive(path, {"pid": os.getpid(), "createdAt": utc_now()}),
                      ContinuityError, "C08C_OPERATION_LOCK_PRESENT"):
        yield


def require_writer(writer_id: Optional[str]) -> None:
    if writer_id != CENTRAL_WRITER:
        raise ContinuityError("C08C_WRITER_NOT_AUTHORIZED")


def verified_ledger(data_root: Path, project_id: str) -> Dict[str, Any]:
    try:
        _, code = verify_ledger(argparse.Namespace(data_root=str(data_root), config=None, project_id=project_id))
        if code != 0:
            raise ContinuityError("C08C_LEDGER_INTEGRITY_UNVERIFIED")
        return load_ledger(data_root, project_id)
    except (LedgerError, C02Error) as error:
        raise ContinuityError(f"C08C_LEDGER_{error}")


def canonical_task_title(task: Dict[str, Any]) -> str:
    stored = task.get("canonicalTitle")
    if isinstance(stored, str) and stored.strip():
        return stored.strip()
    return f"{task['taskId']}｜{task['title']}"


def load_routing(data_root: Path, project_id: str) -> Dict[str, Any]:
    value = read_json(routing_path(data_root, project_id), "C08C_ROUTING_NOT_INITIALIZED")
    if value.get("schemaVersion") != SCHEMA_VERSION or value.get("recordType") != "C08_ROLE_ROUTING" or value.get("projectId") != project_id:
        raise ContinuityError("C08C_ROUTING_SCHEMA_INVALID")
    return value


def commit_routing(data_root: Path, project_id: str, before: Optional[Dict[str, Any]], operation: str, mutate) -> Tuple[Dict[str, Any], str]:
    after = copy.deepcopy(before) if before is not None else {
        "schemaVersion": SCHEMA_VERSION,
        "recordType": "C08_ROLE_ROUTING",
        "projectId": project_id,
        "revision": 0,
        "roles": {"CURRENT_CENTRAL": None, "CURRENT_ADJUDICATION": None},
        "receiptIds": [],
        "latestReceiptId": None,
    }
    mutate(after)
    revision = (before or {"revision": 0})["revision"] + 1
    receipt_id = f"receipt-{revision:06d}-routing"
    after["revision"] = revision
    after["receiptIds"] = [*((before or {}).get("receiptIds", [])), receipt_id]
    after["latestReceiptId"] = receipt_id
    receipt = {
        "schemaVersion": SCHEMA_VERSION,
        "recordType": "C08_IMMUTABLE_ROLE_ROUTING_RECEIPT",
        "receiptId": receipt_id,
        "createdAt": utc_now(),
        "projectId": project_id,
        "operation": operation,
        "beforeRoutingDigest": "NONE" if before is None else canonical_digest(before),
        "afterRoutingDigest": canonical_digest(after),
        "afterRouting": after,
    }
    receipt_file = routing_receipts(data_root, project_id) / f"{receipt_id}.json"
    if receipt_file.exists() and operation == 'ACTIVATE_ROLE_SUCCESSOR':
        pending = read_json(receipt_file, 'C08C_PENDING_ROUTING_RECEIPT_INVALID')
        saved = pending.get('afterRouting')
        def comparable(value):
            result = copy.deepcopy(value)
            for role in result.get('roles', {}).values():
                if isinstance(role, dict): role.pop('activatedAt', None)
            return result
        if (not isinstance(saved, dict) or pending.get('operation') != operation
                or pending.get('receiptId') != receipt_id or pending.get('projectId') != project_id
                or pending.get('schemaVersion') != SCHEMA_VERSION
                or pending.get('recordType') != 'C08_IMMUTABLE_ROLE_ROUTING_RECEIPT'
                or pending.get('beforeRoutingDigest') != receipt['beforeRoutingDigest']
                or pending.get('afterRoutingDigest') != canonical_digest(saved)
                or comparable(saved) != comparable(after)):
            raise ContinuityError('C08C_PENDING_ROUTING_RECEIPT_CONFLICT')
        after = saved  # Finish the same prepared commit, preserving its original time.
    else:
        write_exclusive(receipt_file, receipt)
    replace_json(routing_path(data_root, project_id), after)
    return after, receipt_id


def verify_routing(data_root: Path, project_id: str) -> Dict[str, Any]:
    routing = load_routing(data_root, project_id)
    previous = "NONE"
    previous_revision = 0
    for receipt_id in routing.get("receiptIds", []):
        receipt = read_json(routing_receipts(data_root, project_id) / f"{receipt_id}.json", "C08C_ROUTING_RECEIPT_MISSING")
        snapshot = receipt.get("afterRouting")
        if (
            receipt.get("schemaVersion") != SCHEMA_VERSION
            or receipt.get("recordType") != "C08_IMMUTABLE_ROLE_ROUTING_RECEIPT"
            or receipt.get("receiptId") != receipt_id
            or receipt.get("operation") not in {"INITIALIZE_ROLE_ROUTING", "ACTIVATE_ROLE_SUCCESSOR", "RECORD_ROLE_MODEL_READBACK"}
            or receipt.get("projectId") != project_id
            or receipt.get("beforeRoutingDigest") != previous
            or not isinstance(snapshot, dict)
            or snapshot.get("revision") != previous_revision + 1
            or receipt.get("afterRoutingDigest") != canonical_digest(snapshot)
            or snapshot.get("latestReceiptId") != receipt_id
        ):
            raise ContinuityError("C08C_ROUTING_RECEIPT_CHAIN_INVALID")
        previous = receipt["afterRoutingDigest"]
        previous_revision = snapshot["revision"]
    if not routing.get("receiptIds") or canonical_digest(routing) != previous:
        raise ContinuityError("C08C_ROUTING_AND_RECEIPTS_MISMATCH")
    active = [item for item in routing["roles"].values() if isinstance(item, dict) and item.get("status") == "ACTIVE"]
    if not isinstance(routing["roles"].get("CURRENT_CENTRAL"), dict) or len(active) not in {1, 2}:
        raise ContinuityError("C08C_ACTIVE_ROLE_INVARIANT_BROKEN")
    return routing


def initialize(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args); project_id = project_ref(args.project_id); require_writer(args.writer_id)
    verified_ledger(data_root, project_id)
    calling_thread = ref(args.calling_thread_ref, "C08C_CALLING_THREAD_REF_INVALID")
    central_thread = ref(args.central_thread_ref, "C08C_CENTRAL_THREAD_REF_INVALID")
    if central_thread != calling_thread:
        raise ContinuityError("C08C_INITIAL_CENTRAL_MUST_BE_CALLING_THREAD")
    runtime_project = ref(args.runtime_project_id, "C08C_RUNTIME_PROJECT_ID_INVALID")
    execution_map = ref(args.execution_map_ref, "C08C_EXECUTION_MAP_REF_INVALID")
    model = getattr(args, "model", None)
    if model is not None and not valid_model(model):
        raise ContinuityError("C08C_ROLE_MODEL_INVALID")
    with continuity_lock(data_root, project_id):
        if routing_path(data_root, project_id).exists():
            existing = verify_routing(data_root, project_id)
            current = existing["roles"]["CURRENT_CENTRAL"]
            if current.get("activeThreadRef") == central_thread and current.get("runtimeProjectId") == runtime_project:
                return {"status": "IDEMPOTENT_ROLE_ROUTING", "projectId": project_id, "revision": existing["revision"], "writePerformed": False}, 0
            raise ContinuityError("C08C_ROUTING_ALREADY_INITIALIZED")
        def mutate(after: Dict[str, Any]) -> None:
            after["roles"]["CURRENT_CENTRAL"] = {
                "generation": 1, "activeThreadRef": central_thread, "runtimeProjectId": runtime_project,
                "model": model or "UNVERIFIED", "status": "ACTIVE", "activatedAt": utc_now(),
                "previousThreadRefs": [], "source": "INITIAL_CENTRAL", "executionMapRef": execution_map,
            }
        routing, receipt_id = commit_routing(data_root, project_id, None, "INITIALIZE_ROLE_ROUTING", mutate)
    return {"status": "ROLE_ROUTING_INITIALIZED", "projectId": project_id, "revision": routing["revision"], "centralGeneration": 1, "receiptId": receipt_id, "writePerformed": True}, 0


def record_model(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    """Update an observed model without replacing the central or its authority."""
    data_root = load_data_root(args)
    project_id = project_ref(args.project_id)
    require_writer(args.writer_id)
    thread = ref(args.current_thread_ref, "C08C_THREAD_REF_INVALID")
    evidence = ref(args.evidence_ref, "C08C_MODEL_EVIDENCE_INVALID")
    if not valid_model(args.model) or evidence != thread:
        raise ContinuityError("C08C_MODEL_EVIDENCE_INVALID")
    with continuity_lock(data_root, project_id):
        before = verify_routing(data_root, project_id)
        active = current_role(before, "CURRENT_CENTRAL", thread)
        if active["runtimeProjectId"] != args.runtime_project_id:
            raise ContinuityError("C08C_RUNTIME_PROJECT_ID_MISMATCH")
        if active.get("model") == args.model and active.get("modelEvidenceRef") == evidence:
            return {"status": "IDEMPOTENT_ROLE_MODEL_READBACK", "writePerformed": False}, 0

        def mutate(after):
            after["roles"]["CURRENT_CENTRAL"].update(
                model=args.model, modelEvidenceRef=evidence, modelObservedAt=utc_now())

        routing, receipt_id = commit_routing(data_root, project_id, before, "RECORD_ROLE_MODEL_READBACK", mutate)
    return {"status": "ROLE_MODEL_RECORDED", "model": args.model,
            "centralGeneration": routing["roles"]["CURRENT_CENTRAL"]["generation"],
            "receiptId": receipt_id, "writePerformed": True, "roleSwitched": False, "ledgerUpdated": False}, 0


def validate_handover_request(value: Dict[str, Any], project_id: str) -> Dict[str, Any]:
    request = exact(value, {"handoverSchemaVersion", "recordType", "handoverId", "projectId", "role", "mode", "reasonRef", "executionMapRef", "contextRefs", "nextExpectedSignals"}, "C08C_HANDOVER_REQUEST_SCHEMA_INVALID")
    if request["handoverSchemaVersion"] != SCHEMA_VERSION or request["recordType"] != "C08_ROLE_HANDOVER_REQUEST" or request["projectId"] != project_id:
        raise ContinuityError("C08C_HANDOVER_REQUEST_SCHEMA_INVALID")
    mode = str(request["mode"]).strip().upper()
    if mode not in {"REPLACE_ACTIVE_ROLE", "BOOTSTRAP_ADJUDICATION_FROM_CENTRAL"}:
        raise ContinuityError("C08C_HANDOVER_MODE_INVALID")
    return {
        "handoverId": ref(request["handoverId"], "C08C_HANDOVER_ID_INVALID"),
        "projectId": project_id,
        "role": role_ref(request["role"]),
        "mode": mode,
        "reasonRef": ref(request["reasonRef"], "C08C_REASON_REF_INVALID"),
        "executionMapRef": ref(request["executionMapRef"], "C08C_EXECUTION_MAP_REF_INVALID"),
        "contextRefs": refs(request["contextRefs"], "C08C_CONTEXT_REFS_INVALID"),
        "nextExpectedSignals": refs(request["nextExpectedSignals"], "C08C_NEXT_SIGNALS_INVALID", allow_empty=True),
    }


def pending_event_ids(data_root: Path, project_id: str) -> List[str]:
    inbox = data_root / EVENT_ROOT / project_id / "inbox"
    if not inbox.exists():
        return []
    return sorted(path.stem for path in inbox.glob("*.json") if not acknowledgement_path(data_root, project_id, path.stem).exists())


def handover_ledger_snapshot(ledger: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "revision": ledger["revision"], "digest": canonical_digest(ledger),
        "taskStatus": {key: task.get("status") for key, task in sorted(ledger["tasks"].items())},
        "windows": [{**{key: value.get(key) for key in ("windowId", "taskId", "runtimeThreadRef", "generation",
            "status", "assignmentCount", "maxAssignments")}, "currentTaskId": window_current_task_id(value)}
            for value in ledger["windows"].values()],
        "hardStops": ledger["hardStops"],
        "sectionDigests": {key: canonical_digest(value) for key, value in ledger.items()},
        "taskContinuity": {key: {field: task[field] for field in ("outcomeIds", "outlineContractDigest", "outlineImpacts", "directionCorrections",
            "directionVerifications", "externalWaits", "deliveryFeedback") if field in task}
            for key, task in ledger["tasks"].items()},
    }


def handover_continuity_snapshot(root, project, ledger, include_messages=True):
    value = {"pendingTaskEventIds": pending_event_ids(root, project),
        "openAdjudicationRefs": sorted(key for key, item in ledger["adjudications"].items()
            if item.get("stage") != "BOSS_DECIDED")}
    if include_messages:
        from handover_automation import communication_snapshot
        value["communicationDigest"] = canonical_digest(communication_snapshot(root, project))
    return value


def refresh_handover(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args); project_id = project_ref(args.project_id); require_writer(args.writer_id)
    handover_id = ref(args.handover_id, "C08C_HANDOVER_ID_INVALID")
    revision_id = ref(args.revision_id, "C08C_HANDOVER_ID_INVALID")
    if revision_id == handover_id: raise ContinuityError("C08C_REFRESH_REQUIRES_NEW_ID")
    with continuity_lock(data_root, project_id), ledger_lock(data_root, project_id):
        routing = verify_routing(data_root, project_id)
        current_role(routing, "CURRENT_CENTRAL", args.current_thread_ref)
        base = load_handover(data_root, project_id, handover_id)
        source = routing["roles"].get(base["sourceRole"])
        if (not source or source["activeThreadRef"] != base["sourceThreadRef"]
                or source["generation"] != base["sourceGeneration"]
                or (base["mode"] == "BOOTSTRAP_ADJUDICATION_FROM_CENTRAL"
                    and routing["roles"].get(base["role"]) is not None)):
            raise ContinuityError("C08C_HANDOVER_SOURCE_CHANGED")
        ledger = verified_ledger(data_root, project_id)
        snapshot = handover_ledger_snapshot(ledger)
        continuity = handover_continuity_snapshot(data_root, project_id, ledger)
        def is_current(package):
            return (package["routingSnapshot"]["digest"] == canonical_digest(routing)
                and package["ledgerSnapshot"]["digest"] == snapshot["digest"]
                and package["continuitySnapshot"] == continuity)
        root = handover_root(data_root, project_id, revision_id)
        if root.exists():
            existing = load_handover(data_root, project_id, revision_id)
            if existing.get("delta", {}).get("basePackageDigest") != canonical_digest(base):
                raise ContinuityError("C08C_HANDOVER_ID_REUSED")
            return {"status": "IDEMPOTENT_HANDOVER_REFRESH", "handoverId": revision_id,
                "snapshotCurrent": is_current(existing), "writePerformed": False, "roleSwitched": False}, 0
        if is_current(base):
            return {"status": "HANDOVER_ALREADY_CURRENT", "handoverId": handover_id,
                "snapshotCurrent": True, "writePerformed": False, "roleSwitched": False}, 0
        if base.get("refreshDepth", 0) >= 8:
            raise ContinuityError("C08C_HANDOVER_REFRESH_LIMIT")
        old_events = set(base["continuitySnapshot"]["pendingTaskEventIds"])
        new_events = set(continuity["pendingTaskEventIds"])
        old_sections = base["ledgerSnapshot"].get("sectionDigests", {})
        package = {**base, "handoverId": revision_id, "createdAt": utc_now(), "refreshDepth": base.get("refreshDepth", 0) + 1,
            "sourceInputDigest": canonical_digest({"basePackageDigest": canonical_digest(base), "revisionId": revision_id}),
            "routingSnapshot": {"revision": routing["revision"], "digest": canonical_digest(routing), "roles": routing["roles"]},
            "ledgerSnapshot": snapshot, "continuitySnapshot": continuity,
            "delta": {"baseHandoverId": handover_id, "basePackageDigest": canonical_digest(base),
                "changedLedgerSections": sorted(key for key in set(old_sections) | set(snapshot["sectionDigests"])
                    if old_sections.get(key) != snapshot["sectionDigests"].get(key)),
                "addedPendingEventIds": sorted(new_events - old_events),
                "removedPendingEventIds": sorted(old_events - new_events),
                "successorMustReadChanges": True}}
        receipt = {"schemaVersion": SCHEMA_VERSION, "recordType": "C08_IMMUTABLE_ROLE_HANDOVER_RECEIPT",
            "createdAt": utc_now(), "handoverId": revision_id, "projectId": project_id,
            "artifactDigest": canonical_digest(package), "artifact": package}
        root.mkdir(parents=True, exist_ok=False)
        write_exclusive(root / "receipt-000000-package.json", receipt)
        write_exclusive(root / "handover-package.json", package)
        return {"status": "HANDOVER_REFRESHED", "handoverId": revision_id, "delta": package["delta"],
            "snapshotCurrent": True, "writePerformed": True, "roleSwitched": False}, 0


def prepare_handover(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args); project_id = project_ref(args.project_id); require_writer(args.writer_id)
    raw, input_digest = private_json(args.request, data_root, "C08C_HANDOVER_REQUEST_INVALID_JSON")
    request = validate_handover_request(raw, project_id); routing = verify_routing(data_root, project_id); ledger = verified_ledger(data_root, project_id)
    current = routing["roles"].get(request["role"])
    if request["mode"] == "BOOTSTRAP_ADJUDICATION_FROM_CENTRAL":
        if request["role"] != "CURRENT_ADJUDICATION" or current is not None:
            raise ContinuityError("C08C_ADJUDICATION_BOOTSTRAP_NOT_ALLOWED")
        source_role = "CURRENT_CENTRAL"; source = routing["roles"][source_role]
    else:
        if not isinstance(current, dict) or current.get("status") != "ACTIVE":
            raise ContinuityError("C08C_ACTIVE_ROLE_NOT_FOUND")
        source_role = request["role"]; source = current
    root = handover_root(data_root, project_id, request["handoverId"])
    if root.exists():
        existing = read_json(root / "handover-package.json", "C08C_HANDOVER_PACKAGE_INVALID")
        if existing.get("sourceInputDigest") == input_digest:
            return {"status": "IDEMPOTENT_HANDOVER_PACKAGE", "handoverId": request["handoverId"], "writePerformed": False}, 0
        raise ContinuityError("C08C_HANDOVER_ID_REUSED")
    open_adjudications = sorted(key for key, value in ledger["adjudications"].items() if value.get("stage") != "BOSS_DECIDED")
    package = {
        "schemaVersion": SCHEMA_VERSION, "recordType": "C08_ROLE_HANDOVER_PACKAGE", "createdAt": utc_now(),
        **request, "sourceInputDigest": input_digest, "sourceRole": source_role,
        "sourceGeneration": source["generation"], "sourceThreadRef": source["activeThreadRef"],
        "targetGeneration": 1 if current is None else current["generation"] + 1,
        "routingSnapshot": {"revision": routing["revision"], "digest": canonical_digest(routing), "roles": routing["roles"]},
        "ledgerSnapshot": handover_ledger_snapshot(ledger),
        "continuitySnapshot": handover_continuity_snapshot(data_root, project_id, ledger),
        "boundary": {"roleSwitched": False, "oldRoleStillActive": True, "businessWritePerformed": False, "requiresSuccessorVerification": True},
    }
    receipt = {"schemaVersion": SCHEMA_VERSION, "recordType": "C08_IMMUTABLE_ROLE_HANDOVER_RECEIPT", "createdAt": utc_now(), "handoverId": request["handoverId"], "projectId": project_id, "artifactDigest": canonical_digest(package), "artifact": package}
    with continuity_lock(data_root, project_id):
        root.mkdir(parents=True, exist_ok=False)
        write_exclusive(root / "receipt-000000-package.json", receipt)
        write_exclusive(root / "handover-package.json", package)
    return {"status": "ROLE_HANDOVER_PREPARED", "handoverId": request["handoverId"], "role": request["role"], "mode": request["mode"], "targetGeneration": package["targetGeneration"], "sourceThreadRef": package["sourceThreadRef"], "writePerformed": True, "roleSwitched": False}, 0


def load_handover(data_root: Path, project_id: str, handover_id: str) -> Dict[str, Any]:
    root = handover_root(data_root, project_id, handover_id)
    package = read_json(root / "handover-package.json", "C08C_HANDOVER_PACKAGE_NOT_FOUND")
    receipt = read_json(root / "receipt-000000-package.json", "C08C_HANDOVER_RECEIPT_MISSING")
    if package.get("projectId") != project_id or package.get("handoverId") != handover_id or receipt.get("artifact") != package or receipt.get("artifactDigest") != canonical_digest(package):
        raise ContinuityError("C08C_HANDOVER_PACKAGE_INTEGRITY_INVALID")
    return package


def validate_activation(value: Dict[str, Any], package: Dict[str, Any]) -> Dict[str, Any]:
    activation = exact(value, {"activationSchemaVersion", "recordType", "handoverId", "projectId", "role", "successorThreadRef", "runtimeProjectId", "model", "handoverPackageDigest"}, "C08C_ACTIVATION_SCHEMA_INVALID")
    if activation["activationSchemaVersion"] != SCHEMA_VERSION or activation["recordType"] != "C08_ROLE_SUCCESSOR_ACTIVATION" or activation["handoverId"] != package["handoverId"] or activation["projectId"] != package["projectId"] or role_ref(activation["role"]) != package["role"]:
        raise ContinuityError("C08C_ACTIVATION_SCHEMA_INVALID")
    if activation["handoverPackageDigest"] != canonical_digest(package):
        raise ContinuityError("C08C_HANDOVER_DIGEST_MISMATCH")
    model = activation["model"]
    if not valid_model(model):
        raise ContinuityError("C08C_ROLE_MODEL_INVALID")
    successor = ref(activation["successorThreadRef"], "C08C_SUCCESSOR_THREAD_REF_INVALID")
    if successor == package["sourceThreadRef"]:
        raise ContinuityError("C08C_SUCCESSOR_MUST_BE_NEW_TASK")
    if activation["runtimeProjectId"] != package["routingSnapshot"]["roles"][package["sourceRole"]]["runtimeProjectId"]:
        raise ContinuityError("C08C_SUCCESSOR_PROJECT_MISMATCH")
    if any(isinstance(role, dict) and role.get("activeThreadRef") == successor
           for role in package["routingSnapshot"]["roles"].values()):
        raise ContinuityError("C08C_SUCCESSOR_ALREADY_ACTIVE_ROLE")
    return {"successorThreadRef": successor, "runtimeProjectId": ref(activation["runtimeProjectId"], "C08C_RUNTIME_PROJECT_ID_INVALID"), "model": model}


def persist_activation_result(root: Path, package: Dict[str, Any], activation: Dict[str, Any],
                              input_digest: str, routing: Dict[str, Any]) -> None:
    role = routing["roles"][package["role"]]
    artifact = {
        "schemaVersion": SCHEMA_VERSION, "recordType": "C08_ROLE_SUCCESSOR_ACTIVATED", "createdAt": role["activatedAt"],
        "projectId": package["projectId"], "handoverId": package["handoverId"], "role": package["role"], **activation,
        "sourceThreadRef": package["sourceThreadRef"], "sourceGeneration": package["sourceGeneration"],
        "targetGeneration": package["targetGeneration"], "sourceInputDigest": input_digest,
        "routingReceiptId": routing["latestReceiptId"], "routingRevision": routing["revision"],
        "boundary": {"onlyOneActiveRole": True, "sourceIsReadOnlyForwarder": True, "businessWritePerformed": False}}
    receipt = {"schemaVersion": SCHEMA_VERSION, "recordType": "C08_IMMUTABLE_ROLE_ACTIVATION_RECEIPT",
        "createdAt": artifact["createdAt"], "projectId": package["projectId"], "handoverId": package["handoverId"],
        "artifactDigest": canonical_digest(artifact), "artifact": artifact}
    for name, value in (("receipt-000001-activation.json", receipt), ("activation.json", artifact)):
        path = root / name
        if path.exists():
            if read_json(path, "C08C_ACTIVATION_INVALID") != value:
                raise ContinuityError("C08C_ACTIVATION_RESULT_CONFLICT")
        else:
            write_exclusive(path, value)


def activate_successor(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args); project_id = project_ref(args.project_id); require_writer(args.writer_id)
    handover_id = ref(args.handover_id, "C08C_HANDOVER_ID_INVALID"); package = load_handover(data_root, project_id, handover_id)
    raw, input_digest = private_json(args.activation, data_root, "C08C_ACTIVATION_INVALID_JSON"); activation = validate_activation(raw, package)
    root = handover_root(data_root, project_id, handover_id); target = root / "activation.json"
    if target.exists():
        existing = read_json(target, "C08C_ACTIVATION_INVALID")
        if existing.get("sourceInputDigest") == input_digest:
            receipt = read_json(root / "receipt-000001-activation.json", "C08C_ACTIVATION_RESULT_CONFLICT")
            routing = verify_routing(data_root, project_id)
            if (receipt.get("artifact") != existing or receipt.get("artifactDigest") != canonical_digest(existing)
                    or existing.get("routingReceiptId") not in routing["receiptIds"]
                    or any(existing.get(key) != value for key, value in activation.items())):
                raise ContinuityError("C08C_ACTIVATION_RESULT_CONFLICT")
            committed = read_json(routing_receipts(data_root, project_id) / f"{existing['routingReceiptId']}.json",
                "C08C_ROUTING_RECEIPT_MISSING")["afterRouting"]
            committed_role = committed["roles"].get(package["role"]) or {}
            if (committed_role.get("activeThreadRef") != activation["successorThreadRef"]
                    or committed_role.get("generation") != package["targetGeneration"]):
                raise ContinuityError("C08C_ACTIVATION_RESULT_CONFLICT")
            return {"status": "IDEMPOTENT_SUCCESSOR_ACTIVATION", "handoverId": handover_id, "writePerformed": False}, 0
        raise ContinuityError("C08C_HANDOVER_ALREADY_ACTIVATED")
    with continuity_lock(data_root, project_id), ledger_lock(data_root, project_id):
        routing = verify_routing(data_root, project_id); ledger = verified_ledger(data_root, project_id)
        binding = {"handoverId": handover_id, "packageDigest": canonical_digest(package), "inputDigest": input_digest}
        # Routing is the sole commit point. Result files are recoverable projections.
        committed = None
        for receipt_id in routing["receiptIds"]:
            snapshot = read_json(routing_receipts(data_root, project_id) / f"{receipt_id}.json",
                "C08C_ROUTING_RECEIPT_MISSING")["afterRouting"]
            if (snapshot["roles"].get(package["role"]) or {}).get("activationBinding") == binding:
                committed = snapshot
                break
        if committed is not None:
            committed_role = routing["roles"].get(package["role"]) or {}
            persist_activation_result(root, package, activation, input_digest, committed)
            return {"status": "SUCCESSOR_ACTIVATION_RESULT_RECOVERED", "handoverId": handover_id,
                "activeThreadRef": committed_role["activeThreadRef"], "generation": committed_role["generation"],
                "routingRevision": routing["revision"], "writePerformed": True, "roleSwitched": False}, 0
        current_continuity = handover_continuity_snapshot(data_root, project_id, ledger,
            "communicationDigest" in package["continuitySnapshot"])
        if (
            canonical_digest(routing) != package["routingSnapshot"]["digest"]
            or canonical_digest(ledger) != package["ledgerSnapshot"]["digest"]
            or current_continuity != package["continuitySnapshot"]
        ):
            raise ContinuityError("C08C_HANDOVER_SNAPSHOT_STALE")
        current = routing["roles"].get(package["role"])
        if package["mode"] == "BOOTSTRAP_ADJUDICATION_FROM_CENTRAL":
            if current is not None or routing["roles"]["CURRENT_CENTRAL"]["activeThreadRef"] != package["sourceThreadRef"]:
                raise ContinuityError("C08C_HANDOVER_SOURCE_CHANGED")
            previous: List[str] = []
        else:
            if not isinstance(current, dict) or current["activeThreadRef"] != package["sourceThreadRef"] or current["generation"] != package["sourceGeneration"]:
                raise ContinuityError("C08C_HANDOVER_SOURCE_CHANGED")
            previous = [*current.get("previousThreadRefs", []), current["activeThreadRef"]]
        from handover_automation import require_activation_proof
        require_activation_proof(data_root, project_id, package, activation)
        def mutate(after: Dict[str, Any]) -> None:
            after["roles"][package["role"]] = {
                "generation": package["targetGeneration"], "activeThreadRef": activation["successorThreadRef"],
                "runtimeProjectId": activation["runtimeProjectId"], "model": activation["model"], "status": "ACTIVE",
                "activatedAt": utc_now(), "previousThreadRefs": previous, "source": package["mode"],
                "executionMapRef": package["executionMapRef"],
                "activationBinding": binding,
            }
        updated, routing_receipt = commit_routing(data_root, project_id, routing, "ACTIVATE_ROLE_SUCCESSOR", mutate)
        persist_activation_result(root, package, activation, input_digest, updated)
    return {"status": "ROLE_SUCCESSOR_ACTIVE", "handoverId": handover_id, "role": package["role"], "generation": package["targetGeneration"], "activeThreadRef": activation["successorThreadRef"], "previousThreadRef": package["sourceThreadRef"], "routingRevision": updated["revision"], "writePerformed": True}, 0


def validate_event(value: Dict[str, Any], project_id: str) -> Dict[str, Any]:
    event = exact(value, {"eventSchemaVersion", "recordType", "eventId", "projectId", "taskIdentity", "eventType", "sourceWindow", "targetRole", "routeRevisionSeen", "decisionRef", "evidenceRefs", "summary"}, "C08C_TASK_EVENT_SCHEMA_INVALID")
    if event["eventSchemaVersion"] != SCHEMA_VERSION or event["recordType"] != "C08_TASK_EVENT_INPUT" or event["projectId"] != project_id:
        raise ContinuityError("C08C_TASK_EVENT_SCHEMA_INVALID")
    identity = exact(event["taskIdentity"], {"taskId", "canonicalTitle"}, "C08C_TASK_IDENTITY_INVALID")
    source = exact(event["sourceWindow"], {"windowId", "runtimeThreadRef", "generation"}, "C08C_SOURCE_WINDOW_INVALID")
    event_type = str(event["eventType"]).strip().upper()
    if event_type not in EVENT_TYPES or event["targetRole"] != "CURRENT_CENTRAL" or not isinstance(event["routeRevisionSeen"], int) or event["routeRevisionSeen"] < 1:
        raise ContinuityError("C08C_TASK_EVENT_SCHEMA_INVALID")
    generation = source["generation"]
    if not isinstance(generation, int) or generation < 1:
        raise ContinuityError("C08C_SOURCE_WINDOW_INVALID")
    decision = None if event["decisionRef"] is None else ref(event["decisionRef"], "C08C_DECISION_REF_INVALID")
    return {
        "eventId": ref(event["eventId"], "C08C_EVENT_ID_INVALID"), "projectId": project_id,
        "taskIdentity": {"taskId": task_ref(identity["taskId"], "C08C_TASK_ID_INVALID"), "canonicalTitle": safe_text(identity["canonicalTitle"], "C08C_CANONICAL_TITLE_INVALID", 220)},
        "eventType": event_type,
        "sourceWindow": {"windowId": task_ref(source["windowId"], "C08C_WINDOW_ID_INVALID"), "runtimeThreadRef": ref(source["runtimeThreadRef"], "C08C_RUNTIME_THREAD_REF_INVALID"), "generation": generation},
        "targetRole": "CURRENT_CENTRAL", "routeRevisionSeen": event["routeRevisionSeen"], "decisionRef": decision,
        "evidenceRefs": refs(event["evidenceRefs"], "C08C_EVIDENCE_REFS_INVALID", allow_empty=True),
        "summary": safe_text(event["summary"], "C08C_EVENT_SUMMARY_INVALID"),
    }


def submit_event(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args); project_id = project_ref(args.project_id)
    raw, input_digest = private_json(args.event, data_root, "C08C_TASK_EVENT_INVALID_JSON"); event = validate_event(raw, project_id)
    routing = verify_routing(data_root, project_id); ledger = verified_ledger(data_root, project_id)
    if event["routeRevisionSeen"] > routing["revision"]:
        raise ContinuityError("C08C_TASK_EVENT_FUTURE_ROUTE_REVISION")
    task = ledger["tasks"].get(event["taskIdentity"]["taskId"]); window = ledger["windows"].get(event["sourceWindow"]["windowId"])
    if not isinstance(task, dict) or canonical_task_title(task) != event["taskIdentity"]["canonicalTitle"]:
        raise ContinuityError("C08C_TASK_IDENTITY_MISMATCH")
    if not isinstance(window, dict) or window_current_task_id(window) != task["taskId"] or window.get("runtimeThreadRef", window.get("windowId")) != event["sourceWindow"]["runtimeThreadRef"] or window.get("generation", 1) != event["sourceWindow"]["generation"]:
        raise ContinuityError("C08C_SOURCE_WINDOW_MISMATCH")
    target = event_path(data_root, project_id, event["eventId"])
    if target.exists():
        existing = read_json(target, "C08C_TASK_EVENT_INVALID")
        if existing.get("sourceInputDigest") == input_digest:
            return {"status": "IDEMPOTENT_TASK_EVENT", "eventId": event["eventId"], "writePerformed": False}, 0
        raise ContinuityError("C08C_EVENT_ID_REUSED")
    active = routing["roles"]["CURRENT_CENTRAL"]
    artifact = {
        "schemaVersion": SCHEMA_VERSION, "recordType": "C08_DURABLE_TASK_EVENT", "createdAt": utc_now(), **event,
        "sourceInputDigest": input_digest,
        "targetAtSubmission": {"routingRevision": routing["revision"], "generation": active["generation"], "threadRef": active["activeThreadRef"]},
        "boundary": {"chatNotificationIsAdvisory": True, "eventPersistsAcrossRoleHandover": True, "ledgerUpdated": False, "businessWritePerformed": False},
    }
    receipt = {"schemaVersion": SCHEMA_VERSION, "recordType": "C08_IMMUTABLE_TASK_EVENT_RECEIPT", "createdAt": utc_now(), "projectId": project_id, "eventId": event["eventId"], "artifactDigest": canonical_digest(artifact), "artifact": artifact}
    write_exclusive(event_receipt_path(data_root, project_id, event["eventId"]), receipt)
    write_exclusive(target, artifact)
    return {"status": "TASK_EVENT_QUEUED", "eventId": event["eventId"], "taskId": task["taskId"], "eventType": event["eventType"], "targetRole": "CURRENT_CENTRAL", "targetGenerationAtSubmission": active["generation"], "writePerformed": True, "ledgerUpdated": False}, 0


def relative_private_path(path: Path, data_root: Path, error: str) -> str:
    try:
        return path.resolve().relative_to(data_root.resolve()).as_posix()
    except ValueError:
        raise ContinuityError(error)


def load_return_handback(raw_path: str, data_root: Path, project_id: str) -> Tuple[Dict[str, Any], str]:
    handback, digest = private_json(raw_path, data_root, "C08C_RETURN_HANDBACK_INVALID_JSON")
    required = {
        "handbackSchemaVersion", "recordType", "handbackId", "returnTicketId", "routeRevisionSeen", "projectId", "packageId", "c05ReviewId",
        "taskId", "windowId", "completionSignalId", "submittedBy", "bossHandbackAuthorization", "executedScopeRefs", "evidenceRefs",
        "testAndObjectRefs", "parentQualityReviewRefs", "unresolvedRefs", "residualRiskRefs",
    }
    if "directionContext" in handback:
        from ledger_manager import validate_direction_context
        validate_direction_context(handback["directionContext"])
        required = required | {"directionContext"}
    if set(handback) != required or handback.get("handbackSchemaVersion") != "0.8.0" or handback.get("recordType") != "C06_TASK_WINDOW_HANDBACK" or handback.get("projectId") != project_id:
        raise ContinuityError("C08C_RETURN_HANDBACK_SCHEMA_INVALID")
    ticket_id = return_ticket_ref(handback.get("returnTicketId"), "C08C_RETURN_TICKET_ID_INVALID")
    task_id = task_ref(handback.get("taskId"), "C08C_RETURN_TASK_ID_INVALID")
    window_id = task_ref(handback.get("windowId"), "C08C_RETURN_WINDOW_ID_INVALID")
    handback_id = ref(handback.get("handbackId"), "C08C_RETURN_HANDBACK_ID_INVALID")
    completion_signal_id = ref(handback.get("completionSignalId"), "C08C_RETURN_COMPLETION_SIGNAL_INVALID")
    submitter = exact(handback.get("submittedBy"), {"type", "id"}, "C08C_RETURN_SUBMITTER_INVALID")
    if submitter.get("type") != "task-window" or ref(submitter.get("id"), "C08C_RETURN_SUBMITTER_INVALID") != window_id:
        raise ContinuityError("C08C_RETURN_SUBMITTER_INVALID")
    if not isinstance(handback.get("routeRevisionSeen"), int) or handback["routeRevisionSeen"] < 1:
        raise ContinuityError("C08C_RETURN_ROUTE_REVISION_INVALID")
    return {
        "returnTicketId": ticket_id, "handbackId": handback_id, "taskId": task_id, "windowId": window_id,
        "completionSignalId": completion_signal_id, "routeRevisionSeen": handback["routeRevisionSeen"],
        "relativePath": relative_private_path(Path(raw_path), data_root, "C08C_PRIVATE_INPUT_REQUIRED"),
        "bossHandbackAuthorization": handback.get("bossHandbackAuthorization"),
        "executedScopeRefs": handback.get("executedScopeRefs"),
        "parentQualityReviewRefs": handback.get("parentQualityReviewRefs"),
    }, digest


def superseding_conflict_validation_id(
    data_root: Path, project_id: str, ledger: Dict[str, Any], handback: Dict[str, Any]
) -> Optional[str]:
    """Return the single verified C06 conflict being superseded, otherwise None."""
    task = ledger.get("tasks", {}).get(handback.get("taskId"))
    window = ledger.get("windows", {}).get(handback.get("windowId"))
    authorization = handback.get("bossHandbackAuthorization")
    scope_refs = handback.get("executedScopeRefs")
    parent_refs = handback.get("parentQualityReviewRefs")
    if (
        not isinstance(task, dict) or task.get("status") != "CONFLICT"
        or not isinstance(window, dict) or window_current_task_id(window) != handback.get("taskId")
        or not isinstance(authorization, dict) or authorization.get("status") != "APPROVED"
        or not isinstance(authorization.get("reference"), str) or not authorization["reference"].strip()
        or not isinstance(scope_refs, list) or not all(isinstance(item, str) for item in scope_refs)
        or parent_refs != []
    ):
        return None
    matches = [
        stop for stop in ledger.get("hardStops", [])
        if stop.get("code") == "C06_VALIDATION_CONFLICT"
        and stop.get("taskId") == handback.get("taskId")
        and stop.get("validationId") in scope_refs
    ]
    if len(matches) != 1:
        return None
    validation_id = matches[0].get("validationId")
    decision_path = data_root / "handover-validations" / project_id / validation_id / "validation-decision.json"
    try:
        decision = json.loads(decision_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    if (
        not isinstance(decision, dict)
        or decision.get("recordType") != "C06_VALIDATION_DECISION"
        or decision.get("validationId") != validation_id
        or decision.get("taskId") != handback.get("taskId")
        or decision.get("outcome") != "CONFLICT"
    ):
        return None
    return validation_id


def record_superseding_conflict_completion(
    data_root: Path, project_id: str, task_id: str, signal_id: str,
    conflict_validation_id: str, caller_thread_ref: str,
) -> Dict[str, Any]:
    """Record a completion signal without clearing or bypassing the active conflict."""
    with ledger_lock(data_root, project_id):
        before = load_ledger(data_root, project_id)
        task = before["tasks"][task_id]
        if task.get("status") != "CONFLICT":
            raise ContinuityError("C08C_SUPERSEDING_CONFLICT_CONTEXT_CHANGED")
        matches = [
            stop for stop in before.get("hardStops", [])
            if stop.get("code") == "C06_VALIDATION_CONFLICT"
            and stop.get("taskId") == task_id
            and stop.get("validationId") == conflict_validation_id
        ]
        if len(matches) != 1:
            raise ContinuityError("C08C_SUPERSEDING_CONFLICT_CONTEXT_CHANGED")
        if signal_id in task.get("completionSignalIds", []):
            return {
                "status": "IDEMPOTENT_DUPLICATE_SIGNAL", "taskId": task_id,
                "signalId": signal_id, "currentTaskStatus": "CONFLICT", "writePerformed": False,
            }

        def mutate(after: Dict[str, Any]) -> None:
            target = after["tasks"][task_id]
            target["completionSignalIds"].append(signal_id)
            target["history"].append({
                "at": utc_now(), "event": "C08_SUPERSEDING_CONFLICT_RETURN_ADMITTED",
                "signalId": signal_id, "conflictValidationId": conflict_validation_id,
                "from": "CONFLICT", "to": "CONFLICT", "by": CENTRAL_WRITER,
            })

        commit_kwargs = {}
        if "caller_thread_ref" in inspect.signature(commit_mutation).parameters:
            commit_kwargs["caller_thread_ref"] = caller_thread_ref
        return commit_mutation(
            data_root, project_id, before, CENTRAL_WRITER,
            "C08_ADMIT_SUPERSEDING_CONFLICT_RETURN",
            {
                "taskId": task_id, "signalId": signal_id,
                "conflictValidationId": conflict_validation_id,
                "toStatus": "CONFLICT", "occupancyReleased": False,
            },
            mutate, **commit_kwargs,
        )


def read_return_submission(data_root: Path, project_id: str, ticket_id: str) -> Dict[str, Any]:
    ticket_id = return_ticket_ref(ticket_id, "C08C_RETURN_TICKET_ID_INVALID")
    submission = read_json(return_submission_path(data_root, project_id, ticket_id), "C08C_RETURN_SUBMISSION_NOT_FOUND")
    receipt = read_json(return_submission_receipt_path(data_root, project_id, ticket_id), "C08C_RETURN_SUBMISSION_RECEIPT_MISSING")
    required = {
        "returnSchemaVersion", "recordType", "createdAt", "projectId", "returnTicketId", "handbackId", "taskId", "windowId",
        "completionSignalId", "sourceWindow", "routeRevisionSeen", "sourceHandbackRelativePath", "sourceHandbackDigest", "boundary",
    }
    if set(submission) != required or submission.get("returnSchemaVersion") != RETURN_SCHEMA_VERSION or submission.get("recordType") != "C08_RETURN_SUBMISSION" or submission.get("projectId") != project_id or submission.get("returnTicketId") != ticket_id:
        raise ContinuityError("C08C_RETURN_SUBMISSION_SCHEMA_INVALID")
    receipt_required = {"returnSchemaVersion", "recordType", "createdAt", "projectId", "returnTicketId", "submissionDigest", "submission"}
    if set(receipt) != receipt_required or receipt.get("returnSchemaVersion") != RETURN_SCHEMA_VERSION or receipt.get("recordType") != "C08_IMMUTABLE_RETURN_SUBMISSION_RECEIPT" or receipt.get("projectId") != project_id or receipt.get("returnTicketId") != ticket_id or receipt.get("submission") != submission or receipt.get("submissionDigest") != canonical_digest(submission):
        raise ContinuityError("C08C_RETURN_SUBMISSION_RECEIPT_INVALID")
    return submission


def read_return_delivery(data_root: Path, project_id: str, ticket_id: str) -> Dict[str, Any]:
    submission = read_return_submission(data_root, project_id, ticket_id)
    delivery = read_json(return_delivery_path(data_root, project_id, ticket_id), "C08C_RETURN_DELIVERY_PENDING")
    required = {"returnSchemaVersion", "recordType", "createdAt", "projectId", "returnTicketId", "eventId", "eventDigest", "eventReceiptDigest", "submissionDigest", "boundary"}
    if set(delivery) != required or delivery.get("returnSchemaVersion") != RETURN_SCHEMA_VERSION or delivery.get("recordType") != "C08_RETURN_DELIVERY_RECEIPT" or delivery.get("projectId") != project_id or delivery.get("returnTicketId") != ticket_id or delivery.get("submissionDigest") != canonical_digest(submission):
        raise ContinuityError("C08C_RETURN_DELIVERY_RECEIPT_INVALID")
    event = read_json(event_path(data_root, project_id, delivery["eventId"]), "C08C_RETURN_EVENT_MISSING")
    event_receipt = read_json(event_receipt_path(data_root, project_id, delivery["eventId"]), "C08C_RETURN_EVENT_RECEIPT_MISSING")
    if event.get("eventType") != "TASK_HANDBACK_QUEUED" or event.get("sourceInputDigest") != canonical_digest(submission) or delivery.get("eventDigest") != canonical_digest(event) or delivery.get("eventReceiptDigest") != canonical_digest(event_receipt) or event_receipt.get("artifact") != event or event_receipt.get("artifactDigest") != canonical_digest(event):
        raise ContinuityError("C08C_RETURN_DELIVERY_RECEIPT_INVALID")
    return {"submission": submission, "delivery": delivery, "event": event}


def read_return_validation_abort(data_root: Path, project_id: str, ticket_id: str) -> Dict[str, Any]:
    ticket_id = return_ticket_ref(ticket_id, "C08C_RETURN_TICKET_ID_INVALID")
    abort = read_json(return_validation_abort_path(data_root, project_id, ticket_id), "C08C_RETURN_VALIDATION_ABORT_NOT_FOUND")
    required = {
        "returnSchemaVersion", "recordType", "createdAt", "projectId", "returnTicketId", "taskId", "handbackId",
        "validatorThreadRef", "centralThreadRef", "bossAuthorizationRef", "reasonRef", "reservationDigest", "boundary",
    }
    expected_boundary = {
        "validationReservationAborted": True, "validationResultRecorded": False, "returnEvidencePreserved": True,
        "taskStatusChanged": False, "businessWritePerformed": False,
    }
    if set(abort) != required or abort.get("returnSchemaVersion") != RETURN_SCHEMA_VERSION or abort.get("recordType") != "C08_RETURN_VALIDATION_ABORT" or abort.get("projectId") != project_id or abort.get("returnTicketId") != ticket_id or abort.get("boundary") != expected_boundary:
        raise ContinuityError("C08C_RETURN_VALIDATION_ABORT_INVALID")
    reservation = read_json(return_reservation_path(data_root, project_id, ticket_id), "C08C_RETURN_VALIDATION_ABORT_RESERVATION_MISSING")
    if (
        abort.get("reservationDigest") != canonical_digest(reservation)
        or abort.get("taskId") != reservation.get("taskId")
        or abort.get("handbackId") != reservation.get("handbackId")
        or abort.get("validatorThreadRef") != reservation.get("validatorThreadRef")
        or return_validation_path(data_root, project_id, ticket_id).exists()
    ):
        raise ContinuityError("C08C_RETURN_VALIDATION_ABORT_INVALID")
    ref(abort.get("centralThreadRef"), "C08C_RETURN_VALIDATION_ABORT_INVALID")
    ref(abort.get("bossAuthorizationRef"), "C08C_RETURN_VALIDATION_ABORT_INVALID")
    ref(abort.get("reasonRef"), "C08C_RETURN_VALIDATION_ABORT_INVALID")
    return abort


def return_validation_aborted(data_root: Path, project_id: str, ticket_id: str) -> bool:
    if not return_validation_abort_path(data_root, project_id, ticket_id).exists():
        return False
    read_return_validation_abort(data_root, project_id, ticket_id)
    return True


def validation_reissue_after_abort_allowed(
    data_root: Path, project_id: str, ticket_id: str, submission: Dict[str, Any]
) -> bool:
    """Allow a new immutable ticket after a reserved predecessor was aborted.

    The prior admission already moved the task to NEEDS_REVIEW, so the reissue
    must not attempt to record a second completion signal.  Scope remains bound
    to the same task and window, and the old ticket/abort history is preserved.
    """
    ledger = verified_ledger(data_root, project_id)
    task = ledger.get("tasks", {}).get(submission["taskId"])
    if not isinstance(task, dict) or task.get("status") != "NEEDS_REVIEW":
        return False
    for prior_ticket_id in pending_return_ticket_ids(data_root, project_id):
        if prior_ticket_id == ticket_id or not return_validation_abort_path(data_root, project_id, prior_ticket_id).exists():
            continue
        read_return_validation_abort(data_root, project_id, prior_ticket_id)
        prior = read_return_submission(data_root, project_id, prior_ticket_id)
        if prior.get("taskId") == submission.get("taskId") and prior.get("windowId") == submission.get("windowId"):
            return True
    return False


def return_ticket_state(data_root: Path, project_id: str, ticket_id: str) -> str:
    try:
        read_return_submission(data_root, project_id, ticket_id)
    except ContinuityError as error:
        if str(error) == "C08C_RETURN_SUBMISSION_NOT_FOUND":
            return "LOCAL_REPLY_ONLY"
        raise
    if not return_delivery_path(data_root, project_id, ticket_id).exists():
        return "OUTBOX_PENDING"
    delivery = read_return_delivery(data_root, project_id, ticket_id)
    if not return_admission_path(data_root, project_id, ticket_id).exists():
        acknowledgement = acknowledgement_path(data_root, project_id, delivery["delivery"]["eventId"])
        return "ACKNOWLEDGED" if acknowledgement.exists() else "QUEUED"
    if not return_reservation_path(data_root, project_id, ticket_id).exists():
        return "ADMITTED"
    if return_validation_aborted(data_root, project_id, ticket_id):
        return "VALIDATION_ABORTED"
    if not return_validation_path(data_root, project_id, ticket_id).exists():
        return "VALIDATING"
    return "VALIDATION_RECORDED"


def persist_return_event(data_root: Path, project_id: str, ticket_id: str, routing: Dict[str, Any], ledger: Dict[str, Any]) -> Dict[str, Any]:
    submission = read_return_submission(data_root, project_id, ticket_id)
    event_id = return_event_id(ticket_id)
    target = event_path(data_root, project_id, event_id)
    if target.exists():
        event = read_json(target, "C08C_RETURN_EVENT_INVALID")
        if event.get("sourceInputDigest") != canonical_digest(submission):
            raise ContinuityError("C08C_RETURN_EVENT_COLLISION")
        receipt = read_json(event_receipt_path(data_root, project_id, event_id), "C08C_RETURN_EVENT_RECEIPT_MISSING")
        if receipt.get("artifact") != event or receipt.get("artifactDigest") != canonical_digest(event):
            raise ContinuityError("C08C_RETURN_EVENT_RECEIPT_MISSING")
        if not return_delivery_path(data_root, project_id, ticket_id).exists():
            delivery = {
                "returnSchemaVersion": RETURN_SCHEMA_VERSION, "recordType": "C08_RETURN_DELIVERY_RECEIPT", "createdAt": utc_now(),
                "projectId": project_id, "returnTicketId": ticket_id, "eventId": event_id, "eventDigest": canonical_digest(event),
                "eventReceiptDigest": canonical_digest(receipt), "submissionDigest": canonical_digest(submission),
                "boundary": {"taskEventQueued": True, "chatReplyIsNotDeliveryProof": True, "ledgerUpdated": False, "businessWritePerformed": False},
            }
            write_exclusive(return_delivery_path(data_root, project_id, ticket_id), delivery)
        return event
    task = ledger["tasks"].get(submission["taskId"])
    window = ledger["windows"].get(submission["windowId"])
    if not isinstance(task, dict) or not isinstance(window, dict):
        raise ContinuityError("C08C_RETURN_SOURCE_MISMATCH")
    active = routing["roles"]["CURRENT_CENTRAL"]
    event = {
        "schemaVersion": SCHEMA_VERSION, "recordType": "C08_DURABLE_TASK_EVENT", "createdAt": utc_now(),
        "eventId": event_id, "projectId": project_id,
        "taskIdentity": {"taskId": submission["taskId"], "canonicalTitle": canonical_task_title(task)},
        "eventType": "TASK_HANDBACK_QUEUED",
        "sourceWindow": submission["sourceWindow"], "targetRole": "CURRENT_CENTRAL",
        "routeRevisionSeen": submission["routeRevisionSeen"], "decisionRef": submission["returnTicketId"],
        "evidenceRefs": [submission["returnTicketId"]],
        "summary": f"任务 {submission['taskId']} 已提交可验证回传包；中央只需处理回传票据 {submission['returnTicketId']}。",
        "sourceInputDigest": canonical_digest(submission),
        "targetAtSubmission": {"routingRevision": routing["revision"], "generation": active["generation"], "threadRef": active["activeThreadRef"]},
        "boundary": {"chatNotificationIsAdvisory": True, "eventPersistsAcrossRoleHandover": True, "ledgerUpdated": False, "businessWritePerformed": False, "detailQuarantinedFromCentral": True},
    }
    receipt = {"schemaVersion": SCHEMA_VERSION, "recordType": "C08_IMMUTABLE_TASK_EVENT_RECEIPT", "createdAt": utc_now(), "projectId": project_id, "eventId": event_id, "artifactDigest": canonical_digest(event), "artifact": event}
    write_exclusive(event_receipt_path(data_root, project_id, event_id), receipt)
    write_exclusive(target, event)
    delivery = {
        "returnSchemaVersion": RETURN_SCHEMA_VERSION, "recordType": "C08_RETURN_DELIVERY_RECEIPT", "createdAt": utc_now(),
        "projectId": project_id, "returnTicketId": ticket_id, "eventId": event_id, "eventDigest": canonical_digest(event),
        "eventReceiptDigest": canonical_digest(receipt), "submissionDigest": canonical_digest(submission),
        "boundary": {"taskEventQueued": True, "chatReplyIsNotDeliveryProof": True, "ledgerUpdated": False, "businessWritePerformed": False},
    }
    write_exclusive(return_delivery_path(data_root, project_id, ticket_id), delivery)
    return event


def prepared_recovery_return_case(
    data_root: Path,
    project_id: str,
    ledger: Dict[str, Any],
    task_id: str,
    window_id: str,
) -> Optional[str]:
    """Verify the narrow recovery exit that permits handback, never business resumption."""
    recovery = ledger.get("recovery", {})
    task = ledger.get("tasks", {}).get(task_id)
    window = ledger.get("windows", {}).get(window_id)
    if (
        recovery.get("state") != "RESUME_REVIEW_REQUIRED"
        or recovery.get("decisionAction") != "PREPARE_RESUME"
        or not isinstance(task, dict)
        or task.get("status") != "BLOCKED"
        or not isinstance(window, dict)
        or window.get("status") != "DISCONNECTED"
        or window_current_task_id(window) != task_id
    ):
        return None
    case_id = recovery.get("activeCaseId")
    if not isinstance(case_id, str):
        return None
    try:
        from disconnection_recovery_controller import RecoveryError, verify_stage

        freeze = verify_stage(data_root, project_id, case_id, "freeze", "C08_FREEZE_DISCONNECTED_CONTEXT")
        verify_stage(data_root, project_id, case_id, "takeover", "C08_RECOVER_CENTRAL_CONTROL")
        decision = verify_stage(data_root, project_id, case_id, "decision", "C08_RECORD_RECOVERY_DECISION")
    except RecoveryError as error:
        raise ContinuityError(f"C08C_RECOVERY_RETURN_INTEGRITY_INVALID:{error}")
    if (
        freeze.get("incidentType") != "TASK_WINDOW"
        or freeze.get("taskId") != task_id
        or freeze.get("targetId") != window_id
        or window_id not in freeze.get("affectedContext", {}).get("windowIds", [])
        or decision.get("action") != "PREPARE_RESUME"
        or canonical_digest(ledger.get("objectOccupancies", {})) != freeze.get("snapshot", {}).get("objectOccupanciesDigest")
    ):
        raise ContinuityError("C08C_RECOVERY_RETURN_CONTEXT_MISMATCH")
    return case_id


def record_recovery_return_completion(
    data_root: Path,
    project_id: str,
    task_id: str,
    window_id: str,
    signal_id: str,
    expected_case_id: str,
    caller_thread_ref: str,
) -> Dict[str, Any]:
    """Move a disconnected task directly to review while keeping business execution stopped."""
    with ledger_lock(data_root, project_id):
        before = load_ledger(data_root, project_id)
        case_id = prepared_recovery_return_case(data_root, project_id, before, task_id, window_id)
        if case_id != expected_case_id:
            raise ContinuityError("C08C_RECOVERY_RETURN_CONTEXT_CHANGED")
        task = before["tasks"][task_id]
        if signal_id in task["completionSignalIds"]:
            return {
                "status": "IDEMPOTENT_DUPLICATE_SIGNAL",
                "taskId": task_id,
                "signalId": signal_id,
                "currentTaskStatus": task["status"],
                "writePerformed": False,
            }

        def mutate(after: Dict[str, Any]) -> None:
            target = after["tasks"][task_id]
            target["completionSignalIds"].append(signal_id)
            target["status"] = "NEEDS_REVIEW"
            target["history"].append({
                "at": utc_now(), "event": "C08_RECOVERY_RETURN_ADMITTED", "caseId": case_id,
                "signalId": signal_id, "from": "BLOCKED", "to": "NEEDS_REVIEW", "by": CENTRAL_WRITER,
            })
            after["recovery"].update({
                "state": "CLOSED", "closedAt": utc_now(),
                "closureReason": "RETURN_ADMITTED_FOR_INDEPENDENT_VALIDATION",
                "businessExecutionResumed": False, "occupancyReleaseStatus": "RETAINED_PENDING_C06_AND_BOSS",
            })

        commit_kwargs = {}
        if "caller_thread_ref" in inspect.signature(commit_mutation).parameters:
            commit_kwargs["caller_thread_ref"] = caller_thread_ref
        return commit_mutation(
            data_root, project_id, before, CENTRAL_WRITER, "C08_ADMIT_RECOVERY_RETURN",
            {
                "caseId": case_id, "taskId": task_id, "windowId": window_id,
                "signalId": signal_id, "toStatus": "NEEDS_REVIEW",
                "businessExecutionResumed": False, "occupancyReleased": False,
            },
            mutate,
            **commit_kwargs,
        )


def submit_return(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args); project_id = project_ref(args.project_id)
    handback, handback_digest = load_return_handback(args.handback, data_root, project_id)
    ticket_id = handback["returnTicketId"]
    routing = verify_routing(data_root, project_id); ledger = verified_ledger(data_root, project_id)
    task = ledger["tasks"].get(handback["taskId"]); window = ledger["windows"].get(handback["windowId"])
    if handback["routeRevisionSeen"] > routing["revision"]:
        raise ContinuityError("C08C_RETURN_FUTURE_ROUTE_REVISION")
    normal_source = (
        isinstance(task, dict) and task.get("status") in {"IN_PROGRESS", "NEEDS_REVIEW"}
        and isinstance(window, dict) and window_current_task_id(window) == handback["taskId"]
    )
    superseding_conflict_id = None if normal_source else superseding_conflict_validation_id(
        data_root, project_id, ledger, handback
    )
    superseding_source = superseding_conflict_id is not None
    recovery_case_id = None if normal_source or superseding_source else prepared_recovery_return_case(
        data_root, project_id, ledger, handback["taskId"], handback["windowId"]
    )
    if not normal_source and not superseding_source and recovery_case_id is None:
        raise ContinuityError("C08C_RETURN_SOURCE_MISMATCH")
    source_window = {"windowId": handback["windowId"], "runtimeThreadRef": window.get("runtimeThreadRef", window.get("windowId")), "generation": window.get("generation", 1)}
    if not isinstance(source_window["generation"], int) or source_window["generation"] < 1:
        raise ContinuityError("C08C_RETURN_SOURCE_MISMATCH")
    with continuity_lock(data_root, project_id):
        target = return_submission_path(data_root, project_id, ticket_id)
        delivery_preexisted = return_delivery_path(data_root, project_id, ticket_id).exists()
        if target.exists():
            existing = read_return_submission(data_root, project_id, ticket_id)
            if existing.get("sourceHandbackDigest") != handback_digest:
                raise ContinuityError("C08C_RETURN_TICKET_REUSED")
        else:
            submission = {
                "returnSchemaVersion": RETURN_SCHEMA_VERSION, "recordType": "C08_RETURN_SUBMISSION", "createdAt": utc_now(),
                "projectId": project_id, "returnTicketId": ticket_id, "handbackId": handback["handbackId"], "taskId": handback["taskId"],
                "windowId": handback["windowId"], "completionSignalId": handback["completionSignalId"], "sourceWindow": source_window,
                "routeRevisionSeen": handback["routeRevisionSeen"], "sourceHandbackRelativePath": handback["relativePath"], "sourceHandbackDigest": handback_digest,
                "boundary": {
                    "chatReplyIsNotDeliveryProof": True, "centralGetsSummaryOnly": True,
                    "taskDoneDeclared": False, "businessWritePerformed": False,
                    "recoveryReturnOnly": recovery_case_id is not None,
                    "recoveryCaseId": recovery_case_id,
                    "supersedingConflictReturnOnly": superseding_source,
                    "supersedingConflictValidationId": superseding_conflict_id,
                    "businessExecutionResumed": False,
                },
            }
            receipt = {"returnSchemaVersion": RETURN_SCHEMA_VERSION, "recordType": "C08_IMMUTABLE_RETURN_SUBMISSION_RECEIPT", "createdAt": utc_now(), "projectId": project_id, "returnTicketId": ticket_id, "submissionDigest": canonical_digest(submission), "submission": submission}
            write_exclusive(return_submission_receipt_path(data_root, project_id, ticket_id), receipt)
            write_exclusive(target, submission)
        event = persist_return_event(data_root, project_id, ticket_id, routing, ledger)
    delivery = read_return_delivery(data_root, project_id, ticket_id)["delivery"]
    status = "IDEMPOTENT_TASK_RETURN" if target.exists() and delivery_preexisted else "TASK_EVENT_QUEUED"
    return {"status": status, "returnState": "QUEUED", "returnTicketId": ticket_id, "eventId": event["eventId"], "handbackDigest": handback_digest, "deliveryReceiptId": "receipt-000001-delivery", "taskId": handback["taskId"], "centralPayload": "SUMMARY_ONLY", "doneRecorded": False, "writePerformed": status == "TASK_EVENT_QUEUED", "message": "已写入可消费回传票据和中央事件箱；只有此回执成立，聊天文字不算回传。"}, 0


def reconcile_return(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args); project_id = project_ref(args.project_id); ticket_id = return_ticket_ref(args.return_ticket_id, "C08C_RETURN_TICKET_ID_INVALID")
    routing = verify_routing(data_root, project_id); ledger = verified_ledger(data_root, project_id)
    with continuity_lock(data_root, project_id):
        submission = read_return_submission(data_root, project_id, ticket_id)
        if return_delivery_path(data_root, project_id, ticket_id).exists():
            delivery = read_return_delivery(data_root, project_id, ticket_id)["delivery"]
            return {"status": "IDEMPOTENT_RETURN_DELIVERY", "returnState": return_ticket_state(data_root, project_id, ticket_id), "returnTicketId": ticket_id, "eventId": delivery["eventId"], "writePerformed": False}, 0
        event = persist_return_event(data_root, project_id, ticket_id, routing, ledger)
    return {"status": "TASK_EVENT_QUEUED", "returnState": "QUEUED", "returnTicketId": ticket_id, "eventId": event["eventId"], "writePerformed": True, "message": "已补齐先前中断的回传投递；没有聊天补发可替代此回执。"}, 0


def validate_return_policy(raw: Dict[str, Any], project_id: str) -> Dict[str, Any]:
    required = {"returnPolicySchemaVersion", "recordType", "policyId", "projectId", "mode", "maxConcurrentValidations", "bossAuthorizationRef", "finalDoneRequiresBoss"}
    value = exact(raw, required, "C08C_RETURN_POLICY_SCHEMA_INVALID")
    if value.get("returnPolicySchemaVersion") != RETURN_SCHEMA_VERSION or value.get("recordType") != "C08_RETURN_ADMISSION_POLICY" or value.get("projectId") != project_id or value.get("mode") != "AUTO_QUEUE_AND_VALIDATE_WITHIN_APPROVED_SCOPE" or value.get("maxConcurrentValidations") != 1 or value.get("finalDoneRequiresBoss") is not True:
        raise ContinuityError("C08C_RETURN_POLICY_SCHEMA_INVALID")
    return {"policyId": return_ticket_ref(value.get("policyId"), "C08C_RETURN_POLICY_ID_INVALID"), "projectId": project_id, "mode": value["mode"], "maxConcurrentValidations": 1, "bossAuthorizationRef": ref(value.get("bossAuthorizationRef"), "C08C_RETURN_POLICY_AUTHORIZATION_INVALID"), "finalDoneRequiresBoss": True}


def configure_return_policy(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args); project_id = project_ref(args.project_id); require_writer(args.writer_id)
    raw, input_digest = private_json(args.policy, data_root, "C08C_RETURN_POLICY_INVALID_JSON"); policy = validate_return_policy(raw, project_id)
    routing = verify_routing(data_root, project_id); current_role(routing, "CURRENT_CENTRAL", ref(args.current_thread_ref, "C08C_THREAD_REF_INVALID"))
    target = return_policy_path(data_root, project_id, policy["policyId"])
    if target.exists():
        existing = read_json(target, "C08C_RETURN_POLICY_INVALID")
        if existing.get("sourceInputDigest") == input_digest:
            return {"status": "IDEMPOTENT_RETURN_POLICY", "policyId": policy["policyId"], "writePerformed": False}, 0
        raise ContinuityError("C08C_RETURN_POLICY_ID_REUSED")
    artifact = {"returnSchemaVersion": RETURN_SCHEMA_VERSION, "recordType": "C08_RETURN_ADMISSION_POLICY", "configuredAt": utc_now(), **policy, "sourceInputDigest": input_digest, "configuredByRole": "CURRENT_CENTRAL", "boundary": {"allowsAutoAdmissionOnlyWithinApprovedScope": True, "maxConcurrentValidations": 1, "bossFinalApprovalStillRequired": True, "businessWritePerformed": False}}
    receipt = {"returnSchemaVersion": RETURN_SCHEMA_VERSION, "recordType": "C08_IMMUTABLE_RETURN_POLICY_RECEIPT", "configuredAt": utc_now(), "projectId": project_id, "policyId": policy["policyId"], "artifactDigest": canonical_digest(artifact), "artifact": artifact}
    write_exclusive(return_policy_receipt_path(data_root, project_id, policy["policyId"]), receipt)
    write_exclusive(target, artifact)
    return {"status": "RETURN_POLICY_CONFIGURED", "policyId": policy["policyId"], "mode": policy["mode"], "maxConcurrentValidations": 1, "bossFinalApprovalRequired": True, "writePerformed": True}, 0


def ensure_return_event_acknowledgement(data_root: Path, project_id: str, ticket_id: str, current_thread_ref: str, acknowledgement_ref: str, routing: Dict[str, Any]) -> Dict[str, Any]:
    delivery = read_return_delivery(data_root, project_id, ticket_id)["delivery"]
    target = acknowledgement_path(data_root, project_id, delivery["eventId"])
    if target.exists():
        existing = read_json(target, "C08C_ACKNOWLEDGEMENT_INVALID")
        if existing.get("eventId") != delivery["eventId"] or existing.get("projectId") != project_id:
            raise ContinuityError("C08C_ACKNOWLEDGEMENT_INVALID")
        return existing
    active = current_role(routing, "CURRENT_CENTRAL", current_thread_ref)
    event = read_json(event_path(data_root, project_id, delivery["eventId"]), "C08C_RETURN_EVENT_MISSING")
    acknowledgement = {
        "schemaVersion": SCHEMA_VERSION, "recordType": "C08_TASK_EVENT_ACKNOWLEDGEMENT", "createdAt": utc_now(), "projectId": project_id,
        "eventId": delivery["eventId"], "eventDigest": canonical_digest(event), "acknowledgementRef": acknowledgement_ref,
        "acknowledgedByRole": "CURRENT_CENTRAL", "acknowledgedByThreadRef": current_thread_ref, "centralGeneration": active["generation"], "routingRevision": routing["revision"],
        "boundary": {"eventAcknowledged": True, "ledgerUpdated": False, "businessWritePerformed": False, "acknowledgementIsNotValidation": True},
    }
    write_exclusive(target, acknowledgement)
    return acknowledgement


def admit_return(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args); project_id = project_ref(args.project_id); require_writer(args.writer_id)
    ticket_id = return_ticket_ref(args.return_ticket_id, "C08C_RETURN_TICKET_ID_INVALID"); current_thread_ref = ref(args.current_thread_ref, "C08C_THREAD_REF_INVALID"); admission_ref = ref(args.admission_ref, "C08C_RETURN_ADMISSION_REF_INVALID")
    routing = verify_routing(data_root, project_id); current_role(routing, "CURRENT_CENTRAL", current_thread_ref)
    with continuity_lock(data_root, project_id):
        delivery = read_return_delivery(data_root, project_id, ticket_id); submission = delivery["submission"]
        target = return_admission_path(data_root, project_id, ticket_id)
        if target.exists():
            existing = read_json(target, "C08C_RETURN_ADMISSION_INVALID")
            if existing.get("returnTicketId") != ticket_id or existing.get("completionSignalId") != submission["completionSignalId"]:
                raise ContinuityError("C08C_RETURN_ADMISSION_INVALID")
            return {"status": "IDEMPOTENT_RETURN_ADMISSION", "returnState": return_ticket_state(data_root, project_id, ticket_id), "returnTicketId": ticket_id, "taskId": submission["taskId"], "writePerformed": False}, 0
        acknowledgement = ensure_return_event_acknowledgement(data_root, project_id, ticket_id, current_thread_ref, admission_ref, routing)
        recovery_case_id = submission.get("boundary", {}).get("recoveryCaseId")
        superseding_conflict_id = submission.get("boundary", {}).get("supersedingConflictValidationId")
        if superseding_conflict_id is not None:
            signal_result = record_superseding_conflict_completion(
                data_root, project_id, submission["taskId"], submission["completionSignalId"],
                superseding_conflict_id, current_thread_ref,
            )
        elif recovery_case_id is not None:
            signal_result = record_recovery_return_completion(
                data_root, project_id, submission["taskId"], submission["windowId"],
                submission["completionSignalId"], recovery_case_id, current_thread_ref,
            )
        elif validation_reissue_after_abort_allowed(data_root, project_id, ticket_id, submission):
            signal_result = {"receiptId": None}
        else:
            signal_result, signal_code = record_completion_signal(argparse.Namespace(data_root=str(data_root), config=None, project_id=project_id, writer_id=CENTRAL_WRITER, caller_thread_ref=args.current_thread_ref, task_id=submission["taskId"], signal_id=submission["completionSignalId"]))
            if signal_code != 0:
                raise ContinuityError("C08C_RETURN_COMPLETION_SIGNAL_FAILED")
        artifact = {
            "returnSchemaVersion": RETURN_SCHEMA_VERSION, "recordType": "C08_RETURN_ADMISSION_RECEIPT", "createdAt": utc_now(), "projectId": project_id,
            "returnTicketId": ticket_id, "taskId": submission["taskId"], "completionSignalId": submission["completionSignalId"], "eventId": delivery["delivery"]["eventId"],
            "acknowledgementDigest": canonical_digest(acknowledgement), "ledgerReceiptId": signal_result.get("receiptId"), "admissionRef": admission_ref,
            "boundary": {
                "centralReadSummaryOnly": True,
                "taskMovedToNeedsReview": superseding_conflict_id is None,
                "taskDoneDeclared": False, "businessWritePerformed": False,
                "recoveryReturnOnly": recovery_case_id is not None,
                "recoveryCaseId": recovery_case_id,
                "supersedingConflictReturnOnly": superseding_conflict_id is not None,
                "supersedingConflictValidationId": superseding_conflict_id,
                "validationReissueAfterAbort": signal_result.get("receiptId") is None and recovery_case_id is None and superseding_conflict_id is None,
                "businessExecutionResumed": False,
                "occupancyReleased": False,
            },
        }
        write_exclusive(target, artifact)
    return {"status": "RETURN_ADMITTED_FOR_INDEPENDENT_VALIDATION", "returnState": "ADMITTED", "returnTicketId": ticket_id, "taskId": submission["taskId"], "centralPayload": "SUMMARY_ONLY", "doneRecorded": False, "writePerformed": True, "message": "中央已确认回传并登记完成信号；下一步由独立验收槽处理，中央不读取施工原文。"}, 0


def pending_return_ticket_ids(data_root: Path, project_id: str) -> List[str]:
    tickets = return_root(data_root, project_id) / "tickets"
    if not tickets.exists():
        return []
    candidates = []
    for root in tickets.iterdir():
        if not root.is_dir() or not (root / "submission.json").is_file():
            continue
        submission = read_return_submission(data_root, project_id, root.name)
        candidates.append((submission["createdAt"], submission["returnTicketId"]))
    return [ticket_id for _, ticket_id in sorted(candidates)]


def reserve_next_return(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args); project_id = project_ref(args.project_id); require_writer(args.writer_id)
    current_thread_ref = ref(args.current_thread_ref, "C08C_THREAD_REF_INVALID"); validator_thread_ref = ref(args.validator_thread_ref, "C08C_VALIDATOR_THREAD_REF_INVALID")
    routing = verify_routing(data_root, project_id); current_role(routing, "CURRENT_CENTRAL", current_thread_ref)
    with continuity_lock(data_root, project_id):
        active = [
            ticket_id for ticket_id in pending_return_ticket_ids(data_root, project_id)
            if return_reservation_path(data_root, project_id, ticket_id).exists()
            and not return_validation_path(data_root, project_id, ticket_id).exists()
            and not return_validation_aborted(data_root, project_id, ticket_id)
        ]
        if active:
            return {"status": "VALIDATION_SLOT_BUSY", "maxConcurrentValidations": 1, "activeReturnTicketId": active[0], "writePerformed": False}, 0
        for ticket_id in pending_return_ticket_ids(data_root, project_id):
            if not return_admission_path(data_root, project_id, ticket_id).exists() or return_validation_path(data_root, project_id, ticket_id).exists() or return_validation_aborted(data_root, project_id, ticket_id):
                continue
            delivery = read_return_delivery(data_root, project_id, ticket_id); submission = delivery["submission"]
            reservation = {
                "returnSchemaVersion": RETURN_SCHEMA_VERSION, "recordType": "C08_RETURN_VALIDATION_RESERVATION", "createdAt": utc_now(), "projectId": project_id,
                "returnTicketId": ticket_id, "taskId": submission["taskId"], "handbackId": submission["handbackId"], "validatorThreadRef": validator_thread_ref,
                "validatorModel": RETURN_VALIDATOR_MODEL, "centralThreadRef": current_thread_ref, "submissionDigest": canonical_digest(submission),
                "boundary": {"maxConcurrentValidations": 1, "centralGetsNoEvidenceBody": True, "validatorCannotDispatchOrFinalizeDone": True, "businessWritePerformed": False},
            }
            write_exclusive(return_reservation_path(data_root, project_id, ticket_id), reservation)
            return {"status": "RETURN_VALIDATION_RESERVED", "returnState": "VALIDATING", "returnTicketId": ticket_id, "taskId": submission["taskId"], "validatorThreadRef": validator_thread_ref, "validatorModel": RETURN_VALIDATOR_MODEL, "validationInput": {"returnTicketId": ticket_id, "taskId": submission["taskId"], "handbackId": submission["handbackId"]}, "centralPayload": "TICKET_AND_STATUS_ONLY", "writePerformed": True}, 0
    return {"status": "RETURN_INBOX_EMPTY", "maxConcurrentValidations": 1, "writePerformed": False}, 0


def abort_return_validation(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    """Abort a reserved validation that produced no C06 decision.

    This preserves the immutable return and reservation history while freeing
    the single validation slot for a corrected, immutable reissue. It never
    changes the task status or any business object.
    """
    data_root = load_data_root(args); project_id = project_ref(args.project_id); require_writer(args.writer_id)
    ticket_id = return_ticket_ref(args.return_ticket_id, "C08C_RETURN_TICKET_ID_INVALID")
    current_thread_ref = ref(args.current_thread_ref, "C08C_THREAD_REF_INVALID")
    validator_thread_ref = ref(args.validator_thread_ref, "C08C_VALIDATOR_THREAD_REF_INVALID")
    boss_authorization_ref = ref(args.boss_authorization_ref, "C08C_RETURN_VALIDATION_ABORT_AUTHORIZATION_INVALID")
    reason_ref = ref(args.reason_ref, "C08C_RETURN_VALIDATION_ABORT_REASON_INVALID")
    routing = verify_routing(data_root, project_id); current_role(routing, "CURRENT_CENTRAL", current_thread_ref)
    with continuity_lock(data_root, project_id):
        submission = read_return_delivery(data_root, project_id, ticket_id)["submission"]
        reservation = read_json(return_reservation_path(data_root, project_id, ticket_id), "C08C_RETURN_VALIDATION_NOT_RESERVED")
        if reservation.get("validatorThreadRef") != validator_thread_ref or reservation.get("returnTicketId") != ticket_id:
            raise ContinuityError("C08C_RETURN_VALIDATION_RESERVATION_MISMATCH")
        if return_validation_path(data_root, project_id, ticket_id).exists():
            raise ContinuityError("C08C_RETURN_VALIDATION_ALREADY_RECORDED")
        target = return_validation_abort_path(data_root, project_id, ticket_id)
        if target.exists():
            existing = read_return_validation_abort(data_root, project_id, ticket_id)
            if existing.get("validatorThreadRef") == validator_thread_ref and existing.get("reasonRef") == reason_ref:
                return {"status": "IDEMPOTENT_RETURN_VALIDATION_ABORT", "returnState": "VALIDATION_ABORTED", "returnTicketId": ticket_id, "taskId": submission["taskId"], "writePerformed": False}, 0
            raise ContinuityError("C08C_RETURN_VALIDATION_ABORT_ALREADY_RECORDED")
        abort = {
            "returnSchemaVersion": RETURN_SCHEMA_VERSION,
            "recordType": "C08_RETURN_VALIDATION_ABORT",
            "createdAt": utc_now(),
            "projectId": project_id,
            "returnTicketId": ticket_id,
            "taskId": submission["taskId"],
            "handbackId": submission["handbackId"],
            "validatorThreadRef": validator_thread_ref,
            "centralThreadRef": current_thread_ref,
            "bossAuthorizationRef": boss_authorization_ref,
            "reasonRef": reason_ref,
            "reservationDigest": canonical_digest(reservation),
            "boundary": {
                "validationReservationAborted": True,
                "validationResultRecorded": False,
                "returnEvidencePreserved": True,
                "taskStatusChanged": False,
                "businessWritePerformed": False,
            },
        }
        write_exclusive(target, abort)
    return {"status": "RETURN_VALIDATION_ABORTED", "returnState": "VALIDATION_ABORTED", "returnTicketId": ticket_id, "taskId": submission["taskId"], "writePerformed": True, "businessWritePerformed": False}, 0


def record_return_validation(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args); project_id = project_ref(args.project_id); require_writer(args.writer_id)
    ticket_id = return_ticket_ref(args.return_ticket_id, "C08C_RETURN_TICKET_ID_INVALID"); current_thread_ref = ref(args.current_thread_ref, "C08C_THREAD_REF_INVALID"); validator_thread_ref = ref(args.validator_thread_ref, "C08C_VALIDATOR_THREAD_REF_INVALID"); validation_id = ref(args.validation_id, "C08C_VALIDATION_ID_INVALID")
    routing = verify_routing(data_root, project_id); current_role(routing, "CURRENT_CENTRAL", current_thread_ref)
    with continuity_lock(data_root, project_id):
        submission = read_return_delivery(data_root, project_id, ticket_id)["submission"]
        reservation = read_json(return_reservation_path(data_root, project_id, ticket_id), "C08C_RETURN_VALIDATION_NOT_RESERVED")
        if reservation.get("validatorThreadRef") != validator_thread_ref or reservation.get("returnTicketId") != ticket_id:
            raise ContinuityError("C08C_RETURN_VALIDATION_RESERVATION_MISMATCH")
        target = return_validation_path(data_root, project_id, ticket_id)
        if target.exists():
            existing = read_json(target, "C08C_RETURN_VALIDATION_RESULT_INVALID")
            if existing.get("validationId") == validation_id:
                return {"status": "IDEMPOTENT_RETURN_VALIDATION_RESULT", "returnTicketId": ticket_id, "validationId": validation_id, "writePerformed": False}, 0
            raise ContinuityError("C08C_RETURN_VALIDATION_ALREADY_RECORDED")
        from independent_handover_validator import verify_decision_data
        try:
            decision = verify_decision_data(data_root, project_id, validation_id)
        except Exception as error:
            raise ContinuityError(f"C08C_RETURN_VALIDATION_DECISION_UNVERIFIED:{error}")
        if decision.get("taskId") != submission["taskId"] or decision.get("handbackId") != submission["handbackId"] or decision.get("source", {}).get("returnTicketId") != ticket_id:
            raise ContinuityError("C08C_RETURN_VALIDATION_DECISION_MISMATCH")
        outcome = decision["outcome"]
        alert_kind = "BOSS_FINAL_DONE_APPROVAL" if outcome == "PASS_PENDING_BOSS_APPROVAL" else "BOSS_EXCEPTION_REVIEW"
        artifact = {"returnSchemaVersion": RETURN_SCHEMA_VERSION, "recordType": "C08_RETURN_VALIDATION_RESULT", "createdAt": utc_now(), "projectId": project_id, "returnTicketId": ticket_id, "taskId": submission["taskId"], "validationId": validation_id, "outcome": outcome, "decisionDigest": canonical_digest(decision), "alertKind": alert_kind, "boundary": {"centralReceivesOutcomeOnly": True, "bossFinalApprovalRequired": outcome == "PASS_PENDING_BOSS_APPROVAL", "taskDoneDeclared": False, "businessWritePerformed": False}}
        write_exclusive(target, artifact)
    return {"status": "RETURN_VALIDATION_RECORDED", "returnState": "VALIDATION_RECORDED", "returnTicketId": ticket_id, "taskId": submission["taskId"], "validationId": validation_id, "outcome": outcome, "alertKind": alert_kind, "doneRecorded": False, "centralPayload": "OUTCOME_AND_RECEIPT_ONLY", "writePerformed": True}, 0


def current_role(routing: Dict[str, Any], role: str, thread_ref: str) -> Dict[str, Any]:
    active = routing["roles"].get(role)
    if not isinstance(active, dict) or active.get("status") != "ACTIVE" or active.get("activeThreadRef") != thread_ref:
        raise ContinuityError("C08C_CALLER_IS_NOT_CURRENT_ROLE")
    return active


def list_pending(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args); project_id = project_ref(args.project_id); thread = ref(args.current_thread_ref, "C08C_THREAD_REF_INVALID")
    routing = verify_routing(data_root, project_id); active = current_role(routing, "CURRENT_CENTRAL", thread)
    events = []
    for event_id in pending_event_ids(data_root, project_id):
        event = read_json(event_path(data_root, project_id, event_id), "C08C_TASK_EVENT_INVALID")
        receipt = read_json(event_receipt_path(data_root, project_id, event_id), "C08C_TASK_EVENT_RECEIPT_MISSING")
        if receipt.get("artifact") != event or receipt.get("artifactDigest") != canonical_digest(event):
            raise ContinuityError("C08C_TASK_EVENT_INTEGRITY_INVALID")
        events.append({key: event[key] for key in ("eventId", "taskIdentity", "eventType", "sourceWindow", "decisionRef", "evidenceRefs", "summary")})
    return {"status": "PENDING_TASK_EVENTS_READ", "projectId": project_id, "currentCentralGeneration": active["generation"], "routingRevision": routing["revision"], "pendingCount": len(events), "events": events, "writePerformed": False}, 0


def acknowledge_event(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args); project_id = project_ref(args.project_id); require_writer(args.writer_id)
    event_id = ref(args.event_id, "C08C_EVENT_ID_INVALID"); thread = ref(args.current_thread_ref, "C08C_THREAD_REF_INVALID"); acknowledgement_ref = ref(args.acknowledgement_ref, "C08C_ACKNOWLEDGEMENT_REF_INVALID")
    routing = verify_routing(data_root, project_id); active = current_role(routing, "CURRENT_CENTRAL", thread)
    event = read_json(event_path(data_root, project_id, event_id), "C08C_TASK_EVENT_NOT_FOUND")
    target = acknowledgement_path(data_root, project_id, event_id)
    if target.exists():
        existing = read_json(target, "C08C_ACKNOWLEDGEMENT_INVALID")
        if existing.get("acknowledgementRef") == acknowledgement_ref and existing.get("acknowledgedByThreadRef") == thread:
            return {"status": "IDEMPOTENT_TASK_EVENT_ACKNOWLEDGEMENT", "eventId": event_id, "writePerformed": False}, 0
        raise ContinuityError("C08C_EVENT_ALREADY_ACKNOWLEDGED")
    artifact = {
        "schemaVersion": SCHEMA_VERSION, "recordType": "C08_TASK_EVENT_ACKNOWLEDGEMENT", "createdAt": utc_now(),
        "projectId": project_id, "eventId": event_id, "eventDigest": canonical_digest(event),
        "acknowledgementRef": acknowledgement_ref, "acknowledgedByRole": "CURRENT_CENTRAL",
        "acknowledgedByThreadRef": thread, "centralGeneration": active["generation"], "routingRevision": routing["revision"],
        "boundary": {"eventAcknowledged": True, "ledgerUpdated": False, "businessWritePerformed": False},
    }
    write_exclusive(target, artifact)
    return {"status": "TASK_EVENT_ACKNOWLEDGED", "eventId": event_id, "centralGeneration": active["generation"], "writePerformed": True, "ledgerUpdated": False}, 0


def verify_all(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args); project_id = project_ref(args.project_id); routing = verify_routing(data_root, project_id); verified_ledger(data_root, project_id)
    for event_id in sorted(path.stem for path in (data_root / EVENT_ROOT / project_id / "inbox").glob("*.json")) if (data_root / EVENT_ROOT / project_id / "inbox").exists() else []:
        event = read_json(event_path(data_root, project_id, event_id), "C08C_TASK_EVENT_INVALID")
        receipt = read_json(event_receipt_path(data_root, project_id, event_id), "C08C_TASK_EVENT_RECEIPT_MISSING")
        if receipt.get("artifact") != event or receipt.get("artifactDigest") != canonical_digest(event):
            raise ContinuityError("C08C_TASK_EVENT_INTEGRITY_INVALID")
    return_tickets = pending_return_ticket_ids(data_root, project_id)
    for ticket_id in return_tickets:
        read_return_submission(data_root, project_id, ticket_id)
        if return_delivery_path(data_root, project_id, ticket_id).exists():
            read_return_delivery(data_root, project_id, ticket_id)
        if return_validation_abort_path(data_root, project_id, ticket_id).exists():
            read_return_validation_abort(data_root, project_id, ticket_id)
    return {"status": "C08_ROLE_CONTINUITY_VERIFIED", "projectId": project_id, "routingRevision": routing["revision"], "centralGeneration": routing["roles"]["CURRENT_CENTRAL"]["generation"], "adjudicationGeneration": routing["roles"]["CURRENT_ADJUDICATION"]["generation"] if routing["roles"]["CURRENT_ADJUDICATION"] else None, "pendingEventCount": len(pending_event_ids(data_root, project_id)), "returnTicketCount": len(return_tickets), "writePerformed": False}, 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="C08 logical-role continuity and durable event inbox")
    source = parser.add_mutually_exclusive_group(required=True); source.add_argument("--data-root"); source.add_argument("--config")
    parser.add_argument("--project-id", required=True); parser.add_argument("--writer-id")
    commands = parser.add_subparsers(dest="command", required=True)
    model_parser = commands.add_parser("record-model")
    model_parser.add_argument("--current-thread-ref", required=True)
    model_parser.add_argument("--runtime-project-id", required=True)
    model_parser.add_argument("--model", required=True)
    model_parser.add_argument("--evidence-ref", required=True)
    initialize_parser = commands.add_parser("initialize"); initialize_parser.add_argument("--calling-thread-ref", required=True); initialize_parser.add_argument("--central-thread-ref", required=True); initialize_parser.add_argument("--runtime-project-id", required=True); initialize_parser.add_argument("--execution-map-ref", required=True)
    prepare_parser = commands.add_parser("prepare-handover"); prepare_parser.add_argument("--request", required=True)
    refresh_parser = commands.add_parser("refresh-handover")
    for field in ("handover-id", "revision-id", "current-thread-ref"):
        refresh_parser.add_argument("--" + field, required=True)
    for operation in ("prepare-successor", "record-successor", "record-handover-readback", "record-handover-opened"):
        operation_parser = commands.add_parser(operation)
        operation_parser.add_argument("--handover-id", required=True)
        operation_parser.add_argument("--current-thread-ref", required=True)
        operation_parser.add_argument("--authorization-ref" if operation == "prepare-successor" else "--proof", required=True)
        if operation == "prepare-successor":
            operation_parser.add_argument("--model")
    initialize_parser.add_argument("--model")
    activation_parser = commands.add_parser("activate-successor"); activation_parser.add_argument("--handover-id", required=True); activation_parser.add_argument("--activation", required=True)
    event_parser = commands.add_parser("submit-event"); event_parser.add_argument("--event", required=True)
    return_parser = commands.add_parser("submit-return"); return_parser.add_argument("--handback", required=True)
    reconcile_parser = commands.add_parser("reconcile-return"); reconcile_parser.add_argument("--return-ticket-id", required=True)
    policy_parser = commands.add_parser("configure-return-policy"); policy_parser.add_argument("--policy", required=True); policy_parser.add_argument("--current-thread-ref", required=True)
    admit_parser = commands.add_parser("admit-return"); admit_parser.add_argument("--return-ticket-id", required=True); admit_parser.add_argument("--current-thread-ref", required=True); admit_parser.add_argument("--admission-ref", required=True)
    reserve_parser = commands.add_parser("reserve-next-return"); reserve_parser.add_argument("--current-thread-ref", required=True); reserve_parser.add_argument("--validator-thread-ref", required=True)
    abort_parser = commands.add_parser("abort-return-validation"); abort_parser.add_argument("--return-ticket-id", required=True); abort_parser.add_argument("--current-thread-ref", required=True); abort_parser.add_argument("--validator-thread-ref", required=True); abort_parser.add_argument("--boss-authorization-ref", required=True); abort_parser.add_argument("--reason-ref", required=True)
    result_parser = commands.add_parser("record-return-validation"); result_parser.add_argument("--return-ticket-id", required=True); result_parser.add_argument("--current-thread-ref", required=True); result_parser.add_argument("--validator-thread-ref", required=True); result_parser.add_argument("--validation-id", required=True)
    pending_parser = commands.add_parser("list-pending"); pending_parser.add_argument("--current-thread-ref", required=True)
    ack_parser = commands.add_parser("acknowledge-event"); ack_parser.add_argument("--event-id", required=True); ack_parser.add_argument("--current-thread-ref", required=True); ack_parser.add_argument("--acknowledgement-ref", required=True)
    commands.add_parser("verify")
    return parser.parse_args()


def dispatch(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    if args.command in {"prepare-successor", "record-successor", "record-handover-readback", "record-handover-opened"}:
        from handover_automation import operate
        return operate(args)
    if args.command == "record-model":
        return record_model(args)
    if args.command == "refresh-handover":
        return refresh_handover(args)
    return {"initialize": initialize, "prepare-handover": prepare_handover, "activate-successor": activate_successor, "submit-event": submit_event, "submit-return": submit_return, "reconcile-return": reconcile_return, "configure-return-policy": configure_return_policy, "admit-return": admit_return, "reserve-next-return": reserve_next_return, "abort-return-validation": abort_return_validation, "record-return-validation": record_return_validation, "list-pending": list_pending, "acknowledge-event": acknowledge_event, "verify": verify_all}[args.command](args)


def main() -> int:
    args = parse_args()
    try:
        result, code = dispatch(args); print_result(result); return code
    except (ContinuityError, LedgerError, C02Error) as error:
        print_result({"status": "REFUSED", "reason": str(error), "writePerformed": False, "ledgerUpdated": False, "roleSwitched": False, "businessWritePerformed": False})
        return 2


if __name__ == "__main__":
    # Runtime helpers must share this module's exception and lock definitions.
    sys.modules["role_continuity_controller"] = sys.modules[__name__]
    sys.exit(main())
