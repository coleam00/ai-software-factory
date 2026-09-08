"""Real ordinary targets and ownership tests. No providers or workflows are launched."""
from __future__ import annotations
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "template/factory"))
from runtime_host import RuntimeHost, request, source_files, digest, command

TARGET = r'''
import json, os, sqlite3, subprocess, sys, time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
state = Path(os.environ['FACTORY_RUNTIME_STATE'])
(state / 'pid').write_text(str(os.getpid()))
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'])
(state / 'child').write_text(str(child.pid))
if os.environ.get('FAIL'):
    raise SystemExit(7)
db = sqlite3.connect(state / 'app.db')
db.execute('create table hits (value integer)')
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        if self.path == '/identity':
            value = 'wrong' if (state / 'wrong').exists() else os.environ['FACTORY_RUNTIME_CANDIDATE']
        elif self.path == '/hits':
            db.execute('insert into hits values (1)')
            value = str(db.execute('select count(*) from hits').fetchone()[0])
        else: value = 'healthy baseline'
        self.send_response(200)
        self.end_headers()
        self.wfile.write(value.encode())
HTTPServer(('127.0.0.1', int(os.environ['FACTORY_RUNTIME_PORT'])), Handler).serve_forever()
'''


def alive(pid):
    if os.name == "nt":
        import ctypes as c
        from ctypes import wintypes as w
        kernel = c.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        kernel.OpenProcess.restype = w.HANDLE
        kernel.GetExitCodeProcess.argtypes = [w.HANDLE, c.POINTER(w.DWORD)]
        kernel.CloseHandle.argtypes = [w.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = w.DWORD()
        kernel.GetExitCodeProcess(handle, c.byref(code))
        kernel.CloseHandle(handle)
        return code.value == 259
    try:
        os.kill(pid, 0)
        status = Path(f"/proc/{pid}/stat")
        return not status.exists() or status.read_text().split()[2] != "Z"
    except ProcessLookupError:
        return False


def settled(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(.05)
    return predicate()


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="runtime test ")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "application"
        self.root.mkdir()
        (self.root / "app.py").write_text(TARGET)
        self.cfg = {"version": 1, "roots": {"candidate": str(self.root)}, "include": ["app.py"],
                    "shape": "http", "command": [sys.executable, "app.py"], "timeout_s": 2,
                    "health_path": "/health", "identity_path": "/identity",
                    "mutations": [{"id": "negative", "file": "app.py", "find": "healthy baseline",
                                   "replace": "broken mutation"}]}
        self.config = self.base / "runtime.json"

    def host(self):
        self.config.write_text(json.dumps(self.cfg))
        return RuntimeHost(self.config)

    def start(self, host, **extra):
        return request("start", {"slot": "case", "root": "candidate", **extra}, host.connection)

    def get(self, item, path):
        with urlopen(item["target"] + path, timeout=2) as response:
            return response.read().decode()

    def pids(self, item):
        state = Path(item["state"])
        return [int((state / name).read_text()) for name in ("pid", "child")]

    def assert_clean(self, item, pids):
        self.assertTrue(settled(lambda: not any(alive(pid) for pid in pids)))
        self.assertFalse(Path(item["snapshot"]).parent.exists())

    def test_fresh_baseline_holdout_retry_mutation_and_finally(self):
        # Private evaluator data is neither copied nor changed.
        private = self.root / ".factory/holdout/HOLDOUT.md"
        private.parent.mkdir(parents=True)
        private.write_text("PRIVATE_EVALUATOR_SENTINEL")
        with self.host() as host:
            first = self.start(host)
            pids = self.pids(first)
            self.assertEqual(self.get(first, "/hits"), "1")
            self.assertEqual(self.get(first, "/hits"), "2")
            second = self.start(host)  # explicit shared-node retry/holdout call
            self.assert_clean(first, pids)
            self.assertNotEqual(first["candidate"], second["candidate"])
            self.assertNotEqual(first["state"], second["state"])
            self.assertNotEqual(first["target"], second["target"])
            self.assertEqual(self.get(second, "/hits"), "1")
            mutated = self.start(host, mutation="negative")
            self.assertEqual(self.get(mutated, "/health"), "broken mutation")
            self.assertNotEqual(second["source_digest"], mutated["source_digest"])
            self.assertEqual((self.root / "app.py").read_text(), TARGET)
            self.assertFalse((Path(mutated["snapshot"]) / ".factory").exists())
            self.assertNotIn(host.connection["token"], json.dumps(mutated))
            pids = self.pids(mutated)
        self.assert_clean(mutated, pids)
        self.assertEqual(private.read_text(), "PRIVATE_EVALUATOR_SENTINEL")

    def test_wrong_identity_and_dead_owned_target_refused(self):
        with self.host() as host:
            item = self.start(host)
            pids = self.pids(item)
            (Path(item["state"]) / "wrong").touch()
            with self.assertRaises(HTTPError):
                request("identity", {"slot": "case"}, host.connection)
            self.assert_clean(item, pids)
            item = self.start(host)
            pids = self.pids(item)
            os.kill(pids[0], signal.SIGTERM)
            self.assertTrue(settled(lambda: not alive(pids[0])))
            with self.assertRaises(HTTPError):
                request("identity", {"slot": "case"}, host.connection)
            self.assert_clean(item, pids)

    def test_stale_source_and_snapshot_changes_refused(self):
        with self.host() as host:
            expected = digest(source_files(self.root, ["app.py"]))
            (self.root / "app.py").write_text(TARGET + "\n# new candidate\n")
            with self.assertRaises(HTTPError):
                self.start(host, expected_source=expected)
            item = self.start(host)
            target = Path(item["snapshot"]) / "app.py"
            target.chmod(stat.S_IWRITE | stat.S_IREAD)
            target.write_text("changed after launch")
            pids = self.pids(item)
            with self.assertRaises(HTTPError):
                request("identity", {"slot": "case"}, host.connection)
            self.assert_clean(item, pids)

    def test_malformed_retry_cleans_existing_and_never_executes_caller_command(self):
        with self.host() as host:
            item = self.start(host)
            pids = self.pids(item)
            with self.assertRaises(HTTPError):
                self.start(host, command=["codex", "SENTINEL_SECRET"])
            self.assert_clean(item, pids)
            item = self.start(host)
            pids = self.pids(item)
            with self.assertRaises(HTTPError):
                request("start", ["SENTINEL_SECRET"], host.connection)
            self.assert_clean(item, pids)

    def test_readiness_failure_and_failed_setup_clean_descendants(self):
        for setup in (False, True):
            with self.subTest(setup=setup):
                external = self.base / "pids.json"
                code = ("import json,os,subprocess,sys,time; from pathlib import Path; "
                        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(300)']); "
                        f"Path({str(external)!r}).write_text(json.dumps([os.getpid(),p.pid,os.getcwd()])); "
                        + ("sys.exit(3)" if setup else "time.sleep(300)"))
                self.cfg["setup" if setup else "command"] = [sys.executable, "-c", code]
                with self.host() as host:
                    with self.assertRaises(HTTPError):
                        self.start(host)
                    pid, child, snapshot = json.loads(external.read_text())
                    self.assertTrue(settled(lambda: not alive(pid) and not alive(child)))
                    self.assertFalse(Path(snapshot).exists())

    def test_path_escapes_private_paths_and_nonunique_mutations(self):
        includes = [["../outside"], [str(self.root / "app.py")], [".factory/holdout/HOLDOUT.md"]]
        if os.name == "nt":
            includes += [["D:relative"], ["\\Windows"]]
        for include in includes:
            self.cfg["include"] = include
            with self.host() as host:
                with self.assertRaises(HTTPError):
                    self.start(host)
        self.cfg["include"] = ["app.py"]
        for mutation in ({"file": "../outside", "find": "x", "replace": "y"},
                         {"file": "app.py", "find": "import", "replace": "x"},
                         {"file": "app.py", "find": "", "replace": "x"}):
            self.cfg["mutations"] = [{"id": "bad", **mutation}]
            with self.host() as host:
                with self.assertRaises(HTTPError):
                    self.start(host, mutation="bad")
        with self.host() as host:
            with self.assertRaises(HTTPError):
                self.start(host, root=str(self.root))

    def test_authentication_and_token_not_in_app_environment_or_cli_errors(self):
        with self.host() as host:
            with self.assertRaises(HTTPError) as error:
                request("start", {"slot": "case", "root": "candidate"},
                        {**host.connection, "token": "wrong"})
            self.assertEqual(error.exception.code, 403)
            item = self.start(host)
            result = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] /
                                     "template/factory/runtime_host.py"), "start", "--slot", "case",
                                     "--root", "SENTINEL_SECRET"], env={**os.environ, **host.environment()},
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 1)
            self.assertNotIn("SENTINEL_SECRET", result.stdout + result.stderr)
            self.assertNotIn(host.connection["token"], result.stdout + result.stderr)
        self.cfg.update(shape="cli", command=[sys.executable, "-c",
            "import os; assert 'FACTORY_RUNTIME_TOKEN' not in os.environ; assert 'FACTORY_AGENT_CMD' not in os.environ"])
        with self.host() as host:
            self.assertEqual(self.start(host)["exit_code"], 0)

    def test_cli_library_bind_executed_bytes_and_exit_without_synthetic_verdict(self):
        for shape in ("cli", "library"):
            self.cfg.update(shape=shape, command=[sys.executable, "-c", "raise SystemExit(9)"])
            with self.host() as host:
                result = self.start(host)
                self.assertEqual(result["exit_code"], 9)
                self.assertIsNone(result["target"])
                self.assertNotIn("verdict", result)
                self.assertEqual(request("identity", {"slot": "case"}, host.connection), result)

    def test_provider_shell_commands_refused(self):
        for argv in (["codex", "exec"], ["claude"], ["cmd.exe", "/c", "echo hi"], ["bash", "-c", "true"]):
            with self.assertRaises(ValueError):
                command(argv, {})

    def test_setup_teardown_and_manual_cli_contract(self):
        module = str(Path(__file__).resolve().parents[1] / "template/factory/runtime_host.py")
        self.config.write_text(json.dumps(self.cfg))
        connection = self.base / "private-connection.json"
        parent = subprocess.Popen([sys.executable, module, "serve", "--config", str(self.config),
                                   "--connection-file", str(connection)], stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True)
        try:
            self.assertIn("ready", parent.stdout.readline())
            secret = json.loads(connection.read_text())["token"]
            def cli(action, *args):
                result = subprocess.run([sys.executable, module, action, "--slot", "manual",
                                         "--connection-file", str(connection), *args],
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn(secret, result.stdout + result.stderr)
                return result.stdout
            cli("setup")
            item = json.loads(cli("start", "--root", "candidate"))
            pids = self.pids(item)
            self.assertEqual(json.loads(cli("describe"))["target"], item["target"])
            self.assertEqual(cli("identity").strip(), item["candidate"])
            cli("teardown")
            cli("teardown")
            self.assert_clean(item, pids)
        finally:
            parent.kill()
            parent.wait(timeout=15)
            parent.stdout.close()
            parent.stderr.close()

    def test_parent_exit_during_setup_cancels_wait_and_cleans_tree(self):
        marker = self.base / "setup-pids.json"
        self.cfg["setup"] = [sys.executable, "-c",
            "import json,os,sys,subprocess,time; from pathlib import Path; "
            "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(300)']); "
            f"Path({str(marker)!r}).write_text(json.dumps([os.getpid(),p.pid,os.getcwd()])); time.sleep(300)"]
        self.cfg["timeout_s"] = 60
        self.config.write_text(json.dumps(self.cfg))
        module_dir = str(Path(__file__).resolve().parents[1] / "template/factory")
        script = (f"import sys; sys.path.insert(0,{module_dir!r}); "
                  "from runtime_host import RuntimeHost,request\n"
                  f"with RuntimeHost({str(self.config)!r}) as host:\n"
                  " request('start',{'slot':'case','root':'candidate'},host.connection)\n")
        parent = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)
        try:
            self.assertTrue(settled(marker.exists))
            pid, child, snapshot = json.loads(marker.read_text())
            parent.kill()
            parent.wait(timeout=10)
            self.assertTrue(settled(lambda: not Path(snapshot).parent.exists()))
            self.assertFalse(alive(pid))
            self.assertFalse(alive(child))
        finally:
            if parent.poll() is None:
                parent.kill()
                parent.wait(timeout=10)

    def test_parent_termination_and_cancellation_cleanup(self):
        self.config.write_text(json.dumps(self.cfg))
        module_dir = str(Path(__file__).resolve().parents[1] / "template/factory")
        for mode in ("kill", "cancel"):
            script = (f"import sys,json,time; sys.path.insert(0,{module_dir!r}); "
                      "from runtime_host import RuntimeHost,request\n"
                      f"with RuntimeHost({str(self.config)!r}) as host:\n"
                      " item=request('start',{'slot':'case','root':'candidate'},host.connection)\n"
                      " print(json.dumps(item),flush=True)\n"
                      + (" time.sleep(300)\n" if mode == "kill" else " raise KeyboardInterrupt()\n"))
            parent = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True)
            try:
                line = parent.stdout.readline()
                self.assertTrue(line)
                item = json.loads(line)
                # Capture descendants before cancellation can remove the state files.
                if mode == "kill":
                    pids = self.pids(item)
                    parent.kill()
                else:
                    pids = []
                parent.wait(timeout=20)
                self.assertTrue(settled(lambda: not Path(item["snapshot"]).parent.exists()))
                self.assert_clean(item, pids)
                with self.assertRaises((URLError, OSError)):
                    self.get(item, "/health")
            finally:
                if parent.poll() is None:
                    parent.kill()
                    parent.wait(timeout=10)
                parent.stdout.close()
                parent.stderr.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
