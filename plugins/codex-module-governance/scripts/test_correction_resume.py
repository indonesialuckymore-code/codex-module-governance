"""Correction synchronization and resumption with fictional transport evidence."""
import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from test_task_corrections import command, correct
from test_task_communication_bridge import PROJECT, TASK, WINDOW, CENTRAL_1, CENTRAL_2, setup, write, c14, receipt, invoke, C14, replace_central


def bcall(root, project, *args, writer=False):
    return invoke(C14, ["--data-root", str(root), "--project-id", project,
        *(["--writer-id", "codex-module-central"] if writer else []), *args])


def send(root, message_id, source, delivery_id, project=PROJECT):
    code, ready = bcall(root, project, "prepare-delivery", "--message-id", message_id, "--current-thread-ref", source)
    if code: raise AssertionError(ready)
    proof_path = receipt(root, message_id, source, ready["targetThreadRef"], ready["payloadDigest"], delivery_id)
    proof = json.loads(proof_path.read_text()); proof["projectId"] = project; write(proof_path, proof)
    code, output = bcall(root, project, "record-delivery", "--message-id", message_id, "--current-thread-ref", source,
        "--receipt", str(proof_path))
    if code: raise AssertionError(output)


def queue_correction(root):
    code, current = correct(root)
    if code: raise AssertionError(current)
    request = current["messageRequests"][0]
    code, output = c14(root, "enqueue-central-command", "--current-thread-ref", CENTRAL_1,
        "--request", str(write(root / "inputs/direction.json", request)), writer=True)
    if code: raise AssertionError(output)
    return request


def report_applied(root, request, project=PROJECT, central=CENTRAL_1, window=WINDOW):
    send(root, request["messageId"], central, "delivery-direction", project)
    code, output = bcall(root, project, "acknowledge", "--message-id", request["messageId"],
        "--current-thread-ref", window, "--acknowledgement-ref", "ack-direction")
    if code: raise AssertionError(output)
    result = {"resultSchemaVersion": "0.19.0", "recordType": "C14_TASK_COMMAND_RESULT",
        "resultId": "direction-result", "projectId": project, "messageId": request["messageId"],
        "commandId": request["commandId"], "outcome": "APPLIED", "resultRef": "direction-application-evidence",
        "summary": "Fictional task reports application, pending central evidence review."}
    code, output = bcall(root, project, "record-command-result", "--message-id", request["messageId"],
        "--current-thread-ref", window, "--result", str(write(root / "inputs/result.json", result)))
    if code: raise AssertionError(output)
    return output["resultNotificationMessageId"]


def complete_roundtrip(root, request=None, project=PROJECT, central=CENTRAL_1, window=WINDOW):
    if request is None: request = queue_correction(root)
    else:
        code, output = bcall(root, project, "enqueue-central-command", "--current-thread-ref", central,
            "--request", str(write(root / "inputs/direction.json", request)), writer=True)
        if code: raise AssertionError(output)
    notification = report_applied(root, request, project, central, window)
    send(root, notification, window, "delivery-result", project)
    code, output = bcall(root, project, "acknowledge", "--message-id", notification,
        "--current-thread-ref", central, "--acknowledgement-ref", "central-result-ack")
    if code: raise AssertionError(output)
    return request


def verification(root, project=PROJECT, task=TASK, central=CENTRAL_1):
    current = command(root, "read-correction", "--task-id", task, project=project, caller=central)[1]["current"]
    evidence = write(root / "inputs/readback.json", {"fictional": True, "instructionApplied": current["instructionRef"],
        "retainedRequirements": current["retainedRequirementRefs"], "oldWorkStopped": True, "externalEffects": "none"})
    value = {"recordType": "C03_DIRECTION_APPLICATION_VERIFICATION", "verificationId": "direction-verification-1",
        "projectId": project, "taskId": task, "correctionId": current["correctionId"], "revision": current["revision"],
        "correctionDigest": current["requestDigest"], "verifiedByThreadRef": central,
        "checks": [{"check": check, "status": "PASS", "evidenceRefs": ["readback-proof"]} for check in
            ["CURRENT_INSTRUCTION_APPLIED", "RETAINED_REQUIREMENTS_PRESERVED", "SUPERSEDED_WORK_STOPPED", "IN_FLIGHT_EFFECTS_RECONCILED", "APPROVED_CONTRACT_UNCHANGED"]],
        "evidence": [{"reference": "readback-proof", "file": str(evidence), "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}]}
    return write(root / "inputs/verification.json", value)


class CorrectionResumeTests(unittest.TestCase):
    def test_new_central_can_verify_existing_acknowledged_results_but_old_central_cannot(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root)
            complete_roundtrip(root)
            report = verification(root)
            replace_central(root)
            self.assertEqual(command(root, "verify-correction", "--task-id", TASK, "--verification", str(report))[0], 2)
            data = json.loads(report.read_text()); data["verifiedByThreadRef"] = CENTRAL_2; write(report, data)
            code, output = command(root, "verify-correction", "--task-id", TASK,
                "--verification", str(report), caller=CENTRAL_2)
            self.assertEqual(code, 0, output)
            self.assertFalse(output["governanceHold"])

    def test_failed_business_check_cannot_be_overridden_by_applied_message(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root)
            complete_roundtrip(root)
            report = verification(root)
            data = json.loads(report.read_text()); data["checks"][0]["status"] = "FAIL"; write(report, data)
            code, output = command(root, "verify-correction", "--task-id", TASK, "--verification", str(report))
            self.assertEqual(code, 2, output)
            self.assertIn("CORRECTION_APPLICATION_CHECKS_REQUIRED", output["reason"])
            self.assertTrue(command(root, "read-correction", "--task-id", TASK)[1]["governanceHold"])

    def test_tampered_delivery_cannot_unlock_correction_verification(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root)
            request = complete_roundtrip(root)
            report = verification(root)
            path = root / "task-communication" / PROJECT / "messages" / request["messageId"] / "deliveries/delivery-direction.json"
            data = json.loads(path.read_text()); data["targetThreadRef"] = "wrong-window"; write(path, data)
            code, output = command(root, "verify-correction", "--task-id", TASK, "--verification", str(report))
            self.assertEqual(code, 2, output)
            self.assertEqual(output["reason"], "CORRECTION_DELIVERY_PROOF_INVALID")

    def test_resume_accepts_new_instructions_without_reviving_old_queued_instructions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root)
            old = {"requestSchemaVersion": "0.19.0", "recordType": "C14_CENTRAL_TO_TASK_REQUEST",
                "messageId": "old-work", "projectId": PROJECT, "taskIdentity": {"taskId": TASK, "canonicalTitle": "C-014｜双向通信"},
                "windowId": WINDOW, "commandId": "old-command", "commandType": "TASK_INSTRUCTION",
                "commandRef": "old-instruction", "commandDigest": "a" * 64, "summary": "Old queued work"}
            self.assertEqual(c14(root, "enqueue-central-command", "--current-thread-ref", CENTRAL_1,
                "--request", str(write(root / "inputs/old-command.json", old)), writer=True)[0], 0)
            complete_roundtrip(root)
            self.assertEqual(command(root, "verify-correction", "--task-id", TASK, "--verification", str(verification(root)))[0], 0)
            new = {**old, "messageId": "new-work", "commandId": "new-command", "commandRef": "current-instruction"}
            code, output = c14(root, "enqueue-central-command", "--current-thread-ref", CENTRAL_1,
                "--request", str(write(root / "inputs/new-command.json", new)), writer=True)
            self.assertEqual(code, 0, output)
            self.assertEqual(c14(root, "prepare-delivery", "--message-id", "new-work", "--current-thread-ref", CENTRAL_1)[0], 0)
            code, output = c14(root, "prepare-delivery", "--message-id", "old-work", "--current-thread-ref", CENTRAL_1)
            self.assertEqual(code, 2, output)
            self.assertEqual(output["reason"], "C14_TASK_DIRECTION_VERSION_MISMATCH")

    def test_evidence_hash_and_communication_are_both_required_to_release_hold(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root)
            request = queue_correction(root)
            report = verification(root)
            code, output = command(root, "verify-correction", "--task-id", TASK, "--verification", str(report))
            self.assertEqual(code, 2, output)
            self.assertEqual(output["reason"], "CORRECTION_COMMUNICATION_INCOMPLETE")
            notification = report_applied(root, request)
            send(root, notification, WINDOW, "delivery-result")
            c14(root, "acknowledge", "--message-id", notification, "--current-thread-ref", CENTRAL_1,
                "--acknowledgement-ref", "central-result-ack")
            write(root / "inputs/readback.json", {"changed": True})
            code, output = command(root, "verify-correction", "--task-id", TASK, "--verification", str(report))
            self.assertEqual(code, 2, output)
            self.assertIn("CORRECTION_EVIDENCE_DIGEST_MISMATCH", output["reason"])
            self.assertTrue(command(root, "read-correction", "--task-id", TASK)[1]["governanceHold"])

    def test_verified_current_correction_releases_only_its_hold_without_rewriting_task_history(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root)
            complete_roundtrip(root)
            report = verification(root)
            code, output = command(root, "verify-correction", "--task-id", TASK, "--verification", str(report))
            self.assertEqual(code, 0, output)
            self.assertFalse(output["governanceHold"])
            self.assertEqual(output["taskStatus"], "IN_PROGRESS")
            self.assertFalse(output["runtimeInterrupted"])
            self.assertFalse(command(root, "verify-correction", "--task-id", TASK,
                "--verification", str(report))[1]["writePerformed"])
            self.assertEqual(command(root, "transition-task", "--task-id", TASK, "--to-status", "PARTIAL", "--reason", "Partial result retained")[0], 0)
            self.assertEqual(command(root, "transition-task", "--task-id", TASK, "--to-status", "IN_PROGRESS", "--reason", "Continue verified direction")[0], 0)
            self.assertEqual(correct(root, revision="1", correction="direction-002")[0], 0)
            self.assertTrue(command(root, "read-correction", "--task-id", TASK)[1]["governanceHold"])

    def test_reported_applied_requires_result_delivery_and_central_ack_before_verification(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root)
            request = queue_correction(root)
            notification = report_applied(root, request)
            current = command(root, "read-correction", "--task-id", TASK)[1]
            self.assertEqual(current["communication"][0]["reportedOutcome"], "APPLIED")
            self.assertFalse(current["readyForVerification"])
            send(root, notification, WINDOW, "delivery-result")
            self.assertEqual(c14(root, "acknowledge", "--message-id", notification,
                "--current-thread-ref", CENTRAL_1, "--acknowledgement-ref", "central-result-ack")[0], 0)
            current = command(root, "read-correction", "--task-id", TASK)[1]
            self.assertTrue(current["readyForVerification"])
            self.assertTrue(current["governanceHold"])

    def test_read_correction_distinguishes_queued_delivered_and_acknowledged(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root)
            request = queue_correction(root)
            code, current = command(root, "read-correction", "--task-id", TASK)
            self.assertEqual(code, 0, current)
            self.assertEqual(current["communication"][0]["commandState"], "QUEUED")
            self.assertFalse(current["readyForVerification"])
            send(root, request["messageId"], CENTRAL_1, "delivery-direction")
            current = command(root, "read-correction", "--task-id", TASK)[1]
            self.assertEqual(current["communication"][0]["commandState"], "DELIVERED")
            self.assertEqual(c14(root, "acknowledge", "--message-id", request["messageId"],
                "--current-thread-ref", WINDOW, "--acknowledgement-ref", "ack-direction")[0], 0)
            current = command(root, "read-correction", "--task-id", TASK)[1]
            self.assertEqual(current["communication"][0]["commandState"], "ACKNOWLEDGED")
            self.assertTrue(current["governanceHold"])
            self.assertFalse(current["readyForVerification"])


if __name__ == "__main__": unittest.main()
