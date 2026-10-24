"""The holdout fence: only the holdout subagents may see .factory/holdout/, and nothing may change the fence.

Claude Code runs this before every tool call (`pre`) and tags each call a subagent makes with its
`agent_type`. Calls from `holdout-writer` and `holdout-verifier` pass. Every other call (builders,
reviewers, the manager itself, a human session) is refused, by exit 2, when it would show a scenario:

- it names the folder or a HOLDOUT.md, or globs into `.factory`;
- it is a recursive search whose scope covers the folder. The Grep and Glob tools skip the folder
  through `.ignore`; shell `grep -r`, `git grep` and `rg --no-ignore` do not;
- it is a `git diff`, `git log -p` or `git show` whose output would include a file in the folder.
  Git is asked with `--name-only`, so a command that cannot reach the folder is never refused.

Any call that would change the fence itself (this folder, `.claude/settings.json`, `.ignore`) is
refused for everyone. After each call (`post`) the result is checked for scenario text, and a hit is
appended to `holdout-exposures.jsonl` in the repository's git directory, so a leak the fence did not
foresee is counted instead of invisible.

Claude Code lets a call through when a hook errors, so `pre` fails closed: an error is a refusal.
"""

import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

HOLDOUT_AGENTS = {"holdout-writer", "holdout-verifier"}
FOLDER = ".factory/holdout"
# Tools that read, search or change files, or hand work to another agent. Output-only tools
# (StructuredOutput, TodoWrite, ...) are never refused for mentioning the folder.
ACCESS_TOOLS = {"Bash", "PowerShell", "Read", "Grep", "Glob", "Edit", "Write", "MultiEdit",
                "NotebookEdit", "NotebookRead", "Agent", "Task"}
WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "PowerShell"}

NAMED = re.compile(r"\.factory/+(?:holdout(?![\w-])|[*?\[])|(?<![\w-])holdout\.md\b", re.I)
FENCE = re.compile(r"\.factory/+hooks(?![\w-])|\.claude/+settings(?:\.local)?\.json|(?<![\w.])\.ignore\b", re.I)
MUTATES = re.compile(
    r">|\btee\b|\b(?:sed|perl)\s+-\w*i|\b(?:rm|mv|cp|chmod|truncate|ln|install)\b|"
    r"\bgit\s+(?:checkout|restore|rm|mv|reset|stash)\b|Set-Content|Add-Content|Out-File|"
    r"Remove-Item|Move-Item|Copy-Item|Rename-Item", re.I)
SEGMENT = re.compile(r"\s*(?:&&|\|\||;|\||\n)\s*")
NO_PATCH = {"--stat", "--name-only", "--name-status", "--numstat", "--shortstat", "--summary",
            "-s", "--no-patch", "--quiet", "--exit-code", "--raw"}
LOG_PATCH = re.compile(r"^(?:-p|--patch|-u|-U\d*|--unified(?:=\d+)?|--full-diff|-m|--cc|-c)$")
PROBE_DROP = re.compile(r"^(?:-p|--patch|-u|-U\d*|--unified(?:=\d+)?|--stat(?:=.*)?|--color(?:=.*)?|"
                        r"--format(?:=.*)?|--pretty(?:=.*)?|--oneline|--graph|-w|--word-diff(?:=.*)?)$")
GREP_ARG_OPTS = {"-e", "-f", "-A", "-B", "-C", "-m", "-d", "-D", "--context", "--max-count", "--include",
                 "--exclude", "--exclude-dir", "--regexp", "--file"}


def refuse(reason: str) -> None:
    print(f"Blocked by the holdout fence: {reason}. The holdout scenarios are for the "
          "holdout-verifier and holdout-writer subagents only; scope searches and diffs to source "
          "folders (or exclude .factory).", file=sys.stderr)
    sys.exit(2)


def normal(path: str) -> str:
    """A comparable absolute path: forward slashes, Git Bash drive form folded, case folded on Windows."""
    path = path.strip().strip("'\"").replace("\\", "/")
    drive = re.match(r"^/([a-zA-Z])(/|$)", path)
    if drive and os.name == "nt":
        path = f"{drive.group(1)}:/{path[3:]}"
    path = os.path.normpath(os.path.expanduser(path)).replace("\\", "/")
    return path.lower() if os.name == "nt" else path


def resolve(path: str, cwd: str) -> str:
    path = path.strip().strip("'\"")
    static = re.split(r"[*?\[{]", path, maxsplit=1)[0]
    if static != path:
        static = static.rsplit("/", 1)[0] if "/" in static else ""
    raw = static.replace("\\", "/")
    if raw == "" or not (raw.startswith("/") or re.match(r"^[a-zA-Z]:", raw) or raw.startswith("~")):
        raw = os.path.join(cwd, raw) if raw else cwd
    return normal(raw)


def covers(path: str, holdout: str) -> bool:
    path = path.rstrip("/")
    return holdout == path or holdout.startswith(path + "/")


def tokens(segment: str, posix: bool = True) -> list[str]:
    try:
        words = [w.strip("'\"") for w in shlex.split(segment, posix=posix)]
    except ValueError:
        words = segment.split()
    while words and re.match(r"^\w+=", words[0]):
        words = words[1:]
    return words


def excluded(words: list[str]) -> bool:
    """The search excludes .factory (or the holdout folder) itself."""
    text = " ".join(words).replace("\\", "/")
    return bool(re.search(r"(--exclude-dir[= ]\S*(\.factory|holdout|\.\*))|(:\(exclude\)|:!|:\^)\S*\.factory"
                          r"|(-g|--glob)[= ]['\"]?!\S*\.factory", text, re.I))


def grep_paths(args: list[str]) -> tuple[bool, list[str]]:
    recursive, positional, has_pattern_opt, skip = False, [], False, False
    for index, arg in enumerate(args):
        if skip:
            skip = False
            continue
        if arg == "--":
            positional.extend(args[index + 1:])
            break
        if arg.startswith("--"):
            recursive |= arg in ("--recursive", "--dereference-recursive")
            has_pattern_opt |= arg.startswith(("--regexp", "--file"))
            skip = arg in GREP_ARG_OPTS
        elif arg.startswith("-") and len(arg) > 1:
            letters = arg[1:]
            recursive |= "r" in letters or "R" in letters
            has_pattern_opt |= "e" in letters or "f" in letters
            skip = arg in GREP_ARG_OPTS or letters[-1:] in ("e", "f", "A", "B", "C", "m")
        else:
            positional.append(arg)
    paths = positional if has_pattern_opt else positional[1:]
    return recursive, paths


def git_probe(words: list[str], cwd: str, holdout_rel: str) -> bool:
    """Whether a git diff/log/show would print a file under the holdout folder."""
    index, base = 1, cwd
    while index < len(words) and words[index].startswith("-"):
        if words[index] in ("-C", "-c") and index + 1 < len(words):
            base = resolve(words[index + 1], cwd) if words[index] == "-C" else base
            index += 2
        else:
            index += 1
    if index >= len(words) or words[index] not in ("diff", "log", "show", "whatchanged"):
        return False
    sub, rest = words[index], words[index + 1:]
    if any(arg in NO_PATCH for arg in rest) and not any(LOG_PATCH.match(arg) for arg in rest):
        return False
    if sub in ("log", "whatchanged") and not any(LOG_PATCH.match(arg) for arg in rest):
        return False
    probe = ["git", "-C", base, sub, "--name-only"] + (["--format="] if sub != "diff" else [])
    probe += [arg for arg in rest if not PROBE_DROP.match(arg)]
    try:
        out = subprocess.run(probe, capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return any(line.strip().replace("\\", "/").startswith(holdout_rel + "/") for line in out.splitlines())


def check_shell(command: str, cwd: str, holdout: str, posix: bool) -> None:
    for segment in SEGMENT.split(command):
        words = tokens(segment, posix)
        if not words:
            continue
        program = words[0].rsplit("/", 1)[-1].lower()
        if program == "cd" and len(words) > 1:
            cwd = resolve(words[1], cwd)
            continue
        if program in ("grep", "egrep", "fgrep"):
            recursive, paths = grep_paths(words[1:])
            if recursive and not excluded(words) and any(covers(resolve(p, cwd), holdout) for p in paths or ["."]):
                refuse(f"`{segment.strip()[:80]}` searches the folder")
        elif program == "rg":
            no_ignore = any(re.match(r"^(-u+|--no-ignore(-dot|-files|-vcs)?)$", w) for w in words[1:])
            if no_ignore and not excluded(words):
                paths = [w for w in words[1:] if not w.startswith("-")][1:]
                if any(covers(resolve(p, cwd), holdout) for p in paths or ["."]):
                    refuse(f"`{segment.strip()[:80]}` searches the folder without .ignore")
        elif program == "git" and "grep" in words[1:3]:
            rest = words[words.index("grep") + 1:]
            specs = rest[rest.index("--") + 1:] if "--" in rest else []
            if not excluded(words) and any(covers(resolve(p, cwd), holdout) for p in specs or ["."]):
                refuse(f"`{segment.strip()[:80]}` searches the folder")
        elif program == "git" and git_probe(words, cwd, FOLDER):
            refuse(f"`{segment.strip()[:80]}` would print a holdout file")


def pre(event: dict) -> None:
    tool = event.get("tool_name", "")
    tool_input = event.get("tool_input") or {}
    text = json.dumps(tool_input).replace("\\\\", "/")
    holdout_agent = event.get("agent_type") in HOLDOUT_AGENTS
    target = json.dumps([tool_input.get(key, "") for key in ("file_path", "notebook_path")]).replace("\\\\", "/")
    if tool in WRITE_TOOLS and FENCE.search(target):
        refuse("the fence itself only changes by hand")
    if tool in SHELL_TOOLS and FENCE.search(text) and MUTATES.search(tool_input.get("command", "")):
        refuse("the fence itself only changes by hand")
    if holdout_agent or tool not in ACCESS_TOOLS:
        return
    if tool in ("Agent", "Task") and tool_input.get("subagent_type") in HOLDOUT_AGENTS:
        return
    if NAMED.search(text):
        refuse("the call names the holdout folder")
    if tool in SHELL_TOOLS:
        root = normal(os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or os.getcwd())
        cwd = normal(event.get("cwd") or root)
        check_shell(tool_input.get("command", ""), cwd, f"{root}/{FOLDER}", tool == "Bash")


def strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for item in value.values() for s in strings(item)]
    if isinstance(value, list):
        return [s for item in value for s in strings(item)]
    return []


def squash(text: str) -> str:
    return " ".join(text.lower().split())


def post(event: dict) -> None:
    if event.get("agent_type") in HOLDOUT_AGENTS:
        return
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or os.getcwd())
    scenarios = root / FOLDER / "HOLDOUT.md"
    if not scenarios.is_file():
        return
    current, prints = "", {}
    for line in scenarios.read_text(encoding="utf-8", errors="replace").splitlines():
        heading = re.match(r"^#{2,3}\s+(\S+)", line)
        if heading:
            current = heading.group(1)
        flat = squash(line.lstrip("#"))
        if len(flat) >= 40:
            prints[flat] = current
    seen = squash("\n".join(strings(event.get("tool_response"))))
    hits = sorted({scenario for flat, scenario in prints.items() if flat in seen})
    if not hits:
        return
    common = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=root, capture_output=True,
                            text=True, timeout=20).stdout.strip()
    log = (root / common if common else root / ".git") / "holdout-exposures.jsonl"
    with open(log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                 "session": event.get("session_id", ""), "agent": event.get("agent_type", ""),
                                 "tool": event.get("tool_name", ""), "scenarios": hits}) + "\n")


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "pre"
    try:
        event = json.load(sys.stdin)
        (post if mode == "post" else pre)(event)
    except SystemExit:
        raise
    except Exception as error:  # noqa: BLE001 - any failure must refuse, never let the call through
        if mode == "post":
            sys.exit(0)
        refuse(f"the fence could not check this call ({type(error).__name__}: {error})")
    sys.exit(0)


if __name__ == "__main__":
    main()
