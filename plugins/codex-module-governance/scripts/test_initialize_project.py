#!/usr/bin/env python3
"""Regression tests for the C02 initializer. Uses only temporary private directories."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("initialize_project.py")


def invoke(arguments):
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.returncode, json.loads(completed.stdout)


class InitializeProjectTests(unittest.TestCase):
    def arguments(self, data_root, action, project_id="demo-project", display_name="Demo Project"):
        return [
            "--data-root", str(data_root),
            "--project-id", project_id,
            "--display-name", display_name,
            "--scope-summary", "Fictional isolated validation project only.",
            action,
        ]

    def test_dry_run_creates_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "private-data"
            code, output = invoke(self.arguments(data_root, "--dry-run"))
            self.assertEqual(code, 0)
            self.assertEqual(output["status"], "READY_FOR_BOSS_APPROVAL")
            self.assertFalse(data_root.exists())

    def test_apply_creates_only_startup_records(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "private-data"
            code, output = invoke(self.arguments(data_root, "--apply"))
            self.assertEqual(code, 0)
            self.assertEqual(output["status"], "PROJECT_REGISTERED")

            card_path = data_root / "project-registry" / "project-cards" / "demo-project" / "project-card.json"
            receipt_path = card_path.with_name("initialization-receipt.json")
            index_path = data_root / "project-registry" / "projects-index.json"
            self.assertTrue(card_path.is_file())
            self.assertTrue(receipt_path.is_file())
            self.assertTrue(index_path.is_file())
            card = json.loads(card_path.read_text(encoding="utf-8"))
            self.assertEqual(card["state"], "PROJECT_REGISTERED")
            self.assertEqual(card["executionBoundary"], {
                "dispatchAllowed": False,
                "testCreationAllowed": False,
                "businessWriteAllowed": False,
            })
            self.assertEqual(card["nextRequiredDecision"], "C03_ENGINEERING_LEDGER_GOVERNANCE_REQUIRED")

    def test_existing_id_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "private-data"
            first_code, _ = invoke(self.arguments(data_root, "--apply"))
            second_code, output = invoke(self.arguments(data_root, "--apply"))
            self.assertEqual(first_code, 0)
            self.assertEqual(second_code, 3)
            self.assertEqual(output["action"], "REUSE_EXISTING_PROJECT_CARD")

    def test_similar_name_requires_review(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "private-data"
            first_code, _ = invoke(self.arguments(data_root, "--apply", "inventory-core", "Inventory Core"))
            second_code, output = invoke(self.arguments(data_root, "--dry-run", "inventory-core-v2", "Inventory Core V2"))
            self.assertEqual(first_code, 0)
            self.assertEqual(second_code, 3)
            self.assertEqual(output["action"], "REVIEW_REUSE_OR_MIGRATE")

    def test_git_worktree_data_root_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            git_root = Path(temporary) / "fake-git-root"
            (git_root / ".git").mkdir(parents=True)
            code, output = invoke(self.arguments(git_root / "private-data", "--apply"))
            self.assertEqual(code, 2)
            self.assertEqual(output["reason"], "PRIVATE_DATA_ROOT_IS_INSIDE_GIT_WORKTREE")

    def test_product_repository_is_refused_as_data_root(self):
        product_repository = SCRIPT.parents[3]
        code, output = invoke(self.arguments(product_repository, "--apply"))
        self.assertEqual(code, 2)
        self.assertEqual(output["reason"], "PRIVATE_DATA_ROOT_OVERLAPS_PRODUCT_REPOSITORY")

    def test_private_config_can_supply_data_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            data_root = temporary_root / "private-data"
            config_path = temporary_root / "module-config.json"
            config_path.write_text(
                json.dumps({"storage": {"userDataRoot": str(data_root)}}),
                encoding="utf-8",
            )
            arguments = [
                "--config", str(config_path),
                "--project-id", "config-project",
                "--display-name", "Config Project",
                "--scope-summary", "Fictional isolated validation project only.",
                "--dry-run",
            ]
            code, output = invoke(arguments)
            self.assertEqual(code, 0)
            self.assertEqual(output["status"], "READY_FOR_BOSS_APPROVAL")
            self.assertFalse(data_root.exists())


if __name__ == "__main__":
    unittest.main()
