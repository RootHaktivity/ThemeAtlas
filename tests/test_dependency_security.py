import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from theme_manager import dependencies
from theme_manager.extractor import _batch_install_tools


class TestInstallStepSafety(unittest.TestCase):
    def test_run_install_steps_non_root_uses_argv_not_shell(self):
        calls = []

        def fake_run(cmd, timeout, check):
            calls.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0)

        with patch("theme_manager.dependencies.os.getuid", return_value=1000), patch(
            "theme_manager.dependencies.shutil.which",
            side_effect=lambda c: "/usr/bin/pkexec" if c == "pkexec" else None,
        ), patch("theme_manager.dependencies.subprocess.run", side_effect=fake_run):
            ok = dependencies._run_install_steps(
                [
                    ["apt-get", "update"],
                    ["apt-get", "install", "-y", "--", "meson"],
                ]
            )

        self.assertTrue(ok)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], ["pkexec", "apt-get", "update"])
        self.assertEqual(calls[1], ["pkexec", "apt-get", "install", "-y", "--", "meson"])
        self.assertNotIn("bash", calls[0])
        self.assertNotIn("bash", calls[1])


class TestBatchInstallSafety(unittest.TestCase):
    def test_batch_install_rejects_unsafe_package_like_tool_name(self):
        with patch("theme_manager.extractor.detect_environment", return_value=SimpleNamespace(package_manager="apt")), patch(
            "theme_manager.extractor.shutil.which", return_value=None
        ), patch("theme_manager.extractor._run_install_steps") as install_steps:
            result = _batch_install_tools([("-bad", None)])

        install_steps.assert_not_called()
        self.assertFalse(result["-bad"])

    def test_batch_install_uses_package_separator(self):
        installed = set()
        step_calls = []

        def fake_which(cmd):
            return f"/usr/bin/{cmd}" if cmd in installed else None

        def fake_install(steps):
            step_calls.append(steps)
            for step in steps:
                if "install" in step and "--" in step:
                    idx = step.index("--")
                    for pkg in step[idx + 1 :]:
                        installed.add(pkg)
            return True

        with patch("theme_manager.extractor.detect_environment", return_value=SimpleNamespace(package_manager="apt")), patch(
            "theme_manager.extractor.shutil.which", side_effect=fake_which
        ), patch("theme_manager.extractor._run_install_steps", side_effect=fake_install):
            result = _batch_install_tools([("meson", None), ("ninja-build", "ninja")])

        self.assertTrue(result["meson"])
        self.assertTrue(result["ninja-build"])
        self.assertTrue(step_calls)
        apt_install_step = next(step for call in step_calls for step in call if step[:3] == ["apt-get", "install", "-y"])
        self.assertIn("--", apt_install_step)


if __name__ == "__main__":
    unittest.main()
