#!/usr/bin/env python3
"""Regression tests for C05. Every test uses a temporary, non-Git data directory."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from occupancy_conflict_checker import resolve_window


SCRIPT_DIRECTORY = Path(__file__).parent
C02_SCRIPT = SCRIPT_DIRECTORY / "initialize_project.py"
C03_SCRIPT = SCRIPT_DIRECTORY / "ledger_manager.py"
C04_SCRIPT = SCRIPT_DIRECTORY / "task_package_generator.py"
C05_SCRIPT = SCRIPT_DIRECTORY / "occupancy_conflict_checker.py"
PROJECT_ID = "c05-demo-project"
TASK_ID = "C-05"
PACKAGE_ID = "c05-package-001"
REVIEW_ID = "c05-review-001"


def invoke(script, arguments):
    completed = subprocess.run([sys.executable, str(script), *arguments], check=False, capture_output=True, text=True)
    return completed.returncode, json.loads(completed.stdout)


def c03(data_root, command, *arguments):
    return invoke(C03_SCRIPT, [
        "--data-root", str(data_root), "--project-id", PROJECT_ID,
        "--writer-id", "codex-module-central", command, *arguments,
    ])


def setup_project(data_root):
    code, output = invoke(C02_SCRIPT, [
        "--data-root", str(data_root), "--project-id", PROJECT_ID,
        "--display-name", "C05 Isolated Validation", "--scope-summary", "Fictional occupancy validation only.", "--apply",
    ])
    if code != 0:
        raise AssertionError(output)
    code, output = c03(data_root, "initialize", "--apply")
    if code != 0:
        raise AssertionError(output)
    add_task(data_root, TASK_ID)
    brief = create_brief(data_root)
    code, output = invoke(C04_SCRIPT, [
        "--data-root", str(data_root), "--project-id", PROJECT_ID,
        "--writer-id", "codex-module-central", "generate", "--package-id", PACKAGE_ID,
        "--task-id", TASK_ID, "--brief", str(brief), "--apply",
    ])
    if code != 0:
        raise AssertionError(output)


def add_task(data_root, task_id):
    code, output = c03(
        data_root, "add-task", "--task-id", task_id, "--title", f"Fictional {task_id}",
        "--business-goal", "Validate isolated governance occupancy.", "--plan-ref", "fable-plan-001",
    )
    if code != 0:
        raise AssertionError(output)


def create_brief(data_root):
    brief = {
        "briefSchemaVersion": "0.4.0",
        "recordType": "C04_TASK_PACKAGE_BRIEF",
        "taskId": TASK_ID,
        "windowRecommendation": {"mode": "NEW", "reason": "No compatible fictional window is active."},
        "dependencies": ["fable-plan-001"],
        "requiredReading": [{"reference": "governance-brief-001", "purpose": "Confirm fictional scope."}],
        "realTimeChecks": [{"reference": "fictional-target-001", "purpose": "Read fictional state.", "objectType": "file"}],
        "allowedActions": ["Read fictional state only."],
        "forbiddenActions": ["Do not create TEST data."],
        "preflightSnapshot": ["Capture a fictional snapshot reference."],
        "executionSequence": ["Review scope before permitted action."],
        "acceptance": {
            "positiveCases": ["Record fictional positive case."],
            "negativeCases": ["Record fictional negative case."],
            "idempotencyChecks": ["Repeat without duplicate output."],
            "rollbackChecks": ["Verify fictional rollback point."],
            "logAndHistoryChecks": ["Read fictional history reference."],
            "readbackChecks": ["Read fictional target reference."],
        },
        "hardStops": ["Stop if a real business object is requested."],
        "rollbackPlan": ["Use only fictional rollback references."],
        "deliverables": ["Return fictional validation summary."],
        "handbackRule": "Return to Boss without dispatching.",
    }
    path = data_root / "briefs" / f"{TASK_ID}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def create_review(data_root, *, boss="APPROVED", dependencies="SATISFIED", cross_module="NOT_APPLICABLE", requests=None, window_mode="AUTO", window_id=None, context_compatibility="NOT_ASSESSED", compatibility_refs=None):
    review = {
        "reviewSchemaVersion": "0.5.0",
        "recordType": "C05_OCCUPANCY_REVIEW_REQUEST",
        "reviewId": REVIEW_ID,
        "projectId": PROJECT_ID,
        "packageId": PACKAGE_ID,
        "taskId": TASK_ID,
        "bossReview": {"status": boss, "reference": "boss-review-001"},
        "dependencyGate": {"status": dependencies, "references": ["dependency-proof-001"] if dependencies == "SATISFIED" else []},
        "crossModuleGate": {"status": cross_module, "reference": "fable-handoff-001"},
        "windowReview": {
            "mode": window_mode,
            "candidateWindowId": window_id,
            "contextCompatibility": context_compatibility,
            "compatibilityEvidenceRefs": compatibility_refs or [],
        },
        "occupancyRequests": requests or [{
            "objectKey": "file:fictional-a", "conflictKey": "file:fictional-a",
            "resourceClass": "FILE", "intent": "WRITE", "exclusive": False,
        }],
    }
    path = data_root / "occupancy-inputs" / f"{REVIEW_ID}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


class OccupancyConflictCheckerTests(unittest.TestCase):
    def command(self, data_root, review, mode="--dry-run", writer=None):
        arguments = ["--data-root", str(data_root), "--project-id", PROJECT_ID]
        if writer is not None:
            arguments.extend(["--writer-id", writer])
        arguments.extend(["evaluate", "--package-id", PACKAGE_ID, "--review", str(review), mode])
        return invoke(C05_SCRIPT, arguments)

    def test_dry_run_waits_for_boss_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "private-data"
            setup_project(data_root)
            review = create_review(data_root, boss="REQUIRED")
            before = json.loads((data_root / "module-ledgers" / PROJECT_ID / "ledger.json").read_text())
            code, output = self.command(data_root, review)
            after = json.loads((data_root / "module-ledgers" / PROJECT_ID / "ledger.json").read_text())
            self.assertEqual(code, 0, output)
            self.assertEqual(output["status"], "WAITING_FOR_BOSS_APPROVAL")
            self.assertEqual(before, after)
            self.assertFalse((data_root / "occupancy-reviews").exists())

    def test_safe_apply_reserves_all_objects_atomically_and_sets_ready(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "private-data"
            setup_project(data_root)
            requests = [
                {"objectKey": "file:fictional-a", "conflictKey": "file:fictional-a", "resourceClass": "FILE", "intent": "WRITE", "exclusive": False},
                {"objectKey": "service:fictional-b", "conflictKey": "service:fictional-b", "resourceClass": "SERVICE", "intent": "READ", "exclusive": False},
            ]
            review = create_review(data_root, requests=requests)
            code, preview = self.command(data_root, review)
            self.assertEqual(code, 0, preview)
            self.assertEqual(preview["status"], "ELIGIBLE_FOR_DISPATCH_APPROVAL")
            self.assertFalse(preview["occupancyReserved"])
            code, output = self.command(data_root, review, "--apply", "codex-module-central")
            self.assertEqual(code, 0, output)
            self.assertEqual(output["taskStatusAfter"], "READY")
            self.assertTrue(output["occupancyReserved"])
            self.assertFalse(output["dispatchExecuted"])
            ledger = json.loads((data_root / "module-ledgers" / PROJECT_ID / "ledger.json").read_text())
            self.assertEqual(set(ledger["objectOccupancies"]), {"file:fictional-a", "service:fictional-b"})
            self.assertEqual(ledger["windows"], {})
            self.assertEqual(ledger["subAgents"], {})

    def test_conflict_blocks_later_task_without_partial_claims(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "private-data"
            setup_project(data_root)
            add_task(data_root, "C-OTHER")
            code, output = c03(data_root, "claim-object", "--object-key", "file:shared", "--owner-type", "task", "--owner-id", "C-OTHER", "--intent", "WRITE")
            self.assertEqual(code, 0, output)
            requests = [
                {"objectKey": "file:free", "conflictKey": "file:free", "resourceClass": "FILE", "intent": "WRITE", "exclusive": False},
                {"objectKey": "file:shared", "conflictKey": "file:shared", "resourceClass": "FILE", "intent": "READ", "exclusive": False},
            ]
            review = create_review(data_root, requests=requests)
            code, output = self.command(data_root, review, "--apply", "codex-module-central")
            self.assertEqual(code, 0, output)
            self.assertEqual(output["status"], "CONFLICT_HARD_STOP")
            self.assertEqual(output["taskStatusAfter"], "BLOCKED")
            ledger = json.loads((data_root / "module-ledgers" / PROJECT_ID / "ledger.json").read_text())
            self.assertNotIn("file:free", ledger["objectOccupancies"])
            self.assertEqual(len(ledger["objectOccupancies"]["file:shared"]["claims"]), 1)

    def test_shared_read_is_parallel_safe(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "private-data"
            setup_project(data_root)
            add_task(data_root, "C-OTHER")
            code, output = c03(data_root, "claim-object", "--object-key", "service:shared", "--owner-type", "task", "--owner-id", "C-OTHER", "--intent", "READ")
            self.assertEqual(code, 0, output)
            review = create_review(data_root, requests=[{
                "objectKey": "service:shared", "conflictKey": "service:shared", "resourceClass": "SERVICE", "intent": "READ", "exclusive": False,
            }])
            code, output = self.command(data_root, review)
            self.assertEqual(code, 0, output)
            self.assertEqual(output["status"], "ELIGIBLE_FOR_DISPATCH_APPROVAL")

    def test_cross_module_conflict_uses_handoff_reference_and_hard_stops(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "private-data"
            setup_project(data_root)
            review = create_review(data_root, cross_module="CONFLICT")
            code, output = self.command(data_root, review, "--apply", "codex-module-central")
            self.assertEqual(code, 0, output)
            self.assertEqual(output["status"], "CONFLICT_HARD_STOP")
            self.assertEqual(output["taskStatusAfter"], "BLOCKED")
            ledger = json.loads((data_root / "module-ledgers" / PROJECT_ID / "ledger.json").read_text())
            self.assertEqual(ledger["hardStops"][-1]["code"], "C05_OCCUPANCY_CONFLICT")

    def test_conflict_key_catches_parent_child_resource_overlap(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "private-data"
            setup_project(data_root)
            add_task(data_root, "C-OTHER")
            code, output = c03(data_root, "claim-object", "--object-key", "table:orders", "--owner-type", "task", "--owner-id", "C-OTHER", "--intent", "WRITE")
            self.assertEqual(code, 0, output)
            review = create_review(data_root, requests=[{
                "objectKey": "field:orders-total", "conflictKey": "table:orders", "resourceClass": "FIELD", "intent": "WRITE", "exclusive": False,
            }])
            code, output = self.command(data_root, review)
            self.assertEqual(code, 0, output)
            self.assertEqual(output["status"], "CONFLICT_HARD_STOP")
            self.assertEqual(output["conflictCount"], 1)

    def test_existing_manual_claim_by_candidate_task_is_not_duplicated(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "private-data"
            setup_project(data_root)
            code, output = c03(data_root, "claim-object", "--object-key", "file:fictional-a", "--owner-type", "task", "--owner-id", TASK_ID, "--intent", "WRITE")
            self.assertEqual(code, 0, output)
            review = create_review(data_root)
            code, output = self.command(data_root, review)
            self.assertEqual(code, 0, output)
            self.assertEqual(output["status"], "CONFLICT_HARD_STOP")
            self.assertEqual(output["conflictCount"], 1)

    def test_repeat_apply_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "private-data"
            setup_project(data_root)
            review = create_review(data_root)
            code, first = self.command(data_root, review, "--apply", "codex-module-central")
            self.assertEqual(code, 0, first)
            ledger_path = data_root / "module-ledgers" / PROJECT_ID / "ledger.json"
            revision = json.loads(ledger_path.read_text())["revision"]
            code, second = self.command(data_root, review, "--apply", "codex-module-central")
            self.assertEqual(code, 0, second)
            self.assertEqual(second["status"], "IDEMPOTENT_EXISTING_C05_DECISION")
            self.assertEqual(json.loads(ledger_path.read_text())["revision"], revision)

    def test_only_module_central_can_apply(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "private-data"
            setup_project(data_root)
            review = create_review(data_root)
            code, output = self.command(data_root, review, "--apply", "task-window")
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "C05_WRITER_NOT_AUTHORIZED")

    def test_existing_task_window_is_reused_without_creating_another(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "private-data"
            setup_project(data_root)
            code, output = c03(data_root, "register-window", "--window-id", "window-existing", "--task-id", TASK_ID, "--context-mode", "NEW")
            self.assertEqual(code, 0, output)
            review = create_review(data_root)
            code, output = self.command(data_root, review)
            self.assertEqual(code, 0, output)
            self.assertEqual(output["windowDecision"]["status"], "REUSE_EXISTING_WINDOW")
            self.assertEqual(output["windowDecision"]["windowId"], "window-existing")

    def test_available_window_gets_only_second_assignment_and_retired_window_is_refused(self):
        ledger = {
            "tasks": {
                "C-OLD": {"taskId": "C-OLD", "status": "DONE"},
                TASK_ID: {"taskId": TASK_ID, "status": "PLANNED"},
            },
            "windows": {
                "window-available": {
                    "windowId": "window-available", "taskId": "C-OLD", "currentTaskId": None,
                    "model": "gpt-5.6-terra", "status": "AVAILABLE_FOR_REUSE",
                    "assignmentCount": 1, "maxAssignments": 2,
                    "assignmentHistory": [{"assignmentNumber": 1, "taskId": "C-OLD"}],
                },
                "window-retired": {
                    "windowId": "window-retired", "taskId": "C-OLD", "currentTaskId": None,
                    "model": "gpt-5.6-terra", "status": "RETIRED",
                    "assignmentCount": 2, "maxAssignments": 2,
                    "assignmentHistory": [
                        {"assignmentNumber": 1, "taskId": "C-A"},
                        {"assignmentNumber": 2, "taskId": "C-OLD"},
                    ],
                },
            },
        }
        review = {"reviewId": REVIEW_ID, "taskId": TASK_ID, "windowReview": {"mode": "REUSE", "candidateWindowId": "window-available", "contextCompatibility": "COMPATIBLE"}}
        decision = resolve_window({}, ledger, review)
        self.assertEqual(decision["status"], "REUSE_EXISTING_WINDOW")
        self.assertEqual(decision["assignmentNumber"], 2)
        self.assertEqual(decision["reuseType"], "SECOND_AND_FINAL_ASSIGNMENT")
        review["windowReview"]["candidateWindowId"] = "window-retired"
        refused = resolve_window({}, ledger, review)
        self.assertEqual(refused["status"], "WAITING_WINDOW_REUSE_CAP_REACHED")
        self.assertEqual(refused["assignmentCount"], 2)

    def test_auto_does_not_blindly_reuse_without_compatibility_evidence(self):
        ledger = {
            "tasks": {"C-OLD": {"taskId": "C-OLD", "status": "DONE"}, TASK_ID: {"taskId": TASK_ID, "status": "PLANNED"}},
            "windows": {"window-available": {"windowId": "window-available", "taskId": "C-OLD", "currentTaskId": None, "model": "gpt-5.6-terra", "status": "AVAILABLE_FOR_REUSE", "assignmentCount": 1, "assignmentHistory": [{"assignmentNumber": 1, "taskId": "C-OLD"}]}},
        }
        incompatible = {"reviewId": REVIEW_ID, "taskId": TASK_ID, "windowReview": {"mode": "AUTO", "candidateWindowId": None, "contextCompatibility": "INCOMPATIBLE"}}
        decision = resolve_window({}, ledger, incompatible)
        self.assertEqual(decision["status"], "OPEN_NEW_WINDOW")
        unknown = {"reviewId": REVIEW_ID, "taskId": TASK_ID, "windowReview": {"mode": "AUTO", "candidateWindowId": None, "contextCompatibility": "UNKNOWN"}}
        self.assertEqual(resolve_window({}, ledger, unknown)["status"], "WAITING_FOR_WINDOW_CONTEXT_COMPATIBILITY")

    def test_reserved_second_slot_cannot_be_selected_by_another_task(self):
        ledger = {
            "tasks": {
                "C-OLD": {"taskId": "C-OLD", "status": "DONE"},
                "C-FIRST-SUCCESSOR": {"taskId": "C-FIRST-SUCCESSOR", "status": "READY"},
                TASK_ID: {"taskId": TASK_ID, "status": "PLANNED"},
            },
            "windows": {
                "window-reserved": {
                    "windowId": "window-reserved", "taskId": "C-OLD", "currentTaskId": None,
                    "model": "gpt-5.6-terra", "status": "RESERVED_FOR_REUSE",
                    "assignmentCount": 1, "maxAssignments": 2,
                    "assignmentHistory": [{"assignmentNumber": 1, "taskId": "C-OLD"}],
                    "reservedForTaskId": "C-FIRST-SUCCESSOR",
                    "reuseReservationId": "window-slot:c05-review-first-successor",
                }
            },
        }
        automatic = {"reviewId": REVIEW_ID, "taskId": TASK_ID, "windowReview": {"mode": "AUTO", "candidateWindowId": None, "contextCompatibility": "COMPATIBLE"}}
        self.assertEqual(resolve_window({}, ledger, automatic)["status"], "OPEN_NEW_WINDOW")
        forced = {"reviewId": REVIEW_ID, "taskId": TASK_ID, "windowReview": {"mode": "REUSE", "candidateWindowId": "window-reserved", "contextCompatibility": "COMPATIBLE"}}
        self.assertEqual(resolve_window({}, ledger, forced)["status"], "WAITING_FOR_REUSABLE_WINDOW")

    def test_decision_receipt_tampering_is_detected(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "private-data"
            setup_project(data_root)
            review = create_review(data_root)
            code, output = self.command(data_root, review, "--apply", "codex-module-central")
            self.assertEqual(code, 0, output)
            receipt_path = data_root / "occupancy-reviews" / PROJECT_ID / REVIEW_ID / "receipts" / "receipt-000000-review.json"
            receipt = json.loads(receipt_path.read_text())
            receipt["afterDecision"]["status"] = "TAMPERED"
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            code, output = invoke(C05_SCRIPT, [
                "--data-root", str(data_root), "--project-id", PROJECT_ID,
                "verify", "--review-id", REVIEW_ID,
            ])
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "C05_IMMUTABLE_RECEIPT_CHAIN_INVALID")

    def test_decision_must_link_to_the_real_c03_mutation_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "private-data"
            setup_project(data_root)
            review = create_review(data_root)
            code, output = self.command(data_root, review, "--apply", "codex-module-central")
            self.assertEqual(code, 0, output)
            decision_path = data_root / "occupancy-reviews" / PROJECT_ID / REVIEW_ID / "occupancy-decision.json"
            receipt_path = data_root / "occupancy-reviews" / PROJECT_ID / REVIEW_ID / "receipts" / "receipt-000000-review.json"
            decision = json.loads(decision_path.read_text())
            decision["ledgerMutation"]["receiptId"] = "receipt-999999-missing"
            decision_path.write_text(json.dumps(decision), encoding="utf-8")
            receipt = json.loads(receipt_path.read_text())
            receipt["afterDecision"] = decision
            from hashlib import sha256
            serialized = json.dumps(decision, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            receipt["afterDecisionDigest"] = sha256(serialized.encode("utf-8")).hexdigest()
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            code, output = invoke(C05_SCRIPT, [
                "--data-root", str(data_root), "--project-id", PROJECT_ID,
                "verify", "--review-id", REVIEW_ID,
            ])
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "C05_LINKED_LEDGER_RECEIPT_MISSING_OR_INVALID")


if __name__ == "__main__":
    unittest.main()
