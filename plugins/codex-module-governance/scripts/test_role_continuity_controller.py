#!/usr/bin/env python3
"""C08 role-continuity regression tests with fictional private data only."""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).parent
C02 = SCRIPTS / "initialize_project.py"
C03 = SCRIPTS / "ledger_manager.py"
C08C = SCRIPTS / "role_continuity_controller.py"
PROJECT = "continuity-demo-project"
TASK = "C-003"
WINDOW = "window-c003-g1"


def invoke(script, arguments):
    completed = subprocess.run([sys.executable, str(script), *arguments], check=False, capture_output=True, text=True)
    return completed.returncode, json.loads(completed.stdout)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def c03(root, command, *args):
    return invoke(C03, ["--data-root", str(root), "--project-id", PROJECT, "--writer-id", "codex-module-central", command, *args])


def continuity(root, command, *args, writer=True):
    base = ["--data-root", str(root), "--project-id", PROJECT]
    if writer:
        base += ["--writer-id", "codex-module-central"]
    return invoke(C08C, [*base, command, *args])


def setup(root):
    code, output = invoke(C02, ["--data-root", str(root), "--project-id", PROJECT, "--display-name", "Continuity demo", "--scope-summary", "Fictional continuity only.", "--apply"])
    if code != 0:
        raise AssertionError(output)
    commands = [
        ("initialize", "--apply"),
        ("add-task", "--task-id", TASK, "--title", "业务名称", "--business-goal", "Validate fictional continuity.", "--plan-ref", "plan-c003"),
        ("register-window", "--window-id", WINDOW, "--task-id", TASK, "--context-mode", "NEW"),
        ("transition-task", "--task-id", TASK, "--to-status", "READY", "--reason", "Ready."),
        ("transition-task", "--task-id", TASK, "--to-status", "IN_PROGRESS", "--reason", "Started."),
    ]
    for command in commands:
        code, output = c03(root, command[0], *command[1:])
        if code != 0:
            raise AssertionError(output)
    code, output = continuity(root, "initialize", "--central-thread-ref", "central-thread-g1", "--runtime-project-id", "runtime-project-001", "--execution-map-ref", "execution-map-001")
    if code != 0:
        raise AssertionError(output)


def handover_request(handover_id, role, mode):
    return {
        "handoverSchemaVersion": "0.15.0", "recordType": "C08_ROLE_HANDOVER_REQUEST",
        "handoverId": handover_id, "projectId": PROJECT, "role": role, "mode": mode,
        "reasonRef": f"reason-{handover_id}", "executionMapRef": "execution-map-001",
        "contextRefs": ["construction-outline-001", "ledger-latest"],
        "nextExpectedSignals": ["next-task-event"],
    }


def prepare_handover(root, handover_id, role, mode):
    request = write(root / "continuity-inputs" / f"{handover_id}-request.json", handover_request(handover_id, role, mode))
    code, output = continuity(root, "prepare-handover", "--request", str(request))
    package_path = root / "role-continuity" / PROJECT / "handovers" / handover_id / "handover-package.json"
    return code, output, package_path


def activate(root, handover_id, role, successor, package_path):
    package = json.loads(package_path.read_text(encoding="utf-8"))
    payload = {
        "activationSchemaVersion": "0.15.0", "recordType": "C08_ROLE_SUCCESSOR_ACTIVATION",
        "handoverId": handover_id, "projectId": PROJECT, "role": role,
        "successorThreadRef": successor, "runtimeProjectId": "runtime-project-001",
        "model": "gpt-5.6-sol", "handoverPackageDigest": digest(package),
    }
    path = write(root / "continuity-inputs" / f"{handover_id}-activation.json", payload)
    return continuity(root, "activate-successor", "--handover-id", handover_id, "--activation", str(path))


def event_payload(route_revision=1, event_id="event-c003-001", canonical_title="C-003｜业务名称", event_type="READY_FOR_VALIDATION"):
    return {
        "eventSchemaVersion": "0.15.0", "recordType": "C08_TASK_EVENT_INPUT", "eventId": event_id,
        "projectId": PROJECT, "taskIdentity": {"taskId": TASK, "canonicalTitle": canonical_title},
        "eventType": event_type,
        "sourceWindow": {"windowId": WINDOW, "runtimeThreadRef": WINDOW, "generation": 1},
        "targetRole": "CURRENT_CENTRAL", "routeRevisionSeen": route_revision,
        "decisionRef": None, "evidenceRefs": ["evidence-c003-001"], "summary": "Fictional task is ready for validation.",
    }


class RoleContinuityTests(unittest.TestCase):
    def test_first_adjudication_forks_central_and_later_has_one_current_role(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root)
            code, output, package = prepare_handover(root, "handover-adjudication-g1", "CURRENT_ADJUDICATION", "BOOTSTRAP_ADJUDICATION_FROM_CENTRAL")
            self.assertEqual(code, 0, output)
            code, output = activate(root, "handover-adjudication-g1", "CURRENT_ADJUDICATION", "adjudication-thread-g1", package)
            self.assertEqual(code, 0, output)
            routing = json.loads((root / "role-continuity" / PROJECT / "routing.json").read_text())
            self.assertEqual(routing["roles"]["CURRENT_CENTRAL"]["activeThreadRef"], "central-thread-g1")
            self.assertEqual(routing["roles"]["CURRENT_ADJUDICATION"]["activeThreadRef"], "adjudication-thread-g1")
            code, output, _ = prepare_handover(root, "handover-adjudication-duplicate", "CURRENT_ADJUDICATION", "BOOTSTRAP_ADJUDICATION_FROM_CENTRAL")
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "C08C_ADJUDICATION_BOOTSTRAP_NOT_ALLOWED")

    def test_durable_event_follows_current_central_after_replacement(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root)
            path = write(root / "continuity-inputs" / "event.json", event_payload())
            code, queued = continuity(root, "submit-event", "--event", str(path), writer=False)
            self.assertEqual(code, 0, queued)
            code, output, package = prepare_handover(root, "handover-central-g2", "CURRENT_CENTRAL", "REPLACE_ACTIVE_ROLE")
            self.assertEqual(code, 0, output)
            code, output = activate(root, "handover-central-g2", "CURRENT_CENTRAL", "central-thread-g2", package)
            self.assertEqual(code, 0, output)
            code, old = continuity(root, "list-pending", "--current-thread-ref", "central-thread-g1", writer=False)
            self.assertEqual(code, 2); self.assertEqual(old["reason"], "C08C_CALLER_IS_NOT_CURRENT_ROLE")
            code, current = continuity(root, "list-pending", "--current-thread-ref", "central-thread-g2", writer=False)
            self.assertEqual(code, 0, current); self.assertEqual(current["pendingCount"], 1)
            code, ack = continuity(root, "acknowledge-event", "--event-id", "event-c003-001", "--current-thread-ref", "central-thread-g2", "--acknowledgement-ref", "central-ledger-update-001")
            self.assertEqual(code, 0, ack); self.assertEqual(ack["centralGeneration"], 2)

    def test_in_scope_sub_agent_append_request_reaches_current_central(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root)
            event = event_payload(event_id="event-c003-append", event_type="SUB_AGENT_APPEND_REQUEST")
            event["decisionRef"] = "append-c10-001"; event["summary"] = "A private C10 append request is ready for central review."
            path = write(root / "continuity-inputs" / "append-event.json", event)
            code, output = continuity(root, "submit-event", "--event", str(path), writer=False)
            self.assertEqual(code, 0, output); self.assertEqual(output["eventType"], "SUB_AGENT_APPEND_REQUEST")

    def test_handover_refuses_stale_ledger_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root)
            code, output, package = prepare_handover(root, "handover-stale", "CURRENT_CENTRAL", "REPLACE_ACTIVE_ROLE")
            self.assertEqual(code, 0, output)
            code, changed = c03(root, "transition-task", "--task-id", TASK, "--to-status", "PARTIAL", "--reason", "Changed during handover.")
            self.assertEqual(code, 0, changed)
            code, output = activate(root, "handover-stale", "CURRENT_CENTRAL", "central-thread-g2", package)
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "C08C_HANDOVER_SNAPSHOT_STALE")

    def test_handover_refuses_event_arriving_after_package_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root)
            code, output, package = prepare_handover(root, "handover-late-event", "CURRENT_CENTRAL", "REPLACE_ACTIVE_ROLE")
            self.assertEqual(code, 0, output)
            event = write(root / "continuity-inputs" / "late-event.json", event_payload())
            code, output = continuity(root, "submit-event", "--event", str(event), writer=False)
            self.assertEqual(code, 0, output)
            code, output = activate(root, "handover-late-event", "CURRENT_CENTRAL", "central-thread-g2", package)
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "C08C_HANDOVER_SNAPSHOT_STALE")

    def test_task_identity_and_future_route_revision_are_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root)
            wrong = write(root / "continuity-inputs" / "wrong-title.json", event_payload(canonical_title="C-999｜Wrong"))
            code, output = continuity(root, "submit-event", "--event", str(wrong), writer=False)
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "C08C_TASK_IDENTITY_MISMATCH")
            future = write(root / "continuity-inputs" / "future.json", event_payload(route_revision=99, event_id="event-c003-future"))
            code, output = continuity(root, "submit-event", "--event", str(future), writer=False)
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "C08C_TASK_EVENT_FUTURE_ROUTE_REVISION")

    def test_event_is_idempotent_but_event_id_collision_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root)
            value = event_payload(); path = write(root / "continuity-inputs" / "event.json", value)
            self.assertEqual(continuity(root, "submit-event", "--event", str(path), writer=False)[0], 0)
            code, duplicate = continuity(root, "submit-event", "--event", str(path), writer=False)
            self.assertEqual(code, 0); self.assertEqual(duplicate["status"], "IDEMPOTENT_TASK_EVENT")
            value["summary"] = "Different content with same event id."
            collision = write(root / "continuity-inputs" / "collision.json", value)
            code, output = continuity(root, "submit-event", "--event", str(collision), writer=False)
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "C08C_EVENT_ID_REUSED")

    def test_routing_receipt_tampering_is_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"; setup(root)
            receipt = root / "role-continuity" / PROJECT / "receipts" / "receipt-000001-routing.json"
            value = json.loads(receipt.read_text()); value["operation"] = "TAMPERED"; write(receipt, value)
            code, output = continuity(root, "verify", writer=False)
            self.assertEqual(code, 2); self.assertEqual(output["reason"], "C08C_ROUTING_RECEIPT_CHAIN_INVALID")


if __name__ == "__main__":
    unittest.main(verbosity=2)
