"""User-visible model choice through existing governance command interfaces."""
import json
import tempfile
import unittest
from pathlib import Path

import test_task_window_dispatch_controller as dispatch
import test_role_continuity_controller as continuity


class RuntimeModelFlexibility(unittest.TestCase):
    def test_manual_child_registration_does_not_invent_a_runtime_model(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            dispatch.fixture.setup_project(root)
            self.assertEqual(0, dispatch.c03(root, "register-window", "--window-id", "window-manual",
                "--task-id", dispatch.fixture.TASK_ID, "--context-mode", "NEW")[0])
            code, result = dispatch.c03(root, "register-sub-agent", "--sub-agent-id", "agent-manual",
                "--window-id", "window-manual", "--role", "Read fictional references")
            self.assertEqual(0, code, result)
            path = root / "module-ledgers" / dispatch.fixture.PROJECT_ID / "ledger.json"
            self.assertEqual("UNVERIFIED", json.loads(path.read_text())["subAgents"]["agent-manual"]["model"])

    def test_manual_fallback_preserves_explicit_model_and_requires_package(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            dispatch.setup_ready(root)
            request = dispatch.dispatch_request()
            request["model"] = "gpt-5.6-luna"
            self.assertEqual(0, dispatch.prepare(root, request)[0])
            code, result = dispatch.confirm(root, dispatch.confirmation(model="gpt-5.6-luna", model_method="MANUAL_UI_MODEL_SELECTION_EVIDENCE"))
            self.assertNotEqual(0, code, result)
            code, result = dispatch.export_fallback(root)
            self.assertEqual(0, code, result)
            package = json.loads(Path(result["artifact"]).read_text())
            self.assertEqual("gpt-5.6-luna", package["modelEnforcement"]["requiredModel"])
            self.assertIn("gpt-5.6-luna", package["copyablePrompt"])
            code, result = dispatch.confirm(root, dispatch.confirmation(model="gpt-5.6-luna", model_method="MANUAL_UI_MODEL_SELECTION_EVIDENCE"))
            self.assertEqual(0, code, result)

    def test_current_central_model_readback_preserves_generation_and_ledger(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            continuity.setup(root)
            ledger_path = root / "module-ledgers" / continuity.PROJECT / "ledger.json"
            before = ledger_path.read_bytes()
            code, result = continuity.continuity(root, "record-model", "--current-thread-ref", "central-thread-g1",
                "--runtime-project-id", "runtime-project-001", "--model", "gpt-5.6-luna", "--evidence-ref", "central-thread-g1")
            self.assertEqual(0, code, result)
            routing = json.loads((root / "role-continuity" / continuity.PROJECT / "routing.json").read_text())
            central = routing["roles"]["CURRENT_CENTRAL"]
            self.assertEqual(1, central["generation"])
            self.assertEqual("gpt-5.6-luna", central["model"])
            self.assertEqual("central-thread-g1", central["activeThreadRef"])
            self.assertEqual(before, ledger_path.read_bytes())
            code, result = continuity.continuity(root, "record-model", "--current-thread-ref", "not-current-thread",
                "--runtime-project-id", "runtime-project-001", "--model", "gpt-5.6-sol", "--evidence-ref", "not-current-thread")
            self.assertNotEqual(0, code, result)

    def test_selected_child_model_is_recorded_without_relabeling_as_terra(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            dispatch.setup_ready(root)
            specs = [dispatch.agent_spec("agent-flex-001")]
            specs[0]["model"] = "gpt-5.6-luna"
            self.assertEqual(0, dispatch.prepare(root, dispatch.dispatch_request(specs))[0])
            agent = {"subAgentId": "agent-flex-001", "status": "CREATED", "runtimeAgentRef": "runtime-flex-001",
                "modelControl": dispatch.model_control("NATIVE_SPAWN_AGENT_MODEL_PARAMETER", "runtime-flex-001", "gpt-5.6-luna")}
            code, result = dispatch.confirm(root, dispatch.confirmation(agents=[agent]))
            self.assertEqual(0, code, result)
            ledger = json.loads((root / "module-ledgers" / dispatch.fixture.PROJECT_ID / "ledger.json").read_text())
            self.assertEqual("gpt-5.6-luna", ledger["subAgents"]["agent-flex-001"]["runtimeModel"])

    def test_new_central_can_use_a_different_model_without_changing_tasks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            continuity.setup(root)
            code, result, package_path = continuity.prepare_handover(root, "handover-flex-model", "CURRENT_CENTRAL", "REPLACE_ACTIVE_ROLE")
            self.assertEqual(0, code, result)
            package = json.loads(package_path.read_text())
            activation = {"activationSchemaVersion": "0.15.0", "recordType": "C08_ROLE_SUCCESSOR_ACTIVATION",
                "handoverId": "handover-flex-model", "projectId": continuity.PROJECT, "role": "CURRENT_CENTRAL",
                "successorThreadRef": "central-thread-g2", "runtimeProjectId": "runtime-project-001",
                "model": "gpt-5.6-terra", "handoverPackageDigest": continuity.digest(package)}
            path = continuity.write(root / "activation-flex.json", activation)
            ledger_path = root / "module-ledgers" / continuity.PROJECT / "ledger.json"
            before = ledger_path.read_bytes()
            code, result = continuity.continuity(root, "activate-successor", "--handover-id", "handover-flex-model", "--activation", str(path))
            self.assertEqual(0, code, result)
            routing = json.loads((root / "role-continuity" / continuity.PROJECT / "routing.json").read_text())
            self.assertEqual("gpt-5.6-terra", routing["roles"]["CURRENT_CENTRAL"]["model"])
            self.assertEqual(before, ledger_path.read_bytes())

    def test_reused_window_keeps_selected_model_without_send_override(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            dispatch.fixture.setup_project(root)
            code, result = dispatch.c03(root, "register-window", "--window-id", "window-existing", "--task-id", dispatch.fixture.TASK_ID,
                "--context-mode", "NEW", "--runtime-model", "gpt-5.6-sol", "--runtime-model-evidence-ref", "manual-model-existing",
                "--runtime-permission-profile", "disabled", "--runtime-permission-evidence-ref", "manual-permission-existing",
                "--governance-data-root-access", "NOT_RESTRICTED")
            self.assertEqual(0, code, result)
            review = dispatch.fixture.create_review(root)
            code, result = dispatch.invoke(dispatch.C05, ["--data-root", str(root), "--project-id", dispatch.fixture.PROJECT_ID,
                "--writer-id", "codex-module-central", "evaluate", "--package-id", dispatch.fixture.PACKAGE_ID,
                "--review", str(review), "--apply"])
            self.assertEqual(0, code, result)
            self.assertEqual("gpt-5.6-sol", result["windowDecision"]["model"])
            code, result = dispatch.prepare(root, dispatch.dispatch_request())
            self.assertEqual(0, code, result)
            self.assertEqual("gpt-5.6-sol", result["windowAction"]["model"])
            self.assertIsNone(result["nativeRuntime"]["windowOperation"]["requiredModel"])
            code, result = dispatch.confirm(root, dispatch.confirmation(window_id="window-existing", reused=True,
                model="gpt-5.6-sol", model_method="RUNTIME_MODEL_READBACK", permission_profile="disabled"))
            self.assertEqual(0, code, result)

    def test_explicit_task_model_is_sent_to_native_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            dispatch.setup_ready(root)
            request = dispatch.dispatch_request()
            request["model"] = "gpt-5.6-luna"
            code, result = dispatch.prepare(root, request)
            self.assertEqual(0, code, result)
            self.assertEqual("gpt-5.6-luna", result["windowAction"]["model"])
            self.assertEqual("gpt-5.6-luna", result["nativeRuntime"]["windowOperation"]["requiredModel"])

    def test_task_accepts_selected_model_and_records_actual_model(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            dispatch.setup_ready(root)
            code, result = dispatch.prepare(root, dispatch.dispatch_request())
            self.assertEqual(0, code, result)
            code, result = dispatch.confirm(root, dispatch.confirmation(model="gpt-5.6-sol"))
            self.assertEqual(0, code, result)
            ledger = json.loads((root / "module-ledgers" / dispatch.fixture.PROJECT_ID / "ledger.json").read_text())
            window = ledger["windows"]["window-c10-001"]
            self.assertEqual("gpt-5.6-sol", window["model"])
            self.assertEqual(window["model"], window["runtimeModel"])
            self.assertEqual(window["model"], window["modelEnforcement"]["model"])
            self.assertEqual("IN_PROGRESS", ledger["tasks"][dispatch.fixture.TASK_ID]["status"])


if __name__ == "__main__":
    unittest.main()
