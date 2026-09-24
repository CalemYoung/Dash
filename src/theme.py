"""Light and dark themes for Dash.

style.qss is a template: every color in it is a ``{{token}}`` placeholder
that ``render_stylesheet`` fills from one of the palettes below. Code that
paints with QPainter (the command tree, check boxes, glyph pixmaps) reads the
same tokens through ``color()``, so both follow the active theme.

Which palette is used:

- ``ui.theme = "light"`` or ``"dark"`` fixes it.
- ``ui.theme = "system"`` follows Windows' app mode, read from Qt's style
  hints (Qt 6.5+) and, when Qt cannot tell, from the registry. It changes
  live when the system switches.
- Windows high contrast wins over all of these: the palette is then built
  from the system colors Qt reports (``QPalette``), so the colors the
  person chose for high contrast are the ones Dash uses rather than Dash's
  own.

Windows that draw with ``color()`` should restyle when the theme changes:
connect to ``notifier().changed`` or register a callback with
``on_theme_changed``.
"""
import logging
import re
import sys

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QGuiApplication, QPalette

log = logging.getLogger(__name__)

THEMES = ("light", "dark")
HIGH_CONTRAST = "high-contrast"

# The dark palette is the look Dash has always had. A few text colors were
# nudged up to reach WCAG AA (4.5:1) on the surfaces they sit on:
# text_hint, description_text, destructive, error_text and tree_muted, and
# the settings scroll bar handle to 3:1 on its track.
DARK: dict[str, str] = {
    # Surfaces
    "window_bg": "#16171b",
    "panel_bg": "#202228",
    "panel_border": "#3a414d",
    "raised_bg": "#232427",
    "raised_hover_bg": "#2f3033",
    "input_bg": "#1e2025",
    "input_border": "#353942",
    "border": "#353942",
    "button_bg": "#282b32",
    "button_hover_bg": "#353942",
    "tooltip_bg": "#282b32",
    "list_hover_bg": "#26292f",
    "list_selected_bg": "#282b32",
    "list_divider": "#2b2e35",
    # Text
    "text": "#e7e9ee",
    "text_strong": "#f3f4f7",
    "text_muted": "#8b929e",
    "text_subtle": "#9aa0ad",
    "text_hint": "#858b97",
    "text_hover": "#c9ced8",
    # Accents
    "accent": "#7aa2f7",
    "accent_hover": "#8fb4ff",
    "accent_text": "#10131a",
    "accent_soft_bg": "rgba(122, 162, 247, 0.04)",
    "accent_strong": "#2d7dff",
    "accent_strong_hover": "#4b91ff",
    "accent_strong_border": "#1f5fca",
    "accent_strong_text": "#ffffff",
    "danger": "#f85149",
    "error_text": "#e35d59",
    "destructive": "#ee5743",
    # Launcher
    "search_bg": "#24272e",
    "search_focus_bg": "#272a32",
    "search_edit_bg": "#292d38",
    "search_placeholder": "#8f96a3",
    "tree_bg": "#1c1f25",
    "results_bg": "#202329",
    "results_edit_bg": "#222630",
    "row_hover_bg": "#2d3139",
    "row_selected_bg": "#343841",
    "tile_bg": "#2b2f38",
    "tile_selected_bg": "#3d424d",
    "pill_text": "#9299a6",
    "pill_selected_border": "#4a505c",
    "pill_selected_text": "#c3c8d1",
    "footer_bg": "#181a1f",
    "launcher_muted": "#9299a6",
    # Launcher text
    "search_text": "#f3f4f7",
    "result_text": "#e7e9ee",
    "description_text": "#9ca3b0",
    "clock_day_text": "#f3f4f7",
    "clock_date_text": "#9da4b0",
    # Aliases and the command editor
    "alias_input_bg": "rgba(255, 255, 255, 0.02)",
    "alias_placeholder": "#7d8596",
    "alias_grid_bg": "#171a1f",
    "alias_grid_border": "#2d3138",
    "chip_bg": "#2a2e36",
    "chip_hover_bg": "#343a46",
    "icon_tile_border": "#3a3f4b",
    # Scroll bars
    "scroll_track": "#191b20",
    "scroll_handle": "#606877",
    "scroll_handle_hover": "#7d8695",
    "scrollbar_handle": "#353942",
    "scrollbar_handle_hover": "#8b929e",
    # Painted in Python
    "check_border": "#8b929e",
    "check_border_disabled": "#555b65",
    "swatch_border": "#596170",
    "checker_light": "#2a2e36",
    "checker_dark": "#202228",
    "tree_text": "#e7e9ee",
    "tree_muted": "#818894",
    "tree_line": "#464d59",
    "tree_accent": "#5b9cff",
    "tree_accent_fill": "#293c57",
    "tree_ok": "#3fb950",
    "tree_ok_fill": "#274a33",
    "tree_error": "#ff776d",
    "tree_error_fill": "#57363a",
    "tree_node_border": "#69717e",
    "tree_node_fill": "#303640",
    "status_idle": "#353942",
    "status_checking": "#d0a215",
    "status_ok": "#3fb950",
    "status_bad": "#f85149",
}

# Cool greys around a white work surface, with the accents darkened until
# they read as text on white. Field borders are dark enough (3:1) to find
# the field without relying on its fill.
LIGHT: dict[str, str] = {
    # Surfaces
    "window_bg": "#eef0f4",
    "panel_bg": "#f8f9fb",
    "panel_border": "#c3c9d2",
    "raised_bg": "#ffffff",
    "raised_hover_bg": "#eef3fd",
    "input_bg": "#ffffff",
    "input_border": "#878f9c",
    "border": "#d0d5dd",
    "button_bg": "#eef0f4",
    "button_hover_bg": "#e0e4eb",
    "tooltip_bg": "#ffffff",
    "list_hover_bg": "#f1f4f9",
    "list_selected_bg": "#dfe6f3",
    "list_divider": "#e3e6ec",
    # Text
    "text": "#1f2329",
    "text_strong": "#111418",
    "text_muted": "#555c69",
    "text_subtle": "#4f5663",
    "text_hint": "#5f6673",
    "text_hover": "#2b3038",
    # Accents
    "accent": "#2356c7",
    "accent_hover": "#1b47a8",
    "accent_text": "#ffffff",
    "accent_soft_bg": "rgba(35, 86, 199, 0.05)",
    "accent_strong": "#1a62d6",
    "accent_strong_hover": "#1553b8",
    "accent_strong_border": "#1249a3",
    "accent_strong_text": "#ffffff",
    "danger": "#c42b1c",
    "error_text": "#b42318",
    "destructive": "#b3261e",
    # Launcher
    "search_bg": "#ffffff",
    "search_focus_bg": "#ffffff",
    "search_edit_bg": "#eef3fd",
    "search_placeholder": "#626a78",
    "tree_bg": "#f1f3f6",
    "results_bg": "#f7f8fa",
    "results_edit_bg": "#f0f4fb",
    "row_hover_bg": "#eceff4",
    "row_selected_bg": "#dfe6f3",
    "tile_bg": "#e8ebf0",
    "tile_selected_bg": "#ced7e6",
    "pill_text": "#4d5462",
    "pill_selected_border": "#9aa3b2",
    "pill_selected_text": "#262b33",
    "footer_bg": "#eceef2",
    "launcher_muted": "#555c69",
    # Launcher text
    "search_text": "#111418",
    "result_text": "#1f2329",
    "description_text": "#535a67",
    "clock_day_text": "#1f2329",
    "clock_date_text": "#555c69",
    # Aliases and the command editor
    "alias_input_bg": "rgba(0, 0, 0, 0.02)",
    "alias_placeholder": "#626a78",
    "alias_grid_bg": "#ffffff",
    "alias_grid_border": "#878f9c",
    "chip_bg": "#eceff4",
    "chip_hover_bg": "#e0e5ee",
    "icon_tile_border": "#c3c9d2",
    # Scroll bars
    "scroll_track": "#e6e9ee",
    "scroll_handle": "#7d8593",
    "scroll_handle_hover": "#6b7382",
    "scrollbar_handle": "#c3c9d2",
    "scrollbar_handle_hover": "#878f9c",
    # Painted in Python
    "check_border": "#6b7382",
    "check_border_disabled": "#b8bec8",
    "swatch_border": "#878f9c",
    "checker_light": "#e6e9ee",
    "checker_dark": "#f8f9fb",
    "tree_text": "#1f2329",
    "tree_muted": "#555c69",
    "tree_line": "#b9c0cc",
    "tree_accent": "#1f5fd6",
    "tree_accent_fill": "#dbe6fb",
    "tree_ok": "#1a7f37",
    "tree_ok_fill": "#dcf2e2",
    "tree_error": "#c42b1c",
    "tree_error_fill": "#fbe3e0",
    "tree_node_border": "#878f9c",
    "tree_node_fill": "#e3e7ee",
    "status_idle": "#c3c9d2",
    "status_checking": "#9a6700",
    "status_ok": "#1a7f37",
    "status_bad": "#c42b1c",
}

PALETTES = {"dark": DARK, "light": LIGHT}

# Where each token comes from when Windows high contrast is on. Surfaces map
# to Window/Base/Button, text to WindowText/Text, accents to Highlight, so
# the person's chosen high-contrast colors are what they see.
_R = QPalette.ColorRole
_HIGH_CONTRAST_ROLES: dict[str, QPalette.ColorRole] = {
    **{token: _R.Window for token in (
        "window_bg", "panel_bg", "raised_bg", "raised_hover_bg", "list_hover_bg", "search_bg",
        "search_focus_bg", "search_edit_bg", "tree_bg", "results_bg", "results_edit_bg",
        "row_hover_bg", "tile_bg", "footer_bg", "alias_input_bg", "alias_grid_bg", "chip_bg",
        "chip_hover_bg", "scroll_track", "checker_light", "checker_dark", "tree_node_fill",
        "tree_accent_fill", "tree_ok_fill", "tree_error_fill", "accent_soft_bg",
    )},
    **{token: _R.Base for token in ("input_bg",)},
    **{token: _R.Button for token in ("button_bg", "button_hover_bg", "tooltip_bg")},
    **{token: _R.WindowText for token in (
        "panel_border", "input_border", "border", "list_divider", "text", "text_strong", "text_muted",
        "text_subtle", "text_hint", "text_hover", "search_placeholder", "pill_text", "pill_selected_border",
        "launcher_muted", "search_text", "result_text", "description_text", "clock_day_text",
        "clock_date_text", "alias_placeholder", "alias_grid_border", "icon_tile_border", "scroll_handle",
        "scroll_handle_hover", "scrollbar_handle", "scrollbar_handle_hover", "check_border",
        "check_border_disabled", "swatch_border", "tree_text", "tree_muted", "tree_line",
        "tree_node_border", "status_idle", "danger", "error_text", "destructive", "tree_error",
        "status_bad", "tree_ok", "status_ok", "status_checking",
    )},
    **{token: _R.Highlight for token in (
        "accent", "accent_hover", "accent_strong", "accent_strong_hover", "accent_strong_border",
        "tree_accent",
    )},
    **{token: _R.HighlightedText for token in ("accent_text", "accent_strong_text", "pill_selected_text")},
}

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")
_RGBA = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)")


class ThemeNotifier(QObject):
    """Emits ``changed(name)`` after a new palette has been applied."""

    changed = pyqtSignal(str)


_state: dict = {
    "palette": DARK,
    "name": "dark",
    "app": None,
    "template": "",
    "setting": "system",
    "watching": False,
}
_notifier: ThemeNotifier | None = None
_callbacks: list = []


def notifier() -> ThemeNotifier:
    global _notifier
    if _notifier is None:
        _notifier = ThemeNotifier()
    return _notifier


def on_theme_changed(callback) -> None:
    """Call ``callback(name)`` whenever the theme is (re)applied."""
    if callback not in _callbacks:
        _callbacks.append(callback)


def remove_theme_callback(callback) -> None:
    if callback in _callbacks:
        _callbacks.remove(callback)


# ------------------------------------------------------------------ colors --

def to_qcolor(value: str) -> QColor:
    """QColor from "#rrggbb", a color name or "rgba(r, g, b, a)" (a from 0 to 1)."""
    match = _RGBA.fullmatch(str(value).strip())
    if match:
        red, green, blue, alpha = match.groups()
        color = QColor(int(red), int(green), int(blue))
        if alpha is not None:
            color.setAlphaF(max(0.0, min(1.0, float(alpha))))
        return color
    return QColor(str(value).strip())


def active_palette() -> dict[str, str]:
    return _state["palette"]


def active_name() -> str:
    """"light", "dark" or "high-contrast"."""
    return _state["name"]


def color(token: str) -> QColor:
    """The active theme's color for ``token``, as a QColor.

    An unknown token logs a warning and falls back to the dark palette's
    value, or to magenta so the mistake is visible rather than silent.
    """
    palette = _state["palette"]
    value = palette.get(token) or DARK.get(token)
    if value is None:
        log.warning("Unknown theme color %r", token)
        return QColor("#ff00ff")
    return to_qcolor(value)


def color_name(token: str) -> str:
    """``color(token)`` as "#rrggbb", for building small style sheets in code."""
    return color(token).name(QColor.NameFormat.HexRgb)


def relative_luminance(value) -> float:
    q = value if isinstance(value, QColor) else to_qcolor(value)

    def channel(component: float) -> float:
        return component / 12.92 if component <= 0.04045 else ((component + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(q.redF()) + 0.7152 * channel(q.greenF()) + 0.0722 * channel(q.blueF())


def blend(foreground, background) -> QColor:
    """A translucent color composited over an opaque one."""
    top = foreground if isinstance(foreground, QColor) else to_qcolor(foreground)
    base = background if isinstance(background, QColor) else to_qcolor(background)
    alpha = top.alphaF()
    return QColor.fromRgbF(
        top.redF() * alpha + base.redF() * (1 - alpha),
        top.greenF() * alpha + base.greenF() * (1 - alpha),
        top.blueF() * alpha + base.blueF() * (1 - alpha),
    )


def contrast_ratio(foreground, background) -> float:
    """WCAG 2 contrast ratio, from 1 to 21."""
    lighter, darker = sorted((relative_luminance(foreground), relative_luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


# ---------------------------------------------------------------- rendering --

def render_stylesheet(template_text: str, palette: dict[str, str]) -> str:
    """Fill every ``{{token}}`` in the template from ``palette``.

    A token the palette lacks falls back to the dark value (logged), so a
    template newer than its palette still renders a usable sheet.
    """
    def replace(match: re.Match) -> str:
        token = match.group(1)
        if token in palette:
            return palette[token]
        log.warning("Style sheet color %r is missing from the palette", token)
        return DARK.get(token, "#ff00ff")

    return _PLACEHOLDER.sub(replace, template_text)


def template_tokens(template_text: str) -> set[str]:
    return set(_PLACEHOLDER.findall(template_text))


# --------------------------------------------------------------- resolution --

def _registry_prefers_light() -> bool | None:
    """Windows' AppsUseLightTheme, or None off Windows or when unreadable."""
    if sys.platform != "win32":
        return None
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return bool(value)
    except (ImportError, OSError) as error:
        log.debug("Could not read AppsUseLightTheme: %s", error)
        return None


def system_scheme() -> str | None:
    """"light" or "dark" as the system reports it, or None when it cannot tell."""
    try:
        hints = QGuiApplication.styleHints()
        scheme = hints.colorScheme() if hints is not None else None
    except AttributeError:  # Qt before 6.5
        scheme = None
    if scheme == Qt.ColorScheme.Light:
        return "light"
    if scheme == Qt.ColorScheme.Dark:
        return "dark"
    prefers_light = _registry_prefers_light()
    if prefers_light is None:
        return None
    return "light" if prefers_light else "dark"


def resolve_theme(setting: str) -> str:
    """"light" or "dark" for a ui.theme value; "system" asks Windows, then defaults to dark."""
    value = str(setting or "").strip().lower()
    if value in THEMES:
        return value
    return system_scheme() or "dark"


def is_high_contrast() -> bool:
    """Whether Windows high contrast is on (SPI_GETHIGHCONTRAST, HCF_HIGHCONTRASTON)."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class HIGHCONTRASTW(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwFlags", wintypes.DWORD), ("lpszDefaultScheme", wintypes.LPWSTR)]

        SPI_GETHIGHCONTRAST = 0x0042
        HCF_HIGHCONTRASTON = 0x00000001
        info = HIGHCONTRASTW()
        info.cbSize = ctypes.sizeof(HIGHCONTRASTW)
        if not ctypes.windll.user32.SystemParametersInfoW(SPI_GETHIGHCONTRAST, info.cbSize, ctypes.byref(info), 0):
            return False
        return bool(info.dwFlags & HCF_HIGHCONTRASTON)
    except Exception as error:  # ctypes, windll or the call itself
        log.debug("Could not read the high contrast setting: %s", error)
        return False


# Selected rows carry ordinary text, so under high contrast they are a tint
# of the highlight over the window rather than the highlight itself: the
# row still stands out, and WindowText stays readable on it.
_HIGH_CONTRAST_TINTS = {"row_selected_bg": 0.3, "list_selected_bg": 0.3, "tile_selected_bg": 0.3}


def palette_from_qpalette(qpalette: QPalette) -> dict[str, str]:
    """Every token taken from the system palette, for high contrast."""
    def role_color(role) -> QColor:
        return qpalette.color(QPalette.ColorGroup.Active, role)

    palette = {}
    for token in DARK:
        if token in _HIGH_CONTRAST_TINTS:
            tint = role_color(_R.Highlight)
            tint.setAlphaF(_HIGH_CONTRAST_TINTS[token])
            value = blend(tint, role_color(_R.Window))
        else:
            value = role_color(_HIGH_CONTRAST_ROLES.get(token, _R.WindowText))
        palette[token] = value.name(QColor.NameFormat.HexRgb)
    return palette


def palette_for(setting: str) -> tuple[str, dict[str, str]]:
    """(name, palette) for a ui.theme value, honoring high contrast."""
    if is_high_contrast():
        app = QGuiApplication.instance()
        qpalette = QGuiApplication.palette() if app is not None else QPalette()
        return HIGH_CONTRAST, palette_from_qpalette(qpalette)
    name = resolve_theme(setting)
    return name, PALETTES[name]


# ----------------------------------------------------------------- applying --

def apply_theme(app, settings, template_text: str | None = None) -> str:
    """Render the style sheet for ``settings.ui.theme`` and set it on ``app``.

    The template is remembered, so a later call (after Settings are saved)
    can pass ``None``. With "system", the theme follows Windows live.
    Returns the name applied: "light", "dark" or "high-contrast".
    """
    if template_text is not None:
        _state["template"] = template_text
    _state["app"] = app
    _state["setting"] = str(getattr(getattr(settings, "ui", None), "theme", "system") or "system")
    _apply_current()
    _watch_system_scheme(app)
    return _state["name"]


def _apply_current() -> None:
    name, palette = palette_for(_state["setting"])
    _state["name"] = name
    _state["palette"] = palette
    app = _state["app"]
    if app is not None and _state["template"]:
        app.setStyleSheet(render_stylesheet(_state["template"], palette))
    log.info("Theme applied: %s (setting %s)", name, _state["setting"])
    notifier().changed.emit(name)
    for callback in list(_callbacks):
        try:
            callback(name)
        except Exception:
            log.exception("Theme change callback failed")


def _on_system_scheme_changed(*_args) -> None:
    """Re-apply when Windows switches light/dark, unless the theme is fixed."""
    if str(_state["setting"]).lower() in THEMES:
        return
    _apply_current()


def _watch_system_scheme(app) -> None:
    if _state["watching"] or app is None:
        return
    try:
        hints = QGuiApplication.styleHints()
        hints.colorSchemeChanged.connect(_on_system_scheme_changed)
        _state["watching"] = True
    except AttributeError:  # Qt before 6.5: no live switching
        log.debug("colorSchemeChanged is not available; the system theme is read at startup only")


def reset_for_tests() -> None:
    _state.update(palette=DARK, name="dark", app=None, template="", setting="system")
    _callbacks.clear()
