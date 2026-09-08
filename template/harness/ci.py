#!/usr/bin/env python3
"""The gate's entrypoint. `FACTORY_VALIDATE_CMD` points here.

    python harness/ci.py            the whole gate
    python harness/ci.py --quick    the cheap subset an implementing node runs on itself

THIS FILE IS THE STEP LADDER, AND THE LADDER IS THE SAME IN EVERY FACTORY.

That is not an assumption. Two people built this harness independently, on different
products, never seeing each other's work, and both wrote this shape: an ordered
ladder, a positive marker per rung with a count, a namer that reports WHICH rung
stopped the run, and a `--quick` subset. Both separately invented a "zero tests
discovered is not a pass" guard. The structure is determined by the marker contract,
not by the app.

EVERY COMMAND IT RUNS LIVES IN `harness.config.json`, not here. Hardcoding
`python -m pytest` would make a scaffold claiming to be universal quietly
Python-only: a Go repo, a Node repo or a CLI with no HTTP surface would have to
rewrite the ladder to change two strings.

WHAT IS STILL NOT TEMPLATABLE IS EVERY ASSERTION. What "working" means for your
product is the one thing nobody can write in advance. It lives in two markdown
files an agent reads on every run -- `harness/END-TO-END.md` and, above the
independence line, `.factory/holdout/HOLDOUT.md`.

THE CONTRACT, in four parts:

  1. A POSITIVE marker for every rung that RAN. The gate greps these by name from
     REQUIRED_MARKERS; it never tests for the absence of "error".
  2. A COUNT wherever one exists. A skipped check and a passed check are
     indistinguishable without one.
  3. Exit NON-ZERO when the software is broken.
  4. Print to STDOUT. The runner appends this to the guard's output in one gate log.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
QUICK = "--quick" in sys.argv

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

CONFIG = json.loads((HERE / "harness.config.json").read_text(encoding="utf-8"))


def unquote(token: str) -> str:
    """Take one layer of matching surrounding quotes off a token.

    Commands are split with `posix=False`, which is right on Windows because it
    leaves backslashes in paths alone -- and wrong in that it leaves the QUOTES
    attached to every token it split.

    That was applied to argv[0] only, and the consequence on the rest was severe:
    `python -c "import app"` arrived as the three tokens
    `python`, `-c`, `"import app"`, so Python evaluated the STRING LITERAL
    `"import app"` and exited 0. Measured: `python -c "import
    definitely_not_a_module"` also exits 0. The library driver's import check --
    the entire evidence behind `APP_STARTED driver=library` -- could not fail.
    The Node equivalent this shipped later had exactly the same hole.

    Stripping every token is what posix mode would have done, without giving up
    the backslashes.
    """
    if len(token) > 1 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    return token


def resolve(argv: list[str]) -> list[str]:
    """Make argv[0] something the OS can actually execute.

    On Windows the tools people configure here -- npm, npx, yarn, pnpm, most JS
    tooling -- are `.cmd` shims, and subprocess without a shell does not consult
    PATHEXT. So a perfectly correct `"unit": "npm test"` fails with "the system
    cannot find the file specified", which reads like the tool is not installed when
    it is on PATH and works in any terminal.

    THE QUOTES COME OFF FIRST, and that is not cosmetic. Commands are split with
    posix=False, which is right on Windows because it leaves backslashes in paths
    alone -- but it also leaves the QUOTES attached to the token, so an interpreter
    path containing a space arrives quoted, `shutil.which` cannot match it, and
    subprocess fails with the exact misleading error this function exists to
    prevent. `C:\\Program Files` is where Windows puts things.
    """
    if not argv:
        return argv
    # EVERY token, not just argv[0]. See unquote() for what only doing the head
    # cost: an import check that could not fail.
    argv = [unquote(t) for t in argv]
    return [shutil.which(argv[0]) or argv[0], *argv[1:]]


def run(step: str, cmd: str | list[str], timeout: int = 600) -> tuple[int, str]:
    """One rung. A TIMEOUT IS A FAILURE, not a skip -- a hung check reports nothing."""
    argv = resolve(shlex.split(cmd, posix=False) if isinstance(cmd, str) else list(cmd))
    try:
        p = subprocess.run(
            argv, cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"TIMEOUT after {timeout}s"
    except OSError as e:
        return 127, f"could not run {argv[0] if argv else cmd!r}: {e}"


def watchdog(seconds: int, label: str, app=None):
    """A hard deadline for the one rung that cannot have a subprocess timeout.

    THE RUNG MOST LIKELY TO HANG IS THE ONLY UNPROTECTED ONE. Every other rung goes
    through `run()`, which passes a timeout to subprocess. The e2e rung does not: it
    imports `run_e2e` and calls it in-process, so nothing upstream can interrupt it.

    Browser CLIs -- Playwright's driver server, chromedriver, agent-browser -- spawn
    a PERSISTENT DAEMON that inherits the stdout pipe, so a captured subprocess
    inside a journey blocks on EOF forever after the CLI itself has exited. The gate
    prints APP_STARTED and then nothing at all: no marker, no GATE_FAILED, no exit.
    Five minutes of silence is indistinguishable from a slow test, and an unattended
    factory waits for it all night while holding its dispatch lock.

    A timeout is a FAILURE and not a skip, so this prints the marker and exits
    non-zero. It tears the app down first on a best-effort basis: a hard exit skips
    context managers, and a leaked server holding the port poisons the next lap.
    """

    def bark() -> None:
        print(
            f"E2E_TIMEOUT after {seconds}s - the journey never returned. Nothing upstream "
            f"can interrupt this rung, so the run is being killed here. If a browser CLI "
            f"is involved, the usual cause is capturing the output of a process that "
            f"spawns a daemon: redirect to a real file handle instead. Raise "
            f"e2e_timeout_s in harness.config.json if the journey is genuinely this slow.",
            flush=True,
        )
        print(f"GATE_FAILED: {label}", flush=True)
        try:
            if app is not None:
                app.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass
        sys.stdout.flush()
        os._exit(124)

    t = threading.Timer(seconds, bark)
    t.daemon = True
    t.start()
    return t


def fail(step: str, detail: str = "") -> int:
    """Name the rung that actually stopped the run.

    The gate asserts markers in a FIXED order rather than run order, so without this
    a suite that died early is reported as whichever marker is checked first -- true,
    and several rungs downstream of the cause. Misnaming your own failure is most of
    the cost of a failure nobody watched.
    """
    if detail:
        print(detail.strip()[-4000:], flush=True)
    print(f"GATE_FAILED: {step}", flush=True)
    return 1


def skipped(name: str, marker: str) -> None:
    """A rung with no command configured. LOUD, never silent.

    An unconfigured rung that printed nothing is indistinguishable from one that
    passed, which is the exact failure this whole file exists to prevent -- so
    absence is a fact in the log, and the gate can be told to require the marker.
    """
    print(f"{marker}_SKIPPED no '{name}' command in harness.config.json", flush=True)


def main() -> int:
    print(
        f"HARNESS_START mode={'quick' if QUICK else 'full'} driver={CONFIG.get('driver')}",
        flush=True,
    )

    # --- 1. static -----------------------------------------------------------
    static_cmd = (CONFIG.get("static") or "").strip()
    if not static_cmd:
        skipped("static", "STATIC")
    else:
        rc, out = run("static", static_cmd)
        if rc != 0:
            return fail("static", out)
        print("STATIC_OK", flush=True)

    # --- 2. unit -------------------------------------------------------------
    unit_cmd = (CONFIG.get("unit") or "").strip()
    if not unit_cmd:
        skipped("unit", "UNIT")
    else:
        rc, out = run("unit", unit_cmd)
        if rc != 0:
            return fail("unit", out)
        pattern = (CONFIG.get("unit_count_pattern") or "").strip()
        if pattern:
            m = re.search(pattern, out)
            ran = int(m.group(1)) if m else 0
            # ZERO IS NOT A PASS. A suite that discovered nothing exits 0 and looks
            # perfect; both independent builds of this file added this guard
            # unprompted, which is how you know it is not paranoia.
            if ran == 0:
                return fail(
                    "unit",
                    "UNIT_ERROR: the runner reported 0 tests - a suite that ran nothing is "
                    "not a suite that passed. If the count is real, fix unit_count_pattern.\n"
                    + out[-2000:],
                )
            print(f"UNIT_PASSED tests={ran}", flush=True)
        else:
            print(
                "UNIT_PASSED tests=unknown (no unit_count_pattern set - a passing suite "
                "and an absent one look identical here)",
                flush=True,
            )

    if QUICK:
        # The subset an implementing node runs on itself. A STRICT subset: never a
        # check the full run lacks. Nothing downstream trusts it -- the full gate
        # re-runs everything independently, which is why the builder may run it at all.
        print("GATE_OK mode=quick", flush=True)
        return 0

    # --- 3. the app actually works -------------------------------------------
    # ONE OF THE TWO GATES THAT MUST BE CODE. Without a positive assertion here, a
    # crashed app produces a validator that reports "not testable" and something
    # downstream counts that as fine.
    #
    # The JOURNEYS are markdown and an agent drives them (see agentcheck.py). What
    # stays code is everything that decides whether the rung passed: the app really
    # started, the result really exists, every assertion carries an observed value,
    # and the counts meet the ratchet. The agent supplies evidence; it does not get
    # a vote on the verdict.
    sys.path.insert(0, str(HERE))
    from agentcheck import AgentCheckFailed, MalformedResult, run_rung  # noqa: E402
    from appproc import AppDidNotStart, make_driver  # noqa: E402

    def rung_with_one_retry(kind: str, app):
        """Run a rung; if the agent's REPORT was malformed, once more on a fresh app.

        A missing `observed`, an unreadable file, a contentless answer: none of it
        says anything about the product, and failing the gate on it sends a healthy
        pull request to a repair run that can only diagnose the harness. One retry,
        on a fresh app so the scenarios start from nothing again. The second
        malformed report is the harness failure it looks like.
        """
        try:
            return run_rung(kind, CONFIG, app), app
        except MalformedResult as e:
            print(f"{kind.upper()}_RETRY the agent's report was malformed; once more on a "
                  f"fresh app: {e}", flush=True)
            app.__exit__(None, None, None)
            app = make_driver(CONFIG)
            app.__enter__()
            return run_rung(kind, CONFIG, app), app

    app = make_driver(CONFIG)
    try:
        app.__enter__()  # prints APP_STARTED
    except AppDidNotStart as e:
        # NAMED, not a traceback. "The app did not start" is a specific rung with a
        # specific remedy, and a stack trace ending inside the driver reports the
        # place the exception surfaced rather than the thing that broke. An
        # unattended system that misnames its own failure sends whoever reads the log
        # at 3am to the wrong file, which is most of the cost of a failure nobody
        # watched.
        return fail("app-start", str(e))
    except Exception as e:  # noqa: BLE001
        return fail("app-start", f"{type(e).__name__}: {e}")

    try:
        wd = watchdog(int(CONFIG.get("e2e_timeout_s", 300)), "e2e", app)
        try:
            (journeys, steps, failures), app = rung_with_one_retry("e2e", app)
        except AgentCheckFailed as e:
            # The rung could not be RUN. Named separately from a failing journey
            # because the remedy is different and the log has to say which one it
            # was: a broken harness reads as a broken product otherwise, and
            # somebody spends the morning in the wrong file.
            return fail("e2e-harness", str(e))
        finally:
            wd.cancel()
        if failures:
            for f in failures:
                print(f"  E2E_FAIL  {f}", flush=True)
            return fail("e2e", f"{len(failures)} of {steps} assertions failed")
        print(f"E2E_PASSED journeys={journeys} steps={steps}", flush=True)

        # --- 4. holdout ------------------------------------------------------
        # Assertions the BUILDER cannot read. Everything below the independence line
        # sits inside the agent's optimisation loop; given enough attempts it
        # satisfies those rather than the thing you meant. The step change is
        # independence, not volume -- more tests below the line is not the fix.
        #
        # The SCENARIOS stay in .factory/holdout/, which every builder node is
        # denied. Only the method is public, in .claude/skills/factory-holdout.
        # Knowing that the holdout composes features does not help anybody pass it.
        holdout = ROOT / ".factory" / "holdout" / "HOLDOUT.md"
        if holdout.exists():
            # A FRESH APP, A FRESH DATABASE. The holdout asserts exact figures ("still
            # two links", "exactly one link"), and it used to run against the app the
            # journeys had just filled: seven links where it expected three, five
            # assertions red against a product that was fine (reference app,
            # 2026-09-08). An agent that tidied up after its journeys hid this; one
            # that did not, exposed it. The scenarios describe a user starting from
            # nothing, so that is what they get: a new port, and with it the new state
            # file the config's `{port}` names.
            try:
                app.__exit__(None, None, None)
                app = make_driver(CONFIG)
                app.__enter__()  # prints APP_STARTED again, on a new port
            except AppDidNotStart as e:
                return fail("holdout-harness", f"the app did not restart for the holdout: {e}")
            except Exception as e:  # noqa: BLE001
                return fail("holdout-harness", f"{type(e).__name__}: {e}")
            try:
                (scen, asserts, failures), app = rung_with_one_retry("holdout", app)
            except AgentCheckFailed as e:
                return fail("holdout-harness", str(e))
            if failures:
                for f in failures:
                    print(f"  HOLDOUT_FAIL  {f}", flush=True)
                return fail("holdout", f"{len(failures)} of {asserts} assertions failed")
            print(
                f"HOLDOUT_PASSED scenarios={scen} assertions={asserts}", flush=True
            )
        else:
            print(
                "HOLDOUT_ABSENT no .factory/holdout/HOLDOUT.md - NOTHING above the "
                "independence line ran. Every check in this gate is one the builder "
                "could read and iterate against.",
                flush=True,
            )
    finally:
        # Torn down on EVERY path including failure, or a leaked process holds the
        # port and poisons the next lap.
        app.__exit__(None, None, None)

    # --- 5. mutations ---------------------------------------------------------
    # NOT INSIDE A MUTATION RUN. The mutation runner copies harness/ into each
    # throwaway build, so without this the inner gate re-runs the whole suite -- 6
    # defects becomes 36 gate runs, and it also misattributes which rung caught the
    # defect.
    mutate = HERE / "mutations" / "run.py"
    if os.environ.get("FACTORY_IN_MUTATION") == "1":
        print("MUTATIONS_SKIPPED running inside a mutation build", flush=True)
    elif mutate.exists():
        rc, out = run("mutations", [sys.executable, str(mutate)], timeout=1800)
        print(out.strip(), flush=True)
        if rc != 0:
            return fail("mutations")
    else:
        print(
            "MUTATIONS_ABSENT no harness/mutations/run.py - this gate has never been "
            "shown to fail. A gate that has never failed is a gate nobody has tested.",
            flush=True,
        )

    print("GATE_OK mode=full", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
