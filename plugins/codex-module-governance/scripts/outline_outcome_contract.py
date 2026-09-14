"""Deterministic business-outcome checks for the outline/ledger boundary."""
import copy
import re


class OutlineContractError(ValueError):
    pass


def changed_outcomes(before, after):
    """Ignore candidate grouping, but include business scope and goal changes."""
    old = {item['outcomeId']: item for item in before['outcomes']}
    new = {item['outcomeId']: item for item in after['outcomes']}
    changed = {key for key in old.keys() | new.keys() if old.get(key) != new.get(key)}
    old_goals = {item['goalId']: item for item in before['goals']}
    new_goals = {item['goalId']: item for item in after['goals']}
    changed_goals = {key for key in old_goals.keys() | new_goals.keys() if old_goals.get(key) != new_goals.get(key)}
    changed.update(key for key, item in {**old, **new}.items() if item['goalId'] in changed_goals)
    for key in ('scopeSummary', 'outOfScope'):
        if before.get(key) != after.get(key):
            changed.update(old.keys() | new.keys())
    def registered(handoff):
        return {key for item in handoff['candidateTasks'] if item['codexCentralRegistration'] for key in item['outcomeIds']}
    changed.update(registered(before) ^ registered(after))
    return changed


def identifier(value, error):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{1,127}", value):
        raise OutlineContractError(error)
    return value


def indexed(items, key, error):
    if not isinstance(items, list) or not 1 <= len(items) <= 200:
        raise OutlineContractError(error)
    result = {}
    for item in items:
        if not isinstance(item, dict):
            raise OutlineContractError(error)
        item_id = identifier(item.get(key), error)
        if item_id in result:
            raise OutlineContractError(error)
        result[item_id] = item
    return result


def text(value, error):
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise OutlineContractError(error)


def reference_list(value, error):
    if not isinstance(value, list) or len(value) > 200:
        raise OutlineContractError(error)
    refs = [identifier(item, error) for item in value]
    if len(refs) != len(set(refs)):
        raise OutlineContractError(error)
    return refs


def validate_handoff(value, project_id):
    if (not isinstance(value, dict) or value.get("recordType") != "OUTLINE_HANDOFF"
            or value.get("handoffSchemaVersion") != "0.23.0" or value.get("projectId") != project_id):
        raise OutlineContractError("OUTLINE_HANDOFF_SCHEMA_OR_PROJECT_INVALID")
    outline = value.get("outline", {})
    if (not isinstance(outline, dict) or outline.get("status") != "FROZEN_NEEDS_REGISTRATION"
            or not isinstance(outline.get("version"), str) or not outline["version"].strip()
            or not re.fullmatch(r"[a-f0-9]{64}", str(outline.get("sha256", "")))):
        raise OutlineContractError("OUTLINE_FROZEN_VERSION_REQUIRED")
    approval = value.get("bossApproval")
    if not isinstance(approval, dict):
        raise OutlineContractError("OUTLINE_APPROVAL_REQUIRED")
    identifier(approval.get("reference"), "OUTLINE_APPROVAL_REQUIRED")
    goals = indexed(value.get("goals"), "goalId", "OUTLINE_GOALS_INVALID")
    for goal in goals.values():
        text(goal.get("description"), "OUTLINE_GOAL_DESCRIPTION_REQUIRED")
    outcomes = indexed(value.get("outcomes"), "outcomeId", "OUTLINE_OUTCOMES_INVALID")
    owners = {"CODEX", "CLAUDE", "HUMAN", "EXTERNAL"}
    for outcome in outcomes.values():
        if (not isinstance(outcome.get("goalId"), str) or outcome["goalId"] not in goals
                or not isinstance(outcome.get("executionOwner"), str) or outcome["executionOwner"] not in owners):
            raise OutlineContractError("OUTLINE_OUTCOME_GOAL_OR_OWNER_INVALID")
        text(outcome.get("title"), "OUTLINE_OUTCOME_TITLE_REQUIRED")
        text(outcome.get("completionBoundary"), "OUTLINE_COMPLETION_BOUNDARY_REQUIRED")
        criteria = indexed(outcome.get("acceptanceCriteria"), "criterionId", "OUTLINE_ACCEPTANCE_REQUIRED")
        for criterion in criteria.values():
            text(criterion.get("description"), "OUTLINE_ACCEPTANCE_DESCRIPTION_REQUIRED")
    if set(goals) != {o["goalId"] for o in outcomes.values()}:
        raise OutlineContractError("OUTLINE_GOAL_WITHOUT_OUTCOME")
    candidates = indexed(value.get("candidateTasks"), "candidateKey", "OUTLINE_CANDIDATES_INVALID")
    for candidate in candidates.values():
        refs = candidate.get("outcomeIds")
        if (not isinstance(refs, list) or not refs or not all(isinstance(ref, str) for ref in refs)
                or len(refs) != len(set(refs)) or any(ref not in outcomes for ref in refs)):
            raise OutlineContractError("OUTLINE_CANDIDATE_OUTCOME_INVALID")
        if (candidate.get("status") != "NOT_REGISTERED" or not isinstance(candidate.get("executionOwner"), str)
                or candidate["executionOwner"] not in owners):
            raise OutlineContractError("OUTLINE_CANDIDATE_STATUS_OR_OWNER_INVALID")
        if not isinstance(candidate.get("codexCentralRegistration"), bool):
            raise OutlineContractError("OUTLINE_REGISTRATION_SCOPE_INVALID")
        if candidate["codexCentralRegistration"] and (candidate["executionOwner"] != "CODEX"
                or any(outcomes[ref]["executionOwner"] != "CODEX" for ref in refs)):
            raise OutlineContractError("OUTLINE_NON_CODEX_REGISTRATION_FORBIDDEN")
        dependencies = reference_list(candidate.get("dependsOn"), "OUTLINE_DEPENDENCIES_INVALID")
        if any(ref not in candidates or ref == candidate["candidateKey"] for ref in dependencies):
            raise OutlineContractError("OUTLINE_DEPENDENCIES_INVALID")
    scope = value.get("centralRegistrationScope")
    if not isinstance(scope, dict) or scope.get("policy") != "CODEX_ONLY":
        raise OutlineContractError("OUTLINE_REGISTRATION_SCOPE_INVALID")
    included = set(reference_list(scope.get("includeCandidateKeys"), "OUTLINE_REGISTRATION_SCOPE_INVALID"))
    excluded = set(reference_list(scope.get("excludeCandidateKeys"), "OUTLINE_REGISTRATION_SCOPE_INVALID"))
    if (included & excluded or included | excluded != set(candidates)
            or included != {key for key, item in candidates.items() if item["codexCentralRegistration"]}):
        raise OutlineContractError("OUTLINE_REGISTRATION_SCOPE_INVALID")
    remaining = set(candidates)
    while remaining:
        ready = {key for key in remaining if not remaining.intersection(candidates[key]["dependsOn"])}
        if not ready:
            raise OutlineContractError("OUTLINE_DEPENDENCY_CYCLE")
        remaining -= ready
    return copy.deepcopy(value)
