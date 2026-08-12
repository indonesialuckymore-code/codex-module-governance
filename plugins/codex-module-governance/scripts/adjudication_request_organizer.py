#!/usr/bin/env python3
"""C07 adjudication request organizer. Advice never replaces a Boss decision."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from initialize_project import C02Error, PROJECT_ID_PATTERN, is_within, load_data_root
from ledger_manager import (
    CENTRAL_WRITER,
    LedgerError,
    REFERENCE_PATTERN,
    TASK_ID_PATTERN,
    canonical_digest,
    commit_mutation,
    ledger_lock,
    load_ledger,
    verify_ledger,
)


SCHEMA_VERSION = "0.7.0"
ADVISOR_ID = "central-background-advisor"
REQUESTS_DIRECTORY = Path("adjudication-requests")
REQUEST_FILE = "request.json"
ADVICE_FILE = "advice.json"
DECISION_FILE = "boss-decision.json"
HANDOFF_FILE = "task-window-handoff.json"
CENTRAL_IMPACT_FILE = "central-impact-receipt.json"
RECEIPTS_DIRECTORY = "receipts"
SENSITIVE_VALUE_PATTERN = re.compile(
    r"(?:-----BEGIN [A-Z ]+PRIVATE KEY-----|\b(?:sk|ghp|github_pat)_[A-Za-z0-9_-]{12,}|"
    r"(?:cookie|authorization|password)\s*[:=]\s*\S+)", re.IGNORECASE,
)
ABSOLUTE_REFERENCE_PATTERN = re.compile(r"(?:^|\s)(?:~?/|/[A-Za-z]|[A-Za-z]:\\|https?://)")


class AdjudicationError(Exception):
    """Safe refusal that never authorizes task execution."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def print_result(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def require_exact_object(value: Any, required: set[str], error: str) -> Dict[str, Any]:
    if not isinstance(value, dict) or set(value) != required:
        raise AdjudicationError(error)
    return value


def require_reference(value: Any, error: str) -> str:
    if not isinstance(value, str):
        raise AdjudicationError(error)
    candidate = value.strip()
    if not REFERENCE_PATTERN.fullmatch(candidate):
        raise AdjudicationError(error)
    return candidate


def require_project_id(value: Any, error: str) -> str:
    if not isinstance(value, str):
        raise AdjudicationError(error)
    candidate = value.strip()
    if not PROJECT_ID_PATTERN.fullmatch(candidate):
        raise AdjudicationError(error)
    return candidate


def require_task_id(value: Any, error: str) -> str:
    if not isinstance(value, str):
        raise AdjudicationError(error)
    candidate = value.strip()
    if not TASK_ID_PATTERN.fullmatch(candidate):
        raise AdjudicationError(error)
    return candidate


def require_safe_text(value: Any, error: str, maximum: int = 2000) -> str:
    if not isinstance(value, str):
        raise AdjudicationError(error)
    candidate = value.strip()
    if not candidate or len(candidate) > maximum:
        raise AdjudicationError(error)
    if SENSITIVE_VALUE_PATTERN.search(candidate) or ABSOLUTE_REFERENCE_PATTERN.search(candidate):
        raise AdjudicationError("C07_INPUT_CONTAINS_SENSITIVE_OR_LOCATION_DATA")
    return candidate


def require_text_list(value: Any, error: str, allow_empty: bool = False, maximum: int = 20) -> List[str]:
    if not isinstance(value, list) or len(value) > maximum or (not allow_empty and not value):
        raise AdjudicationError(error)
    return [require_safe_text(item, error, 1000) for item in value]


def require_reference_list(value: Any, error: str, allow_empty: bool = False, maximum: int = 40) -> List[str]:
    if not isinstance(value, list) or len(value) > maximum or (not allow_empty and not value):
        raise AdjudicationError(error)
    return [require_reference(item, error) for item in value]


def request_directory(data_root: Path, project_id: str, request_id: str) -> Path:
    return data_root / REQUESTS_DIRECTORY / project_id / request_id


def artifact_path(data_root: Path, project_id: str, request_id: str, filename: str) -> Path:
    return request_directory(data_root, project_id, request_id) / filename


def receipt_directory(data_root: Path, project_id: str, request_id: str) -> Path:
    return request_directory(data_root, project_id, request_id) / RECEIPTS_DIRECTORY


def write_json_exclusive(path: Path, payload: Dict[str, Any]) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        with path.open("x", encoding="utf-8") as output:
            output.write(serialized)
    except FileExistsError:
        raise AdjudicationError("C07_IMMUTABLE_TARGET_ALREADY_EXISTS")


def read_json(path: Path, error: str) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise AdjudicationError(error)
    if not isinstance(payload, dict):
        raise AdjudicationError(error)
    return payload


def load_private_json(raw_path: str, data_root: Path, error: str) -> Tuple[Dict[str, Any], str]:
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file() or not is_within(path, data_root):
        raise AdjudicationError("C07_PRIVATE_INPUT_REQUIRED_INSIDE_DATA_ROOT")
    payload = read_json(path, error)
    return payload, canonical_digest(payload)


@contextmanager
def c07_lock(data_root: Path, project_id: str) -> Iterator[None]:
    root = data_root / REQUESTS_DIRECTORY / project_id
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".adjudication-request-organizer.lock"
    try:
        write_json_exclusive(lock_path, {
            "recordType": "C07_OPERATION_LOCK", "projectId": project_id,
            "createdAt": utc_now(), "pid": os.getpid(),
        })
    except AdjudicationError:
        raise AdjudicationError("C07_OPERATION_LOCK_PRESENT")
    try:
        yield
    finally:
        if lock_path.exists():
            lock_path.unlink()


def require_writer(writer_id: Optional[str]) -> str:
    if writer_id != CENTRAL_WRITER:
        raise AdjudicationError("C07_WRITER_NOT_AUTHORIZED")
    return writer_id


def verified_ledger(data_root: Path, project_id: str) -> Dict[str, Any]:
    try:
        _, exit_code = verify_ledger(argparse.Namespace(data_root=str(data_root), config=None, project_id=project_id))
        if exit_code != 0:
            raise AdjudicationError("C07_SOURCE_LEDGER_INTEGRITY_UNVERIFIED")
        return load_ledger(data_root, project_id)
    except (LedgerError, C02Error) as error:
        raise AdjudicationError(f"C07_SOURCE_LEDGER_{error}")


def validate_request_input(payload: Dict[str, Any], project_id: str) -> Dict[str, Any]:
    required = {
        "requestSchemaVersion", "recordType", "requestId", "projectId", "taskId", "windowId",
        "submittedBy", "question", "whyBossCannotDecide", "knownFacts", "unknowns",
        "impactedObjectRefs", "options", "noActionImpact", "requestedAdvice",
    }
    request = require_exact_object(payload, required, "C07_REQUEST_SCHEMA_UNSUPPORTED")
    if request["requestSchemaVersion"] != SCHEMA_VERSION or request["recordType"] != "C07_ADJUDICATION_REQUEST_INPUT":
        raise AdjudicationError("C07_REQUEST_SCHEMA_UNSUPPORTED")
    if require_project_id(request["projectId"], "C07_PROJECT_ID_INVALID") != project_id:
        raise AdjudicationError("C07_REQUEST_PROJECT_MISMATCH")
    submitted = require_exact_object(request["submittedBy"], {"type", "id"}, "C07_REQUEST_SUBMITTER_INVALID")
    if submitted["type"] != "task-window":
        raise AdjudicationError("C07_REQUEST_MUST_ORIGINATE_FROM_TASK_WINDOW")
    window_id = require_task_id(request["windowId"], "C07_WINDOW_ID_INVALID")
    if require_task_id(submitted["id"], "C07_REQUEST_SUBMITTER_INVALID") != window_id:
        raise AdjudicationError("C07_REQUEST_SUBMITTER_WINDOW_MISMATCH")
    facts = request["knownFacts"]
    if not isinstance(facts, list) or not 1 <= len(facts) <= 20:
        raise AdjudicationError("C07_KNOWN_FACTS_INVALID")
    normalized_facts = []
    fact_ids = set()
    for item in facts:
        fact = require_exact_object(item, {"factId", "statement", "evidenceRefs"}, "C07_KNOWN_FACT_INVALID")
        fact_id = require_reference(fact["factId"], "C07_FACT_ID_INVALID")
        if fact_id in fact_ids:
            raise AdjudicationError("C07_DUPLICATE_FACT_ID")
        fact_ids.add(fact_id)
        normalized_facts.append({
            "factId": fact_id,
            "statement": require_safe_text(fact["statement"], "C07_FACT_STATEMENT_INVALID"),
            "evidenceRefs": require_reference_list(fact["evidenceRefs"], "C07_FACT_EVIDENCE_INVALID"),
        })
    options = request["options"]
    if not isinstance(options, list) or not 2 <= len(options) <= 5:
        raise AdjudicationError("C07_OPTIONS_REQUIRE_TWO_TO_FIVE")
    normalized_options = []
    option_ids = set()
    for item in options:
        option = require_exact_object(
            item, {"optionId", "title", "summary", "benefits", "risks", "reversibility", "requiredEvidenceRefs"},
            "C07_OPTION_INVALID",
        )
        option_id = require_reference(option["optionId"], "C07_OPTION_ID_INVALID")
        if option_id in option_ids:
            raise AdjudicationError("C07_DUPLICATE_OPTION_ID")
        option_ids.add(option_id)
        normalized_options.append({
            "optionId": option_id,
            "title": require_safe_text(option["title"], "C07_OPTION_TITLE_INVALID", 160),
            "summary": require_safe_text(option["summary"], "C07_OPTION_SUMMARY_INVALID"),
            "benefits": require_text_list(option["benefits"], "C07_OPTION_BENEFITS_INVALID"),
            "risks": require_text_list(option["risks"], "C07_OPTION_RISKS_INVALID"),
            "reversibility": require_safe_text(option["reversibility"], "C07_OPTION_REVERSIBILITY_INVALID", 500),
            "requiredEvidenceRefs": require_reference_list(option["requiredEvidenceRefs"], "C07_OPTION_EVIDENCE_INVALID", allow_empty=True),
        })
    return {
        "requestId": require_reference(request["requestId"], "C07_REQUEST_ID_INVALID"),
        "projectId": project_id,
        "taskId": require_task_id(request["taskId"], "C07_TASK_ID_INVALID"),
        "windowId": window_id,
        "submittedBy": {"type": "task-window", "id": window_id},
        "question": require_safe_text(request["question"], "C07_QUESTION_INVALID"),
        "whyBossCannotDecide": require_safe_text(request["whyBossCannotDecide"], "C07_UNDECIDABLE_REASON_INVALID"),
        "knownFacts": normalized_facts,
        "unknowns": require_text_list(request["unknowns"], "C07_UNKNOWNS_INVALID", allow_empty=True),
        "impactedObjectRefs": require_reference_list(request["impactedObjectRefs"], "C07_IMPACTED_OBJECTS_INVALID"),
        "options": normalized_options,
        "noActionImpact": require_safe_text(request["noActionImpact"], "C07_NO_ACTION_IMPACT_INVALID"),
        "requestedAdvice": require_safe_text(request["requestedAdvice"], "C07_REQUESTED_ADVICE_INVALID"),
    }


def require_source_task(ledger: Dict[str, Any], request: Dict[str, Any]) -> None:
    task = ledger["tasks"].get(request["taskId"])
    window = ledger["windows"].get(request["windowId"])
    if not isinstance(task, dict):
        raise AdjudicationError("C07_SOURCE_TASK_NOT_FOUND")
    if task.get("status") not in {"IN_PROGRESS", "BLOCKED", "NEEDS_REVIEW", "PARTIAL", "CONFLICT"}:
        raise AdjudicationError("C07_SOURCE_TASK_NOT_BLOCKED_OR_REVIEWABLE")
    if not isinstance(window, dict) or window.get("taskId") != request["taskId"]:
        raise AdjudicationError("C07_SOURCE_WINDOW_TASK_MISMATCH")


def linked_ledger_receipt(data_root: Path, project_id: str, mutation: Dict[str, Any], operation: str) -> None:
    path = data_root / "module-ledgers" / project_id / "receipts" / f"{mutation['receiptId']}.json"
    receipt = read_json(path, "C07_LINKED_LEDGER_RECEIPT_MISSING")
    if (
        receipt.get("operation") != operation
        or receipt.get("afterRevision") != mutation.get("afterRevision")
        or receipt.get("afterLedgerDigest") != mutation.get("afterLedgerDigest")
        or canonical_digest(receipt.get("afterLedger", {})) != mutation.get("afterLedgerDigest")
    ):
        raise AdjudicationError("C07_LINKED_LEDGER_RECEIPT_INVALID")


def mutation_summary(result: Dict[str, Any], data_root: Path, project_id: str) -> Dict[str, Any]:
    ledger = load_ledger(data_root, project_id)
    return {
        "receiptId": result["receiptId"], "afterRevision": result["revision"],
        "afterLedgerDigest": canonical_digest(ledger),
    }


def c07_receipt(record_type: str, receipt_id: str, operation: str, project_id: str, request_id: str, artifact: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION, "recordType": record_type, "receiptId": receipt_id,
        "createdAt": utc_now(), "projectId": project_id, "requestId": request_id,
        "writerId": CENTRAL_WRITER, "operation": operation,
        "artifactDigest": canonical_digest(artifact), "artifact": artifact,
    }


def public_result(status: str, request_id: str, task_id: str, write_performed: bool, **extra: Any) -> Dict[str, Any]:
    return {
        "status": status, "requestId": request_id, "taskId": task_id,
        "adviceSentToBossOnly": status in {"ADVICE_RECORDED_FOR_BOSS", "IDEMPOTENT_ADVICE"},
        "bossDecisionRequired": status not in {"BOSS_DECISION_RECORDED", "IDEMPOTENT_BOSS_DECISION"},
        "taskWindowHandoffCreated": status in {"BOSS_DECISION_RECORDED", "IDEMPOTENT_BOSS_DECISION"},
        "centralImpactReceiptCreated": status in {"BOSS_DECISION_RECORDED", "IDEMPOTENT_BOSS_DECISION"},
        "taskStatusChanged": False, "automaticExecutionAllowed": False, "businessWriteAllowed": False,
        "occupancyChanged": False, "writePerformed": write_performed,
        "message": "C07 只整理裁定材料；未修改任务状态、对象占用或业务系统。",
        **extra,
    }


def prepare(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args)
    project_id = require_project_id(args.project_id, "C07_PROJECT_ID_INVALID")
    raw, input_digest = load_private_json(args.request, data_root, "C07_REQUEST_INVALID_JSON")
    request = validate_request_input(raw, project_id)
    ledger = verified_ledger(data_root, project_id)
    require_source_task(ledger, request)
    if args.dry_run:
        return public_result("READY_TO_PREPARE_ADVISORY_REQUEST", request["requestId"], request["taskId"], False), 0
    require_writer(args.writer_id)
    with c07_lock(data_root, project_id), ledger_lock(data_root, project_id):
        if request_directory(data_root, project_id, request["requestId"]).exists():
            existing = read_json(artifact_path(data_root, project_id, request["requestId"], REQUEST_FILE), "C07_REQUEST_INVALID")
            if existing.get("sourceInputDigest") == input_digest:
                return public_result("IDEMPOTENT_REQUEST", request["requestId"], request["taskId"], False), 0
            raise AdjudicationError("C07_REQUEST_ID_ALREADY_EXISTS_WITH_DIFFERENT_CONTENT")
        ledger = load_ledger(data_root, project_id)
        require_source_task(ledger, request)
        if request["requestId"] in ledger["adjudications"]:
            raise AdjudicationError("C07_LEDGER_ADJUDICATION_ID_ALREADY_EXISTS")
        before_status = ledger["tasks"][request["taskId"]]["status"]

        def mutate(after: Dict[str, Any]) -> None:
            after["adjudications"][request["requestId"]] = {
                "adjudicationId": request["requestId"], "taskId": request["taskId"],
                "windowId": request["windowId"], "stage": "REQUESTED",
                "requestRef": request["requestId"], "recordedAt": utc_now(),
            }

        ledger_result = commit_mutation(
            data_root, project_id, ledger, CENTRAL_WRITER, "C07_RECORD_ADJUDICATION_REQUEST",
            {"requestId": request["requestId"], "taskId": request["taskId"], "taskStatusUnchanged": before_status}, mutate,
        )
        mutation = mutation_summary(ledger_result, data_root, project_id)
        artifact = {
            "schemaVersion": SCHEMA_VERSION, "recordType": "C07_ADJUDICATION_REQUEST",
            "createdAt": utc_now(), "writerId": CENTRAL_WRITER, **request,
            "sourceInputDigest": input_digest,
            "sourceLedger": {"beforeRevision": ledger["revision"], "beforeDigest": canonical_digest(ledger), **mutation},
            "routingBoundary": {
                "advisor": ADVISOR_ID, "targetRole": "CURRENT_ADJUDICATION",
                "requiresCurrentRoleResolution": True, "incrementalContextOnly": True,
                "adviceReturnsToBossOnly": True, "bossDecisionRequired": True,
                "taskWindowMayUseAdviceDirectly": False, "taskStateChanged": False,
                "businessWriteAllowed": False, "occupancyChanged": False,
            },
        }
        root = request_directory(data_root, project_id, request["requestId"])
        root.mkdir(parents=True, exist_ok=False)
        receipt_directory(data_root, project_id, request["requestId"]).mkdir(exist_ok=False)
        receipt = c07_receipt("C07_IMMUTABLE_REQUEST_RECEIPT", "receipt-000000-request", "PREPARE_ADJUDICATION_REQUEST", project_id, request["requestId"], artifact)
        write_json_exclusive(receipt_directory(data_root, project_id, request["requestId"]) / "receipt-000000-request.json", receipt)
        write_json_exclusive(artifact_path(data_root, project_id, request["requestId"], REQUEST_FILE), artifact)
    return public_result("ADVISORY_REQUEST_PREPARED", request["requestId"], request["taskId"], True, ledgerReceiptId=mutation["receiptId"]), 0


def load_verified_request(data_root: Path, project_id: str, request_id: str) -> Dict[str, Any]:
    request = read_json(artifact_path(data_root, project_id, request_id, REQUEST_FILE), "C07_REQUEST_NOT_FOUND_OR_INVALID")
    receipt = read_json(receipt_directory(data_root, project_id, request_id) / "receipt-000000-request.json", "C07_REQUEST_RECEIPT_MISSING")
    if (
        request.get("recordType") != "C07_ADJUDICATION_REQUEST" or request.get("projectId") != project_id
        or request.get("requestId") != request_id or receipt.get("artifact") != request
        or receipt.get("artifactDigest") != canonical_digest(request)
        or receipt.get("recordType") != "C07_IMMUTABLE_REQUEST_RECEIPT"
        or receipt.get("operation") != "PREPARE_ADJUDICATION_REQUEST"
        or receipt.get("projectId") != project_id or receipt.get("requestId") != request_id
    ):
        raise AdjudicationError("C07_REQUEST_INTEGRITY_INVALID")
    linked_ledger_receipt(data_root, project_id, request["sourceLedger"], "C07_RECORD_ADJUDICATION_REQUEST")
    return request


def validate_advice(payload: Dict[str, Any], request: Dict[str, Any]) -> Dict[str, Any]:
    required = {"adviceSchemaVersion", "recordType", "requestId", "advisor", "factAssessment", "recommendation", "unknowns", "evidenceGaps", "bossQuestions"}
    advice = require_exact_object(payload, required, "C07_ADVICE_SCHEMA_UNSUPPORTED")
    if advice["adviceSchemaVersion"] != SCHEMA_VERSION or advice["recordType"] != "C07_ADVISORY_OPINION":
        raise AdjudicationError("C07_ADVICE_SCHEMA_UNSUPPORTED")
    if require_reference(advice["requestId"], "C07_REQUEST_ID_INVALID") != request["requestId"]:
        raise AdjudicationError("C07_ADVICE_REQUEST_MISMATCH")
    advisor = require_exact_object(advice["advisor"], {"id", "contextMode"}, "C07_ADVISOR_INVALID")
    if advisor != {"id": ADVISOR_ID, "contextMode": "INDEPENDENT"}:
        raise AdjudicationError("C07_ADVISOR_INVALID")
    assessments = advice["factAssessment"]
    if not isinstance(assessments, list) or len(assessments) != len(request["knownFacts"]):
        raise AdjudicationError("C07_FACT_ASSESSMENT_INCOMPLETE")
    known_ids = {item["factId"] for item in request["knownFacts"]}
    normalized_assessments = []
    seen = set()
    for item in assessments:
        assessment = require_exact_object(item, {"factId", "verdict", "rationale"}, "C07_FACT_ASSESSMENT_INVALID")
        fact_id = require_reference(assessment["factId"], "C07_FACT_ID_INVALID")
        verdict = require_safe_text(assessment["verdict"], "C07_FACT_VERDICT_INVALID", 20).upper()
        if fact_id not in known_ids or fact_id in seen or verdict not in {"VERIFIED", "UNVERIFIED", "CONFLICTING"}:
            raise AdjudicationError("C07_FACT_ASSESSMENT_INVALID")
        seen.add(fact_id)
        normalized_assessments.append({"factId": fact_id, "verdict": verdict, "rationale": require_safe_text(assessment["rationale"], "C07_FACT_RATIONALE_INVALID")})
    recommendation = require_exact_object(advice["recommendation"], {"status", "optionId", "rationale", "conditions", "tradeoffs"}, "C07_RECOMMENDATION_INVALID")
    status = require_safe_text(recommendation["status"], "C07_RECOMMENDATION_INVALID", 40).upper()
    if status not in {"RECOMMEND_OPTION", "REQUEST_MORE_EVIDENCE", "NO_SAFE_RECOMMENDATION"}:
        raise AdjudicationError("C07_RECOMMENDATION_INVALID")
    option_id = recommendation["optionId"]
    valid_options = {item["optionId"] for item in request["options"]}
    if status == "RECOMMEND_OPTION":
        option_id = require_reference(option_id, "C07_RECOMMENDED_OPTION_INVALID")
        if option_id not in valid_options:
            raise AdjudicationError("C07_RECOMMENDED_OPTION_OUTSIDE_REQUEST")
    elif option_id is not None:
        raise AdjudicationError("C07_RECOMMENDATION_OPTION_MUST_BE_NULL")
    boss_questions = require_text_list(advice["bossQuestions"], "C07_BOSS_QUESTIONS_INVALID", allow_empty=True, maximum=3)
    return {
        "requestId": request["requestId"], "advisor": advisor,
        "factAssessment": normalized_assessments,
        "recommendation": {
            "status": status, "optionId": option_id,
            "rationale": require_safe_text(recommendation["rationale"], "C07_RECOMMENDATION_RATIONALE_INVALID"),
            "conditions": require_text_list(recommendation["conditions"], "C07_RECOMMENDATION_CONDITIONS_INVALID", allow_empty=True),
            "tradeoffs": require_text_list(recommendation["tradeoffs"], "C07_RECOMMENDATION_TRADEOFFS_INVALID", allow_empty=True),
        },
        "unknowns": require_text_list(advice["unknowns"], "C07_ADVICE_UNKNOWNS_INVALID", allow_empty=True),
        "evidenceGaps": require_text_list(advice["evidenceGaps"], "C07_EVIDENCE_GAPS_INVALID", allow_empty=True),
        "bossQuestions": boss_questions,
    }


def record_advice(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args)
    project_id = require_project_id(args.project_id, "C07_PROJECT_ID_INVALID")
    request_id = require_reference(args.request_id, "C07_REQUEST_ID_INVALID")
    request = load_verified_request(data_root, project_id, request_id)
    raw, input_digest = load_private_json(args.advice, data_root, "C07_ADVICE_INVALID_JSON")
    advice = validate_advice(raw, request)
    require_writer(args.writer_id)
    target = artifact_path(data_root, project_id, request_id, ADVICE_FILE)
    if target.exists():
        existing = read_json(target, "C07_ADVICE_INVALID")
        if existing.get("sourceInputDigest") == input_digest:
            return public_result("IDEMPOTENT_ADVICE", request_id, request["taskId"], False), 0
        raise AdjudicationError("C07_ADVICE_ALREADY_EXISTS_WITH_DIFFERENT_CONTENT")
    with c07_lock(data_root, project_id), ledger_lock(data_root, project_id):
        ledger = load_ledger(data_root, project_id)
        entry = ledger["adjudications"].get(request_id)
        if not isinstance(entry, dict) or entry.get("stage") != "REQUESTED":
            raise AdjudicationError("C07_ADVICE_REQUIRES_REQUESTED_STAGE")
        task_status = ledger["tasks"][request["taskId"]]["status"]

        def mutate(after: Dict[str, Any]) -> None:
            target_entry = after["adjudications"][request_id]
            target_entry.update({"stage": "ADVICE_RECEIVED", "adviceRef": request_id, "adviceRecordedAt": utc_now()})

        ledger_result = commit_mutation(data_root, project_id, ledger, CENTRAL_WRITER, "C07_RECORD_ADVISORY_OPINION", {"requestId": request_id, "taskStatusUnchanged": task_status}, mutate)
        mutation = mutation_summary(ledger_result, data_root, project_id)
        artifact = {
            "schemaVersion": SCHEMA_VERSION, "recordType": "C07_ADVISORY_OPINION_FOR_BOSS",
            "createdAt": utc_now(), "projectId": project_id, "writerId": CENTRAL_WRITER, **advice,
            "sourceInputDigest": input_digest, "requestDigest": canonical_digest(request), "ledgerMutation": mutation,
            "routingBoundary": {
                "recipient": "boss", "taskWindowRecipient": False, "bossDecisionRequired": True,
                "adviceIsAuthorization": False, "taskStateChanged": False,
                "businessWriteAllowed": False, "occupancyChanged": False,
            },
        }
        receipt = c07_receipt("C07_IMMUTABLE_ADVICE_RECEIPT", "receipt-000001-advice", "RECORD_ADVISORY_OPINION", project_id, request_id, artifact)
        write_json_exclusive(receipt_directory(data_root, project_id, request_id) / "receipt-000001-advice.json", receipt)
        write_json_exclusive(target, artifact)
    return public_result("ADVICE_RECORDED_FOR_BOSS", request_id, request["taskId"], True, recommendation=advice["recommendation"]["status"], ledgerReceiptId=mutation["receiptId"]), 0


def load_verified_advice(data_root: Path, project_id: str, request_id: str, request: Dict[str, Any]) -> Dict[str, Any]:
    advice = read_json(artifact_path(data_root, project_id, request_id, ADVICE_FILE), "C07_ADVICE_NOT_FOUND_OR_INVALID")
    receipt = read_json(receipt_directory(data_root, project_id, request_id) / "receipt-000001-advice.json", "C07_ADVICE_RECEIPT_MISSING")
    if (
        advice.get("requestDigest") != canonical_digest(request)
        or advice.get("recordType") != "C07_ADVISORY_OPINION_FOR_BOSS"
        or advice.get("projectId") != project_id
        or receipt.get("artifact") != advice or receipt.get("artifactDigest") != canonical_digest(advice)
        or receipt.get("recordType") != "C07_IMMUTABLE_ADVICE_RECEIPT"
        or receipt.get("operation") != "RECORD_ADVISORY_OPINION"
        or receipt.get("projectId") != project_id or receipt.get("requestId") != request_id
    ):
        raise AdjudicationError("C07_ADVICE_INTEGRITY_INVALID")
    linked_ledger_receipt(data_root, project_id, advice["ledgerMutation"], "C07_RECORD_ADVISORY_OPINION")
    return advice


def validate_boss_decision(payload: Dict[str, Any], request: Dict[str, Any]) -> Dict[str, Any]:
    required = {"decisionSchemaVersion", "recordType", "requestId", "status", "selectedOptionId", "rationale", "decisionRef", "instructionToOriginalWindow", "taskEffect", "dependencyEffect", "conditions", "invalidWhen"}
    decision = require_exact_object(payload, required, "C07_BOSS_DECISION_SCHEMA_UNSUPPORTED")
    if decision["decisionSchemaVersion"] != SCHEMA_VERSION or decision["recordType"] != "C07_BOSS_DECISION_INPUT":
        raise AdjudicationError("C07_BOSS_DECISION_SCHEMA_UNSUPPORTED")
    if require_reference(decision["requestId"], "C07_REQUEST_ID_INVALID") != request["requestId"]:
        raise AdjudicationError("C07_BOSS_DECISION_REQUEST_MISMATCH")
    status = require_safe_text(decision["status"], "C07_BOSS_DECISION_STATUS_INVALID", 40).upper()
    if status not in {"SELECT_OPTION", "REQUEST_MORE_EVIDENCE", "PAUSE", "REJECT_ALL"}:
        raise AdjudicationError("C07_BOSS_DECISION_STATUS_INVALID")
    selected = decision["selectedOptionId"]
    if status == "SELECT_OPTION":
        selected = require_reference(selected, "C07_BOSS_SELECTED_OPTION_INVALID")
        if selected not in {item["optionId"] for item in request["options"]}:
            raise AdjudicationError("C07_BOSS_SELECTED_OPTION_OUTSIDE_REQUEST")
    elif selected is not None:
        raise AdjudicationError("C07_BOSS_SELECTED_OPTION_MUST_BE_NULL")
    task_effect = require_safe_text(decision["taskEffect"], "C07_TASK_EFFECT_INVALID", 60).upper()
    if task_effect not in {"CONTINUE_ORIGINAL_WINDOW", "KEEP_BLOCKED", "REPLAN_REQUIRED", "CANCEL_RECOMMENDED"}:
        raise AdjudicationError("C07_TASK_EFFECT_INVALID")
    dependency_effect = require_safe_text(decision["dependencyEffect"], "C07_DEPENDENCY_EFFECT_INVALID", 60).upper()
    if dependency_effect not in {"NO_CHANGE", "RECALCULATE_AFFECTED_TASKS"}:
        raise AdjudicationError("C07_DEPENDENCY_EFFECT_INVALID")
    return {
        "requestId": request["requestId"], "status": status, "selectedOptionId": selected,
        "rationale": require_safe_text(decision["rationale"], "C07_BOSS_DECISION_RATIONALE_INVALID"),
        "decisionRef": require_reference(decision["decisionRef"], "C07_BOSS_DECISION_REFERENCE_INVALID"),
        "instructionToOriginalWindow": require_safe_text(decision["instructionToOriginalWindow"], "C07_BOSS_INSTRUCTION_INVALID"),
        "taskEffect": task_effect, "dependencyEffect": dependency_effect,
        "conditions": require_text_list(decision["conditions"], "C07_DECISION_CONDITIONS_INVALID", allow_empty=True),
        "invalidWhen": require_text_list(decision["invalidWhen"], "C07_DECISION_INVALID_WHEN_INVALID", allow_empty=True),
    }


def record_boss_decision(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args)
    project_id = require_project_id(args.project_id, "C07_PROJECT_ID_INVALID")
    request_id = require_reference(args.request_id, "C07_REQUEST_ID_INVALID")
    request = load_verified_request(data_root, project_id, request_id)
    advice = load_verified_advice(data_root, project_id, request_id, request)
    raw, input_digest = load_private_json(args.decision, data_root, "C07_BOSS_DECISION_INVALID_JSON")
    decision = validate_boss_decision(raw, request)
    require_writer(args.writer_id)
    target = artifact_path(data_root, project_id, request_id, DECISION_FILE)
    if target.exists():
        existing = read_json(target, "C07_BOSS_DECISION_INVALID")
        if existing.get("sourceInputDigest") == input_digest:
            return public_result("IDEMPOTENT_BOSS_DECISION", request_id, request["taskId"], False), 0
        raise AdjudicationError("C07_BOSS_DECISION_ALREADY_EXISTS_WITH_DIFFERENT_CONTENT")
    with c07_lock(data_root, project_id), ledger_lock(data_root, project_id):
        ledger = load_ledger(data_root, project_id)
        entry = ledger["adjudications"].get(request_id)
        if not isinstance(entry, dict) or entry.get("stage") != "ADVICE_RECEIVED":
            raise AdjudicationError("C07_BOSS_DECISION_REQUIRES_ADVICE_STAGE")
        task_status = ledger["tasks"][request["taskId"]]["status"]

        def mutate(after: Dict[str, Any]) -> None:
            target_entry = after["adjudications"][request_id]
            target_entry.update({
                "stage": "BOSS_DECIDED", "bossDecisionRef": decision["decisionRef"],
                "bossDecisionStatus": decision["status"], "bossDecisionRecordedAt": utc_now(),
            })

        ledger_result = commit_mutation(data_root, project_id, ledger, CENTRAL_WRITER, "C07_RECORD_BOSS_ADJUDICATION_DECISION", {"requestId": request_id, "decisionStatus": decision["status"], "taskStatusUnchanged": task_status}, mutate)
        mutation = mutation_summary(ledger_result, data_root, project_id)
        artifact = {
            "schemaVersion": SCHEMA_VERSION, "recordType": "C07_BOSS_ADJUDICATION_DECISION",
            "createdAt": utc_now(), "projectId": project_id, "writerId": CENTRAL_WRITER, **decision,
            "sourceInputDigest": input_digest, "requestDigest": canonical_digest(request),
            "adviceDigest": canonical_digest(advice), "ledgerMutation": mutation,
            "executionBoundary": {
                "originalWindowId": request["windowId"], "taskStateChanged": False,
                "automaticExecutionAllowed": False, "scopeExpansionAllowed": False,
                "businessWritePerformed": False, "occupancyChanged": False,
            },
        }
        handoff = {
            "schemaVersion": SCHEMA_VERSION, "recordType": "C07_TASK_WINDOW_ADJUDICATION_HANDOFF",
            "createdAt": utc_now(), "projectId": project_id, "requestId": request_id,
            "taskId": request["taskId"], "windowId": request["windowId"],
            "bossDecisionStatus": decision["status"], "selectedOptionId": decision["selectedOptionId"],
            "decisionRef": decision["decisionRef"], "instruction": decision["instructionToOriginalWindow"],
            "requestDigest": canonical_digest(request), "adviceDigest": canonical_digest(advice),
            "bossDecisionDigest": canonical_digest(artifact),
            "boundary": {
                "recipientMustBeOriginalWindow": True, "adviceAloneIsNotAuthorization": True,
                "automaticExecutionAllowed": False, "scopeExpansionAllowed": False,
                "taskStateChanged": False, "businessWritePerformed": False,
            },
        }
        task = ledger["tasks"][request["taskId"]]
        canonical_title = task.get("canonicalTitle", f"{request['taskId']}｜{task['title']}")
        central_impact = {
            "schemaVersion": SCHEMA_VERSION, "recordType": "C07_COMPACT_CENTRAL_DECISION_IMPACT",
            "createdAt": utc_now(), "projectId": project_id, "requestId": request_id,
            "taskIdentity": {"taskId": request["taskId"], "canonicalTitle": canonical_title},
            "bossDecisionStatus": decision["status"], "selectedOptionId": decision["selectedOptionId"],
            "decisionRef": decision["decisionRef"], "taskEffect": decision["taskEffect"],
            "dependencyEffect": decision["dependencyEffect"], "conditions": decision["conditions"],
            "invalidWhen": decision["invalidWhen"], "originalWindowId": request["windowId"],
            "bossDecisionDigest": canonical_digest(artifact),
            "boundary": {
                "targetRole": "CURRENT_CENTRAL", "fullDiscussionExcluded": True,
                "adviceTextExcluded": True, "rationaleExcluded": True,
                "ledgerUpdatedByReceipt": False, "automaticExecutionAllowed": False,
                "businessWritePerformed": False,
            },
        }
        decision_receipt = c07_receipt("C07_IMMUTABLE_BOSS_DECISION_RECEIPT", "receipt-000002-boss-decision", "RECORD_BOSS_DECISION", project_id, request_id, artifact)
        handoff_receipt = c07_receipt("C07_IMMUTABLE_HANDOFF_RECEIPT", "receipt-000003-window-handoff", "CREATE_ORIGINAL_WINDOW_HANDOFF", project_id, request_id, handoff)
        impact_receipt = c07_receipt("C07_IMMUTABLE_CENTRAL_IMPACT_RECEIPT", "receipt-000004-central-impact", "CREATE_COMPACT_CENTRAL_IMPACT", project_id, request_id, central_impact)
        write_json_exclusive(receipt_directory(data_root, project_id, request_id) / "receipt-000002-boss-decision.json", decision_receipt)
        write_json_exclusive(receipt_directory(data_root, project_id, request_id) / "receipt-000003-window-handoff.json", handoff_receipt)
        write_json_exclusive(receipt_directory(data_root, project_id, request_id) / "receipt-000004-central-impact.json", impact_receipt)
        write_json_exclusive(target, artifact)
        write_json_exclusive(artifact_path(data_root, project_id, request_id, HANDOFF_FILE), handoff)
        write_json_exclusive(artifact_path(data_root, project_id, request_id, CENTRAL_IMPACT_FILE), central_impact)
    return public_result("BOSS_DECISION_RECORDED", request_id, request["taskId"], True, decisionStatus=decision["status"], selectedOptionId=decision["selectedOptionId"], originalWindowId=request["windowId"], ledgerReceiptId=mutation["receiptId"]), 0


def verify_command(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    data_root = load_data_root(args)
    project_id = require_project_id(args.project_id, "C07_PROJECT_ID_INVALID")
    request_id = require_reference(args.request_id, "C07_REQUEST_ID_INVALID")
    request = load_verified_request(data_root, project_id, request_id)
    stage = "REQUESTED"
    advice = None
    decision = None
    if artifact_path(data_root, project_id, request_id, ADVICE_FILE).exists():
        advice = load_verified_advice(data_root, project_id, request_id, request)
        stage = "ADVICE_RECEIVED"
    if artifact_path(data_root, project_id, request_id, DECISION_FILE).exists():
        if advice is None:
            raise AdjudicationError("C07_BOSS_DECISION_WITHOUT_ADVICE")
        decision = read_json(artifact_path(data_root, project_id, request_id, DECISION_FILE), "C07_BOSS_DECISION_INVALID")
        handoff = read_json(artifact_path(data_root, project_id, request_id, HANDOFF_FILE), "C07_HANDOFF_INVALID")
        decision_receipt = read_json(receipt_directory(data_root, project_id, request_id) / "receipt-000002-boss-decision.json", "C07_BOSS_DECISION_RECEIPT_MISSING")
        handoff_receipt = read_json(receipt_directory(data_root, project_id, request_id) / "receipt-000003-window-handoff.json", "C07_HANDOFF_RECEIPT_MISSING")
        central_impact = read_json(artifact_path(data_root, project_id, request_id, CENTRAL_IMPACT_FILE), "C07_CENTRAL_IMPACT_MISSING")
        impact_receipt = read_json(receipt_directory(data_root, project_id, request_id) / "receipt-000004-central-impact.json", "C07_CENTRAL_IMPACT_RECEIPT_MISSING")
        if (
            decision.get("requestDigest") != canonical_digest(request) or decision.get("adviceDigest") != canonical_digest(advice)
            or decision.get("recordType") != "C07_BOSS_ADJUDICATION_DECISION" or decision.get("projectId") != project_id
            or decision_receipt.get("artifact") != decision or decision_receipt.get("artifactDigest") != canonical_digest(decision)
            or decision_receipt.get("recordType") != "C07_IMMUTABLE_BOSS_DECISION_RECEIPT"
            or decision_receipt.get("operation") != "RECORD_BOSS_DECISION"
            or handoff.get("bossDecisionDigest") != canonical_digest(decision)
            or handoff.get("projectId") != project_id or handoff.get("requestId") != request_id
            or handoff.get("windowId") != request["windowId"] or handoff_receipt.get("artifact") != handoff
            or handoff_receipt.get("artifactDigest") != canonical_digest(handoff)
            or handoff_receipt.get("recordType") != "C07_IMMUTABLE_HANDOFF_RECEIPT"
            or handoff_receipt.get("operation") != "CREATE_ORIGINAL_WINDOW_HANDOFF"
            or central_impact.get("bossDecisionDigest") != canonical_digest(decision)
            or central_impact.get("boundary", {}).get("fullDiscussionExcluded") is not True
            or "rationale" in central_impact or "advice" in central_impact
            or impact_receipt.get("artifact") != central_impact
            or impact_receipt.get("artifactDigest") != canonical_digest(central_impact)
            or impact_receipt.get("recordType") != "C07_IMMUTABLE_CENTRAL_IMPACT_RECEIPT"
            or impact_receipt.get("operation") != "CREATE_COMPACT_CENTRAL_IMPACT"
        ):
            raise AdjudicationError("C07_BOSS_DECISION_OR_HANDOFF_INTEGRITY_INVALID")
        linked_ledger_receipt(data_root, project_id, decision["ledgerMutation"], "C07_RECORD_BOSS_ADJUDICATION_DECISION")
        stage = "BOSS_DECIDED"
    ledger = verified_ledger(data_root, project_id)
    entry = ledger["adjudications"].get(request_id)
    if not isinstance(entry, dict) or entry.get("stage") != stage:
        raise AdjudicationError("C07_LEDGER_STAGE_MISMATCH")
    return {
        "status": "C07_ADJUDICATION_INTEGRITY_VERIFIED", "projectId": project_id,
        "requestId": request_id, "taskId": request["taskId"], "stage": stage,
        "bossDecisionStatus": decision["status"] if decision else "PENDING",
        "taskStatus": ledger["tasks"][request["taskId"]]["status"],
        "taskStatusChangedByC07": False, "automaticExecutionAllowed": False,
        "businessWriteAllowed": False, "occupancyChanged": False, "writePerformed": False,
    }, 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="C07 adjudication request organizer")
    root_source = parser.add_mutually_exclusive_group(required=True)
    root_source.add_argument("--data-root")
    root_source.add_argument("--config")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--writer-id")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("--request", required=True)
    action = prepare_parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--dry-run", action="store_true")
    action.add_argument("--apply", action="store_true")
    advice_parser = commands.add_parser("record-advice")
    advice_parser.add_argument("--request-id", required=True)
    advice_parser.add_argument("--advice", required=True)
    decision_parser = commands.add_parser("record-boss-decision")
    decision_parser.add_argument("--request-id", required=True)
    decision_parser.add_argument("--decision", required=True)
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--request-id", required=True)
    return parser.parse_args()


def dispatch(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    return {
        "prepare": prepare,
        "record-advice": record_advice,
        "record-boss-decision": record_boss_decision,
        "verify": verify_command,
    }[args.command](args)


def main() -> int:
    args = parse_args()
    try:
        result, exit_code = dispatch(args)
        print_result(result)
        return exit_code
    except (AdjudicationError, LedgerError, C02Error) as error:
        print_result({
            "status": "REFUSED", "reason": str(error), "taskStatusChanged": False,
            "automaticExecutionAllowed": False, "businessWriteAllowed": False,
            "occupancyChanged": False, "writePerformed": False,
            "message": "C07 已停止；未替 Boss 裁定、修改任务状态、占用或业务对象。",
        })
        return 2


if __name__ == "__main__":
    sys.exit(main())
