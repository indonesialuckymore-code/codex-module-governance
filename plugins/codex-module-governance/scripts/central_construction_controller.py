#!/usr/bin/env python3
"""C09 deterministic central router. It routes governance work; it never performs it."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from initialize_project import C02Error, PROJECT_ID_PATTERN, load_data_root
from ledger_manager import LedgerError, load_ledger


SCHEMA_VERSION = "0.12.0"
CENTRAL_SKILL = "central-construction-controller"
CENTRAL_MODEL = "gpt-5.6-sol"
TASK_MODEL = "gpt-5.6-terra"
REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
REPO_ROOT = Path(__file__).resolve().parents[3]
REGISTRY_PATH = REPO_ROOT / "config" / "core-capability-registry.json"
PLUGIN_SKILLS = REPO_ROOT / "plugins" / "codex-module-governance" / "skills"
PLUGIN_SCRIPTS = REPO_ROOT / "plugins" / "codex-module-governance" / "scripts"

EXPECTED = {
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


def validate_registry(registry: Dict[str, Any], check_files: bool = True) -> Dict[str, Any]:
    required = {"schemaVersion", "recordType", "canonicalCentralSkill", "capabilities", "pendingCapabilities"}
    if set(registry) != required or registry.get("schemaVersion") != SCHEMA_VERSION or registry.get("recordType") != "C09_CORE_CAPABILITY_REGISTRY":
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
        if check_files and (not (PLUGIN_SKILLS / skill / "SKILL.md").is_file() or not (PLUGIN_SCRIPTS / script).is_file()):
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
    context_keys = {"taskId", "packageId", "validationId", "recoveryCaseId", "windowId"}
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
        "centralEntryCount": 1,
        "readyStages": list(EXPECTED),
        "pendingStages": [],
        "models": {"central": CENTRAL_MODEL, "taskWindow": TASK_MODEL, "subAgent": TASK_MODEL},
        "subAgentPolicy": {"maxConcurrentFirstLevel": 3, "allowGrandchildren": False},
        "boundaries": {"naturalLanguageGatewayAvailable": True, "twoPhaseDispatchAvailable": True, "runtimeConfirmationRequired": True, "businessExecutionAvailable": False},
        "registryDigest": canonical_digest(registry),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="C09 central governance router")
    parser.add_argument("--data-root", required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status")
    route_parser = subparsers.add_parser("route")
    route_parser.add_argument("--request", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        data_root = load_data_root(args)
        registry = load_registry()
        if args.command == "status":
            output = status(registry)
        else:
            request_path = Path(args.request).expanduser().resolve()
            if data_root != request_path and data_root not in request_path.parents:
                raise CentralRoutingError("C09_REQUEST_MUST_BE_INSIDE_PRIVATE_DATA_ROOT")
            output = route(data_root, load_json(request_path, "C09_ROUTING_REQUEST_INVALID_JSON"), registry)
        print_result(output)
        return 0
    except (C02Error, CentralRoutingError, ValueError) as error:
        print_result({"status": "REFUSED", "reason": str(error), "writePerformed": False, "dispatchPerformed": False})
        return 2


if __name__ == "__main__":
    sys.exit(main())
