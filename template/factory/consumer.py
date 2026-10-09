"""Invoke a pinned, complete Archon source installation. No stage policy lives here."""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = json.loads((HERE / "pack.json").read_text(encoding="utf-8"))
SETTINGS = ".factory/consumer.json"
ENTRY = "packages/cli/src/cli.ts"
SCHEDULE = ".factory/schedule.json"
TRIGGER = ".factory/trigger.json"
# The shared workflows that carry a merge gate and accept a declared required-check policy.
MERGING_WORKFLOWS = {"archon-lifecycle", "archon-merge-queue"}


def windowless() -> bool:
    """True under pythonw (the Windows timer): no console to show child output in."""
    return sys.platform == "win32" and sys.stdout is None


def execute(argv: list[str], cwd: Path, *, capture: bool = True,
            timeout: int | None = 180, env: dict | None = None,
            output=None) -> subprocess.CompletedProcess:
    # Resolve PATH and PATHEXT once, including Windows .cmd launchers.
    argv = [shutil.which(argv[0]) or argv[0], *argv[1:]]
    flags = {}
    # A console program started from a windowless parent gets a new console window
    # unless told not to; captured or logged output does not need one.
    if sys.platform == "win32" and (capture or output is not None or windowless()):
        flags["creationflags"] = subprocess.CREATE_NO_WINDOW
    streams = ({"stdout": output, "stderr": subprocess.STDOUT} if output is not None
               else {"capture_output": capture})
    return subprocess.run(argv, cwd=cwd, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, env=env, **streams, **flags)


def checked(argv: list[str], cwd: Path, timeout: int = 180) -> str:
    result = execute(argv, cwd, timeout=timeout)
    if result.returncode:
        raise ValueError(f"Command exited {result.returncode}: {argv[:3]}\n"
                         + (result.stderr or result.stdout)[-3000:])
    return result.stdout


def project_root() -> Path:
    return Path(checked(["git", "rev-parse", "--show-toplevel"], Path.cwd()).strip()).resolve()


def shared_root(root: Path) -> Path:
    common = checked(["git", "rev-parse", "--git-common-dir"], root).strip()
    return (root / common).resolve().parent


def read_settings(root: Path) -> dict:
    path = shared_root(root) / SETTINGS
    if not path.is_file():
        raise ValueError("Integration pin required. Run factory init --source <complete Archon "
                         "checkout or URL> --revision <40-character SHA> --cache <directory>.")
    settings = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(settings, dict):
        raise ValueError(f"Invalid consumer settings: {path}")
    return settings


def verify_source(settings: dict) -> Path:
    revision = settings.get("revision", "")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Integration revision must be a full lowercase commit SHA")
    source = Path(settings["source"]).resolve()
    if source.name != revision:
        raise ValueError("Source must use an immutable per-pin directory named with its full SHA")
    actual = checked(["git", "rev-parse", "HEAD"], source).strip()
    if actual != revision:
        raise ValueError(f"Source SHA mismatch: expected {revision}, found {actual}")
    dirty = checked(["git", "status", "--porcelain", "--untracked-files=all"], source)
    if dirty.strip():
        # Name the paths and the way back. The usual cause is a dependency install
        # inside the pinned tree, which looks harmless and stops every run.
        changed = [line[3:].strip() for line in dirty.strip().splitlines() if line[3:].strip()]
        shown = ", ".join(changed[:5])
        if len(changed) > 5:
            shown += f", and {len(changed) - 5} more"
        raise ValueError(
            f"Pinned source has changes ({shown}). The pinned tree is verified byte for byte. "
            f"Restore it with: rm -rf {source} && python3 bin/factory.py init from your repo. "
            f"To change engine behavior, move the pin in factory/pack.json instead of editing the tree"
        )
    # Include ignored files under authoring roots: an ignored workflow can shadow a
    # committed name just as an untracked one can. Runtime dependencies stay ignored.
    tracked = set(checked(["git", "ls-files"], source).splitlines())
    for directory in (source / ".archon", source / "packages/cli/src"):
        if directory.is_symlink() or not directory.resolve().is_relative_to(source):
            raise ValueError(f"Source authoring directory escapes the pin: {directory}")
        for path in directory.rglob("*"):
            if path.is_symlink():
                raise ValueError(f"Source authoring symlink is not supported: {path}")
            if path.is_file() and path.relative_to(source).as_posix() not in tracked:
                raise ValueError(f"Untracked authoring file in pinned source: {path}")
    for rel in (ENTRY, "package.json", "bun.lock", MANIFEST["source_directory"]):
        if not (source / rel).exists():
            raise ValueError(f"Incomplete Archon source: missing {rel}")
    if not (source / "node_modules").is_dir():
        raise ValueError("Archon dependencies missing; complete the pinned source installation")
    return source


def cli(settings: dict, source: Path) -> list[str]:
    # Never use an ambient archon binary or provider command override.
    return [settings.get("bun", "bun"), str(source / ENTRY)]


def native_json(settings: dict, source: Path, args: list[str], cwd: Path) -> dict:
    raw = checked([*cli(settings, source), *args, "--cwd", str(cwd), "--json"], cwd)
    data = json.loads(raw)  # One whole document, including pretty-printed JSON.
    if not isinstance(data, dict) or data.get("ok") is False:
        raise ValueError(f"Invalid native response: {raw[:1000]}")
    return data


def source_workflows(source: Path) -> dict[str, Path]:
    # This is only a provenance index. Archon parses and validates the definitions.
    found = {}
    for path in (source / MANIFEST["source_directory"]).rglob("*"):
        if path.suffix not in (".yaml", ".yml"):
            continue
        match = re.search(r"^name:\s*['\"]?([a-z][a-z0-9-]*)['\"]?\s*$",
                          path.read_text(encoding="utf-8"), re.M)
        if match:
            name = match[1]
            if name in found:
                raise ValueError(f"Duplicate source workflow: {name}")
            found[name] = path
    # A second project definition must not shadow a selected pack member.
    for path in (source / ".archon/workflows").rglob("*"):
        if path.suffix not in (".yaml", ".yml") or path in found.values():
            continue
        match = re.search(r"^name:\s*['\"]?([a-z][a-z0-9-]*)['\"]?\s*$",
                          path.read_text(encoding="utf-8"), re.M)
        if match and match[1] in found:
            raise ValueError(f"Conflicting workflow outside the SDLC pack: {path}")
    # Packaged resources are resolved relative to their author's package by
    # Archon. Require the literal references to exist there, so a home-scoped or
    # bundled fallback cannot make an incomplete source look installable. Native
    # validation below remains responsible for parsing, types and the graph.
    for name, path in found.items():
        for kind, value in re.findall(r"^\s+(command|script|include):\s*([^\n#]+)",
                                      path.read_text(encoding="utf-8"), re.M):
            value = value.strip().strip("'\"")
            # Inline script code is already part of this pinned YAML. Match the
            # native isInlineScript rule; it has no external file to resolve.
            if kind == "script" and re.search(r"[;(){}&|<>$`\"' ]", value):
                continue
            if not re.fullmatch(r"[a-zA-Z0-9_./-]+", value):
                raise ValueError(f"Cannot verify nonliteral {kind} provenance in {name}: {value}")
            if kind == "include":
                if value not in found:
                    raise ValueError(f"Include {value} is missing from pinned SDLC source")
                continue
            directory = path.parent / ("commands" if kind == "command" else "scripts")
            candidates = [directory / (value + ".md")] if kind == "command" else [
                directory / (value + suffix) for suffix in ("", ".py", ".ts", ".js", ".sh")]
            if not any(p.is_file() and p.resolve().is_relative_to(source) for p in candidates):
                raise ValueError(f"Missing source-owned {kind} {value} for {name}")
    return found


def discover(settings: dict, source: Path) -> dict[str, Path]:
    local = source_workflows(source)
    data = native_json(settings, source, ["workflow", "list"], source)
    if data.get("errors"):
        raise ValueError(f"Native workflow discovery errors: {data['errors']}")
    rows = data.get("workflows")
    if not isinstance(rows, list):
        raise ValueError("Native workflow list returned no workflows array")
    names = {row["name"] for row in rows}
    return {name: path for name, path in local.items() if name in names}


def validate(settings: dict, source: Path, name: str) -> None:
    data = native_json(settings, source, ["validate", "workflows", name], source)
    results = data.get("results", [])
    if (not results or any(row.get("valid") is not True for row in results)
            or data.get("summary", {}).get("errors", 0)):
        raise ValueError(f"Workflow/command validation failed for {name}: {data}")


def doctor(settings: dict) -> dict:
    source = verify_source(settings)
    # Global flags such as --json are documented once, in the top-level workflow
    # help, and not repeated under every subcommand (`workflow status --help` lists
    # only --events and --all, yet `status --json` works). A flag counts as
    # documented when either help names it; the subcommand itself must still exist.
    shared_help = checked([*cli(settings, source), "workflow", "--help"], source)
    for command, flags in MANIFEST["capabilities"].items():
        help_text = checked([*cli(settings, source), "workflow", command, "--help"], source)
        if f"workflow {command}" not in help_text or any(
                flag not in help_text and flag not in shared_help for flag in flags):
            raise ValueError(f"Pinned CLI missing workflow {command} capability: {flags}")
    discovered = discover(settings, source)
    missing = sorted(set(MANIFEST["entries"]) - discovered.keys())
    if missing:
        raise ValueError("Incomplete integration source; missing shared workflows: " + ", ".join(missing))
    for name in discovered:
        validate(settings, source, name)
    return {"source": str(source), "revision": settings["revision"],
            "workflows": sorted(discovered), "automation": "supervised integration",
            "provider_configuration": "native configuration preserved; authentication not live-tested"}


def harness_inputs(root: Path, name: str) -> dict:
    # A project's merge policy is a fact the merge gate reads, not something an
    # agent decides. GitHub cannot report required checks on plans without branch
    # protection, so the project declares them once in its harness configuration.
    if name not in MERGING_WORKFLOWS:
        return {}
    try:
        config = json.loads((root / "harness/harness.config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    value = config.get("required_checks") if isinstance(config, dict) else None
    return {"required_checks": value} if isinstance(value, str) and value.strip() else {}


def workflow_defaults(root: Path, name: str) -> dict:
    return {**MANIFEST.get("default_inputs", {}).get(name, {}), **harness_inputs(root, name)}


def with_default_inputs(name: str, args: list[str], options: list[str], root: Path) -> list[str]:
    supplied = set()
    for index, arg in enumerate(options):
        if arg == "--input" and index + 1 < len(options):
            supplied.add(options[index + 1].split("=", 1)[0])
        elif arg.startswith("--input="):
            supplied.add(arg.removeprefix("--input=").split("=", 1)[0])
    defaults = workflow_defaults(root, name)
    injected = [item for key, value in defaults.items() if key not in supplied
                for item in ("--input", f"{key}={value}")]
    return [args[0], *injected, *args[1:]]


def invoke(root: Path, action: str, args: list[str]) -> int:
    args = list(args)
    runtime_config = None
    options = args[:args.index("--")] if "--" in args else args[:]
    runtime_flags = [a for a in options if a.split("=", 1)[0] == "--runtime-host"]
    if runtime_flags:
        if action != "run" or len(runtime_flags) != 1:
            raise ValueError("--runtime-host supports one foreground run only")
        if any(a.split("=", 1)[0] in {"--detach", "--resume", "-d"} for a in options):
            raise ValueError("Detached/resumed runtime-host mode is unsupported: no public durable ownership contract; use a new foreground run")
        flag = runtime_flags[0]
        index = args.index(flag)
        if "=" in flag:
            runtime_config = flag.split("=", 1)[1]
            del args[index]
        else:
            if index + 1 >= len(options) or options[index + 1].startswith("--"):
                raise ValueError("--runtime-host requires a trusted project configuration path")
            runtime_config = args.pop(index + 1)
            args.pop(index)
        if not runtime_config:
            raise ValueError("--runtime-host requires a configuration path")
    if action == "schedule":
        return schedule(root, args)
    if action not in {"run", "list", "get", "status", "approve", "reject", "respond",
                      "cancel", "resume", "doctor", "halt", "unhalt"}:
        raise ValueError(f"Unknown command '{action}'. Run factory --help for the commands.")
    stop = shared_root(root) / ".factory/STOP"
    if action in {"halt", "unhalt"}:
        if args:
            raise ValueError(f"{action} takes no arguments. Cancel an active run by its run ID.")
        if action == "halt":
            stop.parent.mkdir(parents=True, exist_ok=True)
            stop.write_text("Operator stopped new factory launches.\n", encoding="utf-8")
        else:
            stop.unlink(missing_ok=True)
        print("Local launch brake " + ("set. Active runs require cancel <run-id>." if action == "halt" else "cleared."))
        return 0
    for arg in args:
        if arg.split("=", 1)[0] in {"--cwd", "--workflow-source"}:
            raise ValueError("Factory owns --cwd and --workflow-source; select the application by working directory")
    if action in {"run", "resume", "approve", "respond"} and stop.exists():
        raise ValueError("Local STOP is set. Use unhalt to permit launch/continuation; cancel remains available")
    settings = read_settings(root)
    source = verify_source(settings)
    if action == "doctor":
        print(json.dumps(doctor(settings), indent=2))
        return 0
    if action == "list":
        print(json.dumps({"source": str(source), "revision": settings["revision"],
                          "workflows": sorted(discover(settings, source))}, indent=2))
        return 0
    # These flags must precede caller arguments. Appending after a caller's `--`
    # turns them into message text and silently restores ambient source discovery.
    native = ["workflow", action, "--cwd", str(root)]
    if action == "run":
        if not args:
            raise ValueError("Usage: factory run <shared-workflow> [native options and message]")
        name = args[0]
        if name not in discover(settings, source):
            raise ValueError(f"Shared workflow '{name}' is absent from the pinned SDLC source; no fallback")
        validate(settings, source, name)
        options = args[:args.index("--")] if "--" in args else args
        resuming = any(arg.split("=", 1)[0] == "--resume" for arg in options)
        if not resuming:
            native += ["--workflow-source", str(source)]
            args = with_default_inputs(name, args, options, root)
    native += args
    if action == "status":
        print(f"Factory source={source} revision={settings['revision']} local_STOP={stop.exists()}", file=sys.stderr)
    # Native output, exit code, inputs, identity and gates pass through unchanged.
    # No subprocess deadline or retry can guess whether a native run is alive.
    if runtime_config:
        from runtime_host import RuntimeHost
        with RuntimeHost(root / runtime_config) as host:
            return execute([*cli(settings, source), *native], root, capture=False,
                           timeout=None, env={**os.environ, **host.environment()}).returncode
    return execute([*cli(settings, source), *native], root,
                   capture=False, timeout=None).returncode


def repository_slug(root: Path) -> str:
    remote = checked(["git", "remote", "get-url", "origin"], root).strip()
    match = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?/?$", remote)
    if not match:
        raise ValueError("The origin remote does not name one owner/repo")
    return match[1]


def whoami(settings: dict, source: Path, root: Path) -> str:
    # The CLI prints log records before its JSON answer; read the last object.
    raw = checked([*cli(settings, source), "trigger", "whoami"], root)
    for start in reversed([m.start() for m in re.finditer(r"^\{", raw, re.M)]):
        try:
            data = json.loads(raw[start:])
        except ValueError:
            continue
        if isinstance(data, dict) and isinstance(data.get("runAsUserId"), str):
            return data["runAsUserId"]
    raise ValueError("archon trigger whoami returned no runAsUserId")


def trigger_config(root: Path, settings: dict, source: Path, interval: int) -> dict:
    """An Archon trigger binding for the scheduled shared workflow. Archon owns admission,
    the durable queue and overlap; this only names what to start."""
    schedule_file = shared_root(root) / SCHEDULE
    schedule_data = json.loads(schedule_file.read_text(encoding="utf-8"))
    workflow = schedule_data.get("workflow", "archon-lifecycle")
    inputs = schedule_data.get("inputs")
    if not isinstance(workflow, str) or not isinstance(inputs, dict):
        raise ValueError("schedule.json requires a shared workflow and inputs object")
    if "runtime_host" in schedule_data:
        raise ValueError("A trigger cannot wrap the runtime host. Run it as its own service "
                         "(python factory/runtime_host.py serve --config <runtime.json> "
                         "--connection-file <connection.json>) and drop runtime_host from schedule.json")
    if workflow not in discover(settings, source):
        raise ValueError(f"Shared workflow '{workflow}' is absent from the pinned SDLC source")
    for key in inputs:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError("Invalid scheduled workflow input name")
    values = {**workflow_defaults(root, workflow),
              **{k: v if isinstance(v, str) else json.dumps(v) for k, v in inputs.items()}}
    slug = repository_slug(root)
    return {
        "version": 1,
        "sourceInstanceId": "factory-" + slug.replace("/", "-"),
        "binding": {
            "bindingId": "factory-" + workflow,
            "bindingRevision": None,
            "hostId": "factory-" + re.sub(r"[^A-Za-z0-9-]", "-", platform.node() or "host"),
            "runAsUserId": whoami(settings, source, root),
            # One resource per repository and workflow, so laps never overlap. A tick
            # that arrives while a lap runs is skipped; the next tick starts the next lap.
            "resource": f"github:{slug}:{workflow}",
            "overlap": "skip",
            "launch": {"cwd": str(root), "workflowName": workflow, "inputs": values,
                       "isolation": {"kind": "default"}},
        },
        "schedule": {"intervalSeconds": interval, "runAtLoad": False},
    }


def task_xml(python: Path, root: Path, minutes: int) -> str:
    """A Task Scheduler definition that runs `schedule fire` hidden every N minutes."""
    def text(value: object) -> str:
        return (str(value).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))
    start = time.strftime("%Y-%m-%dT%H:%M:%S")
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Archon factory trigger for {text(root)}</Description></RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <Repetition><Interval>PT{minutes}M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>
      <StartBoundary>{start}</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Hidden>true</Hidden>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{text(python)}</Command>
      <Arguments>factory\\consumer.py schedule fire</Arguments>
      <WorkingDirectory>{text(root)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def timer_commands(root: Path, interval: int) -> dict:
    """What the host scheduler runs. Archon installs launchd jobs itself; elsewhere the
    host scheduler calls `factory schedule fire`, which honors the local STOP brake."""
    fire = [sys.executable, str(root / "factory/consumer.py"), "schedule", "fire"]
    name = "ArchonFactory-" + re.sub(r"[^A-Za-z0-9-]", "-", root.name)
    if sys.platform == "win32":
        # pythonw has no console, so a tick flashes no window; schtasks /TR cannot set a
        # working directory, so the task is defined in XML. It runs only while the user
        # is logged on (a rendered runtime check needs the desktop session) and never
        # starts a second instance while a tick is still running.
        pythonw = Path(fire[0]).with_name("pythonw.exe")
        task = shared_root(root) / ".factory/schedule-task.xml"
        return {"task_xml": {task: task_xml(pythonw if pythonw.exists() else Path(fire[0]),
                                            root, max(1, interval // 60))},
                "install": [["schtasks", "/Create", "/TN", name, "/XML", str(task), "/F"]],
                "remove": [["schtasks", "/Delete", "/TN", name, "/F"]]}
    if sys.platform == "darwin":
        return {"install": [], "remove": []}  # Archon installs its own launchd jobs.
    unit = name.lower()
    # The timer gets what a manual run had: the same tool PATH, credentials from a
    # mode-600 file outside the repository, and IS_SANDBOX for Claude Code as root.
    return {"systemd": {
        f"{unit}.service": "[Unit]\nDescription=Archon factory trigger\n\n[Service]\nType=oneshot\n"
                           f"WorkingDirectory={root}\nEnvironment=PATH={os.environ.get('PATH', '')}\n"
                           "Environment=IS_SANDBOX=1\nEnvironmentFile=-%h/.factory-env\n"
                           f"ExecStart={' '.join(fire)}\n",
        f"{unit}.timer": f"[Unit]\nDescription=Archon factory trigger\n\n[Timer]\nOnBootSec=2min\n"
                         f"OnUnitInactiveSec={interval}s\n\n[Install]\nWantedBy=timers.target\n"},
        "install": [["systemctl", "--user", "daemon-reload"],
                    ["systemctl", "--user", "enable", "--now", f"{unit}.timer"]],
        "remove": [["systemctl", "--user", "disable", "--now", f"{unit}.timer"]]}


def schedule(root: Path, args: list[str]) -> int:
    action = args[0] if args else ""
    flags = args[1:]
    apply = "--apply" in flags
    interval = 300
    if "--interval" in flags:
        interval = int(flags[flags.index("--interval") + 1])
        if interval < 60:
            raise ValueError("--interval must be at least 60 seconds")
    settings = read_settings(root)
    source = verify_source(settings)
    config_path = shared_root(root) / TRIGGER
    stop = shared_root(root) / ".factory/STOP"
    native = cli(settings, source)
    if action == "install":
        config = trigger_config(root, settings, source, interval)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        plan = timer_commands(root, interval)
        print(f"Wrote {config_path} (Archon trigger binding for {config['binding']['launch']['workflowName']}).")
        if sys.platform == "darwin":
            plan = {"install": [[*native, "trigger", "schedule", "install", "--config", str(config_path)],
                                [*native, "workflow", "wake", "schedule", "install", "--interval", "60"]]}
        for task_file, body in plan.get("task_xml", {}).items():
            print(f"--- {task_file}\n{body}")
            if apply:
                task_file.parent.mkdir(parents=True, exist_ok=True)
                task_file.write_text(body, encoding="utf-16")
        if "systemd" in plan:
            unit_dir = Path.home() / ".config/systemd/user"
            for file_name, body in plan["systemd"].items():
                print(f"--- {unit_dir / file_name}\n{body}")
                if apply:
                    unit_dir.mkdir(parents=True, exist_ok=True)
                    (unit_dir / file_name).write_text(body, encoding="utf-8")
        for command in plan["install"]:
            print(("Running: " if apply else "Run to enable: ") + subprocess.list2cmdline(command))
            if apply:
                checked(command, root)
        return 0
    if action == "fire":
        if stop.exists():
            print("Local STOP is set; no trigger fired. Use unhalt to resume.", file=sys.stderr)
            return 0
        if not config_path.is_file():
            raise ValueError("No trigger binding. Run factory schedule install first")
        # A windowless timer tick (pythonw) has no console, so its output goes to a log.
        log = ((shared_root(root) / ".factory/schedule.log").open("a", encoding="utf-8")
               if windowless() else None)
        try:
            if log:
                log.write(f"=== schedule fire {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                log.flush()
            fired = execute([*native, "trigger", "fire", "--config", str(config_path)], root,
                            capture=False, timeout=None, output=log).returncode
            # Resume due durable waits (CI pauses, usage-limit resumes) in the same tick.
            woken = execute([*native, "workflow", "wake"], root, capture=False, timeout=None,
                            output=log).returncode
        finally:
            if log:
                log.close()
        return fired or woken
    if action == "remove":
        plan = timer_commands(root, interval)
        if sys.platform == "darwin" and config_path.is_file():
            plan = {"remove": [[*native, "trigger", "schedule", "remove", "--config", str(config_path)]]}
        for command in plan["remove"]:
            print(("Running: " if apply else "Run to disable: ") + subprocess.list2cmdline(command))
            if apply:
                execute(command, root)
        if apply:
            config_path.unlink(missing_ok=True)
        return 0
    if action == "status":
        return execute([*native, "trigger", "list"], root, capture=False, timeout=None).returncode
    raise ValueError("Usage: factory schedule install|fire|remove|status [--interval <seconds>] [--apply]")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"--help", "-h"}:
        print("factory run <shared-workflow> [native arguments]\n"
              "factory run <shared-workflow> --runtime-host <config.json> [foreground native arguments]\n"
              "Runtime host: fresh ordinary apps; detach/resume unsupported. Manual: python factory/runtime_host.py serve --help\n"
              "factory schedule install [--interval <seconds>] [--apply] | fire | remove [--apply] | status\n"
              "factory list | doctor | status | get <run-id>\n"
              "factory approve | reject | respond | cancel | resume <run-id>\n"
              "factory halt | unhalt (local launch brake only)")
        return 0
    try:
        return invoke(project_root(), args[0], args[1:])
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        print(f"Factory refused: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
