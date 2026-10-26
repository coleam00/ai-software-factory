"""Invoke a pinned, complete Archon source installation. No stage policy lives here."""
from __future__ import annotations

import calendar
import contextlib
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
PAUSE = ".factory/PAUSE"
# The shared workflows that carry a merge gate, and the merge policy keys each one accepts.
MERGING_WORKFLOWS = {"archon-merge-queue": ("required_checks", "protected_paths"),
                     "archon-loop": ("protected_paths",)}
# The holdout fence: a Claude Code hook registered in the project's own settings, so every
# lap's checkout carries it. Only the holdout subagents may read .factory/holdout/.
FENCE_SCRIPT = ".factory/hooks/holdout_fence.py"
FENCE_HOOKS = {"PreToolUse": "pre", "PostToolUse": "post"}
FENCE_MATCHERS = {
    "PreToolUse": "Bash|PowerShell|Read|Grep|Glob|Edit|Write|MultiEdit|NotebookEdit|NotebookRead|Agent|Task",
    "PostToolUse": "Bash|PowerShell|Read|Grep|Glob|Agent|Task"}
FENCE_IGNORE = ".factory/holdout/"


def fence_command(mode: str) -> str:
    return f'python "$CLAUDE_PROJECT_DIR/{FENCE_SCRIPT}" {mode}'


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


def fence_report(root: Path) -> str:
    """Prove the holdout fence holds in this checkout, by running the registered hook itself."""
    if not (root / ".factory/holdout/HOLDOUT.md").exists():
        return "no holdout scenarios"
    try:
        settings = json.loads((root / ".claude/settings.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        settings = {}
    registered = {hook.get("command") for entries in settings.get("hooks", {}).values()
                  for entry in entries for hook in entry.get("hooks", [])}
    missing = [event for event, mode in FENCE_HOOKS.items() if fence_command(mode) not in registered]
    if missing:
        raise ValueError(f"Holdout fence not registered in .claude/settings.json for {', '.join(missing)}; rerun factory init")
    ignore = root / ".ignore"
    if not ignore.exists() or FENCE_IGNORE not in ignore.read_text(encoding="utf-8").splitlines():
        raise ValueError(f".ignore does not list {FENCE_IGNORE}; rerun factory init")
    # Laps run in fresh checkouts of the pushed branch, so an uncommitted fence protects nothing there.
    uncommitted = [tracked for tracked in (FENCE_SCRIPT, ".claude/settings.json", ".ignore", ".factory/holdout/HOLDOUT.md")
                   if execute(["git", "ls-files", "--error-unmatch", tracked], root).returncode != 0]
    shell = shutil.which("bash") or shutil.which("sh")
    if shell is None:
        raise ValueError("No bash to run the holdout fence the way Claude Code runs hooks")
    scenarios = str(root / ".factory/holdout/HOLDOUT.md")
    cases = [("a builder reading the scenarios", {"file_path": scenarios}, None, 2),
             ("the verifier reading the scenarios", {"file_path": scenarios}, "holdout-verifier", 0),
             ("a builder reading a source file", {"file_path": str(root / "README.md")}, None, 0)]
    for name, tool_input, agent, expected in cases:
        event = {"tool_name": "Read", "tool_input": tool_input, "cwd": str(root),
                 **({"agent_type": agent} if agent else {})}
        result = subprocess.run([shell, "-c", fence_command("pre")], input=json.dumps(event), text=True,
                                capture_output=True, cwd=root, timeout=60,
                                env={**os.environ, "CLAUDE_PROJECT_DIR": str(root)})
        if result.returncode != expected:
            raise ValueError(f"Holdout fence is open: {name} exited {result.returncode}, expected {expected}. "
                             f"{result.stderr.strip()[:300]} (check that `python` runs Python 3 in bash)")
    if uncommitted:
        return "holds here, but laps will not have it until these are committed and pushed: " + ", ".join(uncommitted)
    return "holds"


def doctor(settings: dict, root: Path | None = None) -> dict:
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
            **({"holdout_fence": fence_report(root)} if root is not None else {}),
            "provider_configuration": "native configuration preserved; authentication not live-tested"}


def harness_inputs(root: Path, name: str) -> dict:
    # A project's merge policy is a fact the merge gate reads, not something an
    # agent decides. GitHub cannot report required checks on plans without branch
    # protection, and nothing on GitHub says which files only a human may change,
    # so the project declares both once in its harness configuration.
    if name not in MERGING_WORKFLOWS:
        return {}
    try:
        config = json.loads((root / "harness/harness.config.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    if not isinstance(config, dict):
        return {}
    return {key: config[key] for key in MERGING_WORKFLOWS[name]
            if isinstance(config.get(key), str) and config[key].strip()}


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
    if action not in {"run", "list", "get", "status", "runs", "abandon", "approve", "reject", "respond",
                      "cancel", "resume", "doctor", "halt", "unhalt", "pause", "unpause"}:
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
    if action in {"pause", "unpause"}:
        # Launch-only: the timer stops starting laps but keeps waking durable waits, so a lap
        # already in flight (paused on CI, a usage limit) still finishes. Disabling the host
        # timer instead strands it; halt blocks its continuation by design.
        if args:
            raise ValueError(f"{action} takes no arguments")
        pause = shared_root(root) / PAUSE
        if action == "pause":
            pause.parent.mkdir(parents=True, exist_ok=True)
            pause.write_text("Operator paused scheduled launches; wakes continue.\n", encoding="utf-8")
        else:
            pause.unlink(missing_ok=True)
        print("Scheduled launches " + ("paused. The timer still wakes active runs; explicit runs still launch."
                                       if action == "pause" else "resumed."))
        return 0
    for arg in args:
        if arg.split("=", 1)[0] in {"--cwd", "--workflow-source"}:
            raise ValueError("Factory owns --cwd and --workflow-source; select the application by working directory")
    if action in {"run", "resume", "approve", "respond"} and stop.exists():
        raise ValueError("Local STOP is set. Use unhalt to permit launch/continuation; cancel remains available")
    settings = read_settings(root)
    source = verify_source(settings)
    if action == "doctor":
        print(json.dumps(doctor(settings, root), indent=2))
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


def base_branch(root: Path, schedule_data: dict) -> str | None:
    """The branch laps start from: schedule.json `base_branch`, else what the last fetch
    recorded as the remote's default (origin/HEAD), read locally. None when neither is
    known; the launch then asks the remote itself."""
    named = schedule_data.get("base_branch")
    if named is not None:
        if not isinstance(named, str) or not re.fullmatch(r"[A-Za-z0-9._/-]+", named):
            raise ValueError("schedule.json base_branch must be a branch name")
        return named
    result = execute(["git", "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"], root)
    head = result.stdout.strip() if result.returncode == 0 else ""
    return head.split("/", 1)[1] if head.startswith("origin/") else None


def trigger_config(root: Path, settings: dict, source: Path, interval: int) -> dict:
    """An Archon trigger binding for the scheduled shared workflow. Archon owns admission,
    the durable queue and overlap; this only names what to start."""
    schedule_file = shared_root(root) / SCHEDULE
    schedule_data = json.loads(schedule_file.read_text(encoding="utf-8"))
    workflow = schedule_data.get("workflow", "archon-loop")
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
    parallel = schedule_data.get("parallel", 1)
    if not isinstance(parallel, int) or isinstance(parallel, bool) or not 1 <= parallel <= 8:
        raise ValueError("schedule.json parallel must be a whole number of laps from 1 to 8")
    slug = repository_slug(root)
    # Archon fixes a slot's capacity when the slot is first used, so a different number
    # of parallel laps is a different slot.
    resource = f"github:{slug}:{workflow}" + (f":x{parallel}" if parallel > 1 else "")
    # Without a known base branch every launch asks the remote for its default branch
    # (one ls-remote, no retry); when that fails the run never starts and holds its slot.
    base = base_branch(root, schedule_data)
    isolation = {"kind": "worktree", "baseOverride": base} if base else {"kind": "default"}
    return {
        "version": 1,
        "sourceInstanceId": "factory-" + slug.replace("/", "-"),
        "binding": {
            "bindingId": "factory-" + workflow,
            "bindingRevision": None,
            "hostId": "factory-" + re.sub(r"[^A-Za-z0-9-]", "-", platform.node() or "host"),
            "runAsUserId": whoami(settings, source, root),
            # One resource per repository and workflow, holding at most `parallel` laps.
            # A tick that arrives while every lap is busy is skipped; the next tick
            # starts the next lap. The workflow keeps parallel laps off each other's work.
            "resource": resource,
            "capacity": parallel,
            "overlap": "skip",
            "launch": {"cwd": str(root), "workflowName": workflow, "inputs": values,
                       "isolation": isolation},
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
    <MultipleInstancesPolicy>Parallel</MultipleInstancesPolicy>
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
        # is logged on (a rendered runtime check needs the desktop session). Ticks run in
        # parallel: a wake that resumes a long lap must not hold back the next tick's
        # launch. `schedule fire` single-flights each phase itself, so ticks cannot stack.
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


@contextlib.contextmanager
def single_flight(path: Path):
    """Yield True to the one process holding an OS lock on `path`, False to the rest.
    The lock dies with its process, so a killed tick never wedges later ones."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+", encoding="utf-8")
    try:
        try:
            if sys.platform == "win32":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        yield True
    finally:
        handle.close()


ENDED = {"completed", "failed", "cancelled"}
ACTIVE = {"pending", "running", "paused"}
# A launch that died before its run started has written its last line by now.
STUCK_AFTER_SECONDS = 600


def archon_home() -> Path:
    return Path(os.environ.get("ARCHON_HOME") or Path.home() / ".archon")


def sweep_stuck(root: Path, settings: dict, source: Path, runs: list[dict], write) -> set[str]:
    """Abandon runs whose launch failed before they started. Such a run stays `pending`
    with its slot held until someone abandons it; its launch log ends in the error."""
    swept = set()
    for run in runs:
        if run.get("status") != "pending" or not isinstance(run.get("id"), str):
            continue
        launch_log = archon_home() / "logs" / f"trigger-run-{run['id']}.log"
        try:
            if time.time() - launch_log.stat().st_mtime < STUCK_AFTER_SECONDS:
                continue
            lines = [line for line in launch_log.read_text(encoding="utf-8", errors="replace").splitlines()
                     if line.strip()]
        except OSError:
            continue
        if not lines or not lines[-1].startswith("Error:"):
            continue
        result = execute([*cli(settings, source), "workflow", "abandon", run["id"], "--cwd", str(root)], root)
        if result.returncode == 0:
            swept.add(run["id"])
            write(f"Abandoned run {run['id']}, whose launch failed before it started: {lines[-1][:300]}\n")
        else:
            write(f"Could not abandon stuck run {run['id']}: {(result.stderr or result.stdout)[-300:]}\n")
    return swept


# A running lap writes its log at least every half hour (measured 2026-10-25 over 150 runs:
# the longest healthy silence was 29.5 min). One silent for an hour has died without ending:
# seen live, a lap whose review step crashed held the only slot for five hours.
SILENT_AFTER_SECONDS = 3600


def sweep_silent(root: Path, settings: dict, source: Path, runs: list[dict], write) -> set[str]:
    """Abandon running runs whose log has been silent for SILENT_AFTER_SECONDS. Abandoning
    frees the slot and removes the worktree but keeps the run's branch, so committed work
    survives for the next lap; uncommitted changes do not."""
    swept = set()
    for run in runs:
        if run.get("status") != "running" or not isinstance(run.get("id"), str):
            continue
        logs = list((archon_home() / "workspaces").glob(f"*/*/logs/{run['id']}.jsonl"))
        try:
            newest = max(log.stat().st_mtime for log in logs) if logs else None
        except OSError:
            continue
        if newest is None or time.time() - newest < SILENT_AFTER_SECONDS:
            continue
        result = execute([*cli(settings, source), "workflow", "abandon", run["id"], "--cwd", str(root)], root)
        if result.returncode == 0:
            swept.add(run["id"])
            write(f"Abandoned run {run['id']}: its log has been silent for "
                  f"{int((time.time() - newest) // 60)} min\n")
        else:
            write(f"Could not abandon silent run {run['id']}: {(result.stderr or result.stdout)[-300:]}\n")
    return swept


def tick_state(root: Path, **updates) -> dict:
    """The tick's small memory between runs (.factory/gate.json), updated in place."""
    path = shared_root(root) / ".factory/gate.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    if updates:
        state.update(updates)
        path.write_text(json.dumps(state) + "\n", encoding="utf-8")
    return state


EMPTY_LAP_SECONDS = 300
CHOOSING_SECONDS = 1800


def _when(value) -> float | None:
    try:
        return calendar.timegm(time.strptime(str(value)[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, OverflowError):
        return None


def empty_lap_since(runs: list[dict], since: float) -> bool:
    """Whether a lap started at or after `since` completed within a few minutes: it picked
    nothing. A lap that failed (a usage limit, an outage) says nothing about the board."""
    for run in runs:
        start, end = _when(run.get("started_at")), _when(run.get("completed_at"))
        if start is None or end is None or start + 60 < since:
            continue
        if run.get("status") == "completed" and end - start < EMPTY_LAP_SECONDS:
            return True
    return False


def launch_decision(root: Path, settings: dict, source: Path, write) -> bool:
    """Whether this tick should start a lap. A workflow without a launch gate always
    launches. With one, the tick first clears runs that never started and claims of runs
    that ended, then launches only when there is workable work and no lap is still
    choosing its item, plus one lap per idle period for bookkeeping. Any failure to read
    the board launches, as before the gate existed."""
    try:
        binding = json.loads((shared_root(root) / TRIGGER).read_text(encoding="utf-8"))["binding"]
        launch_config = binding["launch"]
        workflow, inputs = launch_config["workflowName"], launch_config.get("inputs") or {}
        capacity = binding.get("capacity")
    except (OSError, ValueError, KeyError, TypeError):
        return True
    try:
        schedule_data = json.loads((shared_root(root) / SCHEDULE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        schedule_data = {}
    gate = MANIFEST.get("launch_gates", {}).get(workflow)
    script = source / MANIFEST["source_directory"] / gate if gate else None
    if script is None or not script.is_file():
        return True
    state = tick_state(root)

    signature = ""

    def launch(reason: str) -> bool:
        write(f"Launch: {reason}\n")
        tick_state(root, last_launch=time.time(), last_launch_board=signature)
        return True

    try:
        listed = native_json(settings, source, ["workflow", "runs", "--limit", "100"], root)
        runs = [run for run in listed.get("runs", []) if run.get("workflow_name") == workflow]
        swept = sweep_stuck(root, settings, source, runs, write) | sweep_silent(root, settings, source, runs, write)
        ended = [run["id"] for run in runs if run.get("status") in ENDED]
        active = {run["id"] for run in runs if run.get("status") in ACTIVE} - swept
        result = execute([settings.get("bun", "bun"), str(script)], root, timeout=180, env={
            **os.environ,
            "INPUTS_CLAIM_HOURS": str(inputs.get("claim_hours", "")),
            "INPUTS_ENDED_RUNS": ",".join(ended)})
        if result.returncode:
            raise ValueError((result.stderr or result.stdout)[-500:])
        board = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        return launch(f"the launch gate could not read the board ({str(error)[:300]}).")
    for claim in board.get("ended", []):
        write(f"Released the claim run {claim['run']} left on #{claim['number']} when it ended.\n")
    workable = board.get("workable", [])
    # A lap is still choosing only early on: picking takes minutes. A long lap whose claim aged
    # out is not choosing, and counting it as such held every launch back (seen live: an
    # 8-hour lap blocked the second slot for over an hour).
    recent = {run["id"] for run in runs if run.get("id") in active and
              (_when(run.get("started_at")) or time.time()) >= time.time() - CHOOSING_SECONDS}
    choosing = recent - {claim["run"] for claim in board.get("claims", [])}
    names = ", ".join(f"#{item['number']}" for item in workable) or "nothing"
    # The board as a lap would see it. A lap that ended within minutes of starting picked
    # nothing (a real lap takes far longer); if the board is unchanged since that launch,
    # another lap would see the same and pick nothing too, so wait for the board to change
    # (seen live: items the gate counts as workable that pick stays clear of, because they
    # touch what a live claim is changing).
    # `updated` (the newest change to any open item) makes an answer count as a change: an
    # owner decision that lifts a park leaves the same workable items and claims as before
    # the park (seen live: the factory idled 40 minutes after the owner unblocked an item).
    signature = json.dumps([sorted(f"{item.get('kind')}:{item.get('number')}" for item in workable),
                            sorted(claim["run"] for claim in board.get("claims", [])),
                            str(board.get("updated", ""))])
    # Archon skips a start when every slot is busy; say so instead of recording a launch.
    if isinstance(capacity, int) and len(active) >= capacity:
        write(f"No launch: all {capacity} slot(s) busy; workable {names}.\n")
        return False
    if workable and not choosing and signature == state.get("last_launch_board") and \
            empty_lap_since(runs, state.get("last_launch", 0)):
        write(f"No launch: the last lap found nothing to start on this same board (workable {names}).\n")
        return False
    # One pick at a time: two laps choosing at once converge on the same item and the
    # later one loses the claim race (seen live). The next tick sees the first lap's claim.
    if workable and not choosing:
        return launch(f"workable {names}; no lap still choosing.")
    idle = schedule_data.get("idle_pick_minutes", 60)
    if not isinstance(idle, (int, float)) or isinstance(idle, bool) or idle < 0:
        raise ValueError("schedule.json idle_pick_minutes must be a number of minutes (0 turns it off)")
    if idle and not active and time.time() - state.get("last_launch", 0) >= idle * 60:
        return launch(f"nothing workable, but no lap has looked in {idle} minutes (bookkeeping).")
    write(f"No launch: workable {names}; {len(active)} lap(s) active, {len(choosing)} still choosing.\n")
    return False


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
        base = config["binding"]["launch"]["isolation"].get("baseOverride")
        print(f"Laps start from {base}." if base else
              "No default branch is known locally, so every launch asks the remote for it. "
              "Run `git remote set-head origin --auto` or set base_branch in schedule.json.")
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
            write = log.write if log else sys.stderr.write
            fired = 0
            if (shared_root(root) / PAUSE).exists():
                write("Scheduled launches paused; no trigger fired, waking only. Use unpause to resume.\n")
            else:
                # Ticks run in parallel (a long wake must not hold back the next launch), so
                # each phase admits one tick at a time; a tick that finds a phase busy skips it.
                with single_flight(shared_root(root) / ".factory/fire.lock") as held:
                    if not held:
                        write("Another tick is still launching; this one skips the launch.\n")
                    elif launch_decision(root, settings, source, write):
                        fired = execute([*native, "trigger", "fire", "--config", str(config_path)], root,
                                        capture=False, timeout=None, output=log).returncode
            if log:
                log.flush()
            # Resume due durable waits (CI pauses, usage-limit resumes) in the same tick.
            woken = 0
            with single_flight(shared_root(root) / ".factory/wake.lock") as held:
                if held:
                    woken = execute([*native, "workflow", "wake"], root, capture=False, timeout=None,
                                    output=log).returncode
                else:
                    write("A wake from an earlier tick is still running; this one skips it.\n")
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
              "factory list | doctor | status | runs | get <run-id>\n"
              "factory approve | reject | respond | cancel | resume | abandon <run-id>\n"
              "factory halt | unhalt (local launch brake only)\n"
              "factory pause | unpause (stop scheduled launches; the timer keeps waking active runs)")
        return 0
    try:
        return invoke(project_root(), args[0], args[1:])
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        print(f"Factory refused: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
