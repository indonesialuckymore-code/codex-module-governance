#!/usr/bin/env python3
"""C10 two-phase task-window and first-level sub-agent dispatch controller."""

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
    canonical_digest,
    commit_mutation,
    ledger_lock,
    load_ledger,
    utc_now,
    window_assignment_count,
    window_assignments,
    window_current_task_id,
)
from occupancy_conflict_checker import OccupancyError, verify_decision_data
from task_package_generator import TaskPackageError, load_package, verify_package


SCHEMA_VERSION = "0.15.0"
ROOT = Path("dispatches")
REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
WINDOW_ID = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{2,127}$")
DIGEST = re.compile(r"^[a-f0-9]{64}$")


class DispatchError(Exception):
    """Safe refusal: no dispatch completion may be inferred."""


def print_result(value: Dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def require_ref(value: Any, error: str, pattern: re.Pattern = REFERENCE) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value.strip()):
        raise DispatchError(error)
    return value.strip()


def exact(value: Any, keys: set[str], error: str) -> Dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise DispatchError(error)
    return value


def dispatch_dir(root: Path, project_id: str, dispatch_id: str) -> Path:
    return root / ROOT / project_id / dispatch_id


def plan_path(root: Path, project_id: str, dispatch_id: str) -> Path:
    return dispatch_dir(root, project_id, dispatch_id) / "dispatch-plan.json"


def confirmation_path(root: Path, project_id: str, dispatch_id: str) -> Path:
    return dispatch_dir(root, project_id, dispatch_id) / "dispatch-confirmation.json"


def fallback_path(root: Path, project_id: str, dispatch_id: str) -> Path:
    return dispatch_dir(root, project_id, dispatch_id) / "manual-task-package.json"


def return_path(root: Path, project_id: str, dispatch_id: str, sub_agent_id: str) -> Path:
    return dispatch_dir(root, project_id, dispatch_id) / "sub-agent-returns" / f"{sub_agent_id}.json"


def append_plan_path(root: Path, project_id: str, dispatch_id: str, append_id: str) -> Path:
    return dispatch_dir(root, project_id, dispatch_id) / "sub-agent-appends" / append_id / "append-plan.json"


def append_confirmation_path(root: Path, project_id: str, dispatch_id: str, append_id: str) -> Path:
    return dispatch_dir(root, project_id, dispatch_id) / "sub-agent-appends" / append_id / "append-confirmation.json"


def parent_quality_review_path(root: Path, project_id: str, dispatch_id: str) -> Path:
    return dispatch_dir(root, project_id, dispatch_id) / "parent-quality-review.json"


def write_exclusive(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    except FileExistsError:
        raise DispatchError("C10_IMMUTABLE_TARGET_ALREADY_EXISTS")


def read_json(path: Path, error: str) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise DispatchError(error)
    if not isinstance(value, dict):
        raise DispatchError(error)
    return value


def load_private(path_value: str, root: Path, error: str) -> Tuple[Dict[str, Any], str]:
    path = Path(path_value).expanduser().resolve()
    if not path.is_file() or not is_within(path, root):
        raise DispatchError("C10_INPUT_MUST_BE_INSIDE_PRIVATE_DATA_ROOT")
    value = read_json(path, error)
    return value, canonical_digest(value)


@contextmanager
def dispatch_lock(root: Path, project_id: str) -> Iterator[None]:
    lock_root = root / ROOT / project_id
    lock_root.mkdir(parents=True, exist_ok=True)
    lock = lock_root / ".c10.lock"
    try:
        with lock.open("x", encoding="utf-8") as stream:
            stream.write(str(os.getpid()))
    except FileExistsError:
        raise DispatchError("C10_OPERATION_LOCK_PRESENT")
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def validate_runtime_project(raw: Any) -> Dict[str, Any]:
    value = exact(raw, {"codexProjectId", "projectPath", "isGitRepository", "environment"}, "C10_RUNTIME_PROJECT_SCHEMA_INVALID")
    codex_project_id = require_ref(value["codexProjectId"], "C10_CODEX_PROJECT_ID_INVALID")
    if not isinstance(value["projectPath"], str):
        raise DispatchError("C10_CODEX_PROJECT_PATH_INVALID")
    project_path = Path(value["projectPath"]).expanduser()
    if not project_path.is_absolute() or ".." in project_path.parts:
        raise DispatchError("C10_CODEX_PROJECT_PATH_INVALID")
    if not isinstance(value["isGitRepository"], bool):
        raise DispatchError("C10_CODEX_PROJECT_GIT_FLAG_INVALID")
    environment = str(value["environment"]).strip().upper()
    if environment not in {"LOCAL", "WORKTREE"}:
        raise DispatchError("C10_RUNTIME_ENVIRONMENT_INVALID")
    if environment == "WORKTREE" and not value["isGitRepository"]:
        raise DispatchError("C10_NON_GIT_PROJECT_CANNOT_USE_WORKTREE")
    return {
        "codexProjectId": codex_project_id,
        "projectPath": str(project_path),
        "projectName": project_path.name,
        "isGitRepository": value["isGitRepository"],
        "environment": environment,
    }


def validate_authorization(raw: Any, task_id: str) -> Dict[str, Any]:
    auth = exact(raw, {"status", "reference", "scope"}, "C10_BOSS_AUTHORIZATION_INVALID")
    if auth["status"] != "APPROVED":
        raise DispatchError("C10_EXPLICIT_BOSS_DISPATCH_APPROVAL_REQUIRED")
    scope = exact(auth["scope"], {"type", "scopeId", "scopeDigest", "waveId", "taskIds"}, "C10_BOSS_AUTHORIZATION_SCOPE_INVALID")
    scope_type = str(scope["type"]).strip().upper()
    if scope_type not in {"SINGLE_TASK", "EXECUTION_MAP"}:
        raise DispatchError("C10_BOSS_AUTHORIZATION_SCOPE_INVALID")
    task_ids = scope["taskIds"]
    if not isinstance(task_ids, list) or not task_ids or len(task_ids) > 100:
        raise DispatchError("C10_BOSS_AUTHORIZATION_TASKS_INVALID")
    normalized_task_ids = [require_ref(item, "C10_BOSS_AUTHORIZATION_TASKS_INVALID", WINDOW_ID) for item in task_ids]
    if len(normalized_task_ids) != len(set(normalized_task_ids)) or task_id not in normalized_task_ids:
        raise DispatchError("C10_TASK_OUTSIDE_BOSS_AUTHORIZATION_SCOPE")
    if scope_type == "SINGLE_TASK" and normalized_task_ids != [task_id]:
        raise DispatchError("C10_SINGLE_TASK_AUTHORIZATION_SCOPE_INVALID")
    return {
        "reference": require_ref(auth["reference"], "C10_BOSS_APPROVAL_REFERENCE_INVALID"),
        "scope": {
            "type": scope_type,
            "scopeId": require_ref(scope["scopeId"], "C10_BOSS_AUTHORIZATION_SCOPE_ID_INVALID"),
            "scopeDigest": require_ref(scope["scopeDigest"], "C10_BOSS_AUTHORIZATION_SCOPE_DIGEST_INVALID", DIGEST),
            "waveId": require_ref(scope["waveId"], "C10_BOSS_AUTHORIZATION_WAVE_ID_INVALID"),
            "taskIds": normalized_task_ids,
        },
    }


def validate_sub_agent_specs(raw_agents: Any, error: str = "C10_SUB_AGENT_SCHEMA_INVALID") -> List[Dict[str, str]]:
    """Validate a bounded, independently checkable first-level delegation plan."""
    if not isinstance(raw_agents, list) or len(raw_agents) > 3:
        raise DispatchError("C10_FIRST_LEVEL_SUB_AGENT_LIMIT_EXCEEDED")
    allowed_reasons = {"LARGE_MATERIALS", "INDEPENDENT_SCOPE", "INDEPENDENT_VERIFICATION", "CROSS_CHECK"}
    allowed_modes = {"READ_ONLY", "ISOLATED_WORK", "INDEPENDENT_VALIDATION"}
    normalized: List[Dict[str, str]] = []
    for agent in raw_agents:
        item = exact(agent, {"subAgentId", "role", "level", "delegationReason", "executionMode", "scope"}, error)
        if item["level"] != 1:
            raise DispatchError("C10_GRANDCHILD_SUB_AGENT_FORBIDDEN")
        role = str(item["role"]).strip()
        scope = str(item["scope"]).strip()
        reason = str(item["delegationReason"]).strip().upper()
        mode = str(item["executionMode"]).strip().upper()
        if not role or len(role) > 160 or not scope or len(scope) > 600:
            raise DispatchError("C10_SUB_AGENT_ROLE_OR_SCOPE_INVALID")
        if reason not in allowed_reasons:
            raise DispatchError("C10_SUB_AGENT_DELEGATION_REASON_INVALID")
        if mode not in allowed_modes:
            raise DispatchError("C10_SUB_AGENT_EXECUTION_MODE_INVALID")
        normalized.append({
            "subAgentId": require_ref(item["subAgentId"], "C10_SUB_AGENT_ID_INVALID", WINDOW_ID),
            "role": role,
            "level": 1,
            "delegationReason": reason,
            "executionMode": mode,
            "scope": scope,
        })
    if len({x["subAgentId"] for x in normalized}) != len(normalized):
        raise DispatchError("C10_DUPLICATE_SUB_AGENT_ID")
    return normalized


def validate_request(raw: Dict[str, Any], project_id: str, package_id: str, review_id: str) -> Dict[str, Any]:
    value = exact(raw, {"dispatchSchemaVersion", "recordType", "dispatchId", "projectId", "packageId", "reviewId", "taskId", "runtimeProject", "bossDispatchAuthorization", "subAgents"}, "C10_DISPATCH_REQUEST_SCHEMA_UNSUPPORTED")
    if value["dispatchSchemaVersion"] != SCHEMA_VERSION or value["recordType"] != "C10_DISPATCH_REQUEST" or value["projectId"] != project_id or value["packageId"] != package_id or value["reviewId"] != review_id:
        raise DispatchError("C10_DISPATCH_REQUEST_SCHEMA_UNSUPPORTED")
    task_id = require_ref(value["taskId"], "C10_TASK_ID_INVALID", WINDOW_ID)
    authorization = validate_authorization(value["bossDispatchAuthorization"], task_id)
    runtime_project = validate_runtime_project(value["runtimeProject"])
    normalized = validate_sub_agent_specs(value["subAgents"])
    return {
        "dispatchId": require_ref(value["dispatchId"], "C10_DISPATCH_ID_INVALID"),
        "projectId": project_id,
        "packageId": package_id,
        "reviewId": review_id,
        "taskId": task_id,
        "bossApprovalRef": authorization["reference"],
        "authorizationScope": authorization["scope"],
        "runtimeProject": runtime_project,
        "subAgents": normalized,
    }


def verified_sources(root: Path, project_id: str, package_id: str, review_id: str, task_id: str) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    try:
        _, package_code = verify_package(argparse.Namespace(data_root=str(root), config=None, project_id=project_id, package_id=package_id))
        if package_code != 0:
            raise DispatchError("C10_C04_PACKAGE_INTEGRITY_UNVERIFIED")
        package = load_package(root, project_id, package_id)
        decision = verify_decision_data(root, project_id, review_id)
        ledger = load_ledger(root, project_id)
    except (C02Error, LedgerError, OccupancyError, TaskPackageError) as error:
        raise DispatchError(f"C10_SOURCE_{error}")
    if package.get("taskId") != task_id or decision.get("taskId") != task_id or decision.get("packageId") != package_id:
        raise DispatchError("C10_SOURCE_TASK_OR_PACKAGE_MISMATCH")
    task = ledger.get("tasks", {}).get(task_id, {})
    canonical_title = task.get("canonicalTitle", f"{task_id}｜{task.get('title', '')}")
    identity = package.get("taskIdentity", {})
    if (
        identity.get("taskId") != task_id
        or identity.get("canonicalTitle") != canonical_title
        or package.get("displayName") != f"{canonical_title}｜任务包"
    ):
        raise DispatchError("C10_TASK_IDENTITY_MISMATCH")
    if decision.get("status") != "ELIGIBLE_FOR_DISPATCH_APPROVAL" or decision.get("executionBoundary", {}).get("dispatchEligibility") != "ELIGIBLE":
        raise DispatchError("C10_C05_DISPATCH_ELIGIBILITY_REQUIRED")
    if ledger.get("recovery", {}).get("state") not in {"NORMAL", "CLOSED"}:
        raise DispatchError("C10_RECOVERY_STATE_BLOCKS_DISPATCH")
    if ledger.get("hardStops"):
        raise DispatchError("C10_LEDGER_HARD_STOP_BLOCKS_DISPATCH")
    if ledger.get("tasks", {}).get(task_id, {}).get("status") != "READY":
        raise DispatchError("C10_TASK_MUST_BE_READY")
    return package, decision, ledger


def classify(decision: Dict[str, Any]) -> str:
    requests = decision.get("requestedOccupancies", [])
    if requests and all(item.get("intent") == "READ" and not item.get("exclusive") for item in requests):
        return "GREEN"
    return "YELLOW"


def next_task_generation(ledger: Dict[str, Any], task_id: str) -> int:
    generations = []
    for window in ledger["windows"].values():
        for assignment in window_assignments(window):
            if assignment.get("taskId") == task_id and isinstance(assignment.get("generation"), int):
                generations.append(assignment["generation"])
    return max(generations, default=0) + 1


def window_ready_for_second_assignment(window: Dict[str, Any], ledger: Dict[str, Any], task_id: str, reservation_id: Optional[str]) -> bool:
    if window_assignment_count(window) != 1:
        return False
    previous_task_id = window.get("taskId")
    if ledger["tasks"].get(previous_task_id, {}).get("status") != "DONE":
        return False
    explicit = window.get("status") == "AVAILABLE_FOR_REUSE" and window_current_task_id(window) is None
    legacy = "assignmentHistory" not in window and window.get("status") == "REGISTERED"
    reserved = (
        window.get("status") == "RESERVED_FOR_REUSE"
        and window_current_task_id(window) is None
        and window.get("reservedForTaskId") == task_id
        and reservation_id is not None
        and window.get("reuseReservationId") == reservation_id
    )
    return explicit or legacy or reserved


def prepare(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    root = load_data_root(args); project_id = require_ref(args.project_id, "C10_PROJECT_ID_INVALID")
    if not PROJECT_ID_PATTERN.fullmatch(project_id): raise DispatchError("C10_PROJECT_ID_INVALID")
    package_id = require_ref(args.package_id, "C10_PACKAGE_ID_INVALID"); review_id = require_ref(args.review_id, "C10_REVIEW_ID_INVALID")
    raw, request_digest = load_private(args.request, root, "C10_DISPATCH_REQUEST_INVALID_JSON")
    request = validate_request(raw, project_id, package_id, review_id)
    if args.writer_id != CENTRAL_WRITER: raise DispatchError("C10_WRITER_NOT_AUTHORIZED")
    target = plan_path(root, project_id, request["dispatchId"])
    if target.exists():
        existing = read_json(target, "C10_EXISTING_PLAN_INVALID")
        if existing.get("source", {}).get("requestDigest") == request_digest:
            return {
                "status": "IDEMPOTENT_EXISTING_DISPATCH_PLAN",
                "dispatchId": request["dispatchId"],
                "trafficLight": existing["trafficLight"],
                "windowAction": existing.get("windowAction"),
                "taskIdentity": existing.get("taskIdentity"),
                "writePerformed": False,
                "dispatchPerformed": False,
            }, 0
        raise DispatchError("C10_DISPATCH_ID_REUSED_WITH_DIFFERENT_REQUEST")
    package, decision, ledger = verified_sources(root, project_id, package_id, review_id, request["taskId"])
    window = decision["windowDecision"]
    if window.get("status") not in {"OPEN_NEW_WINDOW", "REUSE_EXISTING_WINDOW"}:
        raise DispatchError("C10_WINDOW_DECISION_NOT_ACTIONABLE")
    light = classify(decision)
    environment_type = request["runtimeProject"]["environment"].lower()
    association_protocol = {
        "primary": "CREATE_REQUESTED_TARGET",
        "onMissingProjectId": "HANDOFF_TO_PROJECT_LOCAL_THEN_RETURN" if request["runtimeProject"]["environment"] == "WORKTREE" else "STOP_NEEDS_REVIEW",
        "requiresFinalProjectReadback": True,
    }
    task = ledger["tasks"][request["taskId"]]
    canonical_title = task.get("canonicalTitle", f"{request['taskId']}｜{task['title']}")
    previous_task_id = None
    assignment_number = 1
    reuse_type = None
    if window["status"] == "OPEN_NEW_WINDOW":
        generation = next_task_generation(ledger, request["taskId"])
    else:
        existing_window = ledger["windows"].get(window.get("windowId"))
        if not isinstance(existing_window, dict):
            raise DispatchError("C10_REUSED_WINDOW_NOT_FOUND")
        reuse_type = window.get("reuseType")
        assignment_number = window.get("assignmentNumber", window_assignment_count(existing_window) + 1)
        if reuse_type == "CURRENT_ASSIGNMENT":
            if window_current_task_id(existing_window) != request["taskId"]:
                raise DispatchError("C10_TASK_IDENTITY_MISMATCH")
            generation = existing_window.get("generation", 1)
            assignment_number = window_assignment_count(existing_window)
        else:
            previous_task_id = existing_window.get("taskId")
            if (
                assignment_number != 2
                or not window_ready_for_second_assignment(existing_window, ledger, request["taskId"], window.get("reuseReservationId"))
            ):
                raise DispatchError("C10_WINDOW_NOT_ELIGIBLE_FOR_SECOND_ASSIGNMENT")
            generation = next_task_generation(ledger, request["taskId"])
    runtime_title = f"{canonical_title}｜G{generation}"
    task_identity = {"taskId": request["taskId"], "canonicalTitle": canonical_title, "runtimeTitle": runtime_title, "generation": generation, "assignmentNumber": assignment_number}
    plan = {
        "schemaVersion": SCHEMA_VERSION, "recordType": "C10_DISPATCH_PLAN", "dispatchId": request["dispatchId"],
        "createdAt": utc_now(), "projectId": project_id, "packageId": package_id, "reviewId": review_id,
        "taskId": request["taskId"], "status": "PENDING_RUNTIME_CONFIRMATION", "trafficLight": light,
        "taskIdentity": task_identity,
        "source": {"requestDigest": request_digest, "packageDigest": canonical_digest(package), "c05DecisionDigest": canonical_digest(decision), "ledgerRevision": ledger["revision"], "ledgerDigest": canonical_digest(ledger)},
        "bossApprovalRef": request["bossApprovalRef"],
        "authorizationScope": request["authorizationScope"],
        "runtimeProject": request["runtimeProject"],
        "runtimeTarget": {"type": "project", "projectId": request["runtimeProject"]["codexProjectId"], "environment": {"type": environment_type}},
        "projectAssociationProtocol": association_protocol,
        "windowAction": {"action": "CREATE_TASK" if window["status"] == "OPEN_NEW_WINDOW" else "SEND_TO_EXISTING_TASK", "windowId": window.get("windowId"), "model": "gpt-5.6-terra", "contextMode": "NEW" if window["status"] == "OPEN_NEW_WINDOW" else "REUSED", "generation": generation, "runtimeTitle": runtime_title, "assignmentNumber": assignment_number, "maxAssignments": MAX_TASKS_PER_WINDOW, "reuseType": reuse_type, "reuseReservationId": window.get("reuseReservationId"), "previousTaskId": previous_task_id, "renameRequired": bool(previous_task_id)},
        "subAgents": [{**item, "model": "gpt-5.6-terra", "parent": "TASK_WINDOW"} for item in request["subAgents"]],
        "taskPackage": {key: package[key] for key in ("displayName", "taskIdentity", "task", "requiredReading", "realTimeChecks", "allowedActions", "forbiddenActions", "preflightSnapshot", "executionSequence", "acceptance", "hardStops", "rollbackPlan", "deliverables", "handbackRule")},
        "boundary": {"runtimeCallPerformed": False, "ledgerUpdated": False, "businessWritePerformed": False, "requiresAllRuntimeResultsBeforeConfirm": True, "completionReturnsToParentWindow": True},
    }
    with dispatch_lock(root, project_id):
        if target.exists(): raise DispatchError("C10_DISPATCH_PLAN_RACE_DETECTED")
        write_exclusive(target, plan)
    return {"status": "READY_FOR_RUNTIME_DISPATCH", "dispatchId": request["dispatchId"], "trafficLight": light, "taskIdentity": task_identity, "windowAction": plan["windowAction"], "runtimeTarget": plan["runtimeTarget"], "projectAssociationProtocol": association_protocol, "authorizationScope": plan["authorizationScope"], "subAgentCount": len(plan["subAgents"]), "writePerformed": True, "dispatchPerformed": False, "businessWritePerformed": False}, 0


def validate_confirmation(raw: Dict[str, Any], dispatch_id: str, plan: Dict[str, Any]) -> Dict[str, Any]:
    value = exact(raw, {"confirmationSchemaVersion", "recordType", "dispatchId", "taskWindow", "subAgents"}, "C10_CONFIRMATION_SCHEMA_UNSUPPORTED")
    if value["confirmationSchemaVersion"] != SCHEMA_VERSION or value["recordType"] != "C10_RUNTIME_CONFIRMATION" or value["dispatchId"] != dispatch_id:
        raise DispatchError("C10_CONFIRMATION_SCHEMA_UNSUPPORTED")
    window = exact(value["taskWindow"], {"status", "taskId", "runtimeTitle", "generation", "windowId", "runtimeThreadRef", "runtimeProjectId", "runtimeCwd", "environmentType", "associationMethod", "associationHandoffRefs"}, "C10_WINDOW_CONFIRMATION_INVALID")
    expected = "CREATED" if plan["windowAction"]["action"] == "CREATE_TASK" else "REUSED"
    if window["status"] != expected: raise DispatchError("C10_WINDOW_RUNTIME_NOT_CONFIRMED")
    if (
        window["taskId"] != plan["taskIdentity"]["taskId"]
        or window["runtimeTitle"] != plan["taskIdentity"]["runtimeTitle"]
        or window["generation"] != plan["taskIdentity"]["generation"]
    ):
        raise DispatchError("C10_TASK_IDENTITY_MISMATCH")
    window_id = require_ref(window["windowId"], "C10_WINDOW_ID_INVALID", WINDOW_ID)
    if expected == "REUSED" and window_id != plan["windowAction"]["windowId"]: raise DispatchError("C10_REUSED_WINDOW_MISMATCH")
    runtime_ref = require_ref(window["runtimeThreadRef"], "C10_RUNTIME_THREAD_REF_INVALID")
    association_method = str(window["associationMethod"]).strip().upper()
    if association_method not in {"DIRECT", "LOCAL_HANDOFF_ROUNDTRIP"}:
        raise DispatchError("C10_PROJECT_ASSOCIATION_METHOD_INVALID")
    raw_handoff_refs = window["associationHandoffRefs"]
    if not isinstance(raw_handoff_refs, list):
        raise DispatchError("C10_PROJECT_ASSOCIATION_REPAIR_EVIDENCE_INVALID")
    handoff_refs = [require_ref(item, "C10_PROJECT_ASSOCIATION_REPAIR_EVIDENCE_INVALID") for item in raw_handoff_refs]
    if association_method == "DIRECT" and handoff_refs:
        raise DispatchError("C10_PROJECT_ASSOCIATION_REPAIR_EVIDENCE_INVALID")
    if association_method == "LOCAL_HANDOFF_ROUNDTRIP":
        if len(handoff_refs) != 2 or len(set(handoff_refs)) != 2 or runtime_ref in handoff_refs:
            raise DispatchError("C10_PROJECT_ASSOCIATION_REPAIR_EVIDENCE_INVALID")
    runtime_project = plan.get("runtimeProject", {})
    if require_ref(window["runtimeProjectId"], "C10_RUNTIME_PROJECT_ID_INVALID") != runtime_project.get("codexProjectId"):
        raise DispatchError("C10_RUNTIME_PROJECT_ASSOCIATION_MISMATCH")
    environment_type = str(window["environmentType"]).strip().upper()
    if environment_type != runtime_project.get("environment"):
        raise DispatchError("C10_RUNTIME_ENVIRONMENT_MISMATCH")
    if not isinstance(window["runtimeCwd"], str):
        raise DispatchError("C10_RUNTIME_CWD_INVALID")
    runtime_cwd = Path(window["runtimeCwd"]).expanduser()
    if not runtime_cwd.is_absolute() or ".." in runtime_cwd.parts:
        raise DispatchError("C10_RUNTIME_CWD_INVALID")
    project_path = Path(runtime_project["projectPath"])
    if environment_type == "LOCAL":
        if runtime_cwd != project_path:
            raise DispatchError("C10_LOCAL_TASK_OUTSIDE_SAVED_PROJECT")
    else:
        parts = runtime_cwd.parts
        if runtime_cwd == project_path or ".codex" not in parts or "worktrees" not in parts or runtime_cwd.name != runtime_project["projectName"]:
            raise DispatchError("C10_NONSTANDARD_WORKTREE_TASK_LOCATION")
    agents = value["subAgents"]
    if not isinstance(agents, list) or len(agents) != len(plan["subAgents"]): raise DispatchError("C10_SUB_AGENT_CONFIRMATION_COUNT_MISMATCH")
    expected_agents = {x["subAgentId"]: x for x in plan["subAgents"]}; normalized = []
    for raw_agent in agents:
        agent = exact(raw_agent, {"subAgentId", "status", "runtimeAgentRef"}, "C10_SUB_AGENT_CONFIRMATION_INVALID")
        agent_id = require_ref(agent["subAgentId"], "C10_SUB_AGENT_ID_INVALID", WINDOW_ID)
        if agent_id not in expected_agents or agent["status"] != "CREATED": raise DispatchError("C10_SUB_AGENT_RUNTIME_NOT_CONFIRMED")
        normalized.append({"subAgentId": agent_id, "runtimeAgentRef": require_ref(agent["runtimeAgentRef"], "C10_RUNTIME_AGENT_REF_INVALID")})
    if len({x["subAgentId"] for x in normalized}) != len(normalized): raise DispatchError("C10_DUPLICATE_SUB_AGENT_CONFIRMATION")
    return {"taskId": window["taskId"], "runtimeTitle": window["runtimeTitle"], "generation": window["generation"], "windowId": window_id, "runtimeThreadRef": runtime_ref, "runtimeProjectId": runtime_project["codexProjectId"], "runtimeCwd": str(runtime_cwd), "environmentType": environment_type, "associationMethod": association_method, "associationHandoffRefs": handoff_refs, "agents": normalized}


def confirm(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    root = load_data_root(args); project_id = require_ref(args.project_id, "C10_PROJECT_ID_INVALID"); dispatch_id = require_ref(args.dispatch_id, "C10_DISPATCH_ID_INVALID")
    if args.writer_id != CENTRAL_WRITER: raise DispatchError("C10_WRITER_NOT_AUTHORIZED")
    plan = read_json(plan_path(root, project_id, dispatch_id), "C10_DISPATCH_PLAN_NOT_FOUND_OR_INVALID")
    raw, confirmation_digest = load_private(args.confirmation, root, "C10_CONFIRMATION_INVALID_JSON")
    confirmation = validate_confirmation(raw, dispatch_id, plan)
    target = confirmation_path(root, project_id, dispatch_id)
    if target.exists():
        existing = read_json(target, "C10_EXISTING_CONFIRMATION_INVALID")
        if existing.get("sourceConfirmationDigest") == confirmation_digest:
            return {"status": "IDEMPOTENT_RUNTIME_CONFIRMATION", "dispatchId": dispatch_id, "taskStatus": "IN_PROGRESS", "writePerformed": False, "dispatchPerformed": True}, 0
        raise DispatchError("C10_CONFIRMATION_ALREADY_EXISTS_WITH_DIFFERENT_CONTENT")
    with dispatch_lock(root, project_id), ledger_lock(root, project_id):
        before = load_ledger(root, project_id)
        if before.get("recovery", {}).get("state") not in {"NORMAL", "CLOSED"}: raise DispatchError("C10_RECOVERY_STATE_BLOCKS_CONFIRMATION")
        task = before.get("tasks", {}).get(plan["taskId"])
        if not isinstance(task, dict) or task.get("status") != "READY": raise DispatchError("C10_TASK_MUST_BE_READY_AT_CONFIRMATION")
        if plan["windowAction"]["action"] == "CREATE_TASK" and confirmation["windowId"] in before["windows"]: raise DispatchError("C10_WINDOW_ID_ALREADY_REGISTERED")
        if plan["windowAction"]["action"] == "SEND_TO_EXISTING_TASK" and confirmation["windowId"] not in before["windows"]: raise DispatchError("C10_REUSED_WINDOW_NOT_FOUND")
        if plan["windowAction"]["action"] == "SEND_TO_EXISTING_TASK":
            existing_window = before["windows"][confirmation["windowId"]]
            if plan["windowAction"].get("reuseType") == "CURRENT_ASSIGNMENT":
                if window_current_task_id(existing_window) != plan["taskId"]:
                    raise DispatchError("C10_TASK_IDENTITY_MISMATCH")
            elif (
                not window_ready_for_second_assignment(existing_window, before, plan["taskId"], plan["windowAction"].get("reuseReservationId"))
            ):
                raise DispatchError("C10_WINDOW_NOT_ELIGIBLE_FOR_SECOND_ASSIGNMENT")
        active = [x for x in before["subAgents"].values() if x.get("windowId") == confirmation["windowId"] and x.get("status") in {"REGISTERED", "FROZEN", "DISCONNECTED"}]
        if len(active) + len(confirmation["agents"]) > 3: raise DispatchError("C10_FIRST_LEVEL_SUB_AGENT_LIMIT_EXCEEDED")
        agent_runtime = {x["subAgentId"]: x["runtimeAgentRef"] for x in confirmation["agents"]}
        plan_agents = {x["subAgentId"]: x for x in plan["subAgents"]}
        def mutate(after: Dict[str, Any]) -> None:
            assigned_at = utc_now()
            if plan["windowAction"]["action"] == "CREATE_TASK":
                assignment = {"assignmentNumber": 1, "taskId": plan["taskId"], "canonicalTitle": plan["taskIdentity"]["canonicalTitle"], "generation": confirmation["generation"], "runtimeTitle": confirmation["runtimeTitle"], "contextMode": "NEW", "dispatchId": dispatch_id, "assignedAt": assigned_at, "completedAt": None, "status": "ACTIVE"}
                after["windows"][confirmation["windowId"]] = {"windowId": confirmation["windowId"], "taskId": plan["taskId"], "currentTaskId": plan["taskId"], "canonicalTitle": plan["taskIdentity"]["canonicalTitle"], "generation": confirmation["generation"], "runtimeTitle": confirmation["runtimeTitle"], "model": "gpt-5.6-terra", "contextMode": "NEW", "status": "REGISTERED", "maxAssignments": MAX_TASKS_PER_WINDOW, "assignmentCount": 1, "assignmentHistory": [assignment], "runtimeThreadRef": confirmation["runtimeThreadRef"], "runtimeProjectId": confirmation["runtimeProjectId"], "environmentType": confirmation["environmentType"], "associationMethod": confirmation["associationMethod"], "dispatchId": dispatch_id, "registeredAt": assigned_at}
            else:
                target_window = after["windows"][confirmation["windowId"]]
                if plan["windowAction"].get("reuseType") == "CURRENT_ASSIGNMENT":
                    if target_window.get("runtimeTitle") != confirmation["runtimeTitle"]:
                        raise DispatchError("C10_TASK_IDENTITY_MISMATCH")
                    target_window.setdefault("maxAssignments", MAX_TASKS_PER_WINDOW)
                    target_window.setdefault("assignmentCount", window_assignment_count(target_window))
                    target_window.setdefault("assignmentHistory", window_assignments(target_window))
                    target_window.setdefault("currentTaskId", plan["taskId"])
                    target_window["assignmentHistory"][-1]["dispatchId"] = dispatch_id
                    target_window["assignmentHistory"][-1]["status"] = "ACTIVE"
                else:
                    history = [dict(item) for item in window_assignments(target_window)]
                    history[-1]["status"] = "DONE"
                    history[-1]["completedAt"] = history[-1].get("completedAt") or assigned_at
                    history.append({"assignmentNumber": 2, "taskId": plan["taskId"], "canonicalTitle": plan["taskIdentity"]["canonicalTitle"], "generation": confirmation["generation"], "runtimeTitle": confirmation["runtimeTitle"], "contextMode": "REUSED", "dispatchId": dispatch_id, "assignedAt": assigned_at, "completedAt": None, "status": "ACTIVE"})
                    target_window.update({"taskId": plan["taskId"], "currentTaskId": plan["taskId"], "canonicalTitle": plan["taskIdentity"]["canonicalTitle"], "generation": confirmation["generation"], "runtimeTitle": confirmation["runtimeTitle"], "contextMode": "REUSED", "status": "REGISTERED", "maxAssignments": MAX_TASKS_PER_WINDOW, "assignmentCount": 2, "assignmentHistory": history})
                    target_window.pop("reservedForTaskId", None)
                    target_window.pop("reuseReservationId", None)
                    target_window.pop("reuseReservedAt", None)
                target_window["runtimeThreadRef"] = confirmation["runtimeThreadRef"]
                target_window["runtimeProjectId"] = confirmation["runtimeProjectId"]
                target_window["environmentType"] = confirmation["environmentType"]
                target_window["associationMethod"] = confirmation["associationMethod"]
                target_window["dispatchId"] = dispatch_id
            for agent_id, runtime_ref in agent_runtime.items():
                if agent_id in after["subAgents"]: raise DispatchError("C10_SUB_AGENT_ID_ALREADY_REGISTERED")
                spec = plan_agents[agent_id]
                after["subAgents"][agent_id] = {
                    "subAgentId": agent_id, "windowId": confirmation["windowId"], "taskId": plan["taskId"],
                    "role": spec["role"], "scope": spec["scope"], "delegationReason": spec["delegationReason"],
                    "executionMode": spec["executionMode"], "model": "gpt-5.6-terra", "level": 1,
                    "status": "REGISTERED", "runtimeAgentRef": runtime_ref, "dispatchId": dispatch_id,
                    "registeredAt": utc_now(),
                }
            target_task = after["tasks"][plan["taskId"]]; target_task["status"] = "IN_PROGRESS"
            target_task["history"].append({"at": utc_now(), "event": "C10_RUNTIME_DISPATCH_CONFIRMED", "dispatchId": dispatch_id, "windowId": confirmation["windowId"], "from": "READY", "to": "IN_PROGRESS", "by": CENTRAL_WRITER})
        result = commit_mutation(root, project_id, before, CENTRAL_WRITER, "C10_CONFIRM_RUNTIME_DISPATCH", {"dispatchId": dispatch_id, "taskId": plan["taskId"], "windowId": confirmation["windowId"], "subAgentCount": len(confirmation["agents"])}, mutate)
        artifact = {"schemaVersion": SCHEMA_VERSION, "recordType": "C10_DISPATCH_CONFIRMATION", "dispatchId": dispatch_id, "confirmedAt": utc_now(), "projectId": project_id, "taskId": plan["taskId"], "taskIdentity": plan["taskIdentity"], "windowId": confirmation["windowId"], "windowAssignment": {"assignmentNumber": plan["windowAction"]["assignmentNumber"], "maxAssignments": MAX_TASKS_PER_WINDOW, "isFinalAllowedAssignment": plan["windowAction"]["assignmentNumber"] == MAX_TASKS_PER_WINDOW}, "runtimeThreadRef": confirmation["runtimeThreadRef"], "runtimeProjectId": confirmation["runtimeProjectId"], "runtimeCwd": confirmation["runtimeCwd"], "environmentType": confirmation["environmentType"], "associationMethod": confirmation["associationMethod"], "associationHandoffRefs": confirmation["associationHandoffRefs"], "authorizationScope": plan["authorizationScope"], "subAgents": confirmation["agents"], "sourceConfirmationDigest": confirmation_digest, "ledgerReceiptId": result["receiptId"], "boundary": {"runtimeConfirmed": True, "projectAssociationVerified": True, "taskStatus": "IN_PROGRESS", "businessWritePerformed": False, "completionMustReturnToParentWindow": True, "parentQualityGateRequiredIfSubAgentsUsed": bool(confirmation["agents"])}}
        write_exclusive(target, artifact)
    return {"status": "RUNTIME_DISPATCH_CONFIRMED", "dispatchId": dispatch_id, "windowId": confirmation["windowId"], "assignmentNumber": plan["windowAction"]["assignmentNumber"], "maxAssignments": MAX_TASKS_PER_WINDOW, "associationMethod": confirmation["associationMethod"], "subAgentCount": len(confirmation["agents"]), "taskStatus": "IN_PROGRESS", "ledgerReceiptId": result["receiptId"], "writePerformed": True, "dispatchPerformed": True, "businessWritePerformed": False}, 0


def task_window_agents(ledger: Dict[str, Any], window_id: str, task_id: str) -> Dict[str, Dict[str, Any]]:
    """Return only the first-level helpers belonging to this live task assignment."""
    return {
        agent_id: agent for agent_id, agent in ledger.get("subAgents", {}).items()
        if isinstance(agent, dict) and agent.get("windowId") == window_id
        and agent.get("taskId") == task_id and agent.get("level") == 1
    }


def validate_append_request(raw: Dict[str, Any], project_id: str, dispatch_id: str, confirmation: Dict[str, Any]) -> Dict[str, Any]:
    value = exact(raw, {"appendSchemaVersion", "recordType", "appendId", "dispatchId", "projectId", "taskId", "windowId", "trigger", "subAgents"}, "C10_APPEND_REQUEST_SCHEMA_UNSUPPORTED")
    if (
        value["appendSchemaVersion"] != SCHEMA_VERSION or value["recordType"] != "C10_SUB_AGENT_APPEND_REQUEST"
        or value["dispatchId"] != dispatch_id or value["projectId"] != project_id
        or value["taskId"] != confirmation.get("taskId") or value["windowId"] != confirmation.get("windowId")
    ):
        raise DispatchError("C10_APPEND_REQUEST_SCOPE_MISMATCH")
    trigger = exact(value["trigger"], {"phase", "delegationReason", "withinApprovedScope", "sharedWriteRisk", "summary"}, "C10_APPEND_TRIGGER_INVALID")
    phase = str(trigger["phase"]).strip().upper()
    reason = str(trigger["delegationReason"]).strip().upper()
    summary = str(trigger["summary"]).strip()
    if phase not in {"PRE_EXECUTION", "DISCOVERY", "PRE_VALIDATION"} or reason not in {"LARGE_MATERIALS", "INDEPENDENT_SCOPE", "INDEPENDENT_VERIFICATION", "CROSS_CHECK"}:
        raise DispatchError("C10_APPEND_TRIGGER_INVALID")
    if trigger["withinApprovedScope"] is not True or trigger["sharedWriteRisk"] is not False or not summary or len(summary) > 600:
        raise DispatchError("C10_APPEND_SCOPE_OR_WRITE_RISK_NOT_ACCEPTABLE")
    return {
        "appendId": require_ref(value["appendId"], "C10_APPEND_ID_INVALID"), "dispatchId": dispatch_id,
        "projectId": project_id, "taskId": confirmation["taskId"], "windowId": confirmation["windowId"],
        "trigger": {"phase": phase, "delegationReason": reason, "withinApprovedScope": True, "sharedWriteRisk": False, "summary": summary},
        "subAgents": validate_sub_agent_specs(value["subAgents"], "C10_APPEND_SUB_AGENT_SCHEMA_INVALID"),
    }


def prepare_append(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    root = load_data_root(args); project_id = require_ref(args.project_id, "C10_PROJECT_ID_INVALID")
    dispatch_id = require_ref(args.dispatch_id, "C10_DISPATCH_ID_INVALID")
    if args.writer_id != CENTRAL_WRITER: raise DispatchError("C10_WRITER_NOT_AUTHORIZED")
    confirmation = read_json(confirmation_path(root, project_id, dispatch_id), "C10_DISPATCH_MUST_BE_CONFIRMED_BEFORE_APPEND")
    raw, request_digest = load_private(args.append_request, root, "C10_APPEND_REQUEST_INVALID_JSON")
    request = validate_append_request(raw, project_id, dispatch_id, confirmation)
    target = append_plan_path(root, project_id, dispatch_id, request["appendId"])
    if target.exists():
        existing = read_json(target, "C10_EXISTING_APPEND_PLAN_INVALID")
        if existing.get("source", {}).get("requestDigest") == request_digest:
            return {"status": "IDEMPOTENT_EXISTING_SUB_AGENT_APPEND_PLAN", "dispatchId": dispatch_id, "appendId": request["appendId"], "writePerformed": False, "dispatchPerformed": False}, 0
        raise DispatchError("C10_APPEND_ID_REUSED_WITH_DIFFERENT_REQUEST")
    with dispatch_lock(root, project_id), ledger_lock(root, project_id):
        ledger = load_ledger(root, project_id)
        if ledger.get("recovery", {}).get("state") not in {"NORMAL", "CLOSED"} or ledger.get("hardStops"):
            raise DispatchError("C10_RECOVERY_OR_HARD_STOP_BLOCKS_APPEND")
        task = ledger.get("tasks", {}).get(request["taskId"])
        window = ledger.get("windows", {}).get(request["windowId"])
        if not isinstance(task, dict) or task.get("status") != "IN_PROGRESS" or not isinstance(window, dict) or window_current_task_id(window) != request["taskId"]:
            raise DispatchError("C10_APPEND_REQUIRES_LIVE_TASK_WINDOW")
        existing_agents = task_window_agents(ledger, request["windowId"], request["taskId"])
        if len(existing_agents) + len(request["subAgents"]) > 3:
            raise DispatchError("C10_FIRST_LEVEL_SUB_AGENT_LIMIT_EXCEEDED")
        if set(existing_agents).intersection({agent["subAgentId"] for agent in request["subAgents"]}):
            raise DispatchError("C10_SUB_AGENT_ID_ALREADY_REGISTERED")
        plan = {
            "schemaVersion": SCHEMA_VERSION, "recordType": "C10_SUB_AGENT_APPEND_PLAN", "appendId": request["appendId"],
            "dispatchId": dispatch_id, "projectId": project_id, "taskId": request["taskId"], "windowId": request["windowId"],
            "createdAt": utc_now(), "status": "PENDING_RUNTIME_CONFIRMATION", "trigger": request["trigger"],
            "subAgents": [{**agent, "model": "gpt-5.6-terra", "parent": "TASK_WINDOW"} for agent in request["subAgents"]],
            "source": {"requestDigest": request_digest, "dispatchConfirmationDigest": canonical_digest(confirmation), "ledgerRevision": ledger["revision"], "ledgerDigest": canonical_digest(ledger)},
            "boundary": {"withinApprovedScope": True, "sharedWriteRisk": False, "businessWritePerformed": False, "requiresParentQualityGate": True, "completionReturnsToParentWindow": True},
        }
        write_exclusive(target, plan)
    return {"status": "READY_FOR_RUNTIME_SUB_AGENT_APPEND", "dispatchId": dispatch_id, "appendId": request["appendId"], "windowId": request["windowId"], "subAgentCount": len(request["subAgents"]), "writePerformed": True, "dispatchPerformed": False, "businessWritePerformed": False}, 0


def validate_append_confirmation(raw: Dict[str, Any], append_id: str, dispatch_id: str, plan: Dict[str, Any]) -> Dict[str, Any]:
    value = exact(raw, {"appendConfirmationSchemaVersion", "recordType", "appendId", "dispatchId", "windowId", "subAgents"}, "C10_APPEND_CONFIRMATION_SCHEMA_UNSUPPORTED")
    if value["appendConfirmationSchemaVersion"] != SCHEMA_VERSION or value["recordType"] != "C10_SUB_AGENT_APPEND_RUNTIME_CONFIRMATION" or value["appendId"] != append_id or value["dispatchId"] != dispatch_id or value["windowId"] != plan["windowId"]:
        raise DispatchError("C10_APPEND_CONFIRMATION_SCOPE_MISMATCH")
    agents = value["subAgents"]
    if not isinstance(agents, list) or len(agents) != len(plan["subAgents"]):
        raise DispatchError("C10_SUB_AGENT_CONFIRMATION_COUNT_MISMATCH")
    expected = {agent["subAgentId"] for agent in plan["subAgents"]}; normalized = []
    for raw_agent in agents:
        agent = exact(raw_agent, {"subAgentId", "status", "runtimeAgentRef"}, "C10_SUB_AGENT_CONFIRMATION_INVALID")
        agent_id = require_ref(agent["subAgentId"], "C10_SUB_AGENT_ID_INVALID", WINDOW_ID)
        if agent_id not in expected or agent["status"] != "CREATED": raise DispatchError("C10_SUB_AGENT_RUNTIME_NOT_CONFIRMED")
        normalized.append({"subAgentId": agent_id, "runtimeAgentRef": require_ref(agent["runtimeAgentRef"], "C10_RUNTIME_AGENT_REF_INVALID")})
    if len({agent["subAgentId"] for agent in normalized}) != len(normalized): raise DispatchError("C10_DUPLICATE_SUB_AGENT_CONFIRMATION")
    return {"windowId": plan["windowId"], "agents": normalized}


def confirm_append(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    root = load_data_root(args); project_id = require_ref(args.project_id, "C10_PROJECT_ID_INVALID"); dispatch_id = require_ref(args.dispatch_id, "C10_DISPATCH_ID_INVALID"); append_id = require_ref(args.append_id, "C10_APPEND_ID_INVALID")
    if args.writer_id != CENTRAL_WRITER: raise DispatchError("C10_WRITER_NOT_AUTHORIZED")
    plan = read_json(append_plan_path(root, project_id, dispatch_id, append_id), "C10_APPEND_PLAN_NOT_FOUND_OR_INVALID")
    raw, confirmation_digest = load_private(args.confirmation, root, "C10_APPEND_CONFIRMATION_INVALID_JSON")
    confirmation = validate_append_confirmation(raw, append_id, dispatch_id, plan)
    target = append_confirmation_path(root, project_id, dispatch_id, append_id)
    if target.exists():
        existing = read_json(target, "C10_EXISTING_APPEND_CONFIRMATION_INVALID")
        if existing.get("sourceConfirmationDigest") == confirmation_digest:
            return {"status": "IDEMPOTENT_SUB_AGENT_APPEND_CONFIRMATION", "dispatchId": dispatch_id, "appendId": append_id, "writePerformed": False, "dispatchPerformed": True}, 0
        raise DispatchError("C10_APPEND_CONFIRMATION_ALREADY_EXISTS_WITH_DIFFERENT_CONTENT")
    with dispatch_lock(root, project_id), ledger_lock(root, project_id):
        before = load_ledger(root, project_id)
        task = before.get("tasks", {}).get(plan["taskId"]); window = before.get("windows", {}).get(plan["windowId"])
        if before.get("recovery", {}).get("state") not in {"NORMAL", "CLOSED"} or before.get("hardStops"):
            raise DispatchError("C10_RECOVERY_OR_HARD_STOP_BLOCKS_APPEND")
        if not isinstance(task, dict) or task.get("status") != "IN_PROGRESS" or not isinstance(window, dict) or window_current_task_id(window) != plan["taskId"]:
            raise DispatchError("C10_APPEND_REQUIRES_LIVE_TASK_WINDOW")
        existing_agents = task_window_agents(before, plan["windowId"], plan["taskId"])
        if len(existing_agents) + len(confirmation["agents"]) > 3: raise DispatchError("C10_FIRST_LEVEL_SUB_AGENT_LIMIT_EXCEEDED")
        if set(existing_agents).intersection({agent["subAgentId"] for agent in confirmation["agents"]}): raise DispatchError("C10_SUB_AGENT_ID_ALREADY_REGISTERED")
        specs = {agent["subAgentId"]: agent for agent in plan["subAgents"]}
        def mutate(after: Dict[str, Any]) -> None:
            for item in confirmation["agents"]:
                spec = specs[item["subAgentId"]]
                after["subAgents"][item["subAgentId"]] = {
                    "subAgentId": item["subAgentId"], "windowId": plan["windowId"], "taskId": plan["taskId"], "role": spec["role"],
                    "scope": spec["scope"], "delegationReason": spec["delegationReason"], "executionMode": spec["executionMode"],
                    "model": "gpt-5.6-terra", "level": 1, "status": "REGISTERED", "runtimeAgentRef": item["runtimeAgentRef"],
                    "dispatchId": dispatch_id, "appendId": append_id, "registeredAt": utc_now(),
                }
            after["tasks"][plan["taskId"]]["history"].append({"at": utc_now(), "event": "C10_SUB_AGENT_APPEND_CONFIRMED", "dispatchId": dispatch_id, "appendId": append_id, "windowId": plan["windowId"], "to": "IN_PROGRESS", "by": CENTRAL_WRITER})
        result = commit_mutation(root, project_id, before, CENTRAL_WRITER, "C10_CONFIRM_SUB_AGENT_APPEND", {"dispatchId": dispatch_id, "appendId": append_id, "taskId": plan["taskId"], "windowId": plan["windowId"], "subAgentCount": len(confirmation["agents"])}, mutate)
        artifact = {"schemaVersion": SCHEMA_VERSION, "recordType": "C10_SUB_AGENT_APPEND_CONFIRMATION", "appendId": append_id, "dispatchId": dispatch_id, "projectId": project_id, "taskId": plan["taskId"], "windowId": plan["windowId"], "confirmedAt": utc_now(), "subAgents": confirmation["agents"], "sourceConfirmationDigest": confirmation_digest, "ledgerReceiptId": result["receiptId"], "boundary": {"runtimeConfirmed": True, "businessWritePerformed": False, "parentQualityGateRequired": True}}
        write_exclusive(target, artifact)
    return {"status": "SUB_AGENT_APPEND_CONFIRMED", "dispatchId": dispatch_id, "appendId": append_id, "windowId": plan["windowId"], "subAgentCount": len(confirmation["agents"]), "ledgerReceiptId": result["receiptId"], "writePerformed": True, "dispatchPerformed": True, "businessWritePerformed": False}, 0


def export_fallback(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    root = load_data_root(args)
    project_id = require_ref(args.project_id, "C10_PROJECT_ID_INVALID")
    dispatch_id = require_ref(args.dispatch_id, "C10_DISPATCH_ID_INVALID")
    if args.writer_id != CENTRAL_WRITER:
        raise DispatchError("C10_WRITER_NOT_AUTHORIZED")
    plan = read_json(plan_path(root, project_id, dispatch_id), "C10_DISPATCH_PLAN_NOT_FOUND_OR_INVALID")
    if plan.get("status") != "PENDING_RUNTIME_CONFIRMATION":
        raise DispatchError("C10_DISPATCH_PLAN_NOT_PENDING")
    if confirmation_path(root, project_id, dispatch_id).exists():
        raise DispatchError("C10_RUNTIME_ALREADY_CONFIRMED")
    target = fallback_path(root, project_id, dispatch_id)
    if target.exists():
        existing = read_json(target, "C10_EXISTING_FALLBACK_INVALID")
        if existing.get("sourcePlanDigest") == canonical_digest(plan):
            return {
                "status": "IDEMPOTENT_MANUAL_FALLBACK_PACKAGE",
                "dispatchId": dispatch_id,
                "artifact": str(target),
                "writePerformed": False,
                "dispatchPerformed": False,
                "ledgerUpdated": False,
            }, 0
        raise DispatchError("C10_FALLBACK_ALREADY_EXISTS_WITH_DIFFERENT_PLAN")
    package = {
        "schemaVersion": "0.13.0",
        "recordType": "C13_MANUAL_TASK_PACKAGE",
        "dispatchId": dispatch_id,
        "projectId": project_id,
        "taskId": plan["taskId"],
        "createdAt": utc_now(),
        "model": plan["windowAction"]["model"],
        "runtimeTarget": plan["runtimeTarget"],
        "projectAssociationProtocol": plan["projectAssociationProtocol"],
        "windowActionRequested": plan["windowAction"],
        "subAgents": plan["subAgents"],
        "taskPackage": plan["taskPackage"],
        "copyablePrompt": (
            "请在派发单指定的 Codex 保存项目中按以下已批准任务包执行，不要另建自定义任务目录。"
            "创建后必须回读项目归属；若 Git worktree 的项目 ID 为空，先原生交接到保存项目根目录，再交接回同一 worktree 并重新回读。"
            "先复述目标、边界、写入占用和硬停条件；"
            "不得扩大范围，不得自行宣布 DONE。任务包：\n"
            + json.dumps(plan["taskPackage"], ensure_ascii=False, indent=2, sort_keys=True)
        ),
        "sourcePlanDigest": canonical_digest(plan),
        "boundary": {
            "runtimeCreationUnavailable": True,
            "manualCopyRequired": True,
            "runtimeConfirmed": False,
            "ledgerUpdated": False,
            "taskStatusChanged": False,
            "businessWritePerformed": False,
            "requiresNormalC10ConfirmationAfterManualCreation": True,
        },
    }
    with dispatch_lock(root, project_id):
        if target.exists():
            raise DispatchError("C10_FALLBACK_RACE_DETECTED")
        write_exclusive(target, package)
    return {
        "status": "READY_FOR_MANUAL_COPY",
        "dispatchId": dispatch_id,
        "artifact": str(target),
        "writePerformed": True,
        "dispatchPerformed": False,
        "ledgerUpdated": False,
        "taskStatusChanged": False,
        "businessWritePerformed": False,
    }, 0


def validate_return(raw: Dict[str, Any], dispatch_id: str, sub_agent_id: str, window_id: str) -> Dict[str, Any]:
    value = exact(raw, {"returnSchemaVersion", "recordType", "dispatchId", "subAgentId", "returnId", "submittedToWindowId", "status", "scope", "confirmedFacts", "completedWork", "evidenceRefs", "unresolvedRefs", "scopeDeviation", "risksAndConflicts", "recommendedParentAction"}, "C10_SUB_AGENT_RETURN_SCHEMA_UNSUPPORTED")
    if value["returnSchemaVersion"] != SCHEMA_VERSION or value["recordType"] != "C10_SUB_AGENT_RETURN" or value["dispatchId"] != dispatch_id or value["subAgentId"] != sub_agent_id or value["submittedToWindowId"] != window_id:
        raise DispatchError("C10_SUB_AGENT_RETURN_SCOPE_MISMATCH")
    if value["status"] not in {"NEEDS_REVIEW", "PARTIAL", "BLOCKED"}:
        raise DispatchError("C10_SUB_AGENT_CANNOT_DECLARE_DONE")
    def refs(raw_refs: Any, error: str, allow_empty: bool = True) -> List[str]:
        if not isinstance(raw_refs, list) or len(raw_refs) > 80 or (not allow_empty and not raw_refs): raise DispatchError(error)
        normalized = [require_ref(item, error) for item in raw_refs]
        if len(normalized) != len(set(normalized)): raise DispatchError(error)
        return normalized
    def statements(raw_items: Any, error: str, allow_empty: bool = False) -> List[str]:
        if not isinstance(raw_items, list) or len(raw_items) > 40 or (not allow_empty and not raw_items): raise DispatchError(error)
        normalized = [str(item).strip() for item in raw_items]
        if any(not item or len(item) > 600 for item in normalized) or len(normalized) != len(set(normalized)): raise DispatchError(error)
        return normalized
    scope = str(value["scope"]).strip(); recommendation = str(value["recommendedParentAction"]).strip()
    if not scope or len(scope) > 600 or not recommendation or len(recommendation) > 1000:
        raise DispatchError("C10_SUB_AGENT_RETURN_SUMMARY_INVALID")
    return {
        "returnId": require_ref(value["returnId"], "C10_RETURN_ID_INVALID"), "status": value["status"], "scope": scope,
        "confirmedFacts": statements(value["confirmedFacts"], "C10_CONFIRMED_FACTS_INVALID"),
        "completedWork": statements(value["completedWork"], "C10_COMPLETED_WORK_INVALID"),
        "evidenceRefs": refs(value["evidenceRefs"], "C10_EVIDENCE_REFS_INVALID", allow_empty=False),
        "unresolvedRefs": refs(value["unresolvedRefs"], "C10_UNRESOLVED_REFS_INVALID"),
        "scopeDeviation": statements(value["scopeDeviation"], "C10_SCOPE_DEVIATION_INVALID", allow_empty=True),
        "risksAndConflicts": statements(value["risksAndConflicts"], "C10_RISKS_AND_CONFLICTS_INVALID", allow_empty=True),
        "recommendedParentAction": recommendation,
    }


def record_return(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    root = load_data_root(args); project_id = require_ref(args.project_id, "C10_PROJECT_ID_INVALID"); dispatch_id = require_ref(args.dispatch_id, "C10_DISPATCH_ID_INVALID"); sub_agent_id = require_ref(args.sub_agent_id, "C10_SUB_AGENT_ID_INVALID", WINDOW_ID)
    if args.writer_id != CENTRAL_WRITER: raise DispatchError("C10_WRITER_NOT_AUTHORIZED")
    confirmation = read_json(confirmation_path(root, project_id, dispatch_id), "C10_DISPATCH_MUST_BE_CONFIRMED_BEFORE_RETURN")
    raw, return_digest = load_private(args.return_file, root, "C10_SUB_AGENT_RETURN_INVALID_JSON")
    value = validate_return(raw, dispatch_id, sub_agent_id, confirmation["windowId"])
    target = return_path(root, project_id, dispatch_id, sub_agent_id)
    if target.exists():
        existing = read_json(target, "C10_EXISTING_SUB_AGENT_RETURN_INVALID")
        if existing.get("sourceReturnDigest") == return_digest:
            return {"status": "IDEMPOTENT_SUB_AGENT_RETURN", "dispatchId": dispatch_id, "subAgentId": sub_agent_id, "writePerformed": False, "parentCompletionAllowed": existing["allSubAgentsReturned"]}, 0
        raise DispatchError("C10_SUB_AGENT_ALREADY_RETURNED_DIFFERENT_CONTENT")
    with dispatch_lock(root, project_id), ledger_lock(root, project_id):
        before = load_ledger(root, project_id); agent = before.get("subAgents", {}).get(sub_agent_id)
        if not isinstance(agent, dict) or agent.get("dispatchId") != dispatch_id or agent.get("windowId") != confirmation["windowId"] or agent.get("level") != 1:
            raise DispatchError("C10_SUB_AGENT_NOT_REGISTERED_TO_PARENT_WINDOW")
        if agent.get("status") != "REGISTERED": raise DispatchError("C10_SUB_AGENT_NOT_ACTIVE")
        expected_agents = task_window_agents(before, confirmation["windowId"], confirmation["taskId"])
        if sub_agent_id not in expected_agents or expected_agents[sub_agent_id].get("dispatchId") != dispatch_id:
            raise DispatchError("C10_SUB_AGENT_NOT_REGISTERED_TO_PARENT_WINDOW")
        all_returned = all(
            agent_id == sub_agent_id or agent.get("status") == "RETURNED"
            for agent_id, agent in expected_agents.items()
        )
        def mutate(after: Dict[str, Any]) -> None:
            target_agent = after["subAgents"][sub_agent_id]; target_agent["status"] = "RETURNED"; target_agent["returnId"] = value["returnId"]; target_agent["returnedAt"] = utc_now()
        result = commit_mutation(root, project_id, before, CENTRAL_WRITER, "C10_RECORD_SUB_AGENT_RETURN", {"dispatchId": dispatch_id, "subAgentId": sub_agent_id, "returnId": value["returnId"], "allSubAgentsReturned": all_returned}, mutate)
        artifact = {"schemaVersion": SCHEMA_VERSION, "recordType": "C10_VERIFIED_SUB_AGENT_RETURN", "dispatchId": dispatch_id, "subAgentId": sub_agent_id, "returnId": value["returnId"], "recordedAt": utc_now(), "submittedToWindowId": confirmation["windowId"], "status": value["status"], "scope": value["scope"], "confirmedFacts": value["confirmedFacts"], "completedWork": value["completedWork"], "evidenceRefs": value["evidenceRefs"], "unresolvedRefs": value["unresolvedRefs"], "scopeDeviation": value["scopeDeviation"], "risksAndConflicts": value["risksAndConflicts"], "recommendedParentAction": value["recommendedParentAction"], "sourceReturnDigest": return_digest, "ledgerReceiptId": result["receiptId"], "allSubAgentsReturned": all_returned, "boundary": {"taskDoneDeclared": False, "parentWindowMustAggregate": True, "parentQualityGateRequired": True, "parentCompletionAllowed": False}}
        write_exclusive(target, artifact)
    return {"status": "SUB_AGENT_RETURN_RECORDED", "dispatchId": dispatch_id, "subAgentId": sub_agent_id, "allSubAgentsReturned": all_returned, "parentQualityGateRequired": True, "parentCompletionAllowed": False, "taskDoneDeclared": False, "writePerformed": True}, 0


def validate_parent_quality_review(raw: Dict[str, Any], dispatch_id: str, project_id: str, task_id: str, window_id: str, agents: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    required = {"qualityReviewSchemaVersion", "recordType", "qualityReviewId", "dispatchId", "projectId", "taskId", "windowId", "subAgentReturns", "acceptanceCoverage", "conflictResolutions", "parentReadbackEvidenceRefs", "scopeCheck", "unifiedStatus", "unresolvedRefs", "recommendedParentAction"}
    value = exact(raw, required, "C10_PARENT_QUALITY_REVIEW_SCHEMA_UNSUPPORTED")
    if value["qualityReviewSchemaVersion"] != SCHEMA_VERSION or value["recordType"] != "C10_PARENT_QUALITY_REVIEW" or value["dispatchId"] != dispatch_id or value["projectId"] != project_id or value["taskId"] != task_id or value["windowId"] != window_id:
        raise DispatchError("C10_PARENT_QUALITY_REVIEW_SCOPE_MISMATCH")
    returns = value["subAgentReturns"]
    if not isinstance(returns, list) or len(returns) != len(agents): raise DispatchError("C10_PARENT_QUALITY_REVIEW_RETURNS_INVALID")
    return_ids: Dict[str, str] = {}
    for item in returns:
        normalized = exact(item, {"subAgentId", "returnId"}, "C10_PARENT_QUALITY_REVIEW_RETURNS_INVALID")
        return_ids[require_ref(normalized["subAgentId"], "C10_PARENT_QUALITY_REVIEW_RETURNS_INVALID", WINDOW_ID)] = require_ref(normalized["returnId"], "C10_PARENT_QUALITY_REVIEW_RETURNS_INVALID")
    expected_returns = {agent_id: agent.get("returnId") for agent_id, agent in agents.items()}
    if return_ids != expected_returns:
        raise DispatchError("C10_PARENT_QUALITY_REVIEW_RETURNS_INCOMPLETE")
    coverage_keys = {"positiveCases", "negativeCases", "idempotencyChecks", "rollbackChecks", "logAndHistoryChecks", "readbackChecks"}
    coverage = exact(value["acceptanceCoverage"], coverage_keys, "C10_PARENT_QUALITY_REVIEW_COVERAGE_INVALID")
    normalized_coverage: Dict[str, List[str]] = {}
    for key, refs in coverage.items():
        if not isinstance(refs, list) or not refs or len(refs) > 40:
            raise DispatchError("C10_PARENT_QUALITY_REVIEW_COVERAGE_INVALID")
        normalized_refs = [require_ref(ref, "C10_PARENT_QUALITY_REVIEW_COVERAGE_INVALID") for ref in refs]
        if len(normalized_refs) != len(set(normalized_refs)):
            raise DispatchError("C10_PARENT_QUALITY_REVIEW_COVERAGE_INVALID")
        normalized_coverage[key] = normalized_refs
    conflicts = value["conflictResolutions"]
    if not isinstance(conflicts, list) or len(conflicts) != len(agents): raise DispatchError("C10_PARENT_QUALITY_REVIEW_CONFLICTS_INVALID")
    seen_conflicts = set(); normalized_conflicts = []
    for item in conflicts:
        normalized = exact(item, {"subAgentId", "outcome", "resolutionRef"}, "C10_PARENT_QUALITY_REVIEW_CONFLICTS_INVALID")
        agent_id = require_ref(normalized["subAgentId"], "C10_PARENT_QUALITY_REVIEW_CONFLICTS_INVALID", WINDOW_ID)
        if agent_id not in agents or agent_id in seen_conflicts or str(normalized["outcome"]).upper() not in {"NO_CONFLICT", "RESOLVED", "ESCALATED"}:
            raise DispatchError("C10_PARENT_QUALITY_REVIEW_CONFLICTS_INVALID")
        resolution_ref = require_ref(normalized["resolutionRef"], "C10_PARENT_QUALITY_REVIEW_CONFLICTS_INVALID")
        normalized_conflicts.append({"subAgentId": agent_id, "outcome": str(normalized["outcome"]).upper(), "resolutionRef": resolution_ref}); seen_conflicts.add(agent_id)
    readback_refs = value["parentReadbackEvidenceRefs"]
    if not isinstance(readback_refs, list) or not readback_refs or len(readback_refs) > 40:
        raise DispatchError("C10_PARENT_QUALITY_REVIEW_READBACK_INVALID")
    normalized_readback = [require_ref(item, "C10_PARENT_QUALITY_REVIEW_READBACK_INVALID") for item in readback_refs]
    if len(normalized_readback) != len(set(normalized_readback)): raise DispatchError("C10_PARENT_QUALITY_REVIEW_READBACK_INVALID")
    scope_check = exact(value["scopeCheck"], {"withinApprovedScope", "unexpectedWriteFound"}, "C10_PARENT_QUALITY_REVIEW_SCOPE_INVALID")
    if scope_check["withinApprovedScope"] is not True or scope_check["unexpectedWriteFound"] is not False:
        raise DispatchError("C10_PARENT_QUALITY_REVIEW_SCOPE_INVALID")
    status = str(value["unifiedStatus"]).upper()
    if status not in {"NEEDS_REVIEW", "PARTIAL", "BLOCKED"}: raise DispatchError("C10_PARENT_QUALITY_REVIEW_CANNOT_DECLARE_DONE")
    unresolved = value["unresolvedRefs"]
    if not isinstance(unresolved, list) or len(unresolved) > 80: raise DispatchError("C10_PARENT_QUALITY_REVIEW_UNRESOLVED_INVALID")
    normalized_unresolved = [require_ref(item, "C10_PARENT_QUALITY_REVIEW_UNRESOLVED_INVALID") for item in unresolved]
    if len(normalized_unresolved) != len(set(normalized_unresolved)): raise DispatchError("C10_PARENT_QUALITY_REVIEW_UNRESOLVED_INVALID")
    recommendation = str(value["recommendedParentAction"]).strip()
    if not recommendation or len(recommendation) > 1000: raise DispatchError("C10_PARENT_QUALITY_REVIEW_RECOMMENDATION_INVALID")
    return {"qualityReviewId": require_ref(value["qualityReviewId"], "C10_PARENT_QUALITY_REVIEW_ID_INVALID"), "subAgentReturns": [{"subAgentId": key, "returnId": return_ids[key]} for key in sorted(return_ids)], "acceptanceCoverage": normalized_coverage, "conflictResolutions": normalized_conflicts, "parentReadbackEvidenceRefs": normalized_readback, "scopeCheck": {"withinApprovedScope": True, "unexpectedWriteFound": False}, "unifiedStatus": status, "unresolvedRefs": normalized_unresolved, "recommendedParentAction": recommendation}


def record_parent_quality_review(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    root = load_data_root(args); project_id = require_ref(args.project_id, "C10_PROJECT_ID_INVALID"); dispatch_id = require_ref(args.dispatch_id, "C10_DISPATCH_ID_INVALID")
    if args.writer_id != CENTRAL_WRITER: raise DispatchError("C10_WRITER_NOT_AUTHORIZED")
    confirmation = read_json(confirmation_path(root, project_id, dispatch_id), "C10_DISPATCH_MUST_BE_CONFIRMED_BEFORE_PARENT_QUALITY_REVIEW")
    raw, review_digest = load_private(args.quality_review, root, "C10_PARENT_QUALITY_REVIEW_INVALID_JSON")
    with dispatch_lock(root, project_id), ledger_lock(root, project_id):
        ledger = load_ledger(root, project_id); agents = task_window_agents(ledger, confirmation["windowId"], confirmation["taskId"])
        if not agents: raise DispatchError("C10_PARENT_QUALITY_REVIEW_REQUIRES_SUB_AGENTS")
        if any(agent.get("status") != "RETURNED" or not agent.get("returnId") for agent in agents.values()):
            raise DispatchError("C10_PARENT_QUALITY_GATE_REQUIRES_ALL_SUB_AGENT_RETURNS")
        review = validate_parent_quality_review(raw, dispatch_id, project_id, confirmation["taskId"], confirmation["windowId"], agents)
        target = parent_quality_review_path(root, project_id, dispatch_id)
        if target.exists():
            existing = read_json(target, "C10_EXISTING_PARENT_QUALITY_REVIEW_INVALID")
            if existing.get("sourceReviewDigest") == review_digest:
                return {"status": "IDEMPOTENT_PARENT_QUALITY_REVIEW", "dispatchId": dispatch_id, "qualityReviewId": existing["qualityReviewId"], "writePerformed": False, "parentMayRequestC06": True}, 0
            raise DispatchError("C10_PARENT_QUALITY_REVIEW_ALREADY_EXISTS_WITH_DIFFERENT_CONTENT")
        artifact = {"schemaVersion": SCHEMA_VERSION, "recordType": "C10_PARENT_QUALITY_REVIEW", "qualityReviewId": review["qualityReviewId"], "dispatchId": dispatch_id, "projectId": project_id, "taskId": confirmation["taskId"], "windowId": confirmation["windowId"], "recordedAt": utc_now(), "subAgentReturns": review["subAgentReturns"], "acceptanceCoverage": review["acceptanceCoverage"], "conflictResolutions": review["conflictResolutions"], "parentReadbackEvidenceRefs": review["parentReadbackEvidenceRefs"], "scopeCheck": review["scopeCheck"], "unifiedStatus": review["unifiedStatus"], "unresolvedRefs": review["unresolvedRefs"], "recommendedParentAction": review["recommendedParentAction"], "sourceReviewDigest": review_digest, "boundary": {"taskDoneDeclared": False, "parentQualityGatePassed": True, "parentMayRequestC06": True, "businessWritePerformed": False}}
        write_exclusive(target, artifact)
    return {"status": "PARENT_QUALITY_GATE_RECORDED", "dispatchId": dispatch_id, "qualityReviewId": review["qualityReviewId"], "parentMayRequestC06": True, "taskDoneDeclared": False, "writePerformed": True, "businessWritePerformed": False}, 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="C10 task-window dispatcher")
    root = parser.add_mutually_exclusive_group(required=True); root.add_argument("--data-root"); root.add_argument("--config")
    parser.add_argument("--project-id", required=True); parser.add_argument("--writer-id")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare"); prepare_parser.add_argument("--package-id", required=True); prepare_parser.add_argument("--review-id", required=True); prepare_parser.add_argument("--request", required=True)
    confirm_parser = commands.add_parser("confirm"); confirm_parser.add_argument("--dispatch-id", required=True); confirm_parser.add_argument("--confirmation", required=True)
    fallback_parser = commands.add_parser("export-fallback"); fallback_parser.add_argument("--dispatch-id", required=True)
    append_prepare_parser = commands.add_parser("prepare-append"); append_prepare_parser.add_argument("--dispatch-id", required=True); append_prepare_parser.add_argument("--append-request", required=True)
    append_confirm_parser = commands.add_parser("confirm-append"); append_confirm_parser.add_argument("--dispatch-id", required=True); append_confirm_parser.add_argument("--append-id", required=True); append_confirm_parser.add_argument("--confirmation", required=True)
    return_parser = commands.add_parser("record-return"); return_parser.add_argument("--dispatch-id", required=True); return_parser.add_argument("--sub-agent-id", required=True); return_parser.add_argument("--return-file", required=True)
    quality_parser = commands.add_parser("record-parent-quality-review"); quality_parser.add_argument("--dispatch-id", required=True); quality_parser.add_argument("--quality-review", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        handlers = {"prepare": prepare, "confirm": confirm, "prepare-append": prepare_append, "confirm-append": confirm_append, "export-fallback": export_fallback, "record-return": record_return, "record-parent-quality-review": record_parent_quality_review}; result, code = handlers[args.command](args); print_result(result); return code
    except (C02Error, DispatchError, LedgerError) as error:
        reason = str(error)
        status = "NEEDS_REVIEW" if reason == "C10_TASK_IDENTITY_MISMATCH" else "REFUSED"
        print_result({"status": status, "reason": reason, "writePerformed": False, "dispatchPerformed": False, "businessWritePerformed": False}); return 2


if __name__ == "__main__": sys.exit(main())
