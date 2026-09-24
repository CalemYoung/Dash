# src/settings.py
import logging
import tomllib
import shutil
import sys
from pathlib import Path
from dataclasses import asdict, dataclass, field, fields

from .fileio import atomic_write_text, quarantine_file

log = logging.getLogger(__name__)

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
    if "'" not in text and not any(_needs_escape(char) for char in text):
        return f"'{text}'"
    escaped = []
    for char in text:
        if char == "\\":
            escaped.append("\\\\")
        elif char == '"':
            escaped.append('\\"')
        elif char in _SHORT_ESCAPES:
            escaped.append(_SHORT_ESCAPES[char])
        elif _needs_escape(char):
            escaped.append(f"\\u{ord(char):04X}")
        else:
            escaped.append(char)
    return '"' + "".join(escaped) + '"'


_SHORT_ESCAPES = {"\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r"}


def _needs_escape(char: str) -> bool:
    """TOML strings cannot hold control characters (other than tab) as they are."""
    code = ord(char)
    return (code < 0x20 and char != "\t") or code == 0x7F


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
    return cls(**_valid_values(cls, data))


def _valid_values(cls, data, problems: list[str] | None = None) -> dict:
    """The keys of `data` that `cls` knows, with values of the right type.

    A hand-edited file can put text where a number belongs (or a number
    where a whole section belongs); those values fall back to the default
    instead of failing later, and each is noted in `problems`.
    """
    if not isinstance(data, dict):
        if problems is not None:
            problems.append(f"[{cls.__name__}] is not a section")
        return {}
    values = {}
    for item in fields(cls):
        if item.name not in data:
            continue
        value = data[item.name]
        expected = type(item.default)
        if expected is float and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        if isinstance(value, expected) and (expected is bool or not isinstance(value, bool)):
            values[item.name] = value
        elif problems is not None:
            problems.append(item.name)
    return values


@dataclass
class GeneralSettings:
    hotkey: str = DEFAULT_SETTINGS["general"]["hotkey"]
    launcher_screen: str = DEFAULT_SETTINGS["general"]["launcher_screen"]
    browser: str = DEFAULT_SETTINGS["general"]["browser"]
    check_updates_on_startup: bool = DEFAULT_SETTINGS["general"]["check_updates_on_startup"]
    web_search: str = DEFAULT_SETTINGS["general"]["web_search"]
    web_search_enabled: bool = DEFAULT_SETTINGS["general"]["web_search_enabled"]
    switch_to_open_apps: bool = DEFAULT_SETTINGS["general"]["switch_to_open_apps"]
    hide_when_focus_lost: bool = DEFAULT_SETTINGS["general"]["hide_when_focus_lost"]
    download_favicons: bool = DEFAULT_SETTINGS["general"]["download_favicons"]


@dataclass
class UISettings:
    theme: str = DEFAULT_SETTINGS["ui"]["theme"]
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
    show_clock: bool = DEFAULT_SETTINGS["ui"]["show_clock"]
    clock_font_size: int = DEFAULT_SETTINGS["ui"]["clock_font_size"]
    clock_day_text_color: str = DEFAULT_SETTINGS["ui"]["clock_day_text_color"]
    clock_date_text_color: str = DEFAULT_SETTINGS["ui"]["clock_date_text_color"]


SORT_RESULTS_OPTIONS = ("popularity", "name")
THEME_OPTIONS = ("system", "light", "dark")

# The text colours Dash shipped with before it had a light theme. A settings
# file still holding exactly these was never customised, so they are read as
# "follow the theme" rather than forcing light text onto a light window.
LEGACY_DEFAULT_OPACITY = 0.97
LEGACY_DEFAULT_TEXT_COLORS = {
    "search_text_color": "#f3f4f7",
    "result_text_color": "#e7e9ee",
    "description_text_color": "#8f96a3",
    "clock_day_text_color": "#f3f4f7",
    "clock_date_text_color": "#9da4b0",
}


@dataclass
class SearchSettings:
    max_results: int = DEFAULT_SETTINGS["search"]["max_results"]
    autocomplete: bool = DEFAULT_SETTINGS["search"]["autocomplete"]
    ignore_case: bool = DEFAULT_SETTINGS["search"]["ignore_case"]
    match_word_starts: bool = DEFAULT_SETTINGS["search"]["match_word_starts"]
    sort_results: str = DEFAULT_SETTINGS["search"]["sort_results"]
    show_descriptions: bool = DEFAULT_SETTINGS["search"]["show_descriptions"]
    show_run_counter: bool = DEFAULT_SETTINGS["search"]["show_run_counter"]
    show_edit_button: bool = DEFAULT_SETTINGS["search"]["show_edit_button"]
    show_command_tree: bool = DEFAULT_SETTINGS["search"]["show_command_tree"]


@dataclass
class ShortcutSettings:
    edit_selected_command: str = DEFAULT_SETTINGS["shortcuts"]["edit_selected_command"]
    new_command: str = DEFAULT_SETTINGS["shortcuts"]["new_command"]
    open_settings: str = DEFAULT_SETTINGS["shortcuts"]["open_settings"]


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
    # Plain-language notes about problems met while loading (a damaged file
    # set aside, say), for the GUI to show once. Never saved.
    load_warnings: list[str] = field(default_factory=list, compare=False, repr=False)

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

        try:
            with settings_path.open("rb") as f:
                data = tomllib.load(f)
        except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as error:
            # A damaged settings file should never stop Dash from starting.
            # Keep it for reference, start from the defaults, and say so.
            log.error("Could not read settings file %s: %s", settings_path, error)
            moved = quarantine_file(settings_path) if not isinstance(error, OSError) else None
            settings = cls()
            if moved is not None:
                settings.load_warnings.append(
                    f"Your settings file could not be read, so Dash started with the default settings.\n\n"
                    f"The old file was kept as:\n{moved}"
                )
            else:
                settings.load_warnings.append("Your settings file could not be read, so Dash is using the default settings for now.")
            return settings.normalize_resource_paths()

        problems: list[str] = []
        settings = cls(
            general=GeneralSettings(**_valid_values(GeneralSettings, data.get("general", {}), problems)),
            ui=UISettings(**_valid_values(UISettings, data.get("ui", {}), problems)),
            search=SearchSettings(**_valid_values(SearchSettings, data.get("search", {}), problems)),
            shortcuts=ShortcutSettings(**_valid_values(ShortcutSettings, data.get("shortcuts", {}), problems)),
            paths=PathSettings(**_valid_values(PathSettings, data.get("paths", {}), problems)),
        )
        if problems:
            log.warning("Settings with the wrong kind of value were reset to defaults: %s", ", ".join(problems))
            settings.load_warnings.append(
                "Some settings had values Dash couldn't use, so they are back to their defaults: " + ", ".join(problems) + "."
            )
        if settings.search.sort_results not in SORT_RESULTS_OPTIONS:
            settings.search.sort_results = SORT_RESULTS_OPTIONS[0]
        if settings.ui.theme not in THEME_OPTIONS:
            settings.ui.theme = THEME_OPTIONS[0]
        # 0.97 was the old default opacity; it only dimmed the text.
        if settings.ui.window_opacity == LEGACY_DEFAULT_OPACITY:
            settings.ui.window_opacity = 1.0
        settings.ui.window_opacity = min(1.0, max(0.3, settings.ui.window_opacity))
        for key, legacy in LEGACY_DEFAULT_TEXT_COLORS.items():
            if str(getattr(settings.ui, key, "")).strip().lower() == legacy:
                setattr(settings.ui, key, "")

        # Older files had a yes/no "follow the mouse" flag instead of a screen choice.
        general = data.get("general", {})
        if not isinstance(general, dict):
            general = {}
        if "launcher_screen" not in general and "show_on_screen_with_mouse" in general:
            settings.general.launcher_screen = "mouse" if general["show_on_screen_with_mouse"] else "primary"
        if not str(settings.general.launcher_screen).strip():
            settings.general.launcher_screen = "mouse"
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
        atomic_write_text(settings_path, "\n".join(lines))

    @staticmethod
    def _create_default_file(settings_path: Path):
        """Create a default settings.toml file"""
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(DEFAULT_SETTINGS_PATH, settings_path)
        log.info("Created default settings file: %s", settings_path)
