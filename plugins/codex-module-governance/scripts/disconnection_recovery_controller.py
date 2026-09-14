#!/usr/bin/env python3
"""C08 disconnection recovery controller. Governance recovery is not business resumption."""

from __future__ import annotations

import argparse
import json
import os
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
    commit_mutation,
    ledger_lock,
    load_ledger,
    verify_ledger,
)
from independent_handover_validator import HandoverValidationError, verify_finalization_data


SCHEMA_VERSION = "0.8.0"
CASES_DIRECTORY = Path("recovery-cases")
RECEIPTS_DIRECTORY = "receipts"
ARTIFACTS = {
    "freeze": ("freeze.json", "receipt-000000-freeze"),
    "takeover": ("takeover.json", "receipt-000001-takeover"),
    "decision": ("decision.json", "receipt-000002-decision"),
    "resume": ("resume.json", "receipt-000003-resume"),
    "release": ("release.json", "receipt-000003-release"),
}


class RecoveryError(Exception):
    """Safe refusal. It never resumes business execution."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def print_result(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def require_exact(value: Any, keys: set[str], error: str) -> Dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise RecoveryError(error)
    return value


def require_reference(value: Any, error: str) -> str:
    if not isinstance(value, str) or not REFERENCE_PATTERN.fullmatch(value.strip()):
        raise RecoveryError(error)
    return value.strip()


def require_optional_reference(value: Any, error: str) -> Optional[str]:
    return None if value is None else require_reference(value, error)


def require_project_id(value: Any) -> str:
    if not isinstance(value, str) or not PROJECT_ID_PATTERN.fullmatch(value.strip()):
        raise RecoveryError("C08_PROJECT_ID_INVALID")
    return value.strip()


def require_task_id(value: Any, error: str) -> str:
    if not isinstance(value, str) or not TASK_ID_PATTERN.fullmatch(value.strip()):
        raise RecoveryError(error)
    return value.strip()


def require_references(value: Any, error: str, allow_empty: bool = False) -> List[str]:
    if not isinstance(value, list) or len(value) > 40 or (not allow_empty and not value):
        raise RecoveryError(error)
    return [require_reference(item, error) for item in value]


def case_directory(data_root: Path, project_id: str, case_id: str) -> Path:
    return data_root / CASES_DIRECTORY / project_id / case_id


def artifact_path(data_root: Path, project_id: str, case_id: str, stage: str) -> Path:
    return case_directory(data_root, project_id, case_id) / ARTIFACTS[stage][0]


def receipt_path(data_root: Path, project_id: str, case_id: str, stage: str) -> Path:
    return case_directory(data_root, project_id, case_id) / RECEIPTS_DIRECTORY / f"{ARTIFACTS[stage][1]}.json"


def write_json_exclusive(path: Path, payload: Dict[str, Any]) -> None:
    try:
        with path.open("x", encoding="utf-8") as output:
            output.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    except FileExistsError:
        raise RecoveryError("C08_IMMUTABLE_TARGET_ALREADY_EXISTS")


def read_json(path: Path, error: str) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise RecoveryError(error)
    if not isinstance(payload, dict):
        raise RecoveryError(error)
    return payload


def load_private(raw_path: str, data_root: Path, error: str) -> Tuple[Dict[str, Any], str]:
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file() or not is_within(path, data_root):
        raise RecoveryError("C08_PRIVATE_INPUT_REQUIRED_INSIDE_DATA_ROOT")
    payload = read_json(path, error)
    return payload, canonical_digest(payload)


@contextmanager
def recovery_lock(data_root: Path, project_id: str) -> Iterator[None]:
    root = data_root / CASES_DIRECTORY / project_id
    root.mkdir(parents=True, exist_ok=True)
    path = root / ".disconnection-recovery-controller.lock"
    try:
        write_json_exclusive(path, {"recordType": "C08_OPERATION_LOCK", "projectId": project_id, "createdAt": utc_now(), "pid": os.getpid()})
    except RecoveryError:
        raise RecoveryError("C08_OPERATION_LOCK_PRESENT")
    try:
        yield
    finally:
        if path.exists():
            path.unlink()


def require_writer(writer_id: Optional[str]) -> None:
    if writer_id != CENTRAL_WRITER:
        raise RecoveryError("C08_WRITER_NOT_AUTHORIZED")


def verified_ledger(data_root: Path, project_id: str) -> Dict[str, Any]:
    try:
        _, code = verify_ledger(argparse.Namespace(data_root=str(data_root), config=None, project_id=project_id))
        if code != 0:
            raise RecoveryError("C08_LEDGER_INTEGRITY_UNVERIFIED")
        return load_ledger(data_root, project_id)
    except (LedgerError, C02Error) as error:
        raise RecoveryError(f"C08_LEDGER_{error}")


def ledger_mutation(result: Dict[str, Any], data_root: Path, project_id: str) -> Dict[str, Any]:
    ledger = load_ledger(data_root, project_id)
    return {"receiptId": result["receiptId"], "afterRevision": result["revision"], "afterLedgerDigest": canonical_digest(ledger)}


def verify_linked_ledger(data_root: Path, project_id: str, mutation: Dict[str, Any], operation: str) -> None:
    receipt = read_json(data_root / "module-ledgers" / project_id / "receipts" / f"{mutation.get('receiptId')}.json", "C08_LINKED_LEDGER_RECEIPT_MISSING")
    if (
        receipt.get("operation") != operation or receipt.get("afterRevision") != mutation.get("afterRevision")
        or receipt.get("afterLedgerDigest") != mutation.get("afterLedgerDigest")
        or canonical_digest(receipt.get("afterLedger", {})) != mutation.get("afterLedgerDigest")
    ):
        raise RecoveryError("C08_LINKED_LEDGER_RECEIPT_INVALID")


def make_receipt(stage: str, operation: str, project_id: str, case_id: str, artifact: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION, "recordType": f"C08_IMMUTABLE_{stage.upper()}_RECEIPT",
        "receiptId": ARTIFACTS[stage][1], "createdAt": utc_now(), "projectId": project_id,
        "caseId": case_id, "writerId": CENTRAL_WRITER, "operation": operation,
        "artifactDigest": canonical_digest(artifact), "artifact": artifact,
    }


def persist_stage(data_root: Path, project_id: str, case_id: str, stage: str, operation: str, artifact: Dict[str, Any]) -> None:
    write_json_exclusive(receipt_path(data_root, project_id, case_id, stage), make_receipt(stage, operation, project_id, case_id, artifact))
    write_json_exclusive(artifact_path(data_root, project_id, case_id, stage), artifact)


def verify_stage(data_root: Path, project_id: str, case_id: str, stage: str, operation: str) -> Dict[str, Any]:
    artifact = read_json(artifact_path(data_root, project_id, case_id, stage), f"C08_{stage.upper()}_NOT_FOUND_OR_INVALID")
    receipt = read_json(receipt_path(data_root, project_id, case_id, stage), f"C08_{stage.upper()}_RECEIPT_MISSING")
    if (
        artifact.get("projectId") != project_id or artifact.get("caseId") != case_id
        or receipt.get("operation") != operation or receipt.get("artifact") != artifact
        or receipt.get("artifactDigest") != canonical_digest(artifact)
    ):
        raise RecoveryError(f"C08_{stage.upper()}_INTEGRITY_INVALID")
    verify_linked_ledger(data_root, project_id, artifact["ledgerMutation"], artifact["ledgerOperation"])
    return artifact


def public(status: str, case_id: str, write_performed: bool, **extra: Any) -> Dict[str, Any]:
    return {
        "status": status, "caseId": case_id, "writePerformed": write_performed,
        "businessExecutionResumed": False, "newDispatchAllowed": False,
        "testCreated": False, "businessWriteAllowed": False,
        "occupancyReleased": status in {"OCCUPANCY_RELEASED_AFTER_DONE", "OCCUPANCY_RELEASED_AFTER_CANCEL"},
        "message": "C08 只恢复治理控制；未自动恢复施工、创建 TEST 或修改业务系统。",
        **extra,
    }


def validate_incident(payload: Dict[str, Any], project_id: str) -> Dict[str, Any]:
    incident = require_exact(payload, {"incidentSchemaVersion", "recordType", "caseId", "projectId", "incidentType", "targetId", "taskId", "detectedBy", "detectionRefs", "reasonRef"}, "C08_INCIDENT_SCHEMA_UNSUPPORTED")
    if incident["incidentSchemaVersion"] != SCHEMA_VERSION or incident["recordType"] != "C08_DISCONNECTION_INCIDENT":
        raise RecoveryError("C08_INCIDENT_SCHEMA_UNSUPPORTED")
    if require_project_id(incident["projectId"]) != project_id:
        raise RecoveryError("C08_INCIDENT_PROJECT_MISMATCH")
    incident_type = str(incident["incidentType"]).strip().upper()
    if incident_type not in {"CENTRAL", "TASK_WINDOW", "SUB_AGENT"}:
        raise RecoveryError("C08_INCIDENT_TYPE_INVALID")
    task_id = require_optional_reference(incident["taskId"], "C08_TASK_ID_INVALID")
    if task_id is not None and not TASK_ID_PATTERN.fullmatch(task_id):
        raise RecoveryError("C08_TASK_ID_INVALID")
    if incident_type == "CENTRAL" and task_id is not None:
        raise RecoveryError("C08_CENTRAL_INCIDENT_TASK_MUST_BE_NULL")
    if incident_type != "CENTRAL" and task_id is None:
        raise RecoveryError("C08_TARGET_INCIDENT_TASK_REQUIRED")
    target_id = require_reference(incident["targetId"], "C08_TARGET_ID_INVALID")
    if incident_type == "CENTRAL" and target_id != CENTRAL_WRITER:
        raise RecoveryError("C08_CENTRAL_TARGET_INVALID")
    return {
        "caseId": require_reference(incident["caseId"], "C08_CASE_ID_INVALID"), "projectId": project_id,
        "incidentType": incident_type, "targetId": target_id, "taskId": task_id,
        "detectedBy": require_reference(incident["detectedBy"], "C08_DETECTOR_INVALID"),
        "detectionRefs": require_references(incident["detectionRefs"], "C08_DETECTION_REFS_INVALID"),
        "reasonRef": require_reference(incident["reasonRef"], "C08_REASON_REF_INVALID"),
    }


def affected_context(ledger: Dict[str, Any], incident: Dict[str, Any]) -> Dict[str, Any]:
    incident_type, target_id, task_id = incident["incidentType"], incident["targetId"], incident["taskId"]
    window_ids: List[str] = []
    sub_agent_ids: List[str] = []
    if incident_type == "TASK_WINDOW":
        window = ledger["windows"].get(target_id)
        if not isinstance(window, dict) or task_id not in {window.get("taskId"), window.get("currentTaskId"), window.get("reservedForTaskId")}:
            raise RecoveryError("C08_WINDOW_TASK_MISMATCH")
        window_ids = [target_id]
        sub_agent_ids = [key for key, value in ledger["subAgents"].items() if value.get("windowId") == target_id]
    elif incident_type == "SUB_AGENT":
        agent = ledger["subAgents"].get(target_id)
        if not isinstance(agent, dict):
            raise RecoveryError("C08_SUB_AGENT_NOT_FOUND")
        window = ledger["windows"].get(agent.get("windowId"))
        if not isinstance(window, dict) or window.get("taskId") != task_id:
            raise RecoveryError("C08_SUB_AGENT_TASK_MISMATCH")
        window_ids = [agent["windowId"]]
        sub_agent_ids = [target_id]
    else:
        window_ids = [key for key, value in ledger["windows"].items() if value.get("status") not in {"CLOSED", "CANCELLED"}]
        sub_agent_ids = [key for key, value in ledger["subAgents"].items() if value.get("status") not in {"CLOSED", "CANCELLED"}]
    if task_id is not None and task_id not in ledger["tasks"]:
        raise RecoveryError("C08_TASK_NOT_FOUND")
    return {"windowIds": window_ids, "subAgentIds": sub_agent_ids}


def freeze(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args)
    project_id = require_project_id(args.project_id)
    raw, input_digest = load_private(args.incident, data_root, "C08_INCIDENT_INVALID_JSON")
    incident = validate_incident(raw, project_id)
    ledger = verified_ledger(data_root, project_id)
    context = affected_context(ledger, incident)
    if args.dry_run:
        return public("READY_TO_FREEZE", incident["caseId"], False, occupancyCount=len(ledger["objectOccupancies"])), 0
    require_writer(args.writer_id)
    case_root = case_directory(data_root, project_id, incident["caseId"])
    if case_root.exists():
        existing = verify_stage(data_root, project_id, incident["caseId"], "freeze", "C08_FREEZE_DISCONNECTED_CONTEXT")
        if existing.get("sourceInputDigest") == input_digest:
            return public("IDEMPOTENT_FREEZE", incident["caseId"], False, recoveryEpoch=existing["recoveryEpoch"]), 0
        raise RecoveryError("C08_CASE_ID_ALREADY_EXISTS_WITH_DIFFERENT_CONTENT")
    with recovery_lock(data_root, project_id), ledger_lock(data_root, project_id):
        before = load_ledger(data_root, project_id)
        if before["recovery"].get("state") not in {"NORMAL", "CLOSED"}:
            raise RecoveryError("C08_ANOTHER_RECOVERY_CASE_ACTIVE")
        context = affected_context(before, incident)
        epoch = int(before["recovery"].get("recoveryEpoch", 0)) + 1
        task_status_before = before["tasks"].get(incident["taskId"], {}).get("status") if incident["taskId"] else None
        occupancy_digest = canonical_digest(before["objectOccupancies"])

        def mutate(after: Dict[str, Any]) -> None:
            after["recovery"] = {
                "state": "FROZEN_AWAITING_RECOVERY", "recoveryEpoch": epoch,
                "activeCaseId": incident["caseId"], "incidentType": incident["incidentType"],
                "frozenAt": utc_now(), "businessExecutionResumed": False,
            }
            for window_id in context["windowIds"]:
                after["windows"][window_id]["status"] = "DISCONNECTED" if incident["incidentType"] != "CENTRAL" else "FROZEN"
            for agent_id in context["subAgentIds"]:
                after["subAgents"][agent_id]["status"] = "DISCONNECTED" if agent_id == incident["targetId"] else "FROZEN"
            if incident["taskId"] is not None:
                task = after["tasks"][incident["taskId"]]
                if task["status"] not in {"DONE", "CANCELLED"}:
                    source = task["status"]
                    task["status"] = "BLOCKED"
                    task["history"].append({"at": utc_now(), "event": "C08_DISCONNECTION_FROZEN", "caseId": incident["caseId"], "from": source, "to": "BLOCKED", "by": CENTRAL_WRITER})

        result = commit_mutation(data_root, project_id, before, CENTRAL_WRITER, "C08_FREEZE_DISCONNECTED_CONTEXT", {"caseId": incident["caseId"], "incidentType": incident["incidentType"], "occupancyReleased": False}, mutate, caller_thread_ref=args.caller_thread_ref)
        mutation = ledger_mutation(result, data_root, project_id)
        artifact = {
            "schemaVersion": SCHEMA_VERSION, "recordType": "C08_FROZEN_RECOVERY_SNAPSHOT",
            "createdAt": utc_now(), **incident, "sourceInputDigest": input_digest,
            "recoveryEpoch": epoch, "taskStatusBefore": task_status_before,
            "affectedContext": context, "snapshot": {
                "sourceLedgerRevision": before["revision"], "sourceLedgerDigest": canonical_digest(before),
                "latestReceiptId": before["latestReceiptId"], "task": before["tasks"].get(incident["taskId"]) if incident["taskId"] else None,
                "windows": {key: before["windows"][key] for key in context["windowIds"]},
                "subAgents": {key: before["subAgents"][key] for key in context["subAgentIds"]},
                "objectOccupanciesDigest": occupancy_digest, "objectOccupancyCount": len(before["objectOccupancies"]),
                "evidenceRefIds": sorted(before["evidenceRefs"]), "adjudicationIds": sorted(before["adjudications"]),
            },
            "ledgerOperation": "C08_FREEZE_DISCONNECTED_CONTEXT", "ledgerMutation": mutation,
            "boundary": {"occupancyReleased": False, "newWindowAllowed": False, "businessExecutionResumed": False, "automaticRedispatchAllowed": False},
        }
        case_root.mkdir(parents=True, exist_ok=False)
        (case_root / RECEIPTS_DIRECTORY).mkdir(exist_ok=False)
        persist_stage(data_root, project_id, incident["caseId"], "freeze", "C08_FREEZE_DISCONNECTED_CONTEXT", artifact)
    return public("FROZEN_AWAITING_RECOVERY", incident["caseId"], True, recoveryEpoch=epoch, occupancyPreserved=True, ledgerReceiptId=mutation["receiptId"]), 0


def validate_takeover(payload: Dict[str, Any], case_id: str) -> Dict[str, Any]:
    value = require_exact(payload, {"takeoverSchemaVersion", "recordType", "caseId", "expectedRecoveryEpoch", "successorInstanceRef", "bossAuthorization"}, "C08_TAKEOVER_SCHEMA_UNSUPPORTED")
    if value["takeoverSchemaVersion"] != SCHEMA_VERSION or value["recordType"] != "C08_CENTRAL_TAKEOVER_INPUT" or require_reference(value["caseId"], "C08_CASE_ID_INVALID") != case_id:
        raise RecoveryError("C08_TAKEOVER_SCHEMA_UNSUPPORTED")
    boss = require_exact(value["bossAuthorization"], {"status", "reference"}, "C08_BOSS_TAKEOVER_AUTHORIZATION_INVALID")
    if boss["status"] != "APPROVED":
        raise RecoveryError("C08_BOSS_TAKEOVER_AUTHORIZATION_REQUIRED")
    if not isinstance(value["expectedRecoveryEpoch"], int) or value["expectedRecoveryEpoch"] < 1:
        raise RecoveryError("C08_RECOVERY_EPOCH_INVALID")
    return {"caseId": case_id, "expectedRecoveryEpoch": value["expectedRecoveryEpoch"], "successorInstanceRef": require_reference(value["successorInstanceRef"], "C08_SUCCESSOR_REF_INVALID"), "bossAuthorizationRef": require_reference(boss["reference"], "C08_BOSS_AUTH_REF_INVALID")}


def takeover(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args); project_id = require_project_id(args.project_id); case_id = require_reference(args.case_id, "C08_CASE_ID_INVALID")
    freeze_artifact = verify_stage(data_root, project_id, case_id, "freeze", "C08_FREEZE_DISCONNECTED_CONTEXT")
    raw, input_digest = load_private(args.authorization, data_root, "C08_TAKEOVER_INVALID_JSON"); takeover_input = validate_takeover(raw, case_id)
    require_writer(args.writer_id)
    target = artifact_path(data_root, project_id, case_id, "takeover")
    if target.exists():
        existing = verify_stage(data_root, project_id, case_id, "takeover", "C08_RECOVER_CENTRAL_CONTROL")
        if existing.get("sourceInputDigest") == input_digest:
            return public("IDEMPOTENT_TAKEOVER", case_id, False, recoveryEpoch=existing["recoveryEpoch"]), 0
        raise RecoveryError("C08_TAKEOVER_ALREADY_EXISTS_WITH_DIFFERENT_CONTENT")
    with recovery_lock(data_root, project_id), ledger_lock(data_root, project_id):
        before = load_ledger(data_root, project_id)
        recovery = before["recovery"]
        if recovery.get("state") != "FROZEN_AWAITING_RECOVERY" or recovery.get("activeCaseId") != case_id or recovery.get("recoveryEpoch") != takeover_input["expectedRecoveryEpoch"]:
            raise RecoveryError("C08_TAKEOVER_STATE_OR_EPOCH_MISMATCH")
        if canonical_digest(before["objectOccupancies"]) != freeze_artifact["snapshot"]["objectOccupanciesDigest"]:
            raise RecoveryError("C08_OCCUPANCY_CHANGED_DURING_FREEZE")

        def mutate(after: Dict[str, Any]) -> None:
            after["recovery"].update({"state": "CENTRAL_CONTROL_RECOVERED", "successorInstanceRef": takeover_input["successorInstanceRef"], "controlRecoveredAt": utc_now(), "businessExecutionResumed": False})

        result = commit_mutation(data_root, project_id, before, CENTRAL_WRITER, "C08_RECOVER_CENTRAL_CONTROL", {"caseId": case_id, "recoveryEpoch": recovery["recoveryEpoch"], "businessExecutionResumed": False}, mutate, caller_thread_ref=args.caller_thread_ref)
        mutation = ledger_mutation(result, data_root, project_id)
        artifact = {"schemaVersion": SCHEMA_VERSION, "recordType": "C08_CENTRAL_TAKEOVER", "createdAt": utc_now(), "projectId": project_id, **takeover_input, "sourceInputDigest": input_digest, "freezeDigest": canonical_digest(freeze_artifact), "recoveryEpoch": recovery["recoveryEpoch"], "ledgerOperation": "C08_RECOVER_CENTRAL_CONTROL", "ledgerMutation": mutation, "boundary": {"governanceControlRecovered": True, "businessExecutionResumed": False, "occupancyReleased": False, "newDispatchAllowed": False}}
        persist_stage(data_root, project_id, case_id, "takeover", "C08_RECOVER_CENTRAL_CONTROL", artifact)
    return public("CENTRAL_CONTROL_RECOVERED", case_id, True, recoveryEpoch=artifact["recoveryEpoch"], occupancyPreserved=True, ledgerReceiptId=mutation["receiptId"]), 0


def validate_decision(payload: Dict[str, Any], case_id: str) -> Dict[str, Any]:
    value = require_exact(payload, {"decisionSchemaVersion", "recordType", "caseId", "action", "bossDecisionRef", "reasonRef"}, "C08_RECOVERY_DECISION_SCHEMA_UNSUPPORTED")
    if value["decisionSchemaVersion"] != SCHEMA_VERSION or value["recordType"] != "C08_RECOVERY_DECISION_INPUT" or require_reference(value["caseId"], "C08_CASE_ID_INVALID") != case_id:
        raise RecoveryError("C08_RECOVERY_DECISION_SCHEMA_UNSUPPORTED")
    action = str(value["action"]).strip().upper()
    if action not in {"KEEP_FROZEN", "PREPARE_RESUME", "CANCEL_TASK"}:
        raise RecoveryError("C08_RECOVERY_ACTION_INVALID")
    return {"action": action, "bossDecisionRef": require_reference(value["bossDecisionRef"], "C08_BOSS_DECISION_REF_INVALID"), "reasonRef": require_reference(value["reasonRef"], "C08_REASON_REF_INVALID")}


def decide(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args); project_id = require_project_id(args.project_id); case_id = require_reference(args.case_id, "C08_CASE_ID_INVALID")
    freeze_artifact = verify_stage(data_root, project_id, case_id, "freeze", "C08_FREEZE_DISCONNECTED_CONTEXT")
    takeover_artifact = verify_stage(data_root, project_id, case_id, "takeover", "C08_RECOVER_CENTRAL_CONTROL")
    raw, input_digest = load_private(args.decision, data_root, "C08_RECOVERY_DECISION_INVALID_JSON"); decision = validate_decision(raw, case_id)
    require_writer(args.writer_id)
    target = artifact_path(data_root, project_id, case_id, "decision")
    if target.exists():
        existing = verify_stage(data_root, project_id, case_id, "decision", "C08_RECORD_RECOVERY_DECISION")
        if existing.get("sourceInputDigest") == input_digest:
            return public("IDEMPOTENT_RECOVERY_DECISION", case_id, False, action=existing["action"]), 0
        raise RecoveryError("C08_RECOVERY_DECISION_ALREADY_EXISTS_WITH_DIFFERENT_CONTENT")
    if decision["action"] == "CANCEL_TASK" and freeze_artifact["taskId"] is None:
        raise RecoveryError("C08_CENTRAL_INCIDENT_CANNOT_CANCEL_TASK")
    with recovery_lock(data_root, project_id), ledger_lock(data_root, project_id):
        before = load_ledger(data_root, project_id)
        if before["recovery"].get("state") != "CENTRAL_CONTROL_RECOVERED" or before["recovery"].get("activeCaseId") != case_id:
            raise RecoveryError("C08_RECOVERY_DECISION_REQUIRES_RECOVERED_CONTROL")

        def mutate(after: Dict[str, Any]) -> None:
            state = {"KEEP_FROZEN": "FROZEN_BY_BOSS", "PREPARE_RESUME": "RESUME_REVIEW_REQUIRED", "CANCEL_TASK": "TASK_CANCELLED_OCCUPANCY_RETAINED"}[decision["action"]]
            after["recovery"].update({"state": state, "decisionAction": decision["action"], "decisionRecordedAt": utc_now(), "businessExecutionResumed": False})
            if decision["action"] == "CANCEL_TASK":
                task = after["tasks"][freeze_artifact["taskId"]]
                if task["status"] == "DONE":
                    raise RecoveryError("C08_DONE_TASK_CANNOT_BE_CANCELLED")
                source = task["status"]
                task["status"] = "CANCELLED"
                task["history"].append({"at": utc_now(), "event": "C08_BOSS_CANCELLED_RECOVERY", "caseId": case_id, "from": source, "to": "CANCELLED", "by": CENTRAL_WRITER})
                for window_id in freeze_artifact["affectedContext"]["windowIds"]:
                    after["windows"][window_id]["status"] = "CANCELLED"
                for agent_id in freeze_artifact["affectedContext"]["subAgentIds"]:
                    after["subAgents"][agent_id]["status"] = "CANCELLED"

        result = commit_mutation(data_root, project_id, before, CENTRAL_WRITER, "C08_RECORD_RECOVERY_DECISION", {"caseId": case_id, "action": decision["action"], "occupancyReleased": False}, mutate, caller_thread_ref=args.caller_thread_ref)
        mutation = ledger_mutation(result, data_root, project_id)
        artifact = {"schemaVersion": SCHEMA_VERSION, "recordType": "C08_RECOVERY_DECISION", "createdAt": utc_now(), "projectId": project_id, "caseId": case_id, **decision, "sourceInputDigest": input_digest, "freezeDigest": canonical_digest(freeze_artifact), "takeoverDigest": canonical_digest(takeover_artifact), "ledgerOperation": "C08_RECORD_RECOVERY_DECISION", "ledgerMutation": mutation, "boundary": {"businessExecutionResumed": False, "automaticRedispatchAllowed": False, "occupancyReleased": False, "requiresC09ForResume": decision["action"] == "PREPARE_RESUME"}}
        persist_stage(data_root, project_id, case_id, "decision", "C08_RECORD_RECOVERY_DECISION", artifact)
    return public("RECOVERY_DECISION_RECORDED", case_id, True, action=decision["action"], occupancyPreserved=True, ledgerReceiptId=mutation["receiptId"]), 0


def validate_replacement_resume(payload: Dict[str, Any], case_id: str) -> Dict[str, Any]:
    value = require_exact(
        payload,
        {
            "resumeSchemaVersion", "recordType", "caseId", "expectedRecoveryEpoch",
            "taskId", "disconnectedWindowId", "bossAuthorization", "reasonRef",
        },
        "C08_REPLACEMENT_RESUME_SCHEMA_UNSUPPORTED",
    )
    if (
        value["resumeSchemaVersion"] != SCHEMA_VERSION
        or value["recordType"] != "C08_TASK_WINDOW_REPLACEMENT_AUTHORIZATION"
        or require_reference(value["caseId"], "C08_CASE_ID_INVALID") != case_id
    ):
        raise RecoveryError("C08_REPLACEMENT_RESUME_SCHEMA_UNSUPPORTED")
    if not isinstance(value["expectedRecoveryEpoch"], int) or value["expectedRecoveryEpoch"] < 1:
        raise RecoveryError("C08_RECOVERY_EPOCH_INVALID")
    boss = require_exact(
        value["bossAuthorization"], {"status", "reference"},
        "C08_BOSS_REPLACEMENT_AUTHORIZATION_INVALID",
    )
    if boss["status"] != "APPROVED":
        raise RecoveryError("C08_BOSS_REPLACEMENT_AUTHORIZATION_REQUIRED")
    return {
        "expectedRecoveryEpoch": value["expectedRecoveryEpoch"],
        "taskId": require_task_id(value["taskId"], "C08_TASK_ID_INVALID"),
        "disconnectedWindowId": require_reference(value["disconnectedWindowId"], "C08_WINDOW_ID_INVALID"),
        "bossAuthorizationRef": require_reference(boss["reference"], "C08_BOSS_REPLACEMENT_REF_INVALID"),
        "reasonRef": require_reference(value["reasonRef"], "C08_REASON_REF_INVALID"),
    }


def resume(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    """Authorize normal C05/C10 redispatch after a task-window disconnection.

    This closes only the recovery freeze and returns the task to READY. It does
    not create a runtime task, resume business execution, or release occupancy.
    """
    data_root = load_data_root(args)
    project_id = require_project_id(args.project_id)
    case_id = require_reference(args.case_id, "C08_CASE_ID_INVALID")
    freeze_artifact = verify_stage(data_root, project_id, case_id, "freeze", "C08_FREEZE_DISCONNECTED_CONTEXT")
    verify_stage(data_root, project_id, case_id, "takeover", "C08_RECOVER_CENTRAL_CONTROL")
    decision_artifact = verify_stage(data_root, project_id, case_id, "decision", "C08_RECORD_RECOVERY_DECISION")
    raw, input_digest = load_private(args.authorization, data_root, "C08_REPLACEMENT_RESUME_INVALID_JSON")
    authorization = validate_replacement_resume(raw, case_id)
    require_writer(args.writer_id)
    target = artifact_path(data_root, project_id, case_id, "resume")
    if target.exists():
        existing = verify_stage(data_root, project_id, case_id, "resume", "C08_AUTHORIZE_TASK_WINDOW_REPLACEMENT")
        if existing.get("sourceInputDigest") == input_digest:
            return public(
                "IDEMPOTENT_TASK_WINDOW_REPLACEMENT_AUTHORIZATION", case_id, False,
                newDispatchAllowed=True, occupancyPreserved=True,
            ), 0
        raise RecoveryError("C08_REPLACEMENT_RESUME_ALREADY_EXISTS_WITH_DIFFERENT_CONTENT")
    if (
        freeze_artifact.get("incidentType") != "TASK_WINDOW"
        or decision_artifact.get("action") != "PREPARE_RESUME"
        or freeze_artifact.get("taskId") != authorization["taskId"]
        or freeze_artifact.get("targetId") != authorization["disconnectedWindowId"]
    ):
        raise RecoveryError("C08_REPLACEMENT_RESUME_CONTEXT_MISMATCH")
    with recovery_lock(data_root, project_id), ledger_lock(data_root, project_id):
        before = load_ledger(data_root, project_id)
        recovery = before.get("recovery", {})
        task = before.get("tasks", {}).get(authorization["taskId"])
        window = before.get("windows", {}).get(authorization["disconnectedWindowId"])
        if (
            recovery.get("state") != "RESUME_REVIEW_REQUIRED"
            or recovery.get("activeCaseId") != case_id
            or recovery.get("recoveryEpoch") != authorization["expectedRecoveryEpoch"]
            or recovery.get("decisionAction") != "PREPARE_RESUME"
            or not isinstance(task, dict) or task.get("status") != "BLOCKED"
            or not isinstance(window, dict) or window.get("status") != "DISCONNECTED"
            or window.get("currentTaskId", window.get("taskId")) != authorization["taskId"]
        ):
            raise RecoveryError("C08_REPLACEMENT_RESUME_STATE_OR_EPOCH_MISMATCH")
        if canonical_digest(before.get("objectOccupancies", {})) != freeze_artifact["snapshot"]["objectOccupanciesDigest"]:
            raise RecoveryError("C08_OCCUPANCY_CHANGED_DURING_RECOVERY")

        def mutate(after: Dict[str, Any]) -> None:
            now = utc_now()
            target_task = after["tasks"][authorization["taskId"]]
            target_task["status"] = "READY"
            target_task["history"].append({
                "at": now, "event": "C08_TASK_WINDOW_REPLACEMENT_AUTHORIZED",
                "caseId": case_id, "from": "BLOCKED", "to": "READY",
                "disconnectedWindowId": authorization["disconnectedWindowId"],
                "bossAuthorizationRef": authorization["bossAuthorizationRef"],
                "by": CENTRAL_WRITER,
            })
            old_window = after["windows"][authorization["disconnectedWindowId"]]
            old_window["status"] = "REPLACED_READ_ONLY"
            old_window["currentTaskId"] = None
            old_window["replacementAuthorizedAt"] = now
            for assignment in old_window.get("assignmentHistory", []):
                if assignment.get("taskId") == authorization["taskId"] and assignment.get("status") == "ACTIVE":
                    assignment["status"] = "DISCONNECTED_REPLACED"
                    assignment["replacementAuthorizedAt"] = now
            for agent_id in freeze_artifact.get("affectedContext", {}).get("subAgentIds", []):
                if agent_id in after.get("subAgents", {}):
                    after["subAgents"][agent_id]["status"] = "REPLACED_READ_ONLY"
            after["recovery"].update({
                "state": "CLOSED", "closedAt": now,
                "closureReason": "BOSS_AUTHORIZED_TASK_WINDOW_REPLACEMENT",
                "businessExecutionResumed": False,
                "newDispatchAllowed": True,
                "occupancyReleaseStatus": "PRESERVED_FOR_REPLACEMENT",
            })

        result = commit_mutation(
            data_root, project_id, before, CENTRAL_WRITER,
            "C08_AUTHORIZE_TASK_WINDOW_REPLACEMENT",
            {
                "caseId": case_id, "taskId": authorization["taskId"],
                "disconnectedWindowId": authorization["disconnectedWindowId"],
                "toStatus": "READY", "businessExecutionResumed": False,
                "newDispatchAllowed": True, "occupancyReleased": False,
            },
            mutate,
            caller_thread_ref=args.caller_thread_ref,
        )
        mutation = ledger_mutation(result, data_root, project_id)
        artifact = {
            "schemaVersion": SCHEMA_VERSION,
            "recordType": "C08_TASK_WINDOW_REPLACEMENT_AUTHORIZATION",
            "createdAt": utc_now(), "projectId": project_id, "caseId": case_id,
            **authorization, "sourceInputDigest": input_digest,
            "freezeDigest": canonical_digest(freeze_artifact),
            "decisionDigest": canonical_digest(decision_artifact),
            "ledgerOperation": "C08_AUTHORIZE_TASK_WINDOW_REPLACEMENT",
            "ledgerMutation": mutation,
            "boundary": {
                "businessExecutionResumed": False, "newDispatchAllowed": True,
                "requiresC05AndC10": True, "occupancyReleased": False,
                "oldWindowReadOnly": True,
            },
        }
        persist_stage(data_root, project_id, case_id, "resume", "C08_AUTHORIZE_TASK_WINDOW_REPLACEMENT", artifact)
    return public(
        "TASK_WINDOW_REPLACEMENT_AUTHORIZED", case_id, True,
        newDispatchAllowed=True, occupancyPreserved=True,
        ledgerReceiptId=mutation["receiptId"], taskStatus="READY",
    ), 0


def validate_release(payload: Dict[str, Any], case_id: str) -> Dict[str, Any]:
    value = require_exact(payload, {"releaseSchemaVersion", "recordType", "caseId", "bossReleaseAuthorization", "validationId", "rollbackConfirmed", "noBusinessWritesOutstanding"}, "C08_RELEASE_SCHEMA_UNSUPPORTED")
    if value["releaseSchemaVersion"] != SCHEMA_VERSION or value["recordType"] != "C08_OCCUPANCY_RELEASE_INPUT" or require_reference(value["caseId"], "C08_CASE_ID_INVALID") != case_id:
        raise RecoveryError("C08_RELEASE_SCHEMA_UNSUPPORTED")
    boss = require_exact(value["bossReleaseAuthorization"], {"status", "reference"}, "C08_BOSS_RELEASE_AUTHORIZATION_INVALID")
    if boss["status"] != "APPROVED":
        raise RecoveryError("C08_BOSS_RELEASE_AUTHORIZATION_REQUIRED")
    if not isinstance(value["rollbackConfirmed"], bool) or not isinstance(value["noBusinessWritesOutstanding"], bool):
        raise RecoveryError("C08_RELEASE_ASSERTIONS_INVALID")
    return {"bossReleaseAuthorizationRef": require_reference(boss["reference"], "C08_BOSS_RELEASE_REF_INVALID"), "validationId": require_optional_reference(value["validationId"], "C08_VALIDATION_ID_INVALID"), "rollbackConfirmed": value["rollbackConfirmed"], "noBusinessWritesOutstanding": value["noBusinessWritesOutstanding"]}


def release(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args); project_id = require_project_id(args.project_id); case_id = require_reference(args.case_id, "C08_CASE_ID_INVALID")
    freeze_artifact = verify_stage(data_root, project_id, case_id, "freeze", "C08_FREEZE_DISCONNECTED_CONTEXT")
    verify_stage(data_root, project_id, case_id, "takeover", "C08_RECOVER_CENTRAL_CONTROL")
    raw, input_digest = load_private(args.release, data_root, "C08_RELEASE_INVALID_JSON"); release_input = validate_release(raw, case_id)
    require_writer(args.writer_id)
    target = artifact_path(data_root, project_id, case_id, "release")
    if target.exists():
        existing = verify_stage(data_root, project_id, case_id, "release", "C08_RELEASE_COMPLETED_OR_CANCELLED_OCCUPANCY")
        if existing.get("sourceInputDigest") == input_digest:
            return public("IDEMPOTENT_OCCUPANCY_RELEASE", case_id, False, releasedClaimCount=existing["releasedClaimCount"]), 0
        raise RecoveryError("C08_RELEASE_ALREADY_EXISTS_WITH_DIFFERENT_CONTENT")
    task_id = freeze_artifact["taskId"]
    if task_id is None:
        raise RecoveryError("C08_PROJECT_WIDE_INCIDENT_HAS_NO_TASK_OCCUPANCY_RELEASE")
    with recovery_lock(data_root, project_id), ledger_lock(data_root, project_id):
        before = load_ledger(data_root, project_id); task = before["tasks"].get(task_id)
        if not isinstance(task, dict) or task.get("status") not in {"DONE", "CANCELLED"}:
            raise RecoveryError("C08_RELEASE_REQUIRES_DONE_OR_CANCELLED_TASK")
        if any(stop.get("taskId") == task_id for stop in before["hardStops"]):
            raise RecoveryError("C08_TASK_HAS_UNRESOLVED_HARD_STOP")
        if task["status"] == "DONE":
            if release_input["validationId"] is None:
                raise RecoveryError("C08_DONE_RELEASE_REQUIRES_C06_VALIDATION")
            try:
                finalization = verify_finalization_data(data_root, project_id, release_input["validationId"])
            except HandoverValidationError as error:
                raise RecoveryError(f"C08_C06_FINALIZATION_{error}")
            if finalization.get("taskId") != task_id or finalization.get("bossDecision") != "APPROVED":
                raise RecoveryError("C08_C06_FINALIZATION_TASK_OR_DECISION_MISMATCH")
            release_status = "OCCUPANCY_RELEASED_AFTER_DONE"
        else:
            if release_input["validationId"] is not None or not release_input["rollbackConfirmed"] or not release_input["noBusinessWritesOutstanding"]:
                raise RecoveryError("C08_CANCELLED_RELEASE_REQUIRES_ROLLBACK_AND_ZERO_OUTSTANDING_WRITES")
            release_status = "OCCUPANCY_RELEASED_AFTER_CANCEL"
        matching = [(key, claim) for key, occupancy in before["objectOccupancies"].items() for claim in occupancy.get("claims", []) if claim.get("ownerType") == "task" and claim.get("ownerId") == task_id]
        if not matching:
            raise RecoveryError("C08_TASK_HAS_NO_RELEASABLE_OCCUPANCY")

        def mutate(after: Dict[str, Any]) -> None:
            for key in list(after["objectOccupancies"]):
                occupancy = after["objectOccupancies"][key]
                occupancy["claims"] = [claim for claim in occupancy.get("claims", []) if not (claim.get("ownerType") == "task" and claim.get("ownerId") == task_id)]
                if not occupancy["claims"]:
                    del after["objectOccupancies"][key]
                else:
                    occupancy["status"] = "CONFLICT" if len(occupancy["claims"]) > 1 else "CLAIMED"
            after["recovery"].update({"state": "CLOSED", "closedAt": utc_now(), "occupancyReleaseStatus": release_status, "businessExecutionResumed": False})
            for window_id in freeze_artifact["affectedContext"]["windowIds"]:
                after["windows"][window_id]["status"] = "CLOSED"
            for agent_id in freeze_artifact["affectedContext"]["subAgentIds"]:
                after["subAgents"][agent_id]["status"] = "CLOSED"

        result = commit_mutation(data_root, project_id, before, CENTRAL_WRITER, "C08_RELEASE_COMPLETED_OR_CANCELLED_OCCUPANCY", {"caseId": case_id, "taskId": task_id, "releasedClaimCount": len(matching), "releaseStatus": release_status}, mutate, caller_thread_ref=args.caller_thread_ref)
        mutation = ledger_mutation(result, data_root, project_id)
        artifact = {"schemaVersion": SCHEMA_VERSION, "recordType": "C08_OCCUPANCY_RELEASE", "createdAt": utc_now(), "projectId": project_id, "caseId": case_id, "taskId": task_id, **release_input, "sourceInputDigest": input_digest, "releaseStatus": release_status, "releasedClaimCount": len(matching), "releasedObjectKeys": sorted({key for key, _ in matching}), "ledgerOperation": "C08_RELEASE_COMPLETED_OR_CANCELLED_OCCUPANCY", "ledgerMutation": mutation, "boundary": {"businessExecutionResumed": False, "otherOwnersPreserved": True, "automaticRedispatchAllowed": False}}
        persist_stage(data_root, project_id, case_id, "release", "C08_RELEASE_COMPLETED_OR_CANCELLED_OCCUPANCY", artifact)
    return public(release_status, case_id, True, releasedClaimCount=len(matching), ledgerReceiptId=mutation["receiptId"]), 0


def verify_command(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args); project_id = require_project_id(args.project_id); case_id = require_reference(args.case_id, "C08_CASE_ID_INVALID")
    freeze_artifact = verify_stage(data_root, project_id, case_id, "freeze", "C08_FREEZE_DISCONNECTED_CONTEXT")
    stage = "FROZEN"; latest = freeze_artifact
    if artifact_path(data_root, project_id, case_id, "takeover").exists():
        latest = verify_stage(data_root, project_id, case_id, "takeover", "C08_RECOVER_CENTRAL_CONTROL"); stage = "CONTROL_RECOVERED"
    if artifact_path(data_root, project_id, case_id, "decision").exists():
        latest = verify_stage(data_root, project_id, case_id, "decision", "C08_RECORD_RECOVERY_DECISION"); stage = "DECIDED"
    if artifact_path(data_root, project_id, case_id, "resume").exists():
        latest = verify_stage(data_root, project_id, case_id, "resume", "C08_AUTHORIZE_TASK_WINDOW_REPLACEMENT"); stage = "REPLACEMENT_AUTHORIZED"
    if artifact_path(data_root, project_id, case_id, "release").exists():
        latest = verify_stage(data_root, project_id, case_id, "release", "C08_RELEASE_COMPLETED_OR_CANCELLED_OCCUPANCY"); stage = "CLOSED"
    ledger = verified_ledger(data_root, project_id)
    if ledger["recovery"].get("activeCaseId") != case_id:
        raise RecoveryError("C08_LEDGER_CASE_MISMATCH")
    return {"status": "C08_RECOVERY_INTEGRITY_VERIFIED", "projectId": project_id, "caseId": case_id, "stage": stage, "recoveryState": ledger["recovery"]["state"], "recoveryEpoch": ledger["recovery"]["recoveryEpoch"], "taskId": freeze_artifact["taskId"], "taskStatus": ledger["tasks"].get(freeze_artifact["taskId"], {}).get("status") if freeze_artifact["taskId"] else None, "occupancyCount": len(ledger["objectOccupancies"]), "businessExecutionResumed": False, "writePerformed": False, "latestArtifactDigest": canonical_digest(latest)}, 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="C08 disconnection recovery controller")
    roots = parser.add_mutually_exclusive_group(required=True); roots.add_argument("--data-root"); roots.add_argument("--config")
    parser.add_argument("--project-id", required=True); parser.add_argument("--writer-id"); parser.add_argument("--caller-thread-ref")
    commands = parser.add_subparsers(dest="command", required=True)
    freeze_parser = commands.add_parser("freeze"); freeze_parser.add_argument("--incident", required=True)
    action = freeze_parser.add_mutually_exclusive_group(required=True); action.add_argument("--dry-run", action="store_true"); action.add_argument("--apply", action="store_true")
    takeover_parser = commands.add_parser("takeover"); takeover_parser.add_argument("--case-id", required=True); takeover_parser.add_argument("--authorization", required=True)
    decision_parser = commands.add_parser("decide"); decision_parser.add_argument("--case-id", required=True); decision_parser.add_argument("--decision", required=True)
    resume_parser = commands.add_parser("resume"); resume_parser.add_argument("--case-id", required=True); resume_parser.add_argument("--authorization", required=True)
    release_parser = commands.add_parser("release"); release_parser.add_argument("--case-id", required=True); release_parser.add_argument("--release", required=True)
    verify_parser = commands.add_parser("verify"); verify_parser.add_argument("--case-id", required=True)
    return parser.parse_args()


def dispatch(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    return {"freeze": freeze, "takeover": takeover, "decide": decide, "resume": resume, "release": release, "verify": verify_command}[args.command](args)


def main() -> int:
    args = parse_args()
    try:
        result, code = dispatch(args); print_result(result); return code
    except (RecoveryError, LedgerError, C02Error, HandoverValidationError) as error:
        print_result({"status": "RECOVERY_BLOCKED", "reason": str(error), "businessExecutionResumed": False, "newDispatchAllowed": False, "occupancyReleased": False, "testCreated": False, "businessWriteAllowed": False, "writePerformed": False, "message": "C08 已停止；未释放占用、重复派工或修改业务系统。"})
        return 2


if __name__ == "__main__":
    sys.exit(main())
