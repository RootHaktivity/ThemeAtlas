import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from theme_manager.extractor import _hydrate_submodules_from_gitmodules, _parse_gitmodules


class TestExtractorSubmoduleHelpers(unittest.TestCase):
    def test_parse_gitmodules_reads_path_and_url(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".gitmodules").write_text(
                """
[submodule \"data/submodules/libadwaita\"]
\tpath = data/submodules/libadwaita
\turl = https://github.com/GNOME/libadwaita.git
""".strip(),
                encoding="utf-8",
            )

            entries = _parse_gitmodules(root)

        self.assertEqual(entries, [("data/submodules/libadwaita", "https://github.com/GNOME/libadwaita.git")])

    def test_hydrate_submodules_skips_relative_url_without_repo_context(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".gitmodules").write_text(
                """
[submodule \"data/submodules/dep\"]
\tpath = data/submodules/dep
\turl = ../dep.git
""".strip(),
                encoding="utf-8",
            )

            with patch("theme_manager.extractor.shutil.which", return_value="/usr/bin/git"), patch(
                "theme_manager.extractor.subprocess.run"
            ) as run_mock:
                ok = _hydrate_submodules_from_gitmodules(root)

        self.assertFalse(ok)
        run_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
