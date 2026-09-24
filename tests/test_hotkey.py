import ctypes
import unittest
from unittest import mock

from PyQt6.QtCore import QCoreApplication

from src import launcher_hotkey
from src.launcher_hotkey import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_NOREPEAT,
    MOD_SHIFT,
    MOD_WIN,
    WM_HOTKEY,
    HotkeyListener,
    format_hotkey,
    hotkey_conflict_warning,
    parse_hotkey,
)


class ParseTests(unittest.TestCase):
    def test_common_hotkeys(self):
        cases = {
            "Alt+Space": (MOD_ALT, 0x20),
            "alt+space": (MOD_ALT, 0x20),
            "Ctrl+Shift+K": (MOD_CONTROL | MOD_SHIFT, ord("K")),
            "Control + Alt + 1": (MOD_CONTROL | MOD_ALT, ord("1")),
            "Win+Space": (MOD_WIN, 0x20),
            "Meta+F": (MOD_WIN, ord("F")),
            "F12": (0, 0x7B),
            "Shift+F1": (MOD_SHIFT, 0x70),
            "Ctrl+F24": (MOD_CONTROL, 0x87),
            "Alt+`": (MOD_ALT, 0xC0),
            "Ctrl+PageDown": (MOD_CONTROL, 0x22),
            "Ctrl++": (MOD_CONTROL, 0xBB),
        }
        for text, (mods, vk) in cases.items():
            with self.subTest(text=text):
                parsed = parse_hotkey(text)
                self.assertIsNotNone(parsed)
                self.assertEqual((parsed.modifiers, parsed.vk), (mods, vk))
                self.assertEqual(parsed.register_flags, mods | MOD_NOREPEAT)

    def test_invalid_hotkeys(self):
        for text in ("", "Alt", "Ctrl+Shift", "Alt+A+B", "Alt+Banana", None):
            with self.subTest(text=text):
                self.assertIsNone(parse_hotkey(text))

    def test_display(self):
        self.assertEqual(format_hotkey("shift+ctrl+k"), "Ctrl+Shift+K")
        self.assertEqual(format_hotkey("alt+space"), "Alt+Space")
        self.assertEqual(format_hotkey("win+f5"), "Win+F5")
        self.assertEqual(format_hotkey("nonsense"), "nonsense")


class ConflictWarningTests(unittest.TestCase):
    def test_well_known_clashes_are_explained(self):
        for hotkey, fragment in {
            "Alt+F": "File menu",
            "Alt+E": "Edit menu",
            "Alt+Q": "opens menus",
            "Alt+Tab": "switch between windows",
            "Alt+F4": "closes the active window",
            "Win+L": "lock",
            "Win+D": "desktop",
            "Win+K": "Windows uses Win+K",
            "Ctrl+C": "Copy",
            "Ctrl+V": "Paste",
            "Ctrl+Z": "Undo",
            "Ctrl+Space": "input method",
            "K": "used for typing",
            "Shift+K": "used for typing",
            "F1": "Help",
            "Alt+Banana": "doesn't recognize",
        }.items():
            with self.subTest(hotkey=hotkey):
                warning = hotkey_conflict_warning(hotkey)
                self.assertIsNotNone(warning)
                self.assertIn(fragment, warning)
                self.assertNotIn("—", warning)

    def test_uncommon_combinations_are_fine(self):
        for hotkey in ("Alt+Space", "Ctrl+Shift+K", "Ctrl+Alt+D", "Win+Shift+Space", "F9", "Ctrl+`"):
            with self.subTest(hotkey=hotkey):
                self.assertIsNone(hotkey_conflict_warning(hotkey))


class FakeMSG(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint), ("wParam", ctypes.c_size_t),
                ("lParam", ctypes.c_ssize_t), ("time", ctypes.c_uint32), ("pt_x", ctypes.c_long), ("pt_y", ctypes.c_long)]


class ListenerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.user32 = mock.Mock()
        self.keyboard = mock.Mock()
        self.keyboard.register_hotkey.return_value = 7
        patches = [
            mock.patch.object(launcher_hotkey.sys, "platform", "win32"),
            mock.patch.object(launcher_hotkey, "_user32", return_value=self.user32),
            mock.patch.object(launcher_hotkey, "_pyhotkey", return_value=(self.keyboard, mock.MagicMock())),
            mock.patch.object(launcher_hotkey, "_running_as_admin", return_value=False),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_register_hotkey_is_used_when_available(self):
        self.user32.RegisterHotKey.return_value = 1
        listener = HotkeyListener("Alt+Space")
        self.addCleanup(listener.stop)
        self.assertTrue(listener.uses_system_hotkey)
        self.assertIsNone(listener.registration_error)
        _, hotkey_id, flags, vk = self.user32.RegisterHotKey.call_args.args
        self.assertEqual((flags, vk), (MOD_ALT | MOD_NOREPEAT, 0x20))
        self.keyboard.register_hotkey.assert_not_called()

        fired = []
        listener.triggered.connect(fired.append)
        msg = FakeMSG(None, WM_HOTKEY, hotkey_id, 0, 0, 0, 0)
        with mock.patch("ctypes.wintypes.MSG", FakeMSG):
            handled, _ = listener._native_filter.nativeEventFilter(b"windows_dispatcher_MSG", ctypes.addressof(msg))
            other = FakeMSG(None, WM_HOTKEY, hotkey_id + 1, 0, 0, 0, 0)
            ignored, _ = listener._native_filter.nativeEventFilter(b"windows_dispatcher_MSG", ctypes.addressof(other))
        self.assertTrue(handled)
        self.assertFalse(ignored)
        self.assertEqual(fired, [True])

    def test_a_taken_hotkey_falls_back_to_the_hook_and_says_why(self):
        self.user32.RegisterHotKey.return_value = 0
        listener = HotkeyListener("alt+space")
        self.addCleanup(listener.stop)
        messages = []
        listener.registrationFailed.connect(messages.append)
        self.app.processEvents()
        expected = "Alt+Space is already used by another app, so it may not open Dash reliably. Choose a different key in Settings."
        self.assertEqual(listener.registration_error, expected)
        self.assertEqual(messages, [expected])
        self.assertFalse(listener.uses_system_hotkey)
        self.assertTrue(self.keyboard.register_hotkey.called)

    def test_update_and_stop_release_the_registration(self):
        self.user32.RegisterHotKey.return_value = 1
        listener = HotkeyListener("Alt+Space")
        first_id = self.user32.RegisterHotKey.call_args.args[1]
        listener.update_hotkey("Ctrl+Shift+K")
        self.user32.UnregisterHotKey.assert_called_with(None, first_id)
        self.assertEqual(self.user32.RegisterHotKey.call_args.args[2:], (MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, ord("K")))
        listener.stop()
        self.assertIsNone(listener._native_filter)
        self.assertEqual(self.user32.UnregisterHotKey.call_count, 2)


if __name__ == "__main__":
    unittest.main()
