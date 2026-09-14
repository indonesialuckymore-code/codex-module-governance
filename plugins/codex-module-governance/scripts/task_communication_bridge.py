#!/usr/bin/env python3
"""C14 receipt-backed delivery bridge for Codex task messages.

It stores governance message metadata and immutable receipts only. A Codex agent
must use native ``send_message_to_thread`` between prepare and record steps.
Chat text alone never changes C03, validates a task, or proves receipt.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

from initialize_project import C02Error, is_within, load_data_root
from ledger_manager import CENTRAL_WRITER, LedgerError, canonical_digest, window_current_task_id
from role_continuity_controller import (
    ContinuityError, canonical_task_title, current_role, event_path, project_ref,
    read_json, ref, return_submission_path, safe_text, utc_now, verified_ledger,
    verify_routing, write_exclusive,
)

SCHEMA_VERSION = "0.19.0"
ROOT = Path("task-communication")
MESSAGE_ID_MAXIMUM = 80
HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")
COMMAND_TYPES = {"TASK_INSTRUCTION", "VALIDATION_REQUEST", "RETRY_REQUEST", "CANCELLATION_NOTICE", "DIRECTION_CORRECTION"}
RESULT_OUTCOMES = {"APPLIED", "REFUSED", "BLOCKED"}
TRANSPORT = "CODEX_SEND_MESSAGE_TO_THREAD"


class BridgeError(Exception):
    """Safe refusal without business-system execution."""


def print_result(value: Dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def exact(value: Any, keys: set[str], error: str) -> Dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise BridgeError(error)
    return value


def message_ref(value: Any, error: str) -> str:
    candidate = ref(value, error)
    if len(candidate) > MESSAGE_ID_MAXIMUM:
        raise BridgeError(error)
    return candidate


def digest(value: Any, error: str) -> str:
    if not isinstance(value, str) or not HEX_DIGEST.fullmatch(value):
        raise BridgeError(error)
    return value


def private_json(raw_path: str, data_root: Path, error: str) -> Tuple[Dict[str, Any], str]:
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file() or not is_within(path, data_root):
        raise BridgeError("C14_PRIVATE_INPUT_REQUIRED")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise BridgeError(error)
    if not isinstance(value, dict):
        raise BridgeError(error)
    return value, canonical_digest(value)


def bridge_root(data_root: Path, project_id: str) -> Path:
    return data_root / ROOT / project_id


def message_dir(data_root: Path, project_id: str, message_id: str) -> Path:
    return bridge_root(data_root, project_id) / "messages" / message_id


def envelope_path(data_root: Path, project_id: str, message_id: str) -> Path:
    return message_dir(data_root, project_id, message_id) / "envelope.json"


def envelope_receipt_path(data_root: Path, project_id: str, message_id: str) -> Path:
    return message_dir(data_root, project_id, message_id) / "receipt-000000-enqueued.json"


def delivery_dir(data_root: Path, project_id: str, message_id: str) -> Path:
    return message_dir(data_root, project_id, message_id) / "deliveries"


def acknowledgement_path(data_root: Path, project_id: str, message_id: str) -> Path:
    return message_dir(data_root, project_id, message_id) / "acknowledgement.json"


def command_result_path(data_root: Path, project_id: str, message_id: str) -> Path:
    return message_dir(data_root, project_id, message_id) / "command-result.json"


def load_envelope(data_root: Path, project_id: str, message_id: str) -> Dict[str, Any]:
    envelope = read_json(envelope_path(data_root, project_id, message_id), "C14_MESSAGE_NOT_FOUND")
    receipt = read_json(envelope_receipt_path(data_root, project_id, message_id), "C14_ENVELOPE_RECEIPT_MISSING")
    if (
        envelope.get("schemaVersion") != SCHEMA_VERSION
        or envelope.get("recordType") != "C14_MESSAGE_ENVELOPE"
        or envelope.get("messageId") != message_id
        or envelope.get("projectId") != project_id
        or receipt.get("artifact") != envelope
        or receipt.get("artifactDigest") != canonical_digest(envelope)
    ):
        raise BridgeError("C14_ENVELOPE_INTEGRITY_INVALID")
    return envelope


def task_and_window(ledger: Dict[str, Any], task_id: str, window_id: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    task = ledger.get("tasks", {}).get(task_id)
    window = ledger.get("windows", {}).get(window_id)
    if not isinstance(task, dict) or not isinstance(window, dict) or window_current_task_id(window) != task_id:
        raise BridgeError("C14_TASK_WINDOW_BINDING_INVALID")
    return task, window


def validate_task_identity(raw: Any, ledger: Dict[str, Any]) -> Dict[str, str]:
    value = exact(raw, {"taskId", "canonicalTitle"}, "C14_TASK_IDENTITY_INVALID")
    task_id = ref(value["taskId"], "C14_TASK_ID_INVALID")
    title = safe_text(value["canonicalTitle"], "C14_CANONICAL_TITLE_INVALID", 220)
    task = ledger.get("tasks", {}).get(task_id)
    if not isinstance(task, dict) or canonical_task_title(task) != title:
        raise BridgeError("C14_TASK_IDENTITY_MISMATCH")
    return {"taskId": task_id, "canonicalTitle": title}


def source_window(raw: Any, task_id: str, ledger: Dict[str, Any]) -> Dict[str, Any]:
    value = exact(raw, {"windowId", "runtimeThreadRef", "generation"}, "C14_SOURCE_WINDOW_INVALID")
    window_id = ref(value["windowId"], "C14_WINDOW_ID_INVALID")
    thread = ref(value["runtimeThreadRef"], "C14_RUNTIME_THREAD_REF_INVALID")
    generation = value["generation"]
    if not isinstance(generation, int) or generation < 1:
        raise BridgeError("C14_SOURCE_WINDOW_INVALID")
    _, window = task_and_window(ledger, task_id, window_id)
    if window.get("runtimeThreadRef", window_id) != thread or window.get("generation", 1) != generation:
        raise BridgeError("C14_SOURCE_WINDOW_MISMATCH")
    return {"windowId": window_id, "runtimeThreadRef": thread, "generation": generation}


def write_envelope(data_root: Path, project_id: str, envelope: Dict[str, Any]) -> None:
    message_id = envelope["messageId"]
    receipt = {
        "schemaVersion": SCHEMA_VERSION, "recordType": "C14_IMMUTABLE_MESSAGE_ENQUEUE_RECEIPT",
        "createdAt": utc_now(), "projectId": project_id, "messageId": message_id,
        "artifactDigest": canonical_digest(envelope), "artifact": envelope,
    }
    write_exclusive(envelope_receipt_path(data_root, project_id, message_id), receipt)
    write_exclusive(envelope_path(data_root, project_id, message_id), envelope)


def return_event_is_valid(data_root: Path, project_id: str, event_id: str, task_identity: Dict[str, str], ticket_id: str, handback_digest: str) -> None:
    event = read_json(event_path(data_root, project_id, event_id), "C14_RETURN_EVENT_NOT_FOUND")
    ticket = read_json(return_submission_path(data_root, project_id, ticket_id), "C14_RETURN_TICKET_NOT_FOUND")
    if (
        event.get("recordType") != "C08_DURABLE_TASK_EVENT" or event.get("eventType") != "TASK_HANDBACK_QUEUED"
        or event.get("projectId") != project_id or event.get("eventId") != event_id
        or event.get("taskIdentity") != task_identity or event.get("decisionRef") != ticket_id
        or ticket.get("projectId") != project_id or ticket.get("returnTicketId") != ticket_id
        or ticket.get("taskId") != task_identity["taskId"] or ticket.get("sourceHandbackDigest") != handback_digest
    ):
        raise BridgeError("C14_RETURN_TICKET_EVENT_MISMATCH")


def queue_task_to_central(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args)
    raw, request_digest = private_json(args.request, data_root, "C14_TASK_MESSAGE_REQUEST_INVALID_JSON")
    required = {"requestSchemaVersion", "recordType", "messageId", "projectId", "taskIdentity", "sourceWindow", "eventId", "returnTicketId", "handbackDigest", "routeRevisionSeen", "summary"}
    value = exact(raw, required, "C14_TASK_MESSAGE_REQUEST_SCHEMA_INVALID")
    project_id = project_ref(value["projectId"])
    if value["requestSchemaVersion"] != SCHEMA_VERSION or value["recordType"] != "C14_TASK_TO_CENTRAL_REQUEST":
        raise BridgeError("C14_TASK_MESSAGE_REQUEST_SCHEMA_INVALID")
    message_id = message_ref(value["messageId"], "C14_MESSAGE_ID_INVALID")
    event_id = ref(value["eventId"], "C14_EVENT_ID_INVALID")
    ticket_id = ref(value["returnTicketId"], "C14_RETURN_TICKET_ID_INVALID")
    handback_digest = digest(value["handbackDigest"], "C14_HANDBACK_DIGEST_INVALID")
    if not isinstance(value["routeRevisionSeen"], int) or value["routeRevisionSeen"] < 1:
        raise BridgeError("C14_ROUTE_REVISION_INVALID")
    ledger = verified_ledger(data_root, project_id)
    routing = verify_routing(data_root, project_id)
    task_identity = validate_task_identity(value["taskIdentity"], ledger)
    window = source_window(value["sourceWindow"], task_identity["taskId"], ledger)
    if value["routeRevisionSeen"] > routing["revision"]:
        raise BridgeError("C14_ROUTE_REVISION_FUTURE")
    return_event_is_valid(data_root, project_id, event_id, task_identity, ticket_id, handback_digest)
    if envelope_path(data_root, project_id, message_id).exists():
        existing = load_envelope(data_root, project_id, message_id)
        if existing.get("sourceRequestDigest") == request_digest:
            return {"status": "IDEMPOTENT_MESSAGE_ENQUEUED", "messageId": message_id, "writePerformed": False, "ledgerUpdated": False}, 0
        raise BridgeError("C14_MESSAGE_ID_REUSED")
    active = routing["roles"]["CURRENT_CENTRAL"]
    payload = {"type": "TASK_HANDBACK_NOTIFICATION", "eventId": event_id, "returnTicketId": ticket_id, "handbackDigest": handback_digest, "summary": safe_text(value["summary"], "C14_MESSAGE_SUMMARY_INVALID")}
    envelope = {
        "schemaVersion": SCHEMA_VERSION, "recordType": "C14_MESSAGE_ENVELOPE", "createdAt": utc_now(),
        "messageId": message_id, "projectId": project_id, "direction": "TASK_TO_CENTRAL",
        "source": {"role": "TASK_WINDOW", **window},
        "targetAtEnqueue": {"role": "CURRENT_CENTRAL", "threadRef": active["activeThreadRef"], "generation": active["generation"], "routingRevision": routing["revision"]},
        "taskIdentity": task_identity, "payload": {**payload, "payloadDigest": canonical_digest(payload)},
        "sourceRequestDigest": request_digest,
        "boundary": {"messageBodyContainsOnlyPointers": True, "centralGetsNoEvidenceBody": True, "c03TaskStateChanged": False, "businessWritePerformed": False},
    }
    write_envelope(data_root, project_id, envelope)
    return {"status": "MESSAGE_ENQUEUED_AWAITING_RUNTIME_DELIVERY", "messageId": message_id, "eventId": event_id, "returnTicketId": ticket_id, "targetRole": "CURRENT_CENTRAL", "writePerformed": True, "ledgerUpdated": False, "message": "票据已入 C14 发件箱；尚未发送原生消息，不能说中央已收到。"}, 0


def queue_central_command(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args); project_id = project_ref(args.project_id)
    current_thread = ref(args.current_thread_ref, "C14_CENTRAL_THREAD_REF_INVALID")
    if args.writer_id != CENTRAL_WRITER:
        raise BridgeError("C14_WRITER_NOT_AUTHORIZED")
    raw, request_digest = private_json(args.request, data_root, "C14_CENTRAL_COMMAND_INVALID_JSON")
    required = {"requestSchemaVersion", "recordType", "messageId", "projectId", "taskIdentity", "windowId", "commandId", "commandType", "commandRef", "commandDigest", "summary"}
    value = exact(raw, required, "C14_CENTRAL_COMMAND_SCHEMA_INVALID")
    if value["requestSchemaVersion"] != SCHEMA_VERSION or value["recordType"] != "C14_CENTRAL_TO_TASK_REQUEST" or value["projectId"] != project_id:
        raise BridgeError("C14_CENTRAL_COMMAND_SCHEMA_INVALID")
    message_id = message_ref(value["messageId"], "C14_MESSAGE_ID_INVALID")
    command_id = ref(value["commandId"], "C14_COMMAND_ID_INVALID")
    command_ref = ref(value["commandRef"], "C14_COMMAND_REF_INVALID")
    command_digest = digest(value["commandDigest"], "C14_COMMAND_DIGEST_INVALID")
    command_type = str(value["commandType"]).strip().upper()
    if command_type not in COMMAND_TYPES:
        raise BridgeError("C14_COMMAND_TYPE_INVALID")
    routing = verify_routing(data_root, project_id)
    current_role(routing, "CURRENT_CENTRAL", current_thread)
    ledger = verified_ledger(data_root, project_id)
    task_identity = validate_task_identity(value["taskIdentity"], ledger)
    window_id = ref(value["windowId"], "C14_WINDOW_ID_INVALID")
    task, window = task_and_window(ledger, task_identity["taskId"], window_id)
    from ledger_manager import direction_context
    direction = {"directionContext": direction_context(task)} if task.get("directionCorrections") else {}
    value = {**value, **direction}
    validate_direction_command({**value, "commandType": command_type}, task, window, window_id)
    thread = ref(window.get("runtimeThreadRef", window_id), "C14_RUNTIME_THREAD_REF_INVALID")
    if envelope_path(data_root, project_id, message_id).exists():
        existing = load_envelope(data_root, project_id, message_id)
        if existing.get("sourceRequestDigest") == request_digest:
            return {"status": "IDEMPOTENT_MESSAGE_ENQUEUED", "messageId": message_id, "writePerformed": False, "ledgerUpdated": False}, 0
        raise BridgeError("C14_MESSAGE_ID_REUSED")
    payload = {"type": "CENTRAL_TASK_COMMAND", "commandId": command_id, "commandType": command_type, "commandRef": command_ref, "commandDigest": command_digest, "summary": safe_text(value["summary"], "C14_MESSAGE_SUMMARY_INVALID"), **direction}
    envelope = {
        "schemaVersion": SCHEMA_VERSION, "recordType": "C14_MESSAGE_ENVELOPE", "createdAt": utc_now(),
        "messageId": message_id, "projectId": project_id, "direction": "CENTRAL_TO_TASK",
        "source": {"role": "CURRENT_CENTRAL", "threadRef": current_thread, "generation": routing["roles"]["CURRENT_CENTRAL"]["generation"], "routingRevision": routing["revision"]},
        "targetAtEnqueue": {"role": "TASK_WINDOW", "windowId": window_id, "threadRef": thread, "generation": window.get("generation", 1)},
        "taskIdentity": task_identity, "payload": {**payload, "payloadDigest": canonical_digest(payload)},
        "sourceRequestDigest": request_digest,
        "boundary": {"messageBodyContainsOnlyPointers": True, "doesNotChangeTaskStatus": True, "businessWritePerformed": False},
    }
    write_envelope(data_root, project_id, envelope)
    return {"status": "MESSAGE_ENQUEUED_AWAITING_RUNTIME_DELIVERY", "messageId": message_id, "commandId": command_id, "targetWindowId": window_id, "writePerformed": True, "ledgerUpdated": False, "message": "中央指令已入 C14 发件箱；尚未送达任务窗口，不能视为窗口已执行。"}, 0


def source_is_current(envelope: Dict[str, Any], routing: Dict[str, Any], ledger: Dict[str, Any], caller_thread: str) -> None:
    source = envelope["source"]
    if source.get("role") == "CURRENT_CENTRAL":
        current_role(routing, "CURRENT_CENTRAL", caller_thread); return
    if source.get("role") == "TASK_WINDOW":
        identity = envelope["taskIdentity"]
        checked = source_window({key: source.get(key) for key in ("windowId", "runtimeThreadRef", "generation")}, identity["taskId"], ledger)
        if checked["runtimeThreadRef"] != caller_thread:
            raise BridgeError("C14_SOURCE_THREAD_MISMATCH")
        return
    raise BridgeError("C14_SOURCE_ROLE_INVALID")


def validate_direction_command(payload: Dict[str, Any], task: Dict[str, Any], window: Dict[str, Any], window_id: str) -> None:
    from ledger_manager import active_external_waits
    if active_external_waits(task) and payload.get("commandType") not in {"DIRECTION_CORRECTION", "CANCELLATION_NOTICE"}:
        raise BridgeError("C14_TASK_EXTERNAL_WAIT_PENDING")
    history = task.get("directionCorrections", [])
    if payload.get("commandType") == "DIRECTION_CORRECTION":
        current = history[-1] if history else {}
        binding = {"windowId": window_id, "runtimeThreadRef": window.get("runtimeThreadRef", window_id),
                   "generation": window.get("generation", 1)}
        if (payload.get("commandId") != current.get("correctionId")
            or payload.get("commandDigest") != current.get("requestDigest")
            or payload.get("commandRef") != current.get("instructionRef")
            or binding not in current.get("windowBindings", [])):
            raise BridgeError("C14_DIRECTION_CORRECTION_SUPERSEDED_OR_MISMATCHED")
    elif history and payload.get("commandType") != "CANCELLATION_NOTICE":
        from ledger_manager import direction_hold, direction_context
        if direction_hold(task): raise BridgeError("C14_TASK_DIRECTION_CORRECTION_PENDING")
        if payload.get("directionContext") != direction_context(task):
            raise BridgeError("C14_TASK_DIRECTION_VERSION_MISMATCH")


def live_target(envelope: Dict[str, Any], routing: Dict[str, Any], ledger: Dict[str, Any]) -> Dict[str, Any]:
    if envelope["direction"] == "TASK_TO_CENTRAL":
        active = routing["roles"]["CURRENT_CENTRAL"]
        return {"role": "CURRENT_CENTRAL", "threadRef": active["activeThreadRef"], "generation": active["generation"], "routingRevision": routing["revision"]}
    target = envelope["targetAtEnqueue"]
    window_id = ref(target.get("windowId"), "C14_WINDOW_ID_INVALID")
    task, window = task_and_window(ledger, envelope["taskIdentity"]["taskId"], window_id)
    validate_direction_command(envelope["payload"], task, window, window_id)
    return {"role": "TASK_WINDOW", "windowId": window_id, "threadRef": ref(window.get("runtimeThreadRef", window_id), "C14_RUNTIME_THREAD_REF_INVALID"), "generation": window.get("generation", 1)}


def delivery_records(data_root: Path, project_id: str, message_id: str) -> List[Dict[str, Any]]:
    root = delivery_dir(data_root, project_id, message_id)
    if not root.exists(): return []
    records = []
    for path in sorted(root.glob("*.json")):
        value = read_json(path, "C14_DELIVERY_RECEIPT_INVALID")
        if value.get("recordType") != "C14_RUNTIME_DELIVERY_RECEIPT" or value.get("messageId") != message_id or value.get("projectId") != project_id:
            raise BridgeError("C14_DELIVERY_RECEIPT_INVALID")
        records.append(value)
    return records


def has_successful_delivery(data_root: Path, project_id: str, message_id: str) -> bool:
    return any(item.get("outcome") == "SUCCEEDED" for item in delivery_records(data_root, project_id, message_id))


def message_state(data_root: Path, project_id: str, message_id: str) -> str:
    result = command_result_path(data_root, project_id, message_id)
    if result.exists():
        outcome = read_json(result, "C14_COMMAND_RESULT_INVALID").get("outcome")
        if outcome in RESULT_OUTCOMES: return outcome
    if acknowledgement_path(data_root, project_id, message_id).exists(): return "ACKNOWLEDGED"
    if has_successful_delivery(data_root, project_id, message_id): return "DELIVERED"
    return "QUEUED"


def prepare_delivery(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args); project_id = project_ref(args.project_id)
    message_id = message_ref(args.message_id, "C14_MESSAGE_ID_INVALID")
    caller = ref(args.current_thread_ref, "C14_CALLER_THREAD_REF_INVALID")
    envelope = load_envelope(data_root, project_id, message_id)
    routing = verify_routing(data_root, project_id); ledger = verified_ledger(data_root, project_id)
    source_is_current(envelope, routing, ledger, caller)
    current_state = message_state(data_root, project_id, message_id)
    if current_state != "QUEUED":
        return {"status": "MESSAGE_NOT_DELIVERABLE", "messageId": message_id, "messageState": current_state, "writePerformed": False, "message": "消息已有后续回执，禁止重复发送。"}, 0
    target = live_target(envelope, routing, ledger)
    prompt = f"[C14] governance-message={message_id}; project={project_id}; payload-digest={envelope['payload']['payloadDigest']}. 请先读取私有 C14 信封并按角色处理；此聊天提醒不改变任务状态。"
    return {"status": "READY_FOR_RUNTIME_MESSAGE_DELIVERY", "messageId": message_id, "direction": envelope["direction"], "sourceThreadRef": caller, "targetThreadRef": target["threadRef"], "target": target, "payloadDigest": envelope["payload"]["payloadDigest"], "outboundPrompt": prompt, "requiredTransport": TRANSPORT, "writePerformed": False, "message": "必须用 Codex 原生 send_message_to_thread 发送上述短消息；成功后才可记录回执。"}, 0


def validate_delivery(raw: Dict[str, Any], project_id: str, message_id: str, source: str, target: str, payload_digest: str) -> Dict[str, Any]:
    required = {"deliverySchemaVersion", "recordType", "deliveryId", "projectId", "messageId", "sourceThreadRef", "targetThreadRef", "payloadDigest", "transport", "outcome", "runtimeReceiptRef", "runtimeReceiptDigest"}
    value = exact(raw, required, "C14_DELIVERY_RECEIPT_SCHEMA_INVALID")
    if (
        value["deliverySchemaVersion"] != SCHEMA_VERSION or value["recordType"] != "C14_RUNTIME_DELIVERY_RECEIPT"
        or value["projectId"] != project_id or value["messageId"] != message_id
        or ref(value["sourceThreadRef"], "C14_SOURCE_THREAD_REF_INVALID") != source
        or ref(value["targetThreadRef"], "C14_TARGET_THREAD_REF_INVALID") != target
        or digest(value["payloadDigest"], "C14_PAYLOAD_DIGEST_INVALID") != payload_digest
        or value["transport"] != TRANSPORT or value["outcome"] not in {"SUCCEEDED", "FAILED"}
    ):
        raise BridgeError("C14_DELIVERY_RECEIPT_SCHEMA_INVALID")
    return {"deliveryId": message_ref(value["deliveryId"], "C14_DELIVERY_ID_INVALID"), "outcome": value["outcome"], "runtimeReceiptRef": ref(value["runtimeReceiptRef"], "C14_RUNTIME_RECEIPT_REF_INVALID"), "runtimeReceiptDigest": digest(value["runtimeReceiptDigest"], "C14_RUNTIME_RECEIPT_DIGEST_INVALID")}


def record_delivery(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args); project_id = project_ref(args.project_id)
    message_id = message_ref(args.message_id, "C14_MESSAGE_ID_INVALID")
    caller = ref(args.current_thread_ref, "C14_CALLER_THREAD_REF_INVALID")
    envelope = load_envelope(data_root, project_id, message_id)
    routing = verify_routing(data_root, project_id); ledger = verified_ledger(data_root, project_id)
    source_is_current(envelope, routing, ledger, caller)
    target = live_target(envelope, routing, ledger)
    raw, source_digest = private_json(args.receipt, data_root, "C14_DELIVERY_RECEIPT_INVALID_JSON")
    delivery = validate_delivery(raw, project_id, message_id, caller, target["threadRef"], envelope["payload"]["payloadDigest"])
    path = delivery_dir(data_root, project_id, message_id) / f"{delivery['deliveryId']}.json"
    if path.exists():
        existing = read_json(path, "C14_DELIVERY_RECEIPT_INVALID")
        if existing.get("sourceReceiptDigest") == source_digest:
            return {"status": "IDEMPOTENT_RUNTIME_MESSAGE_DELIVERY", "messageId": message_id, "deliveryId": delivery["deliveryId"], "messageState": message_state(data_root, project_id, message_id), "writePerformed": False}, 0
        raise BridgeError("C14_DELIVERY_ID_REUSED")
    if message_state(data_root, project_id, message_id) != "QUEUED": raise BridgeError("C14_MESSAGE_ALREADY_DELIVERED_OR_COMPLETED")
    artifact = {"schemaVersion": SCHEMA_VERSION, "recordType": "C14_RUNTIME_DELIVERY_RECEIPT", "createdAt": utc_now(), "projectId": project_id, "messageId": message_id, "deliveryId": delivery["deliveryId"], "sourceThreadRef": caller, "targetThreadRef": target["threadRef"], "payloadDigest": envelope["payload"]["payloadDigest"], "transport": TRANSPORT, "outcome": delivery["outcome"], "runtimeReceiptRef": delivery["runtimeReceiptRef"], "runtimeReceiptDigest": delivery["runtimeReceiptDigest"], "sourceReceiptDigest": source_digest, "boundary": {"nativeTransportReported": True, "deliveryIsNotAcknowledgement": delivery["outcome"] == "SUCCEEDED", "c03TaskStateChanged": False, "businessWritePerformed": False}}
    write_exclusive(path, artifact)
    if delivery["outcome"] == "FAILED":
        return {"status": "MESSAGE_DELIVERY_FAILED_RETRYABLE", "messageId": message_id, "deliveryId": delivery["deliveryId"], "messageState": "QUEUED", "writePerformed": True, "message": "原生发送失败已留痕；可重新准备并向当前逻辑收件人重试，不能称为已送达。"}, 0
    return {"status": "RUNTIME_MESSAGE_DELIVERED_AWAITING_ACKNOWLEDGEMENT", "messageId": message_id, "deliveryId": delivery["deliveryId"], "messageState": "DELIVERED", "writePerformed": True, "message": "原生消息已发送并有回执；只有收件窗口确认后，才能称为对方已收到。"}, 0


def acknowledge(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args); project_id = project_ref(args.project_id)
    message_id = message_ref(args.message_id, "C14_MESSAGE_ID_INVALID")
    caller = ref(args.current_thread_ref, "C14_CALLER_THREAD_REF_INVALID")
    acknowledgement_ref = ref(args.acknowledgement_ref, "C14_ACKNOWLEDGEMENT_REF_INVALID")
    envelope = load_envelope(data_root, project_id, message_id)
    routing = verify_routing(data_root, project_id); ledger = verified_ledger(data_root, project_id)
    if not has_successful_delivery(data_root, project_id, message_id): raise BridgeError("C14_DELIVERY_NOT_CONFIRMED")
    target = live_target(envelope, routing, ledger)
    if target["threadRef"] != caller: raise BridgeError("C14_ACKNOWLEDGER_IS_NOT_CURRENT_TARGET")
    path = acknowledgement_path(data_root, project_id, message_id)
    if path.exists():
        existing = read_json(path, "C14_ACKNOWLEDGEMENT_INVALID")
        if existing.get("acknowledgedByThreadRef") == caller and existing.get("acknowledgementRef") == acknowledgement_ref:
            return {"status": "IDEMPOTENT_MESSAGE_ACKNOWLEDGEMENT", "messageId": message_id, "messageState": message_state(data_root, project_id, message_id), "writePerformed": False}, 0
        raise BridgeError("C14_MESSAGE_ALREADY_ACKNOWLEDGED")
    artifact = {"schemaVersion": SCHEMA_VERSION, "recordType": "C14_MESSAGE_ACKNOWLEDGEMENT", "createdAt": utc_now(), "projectId": project_id, "messageId": message_id, "acknowledgementRef": acknowledgement_ref, "acknowledgedByThreadRef": caller, "targetRole": target["role"], "payloadDigest": envelope["payload"]["payloadDigest"], "boundary": {"messageReadByCurrentTarget": True, "doesNotValidateTask": True, "doesNotChangeTaskStatus": True, "businessWritePerformed": False}}
    write_exclusive(path, artifact)
    action = "C08_ADMIT_RETURN_SUMMARY_ONLY" if envelope["direction"] == "TASK_TO_CENTRAL" and envelope["payload"]["type"] == "TASK_HANDBACK_NOTIFICATION" else "READ_COMMAND_REFERENCE_AND_REPORT_RESULT"
    return {"status": "MESSAGE_ACKNOWLEDGED", "messageId": message_id, "messageState": "ACKNOWLEDGED", "receiverAction": action, "writePerformed": True, "message": "收件方已确认看到票据指针；这不等于任务验收、执行完成或 DONE。"}, 0


def record_command_result(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args); project_id = project_ref(args.project_id)
    message_id = message_ref(args.message_id, "C14_MESSAGE_ID_INVALID")
    caller = ref(args.current_thread_ref, "C14_CALLER_THREAD_REF_INVALID")
    envelope = load_envelope(data_root, project_id, message_id)
    if envelope.get("direction") != "CENTRAL_TO_TASK": raise BridgeError("C14_COMMAND_RESULT_DIRECTION_INVALID")
    routing = verify_routing(data_root, project_id); ledger = verified_ledger(data_root, project_id)
    target = live_target(envelope, routing, ledger)
    if target["threadRef"] != caller: raise BridgeError("C14_COMMAND_RESULT_CALLER_INVALID")
    if not acknowledgement_path(data_root, project_id, message_id).exists(): raise BridgeError("C14_COMMAND_NOT_ACKNOWLEDGED")
    raw, source_digest = private_json(args.result, data_root, "C14_COMMAND_RESULT_INVALID_JSON")
    required = {"resultSchemaVersion", "recordType", "resultId", "projectId", "messageId", "commandId", "outcome", "resultRef", "summary"}
    value = exact(raw, required, "C14_COMMAND_RESULT_SCHEMA_INVALID")
    if value["resultSchemaVersion"] != SCHEMA_VERSION or value["recordType"] != "C14_TASK_COMMAND_RESULT" or value["projectId"] != project_id or value["messageId"] != message_id or value["commandId"] != envelope["payload"]["commandId"]:
        raise BridgeError("C14_COMMAND_RESULT_SCHEMA_INVALID")
    outcome = str(value["outcome"]).strip().upper()
    if outcome not in RESULT_OUTCOMES: raise BridgeError("C14_COMMAND_RESULT_OUTCOME_INVALID")
    path = command_result_path(data_root, project_id, message_id)
    result_message_id = message_ref(f"result-{message_id}", "C14_RESULT_MESSAGE_ID_INVALID")
    result_written = False
    existing = None
    if path.exists():
        existing = read_json(path, "C14_COMMAND_RESULT_INVALID")
        if existing.get("sourceResultDigest") != source_digest:
            raise BridgeError("C14_COMMAND_RESULT_ALREADY_RECORDED")
    result_id = message_ref(value["resultId"], "C14_RESULT_ID_INVALID")
    result = {"schemaVersion": SCHEMA_VERSION, "recordType": "C14_TASK_COMMAND_RESULT", "createdAt": utc_now(), "projectId": project_id, "messageId": message_id, "resultId": result_id, "commandId": envelope["payload"]["commandId"], "outcome": outcome, "resultRef": ref(value["resultRef"], "C14_RESULT_REF_INVALID"), "summary": safe_text(value["summary"], "C14_RESULT_SUMMARY_INVALID"), "sourceResultDigest": source_digest, "boundary": {"doesNotChangeTaskStatus": True, "doesNotDeclareDone": True, "businessWritePerformed": False}}
    if existing is None:
        write_exclusive(path, result)
        result_written = True
    elif any(existing.get(key) != val for key, val in result.items() if key != "createdAt"):
        raise BridgeError("C14_COMMAND_RESULT_INVALID")
    active = routing["roles"]["CURRENT_CENTRAL"]
    payload = {"type": "TASK_COMMAND_RESULT", "resultId": result_id, "commandId": result["commandId"], "outcome": outcome, "resultRef": result["resultRef"], "summary": result["summary"]}
    result_envelope = {"schemaVersion": SCHEMA_VERSION, "recordType": "C14_MESSAGE_ENVELOPE", "createdAt": utc_now(), "messageId": result_message_id, "projectId": project_id, "direction": "TASK_TO_CENTRAL", "source": {"role": "TASK_WINDOW", "windowId": target["windowId"], "runtimeThreadRef": caller, "generation": target["generation"]}, "targetAtEnqueue": {"role": "CURRENT_CENTRAL", "threadRef": active["activeThreadRef"], "generation": active["generation"], "routingRevision": routing["revision"]}, "taskIdentity": envelope["taskIdentity"], "payload": {**payload, "payloadDigest": canonical_digest(payload)}, "sourceRequestDigest": source_digest, "boundary": {"messageBodyContainsOnlyPointers": True, "doesNotChangeTaskStatus": True, "businessWritePerformed": False}}
    if envelope_path(data_root, project_id, result_message_id).exists():
        saved = load_envelope(data_root, project_id, result_message_id)
        if any(saved.get(key) != val for key, val in result_envelope.items() if key not in {"createdAt", "targetAtEnqueue"}):
            raise BridgeError("C14_RESULT_NOTIFICATION_ALREADY_EXISTS")
        return {"status": "IDEMPOTENT_COMMAND_RESULT", "messageId": message_id, "messageState": outcome, "resultNotificationMessageId": result_message_id, "writePerformed": result_written}, 0
    sealed_path = envelope_receipt_path(data_root, project_id, result_message_id)
    if sealed_path.exists():
        sealed = read_json(sealed_path, "C14_ENVELOPE_RECEIPT_MISSING")
        saved = sealed.get("artifact")
        if (sealed.get("schemaVersion") != SCHEMA_VERSION
            or sealed.get("recordType") != "C14_IMMUTABLE_MESSAGE_ENQUEUE_RECEIPT"
            or sealed.get("projectId") != project_id or sealed.get("messageId") != result_message_id
            or not isinstance(saved, dict) or sealed.get("artifactDigest") != canonical_digest(saved)
            or any(saved.get(key) != val for key, val in result_envelope.items() if key not in {"createdAt", "targetAtEnqueue"})):
            raise BridgeError("C14_ENVELOPE_INTEGRITY_INVALID")
        # The sealed receipt is authoritative; do not regenerate historical metadata.
        write_exclusive(envelope_path(data_root, project_id, result_message_id), saved)
    else:
        write_envelope(data_root, project_id, result_envelope)
    return {"status": "COMMAND_RESULT_RECORDED_AWAITING_RUNTIME_DELIVERY", "messageId": message_id, "messageState": outcome, "resultNotificationMessageId": result_message_id, "targetRole": "CURRENT_CENTRAL", "writePerformed": True, "message": "任务窗口结果已封存；还必须把结果指针真实发送并由中央确认，不能只在本窗口回复。"}, 0


def list_incoming(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args); project_id = project_ref(args.project_id)
    caller = ref(args.current_thread_ref, "C14_CENTRAL_THREAD_REF_INVALID")
    routing = verify_routing(data_root, project_id); current_role(routing, "CURRENT_CENTRAL", caller)
    ledger = verified_ledger(data_root, project_id); root = bridge_root(data_root, project_id) / "messages"; items = []
    if root.exists():
        for path in sorted(root.iterdir()):
            if not path.is_dir(): continue
            envelope = load_envelope(data_root, project_id, path.name)
            if envelope["direction"] != "TASK_TO_CENTRAL": continue
            if live_target(envelope, routing, ledger)["threadRef"] != caller: continue
            current_state = message_state(data_root, project_id, path.name)
            if current_state in {"QUEUED", "DELIVERED"}:
                payload = envelope["payload"]
                items.append({"messageId": path.name, "taskIdentity": envelope["taskIdentity"], "payloadType": payload["type"], "payloadRef": payload.get("returnTicketId", payload.get("resultId")), "messageState": current_state})
    return {"status": "C14_INCOMING_MESSAGES_READ", "projectId": project_id, "currentCentralGeneration": routing["roles"]["CURRENT_CENTRAL"]["generation"], "incomingCount": len(items), "messages": items, "writePerformed": False, "ledgerUpdated": False}, 0


def verify(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args); project_id = project_ref(args.project_id)
    routing = verify_routing(data_root, project_id); verified_ledger(data_root, project_id)
    root = bridge_root(data_root, project_id) / "messages"; count = 0
    if root.exists():
        for path in sorted(root.iterdir()):
            if not path.is_dir(): raise BridgeError("C14_MESSAGE_DIRECTORY_INVALID")
            envelope = load_envelope(data_root, project_id, path.name)
            for receipt in delivery_records(data_root, project_id, path.name):
                if receipt.get("payloadDigest") != envelope["payload"]["payloadDigest"]: raise BridgeError("C14_DELIVERY_PAYLOAD_MISMATCH")
            if acknowledgement_path(data_root, project_id, path.name).exists() and not has_successful_delivery(data_root, project_id, path.name): raise BridgeError("C14_ACKNOWLEDGEMENT_WITHOUT_DELIVERY")
            count += 1
    return {"status": "C14_COMMUNICATION_BRIDGE_VERIFIED", "projectId": project_id, "routingRevision": routing["revision"], "messageCount": count, "writePerformed": False, "ledgerUpdated": False}, 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="C14 receipt-backed Codex task communication bridge")
    source = parser.add_mutually_exclusive_group(required=True); source.add_argument("--data-root"); source.add_argument("--config")
    parser.add_argument("--project-id"); parser.add_argument("--writer-id")
    commands = parser.add_subparsers(dest="command", required=True)
    task = commands.add_parser("enqueue-task-to-central"); task.add_argument("--request", required=True)
    central = commands.add_parser("enqueue-central-command"); central.add_argument("--current-thread-ref", required=True); central.add_argument("--request", required=True)
    prepare = commands.add_parser("prepare-delivery"); prepare.add_argument("--message-id", required=True); prepare.add_argument("--current-thread-ref", required=True)
    delivery = commands.add_parser("record-delivery"); delivery.add_argument("--message-id", required=True); delivery.add_argument("--current-thread-ref", required=True); delivery.add_argument("--receipt", required=True)
    ack = commands.add_parser("acknowledge"); ack.add_argument("--message-id", required=True); ack.add_argument("--current-thread-ref", required=True); ack.add_argument("--acknowledgement-ref", required=True)
    result = commands.add_parser("record-command-result"); result.add_argument("--message-id", required=True); result.add_argument("--current-thread-ref", required=True); result.add_argument("--result", required=True)
    incoming = commands.add_parser("list-incoming"); incoming.add_argument("--current-thread-ref", required=True)
    commands.add_parser("verify")
    args = parser.parse_args()
    if args.command != "enqueue-task-to-central" and not args.project_id: parser.error("--project-id is required for this command")
    return args


def dispatch(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    return {"enqueue-task-to-central": queue_task_to_central, "enqueue-central-command": queue_central_command, "prepare-delivery": prepare_delivery, "record-delivery": record_delivery, "acknowledge": acknowledge, "record-command-result": record_command_result, "list-incoming": list_incoming, "verify": verify}[args.command](args)


def main() -> int:
    args = parse_args()
    try:
        result, code = dispatch(args); print_result(result); return code
    except (BridgeError, ContinuityError, LedgerError, C02Error) as error:
        print_result({"status": "REFUSED", "reason": str(error), "writePerformed": False, "ledgerUpdated": False, "businessWritePerformed": False}); return 2


if __name__ == "__main__":
    sys.exit(main())
