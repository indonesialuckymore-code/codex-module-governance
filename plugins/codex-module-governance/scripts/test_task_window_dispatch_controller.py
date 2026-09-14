#!/usr/bin/env python3
"""C10 regression tests with fictional temporary governance data."""

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).parent
C03 = SCRIPTS / "ledger_manager.py"
C05 = SCRIPTS / "occupancy_conflict_checker.py"
C08 = SCRIPTS / "disconnection_recovery_controller.py"
C10 = SCRIPTS / "task_window_dispatch_controller.py"
import test_occupancy_conflict_checker as fixture
import test_disconnection_recovery_controller as recovery_fixture
import task_window_dispatch_controller as dispatch_module


TERRA = "gpt-5.6-terra"


def model_control(method, evidence_ref, model=TERRA):
    return {"model": model, "method": method, "evidenceRef": evidence_ref}


def permission_control(evidence_ref, profile=":workspace", method="PERMISSION_PROFILE_READBACK", writable_roots=None, governance_access=None):
    full_access = profile.lower() in {":danger-full-access", "danger-full-access", "full-access", "disabled"}
    return {
        "permissionClass": "FULL_ACCESS" if full_access else "WORKTREE_SCOPED",
        "profile": profile,
        "method": method,
        "evidenceRef": evidence_ref,
        "writableRoots": [] if full_access else (writable_roots or ["/tmp/.codex/worktrees/abcd/fictional-project"]),
        "governanceDataRootAccess": governance_access or ("NOT_RESTRICTED" if full_access else "DENIED"),
    }


def invoke(script, arguments):
    result = subprocess.run([sys.executable, str(script), *arguments], check=False, capture_output=True, text=True)
    return result.returncode, json.loads(result.stdout)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"); return path


def c03(root, command, *args):
    return invoke(C03, ["--data-root", str(root), "--project-id", fixture.PROJECT_ID, "--writer-id", "codex-module-central", command, *args])


def setup_ready(root, requests=None, window_mode="AUTO", window_id=None, brief_mutation=None):
    fixture.setup_project(root, brief_mutation=brief_mutation)
    review = fixture.create_review(root, requests=requests, window_mode=window_mode, window_id=window_id)
    code, output = invoke(C05, ["--data-root", str(root), "--project-id", fixture.PROJECT_ID, "--writer-id", "codex-module-central", "evaluate", "--package-id", fixture.PACKAGE_ID, "--review", str(review), "--apply"])
    if code != 0: raise AssertionError(output)


def dispatch_request(agents=None, approved=True, dispatch_id="dispatch-c10-001"):
    return {
        "dispatchSchemaVersion": "0.17.0", "recordType": "C10_DISPATCH_REQUEST", "dispatchId": dispatch_id,
        "projectId": fixture.PROJECT_ID, "packageId": fixture.PACKAGE_ID, "reviewId": fixture.REVIEW_ID, "taskId": fixture.TASK_ID,
        "runtimeProject": {"codexProjectId": "codex-project-001", "projectPath": "/tmp/fictional-project", "isGitRepository": True, "environment": "WORKTREE"},
        "bossDispatchAuthorization": {
            "status": "APPROVED" if approved else "PENDING",
            "reference": "boss-review-001",
            "scope": {"type": "EXECUTION_MAP", "scopeId": "central-plan-001", "scopeDigest": "a" * 64, "waveId": "wave-01", "taskIds": [fixture.TASK_ID]},
        },
        "subAgents": agents or [],
    }


def prepare(root, payload):
    path = write(root / "c10-inputs" / "dispatch.json", payload)
    return invoke(C10, ["--data-root", str(root), "--project-id", fixture.PROJECT_ID, "--writer-id", "codex-module-central", "prepare", "--package-id", fixture.PACKAGE_ID, "--review-id", fixture.REVIEW_ID, "--request", str(path)])


def authorize_window_replacement(root):
    """Create and retire one fictional runtime window through the real C10/C08 chain."""
    setup_ready(root)
    code, output = prepare(root, dispatch_request())
    if code != 0:
        raise AssertionError(output)
    code, output = confirm(root, confirmation())
    if code != 0:
        raise AssertionError(output)
    case_id = "recovery-c10-replacement-001"
    payloads = {
        "incident": {
            "incidentSchemaVersion": "0.8.0", "recordType": "C08_DISCONNECTION_INCIDENT",
            "caseId": case_id, "projectId": fixture.PROJECT_ID, "incidentType": "TASK_WINDOW",
            "targetId": "window-c10-001", "taskId": fixture.TASK_ID, "detectedBy": "codex-module-central",
            "detectionRefs": ["runtime-short-execution-c10"], "reasonRef": "runtime-identity-mismatch-c10",
        },
        "takeover": {
            "takeoverSchemaVersion": "0.8.0", "recordType": "C08_CENTRAL_TAKEOVER_INPUT",
            "caseId": case_id, "expectedRecoveryEpoch": 1, "successorInstanceRef": "central-c10-recovery",
            "bossAuthorization": {"status": "APPROVED", "reference": "boss-c10-recovery"},
        },
        "decision": {
            "decisionSchemaVersion": "0.8.0", "recordType": "C08_RECOVERY_DECISION_INPUT",
            "caseId": case_id, "action": "PREPARE_RESUME", "bossDecisionRef": "boss-c10-replace",
            "reasonRef": "replace-window-c10",
        },
        "resume": {
            "resumeSchemaVersion": "0.8.0", "recordType": "C08_TASK_WINDOW_REPLACEMENT_AUTHORIZATION",
            "caseId": case_id, "expectedRecoveryEpoch": 1, "taskId": fixture.TASK_ID,
            "disconnectedWindowId": "window-c10-001",
            "bossAuthorization": {"status": "APPROVED", "reference": "boss-c10-replace"},
            "reasonRef": "replace-window-c10",
        },
    }
    calls = [
        ("freeze", ["--incident", str(write(root / "c08-inputs" / "incident.json", payloads["incident"])), "--apply"]),
        ("takeover", ["--case-id", case_id, "--authorization", str(write(root / "c08-inputs" / "takeover.json", payloads["takeover"]))]),
        ("decide", ["--case-id", case_id, "--decision", str(write(root / "c08-inputs" / "decision.json", payloads["decision"]))]),
        ("resume", ["--case-id", case_id, "--authorization", str(write(root / "c08-inputs" / "resume.json", payloads["resume"]))]),
    ]
    for command, arguments in calls:
        code, output = invoke(C08, [
            "--data-root", str(root), "--project-id", fixture.PROJECT_ID,
            "--writer-id", "codex-module-central", "--caller-thread-ref", "central-thread-c10",
            command, *arguments,
        ])
        if code != 0:
            raise AssertionError(output)


def confirmation(window_id="window-c10-001", reused=False, agents=None, project_id="codex-project-001", cwd="/tmp/.codex/worktrees/abcd/fictional-project", environment="WORKTREE", association_method=None, handoff_refs=None, runtime_ref="thread-c10-001", runtime_title="C-05｜Fictional C-05｜G1", generation=1, model=TERRA, model_method=None, permission_profile=":workspace", permission_method="PERMISSION_PROFILE_READBACK"):
    association_method = association_method or ("EXISTING_REGISTERED_WINDOW" if reused else "DIRECT")
    if handoff_refs is None:
        handoff_refs = ["thread-project-local"] if association_method == "LOCAL_BOOTSTRAP_TO_WORKTREE" else []
    window_method = model_method or ("NATIVE_SEND_MESSAGE_MODEL_OVERRIDE" if reused else "NATIVE_CREATE_THREAD_MODEL_PARAMETER")
    normalized_agents = []
    for agent in agents or []:
        normalized_agents.append({**agent, "modelControl": agent.get("modelControl", model_control("NATIVE_SPAWN_AGENT_MODEL_PARAMETER", agent["runtimeAgentRef"])), "permissionControl": agent.get("permissionControl", permission_control(runtime_ref, permission_profile, "INHERITED_FROM_PARENT_WINDOW"))})
    return {"confirmationSchemaVersion": "0.17.0", "recordType": "C10_RUNTIME_CONFIRMATION", "dispatchId": "dispatch-c10-001", "taskWindow": {"status": "REUSED" if reused else "CREATED", "taskId": fixture.TASK_ID, "runtimeTitle": runtime_title, "generation": generation, "windowId": window_id, "runtimeThreadRef": runtime_ref, "runtimeProjectId": project_id, "runtimeCwd": cwd, "environmentType": environment, "associationMethod": association_method, "associationHandoffRefs": handoff_refs, "modelControl": model_control(window_method, runtime_ref, model), "permissionControl": permission_control(runtime_ref, permission_profile, permission_method)}, "subAgents": normalized_agents}


def confirm(root, value):
    path = write(root / "c10-inputs" / "confirmation.json", value)
    return invoke(C10, ["--data-root", str(root), "--project-id", fixture.PROJECT_ID, "--writer-id", "codex-module-central", "confirm", "--dispatch-id", "dispatch-c10-001", "--confirmation", str(path)])


def legacy_plan(root):
    """Fixture for an already prepared historical local-bootstrap dispatch."""
    path = dispatch_module.plan_path(root, fixture.PROJECT_ID, "dispatch-c10-001")
    plan = json.loads(path.read_text())
    plan["projectAssociationProtocol"]["requiredMethod"] = "LOCAL_BOOTSTRAP_TO_WORKTREE"
    write(path, plan)


def cross_checked_fixture(root):
    saved = root.parent / "saved" / "fictional-project"
    saved.mkdir(parents=True)
    def git(*args):
        subprocess.run(["git", *args], check=True, capture_output=True)
    git("init", str(saved))
    git("-C", str(saved), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "fixture")
    cwd = root.parent / ".codex" / "worktrees" / "fixture" / "fictional-project"
    git("-C", str(saved), "worktree", "add", "--detach", str(cwd))
    request = dispatch_request()
    request["runtimeProject"]["projectPath"] = str(saved)
    value = confirmation(project_id=None, cwd=str(cwd), permission_profile="full-access")
    payloads = {
        "creation": {"target": {"type": "project", "projectId": "codex-project-001", "environment": {"type": "worktree"}}, "threadId": "thread-c10-001"},
        "threadReadback": {"threadId": "thread-c10-001", "projectId": None, "cwd": str(cwd)},
        "savedProject": {"projectId": "codex-project-001", "path": str(saved), "isGitRepository": True},
    }
    evidence = {"method": "NATIVE_CREATION_AND_GIT_COMMON_DIR"}
    for key, payload in payloads.items():
        path = write(root / "native-evidence" / (key + ".json"), payload)
        source = write(root / "native-evidence" / (key + "-source.json"), {"fixture": "fictional native observation", "result": payload})
        evidence[key] = {"file": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "source": {"file": str(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest()},
            "extraction": {field: "/result/" + field for field in payload}}
    value["taskWindow"]["projectAssociationEvidence"] = evidence
    return request, value


def export_fallback(root):
    return invoke(C10, ["--data-root", str(root), "--project-id", fixture.PROJECT_ID, "--writer-id", "codex-module-central", "export-fallback", "--dispatch-id", "dispatch-c10-001"])


def append_request(agents, append_id="append-c10-001", shared_write_risk=False):
    return {
        "appendSchemaVersion": "0.17.0", "recordType": "C10_SUB_AGENT_APPEND_REQUEST", "appendId": append_id,
        "dispatchId": "dispatch-c10-001", "projectId": fixture.PROJECT_ID, "taskId": fixture.TASK_ID, "windowId": "window-c10-001",
        "trigger": {"phase": "DISCOVERY", "delegationReason": "INDEPENDENT_SCOPE", "withinApprovedScope": True, "sharedWriteRisk": shared_write_risk, "summary": "Materials reveal an independent evidence range."},
        "subAgents": agents,
    }


def prepare_append(root, payload):
    path = write(root / "c10-inputs" / "append.json", payload)
    return invoke(C10, ["--data-root", str(root), "--project-id", fixture.PROJECT_ID, "--writer-id", "codex-module-central", "prepare-append", "--dispatch-id", "dispatch-c10-001", "--append-request", str(path)])


def append_confirmation(agents, append_id="append-c10-001"):
    normalized_agents = [{**agent, "modelControl": agent.get("modelControl", model_control("NATIVE_SPAWN_AGENT_MODEL_PARAMETER", agent["runtimeAgentRef"])), "permissionControl": agent.get("permissionControl", permission_control("thread-c10-001", method="INHERITED_FROM_PARENT_WINDOW"))} for agent in agents]
    return {"appendConfirmationSchemaVersion": "0.17.0", "recordType": "C10_SUB_AGENT_APPEND_RUNTIME_CONFIRMATION", "appendId": append_id, "dispatchId": "dispatch-c10-001", "windowId": "window-c10-001", "subAgents": normalized_agents}


def confirm_append(root, payload, append_id="append-c10-001"):
    path = write(root / "c10-inputs" / "append-confirmation.json", payload)
    return invoke(C10, ["--data-root", str(root), "--project-id", fixture.PROJECT_ID, "--writer-id", "codex-module-central", "confirm-append", "--dispatch-id", "dispatch-c10-001", "--append-id", append_id, "--confirmation", str(path)])


def return_payload(agent_id, status="NEEDS_REVIEW", return_id=None):
    return {"returnSchemaVersion": "0.17.0", "recordType": "C10_SUB_AGENT_RETURN", "dispatchId": "dispatch-c10-001", "subAgentId": agent_id, "returnId": return_id or f"return-{agent_id}", "submittedToWindowId": "window-c10-001", "status": status, "scope": "Fictional independent scope.", "confirmedFacts": ["Fictional fact confirmed."], "completedWork": ["Fictional work completed."], "evidenceRefs": [f"evidence-{agent_id}"], "unresolvedRefs": [], "scopeDeviation": [], "risksAndConflicts": [], "recommendedParentAction": "Read the fictional evidence before consolidation."}


def agent_spec(agent_id, role="Fictional helper", reason="INDEPENDENT_SCOPE", mode="READ_ONLY", scope="Read an independent fictional scope."):
    return {"subAgentId": agent_id, "role": role, "level": 1, "delegationReason": reason, "executionMode": mode, "scope": scope}


def quality_review(agents):
    return {
        "qualityReviewSchemaVersion": "0.17.0", "recordType": "C10_PARENT_QUALITY_REVIEW", "qualityReviewId": "quality-c10-001",
        "dispatchId": "dispatch-c10-001", "projectId": fixture.PROJECT_ID, "taskId": fixture.TASK_ID, "windowId": "window-c10-001",
        "subAgentReturns": [{"subAgentId": agent, "returnId": f"return-{agent}"} for agent in agents],
        "acceptanceCoverage": {key: [f"coverage-{key}"] for key in ("positiveCases", "negativeCases", "idempotencyChecks", "rollbackChecks", "logAndHistoryChecks", "readbackChecks")},
        "conflictResolutions": [{"subAgentId": agent, "outcome": "NO_CONFLICT", "resolutionRef": f"resolution-{agent}"} for agent in agents],
        "parentReadbackEvidenceRefs": ["parent-readback-001"], "scopeCheck": {"withinApprovedScope": True, "unexpectedWriteFound": False},
        "unifiedStatus": "NEEDS_REVIEW", "unresolvedRefs": [], "recommendedParentAction": "Submit the consolidated package for independent C06 review.",
    }


def record_quality_review(root, payload):
    path = write(root / "c10-inputs" / "quality-review.json", payload)
    return invoke(C10, ["--data-root", str(root), "--project-id", fixture.PROJECT_ID, "--writer-id", "codex-module-central", "record-parent-quality-review", "--dispatch-id", "dispatch-c10-001", "--quality-review", str(path)])


class C10Tests(unittest.TestCase):
    def test_resumption_requires_a_new_direction_bound_dispatch_plan(self):
        from test_task_corrections import command
        from test_correction_resume import verification
        from test_task_communication_bridge import C08
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private"
            setup_ready(root)
            request = dispatch_request()
            self.assertEqual(prepare(root, request)[0], 0)
            self.assertEqual(invoke(C08, ["--data-root", str(root), "--project-id", fixture.PROJECT_ID,
                "--writer-id", "codex-module-central", "initialize", "--calling-thread-ref", "central-thread-g1",
                "--central-thread-ref", "central-thread-g1", "--runtime-project-id", "runtime-project-c10",
                "--execution-map-ref", "map-c10"])[0], 0)
            code, current = command(root, "record-correction", "--task-id", fixture.TASK_ID,
                "--correction-id", "direction-c10", "--expected-revision", "0", "--instruction-ref", "new-instruction",
                "--reason-ref", "new-focus", "--boss-decision-ref", "boss-direction", "--retain-ref", "original-scope",
                "--supersede-ref", "old-emphasis", project=fixture.PROJECT_ID)
            self.assertEqual(code, 0, current)
            report = verification(root, fixture.PROJECT_ID, fixture.TASK_ID)
            self.assertEqual(command(root, "verify-correction", "--task-id", fixture.TASK_ID,
                "--verification", str(report), project=fixture.PROJECT_ID)[0], 0)
            code, output = prepare(root, request)
            self.assertEqual(code, 2, output)
            self.assertIn("TASK_DIRECTION_VERSION_MISMATCH", output["reason"])
            request["dispatchId"] = "dispatch-new-direction"
            code, output = prepare(root, request)
            self.assertEqual(code, 0, output)
            self.assertEqual(output["nativeRuntime"]["windowOperation"]["requiredDirectionContext"]["revision"], 1)
            code, fallback = invoke(C10, ["--data-root", str(root), "--project-id", fixture.PROJECT_ID,
                "--writer-id", "codex-module-central", "export-fallback", "--dispatch-id", request["dispatchId"]])
            self.assertEqual(0, code, fallback)
            manual = json.loads(Path(fallback["artifact"]).read_text())
            self.assertIn("new-instruction", {item["reference"] for item in manual["taskPackage"]["requiredReading"]})
            self.assertEqual(output["nativeRuntime"]["windowOperation"]["requiredDirectionInstructionRef"], "new-instruction")
            runtime = confirmation(); runtime["dispatchId"] = request["dispatchId"]
            code, output = invoke(C10, ["--data-root", str(root), "--project-id", fixture.PROJECT_ID,
                "--writer-id", "codex-module-central", "--caller-thread-ref", "central-thread-g1", "confirm",
                "--dispatch-id", request["dispatchId"], "--confirmation", str(write(root / "c10-inputs/current-confirmation.json", runtime))])
            self.assertEqual(code, 0, output)

    def test_correction_blocks_reuse_of_an_already_prepared_dispatch_plan(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private"
            setup_ready(root)
            request = dispatch_request()
            self.assertEqual(prepare(root, request)[0], 0)
            self.assertEqual(c03(root, "record-correction", "--task-id", fixture.TASK_ID,
                "--correction-id", "direction-c10", "--expected-revision", "0",
                "--instruction-ref", "current-instruction", "--reason-ref", "change-focus",
                "--boss-decision-ref", "boss-direction", "--retain-ref", "approved-scope",
                "--supersede-ref", "old-emphasis")[0], 0)
            code, output = prepare(root, request)
            self.assertEqual(code, 2, output)
            self.assertIn("TASK_DIRECTION_CORRECTION_PENDING", output["reason"])

    def test_replacement_requires_complete_c08_and_unchanged_c05_occupancy_without_c06_dependency(self):
        event = {
            "at": "2026-01-01T00:00:00Z", "event": "C08_TASK_WINDOW_REPLACEMENT_AUTHORIZED",
            "caseId": "recovery-c10-001", "from": "BLOCKED", "to": "READY",
            "disconnectedWindowId": "window-old-c10",
        }
        request = {"objectKey": "file:fictional-a", "conflictKey": "file:fictional-a", "resourceClass": "FILE", "intent": "WRITE", "exclusive": True}
        decision = {
            "taskId": fixture.TASK_ID, "reviewId": fixture.REVIEW_ID,
            "status": "ELIGIBLE_FOR_DISPATCH_APPROVAL", "source": {"reviewDigest": "a" * 64},
            "executionBoundary": {"occupancyReserved": True},
            "windowDecision": {"status": "OPEN_NEW_WINDOW"},
            "requestedOccupancies": [request], "reservedClaimIds": ["claim-c10-001"],
        }
        claim = {
            "claimId": "claim-c10-001", "ownerType": "task", "ownerId": fixture.TASK_ID,
            "intent": "WRITE", "conflictKey": "file:fictional-a", "resourceClass": "FILE",
            "exclusive": True, "c05ReviewId": fixture.REVIEW_ID,
        }
        ledger = {
            "tasks": {fixture.TASK_ID: {"status": "READY", "history": [event]}},
            "windows": {"window-old-c10": {
                "status": "REPLACED_READ_ONLY", "currentTaskId": None,
                "replacementAuthorizedAt": event["at"],
                "assignmentHistory": [{"taskId": fixture.TASK_ID, "status": "DISCONNECTED_REPLACED", "replacementAuthorizedAt": event["at"]}],
            }},
            "recovery": {"state": "CLOSED", "newDispatchAllowed": True, "activeCaseId": event["caseId"], "occupancyReleaseStatus": "PRESERVED_FOR_REPLACEMENT"},
            "objectOccupancies": {"file:fictional-a": {"objectKey": "file:fictional-a", "status": "CLAIMED", "claims": [claim]}},
            "hardStops": [],
        }
        self.assertTrue(dispatch_module.replacement_dispatch_allowed(ledger, decision, fixture.TASK_ID))
        with_c06 = copy.deepcopy(ledger)
        with_c06["hardStops"] = [{"code": "C06_VALIDATION_CONFLICT", "taskId": fixture.TASK_ID}]
        self.assertTrue(dispatch_module.replacement_dispatch_allowed(with_c06, decision, fixture.TASK_ID))
        mutations = {
            "recovery-not-closed": lambda value: value["recovery"].update(state="RESUME_REVIEW_REQUIRED"),
            "dispatch-not-allowed": lambda value: value["recovery"].update(newDispatchAllowed=False),
            "old-window-not-read-only": lambda value: value["windows"]["window-old-c10"].update(status="DISCONNECTED"),
            "old-assignment-not-replaced": lambda value: value["windows"]["window-old-c10"]["assignmentHistory"][0].update(status="ACTIVE"),
            "occupancy-changed": lambda value: value["objectOccupancies"]["file:fictional-a"]["claims"][0].update(intent="READ"),
            "unrelated-hard-stop": lambda value: value["hardStops"].append({"code": "OTHER_STOP", "taskId": fixture.TASK_ID}),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                changed = copy.deepcopy(ledger); mutate(changed)
                self.assertFalse(dispatch_module.replacement_dispatch_allowed(changed, decision, fixture.TASK_ID))

    def test_disabled_profile_is_accepted_only_as_explicit_unrestricted_full_access(self):
        control = dispatch_module.validate_permission_control(
            {
                "permissionClass": "FULL_ACCESS",
                "profile": "disabled",
                "method": "PERMISSION_PROFILE_READBACK",
                "evidenceRef": "thread-c10-disabled-profile",
                "writableRoots": [],
                "governanceDataRootAccess": "NOT_RESTRICTED",
            },
            {"PERMISSION_PROFILE_READBACK"},
            "C10_WINDOW_PERMISSION_CONTROL_INVALID",
            Path("/tmp/private-governance"),
        )
        self.assertEqual(control["permissionClass"], "FULL_ACCESS")
        self.assertEqual(control["profile"], "disabled")

    def test_green_read_only_parallel_plan(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root, [{"objectKey": "service:shared", "conflictKey": "service:shared", "resourceClass": "SERVICE", "intent": "READ", "exclusive": False}])
            code, output = prepare(root, dispatch_request())
            self.assertEqual(code, 0, output); self.assertEqual(output["trafficLight"], "GREEN"); self.assertFalse(output["dispatchPerformed"])
            self.assertEqual(output["runtimeTarget"], {"type": "project", "projectId": "codex-project-001", "environment": {"type": "worktree"}})
            self.assertEqual(output["projectAssociationProtocol"]["primary"], "CREATE_PROJECT_WORKTREE")
            self.assertEqual(output["projectAssociationProtocol"]["onMissingProjectId"], "VERIFY_NATIVE_CREATION_AND_GIT_COMMON_DIR_OR_STOP")
            self.assertEqual(output["nativeRuntime"]["windowOperation"]["tool"], "codex_app__create_thread")
            self.assertEqual(output["nativeRuntime"]["windowOperation"]["requiredModel"], TERRA)
            self.assertTrue(output["nativeRuntime"]["uiProjectionIsNotTaskState"])
            self.assertFalse(output["windowAction"]["permissionEnforcement"]["dangerFullAccessForbidden"])
            self.assertEqual(output["windowAction"]["permissionEnforcement"]["defaultClass"], "FULL_ACCESS")
            self.assertEqual(output["windowAction"]["permissionEnforcement"]["defaultProfile"], "full-access")
            self.assertEqual(output["authorizationScope"]["type"], "EXECUTION_MAP")
            self.assertEqual(output["taskIdentity"]["runtimeTitle"], "C-05｜Fictional C-05｜G1")

    def test_yellow_isolated_write_plan(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root)
            code, output = prepare(root, dispatch_request())
            self.assertEqual(code, 0, output); self.assertEqual(output["trafficLight"], "YELLOW")

    def test_red_conflict_cannot_prepare(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; fixture.setup_project(root); fixture.add_task(root, "C-OTHER")
            code, _ = fixture.c03(root, "claim-object", "--object-key", "file:fictional-a", "--owner-type", "task", "--owner-id", "C-OTHER", "--intent", "WRITE")
            self.assertEqual(code, 0); review = fixture.create_review(root)
            code, output = invoke(C05, ["--data-root", str(root), "--project-id", fixture.PROJECT_ID, "--writer-id", "codex-module-central", "evaluate", "--package-id", fixture.PACKAGE_ID, "--review", str(review), "--apply"])
            self.assertEqual(output["status"], "CONFLICT_HARD_STOP")
            code, output = prepare(root, dispatch_request())
            self.assertEqual(code, 2); self.assertIn("DISPATCH_ELIGIBILITY_REQUIRED", output["reason"])

    def test_boss_approval_is_required(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root)
            code, output = prepare(root, dispatch_request(approved=False))
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "C10_EXPLICIT_BOSS_DISPATCH_APPROVAL_REQUIRED")

    def test_dispatch_authorization_must_match_the_c05_boss_review(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root)
            payload = dispatch_request(); payload["bossDispatchAuthorization"]["reference"] = "different-unbound-approval"
            code, output = prepare(root, payload)
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "C10_BOSS_AUTHORIZATION_NOT_BOUND_TO_C05_REVIEW")

    def test_task_must_be_inside_batch_authorization_scope(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root); payload = dispatch_request(); payload["bossDispatchAuthorization"]["scope"]["taskIds"] = ["C-OTHER"]
            code, output = prepare(root, payload)
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "C10_TASK_OUTSIDE_BOSS_AUTHORIZATION_SCOPE")

    def test_max_three_first_level_agents(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root)
            agents = [agent_spec(f"agent-c10-{i}") for i in range(4)]
            code, output = prepare(root, dispatch_request(agents))
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "C10_FIRST_LEVEL_SUB_AGENT_LIMIT_EXCEEDED")

    def test_grandchild_agent_is_forbidden(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root)
            invalid = agent_spec("agent-c10-001", role="Helper"); invalid["level"] = 2
            code, output = prepare(root, dispatch_request([invalid]))
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "C10_GRANDCHILD_SUB_AGENT_FORBIDDEN")

    def test_prepare_does_not_change_ledger_or_claim_runtime_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root); ledger_path = root / "module-ledgers" / fixture.PROJECT_ID / "ledger.json"; before = ledger_path.read_bytes()
            code, output = prepare(root, dispatch_request())
            self.assertEqual(code, 0, output); self.assertEqual(before, ledger_path.read_bytes()); self.assertFalse(output["dispatchPerformed"])

    def test_confirm_creates_window_agents_and_progresses_task(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root); specs = [agent_spec("agent-c10-001", role="Readback")]
            self.assertEqual(prepare(root, dispatch_request(specs))[0], 0)
            code, output = confirm(root, confirmation(agents=[{"subAgentId": "agent-c10-001", "status": "CREATED", "runtimeAgentRef": "runtime-agent-c10-001"}]))
            self.assertEqual(code, 0, output); self.assertEqual(output["taskStatus"], "IN_PROGRESS")
            ledger = json.loads((root / "module-ledgers" / fixture.PROJECT_ID / "ledger.json").read_text())
            self.assertEqual(ledger["windows"]["window-c10-001"]["model"], "gpt-5.6-terra"); self.assertEqual(ledger["subAgents"]["agent-c10-001"]["level"], 1)
            self.assertEqual(ledger["windows"]["window-c10-001"]["modelEnforcement"]["method"], "NATIVE_CREATE_THREAD_MODEL_PARAMETER")
            self.assertEqual(ledger["subAgents"]["agent-c10-001"]["modelEnforcement"]["model"], TERRA)
            self.assertEqual(ledger["windows"]["window-c10-001"]["runtimeProjectId"], "codex-project-001")
            self.assertEqual(ledger["windows"]["window-c10-001"]["runtimeTitle"], "C-05｜Fictional C-05｜G1")
            self.assertEqual(ledger["windows"]["window-c10-001"]["permissionEnforcement"]["permissionClass"], "WORKTREE_SCOPED")
            self.assertEqual(ledger["subAgents"]["agent-c10-001"]["permissionEnforcement"]["profile"], ":workspace")

    def test_unknown_task_window_model_is_refused_without_advancing_task(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root); self.assertEqual(prepare(root, dispatch_request())[0], 0)
            ledger_path = root / "module-ledgers" / fixture.PROJECT_ID / "ledger.json"; before = ledger_path.read_bytes()
            code, output = confirm(root, confirmation(model="UNKNOWN"))
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "C10_RUNTIME_MODEL_INVALID"); self.assertEqual(before, ledger_path.read_bytes())

    def test_created_same_directory_fork_accepts_native_send_message_terra_override(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root)
            request = dispatch_request(); request["runtimeProject"]["environment"] = "LOCAL"
            self.assertEqual(prepare(root, request)[0], 0)
            value = confirmation(
                environment="LOCAL", cwd="/tmp/fictional-project", association_method="DIRECT",
                handoff_refs=[], model_method="NATIVE_SEND_MESSAGE_MODEL_OVERRIDE",
                runtime_ref="thread-same-directory-fork-c10", permission_profile="full-access",
            )
            code, output = confirm(root, value)
            self.assertEqual(code, 0, output)
            ledger = json.loads((root / "module-ledgers" / fixture.PROJECT_ID / "ledger.json").read_text())
            window = ledger["windows"]["window-c10-001"]
            self.assertEqual(window["modelEnforcement"]["model"], TERRA)
            self.assertEqual(window["modelEnforcement"]["method"], "NATIVE_SEND_MESSAGE_MODEL_OVERRIDE")
            self.assertEqual(window["modelEnforcement"]["evidenceRef"], "thread-same-directory-fork-c10")
            self.assertEqual(window["runtimeProjectId"], "codex-project-001")
            artifact = json.loads((root / "dispatches" / fixture.PROJECT_ID / "dispatch-c10-001" / "dispatch-confirmation.json").read_text())
            self.assertEqual(artifact["runtimeCwd"], "/tmp/fictional-project")

    def test_created_send_message_override_must_be_bound_to_the_runtime_thread(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root)
            request = dispatch_request(); request["runtimeProject"]["environment"] = "LOCAL"
            self.assertEqual(prepare(root, request)[0], 0)
            value = confirmation(
                environment="LOCAL", cwd="/tmp/fictional-project", association_method="DIRECT",
                handoff_refs=[], model_method="NATIVE_SEND_MESSAGE_MODEL_OVERRIDE",
                runtime_ref="thread-same-directory-fork-c10", permission_profile="full-access",
            )
            value["taskWindow"]["modelControl"]["evidenceRef"] = "different-runtime-thread-c10"
            code, output = confirm(root, value)
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "C10_MODEL_CONTROL_RECEIPT_MISMATCH")

    def test_missing_task_window_model_control_is_refused_without_advancing_task(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root); self.assertEqual(prepare(root, dispatch_request())[0], 0)
            payload = confirmation(); del payload["taskWindow"]["modelControl"]
            code, output = confirm(root, payload)
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "C10_WINDOW_CONFIRMATION_INVALID")

    def test_model_control_must_be_tied_to_the_returned_runtime_id(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root); self.assertEqual(prepare(root, dispatch_request())[0], 0)
            payload = confirmation(); payload["taskWindow"]["modelControl"]["evidenceRef"] = "different-runtime-receipt"
            code, output = confirm(root, payload)
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "C10_MODEL_CONTROL_RECEIPT_MISMATCH")

    def test_full_access_is_accepted_and_advances_task(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root); self.assertEqual(prepare(root, dispatch_request())[0], 0)
            code, output = confirm(root, confirmation(permission_profile="full-access"))
            self.assertEqual(code, 0, output)
            self.assertEqual(output["permissionClass"], "FULL_ACCESS")
            self.assertEqual(output["permissionProfile"], "full-access")
            ledger = json.loads((root / "module-ledgers" / fixture.PROJECT_ID / "ledger.json").read_text())
            self.assertEqual(ledger["windows"]["window-c10-001"]["permissionEnforcement"]["governanceDataRootAccess"], "NOT_RESTRICTED")

    def test_task_window_cannot_receive_the_private_governance_data_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root); self.assertEqual(prepare(root, dispatch_request())[0], 0)
            payload = confirmation(permission_profile=":workspace")
            payload["taskWindow"]["permissionControl"].update({
                "writableRoots": ["/tmp/.codex/worktrees/abcd/fictional-project", str(root)],
                "governanceDataRootAccess": "READ_WRITE",
            })
            code, output = confirm(root, payload)
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "C10_GOVERNANCE_DATA_ROOT_EXPOSED_TO_TASK_WINDOW")

    def test_task_window_writable_root_cannot_be_nested_inside_private_governance_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root); self.assertEqual(prepare(root, dispatch_request())[0], 0)
            payload = confirmation(permission_profile=":workspace")
            payload["taskWindow"]["permissionControl"].update({
                "writableRoots": [str(root / "task-visible-subdirectory")],
                "governanceDataRootAccess": "DENIED",
            })
            code, output = confirm(root, payload)
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "C10_GOVERNANCE_DATA_ROOT_EXPOSED_TO_TASK_WINDOW")

    def test_missing_permission_control_is_refused_without_advancing_task(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root); self.assertEqual(prepare(root, dispatch_request())[0], 0)
            payload = confirmation(); del payload["taskWindow"]["permissionControl"]
            code, output = confirm(root, payload)
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "C10_WINDOW_CONFIRMATION_INVALID")

    def test_permission_control_must_be_tied_to_runtime_id(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root); self.assertEqual(prepare(root, dispatch_request())[0], 0)
            payload = confirmation(); payload["taskWindow"]["permissionControl"]["evidenceRef"] = "different-permission-receipt"
            code, output = confirm(root, payload)
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "C10_PERMISSION_CONTROL_RECEIPT_MISMATCH")

    def test_unknown_sub_agent_model_is_refused_without_advancing_task(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root); specs = [agent_spec("agent-c10-001", role="Readback")]
            self.assertEqual(prepare(root, dispatch_request(specs))[0], 0)
            agent = {"subAgentId": "agent-c10-001", "status": "CREATED", "runtimeAgentRef": "runtime-agent-c10-001", "modelControl": model_control("NATIVE_SPAWN_AGENT_MODEL_PARAMETER", "runtime-agent-c10-001", "UNKNOWN")}
            code, output = confirm(root, confirmation(agents=[agent]))
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "C10_RUNTIME_MODEL_INVALID")

    def test_runtime_title_mismatch_is_needs_review_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root); self.assertEqual(prepare(root, dispatch_request())[0], 0)
            ledger_path = root / "module-ledgers" / fixture.PROJECT_ID / "ledger.json"; before = ledger_path.read_bytes()
            code, output = confirm(root, confirmation(runtime_title="Wrong title"))
            self.assertEqual(code, 2); self.assertEqual(output["status"], "NEEDS_REVIEW"); self.assertEqual(output["reason"], "C10_TASK_IDENTITY_MISMATCH"); self.assertEqual(before, ledger_path.read_bytes())

    def test_wrong_codex_project_is_not_registered(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root); self.assertEqual(prepare(root, dispatch_request())[0], 0)
            ledger_path = root / "module-ledgers" / fixture.PROJECT_ID / "ledger.json"; before = ledger_path.read_bytes()
            code, output = confirm(root, confirmation(project_id="wrong-project"))
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "C10_RUNTIME_PROJECT_ASSOCIATION_MISMATCH"); self.assertEqual(before, ledger_path.read_bytes())

    def test_missing_project_readback_uses_native_evidence_and_real_git_relationship(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root)
            request, value = cross_checked_fixture(root)
            self.assertEqual(prepare(root, request)[0], 0)
            code, output = confirm(root, value)
            self.assertEqual(code, 0, output)
            window = json.loads((root / "module-ledgers" / fixture.PROJECT_ID / "ledger.json").read_text())["windows"]["window-c10-001"]
            self.assertIsNone(window["runtimeProjectId"])
            self.assertEqual(window["verifiedProjectId"], "codex-project-001")
            self.assertEqual(window["projectAssociationEvidence"]["method"], "NATIVE_CREATION_AND_GIT_COMMON_DIR")

    def test_cross_checked_project_rejects_wrong_thread_tampering_and_unrelated_repository(self):
        for corruption in ("digest", "thread", "project", "unrelated", "missing", "source", "cwd_missing"):
            with self.subTest(corruption=corruption), tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / "private"; setup_ready(root)
                request, value = cross_checked_fixture(root)
                self.assertEqual(prepare(root, request)[0], 0)
                evidence = value["taskWindow"]["projectAssociationEvidence"]
                if corruption == "digest":
                    evidence["creation"]["sha256"] = "0" * 64
                elif corruption == "missing":
                    evidence["creation"]["file"] = str(root / "missing.json")
                elif corruption in ("thread", "project"):
                    ref = evidence["creation"]
                    data = json.loads(Path(ref["file"]).read_text())
                    if corruption == "thread": data["threadId"] = "another-thread"
                    else: data["target"]["projectId"] = "another-project"
                    write(Path(ref["file"]), data)
                    ref["sha256"] = hashlib.sha256(Path(ref["file"]).read_bytes()).hexdigest()
                elif corruption in ("unrelated", "cwd_missing"):
                    unrelated = root.parent / "unrelated" / "fictional-project"
                    unrelated.mkdir(parents=True)
                    subprocess.run(["git", "init", str(unrelated)], check=True, capture_output=True)
                    # Same basename, declared saved project and re-hashed evidence are insufficient.
                    plan_file = dispatch_module.plan_path(root, fixture.PROJECT_ID, "dispatch-c10-001")
                    plan = json.loads(plan_file.read_text())
                    plan["runtimeProject"]["projectPath"] = str(unrelated)
                    write(plan_file, plan)
                    ref = evidence["savedProject"]
                    data = json.loads(Path(ref["file"]).read_text()); data["path"] = str(unrelated)
                    write(Path(ref["file"]), data)
                    ref["sha256"] = hashlib.sha256(Path(ref["file"]).read_bytes()).hexdigest()
                    if corruption == "cwd_missing":
                        plan["runtimeProject"]["projectPath"] = str(root / "absent-project")
                        write(plan_file, plan)
                        data["path"] = str(root / "absent-project")
                        write(Path(ref["file"]), data)
                        ref["sha256"] = hashlib.sha256(Path(ref["file"]).read_bytes()).hexdigest()
                else:
                    ref = evidence["creation"]
                    data = json.loads(Path(ref["file"]).read_text()); data["threadId"] = "fabricated-thread"
                    write(Path(ref["file"]), data)
                    ref["sha256"] = hashlib.sha256(Path(ref["file"]).read_bytes()).hexdigest()
                if corruption in ("thread", "project", "unrelated", "cwd_missing"):
                    # Retain internally consistent fictional raw observations so identity/Git checks run.
                    source_path = Path(ref["source"]["file"])
                    write(source_path, {"fixture": "fictional native observation", "result": data})
                    ref["source"]["sha256"] = hashlib.sha256(source_path.read_bytes()).hexdigest()
                ledger = root / "module-ledgers" / fixture.PROJECT_ID / "ledger.json"
                before = ledger.read_bytes()
                code, output = confirm(root, value)
                self.assertEqual(code, 2, output)
                expected = {"digest": "C10_PROJECT_EVIDENCE_DIGEST_MISMATCH", "thread": "C10_PROJECT_EVIDENCE_IDENTITY_MISMATCH", "project": "C10_PROJECT_EVIDENCE_IDENTITY_MISMATCH", "unrelated": "C10_PROJECT_GIT_RELATIONSHIP_MISMATCH", "missing": "C10_PROJECT_EVIDENCE_FILE_INVALID", "source": "C10_PROJECT_EVIDENCE_SOURCE_MISMATCH", "cwd_missing": "C10_PROJECT_GIT_RELATIONSHIP_UNVERIFIED"}
                self.assertEqual(output["reason"], expected[corruption])
                self.assertEqual(before, ledger.read_bytes())

    def test_native_absent_project_field_is_preserved_as_absence_not_filled_in_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root)
            request, value = cross_checked_fixture(root)
            ref = value["taskWindow"]["projectAssociationEvidence"]["threadReadback"]
            source_path = Path(ref["source"]["file"])
            source = json.loads(source_path.read_text()); del source["result"]["projectId"]
            write(source_path, source)
            ref["source"]["sha256"] = hashlib.sha256(source_path.read_bytes()).hexdigest()
            ref["extraction"]["projectId"] = {"pointer": "/result/projectId", "absentAsNull": True}
            self.assertEqual(prepare(root, request)[0], 0)
            code, result = confirm(root, value)
            self.assertEqual(code, 0, result)
            self.assertNotIn("projectId", json.loads(source_path.read_text())["result"])
            window = json.loads((root / "module-ledgers" / fixture.PROJECT_ID / "ledger.json").read_text())["windows"]["window-c10-001"]
            self.assertEqual(window["directProjectReadbackState"], "ABSENT")

    def test_absent_project_field_does_not_accept_wrong_pointer_or_hide_real_value(self):
        for pointer in ("/result/projectTypo", "/wrong-parent/projectId", "/result/projectId"):
            with self.subTest(pointer=pointer), tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / "private"; setup_ready(root)
                request, value = cross_checked_fixture(root)
                ref = value["taskWindow"]["projectAssociationEvidence"]["threadReadback"]
                source_path = Path(ref["source"]["file"])
                source = json.loads(source_path.read_text()); source["result"]["projectId"] = "other-project"
                write(source_path, source)
                ref["source"]["sha256"] = hashlib.sha256(source_path.read_bytes()).hexdigest()
                ref["extraction"]["projectId"] = {"pointer": pointer, "absentAsNull": True}
                self.assertEqual(prepare(root, request)[0], 0)
                self.assertEqual(confirm(root, value)[0], 2)

    def test_native_evidence_rejects_relative_file_and_negative_json_pointer_index(self):
        for corruption in ("relative", "negative-index"):
            with self.subTest(corruption=corruption), tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / "private"; setup_ready(root)
                request, value = cross_checked_fixture(root)
                ref = value["taskWindow"]["projectAssociationEvidence"]["creation"]
                if corruption == "relative":
                    import os
                    ref["file"] = os.path.relpath(ref["file"])
                else:
                    payload = json.loads(Path(ref["file"]).read_text())
                    source_path = Path(ref["source"]["file"])
                    write(source_path, {"results": [payload]})
                    ref["source"]["sha256"] = hashlib.sha256(source_path.read_bytes()).hexdigest()
                    ref["extraction"] = {key: "/results/-1/" + key for key in payload}
                self.assertEqual(prepare(root, request)[0], 0)
                code, result = confirm(root, value)
                self.assertEqual(code, 2, result)

    def test_new_worktree_task_uses_native_direct_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root)
            code, plan = prepare(root, dispatch_request())
            self.assertEqual(code, 0, plan)
            self.assertEqual(plan["projectAssociationProtocol"]["primary"], "CREATE_PROJECT_WORKTREE")
            self.assertEqual(plan["projectAssociationProtocol"]["requiredMethod"], "DIRECT")
            code, output = confirm(root, confirmation(association_method="DIRECT"))
            self.assertEqual(code, 0, output)

    def test_local_bootstrap_then_worktree_handoff_preserves_project_association(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root); self.assertEqual(prepare(root, dispatch_request())[0], 0)
            legacy_plan(root)
            value = confirmation(association_method="LOCAL_BOOTSTRAP_TO_WORKTREE", handoff_refs=["thread-project-local"], runtime_ref="thread-final-worktree")
            code, output = confirm(root, value)
            self.assertEqual(code, 0, output); self.assertEqual(output["associationMethod"], "LOCAL_BOOTSTRAP_TO_WORKTREE")
            ledger = json.loads((root / "module-ledgers" / fixture.PROJECT_ID / "ledger.json").read_text())
            self.assertEqual(ledger["windows"]["window-c10-001"]["associationMethod"], "LOCAL_BOOTSTRAP_TO_WORKTREE")

    def test_worktree_handoff_accepts_same_thread_identity_after_environment_move(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root)
            self.assertEqual(prepare(root, dispatch_request())[0], 0)
            legacy_plan(root)
            value = confirmation(association_method="LOCAL_BOOTSTRAP_TO_WORKTREE",
                handoff_refs=["thread-moved-in-place"], runtime_ref="thread-moved-in-place")
            code, output = confirm(root, value)
            self.assertEqual(code, 0, output)
            self.assertEqual(output["associationMethod"], "LOCAL_BOOTSTRAP_TO_WORKTREE")

    def test_worktree_handoff_requires_one_project_local_thread_ref(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root); self.assertEqual(prepare(root, dispatch_request())[0], 0)
            legacy_plan(root)
            ledger_path = root / "module-ledgers" / fixture.PROJECT_ID / "ledger.json"; before = ledger_path.read_bytes()
            value = confirmation(association_method="LOCAL_BOOTSTRAP_TO_WORKTREE", handoff_refs=[])
            code, output = confirm(root, value)
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "C10_PROJECT_ASSOCIATION_REPAIR_EVIDENCE_INVALID"); self.assertEqual(before, ledger_path.read_bytes())

    def test_direct_association_refuses_false_handoff_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root); self.assertEqual(prepare(root, dispatch_request())[0], 0)
            code, output = confirm(root, confirmation(association_method="DIRECT", handoff_refs=["thread-not-used"]))
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "C10_PROJECT_ASSOCIATION_REPAIR_EVIDENCE_INVALID")

    def test_custom_directory_is_not_accepted_as_project_worktree(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root); self.assertEqual(prepare(root, dispatch_request())[0], 0)
            ledger_path = root / "module-ledgers" / fixture.PROJECT_ID / "ledger.json"; before = ledger_path.read_bytes()
            code, output = confirm(root, confirmation(cwd="/tmp/custom/c001"))
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "C10_NONSTANDARD_WORKTREE_TASK_LOCATION"); self.assertEqual(before, ledger_path.read_bytes())

    def test_partial_runtime_confirmation_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root); specs = [agent_spec("agent-c10-001", role="A"), agent_spec("agent-c10-002", role="B")]
            prepare(root, dispatch_request(specs)); ledger = root / "module-ledgers" / fixture.PROJECT_ID / "ledger.json"; before = ledger.read_bytes()
            code, output = confirm(root, confirmation(agents=[{"subAgentId": "agent-c10-001", "status": "CREATED", "runtimeAgentRef": "runtime-agent-c10-001"}]))
            self.assertEqual(code, 2); self.assertEqual(before, ledger.read_bytes()); self.assertEqual(output["reason"], "C10_SUB_AGENT_CONFIRMATION_COUNT_MISMATCH")

    def test_existing_window_is_reused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; fixture.setup_project(root); code, _ = c03(root, "register-window", "--window-id", "window-existing", "--task-id", fixture.TASK_ID, "--context-mode", "NEW", "--runtime-model-evidence-ref", "manual-terra-window-existing", "--runtime-permission-profile", ":workspace", "--runtime-permission-evidence-ref", "manual-permission-window-existing", "--runtime-writable-root", "/tmp/.codex/worktrees/existing/fictional-project", "--governance-data-root-access", "DENIED"); self.assertEqual(code, 0)
            review = fixture.create_review(root); code, _ = invoke(C05, ["--data-root", str(root), "--project-id", fixture.PROJECT_ID, "--writer-id", "codex-module-central", "evaluate", "--package-id", fixture.PACKAGE_ID, "--review", str(review), "--apply"]); self.assertEqual(code, 0)
            prepare(root, dispatch_request()); code, output = confirm(root, confirmation(window_id="window-existing", reused=True))
            self.assertEqual(code, 0, output); self.assertEqual(output["windowId"], "window-existing")

    def test_reused_window_rechecks_missing_project_field_with_current_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; fixture.setup_project(root)
            code, result = c03(root, "register-window", "--window-id", "window-c10-001", "--task-id", fixture.TASK_ID,
                "--context-mode", "NEW", "--runtime-model-evidence-ref", "thread-c10-001",
                "--runtime-permission-profile", "full-access", "--runtime-permission-evidence-ref", "thread-c10-001",
                "--governance-data-root-access", "NOT_RESTRICTED")
            self.assertEqual(code, 0, result)
            review = fixture.create_review(root)
            self.assertEqual(invoke(C05, ["--data-root", str(root), "--project-id", fixture.PROJECT_ID, "--writer-id", "codex-module-central", "evaluate", "--package-id", fixture.PACKAGE_ID, "--review", str(review), "--apply"])[0], 0)
            request, value = cross_checked_fixture(root)
            value["taskWindow"]["status"] = "REUSED"
            value["taskWindow"]["associationMethod"] = "EXISTING_REGISTERED_WINDOW"
            value["taskWindow"]["modelControl"]["method"] = "RUNTIME_MODEL_READBACK"
            code, prepared = prepare(root, request)
            self.assertEqual(code, 0, prepared)
            self.assertEqual(prepared["projectAssociationProtocol"]["onMissingProjectId"], "VERIFY_NATIVE_CREATION_AND_GIT_COMMON_DIR_OR_STOP")
            code, result = confirm(root, value)
            self.assertEqual(code, 0, result)
            window = json.loads((root / "module-ledgers" / fixture.PROJECT_ID / "ledger.json").read_text())["windows"]["window-c10-001"]
            self.assertIsNone(window["runtimeProjectId"])
            self.assertEqual(window["verifiedProjectId"], "codex-project-001")

    def test_freeze_blocks_prepare(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root); incident = recovery_fixture.incident(kind="CENTRAL", target="codex-module-central", task=None); incident["projectId"] = fixture.PROJECT_ID; incident["caseId"] = "recovery-c10-001"
            path = write(root / "c08-inputs" / "incident.json", incident); code, frozen = invoke(C08, ["--data-root", str(root), "--project-id", fixture.PROJECT_ID, "--writer-id", "codex-module-central", "freeze", "--incident", str(path), "--apply"]); self.assertEqual(code, 0, frozen)
            code, output = prepare(root, dispatch_request()); self.assertEqual(code, 2); self.assertEqual(output["reason"], "C10_RECOVERY_STATE_BLOCKS_DISPATCH")

    def test_closed_c08_replacement_opens_a_new_window_without_c06_hard_stop(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; authorize_window_replacement(root)
            code, output = prepare(root, dispatch_request(dispatch_id="dispatch-c10-002"))
            self.assertEqual(code, 0, output)
            self.assertEqual(output["windowAction"]["action"], "CREATE_TASK")
            self.assertEqual(output["taskIdentity"]["generation"], 2)
            plan = json.loads((root / "dispatches" / fixture.PROJECT_ID / "dispatch-c10-002" / "dispatch-plan.json").read_text())
            self.assertEqual(plan["source"]["replacementAuthorization"]["disconnectedWindowId"], "window-c10-001")
            self.assertRegex(plan["source"]["replacementAuthorization"]["c05ReviewDigest"], r"^[a-f0-9]{64}$")
            self.assertEqual(plan["windowAction"]["action"], "CREATE_TASK")

    def test_replacement_confirmation_refuses_c05_occupancy_drift(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; authorize_window_replacement(root)
            dispatch_id = "dispatch-c10-002"
            code, output = prepare(root, dispatch_request(dispatch_id=dispatch_id))
            self.assertEqual(code, 0, output)
            code, output = c03(root, "claim-object", "--object-key", "file:unexpected-c10", "--owner-type", "task", "--owner-id", fixture.TASK_ID, "--intent", "WRITE")
            self.assertEqual(code, 0, output)
            value = confirmation(window_id="window-c10-002", runtime_ref="thread-c10-002", generation=2, runtime_title="C-05｜Fictional C-05｜G2")
            value["dispatchId"] = dispatch_id
            path = write(root / "c10-inputs" / "replacement-confirmation.json", value)
            code, output = invoke(C10, [
                "--data-root", str(root), "--project-id", fixture.PROJECT_ID,
                "--writer-id", "codex-module-central", "confirm", "--dispatch-id", dispatch_id,
                "--confirmation", str(path),
            ])
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "C10_REPLACEMENT_BOUNDARY_CHANGED_BEFORE_CONFIRMATION")

    def test_replacement_prepare_refuses_c05_occupancy_drift_without_a_hard_stop(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; authorize_window_replacement(root)
            code, output = c03(root, "claim-object", "--object-key", "file:unexpected-c10", "--owner-type", "task", "--owner-id", fixture.TASK_ID, "--intent", "WRITE")
            self.assertEqual(code, 0, output)
            code, output = prepare(root, dispatch_request(dispatch_id="dispatch-c10-002"))
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "C10_REPLACEMENT_BOUNDARY_INVALID")

    def test_prepare_and_confirm_are_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root); payload = dispatch_request(); self.assertEqual(prepare(root, payload)[0], 0); code, second = prepare(root, payload); self.assertEqual(second["status"], "IDEMPOTENT_EXISTING_DISPATCH_PLAN")
            value = confirmation(); self.assertEqual(confirm(root, value)[0], 0); code, second = confirm(root, value); self.assertEqual(second["status"], "IDEMPOTENT_RUNTIME_CONFIRMATION")

    def test_active_window_can_append_an_independent_agent_without_new_boss_approval(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root); self.assertEqual(prepare(root, dispatch_request())[0], 0); self.assertEqual(confirm(root, confirmation())[0], 0)
            payload = append_request([agent_spec("agent-c10-append-001", role="Evidence reader", reason="LARGE_MATERIALS")])
            code, output = prepare_append(root, payload)
            self.assertEqual(code, 0, output); self.assertEqual(output["status"], "READY_FOR_RUNTIME_SUB_AGENT_APPEND")
            runtime = append_confirmation([{"subAgentId": "agent-c10-append-001", "status": "CREATED", "runtimeAgentRef": "runtime-agent-append-001"}])
            code, output = confirm_append(root, runtime)
            self.assertEqual(code, 0, output); self.assertEqual(output["status"], "SUB_AGENT_APPEND_CONFIRMED")
            ledger = json.loads((root / "module-ledgers" / fixture.PROJECT_ID / "ledger.json").read_text())
            agent = ledger["subAgents"]["agent-c10-append-001"]
            self.assertEqual(agent["taskId"], fixture.TASK_ID); self.assertEqual(agent["executionMode"], "READ_ONLY")
            self.assertEqual(agent["modelEnforcement"]["method"], "NATIVE_SPAWN_AGENT_MODEL_PARAMETER")
            code, second = prepare_append(root, payload); self.assertEqual(code, 0); self.assertEqual(second["status"], "IDEMPOTENT_EXISTING_SUB_AGENT_APPEND_PLAN")
            code, second = confirm_append(root, runtime); self.assertEqual(code, 0); self.assertEqual(second["status"], "IDEMPOTENT_SUB_AGENT_APPEND_CONFIRMATION")

    def test_append_refuses_shared_write_risk(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root); prepare(root, dispatch_request()); confirm(root, confirmation())
            code, output = prepare_append(root, append_request([agent_spec("agent-c10-append-001")], shared_write_risk=True))
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "C10_APPEND_SCOPE_OR_WRITE_RISK_NOT_ACCEPTABLE")

    def test_append_refuses_unknown_sub_agent_model(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root); self.assertEqual(prepare(root, dispatch_request())[0], 0); self.assertEqual(confirm(root, confirmation())[0], 0)
            payload = append_request([agent_spec("agent-c10-append-001")]); self.assertEqual(prepare_append(root, payload)[0], 0)
            agent = {"subAgentId": "agent-c10-append-001", "status": "CREATED", "runtimeAgentRef": "runtime-agent-append-001", "modelControl": model_control("NATIVE_SPAWN_AGENT_MODEL_PARAMETER", "runtime-agent-append-001", "UNKNOWN")}
            code, output = confirm_append(root, append_confirmation([agent]))
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "C10_RUNTIME_MODEL_INVALID")

    def test_sub_agent_returns_only_to_parent_and_cannot_declare_done(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root); specs = [agent_spec("agent-c10-001", role="A")]; prepare(root, dispatch_request(specs)); confirm(root, confirmation(agents=[{"subAgentId": "agent-c10-001", "status": "CREATED", "runtimeAgentRef": "runtime-agent-c10-001"}]))
            bad = write(root / "c10-inputs" / "return.json", return_payload("agent-c10-001", status="DONE")); code, output = invoke(C10, ["--data-root", str(root), "--project-id", fixture.PROJECT_ID, "--writer-id", "codex-module-central", "record-return", "--dispatch-id", "dispatch-c10-001", "--sub-agent-id", "agent-c10-001", "--return-file", str(bad)])
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "C10_SUB_AGENT_CANNOT_DECLARE_DONE")

    def test_parent_can_aggregate_only_after_all_agents_return(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root); specs = [agent_spec("agent-c10-001", role="A"), agent_spec("agent-c10-002", role="B")]; prepare(root, dispatch_request(specs)); confirm(root, confirmation(agents=[{"subAgentId": "agent-c10-001", "status": "CREATED", "runtimeAgentRef": "runtime-agent-c10-001"}, {"subAgentId": "agent-c10-002", "status": "CREATED", "runtimeAgentRef": "runtime-agent-c10-002"}]))
            results = []
            for agent in ("agent-c10-001", "agent-c10-002"):
                path = write(root / "c10-inputs" / f"return-{agent}.json", return_payload(agent)); results.append(invoke(C10, ["--data-root", str(root), "--project-id", fixture.PROJECT_ID, "--writer-id", "codex-module-central", "record-return", "--dispatch-id", "dispatch-c10-001", "--sub-agent-id", agent, "--return-file", str(path)])[1])
            self.assertFalse(results[0]["parentCompletionAllowed"]); self.assertFalse(results[1]["parentCompletionAllowed"]); self.assertTrue(results[1]["allSubAgentsReturned"]); self.assertFalse(results[1]["taskDoneDeclared"])

    def test_parent_quality_gate_requires_all_returns_and_a_structured_consolidation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root); specs = [agent_spec("agent-c10-001", role="A")]
            prepare(root, dispatch_request(specs)); confirm(root, confirmation(agents=[{"subAgentId": "agent-c10-001", "status": "CREATED", "runtimeAgentRef": "runtime-agent-c10-001"}]))
            code, output = record_quality_review(root, quality_review(["agent-c10-001"]))
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "C10_PARENT_QUALITY_GATE_REQUIRES_ALL_SUB_AGENT_RETURNS")
            path = write(root / "c10-inputs" / "return.json", return_payload("agent-c10-001"))
            self.assertEqual(invoke(C10, ["--data-root", str(root), "--project-id", fixture.PROJECT_ID, "--writer-id", "codex-module-central", "record-return", "--dispatch-id", "dispatch-c10-001", "--sub-agent-id", "agent-c10-001", "--return-file", str(path)])[0], 0)
            code, output = record_quality_review(root, quality_review(["agent-c10-001"]))
            self.assertEqual(code, 0, output); self.assertEqual(output["status"], "PARENT_QUALITY_GATE_RECORDED"); self.assertTrue(output["parentMayRequestC06"])

    def test_duplicate_return_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root); specs = [agent_spec("agent-c10-001", role="A")]; prepare(root, dispatch_request(specs)); confirm(root, confirmation(agents=[{"subAgentId": "agent-c10-001", "status": "CREATED", "runtimeAgentRef": "runtime-agent-c10-001"}]))
            path = write(root / "c10-inputs" / "return.json", return_payload("agent-c10-001")); args = ["--data-root", str(root), "--project-id", fixture.PROJECT_ID, "--writer-id", "codex-module-central", "record-return", "--dispatch-id", "dispatch-c10-001", "--sub-agent-id", "agent-c10-001", "--return-file", str(path)]
            self.assertEqual(invoke(C10, args)[0], 0); code, second = invoke(C10, args); self.assertEqual(second["status"], "IDEMPOTENT_SUB_AGENT_RETURN")

    def test_manual_fallback_is_copyable_and_does_not_advance_task(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root); self.assertEqual(prepare(root, dispatch_request())[0], 0)
            ledger_path = root / "module-ledgers" / fixture.PROJECT_ID / "ledger.json"; before = ledger_path.read_bytes()
            code, output = export_fallback(root)
            self.assertEqual(code, 0, output); self.assertEqual(output["status"], "READY_FOR_MANUAL_COPY"); self.assertFalse(output["dispatchPerformed"]); self.assertEqual(before, ledger_path.read_bytes())
            artifact = json.loads(Path(output["artifact"]).read_text(encoding="utf-8"))
            self.assertIn("Codex 保存项目", artifact["copyablePrompt"]); self.assertEqual(artifact["runtimeTarget"]["type"], "project"); self.assertFalse(artifact["boundary"]["ledgerUpdated"])
            self.assertIn("5.6 Terra", artifact["copyablePrompt"]); self.assertTrue(artifact["modelEnforcement"]["evidenceRequiredBeforeC10Confirm"])
            self.assertFalse(artifact["permissionEnforcement"]["dangerFullAccessForbidden"]); self.assertIn("完全访问", artifact["copyablePrompt"])
            code, second = export_fallback(root); self.assertEqual(code, 0); self.assertEqual(second["status"], "IDEMPOTENT_MANUAL_FALLBACK_PACKAGE")

    def test_manual_fallback_refuses_after_runtime_confirmation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root); prepare(root, dispatch_request()); confirm(root, confirmation())
            code, output = export_fallback(root)
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "C10_RUNTIME_ALREADY_CONFIRMED")

    def test_manual_fallback_uses_direct_project_and_selected_model(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            setup_ready(root)
            request = dispatch_request()
            request["model"] = "gpt-5.6-sol"
            code, prepared = prepare(root, request)
            self.assertEqual(0, code, prepared)
            code, output = export_fallback(root)
            self.assertEqual(0, code, output)
            package = json.loads(Path(output["artifact"]).read_text())
            self.assertEqual("DIRECT", package["projectAssociationProtocol"]["requiredMethod"])
            self.assertNotIn("先在派发单指定的保存项目 local", package["copyablePrompt"])
            self.assertTrue(package["boundary"]["manualModelEvidenceRequired"])
            self.assertNotIn("manualTerraEvidenceRequired", package["boundary"])
            runtime = confirmation()
            runtime["taskWindow"]["associationMethod"] = package["projectAssociationProtocol"]["requiredMethod"]
            runtime["taskWindow"]["modelControl"] = model_control(
                package["modelEnforcement"]["allowedManualMethod"], "manual-model-proof", package["model"])
            runtime["taskWindow"]["permissionControl"] = permission_control(
                "manual-permission-proof", profile="full-access", method="MANUAL_UI_PERMISSION_EVIDENCE")
            code, confirmed = confirm(root, runtime)
            self.assertEqual(0, code, confirmed)

    def test_legacy_manual_export_is_preserved_but_not_reissued(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            setup_ready(root)
            self.assertEqual(0, prepare(root, dispatch_request())[0])
            code, output = export_fallback(root)
            self.assertEqual(0, code, output)
            current = Path(output["artifact"])
            legacy = current.parent / "manual-task-package.json"
            value = json.loads(current.read_text())
            value["copyablePrompt"] = "Fictional legacy local-bootstrap instructions"
            current.unlink()
            write(legacy, value)
            before = legacy.read_bytes()
            code, refreshed = export_fallback(root)
            self.assertEqual(0, code, refreshed)
            self.assertNotEqual(legacy, Path(refreshed["artifact"]))
            self.assertEqual(before, legacy.read_bytes())
            self.assertNotEqual(value["copyablePrompt"], json.loads(Path(refreshed["artifact"]).read_text())["copyablePrompt"])

    def test_legacy_append_without_permission_class_keeps_scoped_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            setup_ready(root)
            self.assertEqual(0, prepare(root, dispatch_request())[0])
            self.assertEqual(0, confirm(root, confirmation())[0])
            self.assertEqual(0, prepare_append(root, append_request([agent_spec("agent-legacy")]))[0])
            path = dispatch_module.append_plan_path(root, fixture.PROJECT_ID,
                "dispatch-c10-001", "append-c10-001")
            plan = json.loads(path.read_text())
            plan.pop("parentPermissionClass")
            write(path, plan)  # Fixture for the immutable pre-upgrade format.
            before = path.read_bytes()
            value = append_confirmation([{"subAgentId": "agent-legacy", "status": "CREATED",
                "runtimeAgentRef": "runtime-agent-legacy"}])
            invalid = copy.deepcopy(value)
            invalid["subAgents"][0]["permissionControl"] = permission_control(
                "thread-c10-001", profile="full-access", method="INHERITED_FROM_PARENT_WINDOW")
            code, refused = confirm_append(root, invalid)
            self.assertNotEqual(0, code, refused)
            self.assertEqual("C10_SUB_AGENT_PERMISSION_INHERITANCE_MISMATCH", refused["reason"])
            code, result = confirm_append(root, value)
            self.assertEqual(0, code, result)
            self.assertEqual(before, path.read_bytes())


if __name__ == "__main__": unittest.main(verbosity=2)
