"""Invoke a pinned, complete Archon source installation. No stage policy lives here."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = json.loads((HERE / "pack.json").read_text(encoding="utf-8"))
SETTINGS = ".factory/consumer.json"
ENTRY = "packages/cli/src/cli.ts"


def execute(argv: list[str], cwd: Path, *, capture: bool = True,
            timeout: int | None = 180, env: dict | None = None) -> subprocess.CompletedProcess:
    # Resolve PATH and PATHEXT once, including Windows .cmd launchers.
    argv = [shutil.which(argv[0]) or argv[0], *argv[1:]]
    return subprocess.run(argv, cwd=cwd, capture_output=capture, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, env=env)


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
        raise ValueError("Pinned source has changes; restore it or install a new revision")
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


RETIRED = {
    "accept": "Use factory approve/respond <run-id> for an actual declared Archon gate.",
    "level": "The autonomy dial is retired and cannot authorize work or merges.",
    "arm": "Use an OS timer to invoke factory tick; see the README.",
    "disarm": "Remove the old factory cron/Task Scheduler entries explicitly. Use cancel <run-id> for native runs.",
    "merge": "Use a shared queue workflow when present in the integration source; its gate owns merge authorization.",
    "deploy": "Move deployment into a shared release workflow with an explicit gate.",
    "fix": "Use factory run archon-deliver --adopt <run-id> --input work=<findings file>.",
    "implement": "Use factory run archon-ship --input target=<request>.",
    "triage": "Use factory run archon-triage --input target=<request>.",
    "validate": "Use factory run archon-validate with the producer's declared inputs.",
    "regress": "Use a shared regression workflow when present in the pinned source.",
}


def refuse(action: str) -> int:
    print(f"Retired factory operation '{action}'. " + RETIRED.get(action,
          "Use factory run <shared-workflow> or native status/get/cancel/resume."), file=sys.stderr)
    return 2


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
    if action == "tick":
        if args:
            raise ValueError("tick takes no arguments; configure .factory/schedule.json")
        schedule = json.loads((shared_root(root) / ".factory/schedule.json").read_text(encoding="utf-8"))
        workflow = schedule.get("workflow", "archon-lifecycle")
        inputs = schedule.get("inputs")
        if not isinstance(workflow, str) or not isinstance(inputs, dict):
            raise ValueError("schedule.json requires a shared workflow and inputs object")
        args = [workflow]
        for key, value in inputs.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                raise ValueError("Invalid scheduled workflow input name")
            args += ["--input", key + "=" + (value if isinstance(value, str) else json.dumps(value))]
        host = schedule.get("runtime_host")
        if host:
            if not isinstance(host, str):
                raise ValueError("runtime_host must be a configuration path")
            args += ["--runtime-host", host]
        # Scheduling submits exactly one shared workflow, never individual stages.
        return invoke(root, "run", args)
    if action in RETIRED:
        return refuse(action)
    if action not in {"run", "list", "get", "status", "approve", "reject", "respond",
                      "cancel", "resume", "doctor", "halt", "unhalt"}:
        return refuse(action)
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
        if name in RETIRED:
            return refuse(name)
        if name not in discover(settings, source):
            raise ValueError(f"Shared workflow '{name}' is absent from the pinned SDLC source; no fallback")
        validate(settings, source, name)
        options = args[:args.index("--")] if "--" in args else args
        if "--resume" not in options:
            native += ["--workflow-source", str(source)]
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


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"--help", "-h"}:
        print("factory run <shared-workflow> [native arguments]\n"
              "factory run <shared-workflow> --runtime-host <config.json> [foreground native arguments]\n"
              "Runtime host: fresh ordinary apps; detach/resume unsupported. Manual: python factory/runtime_host.py serve --help\n"
              "factory tick (one scheduled shared workflow, foreground)\n"
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
