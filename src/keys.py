"""Shortcut strings as stored in settings, turned into Qt key sequences and
readable labels. Shared by the launcher and the command editor."""
from PyQt6.QtGui import QKeySequence

_DISPLAY_NAMES = {
    "alt": "Alt",
    "ctrl": "Ctrl",
    "control": "Ctrl",
    "shift": "Shift",
    "win": "Win",
    "cmd": "Cmd",
    "meta": "Win",
    "return": "Enter",
    "enter": "Enter",
    "space": "Space",
    "esc": "Esc",
    "escape": "Esc",
}


def key_sequences(shortcut: str) -> list[QKeySequence]:
    """Key sequences for a shortcut string, accepting both Enter keys.

    Qt reports the main Return key and the numeric keypad Enter as different
    keys, so "Ctrl+Return" alone would never match keypad Enter. A shortcut
    written with either name is registered for both.
    """
    text = shortcut.strip()
    variants = [text]
    lowered = text.lower()
    if lowered.endswith("return"):
        variants.append(text[: -len("return")] + "Enter")
    elif lowered.endswith("enter"):
        variants.append(text[: -len("enter")] + "Return")
    sequences = [QKeySequence(variant) for variant in variants]
    return [sequence for sequence in sequences if not sequence.isEmpty()]


def format_shortcut(shortcut: str) -> str:
    """'ctrl+return' -> 'Ctrl + Enter', for hints and the About box."""
    return " + ".join(_DISPLAY_NAMES.get(part.strip().lower(), part.strip().upper()) for part in shortcut.split("+"))
