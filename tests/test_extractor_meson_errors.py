import unittest
import re


class TestMesonErrorClassification(unittest.TestCase):
    def test_missing_submodule_file_message_is_actionable(self):
        # Emulate the meson error payload from a source tree missing submodule files.
        sample = "ERROR: Nonexistent build file 'data/submodules/meson.build'"

        match = re.search(r"Nonexistent build file '([^']*submodules[^']*)'", sample, re.IGNORECASE)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "data/submodules/meson.build")


if __name__ == "__main__":
    unittest.main()
