#!/usr/bin/env python3
"""Regression tests for C04. Every test uses a temporary, non-Git data directory."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIRECTORY = Path(__file__).parent
C02_SCRIPT = SCRIPT_DIRECTORY / "initialize_project.py"
C03_SCRIPT = SCRIPT_DIRECTORY / "ledger_manager.py"
C04_SCRIPT = SCRIPT_DIRECTORY / "task_package_generator.py"
PROJECT_ID = "c04-demo-project"
TASK_ID = "C-04"
PACKAGE_ID = "c04-package-001"


def invoke(script, arguments):
    completed = subprocess.run([sys.executable, str(script), *arguments], check=False, capture_output=True, text=True)
    return completed.returncode, json.loads(completed.stdout)


def create_project_and_ledger(data_root):
    code, output = invoke(C02_SCRIPT, [
        "--data-root", str(data_root), "--project-id", PROJECT_ID,
        "--display-name", "C04 Isolated Validation", "--scope-summary", "Fictional validation only.", "--apply",
    ])
    if code != 0:
        raise AssertionError(output)
    code, output = invoke(C03_SCRIPT, [
        "--data-root", str(data_root), "--project-id", PROJECT_ID,
        "--writer-id", "codex-module-central", "initialize", "--apply",
    ])
    if code != 0:
        raise AssertionError(output)


def add_planned_task(data_root, task_id=TASK_ID):
    code, output = invoke(C03_SCRIPT, [
        "--data-root", str(data_root), "--project-id", PROJECT_ID,
        "--writer-id", "codex-module-central", "add-task", "--task-id", task_id,
        "--title", "Fictional package task", "--business-goal", "Validate draft-only task package controls.",
        "--plan-ref", "fable-plan-001",
    ])
    if code != 0:
        raise AssertionError(output)


def create_brief(data_root, task_id=TASK_ID, mutation=None):
    brief = {
        "briefSchemaVersion": "0.4.0",
        "recordType": "C04_TASK_PACKAGE_BRIEF",
        "taskId": task_id,
        "windowRecommendation": {"mode": "NEW", "reason": "Fictional scope has no compatible active window."},
        "dependencies": ["fable-plan-001"],
        "requiredReading": [{"reference": "governance-brief-001", "purpose": "Confirm the fictional scope."}],
        "realTimeChecks": [{"reference": "fictional-target-001", "purpose": "Read current fictional state.", "objectType": "file"}],
        "allowedActions": ["Read fictional configuration only."],
        "forbiddenActions": ["Do not create TEST data.", "Do not write business data."],
        "preflightSnapshot": ["Capture a fictional preflight snapshot reference."],
        "executionSequence": ["Review scope before any permitted action."],
        "acceptance": {
            "positiveCases": ["Fictional positive condition is recorded."],
            "negativeCases": ["Fictional negative condition is recorded."],
            "idempotencyChecks": ["Repeat the fictional action without duplicate output."],
            "rollbackChecks": ["Verify the stated fictional rollback point."],
            "logAndHistoryChecks": ["Read the fictional log reference."],
            "readbackChecks": ["Read back the fictional target reference."],
        },
        "hardStops": ["Stop if a non-fictional object is requested."],
        "rollbackPlan": ["Do not change a target without a recorded rollback reference."],
        "deliverables": ["Return the fictional validation summary to Boss."],
        "handbackRule": "Return only to Boss; do not dispatch or mark the task DONE.",
    }
    if mutation:
        mutation(brief)
    path = data_root / "briefs" / f"{task_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


class TaskPackageGeneratorTests(unittest.TestCase):
    def command(self, data_root, command, *arguments, writer=None):
        base = ["--data-root", str(data_root), "--project-id", PROJECT_ID]
        if writer is not None:
            base.extend(["--writer-id", writer])
        return invoke(C04_SCRIPT, [*base, command, *arguments])

    def ready(self, data_root):
        create_project_and_ledger(data_root)
        add_planned_task(data_root)
        return create_brief(data_root)

    def test_dry_run_reads_planned_task_but_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "private-data"
            brief = self.ready(data_root)
            code, output = self.command(data_root, "generate", "--package-id", PACKAGE_ID, "--task-id", TASK_ID, "--brief", str(brief), "--dry-run")
            self.assertEqual(code, 0, output)
            self.assertEqual(output["status"], "READY_FOR_BOSS_APPROVAL")
            self.assertFalse(output["writePerformed"])
            self.assertFalse((data_root / "task-packages").exists())

    def test_apply_creates_draft_only_and_leaves_ledger_task_planned(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "private-data"
            brief = self.ready(data_root)
            code, output = self.command(data_root, "generate", "--package-id", PACKAGE_ID, "--task-id", TASK_ID, "--brief", str(brief), "--apply", writer="codex-module-central")
            self.assertEqual(code, 0, output)
            self.assertEqual(output["status"], "DRAFT_REQUIRES_BOSS_REVIEW")
            self.assertFalse(output["dispatchAllowed"])
            self.assertFalse(output["taskWindowCreated"])
            ledger = json.loads((data_root / "module-ledgers" / PROJECT_ID / "ledger.json").read_text(encoding="utf-8"))
            package = json.loads((data_root / "task-packages" / PROJECT_ID / "drafts" / PACKAGE_ID / "task-package.json").read_text(encoding="utf-8"))
            self.assertEqual(ledger["tasks"][TASK_ID]["status"], "PLANNED")
            self.assertEqual(package["approvalAndDispatchBoundary"]["c05OccupancyCheckStatus"], "REQUIRED_BEFORE_DISPATCH")

    def test_only_module_central_can_generate_a_draft(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "private-data"
            brief = self.ready(data_root)
            code, output = self.command(data_root, "generate", "--package-id", PACKAGE_ID, "--task-id", TASK_ID, "--brief", str(brief), "--apply", writer="fable-5-system-router")
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "TASK_PACKAGE_WRITER_NOT_AUTHORIZED")

    def test_generation_requires_a_planned_task(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "private-data"
            brief = self.ready(data_root)
            code, output = invoke(C03_SCRIPT, [
                "--data-root", str(data_root), "--project-id", PROJECT_ID,
                "--writer-id", "codex-module-central", "transition-task", "--task-id", TASK_ID,
                "--to-status", "READY", "--reason", "Fictional task was made ready.",
            ])
            self.assertEqual(code, 0, output)
            code, output = self.command(data_root, "generate", "--package-id", PACKAGE_ID, "--task-id", TASK_ID, "--brief", str(brief), "--dry-run")
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "TASK_PACKAGE_REQUIRES_PLANNED_TASK")

    def test_hard_stop_in_source_ledger_refuses_generation(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "private-data"
            brief = self.ready(data_root)
            add_planned_task(data_root, "C-05")
            code, output = invoke(C03_SCRIPT, [
                "--data-root", str(data_root), "--project-id", PROJECT_ID,
                "--writer-id", "codex-module-central", "claim-object", "--object-key", "fictional:shared-object",
                "--owner-type", "task", "--owner-id", TASK_ID, "--intent", "WRITE",
            ])
            self.assertEqual(code, 0, output)
            code, output = invoke(C03_SCRIPT, [
                "--data-root", str(data_root), "--project-id", PROJECT_ID,
                "--writer-id", "codex-module-central", "claim-object", "--object-key", "fictional:shared-object",
                "--owner-type", "task", "--owner-id", "C-05", "--intent", "WRITE",
            ])
            self.assertEqual(code, 0, output)
            code, output = self.command(data_root, "generate", "--package-id", PACKAGE_ID, "--task-id", TASK_ID, "--brief", str(brief), "--dry-run")
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "SOURCE_LEDGER_HAS_UNRESOLVED_HARD_STOP")

    def test_private_brief_rejects_sensitive_or_location_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "private-data"
            create_project_and_ledger(data_root)
            add_planned_task(data_root)
            brief = create_brief(data_root, mutation=lambda value: value["allowedActions"].append("Read /Users/example/private-file."))
            code, output = self.command(data_root, "generate", "--package-id", PACKAGE_ID, "--task-id", TASK_ID, "--brief", str(brief), "--dry-run")
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "TASK_PACKAGE_BRIEF_CONTAINS_SENSITIVE_OR_LOCATION_DATA")

    def test_package_receipt_tampering_is_detected(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "private-data"
            brief = self.ready(data_root)
            code, output = self.command(data_root, "generate", "--package-id", PACKAGE_ID, "--task-id", TASK_ID, "--brief", str(brief), "--apply", writer="codex-module-central")
            self.assertEqual(code, 0, output)
            receipt_path = data_root / "task-packages" / PROJECT_ID / "drafts" / PACKAGE_ID / "receipts" / "receipt-000000-generate.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["afterPackage"]["status"] = "TAMPERED"
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            code, output = self.command(data_root, "verify", "--package-id", PACKAGE_ID)
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "IMMUTABLE_TASK_PACKAGE_RECEIPT_CHAIN_INVALID")


if __name__ == "__main__":
    unittest.main()
