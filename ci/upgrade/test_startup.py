"""Exercise the real startup shell function without Docker or backend dependencies."""

import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("upgrade.sh").read_text()
STARTUP = SCRIPT[SCRIPT.index("lens_start() {") : SCRIPT.index("lens_stop() {")]
WAL_ERROR = (
    'File "app/domain/textindex.py", line 203, in __init__\n'
    '    self.con.execute("PRAGMA journal_mode=WAL")\n'
    "sqlite3.OperationalError: database is locked\n"
)


class StartupTests(unittest.TestCase):
    def run_startup(self, label="from", failures=1, error=WAL_ERROR, api_fails=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "fastapi_backend").mkdir()
            (root / "bin").mkdir()
            (root / "out").mkdir()
            programs = {
                "fastapi": """
import os, time
from pathlib import Path
root = Path(os.environ['TEST_ROOT'])
(root / 'api-started').touch()
if os.environ['API_FAILS'] == '1':
    raise SystemExit(1)
# Both processes must have started before the API can become ready.
while not (root / 'worker-started').exists():
    time.sleep(.005)
(root / 'api-ready').touch()
while True:
    time.sleep(1)
""",
                "lens": """
import os, time
from pathlib import Path
root = Path(os.environ['TEST_ROOT'])
count_file = root / 'attempts'
count = int(count_file.read_text()) + 1 if count_file.exists() else 1
count_file.write_text(str(count))
(root / 'worker-started').touch()
if count <= int(os.environ['FAILURES']):
    print(os.environ['WORKER_ERROR'], end='', flush=True)
    raise SystemExit(1)
print('Ctrl-C to stop', flush=True)
while True:
    time.sleep(1)
""",
                "curl": """
import os, time
from pathlib import Path
time.sleep(.03)
raise SystemExit(0 if (Path(os.environ['TEST_ROOT']) / 'api-ready').exists() else 1)
""",
            }
            for name, program in programs.items():
                binary = root / "bin" / name
                binary.write_text(f"#!{sys.executable}\n{program}")
                binary.chmod(0o755)
            script = (
                "set -euo pipefail\n"
                'OUT="$TEST_ROOT/out"; API_PORT=1; PIDS=()\n'
                "cleanup() { for p in \"${PIDS[@]}\"; do kill \"$p\" 2>/dev/null || true; done; wait || true; }\n"
                "trap cleanup EXIT\n"
                "sleep() { /bin/sleep .02; }\n"
                + STARTUP
                + f'lens_start {shlex.quote(label)} "$TEST_ROOT" "$TEST_ROOT"\n'
            )
            result = subprocess.run(
                ["bash", "-c", script],
                env={
                    **os.environ,
                    "PATH": f"{root / 'bin'}:{os.environ['PATH']}",
                    "TEST_ROOT": str(root),
                    "FAILURES": str(failures),
                    "WORKER_ERROR": error,
                    "API_FAILS": str(int(api_fails)),
                },
                text=True,
                capture_output=True,
                timeout=15,
            )
            attempts = root / "attempts"
            logs = {p.name: p.read_text() for p in (root / "out").iterdir()}
            return result, int(attempts.read_text()) if attempts.exists() else 0, logs

    def test_concurrent_start_without_retry(self):
        result, attempts, logs = self.run_startup(failures=0)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(attempts, 1)
        self.assertEqual(set(logs), {"from-api.log", "from-worker.log"})

    def test_baseline_wal_race_restarts_worker_and_preserves_log(self):
        result, attempts, logs = self.run_startup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(attempts, 2)
        self.assertEqual(logs["from-worker-attempt-1.log"], WAL_ERROR)
        self.assertEqual(logs["from-worker.log"], "Ctrl-C to stop\n")

    def test_persistent_baseline_wal_failure_is_bounded(self):
        result, attempts, logs = self.run_startup(failures=99)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(attempts, 3)
        self.assertEqual(logs["from-worker-attempt-2.log"], WAL_ERROR)
        self.assertEqual(logs["from-worker.log"], WAL_ERROR)

    def test_target_wal_failure_is_not_retried(self):
        result, attempts, logs = self.run_startup(label="to")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(attempts, 1)
        self.assertEqual(set(logs), {"to-api.log", "to-worker.log"})

    def test_unrelated_worker_failures_are_not_retried(self):
        for error in (
            "RuntimeError: failed migration\n",
            "sqlite3.OperationalError: database is locked\n",
            WAL_ERROR.replace("database is locked", "database disk image is malformed"),
            WAL_ERROR.replace("PRAGMA journal_mode=WAL", "CREATE TABLE example"),
        ):
            with self.subTest(error=error):
                result, attempts, _ = self.run_startup(error=error)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(attempts, 1)

    def test_dead_worker_with_stale_ready_message_fails(self):
        result, attempts, _ = self.run_startup(
            error="Ctrl-C to stop\nRuntimeError: fatal\n"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(attempts, 1)

    def test_api_failure_is_not_retried(self):
        result, attempts, _ = self.run_startup(api_fails=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertLessEqual(attempts, 1)


if __name__ == "__main__":
    unittest.main()
