"""The structural gate, as one fixed command the upstream SDLC workflows may run.

    python <trusted>/fixed_gate.py <gate.json>                  acceptance
    python <trusted>/fixed_gate.py <gate.json> --publication    before a PR is pushed
    python <trusted>/fixed_gate.py <gate.json> --regression      the scheduled run

ONE EVALUATOR, THREE PROFILES, and one reason for that: acceptance, publication and
regression must be judged by the same code, or the thing that decides whether a change
may merge is not the thing that decided it could be published. Three copies of a gate
are three gates that drift, and the one nobody runs is always the one that is wrong.

WHAT IT IS TRUSTED TO BE. `sdlc.snapshot` reconstructs this file, `config.py`,
`guard.py`, `gate.py` and `tripwire.py` out of the BASE tree -- never out of the
candidate and never out of a possibly-dirty operator checkout -- and writes the profile
this reads. So the gate a pull request is held to is the gate a human last agreed to,
structurally rather than by assertion. `gate.json` carries the base revision, the
required markers, the ratchet floor and the project's validate command, all read from
that same base.

EVERY CHECK IS A POSITIVE ASSERTION. None of them test for the absence of the word
"error". A check that never ran produces no failures, and "did anything fail?" reads
that as success.

WHAT EACH PROFILE OWES ITS CALLER. Acceptance owes a report: it runs with this
evaluation's id and identity bound into the environment and writes
`config.ACCEPT_REPORT` into the candidate checkout on every exit, because the judge is
shown that file and nothing else of a private gate. Regression owes the
`REGRESS_EVIDENCE_PATH` report. Publication owes neither -- nothing is judging a
receipt at publication time -- and writes nothing into the tree it is checking.

Exit codes, chosen to mean something to the caller reading them:
    0   every check ran and passed
    1   a check ran and failed -- the candidate's problem, and repairable
    2   the guard, the secret preflight or the tripwire refused -- also the
        candidate's problem, and not repairable by rerunning
    75  the gate could not run at all. Declared to acceptance as an ENVIRONMENT exit,
        because "we could not check" must never be recorded as "we checked".
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import config
import gate
import guard
import runtime
import tripwire

# The gate command's own budget. Under acceptance's ten-minute ceiling on purpose: a
# gate killed by the caller is an inconclusive result with no output to read, where one
# that stops itself prints everything it got to.
COMMAND_TIMEOUT_SECONDS = 540

REGRESS_BINDINGS = ("revision", "base", "base_revision", "scope")

# What acceptance binds into a fixed gate's environment so the report it demands back
# can only ever be about the evaluation that asked for it.
ACCEPT_BINDINGS = ("ACCEPT_EVALUATION_ID", "ACCEPT_IDENTITY")


def measure(log: str, code: int, floor: dict) -> dict:
    """What the run log actually proves, as errors, holds and counts.

    ERRORS BLOCK. HOLDS DO NOT: a hold is "green, and waiting for a person to agree
    with a call the factory made", which is not a failure and must not be reported as
    one. The caller turns holds into the `held` state; nothing here decides that.

    THE RAW FLOOR FILE GOES IN, not a filtered copy. `gate.floors` decides which keys
    are minimums and it strips the `_MAX` ceilings, which are read here too -- so
    filtering before the call silently removed the only thing the uncalibrated hold
    looks at, and a hold that can never fire is indistinguishable from one that has
    nothing to hold on.
    """
    minimums = gate.floors(floor)
    markers = {marker: marker in log for marker in config.REQUIRED_MARKERS}
    errors = [f"the gate command exited {code}"] if code else []
    errors += [f"required marker absent from the run log: {marker}"
               for marker, seen in markers.items() if not seen]

    counts = gate.observed_counts(log, list(minimums))
    for key, minimum in minimums.items():
        observed = counts.get(key)
        if observed is None:
            errors.append(
                f"the ratchet has a floor for '{key}' ({minimum}) and the run log reports "
                f"no count for it. A floor nothing measures is a floor nobody is held to.")
        elif observed < minimum:
            errors.append(f"'{key}' ran {observed} of a required {minimum}. The rest were "
                          f"skipped, and skipped is not passed.")

    # Deliberate defects. All must be injected and all must be caught, or nothing
    # merges: every miss is a class of bug that can currently merge unreviewed, and a
    # mutation set that silently stops injecting reports a perfect score for doing
    # nothing.
    total = gate.counted(log, "MUTATIONS_TOTAL")
    if total is not None:
        if total == 0:
            errors.append("zero deliberate defects were injected; a gate that has never "
                          "failed is a gate nobody has tested")
        if gate.counted(log, "MUTATIONS_NOT_INJECTED"):
            errors.append("a deliberate defect could not be injected -- an anchor moved or "
                          "went ambiguous. See the NOT_INJECTED lines in the log.")
        caught = gate.counted(log, "MUTATIONS_CAUGHT")
        if caught != total:
            errors.append(f"the gate caught {caught} of {total} deliberate defects. See the "
                          f"ESCAPED line in the log for which one and what it means.")

    holds: list[str] = []
    slack = {key: counts[key] - minimum for key, minimum in minimums.items()
             if counts.get(key) is not None and counts[key] > minimum}
    if slack and config.SLACK_CAPS_AUTONOMY:
        holds.append("ratchet slack (" + ", ".join(f"{k}+{v}" for k, v in slack.items()) + ")")
    ceiling = floor.get("UNCALIBRATED_MAX")
    uncalibrated = gate.uncalibrated_total(log)
    if isinstance(ceiling, int) and uncalibrated > ceiling:
        holds.append(f"{uncalibrated} uncalibrated margins against a ceiling of {ceiling}")
    if slack:
        print("RATCHET_SLACK=" + " ".join(f"{k}+{v}" for k, v in slack.items()))
    return {"errors": errors, "holds": holds, "markers": markers,
            "counts": {k: v for k, v in counts.items() if v is not None}}


def structural(profile: dict, publication: bool) -> tuple[int, str]:
    """The checks that run before anything is executed. Returns (exit code, their log).

    CAPTURED RATHER THAN ASSUMED. The markers these emit -- PREFLIGHT_OK, PROTECTED_OK,
    TRIPWIRE_CLEAR -- are part of the evidence `measure` reads, and an earlier version
    of this file simply prepended the string "PROTECTED_OK" to the log on the grounds
    that the guard had returned zero. That is a marker the gate wrote about itself. The
    whole argument for a marker is that the check that ran is the thing that printed it.
    """
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = guard.preflight()
        if code == 0:
            code = guard.main(["--base", profile["base_sha"], "--head", "HEAD"])
            if code == 0 and not publication:
                code = tripwire.main([str(config.ROOT)])
    text = buffer.getvalue()
    print(text, end="")
    # The guard's own 2 means "the diff could not be computed", which is a broken
    # environment rather than a bad candidate, and acceptance must hear the difference.
    return (75 if code == 2 else code), text


def regression_report(measured: dict) -> None:
    """The report `archon-regress` reads back from its trusted check profile.

    `public_cases` IS ALWAYS EMPTY, and `product` is never claimed. Not because the
    feature is finished -- this gate simply cannot supply either. A published case has
    to carry a root cause its author proved and approved for export; this gate knows
    whether the base branch is green and cannot say WHY without inventing prose, which
    is exactly how private evaluator output reaches a public issue. `product` needs
    evidence separating "a test asserted and failed" from "the suite fell over", which
    it does not collect, and a misclassified regression sends whoever reads it at 3am
    to the wrong file.

    SO A RED RUN FROM THIS GATE ALONE ESCALATES TO A PERSON AND FILES NOTHING. The
    factory's route to a filed issue is `FACTORY_PUBLIC_PROBE_SCOPE`: the workflow
    re-runs that public scope after this gate comes back non-clean and publishes only
    what the probe itself proved. Nothing here crosses into it. A project whose own
    check can prove and approve a case fills `public_cases` in instead.
    """
    missing = [key for key in REGRESS_BINDINGS if f"REGRESS_{key.upper()}" not in os.environ]
    if missing or "REGRESS_EVIDENCE_PATH" not in os.environ:
        raise RuntimeError(
            "--regression is the shape archon-regress invokes: it needs REGRESS_EVIDENCE_PATH "
            "and the four REGRESS_* bindings in the environment. Missing: " +
            " ".join(missing or ["REGRESS_EVIDENCE_PATH"]))
    evidence = {key: os.environ[f"REGRESS_{key.upper()}"] for key in REGRESS_BINDINGS}
    evidence.update(status="inconclusive" if measured["errors"] else "clean",
                    public_cases=[], factory_errors=measured["errors"],
                    factory_holds=measured["holds"])
    runtime.write(Path(os.environ["REGRESS_EVIDENCE_PATH"]), evidence)


def acceptance_binding() -> tuple[str, dict]:
    """This evaluation's id and identity, exactly as acceptance handed them over.

    Read BEFORE the gate spends anything. A report bound to another evaluation, head or
    base cannot certify this candidate, and acceptance is right to refuse it -- so a gate
    that discovers the binding is missing after nine minutes has burned the run and
    still produced nothing that can be judged.
    """
    missing = [key for key in ACCEPT_BINDINGS if not os.environ.get(key)]
    if missing:
        raise RuntimeError(
            "the acceptance profile is what invokes this shape, and it binds "
            + " and ".join(ACCEPT_BINDINGS) + " into the gate's environment. Missing: "
            + " ".join(missing))
    identity = json.loads(os.environ["ACCEPT_IDENTITY"])
    if not isinstance(identity, dict):
        raise RuntimeError("ACCEPT_IDENTITY did not carry the evaluation's identity object")
    return os.environ["ACCEPT_EVALUATION_ID"], identity


def acceptance_report(evaluation: str, identity: dict, status: str,
                      measured: dict | None) -> None:
    """The public half of a private gate's result, where the acceptance judge reads it.

    THE JUDGE OTHERWISE HAS AN EXIT CODE AND NOTHING ELSE. A fixed profile keeps argv
    and streams private, which is the only reason this gate may exercise a holdout the
    builder is not allowed to see -- and it leaves an approval resting on "a command the
    operator vouched for exited zero". This is the sanitized remainder: the gate's own
    status, which required markers reported, and the counts it measured.

    SANITIZED BY CONSTRUCTION RATHER THAN BY FILTERING. Nothing assembled here reads a
    failure message, a holdout scenario, an evaluator path or the command that ran. The
    judge is a builder-facing context, and a private gate that leaks its own assertions
    into one has stopped being a holdout.
    """
    markers = (measured or {}).get("markers") or {}
    reported = sorted(name for name, seen in markers.items() if seen)
    absent = sorted(name for name, seen in markers.items() if not seen)
    if measured is None:
        evidence = (
            f"The factory's fixed structural gate did not reach the project's validation "
            f"command ({status}). Its structural stage is the secret preflight, the "
            f"protected-path guard, the diff size and file-scope caps, and the tripwire.")
    else:
        evidence = (
            f"The factory's fixed gate ran against this candidate and {status}. "
            f"Required markers reporting: {len(reported)} of {len(markers)}"
            + (" (" + ", ".join(reported) + ")" if reported else "")
            + ("; absent: " + ", ".join(absent) if absent else "")
            + ". Measured counts: "
            + (", ".join(f"{key}={value}" for key, value in sorted(measured["counts"].items()))
               or "none")
            + f". {len(measured['errors'])} blocking error(s), "
              f"{len(measured['holds'])} merge hold(s). The gate's command, its streams "
              f"and the holdout it exercises are the operator's and are not reproduced here.")
    runtime.write(config.ROOT / config.ACCEPT_REPORT, {
        "schema_version": 1, "evaluation_id": evaluation, "identity": identity,
        "evidence": evidence, "gate_status": status,
        "required_markers": markers,
        "measured_counts": (measured or {}).get("counts"),
        "blocking_errors": None if measured is None else len(measured["errors"]),
        "merge_holds": None if measured is None else len(measured["holds"])})


def main(argv: list[str]) -> int:
    if not argv or argv[0] in {"-h", "--help"}:
        print(__doc__)
        return 0 if argv else 2
    profile = runtime.read(Path(argv[0]))
    publication = "--publication" in argv
    regression = "--regression" in argv
    # ACCEPTANCE IS THE MODE WITH NO FLAG, so it is stated rather than implied. It is
    # the only one that owes a report back, and it owes one on EVERY exit: a guard
    # refusal with no evidence file is read as evidence that could not be verified,
    # which turns a repairable red candidate into an inconclusive one nothing can act on.
    acceptance = not publication and not regression

    # The candidate checkout is wherever the caller ran us. Asserted rather than
    # inherited from import time so the guard, the tripwire and the validate command
    # all judge the same tree.
    config.ROOT = Path.cwd().resolve()
    config.REQUIRED_MARKERS = profile["markers"]
    config.SLACK_CAPS_AUTONOMY = profile["slack_caps_autonomy"]
    evaluation, identity = acceptance_binding() if acceptance else ("", {})

    code, structural_log = structural(profile, publication)
    if code or publication:
        # PUBLICATION STOPS HERE, deliberately. Delivery has already run the project's
        # own gate on this branch; what publication adds is the part a model cannot
        # waive -- protected paths, the size and scope caps, and the secret preflight.
        # It also writes no report: nothing is judging a receipt at publication time.
        if acceptance:
            acceptance_report(evaluation, identity,
                              "environment" if code == 75 else "failed", None)
        return code

    try:
        result = subprocess.run(shlex.split(profile["command"], posix=os.name != "nt"),
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=COMMAND_TIMEOUT_SECONDS)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"GATE_UNRUNNABLE: {profile['command']!r} could not run: {error}", file=sys.stderr)
        if acceptance:
            acceptance_report(evaluation, identity, "environment", None)
        return 75
    log = structural_log + result.stdout + result.stderr
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)

    try:
        measured = measure(log, result.returncode, profile["floor"])
    except (TypeError, ValueError) as error:
        print(f"GATE_UNRUNNABLE: the ratchet floor is unusable: {error}", file=sys.stderr)
        if acceptance:
            acceptance_report(evaluation, identity, "environment", None)
        return 75
    runtime.write(Path(profile["result"]), measured)
    print(json.dumps(measured))
    if regression:
        regression_report(measured)
    if acceptance:
        acceptance_report(evaluation, identity,
                          "failed" if measured["errors"] else "passed", measured)
    return 1 if measured["errors"] else 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as error:  # noqa: BLE001
        # A gate that crashed did not judge anything. Saying so as an ENVIRONMENT exit
        # is the difference between "we could not check" and "we checked and it failed".
        print(f"GATE_UNRUNNABLE: {type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(75)
