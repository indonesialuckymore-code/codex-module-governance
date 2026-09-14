"""Incremental handover uses immutable packages, never a second live central."""
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import test_role_continuity_controller as f


def refresh(root, base="handover-base", revision="handover-update"):
    return f.continuity(root, "refresh-handover", "--handover-id", base,
        "--revision-id", revision, "--current-thread-ref", "central-thread-g1")


class HandoverRefreshTests(unittest.TestCase):
    def test_receipt_written_before_routing_replace_can_finish_same_commit(self):
        import argparse
        from unittest.mock import patch
        import role_continuity_controller as c08
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'private'; f.setup(root)
            _, _, package = f.prepare_handover(root, 'handover-crash', 'CURRENT_CENTRAL', 'REPLACE_ACTIVE_ROLE')
            value = {'activationSchemaVersion': '0.15.0', 'recordType': 'C08_ROLE_SUCCESSOR_ACTIVATION',
                'handoverId': 'handover-crash', 'projectId': f.PROJECT, 'role': 'CURRENT_CENTRAL',
                'successorThreadRef': 'central-thread-g2', 'runtimeProjectId': 'runtime-project-001',
                'model': 'gpt-5.6-sol', 'handoverPackageDigest': f.digest(json.loads(package.read_text()))}
            path = f.write(root / 'inputs/crash-activation.json', value)
            args = argparse.Namespace(data_root=str(root), config=None, project_id=f.PROJECT,
                writer_id='codex-module-central', handover_id='handover-crash', activation=str(path))
            with patch.object(c08, 'replace_json', side_effect=OSError('fictional interrupted routing replace')):
                with self.assertRaises(OSError): c08.activate_successor(args)
            self.assertEqual('central-thread-g1', c08.verify_routing(root, f.PROJECT)['roles']['CURRENT_CENTRAL']['activeThreadRef'])
            result, code = c08.activate_successor(args)
            self.assertEqual(0, code, result)
            self.assertEqual(2, c08.verify_routing(root, f.PROJECT)['revision'])
            self.assertFalse(c08.activate_successor(args)[0]['writePerformed'])

    def test_switching_central_model_does_not_break_refresh_or_role_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; f.setup(root)
            _, _, base = f.prepare_handover(root, "handover-base", "CURRENT_CENTRAL", "REPLACE_ACTIVE_ROLE")
            code, result = f.continuity(root, "record-model", "--current-thread-ref", "central-thread-g1",
                "--runtime-project-id", "runtime-project-001", "--model", "gpt-5.6-terra",
                "--evidence-ref", "central-thread-g1")
            self.assertEqual(0, code, result)
            code, result = refresh(root)
            self.assertEqual(0, code, result)
            updated = json.loads((base.parent.parent / "handover-update/handover-package.json").read_text())
            central = updated["routingSnapshot"]["roles"]["CURRENT_CENTRAL"]
            self.assertEqual(1, central["generation"])
            self.assertEqual("gpt-5.6-terra", central["model"])

    def test_retry_does_not_accept_corrupted_activation_result(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; f.setup(root)
            _, _, base = f.prepare_handover(root, "handover-base", "CURRENT_CENTRAL", "REPLACE_ACTIVE_ROLE")
            self.assertEqual(0, f.activate(root, "handover-base", "CURRENT_CENTRAL", "central-thread-g2", base)[0])
            path = base.parent / "activation.json"
            value = json.loads(path.read_text()); value["successorThreadRef"] = "wrong-successor"
            f.write(path, value)
            code, result = f.activate(root, "handover-base", "CURRENT_CENTRAL", "central-thread-g2", base)
            self.assertEqual(2, code, result)
            self.assertEqual("C08C_ACTIVATION_RESULT_CONFLICT", result["reason"])

    def test_two_successors_competing_for_one_package_only_activate_once(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; f.setup(root)
            _, _, base = f.prepare_handover(root, "handover-base", "CURRENT_CENTRAL", "REPLACE_ACTIVE_ROLE")
            paths = []
            for successor in ("successor-one", "successor-two"):
                paths.append(f.write(root / f"inputs/{successor}.json", {
                    "activationSchemaVersion": "0.15.0", "recordType": "C08_ROLE_SUCCESSOR_ACTIVATION",
                    "handoverId": "handover-base", "projectId": f.PROJECT, "role": "CURRENT_CENTRAL",
                    "successorThreadRef": successor, "runtimeProjectId": "runtime-project-001",
                    "model": "gpt-5.6-terra", "handoverPackageDigest": f.digest(json.loads(base.read_text()))}))
            def run(path):
                return f.continuity(root, "activate-successor", "--handover-id", "handover-base", "--activation", str(path))
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(run, paths))
            self.assertEqual([0, 2], sorted(code for code, _ in results), results)
            routing = json.loads((base.parent.parent.parent / "routing.json").read_text())
            self.assertEqual(2, routing["revision"])
            self.assertEqual(2, routing["roles"]["CURRENT_CENTRAL"]["generation"])

    def test_no_change_needs_no_new_package_and_repeated_refresh_reports_staleness(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; f.setup(root)
            _, _, base = f.prepare_handover(root, "handover-base", "CURRENT_CENTRAL", "REPLACE_ACTIVE_ROLE")
            code, result = refresh(root)
            self.assertEqual(0, code, result); self.assertFalse(result["writePerformed"])
            self.assertEqual("handover-base", result["handoverId"])
            self.assertFalse((base.parent.parent / "handover-update").exists())
            f.c03(root, "record-wait", "--task-id", f.TASK, "--wait-id", "wait-material",
                "--owner-ref", "owner-material", "--condition-ref", "condition-material", "--reason-ref", "reason-material")
            self.assertEqual(0, refresh(root)[0])
            updated = base.parent.parent / "handover-update/handover-package.json"
            package = json.loads(updated.read_text())
            self.assertEqual("WAITING", package["ledgerSnapshot"]["taskContinuity"][f.TASK]["externalWaits"]["wait-material"]["status"])
            f.c03(root, "record-correction", "--task-id", f.TASK, "--correction-id", "direction-new",
                "--expected-revision", "0", "--instruction-ref", "new-instruction", "--reason-ref", "new-focus",
                "--boss-decision-ref", "boss-correction", "--retain-ref", "original-contract", "--supersede-ref", "old-emphasis")
            self.assertFalse(refresh(root)[1]["snapshotCurrent"])
            self.assertEqual(2, f.activate(root, "handover-update", "CURRENT_CENTRAL", "central-thread-g2", updated)[0])
            code, result = refresh(root, "handover-update", "handover-final")
            self.assertEqual(0, code, result)
            final = json.loads((base.parent.parent / "handover-final/handover-package.json").read_text())
            self.assertEqual("new-instruction", final["ledgerSnapshot"]["taskContinuity"][f.TASK]["directionCorrections"][0]["instructionRef"])
            self.assertEqual(0, f.continuity(root, "list-pending", "--current-thread-ref", "central-thread-g1")[0])

    def test_old_central_cannot_refresh_after_successor_activation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; f.setup(root)
            _, _, base = f.prepare_handover(root, "handover-base", "CURRENT_CENTRAL", "REPLACE_ACTIVE_ROLE")
            self.assertEqual(0, f.activate(root, "handover-base", "CURRENT_CENTRAL", "central-thread-g2", base)[0])
            self.assertEqual(2, refresh(root)[0])
            self.assertFalse((base.parent.parent / "handover-update").exists())

    def test_removed_pending_event_is_carried_as_acknowledged_not_lost(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; f.setup(root)
            event = f.write(root / "inputs/event.json", f.event_payload())
            self.assertEqual(0, f.continuity(root, "submit-event", "--event", str(event))[0])
            _, _, base = f.prepare_handover(root, "handover-base", "CURRENT_CENTRAL", "REPLACE_ACTIVE_ROLE")
            self.assertEqual(0, f.continuity(root, "acknowledge-event", "--event-id", "event-c003-001",
                "--current-thread-ref", "central-thread-g1", "--acknowledgement-ref", "acknowledged-event")[0])
            code, result = refresh(root)
            self.assertEqual(0, code, result)
            self.assertEqual(["event-c003-001"], result["delta"]["removedPendingEventIds"])

    def test_successor_in_another_project_is_refused_without_switching(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; f.setup(root)
            _, _, base = f.prepare_handover(root, "handover-base", "CURRENT_CENTRAL", "REPLACE_ACTIVE_ROLE")
            payload = {"activationSchemaVersion": "0.15.0", "recordType": "C08_ROLE_SUCCESSOR_ACTIVATION",
                "handoverId": "handover-base", "projectId": f.PROJECT, "role": "CURRENT_CENTRAL",
                "successorThreadRef": "central-thread-g2", "runtimeProjectId": "another-project",
                "model": "gpt-5.6-terra", "handoverPackageDigest": f.digest(json.loads(base.read_text()))}
            path = f.write(root / "inputs/activation.json", payload)
            code, result = f.continuity(root, "activate-successor", "--handover-id", "handover-base", "--activation", str(path))
            self.assertEqual(2, code, result)
            self.assertEqual("C08C_SUCCESSOR_PROJECT_MISMATCH", result["reason"])
            self.assertEqual(0, f.continuity(root, "list-pending", "--current-thread-ref", "central-thread-g1")[0])

    def test_activation_recovers_missing_result_without_another_generation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; f.setup(root)
            _, _, base = f.prepare_handover(root, "handover-base", "CURRENT_CENTRAL", "REPLACE_ACTIVE_ROLE")
            self.assertEqual(0, f.activate(root, "handover-base", "CURRENT_CENTRAL", "central-thread-g2", base)[0])
            # Simulate a process interruption after the durable routing commit.
            (base.parent / "activation.json").unlink()
            (base.parent / "receipt-000001-activation.json").unlink()
            routing_path = base.parent.parent.parent / "routing.json"
            before = routing_path.read_bytes()
            code, result = f.activate(root, "handover-base", "CURRENT_CENTRAL", "central-thread-g2", base)
            self.assertEqual(0, code, result)
            self.assertEqual(before, routing_path.read_bytes())
            self.assertTrue((base.parent / "activation.json").is_file())
            self.assertEqual(2, f.activate(root, "handover-base", "CURRENT_CENTRAL", "another-successor", base)[0])

    def test_late_event_refresh_keeps_base_and_can_activate_existing_successor(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; f.setup(root)
            _, _, base = f.prepare_handover(root, "handover-base", "CURRENT_CENTRAL", "REPLACE_ACTIVE_ROLE")
            original = base.read_bytes()
            event = f.write(root / "inputs/event.json", f.event_payload())
            self.assertEqual(0, f.continuity(root, "submit-event", "--event", str(event))[0])
            self.assertEqual(2, f.activate(root, "handover-base", "CURRENT_CENTRAL", "central-thread-g2", base)[0])
            code, result = refresh(root)
            self.assertEqual(0, code, result)
            self.assertEqual(original, base.read_bytes())
            updated = base.parent.parent / "handover-update/handover-package.json"
            package = json.loads(updated.read_text())
            self.assertEqual(["event-c003-001"], package["delta"]["addedPendingEventIds"])
            self.assertFalse(result["roleSwitched"])
            self.assertFalse(refresh(root)[1]["writePerformed"])
            code, result = f.activate(root, "handover-update", "CURRENT_CENTRAL", "central-thread-g2", updated)
            self.assertEqual(0, code, result)
            pending = f.continuity(root, "list-pending", "--current-thread-ref", "central-thread-g2")[1]
            self.assertIn("event-c003-001", json.dumps(pending))


if __name__ == "__main__":
    unittest.main()
