import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("LTM_QT_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from theme_manager.gui.api import ThemeRecord
from theme_manager.gui_qt import state as state_module
from theme_manager.gui_qt.app import (
    AvailableTab,
    DesktopSetupDialog,
    PreviewDialog,
    ThemeManagerQtApp,
    _github_clone_url,
    _normalize_preview_url,
    _should_prompt_source_build,
    _record_matches_quality,
    _record_supports_desktop,
    _record_visual_mode,
    _trust_score,
)


def _app() -> QApplication:
    instance = QApplication.instance()
    return instance or QApplication([])


def _record() -> ThemeRecord:
    return ThemeRecord(
        id="theme-1",
        name="Nordic Dark",
        summary="Dark GTK theme",
        description="A dark GTK theme with GNOME support.",
        kind="gtk",
        score=88.0,
        downloads=1234,
        author="EliverLara",
        thumbnail_url="",
        download_url="https://example.com/theme.zip",
        detail_url="https://example.com/theme",
        updated="2026-01-01",
        source="github",
        artifact_type="theme",
        install_verified=True,
        compatibility="Universal",
        supported=True,
        support_note="Compatible with Ubuntu",
    )


class _StubTab:
    def __init__(self) -> None:
        self.refresh_calls = 0
        self.update_calls = 0

    def refresh(self) -> None:
        self.refresh_calls += 1

    def _update_restore_ui(self) -> None:
        self.update_calls += 1


def _stub_main_window() -> ThemeManagerQtApp:
    app = ThemeManagerQtApp.__new__(ThemeManagerQtApp)
    app.env = SimpleNamespace(desktop="gnome")
    app.settings_tab = _StubTab()
    app.installed_tab = _StubTab()
    app._restore_points = []
    app.ui_state = {
        "favorites": [],
        "recent": [],
        "collections": {"minimal": [], "gaming": [], "light": []},
        "onboarding_complete": True,
    }
    app._save_ui_state = lambda: None
    app.set_status = lambda _msg: None
    return app


class TestQtSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = _app()

    def test_preview_dialog_uses_generated_preview_when_live_images_missing(self):
        parent = QWidget()
        generated = QPixmap(20, 20)

        with patch("theme_manager.gui_qt.app._discover_preview_candidates", return_value=[]), patch(
            "theme_manager.gui_qt.app._generate_preview_pixmap", return_value=generated
        ):
            dialog = PreviewDialog(parent, _record(), lambda *_args: None)

        self.assertEqual(dialog.mode_label.text(), "Preview mode: Generated preview")
        self.assertEqual(dialog.screenshot_label.text(), "Generated preview only")
        self.assertIsNotNone(dialog.image_label.pixmap())
        dialog.close()
        parent.close()

    def test_preview_dialog_cycles_between_multiple_screenshots(self):
        parent = QWidget()
        first = QPixmap(20, 20)
        second = QPixmap(24, 24)

        with patch("theme_manager.gui_qt.app._discover_preview_candidates", return_value=["shot-a", "shot-b"]), patch(
            "theme_manager.gui_qt.app._load_source_pixmap", side_effect=[first, second]
        ):
            dialog = PreviewDialog(parent, _record(), lambda *_args: None)
            self.assertEqual(dialog.screenshot_label.text(), "Screenshot 1 of 2")
            dialog._step_screenshot(1)

        self.assertEqual(dialog.screenshot_label.text(), "Screenshot 2 of 2")
        self.assertEqual(dialog._active_image_index, 1)
        dialog.close()
        parent.close()

    def test_desktop_setup_dialog_returns_only_selected_changes(self):
        parent = QWidget()
        choices = {
            "gtk": ["Orchis", "WhiteSur"],
            "icons": ["Papirus"],
            "cursor": ["Bibata"],
            "shell": ["Orchis Shell"],
        }
        current = {"gtk": "Orchis", "icons": "Papirus", "cursor": "", "shell": ""}

        dialog = DesktopSetupDialog(parent, choices, current)
        dialog._combos["gtk"].setCurrentIndex(0)
        dialog._combos["icons"].setCurrentIndex(1)
        dialog._combos["cursor"].setCurrentIndex(1)

        self.assertEqual(dialog.selected_setup(), {"icons": "Papirus", "cursor": "Bibata"})
        dialog.close()
        parent.close()

    def test_available_kind_filter_includes_app_tooling(self):
        app = _stub_main_window()
        app.thread_pool = SimpleNamespace(start=lambda *_args, **_kwargs: None)
        with patch("theme_manager.gui_qt.app.get_sources", return_value=[]), patch.object(
            AvailableTab, "load_default", lambda self: None
        ), patch.object(AvailableTab, "_probe_health", lambda self: None):
            tab = AvailableTab(app)
        values = [tab.kind_combo.itemText(i) for i in range(tab.kind_combo.count())]
        self.assertIn("app/tooling", values)
        tab.close()

    def test_app_tooling_mode_shows_category_filter(self):
        app = _stub_main_window()
        app.thread_pool = SimpleNamespace(start=lambda *_args, **_kwargs: None)
        with patch("theme_manager.gui_qt.app.get_sources", return_value=[]), patch.object(
            AvailableTab, "load_default", lambda self: None
        ), patch.object(AvailableTab, "_probe_health", lambda self: None):
            tab = AvailableTab(app, fixed_kind="app/tooling", show_category_filter=True)

        self.assertEqual(tab.fixed_kind, "app/tooling")
        self.assertIsNotNone(tab.category_combo)
        self.assertIsNotNone(tab.install_path_combo)
        self.assertIsNone(tab.appearance_combo)
        self.assertIsNone(tab.desktop_combo)
        self.assertIsNone(tab.quality_combo)
        categories = [tab.category_combo.itemText(i) for i in range(tab.category_combo.count())]
        self.assertIn("appearance", categories)
        self.assertIn("settings", categories)
        self.assertIn("utilities", categories)
        tab.close()


class TestUiState(unittest.TestCase):
    def test_save_and_load_ui_state_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            state_file = Path(td) / "ui_state.json"
            sample = {
                "favorites": ["gtk:orchis"],
                "recent": ["icons:papirus"],
                "collections": {"minimal": ["gtk:orchis"], "gaming": ["icons:papirus"]},
                "onboarding_complete": True,
            }

            with patch.object(state_module, "_STATE_FILE", state_file), patch.object(
                state_module, "_CONFIG_DIR", Path(td)
            ):
                state_module.save_ui_state(sample)
                loaded = state_module.load_ui_state()

        self.assertEqual(loaded["favorites"], ["gtk:orchis"])
        self.assertEqual(loaded["recent"], ["icons:papirus"])
        self.assertEqual(loaded["collections"]["minimal"], ["gtk:orchis"])
        self.assertTrue(loaded["onboarding_complete"])


class TestDiscoveryAndTrustHelpers(unittest.TestCase):
    def test_preview_url_normalizer_rejects_non_http_scheme(self):
        self.assertEqual(_normalize_preview_url("file:///etc/passwd"), "")
        self.assertEqual(_normalize_preview_url("https://example.com/image.png"), "https://example.com/image.png")

    def test_visual_mode_detection_prefers_dark_keywords(self):
        rec = _record()
        rec.name = "Nordic Dark"
        self.assertEqual(_record_visual_mode(rec), "dark")

    def test_quality_filter_preview_available(self):
        rec = _record()
        rec.thumbnail_url = "https://example.com/thumb.png"
        self.assertTrue(_record_matches_quality(rec, "preview available"))
        rec.thumbnail_url = ""
        rec.detail_url = ""
        self.assertFalse(_record_matches_quality(rec, "preview available"))

    def test_desktop_support_filter_uses_supported_desktops_string(self):
        rec = _record()
        rec.kind = "icons"
        rec.description = "Works on GNOME and KDE Plasma desktops"
        self.assertTrue(_record_supports_desktop(rec, "gnome"))
        self.assertTrue(_record_supports_desktop(rec, "kde plasma"))
        self.assertFalse(_record_supports_desktop(rec, "xfce"))

    def test_trust_score_increases_with_verified_metadata(self):
        rec = _record()
        rec.install_verified = True
        rec.download_url = "https://example.com/theme.zip"
        rec.updated = "2026-01-01"
        score, reasons = _trust_score(rec, screenshot_count=2)
        self.assertGreaterEqual(score, 80)
        self.assertTrue(any("screenshot" in reason for reason in reasons))

    def test_source_prompt_helper_requires_explicit_source_signal(self):
        self.assertTrue(_should_prompt_source_build("Source build required (meson); use --allow-source-build to proceed"))
        self.assertTrue(
            _should_prompt_source_build(
                "The repository may contain source files rather than a packaged theme release."
            )
        )
        self.assertFalse(_should_prompt_source_build("No installable theme directories were found in this archive."))

    def test_github_clone_url_prefers_detail_url_repo_path(self):
        rec = _record()
        rec.detail_url = "https://github.com/example/theme-repo"
        rec.download_url = "https://codeload.github.com/example/theme-repo/zip/refs/heads/main"
        self.assertEqual(_github_clone_url(rec), "https://github.com/example/theme-repo.git")

    def test_github_clone_url_supports_codeload_download_url(self):
        rec = _record()
        rec.detail_url = ""
        rec.download_url = "https://codeload.github.com/example/theme-repo/zip/refs/heads/main"
        self.assertEqual(_github_clone_url(rec), "https://github.com/example/theme-repo.git")


class TestRestoreAndSetupFlows(unittest.TestCase):
    def test_restore_last_snapshot_reapplies_saved_theme_values(self):
        app = _stub_main_window()
        app._restore_points = [
            {
                "label": "Before applying Orchis",
                "snapshot": {"gtk": "Adwaita", "icons": "Papirus", "cursor": None, "shell": None},
            }
        ]

        with patch("theme_manager.gui_qt.app._apply_theme_value", return_value=True) as apply_mock:
            ok, message = ThemeManagerQtApp.restore_last_snapshot(app)

        self.assertTrue(ok)
        self.assertIn("Restored Before applying Orchis", message)
        apply_mock.assert_any_call("gtk", "Adwaita", "gnome")
        apply_mock.assert_any_call("icons", "Papirus", "gnome")
        self.assertEqual(app.settings_tab.refresh_calls, 1)
        self.assertEqual(app.installed_tab.update_calls, 1)

    def test_apply_desktop_setup_rolls_back_checkpoint_after_failure(self):
        app = _stub_main_window()
        remembered: list[tuple[str, str]] = []
        app.remember_recent_theme = lambda kind, name: remembered.append((kind, name))

        apply_calls: list[tuple[str, str, str]] = []

        def fake_apply(kind: str, value: str, desktop: str) -> bool:
            apply_calls.append((kind, value, desktop))
            if (kind, value) == ("icons", "Papirus"):
                return False
            return True

        with patch(
            "theme_manager.gui_qt.app.get_current_themes",
            return_value={"gtk": "Adwaita", "icons": "Yaru", "cursor": "Bibata", "shell": "Default"},
        ), patch("theme_manager.gui_qt.app._apply_theme_value", side_effect=fake_apply):
            ok, message = ThemeManagerQtApp.apply_desktop_setup(app, {"gtk": "Orchis", "icons": "Papirus"})

        self.assertFalse(ok)
        self.assertIn("previous setup restored", message)
        self.assertEqual(remembered, [("gtk", "Orchis")])
        self.assertEqual(
            apply_calls,
            [
                ("gtk", "Orchis", "gnome"),
                ("icons", "Papirus", "gnome"),
                ("gtk", "Adwaita", "gnome"),
                ("icons", "Yaru", "gnome"),
                ("cursor", "Bibata", "gnome"),
                ("shell", "Default", "gnome"),
            ],
        )
        self.assertEqual(app.settings_tab.refresh_calls, 1)
        self.assertEqual(app.installed_tab.update_calls, 1)
        self.assertEqual(app._restore_points, [])


class TestInstallSafetyAndRecovery(unittest.TestCase):
    def test_first_run_onboarding_marks_complete_when_opted_out(self):
        app = _stub_main_window()
        app.ui_state["onboarding_complete"] = False

        with patch("theme_manager.gui_qt.app.WelcomeDialog") as dialog_cls:
            dialog = dialog_cls.return_value
            dialog.hide_next_time.isChecked.return_value = True
            ThemeManagerQtApp._maybe_show_onboarding(app)

        self.assertTrue(app.ui_state["onboarding_complete"])

    def test_install_policy_set_and_get_round_trip(self):
        app = _stub_main_window()

        ThemeManagerQtApp.set_install_policy(app, allow_install_scripts=True, sandbox_install_scripts=False)
        policy = ThemeManagerQtApp.install_policy(app)

        self.assertTrue(policy["allow_install_scripts"])
        self.assertFalse(policy["sandbox_install_scripts"])

    def test_interrupted_install_recovery_clears_active_state(self):
        app = _stub_main_window()
        app.ui_state["active_install"] = {"name": "Orchis", "phase": "extracting", "started_at": "2026-01-01T00:00:00"}

        with patch("theme_manager.gui_qt.app.QMessageBox.warning") as warn_mock:
            ThemeManagerQtApp._check_interrupted_install(app)

        self.assertEqual(app.ui_state.get("active_install"), {})
        self.assertTrue(warn_mock.called)


class TestInstalledStateMethods(unittest.TestCase):
    def test_toggle_favorite_adds_then_removes_entry(self):
        app = _stub_main_window()
        entry_id = ThemeManagerQtApp.theme_entry_id("gtk", "Orchis")

        first = ThemeManagerQtApp.toggle_favorite_entry(app, entry_id)
        second = ThemeManagerQtApp.toggle_favorite_entry(app, entry_id)

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(app.ui_state["favorites"], [])

    def test_remember_recent_theme_keeps_most_recent_unique(self):
        app = _stub_main_window()

        ThemeManagerQtApp.remember_recent_theme(app, "gtk", "Orchis")
        ThemeManagerQtApp.remember_recent_theme(app, "icons", "Papirus")
        ThemeManagerQtApp.remember_recent_theme(app, "gtk", "Orchis")

        self.assertEqual(
            app.ui_state["recent"],
            [
                ThemeManagerQtApp.theme_entry_id("gtk", "Orchis"),
                ThemeManagerQtApp.theme_entry_id("icons", "Papirus"),
            ],
        )

    def test_toggle_entry_collection_and_membership_helpers(self):
        app = _stub_main_window()
        entry_id = ThemeManagerQtApp.theme_entry_id("icons", "Papirus")

        added = ThemeManagerQtApp.toggle_entry_collection(app, entry_id, "Minimal")
        in_collection = ThemeManagerQtApp.entry_in_collection(app, entry_id, "minimal")
        collections = ThemeManagerQtApp.entry_collections(app, entry_id)
        removed = ThemeManagerQtApp.toggle_entry_collection(app, entry_id, "minimal")

        self.assertTrue(added)
        self.assertTrue(in_collection)
        self.assertEqual(collections, ["minimal"])
        self.assertFalse(removed)
        self.assertFalse(ThemeManagerQtApp.entry_in_collection(app, entry_id, "minimal"))


if __name__ == "__main__":
    unittest.main()