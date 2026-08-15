#!/usr/bin/env python3
"""C11 external Skill registry, review, replacement and protocol adapter."""

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
from ledger_manager import CENTRAL_WRITER, LedgerError, canonical_digest, commit_mutation, ledger_lock, load_ledger, utc_now


SCHEMA_VERSION = "0.11.0"
PROTOCOL = "codex-governance-protocol-v1"
REGISTRY_ROOT = Path("external-skill-registry")
REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{2,255}$")
SAFE_PERMISSIONS = {"READ_LOCAL", "WRITE_ISOLATED_LOCAL", "NETWORK", "BROWSER", "LARK", "GITHUB", "DOCUMENT", "SPREADSHEET", "DATABASE", "EMAIL", "DEPLOYMENT"}


class SkillAdapterError(Exception):
    """Safe refusal; never installs or invokes an external Skill."""


def print_result(value: Dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def ref(value: Any, error: str) -> str:
    if not isinstance(value, str) or not REFERENCE.fullmatch(value.strip()): raise SkillAdapterError(error)
    return value.strip()


def exact(value: Any, keys: set[str], error: str) -> Dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys: raise SkillAdapterError(error)
    return value


def registry_path(root: Path, project_id: str) -> Path:
    return root / REGISTRY_ROOT / project_id / "registry.json"


def receipt_dir(root: Path, project_id: str) -> Path:
    return root / REGISTRY_ROOT / project_id / "receipts"


def empty_registry(project_id: str) -> Dict[str, Any]:
    return {"schemaVersion": SCHEMA_VERSION, "recordType": "C11_EXTERNAL_SKILL_REGISTRY", "projectId": project_id, "revision": 0, "protocol": PROTOCOL, "slots": {}, "receiptIds": [], "latestReceiptId": None}


def read_json(path: Path, error: str) -> Dict[str, Any]:
    try: value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError): raise SkillAdapterError(error)
    if not isinstance(value, dict): raise SkillAdapterError(error)
    return value


def load_registry(root: Path, project_id: str) -> Dict[str, Any]:
    path = registry_path(root, project_id)
    if not path.exists(): return empty_registry(project_id)
    value = read_json(path, "C11_REGISTRY_INVALID_JSON")
    required = {"schemaVersion", "recordType", "projectId", "revision", "protocol", "slots", "receiptIds", "latestReceiptId"}
    if set(value) != required or value["schemaVersion"] != SCHEMA_VERSION or value["recordType"] != "C11_EXTERNAL_SKILL_REGISTRY" or value["projectId"] != project_id or value["protocol"] != PROTOCOL: raise SkillAdapterError("C11_REGISTRY_SCHEMA_UNSUPPORTED")
    return value


def write_atomic(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temp.open("x", encoding="utf-8") as stream: stream.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        os.replace(temp, path)
    finally: temp.unlink(missing_ok=True)


def write_exclusive(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream: stream.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    except FileExistsError: raise SkillAdapterError("C11_IMMUTABLE_RECEIPT_EXISTS")


@contextmanager
def lock(root: Path, project_id: str) -> Iterator[None]:
    target = root / REGISTRY_ROOT / project_id / ".lock"; target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("x", encoding="utf-8") as stream: stream.write(str(os.getpid()))
    except FileExistsError: raise SkillAdapterError("C11_REGISTRY_LOCK_PRESENT")
    try: yield
    finally: target.unlink(missing_ok=True)


def load_private(raw_path: str, root: Path) -> Tuple[Dict[str, Any], str]:
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file() or not is_within(path, root): raise SkillAdapterError("C11_INPUT_MUST_BE_INSIDE_PRIVATE_DATA_ROOT")
    value = read_json(path, "C11_REQUEST_INVALID_JSON"); return value, canonical_digest(value)


def validate_request(raw: Dict[str, Any], project_id: str) -> Dict[str, Any]:
    keys = {"requestSchemaVersion", "recordType", "requestId", "projectId", "slotId", "requirement", "candidate", "availability", "bossAuthorization"}
    value = exact(raw, keys, "C11_REQUEST_SCHEMA_UNSUPPORTED")
    if value["requestSchemaVersion"] != SCHEMA_VERSION or value["recordType"] != "C11_EXTERNAL_SKILL_REQUEST" or value["projectId"] != project_id: raise SkillAdapterError("C11_REQUEST_SCHEMA_UNSUPPORTED")
    requirement = exact(value["requirement"], {"criticality", "capabilityContract", "fallback"}, "C11_REQUIREMENT_INVALID")
    if requirement["criticality"] not in {"CORE_REQUIRED", "OPTIONAL"}: raise SkillAdapterError("C11_CRITICALITY_INVALID")
    candidate = exact(value["candidate"], {"skillId", "source", "versionPin", "license", "permissions", "adapter", "replacesSkillId"}, "C11_CANDIDATE_INVALID")
    source = exact(candidate["source"], {"type", "locator"}, "C11_SOURCE_INVALID")
    if source["type"] not in {"BUILT_IN", "GITHUB", "PLUGIN", "LOCAL"}: raise SkillAdapterError("C11_SOURCE_TYPE_INVALID")
    pin = exact(candidate["versionPin"], {"type", "value"}, "C11_VERSION_PIN_INVALID")
    if pin["type"] not in {"SEMVER", "COMMIT", "BUNDLED"}: raise SkillAdapterError("C11_VERSION_PIN_REQUIRED")
    license_data = exact(candidate["license"], {"status", "identifier", "evidenceRef"}, "C11_LICENSE_INVALID")
    if license_data["status"] not in {"APPROVED", "PENDING", "REJECTED"}: raise SkillAdapterError("C11_LICENSE_STATUS_INVALID")
    adapter = exact(candidate["adapter"], {"protocol", "inputContract", "outputContract"}, "C11_ADAPTER_INVALID")
    if adapter["protocol"] != PROTOCOL: raise SkillAdapterError("C11_CENTRAL_PROTOCOL_CHANGE_FORBIDDEN")
    permissions = candidate["permissions"]
    if not isinstance(permissions, list) or not permissions or set(permissions) - SAFE_PERMISSIONS: raise SkillAdapterError("C11_PERMISSION_SCOPE_INVALID")
    availability = exact(value["availability"], {"status", "evidenceRef"}, "C11_AVAILABILITY_INVALID")
    if availability["status"] not in {"AVAILABLE", "MISSING", "INCOMPATIBLE"}: raise SkillAdapterError("C11_AVAILABILITY_STATUS_INVALID")
    auth = exact(value["bossAuthorization"], {"status", "reference"}, "C11_BOSS_AUTHORIZATION_INVALID")
    return {
        "requestId": ref(value["requestId"], "C11_REQUEST_ID_INVALID"), "slotId": ref(value["slotId"], "C11_SLOT_ID_INVALID"),
        "criticality": requirement["criticality"], "capabilityContract": ref(requirement["capabilityContract"], "C11_CAPABILITY_CONTRACT_INVALID"), "fallback": ref(requirement["fallback"], "C11_FALLBACK_INVALID"),
        "skillId": ref(candidate["skillId"], "C11_SKILL_ID_INVALID"), "source": {"type": source["type"], "locator": ref(source["locator"], "C11_SOURCE_LOCATOR_INVALID")},
        "versionPin": {"type": pin["type"], "value": ref(pin["value"], "C11_VERSION_PIN_INVALID")},
        "license": {"status": license_data["status"], "identifier": ref(license_data["identifier"], "C11_LICENSE_IDENTIFIER_INVALID"), "evidenceRef": ref(license_data["evidenceRef"], "C11_LICENSE_EVIDENCE_INVALID")},
        "permissions": sorted(set(permissions)), "adapter": {"protocol": PROTOCOL, "inputContract": ref(adapter["inputContract"], "C11_INPUT_CONTRACT_INVALID"), "outputContract": ref(adapter["outputContract"], "C11_OUTPUT_CONTRACT_INVALID")},
        "replacesSkillId": None if candidate["replacesSkillId"] is None else ref(candidate["replacesSkillId"], "C11_REPLACEMENT_ID_INVALID"),
        "availability": {"status": availability["status"], "evidenceRef": ref(availability["evidenceRef"], "C11_AVAILABILITY_EVIDENCE_INVALID")},
        "bossAuthorization": {"status": auth["status"], "reference": None if auth["reference"] is None else ref(auth["reference"], "C11_BOSS_AUTH_REFERENCE_INVALID")},
    }


def outcome(req: Dict[str, Any]) -> Tuple[str, str]:
    if req["license"]["status"] != "APPROVED": return "BLOCKED_LICENSE_REVIEW", "DO_NOT_INSTALL_OR_INVOKE"
    if req["availability"]["status"] == "INCOMPATIBLE": return "BLOCKED_INCOMPATIBLE", "FIND_COMPATIBLE_REPLACEMENT"
    if req["availability"]["status"] == "MISSING":
        return ("BLOCKED_REQUIRED_CAPABILITY", "WRITE_CONTRACT_OR_FIND_REPLACEMENT") if req["criticality"] == "CORE_REQUIRED" else ("DISABLED_OPTIONAL", req["fallback"])
    return "READY_FOR_ACTIVATION", "ACTIVATE_WITH_BOSS_APPROVAL"


def assess(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    root = load_data_root(args); project_id = ref(args.project_id, "C11_PROJECT_ID_INVALID")
    if not PROJECT_ID_PATTERN.fullmatch(project_id): raise SkillAdapterError("C11_PROJECT_ID_INVALID")
    raw, digest = load_private(args.request, root); req = validate_request(raw, project_id); status, next_action = outcome(req)
    return {"status": status, "requestId": req["requestId"], "slotId": req["slotId"], "skillId": req["skillId"], "nextAction": next_action, "protocol": PROTOCOL, "sourceRequestDigest": digest, "installationPerformed": False, "invocationPerformed": False, "ledgerUpdated": False, "writePerformed": False}, 0


def activate(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    root = load_data_root(args); project_id = ref(args.project_id, "C11_PROJECT_ID_INVALID"); raw, digest = load_private(args.request, root); req = validate_request(raw, project_id)
    if args.writer_id != CENTRAL_WRITER: raise SkillAdapterError("C11_WRITER_NOT_AUTHORIZED")
    status, _ = outcome(req)
    if status != "READY_FOR_ACTIVATION": raise SkillAdapterError(f"C11_NOT_ACTIVATABLE:{status}")
    if req["bossAuthorization"]["status"] != "APPROVED" or req["bossAuthorization"]["reference"] is None: raise SkillAdapterError("C11_EXPLICIT_BOSS_ACTIVATION_APPROVAL_REQUIRED")
    with lock(root, project_id), ledger_lock(root, project_id):
        registry = load_registry(root, project_id); existing = registry["slots"].get(req["slotId"])
        if existing and existing.get("sourceRequestDigest") == digest:
            return {"status": "IDEMPOTENT_ACTIVE_SKILL", "slotId": req["slotId"], "skillId": req["skillId"], "writePerformed": False, "ledgerUpdated": False, "installationPerformed": False}, 0
        if existing and req["replacesSkillId"] != existing.get("skillId"): raise SkillAdapterError("C11_REPLACEMENT_RELATION_REQUIRED")
        if not existing and req["replacesSkillId"] is not None: raise SkillAdapterError("C11_REPLACEMENT_TARGET_NOT_ACTIVE")
        before = load_ledger(root, project_id)
        if before.get("recovery", {}).get("state") not in {"NORMAL", "CLOSED"}: raise SkillAdapterError("C11_RECOVERY_STATE_BLOCKS_ACTIVATION")
        entry = {key: req[key] for key in ("slotId", "criticality", "capabilityContract", "fallback", "skillId", "source", "versionPin", "license", "permissions", "adapter", "replacesSkillId", "availability")}
        entry.update({"status": "ACTIVE", "bossApprovalRef": req["bossAuthorization"]["reference"], "sourceRequestDigest": digest, "activatedAt": utc_now()})
        next_registry = json.loads(json.dumps(registry)); next_registry["revision"] += 1; receipt_id = f"receipt-{next_registry['revision']:06d}-{req['requestId']}"; next_registry["slots"][req["slotId"]] = entry; next_registry["receiptIds"].append(receipt_id); next_registry["latestReceiptId"] = receipt_id
        def mutate(after: Dict[str, Any]) -> None:
            after["skills"][req["slotId"]] = {"slotId": req["slotId"], "skillId": req["skillId"], "sourceVersion": req["versionPin"]["value"], "classification": req["criticality"], "registryReceiptId": receipt_id, "protocol": PROTOCOL, "status": "ACTIVE", "registeredAt": utc_now()}
        ledger_result = commit_mutation(root, project_id, before, CENTRAL_WRITER, "C11_ACTIVATE_EXTERNAL_SKILL", {"slotId": req["slotId"], "skillId": req["skillId"], "replacesSkillId": req["replacesSkillId"]}, mutate, caller_thread_ref=args.caller_thread_ref)
        receipt = {"schemaVersion": SCHEMA_VERSION, "recordType": "C11_IMMUTABLE_SKILL_REGISTRY_RECEIPT", "receiptId": receipt_id, "createdAt": utc_now(), "projectId": project_id, "operation": "ACTIVATE_OR_REPLACE", "beforeRegistryDigest": canonical_digest(registry), "afterRegistryDigest": canonical_digest(next_registry), "sourceRequestDigest": digest, "ledgerReceiptId": ledger_result["receiptId"], "afterRegistry": next_registry}
        write_exclusive(receipt_dir(root, project_id) / f"{receipt_id}.json", receipt); write_atomic(registry_path(root, project_id), next_registry)
    return {"status": "EXTERNAL_SKILL_ACTIVE", "slotId": req["slotId"], "skillId": req["skillId"], "replacedSkillId": req["replacesSkillId"], "registryReceiptId": receipt_id, "ledgerReceiptId": ledger_result["receiptId"], "protocol": PROTOCOL, "installationPerformed": False, "invocationPerformed": False, "ledgerUpdated": True, "writePerformed": True}, 0


def resolve(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    root = load_data_root(args); project_id = ref(args.project_id, "C11_PROJECT_ID_INVALID"); slot_id = ref(args.slot_id, "C11_SLOT_ID_INVALID"); registry = load_registry(root, project_id); entry = registry["slots"].get(slot_id)
    if not entry: return {"status": "SKILL_SLOT_NOT_REGISTERED", "slotId": slot_id, "action": "USE_DECLARED_FALLBACK_OR_DISABLE_OPTIONAL", "protocol": PROTOCOL, "writePerformed": False}, 0
    return {"status": "EXTERNAL_SKILL_RESOLVED", "slotId": slot_id, "skillId": entry["skillId"], "versionPin": entry["versionPin"], "permissions": entry["permissions"], "adapter": entry["adapter"], "protocol": PROTOCOL, "boundary": {"taskProtocolChanged": False, "stateMachineChanged": False, "evidenceContractChanged": False, "authorizationChanged": False, "automaticInvocationAllowed": False}, "writePerformed": False}, 0


def verify(args: argparse.Namespace) -> Tuple[Dict[str, Any], int]:
    root = load_data_root(args); project_id = ref(args.project_id, "C11_PROJECT_ID_INVALID"); registry = load_registry(root, project_id); previous = canonical_digest(empty_registry(project_id))
    for index, receipt_id in enumerate(registry["receiptIds"], 1):
        receipt = read_json(receipt_dir(root, project_id) / f"{receipt_id}.json", "C11_RECEIPT_MISSING_OR_INVALID")
        if receipt.get("beforeRegistryDigest") != previous or receipt.get("afterRegistryDigest") != canonical_digest(receipt.get("afterRegistry", {})) or receipt.get("afterRegistry", {}).get("revision") != index: raise SkillAdapterError("C11_RECEIPT_CHAIN_INVALID")
        previous = receipt["afterRegistryDigest"]
    if registry["receiptIds"] and canonical_digest(registry) != previous: raise SkillAdapterError("C11_REGISTRY_AND_RECEIPT_CHAIN_MISMATCH")
    return {"status": "C11_REGISTRY_INTEGRITY_VERIFIED", "projectId": project_id, "revision": registry["revision"], "activeSlotCount": len(registry["slots"]), "protocol": PROTOCOL, "writePerformed": False}, 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="C11 external Skill adapter controller"); roots = parser.add_mutually_exclusive_group(required=True); roots.add_argument("--data-root"); roots.add_argument("--config"); parser.add_argument("--project-id", required=True); parser.add_argument("--writer-id"); parser.add_argument("--caller-thread-ref")
    commands = parser.add_subparsers(dest="command", required=True); assess_p = commands.add_parser("assess"); assess_p.add_argument("--request", required=True); activate_p = commands.add_parser("activate"); activate_p.add_argument("--request", required=True); resolve_p = commands.add_parser("resolve"); resolve_p.add_argument("--slot-id", required=True); commands.add_parser("verify"); return parser.parse_args()


def main() -> int:
    args = parse_args(); handlers = {"assess": assess, "activate": activate, "resolve": resolve, "verify": verify}
    try: result, code = handlers[args.command](args); print_result(result); return code
    except (C02Error, LedgerError, SkillAdapterError) as error: print_result({"status": "REFUSED", "reason": str(error), "installationPerformed": False, "invocationPerformed": False, "ledgerUpdated": False, "writePerformed": False}); return 2


if __name__ == "__main__": sys.exit(main())
