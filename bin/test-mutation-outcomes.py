#!/usr/bin/env python3
"""Does the mutation runner only count a defect as CAUGHT when one was caught?

    python bin/test-mutation-outcomes.py

THE INCIDENT. A real factory reported MUTATIONS_CAUGHT=9 and GATE_OK with two of
those nine being the e2e rung REFUSING TO RUN. The runner counted any non-zero exit
as a catch, so a timeout, a missing tool, an app that never started or a crashed
harness all read as "the gate noticed". The one number in the whole gate that
measures the harness was measuring whether the harness could break.

Each scenario below drives the REAL runner -- its copy step, its injection, a real
subprocess, its classifier and its counters -- against a stub gate that prints what
`harness/ci.py` actually prints on that path. Offline, no API, no app: the stub is
there so a verdict can be summoned on demand, not to stand in for the logic under
test.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
from pathlib import Path

HOME = Path(__file__).resolve().parent.parent
RUNNER = HOME / "template" / "harness" / "mutations" / "run.py"

FAILURES: list[str] = []
CHECKS = 0

SKILLS = [".claude/skills/factory-e2e/SKILL.md", ".claude/skills/factory-holdout/SKILL.md"]
DEFECT = {
    "id": "status stops changing",
    "file": "app/main.py",
    "find": "STATUS = 200",
    "replace": "STATUS = 500",
    "why": "the status is fixed and nothing notices",
}


def check(what: str, ok: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if not ok:
        FAILURES.append(what + ((" -- " + detail.strip()) if detail else ""))


def load_runner():
    spec = importlib.util.spec_from_file_location("mutation_run", RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


RUN = load_runner()


# --- the stub gate ------------------------------------------------------------
# Everything it prints is copied from what `harness/ci.py` prints on that path:
# `fail()` emits the failing command's own output and then `GATE_FAILED: <rung>`,
# the watchdog emits E2E_TIMEOUT and exits 124, and a pass ends in GATE_OK.


def gate(out: str, code: int, report: Path | None = None,
         watch: list[str] | None = None) -> str:
    return (
        "import os, sys\n"
        f"report = {str(report) if report else ''!r}\n"
        f"watch = {watch or []!r}\n"
        "if report:\n"
        "    seen = [p for p in watch if os.path.exists(p)]\n"
        "    open(report, 'w', encoding='utf-8').write('\\n'.join(seen))\n"
        f"print({out!r})\n"
        f"sys.exit({code})\n"
    )


def make_repo(tmp: Path, gate_src: str, defects: list[dict], copy: list[str]) -> Path:
    repo = tmp / "repo"
    (repo / "app").mkdir(parents=True)
    # Two mentions of STATUS and one of `STATUS = 200`, so the same file serves the
    # unique anchor and the ambiguous one.
    (repo / "app" / "main.py").write_text(
        "STATUS = 200\n\n\ndef status():\n    return STATUS\n", encoding="utf-8")
    (repo / "harness" / "mutations").mkdir(parents=True)
    (repo / "harness" / "ci.py").write_text(gate_src, encoding="utf-8")
    (repo / "harness" / "mutations" / "defects.json").write_text(
        json.dumps({"copy": copy, "defects": defects}), encoding="utf-8"
    )
    for skill in SKILLS:
        target = repo / skill
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# how the agent runs this rung\n", encoding="utf-8")
    # Not a skill, and the thing a blanket `.claude` copy would drag in.
    (repo / ".claude" / "settings.json").write_text("{}\n", encoding="utf-8")
    return repo


def drive(repo: Path, timeout: int = 1200, python: str = sys.executable) -> tuple[int, str]:
    """The real `main()`, against this repo."""
    RUN.ROOT = repo
    RUN.DEFECTS = repo / "harness" / "mutations" / "defects.json"
    RUN.GATE = [python, "harness/ci.py"]
    RUN.GATE_TIMEOUT = timeout
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = RUN.main()
    return rc, buf.getvalue()


def outcome_line(out: str) -> str:
    for line in out.splitlines():
        for word in ("CAUGHT ", "ESCAPED ", "INCONCLUSIVE ", "NOT_INJECTED "):
            if line.strip().startswith(word):
                return line.strip()
    return ""


def scenario(gate_src: str, copy: list[str] | None = None, defects: list[dict] | None = None,
             **kw) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as td:
        repo = make_repo(Path(td), gate_src, defects or [DEFECT], copy or ["app", "harness"])
        return drive(repo, **kw)


# --- a verdict the gate actually reached ---------------------------------------


def product_failure_checks() -> None:
    rc, out = scenario(gate("FAILED tests/test_status.py::test_ok\nGATE_FAILED: unit", 1))
    check("a red unit rung is a catch", "MUTATIONS_CAUGHT=1" in out, out)
    check("a catch names the rung that judged", "by unit" in outcome_line(out), out)
    check("nothing else is counted", "MUTATIONS_INCONCLUSIVE=0" in out
          and "MUTATIONS_NOT_INJECTED=0" in out, out)
    check("a clean sweep still prints MUTATIONS_OK", "MUTATIONS_OK" in out and rc == 0, out)

    rc, out = scenario(gate("  E2E_FAIL  the balance never changed\n"
                            "1 of 12 assertions failed\nGATE_FAILED: e2e", 1))
    check("a failed e2e assertion is a catch", "MUTATIONS_CAUGHT=1" in out and rc == 0, out)
    check("the e2e catch names e2e", "by e2e" in outcome_line(out), out)

    rc, out = scenario(gate("  HOLDOUT_FAIL  the ledger does not balance\n"
                            "2 of 9 assertions failed\nGATE_FAILED: holdout", 1))
    check("a failed holdout assertion is a catch", "MUTATIONS_CAUGHT=1" in out and rc == 0, out)

    rc, out = scenario(gate("app/main.py:1: error: incompatible return\nGATE_FAILED: static", 1))
    check("a red static rung is a catch", "MUTATIONS_CAUGHT=1" in out and rc == 0, out)


def escaped_checks() -> None:
    rc, out = scenario(gate("HARNESS_START mode=full driver=http\nUNIT_PASSED tests=41\n"
                            "E2E_PASSED journeys=3 steps=12\nGATE_OK mode=full", 0))
    check("a gate that passes with the defect in has escaped",
          "ESCAPED" in outcome_line(out), out)
    check("an escape is not a catch", "MUTATIONS_CAUGHT=0" in out, out)
    check("an escape fails the run", rc == 1 and "MUTATIONS_OK" not in out, out)

    # Exit 0 is only a verdict when the gate said so. A mutation that makes the
    # ladder return early exits 0 having judged nothing, which is not an escape.
    rc, out = scenario(gate("HARNESS_START mode=full driver=http", 0))
    check("exit 0 without GATE_OK is inconclusive",
          "INCONCLUSIVE" in outcome_line(out) and "no-verdict" in outcome_line(out), out)
    check("a gate that never finished fails the run", rc == 1 and "MUTATIONS_OK" not in out, out)


# --- everything that exits non-zero without judging the product ----------------


def harness_refusal_checks() -> None:
    rc, out = scenario(gate(
        ".claude/skills/factory-e2e/SKILL.md does not exist, so there is nothing "
        "telling the agent how to run this rung or what to write.\n"
        "GATE_FAILED: e2e-harness", 1))
    check("the e2e rung refusing to run is not a catch",
          "MUTATIONS_CAUGHT=0" in out and "MUTATIONS_INCONCLUSIVE=1" in out, out)
    check("the refusal is named for whoever reads the log",
          "e2e-harness" in outcome_line(out), out)
    check("a refusal fails the run", rc == 1 and "MUTATIONS_OK" not in out, out)

    rc, out = scenario(gate(
        ".factory/holdout/HOLDOUT.md does not exist. That file is where your "
        "scenarios live.\nGATE_FAILED: holdout-harness", 1))
    check("the holdout rung refusing to run is not a catch",
          "MUTATIONS_CAUGHT=0" in out and "MUTATIONS_INCONCLUSIVE=1" in out, out)

    rc, out = scenario(gate("uvicorn exited before the health endpoint answered\n"
                            "GATE_FAILED: app-start", 1))
    check("an app that never started is not a catch",
          "MUTATIONS_CAUGHT=0" in out and "MUTATIONS_INCONCLUSIVE=1" in out, out)

    rc, out = scenario(gate("could not run ruff: [Errno 2] No such file or directory\n"
                            "GATE_FAILED: static", 1))
    check("a tool that is not installed is not a catch",
          "MUTATIONS_CAUGHT=0" in out and "MUTATIONS_INCONCLUSIVE=1" in out, out)

    rc, out = scenario(gate(
        "UNIT_ERROR: the runner reported 0 tests - a suite that ran nothing is not a "
        "suite that passed.\nGATE_FAILED: unit", 1))
    check("a unit rung that discovered nothing is not a catch",
          "MUTATIONS_CAUGHT=0" in out and "MUTATIONS_INCONCLUSIVE=1" in out, out)

    rc, out = scenario(gate("1 of 12 assertions failed\nGATE_FAILED: e2e", 1))
    check("a red e2e rung with no E2E_FAIL line is not a catch",
          "MUTATIONS_CAUGHT=0" in out and "MUTATIONS_INCONCLUSIVE=1" in out, out)

    for diagnostic in ("ModuleNotFoundError: No module named 'pytest'",
                       "ImportError: cannot import test support",
                       "Error: Cannot find module 'vitest'"):
        rc, out = scenario(gate(diagnostic + "\nGATE_FAILED: unit", 1))
        check("a unit runner import failure is not a product catch: " + diagnostic,
              rc == 1 and "MUTATIONS_CAUGHT=0" in out and "MUTATIONS_OK" not in out, out)


def timeout_checks() -> None:
    rc, out = scenario("import time\ntime.sleep(60)\n", timeout=1)
    check("a gate that never returns is not a catch",
          "MUTATIONS_CAUGHT=0" in out and "MUTATIONS_INCONCLUSIVE=1" in out, out)
    check("the timeout says so", "timeout" in outcome_line(out), out)
    check("a timeout fails the run", rc == 1 and "MUTATIONS_OK" not in out, out)

    # The watchdog kills the e2e rung and names it on the way out, so this one
    # arrives wearing a product rung's name.
    rc, out = scenario(gate("E2E_TIMEOUT after 300s - the journey never returned.\n"
                            "GATE_FAILED: e2e", 124))
    check("a watchdog kill that names e2e is not a catch",
          "MUTATIONS_CAUGHT=0" in out and "MUTATIONS_INCONCLUSIVE=1" in out, out)


def unrunnable_gate_checks() -> None:
    rc, out = scenario(gate("GATE_FAILED: unit", 1), python="no-such-interpreter-4f21")
    check("a gate that cannot be spawned is not a catch",
          "MUTATIONS_CAUGHT=0" in out and "MUTATIONS_INCONCLUSIVE=1" in out, out)
    check("the spawn failure is explained", "not-runnable" in outcome_line(out), out)
    check("an unspawnable gate fails the run", rc == 1 and "MUTATIONS_OK" not in out, out)


def unknown_exit_checks() -> None:
    secret = "postgres://factory:hunter2@db.internal:5432/prod"
    noise = "\n".join(f"  File \"harness/ci.py\", line {i}, in main" for i in range(40))
    rc, out = scenario(gate(
        "HARNESS_START mode=full driver=http\n" + noise + f"\nDATABASE_URL={secret}\n"
        "ModuleNotFoundError: No module named 'app'\x07", 3))
    check("a crashed harness is not a catch",
          "MUTATIONS_CAUGHT=0" in out and "MUTATIONS_INCONCLUSIVE=1" in out, out)
    check("an unnamed failure says what the exit code was",
          "unknown-exit" in outcome_line(out) and "exited 3" in outcome_line(out), out)
    check("the diagnosis omits raw output even on unknown termination",
          "ModuleNotFoundError" not in outcome_line(out), out)
    check("the diagnosis is one printable line",
          "\x07" not in out and len(outcome_line(out)) < 300, repr(outcome_line(out)))
    # The build inherits this machine's environment, so the whole capture never
    # lands in a log that gets pasted into an issue.
    check("the capture is not dumped", secret not in out and noise not in out, out)

    rc, out = scenario(gate("DATABASE_URL=" + secret, 3))
    check("even the last output line is not exported", secret not in out and rc == 1, out)
    rc, out = scenario(gate("GATE_FAILED: " + secret, 1))
    check("an unknown rung cannot export arbitrary output", secret not in out and rc == 1, out)

    rc, out = scenario(gate("GATE_FAILED:", 1))
    check("a marker naming no rung is not a catch",
          "MUTATIONS_CAUGHT=0" in out and "MUTATIONS_INCONCLUSIVE=1" in out, out)
    check("the malformed marker is named", "malformed-marker" in outcome_line(out), out)


def never_ok_on_a_broken_harness_checks() -> None:
    defects = [dict(DEFECT, id=f"defect-{n}") for n in range(3)]
    rc, out = scenario(gate("GATE_FAILED: e2e-harness", 1), defects=defects)
    check("three non-zero exits from a broken harness score nothing",
          "MUTATIONS_TOTAL=3" in out and "MUTATIONS_CAUGHT=0" in out
          and "MUTATIONS_INCONCLUSIVE=3" in out, out)
    check("a broken harness never prints MUTATIONS_OK", "MUTATIONS_OK" not in out, out)
    check("a broken harness fails the run", rc == 1, out)


def not_injected_checks() -> None:
    moved = dict(DEFECT, find="STATUS = 418")
    rc, out = scenario(gate("GATE_FAILED: unit", 1), defects=[moved])
    check("an anchor that moved is not injected", "NOT_INJECTED" in outcome_line(out), out)
    check("a defect that never landed is not a catch", "MUTATIONS_CAUGHT=0" in out, out)
    check("a missing injection fails the run", rc == 1 and "MUTATIONS_OK" not in out, out)

    ambiguous = dict(DEFECT, find="STATUS")
    rc, out = scenario(gate("GATE_FAILED: unit", 1), defects=[ambiguous],
                       copy=["app", "harness"])
    check("an ambiguous anchor is not injected",
          "NOT_INJECTED" in outcome_line(out) and "ambiguous" in outcome_line(out), out)

    rc, out = scenario(gate("GATE_FAILED: unit", 1), copy=["nothing", "here"])
    check("a copy list that matches nothing is not injected",
          "NOT_INJECTED" in outcome_line(out) and rc == 1, out)


# --- the harness runtime every build needs ------------------------------------
# The two rung skills were in no copy list, shipped or written, so `agentcheck.py`
# refused both agent rungs in every mutated build and the defects aimed at them
# could not be caught by anything.


def skill_copy_checks() -> None:
    watched = [*SKILLS, ".claude/settings.json", "app/main.py"]

    with tempfile.TemporaryDirectory() as td:
        report = Path(td) / "seen.txt"
        repo = make_repo(Path(td), gate("GATE_FAILED: unit", 1, report, watched),
                         [DEFECT], ["app", "harness"])
        rc, out = drive(repo)
        seen = report.read_text(encoding="utf-8").splitlines()
    check("a legacy copy list still gets both rung skills",
          all(s in seen for s in SKILLS), str(seen))
    check("the project's own copy list is untouched", "app/main.py" in seen, str(seen))
    check("unrelated .claude files stay out of the build",
          ".claude/settings.json" not in seen, str(seen))

    with tempfile.TemporaryDirectory() as td:
        report = Path(td) / "seen.txt"
        repo = make_repo(Path(td), gate("GATE_FAILED: unit", 1, report, watched),
                         [DEFECT], ["app", "harness", ".claude"])
        rc, out = drive(repo)
        seen = report.read_text(encoding="utf-8").splitlines()
    check("a copy list that already covers the skills is not copied over twice",
          all(s in seen for s in SKILLS) and "MUTATIONS_CAUGHT=1" in out, out + str(seen))

    with tempfile.TemporaryDirectory() as td:
        report = Path(td) / "seen.txt"
        repo = make_repo(Path(td), gate("GATE_FAILED: unit", 1, report, watched),
                         [DEFECT], ["app", "harness", ".claude/skills/"])
        rc, out = drive(repo)
        seen = report.read_text(encoding="utf-8").splitlines()
    check("a trailing slash still counts as covering the skills",
          all(s in seen for s in SKILLS) and "MUTATIONS_CAUGHT=1" in out, out + str(seen))


def main() -> int:
    product_failure_checks()
    escaped_checks()
    harness_refusal_checks()
    timeout_checks()
    unrunnable_gate_checks()
    unknown_exit_checks()
    never_ok_on_a_broken_harness_checks()
    not_injected_checks()
    skill_copy_checks()

    if FAILURES:
        print("The mutation runner is scoring the wrong thing:", file=sys.stderr)
        for f in FAILURES:
            print("  FAIL  " + f, file=sys.stderr)
        print(f"MUTATION_OUTCOMES_FAILED checks={CHECKS} failed={len(FAILURES)}")
        return 1
    print("Only a verdict the gate reached counts as a catch.")
    print(f"MUTATION_OUTCOMES_PASSED checks={CHECKS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
