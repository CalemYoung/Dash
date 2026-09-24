import logging
import os
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from PyQt6.QtCore import QEvent, QFileInfo, QObject, QPointF, QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QKeySequence, QLinearGradient, QPainter, QPen, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileIconProvider,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QTabWidget,
    QToolButton,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import explorer_menu, theme

log = logging.getLogger(__name__)

try:  # Added alongside the hotkey clash list; Settings works without it.
    from .launcher_hotkey import hotkey_conflict_warning as _hotkey_conflict_warning
except ImportError:
    _hotkey_conflict_warning = None


def hotkey_warning(hotkey: str) -> str | None:
    """Why a hotkey may not reach Dash, or None when there is nothing to say."""
    if _hotkey_conflict_warning is None or not str(hotkey or "").strip():
        return None
    try:
        return _hotkey_conflict_warning(str(hotkey))
    except Exception:
        log.exception("Could not check hotkey %r for clashes", hotkey)
        return None


# --------------------------------------------------------------- web search --

WEB_SEARCH_CUSTOM = "custom"
# (key, label, address). The address is what general.web_search stores.
WEB_SEARCH_PRESETS = (
    ("google", "Google", "https://www.google.com/search?q={query}"),
    ("bing", "Bing", "https://www.bing.com/search?q={query}"),
    ("duckduckgo", "DuckDuckGo", "https://duckduckgo.com/?q={query}"),
    ("ecosia", "Ecosia", "https://www.ecosia.org/search?q={query}"),
    ("brave", "Brave", "https://search.brave.com/search?q={query}"),
)


def _normalized_search_url(url: str) -> str:
    text = str(url or "").strip().casefold()
    for prefix in ("https://", "http://"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    if text.startswith("www."):
        text = text[4:]
    return text


def web_search_preset(url: str) -> str:
    """The preset key a stored address belongs to, or "custom".

    Addresses are compared without their scheme, "www." or capitalization,
    so a hand-edited "http://google.com/search?q={query}" is still Google.
    """
    wanted = _normalized_search_url(url)
    for key, _label, address in WEB_SEARCH_PRESETS:
        if wanted == _normalized_search_url(address):
            return key
    return WEB_SEARCH_CUSTOM


def web_search_url_for(preset: str) -> str | None:
    for key, _label, address in WEB_SEARCH_PRESETS:
        if key == preset:
            return address
    return None


def web_search_problem(url: str) -> str | None:
    """What is wrong with a custom search address, or None when it will work."""
    text = str(url or "").strip()
    if not text:
        return "Enter the address to search, with {query} where the search text goes."
    if not text.lower().startswith(("http://", "https://")):
        return "The address must start with http:// or https://."
    if "{query}" not in text:
        return "The address must contain {query} where the search text goes."
    return None


# -------------------------------------------------------------- size presets --

SIZE_KEYS = ("program_width", "search_height", "results_height", "search_font_size", "result_font_size", "description_font_size")
SIZE_CUSTOM = "custom"
# Medium is what Dash ships with. Results heights hold five whole rows at each
# size; the launcher snaps the list to whole rows either way.
# Not a settings.toml key: the toggle reads and writes the registry.
EXPLORER_MENU_KEY = "explorer_menu"

SIZE_PRESETS: dict[str, dict[str, int]] = {
    "small": {
        "program_width": 520,
        "search_height": 58,
        "results_height": 248,
        "search_font_size": 18,
        "result_font_size": 12,
        "description_font_size": 9,
    },
    "medium": {
        "program_width": 600,
        "search_height": 70,
        "results_height": 288,
        "search_font_size": 20,
        "result_font_size": 13,
        "description_font_size": 10,
    },
    "large": {
        "program_width": 720,
        "search_height": 84,
        "results_height": 348,
        "search_font_size": 24,
        "result_font_size": 15,
        "description_font_size": 11,
    },
}
SIZE_LABELS = (("small", "Small"), ("medium", "Medium"), ("large", "Large"), (SIZE_CUSTOM, "Custom"))


def size_preset(values) -> str:
    """The preset whose sizes all match, or "custom". ``values`` is a UISettings
    or a dict with the SIZE_KEYS."""
    current = {key: int(values[key] if isinstance(values, dict) else getattr(values, key)) for key in SIZE_KEYS}
    for name, preset in SIZE_PRESETS.items():
        if preset == current:
            return name
    return SIZE_CUSTOM


THEME_LABELS = (("system", "System"), ("light", "Light"), ("dark", "Dark"))


class ScrollEdgeFade(QWidget):
    """Fade the edges of a scroll area where its content continues.

    A dense settings page looks like a full page whether or not more follows,
    and a thin scrollbar is easy to miss. Content dissolving into the edge
    reads as "there is more here" without having to be noticed first.

    Drawn on the viewport, so it never covers the scrollbar, and transparent
    to the mouse so it cannot swallow clicks on the controls underneath.
    """

    FADE_HEIGHT = 30
    MAX_ALPHA = 245

    def __init__(self, scroll_area: QScrollArea, color: str | None = None, token: str = "panel_bg"):
        super().__init__(scroll_area.viewport())
        self._scroll_area = scroll_area
        # A fixed color stays fixed; otherwise the fade matches the theme's
        # panel and follows it when the theme changes.
        self._fixed_color = QColor(color) if color else None
        self._token = token
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        theme.notifier().changed.connect(self._on_theme_changed)

        scroll_area.viewport().installEventFilter(self)
        scroll_bar = scroll_area.verticalScrollBar()
        scroll_bar.valueChanged.connect(self.update)
        scroll_bar.rangeChanged.connect(lambda *_: self.update())
        self._match_viewport()

    @property
    def _color(self) -> QColor:
        return QColor(self._fixed_color) if self._fixed_color is not None else theme.color(self._token)

    def _on_theme_changed(self, _name=None):
        self.update()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize:
            self._match_viewport()
        return False

    def _match_viewport(self):
        viewport = self._scroll_area.viewport()
        self.setGeometry(0, 0, viewport.width(), viewport.height())
        self.raise_()

    def paintEvent(self, event):
        scroll_bar = self._scroll_area.verticalScrollBar()
        if scroll_bar.maximum() <= scroll_bar.minimum():
            return  # Everything already fits; a fade would be a lie.

        painter = QPainter(self)
        if scroll_bar.value() > scroll_bar.minimum():
            self._paint_edge(painter, at_top=True)
        if scroll_bar.value() < scroll_bar.maximum():
            self._paint_edge(painter, at_top=False)

    def _paint_edge(self, painter: QPainter, at_top: bool):
        height = min(self.FADE_HEIGHT, self.height())
        if height <= 0 or self.width() <= 0:
            return

        top = 0 if at_top else self.height() - height
        rect = QRect(0, top, self.width(), height)

        solid = QColor(self._color)
        solid.setAlpha(self.MAX_ALPHA)
        clear = QColor(self._color)
        clear.setAlpha(0)

        # QPointF, not QPoint: PyQt6 6.10 hard-crashes the process rather than
        # raising TypeError when QLinearGradient is handed integer points.
        gradient = QLinearGradient(QPointF(rect.topLeft()), QPointF(rect.bottomLeft()))
        gradient.setColorAt(0.0, solid if at_top else clear)
        gradient.setColorAt(1.0, clear if at_top else solid)
        painter.fillRect(rect, gradient)


class NoScrollSpinBox(QSpinBox):
    """Spin box that ignores mouse wheel input unless it has focus.

    Prevents the parent QScrollArea's scroll gesture from getting hijacked
    by whichever spin box happens to be under the cursor.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setButtonSymbols(QSpinBox.ButtonSymbols.UpDownArrows)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class NoScrollDoubleSpinBox(QDoubleSpinBox):
    """QDoubleSpinBox counterpart of NoScrollSpinBox."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.UpDownArrows)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class NoScrollComboBox(QComboBox):
    """Combo box that ignores the wheel unless focused, like the spin boxes."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class _HotkeyRecording(QObject):
    """Says when a HotkeyLineEdit starts and stops recording keys.

    While one is recording, the global hotkey has to be paused: Windows gives
    its combination to the hotkey before any window sees it, so it could never
    be recorded, and pressing it would open or hide Dash instead.
    """

    changed = pyqtSignal(bool)


_hotkey_recording = None


def hotkey_recording() -> _HotkeyRecording:
    global _hotkey_recording
    if _hotkey_recording is None:
        _hotkey_recording = _HotkeyRecording()
    return _hotkey_recording


class HotkeyLineEdit(QLineEdit):
    """Read-only editor that records the next key combination pressed."""

    def __init__(self, value, parent=None):
        super().__init__(str(value), parent)
        self.setReadOnly(True)
        self.setPlaceholderText("Press a key combination")
        self.setToolTip("Click here, then press the key combination to use")
        self._recording = False

    def _set_recording(self, recording: bool):
        if recording != self._recording:
            self._recording = recording
            hotkey_recording().changed.emit(recording)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self._set_recording(True)

    def focusOutEvent(self, event):
        self._set_recording(False)
        super().focusOutEvent(event)

    def hideEvent(self, event):
        self._set_recording(False)
        super().hideEvent(event)

    def event(self, event):
        # Every key is for recording, including ones Dash's own shortcuts
        # (Ctrl+N, Alt+Enter) would otherwise take before it arrives here.
        if event.type() == QEvent.Type.ShortcutOverride:
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        modifier_keys = {
            Qt.Key.Key_Control,
            Qt.Key.Key_Shift,
            Qt.Key.Key_Alt,
            Qt.Key.Key_Meta,
        }
        if event.key() in modifier_keys:
            event.accept()
            return

        sequence = QKeySequence(event.modifiers().value | event.key())
        text = sequence.toString(QKeySequence.SequenceFormat.PortableText)
        if text:
            self.setText(text)
        event.accept()


class XCheckBox(QCheckBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self):
        return QSize(24, 24)

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        size = 18
        rect = self.rect()
        box = rect.adjusted(1, (rect.height() - size) // 2, -(rect.width() - size - 1), -((rect.height() - size) // 2))
        border = theme.color("check_border" if self.isEnabled() else "check_border_disabled")
        fill = theme.color("accent_strong" if self.isChecked() else "panel_bg")
        painter.setPen(QPen(border, 1.5))
        painter.setBrush(fill)
        painter.drawRoundedRect(box, 4, 4)

        if self.hasFocus():
            # The box is painted by hand, so the focus ring has to be too.
            painter.setPen(QPen(theme.color("accent"), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(box.adjusted(-2, -2, 2, 2), 5, 5)

        if self.isChecked():
            painter.setPen(QPen(theme.color("accent_strong_text"), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            inset = 5
            painter.drawLine(box.left() + inset, box.top() + inset, box.right() - inset, box.bottom() - inset)
            painter.drawLine(box.right() - inset, box.top() + inset, box.left() + inset, box.bottom() - inset)

        painter.end()

from .settings import (
    GeneralSettings,
    SearchSettings,
    Settings,
    SettingsFileUnreadableError,
    ShortcutSettings,
    UISettings,
)
from .installed_programs import filter_new_program_commands
from .window_placement import fit_within_screen
from .icon_browser import FRAMELESS_DIALOG, RECIPE_KEYS, DragToMoveMixin
from .browsers import DEFAULT_BROWSER, installed_browsers
from .command_editor import CommandEditorPanel
from .icon_browser import OutlineIcon, glyph_pixmap, recipe_from_command, render_recipe
from .windows_settings import is_settings_location
from .widgets import ElidedLabel


def browser_options(current: str | None, first_label: str = "Windows default") -> list[tuple[str, str]]:
    """(stored value, label) pairs: the default, then each installed browser.

    A stored browser that is no longer installed stays listed, marked, so the
    choice is not silently lost when the machine changes.
    """
    options = [(DEFAULT_BROWSER, first_label)]
    options += [(browser.key, browser.name) for browser in installed_browsers()]
    current = str(current or "")
    if current and current not in {stored for stored, _ in options}:
        options.append((current, f"{current} (not installed)"))
    return options


def apply_edited_candidate(candidate: dict, edited: dict) -> None:
    """Fold the editor's result back into an import candidate in place.

    Icon handling: a recipe or rendered icon chosen in the editor replaces
    whatever icon data the candidate arrived with (including an embedded
    source image from an export). If the editor left the icon alone, the
    candidate's icon data is kept so it still reaches the import.
    """
    candidate.update(
        {
            "name": edited["name"],
            "aliases": list(edited.get("aliases", [])),
            "location": edited.get("location", ""),
            "description": edited.get("description", ""),
            "type": edited.get("type", "file"),
        }
    )
    candidate.pop("browser", None)
    if edited.get("browser"):
        candidate["browser"] = edited["browser"]
    candidate.pop("targets", None)
    if edited.get("type") == "group":
        candidate["targets"] = list(edited.get("targets", []))
    new_recipe = {key: edited[key] for key in RECIPE_KEYS if edited.get(key)}
    if new_recipe or edited.get("icon"):
        for key in (*RECIPE_KEYS, "icon_source_data"):
            candidate.pop(key, None)
        candidate.update(new_recipe)
        candidate["icon"] = edited.get("icon")


class CommandEditDialog(DragToMoveMixin, QDialog):
    """The command editor in a dialog, for commands that are not stored yet.

    Saving does not touch commands.toml. The result is left in
    ``result_command`` and the dialog is accepted; Cancel rejects it.
    """

    def __init__(self, command: dict, command_manager, icon_manager, parent=None, title="Edit Command"):
        super().__init__(parent)
        self.setObjectName("CommandEditDialog")
        self.setWindowTitle(title)
        self.setWindowFlags(FRAMELESS_DIALOG)
        # The panel paints its own rounded, bordered surface.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.result_command: dict | None = None

        settings = icon_manager.settings
        width = max(520, settings.ui.program_width)

        self.panel = CommandEditorPanel(command, icon_manager, command_manager, self, standalone=True, title=title)
        self.panel.saved.connect(self._on_saved)
        self.panel.closed.connect(self._on_closed)
        self.panel.layoutChanged.connect(self._fit_panel)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.panel)

        self._width = width
        self._editor_height = settings.ui.editor_height
        self._fit_panel()

    def _fit_panel(self, settle=True):
        """Pinned (the alias chips flow to the width), but never shorter than
        the panel needs right now, so nothing gets clipped when the URL type
        adds a row or aliases wrap. The screen clamp still shrinks it on a
        small display. A second measurement follows the layout pass, as in
        the in-window editor."""
        height = max(self._editor_height, self.panel.needed_height(self._width))
        if QSize(self._width, height) != self.size():
            self.setFixedSize(self._width, height)
            if self.isVisible():
                parent = self.parentWidget()
                fit_within_screen(self, parent.frameGeometry().center() if parent is not None else None)
        if settle:
            QTimer.singleShot(0, lambda: self._fit_panel(settle=False))

    def showEvent(self, event):
        super().showEvent(event)
        parent = self.parentWidget()
        fit_within_screen(self, parent.frameGeometry().center() if parent is not None else None)
        self.raise_()
        self.activateWindow()
        self.panel.command_name_edit_box.setFocus()

    def _on_saved(self, command: dict):
        self.result_command = command

    def _on_closed(self):
        if self.result_command is not None:
            self.accept()
        else:
            self.reject()


class SettingsEditorPanel(QFrame):
    """In-app editor for Dash settings."""

    closed = pyqtSignal()
    saved = pyqtSignal(object)
    importProgramsRequested = pyqtSignal()
    exportCommandsRequested = pyqtSignal()
    importCommandsRequested = pyqtSignal()
    manageCommandsRequested = pyqtSignal()
    resetRunCountsRequested = pyqtSignal()
    # Emitted after Save with the new theme's name ("light", "dark" or
    # "high-contrast") when the theme setting changed; the style sheet has
    # already been re-applied by then.
    themeChanged = pyqtSignal(str)

    def __init__(self, settings, settings_path: Path, parent=None):
        super().__init__(parent)
        self.setObjectName("SettingsEditorPanel")
        self._settings = settings
        self._settings_path = settings_path
        self._controls = {}

        title = QLabel("Settings")
        title.setObjectName("SettingsTitle")

        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(8, 6, 8, 8)
        content_layout.setSpacing(18)

        # Left: how Dash behaves. Right: how it looks. Each group is one topic.
        left_column = QVBoxLayout()
        left_column.setSpacing(10)
        left_column.addWidget(self._general_group())
        left_column.addWidget(self._search_group())
        left_column.addWidget(self._results_group())
        left_column.addWidget(self._shortcuts_group())
        left_column.addStretch(1)

        right_column = QVBoxLayout()
        right_column.setSpacing(10)
        right_column.addWidget(self._layout_group())
        right_column.addWidget(self._text_group())
        right_column.addWidget(self._commands_group())
        right_column.addStretch(1)

        content_layout.addLayout(left_column, 1)
        content_layout.addLayout(right_column, 1)

        content_scroll = QScrollArea()
        content_scroll.setObjectName("SettingsScrollArea")
        content_scroll.setWidgetResizable(True)
        content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Always reserve the track, so the page never reflows the moment it
        # becomes scrollable and the bar has somewhere constant to appear.
        content_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        content_scroll.setFrameShape(QFrame.Shape.NoFrame)
        content_scroll.setWidget(content)
        self._scroll_fade = ScrollEdgeFade(content_scroll)

        self.close_button = QPushButton("Close")
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.clicked.connect(self._cancel)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button.clicked.connect(self._cancel)

        self.save_button = QPushButton("Save")
        self.save_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self._save)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.save_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 16, 28, 22)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(content_scroll, 1)
        layout.addLayout(buttons)

        cancel_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        cancel_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        cancel_shortcut.activated.connect(self._cancel)

        self._connect_signals()
        self._update_hotkey_warning()
        self._update_web_search_rows()
        self._update_size_rows()
        self._update_dirty_state()

    def _group(self, title, rows):
        """A titled box of label/field rows.

        Each row is (label, key, control) or (label, [(key, control), ...]) for
        several controls side by side. Fields all stretch to the same right
        edge and never wrap under their label, so rows line up whatever mix
        of spin boxes, drop-downs and color buttons a group holds.

        Every label is its field's buddy, and every control gets an
        accessible name (its label, unless it already has one), so a screen
        reader announces what each field is for.
        """
        group = QGroupBox(title)
        form = QFormLayout(group)
        form.setContentsMargins(14, 14, 14, 12)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(8)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for row in rows:
            if len(row) == 3:
                label, key, control = row
                self._controls[key] = control
                self._name_for_accessibility(control, label)
                label_widget = QLabel(label)
                label_widget.setBuddy(control)
                form.addRow(label_widget, self._stretchy(control))
            else:
                label, pairs = row
                holder = QWidget()
                strip = QHBoxLayout(holder)
                strip.setContentsMargins(0, 0, 0, 0)
                strip.setSpacing(8)
                for key, control in pairs:
                    self._controls[key] = control
                    self._name_for_accessibility(control, label)
                    strip.addWidget(self._stretchy(control), 1)
                label_widget = QLabel(label)
                label_widget.setBuddy(pairs[0][1])
                form.addRow(label_widget, holder)
        return group

    @staticmethod
    def _name_for_accessibility(control, label: str):
        if not control.accessibleName():
            control.setAccessibleName(label)

    @staticmethod
    def _stretchy(control):
        """Let a field take the full field column; checkboxes stay their own size."""
        if isinstance(control, QComboBox):
            control.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            control.setMinimumContentsLength(10)
        if not isinstance(control, QCheckBox):
            control.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return control

    @staticmethod
    def _line_edit(value):
        control = QLineEdit(str(value))
        control.setMinimumWidth(150)
        return control

    @staticmethod
    def _hotkey_edit(value):
        control = HotkeyLineEdit(value)
        control.setMinimumWidth(150)
        return control

    @staticmethod
    def _spin(value, minimum, maximum):
        control = NoScrollSpinBox()
        control.setRange(minimum, maximum)
        control.setValue(int(value))
        return control

    @staticmethod
    def _check(value, tooltip: str = ""):
        control = XCheckBox()
        control.setChecked(value is True or str(value).lower() == "true")
        if tooltip:
            control.setToolTip(tooltip)
        return control

    @staticmethod
    def _note():
        """A small line of text under a field: a warning or a validation message."""
        label = QLabel()
        label.setObjectName("validationMessage")
        label.setWordWrap(True)
        return label

    def _screen_options(self):
        """Choices for where the launcher opens: mouse, primary, then each display.

        A display is stored by name. If the stored one is not connected right
        now it is still listed, marked as such, so the choice survives a
        laptop being undocked.
        """
        options = [("mouse", "Where the mouse is"), ("primary", "Primary display")]
        app = QApplication.instance()
        screens = list(app.screens()) if app is not None else []
        primary = app.primaryScreen() if app is not None else None
        for index, screen in enumerate(screens, start=1):
            size = screen.geometry()
            model = screen.model().strip() or screen.name().strip() or f"Display {index}"
            label = f"Display {index}: {model} ({size.width()}×{size.height()})"
            if screen is primary:
                label += ", primary"
            # Stored by name; a display that reports no name is stored by position.
            options.append((screen.name().strip() or f"display:{index}", label))
        current = str(self._settings.general.launcher_screen or "")
        if current and current not in {stored for stored, _ in options}:
            options.append((current, f"{current} (not connected)"))
        return options

    @staticmethod
    def _choice(value, options):
        """Drop-down over (stored_value, label) pairs; unknown values fall back to the first."""
        control = NoScrollComboBox()
        for stored, label in options:
            control.addItem(label, stored)
        index = control.findData(value)
        control.setCurrentIndex(index if index >= 0 else 0)
        return control

    def _general_group(self):
        general = self._settings.general
        preset = web_search_preset(general.web_search)
        web_search_choice = self._choice(
            preset,
            [(key, label) for key, label, _address in WEB_SEARCH_PRESETS] + [(WEB_SEARCH_CUSTOM, "Custom...")],
        )
        web_search_choice.setToolTip("Where \"Search the web\" sends what you typed")
        self._web_search_custom = self._line_edit(general.web_search if preset == WEB_SEARCH_CUSTOM else "")
        self._web_search_custom.setPlaceholderText("https://example.com/search?q={query}")
        self._web_search_custom.setToolTip("Put {query} where the search text goes")
        self._web_search_custom.setAccessibleName("Custom web search address")
        self._web_search_problem = self._note()
        self._hotkey_warning = self._note()
        # Read from the registry, not settings.toml: the installer's checkbox
        # can turn it on too, and this toggle has to show what is really there.
        self._explorer_menu_initial = explorer_menu.is_enabled()
        explorer_menu_check = self._check(
            self._explorer_menu_initial,
            "Adds “Add to Dash” to the right-click menu of files, folders and shortcuts in File Explorer. "
            "On Windows 11 it is under “Show more options”.",
        )
        if not explorer_menu.available():
            explorer_menu_check.setEnabled(False)
            explorer_menu_check.setToolTip("Available in the installed version of Dash")

        group = self._group(
            "General",
            [
                ("Open Dash with", "general.hotkey", self._hotkey_edit(general.hotkey)),
                ("Open on display", "general.launcher_screen", self._choice(general.launcher_screen, self._screen_options())),
                ("Websites open in", "general.browser", self._choice(general.browser, browser_options(general.browser))),
                ("Check updates at startup", "general.check_updates_on_startup", self._check(general.check_updates_on_startup)),
                ("Switch to apps already open", "general.switch_to_open_apps", self._check(general.switch_to_open_apps)),
                ("Add to Dash in File Explorer", EXPLORER_MENU_KEY, explorer_menu_check),
                (
                    "Hide when clicking elsewhere",
                    "general.hide_when_focus_lost",
                    self._check(general.hide_when_focus_lost, "Close the launcher when you click another window, like the Start menu does"),
                ),
                (
                    "Download website icons",
                    "general.download_favicons",
                    self._check(
                        general.download_favicons,
                        "Fetch each website command's icon from the site, or from Google's or DuckDuckGo's icon "
                        "service when the site has none. Those services then see which sites you have commands for. "
                        "Turn off to make no requests for icons; website commands use the default website icon.",
                    ),
                ),
                ("Web search unknown commands", "general.web_search_enabled", self._check(general.web_search_enabled)),
                ("Search the web with", "general.web_search", web_search_choice),
            ],
        )
        form = cast(QFormLayout, group.layout())
        # The clash warning sits right under the hotkey it is about.
        form.insertRow(1, "", self._hotkey_warning)
        form.addRow("Search address", self._stretchy(self._web_search_custom))
        form.addRow("", self._web_search_problem)
        return group

    def _search_group(self):
        search = self._settings.search
        group = self._group(
            "Search",
            [
                ("Autocomplete", "search.autocomplete", self._check(search.autocomplete)),
                ("Ignore capitalization", "search.ignore_case", self._check(search.ignore_case)),
                (
                    "Match the start of any word",
                    "search.match_word_starts",
                    self._check(search.match_word_starts, "\"code\" finds Visual Studio Code"),
                ),
                (
                    "Sort results by",
                    "search.sort_results",
                    self._choice(search.sort_results, [("popularity", "Most used first"), ("name", "Name (A to Z)")]),
                ),
                ("Maximum results", "search.max_results", self._spin(search.max_results, 1, 200)),
            ],
        )
        # How often each command is opened is what "most used first" sorts
        # by, so the way to clear it sits right under that setting.
        clear_history_button = QPushButton("Clear...")
        clear_history_button.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_history_button.setToolTip("Forget how often each command has been opened, so \"most used first\" starts over")
        clear_history_button.setAccessibleName("Clear usage history")
        clear_history_button.clicked.connect(self.resetRunCountsRequested.emit)
        form = cast(QFormLayout, group.layout())
        form.insertRow(4, "Usage history", clear_history_button)
        return group

    def _results_group(self):
        search = self._settings.search
        ui = self._settings.ui
        return self._group(
            "Display",
            [
                ("Show descriptions", "search.show_descriptions", self._check(search.show_descriptions)),
                ("Show run counts", "search.show_run_counter", self._check(search.show_run_counter)),
                ("Show edit buttons", "search.show_edit_button", self._check(search.show_edit_button)),
                ("Show command tree panel", "search.show_command_tree", self._check(search.show_command_tree)),
                ("Show clock", "ui.show_clock", self._check(ui.show_clock)),
            ],
        )

    def _shortcuts_group(self):
        shortcuts = self._settings.shortcuts
        return self._group(
            "Shortcuts",
            [
                ("Edit selected command", "shortcuts.edit_selected_command", self._hotkey_edit(shortcuts.edit_selected_command)),
                ("New command", "shortcuts.new_command", self._hotkey_edit(shortcuts.new_command)),
                ("Open settings", "shortcuts.open_settings", self._hotkey_edit(shortcuts.open_settings)),
            ],
        )

    def _layout_group(self):
        ui = self._settings.ui
        opacity = NoScrollDoubleSpinBox()
        opacity.setRange(0.30, 1.00)
        opacity.setSingleStep(0.05)
        opacity.setDecimals(2)
        opacity.setValue(ui.window_opacity)
        theme_choice = self._choice(ui.theme, THEME_LABELS)
        theme_choice.setToolTip("System follows the Windows light or dark app mode")
        self._size_choice = self._choice(size_preset(ui), SIZE_LABELS)
        self._size_choice.setToolTip("Sets the launcher's width, heights and text sizes together")
        return self._group(
            "Appearance",
            [
                ("Theme", "ui.theme", theme_choice),
                ("Size", "ui.size_preset", self._size_choice),
                ("Width", "ui.program_width", self._spin(ui.program_width, 280, 2000)),
                ("Search box height", "ui.search_height", self._spin(ui.search_height, 50, 400)),
                ("Results height", "ui.results_height", self._spin(ui.results_height, 80, 1000)),
                ("Editor height", "ui.editor_height", self._spin(ui.editor_height, 400, 1400)),
                ("Opacity", "ui.window_opacity", opacity),
            ],
        )

    def _text_group(self):
        """One row per piece of text: its size, then its color(s)."""
        ui = self._settings.ui

        def size(value, minimum, maximum, name):
            control = self._spin(value, minimum, maximum)
            control.setAccessibleName(name)
            return control

        return self._group(
            "Text",
            [
                ("Search box", "ui.search_font_size", size(ui.search_font_size, 8, 48, "Search box text size")),
                ("Result names", "ui.result_font_size", size(ui.result_font_size, 8, 32, "Result name text size")),
                ("Descriptions", "ui.description_font_size", size(ui.description_font_size, 7, 24, "Description text size")),
                ("Clock size", "ui.clock_font_size", self._spin(ui.clock_font_size, 6, 18)),
            ],
        )

    def _commands_group(self):
        group = QGroupBox("Commands")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(14, 14, 14, 10)
        layout.setSpacing(4)

        manage_button = QPushButton("Manage Commands...")
        manage_button.setCursor(Qt.CursorShape.PointingHandCursor)
        manage_button.setToolTip("See every command in one list to edit or delete them")
        manage_button.clicked.connect(self.manageCommandsRequested.emit)

        auto_populate_button = QPushButton("Find Recommended Commands...")
        auto_populate_button.setCursor(Qt.CursorShape.PointingHandCursor)
        auto_populate_button.clicked.connect(self.importProgramsRequested.emit)

        export_button = QPushButton("Export Commands...")
        export_button.setCursor(Qt.CursorShape.PointingHandCursor)
        export_button.clicked.connect(self.exportCommandsRequested.emit)

        import_button = QPushButton("Import Commands...")
        import_button.setCursor(Qt.CursorShape.PointingHandCursor)
        import_button.clicked.connect(self.importCommandsRequested.emit)

        layout.addWidget(manage_button)
        layout.addWidget(auto_populate_button)
        layout.addWidget(export_button)
        layout.addWidget(import_button)
        return group

    # --------------------------------------------------- dependent rows

    def _set_row_visible(self, control, visible: bool):
        """Show or hide a form row by its field, wherever it sits."""
        parent = control.parentWidget()
        form = parent.layout() if parent is not None else None
        if isinstance(form, QFormLayout):
            form.setRowVisible(control, visible)

    def _update_hotkey_warning(self):
        warning = hotkey_warning(self._value("general.hotkey"))
        self._hotkey_warning.setText(warning or "")
        self._set_row_visible(self._hotkey_warning, bool(warning))

    def _update_web_search_rows(self):
        custom = self._controls["general.web_search"].currentData() == WEB_SEARCH_CUSTOM
        self._set_row_visible(self._web_search_custom, custom)
        problem = web_search_problem(self._web_search_custom.text()) if custom else None
        self._web_search_problem.setText(problem or "")
        self._set_row_visible(self._web_search_problem, bool(problem))

    def _web_search_url(self) -> str:
        preset = self._controls["general.web_search"].currentData()
        if preset == WEB_SEARCH_CUSTOM:
            return self._web_search_custom.text().strip()
        stored = self._settings.general.web_search
        if preset == web_search_preset(stored):
            return stored  # the same engine, spelled as the file had it
        return web_search_url_for(preset) or stored

    def _size_values(self) -> dict[str, int]:
        return {key: int(self._value(f"ui.{key}")) for key in SIZE_KEYS}

    def _on_size_preset_chosen(self, _index=None):
        """A preset fills in all six sizes; Custom keeps them and shows the boxes."""
        preset = SIZE_PRESETS.get(self._size_choice.currentData())
        if preset is not None:
            for key, value in preset.items():
                control = self._controls[f"ui.{key}"]
                control.blockSignals(True)
                control.setValue(value)
                control.blockSignals(False)
        self._update_size_rows()
        self._update_dirty_state()

    def _on_size_value_changed(self, _value=None):
        """Changing one size by hand (a text size, say) makes the choice Custom."""
        matched = size_preset(self._size_values())
        current = self._size_choice.currentData()
        # Custom stays Custom even when the numbers land on a preset, so the
        # boxes being edited do not vanish mid-edit.
        if matched != current and current != SIZE_CUSTOM:
            self._size_choice.blockSignals(True)
            self._size_choice.setCurrentIndex(self._size_choice.findData(matched))
            self._size_choice.blockSignals(False)
            self._update_size_rows()

    def _update_size_rows(self):
        custom = self._size_choice.currentData() == SIZE_CUSTOM
        for key in ("ui.program_width", "ui.search_height", "ui.results_height"):
            self._set_row_visible(self._controls[key], custom)

    # ------------------------------------------------------ values

    def _value(self, key) -> Any:
        control = self._controls[key]
        if isinstance(control, QComboBox):
            return control.currentData()
        if isinstance(control, QCheckBox):
            return control.isChecked()
        if isinstance(control, (QSpinBox, QDoubleSpinBox)):
            return control.value()
        return control.text().strip()

    def _collect(self):
        return Settings(
            general=GeneralSettings(
                hotkey=self._value("general.hotkey"),
                launcher_screen=str(self._value("general.launcher_screen") or "mouse"),
                browser=str(self._value("general.browser") or DEFAULT_BROWSER),
                check_updates_on_startup=bool(self._value("general.check_updates_on_startup")),
                web_search=self._web_search_url(),
                web_search_enabled=bool(self._value("general.web_search_enabled")),
                switch_to_open_apps=bool(self._value("general.switch_to_open_apps")),
                hide_when_focus_lost=bool(self._value("general.hide_when_focus_lost")),
                download_favicons=bool(self._value("general.download_favicons")),
            ),
            ui=UISettings(
                theme=str(self._value("ui.theme") or "system"),
                program_width=self._value("ui.program_width"),
                search_height=self._value("ui.search_height"),
                results_height=self._value("ui.results_height"),
                editor_height=self._value("ui.editor_height"),
                window_opacity=self._value("ui.window_opacity"),
                search_font_size=self._value("ui.search_font_size"),
                result_font_size=self._value("ui.result_font_size"),
                description_font_size=self._value("ui.description_font_size"),
                show_clock=self._value("ui.show_clock"),
                clock_font_size=self._value("ui.clock_font_size"),
            ),
            search=SearchSettings(
                max_results=self._value("search.max_results"),
                autocomplete=self._value("search.autocomplete"),
                ignore_case=bool(self._value("search.ignore_case")),
                match_word_starts=bool(self._value("search.match_word_starts")),
                sort_results=self._value("search.sort_results"),
                show_descriptions=self._value("search.show_descriptions"),
                show_run_counter=self._value("search.show_run_counter"),
                show_edit_button=bool(self._value("search.show_edit_button")),
                show_command_tree=self._value("search.show_command_tree"),
            ),
            shortcuts=ShortcutSettings(
                edit_selected_command=self._value("shortcuts.edit_selected_command"),
                new_command=self._value("shortcuts.new_command"),
                open_settings=self._value("shortcuts.open_settings"),
            ),
            paths=self._settings.paths,
        )

    def _explorer_menu_changed(self) -> bool:
        return bool(self._value(EXPLORER_MENU_KEY)) != self._explorer_menu_initial

    def _is_dirty(self) -> bool:
        current = self._collect()
        return (
            self._explorer_menu_changed()
            or asdict(current.general) != asdict(self._settings.general)
            or asdict(current.ui) != asdict(self._settings.ui)
            or asdict(current.search) != asdict(self._settings.search)
            or asdict(current.shortcuts) != asdict(self._settings.shortcuts)
        )

    def _is_valid(self) -> bool:
        if self._controls["general.web_search"].currentData() == WEB_SEARCH_CUSTOM:
            return web_search_problem(self._web_search_custom.text()) is None
        return True

    def _update_dirty_state(self):
        dirty = self._is_dirty()
        self.close_button.setVisible(not dirty)
        self.cancel_button.setVisible(dirty)
        self.save_button.setVisible(dirty)
        self.save_button.setEnabled(self._is_valid())

    def _connect_signals(self):
        for key, control in self._controls.items():
            if isinstance(control, QComboBox):
                control.currentIndexChanged.connect(self._update_dirty_state)
            elif isinstance(control, QLineEdit):
                control.textChanged.connect(self._update_dirty_state)
            elif isinstance(control, QCheckBox):
                control.toggled.connect(self._update_dirty_state)
            elif isinstance(control, (QSpinBox, QDoubleSpinBox)):
                if key.removeprefix("ui.") in SIZE_KEYS:
                    control.valueChanged.connect(self._on_size_value_changed)
                control.valueChanged.connect(self._update_dirty_state)
        self._size_choice.currentIndexChanged.connect(self._on_size_preset_chosen)
        self._controls["general.hotkey"].textChanged.connect(self._update_hotkey_warning)
        self._controls["general.web_search"].currentIndexChanged.connect(self._update_web_search_rows)
        self._web_search_custom.textChanged.connect(self._update_web_search_rows)
        self._web_search_custom.textChanged.connect(self._update_dirty_state)

    def _save(self):
        if not self._is_valid():
            return
        settings = self._collect()
        settings.read_failed = getattr(self._settings, "read_failed", False)
        try:
            settings.save(self._settings_path)
        except OSError as error:
            log.error("Could not save settings", exc_info=True)
            message = str(error) if isinstance(error, SettingsFileUnreadableError) else (
                "Dash couldn't save your settings. Make sure the settings file isn't open elsewhere and your drive has free space, then try again."
            )
            QMessageBox.warning(self, "Settings", message)
            return
        if self._explorer_menu_changed():
            try:
                explorer_menu.set_enabled(bool(self._value(EXPLORER_MENU_KEY)))
            except OSError:
                log.error("Could not change the File Explorer menu", exc_info=True)
                QMessageBox.warning(self, "Settings", "Dash couldn't change the File Explorer menu. Your other settings were saved.")
        theme_changed = settings.ui.theme != self._settings.ui.theme
        if theme_changed:
            # Re-theme before the window reacts to the new settings, so
            # anything it redraws already uses the new colors.
            name = theme.apply_theme(QApplication.instance(), settings)
        self.saved.emit(settings)
        if theme_changed:
            self.themeChanged.emit(name)
        self.closed.emit()

    def _cancel(self):
        self.closed.emit()


def candidate_kind(candidate: dict) -> str:
    """'website', 'setting', 'folder' or 'app', from what the candidate points at."""
    location = str(candidate.get("location", ""))
    if candidate.get("type") == "url" or location.startswith(("http://", "https://")):
        return "website"
    if is_settings_location(location):
        return "setting"
    try:
        if os.path.isdir(location):
            return "folder"
    except OSError:
        pass
    return "app"


def usage_sort_key(candidate: dict):
    """Most used first: by how often it was opened, then by when it was last
    opened. What the system has no record of comes after, Windows' own
    suggestions leading and the rest in the order they were found."""
    opened = int(candidate.get("opened") or 0)
    last_opened = float(candidate.get("last_opened") or 0)
    return (not (opened or last_opened), -opened, -last_opened, not candidate.get("suggested"))


def usage_summary(candidate: dict) -> str:
    """The record behind a candidate's place in the list, for its row:
    "Opened 12 times \u00b7 last 3 Sep 2026", or empty when there is none."""
    opened = int(candidate.get("opened") or 0)
    when = ""
    try:
        if candidate.get("last_opened"):
            moment = datetime.fromtimestamp(float(candidate["last_opened"]))
            when = f"{moment.day} {moment:%b %Y}"
    except (OverflowError, OSError, ValueError):
        when = ""
    times = f"Opened {opened} time{'s' if opened != 1 else ''}" if opened else ""
    if times and when:
        return f"{times} \u00b7 last {when}"
    if times:
        return times
    if when:
        return f"Last opened {when}"
    return ""


# Opened within this many days counts as "recently used": ticked from the
# start in Find Recommended Commands.
RECENT_USAGE_DAYS = 60


def recently_used(candidate: dict, now: float | None = None) -> bool:
    """Whether the system's record says the candidate was opened lately.

    Only a date counts: a count on its own may be years old.
    """
    try:
        last_opened = float(candidate.get("last_opened") or 0)
    except (TypeError, ValueError):
        return False
    if last_opened <= 0:
        return False
    now = time.time() if now is None else now
    return now - last_opened <= RECENT_USAGE_DAYS * 86_400


class ProgramImportDialog(DragToMoveMixin, QDialog):
    """Choose what the scan found before it becomes commands.

    One tab per kind of thing (apps and tools, folders, websites), each with
    its own filter and list, so a long scan is worked through a page at a
    time instead of as one mixed list. Double-clicking an entry opens it in
    the command editor to change its name, aliases, description, target or
    icon before it is added.
    """

    KINDS = (("app", "Apps and tools"), ("folder", "Folders"), ("website", "Websites"), ("setting", "Windows Settings"))

    def __init__(
        self,
        candidates: list[dict],
        existing_locations: set[str],
        icon_manager=None,
        parent=None,
        command_manager=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Recommended Commands")
        self.setObjectName("ProgramImportDialog")
        self.setWindowFlags(FRAMELESS_DIALOG)
        self.setMinimumSize(600, 680)
        self._icon_manager = icon_manager
        self._command_manager = command_manager
        self._icon_provider = QFileIconProvider()

        title = QLabel("Recommended Commands")
        title.setObjectName("dialogTitle")
        subtitle = QLabel(
            "Found on this PC, most used first. What you opened lately is already ticked; "
            "nothing is added until you choose Add."
        )
        subtitle.setObjectName("dialogSubtitle")
        subtitle.setWordWrap(True)

        selectable = filter_new_program_commands(candidates, existing_locations)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("ProgramImportTabs")
        self.tabs.setDocumentMode(True)
        self.lists: dict[str, QListWidget] = {}
        self.filters: dict[str, QLineEdit] = {}
        for kind, heading in self.KINDS:
            group = [c for c in selectable if candidate_kind(c) == kind]
            if not group:
                continue
            group.sort(key=usage_sort_key)
            page, list_widget, filter_box = self._build_page(kind, group)
            self.lists[kind] = list_widget
            self.filters[kind] = filter_box
            self.tabs.addTab(page, f"{heading} ({len(group)})")

        empty_message = QLabel("Nothing new was found. Everything the scan knows about is already a command.")
        empty_message.setObjectName("dialogSubtitle")
        empty_message.setWordWrap(True)
        empty_message.setVisible(not selectable)
        self.tabs.setVisible(bool(selectable))

        select_recent_button = QPushButton("Select recently used")
        select_recent_button.setToolTip(f"Tick everything opened in the last {RECENT_USAGE_DAYS} days, on every tab")
        select_all_button = QPushButton("Select all on this tab")
        select_none_button = QPushButton("Select none")
        for button in (select_recent_button, select_all_button, select_none_button):
            button.setObjectName("programImportSelectButton")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setEnabled(bool(selectable))
        select_recent_button.setEnabled(any(recently_used(c) for c in selectable))
        select_recent_button.clicked.connect(self.select_recently_used)
        select_all_button.clicked.connect(lambda: self._set_visible_checked(Qt.CheckState.Checked))
        select_none_button.clicked.connect(lambda: self._set_visible_checked(Qt.CheckState.Unchecked))

        self.selected_label = QLabel()
        self.selected_label.setObjectName("dialogSubtitle")

        selection_row = QHBoxLayout()
        selection_row.addWidget(self.selected_label)
        selection_row.addStretch(1)
        selection_row.addWidget(select_recent_button)
        selection_row.addWidget(select_all_button)
        selection_row.addWidget(select_none_button)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.add_button = cast(QPushButton, buttons.addButton("Add Selected", QDialogButtonBox.ButtonRole.AcceptRole))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(empty_message)
        layout.addLayout(selection_row)
        layout.addWidget(buttons)
        self._refresh_selection_count()

    def _build_page(self, kind: str, group: list[dict]):
        page = QWidget()
        filter_box = QLineEdit()
        filter_box.setPlaceholderText(f"Filter {self._heading(kind).lower()}...")
        filter_box.setClearButtonEnabled(True)
        filter_box.setAccessibleName(f"Filter {self._heading(kind).lower()}")
        list_widget = QListWidget()
        list_widget.setObjectName("ScanResultList")
        list_widget.setAccessibleName(self._heading(kind))
        # Long URLs would otherwise add a scrollbar under every page.
        list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for candidate in group:
            self._add_item(list_widget, candidate, checked=recently_used(candidate))
        filter_box.textChanged.connect(lambda text, lw=list_widget: self._apply_filter(lw, text))
        list_widget.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        list_widget.itemClicked.connect(self._toggle_item)  # clicking the row ticks it
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 10, 0, 0)
        page_layout.setSpacing(8)
        page_layout.addWidget(filter_box)
        page_layout.addWidget(list_widget, 1)
        return page, list_widget, filter_box

    def _heading(self, kind: str) -> str:
        return dict(self.KINDS)[kind]

    def showEvent(self, event):
        # Frameless windows are not placed or focused by the window manager
        super().showEvent(event)
        parent = self.parentWidget()
        fit_within_screen(self, parent.frameGeometry().center() if parent is not None else None)
        self.raise_()
        self.activateWindow()

    def _resolve_icon(self, candidate: dict) -> QIcon:
        # A recipe is previewed in memory. Resolving it through the icon
        # manager would write a file named after the candidate for every row,
        # which could overwrite the icon of a command with the same name.
        recipe = None if candidate.get("icon") else recipe_from_command(candidate)
        if recipe is not None:
            pixmap = render_recipe(recipe, 64)
            if pixmap is not None and not pixmap.isNull():
                return QIcon(pixmap)
        if self._icon_manager is not None:
            icon = self._icon_manager.resolve_command_icon(candidate)
            if not icon.isNull():
                return icon
        location = candidate.get("location", "")
        info = QFileInfo(str(location))
        if info.exists():
            icon = self._icon_provider.icon(info)
            if not icon.isNull():
                return icon
        return QIcon()

    def _add_item(self, list_widget: QListWidget, candidate: dict, row: int | None = None, checked: bool = False):
        """One row: checkbox, icon, name over an elided location, and a
        pencil that opens the entry in the editor before it is added."""
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, candidate)
        widget = ImportRow(candidate, self._resolve_icon(candidate), checked, list_widget)
        widget.check.toggled.connect(lambda _on: self._refresh_selection_count())
        widget.edit_button.clicked.connect(lambda: self._edit_item(item))
        item.setSizeHint(widget.sizeHint())
        if row is None:
            list_widget.addItem(item)
        else:
            list_widget.insertItem(row, item)
        list_widget.setItemWidget(item, widget)
        return item

    @staticmethod
    def _row_widget(item: QListWidgetItem) -> "ImportRow | None":
        list_widget = item.listWidget() if item is not None else None
        widget = list_widget.itemWidget(item) if list_widget is not None else None
        return widget if isinstance(widget, ImportRow) else None

    def _toggle_item(self, item: QListWidgetItem):
        widget = self._row_widget(item)
        if widget is not None and widget.check.isEnabled():
            widget.check.setChecked(not widget.check.isChecked())

    def _current_list(self) -> QListWidget | None:
        page = self.tabs.currentWidget()
        if page is None:
            return None
        return page.findChild(QListWidget)

    @staticmethod
    def _apply_filter(list_widget: QListWidget, text: str):
        """Hide rows whose name or location does not contain the text."""
        needle = text.strip().casefold()
        for index in range(list_widget.count()):
            item = list_widget.item(index)
            if item is None:
                continue
            candidate = item.data(Qt.ItemDataRole.UserRole) or {}
            haystack = f"{candidate.get('name', '')} {candidate.get('location', '')} {' '.join(candidate.get('aliases', []))}".casefold()
            item.setHidden(bool(needle) and needle not in haystack)

    def _edit_item(self, item: QListWidgetItem):
        """The pencil: open the entry in the command editor and apply the result."""
        list_widget = item.listWidget() if item is not None else None
        if list_widget is None or self._command_manager is None:
            return
        candidate = item.data(Qt.ItemDataRole.UserRole)
        if candidate is None:
            return

        dialog = CommandEditDialog(candidate, self._command_manager, self._icon_manager, self, title="Edit Command")
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.result_command is None:
            return

        apply_edited_candidate(candidate, dialog.result_command)
        candidate["_error"], candidate["_conflict"] = self._command_manager.check_import_candidate(candidate)

        row = list_widget.row(item)
        list_widget.takeItem(row)
        importable = not candidate["_error"] and not candidate["_conflict"]
        self._add_item(list_widget, candidate, row=row, checked=importable)
        self._refresh_selection_count()

    def _set_visible_checked(self, state: Qt.CheckState):
        """Select all / none applies to the current tab, and only to the rows
        the filter is showing, so a filtered list can be ticked in one go."""
        list_widget = self._current_list()
        if list_widget is None:
            return
        for index in range(list_widget.count()):
            item = list_widget.item(index)
            widget = self._row_widget(item) if item is not None else None
            if widget is not None and not item.isHidden() and widget.check.isEnabled():
                widget.check.setChecked(state == Qt.CheckState.Checked)
        self._refresh_selection_count()

    def select_recently_used(self):
        """Tick every recently opened entry on every tab, whatever the filter."""
        for list_widget in self.lists.values():
            for index in range(list_widget.count()):
                item = list_widget.item(index)
                widget = self._row_widget(item) if item is not None else None
                candidate = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
                if widget is not None and widget.check.isEnabled() and recently_used(candidate or {}):
                    widget.check.setChecked(True)
        self._refresh_selection_count()

    def _refresh_selection_count(self):
        count = len(self.selected_candidates())
        self.selected_label.setText(f"{count} selected" if count else "Nothing selected yet")
        self.add_button.setText(f"Add {count} Selected" if count else "Add Selected")
        self.add_button.setEnabled(count > 0)

    def selected_candidates(self) -> list[dict]:
        """Ticked entries across every tab, filtered or not."""
        selected = []
        for list_widget in self.lists.values():
            for index in range(list_widget.count()):
                item = list_widget.item(index)
                widget = self._row_widget(item) if item is not None else None
                if widget is not None and widget.check.isChecked():
                    selected.append(item.data(Qt.ItemDataRole.UserRole))
        return selected


class ImportRow(QWidget):
    """A scan result: tick box, icon, name over its location, and a pencil.

    The location is elided to the row, so a long web address never widens
    the list or hides the pencil; the full text is its tooltip.
    """

    def __init__(self, candidate: dict, icon: QIcon, checked: bool, parent=None):
        super().__init__(parent)
        self.setObjectName("ImportRow")
        error = candidate.get("_error")

        self.check = XCheckBox()
        self.check.setChecked(checked and not error)
        self.check.setEnabled(not error)

        icon_label = QLabel()
        icon_label.setFixedSize(28, 28)
        icon_label.setPixmap(icon.pixmap(QSize(28, 28)))

        name = ElidedLabel(str(candidate.get("name", "")))
        name.setObjectName("importRowName")
        location = ElidedLabel(str(candidate.get("location", "")))
        location.setObjectName("importRowLocation")
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(1)
        text.addWidget(name)
        text.addWidget(location)
        aliases = [str(alias) for alias in candidate.get("aliases", []) if str(alias).strip()]
        if aliases:
            meta = ElidedLabel("Aliases: " + ", ".join(aliases))
            meta.setObjectName("importRowMeta")
            text.addWidget(meta)
        usage = usage_summary(candidate)
        if usage:
            self.usage_label = ElidedLabel(usage)
            self.usage_label.setObjectName("importRowMeta")
            text.addWidget(self.usage_label)
        if error:
            problem = ElidedLabel(f"\u26a0 {error}")
            problem.setObjectName("importRowError")
            text.addWidget(problem)

        self.edit_button = QToolButton()
        self.edit_button.setObjectName("ResultEditButton")
        self.edit_button.setIcon(QIcon(glyph_pixmap(OutlineIcon.PENCIL, 18, theme.color("text_muted"))))
        self.edit_button.setIconSize(QSize(18, 18))
        self.edit_button.setFixedSize(30, 30)
        self.edit_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.edit_button.setToolTip("Edit name, aliases or icon before adding")
        self.edit_button.setAccessibleName(f"Edit {candidate.get('name', '')} before adding")
        self.check.setAccessibleName(f"Add {candidate.get('name', '')}")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 8, 8)
        layout.setSpacing(10)
        layout.addWidget(self.check)
        layout.addWidget(icon_label)
        layout.addLayout(text, 1)
        layout.addWidget(self.edit_button)


class ExportCommandsDialog(DragToMoveMixin, QDialog):
    """Choose existing commands to write out to a portable TOML file."""

    def __init__(self, commands: list[dict], icon_manager=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export Commands")
        self.setObjectName("ProgramImportDialog")
        self.setWindowFlags(FRAMELESS_DIALOG)
        self.setMinimumSize(560, 640)
        self._icon_manager = icon_manager
        self._icon_provider = QFileIconProvider()

        title = QLabel("Export Commands")
        title.setObjectName("dialogTitle")

        self.command_list = QListWidget()
        self.command_list.setObjectName("ProgramImportList")
        self.command_list.setAccessibleName("Commands to export")
        self.command_list.setIconSize(QSize(28, 28))

        commands = sorted(commands, key=lambda c: str(c.get("name", "")).lower())
        for command in commands:
            item = QListWidgetItem(f"{command.get('name', '')}\n{command.get('location', '')}")
            item.setIcon(self._resolve_icon(command))
            item.setData(Qt.ItemDataRole.UserRole, command.get("name"))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.command_list.addItem(item)

        empty_message = QLabel("No commands to export yet." if not commands else "")
        empty_message.setObjectName("dialogSubtitle")
        empty_message.setVisible(not commands)

        select_all_button = QPushButton("Select All")
        select_none_button = QPushButton("Select None")
        select_all_button.setObjectName("programImportSelectButton")
        select_none_button.setObjectName("programImportSelectButton")
        select_all_button.setEnabled(bool(commands))
        select_none_button.setEnabled(bool(commands))
        select_all_button.clicked.connect(lambda: self._set_all_checked(Qt.CheckState.Checked))
        select_none_button.clicked.connect(lambda: self._set_all_checked(Qt.CheckState.Unchecked))

        selection_row = QHBoxLayout()
        selection_row.addWidget(QLabel(f"{len(commands)} command(s)"))
        selection_row.addStretch(1)
        selection_row.addWidget(select_all_button)
        selection_row.addWidget(select_none_button)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self._export_button = cast(QPushButton, buttons.addButton("Export Selected", QDialogButtonBox.ButtonRole.AcceptRole))
        self._export_button.setEnabled(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.command_list.itemChanged.connect(self._update_export_enabled)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addLayout(selection_row)
        layout.addWidget(self.command_list, 1)
        layout.addWidget(empty_message)
        layout.addWidget(buttons)

    def showEvent(self, event):
        super().showEvent(event)
        parent = self.parentWidget()
        fit_within_screen(self, parent.frameGeometry().center() if parent is not None else None)
        self.raise_()
        self.activateWindow()

    def _resolve_icon(self, command: dict) -> QIcon:
        if self._icon_manager is not None:
            icon = self._icon_manager.resolve_command_icon(command)
            if not icon.isNull():
                return icon
        location = command.get("location", "")
        info = QFileInfo(str(location))
        if info.exists():
            icon = self._icon_provider.icon(info)
            if not icon.isNull():
                return icon
        return QIcon()

    def _set_all_checked(self, state: Qt.CheckState):
        for index in range(self.command_list.count()):
            item = self.command_list.item(index)
            if item is not None:
                item.setCheckState(state)
        self._update_export_enabled()

    def _update_export_enabled(self, _item=None):
        self._export_button.setEnabled(bool(self.selected_names()))

    def selected_names(self) -> list[str]:
        selected = []
        for index in range(self.command_list.count()):
            item = self.command_list.item(index)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                selected.append(item.data(Qt.ItemDataRole.UserRole))
        return selected


def groups_containing(command_manager, name: str) -> list[str]:
    """Names of the groups that open ``name``.

    Uses CommandManager.groups_containing when it exists, and otherwise
    looks through the loaded commands for groups listing it as a target.
    """
    if command_manager is None:
        return []
    finder = getattr(command_manager, "groups_containing", None)
    if callable(finder):
        try:
            return [str(group) for group in finder(name)]
        except Exception:
            log.exception("Could not look up the groups containing %r", name)
            return []
    wanted = str(name).casefold()
    commands = getattr(command_manager, "commands", {}) or {}
    return sorted(
        str(command.get("name", ""))
        for command in commands.values()
        if command.get("type") == "group" and any(str(target).casefold() == wanted for target in command.get("targets", []))
    )


def delete_confirmation_text(names: list[str], groups_by_name: dict[str, list[str]]) -> str:
    """The question asked before deleting: how many, and which groups lose them.

    Groups that are themselves being deleted are not mentioned.
    """
    count = len(names)
    if count == 1:
        question = f"Delete \"{names[0]}\"? This cannot be undone."
    else:
        question = f"Delete {count} commands? This cannot be undone."
    deleting = {name.casefold() for name in names}
    affected: dict[str, list[str]] = {}
    for name in names:
        for group in groups_by_name.get(name, []):
            if group.casefold() not in deleting:
                affected.setdefault(group, []).append(name)
    if not affected:
        return question
    if count == 1:
        heading = "Groups that open it will report it as missing:"
        lines = sorted(affected, key=str.casefold)
    else:
        heading = "Groups that open them will report them as missing:"
        lines = [f"{group}: {', '.join(members)}" for group, members in sorted(affected.items(), key=lambda pair: pair[0].casefold())]
    return f"{question}\n\n{heading}\n" + "\n".join(lines)


def _delete_failure_message(name: str, error: Exception) -> str:
    """A plain reason a delete failed; the technical detail goes to the log."""
    from .command_manager import CommandsFileUnreadableError

    if isinstance(error, CommandsFileUnreadableError):
        return str(error)
    if isinstance(error, PermissionError):
        return f"Dash couldn't delete \"{name}\" because the commands file is in use or read-only. Close anything that has it open and try again."
    if isinstance(error, OSError):
        return f"Dash couldn't delete \"{name}\" because the commands file couldn't be saved. Make sure your drive has free space and try again."
    return f"Dash couldn't delete \"{name}\". Details were saved to the log."


class ManageCommandsDialog(DragToMoveMixin, QDialog):
    """Every command in one list: filter it, edit one, or delete several.

    Deleting happens here, after a confirmation that says how many and which
    groups open them. Editing is the main window's job: ``editRequested``
    carries the command's name and the dialog closes so the editor can open.
    """

    editRequested = pyqtSignal(str)
    commandsDeleted = pyqtSignal(list)

    def __init__(self, commands: list[dict], command_manager, icon_manager=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Manage Commands")
        self.setObjectName("ProgramImportDialog")
        self.setWindowFlags(FRAMELESS_DIALOG)
        self.setMinimumSize(560, 640)
        self._command_manager = command_manager
        self._icon_manager = icon_manager
        self._icon_provider = QFileIconProvider()

        title = QLabel("Manage Commands")
        title.setObjectName("dialogTitle")

        self.filter_box = QLineEdit()
        self.filter_box.setPlaceholderText("Filter by name, alias or target...")
        self.filter_box.setClearButtonEnabled(True)
        self.filter_box.setAccessibleName("Filter commands")
        self.filter_box.textChanged.connect(self._apply_filter)

        self.command_list = QListWidget()
        self.command_list.setObjectName("ProgramImportList")
        self.command_list.setAccessibleName("Commands")
        self.command_list.setIconSize(QSize(28, 28))
        self.command_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.command_list.itemSelectionChanged.connect(self._update_buttons)
        self.command_list.itemDoubleClicked.connect(lambda item: self._request_edit(item))

        commands = sorted(
            (c for c in commands if c.get("type") != "system"),
            key=lambda c: str(c.get("name", "")).casefold(),
        )
        for command in commands:
            detail = command.get("location", "")
            if command.get("type") == "group":
                detail = "Opens " + ", ".join(str(t) for t in command.get("targets", []))
            item = QListWidgetItem(f"{command.get('name', '')}\n{detail}")
            item.setIcon(self._resolve_icon(command))
            item.setData(Qt.ItemDataRole.UserRole, command.get("name"))
            item.setData(Qt.ItemDataRole.UserRole + 1, command)
            item.setToolTip(str(detail))
            self.command_list.addItem(item)

        self.count_label = QLabel()
        self.count_label.setObjectName("dialogSubtitle")
        self.empty_message = QLabel("No commands yet." if not commands else "")
        self.empty_message.setObjectName("dialogSubtitle")
        self.empty_message.setVisible(not commands)

        self.edit_button = QPushButton("Edit...")
        self.edit_button.setToolTip("Open the selected command in the editor")
        self.edit_button.clicked.connect(lambda: self._request_edit())
        self.delete_button = QPushButton("Delete Selected...")
        self.delete_button.setToolTip("Delete the selected commands")
        self.delete_button.clicked.connect(self.delete_selected)
        for button in (self.edit_button, self.delete_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        # Enter in the filter box should never be the one that deletes.
        self.delete_button.setAutoDefault(False)

        actions = QHBoxLayout()
        actions.addWidget(self.count_label)
        actions.addStretch(1)
        actions.addWidget(self.edit_button)
        actions.addWidget(self.delete_button)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(self.filter_box)
        layout.addWidget(self.command_list, 1)
        layout.addWidget(self.empty_message)
        layout.addLayout(actions)
        layout.addWidget(buttons)
        self._update_buttons()

    def showEvent(self, event):
        super().showEvent(event)
        parent = self.parentWidget()
        fit_within_screen(self, parent.frameGeometry().center() if parent is not None else None)
        self.raise_()
        self.activateWindow()
        self.filter_box.setFocus()

    def _resolve_icon(self, command: dict) -> QIcon:
        if self._icon_manager is not None:
            icon = self._icon_manager.resolve_command_icon(command)
            if not icon.isNull():
                return icon
        location = command.get("location", "")
        info = QFileInfo(str(location))
        if location and info.exists():
            icon = self._icon_provider.icon(info)
            if not icon.isNull():
                return icon
        return QIcon()

    def _items(self) -> list[QListWidgetItem]:
        return [item for item in (self.command_list.item(i) for i in range(self.command_list.count())) if item is not None]

    def _apply_filter(self, text: str):
        needle = text.strip().casefold()
        for item in self._items():
            command = item.data(Qt.ItemDataRole.UserRole + 1) or {}
            haystack = " ".join(
                [str(command.get("name", "")), str(command.get("location", "")), *map(str, command.get("aliases", []))]
            ).casefold()
            hidden = bool(needle) and needle not in haystack
            item.setHidden(hidden)
            if hidden:
                item.setSelected(False)
        self._update_buttons()

    def selected_names(self) -> list[str]:
        """Selected commands that the filter is showing, in list order."""
        return [item.data(Qt.ItemDataRole.UserRole) for item in self._items() if item.isSelected() and not item.isHidden()]

    def _update_buttons(self):
        selected = self.selected_names()
        shown = sum(1 for item in self._items() if not item.isHidden())
        total = self.command_list.count()
        self.edit_button.setEnabled(len(selected) == 1)
        self.delete_button.setEnabled(bool(selected))
        self.delete_button.setText(f"Delete {len(selected)} Selected..." if len(selected) > 1 else "Delete Selected...")
        summary = f"{total} command{'s' if total != 1 else ''}" if shown == total else f"{shown} of {total} shown"
        if selected:
            summary += f", {len(selected)} selected"
        self.count_label.setText(summary)

    def _request_edit(self, item: QListWidgetItem | None = None):
        if item is not None:
            name = item.data(Qt.ItemDataRole.UserRole)
        else:
            selected = self.selected_names()
            if len(selected) != 1:
                return
            name = selected[0]
        self.editRequested.emit(str(name))
        self.accept()

    def _confirm_delete(self, names: list[str]) -> bool:
        groups = {name: groups_containing(self._command_manager, name) for name in names}
        answer = QMessageBox.question(
            self,
            "Delete Commands",
            delete_confirmation_text(names, groups),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Yes

    def delete_selected(self):
        names = self.selected_names()
        if not names or not self._confirm_delete(names):
            return
        deleted = []
        for name in names:
            try:
                self._command_manager.delete_command(name)
                deleted.append(name)
            except Exception as error:
                log.exception("Could not delete command %r", name)
                QMessageBox.warning(self, "Delete Commands", _delete_failure_message(name, error))
                break
        gone = set(deleted)
        for item in self._items():
            if item.data(Qt.ItemDataRole.UserRole) in gone:
                self.command_list.takeItem(self.command_list.row(item))
        self.empty_message.setText("No commands left." if not self.command_list.count() else "")
        self.empty_message.setVisible(not self.command_list.count())
        self._update_buttons()
        if deleted:
            self.commandsDeleted.emit(deleted)


class ImportCommandsDialog(DragToMoveMixin, QDialog):
    """Review commands parsed from an imported file before adding them.

    The pencil on a row opens it in the command editor to change its name,
    type, target, aliases, description and icon before it is imported.
    Candidates whose target can't be found are shown disabled with an error
    message until they have been fixed that way.
    Candidates that would collide with an existing command are left out
    entirely so existing commands are never overwritten.
    """

    def __init__(self, candidates: list[dict], command_manager, icon_manager=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import Commands")
        self.setObjectName("ProgramImportDialog")
        self.setWindowFlags(FRAMELESS_DIALOG)
        self.setMinimumSize(560, 680)
        self._command_manager = command_manager
        self._icon_manager = icon_manager
        self._preview_icons = {}
        if icon_manager is not None:
            self._preview_icons = {
                "file": QIcon(icon_manager.settings.paths.file_icon),
                "url": QIcon(icon_manager.settings.paths.url_command_icon),
            }

        title = QLabel("Import Commands")
        title.setObjectName("dialogTitle")

        self.command_list = QListWidget()
        self.command_list.setObjectName("ScanResultList")
        self.command_list.setAccessibleName("Commands to import")
        self.command_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.command_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.command_list.itemClicked.connect(self._toggle_item)

        importable = [c for c in candidates if not c.get("_conflict")]
        for candidate in importable:
            self._add_item(candidate)

        skipped_count = len(candidates) - len(importable)
        subtitle = ""
        if not importable:
            subtitle = "No importable commands were found in this file."
        elif skipped_count:
            subtitle = f"{skipped_count} already exist and were left out."
        empty_message = QLabel(subtitle)
        empty_message.setObjectName("dialogSubtitle")
        empty_message.setVisible(bool(subtitle))

        select_all_button = QPushButton("Select All Valid")
        select_none_button = QPushButton("Select None")
        select_all_button.setObjectName("programImportSelectButton")
        select_none_button.setObjectName("programImportSelectButton")
        select_all_button.setEnabled(bool(importable))
        select_none_button.setEnabled(bool(importable))
        select_all_button.clicked.connect(lambda: self._set_all_checked(Qt.CheckState.Checked))
        select_none_button.clicked.connect(lambda: self._set_all_checked(Qt.CheckState.Unchecked))

        selection_row = QHBoxLayout()
        selection_row.addWidget(QLabel(f"{len(importable)} found"))
        selection_row.addStretch(1)
        selection_row.addWidget(select_all_button)
        selection_row.addWidget(select_none_button)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self._import_button = cast(QPushButton, buttons.addButton("Import Selected", QDialogButtonBox.ButtonRole.AcceptRole))
        self._import_button.setEnabled(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addLayout(selection_row)
        layout.addWidget(self.command_list, 1)
        layout.addWidget(empty_message)
        layout.addWidget(buttons)

    def showEvent(self, event):
        super().showEvent(event)
        parent = self.parentWidget()
        fit_within_screen(self, parent.frameGeometry().center() if parent is not None else None)
        self.raise_()
        self.activateWindow()

    def _add_item(self, candidate: dict, row: int | None = None, checked: bool = False):
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, candidate)
        widget = ImportRow(candidate, self._resolve_icon(candidate), checked, self.command_list)
        widget.check.toggled.connect(lambda _on: self._update_import_enabled())
        widget.edit_button.clicked.connect(lambda: self._edit_item(item))
        item.setSizeHint(widget.sizeHint())
        if row is None:
            self.command_list.addItem(item)
        else:
            self.command_list.insertItem(row, item)
        self.command_list.setItemWidget(item, widget)
        return item

    def _row_widget(self, item: QListWidgetItem) -> "ImportRow | None":
        widget = self.command_list.itemWidget(item) if item is not None else None
        return widget if isinstance(widget, ImportRow) else None

    def _toggle_item(self, item: QListWidgetItem):
        widget = self._row_widget(item)
        if widget is not None and widget.check.isEnabled():
            widget.check.setChecked(not widget.check.isChecked())

    def _resolve_icon(self, candidate: dict) -> QIcon:
        if candidate.get("_error"):
            return QIcon()
        icon_type = "url" if candidate.get("type") == "url" else "file"
        return self._preview_icons.get(icon_type, QIcon())

    def _set_all_checked(self, state: Qt.CheckState):
        for index in range(self.command_list.count()):
            widget = self._row_widget(self.command_list.item(index))
            if widget is not None and widget.check.isEnabled():
                widget.check.setChecked(state == Qt.CheckState.Checked)
        self._update_import_enabled()

    def _update_import_enabled(self, _item=None):
        self._import_button.setEnabled(bool(self.selected_candidates()))

    def _edit_item(self, item: QListWidgetItem):
        """The pencil: open the candidate in the command editor and apply the result."""
        if item is None:
            return
        candidate = item.data(Qt.ItemDataRole.UserRole)
        # An icon that arrived as embedded bytes becomes a source file first,
        # so the editor can show it and keep it like any other recipe.
        self._command_manager.materialize_candidate_source(candidate)

        dialog = CommandEditDialog(candidate, self._command_manager, self._icon_manager, self, title="Edit Import")
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.result_command is None:
            return

        apply_edited_candidate(candidate, dialog.result_command)
        candidate["_error"], candidate["_conflict"] = self._command_manager.check_import_candidate(candidate)

        row = self.command_list.row(item)
        self.command_list.takeItem(row)
        # The user just prepared this command, so it is meant to be imported.
        self._add_item(candidate, row=row, checked=not candidate["_error"] and not candidate["_conflict"])
        self._update_import_enabled()

    def selected_candidates(self) -> list[dict]:
        selected = []
        for index in range(self.command_list.count()):
            item = self.command_list.item(index)
            widget = self._row_widget(item)
            if widget is not None and widget.check.isChecked():
                selected.append(item.data(Qt.ItemDataRole.UserRole))
        return selected
