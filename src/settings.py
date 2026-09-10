# src/settings.py
import tomllib
import shutil
import sys
from pathlib import Path
from dataclasses import asdict, dataclass, field, fields

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.default.toml"

with DEFAULT_SETTINGS_PATH.open("rb") as default_settings_file:
    DEFAULT_SETTINGS = tomllib.load(default_settings_file)


def toml_str(value) -> str:
    """Serialize a string as TOML, preferring the literal form for paths.

    Literal (single-quoted) strings need no escaping, which keeps Windows
    paths readable. Anything containing a single quote or a newline falls
    back to a basic (double-quoted) string with escapes.
    """
    text = str(value)
    if "'" not in text and "\n" not in text and "\r" not in text:
        return f"'{text}'"
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "\\r").replace("\n", "\\n")
    return f'"{escaped}"'


def toml_value(value) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    return toml_str(value)


def _from_section(cls, data: dict):
    """Build a settings dataclass, ignoring keys it no longer knows about.

    Settings files written by older or newer versions of Dash may carry keys
    this version does not have; they should not stop the app from starting.
    """
    known = {f.name for f in fields(cls)}
    return cls(**{key: value for key, value in data.items() if key in known})


@dataclass
class GeneralSettings:
    hotkey: str = DEFAULT_SETTINGS["general"]["hotkey"]
    show_on_screen_with_mouse: bool = DEFAULT_SETTINGS["general"]["show_on_screen_with_mouse"]
    check_updates_on_startup: bool = DEFAULT_SETTINGS["general"]["check_updates_on_startup"]


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


SORT_RESULTS_OPTIONS = ("popularity", "name")


@dataclass
class SearchSettings:
    max_results: int = DEFAULT_SETTINGS["search"]["max_results"]
    autocomplete: bool = DEFAULT_SETTINGS["search"]["autocomplete"]
    ignore_case: bool = DEFAULT_SETTINGS["search"]["ignore_case"]
    sort_results: str = DEFAULT_SETTINGS["search"]["sort_results"]
    show_descriptions: bool = DEFAULT_SETTINGS["search"]["show_descriptions"]
    show_run_counter: bool = DEFAULT_SETTINGS["search"]["show_run_counter"]
    show_edit_button: bool = DEFAULT_SETTINGS["search"]["show_edit_button"]
    show_command_tree: bool = DEFAULT_SETTINGS["search"]["show_command_tree"]


@dataclass
class ShortcutSettings:
    edit_selected_command: str = DEFAULT_SETTINGS["shortcuts"]["edit_selected_command"]
    new_command: str = DEFAULT_SETTINGS["shortcuts"]["new_command"]


@dataclass
class PathSettings:
    program_icon: str = DEFAULT_SETTINGS["paths"]["program_icon"]
    default_command_icon: str = DEFAULT_SETTINGS["paths"]["default_command_icon"]
    folder_icon: str = DEFAULT_SETTINGS["paths"]["folder_icon"]
    file_icon: str = DEFAULT_SETTINGS["paths"]["file_icon"]
    url_command_icon: str = DEFAULT_SETTINGS["paths"]["url_command_icon"]
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
            general=_from_section(GeneralSettings, data.get("general", {})),
            ui=_from_section(UISettings, data.get("ui", {})),
            search=_from_section(SearchSettings, data.get("search", {})),
            shortcuts=_from_section(ShortcutSettings, data.get("shortcuts", {})),
            paths=_from_section(PathSettings, data.get("paths", {})),
        )
        if settings.search.sort_results not in SORT_RESULTS_OPTIONS:
            settings.search.sort_results = SORT_RESULTS_OPTIONS[0]
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
                lines.append(f"{key} = {toml_value(value)}")
            lines.append("")
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text("\n".join(lines), encoding="utf-8")

    @staticmethod
    def _create_default_file(settings_path: Path):
        """Create a default settings.toml file"""
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(DEFAULT_SETTINGS_PATH, settings_path)
        print(f"Created default settings file: {settings_path}")
