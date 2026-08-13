#!/usr/bin/env python3
"""C09 deterministic central router. It routes governance work; it never performs it."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from initialize_project import C02Error, PROJECT_ID_PATTERN, is_within, load_data_root
from independent_handover_validator import HandoverValidationError, verify_finalization_data
from ledger_manager import CENTRAL_WRITER, LedgerError, TASK_ID_PATTERN, load_ledger, verify_ledger
from occupancy_conflict_checker import OccupancyError, evaluate as evaluate_occupancy
from task_package_generator import TaskPackageError
from task_window_dispatch_controller import DispatchError, prepare as prepare_dispatch


SCHEMA_VERSION = "0.12.0"
REGISTRY_SCHEMA_VERSION = "0.19.0"
CENTRAL_SKILL = "central-construction-controller"
BOSS_ENTRY_SKILL = "central-workbench"
INTERNAL_PROTOCOL_SKILLS = {CENTRAL_SKILL, "codex-governance-gateway"}
CENTRAL_MODEL = "gpt-5.6-sol"
TASK_MODEL = "gpt-5.6-terra"
REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PLUGIN_ROOT / "config" / "core-capability-registry.json"
PLUGIN_SKILLS = PLUGIN_ROOT / "skills"
PLUGIN_SCRIPTS = PLUGIN_ROOT / "scripts"
SUCCESSOR_ROOT = Path("successor-dispatches")

EXPECTED = {
    "C00": ("PLANNER", "construction-outline-planner", None),
    "C02": ("EXECUTOR", "new-project-initializer", "initialize_project.py"),
    "C03": ("EXECUTOR", "engineering-ledger-manager", "ledger_manager.py"),
    "C04": ("EXECUTOR", "task-package-generator", "task_package_generator.py"),
    "C05": ("EXECUTOR", "occupancy-conflict-checker", "occupancy_conflict_checker.py"),
    "C06": ("EXECUTOR", "independent-handover-validator", "independent_handover_validator.py"),
    "C07": ("EXECUTOR", "adjudication-request-organizer", "adjudication_request_organizer.py"),
    "C08": ("EXECUTOR", "disconnection-recovery-controller", "disconnection_recovery_controller.py"),
    "C09": ("CENTRAL", CENTRAL_SKILL, "central_construction_controller.py"),
    "C10": ("EXECUTOR", "task-window-dispatch-controller", "task_window_dispatch_controller.py"),
    "C11": ("EXECUTOR", "external-skill-adapter-controller", "external_skill_adapter_controller.py"),
    "C12": ("INTERFACE", "codex-governance-gateway", "natural_language_gateway.py"),
    "C14": ("EXECUTOR", "task-communication-bridge", "task_communication_bridge.py"),
}

ROUTES = {
    "INITIALIZE_PROJECT": ("C02", "new-project-initializer", "CHECK_AND_PREPARE_PROJECT_INITIALIZATION"),
    "READ_LEDGER": ("C03", "engineering-ledger-manager", "READ_VERIFIED_ENGINEERING_LEDGER"),
    "INITIALIZE_LEDGER": ("C03", "engineering-ledger-manager", "PREPARE_OR_INITIALIZE_VERIFIED_ENGINEERING_LEDGER"),
    "REGISTER_GOVERNANCE_STATE": ("C03", "engineering-ledger-manager", "APPLY_AUTHORIZED_LEDGER_MUTATION"),
    "REQUEST_TASK_CANCELLATION": ("C03", "engineering-ledger-manager", "RECORD_AUTHORIZED_CANCELLATION_REQUEST_AND_RETAIN_OCCUPANCY"),
    "GENERATE_TASK_PACKAGE": ("C04", "task-package-generator", "PREPARE_DRAFT_TASK_PACKAGE"),
    "CHECK_OCCUPANCY": ("C05", "occupancy-conflict-checker", "RUN_OCCUPANCY_AND_WINDOW_REVIEW"),
    "VALIDATE_HANDBACK": ("C06", "independent-handover-validator", "RUN_INDEPENDENT_HANDBACK_VALIDATION"),
    "FINALIZE_TASK": ("C06", "independent-handover-validator", "REQUEST_BOSS_FINALIZATION_GATE"),
    "REQUEST_ADVISORY": ("C07", "adjudication-request-organizer", "PREPARE_ADVICE_FOR_BOSS_ONLY"),
    "RECORD_BOSS_ADJUDICATION": ("C07", "adjudication-request-organizer", "RECORD_AND_RETURN_BOSS_DECISION"),
    "FREEZE_DISCONNECTION": ("C08", "disconnection-recovery-controller", "FREEZE_GOVERNANCE_STATE"),
    "TAKEOVER_CONTROL": ("C08", "disconnection-recovery-controller", "VERIFY_BOSS_AUTHORIZED_TAKEOVER"),
    "RECORD_RECOVERY_DECISION": ("C08", "disconnection-recovery-controller", "RECORD_RECOVERY_DECISION"),
    "RELEASE_OCCUPANCY": ("C08", "disconnection-recovery-controller", "VERIFY_CONTROLLED_OCCUPANCY_RELEASE"),
    "CHECK_TASK_COMMUNICATION": ("C14", "task-communication-bridge", "READ_OR_RECONCILE_MESSAGE_RECEIPTS"),
    "REGISTER_EXTERNAL_SKILL": ("C11", "external-skill-adapter-controller", "ASSESS_OR_ACTIVATE_EXTERNAL_SKILL_SLOT"),
    "RESOLVE_EXTERNAL_SKILL": ("C11", "external-skill-adapter-controller", "RESOLVE_ACTIVE_SKILL_OR_SAFE_FALLBACK"),
}
DISPATCH_INTENTS = {"DISPATCH_TASK_WINDOW", "DISPATCH_SUB_AGENT"}
PENDING_INTENTS = {"RESUME_BUSINESS_EXECUTION"}
RECOVERY_INTENTS = {
    "FREEZE_DISCONNECTION", "TAKEOVER_CONTROL", "RECORD_RECOVERY_DECISION", "RELEASE_OCCUPANCY"
}
CONTEXT_REQUIREMENTS = {
    "GENERATE_TASK_PACKAGE": "taskId",
    "REQUEST_TASK_CANCELLATION": "taskId",
    "CHECK_OCCUPANCY": "packageId",
    "VALIDATE_HANDBACK": "packageId",
    "FINALIZE_TASK": "validationId",
    "REQUEST_ADVISORY": "taskId",
    "RECORD_BOSS_ADJUDICATION": "taskId",
    "TAKEOVER_CONTROL": "recoveryCaseId",
    "RECORD_RECOVERY_DECISION": "recoveryCaseId",
    "RELEASE_OCCUPANCY": "recoveryCaseId",
    "DISPATCH_TASK_WINDOW": "packageId",
    "DISPATCH_SUB_AGENT": "windowId",
    "CHECK_TASK_COMMUNICATION": "messageId",
}


class CentralRoutingError(Exception):
    """A safe refusal. No downstream action may be inferred."""


def print_result(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def canonical_digest(payload: Dict[str, Any]) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def load_json(path: Path, error: str) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise CentralRoutingError(error)
    if not isinstance(value, dict):
        raise CentralRoutingError(error)
    return value


def load_private_json(data_root: Path, raw_path: str, error: str) -> Dict[str, Any]:
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file() or not is_within(path, data_root):
        raise CentralRoutingError("C09_INPUT_MUST_BE_INSIDE_PRIVATE_DATA_ROOT")
    return load_json(path, error)


def successor_batch_dir(data_root: Path, project_id: str, validation_id: str) -> Path:
    return data_root / SUCCESSOR_ROOT / project_id / validation_id


def successor_batch_paths(data_root: Path, project_id: str, validation_id: str) -> List[Path]:
    root = successor_batch_dir(data_root, project_id, validation_id)
    paths = sorted(root.glob("successor-batch-r*.json")) if root.is_dir() else []
    legacy = root / "successor-batch.json"
    if legacy.is_file():
        paths.insert(0, legacy)
    return paths


def write_exclusive(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    except FileExistsError:
        raise CentralRoutingError("C09_SUCCESSOR_BATCH_ALREADY_EXISTS")


def validate_execution_map(value: Dict[str, Any], project_id: str) -> Dict[str, Any]:
    required = {"executionMapSchemaVersion", "recordType", "planId", "projectId", "authorization", "executionPlan"}
    if set(value) != required or value.get("executionMapSchemaVersion") != "0.16.0" or value.get("recordType") != "C09_APPROVED_EXECUTION_MAP" or value.get("projectId") != project_id:
        raise CentralRoutingError("C09_EXECUTION_MAP_SCHEMA_UNSUPPORTED")
    plan_id = require_reference(value.get("planId"), "C09_EXECUTION_MAP_ID_INVALID")
    authorization = value.get("authorization")
    if not isinstance(authorization, dict) or set(authorization) != {"scopeId", "scopeDigest", "bossApprovalRef"}:
        raise CentralRoutingError("C09_EXECUTION_MAP_AUTHORIZATION_INVALID")
    if authorization.get("scopeId") != plan_id:
        raise CentralRoutingError("C09_EXECUTION_MAP_SCOPE_ID_MISMATCH")
    scope_digest = authorization.get("scopeDigest")
    if not isinstance(scope_digest, str) or not re.fullmatch(r"[a-f0-9]{64}", scope_digest):
        raise CentralRoutingError("C09_EXECUTION_MAP_DIGEST_INVALID")
    require_reference(authorization.get("bossApprovalRef"), "C09_EXECUTION_MAP_BOSS_APPROVAL_INVALID")
    execution_plan = value.get("executionPlan")
    if not isinstance(execution_plan, dict) or set(execution_plan) != {"tasks"} or canonical_digest(execution_plan) != scope_digest:
        raise CentralRoutingError("C09_EXECUTION_MAP_DIGEST_MISMATCH")
    raw_tasks = execution_plan.get("tasks")
    if not isinstance(raw_tasks, list) or not 1 <= len(raw_tasks) <= 100:
        raise CentralRoutingError("C09_EXECUTION_MAP_TASKS_INVALID")
    tasks: List[Dict[str, Any]] = []
    seen = set()
    required_task_keys = {"taskId", "dependencies", "packageId", "c05ReviewId", "c05ReviewPath", "c10RequestPath"}
    for raw in raw_tasks:
        if not isinstance(raw, dict) or set(raw) != required_task_keys:
            raise CentralRoutingError("C09_EXECUTION_MAP_TASK_INVALID")
        task_id = require_reference(raw.get("taskId"), "C09_EXECUTION_MAP_TASK_ID_INVALID")
        if not TASK_ID_PATTERN.fullmatch(task_id) or task_id in seen:
            raise CentralRoutingError("C09_EXECUTION_MAP_TASK_ID_INVALID")
        seen.add(task_id)
        dependencies = raw.get("dependencies")
        if not isinstance(dependencies, list) or len(dependencies) > 40 or len(dependencies) != len(set(dependencies)):
            raise CentralRoutingError("C09_EXECUTION_MAP_DEPENDENCIES_INVALID")
        for dependency in dependencies:
            if not isinstance(dependency, str) or not TASK_ID_PATTERN.fullmatch(dependency):
                raise CentralRoutingError("C09_EXECUTION_MAP_DEPENDENCIES_INVALID")
        if task_id in dependencies:
            raise CentralRoutingError("C09_EXECUTION_MAP_SELF_DEPENDENCY")
        if not isinstance(raw.get("c05ReviewPath"), str) or not raw["c05ReviewPath"].strip():
            raise CentralRoutingError("C09_EXECUTION_MAP_REVIEW_PATH_INVALID")
        if not isinstance(raw.get("c10RequestPath"), str) or not raw["c10RequestPath"].strip():
            raise CentralRoutingError("C09_EXECUTION_MAP_REQUEST_PATH_INVALID")
        tasks.append({
            "taskId": task_id,
            "dependencies": dependencies,
            "packageId": require_reference(raw.get("packageId"), "C09_EXECUTION_MAP_PACKAGE_ID_INVALID"),
            "c05ReviewId": require_reference(raw.get("c05ReviewId"), "C09_EXECUTION_MAP_REVIEW_ID_INVALID"),
            "c05ReviewPath": raw["c05ReviewPath"].strip(),
            "c10RequestPath": raw["c10RequestPath"].strip(),
        })
    return {**value, "executionPlan": {"tasks": tasks}}


def validate_registry(registry: Dict[str, Any], check_files: bool = True) -> Dict[str, Any]:
    required = {"schemaVersion", "recordType", "canonicalCentralSkill", "capabilities", "pendingCapabilities"}
    if set(registry) != required or registry.get("schemaVersion") != REGISTRY_SCHEMA_VERSION or registry.get("recordType") != "C09_CORE_CAPABILITY_REGISTRY":
        raise CentralRoutingError("C09_CAPABILITY_REGISTRY_SCHEMA_UNSUPPORTED")
    if registry.get("canonicalCentralSkill") != CENTRAL_SKILL or not isinstance(registry.get("capabilities"), list):
        raise CentralRoutingError("C09_CANONICAL_CENTRAL_INVALID")
    central_entries = [entry for entry in registry["capabilities"] if isinstance(entry, dict) and entry.get("role") == "CENTRAL"]
    if len(central_entries) != 1 or central_entries[0].get("skill") != CENTRAL_SKILL:
        raise CentralRoutingError("C09_MULTIPLE_OR_MISSING_CENTRAL_ENTRY")
    if len(registry["capabilities"]) != len(EXPECTED):
        raise CentralRoutingError("C09_CAPABILITY_COUNT_MISMATCH")
    by_stage = {entry.get("stage"): entry for entry in registry["capabilities"] if isinstance(entry, dict)}
    if set(by_stage) != set(EXPECTED):
        raise CentralRoutingError("C09_CAPABILITY_STAGE_MISMATCH")
    for stage, (role, skill, script) in EXPECTED.items():
        entry = by_stage[stage]
        if set(entry) != {"stage", "role", "skill", "script", "status"} or (entry.get("role"), entry.get("skill"), entry.get("script"), entry.get("status")) != (role, skill, script, "READY"):
            raise CentralRoutingError(f"C09_{stage}_CAPABILITY_CONTRACT_INVALID")
        if check_files:
            documented_skill = BOSS_ENTRY_SKILL if skill in INTERNAL_PROTOCOL_SKILLS else skill
            if not (PLUGIN_SKILLS / documented_skill / "SKILL.md").is_file():
                raise CentralRoutingError(f"C09_{stage}_CAPABILITY_FILE_MISSING")
            if script is not None and not (PLUGIN_SCRIPTS / script).is_file():
                raise CentralRoutingError(f"C09_{stage}_CAPABILITY_FILE_MISSING")
    pending = {entry.get("stage"): entry.get("status") for entry in registry.get("pendingCapabilities", []) if isinstance(entry, dict)}
    if pending:
        raise CentralRoutingError("C09_PENDING_CAPABILITY_BOUNDARY_INVALID")
    return registry


def load_registry() -> Dict[str, Any]:
    return validate_registry(load_json(REGISTRY_PATH, "C09_CAPABILITY_REGISTRY_INVALID_JSON"))


def require_reference(value: Any, error: str, nullable: bool = False) -> Optional[str]:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not REFERENCE_PATTERN.fullmatch(value.strip()):
        raise CentralRoutingError(error)
    return value.strip()


def validate_request(request: Dict[str, Any]) -> Dict[str, Any]:
    required = {"requestSchemaVersion", "recordType", "requestId", "submittedBy", "projectId", "intent", "executionMode", "bossAuthorization", "contextRefs"}
    if set(request) != required or request.get("requestSchemaVersion") != SCHEMA_VERSION or request.get("recordType") != "C09_CENTRAL_ROUTING_REQUEST":
        raise CentralRoutingError("C09_ROUTING_REQUEST_SCHEMA_UNSUPPORTED")
    require_reference(request.get("requestId"), "C09_REQUEST_ID_INVALID")
    if request.get("submittedBy") != "boss":
        raise CentralRoutingError("C09_REQUEST_MUST_COME_FROM_BOSS")
    intent = request.get("intent")
    if intent not in set(ROUTES) | DISPATCH_INTENTS | PENDING_INTENTS:
        raise CentralRoutingError("C09_INTENT_UNSUPPORTED")
    if request.get("executionMode") not in {"READ_ONLY", "PREPARE", "APPLY"}:
        raise CentralRoutingError("C09_EXECUTION_MODE_INVALID")
    auth = request.get("bossAuthorization")
    if not isinstance(auth, dict) or set(auth) != {"status", "reference"} or auth.get("status") not in {"NOT_REQUIRED", "PENDING", "APPROVED"}:
        raise CentralRoutingError("C09_BOSS_AUTHORIZATION_INVALID")
    auth_ref = require_reference(auth.get("reference"), "C09_BOSS_AUTHORIZATION_REFERENCE_INVALID", nullable=True)
    if auth.get("status") == "APPROVED" and auth_ref is None:
        raise CentralRoutingError("C09_APPROVED_REQUEST_REQUIRES_BOSS_REFERENCE")
    if request.get("executionMode") == "APPLY" and (auth.get("status") != "APPROVED" or auth_ref is None):
        raise CentralRoutingError("C09_APPLY_REQUIRES_EXPLICIT_BOSS_AUTHORIZATION")
    context = request.get("contextRefs")
    context_keys = {"taskId", "packageId", "validationId", "recoveryCaseId", "windowId", "messageId"}
    if not isinstance(context, dict) or set(context) != context_keys:
        raise CentralRoutingError("C09_CONTEXT_REFS_INVALID")
    for key, value in context.items():
        require_reference(value, f"C09_{key.upper()}_INVALID", nullable=True)
    required_context = CONTEXT_REQUIREMENTS.get(intent)
    if required_context and context.get(required_context) is None:
        raise CentralRoutingError(f"C09_{required_context.upper()}_REQUIRED")
    project_id = request.get("projectId")
    if not isinstance(project_id, str) or not PROJECT_ID_PATTERN.fullmatch(project_id.strip()):
        raise CentralRoutingError("C09_PROJECT_ID_INVALID")
    return request


def decision_base(request: Dict[str, Any], registry: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "recordType": "C09_CENTRAL_ROUTING_DECISION",
        "requestId": request["requestId"],
        "projectId": request["projectId"],
        "intent": request["intent"],
        "models": {"central": CENTRAL_MODEL, "taskWindow": TASK_MODEL, "subAgent": TASK_MODEL},
        "subAgentPolicy": {"maxConcurrentFirstLevel": 3, "allowGrandchildren": False},
        "windowPolicy": {"maxSequentialAssignments": 2, "allowConcurrentAssignments": False, "centralChoosesReuse": True},
        "boundaries": {
            "writePerformed": False,
            "downstreamInvoked": False,
            "businessWriteAllowed": False,
            "dispatchPerformed": False,
            "downstreamGatesStillRequired": True,
        },
        "registryDigest": canonical_digest(registry),
    }


def route(data_root: Path, request: Dict[str, Any], registry: Dict[str, Any]) -> Dict[str, Any]:
    validate_request(request)
    output = decision_base(request, registry)
    intent = request["intent"]
    if intent == "INITIALIZE_PROJECT":
        output.update(routeStatus="ROUTED_PREPARE" if request["executionMode"] != "APPLY" else "ROUTED_APPLY_REQUIRES_DOWNSTREAM_GATE", targetStage="C02", targetSkill="new-project-initializer", requiredAction="CHECK_AND_PREPARE_PROJECT_INITIALIZATION")
        return output
    if intent == "INITIALIZE_LEDGER":
        mode_status = {
            "READ_ONLY": "ROUTED_READ_ONLY",
            "PREPARE": "ROUTED_PREPARE",
            "APPLY": "ROUTED_APPLY_REQUIRES_DOWNSTREAM_GATE",
        }[request["executionMode"]]
        output.update(routeStatus=mode_status, targetStage="C03", targetSkill="engineering-ledger-manager", requiredAction="PREPARE_OR_INITIALIZE_VERIFIED_ENGINEERING_LEDGER")
        return output

    try:
        ledger = load_ledger(data_root, request["projectId"])
    except LedgerError as error:
        raise CentralRoutingError(str(error))
    recovery = ledger.get("recovery", {})
    recovery_state = recovery.get("state", "UNKNOWN")
    if recovery_state not in {"NORMAL", "CLOSED"} and intent not in RECOVERY_INTENTS:
        output.update(
            routeStatus="RECOVERY_OVERRIDE",
            targetStage="C08",
            targetSkill="disconnection-recovery-controller",
            requiredAction="VERIFY_OR_CONTINUE_FROZEN_RECOVERY_BEFORE_NORMAL_ROUTING",
            recoveryState=recovery_state,
            recoveryCaseId=recovery.get("activeCaseId"),
        )
        return output
    if intent in DISPATCH_INTENTS:
        output.update(
            routeStatus="ROUTED_APPLY_REQUIRES_C10_GATES",
            targetStage="C10",
            targetSkill="task-window-dispatch-controller",
            requiredAction="PREPARE_TWO_PHASE_RUNTIME_DISPATCH",
        )
        return output
    if intent in PENDING_INTENTS:
        output.update(
            routeStatus="BLOCKED_CAPABILITY_NOT_IMPLEMENTED",
            targetStage="C08",
            targetSkill=None,
            requiredAction="COMPLETE_RECOVERY_REVIEW_BEFORE_BUSINESS_RESUME",
        )
        return output
    stage, skill, required_action = ROUTES[intent]
    mode_status = {
        "READ_ONLY": "ROUTED_READ_ONLY",
        "PREPARE": "ROUTED_PREPARE",
        "APPLY": "ROUTED_APPLY_REQUIRES_DOWNSTREAM_GATE",
    }[request["executionMode"]]
    output.update(routeStatus=mode_status, targetStage=stage, targetSkill=skill, requiredAction=required_action)
    return output


def status(registry: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "recordType": "C09_CENTRAL_INTEGRATION_STATUS",
        "status": "READY",
        "canonicalCentralSkill": CENTRAL_SKILL,
        "bossEntrySkill": BOSS_ENTRY_SKILL,
        "centralEntryCount": 1,
        "readyStages": list(EXPECTED),
        "pendingStages": [],
        "models": {"central": CENTRAL_MODEL, "taskWindow": TASK_MODEL, "subAgent": TASK_MODEL},
        "subAgentPolicy": {"maxConcurrentFirstLevel": 3, "allowGrandchildren": False},
        "windowPolicy": {"maxSequentialAssignments": 2, "allowConcurrentAssignments": False, "centralChoosesReuse": True},
        "boundaries": {"naturalLanguageGatewayAvailable": True, "onePassExecutionMapAvailable": True, "scopedBatchApprovalAvailable": True, "automaticSuccessorDispatchAvailable": True, "twoPhaseDispatchAvailable": True, "twoTaskWindowLifecycleEnforced": True, "projectBoundDispatchRequired": True, "runtimeConfirmationRequired": True, "businessExecutionAvailable": False},
        "registryDigest": canonical_digest(registry),
    }


def continue_successors(args: argparse.Namespace, registry: Dict[str, Any]) -> Dict[str, Any]:
    data_root = load_data_root(args)
    project_id = require_reference(args.project_id, "C09_PROJECT_ID_INVALID")
    validation_id = require_reference(args.validation_id, "C09_VALIDATION_ID_INVALID")
    if args.writer_id != CENTRAL_WRITER:
        raise CentralRoutingError("C09_WRITER_NOT_AUTHORIZED")
    execution_map_raw = load_private_json(data_root, args.execution_map, "C09_EXECUTION_MAP_INVALID_JSON")
    execution_map = validate_execution_map(execution_map_raw, project_id)
    map_digest = canonical_digest(execution_map_raw)
    existing_paths = successor_batch_paths(data_root, project_id, validation_id)
    existing_batches = [load_json(path, "C09_SUCCESSOR_BATCH_INVALID") for path in existing_paths]
    if any(batch.get("source", {}).get("executionMapDigest") != map_digest for batch in existing_batches):
        raise CentralRoutingError("C09_SUCCESSOR_BATCH_EXISTS_FOR_DIFFERENT_MAP")
    previously_prepared: Dict[str, Dict[str, Any]] = {}
    for batch in existing_batches:
        for item in batch.get("preparedTasks", []):
            if isinstance(item, dict) and isinstance(item.get("taskId"), str):
                previously_prepared[item["taskId"]] = item
    finalization = verify_finalization_data(data_root, project_id, validation_id)
    trigger = finalization.get("successorDispatch", {})
    if (
        finalization.get("bossDecision") != "APPROVED"
        or trigger.get("required") is not True
        or trigger.get("dispatchOnlyIfScopeVerified") is not True
    ):
        raise CentralRoutingError("C09_VALID_SUCCESSOR_TRIGGER_REQUIRED")
    _, ledger_code = verify_ledger(argparse.Namespace(data_root=str(data_root), config=None, project_id=project_id))
    if ledger_code != 0:
        raise CentralRoutingError("C09_LEDGER_INTEGRITY_UNVERIFIED")
    ledger = load_ledger(data_root, project_id)
    if ledger.get("recovery", {}).get("state") not in {"NORMAL", "CLOSED"}:
        raise CentralRoutingError("C09_RECOVERY_STATE_BLOCKS_SUCCESSOR_DISPATCH")
    if ledger.get("hardStops"):
        raise CentralRoutingError("C09_LEDGER_HARD_STOP_BLOCKS_SUCCESSOR_DISPATCH")

    authorization = execution_map["authorization"]
    task_ids = [item["taskId"] for item in execution_map["executionPlan"]["tasks"]]
    successor_inputs: Dict[str, Dict[str, Any]] = {}

    # These checks protect the integrity and authorization of the complete Boss-
    # approved map, so they run before any task-specific mutation.
    for item in execution_map["executionPlan"]["tasks"]:
        if not isinstance(ledger.get("tasks", {}).get(item["taskId"]), dict):
            raise CentralRoutingError(f"C09_EXECUTION_MAP_TASK_NOT_IN_LEDGER:{item['taskId']}")
        missing_dependencies = [dependency for dependency in item["dependencies"] if dependency not in ledger.get("tasks", {})]
        if missing_dependencies:
            raise CentralRoutingError(f"C09_EXECUTION_MAP_DEPENDENCY_NOT_IN_LEDGER:{item['taskId']}")
        review_path = Path(item["c05ReviewPath"]).expanduser().resolve()
        request_path = Path(item["c10RequestPath"]).expanduser().resolve()
        if not review_path.is_file() or not request_path.is_file() or not is_within(review_path, data_root) or not is_within(request_path, data_root):
            raise CentralRoutingError("C09_SUCCESSOR_INPUT_OUTSIDE_PRIVATE_DATA_ROOT")
        review_raw = load_json(review_path, "C09_SUCCESSOR_C05_REVIEW_INVALID")
        request_raw = load_json(request_path, "C09_SUCCESSOR_C10_REQUEST_INVALID")
        scope = request_raw.get("bossDispatchAuthorization", {}).get("scope", {})
        boss_reference = request_raw.get("bossDispatchAuthorization", {}).get("reference")
        if (
            review_raw.get("taskId") != item["taskId"]
            or review_raw.get("reviewId") != item["c05ReviewId"]
            or review_raw.get("bossReview", {}).get("reference") != authorization["bossApprovalRef"]
            or request_raw.get("taskId") != item["taskId"]
            or scope.get("type") != "EXECUTION_MAP"
            or scope.get("scopeId") != authorization["scopeId"]
            or scope.get("scopeDigest") != authorization["scopeDigest"]
            or set(scope.get("taskIds", [])) != set(task_ids)
            or boss_reference != authorization["bossApprovalRef"]
        ):
            raise CentralRoutingError("C09_SUCCESSOR_AUTHORIZATION_SCOPE_MISMATCH")
        successor_inputs[item["taskId"]] = {
            "reviewPath": review_path,
            "requestPath": request_path,
            "request": request_raw,
        }

    prepared_tasks: List[Dict[str, Any]] = []
    waiting_tasks: List[Dict[str, Any]] = []
    completed_tasks: List[Dict[str, Any]] = []
    active_tasks: List[Dict[str, Any]] = []
    previously_prepared_tasks: List[Dict[str, Any]] = []
    blocked_tasks: List[Dict[str, Any]] = []
    failed_tasks: List[Dict[str, Any]] = []

    for item in execution_map["executionPlan"]["tasks"]:
        task = ledger["tasks"][item["taskId"]]
        task_status = task.get("status")
        if task_status == "DONE":
            completed_tasks.append({"taskId": item["taskId"], "taskStatus": task_status})
            continue
        if task_status in {"IN_PROGRESS", "NEEDS_REVIEW", "PARTIAL"}:
            active_tasks.append({"taskId": item["taskId"], "taskStatus": task_status})
            continue
        if item["taskId"] in previously_prepared:
            if task_status != "READY":
                raise CentralRoutingError(f"C09_PREPARED_TASK_LEDGER_STATE_INVALID:{item['taskId']}")
            previously_prepared_tasks.append({
                **previously_prepared[item["taskId"]],
                "taskStatus": task_status,
                "dispatchState": "PREPARED_AWAITING_RUNTIME_CONFIRMATION",
            })
            continue
        if task_status not in {"PLANNED", "READY"}:
            blocked_tasks.append({
                "taskId": item["taskId"],
                "stage": "LEDGER_STATUS",
                "reason": "C09_TASK_STATUS_NOT_DISPATCHABLE",
                "taskStatus": task_status,
            })
            continue
        unmet = [dependency for dependency in item["dependencies"] if ledger.get("tasks", {}).get(dependency, {}).get("status") != "DONE"]
        if unmet:
            waiting_tasks.append({"taskId": item["taskId"], "taskStatus": task_status, "unmetDependencies": unmet})
            continue

        review_path = successor_inputs[item["taskId"]]["reviewPath"]
        request_path = successor_inputs[item["taskId"]]["requestPath"]
        request_raw = successor_inputs[item["taskId"]]["request"]
        if task_status == "PLANNED":
            try:
                c05_preview, c05_preview_code = evaluate_occupancy(argparse.Namespace(
                    data_root=str(data_root), config=None, project_id=project_id, writer_id=CENTRAL_WRITER,
                    command="evaluate", package_id=item["packageId"], review=str(review_path), dry_run=True, apply=False,
                ))
            except (LedgerError, OccupancyError, TaskPackageError, ValueError) as error:
                reason = str(error)
                target_list = blocked_tasks if reason.startswith("C05_GATE_NOT_READY:") else failed_tasks
                target_list.append({"taskId": item["taskId"], "stage": "C05_PREVIEW", "reason": reason})
                continue
            if c05_preview_code != 0 or c05_preview.get("status") != "ELIGIBLE_FOR_DISPATCH_APPROVAL":
                blocked_tasks.append({
                    "taskId": item["taskId"],
                    "stage": "C05_PREVIEW",
                    "reason": c05_preview.get("status", "C09_SUCCESSOR_C05_NOT_ELIGIBLE"),
                })
                continue
            try:
                c05_result, c05_code = evaluate_occupancy(argparse.Namespace(
                    data_root=str(data_root), config=None, project_id=project_id, writer_id=CENTRAL_WRITER,
                    command="evaluate", package_id=item["packageId"], review=str(review_path), dry_run=False, apply=True,
                ))
            except (LedgerError, OccupancyError, TaskPackageError, ValueError) as error:
                reason = str(error)
                target_list = blocked_tasks if reason.startswith("C05_GATE_NOT_READY:") else failed_tasks
                target_list.append({"taskId": item["taskId"], "stage": "C05", "reason": reason})
                continue
            if c05_code != 0 or c05_result.get("status") not in {"ELIGIBLE_FOR_DISPATCH_APPROVAL", "IDEMPOTENT_EXISTING_C05_DECISION"}:
                blocked_tasks.append({
                    "taskId": item["taskId"],
                    "stage": "C05",
                    "reason": c05_result.get("status", "C09_SUCCESSOR_C05_NOT_ELIGIBLE"),
                })
                continue
        try:
            c10_result, c10_code = prepare_dispatch(argparse.Namespace(
                data_root=str(data_root), config=None, project_id=project_id, writer_id=CENTRAL_WRITER,
                command="prepare", package_id=item["packageId"], review_id=item["c05ReviewId"], request=str(request_path),
            ))
        except (DispatchError, LedgerError, OccupancyError, TaskPackageError, ValueError) as error:
            failed_tasks.append({"taskId": item["taskId"], "stage": "C10", "reason": str(error)})
            continue
        if c10_code != 0 or c10_result.get("status") not in {"READY_FOR_RUNTIME_DISPATCH", "IDEMPOTENT_EXISTING_DISPATCH_PLAN"}:
            blocked_tasks.append({
                "taskId": item["taskId"],
                "stage": "C10",
                "reason": c10_result.get("status", "C09_SUCCESSOR_C10_NOT_READY"),
            })
            continue
        prepared_tasks.append({
            "taskId": item["taskId"],
            "packageId": item["packageId"],
            "reviewId": item["c05ReviewId"],
            "dispatchId": request_raw.get("dispatchId"),
            "windowAction": c10_result.get("windowAction"),
            "c10Status": c10_result.get("status"),
        })
        ledger = load_ledger(data_root, project_id)

    classifications = {
        "waitingTasks": waiting_tasks,
        "completedTasks": completed_tasks,
        "activeTasks": active_tasks,
        "previouslyPreparedTasks": previously_prepared_tasks,
        "blockedTasks": blocked_tasks,
        "failedTasks": failed_tasks,
    }
    if not prepared_tasks:
        if existing_batches:
            status_value = "IDEMPOTENT_SUCCESSOR_BATCH"
        else:
            status_value = "WAITING_FOR_SUCCESSOR_DEPENDENCIES" if waiting_tasks and not blocked_tasks and not failed_tasks else "NO_SUCCESSOR_READY"
        return {
            "status": status_value,
            "validationId": validation_id,
            "preparedCount": 0,
            **classifications,
            "writePerformed": False,
            "runtimeDispatchPerformed": False,
            "recomputedFromLiveLedger": True,
        }
    revision = len(existing_batches) + 1
    target = successor_batch_dir(data_root, project_id, validation_id) / f"successor-batch-r{revision:04d}.json"
    artifact = {
        "schemaVersion": "0.16.0",
        "recordType": "C09_SUCCESSOR_DISPATCH_BATCH",
        "projectId": project_id,
        "validationId": validation_id,
        "revision": revision,
        "completedTaskId": finalization["taskId"],
        "planId": execution_map["planId"],
        "authorization": authorization,
        "preparedTasks": prepared_tasks,
        **classifications,
        "source": {
            "executionMapDigest": map_digest,
            "finalizationDigest": canonical_digest(finalization),
            "registryDigest": canonical_digest(registry),
            "previousBatchRefs": [str(path) for path in existing_paths],
        },
        "boundary": {
            "c05AndC10PrepareInvoked": True,
            "runtimeDispatchPerformed": False,
            "runtimeConfirmationStillRequired": True,
            "bossRepromptRequired": False,
            "businessWritePerformed": False,
        },
    }
    write_exclusive(target, artifact)
    return {
        "status": "READY_FOR_BATCH_RUNTIME_DISPATCH",
        "validationId": validation_id,
        "preparedCount": len(prepared_tasks),
        "preparedTasks": prepared_tasks,
        **classifications,
        "batchArtifact": str(target),
        "writePerformed": True,
        "runtimeDispatchPerformed": False,
        "bossRepromptRequired": False,
        "recomputedFromLiveLedger": True,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="C09 central governance router")
    parser.add_argument("--data-root", required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status")
    route_parser = subparsers.add_parser("route")
    route_parser.add_argument("--request", required=True)
    successor_parser = subparsers.add_parser("continue-successors")
    successor_parser.add_argument("--project-id", required=True)
    successor_parser.add_argument("--validation-id", required=True)
    successor_parser.add_argument("--execution-map", required=True)
    successor_parser.add_argument("--writer-id", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        data_root = load_data_root(args)
        registry = load_registry()
        if args.command == "status":
            output = status(registry)
        elif args.command == "route":
            request_path = Path(args.request).expanduser().resolve()
            if data_root != request_path and data_root not in request_path.parents:
                raise CentralRoutingError("C09_REQUEST_MUST_BE_INSIDE_PRIVATE_DATA_ROOT")
            output = route(data_root, load_json(request_path, "C09_ROUTING_REQUEST_INVALID_JSON"), registry)
        else:
            output = continue_successors(args, registry)
        print_result(output)
        return 0
    except (C02Error, CentralRoutingError, DispatchError, HandoverValidationError, LedgerError, OccupancyError, TaskPackageError, ValueError) as error:
        print_result({"status": "REFUSED", "reason": str(error), "writePerformed": False, "dispatchPerformed": False})
        return 2


if __name__ == "__main__":
    sys.exit(main())
