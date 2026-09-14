#!/usr/bin/env python3
"""Regression tests for C06 using only temporary fictional data."""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import ledger_manager
import independent_handover_validator as validator


SCRIPTS = Path(__file__).parent
C02 = SCRIPTS / "initialize_project.py"
C03 = SCRIPTS / "ledger_manager.py"
C04 = SCRIPTS / "task_package_generator.py"
C05 = SCRIPTS / "occupancy_conflict_checker.py"
C06 = SCRIPTS / "independent_handover_validator.py"
C08C = SCRIPTS / "role_continuity_controller.py"
C09 = SCRIPTS / "central_construction_controller.py"
C10 = SCRIPTS / "task_window_dispatch_controller.py"
PROJECT = "c06-demo-project"
TASK = "C-06"
PACKAGE = "c06-package-001"
WINDOW = "window-c06-001"
SIGNAL = "completion-signal-001"
VALIDATION = "validation-c06-001"


def invoke(script, arguments):
    if script == C06 and "--caller-thread-ref" not in arguments:
        command_index = next(index for index, value in enumerate(arguments) if value in {"assess", "finalize", "verify"})
        arguments = [*arguments[:command_index], "--caller-thread-ref", "central-thread-g1", *arguments[command_index:]]
    if script == C10 and "--caller-thread-ref" not in arguments:
        command_index = next(index for index, value in enumerate(arguments) if value in {"prepare", "confirm", "export-fallback", "prepare-append", "confirm-append", "record-return", "record-parent-quality-review", "verify"})
        arguments = [*arguments[:command_index], "--caller-thread-ref", "central-thread-g1", *arguments[command_index:]]
    completed = subprocess.run([sys.executable, str(script), *arguments], check=False, capture_output=True, text=True)
    return completed.returncode, json.loads(completed.stdout)


def c03(root, command, *arguments):
    return invoke(C03, ["--data-root", str(root), "--project-id", PROJECT, "--writer-id", "codex-module-central", "--caller-thread-ref", "central-thread-g1", command, *arguments])


def c08(root, command, *arguments, writer=False):
    base = ["--data-root", str(root), "--project-id", PROJECT]
    if writer:
        base.extend(["--writer-id", "codex-module-central"])
    return invoke(C08C, [*base, command, *arguments])


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def setup_ready_for_validation(root, brief_mutation=None, occupancy_intent="WRITE", outline_handoff=None):
    code, output = invoke(C02, [
        "--data-root", str(root), "--project-id", PROJECT, "--display-name", "C06 Fictional Validation",
        "--scope-summary", "Isolated fictional handback validation only.", "--apply",
    ])
    if code != 0:
        raise AssertionError(output)
    commands = [["initialize", "--apply"]]
    extra = []
    if outline_handoff:
        import hashlib
        value = json.loads(json.dumps(outline_handoff)); value['projectId'] = PROJECT
        outline = root / 'outline.md'; outline.write_text('Fictional approved business outcome')
        value['outline']['sha256'] = hashlib.sha256(outline.read_bytes()).hexdigest()
        path = write_json(root / 'outline-handoff.json', value)
        commands.append(['import-outline', '--handoff', str(path), '--outline-file', str(outline), '--boss-approval-ref', value['bossApproval']['reference']])
        extra = ['--outcome-id', value['outcomes'][0]['outcomeId']]
    commands.append(["add-task", "--task-id", TASK, "--title", "Fictional validation task", "--business-goal", "Validate C06 gates.", "--plan-ref", "fable-plan-006", *extra])
    for command in commands:
        code, output = c03(root, command[0], *command[1:])
        if code != 0:
            raise AssertionError(output)
    brief = {
        "briefSchemaVersion": "0.4.0", "recordType": "C04_TASK_PACKAGE_BRIEF", "taskId": TASK,
        "windowRecommendation": {"mode": "NEW", "reason": "Use an isolated fictional window."},
        "dependencies": ["fable-plan-006"],
        "requiredReading": [{"reference": "fictional-governance-006", "purpose": "Read fictional scope."}],
        "realTimeChecks": [{"reference": "fictional-target-006", "purpose": "Read fictional state.", "objectType": "file"}],
        "allowedActions": ["Use fictional evidence only."], "forbiddenActions": ["Do not write business data."],
        "preflightSnapshot": ["Record fictional before snapshot."], "executionSequence": ["Review before acting."],
        "acceptance": {
            "positiveCases": ["Positive evidence passes."], "negativeCases": ["Negative evidence passes."],
            "idempotencyChecks": ["Repeat has no duplicate."], "rollbackChecks": ["Rollback is executable."],
            "logAndHistoryChecks": ["History is readable."], "readbackChecks": ["Upstream and downstream read back."],
        },
        "hardStops": ["Stop on unknown writer."], "rollbackPlan": ["Use fictional snapshot."],
        "deliverables": ["Return fictional handback."], "handbackRule": "Boss authorizes handback before central review.",
    }
    if brief_mutation:
        brief_mutation(brief)
    brief_path = write_json(root / "briefs" / f"{TASK}.json", brief)
    code, output = invoke(C04, [
        "--data-root", str(root), "--project-id", PROJECT, "--writer-id", "codex-module-central",
        "generate", "--package-id", PACKAGE, "--task-id", TASK, "--brief", str(brief_path), "--apply",
    ])
    if code != 0:
        raise AssertionError(output)
    occupancy = {
        "reviewSchemaVersion": "0.5.0", "recordType": "C05_OCCUPANCY_REVIEW_REQUEST",
        "reviewId": "c05-review-006", "projectId": PROJECT, "packageId": PACKAGE, "taskId": TASK,
        "bossReview": {"status": "APPROVED", "reference": "boss-package-review-006"},
        "dependencyGate": {"status": "SATISFIED", "references": ["dependency-proof-006"]},
        "crossModuleGate": {"status": "NOT_APPLICABLE", "reference": "fable-handoff-006"},
        "windowReview": {"mode": "AUTO", "candidateWindowId": None},
        "occupancyRequests": [{
            "objectKey": "file:fictional-c06", "conflictKey": "file:fictional-c06",
            "resourceClass": "FILE", "intent": occupancy_intent, "exclusive": False,
        }],
    }
    occupancy_path = write_json(root / "occupancy-inputs" / "c05-review-006.json", occupancy)
    code, output = invoke(C05, [
        "--data-root", str(root), "--project-id", PROJECT, "--writer-id", "codex-module-central",
        "evaluate", "--package-id", PACKAGE, "--review", str(occupancy_path), "--apply",
    ])
    if code != 0:
        raise AssertionError(output)
    for command in [
        ["register-window", "--window-id", WINDOW, "--task-id", TASK, "--context-mode", "NEW", "--runtime-model-evidence-ref", "manual-terra-window-c06", "--runtime-permission-profile", ":workspace", "--runtime-permission-evidence-ref", "manual-permission-window-c06", "--runtime-writable-root", "/tmp/.codex/worktrees/abcd/fictional-project", "--governance-data-root-access", "DENIED"],
        ["transition-task", "--task-id", TASK, "--to-status", "IN_PROGRESS", "--reason", "Fictional dispatch already occurred outside C06."],
    ]:
        code, output = c03(root, command[0], *command[1:])
        if code != 0:
            raise AssertionError(output)
    code, output = c08(root, "initialize", "--calling-thread-ref", "central-thread-g1", "--central-thread-ref", "central-thread-g1", "--runtime-project-id", "runtime-project-c06", "--execution-map-ref", "execution-map-c06", writer=True)
    if code != 0:
        raise AssertionError(output)


def evidence_refs():
    return [f"evidence-{category}" for category in (
        "before", "after", "positive", "negative", "idempotency", "rollback", "logs", "upstream", "downstream"
    )]


def create_handback(root, boss_status="APPROVED", unresolved=None, residual=None, ticket_id="return-ticket-c06-001"):
    handback = {
        "handbackSchemaVersion": "0.8.0", "recordType": "C06_TASK_WINDOW_HANDBACK",
        "handbackId": "handback-c06-001", "projectId": PROJECT, "packageId": PACKAGE,
        "returnTicketId": ticket_id, "routeRevisionSeen": 1, "c05ReviewId": "c05-review-006", "taskId": TASK, "windowId": WINDOW,
        "completionSignalId": SIGNAL, "submittedBy": {"type": "task-window", "id": WINDOW},
        "bossHandbackAuthorization": {"status": boss_status, "reference": "boss-handback-approval-006"},
        "executedScopeRefs": ["scope-executed-006"], "evidenceRefs": evidence_refs(),
        "testAndObjectRefs": ["test-object-ids-006"], "parentQualityReviewRefs": [], "unresolvedRefs": unresolved or [],
        "residualRiskRefs": residual or [],
    }
    return write_json(root / "handbacks" / "handback-c06-001.json", handback)


def create_review(root, mutation=None):
    refs = dict(zip(
        ("beforeSnapshot", "afterSnapshot", "positiveCase", "negativeCase", "idempotency", "rollback", "logsAndHistory", "upstreamReadback", "downstreamReadback"),
        evidence_refs(),
    ))
    refs["testAndObjectIds"] = "test-object-ids-006"
    review = {
        "reviewSchemaVersion": "0.8.0", "recordType": "C06_INDEPENDENT_VALIDATION_REVIEW",
        "validationId": VALIDATION, "handbackId": "handback-c06-001", "taskId": TASK,
        "centralReviewer": {"id": "codex-module-central", "independentReadbackPerformed": True},
        "scopeAssessment": {
            "packageScopeMatch": True, "parallelMechanismFound": False, "unknownWriterFound": False,
            "permissionExpansionFound": False, "unexplainedErrorFound": False, "duplicateDataFound": False,
            "blockingResidualRiskFound": False, "rollbackExecutable": True,
        },
        "evidenceAssessment": {
            category: {"reference": reference, "verdict": "PASS", "independentlyReadBack": True}
            for category, reference in refs.items()
        },
    }
    if mutation:
        mutation(review)
    return write_json(root / "validation-inputs" / f"{VALIDATION}.json", review)


def add_returned_sub_agent(root):
    before = ledger_manager.load_ledger(root, PROJECT)
    def mutate(after):
        after["subAgents"]["agent-c06-001"] = {
            "subAgentId": "agent-c06-001", "windowId": WINDOW, "taskId": TASK,
            "dispatchId": "dispatch-c06-001", "level": 1, "status": "RETURNED",
            "returnId": "return-c06-001", "runtimeAgentRef": "runtime-agent-c06-001",
        }
    ledger_manager.commit_mutation(root, PROJECT, before, "codex-module-central", "TEST_ADD_RETURNED_SUB_AGENT", {"taskId": TASK}, mutate, caller_thread_ref="central-thread-g1")


def write_parent_quality_review(root):
    payload = {
        "schemaVersion": "0.17.0", "recordType": "C10_PARENT_QUALITY_REVIEW", "qualityReviewId": "quality-c06-001",
        "dispatchId": "dispatch-c06-001", "projectId": PROJECT, "taskId": TASK, "windowId": WINDOW,
        "subAgentReturns": [{"subAgentId": "agent-c06-001", "returnId": "return-c06-001"}],
        "acceptanceCoverage": {key: [f"coverage-{key}-006"] for key in ("positiveCases", "negativeCases", "idempotencyChecks", "rollbackChecks", "logAndHistoryChecks", "readbackChecks")},
        "conflictResolutions": [{"subAgentId": "agent-c06-001", "outcome": "NO_CONFLICT", "resolutionRef": "resolution-c06-001"}],
        "parentReadbackEvidenceRefs": ["parent-readback-c06-001"], "scopeCheck": {"withinApprovedScope": True, "unexpectedWriteFound": False},
        "unifiedStatus": "NEEDS_REVIEW", "unresolvedRefs": [], "recommendedParentAction": "Send consolidated evidence to C06.",
        "boundary": {"parentQualityGatePassed": True, "parentMayRequestC06": True},
    }
    return write_json(root / "dispatches" / PROJECT / "dispatch-c06-001" / "parent-quality-review.json", payload)


def queue_and_admit_return(root, handback):
    code, output = c08(root, "submit-return", "--handback", str(handback))
    if code != 0:
        raise AssertionError(output)
    code, output = c08(root, "admit-return", "--return-ticket-id", output["returnTicketId"], "--current-thread-ref", "central-thread-g1", "--admission-ref", "central-return-admission-006", writer=True)
    if code != 0:
        raise AssertionError(output)
    return output


def correct_and_verify_direction(root):
    from test_correction_resume import complete_roundtrip, verification
    code, current = c03(root, "record-correction", "--task-id", TASK,
        "--correction-id", "direction-c06-verified", "--expected-revision", "0",
        "--instruction-ref", "new-direction", "--reason-ref", "focus-change",
        "--boss-decision-ref", "boss-direction", "--retain-ref", "original-scope", "--supersede-ref", "old-emphasis")
    if code: raise AssertionError(current)
    complete_roundtrip(root, current["messageRequests"][0], PROJECT, "central-thread-g1", WINDOW)
    report = verification(root, PROJECT, TASK, "central-thread-g1")
    code, output = c03(root, "verify-correction", "--task-id", TASK, "--verification", str(report))
    if code: raise AssertionError(output)
    return current["current"]


class IndependentHandoverValidatorTests(unittest.TestCase):
    def test_current_direction_handback_can_finish_through_c08_c06_and_boss(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_ready_for_validation(root)
            current = correct_and_verify_direction(root)
            handback = create_handback(root)
            value = json.loads(handback.read_text())
            value["directionContext"] = {"revision": current["revision"], "digest": current["requestDigest"]}
            write_json(handback, value)
            code, output = self.assess(root, handback, create_review(root), "--apply", "codex-module-central")
            self.assertEqual(code, 0, output)
            code, final = invoke(C06, ["--data-root", str(root), "--project-id", PROJECT,
                "--writer-id", "codex-module-central", "finalize", "--validation-id", VALIDATION,
                "--boss-decision", "APPROVED", "--boss-decision-ref", "new-direction-final-approval"])
            self.assertEqual(code, 0, final)
            self.assertTrue(final["doneRecorded"])
            code, retained = c03(root, "read-correction", "--task-id", TASK)
            self.assertEqual(code, 0, retained)
            self.assertEqual(retained["communicationSource"], "SEALED_VERIFICATION")
            self.assertFalse(retained["governanceHold"])

    def test_resumed_task_rejects_old_handback_without_current_direction(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_ready_for_validation(root)
            correct_and_verify_direction(root)
            code, output = self.assess(root, create_handback(root), create_review(root))
            self.assertEqual(code, 2, output)
            self.assertIn("TASK_DIRECTION_VERSION_MISMATCH", output["reason"])

    def test_resumption_does_not_revalidate_an_old_pass_decision(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_ready_for_validation(root)
            handback, review = create_handback(root), create_review(root)
            self.assertEqual(self.assess(root, handback, review, "--apply", "codex-module-central")[0], 0)
            correct_and_verify_direction(root)
            code, output = invoke(C06, ["--data-root", str(root), "--project-id", PROJECT,
                "--writer-id", "codex-module-central", "finalize", "--validation-id", VALIDATION,
                "--boss-decision", "APPROVED", "--boss-decision-ref", "old-decision-must-not-finish"])
            self.assertEqual(code, 2, output)
            self.assertIn("TASK_DIRECTION_VERSION_MISMATCH", output["reason"])

    def test_correction_after_pass_blocks_old_final_approval(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_ready_for_validation(root)
            handback, review = create_handback(root), create_review(root)
            self.assertEqual(self.assess(root, handback, review, "--apply", "codex-module-central")[0], 0)
            self.assertEqual(c03(root, "record-correction", "--task-id", TASK,
                "--correction-id", "direction-after-pass", "--expected-revision", "0",
                "--instruction-ref", "new-direction", "--reason-ref", "boss-focus-change",
                "--boss-decision-ref", "boss-correction", "--retain-ref", "original-scope",
                "--supersede-ref", "old-emphasis")[0], 0)
            code, final = invoke(C06, ["--data-root", str(root), "--project-id", PROJECT,
                "--writer-id", "codex-module-central", "finalize", "--validation-id", VALIDATION,
                "--boss-decision", "APPROVED", "--boss-decision-ref", "old-final-approval"])
            self.assertEqual(code, 2, final)
            self.assertIn("TASK_DIRECTION_CORRECTION_PENDING", final["reason"])

    def test_direction_correction_blocks_assessment_of_late_old_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_ready_for_validation(root)
            handback, review = create_handback(root), create_review(root)
            code, recorded = c03(root, "record-correction", "--task-id", TASK,
                "--correction-id", "direction-c06", "--expected-revision", "0",
                "--instruction-ref", "new-direction", "--reason-ref", "boss-focus-change",
                "--boss-decision-ref", "boss-correction", "--retain-ref", "original-scope",
                "--supersede-ref", "old-emphasis")
            self.assertEqual(code, 0, recorded)
            code, output = self.assess(root, handback, review)
            self.assertEqual(code, 2, output)
            self.assertIn("TASK_DIRECTION_CORRECTION_PENDING", output["reason"])

    def test_completion_signal_reissue_requires_linked_aborted_predecessor(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            tickets = root / "return-inbox" / PROJECT / "tickets"
            current_id = "return-c06-reissue-002"
            prior_id = "return-c06-reissue-001"
            current_handback = {
                "returnTicketId": current_id, "taskId": TASK, "windowId": WINDOW,
                "handbackId": "handback-c06-reissue-002", "completionSignalId": "completion-c06-reissue-002",
            }
            current_submission = {
                **current_handback, "recordType": "C08_RETURN_SUBMISSION", "projectId": PROJECT,
            }
            current_digest = ledger_manager.canonical_digest(current_submission)
            current_reservation = {
                "returnTicketId": current_id, "taskId": TASK,
                "handbackId": current_handback["handbackId"], "submissionDigest": current_digest,
                "validatorThreadRef": "validator-c06-sol",
            }
            current_root = tickets / current_id
            write_json(current_root / "submission.json", current_submission)
            write_json(current_root / "receipt-000000-submission.json", {
                "submission": current_submission, "submissionDigest": current_digest,
            })
            write_json(current_root / "receipt-000002-admission.json", {
                "returnTicketId": current_id, "taskId": TASK,
                "completionSignalId": current_handback["completionSignalId"], "ledgerReceiptId": None,
            })
            write_json(current_root / "receipt-000003-validation-reservation.json", current_reservation)

            prior_submission = {
                "returnTicketId": prior_id, "taskId": TASK, "windowId": WINDOW,
                "handbackId": "handback-c06-reissue-001", "completionSignalId": "completion-c06-reissue-001",
            }
            prior_digest = ledger_manager.canonical_digest(prior_submission)
            prior_reservation = {
                "returnTicketId": prior_id, "taskId": TASK,
                "handbackId": prior_submission["handbackId"], "submissionDigest": prior_digest,
                "validatorThreadRef": "validator-c06-sol",
            }
            prior_root = tickets / prior_id
            write_json(prior_root / "submission.json", prior_submission)
            write_json(prior_root / "receipt-000000-submission.json", {
                "submission": prior_submission, "submissionDigest": prior_digest,
            })
            write_json(prior_root / "receipt-000002-admission.json", {
                "returnTicketId": prior_id, "taskId": TASK,
                "completionSignalId": prior_submission["completionSignalId"], "ledgerReceiptId": "receipt-ledger-001",
            })
            write_json(prior_root / "receipt-000003-validation-reservation.json", prior_reservation)
            abort_path = write_json(prior_root / "receipt-000004-validation-aborted.json", {
                "recordType": "C08_RETURN_VALIDATION_ABORT", "returnTicketId": prior_id,
                "taskId": TASK, "handbackId": prior_submission["handbackId"],
                "validatorThreadRef": "validator-c06-sol",
                "reservationDigest": ledger_manager.canonical_digest(prior_reservation),
            })
            task = {"completionSignalIds": [prior_submission["completionSignalId"]]}
            self.assertTrue(validator.valid_completion_signal_reissue_after_abort(root, PROJECT, task, current_handback))
            abort = json.loads(abort_path.read_text(encoding="utf-8"))
            abort["reservationDigest"] = "0" * 64
            write_json(abort_path, abort)
            self.assertFalse(validator.valid_completion_signal_reissue_after_abort(root, PROJECT, task, current_handback))

    def assess(self, root, handback, review, mode="--dry-run", writer=None):
        queue_and_admit_return(root, handback)
        arguments = ["--data-root", str(root), "--project-id", PROJECT]
        if writer:
            arguments.extend(["--writer-id", writer])
        arguments.extend(["assess", "--package-id", PACKAGE, "--handback", str(handback), "--review", str(review), mode])
        return invoke(C06, arguments)

    def test_pass_assessment_stays_needs_review_until_boss_final_approval(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_ready_for_validation(root)
            handback, review = create_handback(root), create_review(root)
            code, preview = self.assess(root, handback, review)
            self.assertEqual(code, 0, preview)
            self.assertEqual(preview["status"], "PASS_PENDING_BOSS_APPROVAL")
            self.assertFalse(preview["doneRecorded"])
            code, output = self.assess(root, handback, review, "--apply", "codex-module-central")
            self.assertEqual(code, 0, output)
            self.assertEqual(output["taskStatusAfter"], "NEEDS_REVIEW")
            self.assertFalse(output["doneRecorded"])

    def test_c06_requires_parent_quality_gate_when_task_used_sub_agents(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"; setup_ready_for_validation(root); add_returned_sub_agent(root)
            handback, review = create_handback(root), create_review(root)
            code, output = self.assess(root, handback, review)
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "C06_PARENT_QUALITY_REVIEW_NOT_FOUND_OR_INVALID")
            write_parent_quality_review(root)
            data = json.loads(handback.read_text()); data["parentQualityReviewRefs"] = ["quality-c06-001"]; data["returnTicketId"] = "return-ticket-c06-002"; handback = write_json(handback, data)
            code, output = self.assess(root, handback, review)
            self.assertEqual(code, 0, output); self.assertEqual(output["status"], "PASS_PENDING_BOSS_APPROVAL")

    def test_only_boss_approved_pass_can_finalize_done(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_ready_for_validation(root)
            handback, review = create_handback(root), create_review(root)
            code, output = self.assess(root, handback, review, "--apply", "codex-module-central")
            self.assertEqual(code, 0, output)
            code, final = invoke(C06, [
                "--data-root", str(root), "--project-id", PROJECT, "--writer-id", "codex-module-central",
                "finalize", "--validation-id", VALIDATION, "--boss-decision", "APPROVED",
                "--boss-decision-ref", "boss-final-approval-006",
            ])
            self.assertEqual(code, 0, final)
            self.assertEqual(final["status"], "DONE")
            self.assertTrue(final["doneRecorded"])
            self.assertTrue(final["nextDispatchRequired"])
            self.assertFalse(final["bossRepromptRequired"])
            self.assertEqual(final["completedWindowStatus"], "AVAILABLE_FOR_REUSE")
            ledger = json.loads((root / "module-ledgers" / PROJECT / "ledger.json").read_text())
            self.assertEqual(ledger["tasks"][TASK]["status"], "DONE")
            self.assertEqual(ledger["windows"][WINDOW]["assignmentCount"], 1)
            self.assertIsNone(ledger["windows"][WINDOW]["currentTaskId"])
            self.assertIn("file:fictional-c06", ledger["objectOccupancies"])
            code, released = c03(
                root,
                "release-done-task-occupancy",
                "--task-id", TASK,
                "--validation-id", VALIDATION,
                "--boss-decision-ref", "boss-final-approval-006",
            )
            self.assertEqual(code, 0, released)
            self.assertTrue(released["occupancyReleased"])
            self.assertEqual(released["releasedClaimCount"], 1)
            ledger = json.loads((root / "module-ledgers" / PROJECT / "ledger.json").read_text())
            self.assertNotIn("file:fictional-c06", ledger["objectOccupancies"])
            self.assertEqual(ledger["releasedOccupancies"][0]["taskId"], TASK)
            code, repeated = c03(
                root,
                "release-done-task-occupancy",
                "--task-id", TASK,
                "--validation-id", VALIDATION,
                "--boss-decision-ref", "boss-final-approval-006",
            )
            self.assertEqual(code, 0, repeated)
            self.assertEqual(repeated["status"], "IDEMPOTENT_DONE_TASK_OCCUPANCY_ALREADY_RELEASED")

    def test_second_task_reuses_window_then_retires_it_and_emits_successor_trigger(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_ready_for_validation(root)
            handback, review = create_handback(root), create_review(root)
            self.assertEqual(self.assess(root, handback, review, "--apply", "codex-module-central")[0], 0)
            first_finalize = [
                "--data-root", str(root), "--project-id", PROJECT, "--writer-id", "codex-module-central",
                "finalize", "--validation-id", VALIDATION, "--boss-decision", "APPROVED",
                "--boss-decision-ref", "boss-final-approval-006",
            ]
            self.assertEqual(invoke(C06, first_finalize)[0], 0)

            second_task, second_package, second_review = "C-07", "c07-package-001", "c05-review-007"
            parallel_task, parallel_package, parallel_review = "C-08", "c08-package-001", "c05-review-008"
            blocked_task, blocked_package, blocked_review = "C-09", "c09-package-001", "c05-review-009"
            code, output = c03(root, "add-task", "--task-id", second_task, "--title", "Fictional second assignment", "--business-goal", "Prove the second and final window assignment.", "--plan-ref", "fable-plan-007")
            self.assertEqual(code, 0, output)
            code, output = c03(root, "add-task", "--task-id", parallel_task, "--title", "Fictional incompatible parallel task", "--business-goal", "Prove incompatible context opens a new Terra window.", "--plan-ref", "fable-plan-008")
            self.assertEqual(code, 0, output)
            code, output = c03(root, "add-task", "--task-id", blocked_task, "--title", "Fictional unresolved context task", "--business-goal", "Prove one blocked task does not stop independent successors.", "--plan-ref", "fable-plan-009")
            self.assertEqual(code, 0, output)
            brief = json.loads((root / "briefs" / f"{TASK}.json").read_text())
            brief["taskId"] = second_task
            brief["windowRecommendation"] = {"mode": "REUSE", "reason": "Reuse the completed fictional window once."}
            brief_path = write_json(root / "briefs" / f"{second_task}.json", brief)
            code, output = invoke(C04, [
                "--data-root", str(root), "--project-id", PROJECT, "--writer-id", "codex-module-central",
                "generate", "--package-id", second_package, "--task-id", second_task, "--brief", str(brief_path), "--apply",
            ])
            self.assertEqual(code, 0, output)
            blocked_brief = {**brief, "taskId": blocked_task, "windowRecommendation": {"mode": "REUSE", "reason": "Wait until context compatibility is known."}}
            blocked_brief_path = write_json(root / "briefs" / f"{blocked_task}.json", blocked_brief)
            code, output = invoke(C04, [
                "--data-root", str(root), "--project-id", PROJECT, "--writer-id", "codex-module-central",
                "generate", "--package-id", blocked_package, "--task-id", blocked_task, "--brief", str(blocked_brief_path), "--apply",
            ])
            self.assertEqual(code, 0, output)
            parallel_brief = {**brief, "taskId": parallel_task, "windowRecommendation": {"mode": "NEW", "reason": "The context is incompatible with the completed window."}}
            parallel_brief_path = write_json(root / "briefs" / f"{parallel_task}.json", parallel_brief)
            code, output = invoke(C04, [
                "--data-root", str(root), "--project-id", PROJECT, "--writer-id", "codex-module-central",
                "generate", "--package-id", parallel_package, "--task-id", parallel_task, "--brief", str(parallel_brief_path), "--apply",
            ])
            self.assertEqual(code, 0, output)
            occupancy = {
                "reviewSchemaVersion": "0.5.0", "recordType": "C05_OCCUPANCY_REVIEW_REQUEST",
                "reviewId": second_review, "projectId": PROJECT, "packageId": second_package, "taskId": second_task,
                "bossReview": {"status": "APPROVED", "reference": "boss-map-approval-007"},
                "dependencyGate": {"status": "SATISFIED", "references": ["dependency-proof-007"]},
                "crossModuleGate": {"status": "NOT_APPLICABLE", "reference": "fable-handoff-007"},
                "windowReview": {"mode": "REUSE", "candidateWindowId": WINDOW, "contextCompatibility": "COMPATIBLE", "compatibilityEvidenceRefs": ["window-context-proof-007"]},
                "occupancyRequests": [{"objectKey": "file:fictional-c07", "conflictKey": "file:fictional-c07", "resourceClass": "FILE", "intent": "WRITE", "exclusive": False}],
            }
            occupancy_path = write_json(root / "occupancy-inputs" / f"{second_review}.json", occupancy)
            parallel_occupancy = {
                **occupancy,
                "reviewId": parallel_review,
                "packageId": parallel_package,
                "taskId": parallel_task,
                "dependencyGate": {"status": "SATISFIED", "references": ["dependency-proof-008"]},
                "crossModuleGate": {"status": "NOT_APPLICABLE", "reference": "fable-handoff-008"},
                "windowReview": {"mode": "AUTO", "candidateWindowId": None, "contextCompatibility": "INCOMPATIBLE", "compatibilityEvidenceRefs": []},
                "occupancyRequests": [{"objectKey": "file:fictional-c08", "conflictKey": "file:fictional-c08", "resourceClass": "FILE", "intent": "WRITE", "exclusive": False}],
            }
            parallel_occupancy_path = write_json(root / "occupancy-inputs" / f"{parallel_review}.json", parallel_occupancy)
            blocked_occupancy = {
                **occupancy,
                "reviewId": blocked_review,
                "packageId": blocked_package,
                "taskId": blocked_task,
                "dependencyGate": {"status": "SATISFIED", "references": ["dependency-proof-009"]},
                "crossModuleGate": {"status": "UNKNOWN", "reference": "fable-handoff-009"},
                "windowReview": {"mode": "AUTO", "candidateWindowId": None, "contextCompatibility": "UNKNOWN", "compatibilityEvidenceRefs": []},
                "occupancyRequests": [{"objectKey": "file:fictional-c09", "conflictKey": "file:fictional-c09", "resourceClass": "FILE", "intent": "WRITE", "exclusive": False}],
            }
            blocked_occupancy_path = write_json(root / "occupancy-inputs" / f"{blocked_review}.json", blocked_occupancy)
            dispatch_id = "dispatch-c10-007"
            parallel_dispatch_id = "dispatch-c10-008"
            blocked_dispatch_id = "dispatch-c10-009"
            second_request_path = root / "c10-inputs" / "dispatch-007.json"
            parallel_request_path = root / "c10-inputs" / "dispatch-008.json"
            blocked_request_path = root / "c10-inputs" / "dispatch-009.json"
            execution_plan = {
                "tasks": [
                    {"taskId": blocked_task, "dependencies": [TASK], "packageId": blocked_package, "c05ReviewId": blocked_review, "c05ReviewPath": str(blocked_occupancy_path), "c10RequestPath": str(blocked_request_path)},
                    {"taskId": second_task, "dependencies": [TASK], "packageId": second_package, "c05ReviewId": second_review, "c05ReviewPath": str(occupancy_path), "c10RequestPath": str(second_request_path)},
                    {"taskId": parallel_task, "dependencies": [TASK], "packageId": parallel_package, "c05ReviewId": parallel_review, "c05ReviewPath": str(parallel_occupancy_path), "c10RequestPath": str(parallel_request_path)},
                ]
            }
            scope_digest = hashlib.sha256(json.dumps(execution_plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            authorization = {"status": "APPROVED", "reference": "boss-map-approval-007", "scope": {"type": "EXECUTION_MAP", "scopeId": "central-plan-007", "scopeDigest": scope_digest, "waveId": "wave-02", "taskIds": [blocked_task, second_task, parallel_task]}}
            request_path = write_json(second_request_path, {
                "dispatchSchemaVersion": "0.17.0", "recordType": "C10_DISPATCH_REQUEST", "dispatchId": dispatch_id,
                "projectId": PROJECT, "packageId": second_package, "reviewId": second_review, "taskId": second_task,
                "runtimeProject": {"codexProjectId": "codex-project-007", "projectPath": "/tmp/fictional-project", "isGitRepository": True, "environment": "WORKTREE"},
                "bossDispatchAuthorization": authorization, "subAgents": [],
            })
            write_json(blocked_request_path, {
                "dispatchSchemaVersion": "0.17.0", "recordType": "C10_DISPATCH_REQUEST", "dispatchId": blocked_dispatch_id,
                "projectId": PROJECT, "packageId": blocked_package, "reviewId": blocked_review, "taskId": blocked_task,
                "runtimeProject": {"codexProjectId": "codex-project-009", "projectPath": "/tmp/fictional-project", "isGitRepository": True, "environment": "WORKTREE"},
                "bossDispatchAuthorization": authorization, "subAgents": [],
            })
            write_json(parallel_request_path, {
                "dispatchSchemaVersion": "0.17.0", "recordType": "C10_DISPATCH_REQUEST", "dispatchId": parallel_dispatch_id,
                "projectId": PROJECT, "packageId": parallel_package, "reviewId": parallel_review, "taskId": parallel_task,
                "runtimeProject": {"codexProjectId": "codex-project-008", "projectPath": "/tmp/fictional-project", "isGitRepository": True, "environment": "WORKTREE"},
                "bossDispatchAuthorization": authorization, "subAgents": [],
            })
            execution_map_path = write_json(root / "c09-inputs" / "approved-execution-map-007.json", {
                "executionMapSchemaVersion": "0.17.0", "recordType": "C09_APPROVED_EXECUTION_MAP",
                "planId": "central-plan-007", "projectId": PROJECT,
                "authorization": {"scopeId": "central-plan-007", "scopeDigest": scope_digest, "bossApprovalRef": "boss-map-approval-007"},
                "executionPlan": execution_plan,
            })
            code, batch = invoke(C09, [
                "--data-root", str(root), "continue-successors", "--project-id", PROJECT,
                "--validation-id", VALIDATION, "--execution-map", str(execution_map_path), "--writer-id", "codex-module-central", "--caller-thread-ref", "central-thread-g1",
            ])
            self.assertEqual(code, 0, batch)
            self.assertEqual(batch["status"], "READY_FOR_BATCH_RUNTIME_DISPATCH")
            self.assertEqual(batch["preparedCount"], 2)
            self.assertEqual([item["taskId"] for item in batch["blockedTasks"]], [blocked_task])
            self.assertEqual(batch["blockedTasks"][0]["stage"], "C05_PREVIEW")
            prepared_by_task = {item["taskId"]: item for item in batch["preparedTasks"]}
            prepared = prepared_by_task[second_task]
            self.assertEqual(prepared["windowAction"]["action"], "SEND_TO_EXISTING_TASK")
            self.assertEqual(prepared["windowAction"]["assignmentNumber"], 2)
            self.assertEqual(prepared_by_task[parallel_task]["windowAction"]["action"], "CREATE_TASK")
            self.assertEqual(prepared_by_task[parallel_task]["windowAction"]["assignmentNumber"], 1)
            code, repeated = invoke(C09, [
                "--data-root", str(root), "continue-successors", "--project-id", PROJECT,
                "--validation-id", VALIDATION, "--execution-map", str(execution_map_path), "--writer-id", "codex-module-central", "--caller-thread-ref", "central-thread-g1",
            ])
            self.assertEqual(code, 0, repeated)
            self.assertEqual(repeated["status"], "IDEMPOTENT_SUCCESSOR_BATCH")
            self.assertTrue(repeated["recomputedFromLiveLedger"])
            self.assertEqual({item["taskId"] for item in repeated["previouslyPreparedTasks"]}, {second_task, parallel_task})
            code, correction = c03(root, "record-correction", "--task-id", parallel_task,
                "--correction-id", "direction-parallel", "--expected-revision", "0",
                "--instruction-ref", "new-instruction", "--reason-ref", "new-focus",
                "--boss-decision-ref", "boss-direction", "--retain-ref", "original-scope",
                "--supersede-ref", "old-emphasis")
            self.assertEqual(0, code, correction)
            code, corrected_batch = invoke(C09, ["--data-root", str(root), "continue-successors",
                "--project-id", PROJECT, "--validation-id", VALIDATION,
                "--execution-map", str(execution_map_path), "--writer-id", "codex-module-central",
                "--caller-thread-ref", "central-thread-g1"])
            self.assertEqual(0, code, corrected_batch)
            self.assertNotIn(parallel_task, {item["taskId"] for item in corrected_batch["previouslyPreparedTasks"]})
            self.assertIn(second_task, {item["taskId"] for item in corrected_batch["previouslyPreparedTasks"]})
            self.assertIn(parallel_task, {item["taskId"] for item in corrected_batch["blockedTasks"]})
            from test_correction_resume import verification
            proof = verification(root, PROJECT, parallel_task, "central-thread-g1")
            code, applied = c03(root, "verify-correction", "--task-id", parallel_task,
                "--verification", str(proof))
            self.assertEqual(0, code, applied)
            code, stale_batch = invoke(C09, ["--data-root", str(root), "continue-successors",
                "--project-id", PROJECT, "--validation-id", VALIDATION,
                "--execution-map", str(execution_map_path), "--writer-id", "codex-module-central",
                "--caller-thread-ref", "central-thread-g1"])
            self.assertEqual(0, code, stale_batch)
            stale = next(item for item in stale_batch["blockedTasks"] if item["taskId"] == parallel_task)
            self.assertEqual("TASK_DIRECTION_VERSION_MISMATCH", stale["reason"])
            self.assertEqual("PREPARE_CURRENT_DIRECTION_DISPATCH", stale["nextAction"])
            self.assertIn(second_task, {item["taskId"] for item in stale_batch["previouslyPreparedTasks"]})
            original_request = Path(next(item for item in execution_plan["tasks"]
                if item["taskId"] == parallel_task)["c10RequestPath"])
            new_request = json.loads(original_request.read_text())
            new_request["dispatchId"] = "dispatch-parallel-new-direction"
            new_request_path = write_json(root / "c10-inputs/new-direction.json", new_request)
            code, new_prepared = invoke(C10, ["--data-root", str(root), "--project-id", PROJECT,
                "--writer-id", "codex-module-central", "prepare", "--package-id", new_request["packageId"],
                "--review-id", new_request["reviewId"], "--request", str(new_request_path)])
            self.assertEqual(0, code, new_prepared)
            code, current_batch = invoke(C09, ["--data-root", str(root), "continue-successors",
                "--project-id", PROJECT, "--validation-id", VALIDATION,
                "--execution-map", str(execution_map_path), "--writer-id", "codex-module-central",
                "--caller-thread-ref", "central-thread-g1"])
            self.assertEqual(0, code, current_batch)
            current = next(item for item in current_batch["previouslyPreparedTasks"] if item["taskId"] == parallel_task)
            self.assertEqual(new_request["dispatchId"], current["dispatchId"])
            new_request["dispatchId"] = "dispatch-parallel-ambiguous-direction"
            write_json(new_request_path, new_request)
            self.assertEqual(0, invoke(C10, ["--data-root", str(root), "--project-id", PROJECT,
                "--writer-id", "codex-module-central", "prepare", "--package-id", new_request["packageId"],
                "--review-id", new_request["reviewId"], "--request", str(new_request_path)])[0])
            code, ambiguous = invoke(C09, ["--data-root", str(root), "continue-successors",
                "--project-id", PROJECT, "--validation-id", VALIDATION,
                "--execution-map", str(execution_map_path), "--writer-id", "codex-module-central",
                "--caller-thread-ref", "central-thread-g1"])
            self.assertEqual(0, code, ambiguous)
            self.assertNotIn(parallel_task, {item["taskId"] for item in ambiguous["previouslyPreparedTasks"]})
            self.assertEqual("C09_MULTIPLE_CURRENT_DIRECTION_DISPATCHES",
                next(item for item in ambiguous["blockedTasks"] if item["taskId"] == parallel_task)["reason"])
            code, wait_record = c03(root, "record-wait", "--task-id", parallel_task, "--wait-id", "wait-parallel-material",
                "--owner-ref", "owner-parallel", "--condition-ref", "condition-material", "--reason-ref", "reason-material")
            self.assertEqual(0, code, wait_record)
            code, waiting_batch = invoke(C09, ["--data-root", str(root), "continue-successors", "--project-id", PROJECT,
                "--validation-id", VALIDATION, "--execution-map", str(execution_map_path),
                "--writer-id", "codex-module-central", "--caller-thread-ref", "central-thread-g1"])
            self.assertEqual(0, code, waiting_batch)
            self.assertNotIn(parallel_task, {item["taskId"] for item in waiting_batch["previouslyPreparedTasks"]})
            self.assertIn(second_task, {item["taskId"] for item in waiting_batch["previouslyPreparedTasks"]})
            self.assertEqual("EXTERNAL_WAIT", waiting_batch["waitingTasks"][0]["reason"])
            rearrangement = {"tasks": [dict(item) for item in reversed(execution_plan["tasks"])]}
            next(item for item in rearrangement["tasks"] if item["taskId"] == blocked_task)["schedulingDependencies"] = [second_task]
            rearranged_path = write_json(root / "c09-inputs" / "rearrangement.json", rearrangement)
            code, revised = invoke(C09, ["--data-root", str(root), "revise-plan", "--project-id", PROJECT,
                "--execution-map", str(execution_map_path), "--arrangement", str(rearranged_path),
                "--revision-id", "revision-live-001", "--reason-ref", "priority-change-001",
                "--writer-id", "codex-module-central", "--caller-thread-ref", "central-thread-g1"])
            self.assertEqual(0, code, revised)
            self.assertFalse(revised["bossRepromptRequired"])
            code, stale = invoke(C09, ["--data-root", str(root), "continue-successors", "--project-id", PROJECT,
                "--validation-id", VALIDATION, "--execution-map", str(execution_map_path),
                "--writer-id", "codex-module-central", "--caller-thread-ref", "central-thread-g1"])
            self.assertNotEqual(0, code, stale)
            self.assertEqual("C09_PLAN_SOURCE_SUPERSEDED", stale["reason"])
            execution_map_path = Path(revised["artifact"])
            blocked_occupancy["windowReview"] = {"mode": "AUTO", "candidateWindowId": None, "contextCompatibility": "INCOMPATIBLE", "compatibilityEvidenceRefs": []}
            blocked_occupancy["crossModuleGate"] = {"status": "NOT_APPLICABLE", "reference": "fable-handoff-009"}
            write_json(blocked_occupancy_path, blocked_occupancy)
            code, waiting = invoke(C09, ["--data-root", str(root), "continue-successors", "--project-id", PROJECT,
                "--validation-id", VALIDATION, "--execution-map", str(execution_map_path),
                "--writer-id", "codex-module-central", "--caller-thread-ref", "central-thread-g1"])
            self.assertEqual(0, code, waiting)
            self.assertEqual(0, waiting["preparedCount"])
            self.assertEqual([second_task], next(item for item in waiting["waitingTasks"] if item["taskId"] == blocked_task)["unmetSchedulingDependencies"])
            code, bypass = invoke(SCRIPTS / "occupancy_conflict_checker.py", ["--data-root", str(root),
                "--project-id", PROJECT, "--writer-id", "codex-module-central", "--caller-thread-ref", "central-thread-g1",
                "evaluate", "--package-id", blocked_package, "--review", str(blocked_occupancy_path), "--apply"])
            self.assertNotEqual(0, code, bypass)
            self.assertEqual("C05_GATE_NOT_READY:WAITING_FOR_EXECUTION_ARRANGEMENT", bypass["reason"])
            next(item for item in rearrangement["tasks"] if item["taskId"] == blocked_task).pop("schedulingDependencies")
            write_json(rearranged_path, rearrangement)
            code, revised = invoke(C09, ["--data-root", str(root), "revise-plan", "--project-id", PROJECT,
                "--execution-map", str(execution_map_path), "--arrangement", str(rearranged_path),
                "--revision-id", "revision-live-002", "--reason-ref", "priority-change-002",
                "--writer-id", "codex-module-central", "--caller-thread-ref", "central-thread-g1"])
            self.assertEqual(0, code, revised)
            execution_map_path = Path(revised["artifact"])
            code, resumed = invoke(C09, [
                "--data-root", str(root), "continue-successors", "--project-id", PROJECT,
                "--validation-id", VALIDATION, "--execution-map", str(execution_map_path), "--writer-id", "codex-module-central", "--caller-thread-ref", "central-thread-g1",
            ])
            self.assertEqual(code, 0, resumed)
            self.assertEqual(resumed["status"], "READY_FOR_BATCH_RUNTIME_DISPATCH")
            self.assertEqual(resumed["preparedCount"], 1)
            self.assertEqual(resumed["preparedTasks"][0]["taskId"], blocked_task)
            self.assertEqual(resumed["preparedTasks"][0]["windowAction"]["action"], "CREATE_TASK")
            self.assertTrue(resumed["batchArtifact"].endswith("successor-batch-r0002.json"))
            ledger = json.loads((root / "module-ledgers" / PROJECT / "ledger.json").read_text())
            self.assertEqual(ledger["windows"][WINDOW]["status"], "RESERVED_FOR_REUSE")
            self.assertEqual(ledger["windows"][WINDOW]["reservedForTaskId"], second_task)
            confirmation = {
                "confirmationSchemaVersion": "0.17.0", "recordType": "C10_RUNTIME_CONFIRMATION", "dispatchId": dispatch_id,
                "taskWindow": {"status": "REUSED", "taskId": second_task, "runtimeTitle": "C-07｜Fictional second assignment｜G1", "generation": 1, "windowId": WINDOW, "runtimeThreadRef": "thread-c07-final", "runtimeProjectId": "codex-project-007", "runtimeCwd": "/tmp/.codex/worktrees/abcd/fictional-project", "environmentType": "WORKTREE", "associationMethod": "EXISTING_REGISTERED_WINDOW", "associationHandoffRefs": [], "modelControl": {"model": "gpt-5.6-terra", "method": "NATIVE_SEND_MESSAGE_MODEL_OVERRIDE", "evidenceRef": "thread-c07-final"}, "permissionControl": {"permissionClass": "WORKTREE_SCOPED", "profile": ":workspace", "method": "PERMISSION_PROFILE_READBACK", "evidenceRef": "thread-c07-final", "writableRoots": ["/tmp/.codex/worktrees/abcd/fictional-project"], "governanceDataRootAccess": "DENIED"}},
                "subAgents": [],
            }
            confirmation_path = write_json(root / "c10-inputs" / "confirmation-007.json", confirmation)
            code, confirmed = invoke(C10, [
                "--data-root", str(root), "--project-id", PROJECT, "--writer-id", "codex-module-central",
                "confirm", "--dispatch-id", dispatch_id, "--confirmation", str(confirmation_path),
            ])
            self.assertEqual(code, 0, confirmed)
            self.assertEqual(confirmed["assignmentNumber"], 2)
            code, output = c03(root, "record-completion-signal", "--task-id", second_task, "--signal-id", "completion-signal-007")
            self.assertEqual(code, 0, output)

            second_evidence = [f"evidence-007-{index}" for index in range(9)]
            second_handback = {
                "handbackSchemaVersion": "0.8.0", "recordType": "C06_TASK_WINDOW_HANDBACK", "handbackId": "handback-c06-007",
                "projectId": PROJECT, "packageId": second_package, "c05ReviewId": second_review, "taskId": second_task, "windowId": WINDOW,
                "returnTicketId": "return-ticket-c06-007", "routeRevisionSeen": 1,
                "completionSignalId": "completion-signal-007", "submittedBy": {"type": "task-window", "id": WINDOW},
                "bossHandbackAuthorization": {"status": "APPROVED", "reference": "boss-handback-007"},
                "executedScopeRefs": ["scope-executed-007"], "evidenceRefs": second_evidence,
                "testAndObjectRefs": ["test-object-ids-007"], "parentQualityReviewRefs": [], "unresolvedRefs": [], "residualRiskRefs": [],
            }
            second_handback_path = write_json(root / "handbacks" / "handback-c06-007.json", second_handback)
            categories = ("beforeSnapshot", "afterSnapshot", "positiveCase", "negativeCase", "idempotency", "rollback", "logsAndHistory", "upstreamReadback", "downstreamReadback")
            evidence_assessment = {category: {"reference": reference, "verdict": "PASS", "independentlyReadBack": True} for category, reference in zip(categories, second_evidence)}
            evidence_assessment["testAndObjectIds"] = {"reference": "test-object-ids-007", "verdict": "PASS", "independentlyReadBack": True}
            second_validation = "validation-c06-007"
            second_validation_review = {
                "reviewSchemaVersion": "0.8.0", "recordType": "C06_INDEPENDENT_VALIDATION_REVIEW", "validationId": second_validation,
                "handbackId": "handback-c06-007", "taskId": second_task,
                "centralReviewer": {"id": "codex-module-central", "independentReadbackPerformed": True},
                "scopeAssessment": {"packageScopeMatch": True, "parallelMechanismFound": False, "unknownWriterFound": False, "permissionExpansionFound": False, "unexplainedErrorFound": False, "duplicateDataFound": False, "blockingResidualRiskFound": False, "rollbackExecutable": True},
                "evidenceAssessment": evidence_assessment,
            }
            second_review_path = write_json(root / "validation-inputs" / "validation-c06-007.json", second_validation_review)
            code, returned = c08(root, "submit-return", "--handback", str(second_handback_path))
            self.assertEqual(code, 0, returned)
            code, admitted = c08(root, "admit-return", "--return-ticket-id", "return-ticket-c06-007", "--current-thread-ref", "central-thread-g1", "--admission-ref", "central-return-admission-007", writer=True)
            self.assertEqual(code, 0, admitted)
            code, assessed = invoke(C06, [
                "--data-root", str(root), "--project-id", PROJECT, "--writer-id", "codex-module-central",
                "assess", "--package-id", second_package, "--handback", str(second_handback_path), "--review", str(second_review_path), "--apply",
            ])
            self.assertEqual(code, 0, assessed)
            code, finalized = invoke(C06, [
                "--data-root", str(root), "--project-id", PROJECT, "--writer-id", "codex-module-central",
                "finalize", "--validation-id", second_validation, "--boss-decision", "APPROVED", "--boss-decision-ref", "boss-final-approval-007",
            ])
            self.assertEqual(code, 0, finalized)
            self.assertEqual(finalized["completedWindowStatus"], "RETIRED")
            ledger = json.loads((root / "module-ledgers" / PROJECT / "ledger.json").read_text())
            self.assertEqual(ledger["windows"][WINDOW]["assignmentCount"], 2)
            self.assertEqual(ledger["windows"][WINDOW]["status"], "RETIRED")
            self.assertIsNone(ledger["windows"][WINDOW]["currentTaskId"])

    def test_missing_evidence_cannot_finish(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_ready_for_validation(root)
            handback = create_handback(root)
            review = create_review(root, lambda value: value["evidenceAssessment"]["downstreamReadback"].update({"verdict": "MISSING", "independentlyReadBack": False}))
            code, output = self.assess(root, handback, review, "--apply", "codex-module-central")
            self.assertEqual(code, 0, output)
            self.assertEqual(output["status"], "NEEDS_REVIEW")
            code, refused = invoke(C06, [
                "--data-root", str(root), "--project-id", PROJECT, "--writer-id", "codex-module-central",
                "finalize", "--validation-id", VALIDATION, "--boss-decision", "APPROVED", "--boss-decision-ref", "boss-final-approval-006",
            ])
            self.assertEqual(code, 2)
            self.assertEqual(refused["reason"], "C06_ONLY_PASSED_VALIDATION_CAN_BE_FINALIZED")

    def test_unresolved_item_cannot_reach_boss_final_approval_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_ready_for_validation(root)
            handback = create_handback(root, unresolved=["unresolved-item-006"])
            code, output = self.assess(root, handback, create_review(root), "--apply", "codex-module-central")
            self.assertEqual(code, 0, output)
            self.assertEqual(output["status"], "NEEDS_REVIEW")
            self.assertFalse(output["bossFinalApprovalRequired"])

    def test_nonblocking_residual_risk_can_be_disclosed_without_faking_resolution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_ready_for_validation(root)
            handback = create_handback(root, residual=["known-nonblocking-risk-006"])
            code, output = self.assess(root, handback, create_review(root), "--apply", "codex-module-central")
            self.assertEqual(code, 0, output)
            self.assertEqual(output["status"], "PASS_PENDING_BOSS_APPROVAL")

    def test_conflict_creates_hard_stop(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_ready_for_validation(root)
            handback = create_handback(root)
            review = create_review(root, lambda value: value["scopeAssessment"].update({"unknownWriterFound": True}))
            code, output = self.assess(root, handback, review, "--apply", "codex-module-central")
            self.assertEqual(code, 0, output)
            self.assertEqual(output["status"], "CONFLICT")
            ledger = json.loads((root / "module-ledgers" / PROJECT / "ledger.json").read_text())
            self.assertEqual(ledger["tasks"][TASK]["status"], "CONFLICT")
            self.assertEqual(ledger["hardStops"][-1]["code"], "C06_VALIDATION_CONFLICT")

    def test_boss_adjudicated_c06_conflict_moves_to_resolved_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_ready_for_validation(root)
            handback = create_handback(root)
            review = create_review(
                root,
                lambda value: value["scopeAssessment"].update({"unknownWriterFound": True}),
            )
            code, conflict = self.assess(root, handback, review, "--apply", "codex-module-central")
            self.assertEqual(code, 0, conflict)
            self.assertEqual(conflict["status"], "CONFLICT")

            superseding_validation = "validation-c06-superseding-pass-002"
            boss_decision = "boss-final-superseding-pass-002"
            write_json(
                root / "handover-validations" / PROJECT / superseding_validation / "validation-decision.json",
                {
                    "recordType": "C06_VALIDATION_DECISION",
                    "projectId": PROJECT,
                    "taskId": TASK,
                    "validationId": superseding_validation,
                    "outcome": "PASS_PENDING_BOSS_APPROVAL",
                },
            )
            code, refused = c03(
                root,
                "resolve-c06-validation-conflict",
                "--task-id", TASK,
                "--conflict-validation-id", VALIDATION,
                "--superseding-validation-id", superseding_validation,
                "--boss-decision-ref", boss_decision,
            )
            self.assertEqual(code, 2)
            self.assertEqual(refused["reason"], "C06_HARD_STOP_BOSS_DECISION_UNVERIFIED")
            code, adjudication = c03(
                root,
                "record-adjudication",
                "--adjudication-id", boss_decision,
                "--request-ref", superseding_validation,
                "--outcome", "BOSS_DECISION",
            )
            self.assertEqual(code, 0, adjudication)
            code, resolved = c03(
                root,
                "resolve-c06-validation-conflict",
                "--task-id", TASK,
                "--conflict-validation-id", VALIDATION,
                "--superseding-validation-id", superseding_validation,
                "--boss-decision-ref", boss_decision,
            )
            self.assertEqual(code, 0, resolved)
            self.assertEqual(resolved["hardStops"], [])
            self.assertEqual(resolved["resolvedHardStopCount"], 1)

            ledger = json.loads((root / "module-ledgers" / PROJECT / "ledger.json").read_text())
            archived = ledger["resolvedHardStops"][0]
            self.assertEqual(archived["originalHardStop"]["validationId"], VALIDATION)
            self.assertEqual(archived["supersedingValidationId"], superseding_validation)
            self.assertEqual(archived["bossDecisionRef"], boss_decision)
            conflict_receipt = json.loads(
                (root / "module-ledgers" / PROJECT / "receipts" / f"{conflict['ledgerReceiptId']}.json").read_text()
            )
            self.assertEqual(
                conflict_receipt["afterLedger"]["hardStops"][0]["validationId"],
                VALIDATION,
            )

            code, repeated = c03(
                root,
                "resolve-c06-validation-conflict",
                "--task-id", TASK,
                "--conflict-validation-id", VALIDATION,
                "--superseding-validation-id", superseding_validation,
                "--boss-decision-ref", boss_decision,
            )
            self.assertEqual(code, 2)
            self.assertEqual(repeated["reason"], "C06_ACTIVE_CONFLICT_HARD_STOP_NOT_UNIQUE")

    def test_failed_evidence_is_partial_not_done(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_ready_for_validation(root)
            handback = create_handback(root)
            review = create_review(root, lambda value: value["evidenceAssessment"]["rollback"].update({"verdict": "FAIL"}))
            code, output = self.assess(root, handback, review, "--apply", "codex-module-central")
            self.assertEqual(code, 0, output)
            self.assertEqual(output["status"], "PARTIAL")
            self.assertEqual(output["taskStatusAfter"], "PARTIAL")

    def test_boss_rejection_keeps_task_needs_review(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_ready_for_validation(root)
            code, output = self.assess(root, create_handback(root), create_review(root), "--apply", "codex-module-central")
            self.assertEqual(code, 0, output)
            code, final = invoke(C06, [
                "--data-root", str(root), "--project-id", PROJECT, "--writer-id", "codex-module-central",
                "finalize", "--validation-id", VALIDATION, "--boss-decision", "REJECTED",
                "--boss-decision-ref", "boss-final-rejection-006",
            ])
            self.assertEqual(code, 0, final)
            self.assertEqual(final["status"], "NEEDS_REVIEW")
            self.assertFalse(final["doneRecorded"])

    def test_validation_must_link_to_real_c03_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_ready_for_validation(root)
            code, output = self.assess(root, create_handback(root), create_review(root), "--apply", "codex-module-central")
            self.assertEqual(code, 0, output)
            decision_path = root / "handover-validations" / PROJECT / VALIDATION / "validation-decision.json"
            receipt_path = root / "handover-validations" / PROJECT / VALIDATION / "receipts" / "receipt-000000-validate.json"
            decision = json.loads(decision_path.read_text())
            decision["ledgerMutation"]["receiptId"] = "receipt-999999-missing"
            decision_path.write_text(json.dumps(decision), encoding="utf-8")
            receipt = json.loads(receipt_path.read_text())
            receipt["afterDecision"] = decision
            from hashlib import sha256
            serialized = json.dumps(decision, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            receipt["afterDecisionDigest"] = sha256(serialized.encode("utf-8")).hexdigest()
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            code, output = invoke(C06, [
                "--data-root", str(root), "--project-id", PROJECT, "verify", "--validation-id", VALIDATION,
            ])
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "C06_LINKED_LEDGER_RECEIPT_MISSING_OR_INVALID")

    def test_boss_must_authorize_handback_before_central_assessment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_ready_for_validation(root)
            handback, review = create_handback(root, "REQUIRED"), create_review(root)
            code, output = self.assess(root, handback, review)
            self.assertEqual(code, 0, output)
            self.assertEqual(output["status"], "WAITING_FOR_BOSS_HANDBACK_AUTHORIZATION")
            code, output = self.assess(root, handback, review, "--apply", "codex-module-central")
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "C06_BOSS_HANDBACK_AUTHORIZATION_REQUIRED")

    def test_boss_approved_return_policy_can_admit_with_no_per_task_reprompt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"; setup_ready_for_validation(root)
            policy = write_json(root / "return-policy-inputs" / "policy.json", {
                "returnPolicySchemaVersion": "0.18.0", "recordType": "C08_RETURN_ADMISSION_POLICY", "policyId": "return-policy-c06-001",
                "projectId": PROJECT, "mode": "AUTO_QUEUE_AND_VALIDATE_WITHIN_APPROVED_SCOPE", "maxConcurrentValidations": 1,
                "bossAuthorizationRef": "boss-return-policy-approval-006", "finalDoneRequiresBoss": True,
            })
            code, output = c08(root, "configure-return-policy", "--policy", str(policy), "--current-thread-ref", "central-thread-g1", writer=True)
            self.assertEqual(code, 0, output)
            handback = create_handback(root, "AUTO_APPROVED_BY_RETURN_POLICY")
            data = json.loads(handback.read_text()); data["bossHandbackAuthorization"]["reference"] = "return-policy-c06-001"; write_json(handback, data)
            code, output = self.assess(root, handback, create_review(root), "--apply", "codex-module-central")
            self.assertEqual(code, 0, output); self.assertEqual(output["status"], "PASS_PENDING_BOSS_APPROVAL")
            self.assertTrue(output["bossFinalApprovalRequired"])

    def test_return_pipeline_quarantines_evidence_and_records_only_validation_outcome_for_central(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"; setup_ready_for_validation(root)
            handback, review = create_handback(root), create_review(root)
            queue_and_admit_return(root, handback)
            code, reserved = c08(root, "reserve-next-return", "--current-thread-ref", "central-thread-g1", "--validator-thread-ref", "independent-validator-c06-001", writer=True)
            self.assertEqual(code, 0, reserved); self.assertEqual(reserved["centralPayload"], "TICKET_AND_STATUS_ONLY")
            code, assessed = invoke(C06, [
                "--data-root", str(root), "--project-id", PROJECT, "--writer-id", "codex-module-central", "assess", "--package-id", PACKAGE,
                "--return-ticket-id", "return-ticket-c06-001", "--review", str(review), "--apply",
            ])
            self.assertEqual(code, 0, assessed); self.assertEqual(assessed["status"], "PASS_PENDING_BOSS_APPROVAL")
            code, recorded = c08(root, "record-return-validation", "--return-ticket-id", "return-ticket-c06-001", "--current-thread-ref", "central-thread-g1", "--validator-thread-ref", "independent-validator-c06-001", "--validation-id", VALIDATION, writer=True)
            self.assertEqual(code, 0, recorded); self.assertEqual(recorded["status"], "RETURN_VALIDATION_RECORDED")
            self.assertEqual(recorded["centralPayload"], "OUTCOME_AND_RECEIPT_ONLY")
            self.assertEqual(recorded["alertKind"], "BOSS_FINAL_DONE_APPROVAL")

    def test_chat_style_handback_without_durable_return_ticket_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"; setup_ready_for_validation(root)
            handback, review = create_handback(root), create_review(root)
            code, output = invoke(C06, [
                "--data-root", str(root), "--project-id", PROJECT, "assess", "--package-id", PACKAGE,
                "--handback", str(handback), "--review", str(review), "--dry-run",
            ])
            self.assertEqual(code, 2, output); self.assertEqual(output["reason"], "C06_RETURN_TICKET_NOT_QUEUED")

    def test_tampered_return_delivery_receipt_is_refused_before_evidence_review(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"; setup_ready_for_validation(root)
            handback, review = create_handback(root), create_review(root)
            queue_and_admit_return(root, handback)
            delivery = root / "return-inbox" / PROJECT / "tickets" / "return-ticket-c06-001" / "receipt-000001-delivery.json"
            data = json.loads(delivery.read_text()); data["eventDigest"] = "0" * 64; write_json(delivery, data)
            code, output = invoke(C06, [
                "--data-root", str(root), "--project-id", PROJECT, "assess", "--package-id", PACKAGE,
                "--return-ticket-id", "return-ticket-c06-001", "--review", str(review), "--dry-run",
            ])
            self.assertEqual(code, 2, output); self.assertEqual(output["reason"], "C06_RETURN_TICKET_INTEGRITY_INVALID")

    def test_non_central_writer_cannot_apply(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_ready_for_validation(root)
            code, output = self.assess(root, create_handback(root), create_review(root), "--apply", "task-window")
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "C06_WRITER_NOT_AUTHORIZED")

    def test_handback_cannot_mix_package_and_c05_decision(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_ready_for_validation(root)
            c05_path = root / "occupancy-reviews" / PROJECT / "c05-review-006" / "occupancy-decision.json"
            c05_receipt_path = root / "occupancy-reviews" / PROJECT / "c05-review-006" / "receipts" / "receipt-000000-review.json"
            decision = json.loads(c05_path.read_text())
            decision["packageId"] = "different-package"
            c05_path.write_text(json.dumps(decision), encoding="utf-8")
            receipt = json.loads(c05_receipt_path.read_text())
            receipt["afterDecision"] = decision
            from hashlib import sha256
            serialized = json.dumps(decision, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            receipt["afterDecisionDigest"] = sha256(serialized.encode("utf-8")).hexdigest()
            c05_receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            code, output = self.assess(root, create_handback(root), create_review(root))
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "C06_TASK_SOURCE_MISMATCH")

    def test_repeat_assessment_and_finalization_are_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_ready_for_validation(root)
            handback, review = create_handback(root), create_review(root)
            code, first = self.assess(root, handback, review, "--apply", "codex-module-central")
            self.assertEqual(code, 0, first)
            code, second = self.assess(root, handback, review, "--apply", "codex-module-central")
            self.assertEqual(code, 0, second)
            self.assertEqual(second["status"], "IDEMPOTENT_EXISTING_C06_DECISION")
            args = [
                "--data-root", str(root), "--project-id", PROJECT, "--writer-id", "codex-module-central",
                "finalize", "--validation-id", VALIDATION, "--boss-decision", "APPROVED", "--boss-decision-ref", "boss-final-approval-006",
            ]
            code, _ = invoke(C06, args)
            self.assertEqual(code, 0)
            revision = json.loads((root / "module-ledgers" / PROJECT / "ledger.json").read_text())["revision"]
            code, repeated = invoke(C06, args)
            self.assertEqual(code, 0, repeated)
            self.assertEqual(repeated["status"], "IDEMPOTENT_EXISTING_C06_FINALIZATION")
            self.assertEqual(json.loads((root / "module-ledgers" / PROJECT / "ledger.json").read_text())["revision"], revision)

    def test_conflict_task_accepts_only_a_bound_superseding_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_ready_for_validation(root)
            first_handback = create_handback(root)
            first_review = create_review(
                root, lambda review: review["scopeAssessment"].update({"parallelMechanismFound": True})
            )
            code, conflict = self.assess(root, first_handback, first_review, "--apply", "codex-module-central")
            self.assertEqual(code, 0, conflict)
            self.assertEqual(conflict["status"], "CONFLICT")

            invalid_payload = json.loads(first_handback.read_text(encoding="utf-8"))
            invalid_payload.update({
                "handbackId": "handback-c06-unbound-002",
                "returnTicketId": "return-ticket-c06-unbound-002",
                "completionSignalId": "completion-signal-c06-unbound-002",
                "executedScopeRefs": ["scope-without-conflict-reference"],
            })
            invalid_handback = write_json(root / "handbacks" / "handback-c06-unbound-002.json", invalid_payload)
            code, refused = c08(root, "submit-return", "--handback", str(invalid_handback))
            self.assertEqual(code, 2, refused)
            self.assertEqual(refused["reason"], "C08C_RETURN_SOURCE_MISMATCH")

            second_validation = "validation-c06-superseding-002"
            second_handback_payload = json.loads(first_handback.read_text(encoding="utf-8"))
            second_handback_payload.update({
                "handbackId": "handback-c06-superseding-002",
                "returnTicketId": "return-ticket-c06-superseding-002",
                "completionSignalId": "completion-signal-c06-superseding-002",
                "executedScopeRefs": [VALIDATION, "scope-executed-c06-superseding-002"],
            })
            second_handback = write_json(
                root / "handbacks" / "handback-c06-superseding-002.json", second_handback_payload
            )
            code, queued = c08(root, "submit-return", "--handback", str(second_handback))
            self.assertEqual(code, 0, queued)
            code, admitted = c08(
                root, "admit-return", "--return-ticket-id", queued["returnTicketId"],
                "--current-thread-ref", "central-thread-g1",
                "--admission-ref", "central-return-admission-c06-superseding-002", writer=True,
            )
            self.assertEqual(code, 0, admitted)
            ledger = ledger_manager.load_ledger(root, PROJECT)
            self.assertEqual(ledger["tasks"][TASK]["status"], "CONFLICT")
            self.assertIn(second_handback_payload["completionSignalId"], ledger["tasks"][TASK]["completionSignalIds"])

            second_review_payload = json.loads(create_review(root).read_text(encoding="utf-8"))
            second_review_payload.update({
                "validationId": second_validation,
                "handbackId": second_handback_payload["handbackId"],
            })
            second_review = write_json(
                root / "validation-inputs" / f"{second_validation}.json", second_review_payload
            )
            code, passed = invoke(C06, [
                "--data-root", str(root), "--project-id", PROJECT,
                "--writer-id", "codex-module-central", "assess", "--package-id", PACKAGE,
                "--handback", str(second_handback), "--review", str(second_review), "--apply",
            ])
            self.assertEqual(code, 0, passed)
            self.assertEqual(passed["status"], "PASS_PENDING_BOSS_APPROVAL")
            self.assertEqual(ledger_manager.load_ledger(root, PROJECT)["tasks"][TASK]["status"], "NEEDS_REVIEW")

    def test_decision_tampering_is_detected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_ready_for_validation(root)
            code, output = self.assess(root, create_handback(root), create_review(root), "--apply", "codex-module-central")
            self.assertEqual(code, 0, output)
            receipt_path = root / "handover-validations" / PROJECT / VALIDATION / "receipts" / "receipt-000000-validate.json"
            receipt = json.loads(receipt_path.read_text())
            receipt["afterDecision"]["outcome"] = "TAMPERED"
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            code, output = invoke(C06, [
                "--data-root", str(root), "--project-id", PROJECT, "verify", "--validation-id", VALIDATION,
            ])
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "C06_VALIDATION_RECEIPT_CHAIN_INVALID")

    def test_finalization_must_bind_the_same_validation_decision(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_ready_for_validation(root)
            code, output = self.assess(root, create_handback(root), create_review(root), "--apply", "codex-module-central")
            self.assertEqual(code, 0, output)
            code, output = invoke(C06, [
                "--data-root", str(root), "--project-id", PROJECT, "--writer-id", "codex-module-central",
                "finalize", "--validation-id", VALIDATION, "--boss-decision", "APPROVED",
                "--boss-decision-ref", "boss-final-approval-006",
            ])
            self.assertEqual(code, 0, output)
            final_path = root / "handover-validations" / PROJECT / VALIDATION / "boss-finalization.json"
            receipt_path = root / "handover-validations" / PROJECT / VALIDATION / "receipts" / "receipt-000001-finalize.json"
            finalization = json.loads(final_path.read_text())
            finalization["validationDecisionDigest"] = "0" * 64
            final_path.write_text(json.dumps(finalization), encoding="utf-8")
            receipt = json.loads(receipt_path.read_text())
            receipt["afterFinalization"] = finalization
            from hashlib import sha256
            serialized = json.dumps(finalization, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            receipt["afterFinalizationDigest"] = sha256(serialized.encode("utf-8")).hexdigest()
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            code, output = invoke(C06, [
                "--data-root", str(root), "--project-id", PROJECT, "verify", "--validation-id", VALIDATION,
            ])
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "C06_FINALIZATION_RECEIPT_CHAIN_INVALID")


if __name__ == "__main__":
    unittest.main()
