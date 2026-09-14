"""Append-only follow-through operations in C03; no separate project database."""
import hashlib
import json
from pathlib import Path
import ledger_manager as c03


def private_proof(root, raw):
    try:
        path = Path(raw).resolve(strict=True)
        if not path.is_relative_to(root.resolve()): raise ValueError('outside private root')
        value = json.loads(path.read_text())
        proof = value['evidence']; evidence_path = Path(proof['file']).resolve(strict=True)
        if (set(proof) != {'reference', 'file', 'sha256'} or not evidence_path.is_relative_to(root.resolve())
                or hashlib.sha256(evidence_path.read_bytes()).hexdigest() != proof['sha256']):
            raise ValueError('evidence mismatch')
        c03.require_opaque_reference(proof['reference'], 'LIFECYCLE_EVIDENCE_REFERENCE_INVALID')
        return {**value, 'evidence': {'reference': proof['reference'], 'sha256': proof['sha256']}}
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise c03.LedgerError('LIFECYCLE_PRIVATE_PROOF_INVALID') from error


def operate(args):
    root = c03.load_data_root(args); project = c03.require_pattern(args.project_id, c03.PROJECT_ID_PATTERN, 'PROJECT_ID_INVALID')
    writer = c03.require_writer(args.writer_id)
    task_id = c03.require_pattern(args.task_id, c03.TASK_ID_PATTERN, 'TASK_ID_INVALID')
    value = private_proof(root, args.proof)
    for key in ('changeId', 'waitId', 'conditionRef', 'feedbackId', 'previousRepairTaskId', 'repairTaskId'):
        if key in value:
            c03.require_opaque_reference(value[key], 'LIFECYCLE_REFERENCE_INVALID')
    with c03.ledger_lock(root, project):
        c03.require_current_central_thread(root, project, args.caller_thread_ref)
        if c03.verify_ledger(args)[1]: raise c03.LedgerError('LIFECYCLE_LEDGER_UNVERIFIED')
        before = c03.load_ledger(root, project); task = c03.ensure_task(before, task_id)
        if args.command == 'reconcile-outline':
            if set(value) != {'changeId', 'replacementTaskIds', 'oldExecutionStopped', 'evidence'} or value['oldExecutionStopped'] is not True:
                raise c03.LedgerError('OUTLINE_RECONCILIATION_PROOF_INVALID')
            impact = next((item for item in task.get('outlineImpacts', []) if item['changeId'] == value['changeId']), None)
            if not impact: raise c03.LedgerError('OUTLINE_IMPACT_NOT_FOUND')
            if impact.get('resolution') == value:
                return {'status': 'IDEMPOTENT_OUTLINE_RECONCILIATION', 'writePerformed': False}, 0
            if impact['status'] != 'RECONCILIATION_REQUIRED': raise c03.LedgerError('OUTLINE_RECONCILIATION_CONFLICT')
            if task['status'] not in {'DONE', 'CANCELLED'}:
                raise c03.LedgerError('OUTLINE_OLD_TASK_MUST_BE_RETAINED_DONE_OR_CANCELLED')
            ids = value['replacementTaskIds']
            if not isinstance(ids, list) or not all(isinstance(key, str) for key in ids) or len(ids) != len(set(ids)) or task_id in ids:
                raise c03.LedgerError('OUTLINE_REPLACEMENT_INVALID')
            contract = before['outlineContract']
            if contract['digest'] != impact['outlineDigest']: raise c03.LedgerError('OUTLINE_RECONCILIATION_STALE')
            allowed = {key for item in contract['handoff']['candidateTasks'] if item['codexCentralRegistration'] for key in item['outcomeIds']}
            needed = set(task.get('outcomeIds', [])) & allowed
            covered = set()
            for key in ids:
                replacement = c03.ensure_task(before, key)
                if replacement.get('outlineContractDigest') != contract['digest'] or replacement['status'] in {'CANCELLED', 'CANCEL_REQUESTED'}:
                    raise c03.LedgerError('OUTLINE_REPLACEMENT_NOT_CURRENT')
                covered.update(replacement.get('outcomeIds', []))
            if not needed <= covered: raise c03.LedgerError('OUTLINE_REPLACEMENT_COVERAGE_INCOMPLETE')
            def mutate(after):
                item = next(item for item in after['tasks'][task_id]['outlineImpacts'] if item['changeId'] == value['changeId'])
                item.update(status='RECONCILED_WITH_HISTORY_RETAINED', resolution=value)
        elif args.command == 'bind-wait-followup':
            if set(value) != {'waitId', 'conditionRef', 'automationId', 'targetThreadRef', 'scheduled', 'evidence'} or value['scheduled'] is not True:
                raise c03.LedgerError('WAIT_FOLLOWUP_PROOF_INVALID')
            for key in ('automationId', 'targetThreadRef'):
                c03.require_opaque_reference(value[key], 'WAIT_FOLLOWUP_REFERENCE_INVALID')
            wait = task.get('externalWaits', {}).get(value['waitId'])
            if not wait or wait['conditionRef'] != value['conditionRef']: raise c03.LedgerError('WAIT_FOLLOWUP_BINDING_INVALID')
            existing_followup = wait.get('followupRetargets', [wait.get('followupReceipt')])[-1]
            if existing_followup == value:
                return {'status': 'IDEMPOTENT_WAIT_FOLLOWUP', 'writePerformed': False}, 0
            if wait['status'] != 'WAITING': raise c03.LedgerError('WAIT_FOLLOWUP_ALREADY_RESOLVED_OR_BOUND')
            if existing_followup and (existing_followup['automationId'] != value['automationId']
                    or existing_followup['targetThreadRef'] == value['targetThreadRef']):
                raise c03.LedgerError('WAIT_FOLLOWUP_CONFLICT_REUSE_EXISTING_AUTOMATION')
            if value['targetThreadRef'] != args.caller_thread_ref:
                raise c03.LedgerError('WAIT_FOLLOWUP_MUST_TARGET_CURRENT_CENTRAL')
            def mutate(after):
                target = after['tasks'][task_id]['externalWaits'][value['waitId']]
                if existing_followup:
                    target.setdefault('followupRetargets', []).append(value)
                else:
                    target.update(followupStatus='SCHEDULED_RECEIPT_RECORDED', followupReceipt=value)
        elif args.command == 'retarget-feedback-repair':
            if set(value) != {'feedbackId', 'previousRepairTaskId', 'repairTaskId', 'reasonRef', 'evidence'}:
                raise c03.LedgerError('FEEDBACK_RETARGET_PROOF_INVALID')
            c03.require_opaque_reference(value['reasonRef'], 'FEEDBACK_RETARGET_REASON_REQUIRED')
            entry = task.get('deliveryFeedback', {}).get(value['feedbackId'])
            if not entry or entry['status'] == 'CLOSED_VERIFIED': raise c03.LedgerError('FEEDBACK_RETARGET_NOT_OPEN')
            if entry.get('repairRetargets', []) and entry['repairRetargets'][-1] == value:
                return {'status': 'IDEMPOTENT_FEEDBACK_RETARGET', 'writePerformed': False}, 0
            if (c03.feedback_repair_task(entry) != value['previousRepairTaskId']
                    or c03.ensure_task(before, value['previousRepairTaskId'])['status'] != 'CANCELLED'):
                raise c03.LedgerError('FEEDBACK_PREVIOUS_REPAIR_NOT_CANCELLED')
            replacement = c03.ensure_task(before, value['repairTaskId'])
            if value['repairTaskId'] == task_id or replacement['status'] in {'DONE', 'CANCELLED', 'CANCEL_REQUESTED'}:
                raise c03.LedgerError('FEEDBACK_ACTIVE_REPLACEMENT_REQUIRED')
            def mutate(after):
                after['tasks'][task_id]['deliveryFeedback'][value['feedbackId']].setdefault('repairRetargets', []).append(value)
        else:
            raise c03.LedgerError('LIFECYCLE_OPERATION_UNSUPPORTED')
        result = c03.commit_mutation(root, project, before, writer, args.command.upper().replace('-', '_'),
            {'taskId': task_id, 'proofDigest': c03.canonical_digest(value)}, mutate, args.caller_thread_ref)
        return {**result, 'runtimeOperationPerformed': False}, 0
