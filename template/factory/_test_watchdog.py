"""The retired watchdog must never infer engine lifecycle or invoke a stage."""
import contextlib
import io
import unittest
from unittest.mock import patch
import watchdog

class WatchdogRetirementTests(unittest.TestCase):
    def test_inert_with_any_arguments(self):
        with patch("subprocess.run", side_effect=AssertionError("unexpected process")), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(watchdog.main(["--replay"]), 2)

if __name__ == "__main__":
    unittest.main()
