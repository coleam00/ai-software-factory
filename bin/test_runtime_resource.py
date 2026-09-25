"""Committed candidate preparation and runtime binding regressions."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "template/factory"))
import runtime_resource
from runtime_host import RuntimeHost, request


APP = r'''from http.server import BaseHTTPRequestHandler, HTTPServer
import os
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        self.send_response(200); self.end_headers()
        value = os.environ['FACTORY_RUNTIME_CANDIDATE'] if self.path == '/identity' else 'VALUE'
        self.wfile.write(value.encode())
HTTPServer(('127.0.0.1', int(os.environ['FACTORY_RUNTIME_PORT'])), Handler).serve_forever()
'''


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="resource test ")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.repo = self.base / "delivery"
        (self.repo / "factory").mkdir(parents=True)
        for name in ("runtime_resource.py", "runtime_host.py", "runtime_process.py"):
            shutil.copy2(ROOT / f"template/factory/{name}", self.repo / f"factory/{name}")
        (self.repo / "app.py").write_text(APP.replace("VALUE", "first"), encoding="utf-8")
        self.git("init")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Runtime Test")
        self.git("add", "app.py", "factory/runtime_resource.py", "factory/runtime_host.py",
                 "factory/runtime_process.py")
        self.git("commit", "-m", "first candidate")
        self.resource = self.base / "candidate"

    def git(self, *args, input=None):
        return subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True,
                              text=True, input=input).stdout.strip()

    def prepare(self, revision=None, destination=None, config=None, helper=None, cwd=None):
        command = [sys.executable, str(helper or self.repo / "factory/runtime_resource.py"),
                   "prepare", "--destination", str(destination or self.resource),
                   "--config", str(config or self.config())]
        if revision is not None:
            command.extend(["--expected-revision", revision])
        return subprocess.run(command, cwd=cwd or self.repo,
                              capture_output=True, text=True, timeout=30)

    def config(self):
        path = self.base / "runtime.json"
        path.write_text(json.dumps({
            "version": 1,
            "roots": {"candidate": {"path": str(self.resource),
                                      "binding": ".factory-resource.json"}},
            "include": ["app.py"], "shape": "http",
            "command": [sys.executable, "app.py"], "timeout_s": 2,
            "health_path": "/health", "identity_path": "/identity",
        }))
        return path

    def start(self, connection, slot):
        command = [sys.executable, str(self.repo / "factory/runtime_resource.py"),
                   "start", "--slot", slot, "--root", "candidate"]
        environment = None
        if connection is not None:
            connection_file = self.base / "connection.json"
            connection_file.write_text(json.dumps(connection))
            command.extend(["--connection-file", str(connection_file)])
        else:
            environment = os.environ.copy()
            environment.update(self.host.environment())
        result = subprocess.run(command, cwd=self.repo, env=environment,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_changed_committed_revision_is_materialized_and_runs(self):
        first = self.git("rev-parse", "HEAD")
        with RuntimeHost(self.config()) as host:
            self.assertEqual(self.prepare(first).returncode, 0)
            first_item = self.start(host.connection, "first")
            with urlopen(first_item["target"] + "/health", timeout=2) as response:
                self.assertEqual(response.read().decode(), "first")
            self.assertEqual(first_item["source_revision"], first)
            self.assertEqual(first_item["input_digest"], first_item["resource_digest"])

            (self.repo / "app.py").write_text(APP.replace("VALUE", "second"), encoding="utf-8")
            self.git("add", "app.py")
            self.git("commit", "-m", "second candidate")
            second = self.git("rev-parse", "HEAD")
            self.assertEqual(self.prepare(second).returncode, 0)
            with self.assertRaises(Exception):
                request("start", {"slot": "stale", "root": "candidate",
                                  "expected_revision": first}, host.connection)
            item = self.start(host.connection, "second")
            with urlopen(item["target"] + "/health", timeout=2) as response:
                self.assertEqual(response.read().decode(), "second")
            self.assertNotEqual(first, item["source_revision"])
            self.assertNotEqual(first_item["input_digest"], item["input_digest"])
            self.assertNotEqual(first_item["resource_digest"], item["resource_digest"])

    def test_foreground_environment_preserves_clean_checkout_validation(self):
        revision = self.git("rev-parse", "HEAD")
        with RuntimeHost(self.config()) as self.host:
            self.assertEqual(self.prepare(revision).returncode, 0)
            item = self.start(None, "foreground")
            self.assertEqual(item["source_revision"], revision)

            (self.repo / "app.py").write_text(APP.replace("VALUE", "dirty"), encoding="utf-8")
            command = [sys.executable, str(self.repo / "factory/runtime_resource.py"),
                       "start", "--slot", "dirty", "--root", "candidate"]
            environment = os.environ.copy()
            environment.update(self.host.environment())
            refused = subprocess.run(command, cwd=self.repo, env=environment,
                                     capture_output=True, text=True, timeout=10)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("Runtime resource operation refused", refused.stderr)

    def test_stale_checkout_and_unowned_destination_are_refused(self):
        revision = self.git("rev-parse", "HEAD")
        self.assertNotEqual(self.prepare("0" * 40).returncode, 0)
        unowned = self.base / "unowned"
        unowned.mkdir()
        sentinel = unowned / "keep.txt"
        sentinel.write_text("keep")
        self.assertNotEqual(self.prepare(revision, unowned).returncode, 0)
        self.assertEqual(sentinel.read_text(), "keep")

        relative = Path("relative-candidate")
        self.assertNotEqual(self.prepare(revision, relative).returncode, 0)
        self.assertFalse((self.repo / relative).exists())

    def test_only_configured_committed_application_paths_are_materialized(self):
        private = self.repo / ".factory/holdout/HOLDOUT.md"
        private.parent.mkdir(parents=True)
        private.write_text("SYNTHETIC_PRIVATE_CONTROL", encoding="utf-8")
        self.git("add", ".factory/holdout/HOLDOUT.md")
        self.git("commit", "-m", "tracked synthetic evaluator control")
        revision = self.git("rev-parse", "HEAD")

        result = self.prepare(revision)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.resource / "app.py").is_file())
        self.assertFalse((self.resource / ".factory").exists())

    def test_invalid_includes_preserve_existing_owned_resource(self):
        revision = self.git("rev-parse", "HEAD")
        self.assertEqual(self.prepare(revision).returncode, 0)
        original_marker = (self.resource / ".factory-resource.json").read_bytes()
        original_app = (self.resource / "app.py").read_bytes()
        outside = self.base / "outside.py"
        outside.write_text("secret", encoding="utf-8")

        blob = self.git("hash-object", "-w", "--stdin", input="outside.py")
        self.git("update-index", "--add", "--cacheinfo", f"120000,{blob},linked.py")
        self.git("commit", "-m", "tracked synthetic link")
        linked_revision = self.git("rev-parse", "HEAD")

        for include in ("../outside.py", ".factory", "missing.py", "linked.py"):
            with self.subTest(include=include):
                config = self.base / f"invalid-{len(include)}.json"
                config.write_text(json.dumps({"version": 1, "include": [include]}))
                result = self.prepare(linked_revision, config=config)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((self.resource / ".factory-resource.json").read_bytes(),
                                 original_marker)
                self.assertEqual((self.resource / "app.py").read_bytes(), original_app)

    def test_failed_promotion_restores_previous_owned_resource(self):
        first = self.git("rev-parse", "HEAD")
        with mock.patch.object(runtime_resource, "delivery", return_value=(self.repo, first)):
            runtime_resource.prepare(self.resource, self.config(), first)
        original_marker = (self.resource / runtime_resource.MARKER).read_bytes()
        original_app = (self.resource / "app.py").read_bytes()

        (self.repo / "app.py").write_text(APP.replace("VALUE", "second"), encoding="utf-8")
        self.git("add", "app.py")
        self.git("commit", "-m", "second candidate")
        second = self.git("rev-parse", "HEAD")
        original_replace = Path.replace

        def fail_new_resource_promotion(source, target):
            source = Path(source)
            if (Path(target) == self.resource
                    and source.name.startswith(".factory-resource-")
                    and not source.name.startswith(".factory-resource-backup-")):
                raise OSError("simulated promotion failure")
            return original_replace(source, target)

        with (mock.patch.object(runtime_resource, "delivery", return_value=(self.repo, second)),
              mock.patch.object(Path, "replace", fail_new_resource_promotion),
              self.assertRaisesRegex(OSError, "simulated promotion failure")):
            runtime_resource.prepare(self.resource, self.config(), second)

        self.assertEqual((self.resource / runtime_resource.MARKER).read_bytes(), original_marker)
        self.assertEqual((self.resource / "app.py").read_bytes(), original_app)
        self.assertEqual(list(self.base.glob(".factory-resource-*")), [])

    def test_absolute_helper_from_an_old_checkout_is_refused_in_delivering_cwd(self):
        old_repo = self.base / "old-delivery"
        shutil.copytree(self.repo, old_repo)
        (self.repo / "app.py").write_text(APP.replace("VALUE", "new"), encoding="utf-8")
        self.git("add", "app.py")
        self.git("commit", "-m", "new delivering candidate")
        destination = self.base / "old-candidate"

        result = self.prepare(destination=destination,
                              helper=old_repo / "factory/runtime_resource.py",
                              cwd=self.repo)

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
