"""Load the findings, check the cap, check out the branch.

THE FINDINGS ARE ASSERTED, NOT ASSUMED. The gate writes them to a shared path when it
records `request_changes`, keyed by target so two PRs in flight cannot read each
other's. If they are missing, this run REFUSES: a fix node with nothing to read
re-reads the diff and invents an objection, which is how you get a confident commit
that addresses nothing and burns one of two attempts. Missing findings mean the gate
never recorded them, which is a machinery fault and belongs in front of a human.

THE PATH MATTERS. The findings live under the MAIN checkout, not this worktree --
they were written by a different run, in a different worktree, that has since been
deleted. That is exactly the state that dies if you resolve it relative to wherever
the current process happens to be standing.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "factory"))

# nodeio FIRST, before anything that might print at import time: importing it
# redirects stdout to stderr for the whole process, so a library marker can never
# be mistaken for this node's value. emit() writes to the real stdout.
from nodeio import emit, note  # noqa: E402

import config  # noqa: E402
import state  # noqa: E402

target = (os.environ.get("INPUTS_TARGET") or "").strip()
artifacts = Path(os.environ.get("ARTIFACTS_DIR") or ".")
artifacts.mkdir(parents=True, exist_ok=True)


def die(msg: str) -> None:
    note(f"PREPARE_FIX_FAILED: {msg}")
    sys.exit(1)


def git(*args: str) -> tuple[int, str]:
    p = subprocess.run(
        ["git", *args], cwd=str(config.ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=600,
    )
    return p.returncode, (p.stdout + p.stderr).strip()


pr = state.fetch(target)
number = target.split(":")[-1]
branch = pr.get("headRefName") or ""
if not branch:
    die(f"{target} has no head branch")

# The fix node is a builder too, so the same wall goes up here before it runs.
import holdoutwall  # noqa: E402

try:
    note(holdoutwall.wall())
except RuntimeError as e:
    die(f"could not wall off the holdout in this worktree: {e}")

if pr["_state"] == "needs-human":
    die(f"{target} is parked at needs-human; a human has to remove the label first")
if pr["_state"] != "failed":
    die(
        f"{target} is '{pr['_state']}', expected 'failed'. Only a PR the validator sent "
        f"back has findings to fix; anything else would be fixing a complaint nobody made."
    )

attempts = pr["_attempts"]
if attempts >= config.MAX_FIX_ATTEMPTS:
    die(
        f"{target} has had {attempts} fix attempts and the cap is "
        f"{config.MAX_FIX_ATTEMPTS} (FACTORY_RULES §8). Without a cap a PR ping-pongs "
        f"until the budget is gone. This one needs a human."
    )
attempt = attempts + 1

# --- the findings --------------------------------------------------------------
key = re.sub(r"[/.:\\]", "-", target)
findings_json = config.FINDINGS_DIR / f"{key}.json"
findings_log = config.FINDINGS_DIR / f"{key}.gate.log"

if not findings_json.exists() or not findings_json.stat().st_size:
    die(
        f"no validator findings at {findings_json}. The gate records them when it "
        f"returns request_changes, so this PR reached the fix loop without a recorded "
        f"objection. A fix node with nothing to read would invent one."
    )

try:
    verdict = json.loads(findings_json.read_text(encoding="utf-8"))
except json.JSONDecodeError as e:
    die(f"the recorded findings are not readable JSON ({e})")

issues = verdict.get("issues_to_fix") or []
if not issues:
    die(
        "the recorded verdict lists no findings. There is nothing to fix, and a fix "
        "node asked to fix nothing will change something anyway."
    )

# Render them for the prompt, highest severity first -- a fix node works top-down and
# the ordering is the only prioritisation it gets.
order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
issues.sort(key=lambda f: order.get(f.get("severity", "low"), 9))
rendered = [f"## What the validator objected to\n"]
rendered.append(f"**Its summary:** {verdict.get('summary', '(none)')}\n")
for i, f in enumerate(issues, 1):
    loc = f.get("file", "")
    if f.get("line"):
        loc = f"{loc}:{f['line']}"
    rendered.append(
        f"{i}. **{f.get('severity', '?').upper()}** [{f.get('category', '?')}]"
        + (f" `{loc}`" if loc else "")
        + f"\n   {f.get('description', '')}\n"
    )
if verdict.get("rules_cited"):
    rendered.append(f"\n_Rules cited: {', '.join(verdict['rules_cited'])}_\n")

(artifacts / "findings.md").write_text("\n".join(rendered), encoding="utf-8")

if findings_log.exists():
    # The log says what the check actually PRINTED; the verdict says what the judge
    # made of it. When a finding names a check, the log is the ground truth.
    (artifacts / "gate.log").write_text(
        findings_log.read_text(encoding="utf-8", errors="replace")[-60_000:], encoding="utf-8"
    )

# --- the issue, so the fix stays inside the original ask -----------------------
issue = state.linked_issue(target)
if issue:
    (artifacts / "issue.md").write_text(state.body_text(issue), encoding="utf-8")

for name in ("MISSION.md", "FACTORY_RULES.md", "CLAUDE.md", "AGENTS.md"):
    src = config.ROOT / name
    if src.exists():
        (artifacts / name).write_text(
            src.read_text(encoding="utf-8", errors="replace"), encoding="utf-8"
        )

# --- the branch ----------------------------------------------------------------
git("fetch", "--quiet", "origin", "main")
base = "origin/main" if git("rev-parse", "--verify", "--quiet", "origin/main")[0] == 0 else "main"
git("fetch", "--quiet", "origin", f"{branch}:refs/remotes/origin/{branch}")
checkout = f"origin/{branch}" if git("rev-parse", "--verify", "--quiet", f"origin/{branch}")[0] == 0 else branch
# DETACHED, for the reason documented in validate/scripts/prepare.py: git refuses to
# have one branch checked out in two worktrees at once, and a sibling run's worktree
# may legitimately still hold this one. Checking out by name fails, and the failure is
# silent unless the return code is read -- which is how a run once printed REBASED and
# had not rebased. Nothing here needs a branch name; land-fix pushes HEAD:<branch>.
rc, out = git("checkout", "-q", "--detach", checkout)
if rc != 0:
    die(f"could not check out {checkout}: {out}")
rc, head = git("rev-parse", "--short", "HEAD")
note(f"CHECKED_OUT {branch} at {head} (detached)")

note(f"FIX_PREPARED {target} attempt {attempt}/{config.MAX_FIX_ATTEMPTS} on {branch}")
note(f"FINDINGS {len(issues)} from {findings_json}")
emit(
        {
            "target": target,
            "number": number,
            "branch": branch,
            "base": base,
            "attempt": attempt,
        }
)
