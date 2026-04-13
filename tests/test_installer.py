import subprocess
import unittest
from unittest.mock import patch

from theme_manager.installer import install_from_package


class TestPackageInstall(unittest.TestCase):
    def test_rejects_empty_package_name(self):
        self.assertFalse(install_from_package("", "apt"))

    def test_installs_with_apt(self):
        ok_result = subprocess.CompletedProcess(
            args=["pkexec", "apt-get", "install", "-y", "--", "adwaita-icon-theme"],
            returncode=0,
        )
        with patch("theme_manager.installer.shutil.which", side_effect=lambda c: "/usr/bin/apt-get" if c == "apt-get" else None):
            with patch("theme_manager.installer.subprocess.run", return_value=ok_result) as run_mock:
                self.assertTrue(install_from_package("adwaita-icon-theme", "apt"))
                run_mock.assert_called_once()
                self.assertIn("--", run_mock.call_args.args[0])

    def test_installs_with_pacman(self):
        ok_result = subprocess.CompletedProcess(
            args=["pkexec", "pacman", "-S", "--noconfirm", "--", "adwaita"],
            returncode=0,
        )
        with patch("theme_manager.installer.shutil.which", side_effect=lambda c: "/usr/bin/pacman" if c == "pacman" else None):
            with patch("theme_manager.installer.subprocess.run", return_value=ok_result) as run_mock:
                self.assertTrue(install_from_package("adwaita", "pacman"))
                self.assertIn("--", run_mock.call_args.args[0])

    def test_rejects_option_like_package_name(self):
        self.assertFalse(install_from_package("--allow-downgrades", "apt"))

    def test_rejects_unknown_package_manager(self):
        self.assertFalse(install_from_package("adwaita", "yum"))


if __name__ == "__main__":
    unittest.main()
