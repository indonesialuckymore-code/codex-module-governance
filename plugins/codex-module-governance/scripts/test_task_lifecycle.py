import hashlib
import json
import tempfile
import unittest
from pathlib import Path
import test_external_waits as wait
from test_task_corrections import CENTRAL_1


def proof(root, value):
    path = root / 'native-followup.json'; path.write_text('Fictional scheduling result, not a live automation')
    value = {**value, 'evidence': {'reference': 'native-result-001', 'file': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}}
    output = root / 'lifecycle-proof.json'; output.write_text(json.dumps(value)); return output


class TaskLifecycleTests(unittest.TestCase):
    def test_cancelled_repair_can_be_retargeted_without_changing_original_report(self):
        import test_delivery_feedback as feedback
        from ledger_manager import feedback_repair_task, feedback_binding
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'private'; feedback.completed(root); f = feedback.fixture
            for key in ('C-repair', 'C-replacement'):
                self.assertEqual(0, f.c03(root, 'add-task', '--task-id', key, '--title', 'Repair result',
                    '--business-goal', 'Fix usable outcome', '--plan-ref', 'approved-repair')[0])
            self.assertEqual(0, feedback.report(root)[0])
            original = f.c03(root, 'read-summary')[1]['deliveryFeedback'][f.TASK][0]
            value = {'feedbackId': 'feedback-missing-case', 'previousRepairTaskId': 'C-repair',
                'repairTaskId': 'C-replacement', 'reasonRef': 'reason-cancelled-repair'}
            path = proof(root, value)
            args = ('retarget-feedback-repair', '--task-id', f.TASK, '--proof', str(path))
            self.assertEqual(2, f.c03(root, *args)[0])
            self.assertEqual(0, f.c03(root, 'cancel-unstarted-task', '--task-id', 'C-repair',
                '--boss-decision-ref', 'boss-replace-repair', '--reason', 'Use replacement repair')[0])
            code, result = f.c03(root, *args); self.assertEqual(0, code, result)
            self.assertFalse(f.c03(root, *args)[1]['writePerformed'])
            entry = f.c03(root, 'read-summary')[1]['deliveryFeedback'][f.TASK][0]
            self.assertEqual(original['repairTaskId'], entry['repairTaskId'])
            self.assertEqual('C-replacement', feedback_repair_task(entry))
            self.assertNotEqual(feedback_binding(original), feedback_binding(entry))

    def test_only_readback_of_matching_wait_and_central_can_bind_followup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'private'; wait.setup(root); wait.wait(root)
            value = {'waitId': 'wait-material', 'conditionRef': 'condition-material-verified',
                'automationId': 'automation-fictional-001', 'targetThreadRef': CENTRAL_1, 'scheduled': True}
            path = proof(root, value)
            args = ('bind-wait-followup', '--task-id', wait.TASK, '--proof', str(path))
            wrong = {**value, 'conditionRef': 'other-condition'}
            proof(root, wrong)
            self.assertEqual(2, wait.command(root, *args)[0])
            proof(root, value)
            code, result = wait.command(root, *args); self.assertEqual(0, code, result)
            self.assertFalse(wait.command(root, *args)[1]['writePerformed'])
            _, summary = wait.command(root, 'read-summary')
            self.assertEqual('SCHEDULED_RECEIPT_RECORDED', summary['externalWaits'][wait.TASK][0]['followupStatus'])
            self.assertEqual('WAITING', summary['externalWaits'][wait.TASK][0]['status'])


if __name__ == '__main__': unittest.main()
