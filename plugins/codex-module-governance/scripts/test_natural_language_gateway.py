#!/usr/bin/env python3
"""C12 isolated natural-language gateway tests with fictional private data."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).parent
C02 = SCRIPTS / "initialize_project.py"
C03 = SCRIPTS / "ledger_manager.py"
C12 = SCRIPTS / "natural_language_gateway.py"
PROJECT = "c12-demo-project"


def invoke(script, arguments):
    completed = subprocess.run([sys.executable, str(script), *arguments], check=False, capture_output=True, text=True)
    return completed.returncode, json.loads(completed.stdout)


def setup(root, tasks=()):
    code, output = invoke(C02, [
        "--data-root", str(root), "--project-id", PROJECT,
        "--display-name", "C12 Fictional Gateway", "--scope-summary", "Isolated C12 tests only.", "--apply",
    ])
    if code != 0:
        raise AssertionError(output)
    code, output = invoke(C03, [
        "--data-root", str(root), "--project-id", PROJECT,
        "--writer-id", "codex-module-central", "initialize", "--apply",
    ])
    if code != 0:
        raise AssertionError(output)
    for task_id in tasks:
        code, output = invoke(C03, [
            "--data-root", str(root), "--project-id", PROJECT,
            "--writer-id", "codex-module-central", "add-task",
            "--task-id", task_id, "--title", f"Fictional {task_id}",
            "--business-goal", "Exercise the natural-language gateway safely.",
            "--plan-ref", f"plan:{task_id}",
        ])
        if code != 0:
            raise AssertionError(output)


def gateway(root, text, *extra):
    return invoke(C12, ["--data-root", str(root), "interpret", "--text", text, *extra])


class C12Tests(unittest.TestCase):
    def test_single_project_is_inferred_for_read_only_summary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private"; setup(root)
            code, output = gateway(root, "查看当前总账和下一个候选任务")
            self.assertEqual(code, 0, output)
            self.assertEqual(output["status"], "ROUTED_TO_SOLE_CENTRAL")
            self.assertEqual(output["intent"], "READ_LEDGER")
            self.assertEqual(output["executionMode"], "READ_ONLY")
            self.assertEqual(output["routingDecision"]["targetStage"], "C03")

    def test_prepare_task_package_infers_only_planned_task(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private"; setup(root, ["task-c12-001"])
            code, output = gateway(root, "生成任务包，不要派发")
            self.assertEqual(code, 0, output)
            self.assertEqual(output["intent"], "GENERATE_TASK_PACKAGE")
            self.assertEqual(output["executionMode"], "PREPARE")
            self.assertEqual(output["routingRequest"]["contextRefs"]["taskId"], "task-c12-001")
            self.assertEqual(output["routingDecision"]["targetStage"], "C04")

    def test_multiple_task_candidates_require_boss_choice(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private"; setup(root, ["task-c12-001", "task-c12-002"])
            code, output = gateway(root, "生成任务包，不要派发")
            self.assertEqual(code, 0, output)
            self.assertEqual(output["status"], "NEEDS_BOSS_INPUT")
            self.assertEqual(output["reason"], "C12_TASKID_REQUIRED")

    def test_approved_dispatch_routes_to_c10_but_does_not_dispatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private"; setup(root)
            code, output = gateway(root, "批准派发这个任务包", "--package-id", "package-c12-001")
            self.assertEqual(code, 0, output)
            self.assertEqual(output["executionMode"], "APPLY")
            self.assertEqual(output["routingDecision"]["targetStage"], "C10")
            self.assertFalse(output["routingDecision"]["boundaries"]["dispatchPerformed"])
            self.assertFalse(output["boundaries"]["downstreamExecutionPerformed"])

    def test_plain_approval_is_not_attached_to_an_unknown_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private"; setup(root)
            code, output = gateway(root, "批准")
            self.assertEqual(code, 0, output)
            self.assertEqual(output["status"], "NEEDS_BOSS_INPUT")
            self.assertEqual(output["reason"], "C12_APPROVAL_TARGET_AMBIGUOUS")

    def test_multiple_actions_are_not_guessed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private"; setup(root)
            code, output = gateway(root, "查看总账并生成任务包")
            self.assertEqual(code, 0, output)
            self.assertEqual(output["reason"], "C12_MULTIPLE_INTENTS_DETECTED")

    def test_read_only_and_approval_conflict_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private"; setup(root)
            code, output = gateway(root, "批准派发任务包，但只看不要执行", "--package-id", "package-c12-001")
            self.assertEqual(code, 0, output)
            self.assertEqual(output["reason"], "C12_READ_ONLY_AND_APPROVAL_CONFLICT")

    def test_sensitive_detail_is_rejected_without_echoing_text(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private"; setup(root)
            secret = "password=super-secret-value"
            code, output = gateway(root, f"查看总账 {secret}")
            self.assertEqual(code, 0, output)
            self.assertEqual(output["reason"], "C12_SENSITIVE_DETAIL_DETECTED")
            self.assertNotIn(secret, json.dumps(output, ensure_ascii=False))

    def test_new_project_requires_project_identifier(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private"
            code, output = gateway(root, "启动新项目")
            self.assertEqual(code, 0, output)
            self.assertEqual(output["reason"], "C12_PROJECT_CONTEXT_REQUIRED")
            self.assertEqual(output["intent"], "INITIALIZE_PROJECT")

    def test_new_project_with_identifier_routes_to_c02_without_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private"
            code, output = gateway(root, "启动新项目", "--project-id", "new-c12-project")
            self.assertEqual(code, 0, output)
            self.assertEqual(output["routingDecision"]["targetStage"], "C02")
            self.assertFalse((root / "project-registry").exists())

    def test_advisory_returns_to_c07_prepare(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private"; setup(root, ["task-c12-001"])
            code, output = gateway(root, "这个问题我不能裁定，请中央后台给意见")
            self.assertEqual(code, 0, output)
            self.assertEqual(output["intent"], "REQUEST_ADVISORY")
            self.assertEqual(output["routingDecision"]["targetStage"], "C07")
            self.assertEqual(output["executionMode"], "PREPARE")

    def test_communication_question_routes_to_c14_with_a_message_identifier(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private"; setup(root)
            code, output = gateway(root, "中央没收到这个回传", "--message-id", "message-c12-001")
            self.assertEqual(code, 0, output)
            self.assertEqual(output["intent"], "CHECK_TASK_COMMUNICATION")
            self.assertEqual(output["routingDecision"]["targetStage"], "C14")

    def test_initialize_ledger_routes_to_c03(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private"
            code, created = invoke(C02, [
                "--data-root", str(root), "--project-id", PROJECT,
                "--display-name", "C12 Ledger Initialization", "--scope-summary", "Only a fictional startup card.", "--apply",
            ])
            self.assertEqual(code, 0, created)
            code, output = gateway(root, "初始化项目总账")
            self.assertEqual(code, 0, output)
            self.assertEqual(output["intent"], "INITIALIZE_LEDGER")
            self.assertEqual(output["routingDecision"]["targetStage"], "C03")
            self.assertFalse((root / "module-ledgers").exists())

    def test_normal_cancellation_routes_to_c03_and_retains_downstream_gates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private"; setup(root, ["task-c12-001"])
            code, output = gateway(root, "撤销任务")
            self.assertEqual(code, 0, output)
            self.assertEqual(output["intent"], "REQUEST_TASK_CANCELLATION")
            self.assertEqual(output["executionMode"], "PREPARE")
            self.assertEqual(output["routingDecision"]["targetStage"], "C03")
            self.assertTrue(output["routingDecision"]["boundaries"]["downstreamGatesStillRequired"])

    def test_disconnection_routes_to_c08(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private"; setup(root)
            code, output = gateway(root, "窗口失联了，先冻结现场")
            self.assertEqual(code, 0, output)
            self.assertEqual(output["intent"], "FREEZE_DISCONNECTION")
            self.assertEqual(output["executionMode"], "APPLY")
            self.assertEqual(output["routingDecision"]["targetStage"], "C08")

    def test_external_skill_registration_routes_to_c11(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private"; setup(root)
            code, output = gateway(root, "固定一个外部 Skill")
            self.assertEqual(code, 0, output)
            self.assertEqual(output["routingDecision"]["targetStage"], "C11")

    def test_gateway_does_not_create_its_own_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private"; setup(root)
            before = sorted(str(path.relative_to(root)) for path in root.rglob("*"))
            code, output = gateway(root, "查看总账")
            after = sorted(str(path.relative_to(root)) for path in root.rglob("*"))
            self.assertEqual(code, 0, output)
            self.assertEqual(before, after)
            self.assertFalse(output["boundaries"]["gatewayStateStored"])

    def test_unknown_intent_requests_one_clarification(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private"; setup(root)
            code, output = gateway(root, "帮我看看这个")
            self.assertEqual(code, 0, output)
            self.assertEqual(output["reason"], "C12_INTENT_NOT_CLEAR")
            self.assertEqual(len(output["questions"]), 1)

    def test_examples_are_business_language_and_read_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private"
            code, output = invoke(C12, ["--data-root", str(root), "examples"])
            self.assertEqual(code, 0, output)
            self.assertGreaterEqual(len(output["examples"]), 5)
            self.assertFalse(output["boundaries"]["writePerformed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
