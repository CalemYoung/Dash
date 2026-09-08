import bisect
import math
from enum import IntEnum
from pathlib import Path

from PyQt6.QtWidgets import (
    QFileDialog,
    QFrame,
    QLineEdit,
    QPushButton,
    QGridLayout,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFileIconProvider,
)
from PyQt6.QtCore import Qt, QSize, QUrl, QTimer, QFileInfo, pyqtSignal
from PyQt6.QtGui import QIcon, QColor, QPixmap, QDesktopServices
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply

from src.icon_browser import IconStudio, glyph_pixmap, OutlineIcon


class CommandType(IntEnum):
    APP = 0
    FOLDER = 1
    URL = 2

    @classmethod
    def from_command(cls, command: dict) -> "CommandType":
        """Best-effort mapping from a stored command dict to an editor mode."""
        location = command.get("location", "") or ""
        if command.get("type") == "url" or location.startswith(("http://", "https://")):
            return cls.URL
        path = Path(location) if location else None
        if path is not None and (path.is_dir() or path.suffix == ""):
            return cls.FOLDER
        return cls.APP

    def to_stored_type(self) -> str:
        return "url" if self is CommandType.URL else "file"


class Alias(QFrame):
    def __init__(self, text, alias_box):
        super().__init__()
        self.alias_text = text
        self.alias_box = alias_box
        self.setObjectName("Alias")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)
        self.text_label = QLabel(text)
        self.delete_label = QLabel("\u2715")
        layout.addWidget(self.text_label)
        layout.addStretch()
        layout.addWidget(self.delete_label)

    def mousePressEvent(self, event):
        self.delete_alias()

    def delete_alias(self):
        self.alias_box.aliases.remove(self)
        self.alias_box.grid_layout.removeWidget(self)
        self.alias_box.update_grid()
        self.alias_box.aliasesChanged.emit()

    def __lt__(self, other):
        return self.alias_text < other.alias_text


class AliasBox(QFrame):
    aliasesChanged = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.MAX_NUMBER_OF_COLUMNS = 24
        self.width_per_column = 0
        self.aliases: list[Alias] = []
        self.keyword_validator = None

        self.grid_container = QFrame()
        self.grid_container.setObjectName("AliasGridContainer")
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.grid_layout.setContentsMargins(10, 10, 10, 10)
        self.grid_layout.setHorizontalSpacing(8)
        self.grid_layout.setVerticalSpacing(8)

        self.enter_box = QLineEdit()
        self.enter_box.setObjectName("AliasEnterBox")
        self.enter_box.returnPressed.connect(self.on_enter_pressed)
        self.enter_box.setPlaceholderText("Add alias, press \u21b5")
        self.enter_box.setMinimumWidth(100)
        self.enter_box.setTextMargins(6, 0, 0, 0)  # line up with the chip text

        self.error_label = QLabel()
        self.error_label.setObjectName("validationMessage")
        self.error_label.hide()

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(self.grid_container)
        outer_layout.addWidget(self.error_label)

    def set_keyword_validator(self, validator):
        self.keyword_validator = validator

    def _show_error(self, message):
        self.error_label.setText(message)
        self.error_label.show()

    def _clear_error(self):
        self.error_label.hide()

    def set_aliases(self, aliases):
        """Replace the current chips with the given alias strings."""
        for alias in list(self.aliases):
            self.grid_layout.removeWidget(alias)
            alias.deleteLater()
        self.aliases = []
        for text in aliases:
            bisect.insort(self.aliases, Alias(text, self))
        self.update_grid()
        self.aliasesChanged.emit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.width_per_column = self.width() // self.MAX_NUMBER_OF_COLUMNS
        for i in range(self.MAX_NUMBER_OF_COLUMNS):
            self.grid_layout.setColumnMinimumWidth(i, self.width_per_column)
        self.update_grid()

    def on_enter_pressed(self):
        text = self.enter_box.text().strip()
        if len(text) > 32:
            self._show_error("Aliases can be at most 32 characters.")
        elif len(self.aliases) >= 18:
            self._show_error("A command can have at most 18 aliases.")
        elif text in [alias.alias_text for alias in self.aliases] or text == "":
            self._show_error("Enter an alias that is not already listed.")
        elif self.keyword_validator and (error := self.keyword_validator(text)):
            self._show_error(error)
        else:
            self._clear_error()
            self.enter_box.setText("")
            alias = Alias(text, self)
            bisect.insort(self.aliases, alias)
            self.update_grid()
            self.aliasesChanged.emit()

    def update_grid(self):
        current_column = 0
        current_row = 0

        self.grid_layout.removeWidget(self.enter_box)

        for alias in self.aliases:
            columns_required = math.ceil((alias.sizeHint().width()) / max(self.width_per_column, 1)) + 2
            if current_column + columns_required > self.MAX_NUMBER_OF_COLUMNS:
                current_row += 1
                current_column = 0

            self.grid_layout.addWidget(alias, current_row, current_column, 1, columns_required)
            current_column += columns_required

        if current_column > 0 or not self.aliases:
            current_row += 1

        self.grid_layout.setRowMinimumHeight(0, self.enter_box.sizeHint().height() if not self.aliases else 0)

        self.grid_layout.addWidget(self.enter_box, current_row, 0, 1, self.MAX_NUMBER_OF_COLUMNS)


class CommandTypeSelector(QFrame):
    typeChanged = pyqtSignal(CommandType)

    def __init__(self, selection: CommandType):
        super().__init__()
        self.setObjectName("typeSelector")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self.buttons: dict[CommandType, QPushButton] = {}
        for type in CommandType:
            button = QPushButton(type.name.title())
            button.setObjectName("typeSegment")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked, t=type: self.select(t))
            layout.addWidget(button)
            self.buttons[type] = button

        self.select(selection)

    def select(self, command_type: CommandType):
        self.selection = command_type
        for type, button in self.buttons.items():
            button.setChecked(type == command_type)
        self.typeChanged.emit(command_type)


class CommandActionEditor(QFrame):
    # Indicator colours: idle border, checking amber, reachable green, error red
    _STATUS_COLORS = {
        "idle": "#353942",
        "checking": "#d0a215",
        "ok": "#3fb950",
        "bad": "#f85149",
    }

    # Resolved target icon; a null QIcon means "fall back to the default"
    iconResolved = pyqtSignal(QIcon)

    def __init__(self, url_icon_path):
        super().__init__()
        self._mode = CommandType.APP
        self._url_icon = QIcon(url_icon_path)
        self._network = QNetworkAccessManager(self)
        self._reply = None
        self._icon_provider = QFileIconProvider()

        self.label = QLabel("Select app that will launch")
        self.label.setObjectName("fieldLabel")

        self.command_action_edit_box = QLineEdit()
        self.command_action_edit_box.setPlaceholderText("Path to application or file")
        self.command_action_edit_box.textChanged.connect(self._on_text_changed)

        self.browse_button = QPushButton()
        self.browse_button.setIcon(QIcon(glyph_pixmap(OutlineIcon.FOLDER, 20, QColor("#e7e9ee"))))
        self.browse_button.setIconSize(QSize(20, 20))
        self.browse_button.clicked.connect(self.clicked)

        # URL mode: a status dot and a way to open the link
        self.status_dot = QLabel()
        self.status_dot.setObjectName("urlStatus")
        self.status_dot.setFixedSize(12, 12)
        self.open_button = QPushButton("Open")
        self.open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_button.clicked.connect(self._open_in_browser)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.status_dot)
        row.addWidget(self.command_action_edit_box)
        row.addWidget(self.browse_button)
        row.addWidget(self.open_button)

        # Debounce reachability checks while the user is still typing
        self._check_timer = QTimer(self)
        self._check_timer.setSingleShot(True)
        self._check_timer.setInterval(500)
        self._check_timer.timeout.connect(self._verify_url)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.label)
        layout.addLayout(row)

        self.set_mode(CommandType.APP)

    def set_mode(self, command_type: CommandType):
        self._mode = command_type
        is_url = command_type == CommandType.URL
        self.browse_button.setVisible(not is_url)
        self.status_dot.setVisible(is_url)
        self.open_button.setVisible(is_url)

        if command_type == CommandType.APP:
            self.label.setText("Select app that will launch")
            self.command_action_edit_box.setPlaceholderText("Path to application or file")
        elif command_type == CommandType.FOLDER:
            self.label.setText("Select folder to open")
            self.command_action_edit_box.setPlaceholderText("Path to folder")
        else:
            self.label.setText("Enter URL to open")
            self.command_action_edit_box.setPlaceholderText("https://example.com")

        self._on_text_changed(self.command_action_edit_box.text())

    def clicked(self):
        if self._mode == CommandType.FOLDER:
            path = QFileDialog.getExistingDirectory(self, "Select Folder")
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Select File")
        if path:
            self.command_action_edit_box.setText(path)

    def _on_text_changed(self, text):
        text = text.strip()
        if self._mode != CommandType.URL:
            self._resolve_file_icon(text)
            return
        url = QUrl.fromUserInput(text) if text else QUrl()
        valid = bool(text) and url.isValid() and url.scheme() in ("http", "https")
        self.open_button.setEnabled(valid)
        if not valid:
            self._check_timer.stop()
            self._set_status("idle", "Enter a full http(s) URL")
            self.iconResolved.emit(self._url_icon)
            return
        self._set_status("checking", "Checking...")
        self.iconResolved.emit(self._url_icon)
        self._check_timer.start()

    def _resolve_file_icon(self, path):
        """Use the OS icon for the app/file/folder, or fall back to the default."""
        if path and QFileInfo(path).exists():
            icon = self._icon_provider.icon(QFileInfo(path))
            self.iconResolved.emit(icon if not icon.isNull() else self._fallback_icon())
        else:
            self.iconResolved.emit(self._fallback_icon())

    def _fallback_icon(self):
        # Folder mode shows a folder by default; other modes use the app default
        if self._mode == CommandType.FOLDER:
            return self._icon_provider.icon(QFileIconProvider.IconType.Folder)
        return QIcon()

    def _verify_url(self):
        url = QUrl.fromUserInput(self.command_action_edit_box.text().strip())
        if not url.isValid():
            self._set_status("bad", "Invalid URL")
            return
        if self._reply is not None:
            old_reply = self._reply
            self._reply = None
            old_reply.finished.disconnect()
            old_reply.abort()
            old_reply.deleteLater()
        request = QNetworkRequest(url)
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
        )
        self._reply = self._network.head(request)
        reply = self._reply
        if reply is not None:
            reply.finished.connect(self._on_reply_finished)

    def _on_reply_finished(self):
        reply = self._reply
        if reply is None:
            return
        self._reply = None
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        ok = reply.error() == QNetworkReply.NetworkError.NoError and (status is None or status < 400)
        if ok:
            self._set_status("ok", f"Reachable ({status})" if status else "Reachable")
        else:
            self._set_status("bad", "Could not reach URL")
        reply.deleteLater()

    def _set_status(self, state, tooltip=""):
        color = self._STATUS_COLORS.get(state, self._STATUS_COLORS["idle"])
        self.status_dot.setStyleSheet(f"background:{color}; border-radius:6px;")
        self.status_dot.setToolTip(tooltip)

    def _open_in_browser(self):
        text = self.command_action_edit_box.text().strip()
        if text:
            QDesktopServices.openUrl(QUrl.fromUserInput(text))


class CommandEditorPanel(QFrame):
    """Full-panel editor for a single command. Saves in the background on exit."""

    # Emitted when the user leaves the editor (after any save/delete).
    closed = pyqtSignal()

    def __init__(self, command, icon_manager, cmd_manager, parent=None):
        super().__init__(parent)
        self.setObjectName("CommandEditorPanel")
        editor_mode = "edit" if command else "new"
        self.setProperty("editorMode", editor_mode)
        self.icon_manager = icon_manager
        self.cmd_manager = cmd_manager
        self._command = command or {}
        self._original_name = self._command.get("name") if command else None
        self._resolved_icon = QIcon()
        self._icon_path = self._command.get("icon")

        self._initial_name = self._command.get("name", "") if command else ""
        self._initial_type = CommandType.from_command(self._command) if command else CommandType.APP
        self._initial_location = self._command.get("location", "") if command else ""
        self._initial_aliases = sorted(self._command.get("aliases", [])) if command else []
        self._initial_icon = self._command.get("icon") if command else None

        title = QLabel("Edit Command" if command else "New Command")
        title.setObjectName("CommandEditTitle")
        title.setProperty("editorMode", editor_mode)
        title_row = QHBoxLayout()
        title_row.addWidget(title)

        self._command_icon = QPushButton()
        self._command_icon.setObjectName("CommandEditIcon")
        default_icon = QIcon(icon_manager.settings.paths.default_command_icon)
        self._command_icon.setIcon(default_icon)
        self._command_icon.setIconSize(QSize(42, 42))
        self._command_icon.setFixedSize(64, 64)
        self._command_icon.setCursor(Qt.CursorShape.PointingHandCursor)
        self._command_icon.clicked.connect(self.open_icon_browser)
        self._default_command_icon = default_icon

        self.command_name_edit_box = QLineEdit()
        self.command_name_edit_box.setObjectName("CommandNameEditBox")
        self.command_name_edit_box.setPlaceholderText("Command Name")
        self.validation_message = QLabel()
        self.validation_message.setObjectName("validationMessage")
        self.validation_message.hide()

        command_name_row = QHBoxLayout()
        command_name_row.addWidget(self._command_icon)
        command_name_row.addSpacing(12)
        command_name_row.addWidget(self.command_name_edit_box)

        command_name_column = QVBoxLayout()
        command_name_column.setContentsMargins(0, 0, 0, 0)
        command_name_column.setSpacing(2)
        command_name_column.addLayout(command_name_row)
        command_name_column.addWidget(self.validation_message)

        command_type_row_label = QLabel("Command type")
        command_type_row_label.setObjectName("fieldLabel")
        start_type = CommandType.from_command(self._command) if command else CommandType.APP
        self.command_type_selector = CommandTypeSelector(start_type)
        command_type_row = QVBoxLayout()
        command_type_row.setContentsMargins(0, 0, 0, 0)
        command_type_row.setSpacing(2)
        command_type_row.addWidget(command_type_row_label)
        command_type_row.addWidget(self.command_type_selector)

        self.delete_command_btn = QPushButton()
        self.delete_command_btn.setObjectName("DeleteCommandObject")
        self.delete_command_btn.setText("Delete command")
        self.delete_command_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.delete_command_btn.clicked.connect(self._delete_and_close)
        self.delete_command_btn.setVisible(command is not None)
        self.close_button = QPushButton("Close")
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.clicked.connect(self._cancel_and_close)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button.clicked.connect(self._cancel_and_close)
        self.save_button = QPushButton("Save")
        self.save_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self._save_and_close)
        bottom_row = QHBoxLayout()
        bottom_row.addWidget(self.delete_command_btn)
        bottom_row.addStretch(1)
        bottom_row.addWidget(self.close_button)
        bottom_row.addWidget(self.cancel_button)
        bottom_row.addWidget(self.save_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 24)
        layout.setSpacing(14)
        layout.addLayout(title_row)
        layout.addLayout(command_name_column)
        layout.addSpacing(6)
        layout.addLayout(command_type_row)

        self.command_action = CommandActionEditor(icon_manager.settings.paths.url_command_icon)
        self.command_type_selector.typeChanged.connect(self.command_action.set_mode)
        self.command_action.iconResolved.connect(self._apply_command_icon)
        self.command_action.set_mode(self.command_type_selector.selection)
        layout.addWidget(self.command_action)

        layout.addSpacing(8)
        alias_box_label = QLabel("Aliases")
        alias_box_label.setObjectName("fieldLabel")
        self.alias_box = AliasBox()
        self.alias_box.set_keyword_validator(self._validate_new_alias)
        self.alias_box.setMaximumHeight(250)

        alias_box_column = QVBoxLayout()
        alias_box_column.setContentsMargins(0, 0, 0, 0)
        alias_box_column.setSpacing(2)
        alias_box_column.addWidget(alias_box_label)
        alias_box_column.addWidget(self.alias_box)
        layout.addLayout(alias_box_column)

        layout.addStretch(1)
        layout.addLayout(bottom_row)

        self._populate(self._command)

        self.command_name_edit_box.textChanged.connect(self._update_dirty_state)
        self.command_type_selector.typeChanged.connect(self._update_dirty_state)
        self.command_action.command_action_edit_box.textChanged.connect(self._update_dirty_state)
        self.alias_box.aliasesChanged.connect(self._update_dirty_state)
        self._update_dirty_state()

        # Explicit tab order so focus follows the visual top-to-bottom flow.
        # Hidden widgets are skipped: Qt warns if they're in the chain.
        order = [self.command_name_edit_box]
        order += [self.command_type_selector.buttons[t] for t in CommandType]
        order += [
            self.command_action.command_action_edit_box,
            self.command_action.browse_button,
            self.command_action.open_button,
            self.alias_box.enter_box,
            self.delete_command_btn,
            self.close_button,
            self.cancel_button,
            self.save_button,
        ]
        order = [w for w in order if w.isVisibleTo(self)]
        for earlier, later in zip(order, order[1:]):
            self.setTabOrder(earlier, later)

    def _populate(self, command):
        if not command:
            return
        self.command_name_edit_box.setText(command.get("name", ""))
        self.command_action.command_action_edit_box.setText(command.get("location", ""))
        self.alias_box.set_aliases(command.get("aliases", []))
        icon_path = command.get("icon")
        is_url = CommandType.from_command(command) == CommandType.URL
        is_generated_favicon = icon_path and Path(icon_path).stem.startswith("auto_web_")
        if icon_path and Path(icon_path).exists() and not (is_url and is_generated_favicon):
            icon = QIcon(icon_path)
            self._resolved_icon = icon
            self._icon_path = icon_path
            self._command_icon.setIcon(icon)
        elif is_url:
            self._command_icon.setIcon(QIcon(self.icon_manager.settings.paths.url_command_icon))

    def _is_dirty(self) -> bool:
        if (
            not hasattr(self, "command_name_edit_box")
            or not hasattr(self, "command_type_selector")
            or not hasattr(self, "command_action")
            or not hasattr(self, "alias_box")
        ):
            return False
        current_name = self.command_name_edit_box.text().strip()
        current_type = self.command_type_selector.selection
        current_location = self.command_action.command_action_edit_box.text().strip()
        current_aliases = sorted([a.alias_text for a in self.alias_box.aliases])
        current_icon = self._icon_path

        return (
            current_name != self._initial_name
            or current_type != self._initial_type
            or current_location != self._initial_location
            or current_aliases != self._initial_aliases
            or current_icon != self._initial_icon
        )

    def _update_dirty_state(self):
        dirty = self._is_dirty()
        self.close_button.setVisible(not dirty)
        self.cancel_button.setVisible(dirty)
        self.save_button.setVisible(dirty)

    def _apply_command_icon(self, icon):
        # A null icon means the action editor had nothing to resolve
        if not icon.isNull():
            self._resolved_icon = icon
            self._icon_path = None
        self._command_icon.setIcon(icon if not icon.isNull() else self._default_command_icon)
        self._update_dirty_state()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._cancel_and_close()
        else:
            super().keyPressEvent(event)

    def _collect(self) -> dict | None:
        name = self.command_name_edit_box.text().strip()
        if not name:
            return None
        command_type = self.command_type_selector.selection
        return {
            "name": name,
            "aliases": [a.alias_text for a in self.alias_box.aliases],
            "location": self.command_action.command_action_edit_box.text().strip(),
            "description": self._command.get("description", ""),
            "icon": self._icon_path,
            "type": command_type.to_stored_type(),
            "command_type": command_type.name.lower(),
            "times_executed": self._command.get("times_executed", 0),
        }

    def _save_and_close(self):
        entry = self._collect()
        error = self.cmd_manager.validate_command(entry or {}, self._original_name)
        if error:
            self._show_validation_error(error)
            return
        if entry is not None:
            if entry["icon"] is None and not self._resolved_icon.isNull():
                entry["icon"] = self.icon_manager.save_command_icon(self._resolved_icon, entry["name"])
            self.cmd_manager.save_command(entry, original_name=self._original_name)
        self.closed.emit()

    def _validate_new_alias(self, alias: str) -> str | None:
        entry = self._collect() or {"name": ""}
        entry["aliases"] = [a.alias_text for a in self.alias_box.aliases]
        entry["aliases"].append(alias)
        error = self.cmd_manager.validate_command(entry, self._original_name, validate_target=False)
        if error == "Enter a command name.":
            return "Set the command name before adding aliases."
        return error

    def _show_validation_error(self, message):
        self.validation_message.setText(message)
        self.validation_message.show()
        if "already used" in message or "name" in message.lower():
            self.command_name_edit_box.setFocus()
        else:
            self.command_action.command_action_edit_box.setFocus()

    def _cancel_and_close(self):
        self.closed.emit()

    def _delete_and_close(self):
        self.icon_manager.delete_command_icon(self._command.get("icon"))
        if self._original_name:
            self.cmd_manager.delete_command(self._original_name)
        self.closed.emit()

    def open_icon_browser(self):
        self._icon_studio = IconStudio(initial_path=self._icon_path, initial_icon=self._command_icon.icon())
        self._icon_studio.setWindowModality(Qt.WindowModality.ApplicationModal)
        self._icon_studio.accepted.connect(self._accept_icon_studio)
        self._icon_studio.show()

    def _accept_icon_studio(self):
        name = self.command_name_edit_box.text().strip() or self._original_name or "command"
        icon_path = str(self.icon_manager.command_icon_path(name))
        if not self._icon_studio.save_png(icon_path):
            return
        self._icon_path = icon_path
        self._resolved_icon = QIcon(icon_path)
        self._command_icon.setIcon(self._resolved_icon)
        self._update_dirty_state()
