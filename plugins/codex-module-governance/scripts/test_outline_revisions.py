import hashlib
import json
import tempfile
import unittest
from pathlib import Path
import test_task_package_generator as f
from test_outline_outcomes import handoff


class OutlineRevisionTests(unittest.TestCase):
    def test_revision_preserves_history_and_holds_only_changed_outcomes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'private'; f.create_project_and_ledger(root)
            base = ['--data-root', str(root), '--project-id', f.PROJECT_ID, '--writer-id', 'codex-module-central']
            value = handoff(); value['projectId'] = f.PROJECT_ID
            other = json.loads(json.dumps(value['outcomes'][0])); other.update(outcomeId='outcome-002', title='Other usable result')
            value['outcomes'].append(other); value['candidateTasks'][0]['outcomeIds'].append('outcome-002')
            outline = root / 'outline.md'; outline.write_text('Approved fictional design')
            value['outline']['sha256'] = hashlib.sha256(outline.read_bytes()).hexdigest()
            path = root / 'handoff.json'; path.write_text(json.dumps(value))
            def call(*args): return f.invoke(f.C03_SCRIPT, [*base, *args])
            self.assertEqual(0, call('import-outline', '--handoff', str(path), '--outline-file', str(outline), '--boss-approval-ref', 'boss-outline-001')[0])
            ledger_path = root / 'module-ledgers' / f.PROJECT_ID / 'ledger.json'
            digest = json.loads(ledger_path.read_text())['outlineContract']['digest']
            for number in ('001', '002'):
                code, result = call('add-task', '--task-id', 'C-'+number, '--title', 'Usable result', '--business-goal', 'Useful outcome', '--plan-ref', 'plan-001', '--outcome-id', 'outcome-'+number)
                self.assertEqual(0, code, result)
            value['outline']['version'] = 'v2'; value['bossApproval']['reference'] = 'boss-outline-002'
            value['outcomes'][0]['completionBoundary'] = 'Changed business result'
            path.write_text(json.dumps(value))
            args = ['revise-outline', '--handoff', str(path), '--outline-file', str(outline), '--boss-approval-ref', 'boss-outline-002', '--expected-digest', digest, '--change-id', 'design-change-001']
            code, result = call(*args); self.assertEqual(0, code, result)
            ledger = json.loads(ledger_path.read_text())
            self.assertIn(digest, ledger['outlineHistory'])
            from ledger_manager import require_direction_ready, LedgerError
            with self.assertRaisesRegex(LedgerError, 'OUTLINE_RECONCILIATION'):
                require_direction_ready(ledger, 'C-001')
            require_direction_ready(ledger, 'C-002')
            self.assertEqual('PLANNED', ledger['tasks']['C-001']['status'])
            self.assertEqual(digest, ledger['tasks']['C-002']['outlineContractDigest'])
            before = ledger_path.read_bytes()
            code, result = call(*args); self.assertEqual(0, code, result)
            self.assertFalse(result['writePerformed']); self.assertEqual(before, ledger_path.read_bytes())
            # A material change reuses unaffected work and retires only the old affected task.
            code, result = call('cancel-unstarted-task', '--task-id', 'C-001', '--boss-decision-ref', 'boss-retire-old-scope', '--reason', 'Superseded by approved design')
            self.assertEqual(0, code, result)
            code, result = call('add-task', '--task-id', 'C-003', '--title', 'Changed result', '--business-goal', 'Current outcome', '--plan-ref', 'plan-current', '--outcome-id', 'outcome-001')
            self.assertEqual(0, code, result)
            from test_task_lifecycle import proof
            resolution = proof(root, {'changeId': 'design-change-001', 'replacementTaskIds': ['C-003'], 'oldExecutionStopped': True})
            args = ['reconcile-outline', '--task-id', 'C-001', '--proof', str(resolution)]
            code, result = call(*args); self.assertEqual(0, code, result)
            self.assertFalse(call(*args)[1]['writePerformed'])
            code, summary = call('read-summary'); self.assertEqual(0, code, summary)
            self.assertEqual('CANCELLED', summary['tasks']['C-001'])
            self.assertEqual('PLANNED', summary['tasks']['C-002'])
            self.assertEqual(2, len(summary['businessProgress']['outcomes']))


if __name__ == '__main__': unittest.main()
