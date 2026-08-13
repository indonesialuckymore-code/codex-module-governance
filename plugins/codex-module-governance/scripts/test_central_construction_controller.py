#!/usr/bin/env python3
"""Regression tests for C09 using temporary fictional governance data only."""

import copy
import hashlib
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
C09 = SCRIPTS / "central_construction_controller.py"
PROJECT = "c09-demo-project"

import central_construction_controller as central
import test_disconnection_recovery_controller as c08_fixture


def invoke(script, arguments):
    completed = subprocess.run([sys.executable, str(script), *arguments], check=False, capture_output=True, text=True)
    return completed.returncode, json.loads(completed.stdout)


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def setup_project(root):
    code, output = invoke(C02, [
        "--data-root", str(root), "--project-id", PROJECT, "--display-name", "C09 Fictional Routing",
        "--scope-summary", "Isolated central routing tests only.", "--apply",
    ])
    if code != 0:
        raise AssertionError(output)
    code, output = invoke(C03, [
        "--data-root", str(root), "--project-id", PROJECT, "--writer-id", "codex-module-central",
        "initialize", "--apply",
    ])
    if code != 0:
        raise AssertionError(output)


def setup_project_card_only(root, project=PROJECT):
    code, output = invoke(C02, [
        "--data-root", str(root), "--project-id", project, "--display-name", "C09 Ledger Initialization",
        "--scope-summary", "Isolated ledger initialization routing only.", "--apply",
    ])
    if code != 0:
        raise AssertionError(output)


def request(intent, mode="READ_ONLY", project=PROJECT, approved=False, **refs):
    context = {"taskId": None, "packageId": None, "validationId": None, "recoveryCaseId": None, "windowId": None, "messageId": None}
    context.update(refs)
    return {
        "requestSchemaVersion": "0.12.0",
        "recordType": "C09_CENTRAL_ROUTING_REQUEST",
        "requestId": f"request-{intent.lower().replace('_', '-')}",
        "submittedBy": "boss",
        "projectId": project,
        "intent": intent,
        "executionMode": mode,
        "bossAuthorization": {"status": "APPROVED" if approved else "NOT_REQUIRED", "reference": "boss-approval-c09" if approved else None},
        "contextRefs": context,
    }


def route(root, payload, filename="request.json"):
    path = write_json(root / "c09-inputs" / filename, payload)
    return invoke(C09, ["--data-root", str(root), "route", "--request", str(path)])


class C09Tests(unittest.TestCase):
    def test_status_confirms_one_canonical_central_and_model_policy(self):
        with tempfile.TemporaryDirectory() as temporary:
            code, output = invoke(C09, ["--data-root", str(Path(temporary) / "private-data"), "status"])
            self.assertEqual(code, 0, output)
            self.assertEqual(output["centralEntryCount"], 1)
            self.assertEqual(output["canonicalCentralSkill"], "central-construction-controller")
            self.assertIn("C00", output["readyStages"])
            self.assertIn("C14", output["readyStages"])
            self.assertEqual(output["models"], {"central": "gpt-5.6-sol", "taskWindow": "gpt-5.6-terra", "subAgent": "gpt-5.6-terra"})
            self.assertEqual(output["subAgentPolicy"], {"maxConcurrentFirstLevel": 3, "allowGrandchildren": False})
            self.assertEqual(output["windowPolicy"], {"maxSequentialAssignments": 2, "allowConcurrentAssignments": False, "centralChoosesReuse": True})
            self.assertTrue(output["boundaries"]["onePassExecutionMapAvailable"])
            self.assertTrue(output["boundaries"]["scopedBatchApprovalAvailable"])
            self.assertTrue(output["boundaries"]["automaticSuccessorDispatchAvailable"])
            self.assertTrue(output["boundaries"]["twoTaskWindowLifecycleEnforced"])
            self.assertTrue(output["boundaries"]["projectBoundDispatchRequired"])

    def test_duplicate_central_registry_is_refused(self):
        registry = json.loads(central.REGISTRY_PATH.read_text(encoding="utf-8"))
        duplicate = copy.deepcopy(registry)
        duplicate["capabilities"].append({"stage": "C99", "role": "CENTRAL", "skill": "parallel-central", "script": "parallel.py", "status": "READY"})
        with self.assertRaisesRegex(central.CentralRoutingError, "MULTIPLE_OR_MISSING_CENTRAL"):
            central.validate_registry(duplicate, check_files=False)

    def test_c00_is_one_non_central_planner_without_runtime_script(self):
        registry = json.loads(central.REGISTRY_PATH.read_text(encoding="utf-8"))
        validated = central.validate_registry(registry, check_files=True)
        planners = [entry for entry in validated["capabilities"] if entry["role"] == "PLANNER"]
        self.assertEqual(planners, [{
            "stage": "C00",
            "role": "PLANNER",
            "skill": "construction-outline-planner",
            "script": None,
            "status": "READY",
        }])

    def test_c00_is_whole_project_chief_designer_with_codex_only_handoff(self):
        planner_root = central.PLUGIN_SKILLS / "construction-outline-planner"
        skill = (planner_root / "SKILL.md").read_text(encoding="utf-8")
        template = (planner_root / "references" / "outline-template.md").read_text(encoding="utf-8")
        self.assertIn("整个项目的总设计师", skill)
        self.assertIn("只接管 Codex 施工范围", skill)
        self.assertIn("centralRegistrationScope", template)
        self.assertIn("policy: CODEX_ONLY", template)

    def test_central_requires_one_pass_execution_map_and_saved_project_dispatch(self):
        central_root = central.PLUGIN_SKILLS / "central-construction-controller"
        skill = (central_root / "SKILL.md").read_text(encoding="utf-8")
        execution_map = (central_root / "references" / "project-execution-map.md").read_text(encoding="utf-8")
        self.assertIn("所有 Boss 决策集中成一个编号清单", skill)
        self.assertIn("第一波合格任务立即批量派发", skill)
        self.assertIn("project + worktree", skill)
        self.assertIn("authorizationScope", execution_map)
        self.assertIn("不允许 `projectless`", execution_map)

    def test_new_project_routes_to_c02_without_creating_project(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            code, output = route(root, request("INITIALIZE_PROJECT", mode="PREPARE", project="new-c09-project"))
            self.assertEqual(code, 0, output)
            self.assertEqual((output["targetStage"], output["routeStatus"]), ("C02", "ROUTED_PREPARE"))
            self.assertFalse((root / "project-registry").exists())

    def test_ledger_initialization_routes_to_c03_before_ledger_exists(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"; setup_project_card_only(root)
            code, output = route(root, request("INITIALIZE_LEDGER", mode="PREPARE"))
            self.assertEqual(code, 0, output)
            self.assertEqual((output["targetStage"], output["routeStatus"]), ("C03", "ROUTED_PREPARE"))
            self.assertFalse((root / "module-ledgers").exists())

    def test_normal_routes_cover_c03_through_c08(self):
        cases = [
            ("READ_LEDGER", {}, "C03"),
            ("INITIALIZE_LEDGER", {}, "C03"),
            ("REGISTER_GOVERNANCE_STATE", {}, "C03"),
            ("REQUEST_TASK_CANCELLATION", {"taskId": "task-c09-001"}, "C03"),
            ("GENERATE_TASK_PACKAGE", {"taskId": "task-c09-001"}, "C04"),
            ("CHECK_OCCUPANCY", {"packageId": "package-c09-001"}, "C05"),
            ("VALIDATE_HANDBACK", {"packageId": "package-c09-001"}, "C06"),
            ("FINALIZE_TASK", {"validationId": "validation-c09-001"}, "C06"),
            ("REQUEST_ADVISORY", {"taskId": "task-c09-001"}, "C07"),
            ("RECORD_BOSS_ADJUDICATION", {"taskId": "task-c09-001"}, "C07"),
            ("FREEZE_DISCONNECTION", {}, "C08"),
            ("TAKEOVER_CONTROL", {"recoveryCaseId": "recovery-c09-001"}, "C08"),
            ("RECORD_RECOVERY_DECISION", {"recoveryCaseId": "recovery-c09-001"}, "C08"),
            ("RELEASE_OCCUPANCY", {"recoveryCaseId": "recovery-c09-001"}, "C08"),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"; setup_project(root)
            for intent, refs, stage in cases:
                with self.subTest(intent=intent):
                    code, output = route(root, request(intent, **refs), f"{intent}.json")
                    self.assertEqual(code, 0, output)
                    self.assertEqual(output["targetStage"], stage)
                    self.assertFalse(output["boundaries"]["downstreamInvoked"])

    def test_prepare_route_does_not_require_apply_authorization(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"; setup_project(root)
            code, output = route(root, request("GENERATE_TASK_PACKAGE", mode="PREPARE", taskId="task-c09-001"))
            self.assertEqual(code, 0, output)
            self.assertEqual(output["routeStatus"], "ROUTED_PREPARE")

    def test_apply_without_boss_authorization_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"; setup_project(root)
            code, output = route(root, request("REGISTER_GOVERNANCE_STATE", mode="APPLY"))
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "C09_APPLY_REQUIRES_EXPLICIT_BOSS_AUTHORIZATION")

    def test_apply_with_boss_authorization_still_requires_downstream_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"; setup_project(root)
            code, output = route(root, request("REGISTER_GOVERNANCE_STATE", mode="APPLY", approved=True))
            self.assertEqual(code, 0, output)
            self.assertEqual(output["routeStatus"], "ROUTED_APPLY_REQUIRES_DOWNSTREAM_GATE")
            self.assertTrue(output["boundaries"]["downstreamGatesStillRequired"])

    def test_task_window_dispatch_routes_to_c10(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"; setup_project(root)
            code, output = route(root, request("DISPATCH_TASK_WINDOW", mode="APPLY", approved=True, packageId="package-c09-001"))
            self.assertEqual(code, 0, output)
            self.assertEqual(output["routeStatus"], "ROUTED_APPLY_REQUIRES_C10_GATES")
            self.assertEqual(output["targetStage"], "C10")
            self.assertFalse(output["boundaries"]["dispatchPerformed"])

    def test_sub_agent_dispatch_routes_to_c10(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"; setup_project(root)
            code, output = route(root, request("DISPATCH_SUB_AGENT", mode="APPLY", approved=True, windowId="window-c09-001"))
            self.assertEqual(code, 0, output)
            self.assertEqual(output["routeStatus"], "ROUTED_APPLY_REQUIRES_C10_GATES")

    def test_business_resume_still_requires_recovery_review(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"; setup_project(root)
            code, output = route(root, request("RESUME_BUSINESS_EXECUTION", mode="APPLY", approved=True))
            self.assertEqual(code, 0, output)
            self.assertEqual(output["routeStatus"], "BLOCKED_CAPABILITY_NOT_IMPLEMENTED")

    def test_external_skill_intents_route_to_c11(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"; setup_project(root)
            for intent in ("REGISTER_EXTERNAL_SKILL", "RESOLVE_EXTERNAL_SKILL"):
                with self.subTest(intent=intent):
                    code, output = route(root, request(intent), f"{intent}.json")
                    self.assertEqual(code, 0, output); self.assertEqual(output["targetStage"], "C11")

    def test_task_communication_routes_to_c14_without_changing_task_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"; setup_project(root)
            code, output = route(root, request("CHECK_TASK_COMMUNICATION", messageId="message-c09-001"))
            self.assertEqual(code, 0, output)
            self.assertEqual(output["targetStage"], "C14")
            self.assertEqual(output["targetSkill"], "task-communication-bridge")
            self.assertFalse(output["boundaries"]["writePerformed"])

    def test_recovery_freeze_overrides_normal_routing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"; c08_fixture.setup_project(root)
            incident_path = write_json(root / "c08-inputs" / "incident.json", c08_fixture.incident())
            code, frozen = invoke(C08, ["--data-root", str(root), "--project-id", c08_fixture.PROJECT, "--writer-id", "codex-module-central", "freeze", "--incident", str(incident_path), "--apply"])
            self.assertEqual(code, 0, frozen)
            payload = request("READ_LEDGER", project=c08_fixture.PROJECT)
            code, output = route(root, payload)
            self.assertEqual(code, 0, output)
            self.assertEqual(output["routeStatus"], "RECOVERY_OVERRIDE")
            self.assertEqual(output["targetStage"], "C08")
            self.assertEqual(output["recoveryCaseId"], c08_fixture.CASE)

    def test_route_is_read_only_and_deterministic(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"; setup_project(root)
            ledger = root / "module-ledgers" / PROJECT / "ledger.json"
            before = digest(ledger)
            payload = request("READ_LEDGER")
            first_code, first = route(root, payload, "same.json")
            second_code, second = route(root, payload, "same.json")
            self.assertEqual((first_code, second_code), (0, 0))
            self.assertEqual(first, second)
            self.assertEqual(before, digest(ledger))

    def test_missing_project_ledger_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            code, output = route(root, request("READ_LEDGER"))
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "C03_LEDGER_NOT_INITIALIZED")

    def test_non_boss_request_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"; setup_project(root)
            payload = request("READ_LEDGER"); payload["submittedBy"] = "task-window"
            code, output = route(root, payload)
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "C09_REQUEST_MUST_COME_FROM_BOSS")

    def test_missing_context_reference_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"; setup_project(root)
            code, output = route(root, request("GENERATE_TASK_PACKAGE"))
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "C09_TASKID_REQUIRED")

    def test_request_outside_private_root_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary); root = base / "private-data"; setup_project(root)
            outside = write_json(base / "outside.json", request("READ_LEDGER"))
            code, output = invoke(C09, ["--data-root", str(root), "route", "--request", str(outside)])
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "C09_REQUEST_MUST_BE_INSIDE_PRIVATE_DATA_ROOT")


if __name__ == "__main__":
    unittest.main(verbosity=2)
