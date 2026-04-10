import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from theme_manager.extractor import extract_archive
from theme_manager.gui.api import ThemeRecord
from theme_manager.gui_qt.app import AvailableTab


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


if __name__ == "__main__":
    unittest.main()
