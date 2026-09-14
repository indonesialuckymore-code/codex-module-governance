"""Task-local waits, using only fictional temporary projects."""
import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from test_task_corrections import command, setup, TASK, PROJECT


def wait(root, wait_id="wait-material", owner="owner-material"):
    return command(root, "record-wait", "--task-id", TASK, "--wait-id", wait_id,
        "--owner-ref", owner, "--condition-ref", "condition-material-verified", "--reason-ref", "reason-missing-material")


def resolution(root, wait_id="wait-material"):
    evidence = root / "material-readback.txt"
    evidence.write_text("Fictional material and in-flight effects have been checked.")
    value = {"waitId": wait_id, "taskId": TASK, "conditionRef": "condition-material-verified",
        "directionContext": {"revision": 0, "digest": "NONE"},
        "conditionSatisfied": True, "executionSafeToResume": True,
        "evidence": {"reference": "evidence-material-readback", "file": str(evidence),
                     "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}}
    path = root / "resolution.json"; path.write_text(json.dumps(value))
    return path


class ExternalWaitTests(unittest.TestCase):
    def test_resolution_retry_does_not_offer_resumption_after_cancellation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root); wait(root)
            args = ("resolve-wait", "--task-id", TASK, "--resolution", str(resolution(root)))
            self.assertTrue(command(root, *args)[1]["resumptionEligible"])
            self.assertEqual(0, command(root, "transition-task", "--task-id", TASK,
                "--to-status", "CANCEL_REQUESTED", "--reason", "Fictional approved cancellation")[0])
            code, result = command(root, *args)
            self.assertEqual(0, code, result)
            self.assertFalse(result["writePerformed"])
            self.assertFalse(result["resumptionEligible"])

    def test_wait_blocks_prepared_dispatch_retry_and_runtime_confirmation(self):
        import test_task_window_dispatch_controller as dispatch
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; dispatch.setup_ready(root)
            request = dispatch.dispatch_request()
            self.assertEqual(0, dispatch.prepare(root, request)[0])
            code, result = command(root, "record-wait", "--task-id", dispatch.fixture.TASK_ID,
                "--wait-id", "wait-dispatch", "--owner-ref", "owner-material", "--condition-ref", "condition-ready",
                "--reason-ref", "reason-material", project=dispatch.fixture.PROJECT_ID)
            self.assertEqual(0, code, result)
            code, result = dispatch.prepare(root, request)
            self.assertEqual(2, code, result)
            self.assertIn("TASK_EXTERNAL_WAIT_PENDING", result["reason"])
            code, result = dispatch.confirm(root, dispatch.confirmation())
            self.assertEqual(2, code, result)
            self.assertIn("TASK_EXTERNAL_WAIT_PENDING", result["reason"])

    def test_wait_preserves_late_handback_but_blocks_final_acceptance(self):
        import test_independent_handover_validator as validation
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; validation.setup_ready_for_validation(root)
            code, result = validation.IndependentHandoverValidatorTests().assess(root,
                validation.create_handback(root), validation.create_review(root), "--apply", "codex-module-central")
            self.assertEqual(0, code, result)
            code, result = validation.c03(root, "record-wait", "--task-id", validation.TASK,
                "--wait-id", "wait-acceptance", "--owner-ref", "owner-material", "--condition-ref", "condition-ready",
                "--reason-ref", "reason-material")
            self.assertEqual(0, code, result)
            code, result = validation.invoke(validation.C06, ["--data-root", str(root), "--project-id", validation.PROJECT,
                "--writer-id", "codex-module-central", "finalize", "--validation-id", validation.VALIDATION,
                "--boss-decision", "APPROVED", "--boss-decision-ref", "boss-final-wait"])
            self.assertEqual(2, code, result)
            self.assertIn("TASK_EXTERNAL_WAIT_PENDING", result["reason"])

    def test_wait_resolution_does_not_clear_a_direction_correction(self):
        from test_task_corrections import correct
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root); wait(root)
            proof = resolution(root)
            code, correction = correct(root)
            self.assertEqual(0, code, correction)
            self.assertEqual(2, command(root, "resolve-wait", "--task-id", TASK, "--resolution", str(proof))[0])
            value = json.loads(proof.read_text())
            value["directionContext"] = {"revision": 1, "digest": correction["current"]["requestDigest"]}
            proof.write_text(json.dumps(value))
            code, result = command(root, "resolve-wait", "--task-id", TASK, "--resolution", str(proof))
            self.assertEqual(0, code, result)
            self.assertFalse(result["resumptionEligible"])
            code, blocked = command(root, "claim-object", "--object-key", "file:old-direction",
                "--owner-type", "task", "--owner-id", TASK, "--intent", "WRITE")
            self.assertEqual("TASK_DIRECTION_CORRECTION_PENDING", blocked["reason"])

    def test_queued_execution_message_stays_blocked_while_waiting(self):
        from test_task_communication_bridge import c14, write, CENTRAL_1, WINDOW
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root)
            request = {"requestSchemaVersion": "0.19.0", "recordType": "C14_CENTRAL_TO_TASK_REQUEST",
                "messageId": "message-before-wait", "projectId": PROJECT,
                "taskIdentity": {"taskId": TASK, "canonicalTitle": "C-014｜双向通信"}, "windowId": WINDOW,
                "commandId": "command-before-wait", "commandType": "TASK_INSTRUCTION",
                "commandRef": "command-work", "commandDigest": "a" * 64, "summary": "Do approved work."}
            self.assertEqual(0, c14(root, "enqueue-central-command", "--current-thread-ref", CENTRAL_1,
                "--request", str(write(root / "command.json", request)), writer=True)[0])
            wait(root)
            code, result = c14(root, "prepare-delivery", "--current-thread-ref", CENTRAL_1,
                "--message-id", "message-before-wait")
            self.assertEqual(2, code, result)
            self.assertEqual("C14_TASK_EXTERNAL_WAIT_PENDING", result["reason"])

    def test_bad_evidence_and_old_central_cannot_resolve_wait(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root); wait(root)
            proof = resolution(root)
            (root / "material-readback.txt").write_text("Changed after verification.")
            code, result = command(root, "resolve-wait", "--task-id", TASK, "--resolution", str(proof))
            self.assertEqual(2, code, result)
            self.assertEqual("EXTERNAL_WAIT_EVIDENCE_MISMATCH", result["reason"])
            proof = resolution(root)
            self.assertEqual(2, command(root, "resolve-wait", "--task-id", TASK,
                "--resolution", str(proof), caller="not-current-central")[0])
            self.assertEqual(2, wait(root, owner="changed-owner")[0])
            self.assertIn(TASK, command(root, "read-summary")[1]["externalWaits"])

    def test_resolution_releases_only_one_wait_and_retries_do_not_reopen_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root)
            self.assertEqual(0, wait(root)[0])
            self.assertEqual(0, wait(root, "wait-supplier")[0])
            proof = resolution(root)
            args = ("resolve-wait", "--task-id", TASK, "--resolution", str(proof))
            code, result = command(root, *args)
            self.assertEqual(0, code, result)
            self.assertFalse(result["resumptionEligible"])
            self.assertFalse(command(root, *args)[1]["writePerformed"])
            self.assertFalse(wait(root)[1]["writePerformed"])
            summary = command(root, "read-summary")[1]
            self.assertEqual(["wait-supplier"], [item["waitId"] for item in summary["externalWaits"][TASK]])
            proof = resolution(root, "wait-supplier")
            code, result = command(root, "resolve-wait", "--task-id", TASK, "--resolution", str(proof))
            self.assertEqual(0, code, result)
            self.assertTrue(result["resumptionEligible"])
            self.assertEqual(0, command(root, "claim-object", "--object-key", "file:resumed",
                "--owner-type", "task", "--owner-id", TASK, "--intent", "WRITE")[0])

    def test_wait_preserves_task_and_only_blocks_affected_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root)
            code, result = wait(root)
            self.assertEqual(0, code, result)
            code, summary = command(root, "read-summary")
            self.assertEqual(0, code, summary)
            self.assertEqual("IN_PROGRESS", summary["tasks"][TASK])
            self.assertEqual([], summary["hardStops"])
            self.assertEqual("NOT_SCHEDULED", summary["externalWaits"][TASK][0]["followupStatus"])
            self.assertFalse(command(root, "read-correction", "--task-id", TASK)[1]["resumptionEligible"])
            code, blocked = command(root, "claim-object", "--object-key", "file:extra-work",
                "--owner-type", "task", "--owner-id", TASK, "--intent", "WRITE")
            self.assertEqual(2, code, blocked)
            self.assertEqual("TASK_EXTERNAL_WAIT_PENDING", blocked["reason"])
            code, other = command(root, "add-task", "--task-id", "C-other", "--title", "Other outcome",
                "--business-goal", "Independent work", "--plan-ref", "approved-other")
            self.assertEqual(0, code, other)
            self.assertEqual(0, command(root, "transition-task", "--task-id", "C-other",
                "--to-status", "READY", "--reason", "Independent approved work")[0])


if __name__ == "__main__":
    unittest.main()
