"""The holdout fence refuses what would show a builder a scenario, and nothing else."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FENCE = ROOT / "template/.factory/hooks/holdout_fence.py"
SCENARIO = "## H-01 Removed member loses every surface on the very next request\nSetup: alice owns workspace W with a note titled zanzibar layoffs plan and bob is a viewer in W.\n"


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


class FenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@t")
        git(self.repo, "config", "user.name", "t")
        (self.repo / "src").mkdir()
        (self.repo / "src/app.py").write_text("print('hi')\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "app")
        (self.repo / ".factory/holdout").mkdir(parents=True)
        (self.repo / ".factory/holdout/HOLDOUT.md").write_text(SCENARIO)
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "holdouts")
        self.holdout_commit = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"],
                                             capture_output=True, text=True).stdout.strip()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def hook(self, tool: str, tool_input: dict, agent: str | None = None, mode: str = "pre",
             response=None) -> int:
        event = {"tool_name": tool, "tool_input": tool_input, "cwd": str(self.repo), "session_id": "s"}
        if agent:
            event["agent_type"] = agent
        if response is not None:
            event["tool_response"] = response
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(self.repo)}
        return subprocess.run([sys.executable, str(FENCE), mode], input=json.dumps(event), text=True,
                              capture_output=True, env=env).returncode

    def bash(self, command: str, agent: str | None = None) -> int:
        return self.hook("Bash", {"command": command}, agent)

    def test_naming_the_folder_is_refused_for_builders_only(self) -> None:
        path = str(self.repo / ".factory/holdout/HOLDOUT.md")
        self.assertEqual(self.hook("Read", {"file_path": path}), 2)
        self.assertEqual(self.hook("Read", {"file_path": path.replace("/", "\\")}), 2)
        self.assertEqual(self.hook("Read", {"file_path": path}, agent="holdout-verifier"), 0)
        self.assertEqual(self.bash("cat .factory/holdout/HOLDOUT.md"), 2)
        self.assertEqual(self.bash("cat .factory/*/*"), 2)
        self.assertEqual(self.hook("Agent", {"subagent_type": "general-purpose", "prompt": "read .factory/holdout"}), 2)
        self.assertEqual(self.hook("Agent", {"subagent_type": "holdout-verifier", "prompt": "run .factory/holdout"}), 0)

    def test_prose_and_output_tools_pass(self) -> None:
        self.assertEqual(self.bash('gh pr comment 3 --body "the holdouts passed on main"'), 0)
        self.assertEqual(self.hook("StructuredOutput", {"summary": "ran .factory/holdout scenarios"}), 0)
        self.assertEqual(self.hook("Read", {"file_path": str(self.repo / "src/app.py")}), 0)

    def test_recursive_searches_that_cover_the_folder(self) -> None:
        self.assertEqual(self.bash('grep -rn "zanzibar" .'), 2)
        self.assertEqual(self.bash('grep -rn "zanzibar"'), 2)
        self.assertEqual(self.bash(f'cd "{self.repo}" && grep -Rl glass .'), 2)
        self.assertEqual(self.bash("git grep zanzibar"), 2)
        self.assertEqual(self.bash("rg -uu zanzibar"), 2)
        self.assertEqual(self.bash('grep -rn "zanzibar" src'), 0)
        self.assertEqual(self.bash('grep -rn --include "*.py" foo src/'), 0)
        self.assertEqual(self.bash('grep -rn foo --exclude-dir=.factory .'), 0)
        self.assertEqual(self.bash("git grep foo -- src"), 0)
        self.assertEqual(self.bash("rg zanzibar"), 0)
        self.assertEqual(self.bash("grep -n foo src/app.py"), 0)

    def test_git_history_is_refused_only_when_it_prints_a_holdout_file(self) -> None:
        self.assertEqual(self.bash(f"git show {self.holdout_commit}"), 2)
        self.assertEqual(self.bash("git log -p -3"), 2)
        self.assertEqual(self.bash("git diff HEAD~1 HEAD"), 2)
        self.assertEqual(self.bash("git show HEAD~1"), 0)
        self.assertEqual(self.bash("git log --oneline -5"), 0)
        self.assertEqual(self.bash("git show --stat HEAD"), 0)
        self.assertEqual(self.bash("git diff HEAD~1 HEAD -- src"), 0)
        self.assertEqual(self.bash("git diff"), 0)

    def test_the_fence_itself_only_changes_by_hand(self) -> None:
        self.assertEqual(self.hook("Edit", {"file_path": str(self.repo / ".claude/settings.json")}), 2)
        self.assertEqual(self.hook("Write", {"file_path": str(self.repo / ".factory/hooks/holdout_fence.py")},
                                   agent="holdout-writer"), 2)
        self.assertEqual(self.bash("rm .ignore"), 2)
        self.assertEqual(self.bash("cat .claude/settings.json"), 0)
        self.assertEqual(self.hook("Write", {"file_path": str(self.repo / "README.md"), "content": "see .ignore"}), 0)

    def test_an_error_refuses(self) -> None:
        result = subprocess.run([sys.executable, str(FENCE), "pre"], input="not json", text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)

    def test_scenario_text_in_a_result_is_recorded(self) -> None:
        leaked = {"stdout": "Setup: alice owns workspace W with a note titled zanzibar layoffs plan and bob is a viewer in W."}
        self.assertEqual(self.hook("Bash", {"command": "x"}, mode="post", response=leaked), 0)
        self.assertEqual(self.hook("Bash", {"command": "x"}, mode="post", response={"stdout": "ok"}), 0)
        log = self.repo / ".git/holdout-exposures.jsonl"
        records = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual([record["scenarios"] for record in records], [["H-01"]])


if __name__ == "__main__":
    unittest.main()
