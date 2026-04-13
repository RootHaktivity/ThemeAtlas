import unittest

from theme_manager.gui.app import _should_prompt_source_build


class TestTkSourcePromptHeuristic(unittest.TestCase):
    def test_source_prompt_helper_requires_explicit_source_signal(self):
        self.assertTrue(_should_prompt_source_build("Source build required (meson); use --allow-source-build to proceed"))
        self.assertTrue(
            _should_prompt_source_build(
                "The repository may contain source files rather than a packaged theme release."
            )
        )
        self.assertFalse(_should_prompt_source_build("No installable theme directories were found in this archive."))


if __name__ == "__main__":
    unittest.main()
