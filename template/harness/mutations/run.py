#!/usr/bin/env python3
"""MUTATION TESTING. Break the software on purpose and require the gate to notice.

    python harness/mutations/run.py

THE ONLY THING IN THE GATE THAT MEASURES YOUR HARNESS RATHER THAN YOUR CODE.

Everything else answers "is this build good?". This answers "would this gate know if
it were not?" -- and they are completely different questions. A gate that has never
failed is a gate nobody has tested, and until you run this you have no evidence that
any of your checks can fail at all.

HOW IT WORKS. For each defect: copy the repo to a temp dir, apply one textual
mutation to real source, run the gate there, and require it to go RED. A mutation
the gate misses is a class of bug that can currently merge unreviewed.

WHAT MAKES A GOOD DEFECT. Not typos -- the compiler finds those. Aim at the seams
where your checks are weakest:

  * an invariant quietly inverted (a comparison flipped, a guard removed)
  * a value that stops changing (a counter that no longer increments)
  * an output made constant (always the same answer)
  * an error path that silently succeeds
  * a persistence write dropped (works until a restart)
  * an off-by-one at a boundary (right on average, wrong at the edge)

AIM AT LEAST ONE DEFECT AT EACH RUNG, and read WHICH rung caught each one. `ci.py`
stops at the first red rung, so a set built only from logic defects gets caught
entirely by 'unit' -- and the e2e, holdout and gate rungs are never once shown to be
able to fail. A real build scored 9/9 that way: the number read as "the gate can
fail" when all it meant was "the unit suite can fail". If every line below says
`by unit`, it is not a finished set.

CAUGHT IS NOT "THE GATE EXITED NON-ZERO", and it used to be. A real build scored
MUTATIONS_CAUGHT=9 with two of those nine being the e2e rung refusing to RUN. A
timeout, an app that never started, a tool that is not installed, an agent that
returned no evidence: each of those exits non-zero with no check having judged the
product, and counting them as catches is how a harness reports that it can fail when
what it proved is that it can break. So a defect is CAUGHT only when `ci.py` names a
product rung it stopped on AND that rung's own failure evidence is in the log.
Everything else is INCONCLUSIVE: the experiment did not run, which is neither a pass
nor an escape, and the gate fails on it for the same reason it fails on an escape --
nothing was measured.

Emits MUTATIONS_TOTAL, MUTATIONS_CAUGHT, MUTATIONS_INCONCLUSIVE and
MUTATIONS_NOT_INJECTED. The gate requires caught == total.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Literal, NamedTuple

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
DEFECTS = HERE / "defects.json"

GATE = [sys.executable, "harness/ci.py"]
GATE_TIMEOUT = 1200

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

# What to copy into each throwaway build. Keep it small; this runs once per defect.
# Read from defects.json so it travels with the project rather than being edited here.
DEFAULT_COPY = ["app", "src", "tests", "harness", ".factory", "pyproject.toml", "package.json"]
SKIP_DIRS = {"__pycache__", ".git", ".venv", "node_modules", "runs", "locks-runtime", "builds", ".worktrees", ".pytest_cache", ".mypy_cache", ".ruff_cache"}

# COPIED INTO EVERY BUILD WHATEVER THE PROJECT'S LIST SAYS. `agentcheck.py` refuses
# to run a rung whose SKILL.md is absent, so a copy list without these -- which is
# every list written before this line existed, including the shipped one -- gave a
# build where the e2e and holdout rungs could not run at all. Named by path rather
# than copying `.claude`, which also holds settings, credentials and the builder's
# own skills.
HARNESS_SKILLS = [".claude/skills/factory-e2e", ".claude/skills/factory-holdout"]

# The rungs whose red means THE PRODUCT IS WRONG. `ci.py` names the rung it stopped
# on with `GATE_FAILED: <rung>`, and the other names it can print -- app-start,
# e2e-harness, holdout-harness -- are the harness or the machine failing before any
# assertion was reached.
PRODUCT_RUNGS = {"static", "unit", "e2e", "holdout"}

# The rungs that print one line per failed assertion, and the line to require. Both
# can reach `GATE_FAILED` without the product being wrong (a watchdog kill names
# 'e2e' on its way out), so for these the rung name alone is not evidence.
ASSERTION_EVIDENCE = {"e2e": "E2E_FAIL", "holdout": "HOLDOUT_FAIL"}

# `ci.py`'s own words for "this rung never got to judge the product", printed as the
# failure detail immediately above GATE_FAILED. A suite whose own output happens to
# contain one of these reads as inconclusive, which is the safe direction: it fails
# the gate loudly instead of scoring a catch nobody made.
NOT_A_VERDICT = ("TIMEOUT after ", "could not run ", "UNIT_ERROR:")
IMPORT_FAILURES = ("ModuleNotFoundError:", "ImportError:", "Error: Cannot find module")
INFRASTRUCTURE_RUNGS = {"app-start", "e2e-harness", "holdout-harness", "mutations"}

Outcome = Literal["CAUGHT", "ESCAPED", "INCONCLUSIVE"]


class Verdict(NamedTuple):
    outcome: Outcome
    label: str  # the rung that judged, or the shape of the failure when none did
    detail: str  # sanitized and bounded, for whoever reads the log


_UNPRINTABLE = re.compile(r"[^\x20-\x7e]+")


def sanitize(text: str, limit: int = 200) -> str:
    """One bounded printable line out of whatever the build printed.

    The throwaway build inherits this machine's environment and tools echo it: a
    traceback quoting a connection string, a runner printing its own configuration.
    Quote a little, never the whole capture.
    """
    flat = " ".join(_UNPRINTABLE.sub(" ", text).split())
    return flat[: limit - 3] + "..." if len(flat) > limit else flat


def named_rung(out: str) -> str | None:
    """The rung `ci.py` says stopped the run, or None if it never said.

    THE LAST MARKER WINS. `fail()` prints the failing command's own output first and
    the marker last, so a suite that echoes `GATE_FAILED: unit` in its own log cannot
    displace the real one -- nothing is printed after it.
    """
    step = None
    for line in out.splitlines():
        if line.strip().startswith("GATE_FAILED:"):
            step = line.split(":", 1)[1].strip()
    return step


def has_line(out: str, prefix: str) -> bool:
    return any(line.strip().startswith(prefix) for line in out.splitlines())


def classify(rc: int, out: str) -> Verdict:
    """What this build's exit code and log actually establish about the defect."""
    if rc == 0:
        if not has_line(out, "GATE_OK"):
            return Verdict(
                "INCONCLUSIVE", "no-verdict",
                "the gate exited 0 without printing GATE_OK, so it never finished",
            )
        return Verdict("ESCAPED", "gate-ok", "")

    # 124 is this harness's timeout everywhere, and `ci.py` exits with it only from
    # the e2e watchdog -- which names the 'e2e' rung on the way out without a single
    # assertion having been judged.
    if rc == 124:
        return Verdict(
            "INCONCLUSIVE", "timeout",
            "the gate was killed on a deadline before it reached a verdict",
        )

    rung = named_rung(out)
    if rung is None:
        return Verdict(
            "INCONCLUSIVE", "unknown-exit",
            f"the gate exited {rc} without naming a rung; no product verdict was recorded",
        )
    if not rung:
        return Verdict("INCONCLUSIVE", "malformed-marker", "GATE_FAILED named no rung")
    if rung not in PRODUCT_RUNGS:
        return Verdict(
            "INCONCLUSIVE", rung if rung in INFRASTRUCTURE_RUNGS else "unknown-rung",
            "the harness or the environment failed, not the product",
        )
    if any(signature in out for signature in IMPORT_FAILURES):
        return Verdict(
            "INCONCLUSIVE", rung,
            "a module import failed; a product validation result is not established",
        )
    for signature in NOT_A_VERDICT:
        if has_line(out, signature):
            return Verdict(
                "INCONCLUSIVE", rung,
                f"'{rung}' went red on '{signature.strip()}', which is the rung not "
                f"running rather than the defect being found",
            )
    evidence = ASSERTION_EVIDENCE.get(rung)
    if evidence and not has_line(out, evidence):
        return Verdict(
            "INCONCLUSIVE", rung,
            f"'{rung}' went red without one {evidence} line, so no assertion is on "
            f"record as having judged the defect",
        )
    return Verdict("CAUGHT", rung, "")


def probe(dest: Path) -> Verdict:
    """Run the gate inside one mutated build and read what it proved."""
    env = dict(os.environ, FACTORY_IN_MUTATION="1")
    try:
        r = subprocess.run(
            GATE, cwd=dest, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=GATE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return Verdict(
            "INCONCLUSIVE", "timeout",
            f"the gate did not finish within {GATE_TIMEOUT}s",
        )
    except OSError as e:
        # The gate never started, so nothing about the defect was tested. Scoring
        # this as a catch is how a machine missing an interpreter reports a perfect
        # run.
        return Verdict("INCONCLUSIVE", "not-runnable", sanitize(f"{GATE[0]}: {e}", 160))
    return classify(r.returncode, r.stdout or "")


def covers(item: str, needed: str) -> bool:
    have = item.replace("\\", "/").strip("/")
    return bool(have) and (needed == have or needed.startswith(have + "/"))


def missing_skills(items: list[str]) -> list[str]:
    """The rung skills this copy list does not already bring.

    An operator who listed `.claude` or `.claude/skills` has them, and copying a
    subdirectory of something already copied would land `copytree` on a path that
    exists.
    """
    return [s for s in HARNESS_SKILLS if not any(covers(i, s) for i in items)]


def build_copy(dest: Path, items: list[str]) -> int:
    copied = 0
    for item in items:
        src = ROOT / item
        if not src.exists():
            continue
        if src.is_dir():
            shutil.copytree(src, dest / item, ignore=shutil.ignore_patterns(*SKIP_DIRS))
        else:
            (dest / item).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, dest / item)
        copied += 1
    return copied


def apply(dest: Path, d: dict) -> tuple[bool, str]:
    """Textual mutation. Returns (injected, why-not).

    THE ANCHOR MUST BE UNIQUE, and that is not fussiness -- it is a bug this runner
    had and a factory found.

    A `replace(find, replace, 1)` hits the FIRST occurrence. When a change adds a
    second, byte-identical occurrence somewhere earlier in the file, the mutation
    silently starts rewriting the new line instead of the intended one. The intended
    target is left correct, so the check aimed at it never fires, and the defect is
    reported as ESCAPED -- pointing at a hole in the harness that does not exist,
    while the real problem is that the defect was injected into the wrong place.

    Observed exactly that way: a new route's `return self._error(409, str(e))` was
    identical to the one in an older handler, and the e2e assertion aimed at the older
    one stopped being exercised. Everything about the report was misleading.

    So an ambiguous anchor is NOT INJECTED, and it says which file and how many
    matches -- a defect that cannot be placed precisely is a defect that proves
    nothing.
    """
    target = dest / d["file"]
    if not target.exists():
        return False, f"{d['file']} does not exist in the build copy"
    body = target.read_text(encoding="utf-8")
    count = body.count(d["find"])
    if count == 0:
        return False, f"anchor not found in {d['file']}"
    if count > 1:
        return False, (
            f"anchor appears {count} times in {d['file']} -- ambiguous. The mutation "
            f"would hit the first one, which may not be the line this defect is about. "
            f"Lengthen the anchor until it is unique, or reword the duplicate."
        )
    target.write_text(body.replace(d["find"], d["replace"], 1), encoding="utf-8")
    return True, ""


def main() -> int:
    if not DEFECTS.exists():
        print("MUTATIONS_ABSENT no defects.json next to this script", flush=True)
        return 0

    spec = json.loads(DEFECTS.read_text(encoding="utf-8"))
    defects = spec["defects"]
    copy_items = spec.get("copy") or DEFAULT_COPY
    extras = missing_skills(copy_items)
    total = caught = escaped = inconclusive = not_injected = 0

    print("MUTATION_START", flush=True)
    for d in defects:
        total += 1
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "build"
            dest.mkdir()
            if build_copy(dest, copy_items) == 0:
                # Pointed at the wrong paths, the runner would otherwise score a
                # perfect run against a build containing no code at all.
                print(
                    f"  NOT_INJECTED  {d['id']:<40} the build copy matched nothing from "
                    f"{copy_items}",
                    flush=True,
                )
                not_injected += 1
                continue
            # Counted separately from the project's own items, so a copy list that
            # matches nothing still reads as matching nothing.
            build_copy(dest, extras)

            injected, why = apply(dest, d)
            if not injected:
                # NOT a pass. The anchor moved or went ambiguous, so this defect tested
                # nothing -- and a mutation set that silently stops injecting reports a
                # perfect score for doing nothing at all.
                not_injected += 1
                print(f"  NOT_INJECTED  {d['id']:<40} {why}", flush=True)
                continue

            v = probe(dest)
            if v.outcome == "CAUGHT":
                caught += 1
                print(f"  CAUGHT        {d['id']:<40} by {v.label}", flush=True)
            elif v.outcome == "INCONCLUSIVE":
                inconclusive += 1
                print(f"  INCONCLUSIVE  {d['id']:<40} {v.label}: {v.detail}", flush=True)
            else:
                escaped += 1
                print(f"  ESCAPED       {d['id']:<40} <-- {d['why']}", flush=True)

    print(f"MUTATIONS_TOTAL={total}", flush=True)
    print(f"MUTATIONS_CAUGHT={caught}", flush=True)
    print(f"MUTATIONS_INCONCLUSIVE={inconclusive}", flush=True)
    print(f"MUTATIONS_NOT_INJECTED={not_injected}", flush=True)
    if not (escaped or inconclusive or not_injected):
        print("MUTATIONS_OK", flush=True)
        return 0
    print(
        f"MUTATIONS_FAILED escaped={escaped} inconclusive={inconclusive} "
        f"not_injected={not_injected}. An escaped defect is a class of bug that can "
        f"currently merge unreviewed. An inconclusive one proves nothing either way: "
        f"the gate went red without judging the product, so read its line for what "
        f"stopped it.",
        flush=True,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
