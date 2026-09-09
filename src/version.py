"""Dash version helpers.

``build/installer/version.txt`` is the single source of truth for the app
version. It is committed, read at runtime, and stamped into the installer and
exe metadata at build time.
"""
import sys
from pathlib import Path

VERSION_RELATIVE_PATH = Path("build") / "installer" / "version.txt"


def _version_root() -> Path:
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent


def current_version() -> str:
    """Return the app version, or ``"0.0.0"`` if the version file is missing."""
    try:
        version = (_version_root() / VERSION_RELATIVE_PATH).read_text(encoding="utf-8").strip()
    except OSError:
        version = ""
    return version or "0.0.0"


def version_key(value: str) -> tuple[int, ...] | None:
    """Turn ``"v2.10.1"`` into ``(2, 10, 1)``; ``None`` if it is not a version."""
    core = value.strip().lstrip("vV").split("-", 1)[0].split("+", 1)[0]
    parts = core.split(".")
    if not parts or any(not part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts) + (0,) * max(0, 3 - len(parts))


def is_newer_version(latest: str, current: str) -> bool:
    latest_key = version_key(latest)
    current_key = version_key(current)
    if latest_key is None or current_key is None:
        return False
    width = max(len(latest_key), len(current_key))
    return latest_key + (0,) * (width - len(latest_key)) > current_key + (0,) * (width - len(current_key))
