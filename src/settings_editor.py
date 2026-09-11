import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from PyQt6.QtCore import QEvent, QFileInfo, QPointF, QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QKeySequence, QLinearGradient, QPainter, QPen, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QColorDialog,
    QFileIconProvider,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QTabWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


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

    def __init__(self, scroll_area: QScrollArea, color: str = "#202228"):
        super().__init__(scroll_area.viewport())
        self._scroll_area = scroll_area
        self._color = QColor(color)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        scroll_area.viewport().installEventFilter(self)
        scroll_bar = scroll_area.verticalScrollBar()
        scroll_bar.valueChanged.connect(self.update)
        scroll_bar.rangeChanged.connect(lambda *_: self.update())
        self._match_viewport()

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


class HotkeyLineEdit(QLineEdit):
    """Read-only editor that records the next key combination pressed."""

    def __init__(self, value, parent=None):
        super().__init__(str(value), parent)
        self.setReadOnly(True)
        self.setPlaceholderText("Press a key combination")
        self.setToolTip("Click here, then press the key combination to use")

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


class ColorButton(QPushButton):
    colorChanged = pyqtSignal(str)

    def __init__(self, value, parent=None):
        super().__init__(parent)
        color = QColor(str(value))
        self._color = color if color.isValid() else QColor(Qt.GlobalColor.white)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumWidth(84)
        self.clicked.connect(self._choose_color)
        self._update_swatch()

    def color(self) -> str:
        return self._color.name(QColor.NameFormat.HexRgb)

    def _choose_color(self):
        selected = QColorDialog.getColor(self._color, self, "Choose Text Color")
        if not selected.isValid() or selected == self._color:
            return
        self._color = selected
        self._update_swatch()
        self.colorChanged.emit(self.color())

    def _update_swatch(self):
        color = self.color()
        text_color = "#111318" if self._color.lightness() > 150 else "#ffffff"
        self.setText(color.upper())
        self.setStyleSheet(
            f"background-color: {color}; color: {text_color}; "
            "border: 1px solid #596170; border-radius: 6px; padding: 6px 10px;"
        )


class XCheckBox(QCheckBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self):
        return QSize(24, 24)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        size = 18
        rect = self.rect()
        box = rect.adjusted(1, (rect.height() - size) // 2, -(rect.width() - size - 1), -((rect.height() - size) // 2))
        border = QColor("#8b929e") if self.isEnabled() else QColor("#555b65")
        fill = QColor("#2d7dff") if self.isChecked() else QColor("#202228")
        painter.setPen(QPen(border, 1.5))
        painter.setBrush(fill)
        painter.drawRoundedRect(box, 4, 4)

        if self.isChecked():
            painter.setPen(QPen(QColor("#ffffff"), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            inset = 5
            painter.drawLine(box.left() + inset, box.top() + inset, box.right() - inset, box.bottom() - inset)
            painter.drawLine(box.right() - inset, box.top() + inset, box.left() + inset, box.bottom() - inset)

        painter.end()

from .settings import (
    GeneralSettings,
    SearchSettings,
    Settings,
    ShortcutSettings,
    UISettings,
)
from .installed_programs import filter_new_program_commands
from .window_placement import fit_within_screen
from .icon_browser import FRAMELESS_DIALOG, RECIPE_KEYS, DragToMoveMixin
from .browsers import DEFAULT_BROWSER, installed_browsers
from .command_editor import CommandEditorPanel


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
    resetRunCountsRequested = pyqtSignal()

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
        self._update_dirty_state()

    def _group(self, title, rows):
        """A titled box of label/field rows.

        Each row is (label, key, control) or (label, [(key, control), ...]) for
        several controls side by side. Fields all stretch to the same right
        edge and never wrap under their label, so rows line up whatever mix
        of spin boxes, drop-downs and colour buttons a group holds.
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
                form.addRow(label, self._stretchy(control))
            else:
                label, pairs = row
                holder = QWidget()
                strip = QHBoxLayout(holder)
                strip.setContentsMargins(0, 0, 0, 0)
                strip.setSpacing(8)
                for key, control in pairs:
                    self._controls[key] = control
                    strip.addWidget(self._stretchy(control), 1)
                form.addRow(label, holder)
        return group

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
    def _check(value):
        control = XCheckBox()
        control.setChecked(value is True or str(value).lower() == "true")
        return control

    @staticmethod
    def _color(value):
        return ColorButton(value)

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
        return self._group(
            "General",
            [
                ("Open Dash with", "general.hotkey", self._hotkey_edit(general.hotkey)),
                ("Open on display", "general.launcher_screen", self._choice(general.launcher_screen, self._screen_options())),
                ("Websites open in", "general.browser", self._choice(general.browser, browser_options(general.browser))),
                ("Check updates at startup", "general.check_updates_on_startup", self._check(general.check_updates_on_startup)),
            ],
        )

    def _search_group(self):
        search = self._settings.search
        return self._group(
            "Search",
            [
                ("Autocomplete", "search.autocomplete", self._check(search.autocomplete)),
                ("Ignore capitalisation", "search.ignore_case", self._check(search.ignore_case)),
                (
                    "Sort results by",
                    "search.sort_results",
                    self._choice(search.sort_results, [("popularity", "Most used first"), ("name", "Name (A to Z)")]),
                ),
                ("Maximum results", "search.max_results", self._spin(search.max_results, 1, 200)),
            ],
        )

    def _results_group(self):
        search = self._settings.search
        return self._group(
            "Results",
            [
                ("Descriptions", "search.show_descriptions", self._check(search.show_descriptions)),
                ("Run counts", "search.show_run_counter", self._check(search.show_run_counter)),
                ("Edit buttons", "search.show_edit_button", self._check(search.show_edit_button)),
                ("Command tree panel", "search.show_command_tree", self._check(search.show_command_tree)),
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
        return self._group(
            "Layout",
            [
                ("Width", "ui.program_width", self._spin(ui.program_width, 280, 2000)),
                ("Search box height", "ui.search_height", self._spin(ui.search_height, 50, 400)),
                ("Results height", "ui.results_height", self._spin(ui.results_height, 80, 1000)),
                ("Editor height", "ui.editor_height", self._spin(ui.editor_height, 400, 1400)),
                ("Opacity", "ui.window_opacity", opacity),
            ],
        )

    def _text_group(self):
        """One row per piece of text: its size, then its colour(s)."""
        ui = self._settings.ui
        return self._group(
            "Text",
            [
                ("Search box", [("ui.search_font_size", self._spin(ui.search_font_size, 8, 48)), ("ui.search_text_color", self._color(ui.search_text_color))]),
                ("Result names", [("ui.result_font_size", self._spin(ui.result_font_size, 8, 32)), ("ui.result_text_color", self._color(ui.result_text_color))]),
                ("Descriptions", [("ui.description_font_size", self._spin(ui.description_font_size, 7, 24)), ("ui.description_text_color", self._color(ui.description_text_color))]),
                ("Clock size", "ui.clock_font_size", self._spin(ui.clock_font_size, 6, 18)),
                (
                    "Clock day, date",
                    [
                        ("ui.clock_day_text_color", self._color(ui.clock_day_text_color)),
                        ("ui.clock_date_text_color", self._color(ui.clock_date_text_color)),
                    ],
                ),
            ],
        )

    def _commands_group(self):
        group = QGroupBox("Commands")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(14, 14, 14, 10)
        layout.setSpacing(4)

        auto_populate_button = QPushButton("Auto-Populate Installed Programs")
        auto_populate_button.setCursor(Qt.CursorShape.PointingHandCursor)
        auto_populate_button.clicked.connect(self.importProgramsRequested.emit)

        export_button = QPushButton("Export Commands...")
        export_button.setCursor(Qt.CursorShape.PointingHandCursor)
        export_button.clicked.connect(self.exportCommandsRequested.emit)

        import_button = QPushButton("Import Commands...")
        import_button.setCursor(Qt.CursorShape.PointingHandCursor)
        import_button.clicked.connect(self.importCommandsRequested.emit)

        reset_counts_button = QPushButton("Reset All Run Counts...")
        reset_counts_button.setCursor(Qt.CursorShape.PointingHandCursor)
        reset_counts_button.setToolTip("Set every command's run count back to zero")
        reset_counts_button.clicked.connect(self.resetRunCountsRequested.emit)

        layout.addWidget(auto_populate_button)
        layout.addWidget(export_button)
        layout.addWidget(import_button)
        layout.addWidget(reset_counts_button)
        return group

    def _value(self, key) -> Any:
        control = self._controls[key]
        if isinstance(control, ColorButton):
            return control.color()
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
            ),
            ui=UISettings(
                program_width=self._value("ui.program_width"),
                search_height=self._value("ui.search_height"),
                results_height=self._value("ui.results_height"),
                editor_height=self._value("ui.editor_height"),
                window_opacity=self._value("ui.window_opacity"),
                search_font_size=self._value("ui.search_font_size"),
                search_text_color=self._value("ui.search_text_color"),
                result_font_size=self._value("ui.result_font_size"),
                result_text_color=self._value("ui.result_text_color"),
                description_font_size=self._value("ui.description_font_size"),
                description_text_color=self._value("ui.description_text_color"),
                clock_font_size=self._value("ui.clock_font_size"),
                clock_day_text_color=self._value("ui.clock_day_text_color"),
                clock_date_text_color=self._value("ui.clock_date_text_color"),
            ),
            search=SearchSettings(
                max_results=self._value("search.max_results"),
                autocomplete=self._value("search.autocomplete"),
                ignore_case=bool(self._value("search.ignore_case")),
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

    def _is_dirty(self) -> bool:
        current = self._collect()
        return (
            asdict(current.general) != asdict(self._settings.general)
            or asdict(current.ui) != asdict(self._settings.ui)
            or asdict(current.search) != asdict(self._settings.search)
            or asdict(current.shortcuts) != asdict(self._settings.shortcuts)
        )

    def _update_dirty_state(self):
        dirty = self._is_dirty()
        self.close_button.setVisible(not dirty)
        self.cancel_button.setVisible(dirty)
        self.save_button.setVisible(dirty)

    def _connect_signals(self):
        for control in self._controls.values():
            if isinstance(control, ColorButton):
                control.colorChanged.connect(self._update_dirty_state)
            elif isinstance(control, QComboBox):
                control.currentIndexChanged.connect(self._update_dirty_state)
            elif isinstance(control, QLineEdit):
                control.textChanged.connect(self._update_dirty_state)
            elif isinstance(control, QCheckBox):
                control.toggled.connect(self._update_dirty_state)
            elif isinstance(control, (QSpinBox, QDoubleSpinBox)):
                control.valueChanged.connect(self._update_dirty_state)

    def _save(self):
        settings = self._collect()
        settings.save(self._settings_path)
        self.saved.emit(settings)
        self.closed.emit()

    def _cancel(self):
        self.closed.emit()


def candidate_kind(candidate: dict) -> str:
    """'website', 'folder' or 'app', from what the candidate points at."""
    location = str(candidate.get("location", ""))
    if candidate.get("type") == "url" or location.startswith(("http://", "https://")):
        return "website"
    try:
        if os.path.isdir(location):
            return "folder"
    except OSError:
        pass
    return "app"


class ProgramImportDialog(DragToMoveMixin, QDialog):
    """Choose what the scan found before it becomes commands.

    One tab per kind of thing (apps and tools, folders, websites), each with
    its own filter and list, so a long scan is worked through a page at a
    time instead of as one mixed list. Double-clicking an entry opens it in
    the command editor to change its name, aliases, description, target or
    icon before it is added.
    """

    KINDS = (("app", "Apps and tools"), ("folder", "Folders"), ("website", "Websites"))

    def __init__(
        self,
        candidates: list[dict],
        existing_locations: set[str],
        icon_manager=None,
        parent=None,
        command_manager=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Add Commands")
        self.setObjectName("ProgramImportDialog")
        self.setWindowFlags(FRAMELESS_DIALOG)
        self.setMinimumSize(600, 680)
        self._icon_manager = icon_manager
        self._command_manager = command_manager
        self._icon_provider = QFileIconProvider()

        title = QLabel("Add Commands")
        title.setObjectName("dialogTitle")
        subtitle = QLabel(
            "Tick what Dash should know about; nothing is added until you choose it. "
            "Double-click an entry to change its name, aliases or icon first."
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
            group.sort(key=lambda c: not c.get("suggested"))  # Windows' own suggestions lead
            page, list_widget, filter_box = self._build_page(kind, group)
            self.lists[kind] = list_widget
            self.filters[kind] = filter_box
            self.tabs.addTab(page, f"{heading} ({len(group)})")

        empty_message = QLabel("Nothing new was found. Everything the scan knows about is already a command.")
        empty_message.setObjectName("dialogSubtitle")
        empty_message.setWordWrap(True)
        empty_message.setVisible(not selectable)
        self.tabs.setVisible(bool(selectable))

        select_all_button = QPushButton("Select all on this tab")
        select_none_button = QPushButton("Select none")
        for button in (select_all_button, select_none_button):
            button.setObjectName("programImportSelectButton")
            button.setEnabled(bool(selectable))
        select_all_button.clicked.connect(lambda: self._set_visible_checked(Qt.CheckState.Checked))
        select_none_button.clicked.connect(lambda: self._set_visible_checked(Qt.CheckState.Unchecked))

        self.selected_label = QLabel()
        self.selected_label.setObjectName("dialogSubtitle")

        selection_row = QHBoxLayout()
        selection_row.addWidget(self.selected_label)
        selection_row.addStretch(1)
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
        list_widget = QListWidget()
        list_widget.setObjectName("ProgramImportList")
        list_widget.setIconSize(QSize(28, 28))
        # Long URLs would otherwise add a scrollbar under every page.
        list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for candidate in group:
            self._add_item(list_widget, candidate)
        filter_box.textChanged.connect(lambda text, lw=list_widget: self._apply_filter(lw, text))
        list_widget.setToolTip("Double-click to edit before adding")
        list_widget.itemChanged.connect(lambda _item: self._refresh_selection_count())
        list_widget.itemDoubleClicked.connect(self._edit_item)
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
        label = f"{candidate.get('name', '')}\n{candidate.get('location', '')}"
        aliases = [str(alias) for alias in candidate.get("aliases", []) if str(alias).strip()]
        if aliases:
            label += "\nAliases: " + ", ".join(aliases)
        error = candidate.get("_error")
        if error:
            label += f"\n⚠ {error}"
        item = QListWidgetItem(label)
        item.setIcon(self._resolve_icon(candidate))
        item.setData(Qt.ItemDataRole.UserRole, candidate)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        if error:
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            item.setForeground(QColor("#d9534f"))
        if row is None:
            list_widget.addItem(item)
        else:
            list_widget.insertItem(row, item)
        return item

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
        """Double-click: open the entry in the command editor and apply the result."""
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
        new_item = self._add_item(list_widget, candidate, row=row, checked=importable)
        list_widget.setCurrentItem(new_item)
        self._refresh_selection_count()

    def _set_visible_checked(self, state: Qt.CheckState):
        """Select all / none applies to the current tab, and only to the rows
        the filter is showing, so a filtered list can be ticked in one go."""
        list_widget = self._current_list()
        if list_widget is None:
            return
        for index in range(list_widget.count()):
            item = list_widget.item(index)
            if item is not None and not item.isHidden() and item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                item.setCheckState(state)
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
                if item is None or item.data(Qt.ItemDataRole.UserRole) is None:
                    continue
                if item.checkState() == Qt.CheckState.Checked:
                    selected.append(item.data(Qt.ItemDataRole.UserRole))
        return selected


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


class ImportCommandsDialog(DragToMoveMixin, QDialog):
    """Review commands parsed from an imported file before adding them.

    Any candidate can be opened in the command editor (Edit Selected, or a
    double-click) to change its name, type, target, aliases, description and
    icon before it is imported. Candidates whose target can't be found are
    shown disabled with an error message until they have been fixed that way.
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
        self.command_list.setObjectName("ProgramImportList")
        self.command_list.setIconSize(QSize(28, 28))

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

        fix_button = QPushButton("Edit Selected...")
        fix_button.setObjectName("programImportSelectButton")
        fix_button.setEnabled(bool(importable))
        fix_button.setToolTip("Open the selected command in the editor before importing it")
        fix_button.clicked.connect(self._edit_selected)
        self.command_list.itemDoubleClicked.connect(lambda _item: self._edit_selected())

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
        selection_row.addWidget(fix_button)
        selection_row.addWidget(select_all_button)
        selection_row.addWidget(select_none_button)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self._import_button = cast(QPushButton, buttons.addButton("Import Selected", QDialogButtonBox.ButtonRole.AcceptRole))
        self._import_button.setEnabled(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.command_list.itemChanged.connect(self._update_import_enabled)

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

    def _add_item(self, candidate: dict, row: int | None = None):
        error = candidate.get("_error")
        label = f"{candidate.get('name', '')}\n{candidate.get('location', '')}"
        aliases = [str(alias) for alias in candidate.get("aliases", []) if str(alias).strip()]
        if aliases:
            label += "\nAliases: " + ", ".join(aliases)
        if error:
            label += f"\n\u26a0 {error}"
        item = QListWidgetItem(label)
        item.setIcon(self._resolve_icon(candidate))
        item.setData(Qt.ItemDataRole.UserRole, candidate)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Unchecked)
        if error:
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            item.setForeground(QColor("#d9534f"))
        if row is None:
            self.command_list.addItem(item)
        else:
            self.command_list.insertItem(row, item)
        return item

    def _resolve_icon(self, candidate: dict) -> QIcon:
        if candidate.get("_error"):
            return QIcon()
        icon_type = "url" if candidate.get("type") == "url" else "file"
        return self._preview_icons.get(icon_type, QIcon())

    def _set_all_checked(self, state: Qt.CheckState):
        self.command_list.blockSignals(True)
        try:
            for index in range(self.command_list.count()):
                item = self.command_list.item(index)
                if item is not None and item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                    item.setCheckState(state)
        finally:
            self.command_list.blockSignals(False)
        self._update_import_enabled()

    def _update_import_enabled(self, _item=None):
        self._import_button.setEnabled(bool(self.selected_candidates()))

    def _edit_selected(self):
        """Open the selected candidate in the command editor and apply the result."""
        item = self.command_list.currentItem()
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
        new_item = self._add_item(candidate, row=row)
        if not candidate["_error"] and not candidate["_conflict"]:
            # The user just prepared this command, so it is meant to be imported.
            new_item.setCheckState(Qt.CheckState.Checked)
        self.command_list.setCurrentItem(new_item)
        self._update_import_enabled()

    def selected_candidates(self) -> list[dict]:
        selected = []
        for index in range(self.command_list.count()):
            item = self.command_list.item(index)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                selected.append(item.data(Qt.ItemDataRole.UserRole))
        return selected
