# src/settings.py
import tomllib
import json
import sys
from pathlib import Path
from dataclasses import asdict, dataclass, field
from textwrap import dedent

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class GeneralSettings:
    hotkey: str = "alt+f"
    show_on_screen_with_mouse: bool = False


@dataclass
class UISettings:
    program_width: int = 500
    search_height: int = 70
    results_height: int = 160
    editor_height: int = 600
    window_opacity: float = 1.0
    search_font_size: int = 32
    result_font_size: int = 14
    description_font_size: int = 9


@dataclass
class SearchSettings:
    max_results: int = 10
    autocomplete: bool = True
    show_descriptions: bool = True
    show_command_tree: bool = True


@dataclass
class ShortcutSettings:
    edit_selected_command: str = "Ctrl+Return"
    new_command: str = "Ctrl+N"


@dataclass
class PathSettings:
    scripts_folder: str = "assets/scripts"
    program_icon: str = "assets/icons/icon.png"
    default_command_icon: str = "assets/icons/icon.png"
    folder_icon: str = "assets/icons/folder.png"
    file_icon: str = "assets/icons/file.png"
    url_command_icon: str = "assets/icons/url.svg"
    cache_folder: str = "assets/cache"
    calculator_icon: str = "assets/icons/calculator.png"
    no_result_icon: str = "assets/icons/no_result.png"
    settings_command_icons: str = "assets/icons/settings.png"
    quit_command_icon: str = "assets/icons/quit.png"


@dataclass
class Settings:
    """Application settings loaded from settings.toml"""

    general: GeneralSettings = field(default_factory=GeneralSettings)
    ui: UISettings = field(default_factory=UISettings)
    search: SearchSettings = field(default_factory=SearchSettings)
    shortcuts: ShortcutSettings = field(default_factory=ShortcutSettings)
    paths: PathSettings = field(default_factory=PathSettings)

    @staticmethod
    def _resolve_asset_path(raw_path: str) -> str:
        """Turn a project-relative asset path into a real filesystem path."""
        if not raw_path:
            return raw_path

        path = Path(raw_path)
        if path.is_absolute():
            return str(path)

        candidates = [
            PROJECT_ROOT / path,
            Path.cwd() / path,
        ]

        if getattr(sys, "_MEIPASS", None):
            candidates.insert(0, Path(sys._MEIPASS) / path)

        for candidate in candidates:
            try:
                if candidate.exists():
                    return str(candidate.resolve())
            except OSError:
                continue

        return str((PROJECT_ROOT / path).resolve())

    def normalize_resource_paths(self) -> "Settings":
        """Ensure path-based assets resolve to the actual bundle on disk."""
        for field_name in vars(self.paths):
            value = getattr(self.paths, field_name)
            if isinstance(value, str):
                setattr(self.paths, field_name, self._resolve_asset_path(value))
        return self

    @classmethod
    def load(cls, settings_path: Path) -> "Settings":
        """Load settings from TOML file, using defaults for missing values"""
        if not settings_path.exists():
            return cls().normalize_resource_paths()  # Return defaults if file doesn't exist

        with settings_path.open("rb") as f:
            data = tomllib.load(f)

        settings = cls(
            general=GeneralSettings(**data.get("general", {})),
            ui=UISettings(**data.get("ui", {})),
            search=SearchSettings(**data.get("search", {})),
            shortcuts=ShortcutSettings(**data.get("shortcuts", {})),
            paths=PathSettings(**data.get("paths", {})),
        )
        return settings.normalize_resource_paths()

    @classmethod
    def load_or_create_default(cls, settings_path: Path) -> "Settings":
        """Load settings or create default file if it doesn't exist"""
        if not settings_path.exists():
            cls._create_default_file(settings_path)
        return cls.load(settings_path)

    def save(self, settings_path: Path):
        """Write settings using the same section structure accepted by load()."""
        sections = {
            "general": asdict(self.general),
            "ui": asdict(self.ui),
            "search": asdict(self.search),
            "shortcuts": asdict(self.shortcuts),
            "paths": asdict(self.paths),
        }
        lines = []
        for section, values in sections.items():
            lines.append(f"[{section}]")
            for key, value in values.items():
                if isinstance(value, bool):
                    rendered = str(value).lower()
                elif isinstance(value, (int, float)):
                    rendered = str(value)
                else:
                    rendered = json.dumps(str(value))
                lines.append(f"{key} = {rendered}")
            lines.append("")
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text("\n".join(lines), encoding="utf-8")

    @staticmethod
    def _create_default_file(settings_path: Path):
        """Create a default settings.toml file"""

        default_content = dedent("""\
        [general]
        hotkey = "alt+f"
        show_on_screen_with_mouse = false

        [ui]
        program_width = 500
        search_height = 70
        results_height = 160
        editor_height = 600
        window_opacity = 1.0
        search_font_size = 32
        result_font_size = 14
        description_font_size = 9

        [search]
        max_results = 10
        autocomplete = true
        show_descriptions = true
        show_command_tree = true

        [shortcuts]
        edit_selected_command = "Ctrl+Return"
        new_command = "Ctrl+N"

        [paths]
        scripts_folder = "assets/scripts"
        program_icon = "assets/icons/icon.png"
        default_command_icon = "assets/icons/icon.png"
        folder_icon = "assets/icons/folder.png"
        file_icon = "assets/icons/file.png"
        url_command_icon = "assets/icons/url.svg"
        cache_folder = "assets/cache"
        calculator_icon = "assets/icons/calculator.png"
        no_result_icon = "assets/icons/no_result.png"
        settings_command_icons = "assets/icons/settings.png"
        quit_command_icon = "assets/icons/quit.png"
        """)

        settings_path.write_text(default_content)
        print(f"Created default settings file: {settings_path}")
