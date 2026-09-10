import bisect
from enum import IntEnum
from pathlib import Path

from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QLineEdit,
    QPushButton,
    QLayout,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFileIconProvider,
)
from PyQt6.QtCore import Qt, QPoint, QRect, QSize, QUrl, QTimer, QFileInfo, pyqtSignal
from PyQt6.QtGui import QIcon, QColor, QPixmap, QDesktopServices, QShortcut
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply

from src.browsers import DEFAULT_BROWSER, installed_browsers
from src.keys import format_shortcut, key_sequences
from src.icon_browser import IconStudio, glyph_pixmap, OutlineIcon, recipe_from_command, recipe_to_fields, render_recipe


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


def _retain_space(widget):
    """Keep a hidden label's row in the layout so showing it later does not
    push or clip its neighbours inside a fixed-height editor."""
    policy = widget.sizePolicy()
    policy.setRetainSizeWhenHidden(True)
    widget.setSizePolicy(policy)


class FlowLayout(QLayout):
    """Lays items out left to right, wrapping to a new line when the width
    runs out, and reports the height that needs. The standard Qt flow layout,
    used for alias chips so they wrap like words rather than being placed on
    a hand-computed grid."""

    def __init__(self, parent=None, spacing=8):
        super().__init__(parent)
        self._items = []
        self._gap = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, test_only=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _arrange(self, rect, test_only):
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y, line_height = area.x(), area.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            if x + hint.width() > area.right() + 1 and line_height > 0:
                x = area.x()
                y += line_height + self._gap
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._gap
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + margins.bottom()


class Alias(QFrame):
    """One alias chip: its text and a \u00d7 that removes it."""

    def __init__(self, text, alias_box):
        super().__init__()
        self.alias_text = text
        self.alias_box = alias_box
        self.setObjectName("Alias")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 6, 3)
        layout.setSpacing(8)
        self.text_label = QLabel(text)
        self.delete_label = QLabel("\u2715")
        self.delete_label.setObjectName("AliasDelete")
        self.delete_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.delete_label.setToolTip(f"Remove alias '{text}'")
        layout.addWidget(self.text_label)
        layout.addWidget(self.delete_label)

    def mousePressEvent(self, event):
        # Only the \u00d7 removes the alias; clicking the text should not lose it.
        if self.delete_label.geometry().contains(event.position().toPoint()):
            self.alias_box.remove_alias(self)
        else:
            super().mousePressEvent(event)

    def __lt__(self, other):
        return self.alias_text.casefold() < other.alias_text.casefold()


class AliasEnterBox(QLineEdit):
    """Line edit whose Enter key only adds an alias.

    A plain QLineEdit leaves Return unaccepted after emitting returnPressed,
    so inside a QDialog the key travels on and also clicks the default (Save)
    button, closing the editor the moment an alias is added.
    """

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.returnPressed.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class AliasBox(QFrame):
    """Alias chips that wrap like words, with the entry box on its own line
    beneath them. The box grows to fit its chips and never clips them."""

    aliasesChanged = pyqtSignal()
    MAX_ALIASES = 18
    MAX_ALIAS_LENGTH = 32

    def __init__(self):
        super().__init__()
        self.aliases: list[Alias] = []
        self.keyword_validator = None

        self.grid_container = QFrame()
        self.grid_container.setObjectName("AliasGridContainer")
        container_layout = QVBoxLayout(self.grid_container)
        container_layout.setContentsMargins(10, 10, 10, 10)
        container_layout.setSpacing(8)

        self.chip_layout = FlowLayout(spacing=8)
        container_layout.addLayout(self.chip_layout)

        self.enter_box = AliasEnterBox()
        self.enter_box.setObjectName("AliasEnterBox")
        self.enter_box.returnPressed.connect(self.on_enter_pressed)
        self.enter_box.textEdited.connect(lambda _text: self._clear_error())
        self.enter_box.setPlaceholderText("Add alias, press \u21b5")
        self.enter_box.setMinimumWidth(100)
        self.enter_box.setTextMargins(6, 0, 0, 0)  # line up with the chip text
        container_layout.addWidget(self.enter_box)

        self.error_label = QLabel()
        self.error_label.setObjectName("validationMessage")
        _retain_space(self.error_label)
        self.error_label.hide()

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(4)
        outer_layout.addWidget(self.grid_container)
        outer_layout.addWidget(self.error_label)

    def set_keyword_validator(self, validator):
        self.keyword_validator = validator

    def _show_error(self, message):
        self.error_label.setText(message)
        self.error_label.show()

    def _clear_error(self):
        self.error_label.hide()

    def alias_texts(self) -> list[str]:
        return [alias.alias_text for alias in self.aliases]

    def set_aliases(self, aliases):
        """Replace the current chips with the given alias strings."""
        for alias in list(self.aliases):
            self.chip_layout.removeWidget(alias)
            alias.deleteLater()
        self.aliases = []
        for text in aliases:
            bisect.insort(self.aliases, Alias(text, self))
        self._relayout_chips()
        self.aliasesChanged.emit()

    def remove_alias(self, alias: Alias):
        if alias not in self.aliases:
            return
        self.aliases.remove(alias)
        self.chip_layout.removeWidget(alias)
        alias.deleteLater()
        self._relayout_chips()
        self._clear_error()
        self.aliasesChanged.emit()

    def on_enter_pressed(self):
        text = self.enter_box.text().strip()
        if not text:
            self._show_error("Type an alias first.")
        elif len(text) > self.MAX_ALIAS_LENGTH:
            self._show_error(f"Aliases can be at most {self.MAX_ALIAS_LENGTH} characters.")
        elif len(self.aliases) >= self.MAX_ALIASES:
            self._show_error(f"A command can have at most {self.MAX_ALIASES} aliases.")
        elif text.casefold() in {alias.casefold() for alias in self.alias_texts()}:
            self._show_error(f"'{text}' is already one of this command's aliases.")
        elif self.keyword_validator and (error := self.keyword_validator(text)):
            self._show_error(error)
        else:
            self._clear_error()
            self.enter_box.clear()
            bisect.insort(self.aliases, Alias(text, self))
            self._relayout_chips()
            self.aliasesChanged.emit()

    def _relayout_chips(self):
        """Re-add chips in sorted order so the flow reads alphabetically."""
        while self.chip_layout.count():
            self.chip_layout.takeAt(0)
        for alias in self.aliases:
            self.chip_layout.addWidget(alias)
            alias.show()
        self.chip_layout.invalidate()
        self.grid_container.updateGeometry()
        self.updateGeometry()


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
    browserChanged = pyqtSignal()

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

        # URL mode: which browser opens it. Empty means "use the setting".
        self.browser_label = QLabel("Open with")
        self.browser_label.setObjectName("fieldLabel")
        self.browser_combo = QComboBox()
        self.browser_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.browser_combo.addItem("Use Global Setting", "")
        for browser in installed_browsers():
            self.browser_combo.addItem(browser.name, browser.key)
        self.browser_combo.currentIndexChanged.connect(lambda _index: self.browserChanged.emit())

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
        layout.addSpacing(6)
        layout.addWidget(self.browser_label)
        layout.addWidget(self.browser_combo)

        self.set_mode(CommandType.APP)

    def browser(self) -> str | None:
        """Chosen browser key for this command, or None to use the setting."""
        value = self.browser_combo.currentData()
        return str(value) if value else None

    def set_browser(self, key: str | None):
        key = str(key or "")
        index = self.browser_combo.findData(key)
        if index < 0 and key:
            # Keep a browser that is not installed on this machine rather than dropping it.
            self.browser_combo.addItem(f"{key} (not installed)", key)
            index = self.browser_combo.count() - 1
        self.browser_combo.setCurrentIndex(max(0, index))

    def set_mode(self, command_type: CommandType):
        self._mode = command_type
        is_url = command_type == CommandType.URL
        self.browse_button.setVisible(not is_url)
        # The status light applies to every type: reachability for URLs,
        # existence (and the right kind of thing) for files and folders.
        self.status_dot.setVisible(True)
        self.open_button.setVisible(is_url)
        self.browser_label.setVisible(is_url)
        self.browser_combo.setVisible(is_url)

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

    def _browse_start(self) -> str:
        """Where the file dialog should open: the current target if it exists,
        otherwise its nearest existing ancestor, otherwise wherever Qt defaults.

        For a file target the file path itself is returned so the dialog
        opens in its folder with that file preselected.
        """
        text = self.command_action_edit_box.text().strip()
        if not text:
            return ""
        path = Path(text).expanduser()
        if path.exists():
            if self._mode == CommandType.FOLDER and path.is_file():
                return str(path.parent)
            return str(path)
        for ancestor in path.parents:
            if ancestor.exists():
                return str(ancestor)
        return ""

    def clicked(self):
        start = self._browse_start()
        if self._mode == CommandType.FOLDER:
            path = QFileDialog.getExistingDirectory(self, "Select Folder", start)
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Select File", start)
        if path:
            self.command_action_edit_box.setText(path)

    def _on_text_changed(self, text):
        text = text.strip()
        if self._mode != CommandType.URL:
            self._check_timer.stop()
            self._resolve_file_icon(text)
            self._verify_path(text)
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

    def _verify_path(self, text):
        """Light the status dot for a file or folder target, checked right now."""
        if not text:
            self._set_status("idle", "Enter a folder" if self._mode == CommandType.FOLDER else "Enter a file or application")
            return
        path = Path(text).expanduser()
        if self._mode == CommandType.FOLDER:
            if path.is_dir():
                self._set_status("ok", "Folder found")
            elif path.is_file():
                self._set_status("bad", "This is a file. Switch the type to App to launch it.")
            else:
                self._set_status("bad", "Folder not found")
        else:
            if path.is_file():
                self._set_status("ok", "File found")
            elif path.is_dir():
                self._set_status("bad", "This is a folder. Switch the type to Folder to open it.")
            else:
                self._set_status("bad", "File not found")

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
            old_reply.metaDataChanged.disconnect()
            old_reply.abort()
            old_reply.deleteLater()
        # A GET that is abandoned as soon as the headers arrive. HEAD is
        # refused by many app sites, and the body is never needed: the
        # question is whether a server answers at all, and what it says.
        request = QNetworkRequest(url)
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
        )
        request.setRawHeader(b"User-Agent", self._BROWSER_USER_AGENT)
        request.setRawHeader(b"Accept", b"text/html,application/xhtml+xml,*/*;q=0.8")
        request.setTransferTimeout(8000)
        self._reply_classified = False
        self._reply = self._network.get(request)
        reply = self._reply
        if reply is not None:
            reply.metaDataChanged.connect(self._on_reply_headers)
            reply.finished.connect(self._on_reply_finished)

    _BROWSER_USER_AGENT = b"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"

    def _classify_status(self, status: int) -> tuple[str, str]:
        """What an HTTP status means for "will this open in a browser".

        A server that answers at all is reachable. Sign-in walls, bot
        protection and blocked automation all come back as 401/403 for a
        probe like this while opening fine for a person, so they are green
        with an explanation. Only "no such page" and server failures are red.
        """
        if status in (404, 410):
            return "bad", f"Page not found (HTTP {status})"
        if status >= 500:
            return "bad", f"The server reported an error (HTTP {status})"
        if status in (401, 403):
            return "ok", f"Reachable, but the site wants a signed-in browser (HTTP {status})"
        if status == 429:
            return "ok", "Reachable (the site is rate-limiting checks, HTTP 429)"
        return "ok", f"Reachable (HTTP {status})"

    def _on_reply_headers(self):
        reply = self._reply
        if reply is None or self._reply_classified:
            return
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        if status is None or 300 <= int(status) < 400:
            return  # a redirect Qt is about to follow; wait for the real answer
        self._reply_classified = True
        self._set_status(*self._classify_status(int(status)))
        reply.abort()  # headers were all that was needed

    def _on_reply_finished(self):
        reply = self._reply
        if reply is None:
            return
        self._reply = None
        if not self._reply_classified:
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            if status is not None:
                self._set_status(*self._classify_status(int(status)))
            elif reply.error() == QNetworkReply.NetworkError.NoError:
                self._set_status("ok", "Reachable")
            else:
                self._set_status("bad", f"Could not reach the site: {reply.errorString()}")
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
    """Full-panel editor for a single command.

    By default Save writes straight to commands.toml through the command
    manager. With ``standalone=True`` nothing is written: the validated
    command is emitted on ``saved`` instead, so the same editor can prepare a
    command that lives somewhere else (an import candidate, for instance).
    """

    # Emitted when the user leaves the editor (after any save/delete).
    closed = pyqtSignal()
    # Standalone mode only: the edited command, validated but not stored.
    saved = pyqtSignal(dict)

    def __init__(self, command, icon_manager, cmd_manager, parent=None, *, standalone=False, title=None):
        super().__init__(parent)
        self.setObjectName("CommandEditorPanel")
        editor_mode = "edit" if command else "new"
        self.setProperty("editorMode", editor_mode)
        self.icon_manager = icon_manager
        self.cmd_manager = cmd_manager
        self._standalone = standalone
        self._command = command or {}
        # A standalone command is not in commands.toml, so there is no stored
        # name to exempt from the keyword-conflict checks.
        self._original_name = self._command.get("name") if command and not standalone else None
        self._resolved_icon = QIcon()
        self._icon_path = self._command.get("icon")
        # What made the icon (library glyph or source image, plus colours), if known.
        self._icon_recipe = recipe_from_command(self._command) if command else None

        self._initial_name = self._command.get("name", "") if command else ""
        self._initial_recipe = dict(self._icon_recipe) if self._icon_recipe else None
        self._initial_description = self._command.get("description", "") if command else ""
        self._initial_type = CommandType.from_command(self._command) if command else CommandType.APP
        self._initial_location = self._command.get("location", "") if command else ""
        self._initial_aliases = sorted(self._command.get("aliases", [])) if command else []
        self._initial_icon = self._command.get("icon") if command else None
        self._initial_browser = (self._command.get("browser") or None) if command else None

        title = QLabel(title or ("Edit Command" if command else "New Command"))
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
        _retain_space(self.validation_message)
        self.validation_message.hide()
        # Say so as soon as the name is left, not when Save is eventually hit.
        self.command_name_edit_box.editingFinished.connect(self._validate_name)
        self.command_name_edit_box.textEdited.connect(lambda _text: self.validation_message.hide())

        command_name_row = QHBoxLayout()
        command_name_row.addWidget(self._command_icon)
        command_name_row.addSpacing(12)
        command_name_row.addWidget(self.command_name_edit_box)

        command_name_column = QVBoxLayout()
        command_name_column.setContentsMargins(0, 0, 0, 0)
        command_name_column.setSpacing(2)
        command_name_column.addLayout(command_name_row)
        command_name_column.addWidget(self.validation_message)

        self.command_description_edit_box = QLineEdit()
        self.command_description_edit_box.setObjectName("CommandDescriptionEditBox")
        self.command_description_edit_box.setPlaceholderText("Description")

        command_description_column = QVBoxLayout()
        command_description_column.setContentsMargins(0, 0, 0, 0)
        command_description_column.setSpacing(2)
        command_description_label = QLabel("Description")
        command_description_label.setObjectName("fieldLabel")
        command_description_column.addWidget(command_description_label)
        command_description_column.addWidget(self.command_description_edit_box)

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
        # Deleting only makes sense for a command that is actually stored.
        self.delete_command_btn.setVisible(command is not None and not standalone)

        # Reset is likewise only for stored commands, and only while there is
        # something to reset. It takes effect immediately, like Delete: it is
        # not part of the Save/Cancel edit cycle.
        self.reset_count_btn = QPushButton()
        self.reset_count_btn.setObjectName("ResetRunCountButton")
        self.reset_count_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset_count_btn.clicked.connect(self._reset_run_count)
        self._refresh_reset_button()
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
        bottom_row.addWidget(self.reset_count_btn)
        bottom_row.addStretch(1)
        bottom_row.addWidget(self.close_button)
        bottom_row.addWidget(self.cancel_button)
        bottom_row.addWidget(self.save_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 24)
        layout.setSpacing(14)
        layout.addLayout(title_row)
        layout.addLayout(command_name_column)
        layout.addLayout(command_description_column)
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

        # Keyboard: Esc leaves without saving; the same key that opens the
        # editor saves it. Both are spelled out, since that key is configurable.
        save_key = icon_manager.settings.shortcuts.edit_selected_command
        self.save_shortcut = QShortcut(self)
        self.save_shortcut.setKeys(key_sequences(save_key))
        self.save_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.save_shortcut.activated.connect(self._save_and_close)
        self.keyboard_hint = QLabel(f"Esc discards  ·  {format_shortcut(save_key)} saves")
        self.keyboard_hint.setObjectName("footerHint")
        _retain_space(self.keyboard_hint)
        self.keyboard_hint.hide()
        layout.addWidget(self.keyboard_hint, 0, Qt.AlignmentFlag.AlignRight)

        self._populate(self._command)

        self.command_name_edit_box.textChanged.connect(self._update_dirty_state)
        self.command_description_edit_box.textChanged.connect(self._update_dirty_state)
        self.command_type_selector.typeChanged.connect(self._update_dirty_state)
        self.command_action.command_action_edit_box.textChanged.connect(self._update_dirty_state)
        self.command_action.browserChanged.connect(self._update_dirty_state)
        self.alias_box.aliasesChanged.connect(self._update_dirty_state)
        self._update_dirty_state()

        # Explicit tab order so focus follows the visual top-to-bottom flow.
        # Hidden widgets are skipped: Qt warns if they're in the chain.
        order = [self.command_name_edit_box, self.command_description_edit_box]
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
        self.command_description_edit_box.setText(command.get("description", ""))
        self.command_action.command_action_edit_box.setText(command.get("location", ""))
        self.command_action.set_browser(command.get("browser"))
        self.alias_box.set_aliases(command.get("aliases", []))
        # Setting the location above resolved the target's own icon, which
        # clears the stored icon path and recipe; put the stored ones back.
        icon_path = command.get("icon")
        recipe = recipe_from_command(command)
        is_url = CommandType.from_command(command) == CommandType.URL
        is_generated_favicon = icon_path and Path(icon_path).stem.startswith("auto_web_")
        if icon_path and Path(icon_path).exists() and not (is_url and is_generated_favicon):
            icon = QIcon(icon_path)
            self._resolved_icon = icon
            self._icon_path = icon_path
            self._icon_recipe = recipe
            self._command_icon.setIcon(icon)
        elif recipe is not None:
            # No rendered file yet (an import candidate, say): preview the recipe.
            pixmap = render_recipe(recipe)
            if pixmap is not None and not pixmap.isNull():
                self._resolved_icon = QIcon(pixmap)
                self._command_icon.setIcon(self._resolved_icon)
            self._icon_recipe = recipe
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
        current_description = self.command_description_edit_box.text().strip()
        current_type = self.command_type_selector.selection
        current_location = self.command_action.command_action_edit_box.text().strip()
        current_aliases = sorted([a.alias_text for a in self.alias_box.aliases])
        current_icon = self._icon_path

        return (
            current_name != self._initial_name
            or current_description != self._initial_description
            or current_type != self._initial_type
            or current_location != self._initial_location
            or current_aliases != self._initial_aliases
            or current_icon != self._initial_icon
            or self._icon_recipe != self._initial_recipe
            or self.command_action.browser() != self._initial_browser
        )

    def _update_dirty_state(self):
        dirty = self._is_dirty()
        self.close_button.setVisible(not dirty)
        self.cancel_button.setVisible(dirty)
        self.save_button.setVisible(dirty)
        # The hint explains Cancel and Save, so it only makes sense beside them.
        if hasattr(self, "keyboard_hint"):
            self.keyboard_hint.setVisible(dirty)

    def _apply_command_icon(self, icon):
        # A null icon means the action editor had nothing to resolve
        if not icon.isNull():
            self._resolved_icon = icon
            self._icon_path = None
            self._icon_recipe = None  # the icon now comes from the target, not the studio
        self._command_icon.setIcon(icon if not icon.isNull() else self._default_command_icon)
        self._update_dirty_state()

    def keyPressEvent(self, event):
        # Esc closes without saving, as the hint beneath the buttons says.
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
            "description": self.command_description_edit_box.text().strip(),
            "icon": self._icon_path,
            "type": command_type.to_stored_type(),
            "command_type": command_type.name.lower(),
            "browser": self.command_action.browser() if command_type == CommandType.URL else None,
            **recipe_to_fields(self._icon_recipe),
        }

    def _save_and_close(self):
        entry = self._collect()
        error = self.cmd_manager.validate_command(entry or {}, self._original_name)
        if error:
            self._show_validation_error(error)
            return
        if entry is not None:
            if entry["icon"] is None:
                if self._icon_recipe:
                    # Render the recipe at full size rather than saving the preview.
                    entry["icon"] = self.icon_manager.render_recipe_icon(entry)
                elif not self._resolved_icon.isNull():
                    entry["icon"] = self.icon_manager.save_command_icon(self._resolved_icon, entry["name"])
            if self._standalone:
                self.saved.emit(entry)
            else:
                self.cmd_manager.save_command(entry, original_name=self._original_name)
        self.closed.emit()

    def _validate_new_alias(self, alias: str) -> str | None:
        """Check only the alias being added, against the name, the existing
        aliases and other commands. Problems with the name or with aliases
        that are already there are reported elsewhere, not blamed on whatever
        is typed next."""
        name = self.command_name_edit_box.text().strip()
        if not name:
            return "Set the command name before adding aliases."
        if alias.casefold() == name.casefold():
            return "That is already the command's name."
        # Validated as if it were a name: the same keyword rules apply, and this
        # leaves the (possibly conflicting) real name out of the check.
        return self.cmd_manager.validate_command({"name": alias, "aliases": []}, self._original_name, validate_target=False)

    def _validate_name(self):
        """Name check on focus-out: empty is left to the placeholder, a clash
        with another command or with this command's own aliases is shown at once."""
        name = self.command_name_edit_box.text().strip()
        error = None
        if name:
            entry = {"name": name, "aliases": self.alias_box.alias_texts()}
            error = self.cmd_manager.validate_command(entry, self._original_name, validate_target=False)
        self.validation_message.setText(error or "")
        self.validation_message.setVisible(bool(error))

    def _show_validation_error(self, message):
        self.validation_message.setText(message)
        self.validation_message.show()
        if "already used" in message or "name" in message.lower():
            self.command_name_edit_box.setFocus()
        else:
            self.command_action.command_action_edit_box.setFocus()

    def _cancel_and_close(self):
        self.closed.emit()

    def _refresh_reset_button(self):
        count = int(self._command.get("times_executed", 0) or 0)
        self.reset_count_btn.setText(f"Reset run count ({count})")
        self.reset_count_btn.setToolTip(f"Run {count} time{'s' if count != 1 else ''}. Set the count back to zero.")
        self.reset_count_btn.setVisible(bool(self._original_name) and not self._standalone and count > 0)

    def _reset_run_count(self):
        if not self._original_name:
            return
        self.cmd_manager.reset_run_count(self._original_name)
        self._command["times_executed"] = 0
        self._refresh_reset_button()

    def _delete_and_close(self):
        self.icon_manager.delete_command_icon(self._command.get("icon"))
        if self._original_name:
            self.cmd_manager.delete_command(self._original_name)
        self.closed.emit()

    def open_icon_browser(self):
        self._icon_studio = IconStudio(
            initial_path=self._icon_path,
            initial_icon=self._command_icon.icon(),
            initial_recipe=self._icon_recipe,
        )
        self._icon_studio.setWindowModality(Qt.WindowModality.ApplicationModal)
        self._icon_studio.accepted.connect(self._accept_icon_studio)
        self._icon_studio.show()

    def _accept_icon_studio(self):
        name = self.command_name_edit_box.text().strip() or self._original_name or "command"
        icon_path = str(self.icon_manager.command_icon_path(name))
        if not self._icon_studio.save_png(icon_path):
            return

        # Keep the recipe alongside the rendered PNG. Disk artwork has no
        # name to refer back to, so its untinted source is stored too.
        recipe = self._icon_studio.recipe()
        if not recipe["glyph"]:
            base = self._icon_studio.base_pixmap()
            source_path = self.icon_manager.command_source_icon_path(name)
            if base is not None and not base.isNull() and base.save(str(source_path), "PNG"):
                recipe["source"] = str(source_path)
            else:
                recipe = None
        self._icon_recipe = recipe

        self._icon_path = icon_path
        self._resolved_icon = QIcon(icon_path)
        self._command_icon.setIcon(self._resolved_icon)
        self._update_dirty_state()
