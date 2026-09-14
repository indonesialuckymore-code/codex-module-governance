"""Native handover operation receipts inside C08, not another task ledger.

The agent executes returned native actions. This module never invents a native
thread, delivery, readback or navigation result and never reissues uncertain creation.
"""
import argparse
import hashlib
from pathlib import Path
import role_continuity_controller as c08
from ledger_manager import canonical_digest
from initialize_project import is_within

READBACK_CHECKS = {"GOAL_AND_OUTLINE", "APPROVAL_AND_SCOPE", "OUTCOMES_AND_TASKS", "WAITS_AND_CORRECTIONS",
                   "DELIVERY_FEEDBACK", "EVENTS_AND_MESSAGES", "NEXT_ACTION"}


def successor_first_turn(source, selected_model=None):
    # fork_thread copies history, not necessarily the source's runtime settings.
    model = selected_model if selected_model is not None else source.get("model")
    if selected_model is not None and not c08.valid_model(selected_model):
        raise c08.ContinuityError("C08C_SUCCESSOR_MODEL_INVALID")
    return {
        "tool": "send_message_to_thread",
        "arguments": {"model": model} if c08.valid_model(model) else {},
        "modelSelection": "USER_SELECTED" if selected_model is not None else "PRESERVE_SOURCE",
        "requiresSourceModelReadback": not c08.valid_model(model),
        "requiresActualModelReadback": True,
        "threadIdSource": "RESOLVED_NATIVE_FORK_RESULT",
        "promptRequirements": ["HANDOVER_PACKAGE_REFERENCE", "READ_ONLY_BEFORE_ACTIVATION"],
        "subsequentMessagesOmitModelUnlessUserChangesIt": True,
    }


def origin(root, project, package):
    seen = set()
    while "delta" in package:
        if package["handoverId"] in seen or len(seen) >= 8:
            raise c08.ContinuityError("C08C_HANDOVER_REFRESH_LIMIT")
        seen.add(package["handoverId"])
        previous = c08.load_handover(root, project, package["delta"]["baseHandoverId"])
        if canonical_digest(previous) != package["delta"]["basePackageDigest"]:
            raise c08.ContinuityError("C08C_HANDOVER_BASE_INTEGRITY_INVALID")
        package = previous
    return package


def directory(root, project, package):
    base = origin(root, project, package)
    return c08.handover_root(root, project, base["handoverId"]) / "native-operations"


def save(path, value):
    if path.exists():
        if read(path, "C08C_NATIVE_RECEIPT_INVALID") != value:
            raise c08.ContinuityError("C08C_NATIVE_RECEIPT_CONFLICT")
        return False
    c08.write_exclusive(path, {'artifact': value, 'artifactDigest': canonical_digest(value)})
    return True


def read(path, error):
    sealed = c08.read_json(path, error)
    if (set(sealed) != {'artifact', 'artifactDigest'} or not isinstance(sealed['artifact'], dict)
            or canonical_digest(sealed['artifact']) != sealed['artifactDigest']):
        raise c08.ContinuityError('C08C_NATIVE_RECEIPT_INTEGRITY_INVALID')
    return sealed['artifact']


def evidence(root, value):
    c08.exact(value, {"reference", "file", "sha256"}, "C08C_NATIVE_EVIDENCE_INVALID")
    reference = c08.ref(value["reference"], "C08C_NATIVE_EVIDENCE_INVALID")
    try:
        path = Path(value["file"]).resolve(strict=True)
        if not is_within(path, root) or hashlib.sha256(path.read_bytes()).hexdigest() != value["sha256"]:
            raise c08.ContinuityError("C08C_NATIVE_EVIDENCE_MISMATCH")
    except (OSError, TypeError) as error:
        raise c08.ContinuityError("C08C_NATIVE_EVIDENCE_INVALID") from error
    return {"reference": reference, "sha256": value["sha256"]}


def communication_snapshot(root, project):
    import task_communication_bridge as bridge
    try:
        bridge.verify(argparse.Namespace(data_root=str(root), config=None, project_id=project))
    except bridge.BridgeError as error:
        raise c08.ContinuityError("C08C_COMMUNICATION_INTEGRITY_UNVERIFIED") from error
    directory = bridge.bridge_root(root, project)
    return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(directory.rglob("*.json"))} if directory.exists() else {}


def require_activation_proof(root, project, package, activation):
    directory_path = directory(root, project, package)
    if not (directory_path / "creation-intent.json").exists():
        return  # Historical/manual handovers retain their existing contract.
    bound = read(directory_path / "successor.json", "C08C_SUCCESSOR_RUNTIME_READBACK_REQUIRED")
    proof = read(directory_path / f"readback-{package['handoverId']}.json", "C08C_SUCCESSOR_CONTEXT_READBACK_REQUIRED")
    if (bound["successorThreadRef"] != activation["successorThreadRef"]
            or bound["runtimeProjectId"] != activation["runtimeProjectId"]
            or proof["successorThreadRef"] != activation["successorThreadRef"]
            or proof["handoverPackageDigest"] != canonical_digest(package)
            or proof["communicationDigest"] != canonical_digest(communication_snapshot(root, project))):
        raise c08.ContinuityError("C08C_SUCCESSOR_READBACK_STALE_OR_MISMATCHED")


def operate(args):
    root = c08.load_data_root(args); project = c08.project_ref(args.project_id)
    c08.require_writer(args.writer_id)
    handover_id = c08.ref(args.handover_id, "C08C_HANDOVER_ID_INVALID")
    package = c08.load_handover(root, project, handover_id)
    directory_path = directory(root, project, package)
    with c08.continuity_lock(root, project):
        routing = c08.verify_routing(root, project)
        caller = c08.ref(args.current_thread_ref, "C08C_THREAD_REF_INVALID")
        if args.command == "prepare-successor":
            c08.current_role(routing, "CURRENT_CENTRAL", caller)
            source = routing["roles"][package["sourceRole"]]
            if source["activeThreadRef"] != package["sourceThreadRef"]:
                raise c08.ContinuityError("C08C_HANDOVER_SOURCE_CHANGED")
            authority = c08.ref(args.authorization_ref, "C08C_HANDOVER_AUTHORIZATION_REQUIRED")
            first_turn = successor_first_turn(source, getattr(args, "model", None))
            intent = {"handoverId": origin(root, project, package)["handoverId"], "authorizationRef": authority,
                "sourceThreadRef": package["sourceThreadRef"], "runtimeProjectId": source["runtimeProjectId"],
                "creationKey": "handover-" + canonical_digest({"projectId": project, "handoverId": origin(root, project, package)["handoverId"]})[:32]}
            written = save(directory_path / "creation-intent.json", intent)
            if (directory_path / "successor.json").exists():
                bound = read(directory_path / "successor.json", "C08C_NATIVE_RECEIPT_INVALID")
                return {"status": "SUCCESSOR_ALREADY_BOUND", "writePerformed": False, "successor": bound}, 0
            return {"status": "CREATE_SUCCESSOR_ONCE" if written else "RECONCILE_EXISTING_SUCCESSOR",
                "writePerformed": written, "intent": intent,
                "successorFirstTurn": first_turn,
                "nativeAction": {"tool": "fork_thread", "arguments": {"threadId": intent["sourceThreadRef"]}}
                    if written else {"tool": "list_threads", "reason": "Find the original native result; do not fork again."},
                "roleSwitched": False}, 0
        value, _ = c08.private_json(args.proof, root, "C08C_NATIVE_PROOF_INVALID")
        if args.command == "record-successor":
            c08.current_role(routing, "CURRENT_CENTRAL", caller)
            intent = read(directory_path / "creation-intent.json", "C08C_CREATION_INTENT_REQUIRED")
            c08.exact(value, {"creationKey", "sourceThreadRef", "successorThreadRef", "runtimeProjectId", "model", "permissionProfile", "evidence"}, "C08C_SUCCESSOR_PROOF_INVALID")
            if (value["creationKey"] != intent["creationKey"] or value["sourceThreadRef"] != intent["sourceThreadRef"]
                    or value["runtimeProjectId"] != intent["runtimeProjectId"]
                    or value["successorThreadRef"] == value["sourceThreadRef"]
                    or not c08.valid_model(value["model"])
                    or not isinstance(value["permissionProfile"], str)
                    or value["permissionProfile"] not in {"full-access", ":danger-full-access", "disabled", ":workspace"}):
                raise c08.ContinuityError("C08C_SUCCESSOR_RUNTIME_MISMATCH")
            c08.ref(value["successorThreadRef"], "C08C_THREAD_REF_INVALID")
            sealed = {**value, "evidence": evidence(root, value["evidence"])}
            written = save(directory_path / "successor.json", sealed)
            return {"status": "SUCCESSOR_BOUND_READ_CONTEXT_NEXT", "writePerformed": written,
                "successorThreadRef": value["successorThreadRef"], "roleSwitched": False}, 0
        bound = read(directory_path / "successor.json", "C08C_SUCCESSOR_RUNTIME_READBACK_REQUIRED")
        if args.command == "record-handover-readback":
            c08.exact(value, {"handoverPackageDigest", "successorThreadRef", "communicationDigest", "checks", "evidence"}, "C08C_CONTEXT_PROOF_INVALID")
            if (caller != bound["successorThreadRef"] or value["successorThreadRef"] != caller
                    or value["handoverPackageDigest"] != canonical_digest(package)
                    or not isinstance(value["checks"], dict) or set(value["checks"]) != READBACK_CHECKS
                    or any(item is not True for item in value["checks"].values())
                    or value["communicationDigest"] != package["continuitySnapshot"].get("communicationDigest")
                    or value["communicationDigest"] != canonical_digest(communication_snapshot(root, project))):
                raise c08.ContinuityError("C08C_CONTEXT_READBACK_INCOMPLETE")
            written = save(directory_path / f"readback-{handover_id}.json", {**value, "evidence": evidence(root, value["evidence"])})
            return {"status": "SUCCESSOR_READBACK_RECORDED", "writePerformed": written, "roleSwitched": False}, 0
        if args.command == "record-handover-opened":
            active = c08.current_role(routing, package["role"], bound["successorThreadRef"])
            if active.get("activationBinding", {}).get("packageDigest") != canonical_digest(package):
                raise c08.ContinuityError("C08C_OPEN_ACTIVATION_PACKAGE_MISMATCH")
            if caller not in {package["sourceThreadRef"], bound["successorThreadRef"]}:
                raise c08.ContinuityError("C08C_NATIVE_CALLER_MISMATCH")
            c08.exact(value, {"successorThreadRef", "opened", "evidence"}, "C08C_OPEN_PROOF_INVALID")
            if value["successorThreadRef"] != active["activeThreadRef"] or value["opened"] is not True:
                raise c08.ContinuityError("C08C_NATIVE_OPEN_NOT_CONFIRMED")
            written = save(directory_path / "opened.json", {**value, "evidence": evidence(root, value["evidence"])})
            return {"status": "CENTRAL_HANDOVER_COMPLETE", "successorThreadRef": active["activeThreadRef"],
                "writePerformed": written, "businessWritePerformed": False}, 0
        raise c08.ContinuityError("C08C_NATIVE_OPERATION_UNSUPPORTED")
