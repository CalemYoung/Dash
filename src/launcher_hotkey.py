"""The global hotkey that opens Dash.

On Windows the hotkey is registered with RegisterHotKey, which works without
admin rights and keeps the keystroke from reaching the app that had focus.
Windows delivers it as a WM_HOTKEY message, picked up by a native event
filter. When RegisterHotKey refuses (another app already owns the
combination, or it is one Windows keeps for itself), Dash falls back to the
PyHotKey keyboard hook, which can share a key with other apps but can only
hide the keystroke from them when Dash runs elevated.
"""
import itertools
import logging
import sys

from PyQt6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, QTimer, pyqtSignal

log = logging.getLogger(__name__)

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

MODIFIER_FLAGS = {
    "alt": MOD_ALT,
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "windows": MOD_WIN,
    "cmd": MOD_WIN,
    "meta": MOD_WIN,
    "super": MOD_WIN,
}

# Modifiers in the order people write them, for display.
_MODIFIER_ORDER = ((MOD_CONTROL, "Ctrl"), (MOD_ALT, "Alt"), (MOD_SHIFT, "Shift"), (MOD_WIN, "Win"))

VIRTUAL_KEYS = {
    "space": 0x20,
    "enter": 0x0D,
    "return": 0x0D,
    "tab": 0x09,
    "esc": 0x1B,
    "escape": 0x1B,
    "backspace": 0x08,
    "delete": 0x2E,
    "del": 0x2E,
    "insert": 0x2D,
    "ins": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pgup": 0x21,
    "pagedown": 0x22,
    "pgdown": 0x22,
    "pgdn": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "pause": 0x13,
    "printscreen": 0x2C,
    "print": 0x2C,
    ";": 0xBA,
    "=": 0xBB,
    ",": 0xBC,
    "-": 0xBD,
    ".": 0xBE,
    "/": 0xBF,
    "`": 0xC0,
    "[": 0xDB,
    "\\": 0xDC,
    "]": 0xDD,
    "'": 0xDE,
    **{f"f{n}": 0x70 + n - 1 for n in range(1, 25)},
    **{chr(c).lower(): c for c in range(ord("A"), ord("Z") + 1)},
    **{str(d): 0x30 + d for d in range(10)},
}

_KEY_NAMES = {
    "space": "Space", "enter": "Enter", "return": "Enter", "tab": "Tab", "esc": "Esc", "escape": "Esc",
    "backspace": "Backspace", "delete": "Delete", "del": "Delete", "insert": "Insert", "ins": "Insert",
    "home": "Home", "end": "End", "pageup": "PageUp", "pgup": "PageUp", "pagedown": "PageDown",
    "pgdown": "PageDown", "pgdn": "PageDown", "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "pause": "Pause", "printscreen": "PrintScreen", "print": "PrintScreen",
}


class Hotkey:
    """A parsed hotkey: RegisterHotKey modifier flags plus a virtual key."""

    def __init__(self, modifiers: int, vk: int, key: str):
        self.modifiers = modifiers
        self.vk = vk
        self.key = key

    def __eq__(self, other):
        return isinstance(other, Hotkey) and (self.modifiers, self.vk) == (other.modifiers, other.vk)

    def __repr__(self):
        return f"Hotkey({self.display()!r})"

    @property
    def register_flags(self) -> int:
        return self.modifiers | MOD_NOREPEAT

    def display(self) -> str:
        names = [label for flag, label in _MODIFIER_ORDER if self.modifiers & flag]
        key = self.key
        if key in _KEY_NAMES:
            key = _KEY_NAMES[key]
        elif len(key) == 1 or (key.startswith("f") and key[1:].isdigit()):
            key = key.upper()
        return "+".join([*names, key])


def _split(hotkey: str) -> list[str]:
    text = str(hotkey or "").strip().lower()
    parts = [part.strip() for part in text.split("+")]
    # "Ctrl++" names the plus key itself, which is "=" on US layouts.
    if text.endswith("++"):
        parts = [part for part in parts if part] + ["="]
    return [part for part in parts if part]


def parse_hotkey(hotkey: str) -> Hotkey | None:
    """Turn "Alt+Space", "Ctrl+Shift+K", "Win+F1" into a :class:`Hotkey`.

    Needs exactly one key that is not a modifier; None otherwise.
    """
    modifiers = 0
    keys = []
    for part in _split(hotkey):
        if part in MODIFIER_FLAGS:
            modifiers |= MODIFIER_FLAGS[part]
        elif part in VIRTUAL_KEYS:
            keys.append(part)
        else:
            return None
    if len(keys) != 1:
        return None
    return Hotkey(modifiers, VIRTUAL_KEYS[keys[0]], keys[0])


def format_hotkey(hotkey: str) -> str:
    """The hotkey written the usual way ("alt+space" -> "Alt+Space")."""
    parsed = parse_hotkey(hotkey)
    return parsed.display() if parsed else str(hotkey or "").strip()


_MENU_LETTERS = {"f": "File", "e": "Edit", "v": "View", "h": "Help", "t": "Tools", "w": "Window", "i": "Insert", "o": "Format"}

_EXACT_WARNINGS = {
    (MOD_ALT, "tab"): "Windows uses Alt+Tab to switch between windows. Choose a different key.",
    (MOD_ALT, "f4"): "Alt+F4 closes the active window, so apps could no longer be closed with it.",
    (MOD_ALT, "esc"): "Windows uses Alt+Esc to cycle through windows.",
    (MOD_CONTROL, "space"): (
        "Ctrl+Space switches the input method on some keyboards and shows suggestions in many editors."
    ),
    (MOD_CONTROL, "esc"): "Windows uses Ctrl+Esc to open the Start menu.",
    (MOD_CONTROL | MOD_SHIFT, "esc"): "Windows uses Ctrl+Shift+Esc to open Task Manager.",
    (MOD_CONTROL | MOD_ALT, "delete"): "Ctrl+Alt+Delete is reserved by Windows.",
    (MOD_WIN, "l"): "Windows uses Win+L to lock the PC. Choose a different key.",
    (MOD_WIN, "d"): "Windows uses Win+D to show the desktop.",
    (MOD_WIN, "e"): "Windows uses Win+E to open File Explorer.",
    (MOD_WIN, "r"): "Windows uses Win+R to open Run.",
    (MOD_WIN, "s"): "Windows uses Win+S to open Search.",
    (MOD_WIN, "i"): "Windows uses Win+I to open Settings.",
    (MOD_WIN, "tab"): "Windows uses Win+Tab to open Task View.",
    (MOD_WIN, "space"): "Windows uses Win+Space to switch keyboard layouts.",
    (MOD_WIN, "v"): "Windows uses Win+V to open clipboard history.",
    (MOD_WIN, "x"): "Windows uses Win+X to open the quick link menu.",
}

_CTRL_ACTIONS = {
    "c": "Copy", "v": "Paste", "x": "Cut", "z": "Undo", "y": "Redo", "a": "Select All", "s": "Save",
    "f": "Find", "p": "Print", "n": "New", "o": "Open", "w": "Close", "t": "New Tab",
}


def hotkey_conflict_warning(hotkey: str) -> str | None:
    """A short, plain warning when `hotkey` clashes with a well-known
    shortcut, for Settings to show beside the hotkey field; None if fine."""
    parsed = parse_hotkey(hotkey)
    if parsed is None:
        return "Dash doesn't recognize this hotkey. Use one or more of Ctrl, Alt, Shift or Win plus a key."
    mods, key, shown = parsed.modifiers, parsed.key, parsed.display()

    exact = _EXACT_WARNINGS.get((mods, key))
    if exact:
        return exact
    if mods == MOD_CONTROL and key in _CTRL_ACTIONS:
        return f"{shown} is {_CTRL_ACTIONS[key]} in almost every app, so it would stop working there."
    if mods == MOD_ALT and len(key) == 1 and key.isalpha():
        menu = _MENU_LETTERS.get(key)
        example = f"opens the {menu} menu" if menu else "opens menus"
        return f"{shown} {example} in many apps. Those apps won't get it while Dash is running."
    if mods == MOD_WIN and (len(key) == 1):
        return f"Windows uses {shown} for one of its own shortcuts, so it may not work for Dash."
    if mods in (0, MOD_SHIFT):
        if key.startswith("f") and key[1:].isdigit():
            if key == "f1" and mods == 0:
                return "F1 opens Help in many apps, so it would stop working there."
            return None
        return f"{shown} on its own is used for typing. Add Ctrl, Alt or Win."
    return None


# -- PyHotKey fallback --------------------------------------------------------


def _running_as_admin() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _pyhotkey():
    """PyHotKey's keyboard and Key, imported only when the hook is needed:
    importing it starts a low-level keyboard hook."""
    from PyHotKey import Key, keyboard

    return keyboard, Key


def _modifier_variants(Key):
    # Every physical key the hook may report for a modifier name. Right Alt is
    # delivered as Key.alt_gr (pynput maps its virtual key to that member), so
    # Key.alt_r would never match an actual key press.
    return {
        "alt": (Key.alt_l, Key.alt_gr, Key.alt),
        "ctrl": (Key.ctrl_l, Key.ctrl_r, Key.ctrl),
        "control": (Key.ctrl_l, Key.ctrl_r, Key.ctrl),
        "shift": (Key.shift_l, Key.shift_r, Key.shift),
        "win": (Key.cmd, Key.cmd_r),
        "cmd": (Key.cmd, Key.cmd_r),
        "meta": (Key.cmd, Key.cmd_r),
    }


def _special_keys(Key):
    return {
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


# -- Win32 RegisterHotKey -----------------------------------------------------


class _WmHotkeyFilter(QAbstractNativeEventFilter):
    """Hands WM_HOTKEY messages for one hotkey id to a callback."""

    def __init__(self, hotkey_id: int, callback):
        super().__init__()
        self.hotkey_id = hotkey_id
        self._callback = callback

    def nativeEventFilter(self, event_type, message):
        try:
            if bytes(event_type) in (b"windows_generic_MSG", b"windows_dispatcher_MSG") and message:
                from ctypes import wintypes

                msg = wintypes.MSG.from_address(int(message))
                if msg.message == WM_HOTKEY and msg.wParam == self.hotkey_id:
                    self._callback()
                    return True, 0
        except Exception:
            log.debug("Hotkey filter failed", exc_info=True)
        return False, 0


def _user32():
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.UnregisterHotKey.restype = wintypes.BOOL
    return user32


class HotkeyListener(QObject):
    triggered = pyqtSignal(bool)
    # Plain-language reason the hotkey could not be registered the normal
    # way, for a tray notification. Also kept in `registration_error`.
    registrationFailed = pyqtSignal(str)

    _next_id = 1  # RegisterHotKey ids for apps are 0x0000-0xBFFF

    def __init__(self, hotkey="alt+space"):
        super().__init__()
        self.hotkey = hotkey
        self.hotkey_ids = []
        self.registration_error: str | None = None
        self.uses_system_hotkey = False
        self._native_id: int | None = None
        self._native_filter: _WmHotkeyFilter | None = None
        self._register()
        log.info("Hotkey listener registered: %s", hotkey)

    # -- registration -------------------------------------------------------

    def _register(self):
        self.registration_error = None
        self.uses_system_hotkey = False
        parsed = parse_hotkey(self.hotkey)
        if sys.platform == "win32" and parsed is not None:
            if self._register_native(parsed):
                self.uses_system_hotkey = True
                return
            self._set_error(f"{parsed.display()} is already used by another app, so it may not open Dash reliably. Choose a different key in Settings.")
        elif parsed is None:
            self._set_error(f"Dash doesn't recognize the hotkey {self.hotkey}. Choose a different key in Settings.")
        self._parse_and_register()

    def _set_error(self, message: str):
        self.registration_error = message
        log.warning("%s", message)
        # Deferred so a listener created before anything is connected
        # still gets its message delivered.
        QTimer.singleShot(0, lambda: self._emit_error(message))

    def _emit_error(self, message: str):
        if self.registration_error == message:
            self.registrationFailed.emit(message)

    def _register_native(self, parsed: Hotkey) -> bool:
        app = QCoreApplication.instance()
        if app is None:
            return False
        try:
            user32 = _user32()
            hotkey_id = HotkeyListener._next_id
            HotkeyListener._next_id = HotkeyListener._next_id % 0xBFFF + 1
            if not user32.RegisterHotKey(None, hotkey_id, parsed.register_flags, parsed.vk):
                import ctypes

                log.info("RegisterHotKey failed for %s (error %s)", parsed.display(), ctypes.get_last_error())
                return False
        except Exception:
            log.warning("RegisterHotKey unavailable", exc_info=True)
            return False
        self._native_id = hotkey_id
        self._native_filter = _WmHotkeyFilter(hotkey_id, self.on_activate)
        app.installNativeEventFilter(self._native_filter)
        return True

    def _parse_and_register(self):
        """Register the hotkey string once per physical modifier combination.

        Settings store a side-agnostic "Alt+G", but the keyboard hook reports
        the left and right keys as different keys, and remote-desktop and
        remapping tools can send the generic code for either side. PyHotKey
        matches keys by name, so a single registration would bind exactly one
        physical key. Register every combination instead; they all fire the
        same callback.
        """
        try:
            keyboard, Key = _pyhotkey()
        except Exception:
            log.error("The keyboard hook is unavailable, so the hotkey is not registered.", exc_info=True)
            return
        modifier_variants = _modifier_variants(Key)
        special_keys = _special_keys(Key)
        parts = [part.strip() for part in self.hotkey.lower().split("+") if part.strip()]
        alternatives = [modifier_variants.get(part) or (special_keys.get(part, part),) for part in parts]

        combos = [list(combo) for combo in itertools.product(*alternatives)]
        # AltGr (right Alt on many non-US layouts) arrives as a fake left Ctrl
        # press followed by right Alt, so an Alt-only hotkey needs a Ctrl variant.
        if "alt" in parts and not ({"ctrl", "control"} & set(parts)):
            combos += [[Key.ctrl_l] + combo for combo in combos if Key.alt_gr in combo]

        log.info("Registering hotkey hook: %s (%d key combinations)", parts, len(combos))
        self.hotkey_ids = []
        for combo in combos:
            # Returns a hotkey ID, -1 if already registered, or 0 if invalid
            hotkey_id = keyboard.register_hotkey(combo, None, self.on_activate)
            if hotkey_id > 0:
                self.hotkey_ids.append(hotkey_id)
        if not self.hotkey_ids:
            log.error("The hotkey %s could not be registered.", self.hotkey)

        # Suppressing the hotkey keeps the keystroke from reaching the app that
        # had focus. PyHotKey can only do that with an elevated process; without
        # it the flag is silently ignored, so say so once instead of leaving the
        # behaviour difference unexplained.
        keyboard.suppress_hotkey = True
        if not _running_as_admin():
            log.info("Hotkey suppression unavailable: Dash is not running elevated, so the hotkey also reaches the focused app.")

    # -- public API ---------------------------------------------------------

    def on_activate(self):
        """Called when hotkey is activated"""
        self.triggered.emit(True)

    def stop(self):
        """Stop listening for hotkey"""
        if self._native_id is not None:
            try:
                _user32().UnregisterHotKey(None, self._native_id)
            except Exception:
                log.debug("UnregisterHotKey failed", exc_info=True)
            self._native_id = None
        if self._native_filter is not None:
            app = QCoreApplication.instance()
            if app is not None:
                app.removeNativeEventFilter(self._native_filter)
            self._native_filter = None
        if self.hotkey_ids:
            keyboard, _ = _pyhotkey()
            for hotkey_id in self.hotkey_ids:
                keyboard.unregister_hotkey_by_id(hotkey_id)
            self.hotkey_ids = []
            keyboard.suppress_hotkey = False
        self.uses_system_hotkey = False

    def update_hotkey(self, hotkey):
        """Replace the registered hotkey without replacing the listener."""
        self.stop()
        self.hotkey = hotkey
        self._register()
