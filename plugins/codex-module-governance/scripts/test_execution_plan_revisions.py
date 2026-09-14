"""Execution rearrangement preserves the approved business contract."""
import copy
import fcntl
import json
import tempfile
import unittest
from pathlib import Path

import test_central_construction_controller as fixture
from central_construction_controller import canonical_digest


def setup(root):
    fixture.setup_project(root)
    tasks = []
    for task_id in ["C-101", "C-102"]:
        code, result = fixture.invoke(fixture.C03, ["--data-root", str(root), "--project-id", fixture.PROJECT,
            "--writer-id", "codex-module-central", "add-task", "--task-id", task_id,
            "--title", "Fictional useful outcome", "--business-goal", "Deliver a fictional useful result",
            "--plan-ref", "approved-plan-001"])
        if code:
            raise AssertionError(result)
        tasks.append({"taskId": task_id, "dependencies": [], "packageId": "package-" + task_id,
            "c05ReviewId": "review-" + task_id, "c05ReviewPath": str(root / (task_id + "-review.json")),
            "c10RequestPath": str(root / (task_id + "-request.json"))})
    plan = {"tasks": tasks}
    value = {"executionMapSchemaVersion": "0.17.0", "recordType": "C09_APPROVED_EXECUTION_MAP",
        "planId": "plan-001", "projectId": fixture.PROJECT,
        "authorization": {"scopeId": "plan-001", "scopeDigest": canonical_digest(plan), "bossApprovalRef": "boss-approval-001"},
        "executionPlan": plan}
    return fixture.write_json(root / "approved.json", value), value


def revise(root, source, plan, revision="revision-001"):
    path = fixture.write_json(root / (revision + "-input.json"), plan)
    return fixture.invoke(fixture.C09, ["--data-root", str(root), "revise-plan", "--project-id", fixture.PROJECT,
        "--execution-map", str(source), "--arrangement", str(path), "--revision-id", revision,
        "--reason-ref", "priority-correction-001", "--writer-id", "codex-module-central",
        "--caller-thread-ref", "central-thread-001"])


class ExecutionPlanRevisions(unittest.TestCase):
    def test_malformed_legacy_dependency_is_refused_without_crashing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            source, value = setup(root)
            value["executionPlan"]["tasks"][0]["dependencies"] = [{}]
            value["authorization"]["scopeDigest"] = canonical_digest(value["executionPlan"])
            fixture.write_json(source, value)
            code, result = revise(root, source, value["executionPlan"])
            self.assertNotEqual(0, code, result)
            self.assertEqual("C09_EXECUTION_MAP_DEPENDENCIES_INVALID", result["reason"])

    def test_an_old_central_cannot_rearrange_the_project(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            source, value = setup(root)
            code, result = fixture.invoke(fixture.SCRIPTS / "role_continuity_controller.py", [
                "--data-root", str(root), "--project-id", fixture.PROJECT, "--writer-id", "codex-module-central",
                "initialize", "--calling-thread-ref", "central-thread-current", "--central-thread-ref", "central-thread-current",
                "--runtime-project-id", "runtime-project-001", "--execution-map-ref", "plan-001"])
            self.assertEqual(0, code, result)
            ledger_path = root / "module-ledgers" / fixture.PROJECT / "ledger.json"
            before = ledger_path.read_bytes()
            code, result = revise(root, source, {"tasks": list(reversed(value["executionPlan"]["tasks"]))})
            self.assertNotEqual(0, code, result)
            self.assertEqual("LEDGER_CALLER_NOT_CURRENT_CENTRAL", result["reason"])
            self.assertEqual(before, ledger_path.read_bytes())

    def test_identical_arrangement_does_not_create_noise_versions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            source, value = setup(root)
            ledger_path = root / "module-ledgers" / fixture.PROJECT / "ledger.json"
            before = ledger_path.read_bytes()
            code, result = revise(root, source, value["executionPlan"])
            self.assertEqual(0, code, result)
            self.assertFalse(result["writePerformed"])
            self.assertEqual("NO_EXECUTION_ARRANGEMENT_CHANGE", result["status"])
            self.assertEqual(before, ledger_path.read_bytes())

    def test_rearrangement_and_successor_preparation_share_a_nonblocking_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            source, value = setup(root)
            lock_path = root / "execution-plan-locks" / fixture.PROJECT / "plan-001.lock"
            lock_path.parent.mkdir(parents=True)
            with lock_path.open("a+") as active_process:
                fcntl.flock(active_process, fcntl.LOCK_EX | fcntl.LOCK_NB)
                code, result = revise(root, source, {"tasks": list(reversed(value["executionPlan"]["tasks"]))})
                self.assertNotEqual(0, code, result)
                self.assertEqual("C09_PLAN_OPERATION_IN_PROGRESS", result["reason"])
                code, result = fixture.invoke(fixture.C09, ["--data-root", str(root), "continue-successors",
                    "--project-id", fixture.PROJECT, "--validation-id", "validation-fictional",
                    "--execution-map", str(source), "--writer-id", "codex-module-central",
                    "--caller-thread-ref", "central-thread-001"])
                self.assertNotEqual(0, code, result)
                self.assertEqual("C09_PLAN_OPERATION_IN_PROGRESS", result["reason"])

    def test_scope_expansion_and_invalid_dependencies_leave_everything_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            source, value = setup(root)
            ledger_path = root / "module-ledgers" / fixture.PROJECT / "ledger.json"
            before = ledger_path.read_bytes()
            changes = [lambda p: p["tasks"][0].update(packageId="different-package"),
                lambda p: p["tasks"][0].update(dependencies=["C-102"]),
                lambda p: p["tasks"].pop(),
                lambda p: p["tasks"].append(copy.deepcopy(p["tasks"][0])),
                lambda p: p["tasks"][0].update(schedulingDependencies=["C-unknown"]),
                lambda p: p["tasks"][0].update(schedulingDependencies=[{}]),
                lambda p: (p["tasks"][0].update(schedulingDependencies=["C-102"]),
                    p["tasks"][1].update(schedulingDependencies=["C-101"]))]
            for index, change in enumerate(changes):
                with self.subTest(change=index):
                    arrangement = copy.deepcopy(value["executionPlan"])
                    change(arrangement)
                    code, result = revise(root, source, arrangement)
                    self.assertNotEqual(0, code, result)
                    self.assertFalse(result["writePerformed"])
                    self.assertEqual(before, ledger_path.read_bytes())

    def test_retry_is_idempotent_and_old_parent_cannot_create_a_parallel_branch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            source, value = setup(root)
            arrangement = {"tasks": list(reversed(value["executionPlan"]["tasks"]))}
            code, first = revise(root, source, arrangement)
            self.assertEqual(0, code, first)
            ledger_path = root / "module-ledgers" / fixture.PROJECT / "ledger.json"
            before = ledger_path.read_bytes()
            code, retry = revise(root, source, arrangement)
            self.assertEqual(0, code, retry)
            self.assertFalse(retry["writePerformed"])
            self.assertEqual(before, ledger_path.read_bytes())
            code, branch = revise(root, source, value["executionPlan"], "revision-002")
            self.assertNotEqual(0, code, branch)
            self.assertEqual("C09_PLAN_SOURCE_SUPERSEDED", branch["reason"])
            self.assertEqual(before, ledger_path.read_bytes())

    def test_export_failure_can_be_recovered_without_repeating_ledger_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            source, value = setup(root)
            arrangement = {"tasks": list(reversed(value["executionPlan"]["tasks"]))}
            obstruction = root / "execution-plan-revisions"
            obstruction.write_text("fictional obstruction")
            code, pending = revise(root, source, arrangement)
            self.assertEqual(0, code, pending)
            self.assertEqual("PLAN_RECORDED_EXPORT_PENDING", pending["status"])
            self.assertTrue(pending["writePerformed"])
            ledger_path = root / "module-ledgers" / fixture.PROJECT / "ledger.json"
            before = ledger_path.read_bytes()
            obstruction.unlink()
            code, recovered = revise(root, source, arrangement)
            self.assertEqual(0, code, recovered)
            self.assertTrue(Path(recovered["artifact"]).is_file())
            self.assertTrue(recovered["writePerformed"])
            self.assertFalse(recovered["ledgerWritePerformed"])
            self.assertTrue(recovered["artifactWritePerformed"])
            self.assertEqual(before, ledger_path.read_bytes())

    def test_new_wait_cannot_silently_override_a_prepared_task(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            source, value = setup(root)
            code, result = fixture.invoke(fixture.C03, ["--data-root", str(root), "--project-id", fixture.PROJECT,
                "--writer-id", "codex-module-central", "transition-task", "--task-id", "C-101", "--to-status", "READY",
                "--reason", "Fictional dispatch preparation already occurred"])
            self.assertEqual(0, code, result)
            ledger_path = root / "module-ledgers" / fixture.PROJECT / "ledger.json"
            before = ledger_path.read_bytes()
            arrangement = copy.deepcopy(value["executionPlan"])
            arrangement["tasks"][0]["schedulingDependencies"] = ["C-102"]
            code, result = revise(root, source, arrangement)
            self.assertNotEqual(0, code, result)
            self.assertEqual("C09_IN_FLIGHT_TASK_REQUIRES_COORDINATED_CHANGE:C-101", result["reason"])
            self.assertEqual(before, ledger_path.read_bytes())

    def test_optional_scheduling_wait_can_change_without_changing_business_dependencies(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            source, value = setup(root)
            arrangement = copy.deepcopy(value["executionPlan"])
            arrangement["tasks"][0]["schedulingDependencies"] = ["C-102"]
            code, result = revise(root, source, arrangement)
            self.assertEqual(0, code, result)
            revised_path = Path(result["artifact"])
            revised = json.loads(revised_path.read_text())
            self.assertEqual([], revised["approvedPlan"]["tasks"][0]["dependencies"])
            code, removed = revise(root, revised_path, value["executionPlan"], "revision-002")
            self.assertEqual(0, code, removed)
            self.assertFalse(removed["bossRepromptRequired"])

    def test_reordering_preserves_approval_and_tasks_and_has_one_current_version(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            source, value = setup(root)
            before = source.read_bytes()
            ledger_path = root / "module-ledgers" / fixture.PROJECT / "ledger.json"
            old_tasks = json.loads(ledger_path.read_text())["tasks"]
            arrangement = copy.deepcopy(value["executionPlan"])
            arrangement["tasks"].reverse()
            code, result = revise(root, source, arrangement)
            self.assertEqual(0, code, result)
            self.assertFalse(result["bossRepromptRequired"])
            revised = json.loads(Path(result["artifact"]).read_text())
            self.assertEqual(value["authorization"], revised["authorization"])
            self.assertEqual("C-102", revised["executionPlan"]["tasks"][0]["taskId"])
            ledger = json.loads(ledger_path.read_text())
            self.assertEqual(canonical_digest(revised), ledger["executionPlanRevisions"]["plan-001"]["currentDigest"])
            self.assertEqual(old_tasks, ledger["tasks"])
            self.assertEqual(before, source.read_bytes())


if __name__ == "__main__":
    unittest.main()
