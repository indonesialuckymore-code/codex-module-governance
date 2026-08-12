#!/usr/bin/env python3
"""Regression tests for C07 using only temporary fictional data."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).parent
C02 = SCRIPTS / "initialize_project.py"
C03 = SCRIPTS / "ledger_manager.py"
C07 = SCRIPTS / "adjudication_request_organizer.py"
PROJECT = "c07-demo-project"
TASK = "C-07"
WINDOW = "window-c07-001"
REQUEST = "adjudication-c07-001"


def invoke(script, arguments):
    completed = subprocess.run([sys.executable, str(script), *arguments], check=False, capture_output=True, text=True)
    return completed.returncode, json.loads(completed.stdout)


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def c03(root, command, *arguments):
    return invoke(C03, ["--data-root", str(root), "--project-id", PROJECT, "--writer-id", "codex-module-central", command, *arguments])


def setup_project(root):
    code, output = invoke(C02, [
        "--data-root", str(root), "--project-id", PROJECT, "--display-name", "C07 Fictional Project",
        "--scope-summary", "Isolated fictional adjudication workflow only.", "--apply",
    ])
    if code != 0:
        raise AssertionError(output)
    commands = [
        ["initialize", "--apply"],
        ["add-task", "--task-id", TASK, "--title", "Fictional blocked task", "--business-goal", "Test C07 routing.", "--plan-ref", "fable-plan-c07"],
        ["register-window", "--window-id", WINDOW, "--task-id", TASK, "--context-mode", "NEW"],
        ["transition-task", "--task-id", TASK, "--to-status", "READY", "--reason", "Fictional package approved."],
        ["transition-task", "--task-id", TASK, "--to-status", "IN_PROGRESS", "--reason", "Fictional execution started."],
        ["transition-task", "--task-id", TASK, "--to-status", "BLOCKED", "--reason", "Boss needs central advisory opinion."],
    ]
    for command in commands:
        code, output = c03(root, command[0], *command[1:])
        if code != 0:
            raise AssertionError(output)


def request_payload(mutation=None):
    payload = {
        "requestSchemaVersion": "0.7.0", "recordType": "C07_ADJUDICATION_REQUEST_INPUT",
        "requestId": REQUEST, "projectId": PROJECT, "taskId": TASK, "windowId": WINDOW,
        "submittedBy": {"type": "task-window", "id": WINDOW},
        "question": "Which fictional rollback approach should the original window use?",
        "whyBossCannotDecide": "The two options have different evidence and reversibility tradeoffs.",
        "knownFacts": [{"factId": "fact-c07-001", "statement": "The fictional task is blocked.", "evidenceRefs": ["evidence-c07-001"]}],
        "unknowns": ["Whether the second fictional path has complete rollback evidence."],
        "impactedObjectRefs": ["object-c07-001"],
        "options": [
            {"optionId": "option-c07-a", "title": "Use reversible path", "summary": "Apply the reversible fictional path.", "benefits": ["Easier rollback."], "risks": ["Takes longer."], "reversibility": "Fully reversible with fictional snapshot.", "requiredEvidenceRefs": ["evidence-c07-001"]},
            {"optionId": "option-c07-b", "title": "Wait for evidence", "summary": "Do not proceed until the missing evidence arrives.", "benefits": ["Lower uncertainty."], "risks": ["Delays the task."], "reversibility": "No business action is taken.", "requiredEvidenceRefs": []},
        ],
        "noActionImpact": "The fictional task remains blocked.",
        "requestedAdvice": "Compare both options and state the safest recommendation.",
    }
    if mutation:
        mutation(payload)
    return payload


def advice_payload(mutation=None):
    payload = {
        "adviceSchemaVersion": "0.7.0", "recordType": "C07_ADVISORY_OPINION", "requestId": REQUEST,
        "advisor": {"id": "central-background-advisor", "contextMode": "INDEPENDENT"},
        "factAssessment": [{"factId": "fact-c07-001", "verdict": "VERIFIED", "rationale": "The ledger shows the fictional blocked state."}],
        "recommendation": {"status": "RECOMMEND_OPTION", "optionId": "option-c07-a", "rationale": "It is the only fully reversible option.", "conditions": ["Retain the fictional snapshot."], "tradeoffs": ["Completion takes longer."]},
        "unknowns": ["The second path remains unverified."], "evidenceGaps": ["No rollback proof for option B."],
        "bossQuestions": ["Do you approve option A for the original window?"],
    }
    if mutation:
        mutation(payload)
    return payload


def decision_payload(status="SELECT_OPTION", selected="option-c07-a", mutation=None):
    payload = {
        "decisionSchemaVersion": "0.7.0", "recordType": "C07_BOSS_DECISION_INPUT", "requestId": REQUEST,
        "status": status, "selectedOptionId": selected, "rationale": "Use the reversible fictional path.",
        "decisionRef": "boss-decision-c07-001", "instructionToOriginalWindow": "Continue only with option A inside the existing scope.",
    }
    if mutation:
        mutation(payload)
    return payload


class C07Tests(unittest.TestCase):
    def prepare(self, root, path, action="--apply", writer="codex-module-central"):
        args = ["--data-root", str(root), "--project-id", PROJECT]
        if writer:
            args += ["--writer-id", writer]
        args += ["prepare", "--request", str(path), action]
        return invoke(C07, args)

    def record_advice(self, root, path, writer="codex-module-central"):
        return invoke(C07, ["--data-root", str(root), "--project-id", PROJECT, "--writer-id", writer, "record-advice", "--request-id", REQUEST, "--advice", str(path)])

    def record_decision(self, root, path, writer="codex-module-central"):
        return invoke(C07, ["--data-root", str(root), "--project-id", PROJECT, "--writer-id", writer, "record-boss-decision", "--request-id", REQUEST, "--decision", str(path)])

    def prepare_request(self, root, payload=None):
        path = write_json(root / "c07-inputs" / "request.json", payload or request_payload())
        return self.prepare(root, path)

    def test_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_project(root)
            path = write_json(root / "c07-inputs" / "request.json", request_payload())
            code, output = self.prepare(root, path, "--dry-run", None)
            self.assertEqual(code, 0, output)
            self.assertFalse(output["writePerformed"])
            self.assertFalse((root / "adjudication-requests").exists())

    def test_request_records_reference_without_changing_task_or_occupancy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_project(root)
            before = json.loads((root / "module-ledgers" / PROJECT / "ledger.json").read_text())
            code, output = self.prepare_request(root)
            self.assertEqual(code, 0, output)
            after = json.loads((root / "module-ledgers" / PROJECT / "ledger.json").read_text())
            self.assertEqual(after["tasks"][TASK]["status"], "BLOCKED")
            self.assertEqual(after["objectOccupancies"], before["objectOccupancies"])
            self.assertEqual(after["adjudications"][REQUEST]["stage"], "REQUESTED")

    def test_advice_goes_to_boss_only_and_is_not_authorization(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_project(root)
            self.prepare_request(root)
            advice = write_json(root / "c07-inputs" / "advice.json", advice_payload())
            code, output = self.record_advice(root, advice)
            self.assertEqual(code, 0, output)
            self.assertTrue(output["adviceSentToBossOnly"])
            self.assertFalse(output["taskWindowHandoffCreated"])
            artifact = json.loads((root / "adjudication-requests" / PROJECT / REQUEST / "advice.json").read_text())
            self.assertEqual(artifact["routingBoundary"]["recipient"], "boss")
            self.assertFalse(artifact["routingBoundary"]["adviceIsAuthorization"])

    def test_boss_decision_returns_only_to_original_window_without_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_project(root)
            self.prepare_request(root)
            advice = write_json(root / "c07-inputs" / "advice.json", advice_payload())
            self.record_advice(root, advice)
            decision = write_json(root / "c07-inputs" / "decision.json", decision_payload())
            code, output = self.record_decision(root, decision)
            self.assertEqual(code, 0, output)
            self.assertEqual(output["originalWindowId"], WINDOW)
            self.assertFalse(output["automaticExecutionAllowed"])
            ledger = json.loads((root / "module-ledgers" / PROJECT / "ledger.json").read_text())
            self.assertEqual(ledger["tasks"][TASK]["status"], "BLOCKED")
            handoff = json.loads((root / "adjudication-requests" / PROJECT / REQUEST / "task-window-handoff.json").read_text())
            self.assertEqual(handoff["windowId"], WINDOW)
            self.assertTrue(handoff["boundary"]["recipientMustBeOriginalWindow"])

    def test_advice_cannot_choose_an_option_outside_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_project(root)
            self.prepare_request(root)
            advice = advice_payload(lambda value: value["recommendation"].update({"optionId": "option-outside"}))
            path = write_json(root / "c07-inputs" / "advice.json", advice)
            code, output = self.record_advice(root, path)
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "C07_RECOMMENDED_OPTION_OUTSIDE_REQUEST")

    def test_boss_decision_cannot_choose_an_option_outside_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_project(root)
            self.prepare_request(root)
            advice = write_json(root / "c07-inputs" / "advice.json", advice_payload())
            self.record_advice(root, advice)
            decision = write_json(root / "c07-inputs" / "decision.json", decision_payload(selected="option-outside"))
            code, output = self.record_decision(root, decision)
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "C07_BOSS_SELECTED_OPTION_OUTSIDE_REQUEST")

    def test_task_window_cannot_write_governance_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_project(root)
            path = write_json(root / "c07-inputs" / "request.json", request_payload())
            code, output = self.prepare(root, path, "--apply", WINDOW)
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "C07_WRITER_NOT_AUTHORIZED")

    def test_sensitive_or_location_data_is_rejected(self):
        for bad_text in ("password=fictional-secret", "https://example.invalid/private", "/private/absolute/path"):
            with self.subTest(bad_text=bad_text), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "private-data"
                setup_project(root)
                payload = request_payload(lambda value: value.update({"question": bad_text}))
                path = write_json(root / "c07-inputs" / "request.json", payload)
                code, output = self.prepare(root, path)
                self.assertEqual(code, 2)
                self.assertEqual(output["reason"], "C07_INPUT_CONTAINS_SENSITIVE_OR_LOCATION_DATA")

    def test_missing_advice_blocks_boss_decision(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_project(root)
            self.prepare_request(root)
            decision = write_json(root / "c07-inputs" / "decision.json", decision_payload())
            code, output = self.record_decision(root, decision)
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "C07_ADVICE_NOT_FOUND_OR_INVALID")

    def test_request_advice_and_decision_are_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_project(root)
            request = write_json(root / "c07-inputs" / "request.json", request_payload())
            self.prepare(root, request)
            code, duplicate = self.prepare(root, request)
            self.assertEqual(code, 0, duplicate)
            self.assertEqual(duplicate["status"], "IDEMPOTENT_REQUEST")
            advice = write_json(root / "c07-inputs" / "advice.json", advice_payload())
            self.record_advice(root, advice)
            code, duplicate = self.record_advice(root, advice)
            self.assertEqual(code, 0, duplicate)
            self.assertEqual(duplicate["status"], "IDEMPOTENT_ADVICE")
            decision = write_json(root / "c07-inputs" / "decision.json", decision_payload())
            self.record_decision(root, decision)
            code, duplicate = self.record_decision(root, decision)
            self.assertEqual(code, 0, duplicate)
            self.assertEqual(duplicate["status"], "IDEMPOTENT_BOSS_DECISION")

    def test_tampering_is_detected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_project(root)
            self.prepare_request(root)
            path = root / "adjudication-requests" / PROJECT / REQUEST / "request.json"
            payload = json.loads(path.read_text())
            payload["question"] = "Tampered fictional question."
            write_json(path, payload)
            code, output = invoke(C07, ["--data-root", str(root), "--project-id", PROJECT, "verify", "--request-id", REQUEST])
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "C07_REQUEST_INTEGRITY_INVALID")

    def test_full_chain_verification_preserves_blocked_status(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private-data"
            setup_project(root)
            self.prepare_request(root)
            advice = write_json(root / "c07-inputs" / "advice.json", advice_payload())
            self.record_advice(root, advice)
            decision = write_json(root / "c07-inputs" / "decision.json", decision_payload())
            self.record_decision(root, decision)
            code, output = invoke(C07, ["--data-root", str(root), "--project-id", PROJECT, "verify", "--request-id", REQUEST])
            self.assertEqual(code, 0, output)
            self.assertEqual(output["stage"], "BOSS_DECIDED")
            self.assertEqual(output["taskStatus"], "BLOCKED")
            self.assertFalse(output["taskStatusChangedByC07"])


if __name__ == "__main__":
    unittest.main()
