"""The optional "Add to Dash" item in File Explorer's right-click menu.

It is a plain shell verb under HKEY_CURRENT_USER, so it needs no admin
rights and affects only this user. It runs `Dash.exe --add "<path>"`; a
running Dash receives the path over the single-instance pipe and opens the
new-command editor with it as the target, as dropping the file would.

On Windows 11 verbs like this appear under "Show more options". Only a
packaged (MSIX, signed) shell extension can reach the short menu.

The registry is the only record of whether the item is on: the installer's
checkbox and the Settings toggle both write it, so neither can disagree
with a copy kept in settings.toml.
"""
import logging
import sys

log = logging.getLogger(__name__)

ADD_FLAG = "--add"
LABEL = "Add to Dash"
VERB = "DashAdd"
# Every file (shortcuts included) and every folder.
PARENT_KEYS = (r"Software\Classes\*\shell", r"Software\Classes\Directory\shell")


def available() -> bool:
    """Only the installed Dash.exe can be put in the menu; from source,
    sys.executable is Python."""
    return sys.platform == "win32" and hasattr(sys, "_MEIPASS")


def command_line(executable: str) -> str:
    return f'"{executable}" {ADD_FLAG} "%1"'


def _winreg():
    import winreg

    return winreg


def _registered_command(parent: str) -> str | None:
    winreg = _winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{parent}\{VERB}\command") as key:
            value, _kind = winreg.QueryValueEx(key, "")
            return str(value)
    except OSError:
        return None


def is_enabled() -> bool:
    if sys.platform != "win32":
        return False
    return any(_registered_command(parent) is not None for parent in PARENT_KEYS)


def enable(executable: str | None = None) -> None:
    """Add the menu item. Raises OSError if the registry refuses."""
    winreg = _winreg()
    executable = executable or sys.executable
    for parent in PARENT_KEYS:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{parent}\{VERB}") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, LABEL)
            winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, f'"{executable}",0')
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{parent}\{VERB}\command") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command_line(executable))
    log.info("Added %r to the File Explorer menu", LABEL)


def disable() -> None:
    """Remove the menu item. Raises OSError if the registry refuses."""
    winreg = _winreg()
    for parent in PARENT_KEYS:
        for subkey in (rf"{parent}\{VERB}\command", rf"{parent}\{VERB}"):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subkey)
            except FileNotFoundError:
                pass
    log.info("Removed %r from the File Explorer menu", LABEL)


def set_enabled(enabled: bool) -> None:
    enable() if enabled else disable()


def refresh() -> None:
    """Point an existing menu item at this Dash.exe, in case Dash was
    reinstalled somewhere else. Does nothing when the item is off."""
    if not available() or not is_enabled():
        return
    expected = command_line(sys.executable)
    if all(_registered_command(parent) == expected for parent in PARENT_KEYS):
        return
    try:
        enable(sys.executable)
    except OSError:
        log.warning("Could not update the File Explorer menu item", exc_info=True)


def path_from_arguments(arguments: list[str]) -> str | None:
    """The path after --add on the command line, or None."""
    if ADD_FLAG not in arguments:
        return None
    index = arguments.index(ADD_FLAG)
    if index + 1 >= len(arguments) or not arguments[index + 1].strip():
        return None
    return arguments[index + 1]
