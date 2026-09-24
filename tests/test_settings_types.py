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


class ExistingHotkeyTests(unittest.TestCase):
    def test_a_hotkey_already_chosen_is_kept(self):
        # Only new installs get the new default; an existing Alt+F stays.
        for hotkey in ("Alt+F", "Ctrl+Shift+K"):
            with self.subTest(hotkey=hotkey), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "settings.toml"
                path.write_text(f'[general]\nhotkey = "{hotkey}"\n', encoding="utf-8")
                self.assertEqual(Settings.load(path).general.hotkey, hotkey)


class UnopenedSettingsFileTests(unittest.TestCase):
    def test_defaults_are_never_saved_over_a_file_that_could_not_be_opened(self):
        from unittest import mock

        from src.settings import SettingsFileUnreadableError

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.toml"
            path.write_text('[general]\nhotkey = "Ctrl+Shift+K"\n', encoding="utf-8")
            with mock.patch.object(Path, "open", side_effect=PermissionError("locked")):
                settings = Settings.load(path)
            self.assertTrue(settings.read_failed)
            with self.assertRaises(SettingsFileUnreadableError):
                settings.save(path)
            self.assertIn("Ctrl+Shift+K", path.read_text(encoding="utf-8"))
