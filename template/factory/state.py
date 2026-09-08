"""The state machine. GitHub issues and PRs, `factory:*` labels as the state.

    python factory/state.py next                     what the dispatcher should run
    python factory/state.py get gh:issue:3
    python factory/state.py set gh:issue:3 state=accepted priority=high
    python factory/state.py body gh:issue:3          the item, as text a node can read
    python factory/state.py comment gh:pr:4          body on stdin
    python factory/state.py list issues --state accepted
    python factory/state.py bump-attempt gh:pr:4
    python factory/state.py stop-requested
    python factory/state.py init-labels

WHY THE LABELS ARE THE STATE. They are visible, editable by a human from a phone,
already backed up, and they ARE the audit trail. No database, no message bus. If
information has to travel between workflows it moves as a label or a comment.

`set` REFUSES a transition the table does not allow. That refusal is the reason
this is a module rather than three lines of `gh` in a workflow: a node that wants a
transition the table forbids has misunderstood something, and inventing the
transition buries the misunderstanding instead of surfacing it.

FOUR THINGS LABELS ARE NOT, all learned the hard way and all handled below:

 1. There is no compare-and-swap. Two dispatchers reading `factory:accepted` both
    claim the issue. The label is the audit trail, not the mutex -- the per-target
    lock in dispatch.py is.
 2. Label writes are not immediately visible to label reads. Measured lag between
    setting a label and seeing it on the list endpoint was two to four seconds, so
    nothing here writes and immediately re-reads to confirm its own write.
 3. The stop button must be a label you ADD, and the read must FAIL CLOSED.
 4. Closed is not a disposition. `deferred` and `rejected` are both closed issues
    and indistinguishable without labels -- and GitHub performs transitions this
    table never authorised (a merged PR saying `Closes #N` closes the issue), so
    closed-and-unlabelled is a real state that has to be handled.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from typing import Any

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))

import config  # noqa: E402

# UTF-8 on every stream, unconditionally. Windows defaults stdio to the ANSI
# codepage, so a rejection comment piped in through `comment` arrives with every
# non-ASCII character replaced -- and nothing notices, because the only thing
# checked afterwards is the exit code.
for _s in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass


ISSUE_STATES = [
    "untriaged",
    "accepted",
    "deferred",
    "rejected",
    "in-progress",
    "needs-human",
    "done",
]
# `needs-human` IS A PR STATE, and leaving it off this list cost 68 dispatches.
#
# `_state_from_labels` skips any state that is an ISSUE state but not a PR state, so a
# pull request carrying `factory:needs-human` fell through the whole table and was read
# back as `open`. `next_action` selects open PRs as "awaiting the independent
# validator", so escalating a PR to needs-human -- the one state that is supposed to
# STOP the machine and hand over to a person -- put it straight back at the front of
# the queue. The label was written correctly every time; it was the read that lied.
#
# TRANSITIONS has always listed needs-human as a legal PR destination, so the two
# tables disagreed, and only the transitions one was ever consulted by a human.
#
# `held` was missing for the same reason and read back correctly only by luck: it is
# in neither list, so the skip never fired for it. It is a PR-only state (`doctor` is
# the only reader, via _list("prs", "held")), so it is declared here rather than left
# to depend on an accident.
PR_STATES = ["open", "validating", "passed", "failed", "rejected", "merged", "needs-human", "held"]

# The one legality table. Anything not on it is refused.
TRANSITIONS: dict[str, set[str]] = {
    "untriaged": {"accepted", "deferred", "rejected", "needs-human"},
    "accepted": {"in-progress", "needs-human", "rejected"},
    "in-progress": {"done", "accepted", "needs-human"},
    "deferred": {"accepted", "needs-human"},
    "rejected": {"accepted", "needs-human"},
    "done": {"needs-human"},
    # A node may never move an item out of needs-human. Only a human, by removing
    # the label. This empty set is the whole escalation guarantee.
    "needs-human": set(),
    # GITHUB MOVES STATE TOO. `Closes #N` in a merged PR closes the issue the
    # moment it merges -- a transition this table never authorised, performed by
    # something that has never read it. The result is a closed issue with no
    # disposition label, which is genuinely "closed, reason not recorded". Treated
    # like `untriaged`: a state you arrive in from outside, from which any honest
    # disposition is reachable. It is NOT a state any node may leave in place.
    "closed-unlabelled": {"done", "deferred", "rejected", "needs-human"},
    "open": {"validating", "needs-human"},
    "validating": {"passed", "held", "failed", "rejected", "needs-human"},
    # HELD. Green on every structural check, and waiting for a person to agree with
    # a call the factory made -- ratchet slack, an uncalibrated threshold, a recorded
    # assumption. It is NOT a failure and NOT needs-human: nothing is wrong and the
    # factory carries on with other work.
    #
    # It exists because the hold used to be a sentence in a PR comment. The gate
    # printed "merge HELD", set the PR to `passed`, and the dispatcher merged it
    # forty-five seconds later -- because `passed` is what a mergeable PR is called.
    # The most subtle gate in the system was defeated by the most obvious one.
    #
    # `held -> open` is the resume: a human raises the floor or accepts the
    # assumptions, and the next validation produces no hold. Only a human moves it,
    # which is the entire point.
    "held": {"open", "needs-human", "rejected"},
    # `failed -> open`, and not `-> validating`. A fixed PR is a PR waiting to be
    # validated, and `open` is what waiting-to-be-validated is called. `validating`
    # is owned by a running validation and nothing else may hand it out.
    "failed": {"open", "rejected", "needs-human"},
    # `passed -> open` requeues for revalidation, which is what a PR needs after a
    # rebase. Without it, any commit landing on the base during a lap parks the PR
    # forever: the merge correctly refuses a branch that is now behind, and the
    # documented remedy is the one move the table forbids.
    "passed": {"merged", "open", "needs-human"},
    "merged": set(),
}

LABEL_FOR_STATE = {
    "untriaged": None,  # the absence of every factory:* label IS untriaged
    "accepted": "factory:accepted",
    "deferred": "factory:deferred",
    "rejected": "factory:rejected",
    "in-progress": "factory:in-progress",
    "needs-human": "factory:needs-human",
    "done": "factory:done",
    "open": "factory:needs-review",
    "validating": "factory:validating",
    "passed": "factory:approved",
    "failed": "factory:needs-fix",
    "held": "factory:held",
    "merged": "factory:merged",
}

LABELS = [
    ("factory:accepted", "0e8a16", "Triaged and in scope. The dispatcher may build it."),
    ("factory:in-progress", "fbca04", "A workflow is on it now. Not a queue state."),
    ("factory:needs-review", "1d76db", "A PR waiting for the independent validator."),
    ("factory:validating", "5319e7", "A validation owns this PR right now."),
    ("factory:needs-fix", "d93f0b", "The validator asked for changes. Under the attempt cap."),
    ("factory:approved", "0e8a16", "Passed every structural gate."),
    ("factory:held", "fbca04", "Green, but waiting for a person to agree with a call the factory made. Not a failure."),
    ("factory:merged", "6f42c1", "Merged by factory/merge.py."),
    ("factory:done", "0e8a16", "The issue its PR closed is finished."),
    ("factory:needs-human", "b60205", "Stopped. The only state that should reach a person."),
    ("factory:rejected", "e4e669", "Out of scope. Closed not-planned, with the rule cited."),
    ("factory:deferred", "c5def5", "In scope, not now. NOT the same as rejected."),
    ("factory:rate-limited", "d4c5f9", "Flood protection. Re-evaluated after midnight UTC."),
    ("factory:stop", "000000", "EMERGENCY STOP. Any open issue with this halts all dispatch."),
    ("factory:from-regression", "bfd4f2", "Filed by the scheduled regression run."),
    ("priority:critical", "b60205", "Production broken, data loss, or a security hole."),
    ("priority:high", "d93f0b", "Core feature broken for most users."),
    ("priority:medium", "fbca04", "Non-core, or a new in-scope feature."),
    ("priority:low", "0e8a16", "Docs, typos, polish."),
]


class GhError(RuntimeError):
    pass


# Failures that mean "ask again", not "something is wrong". GitHub returns these
# routinely and they clear in seconds.
#
# THE INCIDENT: a single HTTP 503 on `gh issue list` took down one tick, wrote a
# needs-human entry and sent a notification. The very next tick, sixty seconds
# later, succeeded. So a thirty-second blip in somebody else's service produced a
# page and a permanent record that a human had to clear.
#
# That is the wrong threshold in the expensive direction. This runs unattended for
# days; a channel that fires on every upstream hiccup is a channel people mute, and
# a muted channel is the failure the escalation path exists to prevent.
#
# Deliberately NARROW. A 404, a 422, a bad token or a rejected merge are answers,
# and retrying an answer just asks the same question three times before reporting
# the same thing.
_TRANSIENT = (
    "503", "502", "504", "500",
    "service unavailable", "bad gateway", "gateway time-out", "timeout",
    "connection reset", "connection refused", "could not resolve host",
    "temporarily unavailable", "try again", "eof occurred", "tls handshake",
)


def _is_transient(stderr: str) -> bool:
    low = stderr.lower()
    return any(sig in low for sig in _TRANSIENT)


def gh(*args: str, check: bool = True, stdin: str | None = None,
       attempts: int = 3) -> str:
    last = None
    for attempt in range(1, attempts + 1):
        p = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            input=stdin,
            cwd=str(config.ROOT),
            timeout=120,
        )
        if p.returncode == 0 or not check:
            return p.stdout
        last = p
        if attempt == attempts or not _is_transient(p.stderr or ""):
            break
        # Said out loud rather than retried silently. A command that quietly takes
        # four seconds sometimes is a mystery later; one that says it retried is a
        # line in the log somebody can correlate with an upstream status page.
        print(
            f"GH_RETRY {attempt}/{attempts - 1} after a transient failure: "
            f"{(p.stderr or '').strip()[:160]}",
            file=sys.stderr, flush=True,
        )
        time.sleep(2 * attempt)
    raise GhError(
        f"gh {' '.join(args)} failed ({last.returncode}): {(last.stderr or '').strip()}"
    )


def parse_target(target: str) -> tuple[str, str]:
    """`gh:issue:12` -> ("issue", "12"). A target is self-describing on purpose."""
    parts = target.split(":")
    if len(parts) != 3 or parts[0] != "gh" or parts[1] not in ("issue", "pr"):
        raise SystemExit(
            f"BAD_TARGET: {target!r}. Expected gh:issue:<n> or gh:pr:<n>. Something "
            f"upstream passed a bare word -- usually an unquoted variable."
        )
    return parts[1], parts[2]


def _labels(item: dict) -> list[str]:
    return [lbl["name"] for lbl in item.get("labels", [])]


def _state_from_labels(kind: str, labels: list[str], closed: bool) -> str:
    for state, label in LABEL_FOR_STATE.items():
        if label and label in labels:
            if kind == "issue" and state in PR_STATES and state not in ISSUE_STATES:
                continue
            if kind == "pr" and state in ISSUE_STATES and state not in PR_STATES:
                continue
            return state
    if closed:
        # See TRANSITIONS["closed-unlabelled"]. A closed item with no disposition
        # is not untriaged and it is not done; it is a state something outside this
        # table put it in, and the factory's job is to record the reason.
        return "closed-unlabelled"
    return "untriaged" if kind == "issue" else "open"


def fetch(target: str) -> dict[str, Any]:
    kind, num = parse_target(target)
    if kind == "issue":
        fields = "number,title,body,labels,state,url,author,createdAt"
        raw = json.loads(gh("issue", "view", num, "--json", fields))
    else:
        # HOLDOUT: only the fields the validator needs. No comments, no reviews,
        # no commit messages -- the coder's chatter must not reach the judge even
        # by accident, and excluding it at the fetch layer is what makes that
        # structural rather than a sentence in a prompt.
        fields = (
            "number,title,body,labels,state,url,author,headRefName,baseRefName,"
            "additions,deletions,changedFiles,isDraft,mergeable,mergeStateStatus"
        )
        raw = json.loads(gh("pr", "view", num, "--json", fields))
    raw["_kind"] = kind
    raw["_target"] = target
    raw["_labels"] = _labels(raw)
    raw["_state"] = _state_from_labels(kind, raw["_labels"], raw.get("state") != "OPEN")
    raw["_priority"] = next(
        (p for p in config.PRIORITIES if f"priority:{p}" in raw["_labels"]), ""
    )
    raw["_attempts"] = sum(1 for lbl in raw["_labels"] if lbl.startswith("factory:attempt-"))
    return raw


class IllegalTransition(Exception):
    """A move the table forbids. Raised, never worked around."""


def set_state(target: str, new: str, force: bool = False) -> None:
    """Move an item, and REFUSE THE MOVE HERE rather than in a wrapper around it.

    The table used to be enforced only in this module's CLI, so the eleven callers
    that import `set_state` and call it directly -- the gate, the merge, the
    dispatcher -- were governed by nothing. The guarantee read as absolute in the
    docs and was, in fact, opt-in.

    Enforcing it at the write also closes a race the wrapper could not. The check
    ran against labels a node read at the START of its work; a validation once
    claimed a PR that had been escalated to needs-human three minutes earlier,
    because the label arrived after the read. `fetch()` below happens at the moment
    of the write, which is the latest anything can know.

    `force` exists for exactly one thing: parking at needs-human. Stopping is always
    allowed from anywhere, and an escalation that cannot label because of a table
    lookup is worse than any move it might have prevented.
    """
    kind, num = parse_target(target)
    verb = "issue" if kind == "issue" else "pr"
    current = fetch(target)

    old = current["_state"]
    if not force and old != new and new not in TRANSITIONS.get(old, set()):
        allowed = sorted(TRANSITIONS.get(old, set())) or ["(nothing -- a human must move this)"]
        extra = ""
        if old == "needs-human":
            extra = (
                " A node may never move an item out of needs-human (FACTORY_RULES 7); "
                "remove the label by hand."
            )
        raise IllegalTransition(
            f"{target} is '{old}'; '{new}' is not in {allowed}.{extra}"
        )

    add = LABEL_FOR_STATE.get(new)
    remove = [
        lbl
        for lbl in current["_labels"]
        if lbl.startswith(config.LABEL_PREFIX)
        and lbl != add
        and lbl in {v for v in LABEL_FOR_STATE.values() if v}
    ]
    args = ["edit", num]
    if add:
        args += ["--add-label", add]
    for lbl in remove:
        args += ["--remove-label", lbl]
    if add or remove:
        gh(verb, *args)

    # The disposition also decides whether the item is open. Doing it here, next to
    # the label write, is what stops "rejected" from meaning two different things
    # depending on which node got there first.
    if kind == "issue":
        if new == "rejected" and current.get("state") == "OPEN":
            gh("issue", "close", num, "--reason", "not planned", check=False)
        elif new == "done" and current.get("state") == "OPEN":
            # CLOSE IT HERE rather than trusting `Fixes #N` in the PR body.
            #
            # GitHub's linkage is prose parsing, and prose is what an agent writes.
            # One PR put the keyword inside backticks -- `Fixes #10` -- and GitHub
            # ignored it: the branch merged, the issue was labelled `factory:done`,
            # and it stayed OPEN. Nothing errored, and the label made the board read
            # as if it had been closed.
            #
            # A step that must happen should not depend on somebody else's markdown
            # parser agreeing with the formatting. `Fixes #N` stays in the body
            # because it makes the PR readable; it is no longer what does the work.
            gh("issue", "close", num, "--reason", "completed", check=False)
        elif new == "accepted" and current.get("state") != "OPEN":
            gh("issue", "reopen", num, check=False)


def set_priority(target: str, priority: str) -> None:
    if priority not in config.PRIORITIES:
        raise SystemExit(f"BAD_PRIORITY: {priority!r}, expected one of {config.PRIORITIES}")
    kind, num = parse_target(target)
    verb = "issue" if kind == "issue" else "pr"
    current = fetch(target)
    args = ["edit", num, "--add-label", f"priority:{priority}"]
    for lbl in current["_labels"]:
        if lbl.startswith("priority:") and lbl != f"priority:{priority}":
            args += ["--remove-label", lbl]
    gh(verb, *args)


def comment(target: str, body: str) -> None:
    """Every human-facing write goes through here, and this is the only way.

    A hand-rolled `gh issue comment` in a workflow node is the same class of
    mistake as a hand-rolled merge: it works until quoting, encoding or a newline
    eats it. A real run once posted a perfect two-rule rejection that reached the
    filer as the two characters `@-`, because the reasoning had been assembled in a
    shell pipeline. Every state transition was right, the `gh` call exited 0, the
    run reported success, and the only thing lost was the entire explanation.

    So: one process, one string, stdin -- and then READ IT BACK, because `exit 0`
    from the tool that posted it proves the API call succeeded, not that it carried
    anything. Empty-is-not-pass, applied to output rather than to checks.
    """
    kind, num = parse_target(target)
    verb = "issue" if kind == "issue" else "pr"
    body = body.strip()
    if not body:
        raise SystemExit("REFUSED: empty comment body. A verdict nobody can read is not a verdict.")
    gh(verb, "comment", num, "--body-file", "-", stdin=body)

    probe = body.strip().splitlines()[0][:60]
    recent = gh(verb, "view", num, "--json", "comments", check=False)
    if recent and probe and probe not in recent:
        print(
            f"COMMENT_UNVERIFIED: posted to {target} but could not read the first "
            f"line back. Check the item before trusting this run's audit trail.",
            file=sys.stderr,
        )
    else:
        print(f"COMMENT_OK {target}")


def bump_attempt(target: str) -> int:
    """Attempts are counted from append-only labels, so the count survives anything.

    A counter in a file dies with the worktree; a counter in a comment has to be
    parsed. `factory:attempt-1`, `factory:attempt-2` are both the count and the
    audit trail, and a fix that never bumped is visibly a fix that never bumped.
    """
    current = fetch(target)
    n = current["_attempts"] + 1
    kind, num = parse_target(target)
    label = f"factory:attempt-{n}"
    gh("label", "create", label, "--color", "ededed", "--description",
       f"Fix attempt {n} (FACTORY_RULES 8)", check=False)
    gh("pr" if kind == "pr" else "issue", "edit", num, "--add-label", label)
    return n


def stop_requested() -> tuple[bool, str]:
    """FAILS CLOSED. Any error reading the stop state counts as stopped.

    An unreadable stop button is a stop button you do not have.
    """
    if config.STOP_FILE.exists():
        reason = config.STOP_FILE.read_text(encoding="utf-8", errors="replace").strip()
        return True, f"{config.STOP_FILE} present" + (f": {reason.splitlines()[0]}" if reason else "")
    try:
        out = gh(
            "issue", "list", "--state", "open", "--label", config.STOP_LABEL,
            "--limit", "5", "--json", "number,title",
        )
        items = json.loads(out or "[]")
    except (GhError, json.JSONDecodeError) as e:
        return True, f"could not read the remote stop state ({e}); failing closed"
    if items:
        return True, f"issue #{items[0]['number']} carries {config.STOP_LABEL}"
    return False, "not stopped"


def _list(kind: str, state: str | None = None) -> list[dict]:
    verb = "issue" if kind == "issues" else "pr"
    fields = "number,title,labels,state,createdAt" + (",headRefName" if verb == "pr" else "")
    out = gh(verb, "list", "--state", "open", "--limit", "100", "--json", fields)
    items = json.loads(out or "[]")
    result = []
    for it in items:
        it["_kind"] = verb
        it["_target"] = f"gh:{verb}:{it['number']}"
        it["_labels"] = _labels(it)
        it["_state"] = _state_from_labels(verb, it["_labels"], False)
        it["_priority"] = next(
            (p for p in config.PRIORITIES if f"priority:{p}" in it["_labels"]), ""
        )
        it["_attempts"] = sum(1 for lbl in it["_labels"] if lbl.startswith("factory:attempt-"))
        if state is None or it["_state"] == state:
            result.append(it)
    return result


def next_action(exclude: set[str] | None = None) -> tuple[str, str, str]:
    """The dispatcher's one question, answered from data. Returns (action, target, why).

    PRIORITY ORDER IS LOAD-BEARING: finish in-flight work before starting new work.
    Reversed, the factory triages forever while its own PRs rot, and throughput
    looks busy while going to zero.
    """
    exclude = exclude or set()
    prs = [p for p in _list("prs") if p["_target"] not in exclude]

    failed = [p for p in prs if p["_state"] == "failed"]
    under_cap = [p for p in failed if p["_attempts"] < config.MAX_FIX_ATTEMPTS]
    if under_cap:
        p = under_cap[0]
        return "fix", p["_target"], f"attempt {p['_attempts'] + 1}/{config.MAX_FIX_ATTEMPTS}"

    capped = [p for p in failed if p["_attempts"] >= config.MAX_FIX_ATTEMPTS]
    if capped:
        return "escalate", capped[0]["_target"], "fix-attempt cap reached (FACTORY_RULES 8)"

    open_prs = sorted([p for p in prs if p["_state"] == "open"], key=lambda p: p["createdAt"])
    if open_prs:
        return "validate", open_prs[0]["_target"], "oldest PR awaiting the independent validator"

    passed = [p for p in prs if p["_state"] == "passed"]
    if passed:
        return "merge", passed[0]["_target"], "every structural gate green"

    issues = [i for i in _list("issues") if i["_target"] not in exclude]

    # AN ISSUE A LIVE PR ALREADY ANSWERS IS NOT WORK. This branch selects on the
    # issue's label alone, and `accepted` is reachable while a pull request for that
    # issue is open -- a human accepting an issue somebody already built, or an issue
    # walked back from `in-progress` after its PR was opened. The dispatcher would
    # then open a SECOND branch for the same issue, and both would try to merge.
    #
    # Seen: PR #13 was held on ratchet slack, and the very next tick answered
    # `implement gh:issue:12` -- the issue that PR was for. Nothing stopped it except
    # a lock that happened to still be held by the validation, which is luck rather
    # than a mechanism.
    #
    # The reconcile sweep already asks this exact question about `in-progress`
    # issues. Asking it here too is what makes the two agree.
    answered = set()
    for pr_ in prs:
        if pr_["_state"] in ("merged", "rejected"):
            continue
        try:
            owner = linked_issue(pr_["_target"])
        except Exception:  # noqa: BLE001
            continue
        if owner:
            answered.add(owner)
    issues = [i for i in issues if i["_target"] not in answered]

    for prio in config.PRIORITIES:
        ready = [i for i in issues if i["_state"] == "accepted" and i["_priority"] == prio]
        if ready:
            return "implement", ready[0]["_target"], f"highest-priority accepted issue ({prio})"
    ready = [i for i in issues if i["_state"] == "accepted"]
    if ready:
        return "implement", ready[0]["_target"], "accepted issue with no priority label"

    untriaged = [
        i for i in issues
        if i["_state"] == "untriaged" and "factory:rate-limited" not in i["_labels"]
    ]
    if untriaged:
        return "triage", untriaged[0]["_target"], f"{len(untriaged)} untriaged"

    # LAST, and only when there is genuinely nothing else to do. A PR sits in
    # `validating` for as long as a validation is running, which is normal and is
    # not this. What this catches is the run that never came back -- a killed
    # process, a reboot, a crash between the tripwire and the verdict -- because
    # `validating` is the one live state no earlier branch looks at, so such a PR
    # would otherwise be reported as idle and never mentioned again.
    #
    # Reported rather than acted on: only the dispatcher holds the runtime lock and
    # can tell "still running" from "died".
    stalled = [p for p in prs if p["_state"] == "validating"]
    if stalled:
        return "stalled-pr", stalled[0]["_target"], "in 'validating'"

    referenced = set()
    for p in prs:
        try:
            linked = linked_issue(p["_target"])
            if linked:
                referenced.add(linked)
        except GhError:
            pass
    orphans = [
        i for i in issues if i["_state"] == "in-progress" and i["_target"] not in referenced
    ]
    if orphans:
        return "stalled-issue", orphans[0]["_target"], "in 'in-progress' with no PR"

    return "idle", "-", "nothing to do"


def linked_issue(pr_target: str) -> str | None:
    """`Fixes #N` in the PR body. A PR without it cannot be validated."""
    import re
    import runtime

    link = runtime.root() / "links" / f"{pr_target.replace(':', '-')}.json"
    if link.exists():
        issue = runtime.read(link)["issue"]
        if parse_target(issue)[0] != "issue":
            raise ValueError("Recorded PR linkage is not an issue")
        return issue

    pr = fetch(pr_target)
    m = re.search(r"(?:fixes|closes|resolves)\s+#(\d+)", pr.get("body") or "", re.I)
    return f"gh:issue:{m.group(1)}" if m else None


def body_text(target: str) -> str:
    """The item rendered as text a node can open.

    Rendered ONCE per run, before any node executes, so every node judges the same
    text. Re-fetching per node would let a mid-run edit change what the judge
    thinks was asked for.
    """
    item = fetch(target)
    lines = [
        f"# {item.get('title', '(no title)')}",
        "",
        f"- target: {target}",
        f"- url: {item.get('url', '')}",
        f"- author: {(item.get('author') or {}).get('login', 'unknown')}",
        f"- labels: {', '.join(item['_labels']) or '(none)'}",
        f"- state: {item['_state']}",
        "",
        "---",
        "",
        (item.get("body") or "(no body)").strip(),
    ]
    return "\n".join(lines) + "\n"


def init_labels(check_only: bool = False) -> int:
    """Create the label vocabulary. Run once, before the first lap.

    THESE LABELS ARE THE STATE MACHINE. A missing one is not cosmetic: the
    dispatcher silently cannot see work in that state, and a factory that cannot
    see work looks exactly like a factory with nothing to do.

    Asserted rather than remembered: every label LABEL_FOR_STATE can write must
    appear in the table below, so adding a state and forgetting its label is a
    loud failure here instead of a silent one at the first green gate.
    """
    known = {name for name, _, _ in LABELS}
    needed = {v for v in LABEL_FOR_STATE.values() if v} | {config.STOP_LABEL}
    missing_from_table = sorted(needed - known)
    if missing_from_table:
        print(
            "LABEL_TABLE_INCOMPLETE: " + " ".join(missing_from_table) + "\n"
            "  state.py writes those labels and this table does not create them. The "
            "factory would fail the moment it first tried to set that state.",
            file=sys.stderr,
        )
        return 2

    try:
        existing = {
            lbl["name"] for lbl in json.loads(gh("label", "list", "--limit", "300", "--json", "name"))
        }
    except (GhError, json.JSONDecodeError) as e:
        print(f"LABELS_UNKNOWN: {e}", file=sys.stderr)
        return 2

    missing = [(n, c, d) for n, c, d in LABELS if n not in existing]
    for name, colour, desc in LABELS:
        if name in existing:
            print(f"  ok       {name}")
    for name, colour, desc in missing:
        if check_only:
            print(f"  MISSING  {name}")
            continue
        gh("label", "create", name, "--color", colour, "--description", desc, check=False)
        print(f"  created  {name}")
    if check_only and missing:
        return 1
    return 0


# --- CLI ----------------------------------------------------------------------


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd, rest = argv[0], argv[1:]

    try:
        if cmd == "next":
            exclude = set()
            if "--exclude" in rest:
                exclude = {t.strip() for t in rest[rest.index("--exclude") + 1].split(",") if t.strip()}
            action, target, why = next_action(exclude)
            print(f"{action}\t{target}\t{why}")
            return 0

        if cmd == "get":
            item = fetch(rest[0])
            for k in ("_target", "_kind", "_state", "_priority", "_attempts"):
                print(f"{k.lstrip('_')}={item[k]}")
            print(f"title={item.get('title', '')}")
            print(f"branch={item.get('headRefName', '')}")
            print(f"url={item.get('url', '')}")
            issue = linked_issue(rest[0]) if item["_kind"] == "pr" else ""
            print(f"issue={issue or ''}")
            return 0

        if cmd == "set":
            target = rest[0]
            for pair in rest[1:]:
                if "=" not in pair:
                    raise SystemExit(
                        f"BAD_ARGUMENT: expected key=value, got {pair!r}. Something "
                        f"upstream passed a bare word -- usually an unquoted variable."
                    )
                key, value = pair.split("=", 1)
                if key == "state":
                    # The check lives in set_state, so this path and the ten callers
                    # that import it get the same answer. Re-applying the state an
                    # item is already in is deliberate and allowed: the labels ARE
                    # the state, so a correct state carrying no label is a state a
                    # human cannot read.
                    try:
                        set_state(target, value)
                    except IllegalTransition as e:
                        print(f"ILLEGAL_TRANSITION: {e}", file=sys.stderr)
                        return 1
                elif key == "priority":
                    set_priority(target, value)
                else:
                    print(f"NOT_STORED: unknown key '{key}'", file=sys.stderr)
                    return 1
            print(f"OK {target} " + " ".join(rest[1:]))
            return 0

        if cmd == "body":
            sys.stdout.write(body_text(rest[0]))
            return 0

        if cmd == "comment":
            comment(rest[0], sys.stdin.read())
            return 0

        if cmd == "list":
            kind = rest[0] if rest else "issues"
            want = rest[rest.index("--state") + 1] if "--state" in rest else None
            for it in _list(kind, want):
                print(f"{it['_target']}\t{it['_state']}\t{it['_priority'] or '-'}\t{it['title']}")
            return 0

        if cmd == "bump-attempt":
            print(f"ATTEMPTS={bump_attempt(rest[0])}")
            return 0

        if cmd == "stop-requested":
            stopped, why = stop_requested()
            print(f"STOP={'1' if stopped else '0'} {why}")
            return 1 if stopped else 0

        if cmd == "linked-issue":
            print(linked_issue(rest[0]) or "")
            return 0

        if cmd == "init-labels":
            return init_labels(check_only="--check" in rest)

    except GhError as e:
        print(f"GH_ERROR: {e}", file=sys.stderr)
        return 4

    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
