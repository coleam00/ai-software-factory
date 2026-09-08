"""Exercise post-merge bookkeeping in a real single-branch clone, without GitHub."""
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "template" / "factory"))
import config
import merge


def git(cwd, *args):
    return subprocess.check_output(["git", *args], cwd=cwd, stderr=subprocess.PIPE,
                                   text=True).strip()


with tempfile.TemporaryDirectory(prefix="factory-merge-fixture-") as temporary:
    root = Path(temporary)
    origin, seed, operator = (root / name for name in ("origin.git", "seed", "operator"))
    git(root, "init", "--bare", str(origin))
    git(root, "init", "-b", "develop", str(seed))
    git(seed, "config", "user.name", "Fixture")
    git(seed, "config", "user.email", "fixture@example.invalid")
    (seed / "value.txt").write_text("before\n")
    git(seed, "add", "value.txt")
    git(seed, "commit", "-m", "Initial fixture")
    git(seed, "remote", "add", "origin", str(origin))
    git(seed, "push", "origin", "develop")
    git(root, "clone", "--single-branch", "--branch", "develop", str(origin), str(operator))
    git(seed, "switch", "-c", "integration")
    git(seed, "push", "origin", "integration")
    git(operator, "fetch", "origin", "refs/heads/integration:refs/remotes/origin/integration")
    git(operator, "switch", "-c", "integration", "origin/integration")
    before = git(operator, "rev-parse", "HEAD")
    (seed / "value.txt").write_text("after\n")
    git(seed, "commit", "-am", "Accepted tree")
    accepted = git(seed, "rev-parse", "HEAD")
    git(seed, "push", "origin", "integration")
    assert "develop" in git(operator, "config", "--get-all", "remote.origin.fetch")
    config.ROOT = config.SHARED = operator
    config.BASE_BRANCH = "integration"
    (operator / "value.txt").write_text("operator work\n")
    try:
        merge.bookkeeping({"head_sha": accepted}, {"counts": {}})
        raise AssertionError("A dirty checkout must refuse bookkeeping")
    except RuntimeError as error:
        assert "uncommitted" in str(error)
    assert git(operator, "rev-parse", "HEAD") == before
    assert (operator / "value.txt").read_text() == "operator work\n"
    git(operator, "restore", "value.txt")
    merge.bookkeeping({"head_sha": accepted}, {"counts": {}})
    assert git(operator, "rev-parse", "HEAD") == accepted
    assert git(operator, "rev-parse", "origin/integration") == accepted
    assert (operator / "value.txt").read_text() == "after\n"
    merge.bookkeeping({"head_sha": accepted}, {"counts": {}})
    assert git(operator, "rev-parse", "HEAD") == accepted
print("MERGE_BOOKKEEPING_PASSED checks=8")
