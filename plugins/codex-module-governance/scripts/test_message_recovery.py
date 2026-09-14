"""Recover interrupted communication using fictional fixtures, not native sends."""
import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import task_communication_bridge as bridge
from test_task_communication_bridge import (
    PROJECT, TASK, WINDOW, CENTRAL_1, CENTRAL_2, setup, write, c14, receipt, replace_central,
)


def acknowledged_command(root):
    command = {"requestSchemaVersion": "0.19.0", "recordType": "C14_CENTRAL_TO_TASK_REQUEST",
               "messageId": "correction-notice", "projectId": PROJECT,
               "taskIdentity": {"taskId": TASK, "canonicalTitle": "C-014｜双向通信"},
               "windowId": WINDOW, "commandId": "direction-1", "commandType": "TASK_INSTRUCTION",
               "commandRef": "private-direction-1", "commandDigest": "b" * 64,
               "summary": "Read the revised execution instruction."}
    code, output = c14(root, "enqueue-central-command", "--current-thread-ref", CENTRAL_1,
                       "--request", str(write(root / "inputs/command.json", command)), writer=True)
    if code: raise AssertionError(output)
    code, ready = c14(root, "prepare-delivery", "--message-id", "correction-notice", "--current-thread-ref", CENTRAL_1)
    if code: raise AssertionError(ready)
    sent = receipt(root, "correction-notice", CENTRAL_1, WINDOW, ready["payloadDigest"], "delivery-1")
    for command, extra in [("record-delivery", ["--receipt", str(sent)]),
                           ("acknowledge", ["--acknowledgement-ref", "ack-1"])]:
        code, output = c14(root, command, "--message-id", "correction-notice",
                           "--current-thread-ref", CENTRAL_1 if command == "record-delivery" else WINDOW, *extra)
        if code: raise AssertionError(output)
    result = {"resultSchemaVersion": "0.19.0", "recordType": "C14_TASK_COMMAND_RESULT",
              "resultId": "direction-result-1", "projectId": PROJECT, "messageId": "correction-notice",
              "commandId": "direction-1", "outcome": "APPLIED", "resultRef": "private-result-1",
              "summary": "Task reports the instruction applied; independent verification is pending."}
    return argparse.Namespace(data_root=str(root), config=None, project_id=PROJECT,
                              message_id="correction-notice", current_thread_ref=WINDOW,
                              result=str(write(root / "inputs/result.json", result))), sent


class MessageRecoveryTests(unittest.TestCase):
    def test_completed_result_retry_survives_central_replacement(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root)
            args, _ = acknowledged_command(root)
            bridge.record_command_result(args)
            before = bridge.envelope_path(root, PROJECT, "result-correction-notice").read_bytes()
            replace_central(root)
            output, code = bridge.record_command_result(args)
            self.assertEqual(code, 0, output)
            self.assertFalse(output["writePerformed"])
            self.assertEqual(before, bridge.envelope_path(root, PROJECT, "result-correction-notice").read_bytes())
            code, ready = c14(root, "prepare-delivery", "--message-id", "result-correction-notice", "--current-thread-ref", WINDOW)
            self.assertEqual(code, 0, ready)
            self.assertEqual(ready["targetThreadRef"], CENTRAL_2)

    def test_changed_result_cannot_replace_the_original(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root)
            args, _ = acknowledged_command(root)
            bridge.record_command_result(args)
            before = bridge.command_result_path(root, PROJECT, args.message_id).read_bytes()
            altered = json.loads(Path(args.result).read_text()); altered["outcome"] = "BLOCKED"
            write(Path(args.result), altered)
            with self.assertRaisesRegex(bridge.BridgeError, "C14_COMMAND_RESULT_ALREADY_RECORDED"):
                bridge.record_command_result(args)
            self.assertEqual(before, bridge.command_result_path(root, PROJECT, args.message_id).read_bytes())

    def test_corrupt_partial_receipt_is_not_used_for_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root)
            args, _ = acknowledged_command(root)
            with patch.object(bridge, "write_envelope", side_effect=OSError("interrupted")):
                with self.assertRaises(OSError): bridge.record_command_result(args)
            sealed = bridge.envelope_receipt_path(root, PROJECT, "result-correction-notice")
            write(sealed, {"artifact": {"payload": "wrong"}, "artifactDigest": "a" * 64})
            before = sealed.read_bytes()
            with self.assertRaisesRegex(bridge.BridgeError, "C14_ENVELOPE_INTEGRITY_INVALID"):
                bridge.record_command_result(args)
            self.assertEqual(before, sealed.read_bytes())
            self.assertFalse(bridge.envelope_path(root, PROJECT, "result-correction-notice").exists())

    def test_changed_delivery_receipt_cannot_replace_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root)
            _, sent = acknowledged_command(root)
            altered = json.loads(sent.read_text()); altered["outcome"] = "FAILED"
            write(sent, altered)
            code, output = c14(root, "record-delivery", "--message-id", "correction-notice",
                               "--current-thread-ref", CENTRAL_1, "--receipt", str(sent))
            self.assertEqual(code, 2, output)
            self.assertEqual(output["reason"], "C14_DELIVERY_ID_REUSED")
            self.assertEqual(bridge.message_state(root, PROJECT, "correction-notice"), "ACKNOWLEDGED")

    def test_successful_delivery_receipt_can_be_replayed_without_resending(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root)
            _, sent = acknowledged_command(root)
            code, output = c14(root, "record-delivery", "--message-id", "correction-notice",
                               "--current-thread-ref", CENTRAL_1, "--receipt", str(sent))
            self.assertEqual(code, 0, output)
            self.assertEqual(output["status"], "IDEMPOTENT_RUNTIME_MESSAGE_DELIVERY")
            self.assertFalse(output["writePerformed"])
            self.assertEqual(output["messageState"], "ACKNOWLEDGED")

    def test_retry_recovers_envelope_from_sealed_enqueue_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root)
            args, _ = acknowledged_command(root)
            original_write = bridge.write_exclusive
            destination = bridge.envelope_path(root, PROJECT, "result-correction-notice")
            def fail_envelope(path, artifact):
                if path.resolve() == destination.resolve(): raise OSError("simulated interruption after receipt")
                return original_write(path, artifact)
            with patch.object(bridge, "write_exclusive", side_effect=fail_envelope):
                with self.assertRaises(OSError): bridge.record_command_result(args)
            sealed = bridge.envelope_receipt_path(root, PROJECT, "result-correction-notice").read_bytes()
            output, code = bridge.record_command_result(args)
            self.assertEqual(code, 0, output)
            self.assertTrue(output["writePerformed"])
            self.assertEqual(sealed, bridge.envelope_receipt_path(root, PROJECT, "result-correction-notice").read_bytes())
            self.assertEqual(bridge.load_envelope(root, PROJECT, "result-correction-notice")["payload"]["outcome"], "APPLIED")

    def test_result_retry_repairs_missing_notification_without_rewriting_result(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root)
            args, _ = acknowledged_command(root)
            ledger = (root / "module-ledgers" / PROJECT / "ledger.json").read_bytes()
            with patch.object(bridge, "write_envelope", side_effect=OSError("simulated interrupted write")):
                with self.assertRaises(OSError): bridge.record_command_result(args)
            result = bridge.command_result_path(root, PROJECT, args.message_id).read_bytes()
            output, code = bridge.record_command_result(args)
            self.assertEqual(code, 0, output)
            recovered = bridge.load_envelope(root, PROJECT, "result-correction-notice")
            self.assertEqual(recovered["payload"]["resultId"], "direction-result-1")
            self.assertTrue(output["writePerformed"])
            self.assertEqual(result, bridge.command_result_path(root, PROJECT, args.message_id).read_bytes())
            self.assertFalse(bridge.record_command_result(args)[0]["writePerformed"])
            self.assertEqual(ledger, (root / "module-ledgers" / PROJECT / "ledger.json").read_bytes())


if __name__ == "__main__": unittest.main()
