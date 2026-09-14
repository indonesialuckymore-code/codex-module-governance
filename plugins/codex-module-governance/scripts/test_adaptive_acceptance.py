"""Outcome-appropriate acceptance through real private-file workflows."""
import json
import tempfile
import unittest
from pathlib import Path

import test_task_package_generator as packages
import test_independent_handover_validator as validation


def research_policy():
    return {
        "effects": {name: False for name in (
            "changesState", "executableBehavior", "repeatableOperation",
            "upstreamDependency", "downstreamConsumer", "highRisk")},
        "notApplicable": {name: "Read-only synthesis has no such operation in scope."
            for name in ("beforeSnapshot", "negativeCase", "idempotency", "rollback",
                         "upstreamReadback", "downstreamReadback")},
    }


def adapt_brief(brief):
    brief["acceptancePolicy"] = research_policy()
    for name in ("negativeCases", "idempotencyChecks", "rollbackChecks"):
        brief["acceptance"][name] = []


def adapt_review(review):
    for name in research_policy()["notApplicable"]:
        review["evidenceAssessment"][name]["verdict"] = "NOT_APPLICABLE"
    review["scopeAssessment"]["rollbackExecutable"] = False


class AdaptiveAcceptanceTests(unittest.TestCase):
    def test_package_cannot_require_and_waive_the_same_check(self):
        import task_package_generator as generator
        with tempfile.TemporaryDirectory() as temp:
            path = packages.create_brief(Path(temp), mutation=adapt_brief)
            brief = json.loads(path.read_text())
            brief["acceptance"]["negativeCases"] = ["Check the explicit excluded scenario."]
            with self.assertRaisesRegex(generator.TaskPackageError, "ACCEPTANCE_CONTRADICTORY_REQUIREMENT"):
                generator.validate_brief(brief, packages.TASK_ID)

    def test_restored_different_package_cannot_borrow_the_old_occupancy_approval(self):
        import task_package_generator as generator
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            validation.setup_ready_for_validation(root)
            # Simulate mixing individually intact backup artifacts from different versions.
            path = generator.task_package_path(root, validation.PROJECT, validation.PACKAGE)
            package = json.loads(path.read_text())
            adapt_brief(package)
            validation.write_json(path, package)
            receipt_path = generator.receipt_directory(root, validation.PROJECT, validation.PACKAGE) / f"{generator.INITIAL_RECEIPT_ID}.json"
            receipt = json.loads(receipt_path.read_text())
            receipt["afterPackage"] = package
            receipt["afterPackageDigest"] = generator.canonical_digest(package)
            validation.write_json(receipt_path, receipt)
            code, result = validation.IndependentHandoverValidatorTests().assess(root,
                validation.create_handback(root), validation.create_review(root, adapt_review))
            self.assertEqual(2, code, result)
            self.assertIn("ACCEPTANCE_PACKAGE_APPROVAL_MISMATCH", result["reason"])

    def test_high_risk_contract_cannot_use_legacy_missing_snapshot_exception(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            def high_risk(brief):
                brief["acceptancePolicy"] = {"effects": {
                    key: True for key in research_policy()["effects"]}, "notApplicable": {}}
            validation.setup_ready_for_validation(root, high_risk)
            def missing_before(review):
                review["evidenceAssessment"]["beforeSnapshot"]["verdict"] = "MISSING"
            code, result = validation.IndependentHandoverValidatorTests().assess(
                root, validation.create_handback(root), validation.create_review(root, missing_before),
                "--apply", "codex-module-central")
            self.assertEqual(0, code, result)
            self.assertEqual("NEEDS_REVIEW", result["status"])
            code, result = validation.invoke(validation.C06, [
                "--data-root", str(root), "--project-id", validation.PROJECT,
                "--writer-id", "codex-module-central", "finalize", "--validation-id", validation.VALIDATION,
                "--boss-decision", "APPROVED", "--boss-decision-ref", "boss-high-risk-final"])
            self.assertEqual(2, code, result)
            self.assertFalse(result["doneRecorded"])

    def test_dispatched_contract_preserves_acceptance_policy(self):
        import test_task_window_dispatch_controller as dispatch
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            dispatch.setup_ready(root, requests=[{"objectKey": "file:research", "conflictKey": "file:research",
                "resourceClass": "FILE", "intent": "READ", "exclusive": False}], brief_mutation=adapt_brief)
            code, result = dispatch.prepare(root, dispatch.dispatch_request())
            self.assertEqual(0, code, result)
            plan = json.loads(dispatch.dispatch_module.plan_path(
                root, dispatch.fixture.PROJECT_ID, "dispatch-c10-001").read_text())
            self.assertEqual(research_policy(), plan["taskPackage"].get("acceptancePolicy"))

    def test_legacy_contract_does_not_gain_exemptions_at_review_time(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            validation.setup_ready_for_validation(root)
            code, result = validation.IndependentHandoverValidatorTests().assess(
                root, validation.create_handback(root), validation.create_review(root, adapt_review))
            self.assertEqual(2, code, result)
            self.assertEqual("C06_NOT_APPLICABLE_NOT_IN_APPROVED_CONTRACT", result["reason"])

    def test_exemptions_do_not_hide_missing_or_unread_core_evidence(self):
        for mutation, expected in (("MISSING", "NEEDS_REVIEW"), ("FAIL", "PARTIAL"),
                                   ("UNREAD", "NEEDS_REVIEW"), ("NA_UNREAD", "NEEDS_REVIEW"), ("WAIVE", "REFUSED")):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / "private"
                validation.setup_ready_for_validation(root, adapt_brief, "READ")
                def mutate(review):
                    adapt_review(review)
                    item = review["evidenceAssessment"]["positiveCase"]
                    if mutation == "NA_UNREAD":
                        review["evidenceAssessment"]["rollback"]["independentlyReadBack"] = False
                    elif mutation == "UNREAD":
                        item["independentlyReadBack"] = False
                    else:
                        item["verdict"] = "NOT_APPLICABLE" if mutation == "WAIVE" else mutation
                code, result = validation.IndependentHandoverValidatorTests().assess(
                    root, validation.create_handback(root), validation.create_review(root, mutate))
                self.assertEqual(2 if mutation == "WAIVE" else 0, code, result)
                self.assertEqual(expected, result["status"])

    def test_risk_required_checks_and_justifications_cannot_be_waived_in_package(self):
        import task_package_generator as generator
        from acceptance_policy import EFFECT_CHECKS
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            path = packages.create_brief(root, mutation=adapt_brief)
            original = json.loads(path.read_text())
            import copy
            for effect in EFFECT_CHECKS:
                with self.subTest(effect=effect):
                    brief = copy.deepcopy(original)
                    brief["acceptancePolicy"]["effects"][effect] = True
                    with self.assertRaisesRegex(generator.TaskPackageError, "REQUIRED_CHECK_CANNOT_BE_WAIVED"):
                        generator.validate_brief(brief, packages.TASK_ID)
            for invalid in ("", "   ", None):
                brief = copy.deepcopy(original)
                brief["acceptancePolicy"]["notApplicable"]["rollback"] = invalid
                with self.assertRaisesRegex(generator.TaskPackageError, "REASON_REQUIRED"):
                    generator.validate_brief(brief, packages.TASK_ID)

    def test_read_only_exemptions_cannot_authorize_a_write_occupancy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            with self.assertRaisesRegex(AssertionError, "ACCEPTANCE_EFFECTS_WRITE_MISMATCH"):
                validation.setup_ready_for_validation(root, adapt_brief, "WRITE")
            ledger = json.loads((root / "module-ledgers" / validation.PROJECT / "ledger.json").read_text())
            self.assertEqual("PLANNED", ledger["tasks"][validation.TASK]["status"])

    def test_read_only_result_uses_approved_exemptions_but_still_needs_boss_finalization(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            validation.setup_ready_for_validation(root, adapt_brief, "READ")
            handback = validation.create_handback(root)
            review = validation.create_review(root, adapt_review)
            code, result = validation.IndependentHandoverValidatorTests().assess(
                root, handback, review, "--apply", "codex-module-central")
            self.assertEqual(0, code, result)
            self.assertEqual("PASS_PENDING_BOSS_APPROVAL", result["status"])
            self.assertFalse(result["doneRecorded"])
            code, repeated = validation.IndependentHandoverValidatorTests().assess(
                root, handback, review, "--apply", "codex-module-central")
            self.assertEqual(0, code, repeated)
            self.assertFalse(repeated["writePerformed"])
            code, final = validation.invoke(validation.C06, [
                "--data-root", str(root), "--project-id", validation.PROJECT,
                "--writer-id", "codex-module-central", "finalize", "--validation-id", validation.VALIDATION,
                "--boss-decision", "APPROVED", "--boss-decision-ref", "boss-research-final"])
            self.assertEqual(0, code, final)
            self.assertTrue(final["doneRecorded"])

    def test_read_only_outcome_package_does_not_require_fictional_software_checks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            packages.create_project_and_ledger(root)
            packages.add_planned_task(root)
            brief = packages.create_brief(root, mutation=adapt_brief)
            code, result = packages.invoke(packages.C04_SCRIPT, [
                "--data-root", str(root), "--project-id", packages.PROJECT_ID,
                "--writer-id", "codex-module-central", "generate", "--package-id", packages.PACKAGE_ID,
                "--task-id", packages.TASK_ID, "--brief", str(brief), "--apply"])
            self.assertEqual(0, code, result)
            package = json.loads((root / "task-packages" / packages.PROJECT_ID / "drafts" /
                                  packages.PACKAGE_ID / "task-package.json").read_text())
            self.assertEqual(research_policy(), package["acceptancePolicy"])
            self.assertEqual([], package["acceptance"]["rollbackChecks"])
            self.assertFalse(package["approvalAndDispatchBoundary"]["dispatchAllowed"])


if __name__ == "__main__":
    unittest.main()
