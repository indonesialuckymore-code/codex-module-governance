"""The outline-to-task bridge carries business outcomes, not a mandatory hierarchy."""
import unittest
import hashlib
import json
import tempfile
from pathlib import Path
import test_task_package_generator as packages


def handoff():
    return {
        "recordType": "OUTLINE_HANDOFF", "handoffSchemaVersion": "0.23.0",
        "projectId": "fictional-project", "outline": {"version": "v1.0", "sha256": "a" * 64,
            "status": "FROZEN_NEEDS_REGISTRATION"},
        "bossApproval": {"reference": "boss-outline-001"},
        "goals": [{"goalId": "goal-001", "description": "Staff can make the required decision."}],
        "outcomes": [{"outcomeId": "outcome-001", "goalId": "goal-001", "title": "Usable decision guide",
            "executionOwner": "CODEX", "acceptanceCriteria": [{"criterionId": "criterion-001", "description": "A representative user can complete a fictional case."}],
            "completionBoundary": "One usable guide; additional examples can follow later."}],
        "candidateTasks": [{"candidateKey": "P-01", "status": "NOT_REGISTERED", "executionOwner": "CODEX",
            "codexCentralRegistration": True, "outcomeIds": ["outcome-001"], "dependsOn": []}],
        "centralRegistrationScope": {"policy": "CODEX_ONLY", "includeCandidateKeys": ["P-01"], "excludeCandidateKeys": []},
    }


class OutlineOutcomes(unittest.TestCase):
    def test_invalid_imports_preserve_the_ledger_and_registered_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            packages.create_project_and_ledger(root)
            ledger_path = root / "module-ledgers" / packages.PROJECT_ID / "ledger.json"
            outline_path = root / "outline.md"
            outline_path.write_text("Fictional approved design", encoding="utf-8")
            value = handoff()
            value["projectId"] = packages.PROJECT_ID
            value["outline"]["sha256"] = hashlib.sha256(outline_path.read_bytes()).hexdigest()
            handoff_path = root / "handoff.json"
            handoff_path.write_text(json.dumps(value), encoding="utf-8")
            args = ["--data-root", str(root), "--project-id", packages.PROJECT_ID,
                "--writer-id", "codex-module-central", "import-outline", "--handoff", str(handoff_path),
                "--outline-file", str(outline_path), "--boss-approval-ref", "boss-outline-001"]
            original = ledger_path.read_bytes()
            failures = [("approval", {"bossApproval": {"reference": "other-approval"}}),
                ("project", {"projectId": "other-project"}),
                ("digest", {"outline": {**value["outline"], "sha256": "b" * 64}})]
            for label, override in failures:
                with self.subTest(label=label):
                    handoff_path.write_text(json.dumps({**value, **override}), encoding="utf-8")
                    code, result = packages.invoke(packages.C03_SCRIPT, args)
                    self.assertNotEqual(0, code, result)
                    self.assertFalse(result["writePerformed"])
                    self.assertEqual(original, ledger_path.read_bytes())
            handoff_path.write_text(json.dumps(value), encoding="utf-8")
            code, result = packages.invoke(packages.C03_SCRIPT, args)
            self.assertEqual(0, code, result)
            registered = ledger_path.read_bytes()
            value["outline"]["version"] = "v2.0"
            handoff_path.write_text(json.dumps(value), encoding="utf-8")
            code, result = packages.invoke(packages.C03_SCRIPT, args)
            self.assertNotEqual(0, code, result)
            self.assertEqual("OUTLINE_UPDATE_REQUIRES_RECONCILIATION", result["reason"])
            self.assertEqual(registered, ledger_path.read_bytes())

    def test_outline_is_bound_to_new_tasks_and_packages_without_rewriting_legacy_tasks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            packages.create_project_and_ledger(root)
            packages.add_planned_task(root, "C-OLD")
            ledger_path = root / "module-ledgers" / packages.PROJECT_ID / "ledger.json"
            old_task = json.loads(ledger_path.read_text())["tasks"]["C-OLD"]
            outline_path = root / "outline.md"
            outline_path.write_text("Fictional approved outline", encoding="utf-8")
            value = handoff()
            value["projectId"] = packages.PROJECT_ID
            value["outline"]["sha256"] = hashlib.sha256(outline_path.read_bytes()).hexdigest()
            handoff_path = root / "handoff.json"
            handoff_path.write_text(json.dumps(value), encoding="utf-8")
            base = ["--data-root", str(root), "--project-id", packages.PROJECT_ID, "--writer-id", "codex-module-central"]
            code, result = packages.invoke(packages.C03_SCRIPT, [*base, "import-outline", "--handoff", str(handoff_path),
                "--outline-file", str(outline_path), "--boss-approval-ref", "boss-outline-001"])
            self.assertEqual(0, code, result)
            registered_bytes = ledger_path.read_bytes()
            code, result = packages.invoke(packages.C03_SCRIPT, [*base, "import-outline", "--handoff", str(handoff_path),
                "--outline-file", str(outline_path), "--boss-approval-ref", "boss-outline-001"])
            self.assertEqual(0, code, result)
            self.assertFalse(result["writePerformed"])
            self.assertEqual(registered_bytes, ledger_path.read_bytes())
            args = [*base, "add-task", "--task-id", packages.TASK_ID, "--title", "Deliver a usable guide",
                "--business-goal", "Support the approved decision", "--plan-ref", "plan-guide-001"]
            code, result = packages.invoke(packages.C03_SCRIPT, args)
            self.assertNotEqual(0, code, result)
            code, result = packages.invoke(packages.C03_SCRIPT, [*args, "--outcome-id", "outcome-001"])
            self.assertEqual(0, code, result)
            brief = packages.create_brief(root)
            code, result = packages.invoke(packages.C04_SCRIPT, [*base, "generate", "--package-id", packages.PACKAGE_ID,
                "--task-id", packages.TASK_ID, "--brief", str(brief), "--apply"])
            self.assertEqual(0, code, result)
            from task_package_generator import load_package
            package = load_package(root, packages.PROJECT_ID, packages.PACKAGE_ID)
            contract = package["task"]["outcomeContract"]
            self.assertEqual("outcome-001", contract["outcomes"][0]["outcomeId"])
            self.assertEqual("criterion-001", contract["outcomes"][0]["acceptanceCriteria"][0]["criterionId"])
            self.assertEqual(old_task, json.loads(ledger_path.read_text())["tasks"]["C-OLD"])

    def test_incomplete_or_conflicting_design_cannot_be_registered(self):
        from outline_outcome_contract import OutlineContractError, validate_handoff
        changes = {
            "missing acceptance": lambda v: v["outcomes"][0].update(acceptanceCriteria=[]),
            "missing goal": lambda v: v["outcomes"][0].update(goalId="unknown-goal"),
            "missing completion boundary": lambda v: v["outcomes"][0].update(completionBoundary=""),
            "missing approval": lambda v: v.update(bossApproval={}),
            "non codex registration": lambda v: v["candidateTasks"][0].update(executionOwner="HUMAN"),
            "unknown dependency": lambda v: v["candidateTasks"][0].update(dependsOn=["P-99"]),
            "self dependency": lambda v: v["candidateTasks"][0].update(dependsOn=["P-01"]),
            "scope mismatch": lambda v: v["centralRegistrationScope"].update(includeCandidateKeys=[]),
            "malformed goal reference": lambda v: v["outcomes"][0].update(goalId=[]),
            "malformed owner": lambda v: v["candidateTasks"][0].update(executionOwner={}),
        }
        for label, change in changes.items():
            with self.subTest(label=label):
                value = handoff()
                change(value)
                with self.assertRaises(OutlineContractError):
                    validate_handoff(value, "fictional-project")

    def test_candidate_cannot_point_at_a_nonexistent_outcome(self):
        from outline_outcome_contract import OutlineContractError, validate_handoff
        value = handoff()
        value["candidateTasks"][0]["outcomeIds"] = ["missing-outcome"]
        with self.assertRaisesRegex(OutlineContractError, "CANDIDATE_OUTCOME"):
            validate_handoff(value, "fictional-project")

    def test_small_project_requires_no_blocks_stages_or_window_plan(self):
        from outline_outcome_contract import validate_handoff
        result = validate_handoff(handoff(), "fictional-project")
        self.assertEqual(["outcome-001"], result["candidateTasks"][0]["outcomeIds"])
        self.assertNotIn("modules", result)


if __name__ == "__main__":
    unittest.main()
