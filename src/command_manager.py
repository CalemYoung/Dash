import base64
import json
import logging
import ntpath
import os
import subprocess
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from .browsers import fill_query, is_search_link
from .browsers import open_url as open_in_browser
from .command_trie import CommandTrie, WordStartIndex
from .fileio import atomic_write_text, quarantine_file
from .icon_manager import command_source_icon_path
from .installed_programs import (
    SAFE_LINK_SCHEMES,
    _path_key,
    drop_clashing_aliases,
    is_link_location,
    is_network_location,
    link_scheme,
)
from .window_switch import switch_to_running
from .settings import Settings, toml_str, toml_value

if TYPE_CHECKING:  # pragma: no cover - type hints only, keeps the GUI out of this module
    from .launcher_gui import MainWindow


logger = logging.getLogger(__name__)

RUN_COUNTS_FILENAME = "run_counts.json"

# The command types a commands file may hold; "system" ones are built in.
COMMAND_TYPES = ("file", "url", "group")

# Optional launch settings of an app or file command:
#   arguments       passed to the program ("--new-window C:\\Projects")
#   working_folder  the folder it starts in, instead of its own
#   process_path    the program a shortcut ends up running, for switching to
#                   its window and for its icon (the shortcut is the location)
LAUNCH_KEYS = ("arguments", "working_folder", "process_path")

# Programs that run whatever their arguments say. A shared commands file that
# hands them arguments is a script in disguise, so such commands are refused
# on import; a person can still make one in the editor.
SCRIPT_HOSTS = frozenset(
    {
        "cmd.exe",
        "powershell.exe",
        "pwsh.exe",
        "wscript.exe",
        "cscript.exe",
        "mshta.exe",
        "rundll32.exe",
        "regsvr32.exe",
        "wsl.exe",
        "bash.exe",
        "msiexec.exe",
        "certutil.exe",
        "bitsadmin.exe",
    }
)

# ShellExecute's answer when the person says No to the administrator prompt.
_ERROR_CANCELLED = 1223

# What made a command's icon (see icon_browser recipes). Stored next to the
# rendered `icon` path so the icon can be re-edited exactly and exported.
ICON_RECIPE_KEYS = ("icon_glyph", "icon_source", "icon_color", "icon_background")
# Export-only: a disk source image travels as base64 under this key.
ICON_SOURCE_DATA_KEY = "icon_source_data"


def _recipe_fields(command: dict) -> dict:
    return {key: str(command[key]) for key in ICON_RECIPE_KEYS if command.get(key)}


def _serialize_command(cmd: dict) -> str:
    """Render a single [[command]] TOML block matching the file's style."""
    lines = ["[[command]]"]
    lines.append(f"name = {toml_str(cmd.get('name', ''))}")
    lines.append(f"aliases = {toml_value([str(alias) for alias in cmd.get('aliases', [])])}")
    lines.append(f"location = {toml_str(cmd.get('location', ''))}")
    lines.append(f"description = {toml_str(cmd.get('description', ''))}")
    if cmd.get("type") == "group":
        lines.append("type = 'group'")
        lines.append(f"targets = {toml_value([str(target) for target in cmd.get('targets', [])])}")
    if cmd.get("icon"):
        lines.append(f"icon = {toml_str(cmd['icon'])}")
    if cmd.get("browser"):
        lines.append(f"browser = {toml_str(cmd['browser'])}")
    for key in LAUNCH_KEYS:
        if cmd.get(key):
            lines.append(f"{key} = {toml_str(cmd[key])}")
    for key in (*ICON_RECIPE_KEYS, ICON_SOURCE_DATA_KEY):
        if cmd.get(key):
            lines.append(f"{key} = {toml_str(cmd[key])}")
    return "\n".join(lines) + "\n"


def _entry_problem(entry) -> str | None:
    """Why a [[command]] entry cannot be loaded, in plain words, or None."""
    if not isinstance(entry, dict):
        return "it is not a command"
    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        return "it has no name"
    for key in ("location", "description", "type", "icon", "browser", *ICON_RECIPE_KEYS, *LAUNCH_KEYS):
        if entry.get(key) is not None and not isinstance(entry[key], str):
            return f"its {key.replace('_', ' ')} is not text"
    for key in ("aliases", "targets"):
        value = entry.get(key)
        if value is not None and not (isinstance(value, list) and all(isinstance(item, str) for item in value)):
            return f"its {key} are not a list of text"
    if entry.get("type") and entry["type"] not in COMMAND_TYPES:
        return f"its type '{entry['type']}' is not one Dash knows"
    return None


def _launch_fields(command: dict) -> dict:
    return {key: str(command[key]).strip() for key in LAUNCH_KEYS if str(command.get(key) or "").strip()}


def _safe_run_count(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


class CommandsFileUnreadableError(OSError):
    """Saving was refused because commands.toml could not be read or set aside."""


class CommandManager:
    def __init__(self, command_file_path: Path, settings: Settings):
        self.command_file_path = command_file_path
        self.settings = settings
        self.commands = {}
        self.lookup_trie = CommandTrie()
        # Commands by the start of any later word of their name ("code" for
        # Visual Studio Code), for the match_word_starts setting.
        self.word_index = WordStartIndex()
        # Every name and alias, as the trie compares them, to its command.
        self.keyword_index: dict[str, str] = {}
        # Plain-language notes about problems met while loading commands.toml
        # (an unreadable file set aside, entries or aliases left out) for the
        # GUI to show. Nothing here stops Dash starting; the GUI clears the
        # list once it has shown it.
        self.load_warnings: list[str] = []
        self._kept_original = False
        # Run counts live beside the commands file rather than in it, so that
        # launching a command never rewrites the user's command definitions.
        self.run_counts_path = command_file_path.parent / RUN_COUNTS_FILENAME
        self.run_counts: dict[str, int] = self._load_run_counts()
        self.reload_command_trie()

    def _get_system_commands(self):
        """Built-in system commands that are always available"""
        return [
            {
                "name": "Dash Settings",
                "aliases": ["settings", "preferences", "config"],
                "description": "Edit Dash settings",
                "icon": self.settings.paths.settings_command_icons,
                "type": "system",
                "action": "open_settings",
            },
        ]

    # ------------------------------------------------------------ run counts

    def _load_run_counts(self) -> dict[str, int]:
        if self.run_counts_path.exists():
            try:
                data = json.loads(self.run_counts_path.read_text(encoding="utf-8"))
            except ValueError:
                logger.warning("Run counts file is unreadable; set aside as %s", quarantine_file(self.run_counts_path))
                data = {}
            except OSError as error:
                logger.warning("Could not read run counts: %s", error)
                data = {}
            if isinstance(data, dict):
                return {str(name): _safe_run_count(count) for name, count in data.items()}
            return {}

        # One-time migration: earlier builds kept `times_executed` inside
        # commands.toml. Carry those over, then the next write drops them.
        migrated = {}
        for entry in self._read_raw_commands():
            count = _safe_run_count(entry.get("times_executed", 0))
            if count and entry.get("name"):
                migrated[str(entry["name"])] = count
        if migrated:
            self._save_run_counts(migrated)
        return migrated

    def _save_run_counts(self, counts: dict[str, int] | None = None):
        counts = self.run_counts if counts is None else counts
        try:
            atomic_write_text(self.run_counts_path, json.dumps(counts, indent=2, sort_keys=True))
        except OSError as error:
            logger.warning("Could not save run counts: %s", error)

    def increment_run_count(self, name: str) -> int:
        """Increment a command's execution count and return the new value."""
        cmd = self.commands.get(name)
        if cmd is None:
            return 0

        next_count = _safe_run_count(cmd.get("times_executed")) + 1
        cmd["times_executed"] = next_count

        if cmd.get("type") != "system":
            self.run_counts[name] = next_count
            self._save_run_counts()
        return next_count

    def reset_run_count(self, name: str) -> None:
        """Set one command's execution count back to zero."""
        cmd = self.commands.get(name)
        if cmd is not None:
            cmd["times_executed"] = 0
        if self.run_counts.pop(name, None) is not None:
            self._save_run_counts()

    def reset_all_run_counts(self) -> int:
        """Clear every execution count. Returns how many commands had one."""
        cleared = len(self.run_counts)
        for cmd in self.commands.values():
            cmd["times_executed"] = 0
        self.run_counts = {}
        self._save_run_counts()
        return cleared

    # --------------------------------------------------------------- loading

    def _warn(self, message: str) -> None:
        """Log a loading problem and keep it for the GUI, once."""
        logger.warning(message)
        if message not in self.load_warnings:
            self.load_warnings.append(message)

    def _read_command_file(self, report: bool = False) -> list[dict]:
        """The well-formed [[command]] entries of commands.toml.

        A file that is not valid TOML is moved aside (see quarantine_file)
        and Dash carries on without it, so one bad edit never stops it
        starting. Entries that cannot be loaded (no name, a list where text
        belongs) are left out; the file as it was is kept beside it before
        anything is written back without them. With `report` the entries left
        out are noted in load_warnings.
        """
        path = self.command_file_path
        try:
            if not path.exists():
                return []
            with path.open("rb") as f:
                cfg = tomllib.load(f)
        except ValueError as error:  # TOMLDecodeError, or text that is not UTF-8
            logger.error("Commands file %s is unreadable: %s", path, error)
            moved = quarantine_file(path)
            if moved is not None:
                self._warn(
                    f"Your commands file could not be read, so Dash started without your commands. "
                    f"The file was kept as {moved.name} in {moved.parent}. Details: {error}"
                )
            else:
                # Still in place: never save over it, or the commands in it are lost.
                self._unreadable_file = True
                self._warn(f"Your commands file could not be read, so Dash started without your commands. Details: {error}")
            return []
        except OSError as error:
            self._unreadable_file = True
            self._warn(f"Your commands file could not be opened, so Dash started without your commands. Details: {error}")
            return []

        entries = cfg.get("command", [])
        if not isinstance(entries, list):
            entries = [entries]
        kept: list[dict] = []
        problems: list[str] = []
        for position, entry in enumerate(entries, start=1):
            problem = _entry_problem(entry)
            if problem is None:
                kept.append(entry)
                continue
            name = entry.get("name") if isinstance(entry, dict) else None
            label = f"'{name}'" if isinstance(name, str) and name.strip() else f"Command {position}"
            problems.append(f"{label}: {problem}")

        if problems:
            if not self._kept_original:
                self._kept_original = True
                self._keep_original_copy()
            if report:
                count = len(problems)
                noun = "command" if count == 1 else "commands"
                self._warn(f"Left out {count} {noun} that could not be read from {path.name}: " + "; ".join(problems) + ".")
        return kept

    def _keep_original_copy(self) -> None:
        """Copy commands.toml aside before a save drops entries it could not load."""
        path = self.command_file_path
        target = path.with_name(path.name + ".bad")
        counter = 1
        while target.exists():
            target = path.with_name(f"{path.name}.bad{counter}")
            counter += 1
        try:
            atomic_write_text(target, path.read_text(encoding="utf-8"))
        except OSError as error:
            logger.warning("Could not keep a copy of %s: %s", path, error)

    def _load_commands_from_file(self) -> tuple[dict, dict[str, str]]:
        """(commands by name, keyword to command name) from commands.toml.

        Names are claimed before aliases, so a command's own name always
        beats another's alias. A later command with a name already taken is
        left out, and an alias that is already some command's name or alias
        is dropped from the later command; either way a note goes into
        load_warnings rather than stopping the load.
        """
        commands: dict[str, dict] = {}
        keyword_to_command: dict[str, str] = {}
        owners: dict[str, str] = {}  # casefolded keyword -> command name
        pending_aliases: list[tuple[dict, list[str], bool]] = []

        for cmd_data in self._get_system_commands():
            name = cmd_data["name"]
            commands[name] = {
                "name": name,
                "description": cmd_data.get("description", ""),
                "icon": cmd_data.get("icon"),  # Don't set default here
                "type": "system",
                "action": cmd_data.get("action"),
                "times_executed": 0,
            }
            owners[name.casefold()] = name
            keyword_to_command[name] = name
            pending_aliases.append((commands[name], list(cmd_data.get("aliases", [])), False))

        for cmd_data in self._read_command_file(report=True):
            name = cmd_data["name"]
            if name.strip().casefold() in owners:
                self._warn(f"Left out a second command named '{name}': another command already has this name.")
                continue
            location = cmd_data.get("location") or ""

            # Auto-detect type
            cmd_type = cmd_data.get("type")
            if not cmd_type:
                cmd_type = "url" if location.startswith(("http://", "https://")) else "file"

            command_obj = {
                "name": name,
                "description": cmd_data.get("description") or "",
                "aliases": [],
                "icon": cmd_data.get("icon"),  # Don't set default here - let icon_manager handle it
                "type": cmd_type,
                "location": location,
                "times_executed": self.run_counts.get(str(name), 0),
                "_path": Path(location).expanduser() if cmd_type == "file" and location else None,
                "browser": str(cmd_data.get("browser") or "") or None,
                "targets": list(cmd_data.get("targets") or []) if cmd_type == "group" else [],
                **(_launch_fields(cmd_data) if cmd_type == "file" else {}),
                **_recipe_fields(cmd_data),
            }
            commands[name] = command_obj
            owners[name.strip().casefold()] = name
            keyword_to_command[name] = name
            pending_aliases.append((command_obj, list(cmd_data.get("aliases") or []), True))

        for command_obj, aliases, is_user in pending_aliases:
            name = command_obj["name"]
            kept = []
            for alias in aliases:
                key = alias.strip().casefold()
                owner = owners.get(key)
                if not key or owner == name:
                    continue  # empty, or a repeat of its own name or alias
                if owner is not None:
                    if is_user:
                        self._warn(f"The alias '{alias}' of '{name}' was ignored because '{owner}' already uses it.")
                    continue
                owners[key] = name
                keyword_to_command[alias] = name
                kept.append(alias)
            if is_user:
                command_obj["aliases"] = kept

        return commands, keyword_to_command

    def _read_raw_commands(self):
        """Return the user command list from the file (system commands excluded)."""
        return self._read_command_file()

    @staticmethod
    def _command_locations(commands: list[dict]) -> set[str]:
        return {_path_key(Path(str(command.get("location", "")))) for command in commands if command.get("location")}

    # ------------------------------------------------------------ validation

    def validate_target(self, command: dict, known_names: set[str] | None = None) -> str | None:
        """Return an error if a command's location doesn't point at something real.

        A group's targets must name existing commands; `known_names` (casefolded)
        replaces the commands on disk when a batch is being checked.
        """
        command_type = command.get("type", "file")
        location = str(command.get("location", "")).strip()
        if command_type == "group":
            return self._validate_group_targets(command, known_names)
        if command_type == "url":
            parsed = urlparse(location)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                return "Enter a complete http(s) website address."
        elif is_link_location(location):
            if command.get("command_type") == "folder":
                return "Choose an existing folder."
        else:
            path = Path(location).expanduser()
            if not path.exists():
                return "Choose a file or folder that exists."
            if command.get("command_type") == "folder" and not path.is_dir():
                return "Choose an existing folder."
            if command.get("command_type") == "app" and not path.is_file():
                return "Choose an existing file."
        working_folder = str(command.get("working_folder") or "").strip()
        if working_folder and command_type == "file" and not Path(working_folder).expanduser().is_dir():
            return "Choose a working folder that exists, or leave it empty."
        return None

    @staticmethod
    def import_problem(candidate: dict) -> str | None:
        """Why a command from a shared commands file is refused, or None.

        Checked on the text alone, before anything looks at the disk: asking
        Windows whether \\\\server\\share exists already connects to that
        server with the user's sign-in. Only commands arriving from a file
        are held to this; one made in the editor goes where its maker chose.
        """
        cmd_type = candidate.get("type") or "file"
        if cmd_type not in COMMAND_TYPES:
            return f"Dash has no '{cmd_type}' commands, so this one can't be imported."
        if cmd_type == "group":
            return None
        location = str(candidate.get("location", "")).strip()
        for value in (location, *(str(candidate.get(key) or "") for key in LAUNCH_KEYS if key != "arguments")):
            if is_network_location(value):
                return "Commands that open a network location (\\\\server\\share) are not imported. Add it yourself if you trust it."
        scheme = link_scheme(location)
        if cmd_type == "url":
            if scheme not in ("http", "https"):
                return "Only http and https website addresses can be imported."
            return None
        if scheme and scheme not in SAFE_LINK_SCHEMES:
            allowed = ", ".join(f"{name}:" for name in SAFE_LINK_SCHEMES)
            return f"Links starting with '{scheme}:' are not imported because they can run programs or fetch files. Only {allowed} links can be."
        if str(candidate.get("arguments") or "").strip() and ntpath.basename(location).casefold() in SCRIPT_HOSTS:
            return "Commands that give arguments to a command line or script host (cmd, PowerShell and the like) are not imported."
        return None

    def _validate_group_targets(self, command: dict, known_names: set[str] | None) -> str | None:
        targets = [str(target).strip() for target in command.get("targets", []) if str(target).strip()]
        if not targets:
            return "Add at least one command for the group to open."
        if known_names is None:
            known_names = {str(existing.get("name", "")).strip().casefold() for existing in self._read_raw_commands()}
            known_names |= {system["name"].casefold() for system in self._get_system_commands()}
        own_name = str(command.get("name", "")).strip().casefold()
        for target in targets:
            if target.casefold() == own_name:
                return "A group can't open itself."
            if target.casefold() not in known_names:
                return f"'{target}' is not one of your commands."
        return None

    def find_command(self, name: str) -> dict | None:
        """The loaded command with this name, matched without regard to case."""
        command = self.commands.get(name)
        if command is not None:
            return command
        wanted = str(name).strip().casefold()
        return next((cmd for cmd in self.commands.values() if str(cmd.get("name", "")).casefold() == wanted), None)

    def _reserved_keywords(
        self,
        exclude_name: str | None = None,
        *,
        commands: list[dict] | None = None,
    ) -> dict[str, str]:
        """Map every keyword (name/alias) already in use to the command name that owns it."""
        exclude_key = exclude_name.strip().casefold() if exclude_name else None
        reserved: dict[str, str] = {}
        user_commands = self._read_raw_commands() if commands is None else commands
        for existing in [*self._get_system_commands(), *user_commands]:
            existing_name = str(existing.get("name", "")).strip()
            if existing_name.casefold() == exclude_key:
                continue
            for keyword in [existing_name, *existing.get("aliases", [])]:
                text = str(keyword).strip()
                if text:
                    reserved[text.casefold()] = existing_name
        return reserved

    def validate_command(
        self,
        command: dict,
        original_name: str | None = None,
        *,
        validate_target: bool = True,
    ) -> str | None:
        """Return a user-facing validation error, or ``None`` for a savable command."""
        name = str(command.get("name", "")).strip()
        if not name:
            return "Enter a command name."

        if validate_target:
            error = self.validate_target(command)
            if error:
                return error

        keywords = [name, *command.get("aliases", [])]
        normalized_keywords: set[str] = set()
        for keyword in keywords:
            normalized = str(keyword).strip().casefold()
            if not normalized:
                return "Command names and aliases cannot be empty."
            if normalized in normalized_keywords:
                return f"'{str(keyword).strip()}' is already used by this command."
            normalized_keywords.add(normalized)

        reserved = self._reserved_keywords(exclude_name=original_name)
        for keyword in keywords:
            owner = reserved.get(str(keyword).strip().casefold())
            if owner:
                return f"'{str(keyword).strip()}' is already used by '{owner}'."
        return None

    # ---------------------------------------------------------------- editing

    def save_command(self, command: dict, original_name: str | None = None):
        """Insert or update a user command in commands.toml, then reload the trie.

        `command` keys: name, aliases, location, description, icon, type.
        Matching uses `original_name` (for renames) or name.
        """
        error = self.validate_command(command, original_name)
        if error:
            raise ValueError(error)

        commands = self._read_raw_commands()
        match_name = original_name or command.get("name")

        entry = {
            "name": command.get("name", ""),
            "aliases": list(command.get("aliases", [])),
            "location": command.get("location", ""),
            "description": command.get("description", ""),
            "type": command.get("type", "file"),
            **_recipe_fields(command),
        }
        if command.get("icon"):
            entry["icon"] = command["icon"]
        if command.get("browser") and entry["type"] == "url":
            entry["browser"] = command["browser"]
        if entry["type"] == "group":
            entry["location"] = ""
            entry["targets"] = [str(target) for target in command.get("targets", [])]
        if entry["type"] == "file":
            # A launch setting the caller did not mention stays as it was,
            # unless the command now opens something else.
            previous = next((existing for existing in commands if existing.get("name") == match_name), None)
            for key in LAUNCH_KEYS:
                if key in command:
                    value = str(command.get(key) or "").strip()
                elif previous is not None and previous.get("location") == entry["location"]:
                    value = str(previous.get(key) or "").strip()
                else:
                    value = ""
                if value:
                    entry[key] = value

        for i, existing in enumerate(commands):
            if existing.get("name") == match_name:
                commands[i] = entry
                break
        else:
            commands.append(entry)

        # Groups refer to commands by name, so a rename follows into them.
        if original_name and original_name != entry["name"]:
            for existing in commands:
                if existing.get("type") == "group" and original_name in existing.get("targets", []):
                    existing["targets"] = [entry["name"] if target == original_name else target for target in existing["targets"]]

        # A rename keeps its run count.
        if original_name and original_name != entry["name"] and original_name in self.run_counts:
            self.run_counts[entry["name"]] = self.run_counts.pop(original_name)
            self._save_run_counts()

        self._write_raw_commands(commands)
        self.reload_command_trie()

    def delete_command(self, name: str):
        """Remove a user command from commands.toml, then reload the trie."""
        commands = [c for c in self._read_raw_commands() if c.get("name") != name]
        self._write_raw_commands(commands)
        if self.run_counts.pop(name, None) is not None:
            self._save_run_counts()
        self.reload_command_trie()

    # ------------------------------------------------------- import / export

    def drop_clashing_aliases(self, candidates: list[dict]) -> list[dict]:
        """Copies of import candidates without the aliases that could not be
        saved: ones an existing command already uses, the names of other
        candidates, and ones an earlier candidate has. Suggested aliases are
        a convenience, so a clash costs the alias, never the command."""
        return drop_clashing_aliases(candidates, set(self._reserved_keywords()))

    def import_program_commands(self, candidates: list[dict]) -> dict:
        """Append selected program candidates without changing existing commands.

        An alias that is already taken (by a command, or by a candidate
        earlier in this batch) is left off rather than stopping the command
        being added; the summary's "dropped_aliases" lists them by command.
        """
        commands = self._read_raw_commands()
        existing_locations = self._command_locations(commands)
        # Names and aliases claimed earlier in this batch. validate_command
        # only knows about commands already on disk, so without this two
        # candidates sharing a name (a suggestion and a Start Menu shortcut for
        # the same tool) would both be written.
        batch_keywords: set[str] = set()
        reserved = set(self._reserved_keywords(commands=commands))
        imported: list[str] = []
        skipped: list[str] = []
        dropped_aliases: dict[str, list[str]] = {}

        for candidate in candidates:
            location = _path_key(Path(str(candidate.get("location", ""))))
            if location in existing_locations:
                skipped.append(candidate.get("name", ""))
                continue
            name_key = str(candidate.get("name", "")).strip().casefold()
            if name_key in batch_keywords:
                skipped.append(candidate.get("name", ""))
                continue
            aliases: list[str] = []
            for alias in candidate.get("aliases", []):
                key = str(alias).strip().casefold()
                if key and key != name_key and key not in reserved and key not in batch_keywords and key not in {a.casefold() for a in aliases}:
                    aliases.append(str(alias).strip())
                elif key:
                    dropped_aliases.setdefault(str(candidate.get("name", "")), []).append(str(alias))
            candidate = {**candidate, "aliases": aliases}
            if self.validate_command(candidate):
                skipped.append(candidate.get("name", ""))
                dropped_aliases.pop(str(candidate.get("name", "")), None)
                continue
            keywords = {name_key, *(alias.casefold() for alias in aliases)}
            entry = {
                "name": candidate["name"],
                "aliases": aliases,
                "location": candidate.get("location", ""),
                "description": candidate.get("description", ""),
                "type": candidate.get("type", "file"),
                **(_launch_fields(candidate) if candidate.get("type", "file") == "file" else {}),
                **_recipe_fields(candidate),
            }
            if candidate.get("icon"):
                # Set when the user styled the program in the editor before adding it.
                entry["icon"] = candidate["icon"]
            commands.append(entry)
            existing_locations.add(location)
            batch_keywords |= keywords
            imported.append(candidate["name"])

        if imported:
            self._write_raw_commands(commands)
            self.reload_command_trie()

        return {
            "imported": imported,
            "skipped": skipped,
            "candidate_count": len(candidates),
            "dropped_aliases": dropped_aliases,
        }

    def _generalize_path(self, location: str) -> str:
        """Replace the current user's home directory with a portable '~' placeholder."""
        if not location:
            return location
        try:
            relative = Path(location).relative_to(Path.home())
        except ValueError:
            return location
        return str(Path("~") / relative)

    @staticmethod
    def _portable_recipe(cmd: dict) -> dict:
        """Recipe fields for export: a library glyph travels as its name, a
        disk source image is embedded as base64 so it can be rebuilt anywhere."""
        fields = {key: cmd[key] for key in ("icon_glyph", "icon_color", "icon_background") if cmd.get(key)}
        source = cmd.get("icon_source")
        if source and not fields.get("icon_glyph"):
            try:
                fields[ICON_SOURCE_DATA_KEY] = base64.b64encode(Path(str(source)).read_bytes()).decode("ascii")
            except OSError:
                pass
        return fields

    @staticmethod
    def _materialize_recipe(candidate: dict) -> dict:
        """Recipe fields for storing an imported command: embedded source
        image bytes are written into the icon store and referenced by path."""
        fields = {key: candidate[key] for key in ("icon_glyph", "icon_color", "icon_background") if candidate.get(key)}
        if candidate.get("icon_source") and Path(str(candidate["icon_source"])).exists():
            fields["icon_source"] = str(candidate["icon_source"])
        elif candidate.get(ICON_SOURCE_DATA_KEY) and not fields.get("icon_glyph"):
            try:
                source_path = command_source_icon_path(candidate.get("name", ""))
                source_path.write_bytes(base64.b64decode(candidate[ICON_SOURCE_DATA_KEY]))
                fields["icon_source"] = str(source_path)
            except (OSError, ValueError):
                pass
        return fields

    def materialize_candidate_source(self, candidate: dict) -> None:
        """Give an import candidate with embedded source bytes a real source file.

        Done before the candidate is opened in the editor, so the editor sees
        an ordinary recipe it can preview and keep rather than opaque bytes.
        """
        if candidate.get("icon_glyph") or candidate.get("icon_source") or not candidate.get(ICON_SOURCE_DATA_KEY):
            return
        fields = self._materialize_recipe(candidate)
        if fields.get("icon_source"):
            candidate["icon_source"] = fields["icon_source"]
            candidate.pop(ICON_SOURCE_DATA_KEY, None)

    def export_commands(self, names: list[str], file_path: Path) -> int:
        """Write selected user commands to a portable TOML file for sharing.

        Rendered icon paths are dropped since they're specific to this
        machine, but icon recipes travel: library glyphs by name, disk
        artwork embedded. Paths under the user's home directory are
        generalized to '~'.
        """
        commands = self._read_raw_commands()
        selected = [c for c in commands if c.get("name") in names]

        portable = []
        for cmd in selected:
            cmd_type = cmd.get("type", "file")
            location = cmd.get("location", "")
            entry = {
                "name": cmd.get("name", ""),
                "aliases": list(cmd.get("aliases", [])),
                "location": location if cmd_type == "url" else self._generalize_path(location),
                "description": cmd.get("description", ""),
                "type": cmd_type,
                **self._portable_recipe(cmd),
            }
            if cmd.get("browser"):
                entry["browser"] = cmd["browser"]
            if cmd_type == "group":
                entry["targets"] = list(cmd.get("targets", []))
            if cmd_type == "file":
                launch = _launch_fields(cmd)
                for key in ("working_folder", "process_path"):
                    if key in launch:
                        launch[key] = self._generalize_path(launch[key])
                entry.update(launch)
            portable.append(entry)

        blocks = [_serialize_command(cmd) for cmd in portable]
        atomic_write_text(file_path, "\n".join(blocks))
        return len(portable)

    def check_import_candidate(self, candidate: dict) -> tuple[str | None, bool]:
        """Return ``(error, conflict)`` for a command that is not yet stored.

        ``error`` is a validation message if the target is missing or malformed.
        ``conflict`` is True if the name, an alias, or the location collides
        with a command that already exists.

        A candidate read from a shared file (marked ``_shared``) is first held
        to import_problem, whose reason becomes the error; nothing about its
        target is looked up on disk then.
        """
        if candidate.get("_shared"):
            problem = self.import_problem(candidate)
            if problem:
                return problem, False
        existing_commands = self._read_raw_commands()
        existing_locations = self._command_locations(existing_commands)
        reserved = self._reserved_keywords(commands=existing_commands)

        error = self.validate_target(candidate)

        name = str(candidate.get("name", "")).strip()
        keywords = [name, *candidate.get("aliases", [])]
        is_conflict = any(str(k).strip().casefold() in reserved for k in keywords if str(k).strip())
        if not is_conflict and candidate.get("type", "file") not in ("url", "group"):
            is_conflict = _path_key(Path(str(candidate.get("location", "")))) in existing_locations
        return error, is_conflict

    def parse_import_candidates(self, file_path: Path) -> list[dict]:
        """Load candidate commands from an exported TOML file for review before import.

        Each candidate is annotated with ``_error`` (a validation message if its
        target doesn't exist) and ``_conflict`` (True if it would collide with an
        existing command name/alias/location).
        """
        with file_path.open("rb") as f:
            cfg = tomllib.load(f)

        entries = cfg.get("command", [])
        candidates = []
        for cmd_data in entries if isinstance(entries, list) else []:
            problem = _entry_problem(cmd_data)
            if problem:
                logger.warning("Skipped a command in %s: %s", file_path, problem)
                continue
            name = cmd_data["name"].strip()
            location = cmd_data.get("location") or ""
            cmd_type = cmd_data.get("type") or ("url" if location.startswith(("http://", "https://")) else "file")

            candidate = {
                "name": name,
                "aliases": list(cmd_data.get("aliases") or []),
                "location": location,
                "description": cmd_data.get("description") or "",
                "type": cmd_type,
                # Held to import_problem for as long as it is a candidate.
                "_shared": True,
            }
            # icon_source is left behind on purpose: it names a file on the
            # machine that exported it. Only artwork embedded in the export
            # (ICON_SOURCE_DATA_KEY) travels.
            for key in ("icon_glyph", "icon_color", "icon_background", ICON_SOURCE_DATA_KEY, "browser"):
                if cmd_data.get(key):
                    candidate[key] = str(cmd_data[key])
            if cmd_type == "file":
                candidate.update(_launch_fields(cmd_data))
            if cmd_type == "group":
                candidate["location"] = ""
                candidate["targets"] = list(cmd_data.get("targets") or [])
            if self.import_problem(candidate) is None and cmd_type == "file":
                # "~" becomes this user's home folder; links are left alone.
                for key in ("location", "working_folder", "process_path"):
                    if candidate.get(key) and not is_link_location(candidate[key]):
                        candidate[key] = str(Path(candidate[key]).expanduser())
            candidate["_error"], candidate["_conflict"] = self.check_import_candidate(candidate)
            candidates.append(candidate)

        return candidates

    def import_commands(self, candidates: list[dict]) -> dict:
        """Append selected imported commands, never overriding existing ones."""
        commands = self._read_raw_commands()
        existing_locations = self._command_locations(commands)
        reserved = self._reserved_keywords(commands=commands)

        imported: list[str] = []
        skipped: list[str] = []

        known_names = {str(command.get("name", "")).strip().casefold() for command in commands}
        known_names |= {system["name"].casefold() for system in self._get_system_commands()}
        for candidate in candidates:
            name = str(candidate.get("name", "")).strip()
            if (candidate.get("_shared") and self.import_problem(candidate)) or self.validate_target(candidate, known_names):
                skipped.append(name)
                continue

            keywords = [name, *candidate.get("aliases", [])]
            if any(str(k).strip().casefold() in reserved for k in keywords if str(k).strip()):
                skipped.append(name)
                continue

            location = str(candidate.get("location", ""))
            cmd_type = candidate.get("type", "file")
            if cmd_type not in ("url", "group"):
                normalized = _path_key(Path(location))
                if normalized in existing_locations:
                    skipped.append(name)
                    continue
                existing_locations.add(normalized)

            entry = {
                "name": name,
                "aliases": list(candidate.get("aliases", [])),
                "location": location,
                "description": candidate.get("description", ""),
                "type": cmd_type,
                **self._materialize_recipe(candidate),
            }
            if candidate.get("icon"):
                # Set when the user styled the command in the editor before importing.
                entry["icon"] = candidate["icon"]
            if candidate.get("browser") and cmd_type == "url":
                entry["browser"] = candidate["browser"]
            if cmd_type == "group":
                entry["targets"] = [str(target) for target in candidate.get("targets", [])]
            if cmd_type == "file":
                entry.update(_launch_fields(candidate))
            commands.append(entry)
            known_names.add(name.casefold())
            for keyword in keywords:
                text = str(keyword).strip()
                if text:
                    reserved[text.casefold()] = name
            imported.append(name)

        if imported:
            self._write_raw_commands(commands)
            self.reload_command_trie()

        return {"imported": imported, "skipped": skipped, "candidate_count": len(candidates)}

    def existing_command_locations(self) -> set[str]:
        """Return normalized locations of user commands already stored on disk."""
        return self._command_locations(self._read_raw_commands())

    def existing_command_urls(self) -> set[str]:
        """Normalised URLs of the website commands already stored on disk."""
        from .personal_places import url_key

        return {
            url_key(str(command.get("location", "")))
            for command in self._read_raw_commands()
            if str(command.get("location", "")).startswith(("http://", "https://"))
        }

    def has_user_commands(self) -> bool:
        """Whether the user has added any commands of their own (system commands excluded)."""
        return bool(self._read_raw_commands())

    # ------------------------------------------------------------------ icons

    def reprocess_command_icons(self, icon_manager, force: bool = False) -> dict:
        """Resolve and store a permanent icon for every user command.

        Commands that already point at a file in the icon store are skipped
        unless `force` is set. Returns the {name: icon_path} map that was saved.
        """
        commands = self._read_raw_commands()
        for entry in commands:
            if not entry.get("type"):
                location = entry.get("location", "") or ""
                entry["type"] = "url" if location.startswith(("http://", "https://")) else "file"

        updated = icon_manager.reprocess_command_icons(commands, force=force)
        if not updated:
            return updated

        for entry in commands:
            new_path = updated.get(entry.get("name"))
            if new_path:
                entry["icon"] = new_path

        self._write_raw_commands(commands)
        self.reload_command_trie()
        return updated

    # ------------------------------------------------------------ persistence

    def _write_raw_commands(self, commands: list[dict]):
        if getattr(self, "_unreadable_file", False):
            # The file Dash could not read is still there. Set it aside now if
            # that has become possible; otherwise refuse, since writing would
            # replace the commands in it with the (empty) list Dash started with.
            path = self.command_file_path
            if path.exists() and quarantine_file(path) is None:
                raise CommandsFileUnreadableError(
                    f"Dash couldn't read your commands file, so it won't save over it. "
                    f"Close anything that has {path.name} open, then restart Dash."
                )
            self._unreadable_file = False
        blocks = [_serialize_command(cmd) for cmd in commands]
        atomic_write_text(self.command_file_path, "\n".join(blocks))

    def reload_command_trie(self):
        """Load commands.toml again and rebuild the search indexes.

        Never raises for a bad file: problems are noted in load_warnings.
        """
        commands, keyword_to_command = self._load_commands_from_file()
        self.commands = commands

        # Reset and rebuild trie
        case_sensitive = not self.settings.search.ignore_case
        self.lookup_trie = CommandTrie(case_sensitive=case_sensitive)
        for keyword, command_name in keyword_to_command.items():
            self.lookup_trie.insert(keyword, command_name)
        self.keyword_index = {self.lookup_trie.normalize(str(keyword)): name for keyword, name in keyword_to_command.items()}
        self.word_index = WordStartIndex(case_sensitive=case_sensitive)
        self.word_index.build((str(command["name"]), name) for name, command in commands.items())

    # -------------------------------------------------------------- searching

    def _is_exact_match(self, command: dict, text: str) -> bool:
        """True if `text` is the whole of the command's name or one of its aliases."""
        # The keyword index holds every name and alias, built-in commands included.
        return self.keyword_index.get(self.lookup_trie.normalize(text)) == command.get("name")

    def completion_keyword(self, name: str | None, text: str) -> str | None:
        """The name or alias of command `name` that `text` is the start of.

        Results match on aliases as well as names, so the completion has to
        come from whichever one matched: "se" completes Dash Settings through
        its alias "settings". The name wins when both fit. None when nothing fits.
        """
        command = self.commands.get(name) if name else None
        if command is None or not text:
            return None
        normalize = self.lookup_trie.normalize
        wanted = normalize(text)
        # The keyword index holds every name and alias, built-in commands included.
        aliases = [keyword for keyword, owner in self.keyword_index.items() if owner == name]
        for keyword in (str(command.get("name", "")), *aliases):
            if keyword and normalize(keyword).startswith(wanted):
                return keyword
        return None

    def find_matching_commands(self, text: str) -> list[dict]:
        """Return the commands whose name or alias starts with `text`.

        A command whose name or alias is exactly what was typed always comes
        first: an alias is a promise that those letters mean that command.
        The rest follow the `sort_results` setting: most-run first (ties by
        name) or purely by name. Every match is sorted before the list is cut
        to `max_results`, so the order never depends on which matches the
        trie happened to reach first.

        With `match_word_starts` on, commands with a later word of the name
        starting with `text` ("code" for Visual Studio Code) follow all of
        those, in the same order. A search keyword with its query leads only
        when nothing starts with the whole text; otherwise it follows those
        commands, so "google ma" still offers Google Maps first.
        """
        max_results = max(1, int(self.settings.search.max_results or 1))
        by_popularity = self.settings.search.sort_results == "popularity"

        def rank(command: dict):
            if by_popularity:
                return (-_safe_run_count(command.get("times_executed")), command["name"].lower())
            return (command["name"].lower(),)

        prefix_names = self.lookup_trie.search_prefix(text, max_results=None)
        results = [self.commands[name] for name in prefix_names if name in self.commands]
        results.sort(key=lambda command: (0 if self._is_exact_match(command, text) else 1, *rank(command)))

        word_matches: list[dict] = []
        if getattr(self.settings.search, "match_word_starts", False):
            found = set(prefix_names)
            word_matches = [self.commands[name] for name in self.word_index.search_prefix(text) if name in self.commands and name not in found]
            word_matches.sort(key=rank)

        search = self.match_search_keyword(text)
        if search is None:
            return (results + word_matches)[:max_results]

        command, query = search
        results = [result for result in results if result["name"] != command["name"]]
        word_matches = [result for result in word_matches if result["name"] != command["name"]]
        # Typing a keyword and a space says a search is wanted, unless some
        # command's own name carries on with those very letters.
        position = min(len(results), max_results - 1)
        search_row = {**command, "_query": query}
        return (results[:position] + [search_row] + results[position:] + word_matches)[:max_results]

    def match_search_keyword(self, text: str) -> tuple[dict, str] | None:
        """``(command, query)`` when the text is a search keyword, a space,
        and what to search for; None otherwise.

        The longest keyword wins, so a command named "google search" is not
        mistaken for "google" searching for "search". Names that merely
        contain a space ("Remote Desktop") are not keywords and search as usual.
        """
        for index in range(len(text) - 1, 0, -1):
            if text[index] != " ":
                continue
            keyword = self.lookup_trie.normalize(text[:index].strip())
            command = self.commands.get(self.keyword_index.get(keyword, ""))
            if command is not None and command.get("type") == "url" and is_search_link(command.get("location", "")):
                return command, text[index + 1 :]
        return None

    def get_matching_commands(self, main_window: "MainWindow", text):
        results = self.find_matching_commands(text)
        main_window.show_results(results)
        return results

    # -------------------------------------------------------------- execution

    def execute_command(
        self,
        main_window: "MainWindow",
        name: str,
        query: str | None = None,
        *,
        new_instance: bool = False,
    ) -> str | None:
        """Run the command called `name`, if there is one.

        `query` is the text typed after a search keyword; a search link run
        without one opens its site's home page. `new_instance` starts a new
        copy of an app even if one is open and the switch_to_open_apps
        setting is on, for this launch only.

        Returns None on success, or a plain-language reason the launch
        failed so the caller can show it and offer a way to fix the command.
        """
        cmd = self.commands.get(name)
        if cmd is None:
            return None
        error = self._launch(main_window, cmd, query, {cmd["name"]}, new_instance=new_instance)
        # A group counts as opened even when one of its commands did not.
        if error is None or cmd.get("type") == "group":
            self.increment_run_count(cmd["name"])
        return error

    def _launch(
        self,
        main_window: "MainWindow",
        cmd: dict,
        query: str | None,
        opening: set[str],
        *,
        new_instance: bool = False,
    ) -> str | None:
        """Open one command; None on success, otherwise the reason it failed.
        `opening` holds the groups being opened, so a group inside itself stops."""
        try:
            cmd_type = cmd["type"]

            # Handle system commands
            if cmd_type == "system":
                self._execute_system_command(main_window, cmd["action"])
            elif cmd_type == "group":
                return self._launch_group(main_window, cmd, opening)
            elif cmd_type == "url":
                location = cmd["location"]
                if is_search_link(location):
                    location = fill_query(location, query or "")
                self._open_url(location, cmd.get("browser") or self.settings.general.browser)
            else:
                self._open_file(cmd.get("_path") or cmd["location"], **_launch_fields(cmd), switch=not new_instance)
            logger.info("Opened %s", cmd.get("name"))
            return None
        except FileNotFoundError as error:
            return str(error)
        except OSError as error:
            # os.startfile: no app associated, access denied, and the like.
            return f"Windows could not open it: {error.strerror or error}"
        except Exception as error:  # pragma: no cover - last resort
            logger.exception("Unexpected error opening %s", cmd.get("name"))
            return f"Unexpected error: {error}"

    def _launch_group(self, main_window: "MainWindow", group: dict, opening: set[str]) -> str | None:
        """Open every command in a group, in order, carrying on past failures.
        Returns one line per command that did not open, or None."""
        failures: list[str] = []
        for target in group.get("targets", []):
            command = self.find_command(target)
            if command is None:
                failures.append(f"{target}: there is no command with this name any more.")
                continue
            if command["name"] in opening:
                failures.append(f"{command['name']}: skipped, it contains this group.")
                continue
            error = self._launch(main_window, command, None, opening | {command["name"]})
            if command.get("type") == "group":
                # Its own lines already name the commands that did not open.
                failures.extend(error.splitlines() if error else [])
            elif error:
                failures.append(f"{command['name']}: {error}")
            else:
                self.increment_run_count(command["name"])
        return "\n".join(failures) or None

    def _execute_system_command(self, main_window: "MainWindow", action: str):
        """Execute a built-in system command"""
        if action == "open_settings":
            main_window.open_settings_editor()

    def _open_file(
        self,
        file_path,
        *,
        arguments: str = "",
        working_folder: str = "",
        process_path: str = "",
        switch: bool = True,
        operation: str = "open",
    ):
        """Open a file or folder, or a link such as a Store app or Settings page.
        A program that is already open is brought to the front instead, when
        the setting for that is on and `switch` allows it.

        `arguments` go to the program as they are written, through
        ShellExecute (os.startfile), never a shell. `process_path` is the
        program a shortcut runs, used to find its window. `operation` "runas"
        starts it as administrator.
        """
        path = Path(file_path) if isinstance(file_path, str) else file_path

        # Arguments ask for a fresh start with them, and so does "runas".
        switch = switch and not arguments and operation == "open"
        if switch and getattr(self.settings.general, "switch_to_open_apps", False) and switch_to_running(process_path or str(path)):
            return
        if is_link_location(str(path)):
            if arguments or operation != "open":
                os.startfile(str(path), operation, arguments=arguments)
            else:
                os.startfile(str(path))
            return
        if not path.exists():
            raise FileNotFoundError(f"The target no longer exists:\n{path}")
        if working_folder:
            folder = Path(working_folder).expanduser()
            if not folder.is_dir():
                raise FileNotFoundError(f"The working folder no longer exists:\n{folder}")
            working_dir = str(folder)
        elif path.is_file() and path.suffix.lower() != ".lnk":
            # Start programs in their own folder, as Explorer does. Many apps
            # look for config and data next to the executable and fail when
            # launched with Dash's working directory instead. A shortcut
            # starts in the folder it names itself.
            working_dir = str(path.parent)
        else:
            working_dir = None
        if arguments or operation != "open":
            os.startfile(str(path), operation, arguments=arguments, cwd=working_dir)
        else:
            os.startfile(str(path), cwd=working_dir)

    def _open_url(self, url: str, browser_key: str | None = None):
        """Open a URL in the command's browser, the configured one, or the default."""
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        open_in_browser(url, browser_key)

    # ------------------------------------------------------- launch options
    # For the result's context menu. Each takes a command name and returns
    # None on success or a plain-language reason, like execute_command.

    def _local_target(self, cmd: dict | None) -> Path | None:
        """The file or folder an app or file command opens; None for links,
        websites, groups and built-in commands."""
        if cmd is None or cmd.get("type") != "file":
            return None
        location = str(cmd.get("location") or "").strip()
        if not location or is_link_location(location):
            return None
        return cmd.get("_path") or Path(location).expanduser()

    def can_run_as_administrator(self, name: str) -> bool:
        """True for an app or file command (not a folder, link or website)."""
        target = self._local_target(self.commands.get(name))
        return target is not None and not target.is_dir()

    def run_as_administrator(self, name: str) -> str | None:
        """Start an app or file command elevated (ShellExecute's "runas"),
        with its arguments and working folder. Saying No to the Windows
        prompt is not an error: None comes back and nothing is counted."""
        cmd = self.commands.get(name)
        if not self.can_run_as_administrator(name):
            return "Only apps and files can be run as administrator."
        try:
            self._open_file(cmd.get("_path") or cmd["location"], **_launch_fields(cmd), switch=False, operation="runas")
        except FileNotFoundError as error:
            return str(error)
        except OSError as error:
            if getattr(error, "winerror", None) == _ERROR_CANCELLED:
                return None
            return f"Windows could not open it: {error.strerror or error}"
        self.increment_run_count(cmd["name"])
        return None

    def containing_folder(self, name: str) -> Path | None:
        """The folder "Open containing folder" shows, or None when the
        command has none. A shortcut with a known program shows the program."""
        cmd = self.commands.get(name)
        target = self._local_target(cmd)
        if target is None:
            return None
        if target.suffix.lower() == ".lnk" and cmd.get("process_path"):
            target = Path(cmd["process_path"]).expanduser()
        return target if target.is_dir() else target.parent

    def open_containing_folder(self, name: str) -> str | None:
        """Show the command's file selected in File Explorer, or open the
        folder itself for a folder command."""
        cmd = self.commands.get(name)
        target = self._local_target(cmd)
        if target is None:
            return "This command does not open a file or folder."
        if target.suffix.lower() == ".lnk" and cmd.get("process_path"):
            target = Path(cmd["process_path"]).expanduser()
        try:
            if target.is_dir():
                os.startfile(str(target))
                return None
            if not target.exists():
                return f"The target no longer exists:\n{target}"
            explorer = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "explorer.exe"
            # One command line, not a list: Explorer wants /select,"path" and
            # would misread the quoting a list gets. No shell is involved.
            subprocess.Popen(f'"{explorer}" /select,"{target}"')
        except OSError as error:
            return f"Windows could not open it: {error.strerror or error}"
        return None

    def copy_text(self, name: str, query: str | None = None) -> str | None:
        """What "Copy path" or "Copy address" puts on the clipboard: the file
        or folder path, or the web address (a search link filled in with
        `query`, or its site with none). None for groups and built-in
        commands; the GUI does the copying."""
        cmd = self.commands.get(name)
        if cmd is None:
            return None
        location = str(cmd.get("location") or "")
        if cmd.get("type") == "url":
            return fill_query(location, query or "") if is_search_link(location) else location
        if cmd.get("type") == "file" and location:
            return location if is_link_location(location) else str(cmd.get("_path") or Path(location).expanduser())
        return None

    # ----------------------------------------------------------------- groups

    def groups_containing(self, name: str) -> list[str]:
        """Names of the groups that open the command `name`, so deleting it
        can say which groups will lose it."""
        wanted = str(name).strip().casefold()
        return [
            command["name"]
            for command in self.commands.values()
            if command.get("type") == "group" and any(str(target).strip().casefold() == wanted for target in command.get("targets", []))
        ]

    def group_summary(self, name: str, max_length: int = 60) -> str:
        """ "Opens: Mail, Calendar, Slack and 2 more" for a group, to show when
        it has no description of its own. Empty for anything else."""
        command = self.find_command(name)
        if command is None or command.get("type") != "group":
            return ""
        targets = [str(target).strip() for target in command.get("targets", []) if str(target).strip()]
        if not targets:
            return ""
        # Targets are matched without regard to case; show the names as they are.
        targets = [(self.find_command(target) or {}).get("name", target) for target in targets]
        shown: list[str] = []
        for index, target in enumerate(targets):
            remaining = len(targets) - index - 1
            more = f" and {remaining} more" if remaining else ""
            if shown and len("Opens: " + ", ".join([*shown, target]) + more) > max_length:
                break
            shown.append(target)
        text = "Opens: " + ", ".join(shown)
        if len(text) > max_length:
            text = text[: max(len("Opens: ") + 1, max_length - 1)].rstrip(", ") + "\u2026"
        remaining = len(targets) - len(shown)
        if remaining:
            text += f" and {remaining} more"
        return text
