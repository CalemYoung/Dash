import tomllib
from pathlib import Path
from .command_trie import CommandTrie
from .launcher_gui import MainWindow
import os
import webbrowser
import subprocess
from urllib.parse import urlparse
from .settings import Settings
from .calculator import eval_expression
from PyQt6.QtWidgets import QApplication
import pyperclip
from .installed_programs import discover_recent_program_commands


def _toml_str(value: str) -> str:
    """Serialize a string, preferring literal (single-quote) form for paths."""
    text = str(value)
    if "'" not in text and "\n" not in text:
        return f"'{text}'"
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def _serialize_command(cmd: dict) -> str:
    """Render a single [[command]] TOML block matching the file's style."""
    lines = ["[[command]]"]
    lines.append(f'name = "{cmd.get("name", "")}"')
    aliases = ", ".join(f'"{a}"' for a in cmd.get("aliases", []))
    lines.append(f"aliases = [{aliases}]")
    lines.append(f"location = {_toml_str(cmd.get('location', ''))}")
    lines.append(f'description = "{cmd.get("description", "")}"')
    if cmd.get("icon"):
        lines.append(f"icon = {_toml_str(cmd['icon'])}")
    if cmd.get("times_executed"):
        lines.append(f"times_executed = {int(cmd['times_executed'])}")
    return "\n".join(lines) + "\n"


class CommandManager:
    def __init__(self, command_file_path: Path, settings: Settings):
        self.command_file_path = command_file_path
        self.settings = settings
        self.commands = {}
        self.lookup_trie = CommandTrie()
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
            {
                "name": "Dash Quit",
                "aliases": ["quit", "exit"],
                "description": "Quit Dash",
                "icon": self.settings.paths.quit_command_icon,
                "type": "system",
                "action": "quit",
            },
        ]

    def _load_commands_from_file(self):
        with self.command_file_path.open("rb") as f:
            cfg = tomllib.load(f)

        commands_list = cfg.get("command", [])
        commands = {}
        keyword_to_command = {}
        conflicts = []

        # Add system commands first
        for cmd_data in self._get_system_commands():
            name = cmd_data["name"]
            aliases = cmd_data.get("aliases", [])

            command_obj = {
                "name": name,
                "description": cmd_data.get("description", ""),
                "icon": cmd_data.get("icon"),  # Don't set default here
                "type": "system",
                "action": cmd_data.get("action"),
                "times_executed": 0,
            }

            commands[name] = command_obj

            all_keywords = [name, *aliases]
            for keyword in all_keywords:
                keyword_to_command[keyword] = name

        # Add user commands from file
        for cmd_data in commands_list:
            name = cmd_data.get("name")
            aliases = cmd_data.get("aliases", [])
            location = cmd_data.get("location", "")
            times_executed = cmd_data.get("times_executed", 0)

            # Auto-detect type
            cmd_type = cmd_data.get("type")
            if not cmd_type:
                cmd_type = "url" if location.startswith(("http://", "https://")) else "file"

            command_obj = {
                "name": name,
                "description": cmd_data.get("description", ""),
                "aliases": list(aliases),
                "icon": cmd_data.get("icon"),  # Don't set default here - let icon_manager handle it
                "type": cmd_type,
                "location": location,
                "times_executed": times_executed,
                "_path": Path(location).expanduser() if cmd_type == "file" else None,
            }

            commands[name] = command_obj

            all_keywords = [name, *aliases]
            for keyword in all_keywords:
                if keyword in keyword_to_command:
                    conflicts.append((keyword, keyword_to_command[keyword], name))
                keyword_to_command[keyword] = name

        return commands, keyword_to_command, conflicts

    def _read_raw_commands(self):
        """Return the user command list from the file (system commands excluded)."""
        if not self.command_file_path.exists():
            return []
        with self.command_file_path.open("rb") as f:
            cfg = tomllib.load(f)
        return cfg.get("command", [])

    @staticmethod
    def _command_locations(commands: list[dict]) -> set[str]:
        return {
            os.path.normcase(os.path.abspath(str(command.get("location", ""))))
            for command in commands
            if command.get("location")
        }

    def validate_target(self, command: dict) -> str | None:
        """Return an error if a command's location doesn't point at something real."""
        command_type = command.get("type", "file")
        location = str(command.get("location", "")).strip()
        if command_type == "url":
            parsed = urlparse(location)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                return "Enter a complete http(s) website address."
        else:
            path = Path(location).expanduser()
            if not path.exists():
                return "Choose a file or folder that exists."
            if command.get("command_type") == "folder" and not path.is_dir():
                return "Choose an existing folder."
            if command.get("command_type") == "app" and not path.is_file():
                return "Choose an existing file."
        return None

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

    def save_command(self, command: dict, original_name: str | None = None):
        """Insert or update a user command in commands.toml, then reload the trie.

        `command` keys: name, aliases, location, description, icon, type,
        times_executed. Matching uses `original_name` (for renames) or name.
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
        }
        if command.get("icon"):
            entry["icon"] = command["icon"]
        if command.get("times_executed"):
            entry["times_executed"] = command["times_executed"]

        for i, existing in enumerate(commands):
            if existing.get("name") == match_name:
                commands[i] = entry
                break
        else:
            commands.append(entry)

        self._write_raw_commands(commands)
        self.reload_command_trie()

    def delete_command(self, name: str):
        """Remove a user command from commands.toml, then reload the trie."""
        commands = [c for c in self._read_raw_commands() if c.get("name") != name]
        self._write_raw_commands(commands)
        self.reload_command_trie()

    def import_recent_program_commands(self, days: int = 365, max_commands: int | None = None) -> dict:
        """Discover and append installed programs to commands.toml."""
        candidates = discover_recent_program_commands(days=days, max_commands=max_commands)
        return self.import_program_commands(candidates)

    def import_program_commands(self, candidates: list[dict]) -> dict:
        """Append selected program candidates without changing existing commands."""
        commands = self._read_raw_commands()
        existing_locations = {
            os.path.normcase(os.path.abspath(str(command.get("location", ""))))
            for command in commands
            if command.get("location")
        }
        imported: list[str] = []
        skipped: list[str] = []

        for candidate in candidates:
            location = os.path.normcase(os.path.abspath(str(candidate.get("location", ""))))
            if location in existing_locations:
                skipped.append(candidate.get("name", ""))
                continue
            if self.validate_command(candidate):
                skipped.append(candidate.get("name", ""))
                continue
            commands.append(candidate)
            existing_locations.add(location)
            imported.append(candidate["name"])

        if imported:
            self._write_raw_commands(commands)
            self.reload_command_trie()

        return {"imported": imported, "skipped": skipped, "candidate_count": len(candidates)}

    def _generalize_path(self, location: str) -> str:
        """Replace the current user's home directory with a portable '~' placeholder."""
        if not location:
            return location
        try:
            relative = Path(location).relative_to(Path.home())
        except ValueError:
            return location
        return str(Path("~") / relative)

    def export_commands(self, names: list[str], file_path: Path) -> int:
        """Write selected user commands to a portable TOML file for sharing.

        Icons and execution counts are dropped since they're specific to this
        machine; paths under the user's home directory are generalized to '~'.
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
            }
            portable.append(entry)

        blocks = [_serialize_command(cmd) for cmd in portable]
        file_path.write_text("\n".join(blocks), encoding="utf-8")
        return len(portable)

    def parse_import_candidates(self, file_path: Path) -> list[dict]:
        """Load candidate commands from an exported TOML file for review before import.

        Each candidate is annotated with ``_error`` (a validation message if its
        target doesn't exist) and ``_conflict`` (True if it would collide with an
        existing command name/alias/location).
        """
        with file_path.open("rb") as f:
            cfg = tomllib.load(f)

        existing_commands = self._read_raw_commands()
        existing_locations = self._command_locations(existing_commands)
        reserved = self._reserved_keywords(commands=existing_commands)

        candidates = []
        for cmd_data in cfg.get("command", []):
            name = str(cmd_data.get("name", "")).strip()
            aliases = list(cmd_data.get("aliases", []))
            location = str(cmd_data.get("location", ""))
            cmd_type = cmd_data.get("type") or ("url" if location.startswith(("http://", "https://")) else "file")
            expanded_location = location if cmd_type == "url" else str(Path(location).expanduser())

            candidate = {
                "name": name,
                "aliases": aliases,
                "location": expanded_location,
                "description": cmd_data.get("description", ""),
                "type": cmd_type,
            }

            candidate["_error"] = self.validate_target(candidate)

            keywords = [name, *aliases]
            is_conflict = any(str(k).strip().casefold() in reserved for k in keywords if str(k).strip())
            if not is_conflict and cmd_type != "url":
                normalized = os.path.normcase(os.path.abspath(expanded_location))
                is_conflict = normalized in existing_locations
            candidate["_conflict"] = is_conflict

            candidates.append(candidate)

        return candidates

    def import_commands(self, candidates: list[dict]) -> dict:
        """Append selected imported commands, never overriding existing ones."""
        commands = self._read_raw_commands()
        existing_locations = self._command_locations(commands)
        reserved = self._reserved_keywords(commands=commands)

        imported: list[str] = []
        skipped: list[str] = []

        for candidate in candidates:
            name = str(candidate.get("name", "")).strip()
            if self.validate_target(candidate):
                skipped.append(name)
                continue

            keywords = [name, *candidate.get("aliases", [])]
            if any(str(k).strip().casefold() in reserved for k in keywords if str(k).strip()):
                skipped.append(name)
                continue

            location = str(candidate.get("location", ""))
            cmd_type = candidate.get("type", "file")
            if cmd_type != "url":
                normalized = os.path.normcase(os.path.abspath(location))
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
            }
            commands.append(entry)
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

    def _write_raw_commands(self, commands: list[dict]):
        blocks = [_serialize_command(cmd) for cmd in commands]
        self.command_file_path.write_text("\n".join(blocks), encoding="utf-8")

    def reload_command_trie(self):
        commands, keyword_to_command, conflicts = self._load_commands_from_file()

        if conflicts:
            conflict_details = "\n".join([f"Keyword '{k}' conflicts between '{cmd1}' and '{cmd2}'" for k, cmd1, cmd2 in conflicts])
            raise ValueError(f"Keyword conflicts found:\n{conflict_details}")

        self.commands = commands

        # Reset and rebuild trie
        self.lookup_trie = CommandTrie()
        for keyword, command_name in keyword_to_command.items():
            self.lookup_trie.insert(keyword, command_name)

    def get_matching_commands(self, main_window: MainWindow, text):
        max_results = self.settings.search.max_results

        # Get unique command names from trie
        command_names = self.lookup_trie.search_prefix(text, max_results=max_results)

        # Get full command data for display
        results = []
        for cmd_name in command_names:
            cmd = self.commands.get(cmd_name)
            if cmd:
                results.append(cmd)

        # Sort by command name
        results = sorted(results, key=lambda x: x["name"].lower())
        main_window.show_results(results)
        return results

    def execute_command(self, main_window: MainWindow, text: str):
        command_names = self.lookup_trie.search_prefix(text, max_results=1)

        cmd = None
        if command_names and command_names[0].lower() == text.lower():
            cmd = self.commands.get(command_names[0])
        elif text.startswith("= "):
            try:
                calc_result = str(text.removeprefix("= "))
                pyperclip.copy(calc_result)
                print(f"Result: {calc_result} (copied to clipboard)")
            except ValueError:
                pass

        if cmd:
            try:
                cmd_type = cmd["type"]

                # Handle system commands
                if cmd_type == "system":
                    self._execute_system_command(main_window, cmd["action"])
                elif cmd_type == "url":
                    self._open_url(cmd["location"])
                elif cmd_type == "file":
                    self._open_file(cmd.get("_path") or cmd["location"])
                elif cmd_type == "script":
                    self._run_script(cmd["location"])
                print(f"Executing: {cmd['description']}")

                cmd["times_executed"] = cmd["times_executed"] + 1

            except Exception as e:
                main_window.display_error_popup(f"Error: {e}")

    def _execute_system_command(self, main_window: MainWindow, action: str):
        """Execute a built-in system command"""
        if action == "open_settings":
            main_window.open_settings_file()
        elif action == "open_commands":
            main_window.open_commands_file()
        elif action == "quit":
            app = QApplication.instance()
            if app:
                app.quit()

    def _run_script(self, script_path: str):
        """Execute a Python script"""
        result = subprocess.run(["python", script_path], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Script failed: {result.stderr}")

    def _open_file(self, file_path):
        """Open a file or folder"""
        path = Path(file_path) if isinstance(file_path, str) else file_path

        if not path.exists():
            raise FileNotFoundError(f"Path does not exist: {path}")
        os.startfile(path)

    def _open_url(self, url: str):
        """Open a URL in the default browser"""
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        webbrowser.open(url)