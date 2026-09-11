"""Places the user already keeps: the browsers' bookmarks bars and Explorer's
Quick Access folders, offered as commands by the program scan.

Every source here is best effort. Browser stores are read straight from
their files, so no browser has to be running or even known to Dash; a store
that is missing, locked, oddly shaped or from a browser we have never heard
of is skipped, never raised. The scan must not fail because of a browser.
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

# Chromium-family browsers keep a JSON "Bookmarks" file per profile. Opera
# keeps it directly in its data folder; the rest under "User Data\<profile>".
_CHROMIUM_STORES = (
    ("Chrome", "LOCALAPPDATA", r"Google\Chrome\User Data"),
    ("Edge", "LOCALAPPDATA", r"Microsoft\Edge\User Data"),
    ("Brave", "LOCALAPPDATA", r"BraveSoftware\Brave-Browser\User Data"),
    ("Vivaldi", "LOCALAPPDATA", r"Vivaldi\User Data"),
    ("Chromium", "LOCALAPPDATA", r"Chromium\User Data"),
    ("Opera", "APPDATA", r"Opera Software\Opera Stable"),
    ("Opera GX", "APPDATA", r"Opera Software\Opera GX Stable"),
)

# Explorer's Quick Access (called Home on Windows 11): pinned and frequent folders.
_QUICK_ACCESS = "shell:::{679f85cb-0220-4080-b29b-5540cc05aab6}"


def discover_bookmark_bar() -> list[dict]:
    """URL commands for every bookmark on the bookmarks bar of every browser
    profile on this machine. Browsers are read in a fixed order; a page
    bookmarked in two of them is listed once, under the first."""
    if sys.platform != "win32":
        return []
    found: list[dict] = []
    seen: set[str] = set()
    for browser, root_var, relative in _CHROMIUM_STORES:
        root = _env_path(root_var, relative)
        if root is None:
            continue
        for store in _chromium_bookmark_files(root):
            for entry in _read_chromium_bar(store, browser):
                _add_unique(found, seen, entry)
    firefox_root = _env_path("APPDATA", r"Mozilla\Firefox\Profiles")
    if firefox_root is not None:
        for store in _safe_glob(firefox_root, "*/places.sqlite"):
            for entry in _read_firefox_bar(store):
                _add_unique(found, seen, entry)
    return found


def discover_quick_access_folders() -> list[dict]:
    """Folder commands for what Explorer shows under Quick Access.

    Goes through the shell, so it needs COM initialised on the calling thread
    (the scan thread does this). Recent files are listed there too; only
    folders that exist are kept.
    """
    if sys.platform != "win32":
        return []
    try:
        import win32com.client

        namespace = win32com.client.Dispatch("Shell.Application").NameSpace(_QUICK_ACCESS)
        items = list(namespace.Items()) if namespace is not None else []
    except Exception:
        return []

    found: list[dict] = []
    seen: set[str] = set()
    for item in items:
        try:
            if not item.IsFolder:
                continue
            path = str(item.Path or "")
            name = str(item.Name or "").strip()
        except Exception:
            continue
        # Virtual folders (This PC, Libraries) have no real path.
        if not path or path.startswith("::") or not os.path.isdir(path):
            continue
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            continue
        seen.add(key)
        found.append(
            {
                "name": name or Path(path).name or path,
                "aliases": [],
                "location": path,
                "description": "Folder from Quick Access",
                "type": "file",
                "group": "folders",
            }
        )
    return found


# ------------------------------------------------------------- chromium family


def _chromium_bookmark_files(root: Path) -> list[Path]:
    files = _safe_glob(root, "Bookmarks")  # Opera: the data folder is the profile
    files += _safe_glob(root, "*/Bookmarks")  # Default, Profile 1, ...
    return files


def _read_chromium_bar(store: Path, browser: str) -> list[dict]:
    try:
        data = json.loads(store.read_text(encoding="utf-8"))
        bar = data["roots"]["bookmark_bar"]
    except Exception:
        return []
    entries: list[dict] = []
    _walk_chromium(bar, [], browser, entries)
    return entries


def _walk_chromium(node, folders: list[str], browser: str, out: list[dict], depth: int = 0):
    if depth > 8 or not isinstance(node, dict):
        return
    for child in node.get("children") or []:
        if not isinstance(child, dict):
            continue
        kind = child.get("type")
        if kind == "folder":
            _walk_chromium(child, folders + [str(child.get("name") or "")], browser, out, depth + 1)
        elif kind == "url":
            entry = _bookmark_entry(child.get("name"), child.get("url"), folders, browser)
            if entry is not None:
                out.append(entry)


# ----------------------------------------------------------------- firefox


def _read_firefox_bar(store: Path) -> list[dict]:
    """Firefox keeps bookmarks in SQLite and holds the file open while it
    runs, so a copy is queried instead. The write-ahead log comes along, or
    recent bookmarks would be missing from the copy."""
    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp(prefix="dash-places-")
        copy = Path(temp_dir) / "places.sqlite"
        shutil.copy2(store, copy)
        wal = store.with_name(store.name + "-wal")
        if wal.exists():
            shutil.copy2(wal, copy.with_name(copy.name + "-wal"))
        connection = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
        try:
            rows = connection.execute(
                """
                WITH RECURSIVE bar(id, path) AS (
                    SELECT id, '' FROM moz_bookmarks WHERE guid = 'toolbar_____'
                    UNION ALL
                    SELECT b.id, CASE WHEN bar.path = '' THEN b.title ELSE bar.path || '/' || b.title END
                    FROM moz_bookmarks b JOIN bar ON b.parent = bar.id WHERE b.type = 2
                )
                SELECT b.title, p.url, bar.path
                FROM moz_bookmarks b
                JOIN bar ON b.parent = bar.id
                JOIN moz_places p ON p.id = b.fk
                WHERE b.type = 1
                ORDER BY bar.path, b.position
                """
            ).fetchall()
        finally:
            connection.close()
    except Exception:
        return []
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
    entries: list[dict] = []
    for title, url, path in rows:
        folders = [part for part in str(path or "").split("/") if part]
        entry = _bookmark_entry(title, url, folders, "Firefox")
        if entry is not None:
            entries.append(entry)
    return entries


# ------------------------------------------------------------------ shared


def _bookmark_entry(title, url, folders: list[str], browser: str) -> dict | None:
    url = str(url or "").strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None  # javascript:, chrome://, file: and the like
    name = " ".join(str(title or "").split())
    if not name:
        name = parts.netloc.removeprefix("www.")
    where = " / ".join(folders) if folders else "bookmarks bar"
    return {
        "name": name[:80],
        "aliases": [],
        "location": url,
        "description": f"{browser} favourite ({where})",
        "type": "url",
        "group": "bookmarks",
    }


def url_key(url: str) -> str:
    """Two spellings of the same page compare equal: case of the host and
    scheme, and a trailing slash, do not make a different bookmark."""
    parts = urlsplit(str(url or "").strip())
    path = parts.path.rstrip("/")
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{path}?{parts.query}#{parts.fragment}"


def drop_known_websites(candidates: list[dict], known_urls: set[str]) -> list[dict]:
    """Leave out bookmarks for sites that are already commands. Paths are
    deduplicated elsewhere; URLs need their own comparison."""
    return [
        candidate
        for candidate in candidates
        if candidate.get("type") != "url" or url_key(str(candidate.get("location", ""))) not in known_urls
    ]


def _add_unique(found: list[dict], seen: set[str], entry: dict):
    key = url_key(entry["location"])
    if key in seen:
        return
    seen.add(key)
    found.append(entry)


def _env_path(variable: str, relative: str) -> Path | None:
    base = os.environ.get(variable)
    if not base:
        return None
    path = Path(base) / relative
    try:
        return path if path.is_dir() else None
    except OSError:
        return None


def _safe_glob(root: Path, pattern: str) -> list[Path]:
    try:
        return [path for path in root.glob(pattern) if path.is_file()]
    except OSError:
        return []
