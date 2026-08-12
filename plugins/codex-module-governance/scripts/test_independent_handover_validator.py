#!/usr/bin/env python3
"""Regression tests for C06 using only temporary fictional data."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).parent
C02 = SCRIPTS / "initialize_project.py"
C03 = SCRIPTS / "ledger_manager.py"
C04 = SCRIPTS / "task_package_generator.py"
C05 = SCRIPTS / "occupancy_conflict_checker.py"
C06 = SCRIPTS / "independent_handover_validator.py"
PROJECT = "c06-demo-project"
TASK = "C-06"
PACKAGE = "c06-package-001"
WINDOW = "window-c06-001"
SIGNAL = "completion-signal-001"
VALIDATION = "validation-c06-001"


def invoke(script, arguments):
    completed = subprocess.run([sys.executable, str(script), *arguments], check=False, capture_output=True, text=True)
    return completed.returncode, json.loads(completed.stdout)


def c03(root, command, *arguments):
    return invoke(C03, ["--data-root", str(root), "--project-id", PROJECT, "--writer-id", "codex-module-central", command, *arguments])


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def setup_ready_for_validation(root):
    code, output = invoke(C02, [
        "--data-root", str(root), "--project-id", PROJECT, "--display-name", "C06 Fictional Validation",
        "--scope-summary", "Isolated fictional handback validation only.", "--apply",
    ])
    if code != 0:
        raise AssertionError(output)
    for command in [
        ["initialize", "--apply"],
        ["add-task", "--task-id", TASK, "--title", "Fictional validation task", "--business-goal", "Validate C06 gates.", "--plan-ref", "fable-plan-006"],
    ]:
        code, output = c03(root, command[0], *command[1:])
        if code != 0:
            raise AssertionError(output)
    brief = {
        "briefSchemaVersion": "0.4.0", "recordType": "C04_TASK_PACKAGE_BRIEF", "taskId": TASK,
        "windowRecommendation": {"mode": "NEW", "reason": "Use an isolated fictional window."},
        "dependencies": ["fable-plan-006"],
        "requiredReading": [{"reference": "fictional-governance-006", "purpose": "Read fictional scope."}],
        "realTimeChecks": [{"reference": "fictional-target-006", "purpose": "Read fictional state.", "objectType": "file"}],
        "allowedActions": ["Use fictional evidence only."], "forbiddenActions": ["Do not write business data."],
        "preflightSnapshot": ["Record fictional before snapshot."], "executionSequence": ["Review before acting."],
        "acceptance": {
            "positiveCases": ["Positive evidence passes."], "negativeCases": ["Negative evidence passes."],
            "idempotencyChecks": ["Repeat has no duplicate."], "rollbackChecks": ["Rollback is executable."],
            "logAndHistoryChecks": ["History is readable."], "readbackChecks": ["Upstream and downstream read back."],
        },
        "hardStops": ["Stop on unknown writer."], "rollbackPlan": ["Use fictional snapshot."],
        "deliverables": ["Return fictional handback."], "handbackRule": "Boss authorizes handback before central review.",
    }
    brief_path = write_json(root / "briefs" / f"{TASK}.json", brief)
    code, output = invoke(C04, [
        "--data-root", str(root), "--project-id", PROJECT, "--writer-id", "codex-module-central",
        "generate", "--package-id", PACKAGE, "--task-id", TASK, "--brief", str(brief_path), "--apply",
    ])
    if code != 0:
        raise AssertionError(output)
    occupancy = {
        "reviewSchemaVersion": "0.5.0", "recordType": "C05_OCCUPANCY_REVIEW_REQUEST",
        "reviewId": "c05-review-006", "projectId": PROJECT, "packageId": PACKAGE, "taskId": TASK,
        "bossReview": {"status": "APPROVED", "reference": "boss-package-review-006"},
        "dependencyGate": {"status": "SATISFIED", "references": ["dependency-proof-006"]},
        "crossModuleGate": {"status": "NOT_APPLICABLE", "reference": "fable-handoff-006"},
        "windowReview": {"mode": "AUTO", "candidateWindowId": None},
        "occupancyRequests": [{
            "objectKey": "file:fictional-c06", "conflictKey": "file:fictional-c06",
            "resourceClass": "FILE", "intent": "WRITE", "exclusive": False,
        }],
    }
    occupancy_path = write_json(root / "occupancy-inputs" / "c05-review-006.json", occupancy)
    code, output = invoke(C05, [
        "--data-root", str(root), "--project-id", PROJECT, "--writer-id", "codex-module-central",
        "evaluate", "--package-id", PACKAGE, "--review", str(occupancy_path), "--apply",
    ])
    if code != 0:
        raise AssertionError(output)
    for command in [
        ["register-window", "--window-id", WINDOW, "--task-id", TASK, "--context-mode", "NEW"],
        ["transition-task", "--task-id", TASK, "--to-status", "IN_PROGRESS", "--reason", "Fictional dispatch already occurred outside C06."],
        ["record-completion-signal", "--task-id", TASK, "--signal-id", SIGNAL],
    ]:
        code, output = c03(root, command[0], *command[1:])
        if code != 0:
            raise AssertionError(output)


def evidence_refs():
    return [f"evidence-{category}" for category in (
        "before", "after", "positive", "negative", "idempotency", "rollback", "logs", "upstream", "downstream"
    )]


def create_handback(root, boss_status="APPROVED", unresolved=None, residual=None):
    handback = {
        "handbackSchemaVersion": "0.6.0", "recordType": "C06_TASK_WINDOW_HANDBACK",
        "handbackId": "handback-c06-001", "projectId": PROJECT, "packageId": PACKAGE,
        "c05ReviewId": "c05-review-006", "taskId": TASK, "windowId": WINDOW,
        "completionSignalId": SIGNAL, "submittedBy": {"type": "task-window", "id": WINDOW},
        "bossHandbackAuthorization": {"status": boss_status, "reference": "boss-handback-approval-006"},
        "executedScopeRefs": ["scope-executed-006"], "evidenceRefs": evidence_refs(),
        "testAndObjectRefs": ["test-object-ids-006"], "unresolvedRefs": unresolved or [],
        "residualRiskRefs": residual or [],
    }
    return write_json(root / "handbacks" / "handback-c06-001.json", handback)


def create_review(root, mutation=None):
    refs = dict(zip(
        ("beforeSnapshot", "afterSnapshot", "positiveCase", "negativeCase", "idempotency", "rollback", "logsAndHistory", "upstreamReadback", "downstreamReadback"),
        evidence_refs(),
    ))
    refs["testAndObjectIds"] = "test-object-ids-006"
    review = {
        "reviewSchemaVersion": "0.6.0", "recordType": "C06_INDEPENDENT_VALIDATION_REVIEW",
        "validationId": VALIDATION, "handbackId": "handback-c06-001", "taskId": TASK,
        "centralReviewer": {"id": "codex-module-central", "independentReadbackPerformed": True},
        "scopeAssessment": {
            "packageScopeMatch": True, "parallelMechanismFound": False, "unknownWriterFound": False,
            "permissionExpansionFound": False, "unexplainedErrorFound": False, "duplicateDataFound": False,
            "blockingResidualRiskFound": False, "rollbackExecutable": True,
        },
        "evidenceAssessment": {
            category: {"reference": reference, "verdict": "PASS", "independentlyReadBack": True}
            for category, reference in refs.items()
        },
    }
    if mutation:
        mutation(review)
    return write_json(root / "validation-inputs" / f"{VALIDATION}.json", review)


class IndependentHandoverValidatorTests(unittest.TestCase):
    def assess(self, root, handback, review, mode="--dry-run", writer=None):
        arguments = ["--data-root", str(root), "--project-id", PROJECT]
        if writer:
            arguments.extend(["--writer-id", writer])
        arguments.extend(["assess", "--package-id", PACKAGE, "--handback", str(handback), "--review", str(review), mode])
        return invoke(C06, arguments)

    def test_pass_assessment_stays_needs_review_until_boss_final_approval(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_ready_for_validation(root)
            handback, review = create_handback(root), create_review(root)
            code, preview = self.assess(root, handback, review)
            self.assertEqual(code, 0, preview)
            self.assertEqual(preview["status"], "PASS_PENDING_BOSS_APPROVAL")
            self.assertFalse(preview["doneRecorded"])
            code, output = self.assess(root, handback, review, "--apply", "codex-module-central")
            self.assertEqual(code, 0, output)
            self.assertEqual(output["taskStatusAfter"], "NEEDS_REVIEW")
            self.assertFalse(output["doneRecorded"])

    def test_only_boss_approved_pass_can_finalize_done(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_ready_for_validation(root)
            handback, review = create_handback(root), create_review(root)
            code, output = self.assess(root, handback, review, "--apply", "codex-module-central")
            self.assertEqual(code, 0, output)
            code, final = invoke(C06, [
                "--data-root", str(root), "--project-id", PROJECT, "--writer-id", "codex-module-central",
                "finalize", "--validation-id", VALIDATION, "--boss-decision", "APPROVED",
                "--boss-decision-ref", "boss-final-approval-006",
            ])
            self.assertEqual(code, 0, final)
            self.assertEqual(final["status"], "DONE")
            self.assertTrue(final["doneRecorded"])
            ledger = json.loads((root / "module-ledgers" / PROJECT / "ledger.json").read_text())
            self.assertEqual(ledger["tasks"][TASK]["status"], "DONE")
            self.assertIn("file:fictional-c06", ledger["objectOccupancies"])

    def test_missing_evidence_cannot_finish(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_ready_for_validation(root)
            handback = create_handback(root)
            review = create_review(root, lambda value: value["evidenceAssessment"]["downstreamReadback"].update({"verdict": "MISSING", "independentlyReadBack": False}))
            code, output = self.assess(root, handback, review, "--apply", "codex-module-central")
            self.assertEqual(code, 0, output)
            self.assertEqual(output["status"], "NEEDS_REVIEW")
            code, refused = invoke(C06, [
                "--data-root", str(root), "--project-id", PROJECT, "--writer-id", "codex-module-central",
                "finalize", "--validation-id", VALIDATION, "--boss-decision", "APPROVED", "--boss-decision-ref", "boss-final-approval-006",
            ])
            self.assertEqual(code, 2)
            self.assertEqual(refused["reason"], "C06_ONLY_PASSED_VALIDATION_CAN_BE_FINALIZED")

    def test_unresolved_item_cannot_reach_boss_final_approval_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_ready_for_validation(root)
            handback = create_handback(root, unresolved=["unresolved-item-006"])
            code, output = self.assess(root, handback, create_review(root), "--apply", "codex-module-central")
            self.assertEqual(code, 0, output)
            self.assertEqual(output["status"], "NEEDS_REVIEW")
            self.assertFalse(output["bossFinalApprovalRequired"])

    def test_nonblocking_residual_risk_can_be_disclosed_without_faking_resolution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_ready_for_validation(root)
            handback = create_handback(root, residual=["known-nonblocking-risk-006"])
            code, output = self.assess(root, handback, create_review(root), "--apply", "codex-module-central")
            self.assertEqual(code, 0, output)
            self.assertEqual(output["status"], "PASS_PENDING_BOSS_APPROVAL")

    def test_conflict_creates_hard_stop(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_ready_for_validation(root)
            handback = create_handback(root)
            review = create_review(root, lambda value: value["scopeAssessment"].update({"unknownWriterFound": True}))
            code, output = self.assess(root, handback, review, "--apply", "codex-module-central")
            self.assertEqual(code, 0, output)
            self.assertEqual(output["status"], "CONFLICT")
            ledger = json.loads((root / "module-ledgers" / PROJECT / "ledger.json").read_text())
            self.assertEqual(ledger["tasks"][TASK]["status"], "CONFLICT")
            self.assertEqual(ledger["hardStops"][-1]["code"], "C06_VALIDATION_CONFLICT")

    def test_failed_evidence_is_partial_not_done(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_ready_for_validation(root)
            handback = create_handback(root)
            review = create_review(root, lambda value: value["evidenceAssessment"]["rollback"].update({"verdict": "FAIL"}))
            code, output = self.assess(root, handback, review, "--apply", "codex-module-central")
            self.assertEqual(code, 0, output)
            self.assertEqual(output["status"], "PARTIAL")
            self.assertEqual(output["taskStatusAfter"], "PARTIAL")

    def test_boss_rejection_keeps_task_needs_review(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_ready_for_validation(root)
            code, output = self.assess(root, create_handback(root), create_review(root), "--apply", "codex-module-central")
            self.assertEqual(code, 0, output)
            code, final = invoke(C06, [
                "--data-root", str(root), "--project-id", PROJECT, "--writer-id", "codex-module-central",
                "finalize", "--validation-id", VALIDATION, "--boss-decision", "REJECTED",
                "--boss-decision-ref", "boss-final-rejection-006",
            ])
            self.assertEqual(code, 0, final)
            self.assertEqual(final["status"], "NEEDS_REVIEW")
            self.assertFalse(final["doneRecorded"])

    def test_validation_must_link_to_real_c03_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_ready_for_validation(root)
            code, output = self.assess(root, create_handback(root), create_review(root), "--apply", "codex-module-central")
            self.assertEqual(code, 0, output)
            decision_path = root / "handover-validations" / PROJECT / VALIDATION / "validation-decision.json"
            receipt_path = root / "handover-validations" / PROJECT / VALIDATION / "receipts" / "receipt-000000-validate.json"
            decision = json.loads(decision_path.read_text())
            decision["ledgerMutation"]["receiptId"] = "receipt-999999-missing"
            decision_path.write_text(json.dumps(decision), encoding="utf-8")
            receipt = json.loads(receipt_path.read_text())
            receipt["afterDecision"] = decision
            from hashlib import sha256
            serialized = json.dumps(decision, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            receipt["afterDecisionDigest"] = sha256(serialized.encode("utf-8")).hexdigest()
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            code, output = invoke(C06, [
                "--data-root", str(root), "--project-id", PROJECT, "verify", "--validation-id", VALIDATION,
            ])
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "C06_LINKED_LEDGER_RECEIPT_MISSING_OR_INVALID")

    def test_boss_must_authorize_handback_before_central_assessment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_ready_for_validation(root)
            handback, review = create_handback(root, "REQUIRED"), create_review(root)
            code, output = self.assess(root, handback, review)
            self.assertEqual(code, 0, output)
            self.assertEqual(output["status"], "WAITING_FOR_BOSS_HANDBACK_AUTHORIZATION")
            code, output = self.assess(root, handback, review, "--apply", "codex-module-central")
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "C06_BOSS_HANDBACK_AUTHORIZATION_REQUIRED")

    def test_non_central_writer_cannot_apply(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_ready_for_validation(root)
            code, output = self.assess(root, create_handback(root), create_review(root), "--apply", "task-window")
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "C06_WRITER_NOT_AUTHORIZED")

    def test_handback_cannot_mix_package_and_c05_decision(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_ready_for_validation(root)
            c05_path = root / "occupancy-reviews" / PROJECT / "c05-review-006" / "occupancy-decision.json"
            c05_receipt_path = root / "occupancy-reviews" / PROJECT / "c05-review-006" / "receipts" / "receipt-000000-review.json"
            decision = json.loads(c05_path.read_text())
            decision["packageId"] = "different-package"
            c05_path.write_text(json.dumps(decision), encoding="utf-8")
            receipt = json.loads(c05_receipt_path.read_text())
            receipt["afterDecision"] = decision
            from hashlib import sha256
            serialized = json.dumps(decision, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            receipt["afterDecisionDigest"] = sha256(serialized.encode("utf-8")).hexdigest()
            c05_receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            code, output = self.assess(root, create_handback(root), create_review(root))
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "C06_TASK_SOURCE_MISMATCH")

    def test_repeat_assessment_and_finalization_are_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_ready_for_validation(root)
            handback, review = create_handback(root), create_review(root)
            code, first = self.assess(root, handback, review, "--apply", "codex-module-central")
            self.assertEqual(code, 0, first)
            code, second = self.assess(root, handback, review, "--apply", "codex-module-central")
            self.assertEqual(code, 0, second)
            self.assertEqual(second["status"], "IDEMPOTENT_EXISTING_C06_DECISION")
            args = [
                "--data-root", str(root), "--project-id", PROJECT, "--writer-id", "codex-module-central",
                "finalize", "--validation-id", VALIDATION, "--boss-decision", "APPROVED", "--boss-decision-ref", "boss-final-approval-006",
            ]
            code, _ = invoke(C06, args)
            self.assertEqual(code, 0)
            revision = json.loads((root / "module-ledgers" / PROJECT / "ledger.json").read_text())["revision"]
            code, repeated = invoke(C06, args)
            self.assertEqual(code, 0, repeated)
            self.assertEqual(repeated["status"], "IDEMPOTENT_EXISTING_C06_FINALIZATION")
            self.assertEqual(json.loads((root / "module-ledgers" / PROJECT / "ledger.json").read_text())["revision"], revision)

    def test_decision_tampering_is_detected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_ready_for_validation(root)
            code, output = self.assess(root, create_handback(root), create_review(root), "--apply", "codex-module-central")
            self.assertEqual(code, 0, output)
            receipt_path = root / "handover-validations" / PROJECT / VALIDATION / "receipts" / "receipt-000000-validate.json"
            receipt = json.loads(receipt_path.read_text())
            receipt["afterDecision"]["outcome"] = "TAMPERED"
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            code, output = invoke(C06, [
                "--data-root", str(root), "--project-id", PROJECT, "verify", "--validation-id", VALIDATION,
            ])
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "C06_VALIDATION_RECEIPT_CHAIN_INVALID")

    def test_finalization_must_bind_the_same_validation_decision(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_ready_for_validation(root)
            code, output = self.assess(root, create_handback(root), create_review(root), "--apply", "codex-module-central")
            self.assertEqual(code, 0, output)
            code, output = invoke(C06, [
                "--data-root", str(root), "--project-id", PROJECT, "--writer-id", "codex-module-central",
                "finalize", "--validation-id", VALIDATION, "--boss-decision", "APPROVED",
                "--boss-decision-ref", "boss-final-approval-006",
            ])
            self.assertEqual(code, 0, output)
            final_path = root / "handover-validations" / PROJECT / VALIDATION / "boss-finalization.json"
            receipt_path = root / "handover-validations" / PROJECT / VALIDATION / "receipts" / "receipt-000001-finalize.json"
            finalization = json.loads(final_path.read_text())
            finalization["validationDecisionDigest"] = "0" * 64
            final_path.write_text(json.dumps(finalization), encoding="utf-8")
            receipt = json.loads(receipt_path.read_text())
            receipt["afterFinalization"] = finalization
            from hashlib import sha256
            serialized = json.dumps(finalization, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            receipt["afterFinalizationDigest"] = sha256(serialized.encode("utf-8")).hexdigest()
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            code, output = invoke(C06, [
                "--data-root", str(root), "--project-id", PROJECT, "verify", "--validation-id", VALIDATION,
            ])
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "C06_FINALIZATION_RECEIPT_CHAIN_INVALID")


if __name__ == "__main__":
    unittest.main()
