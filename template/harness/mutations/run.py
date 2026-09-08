#!/usr/bin/env python3
"""List/apply one deterministic mutation or score a supplied ordinary-check log.

Candidate preparation, verification and retries belong to a shared workflow.
This tool starts no subprocess and never executes agents or other workflows.
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Literal, NamedTuple
HERE = Path(__file__).resolve().parent
DEFECTS = HERE / "defects.json"

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
        if not has_line(out, "CHECKS_OK"):
            return Verdict(
                "INCONCLUSIVE", "no-verdict",
                "the gate exited 0 without printing CHECKS_OK, so it never finished",
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
    target = (dest / d["file"]).resolve()
    if not target.is_relative_to(dest.resolve()):
        return False, "mutation path escapes the candidate"
    if not d.get("find") or d.get("find") == d.get("replace"):
        return False, "mutation must change a nonempty unique anchor"
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--defects", type=Path, default=DEFECTS)
    subs = parser.add_subparsers(dest="action", required=True)
    subs.add_parser("list")
    mutation = subs.add_parser("apply")
    mutation.add_argument("id")
    mutation.add_argument("--candidate", type=Path, required=True)
    score = subs.add_parser("score")
    score.add_argument("--log", type=Path, required=True)
    score.add_argument("--exit-code", type=int, required=True)
    args = parser.parse_args()
    if args.action == "score":
        verdict = classify(args.exit_code, args.log.read_text(encoding="utf-8"))
        print(json.dumps(verdict._asdict()))
        return 0 if verdict.outcome == "CAUGHT" else 1
    defects = json.loads(args.defects.read_text(encoding="utf-8"))["defects"]
    if args.action == "list":
        print(json.dumps(defects, indent=2))
        return 0
    matches = [d for d in defects if d.get("id") == args.id]
    if len(matches) != 1:
        parser.error("mutation ID must identify exactly one defect")
    injected, why = apply(args.candidate, matches[0])
    print(json.dumps({"id": args.id, "injected": injected, "reason": why}))
    return 0 if injected else 1

if __name__ == "__main__":
    raise SystemExit(main())
