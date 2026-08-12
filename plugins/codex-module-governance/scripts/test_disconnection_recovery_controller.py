#!/usr/bin/env python3
"""Regression tests for C08 using temporary fictional governance data only."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).parent
C02 = SCRIPTS / "initialize_project.py"
C03 = SCRIPTS / "ledger_manager.py"
C08 = SCRIPTS / "disconnection_recovery_controller.py"
import test_independent_handover_validator as c06_fixture
from disconnection_recovery_controller import affected_context
PROJECT = "c08-demo-project"
TASK = "C-08"
WINDOW = "window-c08-001"
AGENT = "agent-c08-001"
CASE = "recovery-c08-001"


def invoke(script, arguments):
    completed = subprocess.run([sys.executable, str(script), *arguments], check=False, capture_output=True, text=True)
    return completed.returncode, json.loads(completed.stdout)


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def c03(root, command, *arguments):
    return invoke(C03, ["--data-root", str(root), "--project-id", PROJECT, "--writer-id", "codex-module-central", command, *arguments])


def setup_project(root, with_agent=True):
    code, output = invoke(C02, ["--data-root", str(root), "--project-id", PROJECT, "--display-name", "C08 Fictional Recovery", "--scope-summary", "Isolated recovery tests only.", "--apply"])
    if code != 0:
        raise AssertionError(output)
    commands = [
        ["initialize", "--apply"],
        ["add-task", "--task-id", TASK, "--title", "Fictional recovery task", "--business-goal", "Test C08 safely.", "--plan-ref", "fable-plan-c08"],
        ["register-window", "--window-id", WINDOW, "--task-id", TASK, "--context-mode", "NEW"],
        ["transition-task", "--task-id", TASK, "--to-status", "READY", "--reason", "Fictional task ready."],
        ["transition-task", "--task-id", TASK, "--to-status", "IN_PROGRESS", "--reason", "Fictional task started."],
        ["claim-object", "--object-key", "file:fictional-c08", "--owner-type", "task", "--owner-id", TASK, "--intent", "WRITE"],
    ]
    if with_agent:
        commands.insert(3, ["register-sub-agent", "--sub-agent-id", AGENT, "--window-id", WINDOW, "--role", "Fictional readback helper"])
    for command in commands:
        code, output = c03(root, command[0], *command[1:])
        if code != 0:
            raise AssertionError(output)


def incident(kind="TASK_WINDOW", target=WINDOW, task=TASK, mutation=None):
    payload = {
        "incidentSchemaVersion": "0.8.0", "recordType": "C08_DISCONNECTION_INCIDENT",
        "caseId": CASE, "projectId": PROJECT, "incidentType": kind, "targetId": target,
        "taskId": task, "detectedBy": "codex-module-central", "detectionRefs": ["heartbeat-missed-c08"],
        "reasonRef": "disconnect-reason-c08",
    }
    if mutation:
        mutation(payload)
    return payload


def takeover_input(epoch=1, approved=True):
    return {"takeoverSchemaVersion": "0.8.0", "recordType": "C08_CENTRAL_TAKEOVER_INPUT", "caseId": CASE, "expectedRecoveryEpoch": epoch, "successorInstanceRef": "central-instance-c08-next", "bossAuthorization": {"status": "APPROVED" if approved else "PENDING", "reference": "boss-takeover-c08"}}


def decision_input(action="PREPARE_RESUME"):
    return {"decisionSchemaVersion": "0.8.0", "recordType": "C08_RECOVERY_DECISION_INPUT", "caseId": CASE, "action": action, "bossDecisionRef": "boss-recovery-decision-c08", "reasonRef": "recovery-reason-c08"}


def release_input(rollback=True, outstanding=False, validation=None):
    return {"releaseSchemaVersion": "0.8.0", "recordType": "C08_OCCUPANCY_RELEASE_INPUT", "caseId": CASE, "bossReleaseAuthorization": {"status": "APPROVED", "reference": "boss-release-c08"}, "validationId": validation, "rollbackConfirmed": rollback, "noBusinessWritesOutstanding": not outstanding}


class C08Tests(unittest.TestCase):
    def test_reserved_second_assignment_is_visible_to_controlled_recovery(self):
        ledger = {
            "tasks": {"C-OLD": {"status": "DONE"}, TASK: {"status": "READY"}},
            "windows": {
                WINDOW: {
                    "windowId": WINDOW,
                    "taskId": "C-OLD",
                    "currentTaskId": None,
                    "reservedForTaskId": TASK,
                    "status": "RESERVED_FOR_REUSE",
                }
            },
            "subAgents": {},
        }
        context = affected_context(ledger, incident())
        self.assertEqual(context, {"windowIds": [WINDOW], "subAgentIds": []})

    def freeze(self, root, path, action="--apply", writer="codex-module-central"):
        args = ["--data-root", str(root), "--project-id", PROJECT]
        if writer:
            args += ["--writer-id", writer]
        args += ["freeze", "--incident", str(path), action]
        return invoke(C08, args)

    def takeover(self, root, path, writer="codex-module-central"):
        return invoke(C08, ["--data-root", str(root), "--project-id", PROJECT, "--writer-id", writer, "takeover", "--case-id", CASE, "--authorization", str(path)])

    def decide(self, root, path):
        return invoke(C08, ["--data-root", str(root), "--project-id", PROJECT, "--writer-id", "codex-module-central", "decide", "--case-id", CASE, "--decision", str(path)])

    def release(self, root, path):
        return invoke(C08, ["--data-root", str(root), "--project-id", PROJECT, "--writer-id", "codex-module-central", "release", "--case-id", CASE, "--release", str(path)])

    def freeze_case(self, root, payload=None):
        path = write_json(root / "c08-inputs" / "incident.json", payload or incident())
        return self.freeze(root, path)

    def takeover_case(self, root, payload=None):
        path = write_json(root / "c08-inputs" / "takeover.json", payload or takeover_input())
        return self.takeover(root, path)

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"; setup_project(root)
            path = write_json(root / "c08-inputs" / "incident.json", incident())
            code, output = self.freeze(root, path, "--dry-run", None)
            self.assertEqual(code, 0, output); self.assertFalse(output["writePerformed"])
            self.assertFalse((root / "recovery-cases").exists())

    def test_window_disconnect_freezes_task_and_preserves_occupancy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"; setup_project(root)
            before = json.loads((root / "module-ledgers" / PROJECT / "ledger.json").read_text())
            code, output = self.freeze_case(root)
            self.assertEqual(code, 0, output); self.assertTrue(output["occupancyPreserved"])
            after = json.loads((root / "module-ledgers" / PROJECT / "ledger.json").read_text())
            self.assertEqual(after["tasks"][TASK]["status"], "BLOCKED")
            self.assertEqual(after["windows"][WINDOW]["status"], "DISCONNECTED")
            self.assertEqual(after["objectOccupancies"], before["objectOccupancies"])

    def test_sub_agent_disconnect_does_not_freeze_unrelated_context(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"; setup_project(root)
            code, output = self.freeze_case(root, incident("SUB_AGENT", AGENT, TASK))
            self.assertEqual(code, 0, output)
            ledger = json.loads((root / "module-ledgers" / PROJECT / "ledger.json").read_text())
            self.assertEqual(ledger["subAgents"][AGENT]["status"], "DISCONNECTED")
            self.assertEqual(ledger["tasks"][TASK]["status"], "BLOCKED")

    def test_central_disconnect_freezes_all_active_windows_without_changing_tasks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"; setup_project(root)
            code, output = self.freeze_case(root, incident("CENTRAL", "codex-module-central", None))
            self.assertEqual(code, 0, output)
            ledger = json.loads((root / "module-ledgers" / PROJECT / "ledger.json").read_text())
            self.assertEqual(ledger["windows"][WINDOW]["status"], "FROZEN")
            self.assertEqual(ledger["tasks"][TASK]["status"], "IN_PROGRESS")

    def test_noncentral_writer_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"; setup_project(root)
            path = write_json(root / "c08-inputs" / "incident.json", incident())
            code, output = self.freeze(root, path, writer=WINDOW)
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "C08_WRITER_NOT_AUTHORIZED")

    def test_duplicate_freeze_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"; setup_project(root)
            path = write_json(root / "c08-inputs" / "incident.json", incident())
            self.freeze(root, path); code, output = self.freeze(root, path)
            self.assertEqual(code, 0, output); self.assertEqual(output["status"], "IDEMPOTENT_FREEZE")

    def test_freeze_mechanically_blocks_nonrecovery_ledger_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"; setup_project(root); self.freeze_case(root)
            code, output = c03(root, "record-evidence", "--evidence-id", "evidence-after-freeze", "--reference", "ref-after-freeze", "--category", "fictional", "--summary", "Must be blocked during recovery.")
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "LEDGER_RECOVERY_FREEZE_ACTIVE")

    def test_tampered_ledger_blocks_recovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"; setup_project(root)
            ledger_path = root / "module-ledgers" / PROJECT / "ledger.json"
            ledger = json.loads(ledger_path.read_text()); ledger["tasks"][TASK]["title"] = "Tampered"
            write_json(ledger_path, ledger)
            path = write_json(root / "c08-inputs" / "incident.json", incident())
            code, output = self.freeze(root, path)
            self.assertEqual(code, 2); self.assertIn("LEDGER_AND_RECEIPT_CHAIN_MISMATCH", output["reason"])

    def test_takeover_requires_boss_authorization_and_matching_epoch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"; setup_project(root); self.freeze_case(root)
            path = write_json(root / "c08-inputs" / "takeover.json", takeover_input(approved=False))
            code, output = self.takeover(root, path)
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "C08_BOSS_TAKEOVER_AUTHORIZATION_REQUIRED")
            path = write_json(root / "c08-inputs" / "takeover-wrong.json", takeover_input(epoch=2))
            code, output = self.takeover(root, path)
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "C08_TAKEOVER_STATE_OR_EPOCH_MISMATCH")

    def test_takeover_recovers_control_but_not_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"; setup_project(root); self.freeze_case(root)
            code, output = self.takeover_case(root)
            self.assertEqual(code, 0, output); self.assertFalse(output["businessExecutionResumed"])
            ledger = json.loads((root / "module-ledgers" / PROJECT / "ledger.json").read_text())
            self.assertEqual(ledger["recovery"]["state"], "CENTRAL_CONTROL_RECOVERED")
            self.assertEqual(ledger["tasks"][TASK]["status"], "BLOCKED")
            self.assertIn("file:fictional-c08", ledger["objectOccupancies"])

    def test_prepare_resume_still_does_not_resume_task(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"; setup_project(root); self.freeze_case(root); self.takeover_case(root)
            path = write_json(root / "c08-inputs" / "decision.json", decision_input("PREPARE_RESUME"))
            code, output = self.decide(root, path)
            self.assertEqual(code, 0, output); self.assertFalse(output["businessExecutionResumed"])
            ledger = json.loads((root / "module-ledgers" / PROJECT / "ledger.json").read_text())
            self.assertEqual(ledger["recovery"]["state"], "RESUME_REVIEW_REQUIRED")
            self.assertEqual(ledger["tasks"][TASK]["status"], "BLOCKED")

    def test_cancel_retains_occupancy_until_separate_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"; setup_project(root); self.freeze_case(root); self.takeover_case(root)
            path = write_json(root / "c08-inputs" / "decision.json", decision_input("CANCEL_TASK"))
            code, output = self.decide(root, path)
            self.assertEqual(code, 0, output); self.assertTrue(output["occupancyPreserved"])
            ledger = json.loads((root / "module-ledgers" / PROJECT / "ledger.json").read_text())
            self.assertEqual(ledger["tasks"][TASK]["status"], "CANCELLED")
            self.assertIn("file:fictional-c08", ledger["objectOccupancies"])

    def test_cancelled_release_requires_rollback_and_zero_outstanding_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"; setup_project(root); self.freeze_case(root); self.takeover_case(root)
            self.decide(root, write_json(root / "c08-inputs" / "decision.json", decision_input("CANCEL_TASK")))
            path = write_json(root / "c08-inputs" / "release.json", release_input(rollback=False))
            code, output = self.release(root, path)
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "C08_CANCELLED_RELEASE_REQUIRES_ROLLBACK_AND_ZERO_OUTSTANDING_WRITES")

    def test_cancelled_release_is_separate_boss_authorized_action(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"; setup_project(root); self.freeze_case(root); self.takeover_case(root)
            self.decide(root, write_json(root / "c08-inputs" / "decision.json", decision_input("CANCEL_TASK")))
            path = write_json(root / "c08-inputs" / "release.json", release_input())
            code, output = self.release(root, path)
            self.assertEqual(code, 0, output); self.assertEqual(output["status"], "OCCUPANCY_RELEASED_AFTER_CANCEL")
            ledger = json.loads((root / "module-ledgers" / PROJECT / "ledger.json").read_text())
            self.assertNotIn("file:fictional-c08", ledger["objectOccupancies"])

    def test_active_task_cannot_release_occupancy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"; setup_project(root); self.freeze_case(root); self.takeover_case(root)
            path = write_json(root / "c08-inputs" / "release.json", release_input())
            code, output = self.release(root, path)
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "C08_RELEASE_REQUIRES_DONE_OR_CANCELLED_TASK")

    def test_done_release_requires_and_accepts_verified_c06_finalization(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            c06_fixture.setup_ready_for_validation(root)
            handback = c06_fixture.create_handback(root)
            review = c06_fixture.create_review(root)
            code, output = invoke(c06_fixture.C06, [
                "--data-root", str(root), "--project-id", c06_fixture.PROJECT,
                "--writer-id", "codex-module-central", "assess", "--package-id", c06_fixture.PACKAGE,
                "--handback", str(handback), "--review", str(review), "--apply",
            ])
            self.assertEqual(code, 0, output)
            code, output = invoke(c06_fixture.C06, [
                "--data-root", str(root), "--project-id", c06_fixture.PROJECT,
                "--writer-id", "codex-module-central", "finalize", "--validation-id", c06_fixture.VALIDATION,
                "--boss-decision", "APPROVED", "--boss-decision-ref", "boss-final-approval-c08",
            ])
            self.assertEqual(code, 0, output)
            done_incident = incident()
            done_incident.update({
                "projectId": c06_fixture.PROJECT, "taskId": c06_fixture.TASK,
                "targetId": c06_fixture.WINDOW,
            })
            incident_path = write_json(root / "c08-inputs" / "done-incident.json", done_incident)
            code, output = invoke(C08, [
                "--data-root", str(root), "--project-id", c06_fixture.PROJECT,
                "--writer-id", "codex-module-central", "freeze", "--incident", str(incident_path), "--apply",
            ])
            self.assertEqual(code, 0, output)
            takeover_path = write_json(root / "c08-inputs" / "done-takeover.json", takeover_input())
            code, output = invoke(C08, [
                "--data-root", str(root), "--project-id", c06_fixture.PROJECT,
                "--writer-id", "codex-module-central", "takeover", "--case-id", CASE,
                "--authorization", str(takeover_path),
            ])
            self.assertEqual(code, 0, output)
            missing_validation = write_json(root / "c08-inputs" / "release-missing-validation.json", release_input())
            code, output = invoke(C08, [
                "--data-root", str(root), "--project-id", c06_fixture.PROJECT,
                "--writer-id", "codex-module-central", "release", "--case-id", CASE,
                "--release", str(missing_validation),
            ])
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "C08_DONE_RELEASE_REQUIRES_C06_VALIDATION")
            valid_release = write_json(root / "c08-inputs" / "release-valid.json", release_input(validation=c06_fixture.VALIDATION))
            code, output = invoke(C08, [
                "--data-root", str(root), "--project-id", c06_fixture.PROJECT,
                "--writer-id", "codex-module-central", "release", "--case-id", CASE,
                "--release", str(valid_release),
            ])
            self.assertEqual(code, 0, output); self.assertEqual(output["status"], "OCCUPANCY_RELEASED_AFTER_DONE")
            ledger = json.loads((root / "module-ledgers" / c06_fixture.PROJECT / "ledger.json").read_text())
            self.assertNotIn("file:fictional-c06", ledger["objectOccupancies"])

    def test_tampered_freeze_receipt_blocks_takeover(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"; setup_project(root); self.freeze_case(root)
            path = root / "recovery-cases" / PROJECT / CASE / "freeze.json"
            payload = json.loads(path.read_text()); payload["reasonRef"] = "tampered-reason-c08"; write_json(path, payload)
            takeover_path = write_json(root / "c08-inputs" / "takeover.json", takeover_input())
            code, output = self.takeover(root, takeover_path)
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "C08_FREEZE_INTEGRITY_INVALID")

    def test_full_cancel_chain_verifies_from_receipts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"; setup_project(root); self.freeze_case(root); self.takeover_case(root)
            self.decide(root, write_json(root / "c08-inputs" / "decision.json", decision_input("CANCEL_TASK")))
            self.release(root, write_json(root / "c08-inputs" / "release.json", release_input()))
            code, output = invoke(C08, ["--data-root", str(root), "--project-id", PROJECT, "verify", "--case-id", CASE])
            self.assertEqual(code, 0, output); self.assertEqual(output["stage"], "CLOSED")
            self.assertEqual(output["recoveryState"], "CLOSED"); self.assertFalse(output["businessExecutionResumed"])


if __name__ == "__main__":
    unittest.main()
