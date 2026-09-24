import codecs
import ctypes
import os
import re
import struct
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

# Explorer's tally of what was started through it (UserAssist): one key for
# executables, one for shortcuts. Value names are ROT13-encoded paths, often
# with a known-folder GUID standing in for the folder.
USER_ASSIST_ROOT = r"Software\Microsoft\Windows\CurrentVersion\Explorer\UserAssist"
USER_ASSIST_KEYS = ("{CEBFF5CD-ACE2-4F4F-9178-9926F41749EA}", "{F4E57C4B-2036-45F0-A9AB-443BCFE33D9F}")
_FILETIME_UNIX_EPOCH = 116_444_736_000_000_000  # 1970-01-01 in 100 ns ticks since 1601

# The shell folder listing every Start Menu entry, Store (MSIX) apps included.
# A Store app has no shortcut on disk and is started by its app id, so its
# command location is "shell:AppsFolder\<app id>" rather than a path.
APPS_FOLDER = "shell:AppsFolder"
# Windows' own inbox components (Settings, Get Started, Click to Do...) are
# signed by this publisher; they are not apps anyone adds a command for.
_WINDOWS_COMPONENT_PUBLISHER = "cw5n1h2txyewy"

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


# Vendor names that lead a program's name without being what anyone types to
# find it: "Microsoft Word" is "word", "Google Chrome" is "chrome".
VENDOR_PREFIXES = (
    "Microsoft",
    "Google",
    "Adobe",
    "Mozilla",
    "Apple",
    "Autodesk",
    "JetBrains",
    "Oracle",
    "VMware",
    "NVIDIA",
    "Intel",
    "AMD",
    "Corel",
    "Logitech",
)


def suggest_aliases(name: str) -> list[str]:
    """Aliases worth offering for a scanned program, lowercase and editable:
    its initials when it has two or more words ("vsc" for Visual Studio Code)
    and its name without the vendor ("word" for Microsoft Word).

    A bracketed note ("Microsoft Teams (work or school)") is left out, and
    nothing equal to the name itself is suggested.
    """
    plain = re.sub(r"\s*[(\[].*?[)\]]", " ", str(name or "")).strip()
    words = [word for word in re.split(r"[\s\-_]+", plain) if word]
    suggestions: list[str] = []

    initials = "".join(word[0] for word in words if word[0].isalpha())
    if len(words) >= 2 and len(initials) >= 2:
        suggestions.append(initials.casefold())

    for vendor in VENDOR_PREFIXES:
        if plain.casefold().startswith(vendor.casefold() + " "):
            rest = plain[len(vendor) :].strip()
            if rest:
                suggestions.append(rest.casefold())
            break

    kept: list[str] = []
    for alias in suggestions:
        if alias and alias != str(name).strip().casefold() and alias not in kept:
            kept.append(alias)
    return kept


def drop_clashing_aliases(candidates: list[dict], known_keywords: set[str] = frozenset()) -> list[dict]:
    """Copies of `candidates` without the aliases that could not be saved.

    An alias is dropped when it is already a name or alias in
    `known_keywords` (casefolded), when it is the name of another candidate,
    or when an earlier candidate already has it. Saving and importing reject
    a keyword that is taken, so a suggested alias that clashes would
    otherwise stop the whole command being added. Candidates themselves are
    never dropped here, and the originals are not changed.
    """
    names = {str(candidate.get("name", "")).strip().casefold() for candidate in candidates}
    taken = set(known_keywords)
    kept: list[dict] = []
    for candidate in candidates:
        own_name = str(candidate.get("name", "")).strip().casefold()
        aliases = list(candidate.get("aliases") or [])
        free: list = []
        for alias in aliases:
            key = str(alias).strip().casefold()
            if not key or key in taken or (key in names and key != own_name) or key == own_name:
                continue
            free.append(alias)
            taken.add(key)
        kept.append(candidate if free == aliases else {**candidate, "aliases": free})
    return kept


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
    usage = program_usage()

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
        suggestion = _suggestion(name, path, aliases, description)
        note_usage(suggestion, *usage.get(key, (0, None)))
        suggestions.append(suggestion)

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

    usage = program_usage()

    if shortcut_shell is not None:
        for shortcut_path in _start_menu_shortcuts():
            target_path, arguments = _shortcut_details(shortcut_path, shortcut_shell)
            _add_program_command(
                commands,
                seen_locations,
                seen_names,
                _command_name(shortcut_path.stem),
                target_path,
                from_registry=False,
                usage=usage,
                shortcut_path=shortcut_path,
                shortcut_arguments=arguments,
            )

    for exe_name, target_path in _app_paths_registry_targets():
        _add_program_command(
            commands,
            seen_locations,
            seen_names,
            _command_name_for_exe(exe_name),
            target_path,
            from_registry=True,
            usage=usage,
        )

    commands = sorted(commands, key=lambda command: command["name"].casefold())
    if max_commands is not None:
        return commands[:max_commands]
    return commands


def drop_known_names(candidates: list[dict], known_keywords: set[str]) -> list[dict]:
    """Leave out candidates named like a command that already exists.

    Location alone misses the same program kept in two places: a Start Menu
    shortcut to a copy on a network share, and a command for a local copy.
    A candidate whose name is already a command name or alias could not be
    added under that name anyway. `known_keywords` are casefolded.
    """
    kept = [candidate for candidate in candidates if str(candidate.get("name", "")).strip().casefold() not in known_keywords]
    # An alias another command (or another recommendation) already has would
    # stop the whole recommendation being added; offer it without that alias.
    return drop_clashing_aliases(kept, known_keywords)


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
    usage: dict[str, tuple[int, float | None]] | None = None,
    shortcut_path: Path | None = None,
    shortcut_arguments: str = "",
):
    if target_path is None or target_path.suffix.lower() != ".exe":
        return
    if _in_package_folder(target_path):
        return  # a Store app: offered by app id from the Applications folder instead

    # A shortcut that passes arguments (Discord's "Update.exe --processStart
    # Discord.exe", a Chrome profile's "--profile-directory") only works when
    # started as the shortcut, so the shortcut is the command. The same exe
    # with other arguments is a different command, not a duplicate.
    with_arguments = bool(shortcut_arguments and shortcut_path is not None)
    target_key = _path_key(target_path)
    seen_key = f"{target_key}|{shortcut_arguments.casefold()}" if with_arguments else target_key
    if seen_key in seen_locations:
        return

    name = name.strip()
    if not name or name.casefold() in seen_names:
        return

    if _is_noise_candidate(name, target_path, from_registry):
        return

    seen_locations.add(seen_key)
    seen_names.add(name.casefold())
    command = {
        "name": name,
        "aliases": suggest_aliases(name),
        "location": str(shortcut_path if with_arguments else target_path),
        "description": f"Opens {name}",
        "type": "file",
    }
    if with_arguments:
        # The program that ends up running, for switching to its window and
        # for its icon; the shortcut itself is what gets started.
        command["process_path"] = str(_launched_program(target_path, shortcut_arguments))
    if usage:
        # Starting a program from its Start Menu entry is recorded against
        # the shortcut, starting it any other way against the executable.
        records = [usage.get(target_key)]
        if shortcut_path is not None:
            records.append(usage.get(_path_key(shortcut_path)))
        note_usage(command, *_combine_usage(*(record for record in records if record)))
    commands.append(command)


def program_usage() -> dict[str, tuple[int, float | None]]:
    """How often and when the shell last started each program and shortcut,
    by path key: (times opened, last opened as a POSIX timestamp or None).

    Recent Windows 11 builds no longer keep the count, so it is usually 0
    and only the time says anything. Entries that are not paths (Store
    apps, session bookkeeping) are left out.
    """
    usage: dict[str, tuple[int, float | None]] = {}
    if winreg is None:
        return usage
    folders: dict[str, Path | None] = {}
    for key_name in USER_ASSIST_KEYS:
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{USER_ASSIST_ROOT}\{key_name}\Count")
        except OSError:
            continue
        with key:
            index = 0
            while True:
                try:
                    name, data, _value_type = winreg.EnumValue(key, index)
                except OSError:
                    break
                index += 1
                entry = _usage_entry(name, data, folders)
                if entry is None:
                    continue
                path_key, opened, last_opened = entry
                usage[path_key] = _combine_usage(usage.get(path_key, (0, None)), (opened, last_opened))
    return usage


def _usage_entry(name, data, folders: dict[str, Path | None]) -> tuple[str, int, float | None] | None:
    """One UserAssist value as (key, times opened, last opened), or None for
    a value that is not about a program or holds no record. The key is the
    path key of a program or shortcut, or the app id of a Store app."""
    if not isinstance(data, bytes) or len(data) < 68:
        return None
    text = codecs.decode(str(name), "rot13")
    path = _usage_path(text, folders)
    if path is not None:
        key = _path_key(path)
    elif "!" in text and "\\" not in text:
        key = app_id_key(text)
    else:
        return None
    opened = struct.unpack_from("<I", data, 4)[0]
    ticks = struct.unpack_from("<Q", data, 60)[0]
    last_opened = (ticks - _FILETIME_UNIX_EPOCH) / 10_000_000 if ticks > _FILETIME_UNIX_EPOCH else None
    if not opened and last_opened is None:
        return None
    return key, opened, last_opened


def _usage_path(text: str, folders: dict[str, Path | None]) -> Path | None:
    """The path a UserAssist value names, with a known-folder GUID resolved.
    `folders` caches the resolutions across values."""
    if "\\" not in text:
        return None
    if not text.startswith("{"):
        return Path(text)
    guid, closing, rest = text.partition("}")
    if not closing:
        return None
    guid += closing
    if guid not in folders:
        folders[guid] = _known_folder_path(guid)
    folder = folders[guid]
    if folder is None:
        return None
    return folder / rest.lstrip("\\")


def _combine_usage(*records: tuple[int, float | None]) -> tuple[int, float | None]:
    """Several records of the same thing: the highest count, the latest time."""
    opened = 0
    last_opened = None
    for count, when in records:
        opened = max(opened, count)
        if when is not None and (last_opened is None or when > last_opened):
            last_opened = when
    return opened, last_opened


def note_usage(candidate: dict, opened: int, last_opened: float | None) -> None:
    """Record on an import candidate how often and when it was opened, so
    the import dialog can put the most used first. Nothing is written when
    there is no record; these keys never reach commands.toml."""
    if opened:
        candidate["opened"] = int(opened)
    if last_opened is not None:
        candidate["last_opened"] = float(last_opened)


def discover_packaged_apps() -> list[dict]:
    """Commands for the Store (MSIX) apps on the Start Menu: Claude, Terminal,
    Snipping Tool and the like.

    They have no shortcut on disk, so the Start Menu scan never sees them;
    the shell's Applications folder lists them, and they start by app id.
    The icon offered is the app's own logo inside its package folder. Needs
    COM initialised on the calling thread; returns nothing rather than fail.
    """
    if sys.platform != "win32":
        return []
    try:
        import win32com.client

        folder = win32com.client.Dispatch("Shell.Application").NameSpace(APPS_FOLDER)
        items = list(folder.Items()) if folder is not None else []
    except Exception:
        return []

    usage = program_usage()
    commands: list[dict] = []
    seen: set[str] = set()
    for item in items:
        try:
            package = str(item.ExtendedProperty("System.AppUserModel.PackageFullName") or "")
            if not package or item.ExtendedProperty("System.Link.TargetParsingPath"):
                continue  # a desktop app: the Start Menu scan has it, with its path
            app_id = str(item.Path or "").strip()
            name = str(item.Name or "").strip()
            install_path = str(item.ExtendedProperty("System.AppUserModel.PackageInstallPath") or "")
            logo = str(item.ExtendedProperty("System.Tile.SmallLogoPath") or "")
        except Exception:
            continue
        if not app_id or not name or "!" not in app_id or app_id_key(app_id) in seen:
            continue
        if package.endswith("_" + _WINDOWS_COMPONENT_PUBLISHER):
            continue
        seen.add(app_id_key(app_id))
        command = {
            "name": name,
            "aliases": suggest_aliases(name),
            "location": f"{APPS_FOLDER}\\{app_id}",
            "description": f"Opens {name}",
            "type": "file",
        }
        icon = packaged_app_logo(Path(install_path), logo) if install_path and logo else None
        if icon is not None:
            command["icon"] = str(icon)
        note_usage(command, *usage.get(app_id_key(app_id), (0, None)))
        commands.append(command)
    return sorted(commands, key=lambda command: command["name"].casefold())


def packaged_app_logo(install_path: Path, logo: str) -> Path | None:
    """The best file for a Store app's logo: the manifest names one image,
    the package ships it in several sizes, and often not the plain one."""
    if not logo or logo.startswith("ms-resource:"):
        return None
    wanted = install_path / logo
    try:
        variants = [path for path in wanted.parent.glob(f"{wanted.stem}*{wanted.suffix}") if path.is_file()]
    except OSError:
        return None
    return max(variants, key=_logo_rank, default=None)


def _logo_rank(path: Path) -> tuple[int, int]:
    """Bigger first; at the same size, the version without a tile behind it."""
    name = path.name.casefold()
    target = re.search(r"targetsize-(\d+)", name)
    scale = re.search(r"scale-(\d+)", name)
    if target:
        size = int(target.group(1))
    elif scale:
        size = 44 * int(scale.group(1)) // 100
    else:
        size = 44
    return size, int("altform-unplated" in name)


# A scheme of two or more letters: "ms-settings:display", "shell:startup".
# One letter is a drive ("C:\Tools"), which is a path, not a link.
_LINK_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]+:")


# Link schemes a shared commands file may use. Built-in features only make
# ms-settings: (Settings pages) and shell: (Store apps, shell folders) links;
# other registered schemes (ms-msdt:, search-ms:, file:...) can run or fetch
# things a person would not expect from opening a command.
SAFE_LINK_SCHEMES = ("http", "https", "mailto", "ms-settings", "shell")


def link_scheme(location: str) -> str:
    """The lowercase scheme of a link ("ms-settings"), or "" for a path."""
    text = str(location).strip()
    match = _LINK_SCHEME.match(text)
    return match.group(0)[:-1].casefold() if match else ""


def is_network_location(location: str) -> bool:
    """True for a UNC path (\\\\server\\share, //server/share) or a device path.

    Checked on the text alone: asking Windows whether a UNC path exists
    connects to that server and hands it the user's sign-in hash.
    """
    text = str(location).strip().replace("/", "\\")
    return text.startswith("\\\\")


def is_link_location(location: str) -> bool:
    """True for a command that Windows opens as a link rather than a path:
    a Store app by app id, a Settings page, a shell folder. Web addresses
    are website commands and are not counted here."""
    text = str(location).strip()
    return bool(_LINK_SCHEME.match(text)) and not text.casefold().startswith(("http://", "https://"))


def is_app_id_location(location: str) -> bool:
    """True for a link that starts a Store app by its app id."""
    return str(location).strip().casefold().startswith(APPS_FOLDER.casefold() + "\\")


def app_id_key(app_id: str) -> str:
    return app_id.strip().casefold()


def _in_package_folder(path: Path) -> bool:
    return "\\windowsapps\\" in os.path.normcase(os.path.abspath(os.fspath(path))) + "\\"


def _start_menu_shortcuts() -> list[Path]:
    shortcuts: list[Path] = []
    for folder in START_MENU_DIRS:
        if folder.exists():
            shortcuts.extend(folder.rglob("*.lnk"))
    return sorted(shortcuts, key=lambda path: str(path).casefold())


def _shortcut_target(shortcut_path: Path, shell) -> Path | None:
    return _shortcut_details(shortcut_path, shell)[0]


def _shortcut_details(shortcut_path: Path, shell) -> tuple[Path | None, str]:
    """(the exe a shortcut starts, the arguments it passes); (None, "") for a
    shortcut Dash does not offer."""
    try:
        shortcut = shell.CreateShortcut(str(shortcut_path))
        target = str(shortcut.TargetPath or "").strip()
        arguments = str(shortcut.Arguments or "").strip()
    except Exception:
        return None, ""

    if not target:
        return None, ""

    path = _existing_exe_path(target)
    # A shortcut that hands arguments to a Windows tool ("Edit Commands" ->
    # notepad.exe <file>, "Install Tools" -> cmd.exe /c ...) is a task, not an
    # app, and would just duplicate the built-in tool under a misleading name.
    if path is not None and arguments and _in_windows_dir(path):
        return None, ""
    return path, arguments if path is not None else ""


def _launched_program(target_path: Path, arguments: str) -> Path:
    """The program a shortcut ends up running. Squirrel installers (Discord,
    Slack, Teams classic) point the shortcut at Update.exe, which starts the
    real exe from the newest "app-<version>" folder beside it."""
    match = re.search(r"--processStart(?:=|\s+)\"?([^\"\s]+\.exe)", arguments, re.IGNORECASE)
    if match is None:
        return target_path
    try:
        versions = [folder for folder in target_path.parent.glob("app-*") if (folder / match.group(1)).is_file()]
    except OSError:
        versions = []
    if not versions:
        return target_path
    newest = max(versions, key=lambda folder: _version_key(folder.name[len("app-") :]))
    return newest / match.group(1)


def _version_key(version: str) -> tuple:
    return tuple(int(part) if part.isdigit() else -1 for part in re.split(r"[.\-]", version))


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
    text = os.fspath(path)
    if is_link_location(text):
        return os.path.normcase(text.strip())
    return os.path.normcase(os.path.abspath(text))


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


def command_name_for_link(location: str) -> str:
    """A name for a link location: a Settings page by its listed name, a
    Store app by the name in its app id ("Claude" for
    "Claude_pzs8sxrjxfjjc!Claude", "WindowsTerminal" for
    "Microsoft.WindowsTerminal_8wekyb3d8bbwe!App"), a shell folder by its
    own name ("startup")."""
    from .windows_settings import is_settings_location, settings_page_name

    if is_settings_location(location):
        return settings_page_name(location)
    text = str(location).strip()
    if is_app_id_location(text):
        app_id = text.split("\\", 1)[1]
        package, _, entry = app_id.partition("!")
        if entry and entry.casefold() != "app":
            return entry
        return package.split("_", 1)[0].rsplit(".", 1)[-1]
    return text.split(":", 1)[1].strip("\\/ ") or text


def command_name_for_target(path: Path) -> str:
    """The name a command for this file, shortcut or folder would get:
    the file name without its extension, known programs by their friendly
    name, and " - Shortcut" dropped from a shortcut. Empty for a drive root."""
    return _command_name_for_exe(path.name)