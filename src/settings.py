# src/settings.py
import tomllib
import json
import shutil
import sys
from pathlib import Path
from dataclasses import asdict, dataclass, field

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.default.toml"

with DEFAULT_SETTINGS_PATH.open("rb") as default_settings_file:
    DEFAULT_SETTINGS = tomllib.load(default_settings_file)


@dataclass
class GeneralSettings:
    hotkey: str = DEFAULT_SETTINGS["general"]["hotkey"]
    show_on_screen_with_mouse: bool = DEFAULT_SETTINGS["general"]["show_on_screen_with_mouse"]


@dataclass
class UISettings:
    program_width: int = DEFAULT_SETTINGS["ui"]["program_width"]
    search_height: int = DEFAULT_SETTINGS["ui"]["search_height"]
    results_height: int = DEFAULT_SETTINGS["ui"]["results_height"]
    editor_height: int = DEFAULT_SETTINGS["ui"]["editor_height"]
    window_opacity: float = DEFAULT_SETTINGS["ui"]["window_opacity"]
    search_font_size: int = DEFAULT_SETTINGS["ui"]["search_font_size"]
    search_text_color: str = DEFAULT_SETTINGS["ui"]["search_text_color"]
    result_font_size: int = DEFAULT_SETTINGS["ui"]["result_font_size"]
    result_text_color: str = DEFAULT_SETTINGS["ui"]["result_text_color"]
    description_font_size: int = DEFAULT_SETTINGS["ui"]["description_font_size"]
    description_text_color: str = DEFAULT_SETTINGS["ui"]["description_text_color"]
    clock_font_size: int = DEFAULT_SETTINGS["ui"]["clock_font_size"]
    clock_day_text_color: str = DEFAULT_SETTINGS["ui"]["clock_day_text_color"]
    clock_date_text_color: str = DEFAULT_SETTINGS["ui"]["clock_date_text_color"]


@dataclass
class SearchSettings:
    max_results: int = DEFAULT_SETTINGS["search"]["max_results"]
    autocomplete: bool = DEFAULT_SETTINGS["search"]["autocomplete"]
    show_descriptions: bool = DEFAULT_SETTINGS["search"]["show_descriptions"]
    show_command_tree: bool = DEFAULT_SETTINGS["search"]["show_command_tree"]


@dataclass
class ShortcutSettings:
    edit_selected_command: str = DEFAULT_SETTINGS["shortcuts"]["edit_selected_command"]
    new_command: str = DEFAULT_SETTINGS["shortcuts"]["new_command"]


@dataclass
class PathSettings:
    scripts_folder: str = DEFAULT_SETTINGS["paths"]["scripts_folder"]
    program_icon: str = DEFAULT_SETTINGS["paths"]["program_icon"]
    default_command_icon: str = DEFAULT_SETTINGS["paths"]["default_command_icon"]
    folder_icon: str = DEFAULT_SETTINGS["paths"]["folder_icon"]
    file_icon: str = DEFAULT_SETTINGS["paths"]["file_icon"]
    url_command_icon: str = DEFAULT_SETTINGS["paths"]["url_command_icon"]
    cache_folder: str = DEFAULT_SETTINGS["paths"]["cache_folder"]
    calculator_icon: str = DEFAULT_SETTINGS["paths"]["calculator_icon"]
    no_result_icon: str = DEFAULT_SETTINGS["paths"]["no_result_icon"]
    settings_command_icons: str = DEFAULT_SETTINGS["paths"]["settings_command_icons"]
    quit_command_icon: str = DEFAULT_SETTINGS["paths"]["quit_command_icon"]


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
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(DEFAULT_SETTINGS_PATH, settings_path)
        print(f"Created default settings file: {settings_path}")
