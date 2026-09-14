#!/usr/bin/env python3
"""C06 independent handback validation. No business-system execution."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from initialize_project import C02Error, PROJECT_ID_PATTERN, is_within, load_data_root
from ledger_manager import (
    CENTRAL_WRITER,
    LedgerError,
    MAX_TASKS_PER_WINDOW,
    TASK_ID_PATTERN,
    canonical_digest,
    commit_mutation,
    ledger_lock,
    load_ledger,
    receipt_directory as ledger_receipt_directory,
    utc_now,
    verify_ledger,
    window_assignment_count,
    window_assignments,
    window_current_task_id,
)
from occupancy_conflict_checker import OccupancyError, verify_decision_data as verify_c05_decision
from task_package_generator import TaskPackageError, load_package, verify_package
from acceptance_policy import EVIDENCE_CATEGORIES, AcceptancePolicyError, verify_approved_contract


SCHEMA_VERSION = "0.8.0"
C10_SCHEMA_VERSION = "0.17.0"
DISPATCHES_DIRECTORY = Path("dispatches")
VALIDATIONS_DIRECTORY = Path("handover-validations")
RETURN_ROOT = Path("return-inbox")
RETURN_SCHEMA_VERSION = "0.18.0"
DECISION_FILENAME = "validation-decision.json"
FINALIZATION_FILENAME = "boss-finalization.json"
RECEIPTS_DIRECTORY = "receipts"
INITIAL_RECEIPT_ID = "receipt-000000-validate"
FINAL_RECEIPT_ID = "receipt-000001-finalize"
REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")


class HandoverValidationError(Exception):
    """A safe refusal; it never changes a business system."""


def print_result(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def require_reference(value: Any, error: str) -> str:
    if not isinstance(value, str):
        raise HandoverValidationError(error)
    candidate = value.strip()
    if not REFERENCE_PATTERN.fullmatch(candidate):
        raise HandoverValidationError(error)
    return candidate


def require_exact_object(value: Any, required: set[str], error: str) -> Dict[str, Any]:
    if not isinstance(value, dict) or set(value) != required:
        raise HandoverValidationError(error)
    return value


def require_reference_list(value: Any, error: str, allow_empty: bool = False, maximum: int = 80) -> List[str]:
    if not isinstance(value, list) or len(value) > maximum or (not allow_empty and not value):
        raise HandoverValidationError(error)
    normalized = [require_reference(item, error) for item in value]
    if len(normalized) != len(set(normalized)):
        raise HandoverValidationError(error)
    return normalized


def validation_directory(data_root: Path, project_id: str, validation_id: str) -> Path:
    return data_root / VALIDATIONS_DIRECTORY / project_id / validation_id


def decision_path(data_root: Path, project_id: str, validation_id: str) -> Path:
    return validation_directory(data_root, project_id, validation_id) / DECISION_FILENAME


def finalization_path(data_root: Path, project_id: str, validation_id: str) -> Path:
    return validation_directory(data_root, project_id, validation_id) / FINALIZATION_FILENAME


def receipt_directory(data_root: Path, project_id: str, validation_id: str) -> Path:
    return validation_directory(data_root, project_id, validation_id) / RECEIPTS_DIRECTORY


def parent_quality_review_path(data_root: Path, project_id: str, dispatch_id: str) -> Path:
    return data_root / DISPATCHES_DIRECTORY / project_id / dispatch_id / "parent-quality-review.json"


def write_json_exclusive(path: Path, payload: Dict[str, Any]) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        with path.open("x", encoding="utf-8") as output:
            output.write(serialized)
    except FileExistsError:
        raise HandoverValidationError("IMMUTABLE_TARGET_ALREADY_EXISTS")


@contextmanager
def validation_lock(data_root: Path, project_id: str) -> Iterator[None]:
    root = data_root / VALIDATIONS_DIRECTORY / ".locks"
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / f"{project_id}.lock"
    try:
        write_json_exclusive(lock_path, {
            "recordType": "C06_VALIDATION_OPERATION_LOCK", "projectId": project_id,
            "createdAt": utc_now(), "pid": os.getpid(),
        })
    except HandoverValidationError:
        raise HandoverValidationError("C06_VALIDATION_OPERATION_LOCK_PRESENT")
    try:
        yield
    finally:
        if lock_path.exists():
            lock_path.unlink()


def load_json_inside_data_root(raw_path: str, data_root: Path, missing_error: str, json_error: str) -> Tuple[Any, str]:
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file() or not is_within(path, data_root):
        raise HandoverValidationError(missing_error)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise HandoverValidationError(json_error)
    return raw, canonical_digest(raw)


def require_return_ticket_id(value: Any, error: str) -> str:
    ticket_id = require_reference(value, error)
    if len(ticket_id) > 96:
        raise HandoverValidationError(error)
    return ticket_id


def return_ticket_root(data_root: Path, project_id: str, ticket_id: str) -> Path:
    return data_root / RETURN_ROOT / project_id / "tickets" / ticket_id


def return_submission_path(data_root: Path, project_id: str, ticket_id: str) -> Path:
    return return_ticket_root(data_root, project_id, ticket_id) / "submission.json"


def return_submission_receipt_path(data_root: Path, project_id: str, ticket_id: str) -> Path:
    return return_ticket_root(data_root, project_id, ticket_id) / "receipt-000000-submission.json"


def return_delivery_path(data_root: Path, project_id: str, ticket_id: str) -> Path:
    return return_ticket_root(data_root, project_id, ticket_id) / "receipt-000001-delivery.json"


def return_policy_path(data_root: Path, project_id: str, policy_id: str) -> Path:
    return data_root / RETURN_ROOT / project_id / "policies" / f"{policy_id}.json"


def return_policy_receipt_path(data_root: Path, project_id: str, policy_id: str) -> Path:
    return data_root / RETURN_ROOT / project_id / "policies" / f"{policy_id}.receipt.json"


def load_return_ticket_submission(data_root: Path, project_id: str, ticket_id: str) -> Dict[str, Any]:
    ticket_id = require_return_ticket_id(ticket_id, "C06_RETURN_TICKET_ID_INVALID")
    try:
        submission = json.loads(return_submission_path(data_root, project_id, ticket_id).read_text(encoding="utf-8"))
        receipt = json.loads(return_submission_receipt_path(data_root, project_id, ticket_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise HandoverValidationError("C06_RETURN_TICKET_NOT_QUEUED")
    required = {
        "returnSchemaVersion", "recordType", "createdAt", "projectId", "returnTicketId", "handbackId", "taskId", "windowId",
        "completionSignalId", "sourceWindow", "routeRevisionSeen", "sourceHandbackRelativePath", "sourceHandbackDigest", "boundary",
    }
    receipt_required = {"returnSchemaVersion", "recordType", "createdAt", "projectId", "returnTicketId", "submissionDigest", "submission"}
    if not isinstance(submission, dict) or set(submission) != required or submission.get("returnSchemaVersion") != RETURN_SCHEMA_VERSION or submission.get("recordType") != "C08_RETURN_SUBMISSION" or submission.get("projectId") != project_id or submission.get("returnTicketId") != ticket_id or not isinstance(receipt, dict) or set(receipt) != receipt_required or receipt.get("returnSchemaVersion") != RETURN_SCHEMA_VERSION or receipt.get("recordType") != "C08_IMMUTABLE_RETURN_SUBMISSION_RECEIPT" or receipt.get("projectId") != project_id or receipt.get("returnTicketId") != ticket_id or receipt.get("submission") != submission or receipt.get("submissionDigest") != canonical_digest(submission):
        raise HandoverValidationError("C06_RETURN_TICKET_INTEGRITY_INVALID")
    return submission


def verify_return_ticket(data_root: Path, project_id: str, handback: Dict[str, Any], handback_digest: str) -> Dict[str, Any]:
    ticket_id = handback["returnTicketId"]
    submission = load_return_ticket_submission(data_root, project_id, ticket_id)
    try:
        delivery = json.loads(return_delivery_path(data_root, project_id, ticket_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise HandoverValidationError("C06_RETURN_TICKET_NOT_QUEUED")
    delivery_required = {"returnSchemaVersion", "recordType", "createdAt", "projectId", "returnTicketId", "eventId", "eventDigest", "eventReceiptDigest", "submissionDigest", "boundary"}
    if not isinstance(delivery, dict):
        raise HandoverValidationError("C06_RETURN_TICKET_INTEGRITY_INVALID")
    try:
        event = json.loads((data_root / "task-events" / project_id / "inbox" / f"{delivery.get('eventId')}.json").read_text(encoding="utf-8"))
        event_receipt = json.loads((data_root / "task-events" / project_id / "receipts" / f"{delivery.get('eventId')}.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise HandoverValidationError("C06_RETURN_TICKET_NOT_QUEUED")
    if set(delivery) != delivery_required or delivery.get("returnSchemaVersion") != RETURN_SCHEMA_VERSION or delivery.get("recordType") != "C08_RETURN_DELIVERY_RECEIPT" or delivery.get("projectId") != project_id or delivery.get("returnTicketId") != ticket_id or delivery.get("submissionDigest") != canonical_digest(submission) or not isinstance(event, dict) or event.get("eventType") != "TASK_HANDBACK_QUEUED" or event.get("decisionRef") != ticket_id or event.get("sourceInputDigest") != canonical_digest(submission) or delivery.get("eventDigest") != canonical_digest(event) or not isinstance(event_receipt, dict) or event_receipt.get("artifact") != event or event_receipt.get("artifactDigest") != canonical_digest(event) or delivery.get("eventReceiptDigest") != canonical_digest(event_receipt):
        raise HandoverValidationError("C06_RETURN_TICKET_INTEGRITY_INVALID")
    pairs = {"handbackId": handback["handbackId"], "taskId": handback["taskId"], "windowId": handback["windowId"], "completionSignalId": handback["completionSignalId"]}
    if any(submission.get(key) != value for key, value in pairs.items()) or submission.get("sourceHandbackDigest") != handback_digest:
        raise HandoverValidationError("C06_RETURN_TICKET_HANDOFF_MISMATCH")
    return submission


def verify_auto_return_policy(data_root: Path, project_id: str, policy_id: str) -> Dict[str, Any]:
    policy_id = require_return_ticket_id(policy_id, "C06_RETURN_POLICY_ID_INVALID")
    try:
        policy = json.loads(return_policy_path(data_root, project_id, policy_id).read_text(encoding="utf-8"))
        receipt = json.loads(return_policy_receipt_path(data_root, project_id, policy_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise HandoverValidationError("C06_RETURN_POLICY_NOT_FOUND")
    required = {"returnSchemaVersion", "recordType", "configuredAt", "policyId", "projectId", "mode", "maxConcurrentValidations", "bossAuthorizationRef", "finalDoneRequiresBoss", "sourceInputDigest", "configuredByRole", "boundary"}
    receipt_required = {"returnSchemaVersion", "recordType", "configuredAt", "projectId", "policyId", "artifactDigest", "artifact"}
    if not isinstance(policy, dict) or set(policy) != required or policy.get("returnSchemaVersion") != RETURN_SCHEMA_VERSION or policy.get("recordType") != "C08_RETURN_ADMISSION_POLICY" or policy.get("projectId") != project_id or policy.get("policyId") != policy_id or policy.get("mode") != "AUTO_QUEUE_AND_VALIDATE_WITHIN_APPROVED_SCOPE" or policy.get("maxConcurrentValidations") != 1 or policy.get("finalDoneRequiresBoss") is not True or not isinstance(receipt, dict) or set(receipt) != receipt_required or receipt.get("recordType") != "C08_IMMUTABLE_RETURN_POLICY_RECEIPT" or receipt.get("artifact") != policy or receipt.get("artifactDigest") != canonical_digest(policy):
        raise HandoverValidationError("C06_RETURN_POLICY_INTEGRITY_INVALID")
    return policy


def load_assessment_handback(args: argparse.Namespace, data_root: Path, project_id: str) -> Tuple[Any, str, Optional[str]]:
    if args.handback:
        raw, digest = load_json_inside_data_root(args.handback, data_root, "PRIVATE_C06_HANDBACK_REQUIRED_INSIDE_DATA_ROOT", "C06_HANDBACK_INVALID_JSON")
        return raw, digest, None
    ticket_id = require_return_ticket_id(args.return_ticket_id, "C06_RETURN_TICKET_ID_INVALID")
    submission = load_return_ticket_submission(data_root, project_id, ticket_id)
    candidate = (data_root / submission["sourceHandbackRelativePath"]).resolve()
    if not candidate.is_file() or not is_within(candidate, data_root):
        raise HandoverValidationError("C06_RETURN_TICKET_HANDBACK_UNAVAILABLE")
    try:
        raw = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise HandoverValidationError("C06_RETURN_TICKET_HANDBACK_UNAVAILABLE")
    return raw, canonical_digest(raw), ticket_id


def validate_handback(payload: Any, project_id: str, package_id: str) -> Dict[str, Any]:
    required = {
        "handbackSchemaVersion", "recordType", "handbackId", "returnTicketId", "routeRevisionSeen", "projectId", "packageId", "c05ReviewId",
        "taskId", "windowId", "completionSignalId", "submittedBy", "bossHandbackAuthorization",
        "executedScopeRefs", "evidenceRefs", "testAndObjectRefs", "parentQualityReviewRefs", "unresolvedRefs", "residualRiskRefs",
    }
    direction = {}
    if isinstance(payload, dict) and "directionContext" in payload:
        from ledger_manager import validate_direction_context
        direction = {"directionContext": validate_direction_context(payload["directionContext"])}
        required = required | {"directionContext"}
    handback = require_exact_object(payload, required, "C06_HANDBACK_SCHEMA_UNSUPPORTED")
    if handback["handbackSchemaVersion"] != SCHEMA_VERSION or handback["recordType"] != "C06_TASK_WINDOW_HANDBACK":
        raise HandoverValidationError("C06_HANDBACK_SCHEMA_UNSUPPORTED")
    if handback["projectId"] != project_id or handback["packageId"] != package_id:
        raise HandoverValidationError("C06_HANDBACK_SOURCE_MISMATCH")
    task_id = require_reference(handback["taskId"], "TASK_ID_INVALID")
    window_id = require_reference(handback["windowId"], "WINDOW_ID_INVALID")
    if not TASK_ID_PATTERN.fullmatch(task_id) or not TASK_ID_PATTERN.fullmatch(window_id):
        raise HandoverValidationError("C06_TASK_OR_WINDOW_ID_INVALID")
    submitted_by = require_exact_object(handback["submittedBy"], {"type", "id"}, "C06_SUBMITTER_INVALID")
    if submitted_by["type"] != "task-window" or require_reference(submitted_by["id"], "C06_SUBMITTER_INVALID") != window_id:
        raise HandoverValidationError("C06_SUBMITTER_INVALID")
    authorization = require_exact_object(
        handback["bossHandbackAuthorization"], {"status", "reference"}, "C06_BOSS_HANDBACK_AUTHORIZATION_INVALID"
    )
    if str(authorization["status"]).upper() not in {"APPROVED", "REQUIRED", "REJECTED", "AUTO_APPROVED_BY_RETURN_POLICY"}:
        raise HandoverValidationError("C06_BOSS_HANDBACK_AUTHORIZATION_INVALID")
    if not isinstance(handback["routeRevisionSeen"], int) or handback["routeRevisionSeen"] < 1:
        raise HandoverValidationError("C06_RETURN_ROUTE_REVISION_INVALID")
    return {
        **direction,
        "handbackId": require_reference(handback["handbackId"], "C06_HANDBACK_ID_INVALID"),
        "returnTicketId": require_return_ticket_id(handback["returnTicketId"], "C06_RETURN_TICKET_ID_INVALID"),
        "routeRevisionSeen": handback["routeRevisionSeen"],
        "projectId": project_id,
        "packageId": package_id,
        "c05ReviewId": require_reference(handback["c05ReviewId"], "C05_REVIEW_ID_INVALID"),
        "taskId": task_id,
        "windowId": window_id,
        "completionSignalId": require_reference(handback["completionSignalId"], "COMPLETION_SIGNAL_ID_INVALID"),
        "submittedBy": {"type": "task-window", "id": window_id},
        "bossHandbackAuthorization": {
            "status": str(authorization["status"]).upper(),
            "reference": require_reference(authorization["reference"], "C06_BOSS_HANDBACK_REFERENCE_INVALID"),
        },
        "executedScopeRefs": require_reference_list(handback["executedScopeRefs"], "C06_EXECUTED_SCOPE_REFS_INVALID"),
        "evidenceRefs": require_reference_list(handback["evidenceRefs"], "C06_EVIDENCE_REFS_INVALID"),
        "testAndObjectRefs": require_reference_list(handback["testAndObjectRefs"], "C06_TEST_OBJECT_REFS_INVALID"),
        "parentQualityReviewRefs": require_reference_list(handback["parentQualityReviewRefs"], "C06_PARENT_QUALITY_REVIEW_REFS_INVALID", allow_empty=True),
        "unresolvedRefs": require_reference_list(handback["unresolvedRefs"], "C06_UNRESOLVED_REFS_INVALID", allow_empty=True),
        "residualRiskRefs": require_reference_list(handback["residualRiskRefs"], "C06_RESIDUAL_RISK_REFS_INVALID", allow_empty=True),
    }


def superseding_conflict_validation_id(
    data_root: Path, project_id: str, ledger: Dict[str, Any], handback: Dict[str, Any]
) -> Optional[str]:
    """Verify the narrow path for an independent validation superseding one active C06 conflict."""
    task = ledger.get("tasks", {}).get(handback.get("taskId"))
    window = ledger.get("windows", {}).get(handback.get("windowId"))
    authorization = handback.get("bossHandbackAuthorization", {})
    if (
        not isinstance(task, dict) or task.get("status") != "CONFLICT"
        or not isinstance(window, dict) or window_current_task_id(window) != handback.get("taskId")
        or authorization.get("status") != "APPROVED"
        or handback.get("parentQualityReviewRefs") != []
    ):
        return None
    matches = [
        stop for stop in ledger.get("hardStops", [])
        if stop.get("code") == "C06_VALIDATION_CONFLICT"
        and stop.get("taskId") == handback.get("taskId")
        and stop.get("validationId") in handback.get("executedScopeRefs", [])
    ]
    if len(matches) != 1:
        return None
    validation_id = matches[0].get("validationId")
    try:
        prior = verify_decision_data(data_root, project_id, validation_id)
    except HandoverValidationError:
        return None
    if prior.get("taskId") != handback.get("taskId") or prior.get("outcome") != "CONFLICT":
        return None
    return validation_id


def validate_review(payload: Any, handback: Dict[str, Any], acceptance_policy=None, outcome_contract=None) -> Dict[str, Any]:
    required = {
        "reviewSchemaVersion", "recordType", "validationId", "handbackId", "taskId", "centralReviewer",
        "scopeAssessment", "evidenceAssessment",
    }
    if isinstance(payload, dict) and "feedbackAssessments" in payload:
        required.add("feedbackAssessments")
    if isinstance(payload, dict) and "outcomeAssessments" in payload:
        required.add("outcomeAssessments")
    review = require_exact_object(payload, required, "C06_REVIEW_SCHEMA_UNSUPPORTED")
    if review["reviewSchemaVersion"] != SCHEMA_VERSION or review["recordType"] != "C06_INDEPENDENT_VALIDATION_REVIEW":
        raise HandoverValidationError("C06_REVIEW_SCHEMA_UNSUPPORTED")
    if review["handbackId"] != handback["handbackId"] or review["taskId"] != handback["taskId"]:
        raise HandoverValidationError("C06_REVIEW_HANDBACK_MISMATCH")
    reviewer = require_exact_object(review["centralReviewer"], {"id", "independentReadbackPerformed"}, "C06_REVIEWER_INVALID")
    if reviewer["id"] != CENTRAL_WRITER or type(reviewer["independentReadbackPerformed"]) is not bool:
        raise HandoverValidationError("C06_REVIEWER_INVALID")
    scope_keys = {
        "packageScopeMatch", "parallelMechanismFound", "unknownWriterFound", "permissionExpansionFound",
        "unexplainedErrorFound", "duplicateDataFound", "blockingResidualRiskFound", "rollbackExecutable",
    }
    scope = require_exact_object(review["scopeAssessment"], scope_keys, "C06_SCOPE_ASSESSMENT_INVALID")
    if any(type(value) is not bool for value in scope.values()):
        raise HandoverValidationError("C06_SCOPE_ASSESSMENT_INVALID")
    evidence = require_exact_object(review["evidenceAssessment"], set(EVIDENCE_CATEGORIES), "C06_EVIDENCE_ASSESSMENT_INVALID")
    normalized_evidence: Dict[str, Dict[str, Any]] = {}
    handback_refs = set(handback["evidenceRefs"] + handback["testAndObjectRefs"])
    for category in EVIDENCE_CATEGORIES:
        item = require_exact_object(evidence[category], {"reference", "verdict", "independentlyReadBack"}, "C06_EVIDENCE_ITEM_INVALID")
        reference = require_reference(item["reference"], "C06_EVIDENCE_REFERENCE_INVALID")
        verdict = str(item["verdict"]).upper()
        if verdict not in {"PASS", "FAIL", "MISSING", "NOT_APPLICABLE"} or type(item["independentlyReadBack"]) is not bool:
            raise HandoverValidationError("C06_EVIDENCE_ITEM_INVALID")
        if verdict == "NOT_APPLICABLE" and (
            acceptance_policy is None or category not in acceptance_policy["notApplicable"]
        ):
            raise HandoverValidationError("C06_NOT_APPLICABLE_NOT_IN_APPROVED_CONTRACT")
        if verdict != "MISSING" and reference not in handback_refs:
            raise HandoverValidationError("C06_EVIDENCE_NOT_DECLARED_BY_HANDBACK")
        normalized_evidence[category] = {
            "reference": reference, "verdict": verdict, "independentlyReadBack": item["independentlyReadBack"],
        }
    feedback = review.get("feedbackAssessments", [])
    if "feedbackAssessments" in review and (not isinstance(feedback, list) or not 1 <= len(feedback) <= 80):
        raise HandoverValidationError("C06_FEEDBACK_ASSESSMENTS_INVALID")
    seen = set()
    for item in feedback:
        require_exact_object(item, {"originalTaskId", "feedbackId", "feedbackDigest", "evidenceRef",
                                   "issueResolved", "independentlyReadBack"}, "C06_FEEDBACK_ASSESSMENT_INVALID")
        for key in ("originalTaskId", "feedbackId", "evidenceRef"):
            require_reference(item[key], "C06_FEEDBACK_REFERENCE_INVALID")
        if (not isinstance(item["feedbackDigest"], str) or re.fullmatch(r"[a-f0-9]{64}", item["feedbackDigest"]) is None
                or type(item["issueResolved"]) is not bool or type(item["independentlyReadBack"]) is not bool):
            raise HandoverValidationError("C06_FEEDBACK_ASSESSMENT_INVALID")
        key = (item["originalTaskId"], item["feedbackId"])
        if key in seen: raise HandoverValidationError("C06_FEEDBACK_DUPLICATE")
        seen.add(key)
        if item["evidenceRef"] not in handback_refs:
            raise HandoverValidationError("C06_FEEDBACK_EVIDENCE_NOT_DECLARED")
    outcomes = review.get("outcomeAssessments", [])
    if outcome_contract is None and "outcomeAssessments" in review:
        raise HandoverValidationError("C06_OUTCOME_CONTRACT_REQUIRED")
    if outcome_contract is not None:
        expected = {(item['outcomeId'], criterion['criterionId']) for item in outcome_contract['outcomes']
                    for criterion in item['acceptanceCriteria']}
        if not isinstance(outcomes, list) or len(outcomes) != len(expected):
            raise HandoverValidationError("C06_OUTCOME_CRITERIA_INCOMPLETE")
        seen = set()
        for item in outcomes:
            require_exact_object(item, {"outcomeId", "criterionId", "reference", "verdict", "independentlyReadBack"}, "C06_OUTCOME_ASSESSMENT_INVALID")
            for key in ('outcomeId', 'criterionId', 'reference'):
                require_reference(item[key], 'C06_OUTCOME_REFERENCE_INVALID')
            pair = (item['outcomeId'], item['criterionId'])
            if (pair not in expected or pair in seen or not isinstance(item['verdict'], str)
                    or item['verdict'] not in {'PASS', 'FAIL', 'MISSING'}
                    or type(item['independentlyReadBack']) is not bool
                    or (item['verdict'] != 'MISSING' and item['reference'] not in handback_refs)):
                raise HandoverValidationError('C06_OUTCOME_ASSESSMENT_INVALID')
            seen.add(pair)
    return {
        **({"outcomeAssessments": outcomes} if outcome_contract is not None else {}),
        **({"feedbackAssessments": feedback} if feedback else {}),
        "validationId": require_reference(review["validationId"], "C06_VALIDATION_ID_INVALID"),
        "handbackId": handback["handbackId"],
        "taskId": handback["taskId"],
        "centralReviewer": {"id": CENTRAL_WRITER, "independentReadbackPerformed": reviewer["independentReadbackPerformed"]},
        "scopeAssessment": scope,
        "evidenceAssessment": normalized_evidence,
    }


def derive_outcome(review: Dict[str, Any], handback: Dict[str, Any]) -> str:
    scope = review["scopeAssessment"]
    if (
        not scope["packageScopeMatch"] or scope["parallelMechanismFound"] or scope["unknownWriterFound"]
        or scope["permissionExpansionFound"] or scope["duplicateDataFound"]
    ):
        return "CONFLICT"
    verdicts = [item["verdict"] for item in review["evidenceAssessment"].values()]
    if (
        "FAIL" in verdicts or scope["unexplainedErrorFound"] or scope["blockingResidualRiskFound"]
        or any(item['verdict'] == 'FAIL' for item in review.get('outcomeAssessments', []))
        or any(not item["issueResolved"] for item in review.get("feedbackAssessments", []))
        or (not scope["rollbackExecutable"] and review["evidenceAssessment"]["rollback"]["verdict"] != "NOT_APPLICABLE")
    ):
        return "PARTIAL"
    if (
        "MISSING" in verdicts or handback["unresolvedRefs"]
        or any(item['verdict'] == 'MISSING' or not item['independentlyReadBack'] for item in review.get('outcomeAssessments', []))
        or not review["centralReviewer"]["independentReadbackPerformed"]
        or any(not item["independentlyReadBack"] for item in review["evidenceAssessment"].values())
        or any(not item["independentlyReadBack"] for item in review.get("feedbackAssessments", []))
    ):
        return "NEEDS_REVIEW"
    return "PASS_PENDING_BOSS_APPROVAL"


def verify_feedback_assessments(data_root: Path, project_id: str, ledger: Dict[str, Any],
                                task_id: str, artifact: Dict[str, Any]) -> None:
    from ledger_manager import feedback_binding, feedback_repair_task
    for item in artifact.get("feedbackAssessments", []):
        original = ledger["tasks"].get(item["originalTaskId"], {})
        entry = original.get("deliveryFeedback", {}).get(item["feedbackId"])
        if (not entry or feedback_repair_task(entry) != task_id
                or feedback_binding(entry) != item["feedbackDigest"]):
            raise HandoverValidationError("C06_FEEDBACK_REPAIR_BINDING_MISMATCH")
        original_decision = verify_decision_data(data_root, project_id, entry["originalValidationId"])
        original_finalization = verify_finalization_data(data_root, project_id, entry["originalValidationId"])
        if (canonical_digest(original_decision) != entry["originalDecisionDigest"]
                or canonical_digest(original_finalization) != entry["originalFinalizationDigest"]
                or original_finalization["bossDecision"] != "APPROVED"):
            raise HandoverValidationError("C06_FEEDBACK_ORIGINAL_DECISION_MISMATCH")
        if item["evidenceRef"] in {value["reference"] for value in original_decision["evidenceAssessment"].values()}:
            raise HandoverValidationError("C06_FEEDBACK_NEW_EVIDENCE_REQUIRED")


def require_writer(writer_id: Optional[str]) -> str:
    if writer_id != CENTRAL_WRITER:
        raise HandoverValidationError("C06_WRITER_NOT_AUTHORIZED")
    return writer_id


def verify_parent_quality_reviews(data_root: Path, project_id: str, ledger: Dict[str, Any], handback: Dict[str, Any]) -> None:
    """Require the parent window's consolidation when this assignment used helpers.

    C06 remains the independent final reviewer. This check only stops the parent
    from skipping its own evidence consolidation or silently pasting child output.
    """
    grouped: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for agent_id, agent in ledger.get("subAgents", {}).items():
        if (
            isinstance(agent, dict) and agent.get("windowId") == handback["windowId"]
            and agent.get("taskId") == handback["taskId"] and agent.get("level") == 1
        ):
            dispatch_id = agent.get("dispatchId")
            if not isinstance(dispatch_id, str) or not REFERENCE_PATTERN.fullmatch(dispatch_id):
                raise HandoverValidationError("C06_SUB_AGENT_DISPATCH_LINK_INVALID")
            grouped.setdefault(dispatch_id, {})[agent_id] = agent
    supplied_refs = set(handback["parentQualityReviewRefs"])
    if not grouped:
        if supplied_refs:
            raise HandoverValidationError("C06_UNEXPECTED_PARENT_QUALITY_REVIEW_REFERENCE")
        return
    expected_refs = set()
    for dispatch_id, agents in grouped.items():
        if any(agent.get("status") != "RETURNED" or not isinstance(agent.get("returnId"), str) for agent in agents.values()):
            raise HandoverValidationError("C06_SUB_AGENT_RETURN_NOT_COMPLETE")
        path = parent_quality_review_path(data_root, project_id, dispatch_id)
        try:
            review = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise HandoverValidationError("C06_PARENT_QUALITY_REVIEW_NOT_FOUND_OR_INVALID")
        returns = review.get("subAgentReturns")
        actual_returns = {}
        if isinstance(returns, list):
            for item in returns:
                if isinstance(item, dict) and isinstance(item.get("subAgentId"), str) and isinstance(item.get("returnId"), str):
                    actual_returns[item["subAgentId"]] = item["returnId"]
        expected_returns = {agent_id: agent["returnId"] for agent_id, agent in agents.items()}
        boundary = review.get("boundary", {})
        quality_id = review.get("qualityReviewId")
        coverage = review.get("acceptanceCoverage", {})
        expected_coverage = {"positiveCases", "negativeCases", "idempotencyChecks", "rollbackChecks", "logAndHistoryChecks", "readbackChecks"}
        coverage_is_complete = isinstance(coverage, dict) and set(coverage) == expected_coverage and all(
            isinstance(refs, list) and refs and all(isinstance(ref, str) and REFERENCE_PATTERN.fullmatch(ref) for ref in refs)
            for refs in coverage.values()
        )
        scope_check = review.get("scopeCheck", {})
        conflicts = review.get("conflictResolutions")
        conflict_agents = {
            item.get("subAgentId") for item in conflicts
            if isinstance(item, dict) and isinstance(item.get("subAgentId"), str)
            and item.get("outcome") in {"NO_CONFLICT", "RESOLVED", "ESCALATED"}
            and isinstance(item.get("resolutionRef"), str) and REFERENCE_PATTERN.fullmatch(item["resolutionRef"])
        } if isinstance(conflicts, list) else set()
        parent_readback = review.get("parentReadbackEvidenceRefs")
        readback_is_present = isinstance(parent_readback, list) and bool(parent_readback) and all(
            isinstance(ref, str) and REFERENCE_PATTERN.fullmatch(ref) for ref in parent_readback
        )
        if (
            review.get("schemaVersion") != C10_SCHEMA_VERSION or review.get("recordType") != "C10_PARENT_QUALITY_REVIEW"
            or review.get("projectId") != project_id or review.get("dispatchId") != dispatch_id
            or review.get("taskId") != handback["taskId"] or review.get("windowId") != handback["windowId"]
            or actual_returns != expected_returns or boundary.get("parentQualityGatePassed") is not True
            or boundary.get("parentMayRequestC06") is not True or not isinstance(quality_id, str)
            or not REFERENCE_PATTERN.fullmatch(quality_id)
            or not coverage_is_complete or conflict_agents != set(agents)
            or scope_check != {"withinApprovedScope": True, "unexpectedWriteFound": False}
            or review.get("unifiedStatus") not in {"NEEDS_REVIEW", "PARTIAL", "BLOCKED"} or not readback_is_present
        ):
            raise HandoverValidationError("C06_PARENT_QUALITY_REVIEW_CHAIN_INVALID")
        expected_refs.add(quality_id)
    if supplied_refs != expected_refs:
        raise HandoverValidationError("C06_PARENT_QUALITY_REVIEW_REFERENCE_MISMATCH")


def verified_sources(data_root: Path, project_id: str, package_id: str, handback: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    try:
        _, package_exit = verify_package(argparse.Namespace(data_root=str(data_root), config=None, project_id=project_id, package_id=package_id))
        _, ledger_exit = verify_ledger(argparse.Namespace(data_root=str(data_root), config=None, project_id=project_id))
        if package_exit != 0 or ledger_exit != 0:
            raise HandoverValidationError("C06_SOURCE_INTEGRITY_UNVERIFIED")
        package = load_package(data_root, project_id, package_id)
        ledger = load_ledger(data_root, project_id)
        from ledger_manager import require_current_direction
        require_current_direction(ledger, handback["taskId"], handback)
        c05_decision = verify_c05_decision(data_root, project_id, handback["c05ReviewId"])
        verify_approved_contract(package, c05_decision, canonical_digest(package))
    except (TaskPackageError, OccupancyError, LedgerError, C02Error, AcceptancePolicyError) as error:
        raise HandoverValidationError(f"C06_SOURCE_{error}")
    if (
        package["taskId"] != handback["taskId"]
        or c05_decision["taskId"] != handback["taskId"]
        or c05_decision["packageId"] != package_id
    ):
        raise HandoverValidationError("C06_TASK_SOURCE_MISMATCH")
    if c05_decision["status"] != "ELIGIBLE_FOR_DISPATCH_APPROVAL":
        raise HandoverValidationError("C06_REQUIRES_ELIGIBLE_C05_DECISION")
    task = ledger["tasks"].get(handback["taskId"])
    window = ledger["windows"].get(handback["windowId"])
    superseding_conflict_id = superseding_conflict_validation_id(
        data_root, project_id, ledger, handback
    )
    if not isinstance(task, dict) or (
        task.get("status") != "NEEDS_REVIEW" and superseding_conflict_id is None
    ):
        raise HandoverValidationError("C06_REQUIRES_NEEDS_REVIEW_TASK")
    if not isinstance(window, dict) or window_current_task_id(window) != handback["taskId"]:
        raise HandoverValidationError("C06_HANDBACK_WINDOW_MISMATCH")
    if (
        handback["completionSignalId"] not in task.get("completionSignalIds", [])
        and not valid_completion_signal_reissue_after_abort(data_root, project_id, task, handback)
    ):
        raise HandoverValidationError("C06_COMPLETION_SIGNAL_NOT_RECORDED")
    verify_parent_quality_reviews(data_root, project_id, ledger, handback)
    return package, c05_decision, ledger


def valid_completion_signal_reissue_after_abort(
    data_root: Path,
    project_id: str,
    task: Dict[str, Any],
    handback: Dict[str, Any],
) -> bool:
    """Accept only the C08-supported immutable reissue after a valid abort.

    C08 deliberately does not append a second completion signal when a ticket is
    reissued for the same task/window after validation was aborted.  C06 may
    accept that missing *new* signal only when the current admission explicitly
    performed no ledger mutation and a fully linked predecessor has a recorded
    completion signal, reservation, and immutable abort receipt.
    """
    ticket_id = handback.get("returnTicketId")
    if not isinstance(ticket_id, str):
        return False
    tickets_root = data_root / RETURN_ROOT / project_id / "tickets"
    current_root = tickets_root / ticket_id

    def load(path: Path) -> Optional[Dict[str, Any]]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    current_submission = load(current_root / "submission.json")
    current_receipt = load(current_root / "receipt-000000-submission.json")
    current_admission = load(current_root / "receipt-000002-admission.json")
    current_reservation = load(current_root / "receipt-000003-validation-reservation.json")
    if not all(isinstance(item, dict) for item in (current_submission, current_receipt, current_admission, current_reservation)):
        return False
    current_digest = canonical_digest(current_submission)
    if (
        current_submission.get("returnTicketId") != ticket_id
        or current_submission.get("taskId") != handback.get("taskId")
        or current_submission.get("windowId") != handback.get("windowId")
        or current_submission.get("handbackId") != handback.get("handbackId")
        or current_submission.get("completionSignalId") != handback.get("completionSignalId")
        or current_receipt.get("submission") != current_submission
        or current_receipt.get("submissionDigest") != current_digest
        or current_admission.get("returnTicketId") != ticket_id
        or current_admission.get("taskId") != handback.get("taskId")
        or current_admission.get("completionSignalId") != handback.get("completionSignalId")
        or current_admission.get("ledgerReceiptId") is not None
        or current_reservation.get("returnTicketId") != ticket_id
        or current_reservation.get("taskId") != handback.get("taskId")
        or current_reservation.get("handbackId") != handback.get("handbackId")
        or current_reservation.get("submissionDigest") != current_digest
    ):
        return False

    recorded_signals = task.get("completionSignalIds", [])
    if not isinstance(recorded_signals, list):
        return False
    try:
        prior_roots = sorted(path for path in tickets_root.iterdir() if path.is_dir() and path.name != ticket_id)
    except OSError:
        return False
    for prior_root in prior_roots:
        prior_submission = load(prior_root / "submission.json")
        prior_receipt = load(prior_root / "receipt-000000-submission.json")
        prior_admission = load(prior_root / "receipt-000002-admission.json")
        prior_reservation = load(prior_root / "receipt-000003-validation-reservation.json")
        prior_abort = load(prior_root / "receipt-000004-validation-aborted.json")
        if not all(isinstance(item, dict) for item in (prior_submission, prior_receipt, prior_admission, prior_reservation, prior_abort)):
            continue
        if (prior_root / "receipt-000004-validation-result.json").exists():
            continue
        prior_ticket_id = prior_root.name
        prior_digest = canonical_digest(prior_submission)
        prior_signal = prior_submission.get("completionSignalId")
        if (
            prior_submission.get("returnTicketId") == prior_ticket_id
            and prior_submission.get("taskId") == handback.get("taskId")
            and prior_submission.get("windowId") == handback.get("windowId")
            and prior_receipt.get("submission") == prior_submission
            and prior_receipt.get("submissionDigest") == prior_digest
            and prior_admission.get("returnTicketId") == prior_ticket_id
            and prior_admission.get("taskId") == handback.get("taskId")
            and prior_admission.get("completionSignalId") == prior_signal
            and isinstance(prior_admission.get("ledgerReceiptId"), str)
            and prior_reservation.get("returnTicketId") == prior_ticket_id
            and prior_reservation.get("taskId") == handback.get("taskId")
            and prior_reservation.get("submissionDigest") == prior_digest
            and prior_reservation.get("validatorThreadRef") == current_reservation.get("validatorThreadRef")
            and prior_abort.get("recordType") == "C08_RETURN_VALIDATION_ABORT"
            and prior_abort.get("returnTicketId") == prior_ticket_id
            and prior_abort.get("taskId") == handback.get("taskId")
            and prior_abort.get("handbackId") == prior_submission.get("handbackId")
            and prior_abort.get("validatorThreadRef") == prior_reservation.get("validatorThreadRef")
            and prior_abort.get("reservationDigest") == canonical_digest(prior_reservation)
            and prior_signal in recorded_signals
        ):
            return True
    return False


def linked_ledger_receipt(data_root: Path, project_id: str, mutation: Dict[str, Any], expected_operation: str, expected_status: str) -> Dict[str, Any]:
    receipt_id = mutation.get("receiptId")
    try:
        receipt = json.loads((ledger_receipt_directory(data_root, project_id) / f"{receipt_id}.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        raise HandoverValidationError("C06_LINKED_LEDGER_RECEIPT_MISSING_OR_INVALID")
    snapshot = receipt.get("afterLedger", {})
    if (
        receipt.get("recordType") != "C03_IMMUTABLE_LEDGER_RECEIPT" or receipt.get("receiptId") != receipt_id
        or receipt.get("operation") != expected_operation or receipt.get("afterLedgerDigest") != mutation.get("afterLedgerDigest")
        or receipt.get("afterRevision") != mutation.get("afterRevision") or not isinstance(snapshot, dict)
        or canonical_digest(snapshot) != mutation.get("afterLedgerDigest")
        or snapshot.get("tasks", {}).get(mutation.get("taskId"), {}).get("status") != expected_status
        or mutation.get("taskStatusAfter") != expected_status
    ):
        raise HandoverValidationError("C06_LINKED_LEDGER_RECEIPT_INVALID")
    return receipt


def build_decision(
    project_id: str, package: Dict[str, Any], c05_decision: Dict[str, Any], handback: Dict[str, Any], handback_digest: str,
    review: Dict[str, Any], review_digest: str, outcome: str, before: Dict[str, Any], after: Dict[str, Any], ledger_result: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "recordType": "C06_VALIDATION_DECISION",
        **({"directionContext": handback["directionContext"]} if "directionContext" in handback else {}),
        "validationId": review["validationId"],
        "projectId": project_id,
        "packageId": handback["packageId"],
        "c05ReviewId": handback["c05ReviewId"],
        "handbackId": handback["handbackId"],
        "taskId": handback["taskId"],
        "windowId": handback["windowId"],
        "createdAt": utc_now(),
        "writerId": CENTRAL_WRITER,
        "outcome": outcome,
        "source": {
            **({"outlineContractDigest": package['task']['outcomeContract']['digest']} if package.get('task', {}).get('outcomeContract') else {}),
            **({"acceptancePolicyDigest": canonical_digest(package["acceptancePolicy"])} if "acceptancePolicy" in package else {}),
            "packageDigest": canonical_digest(package), "c05DecisionDigest": canonical_digest(c05_decision),
            "handbackDigest": handback_digest, "reviewDigest": review_digest,
            "returnTicketId": handback["returnTicketId"],
            "parentQualityReviewRefs": handback["parentQualityReviewRefs"],
            "beforeLedgerRevision": before["revision"], "beforeLedgerDigest": canonical_digest(before),
        },
        "bossHandbackAuthorizationRef": handback["bossHandbackAuthorization"]["reference"],
        "scopeAssessment": review["scopeAssessment"],
        "evidenceAssessment": review["evidenceAssessment"],
        **({"outcomeAssessments": review["outcomeAssessments"]} if "outcomeAssessments" in review else {}),
        **({"feedbackAssessments": review["feedbackAssessments"]} if "feedbackAssessments" in review else {}),
        "unresolvedRefs": handback["unresolvedRefs"],
        "residualRiskRefs": handback["residualRiskRefs"],
        "ledgerMutation": {
            "receiptId": ledger_result["receiptId"], "afterRevision": after["revision"],
            "afterLedgerDigest": canonical_digest(after), "taskId": handback["taskId"],
            "taskStatusAfter": after["tasks"][handback["taskId"]]["status"],
        },
        "completionBoundary": {
            "bossFinalApprovalRequired": outcome == "PASS_PENDING_BOSS_APPROVAL",
            "doneAllowed": False, "doneRecorded": False,
            "testCreationAllowed": False, "businessWriteAllowed": False, "occupancyReleased": False,
        },
        "receiptIds": [INITIAL_RECEIPT_ID], "latestReceiptId": INITIAL_RECEIPT_ID,
    }


def persist_decision(data_root: Path, decision: Dict[str, Any]) -> None:
    project_id, validation_id = decision["projectId"], decision["validationId"]
    root = validation_directory(data_root, project_id, validation_id)
    root.mkdir(parents=True, exist_ok=False)
    receipt_directory(data_root, project_id, validation_id).mkdir(exist_ok=False)
    receipt = {
        "schemaVersion": SCHEMA_VERSION, "recordType": "C06_IMMUTABLE_VALIDATION_RECEIPT",
        "receiptId": INITIAL_RECEIPT_ID, "createdAt": utc_now(), "projectId": project_id,
        "validationId": validation_id, "writerId": CENTRAL_WRITER, "operation": "C06_RECORD_INDEPENDENT_VALIDATION",
        "beforeDecisionDigest": "NONE", "afterDecisionDigest": canonical_digest(decision), "afterDecision": decision,
    }
    write_json_exclusive(receipt_directory(data_root, project_id, validation_id) / f"{INITIAL_RECEIPT_ID}.json", receipt)
    write_json_exclusive(decision_path(data_root, project_id, validation_id), decision)


def load_decision(data_root: Path, project_id: str, validation_id: str) -> Dict[str, Any]:
    path = decision_path(data_root, project_id, validation_id)
    if not path.is_file():
        raise HandoverValidationError("C06_VALIDATION_DECISION_NOT_FOUND")
    try:
        decision = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise HandoverValidationError("C06_VALIDATION_DECISION_INVALID_JSON")
    if (
        not isinstance(decision, dict) or decision.get("schemaVersion") != SCHEMA_VERSION
        or decision.get("recordType") != "C06_VALIDATION_DECISION" or decision.get("projectId") != project_id
        or decision.get("validationId") != validation_id or decision.get("writerId") != CENTRAL_WRITER
    ):
        raise HandoverValidationError("C06_VALIDATION_DECISION_SCHEMA_UNSUPPORTED")
    return decision


def verify_decision_data(data_root: Path, project_id: str, validation_id: str) -> Dict[str, Any]:
    decision = load_decision(data_root, project_id, validation_id)
    try:
        receipt = json.loads((receipt_directory(data_root, project_id, validation_id) / f"{INITIAL_RECEIPT_ID}.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise HandoverValidationError("C06_VALIDATION_RECEIPT_MISSING_OR_INVALID")
    expected_status = {
        "PASS_PENDING_BOSS_APPROVAL": "NEEDS_REVIEW", "NEEDS_REVIEW": "NEEDS_REVIEW",
        "PARTIAL": "PARTIAL", "CONFLICT": "CONFLICT",
    }.get(decision.get("outcome"))
    expected_operation = "C06_RECORD_VALIDATION_" + str(decision.get("outcome"))
    boundary = decision.get("completionBoundary", {})
    if (
        expected_status is None or receipt.get("recordType") != "C06_IMMUTABLE_VALIDATION_RECEIPT"
        or receipt.get("receiptId") != INITIAL_RECEIPT_ID or receipt.get("beforeDecisionDigest") != "NONE"
        or receipt.get("afterDecisionDigest") != canonical_digest(receipt.get("afterDecision", {}))
        or receipt.get("afterDecision") != decision or canonical_digest(decision) != receipt.get("afterDecisionDigest")
        or boundary.get("bossFinalApprovalRequired") != (decision.get("outcome") == "PASS_PENDING_BOSS_APPROVAL")
        or boundary.get("doneAllowed") is not False or boundary.get("doneRecorded") is not False
        or boundary.get("testCreationAllowed") is not False or boundary.get("businessWriteAllowed") is not False
        or boundary.get("occupancyReleased") is not False
    ):
        raise HandoverValidationError("C06_VALIDATION_RECEIPT_CHAIN_INVALID")
    linked_ledger_receipt(data_root, project_id, decision["ledgerMutation"], expected_operation, expected_status)
    return decision


def public_assessment_result(decision: Dict[str, Any], write_performed: bool) -> Dict[str, Any]:
    return {
        "status": decision["outcome"], "validationId": decision["validationId"], "taskId": decision["taskId"],
        "taskStatusAfter": decision["ledgerMutation"]["taskStatusAfter"] if write_performed else "UNCHANGED",
        "bossFinalApprovalRequired": decision["outcome"] == "PASS_PENDING_BOSS_APPROVAL",
        "doneRecorded": False, "businessWriteAllowed": False, "occupancyReleased": False,
        "writePerformed": write_performed,
        "message": "C06 只记录独立验收；未修改业务对象、创建 TEST 或释放占用。",
    }


def assess(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args)
    project_id = require_reference(args.project_id, "PROJECT_ID_INVALID")
    package_id = require_reference(args.package_id, "TASK_PACKAGE_ID_INVALID")
    if not PROJECT_ID_PATTERN.fullmatch(project_id) or not PROJECT_ID_PATTERN.fullmatch(package_id):
        raise HandoverValidationError("C06_PROJECT_OR_PACKAGE_ID_INVALID")
    raw_handback, handback_digest, requested_ticket_id = load_assessment_handback(args, data_root, project_id)
    handback = validate_handback(raw_handback, project_id, package_id)
    if requested_ticket_id and handback["returnTicketId"] != requested_ticket_id:
        raise HandoverValidationError("C06_RETURN_TICKET_HANDOFF_MISMATCH")
    verify_return_ticket(data_root, project_id, handback, handback_digest)
    raw_review, review_digest = load_json_inside_data_root(
        args.review, data_root, "PRIVATE_C06_REVIEW_REQUIRED_INSIDE_DATA_ROOT", "C06_REVIEW_INVALID_JSON"
    )
    # Bind exemptions to the sealed C04 contract, never to a late reviewer claim.
    try:
        _, package_exit = verify_package(argparse.Namespace(
            data_root=str(data_root), config=None, project_id=project_id, package_id=package_id))
        if package_exit != 0:
            raise HandoverValidationError("C06_SOURCE_INTEGRITY_UNVERIFIED")
        contract = load_package(data_root, project_id, package_id)
        verify_approved_contract(contract, verify_c05_decision(data_root, project_id, handback["c05ReviewId"]), canonical_digest(contract))
    except (TaskPackageError, C02Error, OccupancyError, AcceptancePolicyError) as error:
        raise HandoverValidationError(f"C06_SOURCE_{error}")
    review = validate_review(raw_review, handback, contract.get("acceptancePolicy"), contract.get('task', {}).get('outcomeContract'))
    from ledger_manager import require_current_direction
    require_current_direction(load_ledger(data_root, project_id), handback["taskId"], handback)
    outcome = derive_outcome(review, handback)
    if args.apply:
        require_writer(args.writer_id)
        if decision_path(data_root, project_id, review["validationId"]).exists():
            decision = verify_decision_data(data_root, project_id, review["validationId"])
            if decision["source"]["handbackDigest"] != handback_digest or decision["source"]["reviewDigest"] != review_digest:
                raise HandoverValidationError("C06_VALIDATION_ID_REUSE_WITH_DIFFERENT_INPUT")
            result = public_assessment_result(decision, False)
            result["status"] = "IDEMPOTENT_EXISTING_C06_DECISION"
            result["decisionOutcome"] = decision["outcome"]
            return result, 0
    package, c05_decision, ledger = verified_sources(data_root, project_id, package_id, handback)
    verify_feedback_assessments(data_root, project_id, ledger, handback["taskId"], review)
    authorization_status = handback["bossHandbackAuthorization"]["status"]
    automatic_policy = authorization_status == "AUTO_APPROVED_BY_RETURN_POLICY"
    if automatic_policy:
        verify_auto_return_policy(data_root, project_id, handback["bossHandbackAuthorization"]["reference"])
    if authorization_status != "APPROVED" and not automatic_policy:
        if args.apply:
            raise HandoverValidationError("C06_BOSS_HANDBACK_AUTHORIZATION_REQUIRED")
        return {
            "status": "WAITING_FOR_BOSS_HANDBACK_AUTHORIZATION", "validationId": review["validationId"],
            "taskId": handback["taskId"], "doneRecorded": False, "writePerformed": False,
        }, 0
    if args.dry_run:
        return {
            "status": outcome, "validationId": review["validationId"], "taskId": handback["taskId"],
            "bossFinalApprovalRequired": outcome == "PASS_PENDING_BOSS_APPROVAL", "doneRecorded": False,
            "businessWriteAllowed": False, "writePerformed": False,
        }, 0

    writer_id = require_writer(args.writer_id)
    with validation_lock(data_root, project_id):
        if decision_path(data_root, project_id, review["validationId"]).exists():
            decision = verify_decision_data(data_root, project_id, review["validationId"])
            result = public_assessment_result(decision, False)
            result["status"] = "IDEMPOTENT_EXISTING_C06_DECISION"
            result["decisionOutcome"] = decision["outcome"]
            return result, 0
        with ledger_lock(data_root, project_id):
            package, c05_decision, before = verified_sources(data_root, project_id, package_id, handback)
            verify_feedback_assessments(data_root, project_id, before, handback["taskId"], review)
            superseding_conflict_id = superseding_conflict_validation_id(
                data_root, project_id, before, handback
            )
            if superseding_conflict_id is not None and outcome != "PASS_PENDING_BOSS_APPROVAL":
                raise HandoverValidationError("C06_SUPERSEDING_CONFLICT_VALIDATION_MUST_PASS")
            target_status = {"PASS_PENDING_BOSS_APPROVAL": "NEEDS_REVIEW", "NEEDS_REVIEW": "NEEDS_REVIEW", "PARTIAL": "PARTIAL", "CONFLICT": "CONFLICT"}[outcome]

            def mutate(after: Dict[str, Any]) -> None:
                task = after["tasks"][handback["taskId"]]
                allowed_status = "CONFLICT" if superseding_conflict_id is not None else "NEEDS_REVIEW"
                if task["status"] != allowed_status:
                    raise HandoverValidationError("C06_REQUIRES_NEEDS_REVIEW_TASK")
                task["status"] = target_status
                task["history"].append({
                    "at": utc_now(), "event": "C06_INDEPENDENT_VALIDATION", "validationId": review["validationId"],
                    "outcome": outcome, "to": target_status,
                    "supersedesConflictValidationId": superseding_conflict_id,
                    "by": CENTRAL_WRITER,
                })
                if outcome == "CONFLICT":
                    after["hardStops"].append({
                        "code": "C06_VALIDATION_CONFLICT", "validationId": review["validationId"],
                        "taskId": handback["taskId"], "detectedAt": utc_now(),
                    })

            operation = "C06_RECORD_VALIDATION_" + outcome
            ledger_result = commit_mutation(
                data_root, project_id, before, writer_id, operation,
                {"validationId": review["validationId"], "taskId": handback["taskId"], "outcome": outcome}, mutate,
                caller_thread_ref=args.caller_thread_ref,
            )
            after = load_ledger(data_root, project_id)
        decision = build_decision(
            project_id, package, c05_decision, handback, handback_digest, review, review_digest,
            outcome, before, after, ledger_result,
        )
        persist_decision(data_root, decision)
    result = public_assessment_result(decision, True)
    result["ledgerReceiptId"] = ledger_result["receiptId"]
    result["decisionReceiptId"] = INITIAL_RECEIPT_ID
    return result, 0


def persist_finalization(data_root: Path, finalization: Dict[str, Any]) -> None:
    project_id, validation_id = finalization["projectId"], finalization["validationId"]
    receipt = {
        "schemaVersion": SCHEMA_VERSION, "recordType": "C06_IMMUTABLE_FINALIZATION_RECEIPT",
        "receiptId": FINAL_RECEIPT_ID, "createdAt": utc_now(), "projectId": project_id,
        "validationId": validation_id, "writerId": CENTRAL_WRITER, "operation": "C06_RECORD_BOSS_FINALIZATION",
        "beforeFinalizationDigest": "NONE", "afterFinalizationDigest": canonical_digest(finalization),
        "afterFinalization": finalization,
    }
    write_json_exclusive(receipt_directory(data_root, project_id, validation_id) / f"{FINAL_RECEIPT_ID}.json", receipt)
    write_json_exclusive(finalization_path(data_root, project_id, validation_id), finalization)


def load_finalization(data_root: Path, project_id: str, validation_id: str) -> Dict[str, Any]:
    path = finalization_path(data_root, project_id, validation_id)
    if not path.is_file():
        raise HandoverValidationError("C06_BOSS_FINALIZATION_NOT_FOUND")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise HandoverValidationError("C06_BOSS_FINALIZATION_INVALID_JSON")


def verify_finalization_data(data_root: Path, project_id: str, validation_id: str) -> Dict[str, Any]:
    finalization = load_finalization(data_root, project_id, validation_id)
    decision = verify_decision_data(data_root, project_id, validation_id)
    try:
        receipt = json.loads((receipt_directory(data_root, project_id, validation_id) / f"{FINAL_RECEIPT_ID}.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise HandoverValidationError("C06_FINALIZATION_RECEIPT_MISSING_OR_INVALID")
    expected_status = "DONE" if finalization.get("bossDecision") == "APPROVED" else "NEEDS_REVIEW"
    expected_operation = "C06_FINALIZE_DONE" if expected_status == "DONE" else "C06_RECORD_BOSS_REJECTION"
    boundary = finalization.get("executionBoundary", {})
    if (
        finalization.get("schemaVersion") != SCHEMA_VERSION or finalization.get("recordType") != "C06_BOSS_FINALIZATION"
        or finalization.get("projectId") != project_id or finalization.get("validationId") != validation_id
        or finalization.get("writerId") != CENTRAL_WRITER or finalization.get("bossDecision") not in {"APPROVED", "REJECTED"}
        or finalization.get("taskId") != decision.get("taskId")
        or finalization.get("validationDecisionDigest") != canonical_digest(decision)
        or receipt.get("recordType") != "C06_IMMUTABLE_FINALIZATION_RECEIPT" or receipt.get("receiptId") != FINAL_RECEIPT_ID
        or receipt.get("beforeFinalizationDigest") != "NONE"
        or receipt.get("afterFinalizationDigest") != canonical_digest(receipt.get("afterFinalization", {}))
        or receipt.get("afterFinalization") != finalization or canonical_digest(finalization) != receipt.get("afterFinalizationDigest")
        or boundary.get("testCreated") is not False or boundary.get("businessWritePerformed") is not False
        or boundary.get("occupancyReleased") is not False
    ):
        raise HandoverValidationError("C06_FINALIZATION_RECEIPT_CHAIN_INVALID")
    linked = linked_ledger_receipt(data_root, project_id, finalization["ledgerMutation"], expected_operation, expected_status)
    window_mutation = finalization.get("windowMutation", {})
    successor = finalization.get("successorDispatch", {})
    snapshot_window = linked.get("afterLedger", {}).get("windows", {}).get(decision.get("windowId"), {})
    if (
        window_mutation.get("windowId") != decision.get("windowId")
        or window_mutation.get("assignmentCount") != window_assignment_count(snapshot_window)
        or window_mutation.get("windowStatusAfter") != snapshot_window.get("status")
        or window_mutation.get("currentTaskIdAfter") != window_current_task_id(snapshot_window)
        or successor.get("required") != (expected_status == "DONE")
        or successor.get("authorizationSource") != "VERIFY_EXISTING_APPROVED_EXECUTION_MAP"
        or successor.get("dispatchOnlyIfScopeVerified") is not True
        or successor.get("bossRepromptRequired") is not False
        or successor.get("completedTaskId") != decision.get("taskId")
        or successor.get("completedWindowId") != decision.get("windowId")
    ):
        raise HandoverValidationError("C06_FINALIZATION_WINDOW_OR_SUCCESSOR_TRIGGER_INVALID")
    return finalization


def finalize(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args)
    project_id = require_reference(args.project_id, "PROJECT_ID_INVALID")
    validation_id = require_reference(args.validation_id, "C06_VALIDATION_ID_INVALID")
    writer_id = require_writer(args.writer_id)
    boss_decision = str(args.boss_decision).upper()
    if boss_decision not in {"APPROVED", "REJECTED"}:
        raise HandoverValidationError("C06_BOSS_FINAL_DECISION_INVALID")
    boss_ref = require_reference(args.boss_decision_ref, "C06_BOSS_FINAL_DECISION_REFERENCE_INVALID")
    decision = verify_decision_data(data_root, project_id, validation_id)
    exception_allowed = False
    if (decision["outcome"] == "NEEDS_REVIEW" and boss_decision == "APPROVED"
            and "acceptancePolicyDigest" not in decision["source"]):
        evidence = decision.get("evidenceAssessment", {})
        scope = decision.get("scopeAssessment", {})
        exception_allowed = (
            isinstance(evidence, dict)
            and evidence.get("beforeSnapshot", {}).get("verdict") == "MISSING"
            and all(evidence.get(key, {}).get("verdict") == "PASS" for key in EVIDENCE_CATEGORIES if key != "beforeSnapshot")
            and isinstance(scope, dict)
            and scope.get("packageScopeMatch") is True and scope.get("rollbackExecutable") is True
            and all(scope.get(key) is False for key in ("parallelMechanismFound", "unknownWriterFound", "permissionExpansionFound", "unexplainedErrorFound", "duplicateDataFound", "blockingResidualRiskFound"))
        )
    if decision["outcome"] != "PASS_PENDING_BOSS_APPROVAL" and not exception_allowed:
        raise HandoverValidationError("C06_ONLY_PASSED_VALIDATION_CAN_BE_FINALIZED")
    if finalization_path(data_root, project_id, validation_id).exists():
        finalization = verify_finalization_data(data_root, project_id, validation_id)
        if finalization["bossDecision"] != boss_decision or finalization["bossDecisionRef"] != boss_ref:
            raise HandoverValidationError("C06_FINALIZATION_ALREADY_EXISTS_WITH_DIFFERENT_DECISION")
        return {
            "status": "IDEMPOTENT_EXISTING_C06_FINALIZATION", "validationId": validation_id,
            "taskId": decision["taskId"], "taskStatusAfter": finalization["ledgerMutation"]["taskStatusAfter"],
            "doneRecorded": finalization["bossDecision"] == "APPROVED",
            "nextDispatchRequired": finalization["successorDispatch"]["required"],
            "completedWindowStatus": finalization["windowMutation"]["windowStatusAfter"],
            "writePerformed": False,
        }, 0
    with validation_lock(data_root, project_id):
        if finalization_path(data_root, project_id, validation_id).exists():
            finalization = verify_finalization_data(data_root, project_id, validation_id)
            if finalization["bossDecision"] != boss_decision or finalization["bossDecisionRef"] != boss_ref:
                raise HandoverValidationError("C06_FINALIZATION_ALREADY_EXISTS_WITH_DIFFERENT_DECISION")
            return {
                "status": "IDEMPOTENT_EXISTING_C06_FINALIZATION", "validationId": validation_id,
                "taskId": decision["taskId"], "taskStatusAfter": finalization["ledgerMutation"]["taskStatusAfter"],
                "doneRecorded": finalization["bossDecision"] == "APPROVED",
                "nextDispatchRequired": finalization["successorDispatch"]["required"],
                "completedWindowStatus": finalization["windowMutation"]["windowStatusAfter"],
                "writePerformed": False,
            }, 0
        with ledger_lock(data_root, project_id):
            _, ledger_exit = verify_ledger(argparse.Namespace(data_root=str(data_root), config=None, project_id=project_id))
            if ledger_exit != 0:
                raise HandoverValidationError("C06_SOURCE_LEDGER_INTEGRITY_UNVERIFIED")
            before = load_ledger(data_root, project_id)
            from ledger_manager import require_current_direction
            require_current_direction(before, decision["taskId"], decision)
            verify_feedback_assessments(data_root, project_id, before, decision["taskId"], decision)
            allowed_conflict = exception_allowed and all(
                stop.get("code") == "C06_VALIDATION_CONFLICT" and stop.get("taskId") == decision["taskId"]
                for stop in before["hardStops"]
            )
            if before["hardStops"] and not allowed_conflict:
                raise HandoverValidationError("C06_SOURCE_LEDGER_HAS_UNRESOLVED_HARD_STOP")
            task = before["tasks"].get(decision["taskId"])
            if not isinstance(task, dict) or task.get("status") != "NEEDS_REVIEW":
                raise HandoverValidationError("C06_FINALIZATION_REQUIRES_NEEDS_REVIEW_TASK")
            target_status = "DONE" if boss_decision == "APPROVED" else "NEEDS_REVIEW"
            window_before = before["windows"].get(decision["windowId"])
            if not isinstance(window_before, dict) or window_current_task_id(window_before) != decision["taskId"]:
                raise HandoverValidationError("C06_FINALIZATION_WINDOW_ASSIGNMENT_MISMATCH")

            def mutate(after: Dict[str, Any]) -> None:
                completed_at = utc_now()
                target = after["tasks"][decision["taskId"]]
                target["status"] = target_status
                target["history"].append({
                    "at": completed_at, "event": "C06_BOSS_FINALIZATION", "validationId": validation_id,
                    "bossDecision": boss_decision, "to": target_status, "by": CENTRAL_WRITER,
                })
                if boss_decision == "APPROVED":
                    target_window = after["windows"][decision["windowId"]]
                    history = [dict(item) for item in window_assignments(target_window)]
                    if not history or history[-1].get("taskId") != decision["taskId"]:
                        raise HandoverValidationError("C06_FINALIZATION_WINDOW_ASSIGNMENT_MISMATCH")
                    history[-1]["status"] = "DONE"
                    history[-1]["completedAt"] = completed_at
                    assignment_count = len(history)
                    target_window["assignmentHistory"] = history
                    target_window["assignmentCount"] = assignment_count
                    target_window["maxAssignments"] = MAX_TASKS_PER_WINDOW
                    target_window["currentTaskId"] = None
                    target_window["status"] = "AVAILABLE_FOR_REUSE" if assignment_count < MAX_TASKS_PER_WINDOW else "RETIRED"
                    target_window["lastCompletedAt"] = completed_at
                    for agent in after["subAgents"].values():
                        if agent.get("windowId") == decision["windowId"] and agent.get("status") in {"REGISTERED", "FROZEN", "DISCONNECTED"}:
                            agent["status"] = "COMPLETED"
                            agent["completedAt"] = completed_at

            operation = "C06_FINALIZE_DONE" if boss_decision == "APPROVED" else "C06_RECORD_BOSS_REJECTION"
            ledger_result = commit_mutation(
                data_root, project_id, before, writer_id, operation,
                {"validationId": validation_id, "taskId": decision["taskId"], "bossDecision": boss_decision}, mutate,
                caller_thread_ref=args.caller_thread_ref,
            )
            after = load_ledger(data_root, project_id)
        window_after = after["windows"][decision["windowId"]]
        assignment_count = window_assignment_count(window_after)
        finalization = {
            "schemaVersion": SCHEMA_VERSION, "recordType": "C06_BOSS_FINALIZATION",
            "validationId": validation_id, "projectId": project_id, "taskId": decision["taskId"],
            "createdAt": utc_now(), "writerId": CENTRAL_WRITER, "validationDecisionDigest": canonical_digest(decision),
            "bossDecision": boss_decision, "bossDecisionRef": boss_ref,
            "bossException": {"kind": "HISTORICAL_BEFORE_SNAPSHOT_UNRECOVERABLE", "accepted": exception_allowed},
            "ledgerMutation": {
                "receiptId": ledger_result["receiptId"], "afterRevision": after["revision"],
                "afterLedgerDigest": canonical_digest(after), "taskId": decision["taskId"], "taskStatusAfter": target_status,
            },
            "windowMutation": {
                "windowId": decision["windowId"],
                "assignmentCount": assignment_count,
                "maxAssignments": MAX_TASKS_PER_WINDOW,
                "currentTaskIdAfter": window_current_task_id(window_after),
                "windowStatusAfter": window_after.get("status"),
                "canReceiveOneMoreTask": boss_decision == "APPROVED" and assignment_count == 1,
            },
            "successorDispatch": {
                "required": boss_decision == "APPROVED",
                "action": "RECALCULATE_AND_DISPATCH_ALL_NEXT_ELIGIBLE_TASKS" if boss_decision == "APPROVED" else "NONE",
                "completedTaskId": decision["taskId"],
                "completedWindowId": decision["windowId"],
                "authorizationSource": "VERIFY_EXISTING_APPROVED_EXECUTION_MAP",
                "dispatchOnlyIfScopeVerified": True,
                "bossRepromptRequired": False,
                "centralChoosesWindow": True,
                "windowMaxAssignments": MAX_TASKS_PER_WINDOW,
                "scopeChangeRequiresBoss": True,
            },
            "executionBoundary": {
                "doneRecorded": boss_decision == "APPROVED", "testCreated": False,
                "businessWritePerformed": False, "occupancyReleased": False,
            },
        }
        persist_finalization(data_root, finalization)
    return {
        "status": "DONE" if boss_decision == "APPROVED" else "NEEDS_REVIEW",
        "validationId": validation_id, "taskId": decision["taskId"], "taskStatusAfter": target_status,
        "doneRecorded": boss_decision == "APPROVED", "nextDispatchRequired": boss_decision == "APPROVED",
        "bossRepromptRequired": False, "completedWindowStatus": window_after.get("status"),
        "windowAssignmentCount": assignment_count, "occupancyReleased": False,
        "businessWriteAllowed": False, "ledgerReceiptId": ledger_result["receiptId"],
        "finalizationReceiptId": FINAL_RECEIPT_ID, "writePerformed": True,
    }, 0


def verify_command(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args)
    project_id = require_reference(args.project_id, "PROJECT_ID_INVALID")
    validation_id = require_reference(args.validation_id, "C06_VALIDATION_ID_INVALID")
    decision = verify_decision_data(data_root, project_id, validation_id)
    finalization = None
    if finalization_path(data_root, project_id, validation_id).exists():
        finalization = verify_finalization_data(data_root, project_id, validation_id)
    return {
        "status": "C06_VALIDATION_INTEGRITY_VERIFIED", "projectId": project_id,
        "validationId": validation_id, "taskId": decision["taskId"], "validationOutcome": decision["outcome"],
        "bossFinalization": finalization["bossDecision"] if finalization else "PENDING",
        "doneRecorded": bool(finalization and finalization["bossDecision"] == "APPROVED"),
        "nextDispatchRequired": bool(finalization and finalization["successorDispatch"]["required"]),
        "businessWriteAllowed": False, "occupancyReleased": False, "writePerformed": False,
    }, 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="C06 independent task handback validator")
    root_source = parser.add_mutually_exclusive_group(required=True)
    root_source.add_argument("--data-root")
    root_source.add_argument("--config")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--writer-id")
    parser.add_argument("--caller-thread-ref")
    commands = parser.add_subparsers(dest="command", required=True)

    assess_command = commands.add_parser("assess")
    assess_command.add_argument("--package-id", required=True)
    handback_source = assess_command.add_mutually_exclusive_group(required=True)
    handback_source.add_argument("--handback")
    handback_source.add_argument("--return-ticket-id")
    assess_command.add_argument("--review", required=True)
    action = assess_command.add_mutually_exclusive_group(required=True)
    action.add_argument("--dry-run", action="store_true")
    action.add_argument("--apply", action="store_true")

    finalize_command = commands.add_parser("finalize")
    finalize_command.add_argument("--validation-id", required=True)
    finalize_command.add_argument("--boss-decision", required=True)
    finalize_command.add_argument("--boss-decision-ref", required=True)

    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--validation-id", required=True)
    return parser.parse_args()


def dispatch(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    return {"assess": assess, "finalize": finalize, "verify": verify_command}[args.command](args)


def main() -> int:
    args = parse_args()
    try:
        result, exit_code = dispatch(args)
        print_result(result)
        return exit_code
    except (HandoverValidationError, TaskPackageError, OccupancyError, LedgerError, C02Error) as error:
        print_result({
            "status": "REFUSED", "reason": str(error), "doneRecorded": False,
            "testCreated": False, "businessWriteAllowed": False, "occupancyReleased": False,
            "writePerformed": False,
            "message": "C06 已停止；未标记 DONE、修改业务对象、创建 TEST 或释放占用。",
        })
        return 2


if __name__ == "__main__":
    sys.exit(main())
