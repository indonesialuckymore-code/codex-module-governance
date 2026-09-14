"""Task-local direction holds using public commands and fictional private data."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_task_communication_bridge import C03, PROJECT, TASK, CENTRAL_1, setup, write, c14
import ledger_manager
import occupancy_conflict_checker


def command(root, *args, caller=CENTRAL_1, project=PROJECT):
    result = subprocess.run([sys.executable, str(C03), "--data-root", str(root),
        "--project-id", project, "--writer-id", "codex-module-central",
        "--caller-thread-ref", caller, *args], capture_output=True, text=True)
    try: value = json.loads(result.stdout)
    except ValueError: value = {"stderr": result.stderr}
    return result.returncode, value


def correct(root, revision="0", correction="direction-001", caller=CENTRAL_1):
    return command(root, "record-correction", "--task-id", TASK,
        "--correction-id", correction, "--expected-revision", revision,
        "--instruction-ref", "instruction-current", "--reason-ref", "reason-business-focus",
        "--boss-decision-ref", "boss-direction-approved", "--retain-ref", "approved-task-scope",
        "--supersede-ref", "old-execution-emphasis", caller=caller)


class TaskCorrectionTests(unittest.TestCase):
    def test_hold_disallows_new_write_claim_but_allows_readback_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root)
            self.assertEqual(correct(root)[0], 0)
            code, output = command(root, "claim-object", "--object-key", "file:more-work",
                "--owner-type", "task", "--owner-id", TASK, "--intent", "WRITE")
            self.assertEqual(code, 2, output)
            self.assertEqual(output["reason"], "TASK_DIRECTION_CORRECTION_PENDING")
            self.assertEqual(command(root, "claim-object", "--object-key", "file:readback",
                "--owner-type", "task", "--owner-id", TASK, "--intent", "READ")[0], 0)

    def test_current_correction_can_be_queued_but_superseded_message_cannot_be_sent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root)
            code, recorded = correct(root)
            self.assertEqual(code, 0, recorded)
            request = recorded["messageRequests"][0]
            code, queued = c14(root, "enqueue-central-command", "--current-thread-ref", CENTRAL_1,
                "--request", str(write(root / "inputs/correction-message.json", request)), writer=True)
            self.assertEqual(code, 0, queued)
            self.assertEqual(c14(root, "prepare-delivery", "--current-thread-ref", CENTRAL_1,
                "--message-id", request["messageId"])[0], 0)
            self.assertEqual(correct(root, revision="1", correction="direction-002")[0], 0)
            code, stale = c14(root, "prepare-delivery", "--current-thread-ref", CENTRAL_1,
                "--message-id", request["messageId"])
            self.assertEqual(code, 2, stale)
            self.assertEqual(stale["reason"], "C14_DIRECTION_CORRECTION_SUPERSEDED_OR_MISMATCHED")

    def test_retries_are_idempotent_and_stale_revision_cannot_replace_new_direction(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root)
            self.assertEqual(correct(root)[0], 0)
            code, duplicate = correct(root)
            self.assertEqual(code, 0, duplicate)
            self.assertFalse(duplicate["writePerformed"])
            code, stale = correct(root, correction="direction-002")
            self.assertEqual(code, 2, stale)
            self.assertEqual(stale["reason"], "CORRECTION_STALE_REVISION")
            code, revised = correct(root, revision="1", correction="direction-002")
            self.assertEqual(code, 0, revised)
            self.assertEqual(revised["correctionRevision"], 2)
            self.assertEqual(correct(root)[1]["current"]["correctionId"], "direction-002")

    def test_old_central_cannot_record_or_replay_a_correction(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root)
            self.assertEqual(correct(root)[0], 0)
            code, output = correct(root, caller="central-not-current")
            self.assertEqual(code, 2, output)
            self.assertIn("CURRENT_CENTRAL", output["reason"])

    def test_reusing_correction_id_with_changed_request_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root)
            self.assertEqual(correct(root)[0], 0)
            code, output = correct(root, revision="1")
            self.assertEqual(code, 2, output)
            self.assertEqual(output["reason"], "CORRECTION_ID_REUSED")

    def test_occupancy_preflight_refuses_held_task_before_reserving(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root)
            self.assertEqual(correct(root)[0], 0)
            ledger = ledger_manager.load_ledger(root, PROJECT)
            with self.assertRaisesRegex(ledger_manager.LedgerError, "TASK_DIRECTION_CORRECTION_PENDING"):
                occupancy_conflict_checker.analyse({}, ledger, {"taskId": TASK})

    def test_held_task_cannot_expand_execution_by_registering_an_agent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root)
            self.assertEqual(correct(root)[0], 0)
            code, output = command(root, "register-sub-agent", "--sub-agent-id", "agent-new-work",
                "--window-id", "window-c014-g1", "--role", "Additional execution")
            self.assertEqual(code, 2, output)
            self.assertEqual(output["reason"], "TASK_DIRECTION_CORRECTION_PENDING")

    def test_pending_correction_blocks_restart_but_preserves_late_completion_signal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root)
            self.assertEqual(correct(root)[0], 0)
            self.assertEqual(command(root, "record-completion-signal", "--task-id", TASK,
                "--signal-id", "late-old-result")[0], 0)
            self.assertEqual(command(root, "transition-task", "--task-id", TASK,
                "--to-status", "PARTIAL", "--reason", "Preserve useful partial work")[0], 0)
            code, output = command(root, "transition-task", "--task-id", TASK,
                "--to-status", "IN_PROGRESS", "--reason", "Try old direction")
            self.assertEqual(code, 2, output)
            self.assertEqual(output["reason"], "TASK_DIRECTION_CORRECTION_PENDING")

    def test_correction_is_versioned_without_restarting_task_or_stopping_unrelated_work(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root)
            self.assertEqual(command(root, "add-task", "--task-id", "C-015", "--title", "Other outcome",
                "--business-goal", "An unrelated outcome", "--plan-ref", "plan-other")[0], 0)
            code, output = correct(root)
            self.assertEqual(code, 0, output)
            self.assertEqual(output["correctionRevision"], 1)
            self.assertEqual(output["synchronizationState"], "PAUSE_REQUESTED")
            self.assertFalse(output["runtimeInterrupted"])
            self.assertEqual(command(root, "transition-task", "--task-id", "C-015", "--to-status", "READY", "--reason", "Unrelated work may proceed")[0], 0)
            code, current = command(root, "read-correction", "--task-id", TASK)
            self.assertEqual(code, 0, current)
            self.assertEqual(current["taskStatus"], "IN_PROGRESS")
            self.assertEqual(current["current"]["instructionRef"], "instruction-current")
            self.assertEqual(current["current"]["retainedRequirementRefs"], ["approved-task-scope"])
            self.assertEqual(command(root, "verify")[0], 0)


if __name__ == "__main__": unittest.main()
