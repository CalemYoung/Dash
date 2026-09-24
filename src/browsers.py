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


# The registry scan is kept until refresh_installed_browsers(), so opening a
# URL does not read the registry and check every browser's exe each time.
_browser_cache: list[Browser] | None = None


def installed_browsers(refresh: bool = False) -> list[Browser]:
    """Browsers registered with Windows whose executable exists, in registry order.

    Read once and remembered; pass `refresh` (or call
    refresh_installed_browsers) to pick up a browser installed since.
    """
    global _browser_cache
    if _browser_cache is None or refresh:
        _browser_cache = _read_installed_browsers()
    return list(_browser_cache)


def refresh_installed_browsers() -> list[Browser]:
    """Read the registry again, e.g. when Settings or the editor opens."""
    return installed_browsers(refresh=True)


def _read_installed_browsers() -> list[Browser]:
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
        try:
            subprocess.Popen([browser.executable, url], close_fds=True)
            return
        except OSError:
            # Uninstalled since the list was read: read it again next time.
            refresh_installed_browsers()
    if not webbrowser.open(url):
        raise OSError(f"No web browser could be opened for {url}")


# ------------------------------------------------------------ search links

QUERY_PLACEHOLDER = "{query}"


def is_search_link(url: str) -> bool:
    """True for a website command that searches: its address has {query}."""
    return QUERY_PLACEHOLDER in str(url or "")


def fill_query(template: str, query: str) -> str:
    """The address to open for a search link and the text typed after it.

    Text after "?" or "#" is encoded as a query value (spaces become "+");
    text in the path is encoded as a path segment, so "EC 12" stays one
    segment. With nothing typed the site's home page opens instead of a
    search for nothing.
    """
    from urllib.parse import quote, quote_plus, urlsplit

    template = str(template or "")
    query = str(query or "").strip()
    if not is_search_link(template):
        return template
    if not query:
        parts = urlsplit(template if "://" in template else "https://" + template)
        return f"{parts.scheme}://{parts.netloc}/"
    position = template.index(QUERY_PLACEHOLDER)
    in_query = "?" in template[:position] or "#" in template[:position]
    encoded = quote_plus(query) if in_query else quote(query, safe="")
    return template.replace(QUERY_PLACEHOLDER, encoded)
