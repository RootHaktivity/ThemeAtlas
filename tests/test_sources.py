import subprocess
import base64
import json
import unittest
from unittest.mock import patch

from theme_manager.gui.api import ThemeRecord
from theme_manager.gui.sources import AptSource, GitHubSource, _http_get, get_sources, search_source
from theme_manager.gui.sources import sort_records


class TestSources(unittest.TestCase):
    def test_sort_records_highest_rated_orders_by_score(self):
        records = [
            ThemeRecord("1", "Alpha", "", "", "gtk", 1.5, 25, "", "", "", "", "2025-01-01"),
            ThemeRecord("2", "Beta", "", "", "gtk", 3.1, 5, "", "", "", "", "2025-01-02"),
            ThemeRecord("3", "Gamma", "", "", "gtk", 2.2, 100, "", "", "", "", "2025-01-03"),
        ]

        ordered = sort_records(records, "highest-rated")
        self.assertEqual([r.name for r in ordered], ["Beta", "Gamma", "Alpha"])

    def test_sort_records_trending_prioritizes_recent_activity(self):
        records = [
            ThemeRecord("1", "Old", "", "", "gtk", 4.7, 240, "", "", "", "", "2020-01-01"),
            ThemeRecord("2", "Fresh", "", "", "gtk", 4.7, 240, "", "", "", "", "2030-01-01"),
        ]

        ordered = sort_records(records, "trending")
        self.assertEqual(ordered[0].name, "Fresh")

    def test_http_get_rejects_non_http_scheme(self):
        with self.assertRaises(ValueError):
            _http_get("file:///etc/passwd")

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
            "gnome-tweaks - tweak advanced GNOME options app\n"
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

    def test_apt_source_parses_app_tooling_kind(self):
        sample = (
            "gnome-tweaks - tweak advanced GNOME options app\n"
            "meld - graphical diff and merge tool\n"
        )
        run_result = subprocess.CompletedProcess(
            args=["apt-cache", "search", "app"],
            returncode=0,
            stdout=sample,
            stderr="",
        )

        with patch("theme_manager.gui.sources.shutil.which", side_effect=lambda c: "/usr/bin/apt-cache" if c == "apt-cache" else None):
            with patch("theme_manager.gui.sources.subprocess.run", return_value=run_result):
                records = AptSource().search("app", "app/tooling", 1)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].name, "Gnome Tweaks")
        self.assertTrue(all(r.kind == "app/tooling" for r in records))
        self.assertTrue(all(r.install_method == "package-manager" for r in records))
        self.assertTrue(all((r.category or "") for r in records))

    def test_github_source_filters_probable_non_theme_repo(self):
        payload = {
            "items": [
                {
                    "id": 1,
                    "name": "Gradience",
                    "full_name": "GradienceTeam/Gradience",
                    "topics": ["gnome-shell-theme"],
                    "description": "Change the look of Adwaita, with ease",
                    "default_branch": "main",
                    "stargazers_count": 100,
                    "forks_count": 5,
                    "owner": {"login": "GradienceTeam"},
                    "html_url": "https://github.com/GradienceTeam/Gradience",
                    "pushed_at": "2026-04-11T00:00:00Z",
                }
            ]
        }
        with patch("theme_manager.gui.sources._http_get", return_value=payload):
            records = GitHubSource().search("", "shell", 1)
        self.assertEqual(records, [])

    def test_github_source_keeps_likely_theme_repo(self):
        payload = {
            "items": [
                {
                    "id": 2,
                    "name": "orchis-theme",
                    "full_name": "vinceliuice/orchis-theme",
                    "topics": ["gtk-theme", "gnome-shell-theme"],
                    "description": "A gtk theme for GNOME desktops",
                    "default_branch": "main",
                    "stargazers_count": 100,
                    "forks_count": 5,
                    "owner": {"login": "vinceliuice"},
                    "html_url": "https://github.com/vinceliuice/orchis-theme",
                    "pushed_at": "2026-04-11T00:00:00Z",
                }
            ]
        }
        with patch("theme_manager.gui.sources._http_get", return_value=payload):
            records = GitHubSource().search("", "gtk", 1)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].name, "Orchis Theme")

    def test_github_source_app_tooling_filters_non_theming_tools(self):
        payload = {
            "items": [
                {
                    "id": 3,
                    "name": "mission-center",
                    "full_name": "mission-center-devs/mission-center",
                    "topics": ["gtk4", "monitoring", "linux"],
                    "description": "Monitor CPU, memory, disk, network and GPU usage",
                    "default_branch": "main",
                    "stargazers_count": 100,
                    "forks_count": 5,
                    "owner": {"login": "mission-center-devs"},
                    "html_url": "https://github.com/mission-center-devs/mission-center",
                    "pushed_at": "2026-04-11T00:00:00Z",
                }
            ]
        }
        with patch("theme_manager.gui.sources._http_get", return_value=payload):
            records = GitHubSource().search("", "app/tooling", 1)
        self.assertEqual(records, [])

    def test_github_source_app_tooling_keeps_theming_tools(self):
        payload = {
            "items": [
                {
                    "id": 4,
                    "name": "Gradience",
                    "full_name": "GradienceTeam/Gradience",
                    "topics": ["gtk4", "libadwaita", "theme"],
                    "description": "Tool to customize GTK and Adwaita theme colors",
                    "default_branch": "main",
                    "stargazers_count": 300,
                    "forks_count": 10,
                    "owner": {"login": "GradienceTeam"},
                    "html_url": "https://github.com/GradienceTeam/Gradience",
                    "pushed_at": "2026-04-11T00:00:00Z",
                }
            ]
        }
        with patch("theme_manager.gui.sources._http_get", return_value=payload):
            records = GitHubSource().search("", "app/tooling", 1)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].kind, "app/tooling")
        self.assertEqual(records[0].install_method, "source")
        self.assertTrue(records[0].download_url.startswith("https://github.com/"))
        self.assertTrue(records[0].category)

    def test_github_source_app_tooling_keeps_extension_repos(self):
        payload = {
            "items": [
                {
                    "id": 401,
                    "name": "gnome-shell-extensions-sync",
                    "full_name": "vuboi/gnome-shell-extension-sync",
                    "topics": ["gnome-shell-extension"],
                    "description": "Sync your GNOME Shell extensions",
                    "default_branch": "main",
                    "stargazers_count": 220,
                    "forks_count": 17,
                    "owner": {"login": "vuboi"},
                    "html_url": "https://github.com/vuboi/gnome-shell-extension-sync",
                    "pushed_at": "2026-04-11T00:00:00Z",
                }
            ]
        }

        with patch("theme_manager.gui.sources._http_get", return_value=payload):
            records = GitHubSource().search("", "app/tooling", 1)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].artifact_type, "extension")

    def test_github_source_app_tooling_default_browse_uses_focused_queries(self):
        payloads = [
            {
                "items": [
                    {
                        "id": 41,
                        "name": "Gradience",
                        "full_name": "GradienceTeam/Gradience",
                        "topics": ["gtk4", "libadwaita", "theme"],
                        "description": "Tool to customize GTK and Adwaita theme colors",
                        "default_branch": "main",
                        "stargazers_count": 300,
                        "forks_count": 10,
                        "owner": {"login": "GradienceTeam"},
                        "html_url": "https://github.com/GradienceTeam/Gradience",
                        "pushed_at": "2026-04-11T00:00:00Z",
                    }
                ]
            }
        ] + [{"items": []} for _ in range(9)]

        with patch("theme_manager.gui.sources._http_get", side_effect=payloads):
            records = GitHubSource().search("", "app/tooling", 1)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].name, "Gradience")

    def test_github_source_app_tooling_filters_cli_customization_helpers(self):
        payload = {
            "items": [
                {
                    "id": 5,
                    "name": "pywal",
                    "full_name": "dylanaraps/pywal",
                    "topics": ["theme", "terminal", "colors"],
                    "description": "Command line tool to generate terminal colors from wallpapers",
                    "default_branch": "main",
                    "stargazers_count": 200,
                    "forks_count": 10,
                    "owner": {"login": "dylanaraps"},
                    "html_url": "https://github.com/dylanaraps/pywal",
                    "pushed_at": "2026-04-11T00:00:00Z",
                }
            ]
        }
        with patch("theme_manager.gui.sources._http_get", return_value=payload):
            records = GitHubSource().search("", "app/tooling", 1)
        self.assertEqual(records, [])

    def test_github_source_app_tooling_filters_dotfiles_and_config_repos(self):
        payload = {
            "items": [
                {
                    "id": 6,
                    "name": "dotfiles",
                    "full_name": "example/dotfiles",
                    "topics": ["gnome", "theme", "dotfiles"],
                    "description": "My personal dotfiles and desktop customization setup",
                    "default_branch": "main",
                    "stargazers_count": 200,
                    "forks_count": 10,
                    "owner": {"login": "example"},
                    "html_url": "https://github.com/example/dotfiles",
                    "pushed_at": "2026-04-11T00:00:00Z",
                }
            ]
        }
        with patch("theme_manager.gui.sources._http_get", return_value=payload):
            records = GitHubSource().search("", "app/tooling", 1)
        self.assertEqual(records, [])

    def test_apt_source_keeps_allowlisted_desktop_customization_tool(self):
        sample = "qt5ct - Qt5 Configuration Tool\n"
        run_result = subprocess.CompletedProcess(
            args=["apt-cache", "search", "qt5ct"],
            returncode=0,
            stdout=sample,
            stderr="",
        )

        with patch("theme_manager.gui.sources.shutil.which", side_effect=lambda c: "/usr/bin/apt-cache" if c == "apt-cache" else None):
            with patch("theme_manager.gui.sources.subprocess.run", return_value=run_result):
                records = AptSource().search("qt5ct", "app/tooling", 1)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].name, "Qt5Ct")
        self.assertEqual(records[0].kind, "app/tooling")

    def test_search_source_app_tooling_default_falls_back_to_mock_extensions_when_empty(self):
        class _EmptySource:
            name = "github"
            label = "GitHub"

            def search(self, query: str, kind: str = "all", page: int = 1):
                return []

        with patch("theme_manager.gui.sources.get_sources", return_value=[_EmptySource()]):
            records = search_source("github", "", "app/tooling", 1)

        self.assertGreaterEqual(len(records), 1)
        self.assertTrue(all(r.kind == "app/tooling" for r in records))
        self.assertTrue(any(r.artifact_type == "extension" for r in records))

    def test_github_extension_record_includes_artifact_type_and_shell_compatibility(self):
        item = {
            "id": 7,
            "name": "quick-settings-tweaks",
            "full_name": "qwreey/quick-settings-tweaks",
            "topics": ["gnome-shell-extension"],
            "description": "Quick settings extension for GNOME",
            "default_branch": "master",
            "stargazers_count": 123,
            "forks_count": 9,
            "owner": {"login": "qwreey"},
            "html_url": "https://github.com/qwreey/quick-settings-tweaks",
            "pushed_at": "2026-04-13T00:00:00Z",
        }
        metadata = {
            "uuid": "quick-settings-tweaks@qwreey",
            "shell-version": ["48", "49"],
        }
        metadata_payload = {
            "content": base64.b64encode(json.dumps(metadata).encode("utf-8")).decode("ascii")
        }

        with patch("theme_manager.gui.sources._http_get", return_value=metadata_payload):
            record = GitHubSource._to_record(item, "all")

        self.assertEqual(record.artifact_type, "extension")
        self.assertEqual(record.compatibility, "GNOME Shell 48, 49")

    def test_github_app_tooling_extension_keeps_extension_artifact_type(self):
        item = {
            "id": 9,
            "name": "quick-settings-tweaks",
            "full_name": "qwreey/quick-settings-tweaks",
            "topics": ["gnome-shell-extension"],
            "description": "Quick settings extension for GNOME",
            "default_branch": "master",
            "stargazers_count": 123,
            "forks_count": 9,
            "owner": {"login": "qwreey"},
            "html_url": "https://github.com/qwreey/quick-settings-tweaks",
            "pushed_at": "2026-04-13T00:00:00Z",
        }
        metadata = {
            "uuid": "quick-settings-tweaks@qwreey",
            "shell-version": ["48", "49"],
        }
        metadata_payload = {
            "content": base64.b64encode(json.dumps(metadata).encode("utf-8")).decode("ascii")
        }

        with patch("theme_manager.gui.sources._http_get", return_value=metadata_payload):
            record = GitHubSource._to_record(item, "app/tooling")

        self.assertEqual(record.artifact_type, "extension")
        self.assertEqual(record.install_method, "archive")
        self.assertEqual(record.support_note, "")

    def test_github_app_tooling_extension_detected_without_topics_via_metadata(self):
        item = {
            "id": 10,
            "name": "quick-settings-tweaks",
            "full_name": "qwreey/quick-settings-tweaks",
            "topics": [],
            "description": "Let's tweak gnome Quick Settings!",
            "default_branch": "master",
            "stargazers_count": 123,
            "forks_count": 9,
            "owner": {"login": "qwreey"},
            "html_url": "https://github.com/qwreey/quick-settings-tweaks",
            "pushed_at": "2026-04-13T00:00:00Z",
        }
        metadata = {
            "uuid": "quick-settings-tweaks@qwreey",
            "shell-version": ["48", "49"],
        }
        metadata_payload = {
            "content": base64.b64encode(json.dumps(metadata).encode("utf-8")).decode("ascii")
        }

        with patch("theme_manager.gui.sources._http_get", return_value=metadata_payload):
            record = GitHubSource._to_record(item, "app/tooling")

        self.assertEqual(record.artifact_type, "extension")
        self.assertEqual(record.compatibility, "GNOME Shell 48, 49")
        self.assertEqual(record.install_method, "archive")

    def test_github_extension_without_metadata_marks_unknown_shell_compatibility(self):
        item = {
            "id": 8,
            "name": "some-extension",
            "full_name": "example/some-extension",
            "topics": ["gnome-shell-extension"],
            "description": "A shell extension",
            "default_branch": "main",
            "stargazers_count": 50,
            "forks_count": 2,
            "owner": {"login": "example"},
            "html_url": "https://github.com/example/some-extension",
            "pushed_at": "2026-04-13T00:00:00Z",
        }

        with patch("theme_manager.gui.sources._http_get", return_value={}):
            record = GitHubSource._to_record(item, "all")

        self.assertEqual(record.artifact_type, "extension")
        self.assertEqual(record.compatibility, "GNOME Shell (version not declared)")


if __name__ == "__main__":
    unittest.main()
