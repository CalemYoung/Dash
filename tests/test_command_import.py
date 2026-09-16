from pathlib import Path

from src.command_manager import CommandManager
from src.settings import Settings


def test_selected_program_import_does_not_replace_existing_command(tmp_path: Path):
    commands_path = tmp_path / "commands.toml"
    existing_target = tmp_path / "Existing.exe"
    new_target = tmp_path / "New.exe"
    existing_target.touch()
    new_target.touch()
    commands_path.write_text(
        f"""[[command]]
name = "Existing"
aliases = []
location = '{existing_target}'
description = "Existing command"
""",
        encoding="utf-8",
    )
    manager = CommandManager(commands_path, Settings())

    summary = manager.import_program_commands(
        [
            {
                "name": "Different name",
                "aliases": [],
                "location": str(existing_target),
                "description": "Must not replace existing command",
                "type": "file",
            },
            {
                "name": "New Program",
                "aliases": [],
                "location": str(new_target),
                "description": "Opens New Program",
                "type": "file",
            },
        ]
    )

    assert summary["imported"] == ["New Program"]
    assert summary["skipped"] == ["Different name"]
    stored = manager._read_raw_commands()
    assert [(command["name"], command["location"]) for command in stored] == [
        ("Existing", str(existing_target)),
        ("New Program", str(new_target)),
    ]