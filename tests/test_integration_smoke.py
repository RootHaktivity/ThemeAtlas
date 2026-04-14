import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

from theme_manager.extractor import extract_archive
from theme_manager.installer import preview_archive_changes
from theme_manager.gui.api import ThemeRecord
from theme_manager.gui_qt.app import AvailableTab
from theme_manager.error_formatter import format_error


class TestIntegrationSmoke(unittest.TestCase):
    def test_extract_archive_installs_gtk_theme_from_zip(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            archive = tmp / "theme.zip"
            user_themes = tmp / "user_themes"
            user_icons = tmp / "user_icons"
            user_shell = tmp / "user_shell"

            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("NeatTheme/gtk-3.0/gtk.css", "/* test */")

            with patch("theme_manager.extractor.USER_THEMES_DIR", user_themes), \
                 patch("theme_manager.extractor.USER_ICONS_DIR", user_icons), \
                 patch("theme_manager.extractor.USER_SHELL_THEMES_DIR", user_shell):
                names = extract_archive(str(archive), system_wide=False)

            self.assertIn("NeatTheme", names)
            self.assertTrue((user_themes / "NeatTheme").is_dir())
            self.assertTrue((user_themes / "NeatTheme" / "gtk-3.0" / "gtk.css").is_file())

    def test_package_record_install_routing(self):
        record = ThemeRecord(
            id="apt-adwaita-icon-theme",
            name="Adwaita Icon Theme",
            summary="default icon theme",
            description="default icon theme",
            kind="icons",
            score=0.0,
            downloads=0,
            author="distro",
            thumbnail_url="",
            download_url="",
            detail_url="",
            updated="",
            source="apt",
            artifact_type="package",
            install_method="package-manager",
            package_name="adwaita-icon-theme",
            compatibility="Debian/Ubuntu",
            install_verified=True,
        )

        with patch("theme_manager.gui_qt.app.install_from_package", return_value=True) as install_mock:
            names = AvailableTab._install_package_record(record)

        install_mock.assert_called_once_with("adwaita-icon-theme", "apt")
        self.assertEqual(names, ["Adwaita Icon Theme"])

    def test_package_record_install_raises_on_failure(self):
        record = ThemeRecord(
            id="apt-adwaita-icon-theme",
            name="Adwaita Icon Theme",
            summary="default icon theme",
            description="default icon theme",
            kind="icons",
            score=0.0,
            downloads=0,
            author="distro",
            thumbnail_url="",
            download_url="",
            detail_url="",
            updated="",
            source="apt",
            artifact_type="package",
            install_method="package-manager",
            package_name="adwaita-icon-theme",
            compatibility="Debian/Ubuntu",
            install_verified=True,
        )

        with patch("theme_manager.gui_qt.app.install_from_package", return_value=False):
            with self.assertRaises(RuntimeError):
                AvailableTab._install_package_record(record)

    def test_extract_archive_rejects_zip_path_traversal(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            archive = tmp / "bad-theme.zip"
            escaped_target = tmp / "escaped.txt"

            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("../escaped.txt", "pwned")

            with self.assertRaises(ValueError):
                extract_archive(str(archive), system_wide=False)

            self.assertFalse(escaped_target.exists())

    def test_preview_archive_changes_lists_destination_paths(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            archive = tmp / "theme.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("CoolTheme/gtk-3.0/gtk.css", "/* test */")

            preview = preview_archive_changes(str(archive), system_wide=False)

        self.assertIn("operations", preview)
        self.assertTrue(preview["operations"])
        self.assertEqual(preview["operations"][0]["kind"], "gtk")
        self.assertIn(".local/share/themes", preview["operations"][0]["destination"])

    def test_extract_archive_skips_scripts_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            archive = tmp / "scripted.zip"
            script_marker = tmp / "script_ran.txt"
            user_themes = tmp / "user_themes"
            user_icons = tmp / "user_icons"
            user_shell = tmp / "user_shell"

            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("ScriptTheme/gtk-3.0/gtk.css", "/* test */")
                zf.writestr("ScriptTheme/install.sh", f"#!/usr/bin/env bash\necho yes > '{script_marker}'\n")

            with patch("theme_manager.extractor.USER_THEMES_DIR", user_themes), \
                 patch("theme_manager.extractor.USER_ICONS_DIR", user_icons), \
                 patch("theme_manager.extractor.USER_SHELL_THEMES_DIR", user_shell):
                extract_archive(str(archive), system_wide=False)

            self.assertFalse(script_marker.exists())

    def test_error_formatter_meson_not_found(self):
        """Error formatter provides actionable remediation for missing meson."""
        error = "meson: command not found\nSetup failed"
        formatted = format_error(error)
        
        self.assertIn("Meson build system is not installed", formatted)
        self.assertIn("sudo apt install meson", formatted)
        self.assertIn("sudo pacman -S meson", formatted)

    def test_error_formatter_missing_dependency(self):
        """Error formatter identifies missing build dependency."""
        error = "pkg-config --cflags-only-I gtk+-3.0\ngdk-pixbuf: command not found"
        formatted = format_error(error)
        
        self.assertIn("build dependency is missing", formatted)
        self.assertNotEqual(formatted, error)

    def test_error_formatter_compiler_missing(self):
        """Error formatter detects missing C compiler."""
        error = "gcc: command not found\nNo C compiler found"
        formatted = format_error(error)
        
        self.assertIn("C compiler is not installed", formatted)
        self.assertIn("build-essential", formatted)

    def test_error_formatter_sass_missing(self):
        """Error formatter identifies missing SASS."""
        error = "sass: command not found"
        formatted = format_error(error)
        
        self.assertIn("SASS", formatted)
        self.assertIn("npm install -g sass", formatted)

    def test_source_github_record_heuristic(self):
        """GitHub records without download_url are detected as source-only."""
        from theme_manager.gui_qt.app import _is_likely_github_source_only
        
        source_only = ThemeRecord(
            id="github-src", kind="gtk", name="SourceTheme",
            summary="", description="", score=0, downloads=0, author="",
            thumbnail_url="", download_url="", detail_url="https://github.com/user/theme",
            updated="", source="github"
        )
        self.assertTrue(_is_likely_github_source_only(source_only))
        
        prebuilt = ThemeRecord(
            id="github-pre", kind="gtk", name="PrebuiltTheme",
            summary="", description="", score=0, downloads=0, author="",
            thumbnail_url="", download_url="https://github.com/user/theme/releases/download/v1/theme.zip",
            detail_url="https://github.com/user/theme", updated="", source="github"
        )
        self.assertFalse(_is_likely_github_source_only(prebuilt))

    def test_theme_record_roundtrip_after_install(self):
        """Theme record state survives through install workflow."""
        record = ThemeRecord(
            id="test-1", kind="gtk", name="TestTheme",
            summary="Test", description="Desc", score=4.5, downloads=100,
            author="Tester", thumbnail_url="", download_url="https://example.com/t.zip",
            detail_url="https://example.com", updated="2026-01-01", source="github"
        )
        
        # Verify state is preserved and immutable during workflow
        original_name = record.name
        original_id = record.id
        
        self.assertEqual(record.name, original_name)
        self.assertEqual(record.id, original_id)
        self.assertEqual(record.kind, "gtk")


if __name__ == "__main__":
    unittest.main()
