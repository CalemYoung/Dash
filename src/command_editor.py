import bisect
import logging
from enum import IntEnum
from pathlib import Path

from PyQt6.QtWidgets import (
    QComboBox,
    QMessageBox,
    QFileDialog,
    QFrame,
    QLineEdit,
    QPushButton,
    QLayout,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFileIconProvider,
    QCompleter,
)
from PyQt6.QtCore import Qt, QPoint, QRect, QSize, QUrl, QTimer, QFileInfo, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap, QDesktopServices, QShortcut
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply

from src.browsers import DEFAULT_BROWSER, fill_query, installed_browsers, is_search_link
from src.installed_programs import command_name_for_link, command_name_for_target, is_app_id_location, is_link_location
from src.windows_settings import is_settings_location, settings_description
from src.keys import format_shortcut, key_sequences
from src.icon_browser import IconStudio, glyph_pixmap, OutlineIcon, recipe_from_command, recipe_to_fields, render_recipe
from src import theme

log = logging.getLogger(__name__)


class CommandType(IntEnum):
    APP = 0
    FOLDER = 1
    URL = 2
    GROUP = 3

    @classmethod
    def from_command(cls, command: dict) -> "CommandType":
        """Best-effort mapping from a stored command dict to an editor mode."""
        location = command.get("location", "") or ""
        if command.get("type") == "group":
            return cls.GROUP
        if command.get("type") == "url" or location.startswith(("http://", "https://")):
            return cls.URL
        if is_link_location(location):
            return cls.APP  # a Store app, Settings page or shell link: no extension, but not a folder
        path = Path(location) if location else None
        if path is not None and (path.is_dir() or (path.suffix == "" and not path.is_file())):
            return cls.FOLDER
        return cls.APP

    def to_stored_type(self) -> str:
        return {CommandType.URL: "url", CommandType.GROUP: "group"}.get(self, "file")

    @property
    def label(self) -> str:
        """What the type selector shows. APP covers documents as well as
        programs, so it says so; the stored value does not change."""
        return {CommandType.APP: "App or file", CommandType.URL: "URL"}.get(self, self.name.title())


def _invalidate_layout_tree(layout):
    """Drop cached size data in `layout` and every layout nested under it,
    including the layouts of child widgets."""
    if layout is None:
        return
    layout.invalidate()
    for index in range(layout.count()):
        item = layout.itemAt(index)
        if item is None:
            continue
        if item.layout() is not None:
            _invalidate_layout_tree(item.layout())
        elif item.widget() is not None:
            _invalidate_layout_tree(item.widget().layout())


def _retain_space(widget):
    """Keep a hidden label's row in the layout so showing it later does not
    push or clip its neighbors inside a fixed-height editor."""
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
    """One alias chip: its text and a \u00d7 that removes it. The chip takes
    keyboard focus, and Delete or Backspace removes it."""

    def __init__(self, text, alias_box, noun="alias"):
        super().__init__()
        self.alias_text = text
        self.alias_box = alias_box
        self.setObjectName("Alias")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(f"{noun.capitalize()} {text}")
        self.setAccessibleDescription("Press Delete to remove it")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 6, 3)
        layout.setSpacing(8)
        self.text_label = QLabel(text)
        self.delete_label = QLabel("\u2715")
        self.delete_label.setObjectName("AliasDelete")
        self.delete_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.delete_label.setToolTip(f"Remove {noun} '{text}' (or select it and press Delete)")
        self.delete_label.setAccessibleName(f"Remove {noun} {text}")
        layout.addWidget(self.text_label)
        layout.addWidget(self.delete_label)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.alias_box.remove_alias(self, keep_focus=True)
            event.accept()
            return
        super().keyPressEvent(event)

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

    def __init__(self, noun="alias", placeholder="Add alias, press \u21b5", keep_order=False):
        super().__init__()
        self.aliases: list[Alias] = []
        self.keyword_validator = None
        # Turns accepted text into what is stored, e.g. a command's exact name.
        self.normalizer = None
        self.noun = noun
        # Aliases read alphabetically; a group's commands open in the order added.
        self.keep_order = keep_order

        self.grid_container = QFrame()
        self.grid_container.setObjectName("AliasGridContainer")
        self.grid_container.setAccessibleName(f"{noun.capitalize()} list")
        container_layout = QVBoxLayout(self.grid_container)
        container_layout.setContentsMargins(10, 10, 10, 10)
        container_layout.setSpacing(8)

        self.chip_layout = FlowLayout(spacing=8)
        container_layout.addLayout(self.chip_layout)

        self.enter_box = AliasEnterBox()
        self.enter_box.setObjectName("AliasEnterBox")
        self.enter_box.returnPressed.connect(self.on_enter_pressed)
        self.enter_box.textEdited.connect(lambda _text: self._clear_error())
        self.enter_box.setPlaceholderText(placeholder)
        self.enter_box.setMinimumWidth(100)
        self.enter_box.setAccessibleName(f"Add {'an' if noun[0] in 'aeiou' else 'a'} {noun}")
        self.enter_box.setAccessibleDescription(f"Type {'an' if noun[0] in 'aeiou' else 'a'} {noun} and press Enter")
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
            self._insert_chip(text)
        self._relayout_chips()
        self.aliasesChanged.emit()

    def remove_alias(self, alias: Alias, keep_focus: bool = False):
        """Drop a chip. With `keep_focus` (removed from the keyboard) focus
        moves to the next chip, or the entry box after the last one."""
        if alias not in self.aliases:
            return
        index = self.aliases.index(alias)
        self.aliases.remove(alias)
        if keep_focus:
            following = self.aliases[index] if index < len(self.aliases) else (self.aliases[index - 1] if index else None)
            (following or self.enter_box).setFocus()
        self.chip_layout.removeWidget(alias)
        alias.deleteLater()
        self._relayout_chips()
        self._clear_error()
        self.aliasesChanged.emit()

    def _insert_chip(self, text):
        chip = Alias(text, self, self.noun)
        if self.keep_order:
            self.aliases.append(chip)
        else:
            bisect.insort(self.aliases, chip)

    def on_enter_pressed(self):
        text = self.enter_box.text().strip()
        if text and self.normalizer is not None:
            text = self.normalizer(text)
        noun = self.noun
        if not text:
            self._show_error(f"Type {'an' if noun[0] in 'aeiou' else 'a'} {noun} first.")
        elif len(text) > self.MAX_ALIAS_LENGTH and self.normalizer is None:
            self._show_error(f"Aliases can be at most {self.MAX_ALIAS_LENGTH} characters.")
        elif len(self.aliases) >= self.MAX_ALIASES:
            self._show_error(f"A command can have at most {self.MAX_ALIASES} {noun}{'es' if noun.endswith('s') else 's'}.")
        elif text.casefold() in {alias.casefold() for alias in self.alias_texts()}:
            self._show_error(f"'{text}' is already in the list.")
        elif self.keyword_validator and (error := self.keyword_validator(text)):
            self._show_error(error)
        else:
            self._clear_error()
            self.enter_box.clear()
            self._insert_chip(text)
            self._relayout_chips()
            self.aliasesChanged.emit()

    def _relayout_chips(self):
        """Re-add chips in sorted order so the flow reads alphabetically."""
        while self.chip_layout.count():
            self.chip_layout.takeAt(0)
        for alias in self.aliases:
            self.chip_layout.addWidget(alias)
            alias.show()
        self.fix_tab_order()
        self.chip_layout.invalidate()
        self.grid_container.updateGeometry()
        self.updateGeometry()


    def fix_tab_order(self):
        """Tab reaches the chips in reading order, then the entry box.

        Chips are created after the rest of the editor, so Qt would put them
        at the end of the focus chain, behind Save. Chaining them after the
        entry box and then moving the entry box behind the last chip leaves
        them where the entry box was, in the order shown."""
        if not self.aliases:
            return
        previous = self.enter_box
        for alias in self.aliases:
            QFrame.setTabOrder(previous, alias)
            previous = alias
        QFrame.setTabOrder(previous, self.enter_box)


class CommandTypeSelector(QFrame):
    typeChanged = pyqtSignal(CommandType)

    def __init__(self, selection: CommandType):
        super().__init__()
        self.setObjectName("typeSelector")
        self.setAccessibleName("Command type")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self.buttons: dict[CommandType, QPushButton] = {}
        for type in CommandType:
            button = QPushButton(type.label)
            button.setObjectName("typeSegment")
            button.setCheckable(True)
            button.setAccessibleDescription("Command type")
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


def _folder_glyph() -> QIcon:
    """The browse buttons' folder, in the theme's text color."""
    return QIcon(glyph_pixmap(OutlineIcon.FOLDER, 20, theme.color("text")))


class CommandActionEditor(QFrame):
    # Status dot states, each colored by the theme token "status_<state>":
    # idle, checking, reachable (ok) and error (bad).
    STATUS_STATES = ("idle", "checking", "ok", "bad")

    # Resolved target icon; a null QIcon means "fall back to the default"
    iconResolved = pyqtSignal(QIcon)
    browserChanged = pyqtSignal()
    # Arguments or the start folder changed.
    launchOptionsChanged = pyqtSignal()
    # A row appeared or went away, so the editor may need another height.
    layoutChanged = pyqtSignal()

    # How long to keep looking for a favicon the background worker is fetching
    FAVICON_WAIT_MS = 30_000

    def __init__(self, url_icon_path, icon_manager=None):
        super().__init__()
        self._mode = CommandType.APP
        self._url_icon = QIcon(url_icon_path)
        self._icon_manager = icon_manager
        self._network = QNetworkAccessManager(self)
        self._reply = None
        self._icon_provider = QFileIconProvider()
        self._status_state = "idle"
        # (location, program) of a shortcut whose real program is known, so
        # its icon comes from the program while the location is unchanged.
        self._shortcut_program: tuple[str, str] | None = None

        self.label = QLabel("App or file to open")
        self.label.setObjectName("fieldLabel")

        self.command_action_edit_box = QLineEdit()
        self.command_action_edit_box.setPlaceholderText("Path to application or file")
        self.command_action_edit_box.textChanged.connect(self._on_text_changed)
        self.label.setBuddy(self.command_action_edit_box)

        self.browse_button = QPushButton()
        self.browse_button.setIcon(_folder_glyph())
        self.browse_button.setIconSize(QSize(20, 20))
        self.browse_button.clicked.connect(self.clicked)

        # A status dot (reachability for URLs, existence for paths) and, for
        # URLs, a way to open the link
        self.status_dot = QLabel()
        self.status_dot.setObjectName("urlStatus")
        self.status_dot.setFixedSize(12, 12)
        self.status_dot.setAccessibleName("Target status")
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
        self.browser_label.setBuddy(self.browser_combo)
        self.browser_combo.addItem("Use Global Setting", "")
        for browser in installed_browsers():
            self.browser_combo.addItem(browser.name, browser.key)
        self.browser_combo.currentIndexChanged.connect(lambda _index: self.browserChanged.emit())

        # Debounce reachability checks while the user is still typing
        self._check_timer = QTimer(self)
        self._check_timer.setSingleShot(True)
        self._check_timer.setInterval(500)
        self._check_timer.timeout.connect(self._verify_url)

        # Favicons are fetched by the icon manager's background worker; poll
        # the cache briefly so the icon fills in while the editor is open.
        self._favicon_timer = QTimer(self)
        self._favicon_timer.setInterval(500)
        self._favicon_timer.timeout.connect(self._poll_favicon)
        self._favicon_url = ""
        self._favicon_waited_ms = 0

        # Group mode: the commands it opens, instead of a location
        self.targets_box = AliasBox(noun="command", placeholder="Add a command by name, press \u21b5", keep_order=True)
        self.targets_box.setMaximumHeight(250)
        self.targets_box.grid_container.setAccessibleName("Commands this group opens, in order")

        # App or file mode: what to pass the program, and where it starts.
        # Both are optional; empty means "as Windows would start it".
        self.arguments_label = QLabel("Arguments")
        self.arguments_label.setObjectName("fieldLabel")
        self.arguments_edit = QLineEdit()
        self.arguments_edit.setObjectName("CommandArgumentsEdit")
        self.arguments_edit.setPlaceholderText("--new-window")
        self.arguments_edit.setAccessibleName("Arguments")
        self.arguments_edit.setAccessibleDescription("Optional. Passed to the program when it starts.")
        self.arguments_edit.setToolTip("Optional. Passed to the program when it starts.")
        self.arguments_edit.textChanged.connect(lambda _text: self.launchOptionsChanged.emit())
        self.arguments_label.setBuddy(self.arguments_edit)

        self.working_folder_label = QLabel("Start in")
        self.working_folder_label.setObjectName("fieldLabel")
        self.working_folder_edit = QLineEdit()
        self.working_folder_edit.setObjectName("CommandWorkingFolderEdit")
        self.working_folder_edit.setPlaceholderText("Default")
        self.working_folder_edit.setAccessibleName("Start in folder")
        self.working_folder_edit.setAccessibleDescription("Optional. The folder the program starts in; empty uses its own.")
        self.working_folder_edit.setToolTip("Optional. The folder the program starts in; empty uses its own.")
        self.working_folder_edit.textChanged.connect(lambda _text: self.launchOptionsChanged.emit())
        self.working_folder_label.setBuddy(self.working_folder_edit)
        self.working_folder_button = QPushButton()
        self.working_folder_button.setIcon(_folder_glyph())
        self.working_folder_button.setIconSize(QSize(20, 20))
        self.working_folder_button.setAccessibleName("Choose the folder to start in")
        self.working_folder_button.setToolTip("Choose the folder to start in")
        self.working_folder_button.clicked.connect(self._choose_working_folder)

        arguments_column = QVBoxLayout()
        arguments_column.setContentsMargins(0, 0, 0, 0)
        arguments_column.setSpacing(2)
        arguments_column.addWidget(self.arguments_label)
        # As tall as the start folder row with its button, so the two fields
        # line up side by side at any font size.
        arguments_row = QHBoxLayout()
        arguments_row.addWidget(self.arguments_edit)
        arguments_row.addStrut(max(self.working_folder_button.sizeHint().height(), self.arguments_edit.sizeHint().height()))
        arguments_column.addLayout(arguments_row)
        working_folder_row = QHBoxLayout()
        working_folder_row.setSpacing(8)
        working_folder_row.addWidget(self.working_folder_edit, 1)
        working_folder_row.addWidget(self.working_folder_button)
        working_folder_column = QVBoxLayout()
        working_folder_column.setContentsMargins(0, 0, 0, 0)
        working_folder_column.setSpacing(2)
        working_folder_column.addWidget(self.working_folder_label)
        working_folder_column.addLayout(working_folder_row)
        self.launch_options = QFrame()
        self.launch_options.setObjectName("launchOptions")
        launch_layout = QHBoxLayout(self.launch_options)
        launch_layout.setContentsMargins(0, 8, 0, 0)
        launch_layout.setSpacing(12)
        launch_layout.addLayout(arguments_column, 1)
        launch_layout.addLayout(working_folder_column, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.label)
        layout.addLayout(row)
        layout.addWidget(self.targets_box)
        layout.addWidget(self.launch_options)
        layout.addSpacing(6)
        layout.addWidget(self.browser_label)
        layout.addWidget(self.browser_combo)

        self.set_mode(CommandType.APP)
        theme.notifier().changed.connect(self._on_theme_changed)

    def _on_theme_changed(self, *_args):
        for button in (self.browse_button, self.working_folder_button):
            button.setIcon(_folder_glyph())
        self._apply_status_color()

    # ---------------------------------------------------------- launch options

    def launch_options_apply(self) -> bool:
        """Whether arguments and a start folder mean anything for the current
        target: a program or file, not a folder, website, group, Store app,
        Settings page or other Windows link."""
        if self._mode != CommandType.APP:
            return False
        return not is_link_location(self.command_action_edit_box.text().strip())

    def arguments(self) -> str:
        return self.arguments_edit.text().strip()

    def working_folder(self) -> str:
        return self.working_folder_edit.text().strip()

    def set_launch_options(self, arguments: str = "", working_folder: str = ""):
        self.arguments_edit.setText(str(arguments or ""))
        self.working_folder_edit.setText(str(working_folder or ""))

    def set_shortcut_program(self, location: str, program: str):
        """The program a shortcut runs, for showing its icon while the
        location stays the shortcut."""
        self._shortcut_program = (location.strip(), program.strip()) if location and program else None

    def _update_launch_options_visibility(self):
        visible = self.launch_options_apply()
        if visible != self.launch_options.isVisibleTo(self):
            self.launch_options.setVisible(visible)
            self.layoutChanged.emit()

    def _choose_working_folder(self):
        start = self.working_folder()
        if not start:
            target = self.command_action_edit_box.text().strip()
            start = str(Path(target).expanduser().parent) if target else ""
        path = QFileDialog.getExistingDirectory(self, "Choose the Folder to Start In", start)
        if path:
            self.working_folder_edit.setText(str(Path(path)))

    def targets(self) -> list[str]:
        return self.targets_box.alias_texts()

    def set_targets(self, targets):
        self.targets_box.set_aliases([str(target) for target in targets])

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
        is_group = command_type == CommandType.GROUP
        self.browse_button.setVisible(not is_url and not is_group)
        # The status light applies to every type but groups: reachability for
        # URLs, existence (and the right kind of thing) for files and folders.
        self.status_dot.setVisible(not is_group)
        self.command_action_edit_box.setVisible(not is_group)
        self.targets_box.setVisible(is_group)
        self.open_button.setVisible(is_url)
        self.browser_label.setVisible(is_url)
        self.browser_combo.setVisible(is_url)
        self.launch_options.setVisible(self.launch_options_apply())

        if is_group:
            self.label.setText("Commands to open, in order")
            self.label.setBuddy(self.targets_box.enter_box)
            self._check_timer.stop()
            self._favicon_timer.stop()
            self.iconResolved.emit(QIcon())
            return
        self.label.setBuddy(self.command_action_edit_box)
        if command_type == CommandType.APP:
            self.label.setText("App or file to open")
            self.command_action_edit_box.setPlaceholderText("Path to application or file")
            self.command_action_edit_box.setAccessibleName("App or file to open")
            self.browse_button.setAccessibleName("Browse for an app or file")
            self.browse_button.setToolTip("Browse for an app or file")
        elif command_type == CommandType.FOLDER:
            self.label.setText("Folder to open")
            self.command_action_edit_box.setPlaceholderText("Path to folder")
            self.command_action_edit_box.setAccessibleName("Folder to open")
            self.browse_button.setAccessibleName("Browse for a folder")
            self.browse_button.setToolTip("Browse for a folder")
        else:
            # {query} only adds searching: the address opens on its own when
            # nothing is typed after the command, so it reads as the extra it is.
            self.label.setText("Enter URL to open; add {query} to search the site too")
            self.command_action_edit_box.setPlaceholderText("https://example.com  or  https://example.com/search?q={query}")
            self.command_action_edit_box.setAccessibleName("Website address")

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

    def refresh_icon(self):
        """Resolve the target's own icon again and emit it.

        Used when the user drops a custom icon: the command goes back to
        whatever its target gives it.
        """
        if self._mode == CommandType.GROUP:
            self.iconResolved.emit(QIcon())
            return
        self._on_text_changed(self.command_action_edit_box.text())

    def _on_text_changed(self, text):
        text = text.strip()
        if self._mode == CommandType.GROUP:
            return  # a group has no location to check
        self._update_launch_options_visibility()
        if self._mode != CommandType.URL:
            self._check_timer.stop()
            self._favicon_timer.stop()
            self._resolve_file_icon(text)
            self._verify_path(text)
            return
        # A search link is checked with a sample search filled in.
        probe = self._probe_url(text)
        url = QUrl.fromUserInput(probe) if probe else QUrl()
        valid = bool(probe) and url.isValid() and url.scheme() in ("http", "https")
        self.open_button.setEnabled(valid)
        if not valid:
            self._check_timer.stop()
            self._favicon_timer.stop()
            self._set_status("idle", "Enter a full http(s) URL")
            self.iconResolved.emit(QIcon())
            return
        self._set_status("checking", "Checking...")
        self._resolve_favicon(probe)
        self._check_timer.start()

    @staticmethod
    def _probe_url(text: str) -> str:
        return fill_query(text, "dash") if is_search_link(text) else text

    def _resolve_favicon(self, url):
        """Emit the site's favicon if it is cached; otherwise start the
        download and keep checking until it lands or the wait runs out."""
        self._favicon_timer.stop()
        self._favicon_url = url
        self._favicon_waited_ms = 0
        if self._icon_manager is None:
            self.iconResolved.emit(QIcon())
            return
        cached = self._icon_manager._check_favicon_cache(url)
        if cached:
            self.iconResolved.emit(QIcon(cached))
            return
        self.iconResolved.emit(QIcon())
        self._icon_manager._queue_favicon_download(url)
        self._favicon_timer.start()

    def _poll_favicon(self):
        self._favicon_waited_ms += self._favicon_timer.interval()
        cached = self._icon_manager._check_favicon_cache(self._favicon_url)
        if cached:
            self._favicon_timer.stop()
            self.iconResolved.emit(QIcon(cached))
        elif self._favicon_waited_ms >= self.FAVICON_WAIT_MS:
            self._favicon_timer.stop()

    def _verify_path(self, text):
        """Light the status dot for a file or folder target, checked right now."""
        if not text:
            self._set_status("idle", "Enter a folder" if self._mode == CommandType.FOLDER else "Enter a file or application")
            return
        if is_link_location(text):
            if self._mode == CommandType.FOLDER:
                self._set_status("bad", "This is a Windows link. Switch the type to App or file to open it.")
            elif is_app_id_location(text):
                self._set_status("ok", "A Windows app, started by its app id")
            elif text.casefold().startswith("ms-settings:"):
                self._set_status("ok", "A Windows Settings page")
            else:
                self._set_status("ok", "A Windows link, opened by the shell")
            return
        path = Path(text).expanduser()
        if self._mode == CommandType.FOLDER:
            if path.is_dir():
                self._set_status("ok", "Folder found")
            elif path.is_file():
                self._set_status("bad", "This is a file. Switch the type to App or file to open it.")
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
        """Use the OS icon for the app/file/folder, or fall back to the default.
        A shortcut whose program is known shows the program's icon."""
        if self._shortcut_program and path == self._shortcut_program[0] and QFileInfo(self._shortcut_program[1]).isFile():
            path = self._shortcut_program[1]
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
        url = QUrl.fromUserInput(self._probe_url(self.command_action_edit_box.text().strip()))
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
        if state == "ok" and self._mode == CommandType.URL and is_search_link(self.command_action_edit_box.text()):
            tooltip += ". A search keyword: type its name, a space and what to search for."
        self._status_state = state if state in self.STATUS_STATES else "idle"
        self._apply_status_color()
        self.status_dot.setToolTip(tooltip)
        self.status_dot.setAccessibleDescription(tooltip)

    def _apply_status_color(self):
        # Read at paint time, so the dot follows a theme switched while open.
        color = theme.color_name(f"status_{self._status_state}")
        self.status_dot.setStyleSheet(f"background:{color}; border-radius:6px;")

    def _open_in_browser(self):
        text = self.command_action_edit_box.text().strip()
        if text:
            QDesktopServices.openUrl(QUrl.fromUserInput(fill_query(text, "")))


# Second-level labels that are part of the public suffix, not the site's
# own name: the "co" in bbc.co.uk, the "com" in example.com.au.
_GENERIC_SECOND_LEVELS = {"ac", "co", "com", "edu", "gov", "govt", "gouv", "ne", "net", "or", "org"}


def _site_host(location: str) -> str:
    """The host of a URL as a person would say it: no scheme, no www."""
    url = QUrl.fromUserInput(location.strip())
    if not url.isValid() or url.scheme() not in ("http", "https"):
        return ""
    return url.host().removeprefix("www.")


def _site_name(host: str) -> str:
    """The label a site is known by: github for github.com, google for
    mail.google.com, bbc for bbc.co.uk. An address stays an address."""
    labels = [label for label in host.split(".") if label]
    if not labels or all(label.isdigit() for label in labels):
        return host
    if len(labels) >= 3 and labels[-2] in _GENERIC_SECOND_LEVELS:
        return labels[-3]
    if len(labels) >= 2:
        return labels[-2]
    return labels[0]


def suggested_name(command_type: CommandType, location: str) -> str:
    """A name to offer for the target the user just picked or typed, or
    empty when there is nothing to go on yet."""
    location = location.strip()
    if not location:
        return ""
    if command_type == CommandType.URL:
        return _site_name(_site_host(location))
    if is_link_location(location):
        return command_name_for_link(location)
    return command_name_for_target(Path(location).expanduser()) or location


def suggested_description(command_type: CommandType, location: str, name: str) -> str:
    """A description to go with a suggested name, in the wording the
    import dialog uses for the same kinds of target."""
    if not name:
        return ""
    if command_type == CommandType.URL:
        verb = "Searches" if is_search_link(location) else "Opens"
        return f"{verb} {_site_host(location)}"
    if is_settings_location(location):
        return settings_description(name)
    if command_type == CommandType.FOLDER:
        return f"Opens the {name} folder"
    return f"Opens {name}"


# The icon a group gets when none is chosen: a stack, in Dash's colors.
# Baked into the saved icon, so it is the same in every theme.
GROUP_ICON_RECIPE = {"glyph": "outline:STACK_2", "source": None, "color": "#f3f4f7", "background": "#4a3f66"}


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
    # The panel needs a different height (type switched, alias rows changed).
    layoutChanged = pyqtSignal()

    def needed_height(self, width: int) -> int:
        """Height that shows every visible field at `width` without squeezing.

        Box layouts cache their height-for-width by width, and adding an
        alias chip or toggling a row does not always reach every nested
        layout, so a repeat query at the same width can return the old
        answer. Invalidate the whole tree first so the measurement is fresh.
        """
        layout = self.layout()
        _invalidate_layout_tree(layout)
        if layout.hasHeightForWidth():
            return layout.totalHeightForWidth(width)
        return self.sizeHint().height()

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
        # What made the icon (library glyph or source image, plus colors), if known.
        self._icon_recipe = recipe_from_command(self._command) if command else None

        self._initial_name = self._command.get("name", "") if command else ""
        self._initial_recipe = dict(self._icon_recipe) if self._icon_recipe else None
        self._initial_description = self._command.get("description", "") if command else ""
        self._initial_type = CommandType.from_command(self._command) if command else CommandType.APP
        self._initial_location = self._command.get("location", "") if command else ""
        self._initial_aliases = sorted(self._command.get("aliases", [])) if command else []
        self._initial_icon = self._command.get("icon") if command else None
        self._initial_browser = (self._command.get("browser") or None) if command else None
        self._initial_targets = [str(target) for target in self._command.get("targets", [])] if command else []
        self._initial_arguments = str(self._command.get("arguments") or "").strip() if command else ""
        self._initial_working_folder = str(self._command.get("working_folder") or "").strip() if command else ""
        # What the editor last offered for the name and description from the
        # target. A field still holding its offer follows the next target the
        # user picks; one the user has written in is theirs and is left alone.
        self._auto_name = ""
        self._auto_description = ""
        # Icon files to remove once the command is saved without them, so
        # cancelling after a reset leaves the old icon where it was.
        self._dropped_icon_files: set[str] = set()
        # Whether the icon still follows the target. False once the command
        # has an icon of its own, so a favicon that lands while the editor is
        # open cannot quietly replace it; picking a new target hands the icon
        # back to the target.
        self._icon_follows_target = not (self._icon_path or self._icon_recipe)

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
        self._command_icon.setAccessibleName("Command icon")
        self._command_icon.setAccessibleDescription("Opens the icon editor")
        self._command_icon.setToolTip("Change the icon")
        self._command_icon.clicked.connect(self.open_icon_browser)
        self._default_command_icon = default_icon

        self.command_name_edit_box = QLineEdit()
        self.command_name_edit_box.setObjectName("CommandNameEditBox")
        self.command_name_edit_box.setPlaceholderText("Command Name")
        self.command_name_edit_box.setAccessibleName("Command name")
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
        command_description_label.setBuddy(self.command_description_edit_box)
        command_description_column.addWidget(command_description_label)
        command_description_column.addWidget(self.command_description_edit_box)

        command_type_row_label = QLabel("Command type")
        command_type_row_label.setObjectName("fieldLabel")
        start_type = CommandType.from_command(self._command) if command else CommandType.APP
        self.command_type_selector = CommandTypeSelector(start_type)
        command_type_row_label.setBuddy(self.command_type_selector.buttons[start_type])
        command_type_row = QVBoxLayout()
        command_type_row.setContentsMargins(0, 0, 0, 0)
        command_type_row.setSpacing(2)
        command_type_row.addWidget(command_type_row_label)
        command_type_row.addWidget(self.command_type_selector)

        # The bottom-row buttons are parented straight away: their visibility
        # is set while the panel is still being built, and a parentless widget
        # made visible is a top-level window, which Windows flashes on screen.
        self.delete_command_btn = QPushButton(self)
        self.delete_command_btn.setObjectName("DeleteCommandObject")
        self.delete_command_btn.setText("Delete command")
        self.delete_command_btn.setAccessibleDescription("Asks before deleting")
        self.delete_command_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.delete_command_btn.clicked.connect(self._delete_and_close)
        # Deleting only makes sense for a command that is actually stored.
        self.delete_command_btn.setVisible(command is not None and not standalone)

        # Reset is likewise only for stored commands, and only while there is
        # something to reset. It takes effect immediately, like Delete: it is
        # not part of the Save/Cancel edit cycle.
        self.reset_count_btn = QPushButton(self)
        self.reset_count_btn.setObjectName("ResetRunCountButton")
        self.reset_count_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset_count_btn.clicked.connect(self._reset_run_count)
        self._refresh_reset_button()
        self.close_button = QPushButton("Close", self)
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.clicked.connect(self._cancel_and_close)
        self.cancel_button = QPushButton("Cancel", self)
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button.clicked.connect(self._cancel_and_close)
        self.save_button = QPushButton("Save", self)
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

        self.command_action = CommandActionEditor(icon_manager.settings.paths.url_command_icon, icon_manager)
        self.command_type_selector.typeChanged.connect(self.command_action.set_mode)
        # The URL type adds a row and alias chips wrap onto new lines, so the
        # window holding this panel must be able to re-fit its height.
        self.command_type_selector.typeChanged.connect(lambda _type: self.layoutChanged.emit())
        self.command_action.iconResolved.connect(self._apply_command_icon)
        self.command_action.layoutChanged.connect(self.layoutChanged)
        self.command_action.set_mode(self.command_type_selector.selection)
        targets_box = self.command_action.targets_box
        targets_box.set_keyword_validator(self._validate_new_target)
        targets_box.normalizer = self._target_name
        completer = QCompleter(self._target_choices(), targets_box.enter_box)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        targets_box.enter_box.setCompleter(completer)
        layout.addWidget(self.command_action)

        layout.addSpacing(8)
        alias_box_label = QLabel("Aliases")
        alias_box_label.setObjectName("fieldLabel")
        self.alias_box = AliasBox()
        alias_box_label.setBuddy(self.alias_box.enter_box)
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

        self.command_action.command_action_edit_box.textChanged.connect(self._prefill_from_target)
        self.command_type_selector.typeChanged.connect(self._prefill_from_target)
        # textEdited, not textChanged: only what the user types counts as
        # choosing a new target, never the panel filling the field in.
        self.command_action.command_action_edit_box.textEdited.connect(self._follow_target_icon)
        self.command_type_selector.typeChanged.connect(self._follow_target_icon)
        self.command_name_edit_box.textChanged.connect(self._update_dirty_state)
        self.command_description_edit_box.textChanged.connect(self._update_dirty_state)
        self.command_type_selector.typeChanged.connect(self._update_dirty_state)
        self.command_action.command_action_edit_box.textChanged.connect(self._update_dirty_state)
        self.command_action.browserChanged.connect(self._update_dirty_state)
        self.command_action.launchOptionsChanged.connect(self._update_dirty_state)
        self.command_action.layoutChanged.connect(self._update_dirty_state)
        self.command_type_selector.typeChanged.connect(self._update_description_hint)
        self.alias_box.aliasesChanged.connect(self._update_dirty_state)
        self.alias_box.aliasesChanged.connect(self.layoutChanged)
        self.command_action.targets_box.aliasesChanged.connect(self._update_dirty_state)
        self.command_action.targets_box.aliasesChanged.connect(self.layoutChanged)
        self._built = True
        self._update_dirty_state()
        self._update_description_hint()

        # Explicit tab order so focus follows the visual top-to-bottom flow.
        # Every control is chained, shown or not: Tab skips hidden ones, and
        # one left out would keep its creation-order place when the type
        # switch shows it. Alias chips slot in before their entry boxes.
        action = self.command_action
        order = [self._command_icon, self.command_name_edit_box, self.command_description_edit_box]
        order += [self.command_type_selector.buttons[t] for t in CommandType]
        order += [
            action.command_action_edit_box,
            action.browse_button,
            action.open_button,
            action.targets_box.enter_box,
            action.arguments_edit,
            action.working_folder_edit,
            action.working_folder_button,
            action.browser_combo,
            self.alias_box.enter_box,
            self.delete_command_btn,
            self.reset_count_btn,
            self.close_button,
            self.cancel_button,
            self.save_button,
        ]
        for earlier, later in zip(order, order[1:]):
            self.setTabOrder(earlier, later)
        action.targets_box.fix_tab_order()
        self.alias_box.fix_tab_order()

    def _populate(self, command):
        if not command:
            return
        self.command_name_edit_box.setText(command.get("name", ""))
        self.command_description_edit_box.setText(command.get("description", ""))
        # Before the location, whose icon it changes.
        self.command_action.set_shortcut_program(str(command.get("location", "") or ""), str(command.get("process_path", "") or ""))
        self.command_action.command_action_edit_box.setText(command.get("location", ""))
        self.command_action.set_browser(command.get("browser"))
        self.command_action.set_launch_options(command.get("arguments", ""), command.get("working_folder", ""))
        self.command_action.set_targets(command.get("targets", []))
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
        elif is_url and self._resolved_icon.isNull():
            self._command_icon.setIcon(QIcon(self.icon_manager.settings.paths.url_command_icon))

    def _prefill_from_target(self, *_):
        """Offer a name and description for the file, app, folder or site
        that was just picked or typed, unless the user has written their own."""
        command_type = self.command_type_selector.selection
        location = self.command_action.command_action_edit_box.text()
        name = suggested_name(command_type, location)
        if not name:
            return  # nothing to go on yet; whatever is there stays
        if self.command_name_edit_box.text().strip() in ("", self._auto_name):
            self._auto_name = name
            self.command_name_edit_box.setText(name)
            self._validate_name()  # a clash with an existing command shows at once
        description = suggested_description(command_type, location, name)
        if self.command_description_edit_box.text().strip() in ("", self._auto_description):
            self._auto_description = description
            self.command_description_edit_box.setText(description)

    def prefill(self, name: str | None = None, target: str | None = None) -> None:
        """Fill in a new command as though the person had typed it.

        `target` is a file, program, shortcut (.lnk), folder or web address,
        from typed text or a drop (a file:// URL is accepted too). The type
        follows it: a web address is a URL command, an existing folder a
        Folder command, anything else App or file. The name and description
        are then suggested from it, as when a target is picked by hand.

        `name`, when given, wins over the suggestion and is kept if the
        target changes later, since the person chose it.

        The fields change through their normal signals, so the editor is
        dirty afterwards (Save and Cancel show) and the name is checked for
        clashes at once. Focus is left where it is.
        """
        target = self._normalized_target(target)
        if target:
            command_type = self._type_for_target(target)
            if command_type != self.command_type_selector.selection:
                self.command_type_selector.select(command_type)
            self.command_action.command_action_edit_box.setText(target)
        name = str(name or "").strip()
        if name:
            self.command_name_edit_box.setText(name)
            self._validate_name()
        self._update_dirty_state()
        self.layoutChanged.emit()

    @staticmethod
    def _normalized_target(target: str | None) -> str:
        """A dropped or typed target as the location field wants it: no
        surrounding quotes or blanks, local files rather than file:// URLs,
        and https:// in front of a bare www. address."""
        text = str(target or "").strip().strip('"').strip()
        if not text:
            return ""
        if text.casefold().startswith("file:"):
            local = QUrl(text).toLocalFile()
            return str(Path(local)) if local else text
        if text.casefold().startswith("www."):
            return "https://" + text
        return text

    @staticmethod
    def _type_for_target(target: str) -> CommandType:
        if target.casefold().startswith(("http://", "https://")):
            return CommandType.URL
        if is_link_location(target):
            return CommandType.APP
        path = Path(target).expanduser()
        if path.is_dir():
            return CommandType.FOLDER
        return CommandType.APP

    def _update_description_hint(self, *_args):
        """A group with no description of its own shows what it opens, the
        summary the launcher uses in its place, as the field's hint."""
        hint = "Description"
        if self._original_name and self.command_type_selector.selection == CommandType.GROUP:
            hint = self.cmd_manager.group_summary(self._original_name) or hint
        self.command_description_edit_box.setPlaceholderText(hint)

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
            or (current_type == CommandType.GROUP and self.command_action.targets() != self._initial_targets)
            or self._launch_options_dirty()
        )

    def _launch_options_dirty(self) -> bool:
        """Arguments and start folder count only while they apply: fields
        hidden by a type or target that ignores them do not make a change."""
        action = self.command_action
        if not action.launch_options_apply():
            return False
        return action.arguments() != self._initial_arguments or action.working_folder() != self._initial_working_folder

    def _update_dirty_state(self):
        # Icon and text signals fire during construction, before every widget
        # is in place; the state is applied once building is done.
        if not getattr(self, "_built", False):
            return
        dirty = self._is_dirty()
        self.close_button.setVisible(not dirty)
        self.cancel_button.setVisible(dirty)
        self.save_button.setVisible(dirty)
        # The hint explains Cancel and Save, so it only makes sense beside them.
        if hasattr(self, "keyboard_hint"):
            self.keyboard_hint.setVisible(dirty)

    def _apply_command_icon(self, icon):
        """The target's own icon, resolved by the action editor.

        A null icon means there was nothing to resolve (yet): the type's
        placeholder is shown, and nothing is saved so the command keeps
        resolving its icon later, when a favicon may have arrived.

        An icon the user set wins: the site's favicon can land seconds after
        the editor opens, and it must not take over an icon they chose.
        """
        if not self._icon_follows_target:
            return
        self._resolved_icon = icon
        self._icon_path = None
        self._icon_recipe = None  # the icon now comes from the target, not the studio
        self._command_icon.setIcon(icon if not icon.isNull() else self._placeholder_icon())
        self._update_dirty_state()

    def _follow_target_icon(self, *_args):
        """Take the icon from the target again, and show its icon now."""
        if self._icon_follows_target:
            return
        self._icon_follows_target = True
        self.command_action.refresh_icon()

    def _placeholder_icon(self):
        if self.command_type_selector.selection == CommandType.URL:
            return QIcon(self.icon_manager.settings.paths.url_command_icon)
        if self.command_type_selector.selection == CommandType.GROUP:
            pixmap = render_recipe(GROUP_ICON_RECIPE, 128)
            if pixmap is not None and not pixmap.isNull():
                return QIcon(pixmap)
        return self._default_command_icon

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
        is_group = command_type == CommandType.GROUP
        return {
            "name": name,
            "aliases": [a.alias_text for a in self.alias_box.aliases],
            "location": "" if is_group else self.command_action.command_action_edit_box.text().strip(),
            "targets": self.command_action.targets() if is_group else [],
            "description": self.command_description_edit_box.text().strip(),
            "icon": self._icon_path,
            "type": command_type.to_stored_type(),
            "command_type": command_type.name.lower(),
            "browser": self.command_action.browser() if command_type == CommandType.URL else None,
            **recipe_to_fields(self._icon_recipe),
            **self._collect_launch_options(),
        }

    def _collect_launch_options(self) -> dict:
        """Arguments, start folder and the shortcut's program, for save_command.

        A key that is present replaces the stored value (empty clears it);
        a key left out keeps the stored value while the location is
        unchanged. So the fields are sent while they apply, and left out when
        hidden, which neither clears nor invents a value the person cannot see.
        The shortcut's program is never shown; it is kept while the location is.
        """
        action = self.command_action
        options = {}
        if action.launch_options_apply():
            options["arguments"] = action.arguments()
            options["working_folder"] = action.working_folder()
        location = action.command_action_edit_box.text().strip()
        process_path = str(self._command.get("process_path") or "").strip()
        if process_path and location == str(self._command.get("location") or "").strip():
            options["process_path"] = process_path
        return options

    def _save_and_close(self):
        # An alias typed but not yet confirmed with Enter is meant to be kept:
        # add it now, and if it is not valid, stay open with the reason shown.
        for chip_box in (self.alias_box, self.command_action.targets_box):
            if chip_box.isVisibleTo(self) and chip_box.enter_box.text().strip():
                chip_box.on_enter_pressed()
                if chip_box.enter_box.text().strip():
                    chip_box.enter_box.setFocus()
                    return
        entry = self._collect()
        error = self.cmd_manager.validate_command(entry or {}, self._original_name)
        if error:
            self._show_validation_error(error)
            return
        if entry is not None:
            if entry["icon"] is None and entry["type"] == "group" and not self._icon_recipe:
                # A group has no target to take an icon from: give it the stack.
                self._icon_recipe = dict(GROUP_ICON_RECIPE)
                entry.update(recipe_to_fields(self._icon_recipe))
            if entry["icon"] is None:
                if self._icon_recipe:
                    # Render the recipe at full size rather than saving the preview.
                    entry["icon"] = self.icon_manager.render_recipe_icon(entry)
                elif not self._resolved_icon.isNull():
                    entry["icon"] = self.icon_manager.save_command_icon(self._resolved_icon, entry["name"])
            for dropped in self._dropped_icon_files:
                if dropped not in (entry["icon"], entry.get("icon_source")):
                    self.icon_manager.delete_command_icon(dropped)
            if self._standalone:
                self.saved.emit(entry)
            else:
                try:
                    self.cmd_manager.save_command(entry, original_name=self._original_name)
                except (OSError, ValueError) as error:
                    # CommandsFileUnreadableError is an OSError: the file
                    # could not be read, so Dash will not write over it.
                    log.warning("Could not save command %r: %s", entry["name"], error)
                    self._show_problem(f"Could not save: {error}")
                    return
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

    def _target_choices(self) -> list[str]:
        own = self._original_name
        return sorted((name for name in self.cmd_manager.commands if name != own), key=str.casefold)

    def _target_name(self, text: str) -> str:
        command = self.cmd_manager.find_command(text)
        return command["name"] if command is not None else text

    def _validate_new_target(self, text: str) -> str | None:
        command = self.cmd_manager.find_command(text)
        if command is None:
            return f"'{text}' is not one of your commands."
        own_names = {self.command_name_edit_box.text().strip().casefold(), str(self._original_name or "").casefold()}
        if command["name"].casefold() in own_names:
            return "A group can't open itself."
        if command["name"].casefold() in {target.casefold() for target in self.command_action.targets()}:
            return f"'{command['name']}' is already in the list."
        return None

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

    def _show_problem(self, message):
        """Say what went wrong under the name, plainly, and stay open."""
        self.validation_message.setText(message)
        self.validation_message.show()
        self.layoutChanged.emit()

    def _show_validation_error(self, message):
        self.validation_message.setText(message)
        self.validation_message.show()
        if "working folder" in message and self.command_action.launch_options_apply():
            self.command_action.working_folder_edit.setFocus()
        elif "already used" in message or "name" in message.lower():
            self.command_name_edit_box.setFocus()
        elif self.command_type_selector.selection == CommandType.GROUP:
            self.command_action.targets_box.enter_box.setFocus()
        else:
            self.command_action.command_action_edit_box.setFocus()

    def _cancel_and_close(self):
        self.closed.emit()

    def _refresh_reset_button(self):
        count = int(self._command.get("times_executed", 0) or 0)
        self.reset_count_btn.setText(f"Opened {count} time{'s' if count != 1 else ''} \u00b7 Clear")
        self.reset_count_btn.setToolTip("Forget how often this command has been opened; it will sort as unused.")
        self.reset_count_btn.setVisible(bool(self._original_name) and not self._standalone and count > 0)

    def _reset_run_count(self):
        if not self._original_name:
            return
        self.cmd_manager.reset_run_count(self._original_name)
        self._command["times_executed"] = 0
        self._refresh_reset_button()

    def delete_question(self) -> tuple[str, str]:
        """The question Delete asks, and the groups it affects, if any."""
        name = self._original_name or self.command_name_edit_box.text().strip()
        groups = self.cmd_manager.groups_containing(name) if name else []
        detail = ""
        if len(groups) == 1:
            detail = f"The group {groups[0]} opens it, and will report it as missing."
        elif groups:
            detail = f"The groups {', '.join(groups)} open it, and will report it as missing."
        return f"Delete {name}?", detail

    def _confirm_delete(self) -> bool:
        question, detail = self.delete_question()
        box = QMessageBox(self)
        box.setWindowTitle("Delete Command")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(question)
        if detail:
            box.setInformativeText(detail)
        delete_button = box.addButton("Delete", QMessageBox.ButtonRole.DestructiveRole)
        cancel_button = box.addButton(QMessageBox.StandardButton.Cancel)
        # Enter must not delete: Cancel is the default, and Esc cancels too.
        box.setDefaultButton(cancel_button)
        box.setEscapeButton(cancel_button)
        self._delete_box = box
        try:
            box.exec()
            return box.clickedButton() is delete_button
        finally:
            self._delete_box = None

    def _delete_and_close(self):
        if not self._original_name or not self._confirm_delete():
            return
        try:
            self.cmd_manager.delete_command(self._original_name)
        except OSError as error:
            log.warning("Could not delete command %r: %s", self._original_name, error)
            self._show_problem(f"Could not delete: {error}")
            return
        # The icon goes only once the command is gone, so a failed delete
        # leaves the command with its icon.
        self.icon_manager.delete_command_icon(self._command.get("icon"))
        self.closed.emit()

    def open_icon_browser(self):
        self._icon_studio = IconStudio(
            initial_path=self._icon_path,
            initial_icon=self._command_icon.icon(),
            initial_recipe=self._icon_recipe,
            # Only worth offering when there is an icon of the user's to drop.
            allow_clear=bool(self._icon_path or self._icon_recipe),
        )
        self._icon_studio.setWindowModality(Qt.WindowModality.ApplicationModal)
        self._icon_studio.accepted.connect(self._accept_icon_studio)
        self._icon_studio.cleared.connect(self._clear_icon)
        self._icon_studio.show()

    def _clear_icon(self):
        """Drop the icon the user set and go back to the command's own one:
        the target's icon, a favicon for URLs, or the type's default."""
        # Only files in the icon store are ever removed; delete_command_icon
        # ignores anything else the icon may point at.
        for dropped in (self._icon_path, (self._icon_recipe or {}).get("source")):
            if dropped:
                self._dropped_icon_files.add(str(dropped))

        self._icon_path = None
        self._icon_recipe = None
        self._resolved_icon = QIcon()
        self._icon_follows_target = True
        self._command_icon.setIcon(self._placeholder_icon())
        # Resolving the target emits the icon back, which applies it here.
        self.command_action.refresh_icon()
        self._update_dirty_state()

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
        self._icon_follows_target = False
        self._command_icon.setIcon(self._resolved_icon)
        self._update_dirty_state()
