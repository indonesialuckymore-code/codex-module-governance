#!/usr/bin/env python3
"""C10 regression tests with fictional temporary governance data."""

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


TERRA = "gpt-5.6-terra"


def model_control(method, evidence_ref, model=TERRA):
    return {"model": model, "method": method, "evidenceRef": evidence_ref}


def invoke(script, arguments):
    result = subprocess.run([sys.executable, str(script), *arguments], check=False, capture_output=True, text=True)
    return result.returncode, json.loads(result.stdout)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"); return path


def c03(root, command, *args):
    return invoke(C03, ["--data-root", str(root), "--project-id", fixture.PROJECT_ID, "--writer-id", "codex-module-central", command, *args])


def setup_ready(root, requests=None, window_mode="AUTO", window_id=None):
    fixture.setup_project(root)
    review = fixture.create_review(root, requests=requests, window_mode=window_mode, window_id=window_id)
    code, output = invoke(C05, ["--data-root", str(root), "--project-id", fixture.PROJECT_ID, "--writer-id", "codex-module-central", "evaluate", "--package-id", fixture.PACKAGE_ID, "--review", str(review), "--apply"])
    if code != 0: raise AssertionError(output)


def dispatch_request(agents=None, approved=True, dispatch_id="dispatch-c10-001"):
    return {
        "dispatchSchemaVersion": "0.16.0", "recordType": "C10_DISPATCH_REQUEST", "dispatchId": dispatch_id,
        "projectId": fixture.PROJECT_ID, "packageId": fixture.PACKAGE_ID, "reviewId": fixture.REVIEW_ID, "taskId": fixture.TASK_ID,
        "runtimeProject": {"codexProjectId": "codex-project-001", "projectPath": "/tmp/fictional-project", "isGitRepository": True, "environment": "WORKTREE"},
        "bossDispatchAuthorization": {
            "status": "APPROVED" if approved else "PENDING",
            "reference": "boss-dispatch-c10",
            "scope": {"type": "EXECUTION_MAP", "scopeId": "central-plan-001", "scopeDigest": "a" * 64, "waveId": "wave-01", "taskIds": [fixture.TASK_ID]},
        },
        "subAgents": agents or [],
    }


def prepare(root, payload):
    path = write(root / "c10-inputs" / "dispatch.json", payload)
    return invoke(C10, ["--data-root", str(root), "--project-id", fixture.PROJECT_ID, "--writer-id", "codex-module-central", "prepare", "--package-id", fixture.PACKAGE_ID, "--review-id", fixture.REVIEW_ID, "--request", str(path)])


def confirmation(window_id="window-c10-001", reused=False, agents=None, project_id="codex-project-001", cwd="/tmp/.codex/worktrees/abcd/fictional-project", environment="WORKTREE", association_method="DIRECT", handoff_refs=None, runtime_ref="thread-c10-001", runtime_title="C-05｜Fictional C-05｜G1", generation=1, model=TERRA, model_method=None):
    window_method = model_method or ("NATIVE_SEND_MESSAGE_MODEL_OVERRIDE" if reused else "NATIVE_CREATE_THREAD_MODEL_PARAMETER")
    normalized_agents = []
    for agent in agents or []:
        normalized_agents.append({**agent, "modelControl": agent.get("modelControl", model_control("NATIVE_SPAWN_AGENT_MODEL_PARAMETER", agent["runtimeAgentRef"]))})
    return {"confirmationSchemaVersion": "0.16.0", "recordType": "C10_RUNTIME_CONFIRMATION", "dispatchId": "dispatch-c10-001", "taskWindow": {"status": "REUSED" if reused else "CREATED", "taskId": fixture.TASK_ID, "runtimeTitle": runtime_title, "generation": generation, "windowId": window_id, "runtimeThreadRef": runtime_ref, "runtimeProjectId": project_id, "runtimeCwd": cwd, "environmentType": environment, "associationMethod": association_method, "associationHandoffRefs": handoff_refs or [], "modelControl": model_control(window_method, runtime_ref, model)}, "subAgents": normalized_agents}


def confirm(root, value):
    path = write(root / "c10-inputs" / "confirmation.json", value)
    return invoke(C10, ["--data-root", str(root), "--project-id", fixture.PROJECT_ID, "--writer-id", "codex-module-central", "confirm", "--dispatch-id", "dispatch-c10-001", "--confirmation", str(path)])


def export_fallback(root):
    return invoke(C10, ["--data-root", str(root), "--project-id", fixture.PROJECT_ID, "--writer-id", "codex-module-central", "export-fallback", "--dispatch-id", "dispatch-c10-001"])


def append_request(agents, append_id="append-c10-001", shared_write_risk=False):
    return {
        "appendSchemaVersion": "0.16.0", "recordType": "C10_SUB_AGENT_APPEND_REQUEST", "appendId": append_id,
        "dispatchId": "dispatch-c10-001", "projectId": fixture.PROJECT_ID, "taskId": fixture.TASK_ID, "windowId": "window-c10-001",
        "trigger": {"phase": "DISCOVERY", "delegationReason": "INDEPENDENT_SCOPE", "withinApprovedScope": True, "sharedWriteRisk": shared_write_risk, "summary": "Materials reveal an independent evidence range."},
        "subAgents": agents,
    }


def prepare_append(root, payload):
    path = write(root / "c10-inputs" / "append.json", payload)
    return invoke(C10, ["--data-root", str(root), "--project-id", fixture.PROJECT_ID, "--writer-id", "codex-module-central", "prepare-append", "--dispatch-id", "dispatch-c10-001", "--append-request", str(path)])


def append_confirmation(agents, append_id="append-c10-001"):
    normalized_agents = [{**agent, "modelControl": agent.get("modelControl", model_control("NATIVE_SPAWN_AGENT_MODEL_PARAMETER", agent["runtimeAgentRef"]))} for agent in agents]
    return {"appendConfirmationSchemaVersion": "0.16.0", "recordType": "C10_SUB_AGENT_APPEND_RUNTIME_CONFIRMATION", "appendId": append_id, "dispatchId": "dispatch-c10-001", "windowId": "window-c10-001", "subAgents": normalized_agents}


def confirm_append(root, payload, append_id="append-c10-001"):
    path = write(root / "c10-inputs" / "append-confirmation.json", payload)
    return invoke(C10, ["--data-root", str(root), "--project-id", fixture.PROJECT_ID, "--writer-id", "codex-module-central", "confirm-append", "--dispatch-id", "dispatch-c10-001", "--append-id", append_id, "--confirmation", str(path)])


def return_payload(agent_id, status="NEEDS_REVIEW", return_id=None):
    return {"returnSchemaVersion": "0.16.0", "recordType": "C10_SUB_AGENT_RETURN", "dispatchId": "dispatch-c10-001", "subAgentId": agent_id, "returnId": return_id or f"return-{agent_id}", "submittedToWindowId": "window-c10-001", "status": status, "scope": "Fictional independent scope.", "confirmedFacts": ["Fictional fact confirmed."], "completedWork": ["Fictional work completed."], "evidenceRefs": [f"evidence-{agent_id}"], "unresolvedRefs": [], "scopeDeviation": [], "risksAndConflicts": [], "recommendedParentAction": "Read the fictional evidence before consolidation."}


def agent_spec(agent_id, role="Fictional helper", reason="INDEPENDENT_SCOPE", mode="READ_ONLY", scope="Read an independent fictional scope."):
    return {"subAgentId": agent_id, "role": role, "level": 1, "delegationReason": reason, "executionMode": mode, "scope": scope}


def quality_review(agents):
    return {
        "qualityReviewSchemaVersion": "0.16.0", "recordType": "C10_PARENT_QUALITY_REVIEW", "qualityReviewId": "quality-c10-001",
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
    def test_green_read_only_parallel_plan(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root, [{"objectKey": "service:shared", "conflictKey": "service:shared", "resourceClass": "SERVICE", "intent": "READ", "exclusive": False}])
            code, output = prepare(root, dispatch_request())
            self.assertEqual(code, 0, output); self.assertEqual(output["trafficLight"], "GREEN"); self.assertFalse(output["dispatchPerformed"])
            self.assertEqual(output["runtimeTarget"], {"type": "project", "projectId": "codex-project-001", "environment": {"type": "worktree"}})
            self.assertEqual(output["projectAssociationProtocol"]["onMissingProjectId"], "HANDOFF_TO_PROJECT_LOCAL_THEN_RETURN")
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

    def test_sol_task_window_confirmation_is_refused_without_advancing_task(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root); self.assertEqual(prepare(root, dispatch_request())[0], 0)
            ledger_path = root / "module-ledgers" / fixture.PROJECT_ID / "ledger.json"; before = ledger_path.read_bytes()
            code, output = confirm(root, confirmation(model="gpt-5.6-sol"))
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "C10_RUNTIME_MODEL_MUST_BE_TERRA"); self.assertEqual(before, ledger_path.read_bytes())

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

    def test_sol_sub_agent_confirmation_is_refused_without_advancing_task(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root); specs = [agent_spec("agent-c10-001", role="Readback")]
            self.assertEqual(prepare(root, dispatch_request(specs))[0], 0)
            agent = {"subAgentId": "agent-c10-001", "status": "CREATED", "runtimeAgentRef": "runtime-agent-c10-001", "modelControl": model_control("NATIVE_SPAWN_AGENT_MODEL_PARAMETER", "runtime-agent-c10-001", "gpt-5.6-sol")}
            code, output = confirm(root, confirmation(agents=[agent]))
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "C10_RUNTIME_MODEL_MUST_BE_TERRA")

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

    def test_local_handoff_roundtrip_repairs_project_association(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root); self.assertEqual(prepare(root, dispatch_request())[0], 0)
            value = confirmation(association_method="LOCAL_HANDOFF_ROUNDTRIP", handoff_refs=["thread-initial", "thread-project-local"], runtime_ref="thread-final-worktree")
            code, output = confirm(root, value)
            self.assertEqual(code, 0, output); self.assertEqual(output["associationMethod"], "LOCAL_HANDOFF_ROUNDTRIP")
            ledger = json.loads((root / "module-ledgers" / fixture.PROJECT_ID / "ledger.json").read_text())
            self.assertEqual(ledger["windows"]["window-c10-001"]["associationMethod"], "LOCAL_HANDOFF_ROUNDTRIP")

    def test_handoff_repair_requires_two_prior_thread_refs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root); self.assertEqual(prepare(root, dispatch_request())[0], 0)
            ledger_path = root / "module-ledgers" / fixture.PROJECT_ID / "ledger.json"; before = ledger_path.read_bytes()
            value = confirmation(association_method="LOCAL_HANDOFF_ROUNDTRIP", handoff_refs=["thread-initial"])
            code, output = confirm(root, value)
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "C10_PROJECT_ASSOCIATION_REPAIR_EVIDENCE_INVALID"); self.assertEqual(before, ledger_path.read_bytes())

    def test_direct_association_refuses_false_handoff_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root); self.assertEqual(prepare(root, dispatch_request())[0], 0)
            code, output = confirm(root, confirmation(handoff_refs=["thread-not-used"]))
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
            root = Path(temp) / "private"; fixture.setup_project(root); code, _ = c03(root, "register-window", "--window-id", "window-existing", "--task-id", fixture.TASK_ID, "--context-mode", "NEW", "--runtime-model-evidence-ref", "manual-terra-window-existing"); self.assertEqual(code, 0)
            review = fixture.create_review(root); code, _ = invoke(C05, ["--data-root", str(root), "--project-id", fixture.PROJECT_ID, "--writer-id", "codex-module-central", "evaluate", "--package-id", fixture.PACKAGE_ID, "--review", str(review), "--apply"]); self.assertEqual(code, 0)
            prepare(root, dispatch_request()); code, output = confirm(root, confirmation(window_id="window-existing", reused=True))
            self.assertEqual(code, 0, output); self.assertEqual(output["windowId"], "window-existing")

    def test_freeze_blocks_prepare(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root); incident = recovery_fixture.incident(kind="CENTRAL", target="codex-module-central", task=None); incident["projectId"] = fixture.PROJECT_ID; incident["caseId"] = "recovery-c10-001"
            path = write(root / "c08-inputs" / "incident.json", incident); code, frozen = invoke(C08, ["--data-root", str(root), "--project-id", fixture.PROJECT_ID, "--writer-id", "codex-module-central", "freeze", "--incident", str(path), "--apply"]); self.assertEqual(code, 0, frozen)
            code, output = prepare(root, dispatch_request()); self.assertEqual(code, 2); self.assertEqual(output["reason"], "C10_RECOVERY_STATE_BLOCKS_DISPATCH")

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

    def test_append_refuses_non_terra_sub_agent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root); self.assertEqual(prepare(root, dispatch_request())[0], 0); self.assertEqual(confirm(root, confirmation())[0], 0)
            payload = append_request([agent_spec("agent-c10-append-001")]); self.assertEqual(prepare_append(root, payload)[0], 0)
            agent = {"subAgentId": "agent-c10-append-001", "status": "CREATED", "runtimeAgentRef": "runtime-agent-append-001", "modelControl": model_control("NATIVE_SPAWN_AGENT_MODEL_PARAMETER", "runtime-agent-append-001", "gpt-5.6-sol")}
            code, output = confirm_append(root, append_confirmation([agent]))
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "C10_RUNTIME_MODEL_MUST_BE_TERRA")

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
            code, second = export_fallback(root); self.assertEqual(code, 0); self.assertEqual(second["status"], "IDEMPOTENT_MANUAL_FALLBACK_PACKAGE")

    def test_manual_fallback_refuses_after_runtime_confirmation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup_ready(root); prepare(root, dispatch_request()); confirm(root, confirmation())
            code, output = export_fallback(root)
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "C10_RUNTIME_ALREADY_CONFIRMED")


if __name__ == "__main__": unittest.main(verbosity=2)
