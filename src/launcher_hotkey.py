from PyQt6.QtCore import QObject, pyqtSignal
from PyHotKey import keyboard, Key


class HotkeyListener(QObject):
    triggered = pyqtSignal(bool)

    def __init__(self, hotkey="alt+f"):
        super().__init__()
        self.hotkey = hotkey
        self.hotkey_id = None
        self._parse_and_register()
        print(f"Hotkey Listener registered: {hotkey}")

    def _parse_and_register(self):
        """Parse hotkey string and register with PyHotKey"""
        parts = self.hotkey.lower().split("+")
        key_list = []

        # Map string names to PyHotKey Key objects
        key_map = {
            "alt": Key.alt_l,
            "ctrl": Key.ctrl_l,
            "control": Key.ctrl_l,
            "shift": Key.shift_l,
            "win": Key.cmd_l,
            "cmd": Key.cmd_l,
            # Special keys
            "enter": Key.enter,
            "return": Key.enter,
            "tab": Key.tab,
            "space": Key.space,
            "esc": Key.esc,
            "backspace": Key.backspace,
            "delete": Key.delete,
            # Arrow keys
            "up": Key.up,
            "down": Key.down,
            "left": Key.left,
            "right": Key.right,
            # Function keys
            "f1": Key.f1,
            "f2": Key.f2,
            "f3": Key.f3,
            "f4": Key.f4,
            "f5": Key.f5,
            "f6": Key.f6,
            "f7": Key.f7,
            "f8": Key.f8,
            "f9": Key.f9,
            "f10": Key.f10,
            "f11": Key.f11,
            "f12": Key.f12,
        }

        for part in parts:
            part = part.strip()
            if part in key_map:
                key_list.append(key_map[part])
            else:
                # Regular character key (letter, number, symbol)
                key_list.append(part)

        print(f"Registering hotkey: {key_list}")

        # Register the hotkey with PyHotKey
        # Returns hotkey ID, or -1 if already registered, or 0 if invalid
        self.hotkey_id = keyboard.register_hotkey(
            key_list,
            None,  # tap count (None for combination hotkey)
            self.on_activate,
        )

        if self.hotkey_id == -1:
            print("Warning: Hotkey already registered!")
        elif self.hotkey_id == 0:
            print("Error: Invalid hotkey parameters!")
        else:
            print(f"Hotkey registered with ID: {self.hotkey_id}")

        # Enable hotkey suppression (requires admin privileges)
        keyboard.suppress_hotkey = True

    def on_activate(self):
        """Called when hotkey is activated"""
        self.triggered.emit(True)

    def stop(self):
        """Stop listening for hotkey"""
        if self.hotkey_id and self.hotkey_id > 0:
            keyboard.unregister_hotkey_by_id(self.hotkey_id)
        keyboard.suppress_hotkey = False

    def update_hotkey(self, hotkey):
        """Replace the registered hotkey without replacing the listener."""
        self.stop()
        self.hotkey = hotkey
        self._parse_and_register()
