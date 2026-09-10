"""Which browser opens a URL.

Windows keeps a registry of installed browsers (the same list the Default
Apps page shows), each with a display name and the command that starts it.
Dash reads that list so the user can pick a browser by its real name, and
identifies a choice by the registry key so it survives reinstalls and moves.

"default" always means whatever Windows would use.
"""
import os
import shlex
import subprocess
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BROWSER = "default"

# Registered but not something anyone wants URLs sent to any more.
_IGNORED_KEYS = {"IEXPLORE.EXE"}


@dataclass(frozen=True)
class Browser:
    key: str  # registry subkey, the stored identifier
    name: str  # display name, e.g. "Google Chrome"
    executable: str


def _split_command(command: str) -> str:
    """Executable path out of a registry command string like '"C:\\...\\x.exe" "%1"'."""
    text = command.strip()
    if text.startswith('"'):
        return text[1:].split('"', 1)[0]
    try:
        return shlex.split(text, posix=False)[0].strip('"')
    except ValueError:
        return text.split(" ", 1)[0]


def installed_browsers() -> list[Browser]:
    """Browsers registered with Windows whose executable exists, in registry order."""
    if sys.platform != "win32":
        return []
    import winreg

    found: dict[str, Browser] = {}
    hives = (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE)
    views = [getattr(winreg, flag, 0) for flag in ("KEY_WOW64_64KEY", "KEY_WOW64_32KEY")]
    for hive in hives:
        for view in views:
            try:
                root = winreg.OpenKey(hive, r"SOFTWARE\Clients\StartMenuInternet", 0, winreg.KEY_READ | view)
            except OSError:
                continue
            with root:
                index = 0
                while True:
                    try:
                        key = winreg.EnumKey(root, index)
                    except OSError:
                        break
                    index += 1
                    if key in found or key in _IGNORED_KEYS:
                        continue
                    try:
                        with winreg.OpenKey(root, key) as entry:
                            name = str(winreg.QueryValue(entry, None) or key).strip()
                        with winreg.OpenKey(root, key + r"\shell\open\command") as command_key:
                            executable = _split_command(str(winreg.QueryValue(command_key, None)))
                    except OSError:
                        continue
                    executable = os.path.expandvars(executable)
                    if Path(executable).is_file():
                        found[key] = Browser(key, name, executable)
    return list(found.values())


def find_browser(key: str | None) -> Browser | None:
    if not key or key == DEFAULT_BROWSER:
        return None
    for browser in installed_browsers():
        if browser.key == key:
            return browser
    return None


def open_url(url: str, browser_key: str | None = None) -> None:
    """Open `url` in the chosen browser, or the system default.

    A chosen browser that is no longer installed falls back to the default:
    the user asked for the page, not for an error about a missing program.
    Raises OSError only if nothing at all could open it.
    """
    browser = find_browser(browser_key)
    if browser is not None:
        subprocess.Popen([browser.executable, url], close_fds=True)
        return
    if not webbrowser.open(url):
        raise OSError(f"No web browser could be opened for {url}")
