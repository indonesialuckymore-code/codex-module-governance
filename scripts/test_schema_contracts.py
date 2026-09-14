"""Validate shipped examples with a real JSON Schema engine, not text matching."""
import json
import copy
import unittest
import sys
import tempfile
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/codex-module-governance/scripts"))
import test_task_window_dispatch_controller as dispatch


class SchemaContracts(unittest.TestCase):
    def test_outcome_review_and_final_decision_match_schemas(self):
        import test_independent_handover_validator as validation
        from test_outline_outcomes import handoff
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'private'; validation.setup_ready_for_validation(root, outline_handoff=handoff())
            path = validation.create_review(root); review = json.loads(path.read_text())
            review['outcomeAssessments'] = [{'outcomeId': 'outcome-001', 'criterionId': 'criterion-001',
                'reference': 'evidence-after', 'verdict': 'PASS', 'independentlyReadBack': True}]
            validation.write_json(path, review)
            self.assert_schema('codex-independent-validation-review.schema.json', review)
            code, result = validation.IndependentHandoverValidatorTests().assess(root,
                validation.create_handback(root), path, '--apply', 'codex-module-central')
            self.assertEqual(0, code, result)
            self.assert_schema('codex-validation-decision.schema.json', validation.validator.load_decision(root, validation.PROJECT, validation.VALIDATION))

    def test_handover_base_and_refresh_packages_bind_delta_schema(self):
        import test_handover_refresh as handover
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; handover.f.setup(root)
            _, _, base = handover.f.prepare_handover(root, "handover-base", "CURRENT_CENTRAL", "REPLACE_ACTIVE_ROLE")
            self.assert_schema("codex-role-handover-package.schema.json", json.loads(base.read_text()))
            event = handover.f.write(root / "inputs/event.json", handover.f.event_payload())
            self.assertEqual(0, handover.f.continuity(root, "submit-event", "--event", str(event))[0])
            self.assertEqual(0, handover.refresh(root)[0])
            package = json.loads((base.parent.parent / "handover-update/handover-package.json").read_text())
            self.assert_schema("codex-role-handover-package.schema.json", package)
            package["delta"]["successorMustReadChanges"] = False
            with self.assertRaises(AssertionError):
                self.assert_schema("codex-role-handover-package.schema.json", package)

    def test_issue_specific_review_decision_and_closed_feedback_match_schemas(self):
        import test_delivery_feedback as feedback
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            feedback.completed(root)
            paths = feedback.repair_ready(root)
            self.assert_schema("codex-independent-validation-review.schema.json", json.loads(paths[1].read_text()))
            self.assertEqual(0, feedback.assess_repair(root, paths)[0])
            self.assert_schema("codex-validation-decision.schema.json",
                feedback.fixture.validator.load_decision(root, feedback.fixture.PROJECT, "repair-validation"))
            self.assertEqual(0, feedback.finalize_repair(root)[0])
            self.assertEqual(0, feedback.close(root)[0])
            ledger = feedback.fixture.ledger_manager.load_ledger(root, feedback.fixture.PROJECT)
            self.assert_schema("codex-module-ledger.schema.json", ledger)
            ledger["tasks"][feedback.fixture.TASK]["deliveryFeedback"]["feedback-missing-case"].pop("closure")
            with self.assertRaises(AssertionError):
                self.assert_schema("codex-module-ledger.schema.json", ledger)

    def test_external_wait_resolution_and_reported_feedback_match_ledger_schema(self):
        import test_external_waits as waits
        import test_delivery_feedback as feedback
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            waits.setup(root)
            self.assertEqual(0, waits.wait(root)[0])
            self.assertEqual(0, waits.command(root, "resolve-wait", "--task-id", waits.TASK,
                "--resolution", str(waits.resolution(root)))[0])
            ledger = json.loads((root / "module-ledgers" / waits.PROJECT / "ledger.json").read_text())
            self.assert_schema("codex-module-ledger.schema.json", ledger)
            ledger["tasks"][waits.TASK]["externalWaits"]["wait-material"].pop("resolution")
            with self.assertRaises(AssertionError):
                self.assert_schema("codex-module-ledger.schema.json", ledger)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            feedback.completed(root)
            code, result = feedback.fixture.c03(root, "record-feedback", "--task-id", feedback.fixture.TASK,
                "--feedback-id", "feedback-schema", "--validation-id", feedback.fixture.VALIDATION,
                "--issue-ref", "issue-schema", "--affected-ref", "affected-outcome")
            self.assertEqual(0, code, result)
            self.assert_schema("codex-module-ledger.schema.json",
                json.loads((root / "module-ledgers" / feedback.fixture.PROJECT / "ledger.json").read_text()))
            self.assertEqual(0, feedback.fixture.c03(root, "add-task", "--task-id", "C-repair",
                "--title", "Repair outcome", "--business-goal", "Correct result", "--plan-ref", "approved-repair")[0])
            self.assertEqual(0, feedback.fixture.c03(root, "link-feedback-repair", "--task-id", feedback.fixture.TASK,
                "--feedback-id", "feedback-schema", "--repair-task-id", "C-repair")[0])
            self.assert_schema("codex-module-ledger.schema.json", feedback.fixture.ledger_manager.load_ledger(root, feedback.fixture.PROJECT))

    def test_adaptive_schema_rejects_core_risk_waivers_and_empty_legacy_checks(self):
        import test_adaptive_acceptance as adaptive
        import test_task_package_generator as packages
        policy_schema = json.loads((ROOT / "schemas/codex-acceptance-policy.schema.json").read_text())
        checker = Draft202012Validator(policy_schema)
        policy = adaptive.research_policy()
        self.assertTrue(checker.is_valid(policy))
        for effect in policy["effects"]:
            invalid = copy.deepcopy(policy)
            invalid["effects"][effect] = True
            self.assertFalse(checker.is_valid(invalid), effect)
        for category in ("positiveCase", "afterSnapshot", "logsAndHistory", "testAndObjectIds"):
            invalid = copy.deepcopy(policy)
            invalid["notApplicable"][category] = "Unjustified exemption."
            self.assertFalse(checker.is_valid(invalid), category)
        with tempfile.TemporaryDirectory() as temp:
            brief = json.loads(packages.create_brief(Path(temp), mutation=adaptive.adapt_brief).read_text())
            self.assert_schema("codex-task-package-brief.schema.json", brief)
            brief.pop("acceptancePolicy")
            with self.assertRaises(AssertionError):
                self.assert_schema("codex-task-package-brief.schema.json", brief)

    def test_adaptive_package_review_and_decision_match_active_schemas(self):
        import test_adaptive_acceptance as adaptive
        import test_independent_handover_validator as validation
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            validation.setup_ready_for_validation(root, adaptive.adapt_brief, "READ")
            self.assert_schema("codex-task-package-brief.schema.json",
                json.loads((root / "briefs" / f"{validation.TASK}.json").read_text()))
            self.assert_schema("codex-draft-task-package.schema.json",
                json.loads((root / "task-packages" / validation.PROJECT / "drafts" /
                    validation.PACKAGE / "task-package.json").read_text()))
            review = validation.create_review(root, adaptive.adapt_review)
            self.assert_schema("codex-independent-validation-review.schema.json", json.loads(review.read_text()))
            code, result = validation.IndependentHandoverValidatorTests().assess(root,
                validation.create_handback(root), review, "--apply", "codex-module-central")
            self.assertEqual(0, code, result)
            decision = validation.validator.load_decision(root, validation.PROJECT, validation.VALIDATION)
            self.assert_schema("codex-validation-decision.schema.json", decision)

    def test_verified_correction_and_direction_handback_match_active_schemas(self):
        import test_correction_resume as corrections
        import test_task_communication_bridge as bridge
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            corrections.setup(root)
            corrections.complete_roundtrip(root)
            code, current = corrections.command(root, "verify-correction", "--task-id", corrections.TASK,
                "--verification", str(corrections.verification(root)))
            self.assertEqual(code, 0, current)
            ledger = json.loads((root / "module-ledgers" / corrections.PROJECT / "ledger.json").read_text())
            self.assert_schema("codex-module-ledger.schema.json", ledger)
            handback = bridge.handback()
            handback["directionContext"] = {"revision": 1, "digest": current["current"]["requestDigest"]}
            self.assert_schema("codex-task-handback.schema.json", handback)
            ledger["tasks"][corrections.TASK]["directionVerifications"][0]["revision"] = False
            validator = Draft202012Validator(json.loads((ROOT / "schemas/codex-module-ledger.schema.json").read_text()))
            self.assertFalse(validator.is_valid(ledger))

    def test_direction_correction_records_match_schema_and_reject_false_revision(self):
        import test_task_corrections as corrections
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            corrections.setup(root)
            self.assertEqual(corrections.correct(root)[0], 0)
            ledger = json.loads((root / "module-ledgers" / corrections.PROJECT / "ledger.json").read_text())
            self.assert_schema("codex-module-ledger.schema.json", ledger)
            ledger["tasks"][corrections.TASK]["directionCorrections"][0]["revision"] = False
            validator = Draft202012Validator(json.loads((ROOT / "schemas/codex-module-ledger.schema.json").read_text()))
            self.assertFalse(validator.is_valid(ledger))

    def test_registered_execution_revision_and_ledger_match_shipped_schemas(self):
        import test_execution_plan_revisions as revisions
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            source, value = revisions.setup(root)
            arrangement = {"tasks": copy.deepcopy(list(reversed(value["executionPlan"]["tasks"])))}
            arrangement["tasks"][0]["schedulingDependencies"] = ["C-101"]
            code, result = revisions.revise(root, source, arrangement)
            self.assertEqual(0, code, result)
            self.assert_schema("codex-approved-execution-map.schema.json", json.loads(Path(result["artifact"]).read_text()))
            self.assert_schema("codex-module-ledger.schema.json",
                json.loads((root / "module-ledgers" / revisions.fixture.PROJECT / "ledger.json").read_text()))
            invalid = json.loads(Path(result["artifact"]).read_text())
            invalid.pop("approvedPlan")
            validator = Draft202012Validator(json.loads((ROOT / "schemas/codex-approved-execution-map.schema.json").read_text()))
            self.assertFalse(validator.is_valid(invalid))
            legacy_with_new_field = copy.deepcopy(value)
            legacy_with_new_field["executionPlan"]["tasks"][0]["schedulingDependencies"] = []
            self.assertFalse(validator.is_valid(legacy_with_new_field))

    def assert_schema(self, filename, value):
        schema = json.loads((ROOT / "schemas" / filename).read_text())
        Draft202012Validator.check_schema(schema)
        resources = [json.loads(path.read_text()) for path in (ROOT / "schemas").glob("*.schema.json")]
        registry = Registry().with_resources((item["$id"], Resource.from_contents(item)) for item in resources)
        errors = list(Draft202012Validator(schema, registry=registry).iter_errors(value))
        self.assertEqual([], [f"{filename} {list(e.path)}: {e.message}" for e in errors])

    def test_flexible_runtime_contracts_and_current_ledger(self):
        request = dispatch.dispatch_request()
        request["model"] = "gpt-5.6-luna"
        self.assert_schema("codex-dispatch-request.schema.json", request)
        self.assert_schema("codex-runtime-dispatch-confirmation.schema.json",
            dispatch.confirmation(model="gpt-5.6-luna", model_method="RUNTIME_MODEL_READBACK", permission_profile="disabled"))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            dispatch.setup_ready(root)
            self.assert_schema("codex-module-ledger.schema.json",
                json.loads((root / "module-ledgers" / dispatch.fixture.PROJECT_ID / "ledger.json").read_text()))

    def test_shipped_module_config_is_accepted(self):
        schema = json.loads((ROOT / "schemas/module-config.schema.json").read_text())
        config = json.loads((ROOT / "config/module-config.example.json").read_text())
        Draft202012Validator.check_schema(schema)
        errors = list(Draft202012Validator(schema).iter_errors(config))
        self.assertEqual([], [f"{list(e.path)}: {e.message}" for e in errors])


if __name__ == "__main__":
    unittest.main()
