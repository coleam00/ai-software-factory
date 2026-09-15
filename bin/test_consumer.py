"""Local boundary tests. Fake CLI records argv and never launches a provider."""
from __future__ import annotations
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from install import (AGENTS_POINTER, HOME, TEMPLATE, configure, install_agents_pointer,
                     install_source, sync)
import consumer


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


FAKE_CLI = r'''
import json, os, re, sys
from pathlib import Path
args = sys.argv[1:]
source = Path(__file__).resolve().parents[3]
with open(os.environ["FACTORY_TEST_TRACE"], "a", encoding="utf-8") as f:
    f.write(json.dumps(args) + "\n")
if "--help" in args:
    print("workflow " + args[1] + " --workflow-source --input --adopt --detach --json --events --comment --reason")
    raise SystemExit(0)
if args[:2] == ["workflow", "list"]:
    names = [p.stem for p in (source / ".archon/workflows/sdlc").rglob("*.yaml")]
    print(json.dumps({"workflows": [{"name": n} for n in names], "errors": []}, indent=2))
elif args[:2] == ["validate", "workflows"]:
    name = args[2]
    files = list((source / ".archon/workflows/sdlc").rglob(name + ".yaml"))
    valid = bool(files) and (files[0].parent / "commands/check.md").is_file()
    print(json.dumps({"results": [{"workflowName": name, "valid": valid}], "summary": {"errors": 0 if valid else 1}}))
    raise SystemExit(0 if valid else 1)
else:
    if os.environ.get("FACTORY_RUNTIME_URL"):
        from urllib.request import Request, urlopen
        req = Request(os.environ["FACTORY_RUNTIME_URL"] + "/start",
                      data=json.dumps({"slot":"baseline", "root":"candidate"}).encode(),
                      headers={"Authorization":"Bearer " + os.environ["FACTORY_RUNTIME_TOKEN"]})
        with urlopen(req) as response:
            Path(os.environ["FACTORY_TEST_HOST_RESULT"]).write_bytes(response.read())
    status = os.environ.get("FACTORY_TEST_STATUS", "paused")
    print(json.dumps({"id": "run-123", "status": status, "source": str(source),
                      "working_path": "candidate checkout", "argv": args}, indent=2))
    raise SystemExit(int(os.environ.get("FACTORY_TEST_EXIT", "0")))
'''


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="factory consumer ")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.app = self.base / "application with spaces"
        self.app.mkdir()
        self.git(self.app, "init", "-q")
        self.git(self.app, "config", "user.name", "Fixture")
        self.git(self.app, "config", "user.email", "fixture@example.invalid")
        (self.app / "app.txt").write_text("original app\n")
        self.git(self.app, "add", ".")
        self.git(self.app, "commit", "-qm", "fixture")
        self.origin = self.base / "complete producer source"
        self.origin.mkdir()
        self.git(self.origin, "init", "-q")
        self.git(self.origin, "config", "user.name", "Fixture")
        self.git(self.origin, "config", "user.email", "fixture@example.invalid")
        for rel, body in {consumer.ENTRY: FAKE_CLI, "package.json": "{}", "bun.lock": "{}",
                          ".gitignore": "node_modules/\n.archon/ignored.yaml\n"}.items():
            path = self.origin / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        for name in [*consumer.MANIFEST["entries"], "archon-future-queue"]:
            folder = self.origin / consumer.MANIFEST["source_directory"] / name
            (folder / "commands").mkdir(parents=True)
            (folder / (name + ".yaml")).write_text(f"name: {name}\nnodes:\n  - id: check\n    command: check\n")
            (folder / "commands/check.md").write_text("fixture only\n")
        self.git(self.origin, "add", ".")
        self.git(self.origin, "commit", "-qm", "producer fixture")
        self.revision = self.git(self.origin, "rev-parse", "HEAD").strip()
        self.source = self.base / self.revision
        self.git(self.base, "clone", "-q", "--no-hardlinks", str(self.origin), str(self.source))
        (self.source / "node_modules").mkdir()
        self.settings = {"source": str(self.source), "revision": self.revision, "bun": sys.executable}
        self.trace = self.base / "argv.jsonl"
        self.env = patch.dict(os.environ, {"FACTORY_TEST_TRACE": str(self.trace),
                         "FACTORY_AGENT_CMD": "provider-must-never-run", "FACTORY_AUTONOMY": "4"})
        self.env.start()
        self.addCleanup(self.env.stop)
        configure(self.app, self.settings)

    def git(self, cwd, *args):
        result = consumer.execute(["git", *args], cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def command(self, *args, env=None):
        return subprocess.run([sys.executable, str(HOME / "bin/factory.py"), *args], cwd=self.app,
                              env={**os.environ, **(env or {})}, capture_output=True, text=True,
                              encoding="utf-8", timeout=60)

    def calls(self):
        return [json.loads(line) for line in self.trace.read_text().splitlines()] if self.trace.exists() else []


class ConsumerTests(Fixture):
    def test_factory_state_labels_default_only_for_declaring_workflows(self):
        expected = consumer.MANIFEST["default_inputs"]["archon-triage"]["state_labels"]
        for workflow in ("archon-triage", "archon-ship", "archon-lifecycle"):
            with self.subTest(workflow=workflow):
                result = self.command("run", workflow, "--json")
                self.assertEqual(result.returncode, 0, result.stderr)
                argv = json.loads(result.stdout)["argv"]
                self.assertEqual(argv.count("--input"), 1)
                self.assertEqual(argv.count("state_labels=" + expected), 1)
                self.assertFalse(any(arg.startswith(("publish=", "publish_holds="))
                                     for arg in argv))
        result = self.command("run", "archon-backlog", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(arg.startswith("state_labels=") for arg in json.loads(result.stdout)["argv"]))

    def test_explicit_state_labels_override_defaults_in_both_flag_forms(self):
        for input_args in (("--input", "state_labels={}"),
                           ("--input=state_labels={}",)):
            with self.subTest(input_args=input_args):
                result = self.command("run", "archon-ship", *input_args, "--json")
                self.assertEqual(result.returncode, 0, result.stderr)
                argv = json.loads(result.stdout)["argv"]
                values = [argv[index + 1] for index, arg in enumerate(argv[:-1])
                          if arg == "--input"]
                values += [arg.removeprefix("--input=") for arg in argv
                           if arg.startswith("--input=")]
                self.assertEqual(values, ["state_labels={}"])

    def test_defaults_do_not_enter_messages_or_native_continuations(self):
        result = self.command("run", "archon-ship", "--", "--input", "state_labels={}")
        self.assertEqual(result.returncode, 0, result.stderr)
        argv = json.loads(result.stdout)["argv"]
        self.assertEqual(argv[argv.index("--") + 1:], ["--input", "state_labels={}"])
        self.assertIn("state_labels=" + consumer.MANIFEST["default_inputs"]["archon-ship"]["state_labels"], argv)
        result = self.command("run", "archon-ship", "--resume", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        argv = json.loads(result.stdout)["argv"]
        self.assertFalse(any(arg.startswith("state_labels=") for arg in argv))
        self.assertNotIn("--workflow-source", argv)

    def test_scheduled_defaults_and_explicit_empty_mapping(self):
        schedule = self.app / ".factory/schedule.json"
        schedule.write_text(json.dumps({"workflow": "archon-lifecycle", "inputs": {}}))
        result = self.command("tick")
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = consumer.MANIFEST["default_inputs"]["archon-lifecycle"]["state_labels"]
        self.assertIn("state_labels=" + expected, json.loads(result.stdout)["argv"])
        schedule.write_text(json.dumps({"workflow": "archon-lifecycle",
                                        "inputs": {"state_labels": {}}}))
        result = self.command("tick")
        self.assertEqual(result.returncode, 0, result.stderr)
        argv = json.loads(result.stdout)["argv"]
        self.assertEqual(argv.count("state_labels={}"), 1)
        self.assertNotIn("state_labels=" + expected, argv)

    def test_runtime_host_detach_and_resume_refused_before_native_launch(self):
        for args in [("--detach",), ("--detach=true",), ("-d",), ("--resume",)]:
            result = self.command("run", "archon-ship", "--runtime-host", "missing.json", *args)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ownership contract", result.stderr)
        self.assertEqual(self.calls(), [])
        result = self.command("run", "archon-ship", "--detach", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--detach", json.loads(result.stdout)["argv"])

    def test_foreground_runtime_host_wraps_direct_and_scheduled_native_runs_and_cleans(self):
        from test_runtime_host import TARGET
        (self.app / "app.py").write_text(TARGET)
        config = self.app / "runtime.json"
        config.write_text(json.dumps({"version": 1, "roots": {"candidate": str(self.app)},
            "include": ["app.py"], "shape": "http", "command": [sys.executable, "app.py"],
            "health_path": "/health", "identity_path": "/identity", "timeout_s": 5}))
        output = self.base / "host-result.json"
        for rc in (0, 23):
            result = self.command("run", "archon-ship", "--runtime-host", str(config), "--json",
                env={"FACTORY_TEST_HOST_RESULT": str(output), "FACTORY_TEST_EXIT": str(rc)})
            self.assertEqual(result.returncode, rc, result.stderr)
            argv = json.loads(result.stdout)["argv"]
            self.assertNotIn("--runtime-host", argv)
            item = json.loads(output.read_text())
            self.assertFalse(Path(item["snapshot"]).parent.exists())
        schedule = self.app / ".factory/schedule.json"
        schedule.write_text(json.dumps({"workflow": "archon-ship", "inputs": {},
                                        "runtime_host": str(config)}))
        result = self.command("tick", env={"FACTORY_TEST_HOST_RESULT": str(output)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("--runtime-host", json.loads(result.stdout)["argv"])
        item = json.loads(output.read_text())
        self.assertFalse(Path(item["snapshot"]).parent.exists())
        self.assertEqual(len([row for row in self.calls() if row[:2] == ["workflow", "run"]]), 3)

    def test_scheduled_falsey_runtime_hosts_refuse_before_native_launch(self):
        schedule = self.app / ".factory/schedule.json"
        for host in ("", None, False, 0, [], {}):
            with self.subTest(runtime_host=host):
                schedule.write_text(json.dumps({"workflow": "archon-ship", "inputs": {},
                                                "runtime_host": host}))
                result = self.command("tick")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("runtime_host must be a non-empty configuration path", result.stderr)
                self.assertEqual(self.calls(), [])

    def test_pinned_run_ignores_local_conflict_and_provider_override(self):
        local = self.app / ".archon/workflows/archon-merge-queue.yaml"
        local.parent.mkdir(parents=True)
        local.write_text("name: archon-merge-queue\nTHIS MUST NOT RUN\n")
        direct_inputs = [
            'prs=["https://github.com/owner/repo/pull/12"]',
            "mode=preview",
            'evidence=[{"path":"proof.json","sha256":"abc123"}]',
            "merge_method=merge",
        ]
        result = self.command("run", "archon-merge-queue", "--json", *[
            value for item in direct_inputs for value in ("--input", item)
        ], "--detach")
        self.assertEqual(result.returncode, 0, result.stderr)
        argv = json.loads(result.stdout)["argv"]
        self.assertEqual(argv[argv.index("--workflow-source") + 1], str(self.source))
        self.assertEqual(argv[argv.index("--cwd") + 1], str(self.app))
        for item in direct_inputs:
            self.assertEqual(argv.count(item), 1)
        self.assertNotIn("--no-worktree", argv)
        self.assertNotIn("provider-must-never-run", self.trace.read_text())

    def test_reliability_inputs_pass_through_scheduled_run(self):
        scheduled_inputs = {
            "target": "https://github.com/owner/repo/issues/12",
            "publish": "false",
            "merge_method": "merge",
            "merge_mode": "preview",
            "discovery_publication": "preview",
            "validation_scope": "packages/api",
            "validation_context": "ubuntu-postgres-16",
            "scenario": "runtime.json",
            "holdout": "holdout.json",
            "deploy": "",
            "health": "",
            "identity": "",
        }
        schedule = self.app / ".factory/schedule.json"
        schedule.write_text(json.dumps({"workflow": "archon-lifecycle", "inputs": scheduled_inputs}))
        scheduled = self.command("tick")
        self.assertEqual(scheduled.returncode, 0, scheduled.stderr)
        scheduled_argv = json.loads(scheduled.stdout)["argv"]
        for key, value in scheduled_inputs.items():
            self.assertEqual(scheduled_argv.count(f"{key}={value}"), 1)

    def test_future_source_workflow_needs_no_alias(self):
        result = self.command("run", "archon-future-queue", "--input", "candidate=pr:1")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_shared_workflow_fails_closed(self):
        result = self.command("run", "archon-ambient-only")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no fallback", result.stderr)
        self.assertFalse(any(row[:2] == ["workflow", "run"] for row in self.calls()))

    def test_legacy_dial_and_receipts_cannot_merge(self):
        config = self.app / "factory/config.py"
        config.parent.mkdir()
        config.write_text('raise RuntimeError("legacy config must not be imported")')
        receipt = self.app / ".factory/acceptance.json"
        receipt.write_text('{"verdict":"approve","autonomy":4}')
        for args in [("level", "4"), ("accept", "gh:pr:1"), ("run", "merge", "gh:pr:1")]:
            result = self.command(*args)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("retired", result.stderr.lower())
        self.assertFalse(any(row[:2] == ["workflow", "run"] for row in self.calls()))
        self.assertEqual(receipt.read_text(), '{"verdict":"approve","autonomy":4}')

    def test_native_controls_keep_args_and_exit(self):
        for action, args in [("get", ["run-123", "--events"]), ("status", ["--all"]),
                             ("approve", ["run-123", "--comment", "accepted scope"]),
                             ("reject", ["run-123", "--reason", "changed head"]),
                             ("respond", ["run-123", "revise", "keep this text"]),
                             ("cancel", ["run-123"]), ("resume", ["run-123", "--detach"])]:
            with self.subTest(action=action):
                result = self.command(action, *args, "--json", env={"FACTORY_TEST_EXIT": "7"})
                self.assertEqual(result.returncode, 7, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["id"], "run-123")
                self.assertEqual(payload["status"], "paused")
                self.assertEqual(payload["argv"][:2], ["workflow", action])
                self.assertEqual(payload["argv"][4:4 + len(args)], args)
                self.assertNotIn("--workflow-source", payload["argv"])
        self.assertFalse((self.app / ".factory/runs").exists())

    def test_native_failed_and_unknown_are_not_reinterpreted(self):
        for status in ("running", "failed", "paused", "future-status"):
            result = self.command("get", "run-123", "--json", env={"FACTORY_TEST_STATUS": status})
            self.assertEqual(json.loads(result.stdout)["status"], status)

    def test_adopt_and_resume_have_native_source_semantics(self):
        result = self.command("run", "archon-deliver", "--adopt", "producer-run", "--input", "work=fix.md")
        argv = json.loads(result.stdout)["argv"]
        self.assertIn("producer-run", argv)
        self.assertIn("--workflow-source", argv)
        result = self.command("run", "archon-deliver", "--resume")
        self.assertNotIn("--workflow-source", json.loads(result.stdout)["argv"])

    def test_source_overrides_refused(self):
        for flag in ("--workflow-source=evil", "--cwd=evil", "--workflow-source", "--cwd"):
            self.assertNotEqual(self.command("run", "archon-ship", flag, "evil").returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_separator_cannot_turn_source_flags_into_message_text(self):
        result = self.command("run", "archon-ship", "--", "--resume", "literal message")
        self.assertEqual(result.returncode, 0, result.stderr)
        argv = json.loads(result.stdout)["argv"]
        self.assertLess(argv.index("--cwd"), argv.index("--"))
        self.assertLess(argv.index("--workflow-source"), argv.index("--"))
        self.assertEqual(argv[argv.index("--") + 1:], ["--resume", "literal message"])

    def test_failed_native_launch_is_not_retried_or_promoted(self):
        result = self.command("run", "archon-ship", "--json",
                              env={"FACTORY_TEST_STATUS": "failed", "FACTORY_TEST_EXIT": "23"})
        self.assertEqual(result.returncode, 23)
        self.assertEqual(json.loads(result.stdout)["status"], "failed")
        runs = [args for args in self.calls() if args[:2] == ["workflow", "run"]]
        self.assertEqual(len(runs), 1)
        self.assertFalse((self.app / ".factory/runs").exists())

    def test_stop_does_not_guess_or_cancel_active_runs(self):
        self.assertEqual(self.command("halt").returncode, 0)
        self.assertNotEqual(self.command("run", "archon-ship").returncode, 0)
        self.assertEqual(self.command("status", "--json").returncode, 0)
        self.assertEqual(self.command("cancel", "run-123").returncode, 0)
        self.assertEqual(self.command("unhalt").returncode, 0)
        self.assertFalse(any(row[:2] == ["workflow", "run"] for row in self.calls()))

    def test_sha_dirty_and_ignored_authoring_drift_refused(self):
        with self.assertRaisesRegex(ValueError, "SHA mismatch"):
            other = self.base / ("a" * 40)
            other.mkdir()
            self.git(self.base, "clone", "-q", str(self.origin), str(other))
            consumer.verify_source({**self.settings, "source": str(other), "revision": "a" * 40})
        command = next(self.source.rglob("check.md"))
        command.write_text("modified")
        with self.assertRaisesRegex(ValueError, "changes"):
            consumer.verify_source(self.settings)
        self.git(self.source, "restore", ".")
        (self.source / ".archon/ignored.yaml").write_text("name: archon-ship")
        with self.assertRaisesRegex(ValueError, "Untracked authoring"):
            consumer.verify_source(self.settings)

    def test_doctor_missing_workflow_or_command_is_not_ready(self):
        self.assertEqual(consumer.doctor(self.settings)["revision"], self.revision)
        with patch.object(consumer, "discover", return_value={}):
            with self.assertRaisesRegex(ValueError, "missing shared"):
                consumer.doctor(self.settings)
        with patch.object(consumer, "native_json", return_value={"results": [{"valid": False}]}):
            with self.assertRaisesRegex(ValueError, "validation failed"):
                consumer.validate(self.settings, self.source, "archon-ship")

    def test_capability_probe_refuses_unsupported_cli(self):
        with patch.object(consumer, "checked", return_value="no capabilities"), \
             patch.object(consumer, "verify_source", return_value=self.source):
            with self.assertRaisesRegex(ValueError, "missing workflow"):
                consumer.doctor(self.settings)

    def test_source_index_refuses_missing_command_include_and_conflict(self):
        path = next(self.source.rglob("archon-ship.yaml"))
        body = path.read_text()
        command = path.parent / "commands/check.md"
        command.unlink()
        with self.assertRaisesRegex(ValueError, "Missing source-owned command"):
            consumer.source_workflows(self.source)
        command.write_text("fixture")
        path.write_text(body + "    include: archon-only-in-global-config\n")
        with self.assertRaisesRegex(ValueError, "Include .* missing"):
            consumer.source_workflows(self.source)
        path.write_text(body)
        conflict = self.source / ".archon/workflows/conflict.yaml"
        conflict.write_text("name: archon-ship\n")
        with self.assertRaisesRegex(ValueError, "Conflicting workflow"):
            consumer.source_workflows(self.source)

    def test_pretty_json_and_malformed_response(self):
        self.assertTrue(consumer.native_json(self.settings, self.source, ["workflow", "list"], self.source)["workflows"])
        with patch.object(consumer, "checked", return_value='{}\n{}'):
            with self.assertRaises(ValueError):
                consumer.native_json(self.settings, self.source, [], self.source)

    def test_worktree_uses_shared_settings_and_its_own_target(self):
        worktree = self.base / "application worktree"
        self.git(self.app, "worktree", "add", "-b", "fixture-branch", str(worktree))
        self.assertEqual(consumer.read_settings(worktree), self.settings)
        result = subprocess.run([sys.executable, str(HOME / "bin/factory.py"), "run", "archon-ship", "--json"],
                                cwd=worktree, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        argv = json.loads(result.stdout)["argv"]
        self.assertEqual(argv[argv.index("--cwd") + 1], str(worktree))
        self.assertEqual(argv[argv.index("--workflow-source") + 1], str(self.source))


class SourceConformanceTests(unittest.TestCase):
    @patch.object(sys, "dont_write_bytecode", True)
    def test_supplied_pinned_source_accepts_factory_state_labels(self):
        supplied = os.environ.get("FACTORY_ARCHON_CONFORMANCE_SOURCE")
        if not supplied:
            self.skipTest("set FACTORY_ARCHON_CONFORMANCE_SOURCE to a complete pinned checkout")
        source = Path(supplied).resolve()
        revision = consumer.MANIFEST["integration_revision_required"]
        self.assertEqual(consumer.checked(["git", "rev-parse", "HEAD"], source).strip(), revision)
        consumer.verify_source({"source": str(source), "revision": revision})

        defaults = consumer.MANIFEST["default_inputs"]
        workflows = ("archon-triage", "archon-ship", "archon-lifecycle")
        self.assertEqual(set(defaults), set(workflows))
        mappings = [json.loads(defaults[name]["state_labels"]) for name in workflows]
        self.assertTrue(all(mapping == mappings[0] for mapping in mappings))

        triage = load("pinned_validate_contract", source / consumer.MANIFEST["source_directory"] /
                       "triage/scripts/validate-contract.py")
        lifecycle = load("pinned_select_target", source / consumer.MANIFEST["source_directory"] /
                          "lifecycle/scripts/select-target.py")
        self.assertEqual(set(mappings[0]), set(triage.STATE_LABEL_METADATA))
        self.assertEqual(set(mappings[0]), lifecycle.STATES)
        for mapping in mappings:
            self.assertEqual(triage.parse_state_labels(json.dumps(mapping)), mapping)
            with patch.dict(os.environ, {"INPUTS_STATE_LABELS": json.dumps(mapping)}):
                labels, complete = lifecycle.state_labels()
            self.assertTrue(complete)
            self.assertEqual(labels, {label.casefold() for label in mapping.values()})

        directory = source / consumer.MANIFEST["source_directory"]
        for name, folder in (("archon-triage", "triage"), ("archon-ship", "ship"),
                             ("archon-lifecycle", "lifecycle")):
            lines = (directory / folder / f"{name}.yaml").read_text(encoding="utf-8").splitlines()
            start = lines.index("inputs:") + 1
            input_lines = next((lines[start:index] for index in range(start, len(lines))
                                if lines[index] and not lines[index].startswith(" ")), lines[start:])
            declared = {line[2:-1] for line in input_lines
                        if line.startswith("  ") and not line.startswith("    ") and line.endswith(":" )}
            self.assertIn("state_labels", declared)

        policy_labels = {}
        for line in (TEMPLATE / "factory/WORKFLOW_POLICY.md").read_text(encoding="utf-8").splitlines():
            if line.startswith("| `"):
                state, label = [cell.strip().strip("`") for cell in line.strip("|").split("|")]
                policy_labels[state] = label
        self.assertEqual(policy_labels, mappings[0])
        consumer.verify_source({"source": str(source), "revision": revision})


class InstallTests(Fixture):
    def test_agents_pointer_preserves_existing_bytes_and_is_idempotent(self):
        agents = self.app / "AGENTS.md"
        original = b"# Existing guidance\r\n\r\nKeep this byte-for-byte."
        agents.write_bytes(original)
        preserved_time = 946684800_000_000_000
        os.utime(agents, ns=(preserved_time, preserved_time))
        with contextlib.redirect_stdout(io.StringIO()):
            sync(self.app)
        installed = agents.read_bytes()
        self.assertTrue(installed.startswith(original))
        self.assertEqual(installed.count(b"factory/WORKFLOW_POLICY.md"), 1)
        self.assertEqual(agents.stat().st_mtime_ns, preserved_time)
        with contextlib.redirect_stdout(io.StringIO()):
            sync(self.app)
        self.assertEqual(agents.read_bytes(), installed)
        self.assertTrue((self.app / "factory/WORKFLOW_POLICY.md").is_file())

    def test_agents_pointer_failed_temp_write_preserves_original_and_cleans_temp(self):
        agents = self.app / "AGENTS.md"
        original = b"# Existing guidance\n"
        agents.write_bytes(original)
        with patch("install.os.fsync", side_effect=OSError("interrupted write")), \
             self.assertRaisesRegex(OSError, "interrupted write"):
            install_agents_pointer(self.app, False)
        self.assertEqual(agents.read_bytes(), original)
        self.assertEqual(list(self.app.glob(".AGENTS.md.*.tmp")), [])

    def test_agents_pointer_failed_replace_preserves_original_and_cleans_temp(self):
        agents = self.app / "AGENTS.md"
        original = b"# Existing guidance\n"
        agents.write_bytes(original)
        with patch("install.os.replace", side_effect=OSError("interrupted replace")), \
             self.assertRaisesRegex(OSError, "interrupted replace"):
            install_agents_pointer(self.app, False)
        self.assertEqual(agents.read_bytes(), original)
        self.assertEqual(list(self.app.glob(".AGENTS.md.*.tmp")), [])

    def test_agents_pointer_honors_dry_run_and_creates_missing_file(self):
        agents = self.app / "AGENTS.md"
        with contextlib.redirect_stdout(io.StringIO()) as output:
            sync(self.app, True)
        self.assertFalse(agents.exists())
        self.assertIn("AGENTS.md factory policy pointer", output.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            sync(self.app)
        self.assertEqual(agents.read_bytes(), AGENTS_POINTER)

    def test_agents_pointer_refuses_escaping_symlink(self):
        agents = self.app / "AGENTS.md"
        outside = self.base / "outside-agents.md"
        outside.write_text("outside")
        try:
            agents.symlink_to(outside)
        except OSError:
            real_resolve = Path.resolve

            def escaping_resolve(path, *args, **kwargs):
                return outside if path == agents else real_resolve(path, *args, **kwargs)
            with patch.object(Path, "resolve", escaping_resolve), \
                 self.assertRaisesRegex(ValueError, "outside application"):
                install_agents_pointer(self.app, False)
            self.assertEqual(outside.read_text(), "outside")
            return
        with self.assertRaisesRegex(ValueError, "outside application"):
            install_agents_pointer(self.app, False)
        self.assertEqual(outside.read_text(), "outside")

    def test_upgrade_preserves_personal_files_and_backs_up_execution_surfaces(self):
        originals = {"factory/config.py": b"AUTONOMY=4\r\n", "harness/harness.config.json": b'{"agent":{"cmd":"do not run"}}',
                     "harness/END-TO-END.md": b"custom journey\r\n", ".factory/holdout/HOLDOUT.md": b"private scenario\n",
                     ".archon/config.yaml": b"defaultAssistant: custom\r\n", "app.txt": b"original app\n"}
        for rel, data in originals.items():
            path = self.app / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        custom = self.app / ".claude/skills/factory-e2e/SKILL.md"
        custom.parent.mkdir(parents=True)
        custom.write_bytes(b"custom original\r\n")
        provider = self.base / "home/.archon/config.yaml"
        provider.parent.mkdir(parents=True)
        provider.write_bytes(b"defaultAssistant: custom\r\nprivate: preserved\n")
        provider_before = provider.read_bytes()
        with patch.dict(os.environ, {"HOME": str(self.base / "home"), "USERPROFILE": str(self.base / "home")}), contextlib.redirect_stdout(io.StringIO()):
            sync(self.app)
            consumer.doctor(self.settings)
        self.assertEqual(provider.read_bytes(), provider_before)
        for rel, data in originals.items():
            self.assertEqual((self.app / rel).read_bytes(), data, rel)
        self.assertFalse(custom.exists())
        self.assertEqual((self.app / ".factory/retired/.claude/skills/factory-e2e/SKILL.md").read_bytes(), b"custom original\r\n")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            sync(self.app)
        self.assertNotIn("install ", out.getvalue())
        self.assertNotIn("preserve ", out.getvalue())

    def test_scaffold_leaves_integration_unconfigured(self):
        (self.app / consumer.SETTINGS).unlink()
        result = self.command("init", "--scaffold-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(list((self.app / ".archon").rglob("*.yaml")))
        result = self.command("doctor")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Integration pin required", result.stderr)

    def test_default_init_installs_manifest_revision(self):
        factory = load("factory_init_test", HOME / "bin/factory.py")
        settings = {"source": "immutable", "revision": consumer.MANIFEST["integration_revision_required"]}
        with patch.object(sys, "argv", ["factory.py", "init"]), \
             patch.object(factory.consumer, "project_root", return_value=self.app), \
             patch.object(factory, "sync") as sync_mock, \
             patch.object(factory, "install_source", return_value=settings) as install_mock, \
             patch.object(factory, "configure") as configure_mock, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(factory.main(), 0)
        sync_mock.assert_called_once_with(self.app)
        install_mock.assert_called_once_with(
            consumer.MANIFEST["repository"], consumer.MANIFEST["integration_revision_required"],
            Path.home() / ".cache/factory/archon", "bun")
        configure_mock.assert_called_once_with(self.app, settings)

    def test_installer_clones_full_source_and_never_repoints_cache(self):
        cache = self.base / "cache with spaces"
        # Only Bun's package install boundary is faked; clone, SHA checks, native CLI
        # discovery and command validation run as real subprocesses.
        original = consumer.checked
        def checked(argv, cwd, timeout=180):
            if argv[1:3] == ["install", "--frozen-lockfile"]:
                (cwd / "node_modules").mkdir()
                return "installed"
            return original(argv, cwd, timeout)
        with patch.object(consumer, "checked", side_effect=checked):
            settings = install_source(str(self.origin), self.revision, cache, sys.executable)
        self.assertEqual(settings["source"], str(cache / self.revision))
        self.assertTrue((Path(settings["source"]) / "package.json").is_file())
        tracked = Path(settings["source"]) / "package.json"
        tracked.write_text("dirty")
        with self.assertRaisesRegex(ValueError, "dirty"):
            install_source(str(self.origin), self.revision, cache, sys.executable)
        self.assertEqual(tracked.read_text(), "dirty")

    def test_dry_upgrade_and_repeat_backup_preserve_bytes(self):
        path = self.app / "factory/dispatch.py"
        path.parent.mkdir()
        path.write_bytes(b"custom scheduler\r\n")
        with contextlib.redirect_stdout(io.StringIO()):
            sync(self.app, True)
        self.assertEqual(path.read_bytes(), b"custom scheduler\r\n")
        self.assertFalse((self.app / ".factory/retired").exists())
        with contextlib.redirect_stdout(io.StringIO()):
            sync(self.app)
            path.write_bytes(b"second custom version")
            sync(self.app)
        base = self.app / ".factory/retired/factory/dispatch.py"
        self.assertEqual(base.read_bytes(), b"custom scheduler\r\n")
        self.assertEqual(base.with_suffix(".py.1").read_bytes(), b"second custom version")


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ordinary harness ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copytree(TEMPLATE / "harness", self.root / "harness", ignore=shutil.ignore_patterns("__pycache__"))
        self.config = self.root / "harness/harness.config.json"

    def run_checks(self, cfg):
        self.config.write_text(json.dumps(cfg))
        return subprocess.run([sys.executable, "harness/ci.py"], cwd=self.root,
                              capture_output=True, text=True, timeout=30)

    def test_static_unit_only_and_provider_sentinel_never_executes(self):
        sentinel = self.root / "provider.py"
        sentinel.write_text('from pathlib import Path\nPath("PROVIDER_EXECUTED").touch()\n')
        result = self.run_checks({"static": [sys.executable, "-c", "pass"],
                    "unit": [sys.executable, "-c", "print('3 passed')"], "unit_count_pattern": r"(\d+) passed",
                    "agent": {"cmd": f'"{sys.executable}" provider.py'}, "driver": "missing"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("UNIT_PASSED tests=3", result.stdout)
        self.assertIn("CHECKS_OK mode=ordinary", result.stdout)
        self.assertNotIn("E2E_PASSED", result.stdout)
        self.assertFalse((self.root / "PROVIDER_EXECUTED").exists())
        result = subprocess.run([sys.executable, "harness/agentcheck.py"], cwd=self.root, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.root / "PROVIDER_EXECUTED").exists())

    def test_no_checks_zero_tests_and_quoted_failure_are_red(self):
        for cfg in ({}, {"unit": [sys.executable, "-c", "print('0 passed')"], "unit_count_pattern": r"(\d+) passed"},
                    {"static": f'"{sys.executable}" -c "import no_such_factory_module"'}):
            result = self.run_checks(cfg)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertNotIn("CHECKS_OK", result.stdout)

    def test_windows_pathext_resolution(self):
        if os.name != "nt":
            self.skipTest("Windows launcher fixture")
        shim = self.root / "factory-test-shim.cmd"
        shim.write_text("@echo off\r\necho SHIM_OK\r\n")
        with patch.dict(os.environ, {"PATH": str(self.root) + os.pathsep + os.environ["PATH"]}):
            self.assertEqual(consumer.execute(["factory-test-shim"], self.root).stdout.strip(), "SHIM_OK")

    def test_runtime_export_preserves_environment_and_freshness_without_launch(self):
        module = load("runtime_data_test", TEMPLATE / "harness/runtime_data.py")
        cfg = {"driver": "http", "http": {"start": "ordinary app", "env": {"DB": "state-{port}"}},
               "agent": {"cmd": "provider forbidden", "timeout_s": 900}, "e2e_timeout_s": 300}
        original = json.dumps(cfg)
        data = module.export(cfg)
        self.assertEqual(data["environment"]["http"], cfg["http"])
        self.assertEqual(data["requirements"]["fresh_environment_per"], ["runtime", "holdout", "retry", "mutation"])
        self.assertNotIn("provider forbidden", json.dumps(data))
        self.assertEqual(json.dumps(cfg), original)

    def test_application_helper_quoted_import_can_fail(self):
        appproc = load("appproc_test", TEMPLATE / "harness/appproc.py")
        appproc.ROOT = self.root
        cfg = {"driver": "library", "library": {"import_check":
               f'"{sys.executable}" -c "import no_such_factory_module"'}}
        with self.assertRaises(appproc.AppDidNotStart):
            with appproc.make_driver(cfg):
                pass

    def test_mutation_unique_anchor_path_escape_and_noop(self):
        runner = load("mutation_test", TEMPLATE / "harness/mutations/run.py")
        target = self.root / "app.txt"
        target.write_text("value = 1\nvalue = 1\n")
        mutation = {"file": "app.txt", "find": "value = 1", "replace": "value = 2"}
        self.assertFalse(runner.apply(self.root, mutation)[0])
        target.write_text("value = 1\n")
        self.assertTrue(runner.apply(self.root, mutation)[0])
        self.assertEqual(target.read_text(), "value = 2\n")
        self.assertFalse(runner.apply(self.root, {**mutation, "file": "../outside"})[0])
        self.assertFalse(runner.apply(self.root, {**mutation, "find": "", "replace": ""})[0])

    def test_mutation_outcomes_require_real_verdict(self):
        runner = load("mutation_verdict_test", TEMPLATE / "harness/mutations/run.py")
        for rc, log, expected in [(0, "", "INCONCLUSIVE"), (0, "CHECKS_OK mode=ordinary", "ESCAPED"),
                 (1, "GATE_FAILED: unit", "CAUGHT"), (124, "GATE_FAILED: unit", "INCONCLUSIVE"),
                 (1, "GATE_FAILED: e2e", "INCONCLUSIVE"),
                 (1, "E2E_FAIL assertion\nGATE_FAILED: e2e", "CAUGHT"),
                 (1, "could not run missing\nGATE_FAILED: unit", "INCONCLUSIVE"),
                 (1, "ModuleNotFoundError: missing\nGATE_FAILED: unit", "INCONCLUSIVE")]:
            self.assertEqual(runner.classify(rc, log).outcome, expected)

    def test_runtime_mutation_scoring_requires_bound_typed_return(self):
        runner = load("runtime_mutation_verdict_test", TEMPLATE / "harness/mutations/run.py")
        result = {"verified": False, "verdict": "failed", "candidate": "attempt-id",
                  "checkout": "", "summary": "observed failure"}
        self.assertEqual(runner.classify_runtime(result, "attempt-id").outcome, "CAUGHT")
        self.assertEqual(runner.classify_runtime({**result, "verified": True, "verdict": "verified"},
                                               "attempt-id").outcome, "ESCAPED")
        for invalid in (None, {}, {**result, "verified": True}, {**result, "candidate": "old"},
                        {**result, "summary": ""}, {**result, "verdict": "inconclusive"},
                        {"exit_code": 0, "stdout": "[PASS]"}):
            self.assertEqual(runner.classify_runtime(invalid, "attempt-id").outcome, "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main(verbosity=2)
