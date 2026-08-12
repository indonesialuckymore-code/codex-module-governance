#!/usr/bin/env python3
"""C08 role continuity and durable task-event inbox.

This controller keeps logical roles stable while Codex tasks are replaced. It does
not execute business work and it never treats chat delivery as the source of truth.
"""

from __future__ import annotations

import argparse
import copy
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
    load_ledger,
    verify_ledger,
    window_current_task_id,
)


SCHEMA_VERSION = "0.15.0"
ROLES = {"CURRENT_CENTRAL", "CURRENT_ADJUDICATION"}
ROLE_MODELS = {"CURRENT_CENTRAL": "gpt-5.6-sol", "CURRENT_ADJUDICATION": "gpt-5.6-sol"}
EVENT_TYPES = {"IN_PROGRESS", "BLOCKED_FOR_DECISION", "DECISION_APPLIED", "READY_FOR_VALIDATION", "SUB_AGENT_APPEND_REQUEST"}
ROOT = Path("role-continuity")
EVENT_ROOT = Path("task-events")
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
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        write_exclusive(path, {"pid": os.getpid(), "createdAt": utc_now()})
    except ContinuityError:
        raise ContinuityError("C08C_OPERATION_LOCK_PRESENT")
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


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
    write_exclusive(routing_receipts(data_root, project_id) / f"{receipt_id}.json", receipt)
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
            or receipt.get("operation") not in {"INITIALIZE_ROLE_ROUTING", "ACTIVATE_ROLE_SUCCESSOR"}
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
    central_thread = ref(args.central_thread_ref, "C08C_CENTRAL_THREAD_REF_INVALID")
    runtime_project = ref(args.runtime_project_id, "C08C_RUNTIME_PROJECT_ID_INVALID")
    execution_map = ref(args.execution_map_ref, "C08C_EXECUTION_MAP_REF_INVALID")
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
                "model": ROLE_MODELS["CURRENT_CENTRAL"], "status": "ACTIVE", "activatedAt": utc_now(),
                "previousThreadRefs": [], "source": "INITIAL_CENTRAL", "executionMapRef": execution_map,
            }
        routing, receipt_id = commit_routing(data_root, project_id, None, "INITIALIZE_ROLE_ROUTING", mutate)
    return {"status": "ROLE_ROUTING_INITIALIZED", "projectId": project_id, "revision": routing["revision"], "centralGeneration": 1, "receiptId": receipt_id, "writePerformed": True}, 0


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
    task_status = {task_id: task.get("status") for task_id, task in sorted(ledger["tasks"].items())}
    windows = [
        {
            **{key: value.get(key) for key in ("windowId", "taskId", "runtimeThreadRef", "generation", "status", "assignmentCount", "maxAssignments")},
            "currentTaskId": window_current_task_id(value),
        }
        for value in ledger["windows"].values()
    ]
    open_adjudications = sorted(key for key, value in ledger["adjudications"].items() if value.get("stage") != "BOSS_DECIDED")
    package = {
        "schemaVersion": SCHEMA_VERSION, "recordType": "C08_ROLE_HANDOVER_PACKAGE", "createdAt": utc_now(),
        **request, "sourceInputDigest": input_digest, "sourceRole": source_role,
        "sourceGeneration": source["generation"], "sourceThreadRef": source["activeThreadRef"],
        "targetGeneration": 1 if current is None else current["generation"] + 1,
        "routingSnapshot": {"revision": routing["revision"], "digest": canonical_digest(routing), "roles": routing["roles"]},
        "ledgerSnapshot": {"revision": ledger["revision"], "digest": canonical_digest(ledger), "taskStatus": task_status, "windows": windows, "hardStops": ledger["hardStops"]},
        "continuitySnapshot": {"pendingTaskEventIds": pending_event_ids(data_root, project_id), "openAdjudicationRefs": open_adjudications},
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
    model = str(activation["model"]).strip()
    if model != ROLE_MODELS[package["role"]]:
        raise ContinuityError("C08C_ROLE_MODEL_MISMATCH")
    successor = ref(activation["successorThreadRef"], "C08C_SUCCESSOR_THREAD_REF_INVALID")
    if successor == package["sourceThreadRef"]:
        raise ContinuityError("C08C_SUCCESSOR_MUST_BE_NEW_TASK")
    return {"successorThreadRef": successor, "runtimeProjectId": ref(activation["runtimeProjectId"], "C08C_RUNTIME_PROJECT_ID_INVALID"), "model": model}


def activate_successor(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args); project_id = project_ref(args.project_id); require_writer(args.writer_id)
    handover_id = ref(args.handover_id, "C08C_HANDOVER_ID_INVALID"); package = load_handover(data_root, project_id, handover_id)
    raw, input_digest = private_json(args.activation, data_root, "C08C_ACTIVATION_INVALID_JSON"); activation = validate_activation(raw, package)
    root = handover_root(data_root, project_id, handover_id); target = root / "activation.json"
    if target.exists():
        existing = read_json(target, "C08C_ACTIVATION_INVALID")
        if existing.get("sourceInputDigest") == input_digest:
            return {"status": "IDEMPOTENT_SUCCESSOR_ACTIVATION", "handoverId": handover_id, "writePerformed": False}, 0
        raise ContinuityError("C08C_HANDOVER_ALREADY_ACTIVATED")
    with continuity_lock(data_root, project_id):
        routing = verify_routing(data_root, project_id); ledger = verified_ledger(data_root, project_id)
        current_continuity = {
            "pendingTaskEventIds": pending_event_ids(data_root, project_id),
            "openAdjudicationRefs": sorted(key for key, value in ledger["adjudications"].items() if value.get("stage") != "BOSS_DECIDED"),
        }
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
        def mutate(after: Dict[str, Any]) -> None:
            after["roles"][package["role"]] = {
                "generation": package["targetGeneration"], "activeThreadRef": activation["successorThreadRef"],
                "runtimeProjectId": activation["runtimeProjectId"], "model": activation["model"], "status": "ACTIVE",
                "activatedAt": utc_now(), "previousThreadRefs": previous, "source": package["mode"],
                "executionMapRef": package["executionMapRef"],
            }
        updated, routing_receipt = commit_routing(data_root, project_id, routing, "ACTIVATE_ROLE_SUCCESSOR", mutate)
        artifact = {
            "schemaVersion": SCHEMA_VERSION, "recordType": "C08_ROLE_SUCCESSOR_ACTIVATED", "createdAt": utc_now(),
            "projectId": project_id, "handoverId": handover_id, "role": package["role"], **activation,
            "sourceThreadRef": package["sourceThreadRef"], "sourceGeneration": package["sourceGeneration"],
            "targetGeneration": package["targetGeneration"], "sourceInputDigest": input_digest,
            "routingReceiptId": routing_receipt, "routingRevision": updated["revision"],
            "boundary": {"onlyOneActiveRole": True, "sourceIsReadOnlyForwarder": True, "businessWritePerformed": False},
        }
        receipt = {"schemaVersion": SCHEMA_VERSION, "recordType": "C08_IMMUTABLE_ROLE_ACTIVATION_RECEIPT", "createdAt": utc_now(), "projectId": project_id, "handoverId": handover_id, "artifactDigest": canonical_digest(artifact), "artifact": artifact}
        write_exclusive(root / "receipt-000001-activation.json", receipt)
        write_exclusive(target, artifact)
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
    return {"status": "C08_ROLE_CONTINUITY_VERIFIED", "projectId": project_id, "routingRevision": routing["revision"], "centralGeneration": routing["roles"]["CURRENT_CENTRAL"]["generation"], "adjudicationGeneration": routing["roles"]["CURRENT_ADJUDICATION"]["generation"] if routing["roles"]["CURRENT_ADJUDICATION"] else None, "pendingEventCount": len(pending_event_ids(data_root, project_id)), "writePerformed": False}, 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="C08 logical-role continuity and durable event inbox")
    source = parser.add_mutually_exclusive_group(required=True); source.add_argument("--data-root"); source.add_argument("--config")
    parser.add_argument("--project-id", required=True); parser.add_argument("--writer-id")
    commands = parser.add_subparsers(dest="command", required=True)
    initialize_parser = commands.add_parser("initialize"); initialize_parser.add_argument("--central-thread-ref", required=True); initialize_parser.add_argument("--runtime-project-id", required=True); initialize_parser.add_argument("--execution-map-ref", required=True)
    prepare_parser = commands.add_parser("prepare-handover"); prepare_parser.add_argument("--request", required=True)
    activation_parser = commands.add_parser("activate-successor"); activation_parser.add_argument("--handover-id", required=True); activation_parser.add_argument("--activation", required=True)
    event_parser = commands.add_parser("submit-event"); event_parser.add_argument("--event", required=True)
    pending_parser = commands.add_parser("list-pending"); pending_parser.add_argument("--current-thread-ref", required=True)
    ack_parser = commands.add_parser("acknowledge-event"); ack_parser.add_argument("--event-id", required=True); ack_parser.add_argument("--current-thread-ref", required=True); ack_parser.add_argument("--acknowledgement-ref", required=True)
    commands.add_parser("verify")
    return parser.parse_args()


def dispatch(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    return {"initialize": initialize, "prepare-handover": prepare_handover, "activate-successor": activate_successor, "submit-event": submit_event, "list-pending": list_pending, "acknowledge-event": acknowledge_event, "verify": verify_all}[args.command](args)


def main() -> int:
    args = parse_args()
    try:
        result, code = dispatch(args); print_result(result); return code
    except (ContinuityError, LedgerError, C02Error) as error:
        print_result({"status": "REFUSED", "reason": str(error), "writePerformed": False, "ledgerUpdated": False, "roleSwitched": False, "businessWritePerformed": False})
        return 2


if __name__ == "__main__":
    sys.exit(main())
