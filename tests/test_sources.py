import subprocess
import unittest
from unittest.mock import patch

from theme_manager.gui.sources import AptSource, get_sources


class TestSources(unittest.TestCase):
    def test_builtin_source_registry_includes_apt_when_available(self):
        def fake_which(cmd):
            return "/usr/bin/apt-cache" if cmd == "apt-cache" else None

        with patch("theme_manager.gui.sources.shutil.which", side_effect=fake_which):
            names = [s.name for s in get_sources()]
        self.assertIn("apt", names)
        self.assertNotIn("pacman", names)

    def test_apt_source_parses_theme_packages(self):
        sample = (
            "adwaita-icon-theme - default icon theme of GNOME\n"
            "dmz-cursor-theme - DMZ cursor theme\n"
            "curl - command line tool\n"
        )
        run_result = subprocess.CompletedProcess(
            args=["apt-cache", "search", "theme"],
            returncode=0,
            stdout=sample,
            stderr="",
        )

        with patch("theme_manager.gui.sources.shutil.which", side_effect=lambda c: "/usr/bin/apt-cache" if c == "apt-cache" else None):
            with patch("theme_manager.gui.sources.subprocess.run", return_value=run_result):
                records = AptSource().search("theme", "all", 1)

        self.assertGreaterEqual(len(records), 2)
        self.assertTrue(all(r.install_method == "package-manager" for r in records))
        self.assertTrue(all(r.artifact_type == "package" for r in records))


if __name__ == "__main__":
    unittest.main()
