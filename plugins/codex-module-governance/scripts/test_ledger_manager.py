#!/usr/bin/env python3
"""Regression tests for C03. Every test uses a temporary, non-Git data directory."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIRECTORY = Path(__file__).parent
LEDGER_SCRIPT = SCRIPT_DIRECTORY / "ledger_manager.py"
C02_SCRIPT = SCRIPT_DIRECTORY / "initialize_project.py"
PROJECT_ID = "demo-project"


def invoke(script, arguments):
    completed = subprocess.run(
        [sys.executable, str(script), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.returncode, json.loads(completed.stdout)


def create_c02_project(data_root):
    arguments = [
        "--data-root", str(data_root),
        "--project-id", PROJECT_ID,
        "--display-name", "Demo Project",
        "--scope-summary", "Fictional isolated validation project only.",
        "--apply",
    ]
    code, output = invoke(C02_SCRIPT, arguments)
    if code != 0:
        raise AssertionError(output)


class LedgerManagerTests(unittest.TestCase):
    def command(self, data_root, command, *arguments, writer="codex-module-central", caller=None):
        base = ["--data-root", str(data_root), "--project-id", PROJECT_ID]
        if writer is not None:
            base.extend(["--writer-id", writer])
        if caller is not None:
            base.extend(["--caller-thread-ref", caller])
        return invoke(LEDGER_SCRIPT, [*base, command, *arguments])

    def initialize(self, data_root):
        create_c02_project(data_root)
        code, output = self.command(data_root, "initialize", "--apply")
        self.assertEqual(code, 0, output)
        self.assertEqual(output["status"], "C03_LEDGER_INITIALIZED")

    def add_task(self, data_root, task_id="C-07", writer="codex-module-central"):
        return self.command(
            data_root,
            "add-task",
            "--task-id", task_id,
            "--title", "Fictional task",
            "--business-goal", "Validate isolated ledger controls only.",
            "--plan-ref", "plan-ref-001",
            writer=writer,
        )

    def test_c02_card_migrates_to_a_module_only_ledger(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "private-data"
            create_c02_project(data_root)
            dry_code, dry_output = self.command(data_root, "initialize", "--dry-run")
            apply_code, apply_output = self.command(data_root, "initialize", "--apply")
            verify_code, verify_output = self.command(data_root, "verify", writer=None)
            self.assertEqual(dry_code, 0)
            self.assertEqual(dry_output["action"], "MIGRATE_C02_STARTUP_CARD_TO_C03_LEDGER")
            self.assertEqual(apply_code, 0)
            self.assertEqual(apply_output["writer"], "codex-module-central")
            self.assertEqual(verify_code, 0)
            self.assertEqual(verify_output["receiptCount"], 1)

    def test_only_module_central_can_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "private-data"
            self.initialize(data_root)
            code, output = self.add_task(data_root, writer="fable-5-system-router")
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "LEDGER_WRITER_NOT_AUTHORIZED")

    def test_only_c08_current_central_thread_can_write_after_routing_activation(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "private-data"
            self.initialize(data_root)
            routing_directory = data_root / "role-continuity" / PROJECT_ID
            routing_directory.mkdir(parents=True)
            (routing_directory / "routing.json").write_text(json.dumps({
                "recordType": "C08_ROLE_ROUTING",
                "projectId": PROJECT_ID,
                "roles": {
                    "CURRENT_CENTRAL": {
                        "status": "ACTIVE",
                        "activeThreadRef": "thread-current-central",
                    },
                    "CURRENT_ADJUDICATION": None,
                },
            }), encoding="utf-8")

            missing_code, missing_output = self.add_task(data_root, "C-08")
            wrong_code, wrong_output = self.command(
                data_root, "add-task",
                "--task-id", "C-09", "--title", "Wrong central",
                "--business-goal", "Must not mutate the ledger.",
                "--plan-ref", "plan-ref-009",
                caller="thread-bootstrap-launcher",
            )
            right_code, right_output = self.command(
                data_root, "add-task",
                "--task-id", "C-10", "--title", "Current central",
                "--business-goal", "Authorized current central mutation.",
                "--plan-ref", "plan-ref-010",
                caller="thread-current-central",
            )

            self.assertEqual(missing_code, 2)
            self.assertEqual(missing_output["reason"], "LEDGER_CALLER_THREAD_REQUIRED")
            self.assertEqual(wrong_code, 2)
            self.assertEqual(wrong_output["reason"], "LEDGER_CALLER_NOT_CURRENT_CENTRAL")
            self.assertEqual(right_code, 0, right_output)
            ledger = json.loads((data_root / "module-ledgers" / PROJECT_ID / "ledger.json").read_text(encoding="utf-8"))
            self.assertEqual(ledger["revision"], 1)
            self.assertEqual(set(ledger["tasks"]), {"C-10"})

    def test_invalid_c08_routing_without_a_current_central_is_a_safe_refusal(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "private-data"
            self.initialize(data_root)
            routing_directory = data_root / "role-continuity" / PROJECT_ID
            routing_directory.mkdir(parents=True)
            (routing_directory / "routing.json").write_text(json.dumps({
                "recordType": "C08_ROLE_ROUTING",
                "projectId": PROJECT_ID,
                "roles": {"CURRENT_ADJUDICATION": None},
            }), encoding="utf-8")

            code, output = self.command(
                data_root, "add-task",
                "--task-id", "C-08", "--title", "Invalid routing",
                "--business-goal", "Reject corrupt routing without crashing.",
                "--plan-ref", "plan-ref-008",
                caller="thread-current-central",
            )

            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "C08_CURRENT_CENTRAL_ROUTING_INVALID")

    def test_completion_signal_is_idempotent_and_needs_review(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "private-data"
            self.initialize(data_root)
            add_code, _ = self.add_task(data_root)
            ready_code, _ = self.command(data_root, "transition-task", "--task-id", "C-07", "--to-status", "READY", "--reason", "Plan ready")
            progress_code, _ = self.command(data_root, "transition-task", "--task-id", "C-07", "--to-status", "IN_PROGRESS", "--reason", "Boss approved dispatch")
            signal_code, signal_output = self.command(data_root, "record-completion-signal", "--task-id", "C-07", "--signal-id", "signal-001")
            duplicate_code, duplicate_output = self.command(data_root, "record-completion-signal", "--task-id", "C-07", "--signal-id", "signal-001")
            read_code, read_output = self.command(data_root, "read-summary", writer=None)
            self.assertEqual(add_code, 0)
            self.assertEqual(ready_code, 0)
            self.assertEqual(progress_code, 0)
            self.assertEqual(signal_code, 0)
            self.assertEqual(signal_output["operation"], "RECORD_COMPLETION_SIGNAL")
            self.assertEqual(duplicate_code, 0)
            self.assertEqual(duplicate_output["status"], "IDEMPOTENT_DUPLICATE_SIGNAL")
            self.assertEqual(read_code, 0)
            self.assertEqual(read_output["tasks"]["C-07"], "NEEDS_REVIEW")

    def test_done_is_refused_until_c06(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "private-data"
            self.initialize(data_root)
            self.add_task(data_root)
            code, output = self.command(data_root, "transition-task", "--task-id", "C-07", "--to-status", "DONE", "--reason", "Not enough evidence")
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "DONE_REQUIRES_C06_INDEPENDENT_VALIDATION")

    def test_cancel_unstarted_ready_task_releases_only_its_claims(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "private-data"
            self.initialize(data_root)
            self.add_task(data_root, "C-07")
            self.add_task(data_root, "C-08")
            self.command(data_root, "transition-task", "--task-id", "C-07", "--to-status", "READY", "--reason", "Ready")
            self.command(data_root, "claim-object", "--object-key", "file:cancel-me", "--owner-type", "task", "--owner-id", "C-07", "--intent", "WRITE")
            self.command(data_root, "claim-object", "--object-key", "file:keep-me", "--owner-type", "task", "--owner-id", "C-08", "--intent", "WRITE")

            code, output = self.command(
                data_root, "cancel-unstarted-task",
                "--task-id", "C-07",
                "--boss-decision-ref", "boss-priority-change-001",
                "--reason", "Boss reprioritized the unstarted task.",
            )

            self.assertEqual(code, 0, output)
            self.assertEqual(output["status"], "UNSTARTED_TASK_CANCELLED")
            self.assertEqual(output["releasedClaimCount"], 1)
            ledger = json.loads((data_root / "module-ledgers" / PROJECT_ID / "ledger.json").read_text(encoding="utf-8"))
            self.assertEqual(ledger["tasks"]["C-07"]["status"], "CANCELLED")
            self.assertNotIn("file:cancel-me", ledger["objectOccupancies"])
            self.assertIn("file:keep-me", ledger["objectOccupancies"])
            self.assertEqual(ledger["releasedOccupancies"][-1]["releaseType"], "CANCELLED_PRE_RUNTIME")

    def test_cancel_unstarted_task_refuses_active_runtime_window(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "private-data"
            self.initialize(data_root)
            self.add_task(data_root, "C-07")
            self.command(data_root, "transition-task", "--task-id", "C-07", "--to-status", "READY", "--reason", "Ready")
            self.command(data_root, "register-window", "--window-id", "window-active", "--task-id", "C-07", "--context-mode", "NEW")

            code, output = self.command(
                data_root, "cancel-unstarted-task",
                "--task-id", "C-07",
                "--boss-decision-ref", "boss-priority-change-001",
                "--reason", "Must refuse once a runtime window is active.",
            )

            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "CANCEL_UNSTARTED_TASK_HAS_ACTIVE_WINDOW")

    def test_object_conflict_preserves_both_claims_and_hard_stops(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "private-data"
            self.initialize(data_root)
            self.add_task(data_root, "C-07")
            self.add_task(data_root, "C-08")
            first_code, _ = self.command(data_root, "claim-object", "--object-key", "file:docs/example.md", "--owner-type", "task", "--owner-id", "C-07", "--intent", "WRITE")
            second_code, second_output = self.command(data_root, "claim-object", "--object-key", "file:docs/example.md", "--owner-type", "task", "--owner-id", "C-08", "--intent", "WRITE")
            verify_code, verify_output = self.command(data_root, "verify", writer=None)
            self.assertEqual(first_code, 0)
            self.assertEqual(second_code, 0)
            self.assertTrue(second_output["conflictCheckRequired"])
            self.assertEqual(verify_code, 0)
            self.assertEqual(verify_output["hardStops"][0]["code"], "OBJECT_OCCUPANCY_CONFLICT")
            ledger_path = data_root / "module-ledgers" / PROJECT_ID / "ledger.json"
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            occupancy = ledger["objectOccupancies"]["file:docs/example.md"]
            self.assertEqual(occupancy["status"], "CONFLICT")
            self.assertEqual(len(occupancy["claims"]), 2)

    def test_first_level_sub_agents_are_limited_to_three(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "private-data"
            self.initialize(data_root)
            self.add_task(data_root)
            window_code, _ = self.command(data_root, "register-window", "--window-id", "window-001", "--task-id", "C-07", "--context-mode", "NEW")
            self.assertEqual(window_code, 0)
            for identifier in ("agent-001", "agent-002", "agent-003"):
                code, _ = self.command(data_root, "register-sub-agent", "--sub-agent-id", identifier, "--window-id", "window-001", "--role", "Isolated validation role")
                self.assertEqual(code, 0)
            fourth_code, fourth_output = self.command(data_root, "register-sub-agent", "--sub-agent-id", "agent-004", "--window-id", "window-001", "--role", "Must be refused")
            self.assertEqual(fourth_code, 2)
            self.assertEqual(fourth_output["reason"], "FIRST_LEVEL_SUB_AGENT_LIMIT_REACHED")

    def test_manual_window_registration_preserves_native_thread_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "private-data"
            self.initialize(data_root)
            self.add_task(data_root)
            native_ref = "00000000-0000-7000-8000-000000000001"
            code, output = self.command(
                data_root, "register-window", "--window-id", "window-native",
                "--task-id", "C-07", "--context-mode", "NEW",
                "--runtime-thread-ref", native_ref,
                "--runtime-thread-evidence-ref", "native-identity-readback-001",
            )
            self.assertEqual(code, 0, output)
            ledger = json.loads((data_root / "module-ledgers" / PROJECT_ID / "ledger.json").read_text(encoding="utf-8"))
            self.assertEqual(ledger["windows"]["window-native"]["runtimeThreadRef"], native_ref)
            self.assertEqual(ledger["windows"]["window-native"]["runtimeThreadEvidenceRef"], "native-identity-readback-001")
            self.assertEqual(ledger["tasks"]["C-07"]["status"], "PLANNED")
            self.assertEqual(self.command(data_root, "verify")[0], 0)

    def test_manual_native_identity_requires_evidence_and_cannot_alias_another_window(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "private-data"
            self.initialize(data_root)
            self.add_task(data_root)
            args = ["--task-id", "C-07", "--context-mode", "NEW"]
            native_ref = "00000000-0000-7000-8000-000000000001"
            for extra in (["--runtime-thread-ref", native_ref], ["--runtime-thread-evidence-ref", "readback-001"]):
                code, output = self.command(data_root, "register-window", "--window-id", "window-incomplete", *args, *extra)
                self.assertEqual(code, 2, output)
                self.assertEqual(output["reason"], "WINDOW_RUNTIME_THREAD_EVIDENCE_INCOMPLETE")
            identity = ["--runtime-thread-ref", native_ref, "--runtime-thread-evidence-ref", "readback-001"]
            self.assertEqual(self.command(data_root, "register-window", "--window-id", "window-first", *args, *identity)[0], 0)
            ledger_path = data_root / "module-ledgers" / PROJECT_ID / "ledger.json"
            before = ledger_path.read_bytes()
            code, output = self.command(data_root, "register-window", "--window-id", "window-alias", *args, *identity)
            self.assertEqual(code, 2, output)
            self.assertEqual(output["reason"], "WINDOW_RUNTIME_THREAD_ALREADY_REGISTERED")
            self.assertEqual(ledger_path.read_bytes(), before)

    def test_manual_window_registration_accepts_full_access_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "private-data"
            self.initialize(data_root)
            self.add_task(data_root)
            code, output = self.command(
                data_root, "register-window", "--window-id", "window-full-access", "--task-id", "C-07", "--context-mode", "NEW",
                "--runtime-model-evidence-ref", "manual-terra-full-access",
                "--runtime-permission-profile", "full-access",
                "--runtime-permission-evidence-ref", "manual-permission-full-access",
                "--governance-data-root-access", "NOT_RESTRICTED",
            )
            self.assertEqual(code, 0, output)
            ledger = json.loads((data_root / "module-ledgers" / PROJECT_ID / "ledger.json").read_text(encoding="utf-8"))
            permission = ledger["windows"]["window-full-access"]["permissionEnforcement"]
            self.assertEqual(permission["permissionClass"], "FULL_ACCESS")
            self.assertEqual(permission["writableRoots"], [])
            self.assertEqual(permission["governanceDataRootAccess"], "NOT_RESTRICTED")

    def test_task_id_is_embedded_in_every_task_and_window_title(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "private-data"
            self.initialize(data_root)
            add_code, add_output = self.command(
                data_root, "add-task", "--task-id", "C-003", "--title", "C-003｜业务名称｜G9",
                "--business-goal", "Validate immutable identity.", "--plan-ref", "plan-ref-003",
            )
            first_code, first_output = self.command(data_root, "register-window", "--window-id", "window-003-a", "--task-id", "C-003", "--context-mode", "NEW")
            second_code, second_output = self.command(data_root, "register-window", "--window-id", "window-003-b", "--task-id", "C-003", "--context-mode", "NEW")
            self.assertEqual((add_code, first_code, second_code), (0, 0, 0), (add_output, first_output, second_output))
            ledger = json.loads((data_root / "module-ledgers" / PROJECT_ID / "ledger.json").read_text(encoding="utf-8"))
            self.assertEqual(ledger["tasks"]["C-003"]["title"], "业务名称")
            self.assertEqual(ledger["tasks"]["C-003"]["canonicalTitle"], "C-003｜业务名称")
            self.assertEqual(ledger["windows"]["window-003-a"]["runtimeTitle"], "C-003｜业务名称｜G1")
            self.assertEqual(ledger["windows"]["window-003-b"]["runtimeTitle"], "C-003｜业务名称｜G2")

    def test_receipt_tampering_is_detected(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "private-data"
            self.initialize(data_root)
            receipt_path = data_root / "module-ledgers" / PROJECT_ID / "receipts" / "receipt-000000-initialize.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["afterLedger"]["status"] = "TAMPERED"
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            code, output = self.command(data_root, "verify", writer=None)
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "IMMUTABLE_RECEIPT_CHAIN_INVALID")


if __name__ == "__main__":
    unittest.main()
