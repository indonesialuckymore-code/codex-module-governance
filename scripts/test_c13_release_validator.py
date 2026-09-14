#!/usr/bin/env python3
"""C13 deterministic tests with temporary fictional package trees."""

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("c13-release-validator.py")
SPEC = importlib.util.spec_from_file_location("c13_release_validator", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(module)


REPO = SCRIPT.parents[1]


def fictional_old(repo: Path) -> None:
    shutil.copytree(REPO / "plugins" / module.PLUGIN, repo / "plugins" / module.PLUGIN)
    (repo / ".agents" / "plugins").mkdir(parents=True)
    shutil.copy2(REPO / ".agents" / "plugins" / "marketplace.json", repo / ".agents" / "plugins" / "marketplace.json")
    manifest_path = repo / "plugins" / module.PLUGIN / ".codex-plugin" / "plugin.json"
    value = json.loads(manifest_path.read_text(encoding="utf-8")); value["version"] = "0.13.0"
    manifest_path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class C13Tests(unittest.TestCase):
    def test_clean_install_and_duplicate_refusal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); result = module.install(REPO, root / "install")
            self.assertEqual(result["status"], "CLEAN_INSTALL_VERIFIED")
            with self.assertRaisesRegex(module.C13Error, "TARGET_ALREADY_EXISTS"):
                module.install(REPO, root / "install")

    def test_upgrade_and_rollback_preserve_previous_version(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); old = root / "old"; fictional_old(old)
            installed = root / "installed" / module.PLUGIN; installed.parent.mkdir(); shutil.copytree(module.plugin_root(old), installed)
            upgraded = module.upgrade(installed, REPO, root / "backups", False)
            self.assertEqual(upgraded["fromVersion"], "0.13.0"); self.assertEqual(upgraded["toVersion"], module.manifest(REPO)["version"])
            rolled = module.rollback(installed, Path(upgraded["backupPath"]), [root])
            self.assertEqual(rolled["toVersion"], "0.13.0")

    def test_failed_upgrade_keeps_old_program(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); old = root / "old"; fictional_old(old)
            installed = root / "installed" / module.PLUGIN; installed.parent.mkdir(); shutil.copytree(module.plugin_root(old), installed)
            before = module.tree_digest(installed)
            with self.assertRaisesRegex(module.C13Error, "PREVIOUS_VERSION_PRESERVED"):
                module.upgrade(installed, REPO, root / "backups", True)
            self.assertEqual(before, module.tree_digest(installed))

    def test_private_data_outside_plugin_is_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); old = root / "old"; fictional_old(old)
            installed = root / "installed" / module.PLUGIN; installed.parent.mkdir(); shutil.copytree(module.plugin_root(old), installed)
            private = root / "private" / "ledger.json"; private.parent.mkdir(); private.write_text('{"fictional":"preserve"}\n', encoding="utf-8"); before = private.read_bytes()
            upgraded = module.upgrade(installed, REPO, root / "backups", False); module.rollback(installed, Path(upgraded["backupPath"]), [root / 'private'])
            self.assertEqual(before, private.read_bytes())

    def test_new_work_blocks_incompatible_rollback_without_deleting_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); old = root / 'old'; fictional_old(old)
            installed = root / 'installed' / module.PLUGIN
            installed.parent.mkdir(); shutil.copytree(module.plugin_root(old), installed)
            upgraded = module.upgrade(installed, REPO, root / 'backups', False)
            private = root / 'private'; ledger = private / 'module-ledgers/fictional-project/ledger.json'
            ledger.parent.mkdir(parents=True)
            ledger.write_text(json.dumps({'tasks': {'C-new': {'externalWaits': {'wait-new': {'status': 'WAITING'}}}}}))
            before = ledger.read_bytes(); program = module.tree_digest(installed)
            with self.assertRaisesRegex(module.C13Error, 'ALL_PRIVATE_DATA_ROOTS_REQUIRED'):
                module.rollback(installed, Path(upgraded['backupPath']))
            with self.assertRaisesRegex(module.C13Error, 'NEW_WORK_REQUIRES_CURRENT_READER'):
                module.rollback(installed, Path(upgraded['backupPath']), [private])
            self.assertEqual(before, ledger.read_bytes()); self.assertEqual(program, module.tree_digest(installed))

    def test_marketplace_name_is_project_specific(self):
        market = module.marketplace(REPO)
        self.assertEqual(market["name"], module.MARKETPLACE); self.assertNotEqual(market["name"], "personal")


if __name__ == "__main__":
    unittest.main(verbosity=2)
