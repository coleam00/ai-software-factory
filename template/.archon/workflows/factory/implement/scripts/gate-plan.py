"""Read what the plan node decided, and act on it in code.

Three files the plan node may write, and they mean three different things:

  plan.md       required. No plan, no build -- a node asked to implement from a
                plan that does not exist invents one, and an invented plan is the
                most expensive kind of guess this system can make.

  ESCALATE      the short stop list fired. Park the issue, tell a human, and CANCEL
                the run. Everything downstream would otherwise run against a plan
                that says "do not build this".

  ASSUMPTIONS   a PRODUCT value the plan chose rather than stopped for. This does
                NOT stop the run. It rides into the PR record and holds the MERGE,
                so the work is built, validated and waiting with the reasoning at
                the top -- and the human answers a concrete question about a running
                thing instead of an abstract one in the dark.

  FOLLOWUP      part of the issue the plan deliberately left. Recorded rather than
                lost: partially building an issue is right, silently dropping the
                rest is not.

THE FAILURE THIS REPLACES. When the plan node is told to stop for "any open
question", one unmade product decision blocks every issue downstream of it. Measured
on a real factory: four issues, four escalations, zero PRs, and the SAME question
asked four times -- because an open question in a PRD was read as "you may not
propose" when the author meant "I have not decided". The more honest the PRD, the
less the factory could do.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "factory"))

# nodeio FIRST, before anything that might print at import time: importing it
# redirects stdout to stderr for the whole process, so a library marker can never
# be mistaken for this node's value. emit() writes to the real stdout.
from nodeio import emit, note  # noqa: E402

import config  # noqa: E402
import notify  # noqa: E402
import state  # noqa: E402

target = (os.environ.get("INPUTS_TARGET") or "").strip()
artifacts = Path(os.environ.get("ARTIFACTS_DIR") or ".")


def read(name: str) -> str:
    p = artifacts / name
    return p.read_text(encoding="utf-8", errors="replace").strip() if p.exists() else ""


plan = read("plan.md")
escalate = read("ESCALATE")

# THE PACK'S OWN REFUSAL, which is now the primary one. The planner is Archon's
# `archon-plan`; it returns `ready` and writes no ESCALATE sentinel, so treating a
# missing sentinel as consent would silently discard every refusal it makes. The
# sentinel is still read above because a REWRITTEN planner -- this prompt is the
# personalisation layer -- may still use it, and losing that would be the same bug in
# the other direction.
#
# Anything that is not an explicit "true" is a refusal. An unset binding, an empty
# string or a malformed value all mean the same thing here: nobody said it was ready,
# and this is the last gate before an unattended build starts spending.
plan_ready = (os.environ.get("INPUTS_PLAN_READY") or "").strip().lower()
if plan_ready and plan_ready != "true":
    note("PLAN_NOT_READY: archon-plan returned ready=" + plan_ready)
    try:
        state.main(["set", target, "state=needs-human"])
    except Exception:  # noqa: BLE001
        note("  (could not park " + target + " at needs-human)")
    try:
        notify.send(
            "plan not ready: " + target,
            "archon-plan declined to plan this as specified. Read plan.md on the run.",
        )
    except Exception:  # noqa: BLE001
        pass
    emit({"proceed": False, "reason": "archon-plan returned ready=" + plan_ready})
    sys.exit(0)
assumptions = read("ASSUMPTIONS")
followup = read("FOLLOWUP")

# --- the stop list ------------------------------------------------------------
if escalate:
    reason = escalate.splitlines()[0][:300] if escalate.splitlines() else "the plan node escalated"
    note(f"PLAN_ESCALATED: {reason}")
    try:
        state.main(["set", target, "state=needs-human"])
    except Exception:  # noqa: BLE001
        pass
    config.NEEDS_HUMAN.parent.mkdir(parents=True, exist_ok=True)
    with config.NEEDS_HUMAN.open("a", encoding="utf-8") as fh:
        fh.write(
            f"- {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}  {target}  "
            f"(plan)  {reason}\n"
        )
    try:
        state.comment(
            target,
            "**Factory plan: escalated**\n\n"
            f"{escalate[:3000]}\n\n"
            "_No branch was opened. The factory stops here until a human acts._",
        )
    except Exception:  # noqa: BLE001
        pass
    note(notify.send(target, f"(plan) {reason}"))
    emit({"proceed": False, "reason": reason})
    sys.exit(0)

# --- the plan itself ----------------------------------------------------------
if not plan:
    # NOT a proceed:false. This is a machinery fault rather than a decision about the
    # work, and the two must not look the same: an escalation is a considered stop,
    # this is a node that did not do its job. Failing loudly re-dispatches the issue
    # rather than parking it with a reason that would be a lie.
    note(
        f"PLAN_MISSING: the plan node wrote no {artifacts / 'plan.md'}. A build node "
        f"handed no plan invents one. This is a workflow fault -- the issue keeps its "
        f"state and the dispatcher will try again.",
    )
    sys.exit(1)

# --- assumptions hold the merge, they do not stop the work --------------------
if assumptions:
    config.ASSUMPTIONS_DIR.mkdir(parents=True, exist_ok=True)
    dest = config.ASSUMPTIONS_DIR / f"{target.replace(':', '-')}.txt"
    dest.write_text(assumptions, encoding="utf-8")
    n = len([ln for ln in assumptions.splitlines() if ln.strip()])
    note(f"ASSUMPTIONS_RECORDED {n} -> {dest} (the build continues; the MERGE will be held)")
    for line in assumptions.splitlines()[:20]:
        note(f"    {line}")

if followup:
    followups = config.SHARED / ".factory" / "followups"
    followups.mkdir(parents=True, exist_ok=True)
    (followups / f"{target.replace(':', '-')}.md").write_text(followup, encoding="utf-8")
    note(f"FOLLOWUP_RECORDED - part of this issue was deliberately left")

note(f"PLAN_OK {len(plan.splitlines())} lines")
emit({"proceed": True, "reason": "plan written"})
