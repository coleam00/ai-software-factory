"""Marker, ratchet and assumption measurements shared by the fixed SDLC gate."""

from __future__ import annotations

import json
import re
import config


def read_floor() -> dict:
    if not config.FLOOR_FILE.exists():
        return {}
    try:
        return floors(json.loads(config.FLOOR_FILE.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return {}


def floors(raw: dict) -> dict:
    """Which keys in a floor file are FLOORS. The one place that decides.

    Two readers of one file is the shape of the bug this exists to prevent, and the
    gate is no longer the only one: `fixed_gate` enforces the floor and `merge` raises
    it, so "what counts as a floor key" had to stop being a filter written out three
    times. A change to what the file MEANS lands here or nowhere.
    """
    # `_MAX` KEYS ARE CEILINGS, NOT FLOORS, and missing this cost a blocked PR.
    #
    # I taught `harness/ci.ts` to skip them and forgot this reader, which is the other
    # half of the same ratchet. The gate then demanded a count for `UNCALIBRATED_MAX`,
    # no rung emits a marker by that name, and it escalated PR #15 and its issue with:
    # "a floor nothing measures is a floor nobody is held to". That message is exactly
    # right and it was aimed at a key that is not a floor.
    #
    # Two readers of one file is the shape of the bug: a change to what the file MEANS
    # has to land in every place that reads it, and one of them was in TypeScript in
    # another directory. `bin/audit.py` now checks the pair agree.
    return {k: v for k, v in raw.items()
            if isinstance(v, int) and not k.startswith("_") and not k.endswith("_MAX")}


def counted(log: str, marker: str) -> int | None:
    """`E2E_PASSED steps=11` -> 11. The count is the point: a skipped check and a
    passed check are indistinguishable without one."""
    m = None
    for m in re.finditer(rf"{re.escape(marker)}[^\n]*?=(\d+)", log):
        pass
    return int(m.group(1)) if m else None


# Which marker carries the observed count for each floor key. Adding a floor key
# without a source here is a configuration error, reported rather than ignored --
# a ratchet nobody can read is not a ratchet.
FLOOR_SOURCES = {
    # JOURNEYS AND SCENARIOS, NOT ASSERTIONS, for the two agent-driven rungs.
    #
    # An agent reading END-TO-END.md decides how many assertions a journey needs, and
    # that number moves between runs. Measured on the SAME unchanged code: 12, then 13.
    # merge.py raises each floor
    # to what the gate just observed, so an assertion floor would climb to the
    # luckiest run and then fail every ordinary one -- a helpful extra check turning
    # into a broken factory two laps later.
    #
    # The journey COUNT is stable, because it is the number of headings in a file
    # that lives on the protected list. Deleting a journey is the thing this floor
    # exists to prevent, and it is already an auto-reject; the floor is the backstop
    # for a rung that silently stops running one.
    #
    # The assertion counts are still printed on every run. They are a signal to read,
    # not a number to hold a merge against.
    "e2e_journeys": "E2E_PASSED journeys",
    "holdout_scenarios": "HOLDOUT_PASSED scenarios",
    "unit_tests": "UNIT_PASSED tests",
    "mutations_caught": "MUTATIONS_CAUGHT",
}


def observed_counts(log: str, floor_keys: "list[str] | None" = None) -> dict[str, int | None]:
    """What the run log says each floor key measured.

    A KEY THAT NAMES ITS OWN MARKER NEEDS NO MAPPING, and that is now the default.
    The table above describes this template's example harness; a project with its own
    vocabulary got "the ratchet has a floor for UNIT_CHECKS but the run log reports no
    count for it" while the log said `UNIT_CHECKS=64` three lines further up. The
    floor file and the harness agreed with each other and disagreed only with a lookup
    table neither of them had heard of.

    So: use the mapping when there is one, and otherwise look for `KEY=<n>`, which is
    what a harness that named its floor keys after its own markers already emits.
    """
    out: dict[str, int | None] = {
        "e2e_journeys": counted(log, "E2E_PASSED journeys"),
        "holdout_scenarios": counted(log, "HOLDOUT_PASSED scenarios"),
        "unit_tests": counted(log, "UNIT_PASSED tests"),
        "mutations_caught": counted(log, "MUTATIONS_CAUGHT"),
        # Read for an install that predates the agent-driven rungs, so its floor file
        # keeps working rather than failing as "a floor nothing measures".
        "e2e_steps_asserted": counted(log, "E2E_PASSED steps"),
        "holdout_assertions": counted(log, "assertions"),
    }
    for key in floor_keys or []:
        if key in out and out[key] is not None:
            continue
        out[key] = counted(log, key)
    return out


def uncalibrated_total(log: str) -> int:
    """How many margins this run reported as having no threshold anybody set.

    A FUNCTION rather than an inline regex so the self-test can call the real thing.
    The first version of that test carried its own copy of the pattern, which meant
    breaking the pattern here would not have failed it -- a check that cannot see the
    code it is checking, which is exactly how the dead `_UNCALIBRATED` regex survived
    unnoticed in the first place.
    """
    return sum(int(n) for n in re.findall(r"UNCALIBRATED=(\d+)", log))


def assumption_keys(text: str) -> list[str]:
    """The KEYS in an assumptions file, in order.

    The format is `KEY=value` at the start of a line, followed by an indented WHY
    paragraph that may run for many lines. Anything indented belongs to the key above
    it, so only unindented `NAME=` lines are assumptions.
    """
    keys: list[str] = []
    for line in text.splitlines():
        if not line or line[0].isspace() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if m:
            keys.append(m.group(1))

    # A NON-EMPTY FILE IS NEVER ZERO ASSUMPTIONS. The KEY=value shape was an implicit
    # contract between this parser and the factory's own plan prompt. The planner is
    # Archon's `archon-plan` now, which records an assumption as prose and has never
    # heard of the format -- so a real, merge-holding assumption counted as 0 and the
    # hold announced itself as "0 recorded assumption(s)".
    #
    # That is the exact failure the comment at the call site warns about: the count is
    # the first thing a person reads on a hold, and one that misdescribes itself gets
    # rubber-stamped. Under-reporting to zero is the worst direction available, because
    # it reads as "nothing to review" on a PR that is being held precisely because there
    # is something to review.
    #
    # So: fall back to counting paragraphs. Wrong-by-a-little beats confidently zero,
    # and the text itself is printed on the hold either way.
    if not keys and text.strip():
        # Unindented, non-blank lines: in the KEY=value shape those ARE the keys, and
        # in free prose it is the block itself. No regex, because the only thing this
        # has to get right is "not zero".
        entries = [ln for ln in text.splitlines() if ln.strip() and not ln[0].isspace()]
        return ["(unkeyed " + str(n + 1) + ")" for n in range(len(entries) or 1)]
    return keys


