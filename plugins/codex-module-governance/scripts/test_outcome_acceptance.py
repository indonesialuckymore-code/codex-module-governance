import json
import tempfile
import unittest
from pathlib import Path
import test_independent_handover_validator as f
from test_outline_outcomes import handoff
import independent_handover_validator as validator


class OutcomeAcceptanceTests(unittest.TestCase):
    def test_full_outcome_chain_requires_boss_approval_and_exposes_later_feedback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'private'; f.setup_ready_for_validation(root, outline_handoff=handoff())
            handback = f.create_handback(root)
            path = f.create_review(root)
            value = json.loads(path.read_text())
            value['outcomeAssessments'] = [{'outcomeId': 'outcome-001', 'criterionId': 'criterion-001',
                'reference': 'evidence-after', 'verdict': 'PASS', 'independentlyReadBack': True}]
            f.write_json(path, value)
            code, result = f.IndependentHandoverValidatorTests().assess(root, handback, path, '--apply', 'codex-module-central')
            self.assertEqual(0, code, result)
            self.assertNotEqual('ACCEPTED', f.c03(root, 'read-summary')[1]['businessProgress']['outcomes'][0]['status'])
            code, result = f.invoke(f.C06, ['--data-root', str(root), '--project-id', f.PROJECT, '--writer-id', 'codex-module-central',
                'finalize', '--validation-id', f.VALIDATION, '--boss-decision', 'APPROVED', '--boss-decision-ref', 'boss-outcome-approved'])
            self.assertEqual(0, code, result)
            self.assertEqual('ACCEPTED', f.c03(root, 'read-summary')[1]['businessProgress']['outcomes'][0]['status'])
            code, result = f.c03(root, 'record-feedback', '--task-id', f.TASK, '--feedback-id', 'feedback-new',
                '--validation-id', f.VALIDATION, '--issue-ref', 'new-issue', '--affected-ref', 'outcome-001')
            self.assertEqual(0, code, result)
            summary = f.c03(root, 'read-summary')[1]
            self.assertEqual('DONE', summary['tasks'][f.TASK])
            self.assertEqual('NEEDS_REVIEW', summary['businessProgress']['outcomes'][0]['status'])

    def test_business_criteria_require_complete_independent_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            handback = json.loads(f.create_handback(root).read_text())
            review = json.loads(f.create_review(root).read_text())
            contract = {'digest': 'a' * 64, 'outcomes': handoff()['outcomes']}
            with self.assertRaisesRegex(validator.HandoverValidationError, 'OUTCOME'):
                validator.validate_review(review, handback, outcome_contract=contract)
            review['outcomeAssessments'] = [{'outcomeId': 'outcome-001', 'criterionId': 'criterion-001',
                'reference': 'evidence-after', 'verdict': 'PASS', 'independentlyReadBack': True}]
            normalized = validator.validate_review(review, handback, outcome_contract=contract)
            self.assertEqual('PASS_PENDING_BOSS_APPROVAL', validator.derive_outcome(normalized, handback))
            review['outcomeAssessments'][0]['verdict'] = 'FAIL'
            normalized = validator.validate_review(review, handback, outcome_contract=contract)
            self.assertEqual('PARTIAL', validator.derive_outcome(normalized, handback))
            review['outcomeAssessments'][0].update(verdict='PASS', independentlyReadBack=False)
            normalized = validator.validate_review(review, handback, outcome_contract=contract)
            self.assertEqual('NEEDS_REVIEW', validator.derive_outcome(normalized, handback))
            review['outcomeAssessments'][0]['criterionId'] = 'unapproved-criterion'
            with self.assertRaisesRegex(validator.HandoverValidationError, 'OUTCOME'):
                validator.validate_review(review, handback, outcome_contract=contract)


if __name__ == '__main__': unittest.main()
