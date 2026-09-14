"""Read-only business projection of the existing ledger and sealed C06 decisions."""


def business_progress(root, project, ledger):
    import argparse
    import ledger_manager as c03
    from ledger_manager import direction_hold, active_external_waits
    import independent_handover_validator as c06
    contract = ledger.get('outlineContract')
    if not contract:
        return {'status': 'LEGACY_NO_OUTCOME_CONTRACT', 'outcomes': [], 'efficiency': 'NOT_MEASURED'}
    if (c03.verify_ledger(argparse.Namespace(data_root=str(root), config=None, project_id=project))[1]
            or c03.canonical_digest(contract.get('handoff')) != contract.get('digest')):
        return {'status': 'UNVERIFIED_LEDGER', 'outcomes': [], 'efficiency': 'NOT_MEASURED'}
    result = []
    for outcome in contract['handoff']['outcomes']:
        tasks = {key: task for key, task in ledger['tasks'].items() if outcome['outcomeId'] in task.get('outcomeIds', [])}
        accepted = set(); references = []; unverified = []; feedback = []
        for key, task in tasks.items():
            if any(item['status'] != 'CLOSED_VERIFIED' for item in task.get('deliveryFeedback', {}).values()):
                feedback.append(key)
                continue
            if task['status'] != 'DONE': continue
            source = (contract if task.get('outlineContractDigest') == contract['digest'] else
                      ledger.get('outlineHistory', {}).get(task.get('outlineContractDigest')))
            if source is None:
                unverified.append(key)
                continue
            previous = next((item for item in source['handoff']['outcomes'] if item['outcomeId'] == outcome['outcomeId']), None)
            # Old evidence is reusable only for the exact same outcome and goal.
            goal = next(item for item in contract['handoff']['goals'] if item['goalId'] == outcome['goalId'])
            if previous != outcome or goal not in source['handoff']['goals']: continue
            history = [item for item in task.get('history', []) if item.get('event') == 'C06_BOSS_FINALIZATION' and item.get('bossDecision') == 'APPROVED']
            if not history: continue
            try:
                validation = history[-1]['validationId']
                decision = c06.verify_decision_data(root, project, validation)
                final = c06.verify_finalization_data(root, project, validation)
                if (decision['taskId'] != key or final['bossDecision'] != 'APPROVED'
                        or decision['outcome'] != 'PASS_PENDING_BOSS_APPROVAL'
                        or decision['source'].get('outlineContractDigest') != task.get('outlineContractDigest')):
                    raise c06.HandoverValidationError('OUTCOME_ACCEPTANCE_BINDING_INVALID')
                for item in decision.get('outcomeAssessments', []):
                    if item['outcomeId'] == outcome['outcomeId'] and item['verdict'] == 'PASS' and item['independentlyReadBack']:
                        accepted.add(item['criterionId']); references.append(item['reference'])
            except (c06.HandoverValidationError, OSError, ValueError, KeyError):
                unverified.append(key)
        expected = {item['criterionId'] for item in outcome['acceptanceCriteria']}
        held = [key for key, task in tasks.items() if direction_hold(task) or any(item.get('status') == 'RECONCILIATION_REQUIRED' for item in task.get('outlineImpacts', []))]
        waiting = [key for key, task in tasks.items() if active_external_waits(task)]
        state = ('NEEDS_REVIEW' if feedback or unverified or held else 'ACCEPTED' if expected <= accepted
                 else 'IN_PROGRESS' if tasks else 'NOT_STARTED')
        result.append({'outcomeId': outcome['outcomeId'], 'title': outcome['title'], 'goalId': outcome['goalId'],
            'status': state, 'completionBoundary': outcome['completionBoundary'], 'taskIds': list(tasks),
            'acceptedCriterionIds': sorted(accepted), 'missingCriterionIds': sorted(expected - accepted),
            'evidenceRefs': sorted(set(references)), 'waitingTaskIds': waiting, 'heldTaskIds': held,
            'feedbackTaskIds': feedback, 'unverifiedTaskIds': unverified})
    return {'status': 'BUSINESS_PROGRESS_READ', 'outlineVersion': contract['handoff']['outline']['version'],
            'goals': contract['handoff']['goals'], 'outcomes': result, 'efficiency': 'NOT_MEASURED',
            'completionMetric': 'ACCEPTED_OUTCOMES_NOT_TASK_COUNT'}
