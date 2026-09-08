#!/usr/bin/env python3
"""Run one agent-driven rung and check what it reports.

The journeys and the holdout scenarios are markdown, not Python. An agent reads
them, drives the running app, and writes a result file. This module builds that
prompt, runs the agent, and validates the result.

WHY MARKDOWN AND NOT A SCRIPT
-----------------------------
A scripted end-to-end runs the same two requests forever. It goes stale the week
after it is written, because the product moves and the script does not, and the
staleness is invisible: it still passes. Journeys in prose stay true to the
product because the agent re-reads them against whatever the app is today, and
they cover more than the one path somebody had time to code.

WHAT THAT COSTS, AND WHERE IT IS PAID BACK
------------------------------------------
A model reporting on its own work is the exact shape of defect this project keeps
finding: something announces success without checking anything. Three mechanisms
answer that, and none of them is "trust the agent":

  1. THE RESULT IS DATA, NOT A CLAIM. Every assertion carries the value actually
     observed. `_validate` rejects a result whose `observed` is empty, or is a
     restatement of `expected`, because that is a report from a run that did not
     look.
  2. THE COUNTS FEED THE RATCHET. `E2E_PASSED steps=N` is compared to a protected
     floor. An agent that quietly asserts less than last time fails the gate.
  3. THE MUTATION SET MEASURES THIS RUNG. Deliberate defects are planted and the
     gate must go red. That is the only evidence that any of these checks can
     fail, and it now covers the agent as well as the code.

FAIL CLOSED, ALWAYS. A missing result file, an unparseable one, a timeout, or an
agent that is not configured are all FAILURES. None of them is a skip. An empty
answer read as a negative answer is how a gate reports green having run nothing.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


class AgentCheckFailed(RuntimeError):
    """The rung did not pass. The message is what the log says."""


# --- what each rung is made of ------------------------------------------------

RUNGS = {
    "e2e": {
        "spec": "harness/END-TO-END.md",
        "skill": ".claude/skills/factory-e2e/SKILL.md",
        "result": "harness/.results/e2e.json",
        "group": "journeys",
        "noun": "journey",
    },
    "holdout": {
        "spec": ".factory/holdout/HOLDOUT.md",
        "skill": ".claude/skills/factory-holdout/SKILL.md",
        "result": ".factory/holdout/.results/holdout.json",
        "group": "scenarios",
        "noun": "scenario",
    },
}


# --- how the agent reaches the app --------------------------------------------


def reach(config: dict, app, rung_noun: str = "journey") -> str:
    """Describe the ALREADY RUNNING app, in the words the agent needs.

    The app is started once by `ci.py` and held open for both rungs. Telling the
    agent to start its own would leave two processes fighting for a port, and the
    second one's failure would be reported as a broken product.
    """
    driver = config.get("driver", "http")
    if driver == "http":
        http = config.get("http", {})
        port = str(getattr(app, "port", ""))
        start = (http.get("start") or "").replace("{port}", port)
        # THE ENVIRONMENT IS PART OF THE START COMMAND. The harness launches the app
        # with `http.env` (that is where the gate's own database path lives), but the
        # restart line handed to the agent used to carry only `http.start`. An agent
        # that restarted the app exactly as told then opened the app's DEFAULT database,
        # empty, and every persistence-across-restart scenario failed against a product
        # that had lost nothing. Three fix runs diagnosed it independently before it was
        # believed. The variables are spelled out so a restart is the same launch.
        env = {k: str(v).replace("{port}", port) for k, v in (http.get("env") or {}).items()}
        env_line = ""
        if env:
            shown = " ".join(f"{k}={v}" for k, v in env.items())
            env_line = (
                f"It was started WITH THESE ENVIRONMENT VARIABLES, and a restart must set the "
                f"same ones or it opens a different database: {shown}\n"
            )
            start = shown + " " + start
        return (
            f"The app is ALREADY RUNNING at {app.base}\n"
            f"Reach it with curl, or any HTTP client. Do not start another one.\n"
            f"Health endpoint: {http.get('health_path', '/health')}\n"
            f"{env_line}"
            f"If a step needs a restart, the start command is: {start}\n"
            f"Restart means: stop that process, run it again on the SAME port "
            f"({port}), with the SAME environment, wait for the health endpoint, continue."
            "\nTHE PORT IS NOT NEGOTIABLE. Anything started on a different port is "
            "invisible to the gate, which then cannot re-check your evidence and "
            "cannot stop the process afterwards."
            f"\nThe {rung_noun}s below run IN ORDER against this one app and SHARE "
            f"its state. Only restart when a {rung_noun} cannot be true otherwise."
        )
    if driver == "cli":
        return (
            "The app is a command line tool. Invoke it with:\n"
            f"  {config.get('cli', {}).get('invoke', '')}\n"
            "substituting your arguments for {args}. There is no process to restart; "
            "each invocation is already a fresh one."
        )
    return (
        "The app is a library. There is no process. Exercise it by importing it and "
        "calling it from a short script you write in a temporary directory:\n"
        f"  {config.get('library', {}).get('import_check', '')}\n"
        "A restart means a fresh interpreter, so run a second script."
    )


# --- the prompt ---------------------------------------------------------------


def build_prompt(kind: str, config: dict, app, only: str = "") -> tuple[str, Path]:
    rung = RUNGS[kind]
    spec_path = ROOT / rung["spec"]
    skill_path = ROOT / rung["skill"]
    result_path = ROOT / rung["result"]

    if not spec_path.exists():
        raise AgentCheckFailed(
            f"{rung['spec']} does not exist. That file is where your {rung['noun']}s "
            f"live and nobody else can write it. `factory doctor` says which level "
            f"this blocks."
        )
    if not skill_path.exists():
        raise AgentCheckFailed(
            f"{rung['skill']} does not exist, so there is nothing telling the agent "
            f"how to run this rung or what to write. Reinstall the skills with "
            f"`factory init`."
        )

    spec = spec_path.read_text(encoding="utf-8")
    skill = skill_path.read_text(encoding="utf-8")
    # The skill body is INLINED rather than loaded by name. Whether a given CLI
    # auto-discovers a project skill is its own business and varies by tool; a
    # prompt that carries its own instructions works on all of them, and cannot
    # silently run with the instructions missing.
    if skill.startswith("---"):
        parts = skill.split("---", 2)
        skill = parts[2] if len(parts) > 2 else skill

    scope = (
        f"\nRun ONLY the {rung['noun']} named: {only}\n" if only else ""
    )

    prompt = f"""You are running the {kind} rung of a software factory's validation gate.

Follow these instructions exactly.
{skill}

--- THE {rung['noun'].upper()}S, from {rung['spec']} ---

{spec}

--- HOW TO REACH THE APP ---

{reach(config, app, rung['noun'])}
{scope}
--- WHERE TO WRITE THE RESULT ---

Write the JSON result to this exact path, creating the directory if needed:

  {result_path}

Write nothing else to the repository. Do not modify application code, tests,
{rung['spec']}, or anything under harness/. When the file is written, stop.
"""
    return prompt, result_path


# --- running it ---------------------------------------------------------------


def agent_command(config: dict) -> list[str]:
    cmd = os.environ.get("FACTORY_AGENT_CMD") or config.get("agent", {}).get("cmd") or ""
    if not cmd.strip():
        raise AgentCheckFailed(
            "No agent command configured. Set `agent.cmd` in harness/harness.config.json "
            "(or FACTORY_AGENT_CMD). This rung is driven by a coding agent reading "
            "markdown, so without one there is nothing to run. It is a FAILURE and not a "
            "skip: a gate that quietly drops its end-to-end rung reports green having "
            "never touched the app."
        )
    return shlex.split(cmd, posix=os.name != "nt")


def run_rung(kind: str, config: dict, app, only: str = "") -> tuple[int, int, list[str]]:
    """Run one rung. Returns (groups, assertions, failures).

    Raises AgentCheckFailed when the rung could not be run at all, which is a
    different thing from the app being broken and is named differently in the log.
    """
    rung = RUNGS[kind]
    prompt, result_path = build_prompt(kind, config, app, only)
    argv = agent_command(config)
    timeout = int(config.get("agent", {}).get("timeout_s", 900))

    # The stale-result trap. If a previous run's file is still here and this run
    # dies before writing, the old file is read as this run's answer -- a gate that
    # passes on evidence from a build that no longer exists.
    if result_path.exists():
        result_path.unlink()
    result_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"AGENT_RUNNING rung={kind} cmd={argv[0]}", flush=True)
    try:
        proc = subprocess.run(
            argv, input=prompt, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, cwd=str(ROOT),
        )
    except FileNotFoundError:
        raise AgentCheckFailed(
            f"the agent command `{argv[0]}` is not on PATH. harness.config.json points "
            f"at it; either install it or change `agent.cmd`."
        ) from None
    except subprocess.TimeoutExpired:
        raise AgentCheckFailed(
            f"the agent did not finish within {timeout}s. Raise `agent.timeout_s` if the "
            f"{rung['noun']}s are genuinely that long, but check first that the app is "
            f"answering: an agent waiting on a dead port also looks like this."
        ) from None

    tail = ((proc.stdout or "") + (proc.stderr or "")).strip()

    if not result_path.exists():
        # DELIBERATELY NOT A SKIP. The agent exiting 0 having written nothing is the
        # most likely quiet failure here, and reading that as "nothing to report"
        # would turn a rung that never ran into a rung that passed.
        raise AgentCheckFailed(
            f"the agent wrote no result file at {rung['result']} (exit {proc.returncode}). "
            f"Last output:\n{tail[-1500:]}"
        )

    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise AgentCheckFailed(f"the result file is not readable JSON: {e}") from None

    # "I COULD NOT CHECK" IS A DIFFERENT ANSWER FROM "IT IS BROKEN", and the log has
    # to say which. Measured here: an agent whose shell was locked down could not
    # issue a single request and reported all 13 assertions failed. Every word of
    # that was true and the gate said GATE_FAILED: e2e, which reads as a broken
    # product and sends the next hour to the wrong file.
    #
    # THERE IS NO INCENTIVE TO ABUSE THIS. Both branches fail the gate. Only the
    # NAME changes, and the app is re-probed below so the name is not taken on the
    # agent's word either.
    if isinstance(data, dict) and data.get("blocked"):
        why = str(data.get("blocked_reason") or "").strip() or "no reason given"
        alive = _still_healthy(config, app)
        if alive is True:
            raise AgentCheckFailed(
                f"the agent reported it could not check anything: {why}. The app "
                f"ANSWERED a health probe from this process immediately afterwards, so "
                f"this is the agent's environment, not the product. Check that "
                f"`agent.cmd` can run a shell command."
            )
        if alive is False:
            # It said it could not reach the app and the app is in fact down. That is
            # the product, and calling it a harness problem would bury a real failure.
            return _validate(kind, {RUNGS[kind]["group"]: [{
                "name": "the app stayed up for the whole rung",
                "assertions": [{
                    "name": "the app answers a health probe after the rung",
                    "expected": "a healthy response",
                    "observed": f"no response. The agent also reported: {why}",
                    "ok": False,
                }],
            }]})
        raise AgentCheckFailed(
            f"the agent reported it could not check anything: {why}. There is no health "
            f"probe for this driver, so which side is broken is unknown -- and an "
            f"unknown is never a pass."
        )

    groups, assertions, failures = _validate(kind, data)

    # THE HARNESS HAS TO GET THE APP BACK. A journey can legitimately end with the
    # process stopped -- "the list survives a restart" is a journey, and the agent
    # may well stop it last. What is not acceptable is the next rung running against
    # a port nobody is listening on and reporting that as a broken product.
    #
    # The FIRST version of this failed the rung outright, and it fired on its first
    # real run against a report that was fine. Demanding the app be up afterwards
    # confuses housekeeping with evidence: the assertions and their observed values
    # are the evidence, and they stand on their own.
    #
    # So: if it is down, put it back. Only a failure to come back is a failure, and
    # then it is named as the harness rather than the product.
    if _still_healthy(config, app) is False:
        print("APP_DOWN_AFTER_RUNG restarting it for the next one", flush=True)
        try:
            app.__exit__(None, None, None)
            app.__enter__()
        except Exception as e:  # noqa: BLE001
            raise AgentCheckFailed(
                f"the app was not answering after the {kind} rung and would not "
                f"restart: {e}. Anything the agent started on a different port is "
                f"still running and holding it."
            ) from None
        if _still_healthy(config, app) is False:
            raise AgentCheckFailed(
                f"the app was not answering after the {kind} rung and did not come "
                f"back on {getattr(app, 'base', 'its port')}. The next rung would run "
                f"against nothing and report it as a broken product."
            )

    return groups, assertions, failures


def _still_healthy(config: dict, app) -> "bool | None":
    """Is the app answering right now? None when there is nothing to ask.

    Only meaningful for the http driver. A cli or library app has no liveness to
    probe, and inventing one would answer a question nobody asked.
    """
    if config.get("driver", "http") != "http" or not hasattr(app, "get"):
        return None
    path = config.get("http", {}).get("health_path", "/health")
    want = config.get("http", {}).get("health_contains", "")
    try:
        status, body, _ = app.get(path)
    except Exception:  # noqa: BLE001
        return False
    return status == 200 and (not want or want in body)


# --- checking the report ------------------------------------------------------


def _validate(kind: str, data: object) -> tuple[int, int, list[str]]:
    """Reject a report that is not evidence, before counting anything in it.

    Every rule here exists because the alternative is a rung that passes on a
    sentence. `ok: true` with nothing observed is a claim; it is not a check.
    """
    rung = RUNGS[kind]
    group_key = rung["group"]

    if not isinstance(data, dict) or not isinstance(data.get(group_key), list):
        raise AgentCheckFailed(
            f"the result has no `{group_key}` list. Expected "
            f'{{"{group_key}": [{{"name": ..., "assertions": [...]}}]}}'
        )

    groups = data[group_key]
    if not groups:
        raise AgentCheckFailed(
            f"the result reports zero {rung['noun']}s. An empty run is not a pass."
        )

    assertions = 0
    failures: list[str] = []

    for i, g in enumerate(groups):
        if not isinstance(g, dict):
            raise AgentCheckFailed(f"{group_key}[{i}] is not an object")
        gname = str(g.get("name") or f"{rung['noun']} {i + 1}")
        items = g.get("assertions")
        if not isinstance(items, list) or not items:
            raise AgentCheckFailed(
                f"'{gname}' has no assertions. A {rung['noun']} that checked nothing "
                f"is a failure, not a pass."
            )
        for j, a in enumerate(items):
            if not isinstance(a, dict):
                raise AgentCheckFailed(f"'{gname}' assertion {j + 1} is not an object")
            missing = [k for k in ("name", "expected", "observed", "ok") if k not in a]
            if missing:
                raise AgentCheckFailed(
                    f"'{gname}' assertion {j + 1} is missing {', '.join(missing)}. "
                    f"All four keys are required; `observed` is the one that makes this "
                    f"a measurement rather than an opinion."
                )
            observed = str(a.get("observed") or "").strip()
            expected = str(a.get("expected") or "").strip()
            name = str(a.get("name") or f"assertion {j + 1}")
            if not observed:
                raise AgentCheckFailed(
                    f"'{gname}' / '{name}' reports no observed value. An assertion with "
                    f"nothing observed did not run."
                )
            # A CONTENTLESS ANSWER IS NOT A MEASUREMENT. "as expected" is the shape an
            # agent produces when it did not look, and it is indistinguishable from
            # success unless it is refused here.
            #
            # WHAT IS *NOT* CHECKED, AND WHY IT WAS REMOVED. This also refused
            # `observed == expected`, and that rule could not work. On a passing
            # concrete assertion the two are IDENTICAL by construction -- expected
            # "total 14, held 6, committed 0, available 8", observed the same string,
            # because that is what the app returned and the check passed. The rule
            # therefore had no discriminating power at all: it fired on every honest
            # passing measurement whose expectation was written as a value.
            #
            # It cost a full holdout run on a healthy app. At level 3 that is not a
            # nuisance -- the holdout is a required marker, so nothing can ever merge,
            # and the failure reads as "the agent is fabricating" when the agent did
            # exactly what it was asked.
            #
            # What DOES discriminate is below: an observation that restates the
            # assertion's own NAME is a restatement, because the name is the question
            # and the observation is supposed to be the answer.
            if observed.lower() in _EMPTY_ANSWERS:
                raise AgentCheckFailed(
                    f"'{gname}' / '{name}' observed {observed!r}, which says nothing "
                    f"about what happened. Report the value the app actually produced."
                )
            if observed.lower() == name.lower():
                raise AgentCheckFailed(
                    f"'{gname}' / '{name}' observed {observed!r}, which restates the "
                    f"assertion instead of answering it. Report the value the app "
                    f"actually produced."
                )
            assertions += 1
            if not a.get("ok"):
                failures.append(f"{gname} / {name}: expected {expected}, observed {observed}")

    if assertions == 0:
        raise AgentCheckFailed("zero assertions ran. That is a failure, never a pass.")

    return len(groups), assertions, failures


# Phrases that are a way of saying nothing. Kept short and literal on purpose: a
# clever similarity check here would start rejecting honest short answers like
# "200" or "empty", which are real observations.
_EMPTY_ANSWERS = {
    "as expected", "ok", "okay", "pass", "passed", "success", "successful",
    "correct", "matches", "match", "same", "yes", "true", "n/a", "na", "none",
    "as described", "as above", "worked", "works", "fine", "good",
}
