from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from PyQt6.QtCore import QFileInfo, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPen, QShortcut
from PyQt6.QtWidgets import (
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
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


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
        self.setMinimumWidth(110)
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
from .icon_browser import FRAMELESS_DIALOG, RECIPE_KEYS, DragToMoveMixin
from .command_editor import CommandEditorPanel


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

        # Pinned, not merely resized: the alias grid sizes itself from its
        # container, so an unconstrained dialog would grow without bound.
        settings = icon_manager.settings
        self.setFixedSize(max(520, settings.ui.program_width), settings.ui.editor_height)

        self.panel = CommandEditorPanel(command, icon_manager, command_manager, self, standalone=True, title=title)
        self.panel.saved.connect(self._on_saved)
        self.panel.closed.connect(self._on_closed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.panel)

    def showEvent(self, event):
        super().showEvent(event)
        parent = self.parentWidget()
        if parent is not None:
            center = parent.frameGeometry().center()
            self.move(center - self.rect().center())
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

        left_column = QVBoxLayout()
        left_column.setSpacing(10)
        left_column.addWidget(self._search_group())
        left_column.addWidget(self._shortcuts_group())
        left_column.addWidget(self._commands_group())
        left_column.addStretch(1)

        right_column = QVBoxLayout()
        right_column.setSpacing(10)
        right_column.addWidget(self._ui_group())
        right_column.addStretch(1)

        content_layout.addLayout(left_column, 1)
        content_layout.addLayout(right_column, 1)

        content_scroll = QScrollArea()
        content_scroll.setWidgetResizable(True)
        content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content_scroll.setFrameShape(QFrame.Shape.NoFrame)
        content_scroll.setWidget(content)

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

    def _group(self, title, fields):
        group = QGroupBox(title)
        form = QFormLayout(group)
        form.setContentsMargins(14, 14, 14, 10)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(6)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        for label, key, control in fields:
            self._controls[key] = control
            form.addRow(label, control)
        return group

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

    @staticmethod
    def _choice(value, options):
        """Drop-down over (stored_value, label) pairs; unknown values fall back to the first."""
        control = NoScrollComboBox()
        for stored, label in options:
            control.addItem(label, stored)
        index = control.findData(value)
        control.setCurrentIndex(index if index >= 0 else 0)
        return control

    def _ui_group(self):
        opacity = NoScrollDoubleSpinBox()
        opacity.setRange(0.30, 1.00)
        opacity.setSingleStep(0.05)
        opacity.setDecimals(2)
        opacity.setValue(self._settings.ui.window_opacity)
        return self._group(
            "Launcher Appearance",
            [
                ("Program width", "ui.program_width", self._spin(self._settings.ui.program_width, 280, 2000)),
                ("Search height", "ui.search_height", self._spin(self._settings.ui.search_height, 50, 400)),
                ("Results height", "ui.results_height", self._spin(self._settings.ui.results_height, 80, 1000)),
                ("Editor height", "ui.editor_height", self._spin(self._settings.ui.editor_height, 400, 1400)),
                ("Window opacity", "ui.window_opacity", opacity),
                ("Search font size", "ui.search_font_size", self._spin(self._settings.ui.search_font_size, 8, 48)),
                ("Search text color", "ui.search_text_color", self._color(self._settings.ui.search_text_color)),
                ("Result font size", "ui.result_font_size", self._spin(self._settings.ui.result_font_size, 8, 32)),
                ("Result text color", "ui.result_text_color", self._color(self._settings.ui.result_text_color)),
                (
                    "Description font size",
                    "ui.description_font_size",
                    self._spin(self._settings.ui.description_font_size, 7, 24),
                ),
                (
                    "Description text color",
                    "ui.description_text_color",
                    self._color(self._settings.ui.description_text_color),
                ),
                ("Clock font size", "ui.clock_font_size", self._spin(self._settings.ui.clock_font_size, 6, 18)),
                ("Clock day color", "ui.clock_day_text_color", self._color(self._settings.ui.clock_day_text_color)),
                ("Clock date color", "ui.clock_date_text_color", self._color(self._settings.ui.clock_date_text_color)),
            ],
        )

    def _search_group(self):
        return self._group(
            "Search",
            [
                (
                    "Maximum results",
                    "search.max_results",
                    self._spin(self._settings.search.max_results, 1, 2_147_483_647),
                ),
                (
                    "Open on screen with mouse",
                    "general.show_on_screen_with_mouse",
                    self._check(self._settings.general.show_on_screen_with_mouse),
                ),
                (
                    "Install updates automatically",
                    "general.auto_install_updates",
                    self._check(self._settings.general.auto_install_updates),
                ),
                ("Autocomplete", "search.autocomplete", self._check(self._settings.search.autocomplete)),
                (
                    "Sort results by",
                    "search.sort_results",
                    self._choice(
                        self._settings.search.sort_results,
                        [("popularity", "Most used first"), ("name", "Name (A to Z)")],
                    ),
                ),
                (
                    "Show descriptions",
                    "search.show_descriptions",
                    self._check(self._settings.search.show_descriptions),
                ),
                (
                    "Show run count on results",
                    "search.show_run_counter",
                    self._check(self._settings.search.show_run_counter),
                ),
                (
                    "Show command tree",
                    "search.show_command_tree",
                    self._check(self._settings.search.show_command_tree),
                ),
            ],
        )

    def _shortcuts_group(self):
        return self._group(
            "Shortcuts",
            [
                ("Launcher hotkey", "general.hotkey", self._hotkey_edit(self._settings.general.hotkey)),
                (
                    "Edit selected command",
                    "shortcuts.edit_selected_command",
                    self._hotkey_edit(self._settings.shortcuts.edit_selected_command),
                ),
                (
                    "New command",
                    "shortcuts.new_command",
                    self._hotkey_edit(self._settings.shortcuts.new_command),
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
                show_on_screen_with_mouse=bool(self._value("general.show_on_screen_with_mouse")),
                auto_install_updates=bool(self._value("general.auto_install_updates")),
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
                sort_results=self._value("search.sort_results"),
                show_descriptions=self._value("search.show_descriptions"),
                show_run_counter=self._value("search.show_run_counter"),
                show_command_tree=self._value("search.show_command_tree"),
            ),
            shortcuts=ShortcutSettings(
                edit_selected_command=self._value("shortcuts.edit_selected_command"),
                new_command=self._value("shortcuts.new_command"),
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


class ProgramImportDialog(DragToMoveMixin, QDialog):
    """Choose discovered programs before adding them as Dash commands.

    Any candidate can be opened in the command editor (Edit Selected, or a
    double-click) to change its name, aliases, description, target or icon
    before it is added.
    """

    def __init__(
        self,
        candidates: list[dict],
        existing_locations: set[str],
        icon_manager=None,
        parent=None,
        command_manager=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Auto-Populate Commands")
        self.setObjectName("ProgramImportDialog")
        self.setWindowFlags(FRAMELESS_DIALOG)
        self.setMinimumSize(560, 640)
        self._icon_manager = icon_manager
        self._command_manager = command_manager
        self._icon_provider = QFileIconProvider()

        title = QLabel("Auto-Populate Commands")
        title.setObjectName("dialogTitle")

        self.program_list = QListWidget()
        self.program_list.setObjectName("ProgramImportList")
        self.program_list.setIconSize(QSize(28, 28))

        selectable_candidates = filter_new_program_commands(candidates, existing_locations)
        for candidate in selectable_candidates:
            self._add_item(candidate)

        empty_message = QLabel("No new installed programs were found." if not selectable_candidates else "")
        empty_message.setObjectName("dialogSubtitle")
        empty_message.setVisible(not selectable_candidates)

        edit_button = QPushButton("Edit Selected...")
        edit_button.setObjectName("programImportSelectButton")
        edit_button.setEnabled(bool(selectable_candidates) and command_manager is not None)
        edit_button.setToolTip("Open the selected program in the editor before adding it")
        edit_button.clicked.connect(self._edit_selected)
        self.program_list.itemDoubleClicked.connect(lambda _item: self._edit_selected())

        select_all_button = QPushButton("Select All")
        select_none_button = QPushButton("Select None")
        select_all_button.setObjectName("programImportSelectButton")
        select_none_button.setObjectName("programImportSelectButton")
        select_all_button.setEnabled(bool(selectable_candidates))
        select_none_button.setEnabled(bool(selectable_candidates))
        select_all_button.clicked.connect(lambda: self._set_all_checked(Qt.CheckState.Checked))
        select_none_button.clicked.connect(lambda: self._set_all_checked(Qt.CheckState.Unchecked))

        selection_row = QHBoxLayout()
        selection_row.addWidget(QLabel(f"{len(selectable_candidates)} found"))
        selection_row.addStretch(1)
        selection_row.addWidget(edit_button)
        selection_row.addWidget(select_all_button)
        selection_row.addWidget(select_none_button)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        add_button = cast(QPushButton, buttons.addButton("Add Selected", QDialogButtonBox.ButtonRole.AcceptRole))
        add_button.setEnabled(bool(selectable_candidates))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addLayout(selection_row)
        layout.addWidget(self.program_list, 1)
        layout.addWidget(empty_message)
        layout.addWidget(buttons)

    def showEvent(self, event):
        # Frameless windows are not placed or focused by the window manager
        super().showEvent(event)
        parent = self.parentWidget()
        if parent is not None:
            center = parent.frameGeometry().center()
            self.move(center - self.rect().center())
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

    def _add_item(self, candidate: dict, row: int | None = None, checked: bool = False):
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
            self.program_list.addItem(item)
        else:
            self.program_list.insertItem(row, item)
        return item

    def _edit_selected(self):
        """Open the selected program in the command editor and apply the result."""
        item = self.program_list.currentItem()
        if item is None or self._command_manager is None:
            return
        candidate = item.data(Qt.ItemDataRole.UserRole)

        dialog = CommandEditDialog(candidate, self._command_manager, self._icon_manager, self, title="Edit Program")
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.result_command is None:
            return

        apply_edited_candidate(candidate, dialog.result_command)
        candidate["_error"], candidate["_conflict"] = self._command_manager.check_import_candidate(candidate)

        row = self.program_list.row(item)
        self.program_list.takeItem(row)
        importable = not candidate["_error"] and not candidate["_conflict"]
        new_item = self._add_item(candidate, row=row, checked=importable)
        self.program_list.setCurrentItem(new_item)

    def _set_all_checked(self, state: Qt.CheckState):
        for index in range(self.program_list.count()):
            item = self.program_list.item(index)
            if item is not None and item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                item.setCheckState(state)

    def selected_candidates(self) -> list[dict]:
        selected = []
        for index in range(self.program_list.count()):
            item = self.program_list.item(index)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
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
        if parent is not None:
            center = parent.frameGeometry().center()
            self.move(center - self.rect().center())
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
        if parent is not None:
            center = parent.frameGeometry().center()
            self.move(center - self.rect().center())
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
