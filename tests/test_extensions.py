import subprocess
import unittest
from unittest.mock import patch

from theme_manager import extensions


class TestExtensionEnabledDetection(unittest.TestCase):
    def test_returns_false_when_cli_missing(self):
        with patch.object(extensions, "_gnome_extensions_cli", return_value=None):
            self.assertFalse(extensions.is_extension_enabled("foo@bar"))

    def test_uses_enabled_list_as_primary_source(self):
        uuid = "apps-menu@gnome-shell-extensions.gcampax.github.com"
        enabled_output = subprocess.CompletedProcess(
            args=["gnome-extensions", "list", "--enabled"],
            returncode=0,
            stdout=f"{uuid}\nother@ext\n",
            stderr="",
        )
        with patch.object(extensions, "_gnome_extensions_cli", return_value="/usr/bin/gnome-extensions"):
            with patch.object(extensions, "_run_silent", return_value=enabled_output):
                self.assertTrue(extensions.is_extension_enabled(uuid))

    def test_falls_back_to_info_enabled_yes(self):
        uuid = "apps-menu@gnome-shell-extensions.gcampax.github.com"
        list_output = subprocess.CompletedProcess(
            args=["gnome-extensions", "list", "--enabled"],
            returncode=0,
            stdout="other@ext\n",
            stderr="",
        )
        info_output = subprocess.CompletedProcess(
            args=["gnome-extensions", "info", uuid],
            returncode=0,
            stdout="Name: Apps Menu\nEnabled: Yes\nState: ACTIVE\n",
            stderr="",
        )
        with patch.object(extensions, "_gnome_extensions_cli", return_value="/usr/bin/gnome-extensions"):
            with patch.object(extensions, "_run_silent", side_effect=[list_output, info_output]):
                self.assertTrue(extensions.is_extension_enabled(uuid))


if __name__ == "__main__":
    unittest.main()
