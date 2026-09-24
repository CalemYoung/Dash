import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from PyQt6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

from src import explorer_menu  # noqa: E402
from src.settings import Settings  # noqa: E402
from src.settings_editor import EXPLORER_MENU_KEY, SettingsEditorPanel  # noqa: E402
from src.single_instance import SHOW_MESSAGE, add_message, path_from_add_message  # noqa: E402


class FakeWinreg:
    """Just enough of winreg for explorer_menu, kept in a dict, so the
    tests never touch the real registry."""

    HKEY_CURRENT_USER = "HKCU"
    REG_SZ = 1

    def __init__(self):
        self.keys: dict[str, dict[str, str]] = {}

    class _Key:
        def __init__(self, store, path):
            self.store, self.path = store, path

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def CreateKey(self, root, path):
        self.keys.setdefault(path, {})
        return self._Key(self, path)

    def OpenKey(self, root, path):
        if path not in self.keys:
            raise FileNotFoundError(path)
        return self._Key(self, path)

    def SetValueEx(self, key, name, reserved, kind, value):
        self.keys[key.path][name] = value

    def QueryValueEx(self, key, name):
        values = self.keys[key.path]
        if name not in values:
            raise FileNotFoundError(name)
        return values[name], self.REG_SZ

    def DeleteKey(self, root, path):
        if any(other.startswith(path + "\\") for other in self.keys):
            raise PermissionError("key has subkeys")
        if path not in self.keys:
            raise FileNotFoundError(path)
        del self.keys[path]


class ExplorerMenuTests(unittest.TestCase):
    def setUp(self):
        self.registry = FakeWinreg()
        patches = [
            mock.patch.object(explorer_menu, "_winreg", return_value=self.registry),
            mock.patch.object(explorer_menu.sys, "platform", "win32"),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_enable_adds_the_item_for_files_and_folders(self):
        explorer_menu.enable(r"C:\Apps\Dash\Dash.exe")
        self.assertTrue(explorer_menu.is_enabled())
        for parent in explorer_menu.PARENT_KEYS:
            verb = self.registry.keys[rf"{parent}\DashAdd"]
            self.assertEqual(verb[""], "Add to Dash")
            self.assertEqual(verb["Icon"], r'"C:\Apps\Dash\Dash.exe",0')
            self.assertEqual(self.registry.keys[rf"{parent}\DashAdd\command"][""], r'"C:\Apps\Dash\Dash.exe" --add "%1"')

    def test_disable_removes_everything_and_can_run_twice(self):
        explorer_menu.enable(r"C:\Apps\Dash\Dash.exe")
        explorer_menu.disable()
        explorer_menu.disable()
        self.assertFalse(explorer_menu.is_enabled())
        self.assertEqual(self.registry.keys, {})

    def test_refresh_points_the_item_at_this_dash(self):
        explorer_menu.enable(r"C:\Old\Dash.exe")
        with mock.patch.object(explorer_menu, "available", return_value=True), \
                mock.patch.object(explorer_menu.sys, "executable", r"C:\New\Dash.exe"):
            explorer_menu.refresh()
        command = self.registry.keys[rf"{explorer_menu.PARENT_KEYS[0]}\DashAdd\command"][""]
        self.assertEqual(command, r'"C:\New\Dash.exe" --add "%1"')

    def test_refresh_leaves_a_disabled_item_off(self):
        with mock.patch.object(explorer_menu, "available", return_value=True):
            explorer_menu.refresh()
        self.assertEqual(self.registry.keys, {})


class ArgumentAndMessageTests(unittest.TestCase):
    def test_the_path_after_add_is_taken(self):
        self.assertEqual(explorer_menu.path_from_arguments(["--add", r"C:\My Files\a.txt"]), r"C:\My Files\a.txt")
        self.assertIsNone(explorer_menu.path_from_arguments(["--startup"]))
        self.assertIsNone(explorer_menu.path_from_arguments(["--add"]))

    def test_an_add_message_carries_the_path(self):
        path = r"C:\Users\Zoë\Café Notes.lnk"
        self.assertEqual(path_from_add_message(add_message(path)), path)
        self.assertIsNone(path_from_add_message(SHOW_MESSAGE))


class SettingsToggleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.settings_path = Path(self._tmp.name) / "settings.toml"
        patches = [
            mock.patch.object(explorer_menu, "is_enabled", return_value=False),
            mock.patch.object(explorer_menu, "available", return_value=True),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_the_toggle_starts_from_the_registry_and_saves_to_it(self):
        panel = SettingsEditorPanel(Settings(), self.settings_path)
        toggle = panel._controls[EXPLORER_MENU_KEY]
        self.assertFalse(toggle.isChecked())
        self.assertFalse(panel._is_dirty())
        toggle.setChecked(True)
        self.assertTrue(panel._is_dirty())
        with mock.patch.object(explorer_menu, "set_enabled") as set_enabled:
            panel._save()
        set_enabled.assert_called_once_with(True)

    def test_saving_other_settings_leaves_the_menu_alone(self):
        panel = SettingsEditorPanel(Settings(), self.settings_path)
        panel._controls["general.check_updates_on_startup"].toggle()
        with mock.patch.object(explorer_menu, "set_enabled") as set_enabled:
            panel._save()
        set_enabled.assert_not_called()

    def test_from_source_the_toggle_is_unavailable(self):
        with mock.patch.object(explorer_menu, "available", return_value=False):
            panel = SettingsEditorPanel(Settings(), self.settings_path)
        self.assertFalse(panel._controls[EXPLORER_MENU_KEY].isEnabled())


if __name__ == "__main__":
    unittest.main()
