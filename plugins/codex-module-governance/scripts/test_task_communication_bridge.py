#!/usr/bin/env python3
"""C14 regression tests with fictional private data only."""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parent
C02 = SCRIPTS / "initialize_project.py"
C03 = SCRIPTS / "ledger_manager.py"
C08 = SCRIPTS / "role_continuity_controller.py"
C14 = SCRIPTS / "task_communication_bridge.py"
PROJECT, TASK, WINDOW = "bridge-demo-project", "C-014", "window-c014-g1"
CENTRAL_1, CENTRAL_2 = "central-thread-g1", "central-thread-g2"


def invoke(script, arguments):
    completed = subprocess.run([sys.executable, str(script), *arguments], check=False, capture_output=True, text=True)
    return completed.returncode, json.loads(completed.stdout)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def c03(root, command, *arguments):
    return invoke(C03, ["--data-root", str(root), "--project-id", PROJECT, "--writer-id", "codex-module-central", command, *arguments])


def c08(root, command, *arguments, writer=True):
    base = ["--data-root", str(root), "--project-id", PROJECT]
    if writer: base += ["--writer-id", "codex-module-central"]
    return invoke(C08, [*base, command, *arguments])


def c14(root, command, *arguments, writer=False):
    base = ["--data-root", str(root), "--project-id", PROJECT]
    if writer: base += ["--writer-id", "codex-module-central"]
    return invoke(C14, [*base, command, *arguments])


def setup(root):
    code, output = invoke(C02, ["--data-root", str(root), "--project-id", PROJECT, "--display-name", "Bridge demo", "--scope-summary", "Fictional C14 bridge only.", "--apply"])
    if code != 0: raise AssertionError(output)
    for command in [
        ("initialize", "--apply"),
        ("add-task", "--task-id", TASK, "--title", "双向通信", "--business-goal", "Verify a fictional message bridge.", "--plan-ref", "plan-c014"),
        ("register-window", "--window-id", WINDOW, "--task-id", TASK, "--context-mode", "NEW"),
        ("transition-task", "--task-id", TASK, "--to-status", "READY", "--reason", "Ready."),
        ("transition-task", "--task-id", TASK, "--to-status", "IN_PROGRESS", "--reason", "Started."),
    ]:
        code, output = c03(root, command[0], *command[1:])
        if code != 0: raise AssertionError(output)
    code, output = c08(root, "initialize", "--calling-thread-ref", CENTRAL_1, "--central-thread-ref", CENTRAL_1, "--runtime-project-id", "runtime-project-c14", "--execution-map-ref", "execution-map-c14")
    if code != 0: raise AssertionError(output)


def handback():
    return {
        "handbackSchemaVersion": "0.8.0", "recordType": "C06_TASK_WINDOW_HANDBACK", "handbackId": "handback-c014-001", "returnTicketId": "return-ticket-c014-001", "routeRevisionSeen": 1,
        "projectId": PROJECT, "packageId": "package-c014-001", "c05ReviewId": "c05-review-c014-001", "taskId": TASK, "windowId": WINDOW, "completionSignalId": "completion-signal-c014-001",
        "submittedBy": {"type": "task-window", "id": WINDOW}, "bossHandbackAuthorization": {"status": "APPROVED", "reference": "boss-return-c014"},
        "executedScopeRefs": ["scope-c014"], "evidenceRefs": ["evidence-c014"], "testAndObjectRefs": ["test-c014"], "parentQualityReviewRefs": [], "unresolvedRefs": [], "residualRiskRefs": [],
    }


def queue_return(root):
    handback_path = write(root / "inputs" / "handback.json", handback())
    code, output = c08(root, "submit-return", "--handback", str(handback_path), writer=False)
    if code != 0: raise AssertionError(output)
    request = {
        "requestSchemaVersion": "0.19.0", "recordType": "C14_TASK_TO_CENTRAL_REQUEST", "messageId": "message-c014-return", "projectId": PROJECT,
        "taskIdentity": {"taskId": TASK, "canonicalTitle": "C-014｜双向通信"}, "sourceWindow": {"windowId": WINDOW, "runtimeThreadRef": WINDOW, "generation": 1},
        "eventId": output["eventId"], "returnTicketId": output["returnTicketId"], "handbackDigest": output["handbackDigest"], "routeRevisionSeen": 1, "summary": "Fictional return ticket is ready.",
    }
    request_path = write(root / "inputs" / "task-to-central.json", request)
    return output, c14(root, "enqueue-task-to-central", "--request", str(request_path))


def receipt(root, message_id, source, target, payload_digest, delivery_id):
    value = {
        "deliverySchemaVersion": "0.19.0", "recordType": "C14_RUNTIME_DELIVERY_RECEIPT", "deliveryId": delivery_id, "projectId": PROJECT, "messageId": message_id,
        "sourceThreadRef": source, "targetThreadRef": target, "payloadDigest": payload_digest, "transport": "CODEX_SEND_MESSAGE_TO_THREAD", "outcome": "SUCCEEDED", "runtimeReceiptRef": f"runtime-receipt-{delivery_id}", "runtimeReceiptDigest": "a" * 64,
    }
    return write(root / "inputs" / f"{delivery_id}.json", value)


def replace_central(root):
    request = {"handoverSchemaVersion": "0.15.0", "recordType": "C08_ROLE_HANDOVER_REQUEST", "handoverId": "handover-c14-central-g2", "projectId": PROJECT, "role": "CURRENT_CENTRAL", "mode": "REPLACE_ACTIVE_ROLE", "reasonRef": "reason-c14", "executionMapRef": "execution-map-c14", "contextRefs": ["outline-c14", "ledger-c14"], "nextExpectedSignals": ["bridge-message"]}
    request_path = write(root / "inputs" / "handover-request.json", request)
    code, output = c08(root, "prepare-handover", "--request", str(request_path))
    if code != 0: raise AssertionError(output)
    package_path = root / "role-continuity" / PROJECT / "handovers" / "handover-c14-central-g2" / "handover-package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    activation = {"activationSchemaVersion": "0.15.0", "recordType": "C08_ROLE_SUCCESSOR_ACTIVATION", "handoverId": "handover-c14-central-g2", "projectId": PROJECT, "role": "CURRENT_CENTRAL", "successorThreadRef": CENTRAL_2, "runtimeProjectId": "runtime-project-c14", "model": "gpt-5.6-sol", "handoverPackageDigest": sha(package)}
    code, output = c08(root, "activate-successor", "--handover-id", "handover-c14-central-g2", "--activation", str(write(root / "inputs" / "handover-activation.json", activation)))
    if code != 0: raise AssertionError(output)


class TaskCommunicationBridgeTests(unittest.TestCase):
    def test_return_requires_native_delivery_before_acknowledgement(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root)
            _, (code, queued) = queue_return(root)
            self.assertEqual(code, 0, queued)
            code, fake = c14(root, "acknowledge", "--message-id", "message-c014-return", "--current-thread-ref", CENTRAL_1, "--acknowledgement-ref", "central-ack-c014")
            self.assertEqual(code, 2); self.assertEqual(fake["reason"], "C14_DELIVERY_NOT_CONFIRMED")
            code, ready = c14(root, "prepare-delivery", "--message-id", "message-c014-return", "--current-thread-ref", WINDOW)
            self.assertEqual(code, 0, ready); self.assertEqual(ready["targetThreadRef"], CENTRAL_1); self.assertNotIn("evidence", ready["outboundPrompt"].lower())
            code, sent = c14(root, "record-delivery", "--message-id", "message-c014-return", "--current-thread-ref", WINDOW, "--receipt", str(receipt(root, "message-c014-return", WINDOW, CENTRAL_1, ready["payloadDigest"], "delivery-c014-return")))
            self.assertEqual(code, 0, sent); self.assertEqual(sent["messageState"], "DELIVERED")
            code, acknowledged = c14(root, "acknowledge", "--message-id", "message-c014-return", "--current-thread-ref", CENTRAL_1, "--acknowledgement-ref", "central-ack-c014")
            self.assertEqual(code, 0, acknowledged); self.assertEqual(acknowledged["receiverAction"], "C08_ADMIT_RETURN_SUMMARY_ONLY")

    def test_command_round_trip_does_not_change_c03_task_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root)
            before = (root / "module-ledgers" / PROJECT / "ledger.json").read_bytes()
            command = {"requestSchemaVersion": "0.19.0", "recordType": "C14_CENTRAL_TO_TASK_REQUEST", "messageId": "message-c014-command", "projectId": PROJECT, "taskIdentity": {"taskId": TASK, "canonicalTitle": "C-014｜双向通信"}, "windowId": WINDOW, "commandId": "command-c014-001", "commandType": "TASK_INSTRUCTION", "commandRef": "command-file-c014", "commandDigest": "b" * 64, "summary": "Read the fictional instruction file."}
            code, queued = c14(root, "enqueue-central-command", "--current-thread-ref", CENTRAL_1, "--request", str(write(root / "inputs" / "central-command.json", command)), writer=True)
            self.assertEqual(code, 0, queued)
            code, ready = c14(root, "prepare-delivery", "--message-id", "message-c014-command", "--current-thread-ref", CENTRAL_1)
            self.assertEqual(code, 0, ready); self.assertEqual(ready["targetThreadRef"], WINDOW)
            self.assertEqual(c14(root, "record-delivery", "--message-id", "message-c014-command", "--current-thread-ref", CENTRAL_1, "--receipt", str(receipt(root, "message-c014-command", CENTRAL_1, WINDOW, ready["payloadDigest"], "delivery-c014-command")))[0], 0)
            self.assertEqual(c14(root, "acknowledge", "--message-id", "message-c014-command", "--current-thread-ref", WINDOW, "--acknowledgement-ref", "task-ack-c014")[0], 0)
            result = {"resultSchemaVersion": "0.19.0", "recordType": "C14_TASK_COMMAND_RESULT", "resultId": "result-c014-command", "projectId": PROJECT, "messageId": "message-c014-command", "commandId": "command-c014-001", "outcome": "APPLIED", "resultRef": "result-evidence-c014", "summary": "Fictional instruction applied."}
            code, recorded = c14(root, "record-command-result", "--message-id", "message-c014-command", "--current-thread-ref", WINDOW, "--result", str(write(root / "inputs" / "command-result.json", result)))
            self.assertEqual(code, 0, recorded); self.assertEqual(recorded["resultNotificationMessageId"], "result-message-c014-command")
            code, ready_result = c14(root, "prepare-delivery", "--message-id", recorded["resultNotificationMessageId"], "--current-thread-ref", WINDOW)
            self.assertEqual(code, 0, ready_result)
            self.assertEqual(c14(root, "record-delivery", "--message-id", recorded["resultNotificationMessageId"], "--current-thread-ref", WINDOW, "--receipt", str(receipt(root, recorded["resultNotificationMessageId"], WINDOW, CENTRAL_1, ready_result["payloadDigest"], "delivery-c014-result")))[0], 0)
            self.assertEqual(c14(root, "acknowledge", "--message-id", recorded["resultNotificationMessageId"], "--current-thread-ref", CENTRAL_1, "--acknowledgement-ref", "central-result-c014")[0], 0)
            self.assertEqual(before, (root / "module-ledgers" / PROJECT / "ledger.json").read_bytes())

    def test_unacknowledged_return_retargets_new_central(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root)
            _, (code, queued) = queue_return(root); self.assertEqual(code, 0, queued)
            replace_central(root)
            code, ready = c14(root, "prepare-delivery", "--message-id", "message-c014-return", "--current-thread-ref", WINDOW)
            self.assertEqual(code, 0, ready); self.assertEqual(ready["targetThreadRef"], CENTRAL_2)
            self.assertEqual(c14(root, "record-delivery", "--message-id", "message-c014-return", "--current-thread-ref", WINDOW, "--receipt", str(receipt(root, "message-c014-return", WINDOW, CENTRAL_2, ready["payloadDigest"], "delivery-c014-new")))[0], 0)
            self.assertEqual(c14(root, "acknowledge", "--message-id", "message-c014-return", "--current-thread-ref", CENTRAL_2, "--acknowledgement-ref", "central-ack-c014-g2")[0], 0)


if __name__ == "__main__":
    unittest.main()
