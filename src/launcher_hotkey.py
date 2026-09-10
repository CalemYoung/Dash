import itertools
import sys

from PyQt6.QtCore import QObject, pyqtSignal
from PyHotKey import keyboard, Key


def _running_as_admin() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


# Every physical key the hook may report for a modifier name. Right Alt is
# delivered as Key.alt_gr (pynput maps its virtual key to that member), so
# Key.alt_r would never match an actual key press.
MODIFIER_VARIANTS = {
    "alt": (Key.alt_l, Key.alt_gr, Key.alt),
    "ctrl": (Key.ctrl_l, Key.ctrl_r, Key.ctrl),
    "control": (Key.ctrl_l, Key.ctrl_r, Key.ctrl),
    "shift": (Key.shift_l, Key.shift_r, Key.shift),
    "win": (Key.cmd, Key.cmd_r),
    "cmd": (Key.cmd, Key.cmd_r),
    "meta": (Key.cmd, Key.cmd_r),
}

SPECIAL_KEYS = {
    "enter": Key.enter,
    "return": Key.enter,
    "tab": Key.tab,
    "space": Key.space,
    "esc": Key.esc,
    "escape": Key.esc,
    "backspace": Key.backspace,
    "delete": Key.delete,
    "up": Key.up,
    "down": Key.down,
    "left": Key.left,
    "right": Key.right,
    **{f"f{n}": getattr(Key, f"f{n}") for n in range(1, 13)},
}


class HotkeyListener(QObject):
    triggered = pyqtSignal(bool)

    def __init__(self, hotkey="alt+f"):
        super().__init__()
        self.hotkey = hotkey
        self.hotkey_ids = []
        self._parse_and_register()
        print(f"Hotkey Listener registered: {hotkey}")

    def _parse_and_register(self):
        """Register the hotkey string once per physical modifier combination.

        Settings store a side-agnostic "Alt+G", but the keyboard hook reports
        the left and right keys as different keys, and remote-desktop and
        remapping tools can send the generic code for either side. PyHotKey
        matches keys by name, so a single registration would bind exactly one
        physical key. Register every combination instead; they all fire the
        same callback.
        """
        parts = [part.strip() for part in self.hotkey.lower().split("+") if part.strip()]
        alternatives = [MODIFIER_VARIANTS.get(part) or (SPECIAL_KEYS.get(part, part),) for part in parts]

        combos = [list(combo) for combo in itertools.product(*alternatives)]
        # AltGr (right Alt on many non-US layouts) arrives as a fake left Ctrl
        # press followed by right Alt, so an Alt-only hotkey needs a Ctrl variant.
        if "alt" in parts and not ({"ctrl", "control"} & set(parts)):
            combos += [[Key.ctrl_l] + combo for combo in combos if Key.alt_gr in combo]

        print(f"Registering hotkey: {parts} ({len(combos)} key combinations)")
        self.hotkey_ids = []
        for combo in combos:
            # Returns a hotkey ID, -1 if already registered, or 0 if invalid
            hotkey_id = keyboard.register_hotkey(combo, None, self.on_activate)
            if hotkey_id > 0:
                self.hotkey_ids.append(hotkey_id)
        if not self.hotkey_ids:
            print("Error: hotkey could not be registered!")

        # Suppressing the hotkey keeps the keystroke from reaching the app that
        # had focus. PyHotKey can only do that with an elevated process; without
        # it the flag is silently ignored, so say so once instead of leaving the
        # behaviour difference unexplained.
        keyboard.suppress_hotkey = True
        if not _running_as_admin():
            print("Hotkey suppression unavailable: Dash is not running elevated, so the hotkey also reaches the focused app.")

    def on_activate(self):
        """Called when hotkey is activated"""
        self.triggered.emit(True)

    def stop(self):
        """Stop listening for hotkey"""
        for hotkey_id in self.hotkey_ids:
            keyboard.unregister_hotkey_by_id(hotkey_id)
        self.hotkey_ids = []
        keyboard.suppress_hotkey = False

    def update_hotkey(self, hotkey):
        """Replace the registered hotkey without replacing the listener."""
        self.stop()
        self.hotkey = hotkey
        self._parse_and_register()
