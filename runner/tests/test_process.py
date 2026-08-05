"""Process-safety tests for the private runner's safe execution foundation."""

import os
import tempfile
import unittest

from joeos_runner.process import ProcessExecutionError, canonicalize_path, run_process


class ProcessSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = self.tempdir.name

    def tearDown(self):
        self.tempdir.cleanup()

    def test_shell_is_never_used(self):
        with self.assertRaises(ProcessExecutionError):
            run_process(executable="echo", arguments=["x; rm -rf /"], cwd=self.root)

    def test_allowlisted_executable_only(self):
        with self.assertRaises(ProcessExecutionError):
            run_process(executable="/bin/bash", arguments=["-c", "true"], cwd=self.root)

    def test_path_traversal_rejected(self):
        with self.assertRaises(ProcessExecutionError):
            canonicalize_path(self.root, "../outside")
        with self.assertRaises(ProcessExecutionError):
            canonicalize_path(self.root, "sub/../../escape")

    def test_bounded_output(self):
        result = run_process(
            executable="python3", arguments=["-c", "print('x' * 500000)"],
            cwd=self.root, max_output_bytes=2048,
        )
        self.assertLessEqual(len(result.stdout), 2048)

    def test_timeout_terminates_process_group(self):
        result = run_process(
            executable="python3", arguments=["-c", "import time\ntime.sleep(30)"],
            cwd=self.root, timeout_ms=250,
        )
        self.assertTrue(result.timed_out or result.cancelled)

    def test_redaction(self):
        result = run_process(
            executable="python3", arguments=["-c", "print('secret-value-999')"],
            cwd=self.root,
        ).redacted(["secret-value-999"])
        self.assertNotIn("secret-value-999", result.stdout)
        self.assertIn("[REDACTED]", result.stdout)


if __name__ == "__main__":
    unittest.main()
