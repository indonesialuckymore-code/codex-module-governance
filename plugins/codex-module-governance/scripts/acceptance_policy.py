"""One acceptance contract shared by package, dispatch and independent review.

Task labels never authorize exemptions. Effects define a minimum; the approved
contract may require more. Missing policies retain the legacy full checklist.
"""

EVIDENCE_CATEGORIES = (
    "beforeSnapshot", "afterSnapshot", "positiveCase", "negativeCase", "idempotency",
    "rollback", "logsAndHistory", "upstreamReadback", "downstreamReadback", "testAndObjectIds",
)
EFFECT_CHECKS = {
    "changesState": {"beforeSnapshot", "rollback"},
    "executableBehavior": {"negativeCase"},
    "repeatableOperation": {"idempotency"},
    "upstreamDependency": {"upstreamReadback"},
    "downstreamConsumer": {"downstreamReadback"},
    "highRisk": set(EVIDENCE_CATEGORIES),
}
CORE_CHECKS = {"afterSnapshot", "positiveCase", "logsAndHistory", "testAndObjectIds"}
ACCEPTANCE_GROUPS = {
    "positiveCases": {"positiveCase"}, "negativeCases": {"negativeCase"},
    "idempotencyChecks": {"idempotency"}, "rollbackChecks": {"rollback"},
    "logAndHistoryChecks": {"logsAndHistory"},
    "readbackChecks": {"afterSnapshot", "upstreamReadback", "downstreamReadback"},
}


class AcceptancePolicyError(ValueError):
    pass


def validate_policy(value):
    if not isinstance(value, dict) or set(value) != {"effects", "notApplicable"}:
        raise AcceptancePolicyError("ACCEPTANCE_POLICY_INVALID")
    effects, exemptions = value["effects"], value["notApplicable"]
    if (not isinstance(effects, dict) or set(effects) != set(EFFECT_CHECKS)
            or any(type(flag) is not bool for flag in effects.values())
            or not isinstance(exemptions, dict)):
        raise AcceptancePolicyError("ACCEPTANCE_POLICY_INVALID")
    required = set(CORE_CHECKS)
    for effect, checks in EFFECT_CHECKS.items():
        if effects[effect]:
            required.update(checks)
    for category, reason in exemptions.items():
        if category not in EVIDENCE_CATEGORIES or category in required:
            raise AcceptancePolicyError("ACCEPTANCE_REQUIRED_CHECK_CANNOT_BE_WAIVED")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 600:
            raise AcceptancePolicyError("ACCEPTANCE_NOT_APPLICABLE_REASON_REQUIRED")
    return {"effects": dict(effects), "notApplicable": dict(exemptions)}


def group_is_not_applicable(policy, group):
    return policy is not None and ACCEPTANCE_GROUPS[group].issubset(policy["notApplicable"])


def verify_occupancy_effects(package, occupancies):
    if "acceptancePolicy" not in package:
        return
    policy = validate_policy(package["acceptancePolicy"])
    if not policy["effects"]["changesState"] and any(item.get("intent") == "WRITE" for item in occupancies):
        raise AcceptancePolicyError("ACCEPTANCE_EFFECTS_WRITE_MISMATCH")


def verify_approved_contract(package, decision, package_digest):
    if decision.get("source", {}).get("packageDigest") != package_digest:
        raise AcceptancePolicyError("ACCEPTANCE_PACKAGE_APPROVAL_MISMATCH")
    verify_occupancy_effects(package, decision.get("requestedOccupancies", []))
