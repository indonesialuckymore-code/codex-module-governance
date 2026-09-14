"""Reported issues never erase historical acceptance or create duplicate tasks."""
import tempfile
import json
import unittest
from pathlib import Path
import test_independent_handover_validator as fixture


def completed(root):
    fixture.setup_ready_for_validation(root)
    code, result = fixture.IndependentHandoverValidatorTests().assess(root,
        fixture.create_handback(root), fixture.create_review(root), "--apply", "codex-module-central")
    if code: raise AssertionError(result)
    code, result = fixture.invoke(fixture.C06, ["--data-root", str(root), "--project-id", fixture.PROJECT,
        "--writer-id", "codex-module-central", "finalize", "--validation-id", fixture.VALIDATION,
        "--boss-decision", "APPROVED", "--boss-decision-ref", "boss-final-feedback"])
    if code: raise AssertionError(result)


def report(root, validation_id=fixture.VALIDATION):
    return fixture.c03(root, "record-feedback", "--task-id", fixture.TASK, "--feedback-id", "feedback-missing-case",
        "--validation-id", validation_id, "--issue-ref", "issue-missing-case",
        "--affected-ref", "delivered-instruction", "--repair-task-id", "C-repair")


def repair_ready(root):
    """A second real governed task, not a mocked DONE or forged acceptance."""
    def check(result):
        if result[0]: raise AssertionError(result)
    check(fixture.c03(root, "add-task", "--task-id", "C-repair", "--title", "Repair outcome",
        "--business-goal", "Resolve reported issue", "--plan-ref", "approved-repair-scope"))
    check(report(root))
    brief = json.loads((root / "briefs" / f"{fixture.TASK}.json").read_text())
    brief["taskId"] = "C-repair"
    brief["deliverables"] = ["Correct delivered-instruction and independently verify issue-missing-case."]
    path = fixture.write_json(root / "briefs/repair.json", brief)
    check(fixture.invoke(fixture.C04, ["--data-root", str(root), "--project-id", fixture.PROJECT,
        "--writer-id", "codex-module-central", "generate", "--package-id", "repair-package",
        "--task-id", "C-repair", "--brief", str(path), "--apply"]))
    occupancy = json.loads((root / "occupancy-inputs/c05-review-006.json").read_text())
    occupancy.update(reviewId="repair-occupancy", packageId="repair-package", taskId="C-repair")
    occupancy["occupancyRequests"][0].update(objectKey="file:repair-outcome", conflictKey="file:repair-outcome")
    path = fixture.write_json(root / "occupancy-inputs/repair.json", occupancy)
    check(fixture.invoke(fixture.C05, ["--data-root", str(root), "--project-id", fixture.PROJECT,
        "--writer-id", "codex-module-central", "--caller-thread-ref", "central-thread-g1", "evaluate", "--package-id", "repair-package",
        "--review", str(path), "--apply"]))
    check(fixture.c03(root, "register-window", "--window-id", "repair-window", "--task-id", "C-repair",
        "--context-mode", "NEW", "--runtime-model-evidence-ref", "repair-model",
        "--runtime-permission-profile", ":workspace", "--runtime-permission-evidence-ref", "repair-permission",
        "--runtime-writable-root", "/tmp/.codex/worktrees/abcd/fictional-project", "--governance-data-root-access", "DENIED"))
    check(fixture.c03(root, "transition-task", "--task-id", "C-repair", "--to-status", "IN_PROGRESS",
        "--reason", "Approved fictional repair"))
    handback = json.loads(fixture.create_handback(root).read_text())
    handback.update(taskId="C-repair", packageId="repair-package", c05ReviewId="repair-occupancy",
        handbackId="repair-handback", returnTicketId="repair-ticket", windowId="repair-window",
        completionSignalId="repair-completion", submittedBy={"type": "task-window", "id": "repair-window"})
    handback["evidenceRefs"].append("new-issue-resolution-evidence")
    path = fixture.write_json(root / "handbacks/repair.json", handback)
    fixture.queue_and_admit_return(root, path)
    review = json.loads(fixture.create_review(root).read_text())
    review.update(taskId="C-repair", handbackId="repair-handback", validationId="repair-validation")
    from ledger_manager import feedback_binding
    entry = fixture.c03(root, "read-summary")[1]["deliveryFeedback"][fixture.TASK][0]
    review["feedbackAssessments"] = [{"originalTaskId": fixture.TASK, "feedbackId": entry["feedbackId"],
        "feedbackDigest": feedback_binding(entry), "evidenceRef": "new-issue-resolution-evidence",
        "issueResolved": True, "independentlyReadBack": True}]
    return path, fixture.write_json(root / "validation-inputs/repair.json", review)


def assess_repair(root, paths):
    return fixture.invoke(fixture.C06, ["--data-root", str(root), "--project-id", fixture.PROJECT,
        "--writer-id", "codex-module-central", "assess", "--package-id", "repair-package",
        "--handback", str(paths[0]), "--review", str(paths[1]), "--apply"])


def finalize_repair(root):
    return fixture.invoke(fixture.C06, ["--data-root", str(root), "--project-id", fixture.PROJECT,
        "--writer-id", "codex-module-central", "finalize", "--validation-id", "repair-validation",
        "--boss-decision", "APPROVED", "--boss-decision-ref", "boss-repair-approved"])


def close(root, validation="repair-validation"):
    return fixture.c03(root, "close-feedback", "--task-id", fixture.TASK,
        "--feedback-id", "feedback-missing-case", "--validation-id", validation)


class DeliveryFeedbackTests(unittest.TestCase):
    def test_damaged_original_final_approval_prevents_feedback_closure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; completed(root)
            paths = repair_ready(root)
            self.assertEqual(0, assess_repair(root, paths)[0])
            self.assertEqual(0, finalize_repair(root)[0])
            path = fixture.validator.finalization_path(root, fixture.PROJECT, fixture.VALIDATION)
            original = json.loads(path.read_text()); original["bossDecision"] = "REJECTED"
            fixture.write_json(path, original)
            code, result = close(root)
            self.assertEqual(2, code, result)
            self.assertEqual("DELIVERY_FEEDBACK_REPAIR_ACCEPTANCE_UNVERIFIED", result["reason"])

    def test_unrelated_pass_does_not_close_issue_or_hide_another_open_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; completed(root)
            paths = repair_ready(root)
            review = json.loads(paths[1].read_text()); review.pop("feedbackAssessments")
            fixture.write_json(paths[1], review)
            self.assertEqual(0, assess_repair(root, paths)[0])
            self.assertEqual(0, finalize_repair(root)[0])
            code, result = close(root)
            self.assertEqual(2, code, result)
            self.assertEqual("DELIVERY_FEEDBACK_ISSUE_SPECIFIC_ACCEPTANCE_REQUIRED", result["reason"])
            self.assertEqual(2, close(root, fixture.VALIDATION)[0])

    def test_wrong_issue_version_old_evidence_or_unread_evidence_cannot_pass(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; completed(root)
            paths = repair_ready(root)
            original = json.loads(paths[1].read_text())
            for key, value, expected in (
                ("feedbackDigest", "0" * 64, "C06_FEEDBACK_REPAIR_BINDING_MISMATCH"),
                ("feedbackId", "another-feedback", "C06_FEEDBACK_REPAIR_BINDING_MISMATCH"),
                ("evidenceRef", "evidence-after", "C06_FEEDBACK_NEW_EVIDENCE_REQUIRED"),
                ("evidenceRef", "not-in-handback", "C06_FEEDBACK_EVIDENCE_NOT_DECLARED"),
            ):
                review = json.loads(json.dumps(original)); review["feedbackAssessments"][0][key] = value
                fixture.write_json(paths[1], review)
                code, result = assess_repair(root, paths)
                self.assertEqual(2, code, result); self.assertEqual(expected, result["reason"])
            original["feedbackAssessments"][0]["independentlyReadBack"] = False
            fixture.write_json(paths[1], original)
            code, result = assess_repair(root, paths)
            self.assertEqual(0, code, result)
            self.assertEqual("NEEDS_REVIEW", result["status"])
            self.assertEqual(2, finalize_repair(root)[0])
            self.assertEqual(2, close(root)[0])

    def test_closing_one_issue_preserves_other_issues_and_replay_keeps_history(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; completed(root)
            paths = repair_ready(root)
            self.assertEqual(0, assess_repair(root, paths)[0])
            self.assertEqual(0, finalize_repair(root)[0])
            self.assertEqual(0, fixture.c03(root, "record-feedback", "--task-id", fixture.TASK,
                "--feedback-id", "feedback-other", "--validation-id", fixture.VALIDATION,
                "--issue-ref", "issue-other", "--affected-ref", "affected-other")[0])
            self.assertEqual(0, close(root)[0])
            self.assertFalse(report(root)[1]["writePerformed"])
            summary = fixture.c03(root, "read-summary")[1]
            self.assertEqual("REPORTED_ISSUE_REQUIRES_REVIEW", summary["currentDeliveryState"][fixture.TASK])
            self.assertEqual({"CLOSED_VERIFIED", "REPORTED_REQUIRES_REVIEW"},
                             {item["status"] for item in summary["deliveryFeedback"][fixture.TASK]})

    def test_only_new_issue_specific_acceptance_and_boss_final_can_close_feedback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; completed(root)
            paths = repair_ready(root)
            code, result = assess_repair(root, paths)
            self.assertEqual(0, code, result)
            self.assertEqual(2, close(root)[0], "C06 pass alone is not final acceptance")
            self.assertEqual(0, finalize_repair(root)[0])
            code, result = close(root)
            self.assertEqual(0, code, result)
            self.assertFalse(close(root)[1]["writePerformed"])
            summary = fixture.c03(root, "read-summary")[1]
            self.assertEqual("DONE", summary["tasks"][fixture.TASK])
            self.assertEqual("CLOSED_VERIFIED", summary["deliveryFeedback"][fixture.TASK][0]["status"])
            self.assertEqual("REPORTED_ISSUES_CLOSED", summary["currentDeliveryState"][fixture.TASK])

    def test_report_can_link_repair_later_without_rewriting_original_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; completed(root)
            args = ("record-feedback", "--task-id", fixture.TASK, "--feedback-id", "feedback-later",
                "--validation-id", fixture.VALIDATION, "--issue-ref", "issue-later", "--affected-ref", "delivered-later")
            self.assertEqual(0, fixture.c03(root, *args)[0])
            fixture.c03(root, "add-task", "--task-id", "C-repair", "--title", "Repair outcome",
                "--business-goal", "Correct result", "--plan-ref", "approved-repair-scope")
            link = ("link-feedback-repair", "--task-id", fixture.TASK, "--feedback-id", "feedback-later",
                    "--repair-task-id", "C-repair")
            code, result = fixture.c03(root, *link)
            self.assertEqual(0, code, result)
            self.assertFalse(fixture.c03(root, *link)[1]["writePerformed"])
            self.assertFalse(fixture.c03(root, *args)[1]["writePerformed"])
            issue = fixture.c03(root, "read-summary")[1]["deliveryFeedback"][fixture.TASK][0]
            self.assertIsNone(issue["repairTaskId"])
            self.assertEqual("C-repair", issue["repairLink"]["repairTaskId"])

    def test_unverified_original_cannot_be_presented_as_accepted_delivery(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; fixture.setup_ready_for_validation(root)
            code, result = report(root)
            self.assertEqual(2, code, result)
            self.assertEqual("DELIVERY_FEEDBACK_ORIGINAL_ACCEPTANCE_UNVERIFIED", result["reason"])
            self.assertEqual({}, fixture.c03(root, "read-summary")[1]["deliveryFeedback"])

    def test_report_links_existing_repair_preserves_done_and_shows_current_issue(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; completed(root)
            fixture.c03(root, "add-task", "--task-id", "C-repair", "--title", "Repair reported outcome",
                "--business-goal", "Correct the affected result", "--plan-ref", "approved-repair-scope")
            code, result = report(root)
            self.assertEqual(0, code, result)
            self.assertFalse(report(root)[1]["writePerformed"])
            code, result = fixture.c03(root, "read-summary")
            self.assertEqual(0, code, result)
            self.assertEqual("DONE", result["tasks"][fixture.TASK])
            self.assertEqual("REPORTED_ISSUE_REQUIRES_REVIEW", result["currentDeliveryState"][fixture.TASK])
            self.assertEqual(2, len(result["tasks"]))
            issue = result["deliveryFeedback"][fixture.TASK][0]
            self.assertEqual("REPORTED_REQUIRES_REVIEW", issue["status"])
            self.assertEqual("C-repair", issue["repairTaskId"])
            self.assertEqual(fixture.VALIDATION, issue["originalValidationId"])
            self.assertTrue(issue["originalDecisionDigest"])


if __name__ == "__main__":
    unittest.main()
