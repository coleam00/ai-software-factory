"""Component 2, the trigger. Built LAST on purpose.

    python factory/trigger.py --status
    python factory/trigger.py --install
    python factory/trigger.py --remove

TURNING ON A SCHEDULER IS THE MOMENT THE REPO BECOMES AUTONOMOUS. Everything before
it can be run by hand and inspected. A factory whose dispatcher was built first is an
unsupervised code generator that nobody has ever checked.

IT REFUSES BELOW DIAL 1. A scheduler at level 0 wakes up forever and correctly does
nothing, which is exactly how people convince themselves a factory is running when it
has never completed a lap.

NOTHING PUSHES. There is no webhook and there is not meant to be one: this wakes on a
timer, reads the state, and dispatches at most MAX_PARALLEL things. An issue filed at
09:01 waits for the next tick. A push trigger that breaks fails SILENTLY and looks
exactly like a factory with nothing to do; a poll that breaks is a poll you can see
not running.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402

DISPATCH = f'"{sys.executable}" factory/dispatch.py'
REGRESS = f'"{sys.executable}" factory/regress-trigger.py'


def record(kind: str, detail: str) -> None:
    config.TRIGGER_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.TRIGGER_FILE.write_text(
        json.dumps(
            {
                "kind": kind,
                "detail": detail,
                "interval_minutes": config.INTERVAL_MINUTES,
                "armed_at": datetime.now(timezone.utc).isoformat(),
                "autonomy_at_arming": config.AUTONOMY,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def status() -> int:
    if config.TRIGGER_FILE.exists():
        print(config.TRIGGER_FILE.read_text(encoding="utf-8"))
    else:
        print("NOT_ARMED - the factory only runs when you run it.")
        print()
        print("  A fully built factory with nothing scheduled audits identically to a")
        print("  running one. That is why this is a check and not a comment.")
    return 0


CRON_ENV_PREFIXES = (
    "FACTORY_", "CLAUDE_", "ANTHROPIC_", "ARCHON_", "OPENAI_", "OPENROUTER_",
    "GEMINI_", "GOOGLE_", "KIMI_", "MOONSHOT_", "DEEPSEEK_", "MINIMAX_", "XAI_",
    "GH_", "GITHUB_",
)


def write_cron_env() -> Path:
    """Snapshot the arming shell's environment for cron, which has none.

    A crontab entry runs with PATH=/usr/bin:/bin and nothing else. The engine lives in
    ~/.bun/bin, the coding agent in ~/.local/bin, and its login is an environment
    variable that only the interactive shell exports. So the shipped entry woke up
    every N minutes, found neither `archon` nor a logged-in agent, and wrote an error to
    a log nobody reads -- which is indistinguishable, from the outside, from a factory
    with nothing to do. Found by running the tick under `env -i` on a real VPS.

    PATH plus every variable a provider or this factory reads, shell-quoted, mode 600,
    gitignored. The cron line sources it before dispatching, so the tick sees exactly
    what `factory arm` saw.
    """
    env_file = config.SHARED / ".factory" / "cron.env"
    env_file.parent.mkdir(parents=True, exist_ok=True)
    keep = {k: v for k, v in os.environ.items()
            if k == "PATH" or k == "HOME" or k.startswith(CRON_ENV_PREFIXES)}
    body = "\n".join(f"export {k}={_sh_quote(v)}" for k, v in sorted(keep.items())) + "\n"
    env_file.write_text("# written by `factory arm`; re-arm to refresh\n" + body, encoding="utf-8")
    try:
        os.chmod(env_file, 0o600)
    except OSError:
        pass
    return env_file


def _sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def install_cron() -> int:
    rc = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=60)
    existing = rc.stdout if rc.returncode == 0 else ""
    if config.TASK_NAME in existing:
        print(f"ALREADY_ARMED: a crontab entry for {config.TASK_NAME} exists")
        return 0

    log = config.SHARED / ".factory" / "factory.log"
    env_file = write_cron_env()
    lines = [
        existing.rstrip(),
        f"# {config.TASK_NAME} -- the factory dispatcher",
        f"*/{config.INTERVAL_MINUTES} * * * * cd {config.SHARED} && . {env_file} && {DISPATCH} >> {log} 2>&1",
        f"# {config.TASK_NAME} -- the scheduled regression",
        f"{config.REGRESS_CRON} cd {config.SHARED} && . {env_file} && {REGRESS} >> {log} 2>&1",
        "",
    ]
    p = subprocess.run(["crontab", "-"], input="\n".join(lines), text=True, timeout=60)
    if p.returncode != 0:
        print("ARM_FAILED: crontab refused the entry", file=sys.stderr)
        return 1
    record("cron", f"*/{config.INTERVAL_MINUTES} * * * *")
    print(f"ARMED cron every {config.INTERVAL_MINUTES} minutes; log -> {log}")
    return 0


def _weekly_from_cron(spec: str) -> tuple[str, str]:
    """`m h * * dow` -> ("MON", "06:00") for schtasks.

    Only the shape this factory ships is parsed. Anything else falls back to the
    documented default and SAYS SO -- silently scheduling a regression at a time
    nobody chose is how you find out months later that it never ran on the day you
    thought it did.
    """
    days = {"0": "SUN", "1": "MON", "2": "TUE", "3": "WED", "4": "THU", "5": "FRI",
            "6": "SAT", "7": "SUN"}
    parts = spec.split()
    try:
        minute, hour, _, _, dow = parts[0], parts[1], parts[2], parts[3], parts[4]
        return days[dow.split(",")[0]], f"{int(hour):02d}:{int(minute):02d}"
    except (IndexError, KeyError, ValueError):
        print(
            f"  ! FACTORY_REGRESS_CRON={spec!r} is not a plain 'm h * * dow'; "
            f"scheduling the regression MON 06:00 instead"
        )
        return "MON", "06:00"


def install_regress_task_scheduler() -> int:
    """Windows. The weekly re-test of what already merged."""
    log = config.SHARED / ".factory" / "factory.log"
    day, at = _weekly_from_cron(config.REGRESS_CRON)
    name = f"{config.TASK_NAME}-regress"
    cmd = f'cmd /c "cd /d {config.SHARED} && {REGRESS} >> {log} 2>&1"'
    p = subprocess.run(
        [
            "schtasks", "/Create", "/F",
            "/SC", "WEEKLY", "/D", day, "/ST", at,
            "/TN", name,
            "/TR", cmd,
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    if p.returncode != 0:
        print(f"REGRESS_ARM_FAILED: {p.stdout} {p.stderr}", file=sys.stderr)
        return 1
    print(f"ARMED Task Scheduler task '{name}' weekly, {day} {at}")
    return 0


def install_task_scheduler() -> int:
    """Windows. Note it only runs while someone is logged in unless configured
    otherwise -- a detail that presents as "the factory stopped overnight"."""
    log = config.SHARED / ".factory" / "factory.log"
    cmd = (
        f'cmd /c "cd /d {config.SHARED} && {DISPATCH} >> {log} 2>&1"'
    )
    p = subprocess.run(
        [
            "schtasks", "/Create", "/F",
            "/SC", "MINUTE", "/MO", str(config.INTERVAL_MINUTES),
            "/TN", config.TASK_NAME,
            "/TR", cmd,
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    if p.returncode != 0:
        print(f"ARM_FAILED: {p.stdout} {p.stderr}", file=sys.stderr)
        return 1
    # THE REGRESSION GETS ITS OWN SCHEDULE, and it did not.
    #
    # The cron path installs two entries -- the dispatcher and the weekly regression.
    # This path installed one, and said "ARMED". The doctor then reported the factory
    # as armed, because a trigger file existed. So a Windows factory could be fully
    # armed, fully green, and never once re-test what it had already merged: the
    # component whose entire job is noticing that merged code stopped working simply
    # was not there.
    #
    # That is the "audits identically to a running one" failure the trigger check was
    # written to prevent, reproduced one level down inside the thing that installs it.
    regress_rc = install_regress_task_scheduler()

    record("schtasks", config.TASK_NAME)
    print(f"ARMED Task Scheduler task '{config.TASK_NAME}' every {config.INTERVAL_MINUTES} minutes")
    if regress_rc != 0:
        print(
            f"  ! the weekly regression task was NOT installed. Merged code will never "
            f"be re-tested; run `schtasks /Query /TN {config.TASK_NAME}-regress` to check"
        )
    print()
    print("  NOTE: a Task Scheduler task only runs while someone is logged in unless")
    print("  you configure it otherwise. That detail presents as 'the factory stopped")
    print("  overnight', which looks exactly like a factory with nothing to do.")
    return 0


def remove() -> int:
    removed = False
    if os.name == "nt":
        # BOTH. Removing only the dispatcher leaves the weekly regression running
        # against a factory nobody is dispatching for -- it would keep filing issues
        # into a queue that never moves, which is a worse state than either armed or
        # disarmed and belongs to neither.
        for name in (config.TASK_NAME, f"{config.TASK_NAME}-regress"):
            p = subprocess.run(
                ["schtasks", "/Delete", "/F", "/TN", name],
                capture_output=True, text=True, timeout=120,
            )
            removed = removed or p.returncode == 0
    elif shutil.which("crontab"):
        rc = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=60)
        if rc.returncode == 0 and config.TASK_NAME in rc.stdout:
            kept = []
            skip_next = False
            for line in rc.stdout.splitlines():
                if config.TASK_NAME in line and line.strip().startswith("#"):
                    skip_next = True
                    continue
                if skip_next:
                    skip_next = False
                    continue
                kept.append(line)
            subprocess.run(["crontab", "-"], input="\n".join(kept) + "\n", text=True, timeout=60)
            removed = True
    config.TRIGGER_FILE.unlink(missing_ok=True)
    # The env snapshot holds the agent's login. Nothing reads it once the entry is
    # gone, so leaving it is a secret on disk for no reason.
    (config.SHARED / ".factory" / "cron.env").unlink(missing_ok=True)
    print("DISARMED" if removed else "DISARMED (nothing was armed)")
    return 0


def main(argv: list[str]) -> int:
    if "--remove" in argv:
        return remove()
    if "--status" in argv or not argv:
        return status()

    if config.AUTONOMY < 1:
        print(
            "REFUSED: the dial is at 0, so a scheduler would wake up forever and\n"
            "  correctly do nothing. That is exactly how people convince themselves a\n"
            "  factory is running when it has never completed a lap.\n\n"
            "  Prove one lap by hand first:\n"
            "    factory run implement gh:issue:<n>\n"
            "  then raise the dial:\n"
            "    factory level 1",
            file=sys.stderr,
        )
        return 1

    if os.name == "nt":
        return install_task_scheduler()
    if shutil.which("crontab"):
        return install_cron()

    print(
        "ARM_FAILED: no scheduler found. Install one by hand:\n\n"
        f"  */{config.INTERVAL_MINUTES} * * * * cd {config.SHARED} && {DISPATCH} "
        f">> {config.SHARED}/.factory/factory.log 2>&1\n"
        f"  {config.REGRESS_CRON} cd {config.SHARED} && {REGRESS} "
        f">> {config.SHARED}/.factory/factory.log 2>&1\n\n"
        "  A systemd timer with OnUnitInactiveSec is worth it once a lap takes longer\n"
        "  than the interval -- it starts counting after the previous run FINISHES,\n"
        "  which a cron does not.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
