import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from ledger_manager import ledger_lock, LedgerError


class ProcessLockTests(unittest.TestCase):
    def test_killed_owner_is_recovered_but_live_owner_is_not(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            code = "from pathlib import Path; from ledger_manager import ledger_lock; import sys; "
            code += "\nwith ledger_lock(Path(sys.argv[1]), 'fictional-project'):\n print('locked', flush=True)\n sys.stdin.read()"
            child = subprocess.Popen([sys.executable, '-B', '-c', code, str(root)], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, text=True, cwd=Path(__file__).parent)
            try:
                self.assertEqual('locked', child.stdout.readline().strip())
                with self.assertRaises(LedgerError):
                    with ledger_lock(root, 'fictional-project'): pass
                child.kill(); child.wait(timeout=5)
                with ledger_lock(root, 'fictional-project'): pass
                marker = root / 'module-ledgers/.locks/fictional-project.lock'
                marker.write_text(json.dumps({'pid': os.getpid()}))
                with self.assertRaises(LedgerError):
                    with ledger_lock(root, 'fictional-project'): pass
                self.assertTrue(marker.exists())
            finally:
                if child.poll() is None: child.kill(); child.wait(timeout=5)
                child.stdin.close(); child.stdout.close()


if __name__ == '__main__': unittest.main()
