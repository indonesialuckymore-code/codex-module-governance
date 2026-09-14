"""Fictional native boundary receipts exercise the complete C08 handover chain."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
import test_role_continuity_controller as f
from handover_automation import READBACK_CHECKS, communication_snapshot


def proof(root, name, value):
    raw = f.write(root / f"native-evidence/{name}.json", {"fictionalNativeReadback": name, "observed": value})
    return {"reference": name, "file": str(raw), "sha256": hashlib.sha256(raw.read_bytes()).hexdigest()}


def setup(root):
    f.setup(root)
    _, _, package = f.prepare_handover(root, "handover-auto", "CURRENT_CENTRAL", "REPLACE_ACTIVE_ROLE")
    code, created = f.continuity(root, "prepare-successor", "--handover-id", "handover-auto",
        "--current-thread-ref", "central-thread-g1", "--authorization-ref", "boss-requested-central-handover")
    if code: raise AssertionError(created)
    value = {"creationKey": created["intent"]["creationKey"], "sourceThreadRef": "central-thread-g1",
        "successorThreadRef": "central-thread-g2", "runtimeProjectId": "runtime-project-001",
        "model": "gpt-5.6-sol", "permissionProfile": "full-access"}
    value["evidence"] = proof(root, "native-successor", value)
    path = f.write(root / "inputs/successor.json", value)
    code, result = f.continuity(root, "record-successor", "--handover-id", "handover-auto",
        "--current-thread-ref", "central-thread-g1", "--proof", str(path))
    if code: raise AssertionError(result)
    return package


def readback(root, package):
    value = {"handoverPackageDigest": f.digest(json.loads(package.read_text())), "successorThreadRef": "central-thread-g2",
        "communicationDigest": f.digest(communication_snapshot(root, f.PROJECT)), "checks": dict.fromkeys(READBACK_CHECKS, True)}
    value["evidence"] = proof(root, "context-readback", value)
    return f.write(root / "inputs/readback.json", value)


class HandoverAutomationTests(unittest.TestCase):
    def test_fork_first_message_explicitly_preserves_observed_source_model(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; f.setup(root)
            code, result = f.continuity(root, "record-model", "--current-thread-ref", "central-thread-g1",
                "--runtime-project-id", "runtime-project-001", "--model", "gpt-5.6-sol", "--evidence-ref", "central-thread-g1")
            self.assertEqual(0, code, result)
            f.prepare_handover(root, "handover-model", "CURRENT_CENTRAL", "REPLACE_ACTIVE_ROLE")
            code, result = f.continuity(root, "prepare-successor", "--handover-id", "handover-model",
                "--current-thread-ref", "central-thread-g1", "--authorization-ref", "boss-handover")
            self.assertEqual(0, code, result)
            self.assertEqual("gpt-5.6-sol", result["successorFirstTurn"]["arguments"]["model"])
            self.assertNotIn("model", result["nativeAction"]["arguments"])
            self.assertTrue(result["successorFirstTurn"]["requiresActualModelReadback"])

    def test_native_binding_context_activation_and_open_complete_without_duplicate_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; package = setup(root)
            code, result = f.continuity(root, "prepare-successor", "--handover-id", "handover-auto",
                "--current-thread-ref", "central-thread-g1", "--authorization-ref", "boss-requested-central-handover")
            self.assertEqual(0, code, result); self.assertEqual("SUCCESSOR_ALREADY_BOUND", result["status"])
            self.assertNotIn("nativeAction", result)
            self.assertEqual(2, f.activate(root, "handover-auto", "CURRENT_CENTRAL", "central-thread-g2", package)[0])
            path = readback(root, package)
            code, result = f.continuity(root, "record-handover-readback", "--handover-id", "handover-auto",
                "--current-thread-ref", "central-thread-g2", "--proof", str(path))
            self.assertEqual(0, code, result)
            self.assertEqual(0, f.activate(root, "handover-auto", "CURRENT_CENTRAL", "central-thread-g2", package)[0])
            opened = {"successorThreadRef": "central-thread-g2", "opened": True}
            opened["evidence"] = proof(root, "native-navigation", opened)
            path = f.write(root / "inputs/opened.json", opened)
            args = ("record-handover-opened", "--handover-id", "handover-auto", "--current-thread-ref", "central-thread-g1", "--proof", str(path))
            code, result = f.continuity(root, *args)
            self.assertEqual(0, code, result); self.assertEqual("CENTRAL_HANDOVER_COMPLETE", result["status"])
            self.assertFalse(f.continuity(root, *args)[1]["writePerformed"])

    def test_unknown_source_never_invents_a_default_and_user_can_select_successor_model(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; f.setup(root)
            f.prepare_handover(root, "handover-model", "CURRENT_CENTRAL", "REPLACE_ACTIVE_ROLE")
            args = ("prepare-successor", "--handover-id", "handover-model",
                "--current-thread-ref", "central-thread-g1", "--authorization-ref", "boss-handover")
            code, result = f.continuity(root, *args)
            self.assertEqual(0, code, result)
            self.assertTrue(result["successorFirstTurn"]["requiresSourceModelReadback"])
            self.assertNotIn("model", result["successorFirstTurn"]["arguments"])
            code, selected = f.continuity(root, *args, "--model", "gpt-5.6-terra")
            self.assertEqual(0, code, selected)
            self.assertEqual("RECONCILE_EXISTING_SUCCESSOR", selected["status"])
            self.assertEqual({"model": "gpt-5.6-terra"}, selected["successorFirstTurn"]["arguments"])
            self.assertEqual("USER_SELECTED", selected["successorFirstTurn"]["modelSelection"])
            self.assertEqual(2, f.continuity(root, *args, "--model", "UNVERIFIED")[0])

    def test_uncertain_creation_is_not_issued_again(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; f.setup(root)
            f.prepare_handover(root, "handover-auto", "CURRENT_CENTRAL", "REPLACE_ACTIVE_ROLE")
            args = ("prepare-successor", "--handover-id", "handover-auto", "--current-thread-ref", "central-thread-g1", "--authorization-ref", "boss-handover")
            self.assertEqual("CREATE_SUCCESSOR_ONCE", f.continuity(root, *args)[1]["status"])
            second = f.continuity(root, *args)[1]
            self.assertEqual("RECONCILE_EXISTING_SUCCESSOR", second["status"])
            self.assertNotEqual("fork_thread", second["nativeAction"]["tool"])

    def test_missing_context_check_wrong_reader_and_changed_file_refuse(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; package = setup(root)
            path = readback(root, package)
            args = ("record-handover-readback", "--handover-id", "handover-auto", "--current-thread-ref", "central-thread-g1", "--proof", str(path))
            self.assertEqual(2, f.continuity(root, *args)[0])
            value = json.loads(path.read_text()); value["checks"]["WAITS_AND_CORRECTIONS"] = False
            f.write(path, value)
            self.assertEqual(2, f.continuity(root, *args[:4], "central-thread-g2", *args[5:])[0])
            value['checks']['WAITS_AND_CORRECTIONS'] = True
            Path(value['evidence']['file']).write_text('Changed bytes after readback')
            f.write(path, value)
            self.assertEqual(2, f.continuity(root, *args[:4], "central-thread-g2", *args[5:])[0])


if __name__ == "__main__":
    unittest.main()
