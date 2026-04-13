import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from theme_manager.extractor import _apply_known_app_runtime_patches, _install_built_output


class TestBuiltOutputFiltering(unittest.TestCase):
    def test_install_built_output_skips_stock_icon_bases(self):
        with tempfile.TemporaryDirectory() as td:
            prefix = Path(td)
            (prefix / "share" / "icons" / "hicolor").mkdir(parents=True)
            (prefix / "share" / "icons" / "MyIcons").mkdir(parents=True)

            with patch("theme_manager.extractor._classify_theme", return_value="icons"), patch(
                "theme_manager.extractor._install_theme_folder", side_effect=lambda src, kind, system_wide: src.name
            ):
                installed = _install_built_output(prefix, system_wide=False)

        self.assertIn("MyIcons", installed)
        self.assertNotIn("hicolor", installed)

    def test_apply_known_app_runtime_patches_updates_gradience_startup_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            user_site = Path(td)
            gradience_main = user_site / "gradience" / "frontend" / "main.py"
            gradience_main.parent.mkdir(parents=True)
            gradience_main.write_text(
                '    def load_preset_from_css(self):\n'
                '        try:\n'
                '            logging.debug(f"Loaded custom CSS variables: {variables}")\n'
                '            preset = {}\n'
                '        except OSError:  # fallback to adwaita\n'
                '            logging.warning("Custom preset not found. Fallback to Adwaita")\n'
                '    def do_activate(self):\n'
                '        self.load_preset_from_css()\n',
                encoding="utf-8",
            )

            _apply_known_app_runtime_patches(user_site)

            updated = gradience_main.read_text(encoding="utf-8")
            self.assertIn('raise KeyError("window_bg_color")', updated)
            self.assertIn('except (OSError, KeyError):', updated)
            self.assertIn('Custom preset is missing required variables. Fallback to Adwaita', updated)
            self.assertIn('self.win.present()\n        self.load_preset_from_css()', updated)


if __name__ == "__main__":
    unittest.main()
