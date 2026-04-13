import subprocess
import tempfile
import unittest
import json
from pathlib import Path
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


class TestExtensionCompatibility(unittest.TestCase):
    def _write_metadata(self, root: Path, payload: dict) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        (root / "metadata.json").write_text(json.dumps(payload), encoding="utf-8")
        return root

    def test_compatible_when_current_shell_in_supported_versions(self):
        with tempfile.TemporaryDirectory() as td:
            ext_dir = self._write_metadata(
                Path(td),
                {
                    "uuid": "quick-settings-tweaks@qwreey",
                    "shell-version": ["46", "47"],
                },
            )
            with patch.object(extensions, "get_current_gnome_shell_major", return_value="46"):
                ok, reason = extensions.extension_is_compatible_with_shell(ext_dir)
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_incompatible_when_current_shell_not_supported(self):
        with tempfile.TemporaryDirectory() as td:
            ext_dir = self._write_metadata(
                Path(td),
                {
                    "uuid": "quick-settings-tweaks@qwreey",
                    "shell-version": ["48", "49"],
                },
            )
            with patch.object(extensions, "get_current_gnome_shell_major", return_value="46"):
                ok, reason = extensions.extension_is_compatible_with_shell(ext_dir)
        self.assertFalse(ok)
        self.assertIn("supports GNOME Shell 48, 49", reason)
        self.assertIn("current GNOME Shell is 46", reason)


if __name__ == "__main__":
    unittest.main()
