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

    for shortcut_path in _start_menu_shortcuts():
        target_path = _shortcut_target(shortcut_path)
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


def _shortcut_target(shortcut_path: Path) -> Path | None:
    try:
        import win32com.client

        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortcut(str(shortcut_path))
        target = str(shortcut.TargetPath or "").strip()
    except Exception:
        return None

    if not target:
        return None

    return _existing_exe_path(target)


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