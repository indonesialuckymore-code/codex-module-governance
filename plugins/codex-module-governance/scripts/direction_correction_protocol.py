"""Read C14 proofs for C03 corrections; no parallel task state or native sends."""
from pathlib import Path
import hashlib
import json

import task_communication_bridge as bridge
from ledger_manager import canonical_digest, correction_snapshot, window_current_task_id
from role_continuity_controller import ContinuityError, read_json


class CorrectionError(Exception):
    pass


APPLICATION_CHECKS = {"CURRENT_INSTRUCTION_APPLIED", "RETAINED_REQUIREMENTS_PRESERVED",
    "SUPERSEDED_WORK_STOPPED", "IN_FLIGHT_EFFECTS_RECONCILED", "APPROVED_CONTRACT_UNCHANGED"}


def load_verification(root, filename, project_id, task, caller):
    """Bind a central evidence assessment; hashes do not prove business semantics."""
    try:
        path = Path(filename).resolve(strict=True)
        if not path.is_relative_to(root.resolve()): raise CorrectionError("CORRECTION_PRIVATE_VERIFICATION_REQUIRED")
        value = json.loads(path.read_text(encoding="utf-8"))
        required = {"recordType", "verificationId", "projectId", "taskId", "correctionId", "revision",
                    "correctionDigest", "verifiedByThreadRef", "checks", "evidence"}
        current = task.get("directionCorrections", [])[-1]
        if caller in {item["runtimeThreadRef"] for item in current["windowBindings"]}:
            raise CorrectionError("CORRECTION_VERIFIER_CANNOT_BE_EXECUTING_WINDOW")
        if (not isinstance(value, dict) or set(value) != required
            or value["recordType"] != "C03_DIRECTION_APPLICATION_VERIFICATION"
            or value["projectId"] != project_id or value["taskId"] != task["taskId"]
            or value["verifiedByThreadRef"] != caller
            or type(value["revision"]) is not int or value["revision"] != current["revision"]
            or value["correctionId"] != current["correctionId"] or value["correctionDigest"] != current["requestDigest"]):
            raise CorrectionError("CORRECTION_VERIFICATION_BINDING_INVALID")
        bridge.ref(value["verificationId"], "CORRECTION_VERIFICATION_ID_INVALID")
        checks = value["checks"]
        if not isinstance(checks, list) or len(checks) != len(APPLICATION_CHECKS):
            raise CorrectionError("CORRECTION_APPLICATION_CHECKS_REQUIRED")
        if any(not isinstance(item, dict) or set(item) != {"check", "status", "evidenceRefs"}
               or item["status"] != "PASS" or not isinstance(item["evidenceRefs"], list) or not item["evidenceRefs"]
               or any(not isinstance(ref, str) for ref in item["evidenceRefs"]) for item in checks):
            raise CorrectionError("CORRECTION_APPLICATION_CHECKS_REQUIRED")
        if {item["check"] for item in checks} != APPLICATION_CHECKS:
            raise CorrectionError("CORRECTION_APPLICATION_CHECKS_REQUIRED")
        proofs = value["evidence"]
        if not isinstance(proofs, list) or not proofs: raise CorrectionError("CORRECTION_EVIDENCE_REQUIRED")
        evidence = []
        for proof in proofs:
            if not isinstance(proof, dict) or set(proof) != {"reference", "file", "sha256"}:
                raise CorrectionError("CORRECTION_EVIDENCE_INVALID")
            reference = bridge.ref(proof["reference"], "CORRECTION_EVIDENCE_REFERENCE_INVALID")
            evidence_path = Path(proof["file"]).resolve(strict=True)
            if not evidence_path.is_relative_to(root.resolve()): raise CorrectionError("CORRECTION_PRIVATE_EVIDENCE_REQUIRED")
            digest = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
            if digest != proof["sha256"]: raise CorrectionError("CORRECTION_EVIDENCE_DIGEST_MISMATCH")
            evidence.append({"reference": reference, "sha256": digest})
        available = {item["reference"] for item in evidence}
        if len(available) != len(evidence) or any(not set(item["evidenceRefs"]) <= available for item in checks):
            raise CorrectionError("CORRECTION_EVIDENCE_REFERENCE_MISMATCH")
        return {key: value[key] for key in required - {"recordType", "evidence"}} | {
            "evidence": evidence, "sourceDigest": canonical_digest(value)}
    except (OSError, ValueError, TypeError, IndexError, KeyError, bridge.BridgeError) as error:
        raise CorrectionError("CORRECTION_VERIFICATION_INVALID: " + str(error)) from error


def delivery_state(root, project_id, envelope, expected_target=None, expected_source=None):
    message_id = envelope["messageId"]
    successes = []
    for record in bridge.delivery_records(root, project_id, message_id):
        raw = {key: record.get(key) for key in ("recordType", "deliveryId", "projectId", "messageId",
            "sourceThreadRef", "targetThreadRef", "payloadDigest", "transport", "outcome", "runtimeReceiptRef", "runtimeReceiptDigest")}
        raw["deliverySchemaVersion"] = bridge.SCHEMA_VERSION
        if (record.get("sourceReceiptDigest") != canonical_digest(raw)
            or raw["payloadDigest"] != envelope["payload"]["payloadDigest"]
            or raw["transport"] != bridge.TRANSPORT
            or raw["outcome"] not in {"SUCCEEDED", "FAILED"}
            or expected_target and raw["targetThreadRef"] != expected_target
            or expected_source and raw["sourceThreadRef"] != expected_source):
            raise CorrectionError("CORRECTION_DELIVERY_PROOF_INVALID")
        if raw["outcome"] == "SUCCEEDED": successes.append(record)
    ack_path = bridge.acknowledgement_path(root, project_id, message_id)
    if ack_path.exists():
        ack = read_json(ack_path, "CORRECTION_ACK_INVALID")
        if (ack.get("recordType") != "C14_MESSAGE_ACKNOWLEDGEMENT"
            or ack.get("projectId") != project_id or ack.get("messageId") != message_id
            or ack.get("payloadDigest") != envelope["payload"]["payloadDigest"]
            or not any(item["targetThreadRef"] == ack.get("acknowledgedByThreadRef") for item in successes)):
            raise CorrectionError("CORRECTION_ACK_INVALID")
        return "ACKNOWLEDGED"
    return "DELIVERED" if successes else "QUEUED"


def communication_snapshot(root: Path, project_id: str, ledger, task_id: str):
    task = ledger["tasks"][task_id]
    current = correction_snapshot(task, project_id)
    if current["current"] and not current["governanceHold"]:
        verified = next(item for item in task["directionVerifications"] if item["revision"] == current["correctionRevision"])
        return {"communication": verified["communication"], "readyForVerification": False,
                "communicationSource": "SEALED_VERIFICATION"}
    records = []
    try:
        for request, binding in zip(current["messageRequests"], current["current"]["windowBindings"] if current["current"] else []):
            window = ledger["windows"].get(binding["windowId"], {})
            if (window_current_task_id(window) != task_id or window.get("runtimeThreadRef", binding["windowId"]) != binding["runtimeThreadRef"]
                or window.get("generation", 1) != binding["generation"]):
                raise CorrectionError("CORRECTION_WINDOW_BINDING_CHANGED")
            message_id = request["messageId"]
            record = {"windowId": binding["windowId"], "messageId": message_id, "commandState": "NOT_QUEUED",
                      "reportedOutcome": None, "resultNotificationState": "NOT_QUEUED"}
            if bridge.envelope_path(root, project_id, message_id).exists():
                envelope = bridge.load_envelope(root, project_id, message_id)
                if envelope.get("sourceRequestDigest") != canonical_digest(request):
                    raise CorrectionError("CORRECTION_MESSAGE_MISMATCH")
                bridge.validate_direction_command(envelope["payload"], task, window, binding["windowId"])
                record["commandState"] = delivery_state(root, project_id, envelope, expected_target=binding["runtimeThreadRef"])
                result_path = bridge.command_result_path(root, project_id, message_id)
                if result_path.exists():
                    result = read_json(result_path, "CORRECTION_RESULT_INVALID")
                    raw = {key: result.get(key) for key in ("recordType", "resultId", "projectId", "messageId",
                        "commandId", "outcome", "resultRef", "summary")}
                    raw["resultSchemaVersion"] = bridge.SCHEMA_VERSION
                    if (record["commandState"] != "ACKNOWLEDGED" or raw["recordType"] != "C14_TASK_COMMAND_RESULT"
                        or raw["projectId"] != project_id or raw["messageId"] != message_id
                        or raw["commandId"] != request["commandId"] or raw["outcome"] not in bridge.RESULT_OUTCOMES
                        or result.get("sourceResultDigest") != canonical_digest(raw)):
                        raise CorrectionError("CORRECTION_RESULT_INVALID")
                    record["reportedOutcome"] = raw["outcome"]
                    notification_id = "result-" + message_id
                    if bridge.envelope_path(root, project_id, notification_id).exists():
                        notification = bridge.load_envelope(root, project_id, notification_id)
                        expected = {"type": "TASK_COMMAND_RESULT", **{key: raw[key] for key in
                            ("resultId", "commandId", "outcome", "resultRef", "summary")}}
                        if (notification.get("direction") != "TASK_TO_CENTRAL"
                            or notification.get("taskIdentity") != request["taskIdentity"]
                            or notification.get("source") != {"role": "TASK_WINDOW", **binding}
                            or notification.get("sourceRequestDigest") != result["sourceResultDigest"]
                            or notification.get("payload") != {**expected, "payloadDigest": canonical_digest(expected)}):
                            raise CorrectionError("CORRECTION_RESULT_NOTIFICATION_MISMATCH")
                        record["resultNotificationState"] = delivery_state(root, project_id, notification,
                            expected_source=binding["runtimeThreadRef"])
                        record["resultNotificationDigest"] = canonical_digest(notification)
            records.append(record)
    except (bridge.BridgeError, ContinuityError) as error:
        raise CorrectionError(str(error)) from error
    return {"communication": records, "readyForVerification": bool(current["current"]) and all(
        item["reportedOutcome"] == "APPLIED" and item["resultNotificationState"] == "ACKNOWLEDGED" for item in records)}
