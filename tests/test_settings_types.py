import tempfile
import unittest
from pathlib import Path

from src.settings import Settings


class SettingsValueTypeTests(unittest.TestCase):
    def _load(self, text: str) -> Settings:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.toml"
            path.write_text(text, encoding="utf-8")
            return Settings.load(path)

    def test_a_value_of_the_wrong_type_falls_back_to_the_default(self):
        settings = self._load('[ui]\nprogram_width = "abc"\nshow_clock = 1\nwindow_opacity = 1\n')
        self.assertEqual(settings.ui.program_width, Settings().ui.program_width)
        self.assertEqual(settings.ui.show_clock, Settings().ui.show_clock)
        self.assertEqual(settings.ui.window_opacity, 1.0)
        self.assertTrue(any("program_width" in warning for warning in settings.load_warnings))

    def test_a_section_that_is_not_a_table_is_ignored(self):
        settings = self._load("general = 5\n")
        self.assertEqual(settings.general.hotkey, Settings().general.hotkey)
        self.assertTrue(settings.load_warnings)

    def test_the_old_default_opacity_becomes_opaque(self):
        self.assertEqual(self._load("[ui]\nwindow_opacity = 0.97\n").ui.window_opacity, 1.0)
        self.assertEqual(self._load("[ui]\nwindow_opacity = 0.8\n").ui.window_opacity, 0.8)


if __name__ == "__main__":
    unittest.main()
