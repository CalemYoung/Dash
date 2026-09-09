import ctypes
import os
import sys
from pathlib import Path

if sys.platform == "win32":
    import winreg
else:
    winreg = None


START_MENU_DIRS = [
    Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
    Path(os.environ.get("PROGRAMDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
]

APP_PATHS_ROOT = r"Software\Microsoft\Windows\CurrentVersion\App Paths"

FRIENDLY_EXE_NAMES = {
    "excel": "Excel",
    "msaccess": "Access",
    "mspub": "Publisher",
    "onenote": "OneNote",
    "outlook": "Outlook",
    "powerpnt": "PowerPoint",
    "winword": "Word",
    # Store editions registered under App Paths; naming them like the
    # suggestions lets the merge drop them as duplicates.
    "mspaint": "Paint",
    "snippingtool": "Snipping Tool",
    "notepad": "Notepad",
}

# Substrings that flag an entry as a support tool rather than an app someone
# would launch on purpose (uninstallers, diagnostics, redistributables, etc.).
NOISE_NAME_KEYWORDS = (
    "uninstall",
    "unins000",
    "installer",
    "updater",
    "update service",
    "helper",
    "crash reporter",
    "crashreporter",
    "crashpad",
    "diag",
    "repair",
    "readme",
    "changelog",
    "release notes",
    "license",
    "licence",
    "eula",
    "redistributable",
    "redist",
    "vcredist",
    "unregister",
    "background service",
    "daemon",
    "cleanup",
    "activation",
    "activator",
    "shellext",
    "watchdog",
    "telemetry",
)

# App Paths registers many Windows components under these folders; they are
# system plumbing, not apps a user would pick from a launcher.
WINDOWS_SYSTEM_DIR_MARKERS = (
    "\\windows\\system32\\",
    "\\windows\\syswow64\\",
    "\\windows\\winsxs\\",
)


# Personal folders, looked up by KNOWNFOLDERID rather than assembled from
# USERPROFILE - any of these can be redirected to OneDrive or another drive.
#
# Aliases lead with the shortest thing someone would actually type ("dl",
# "tm"); the longer forms are there for people who know the exe name.
SUGGESTED_FOLDERS = (
    ("Downloads", "{374DE290-123F-4565-9164-39C4925E467B}", ["dl", "downloads"]),
    ("Desktop", "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}", ["dt", "desktop"]),
    ("Documents", "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}", ["docs", "documents"]),
    ("Pictures", "{33E28130-4E1E-4676-835A-98395C3BC3BB}", ["pics", "photos", "pictures"]),
    ("Videos", "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}", ["vids", "videos"]),
    ("Music", "{4BD8D571-6D19-48D3-BE97-422220080E43}", ["music"]),
)

# Built-in tools worth launching by name. These live in System32, which the
# registry scan deliberately skips, so they never turn up on their own.
SUGGESTED_TOOLS = (
    ("Remote Desktop", "mstsc.exe", ["rdp", "mstsc", "remote"], "Connect to another PC"),
    ("Task Manager", "taskmgr.exe", ["tm", "taskmgr", "tasks"], "View running apps and processes"),
    ("Control Panel", "control.exe", ["cp", "control"], "Open the Windows Control Panel"),
    ("File Explorer", "explorer.exe", ["fe", "explorer", "files"], "Browse your files"),
    ("Command Prompt", "cmd.exe", ["cmd", "prompt"], "Open a command prompt"),
    ("PowerShell", "WindowsPowerShell\\v1.0\\powershell.exe", ["ps", "powershell"], "Open PowerShell"),
    ("Snipping Tool", "SnippingTool.exe", ["snip", "ss", "screenshot"], "Capture part of the screen"),
    ("Calculator", "calc.exe", ["calc"], "Open the Windows calculator"),
    ("Notepad", "notepad.exe", ["np", "notepad"], "Open Notepad"),
    ("Paint", "mspaint.exe", ["mspaint", "draw"], "Open Paint"),
    ("Magnifier", "magnify.exe", ["mag", "magnify", "zoom"], "Magnify part of the screen"),
    ("On-Screen Keyboard", "osk.exe", ["osk", "keyboard"], "Show the on-screen keyboard"),
    ("Character Map", "charmap.exe", ["charmap", "symbols"], "Look up special characters"),
    ("Disk Cleanup", "cleanmgr.exe", ["cleanup", "cleanmgr"], "Free up disk space"),
    ("Resource Monitor", "resmon.exe", ["resmon", "resources"], "Watch CPU, memory and disk use"),
    ("System Information", "msinfo32.exe", ["sysinfo", "msinfo", "specs"], "View system specifications"),
    ("Device Manager", "devmgmt.msc", ["dm", "devmgmt", "devices"], "Manage hardware and drivers"),
    ("Registry Editor", "regedit.exe", ["regedit", "reg", "registry"], "Edit the Windows registry"),
)


def discover_windows_suggestions() -> list[dict]:
    """Return commands for standard Windows folders and tools.

    Neither source shows up in the normal scan: personal folders are not
    programs, and the built-in tools sit in System32, which is filtered out as
    system plumbing. They are the entries people most often add by hand.
    """
    if sys.platform != "win32":
        return []

    suggestions: list[dict] = []
    seen: set[str] = set()

    for name, folder_id, aliases in SUGGESTED_FOLDERS:
        path = _known_folder_path(folder_id)
        if path is None:
            continue
        key = _path_key(path)
        if key in seen:
            continue
        seen.add(key)
        suggestions.append(_suggestion(name, path, aliases, f"Opens your {name} folder"))

    for name, relative, aliases, description in SUGGESTED_TOOLS:
        path = _windows_tool_path(relative)
        if path is None:
            continue
        key = _path_key(path)
        if key in seen:
            continue
        seen.add(key)
        suggestions.append(_suggestion(name, path, aliases, description))

    return suggestions


def merge_program_candidates(suggestions: list[dict], discovered: list[dict]) -> list[dict]:
    """Suggestions first, then discovered programs that do not repeat one.

    Start Menu shortcuts for built-in tools (Task Manager, Character Map...)
    resolve to the same executables the suggestions do, and Store editions of
    those tools (Notepad) share the name with a different path. Either way the
    user only wants one entry, and the suggestion carries the better aliases.
    """
    taken_locations = {_path_key(Path(str(candidate.get("location", "")))) for candidate in suggestions}
    taken_names = {str(candidate.get("name", "")).casefold() for candidate in suggestions}

    merged = list(suggestions)
    for candidate in discovered:
        if _path_key(Path(str(candidate.get("location", "")))) in taken_locations:
            continue
        if str(candidate.get("name", "")).casefold() in taken_names:
            continue
        merged.append(candidate)
    return merged


def _suggestion(name: str, path: Path, aliases: list[str], description: str) -> dict:
    return {
        "name": name,
        # An alias matching the name is redundant and trips the duplicate check.
        "aliases": [alias for alias in aliases if alias.casefold() != name.casefold()],
        "location": str(path),
        "description": description,
        "type": "file",
        "suggested": True,
    }


def _known_folder_path(folder_id: str) -> Path | None:
    """Resolve a KNOWNFOLDERID to its current location on disk."""
    try:
        shell32 = ctypes.windll.shell32
        ole32 = ctypes.windll.ole32
        guid = ctypes.create_string_buffer(16)
        if ole32.CLSIDFromString(ctypes.c_wchar_p(folder_id), guid) != 0:
            return None
        buffer = ctypes.c_wchar_p()
        if shell32.SHGetKnownFolderPath(guid, 0, None, ctypes.byref(buffer)) != 0:
            return None
        try:
            value = buffer.value
        finally:
            ole32.CoTaskMemFree(buffer)
    except Exception:
        return None

    if not value:
        return None
    path = Path(value)
    return path if path.is_dir() else None


def _windows_tool_path(relative: str) -> Path | None:
    """Locate a bundled Windows tool, skipping any that this edition lacks."""
    windows_dir = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    for base in (windows_dir / "System32", windows_dir):
        candidate = base / relative
        if candidate.is_file():
            return candidate
    # Windows 11 moved Paint and Snipping Tool out of System32 into Store
    # packages. Their app execution alias survives package updates, whereas the
    # App Paths target carries the package version and breaks on the next one.
    exe_name = Path(relative).name
    alias = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps" / exe_name
    if alias.is_file():
        return alias
    return _app_paths_lookup(exe_name)


def _app_paths_lookup(exe_name: str) -> Path | None:
    if winreg is None:
        return None
    for hive, view_flag in _registry_views():
        try:
            root = winreg.OpenKey(hive, APP_PATHS_ROOT, 0, winreg.KEY_READ | view_flag)
        except OSError:
            continue
        with root:
            target = _app_path_target(root, exe_name)
        if target is not None:
            return target
    return None


def _is_noise_candidate(name: str, target_path: Path, from_registry: bool) -> bool:
    lname = name.casefold()
    if any(keyword in lname for keyword in NOISE_NAME_KEYWORDS):
        return True
    if target_path.stem.casefold().startswith("unins"):
        return True
    if from_registry:
        path_str = str(target_path).casefold()
        if any(marker in path_str for marker in WINDOWS_SYSTEM_DIR_MARKERS):
            return True
    return False


def discover_recent_program_commands(days: int = 365, max_commands: int | None = None) -> list[dict]:
    """Return Dash command entries for installed Windows programs.

    Windows does not expose a single clean installed-app API. Dash uses Start
    Menu shortcuts for user-visible apps and App Paths registry entries for
    launchable executables such as Office apps.
    """
    if sys.platform != "win32":
        return []

    commands: list[dict] = []
    seen_locations: set[str] = set()
    seen_names: set[str] = set()

    # The installer adds a Start Menu shortcut for Dash itself; a launcher
    # command that launches the launcher is never wanted.
    if getattr(sys, "frozen", False):
        seen_locations.add(_path_key(Path(sys.executable)))

    try:
        import win32com.client

        shortcut_shell = win32com.client.Dispatch("WScript.Shell")
    except Exception:
        shortcut_shell = None

    if shortcut_shell is not None:
        for shortcut_path in _start_menu_shortcuts():
            target_path = _shortcut_target(shortcut_path, shortcut_shell)
            _add_program_command(
                commands, seen_locations, seen_names, _command_name(shortcut_path.stem), target_path, from_registry=False
            )

    for exe_name, target_path in _app_paths_registry_targets():
        _add_program_command(
            commands, seen_locations, seen_names, _command_name_for_exe(exe_name), target_path, from_registry=True
        )

    commands = sorted(commands, key=lambda command: command["name"].casefold())
    if max_commands is not None:
        return commands[:max_commands]
    return commands


def filter_new_program_commands(candidates: list[dict], existing_locations: set[str]) -> list[dict]:
    """Return candidates whose locations are not already stored as commands."""
    return [
        candidate
        for candidate in candidates
        if _path_key(Path(str(candidate["location"]))) not in existing_locations
    ]


def _add_program_command(
    commands: list[dict],
    seen_locations: set[str],
    seen_names: set[str],
    name: str,
    target_path: Path | None,
    from_registry: bool = False,
):
    if target_path is None or target_path.suffix.lower() != ".exe":
        return

    target_key = _path_key(target_path)
    if target_key in seen_locations:
        return

    name = name.strip()
    if not name or name.casefold() in seen_names:
        return

    if _is_noise_candidate(name, target_path, from_registry):
        return

    seen_locations.add(target_key)
    seen_names.add(name.casefold())
    commands.append(
        {
            "name": name,
            "aliases": [],
            "location": str(target_path),
            "description": f"Opens {name}",
            "type": "file",
        }
    )


def _start_menu_shortcuts() -> list[Path]:
    shortcuts: list[Path] = []
    for folder in START_MENU_DIRS:
        if folder.exists():
            shortcuts.extend(folder.rglob("*.lnk"))
    return sorted(shortcuts, key=lambda path: str(path).casefold())


def _shortcut_target(shortcut_path: Path, shell) -> Path | None:
    try:
        shortcut = shell.CreateShortcut(str(shortcut_path))
        target = str(shortcut.TargetPath or "").strip()
        arguments = str(shortcut.Arguments or "").strip()
    except Exception:
        return None

    if not target:
        return None

    path = _existing_exe_path(target)
    # A shortcut that hands arguments to a Windows tool ("Edit Commands" ->
    # notepad.exe <file>, "Install Tools" -> cmd.exe /c ...) is a task, not an
    # app. Dash only records the bare exe, which would just duplicate the
    # built-in tool under a misleading name.
    if path is not None and arguments and _in_windows_dir(path):
        return None
    return path


def _in_windows_dir(path: Path) -> bool:
    windows_dir = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    return _path_key(path).startswith(_path_key(windows_dir) + os.sep)


def _app_paths_registry_targets() -> list[tuple[str, Path]]:
    targets: list[tuple[str, Path]] = []
    if winreg is None:
        return targets

    for hive, view_flag in _registry_views():
        try:
            root = winreg.OpenKey(hive, APP_PATHS_ROOT, 0, winreg.KEY_READ | view_flag)
        except OSError:
            continue

        with root:
            index = 0
            while True:
                try:
                    exe_name = winreg.EnumKey(root, index)
                except OSError:
                    break
                index += 1
                target = _app_path_target(root, exe_name)
                if target is not None:
                    targets.append((exe_name, target))

    return targets


def _app_path_target(root, exe_name: str) -> Path | None:
    try:
        key = winreg.OpenKey(root, exe_name)
    except OSError:
        return None

    with key:
        try:
            value, _value_type = winreg.QueryValueEx(key, "")
        except OSError:
            return None
    return _existing_exe_path(str(value))


def _registry_views():
    if winreg is None:
        return []

    views = [0]
    for flag_name in ("KEY_WOW64_64KEY", "KEY_WOW64_32KEY"):
        flag = getattr(winreg, flag_name, 0)
        if flag:
            views.append(flag)
    return [(winreg.HKEY_CURRENT_USER, view) for view in views] + [(winreg.HKEY_LOCAL_MACHINE, view) for view in views]


def _existing_exe_path(value: str) -> Path | None:
    text = os.path.expandvars(value).strip()
    if not text:
        return None

    if text.startswith('"'):
        text = text.split('"', 2)[1]
    else:
        text = text.split(",", 1)[0].strip()
    path = Path(text).expanduser()
    if path.exists() and path.suffix.lower() == ".exe":
        return path
    return None


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _command_name(name: str) -> str:
    for suffix in (" - Shortcut", " Shortcut"):
        if name.endswith(suffix):
            return name[: -len(suffix)].strip()
    return name.strip()


def _command_name_for_exe(exe_name: str) -> str:
    stem = Path(exe_name).stem
    friendly_name = FRIENDLY_EXE_NAMES.get(stem.casefold())
    if friendly_name:
        return friendly_name
    return _command_name(stem)