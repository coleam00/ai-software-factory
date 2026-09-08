"""Check legacy settings diagnostics and preservation during retirement."""
import importlib.util
import contextlib
import io
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("sync_settings_under_test", HERE / "sync-to.py")
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)
checks = 0


def check(condition, explanation):
    global checks
    checks += 1
    assert condition, explanation


with tempfile.TemporaryDirectory(prefix="factory-sync-settings-") as temporary:
    root = Path(temporary)
    (root / "factory").mkdir()
    config = root / "factory/config.py"
    current = (sync.TEMPLATE / "factory/config.py").read_text(encoding="utf-8")
    old = current.replace('"archon-admit"', '"factory-triage"').replace('"archon-ship"', '"factory-implement"')
    old = old.replace('"archon-accept"', '"factory-validate"').replace('"archon-revise-pr"', '"factory-fix"')
    old = old.replace('"archon-regress"', '"factory-regress"')
    config.write_text(old, encoding="utf-8")
    original = config.read_bytes()
    changes = dict(sync.missing_settings(root))
    for name in ("TRIAGE", "IMPLEMENT", "VALIDATE", "FIX", "REGRESS"):
        check("WORKFLOW_" + name in changes, "retired workflow default must be reported")
    check(config.read_bytes() == original, "diagnostics must preserve the user's config")
    config.write_text(old.replace('"factory-triage"', '"my-team-admission"'), encoding="utf-8")
    check("WORKFLOW_TRIAGE" not in dict(sync.missing_settings(root)), "custom workflow selection is preserved")

    # Exercise the actual copy decision with a known historical template version.
    skill_rel = ".claude/skills/factory-triage/SKILL.md"
    skill = root / skill_rel
    skill.parent.mkdir(parents=True)
    old_skill = "Historical template instructions pointing at the retired triage prompt.\n"
    skill.write_text(old_skill, encoding="utf-8")
    sync.SYNC = [skill_rel]
    sync.shipped_versions = lambda path: {old_skill}
    with contextlib.redirect_stdout(io.StringIO()):
        sync.main([str(root)])
    check(sync.same_content(sync.TEMPLATE / skill_rel, skill), "unchanged historical default upgrades")
    skill.write_text("My team's customized admission instructions.\n", encoding="utf-8")
    custom_skill = skill.read_bytes()
    with contextlib.redirect_stdout(io.StringIO()):
        sync.main([str(root)])
    check(skill.read_bytes() == custom_skill, "customized skill remains byte-for-byte intact")
    sync.shipped_versions = lambda path: set()
    skill.write_text(old_skill, encoding="utf-8")
    unknown_skill = skill.read_bytes()
    with contextlib.redirect_stdout(io.StringIO()):
        sync.main([str(root)])
    check(skill.read_bytes() == unknown_skill, "unavailable history preserves even a possible default")

    # An edited retired file can return on a later sync; never overwrite the earlier backup.
    owned = root / ".archon/workflows/factory/triage/custom.md"
    owned.parent.mkdir(parents=True)
    owned.write_text("first custom prompt", encoding="utf-8")
    sync.shipped_versions = lambda path: set()
    sync.retire(root, False)
    backup = root / sync.RETIRED_BACKUP / ".archon/workflows/factory/triage/custom.md"
    check(backup.read_text() == "first custom prompt", "first customization backed up")
    owned.parent.mkdir(parents=True)
    owned.write_text("second custom prompt", encoding="utf-8")
    sync.retire(root, False)
    check(backup.read_text() == "first custom prompt", "earlier backup remains intact")
    check(backup.with_name("custom.md.1").read_text() == "second custom prompt", "later backup is separate")

print(f"SYNC_SETTINGS_PASSED checks={checks}")
