from PyQt6.QtCore import QDate, QEasingCurve, QEvent, QLocale, QPointF, QRectF, QSize, Qt, QThread, QTimer, QPropertyAnimation, QUrl, pyqtSignal
from PyQt6.QtWidgets import QMainWindow, QLineEdit, QVBoxLayout, QHBoxLayout, QWidget, QStackedWidget, QPushButton
from PyQt6.QtWidgets import QListWidget, QMessageBox, QSystemTrayIcon, QMenu, QApplication, QErrorMessage, QLabel, QListWidgetItem
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QFileDialog, QProgressDialog, QToolButton
from PyQt6.QtGui import QBrush, QContextMenuEvent, QFont, QFontMetrics, QIcon, QAction, QDesktopServices, QMouseEvent, QPainter, QPen, QPixmap, QCursor, QScreen, QKeySequence, QShortcut, QColor
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from .settings import Settings
from .settings_editor import ExportCommandsDialog, ImportCommandsDialog, ManageCommandsDialog, ProgramImportDialog, SettingsEditorPanel
from .settings_editor import delete_confirmation_text
from .window_placement import available_geometry_for, clamp_size_to_screen, move_within_screen, pin_within_screen
from .icon_manager import IconManager
from .command_manager import CommandsFileUnreadableError
from .command_trie import TrieSnapshot
from typing import cast
from contextlib import contextmanager
from . import app_log, browsers, calculator, theme
from .popular_websites import POPULAR_WEBSITES
from .browsers import fill_query, is_search_link
from .browsers import open_url as open_in_browser
from .icon_browser import glyph_pixmap, OutlineIcon
from .installed_programs import is_link_location
from .version import current_version, is_newer_version
from .keys import format_shortcut, key_sequences
from .widgets import ElidedLabel
from .updater import ReleaseInfo, UpdateDownloader, is_installed_build, launch_installer, parse_release
import logging
import os
import re
import sys
import time
from pathlib import Path
import win32gui
import win32con
import pyperclip

import win32process
import win32api

log = logging.getLogger(__name__)

LATEST_RELEASE_API = "https://api.github.com/repos/CalemYoung/Dash/releases/latest"
LATEST_RELEASE_PAGE = "https://github.com/CalemYoung/Dash/releases/latest"

# Windows may not have the notification area ready when Dash starts at login.
TRAY_SETUP_RETRIES = 20
TRAY_SETUP_RETRY_MS = 3000

# Vertical padding of #ResultsList in style.qss; the list height is sized to
# whole rows so the last visible row is never cut through its text.
RESULTS_LIST_PADDING_V = 4

# Showing the launcher takes a few activation round trips on Windows (Qt's,
# then _force_focus's), and the window can briefly read as inactive between
# them. Focus loss inside this window after opening is not a click-away.
FOCUS_GRACE_MS = 800
# How long after a dialog closes a deactivation is still put down to it.
DIALOG_GRACE_MS = 400
# The "something went wrong" notice is shown at most this often.
ERROR_NOTICE_INTERVAL_S = 30

UNEXPECTED_ERROR_TEXT = "Something went wrong in Dash. Details were saved to the log."
SAVE_FAILED_TEXT = "Dash couldn't save your commands. Check that the config folder isn't read-only and that the drive has free space."

# Top-level domains that make a bare "example.com" read as a website rather
# than a file name, for prefilling a new command from the search text.
_WEB_TLDS = {"com", "org", "net", "io", "dev", "app", "co", "uk", "de", "fr", "nl", "edu", "gov", "ai", "me", "tv", "info", "us", "ca", "au"}
_WINDOWS_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|~[\\/]|%[^%]+%)")
_BARE_DOMAIN = re.compile(r"^(?:[\w-]+\.)+([A-Za-z]{2,24})(?:[/?#]\S*)?$")


def target_from_text(text: str) -> str | None:
    """The file, folder or web address that typed or dropped text names, or
    None when it reads as a name. "www.x.com" and "github.com" gain https://."""
    text = str(text or "").strip().strip('"')
    if not text or "\n" in text:
        return None
    if text.lower().startswith(("http://", "https://")):
        return text
    if " " not in text:
        if text.lower().startswith("www."):
            return "https://" + text
        match = _BARE_DOMAIN.match(text)
        if match and match.group(1).lower() in _WEB_TLDS:
            return "https://" + text
    if _WINDOWS_PATH.match(text) or is_link_location(text):
        return text
    return None


class ProgramDiscoveryThread(QThread):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.candidates: list[dict] = []
        self.error_message = ""

    def run(self):
        pythoncom_module = None
        try:
            if sys.platform == "win32":
                import pythoncom

                pythoncom_module = pythoncom
                pythoncom_module.CoInitialize()

            from .installed_programs import (
                discover_packaged_apps,
                discover_recent_program_commands,
                discover_windows_suggestions,
                merge_program_candidates,
            )
            from .personal_places import discover_bookmark_bar, discover_quick_access_folders
            from .popular_websites import discover_popular_websites
            from .windows_settings import discover_settings_pages

            self.candidates = discover_recent_program_commands(days=365)
            self.candidates += discover_packaged_apps()
            # The places the user already keeps. Each is best effort and
            # returns nothing rather than failing the scan.
            self.candidates += discover_quick_access_folders()
            self.candidates += discover_bookmark_bar()
            self.candidates += discover_settings_pages()
            # Last, so a site the user has bookmarked is offered as theirs
            # rather than twice.
            self.candidates += discover_popular_websites(self.candidates)
            # Windows' own folders and tools lead: they are the handful of
            # entries most people want, and would be lost partway down a
            # list of installed programs. Resolved here, off the UI thread,
            # since it reads the registry and known folders.
            self.candidates = merge_program_candidates(discover_windows_suggestions(), self.candidates)
        except Exception as error:
            log.exception("Looking for programs failed")
            self.error_message = str(error) or type(error).__name__
        finally:
            if pythoncom_module is not None:
                pythoncom_module.CoUninitialize()


_key_sequences = key_sequences


def _text_style(color_value: str, point_size: int | None = None) -> str:
    declarations = []
    if point_size is not None:
        declarations.append(f"font-size: {point_size}pt")
    color = QColor(str(color_value))
    if color.isValid():
        declarations.append(f"color: {color.name(QColor.NameFormat.HexRgb)}")
    return "; ".join(declarations) + ";"


class SearchTreeWidget(QWidget):
    def __init__(self, scale: float, parent=None):
        super().__init__(parent)
        self.setObjectName("SearchTree")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._snapshot: TrieSnapshot | None = None
        self._scale = scale
        self.set_scale(scale)

        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity_effect)
        self._flash_animation = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._flash_animation.setDuration(150)
        self._flash_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def set_scale(self, scale: float):
        self._scale = scale
        self.update()

    def branch_capacity(self) -> int:
        scale = self._scale
        padding = max(12, round(14 * scale))
        row_step = max(20, round(22 * scale))
        branch_top = round(55 * scale) + row_step * 2
        return max(0, (self.height() - padding - branch_top) // row_step + 1)

    def set_snapshot(self, snapshot: TrieSnapshot):
        was_hidden = self.isHidden()
        self._snapshot = snapshot
        self.show()
        self.update()

        self._flash_animation.stop()
        self._flash_animation.setStartValue(0.58 if was_hidden else 0.82)
        self._flash_animation.setEndValue(1.0)
        self._flash_animation.start()

    def clear_snapshot(self):
        self._flash_animation.stop()
        self._opacity_effect.setOpacity(1.0)
        self._snapshot = None
        self.hide()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._snapshot is None:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        scale = self._scale
        width = self.width()
        padding = max(12, round(14 * scale))
        # Read on every paint, so a theme switch shows on the next repaint.
        accent = theme.color("tree_accent")
        text = theme.color("tree_text")
        muted = theme.color("tree_muted")
        line = theme.color("tree_line")
        error = theme.color("tree_error")
        terminal = theme.color("tree_ok")  # green: what was typed is exactly a command

        header_font = painter.font()
        header_font.setPixelSize(max(9, round(10 * scale)))
        header_font.setBold(True)
        painter.setFont(header_font)
        painter.setPen(muted)
        painter.drawText(
            QRectF(padding, round(8 * scale), width - padding * 2, round(18 * scale)),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "COMMAND TREE",
        )

        body_font = painter.font()
        body_font.setPixelSize(max(9, round(10 * scale)))
        body_font.setBold(False)
        value_font = painter.font()
        value_font.setPixelSize(max(9, round(10 * scale)))
        value_font.setBold(True)
        detail_font = painter.font()
        detail_font.setPixelSize(max(8, round(9 * scale)))
        detail_font.setBold(False)

        snapshot = self._snapshot
        row_step = max(20, round(22 * scale))
        root_y = round(55 * scale)
        active_y = root_y + row_step
        root_x = round(20 * scale)
        active_x = root_x + round(18 * scale)
        branch_x = active_x + round(18 * scale)
        node_radius = max(3, round(4 * scale))
        leaf_radius = max(2, round(3 * scale))
        count_width = round(28 * scale)
        label_right = width - padding - count_width

        branch_top = active_y + row_step
        branch_capacity = self.branch_capacity()
        show_more = snapshot.branch_count > branch_capacity
        visible_branch_count = min(snapshot.branch_count, max(0, branch_capacity - int(show_more)))
        visible_branches = snapshot.branches[:visible_branch_count]
        branch_row_count = len(visible_branches) + int(show_more and branch_capacity > 0)

        connector_pen = QPen(line, max(1.0, scale))
        connector_pen.setCapStyle(Qt.PenCapStyle.SquareCap)
        painter.setPen(connector_pen)
        painter.drawLine(QPointF(root_x, root_y + node_radius), QPointF(root_x, active_y))
        painter.drawLine(QPointF(root_x, active_y), QPointF(active_x - node_radius, active_y))

        if branch_row_count:
            last_branch_y = branch_top + (branch_row_count - 1) * row_step
            painter.drawLine(QPointF(active_x, active_y + node_radius), QPointF(active_x, last_branch_y))
            for index in range(branch_row_count):
                branch_y = branch_top + index * row_step
                painter.drawLine(QPointF(active_x, branch_y), QPointF(branch_x - leaf_radius, branch_y))

        painter.setPen(QPen(theme.color("tree_node_border"), max(1.0, 1.2 * scale)))
        painter.setBrush(QBrush(theme.color("tree_node_fill")))
        painter.drawEllipse(QPointF(root_x, root_y), node_radius, node_radius)
        painter.setFont(body_font)
        painter.setPen(muted)
        painter.drawText(
            QRectF(root_x + round(11 * scale), root_y - round(8 * scale), label_right - root_x, round(16 * scale)),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "ALL COMMANDS",
        )
        painter.setFont(value_font)
        painter.drawText(
            QRectF(label_right, root_y - round(8 * scale), count_width, round(16 * scale)),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            str(snapshot.total_command_count),
        )

        if snapshot.command_count == 0:
            active_color = error
            active_fill = theme.color("tree_error_fill")
        elif snapshot.is_terminal:
            active_color = terminal
            active_fill = theme.color("tree_ok_fill")
        else:
            active_color = accent
            active_fill = theme.color("tree_accent_fill")

        painter.setPen(QPen(active_color, max(1.0, 1.2 * scale)))
        painter.setBrush(QBrush(active_fill))
        painter.drawEllipse(QPointF(active_x, active_y), node_radius, node_radius)
        painter.setFont(value_font)
        painter.setPen(active_color)
        active_label = painter.fontMetrics().elidedText(
            snapshot.prefix.upper(),
            Qt.TextElideMode.ElideRight,
            max(0, int(label_right - active_x - round(11 * scale))),
        )
        painter.drawText(
            QRectF(active_x + round(11 * scale), active_y - round(8 * scale), label_right - active_x, round(16 * scale)),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            active_label,
        )
        painter.drawText(
            QRectF(label_right, active_y - round(8 * scale), count_width, round(16 * scale)),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            str(snapshot.command_count),
        )

        for index, branch in enumerate(visible_branches):
            branch_y = branch_top + index * row_step
            painter.setPen(QPen(accent, max(1.0, scale)))
            painter.setBrush(QBrush(theme.color("tree_accent_fill")))
            painter.drawEllipse(QPointF(branch_x, branch_y), leaf_radius, leaf_radius)
            painter.setFont(body_font)
            painter.setPen(text)
            branch_label = painter.fontMetrics().elidedText(
                branch.prefix.upper(),
                Qt.TextElideMode.ElideRight,
                max(0, int(label_right - branch_x - round(10 * scale))),
            )
            painter.drawText(
                QRectF(branch_x + round(10 * scale), branch_y - round(8 * scale), label_right - branch_x, round(16 * scale)),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                branch_label,
            )
            painter.setFont(value_font)
            painter.setPen(accent)
            painter.drawText(
                QRectF(label_right, branch_y - round(8 * scale), count_width, round(16 * scale)),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                str(branch.command_count),
            )

        if show_more and branch_capacity > 0:
            summary_y = branch_top + len(visible_branches) * row_step
            painter.setPen(QPen(muted, max(1.0, scale)))
            painter.setBrush(QBrush(theme.color("tree_node_fill")))
            painter.drawEllipse(QPointF(branch_x, summary_y), leaf_radius, leaf_radius)
            painter.setFont(detail_font)
            painter.setPen(muted)
            painter.drawText(
                QRectF(branch_x + round(10 * scale), summary_y - round(8 * scale), width - branch_x - padding, round(16 * scale)),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                "...",
            )

        painter.end()


class MainWindow(QMainWindow):
    # Carries an unexpected error's summary to the GUI thread: app_log may
    # report it from a worker thread's exception hook.
    _errorReported = pyqtSignal(str)

    def __init__(self, cmd_manager, settings: Settings, settings_path: Path | None = None):
        super().__init__()
        self.cmd_manager = cmd_manager
        self.settings = settings
        self._loaded_settings_path = settings_path
        self.user_text = ""
        self.current_suggestion = ""
        self.is_deleting = False
        self._editor_panel = None
        self._hotkey_listener = None
        self.icon_manager = IconManager(settings)
        try:
            self.cmd_manager.reprocess_command_icons(self.icon_manager)
        except OSError:
            # Icons are redone next time; a commands file that can't be
            # written is reported through load_warnings or the next save.
            log.warning("Could not store command icons at startup", exc_info=True)
        self.tray = None
        self._tray_retry_count = 0
        self._update_network = QNetworkAccessManager(self)
        self._update_reply = None
        self._update_check_manual = False
        self._latest_release: ReleaseInfo | None = None
        self._check_updates_action = None
        self._download_update_action = None
        self._update_downloader: UpdateDownloader | None = None
        self._update_progress = None
        self._program_discovery_thread = None
        self._program_discovery_progress = None
        # Hide on focus loss: dialogs Dash has open (or is opening), and a
        # short window after showing or after a dialog closes, keep it open.
        self._dialog_depth = 0
        self._focus_grace_until = 0.0
        self._tray_menu_open = False
        self._error_notice: QMessageBox | None = None
        self._last_error_notice = float("-inf")
        self._pending_tray_messages: list[tuple[str, str]] = []
        self._missing_screens_logged: set[str] = set()
        self._tips_checked = False
        self._row_menu_from_key = False
        self._decimal_separator = calculator.locale_decimal_separator()

        self._setup_window()
        self._setup_widgets()
        self._setup_layout()
        self._setup_shortcuts()
        self.hide_launcher()

        # Setup tray icon (with retry logic for Windows startup)
        self._setup_tray()
        # One check per process start; the result is surfaced once, as a tray
        # notification, and never re-asked during the session.
        if self.settings.general.check_updates_on_startup:
            QTimer.singleShot(2500, self.check_for_updates)

        self._errorReported.connect(self._show_error_notice, Qt.ConnectionType.QueuedConnection)
        app_log.set_error_reporter(self._errorReported.emit)
        # The notifier, not on_theme_changed: a signal lets go of the window
        # when it is destroyed, where a stored callback would keep it alive.
        theme.notifier().changed.connect(self._on_theme_changed)
        app = QApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self._on_application_state_changed)
        # Problems met while loading settings or commands, once the event
        # loop is running so the notice has a window to belong to.
        QTimer.singleShot(0, self.show_load_warnings)

    @staticmethod
    def _format_shortcut(shortcut):
        return format_shortcut(shortcut)

    def _shortcut_hint_text(self):
        return "  |  ".join(
            (
                f"New: {self._format_shortcut(self.settings.shortcuts.new_command)}",
                f"Edit: {self._format_shortcut(self.settings.shortcuts.edit_selected_command)}",
                f"Settings: {self._format_shortcut(self.settings.shortcuts.open_settings)}",
            )
        )

    def _keys_help_lines(self) -> list[str]:
        """Every launcher key, for the footer's Keys tooltip and the About box."""
        shortcuts = self.settings.shortcuts
        return [
            f"{self._format_shortcut(self.settings.general.hotkey)}  Open Dash",
            "Enter  Open the selected command",
            "Tab  Complete the name, or start a site search",
            f"{self._format_shortcut(shortcuts.edit_selected_command)}  Edit the selected command",
            f"{self._format_shortcut(shortcuts.new_command)}  New command",
            f"{self._format_shortcut(shortcuts.open_settings)}  Open Settings",
            "Shift+Enter  Open a new copy",
            "Ctrl+Shift+Enter  Run as administrator",
            "Alt+Enter  Open the containing folder",
            "Ctrl+C  Copy the path or address",
            "Shift+F10 or the Menu key  More actions",
            "Esc  Close Dash",
        ]

    def _setup_window(self):
        self.setWindowTitle("Dash")
        self.setWindowIcon(QIcon(self.settings.paths.default_command_icon))
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowOpacity(self.settings.ui.window_opacity)
        self.setAccessibleName("Dash")
        # Files, folders, shortcuts and web addresses dropped on the launcher
        # become new commands.
        self.setAcceptDrops(True)

    def _setup_widgets(self):
        # 1: Create main widget
        self.central_widget = QWidget()
        self.central_widget.setObjectName("MainWidget")
        self._layout_scale = max(0.8, min(1.4, self.settings.ui.program_width / 500))
        self._layout_margin = max(10, round(10 * self._layout_scale))
        self._layout_spacing = max(10, round(10 * self._layout_scale))

        # 2.1: Create overall search box container widget -> contains text input + multiline info widgets
        self.search_container_widget = QWidget()
        self.search_container_widget.setFixedSize(QSize(self.settings.ui.program_width, self._search_height()))

        # 2.2: Create search box input widget for user searching
        self.search_input_widget = QLineEdit()
        self.search_input_widget.setObjectName("SearchInput")
        self.search_input_widget.setPlaceholderText("Type an app, file, website or sum...")
        self.search_input_widget.setAccessibleName("Search commands")
        self.search_input_widget.setAccessibleDescription("Type to find a command. Up and Down choose a result, Enter opens it.")
        # Drops go to the window, which turns them into new commands, rather
        # than pasting a path into the box.
        self.search_input_widget.setAcceptDrops(False)
        self._apply_search_text_style()
        self.search_input_widget.installEventFilter(self)
        self.search_input_widget.textChanged.connect(self.on_text_change)
        self.search_input_widget.returnPressed.connect(self.on_enter_pressed)

        # 2.3: Create search box info widget for date and time
        self.date_info_widget = QWidget()
        self.date_info_widget.setObjectName("SearchMeta")
        self.date_info_widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.date_info_widget.setFixedWidth(max(88, round(88 * self._layout_scale)))
        self.date_info_day_label = QLabel()
        self.date_info_day_label.setObjectName("SearchMetaDay")
        self.date_info_date_label = QLabel()
        self.date_info_date_label.setObjectName("SearchMetaDate")
        self.date_info_day_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.date_info_date_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.date_info_widget.setAccessibleName("Date")
        self._apply_clock_text_style()

        # 3: Create results widget
        self._results_list_height = self.settings.ui.results_height
        self.results_list_widget = QListWidget()
        self.results_list_widget.setObjectName("ResultsList")
        self.results_list_widget.setAccessibleName("Results")
        self.results_list_widget.setAccessibleDescription("Commands matching the search. Right-click or press the Menu key for more actions.")
        self.results_list_widget.setFixedSize(QSize(self.settings.ui.program_width, self._results_list_height))
        self.results_list_widget.currentItemChanged.connect(self._on_current_result_changed)
        # Arrow keys and the wheel still scroll; the bar itself only cluttered the inset cards.
        self.results_list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results_list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results_list_widget.hide()
        self.search_tree_widget = SearchTreeWidget(self._layout_scale)
        self.search_tree_widget.setFixedSize(
            self._search_tree_width(),
            self._search_height() + self._results_list_height,
        )
        self.search_tree_widget.hide()
        self.shortcut_hint_label = QLabel(self._shortcut_hint_text())
        self.shortcut_hint_label.setObjectName("ShortcutHint")
        self.shortcut_hint_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.shortcut_hint_label.setAccessibleName("Keyboard shortcuts")
        # The rest of the keys do not fit in the footer: they are a tooltip
        # here and are listed in the About box.
        self.keys_hint_label = QLabel("Keys")
        self.keys_hint_label.setObjectName("ShortcutHint")
        self.keys_hint_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.keys_hint_label.setToolTip("\n".join(self._keys_help_lines()))
        self.keys_hint_label.setAccessibleName("More keys")
        self.keys_hint_label.setAccessibleDescription("; ".join(self._keys_help_lines()))
        # First-run tips, shown once in place of the shortcut hint.
        self.tip_label = QLabel()
        self.tip_label.setObjectName("ShortcutHint")
        self.tip_label.setWordWrap(True)
        self.tip_label.setAccessibleName("Tip")
        self.tip_label.hide()
        self.tip_dismiss_button = QToolButton()
        self.tip_dismiss_button.setObjectName("ResultEditButton")
        self.tip_dismiss_button.setText("\u00d7")
        self.tip_dismiss_button.setToolTip("Dismiss tip")
        self.tip_dismiss_button.setAccessibleName("Dismiss tip")
        self.tip_dismiss_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.tip_dismiss_button.clicked.connect(self.dismiss_tips)
        self.tip_dismiss_button.hide()
        self.footer_height = self._base_footer_height()
        self._search_view_size = QSize(
            self.settings.ui.program_width,
            self._search_height() + self.footer_height,
        )

    def _setup_layout(self):
        date_info_layout = QVBoxLayout()
        date_info_layout.setContentsMargins(0, 0, 0, 0)
        date_info_layout.setSpacing(0)
        date_info_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        date_info_layout.addWidget(self.date_info_day_label)
        date_info_layout.addWidget(self.date_info_date_label)
        self.date_info_widget.setLayout(date_info_layout)
        self.date_info_widget.setVisible(self.settings.ui.show_clock)

        search_container_layout = QHBoxLayout()
        search_container_layout.addWidget(self.search_input_widget, 1)
        search_container_layout.addWidget(self.date_info_widget)
        search_container_layout.setContentsMargins(self._layout_margin, 0, self._layout_margin, 0)
        search_container_layout.setSpacing(self._layout_spacing)
        self.search_container_widget.setLayout(search_container_layout)
        self.search_container_widget.setObjectName("SearchContainer")

        search_results_layout = QVBoxLayout()
        search_results_layout.setContentsMargins(0, 0, 0, 0)
        search_results_layout.setSpacing(0)
        search_results_layout.addWidget(self.search_container_widget)
        search_results_layout.addWidget(self.results_list_widget)

        launcher_body_layout = QHBoxLayout()
        launcher_body_layout.setContentsMargins(0, 0, 0, 0)
        launcher_body_layout.setSpacing(0)
        launcher_body_layout.addLayout(search_results_layout)
        launcher_body_layout.addWidget(self.search_tree_widget)

        footer_layout = QHBoxLayout()
        footer_layout.setContentsMargins(self._layout_margin, 0, self._layout_margin, 0)
        footer_layout.setSpacing(self._layout_spacing)
        footer_layout.addWidget(self.shortcut_hint_label, 1)
        footer_layout.addWidget(self.tip_label, 1)
        footer_layout.addWidget(self.tip_dismiss_button)
        footer_layout.addWidget(self.keys_hint_label)
        self.footer_widget = QWidget()
        self.footer_widget.setObjectName("LauncherFooter")
        self.footer_widget.setAccessibleName("Footer")
        self.footer_widget.setFixedHeight(self.footer_height)
        self.footer_widget.setLayout(footer_layout)

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        main_layout.addLayout(launcher_body_layout)
        main_layout.addWidget(self.footer_widget)

        self.central_widget.setLayout(main_layout)
        self.central_widget.setFixedSize(self._search_view_size)

        # A stack lets us swap the search view for the full-panel command editor.
        self.view_stack = QStackedWidget()
        self.view_stack.addWidget(self.central_widget)
        self.setCentralWidget(self.view_stack)

    def _on_current_result_changed(self, current, previous):
        """Mirror the list selection onto the row widgets so style.qss can
        restyle their children (icon tile, counter pill) for the selected row."""
        for item, selected in ((previous, False), (current, True)):
            if item is None:
                continue
            row = self.results_list_widget.itemWidget(item)
            if isinstance(row, ResultRow):
                row.set_selected(selected)

    def _search_tree_width(self):
        return max(240, min(300, round(self.settings.ui.program_width * 0.56)))

    @staticmethod
    def _font_height(point_size: int, bold: bool = False) -> int:
        font = QFont(QApplication.font())
        font.setPointSize(max(1, int(point_size)))
        font.setBold(bold)
        return QFontMetrics(font).height()

    def _search_height(self) -> int:
        """The configured search bar height, raised when the search font
        needs more (a line edit adds about 18px of frame and margin)."""
        return max(int(self.settings.ui.search_height), self._font_height(self.settings.ui.search_font_size) + 26)

    def _row_height(self) -> int:
        """Height of one result row: the icon tile, or the name above the
        description in the configured fonts, whichever is taller. At the
        default fonts that is the tile, 48px."""
        ui = self.settings.ui
        text = self._font_height(ui.result_font_size, bold=True) + 2 + self._font_height(ui.description_font_size)
        return max(ResultRow.ICON_TILE_SIZE, text + 2) + 6

    def _base_footer_height(self) -> int:
        return max(24, round(24 * self._layout_scale), self._font_height(9) + 8)

    def _add_row(self, row: "ResultRow", accessible_text: str = "", accessible_description: str = "") -> QListWidgetItem:
        """Put a row widget in the results list with an item as tall as the
        fonts need, and give the item the text screen readers announce: the
        row itself is a widget, which list accessibility does not look into."""
        item = QListWidgetItem(self.results_list_widget)
        hint = row.sizeHint()
        row_height = getattr(self, "_row_height", None)
        height = max(hint.height(), row_height()) if callable(row_height) else hint.height()
        item.setSizeHint(QSize(hint.width(), height))
        title = row.command_label.full_text()
        description = row.description_label.full_text()
        item.setData(Qt.ItemDataRole.AccessibleTextRole, accessible_text or (f"{title}, {description}" if description else title))
        item.setData(Qt.ItemDataRole.AccessibleDescriptionRole, accessible_description or description)
        self.results_list_widget.setItemWidget(item, row)
        return item

    def _sync_search_view_size(self):
        tree_width = 0 if self.search_tree_widget.isHidden() else self.search_tree_widget.width()
        results_height = 0 if self.results_list_widget.isHidden() else self._results_list_height
        self._search_view_size = QSize(
            self.settings.ui.program_width + tree_width,
            self._search_height() + results_height + self.footer_height,
        )
        self.central_widget.setFixedSize(self._search_view_size)
        if hasattr(self, "view_stack") and self.view_stack.currentWidget() is self.central_widget:
            self.setFixedSize(self._search_view_size)

    def _update_search_tree(self, prefix: str):
        tree_visible = self.settings.search.show_command_tree and bool(prefix)
        if tree_visible:
            self.search_tree_widget.set_snapshot(
                self.cmd_manager.lookup_trie.snapshot(
                    prefix,
                    max_branches=self.search_tree_widget.branch_capacity(),
                )
            )
        else:
            self.search_tree_widget.clear_snapshot()
        for widget in (self.search_container_widget, self.results_list_widget):
            widget.setProperty("treeVisible", tree_visible)
            style = widget.style()
            if style is not None:
                style.unpolish(widget)
                style.polish(widget)
        self._sync_search_view_size()

    def _setup_shortcuts(self):
        self.edit_shortcut = QShortcut(self.central_widget)
        self.edit_shortcut.setKeys(_key_sequences(self.settings.shortcuts.edit_selected_command))
        self.edit_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.edit_shortcut.activated.connect(self.open_selected_command_editor)

        self.new_command_shortcut = QShortcut(self.central_widget)
        self.new_command_shortcut.setKeys(_key_sequences(self.settings.shortcuts.new_command))
        self.new_command_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.new_command_shortcut.activated.connect(self.open_new_command)

        self.settings_shortcut = QShortcut(self.central_widget)
        self.settings_shortcut.setKeys(_key_sequences(self.settings.shortcuts.open_settings))
        self.settings_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.settings_shortcut.activated.connect(self.open_settings_from_search)

    def _apply_search_text_style(self):
        self.search_input_widget.setStyleSheet(
            _text_style(self.settings.ui.search_text_color, self.settings.ui.search_font_size)
        )

    @staticmethod
    def _month_day_format(locale: QLocale) -> str:
        """"d MMMM" or "MMMM d", in the order the locale's long date puts
        the day and the month ("24 September" or "September 24")."""
        pattern = re.sub(r"'[^']*'", "", locale.dateFormat(QLocale.FormatType.LongFormat))
        day = re.search(r"(?<!d)d{1,2}(?!d)", pattern)
        month = pattern.find("M")
        if day is None or month < 0:
            return "d MMMM"
        return "d MMMM" if day.start() < month else "MMMM d"

    def _apply_clock_text_style(self):
        day_size = self.settings.ui.clock_font_size
        date_size = max(6, day_size - 1)
        self.date_info_day_label.setStyleSheet(_text_style(self.settings.ui.clock_day_text_color, day_size))
        self.date_info_date_label.setStyleSheet(_text_style(self.settings.ui.clock_date_text_color, date_size))
        self.date_info_day_label.setFixedHeight(self.date_info_day_label.fontMetrics().height() + 2)
        self.date_info_date_label.setFixedHeight(self.date_info_date_label.fontMetrics().height() + 2)
        # Wide enough for the longest day and month names in this language.
        locale = QLocale()
        date_format = self._month_day_format(locale)
        day_names = [locale.dayName(day, QLocale.FormatType.LongFormat) for day in range(1, 8)]
        dates = [locale.toString(QDate(2024, month, 28), date_format) for month in range(1, 13)]
        day_metrics = self.date_info_day_label.fontMetrics()
        date_metrics = self.date_info_date_label.fontMetrics()
        text_width = max(
            max(day_metrics.horizontalAdvance(name) for name in day_names),
            max(date_metrics.horizontalAdvance(text) for text in dates),
        )
        self.date_info_widget.setFixedWidth(max(round(88 * self._layout_scale), text_width + 12))

    def _user_selected_text(self) -> bool:
        """Whether the box holds a selection the user made, rather than the
        blue completion Dash selects after what was typed."""
        box = self.search_input_widget
        if not box.hasSelectedText():
            return False
        is_completion = (
            bool(self.current_suggestion)
            and box.selectionStart() == len(self.user_text)
            and box.selectionStart() + len(box.selectedText()) == len(box.text())
        )
        return not is_completion

    def eventFilter(self, obj, event):
        """Catch key presses on the input box"""
        if obj == self.search_input_widget and event.type() == QEvent.Type.ContextMenu:
            # The Menu key may also arrive as a keyboard context menu event;
            # it is the selected row's menu, never the box's edit menu. The
            # mouse still gets the edit menu.
            if event.reason() == QContextMenuEvent.Reason.Keyboard and self.get_selected_row() is not None:
                if self._row_menu_from_key:
                    self._row_menu_from_key = False
                else:
                    self.show_selected_row_menu()
                return True
        if obj == self.search_input_widget and event.type() == event.Type.KeyPress:
            key = event.key()
            self._row_menu_from_key = False
            modifiers = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
            # Tab completes in the box; it never moves focus out of it.
            if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
                if key == Qt.Key.Key_Tab:
                    self.accept_suggestion()
                return True
            # Enter with a modifier: the launch options of the selected row.
            # Plain Enter (and the edit shortcut, which is handled before
            # the key gets here) keep their usual meaning.
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                ctrl_shift = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
                if modifiers == ctrl_shift:
                    self.run_selected_as_administrator()
                    return True
                if modifiers == Qt.KeyboardModifier.ShiftModifier:
                    self.open_selected_new_copy()
                    return True
                if modifiers == Qt.KeyboardModifier.AltModifier:
                    self.open_selected_containing_folder()
                    return True
            # Ctrl+C copies text the user selected; with none, the selected
            # command's path or address.
            if event.matches(QKeySequence.StandardKey.Copy) and not self._user_selected_text():
                if self.copy_selected_path():
                    return True
            # Pasting a file copied in Explorer makes it a command, the
            # keyboard twin of dropping it (a drag from Explorer usually
            # hides Dash, since starting one clicks away from it).
            if event.matches(QKeySequence.StandardKey.Paste) and self._paste_copied_file():
                return True
            if key == Qt.Key.Key_Menu or (key == Qt.Key.Key_F10 and modifiers == Qt.KeyboardModifier.ShiftModifier):
                if self.get_selected_row() is not None:
                    # A context menu event for the same key press may follow.
                    self._row_menu_from_key = True
                    self.show_selected_row_menu()
                    return True
            # Check if user is deleting
            if key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
                self.is_deleting = True
            else:
                self.is_deleting = False
        return super().eventFilter(obj, event)

    def hide_launcher(self):
        if self._editor_panel is not None:
            self.close_editor()
        self.hide()
        self.search_input_widget.clear()
        self.user_text = ""
        if not self.tip_label.isHidden():
            self.dismiss_tips()

    def activate_launcher(self):
        was_visible = self.isVisible()
        self._begin_focus_grace(FOCUS_GRACE_MS)
        locale = QLocale()
        today = QDate.currentDate()
        self.date_info_day_label.setText(locale.toString(today, "dddd"))
        self.date_info_date_label.setText(locale.toString(today, self._month_day_format(locale)))
        self.date_info_widget.setAccessibleDescription(locale.toString(today, QLocale.FormatType.LongFormat))

        # Opening on an empty box: normally nothing to show, but a launcher
        # with no commands yet surfaces its offer to scan for programs here.
        if not self.search_input_widget.text():
            self._clear_and_hide_results()
        self._maybe_show_tips()

        self.adjustSize()
        self._move_to_configured_screen()
        self.show()
        if was_visible:
            self.raise_()
            self.activateWindow()
            QTimer.singleShot(100, self._force_focus)

    def _force_focus(self):
        """Force focus on the window with proper Windows API handling"""
        self._begin_focus_grace(DIALOG_GRACE_MS)
        try:
            hwnd = self.winId().__int__()

            # Get the current foreground window
            current_foreground = win32gui.GetForegroundWindow()

            # Get the thread ID of the current foreground window
            current_thread = win32process.GetWindowThreadProcessId(current_foreground)[0] if current_foreground else 0
            # Get our thread ID
            our_thread = win32api.GetCurrentThreadId()

            # Attach to the foreground thread to bypass SetForegroundWindow restrictions
            if current_thread != our_thread:
                win32process.AttachThreadInput(current_thread, our_thread, True)

            # Show and restore the window
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0, win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
            win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0, win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)

            # Set focus
            win32gui.SetForegroundWindow(hwnd)
            win32gui.SetFocus(hwnd)

            # Detach thread input
            if current_thread != our_thread:
                win32process.AttachThreadInput(current_thread, our_thread, False)

        except Exception as e:
            log.warning("Could not force focus: %s", e)
            # Fallback to Qt methods
            self.raise_()
            self.activateWindow()
            self.search_input_widget.setFocus()

    # -- hiding when focus moves away ------------------------------------------

    def _begin_focus_grace(self, milliseconds: int):
        self._focus_grace_until = max(self._focus_grace_until, time.monotonic() + milliseconds / 1000)

    @contextmanager
    def _dialog_open(self):
        """Keep the launcher open while a dialog of Dash's is being built and
        shown, and for a moment after it closes while focus comes back."""
        self._dialog_depth += 1
        try:
            yield
        finally:
            self._dialog_depth -= 1
            self._begin_focus_grace(DIALOG_GRACE_MS)

    def launcher_in_use(self) -> bool:
        """True while losing focus should not hide the launcher: an editor
        or Settings is open in the window, a dialog, menu or notice of Dash's
        is open or opening, or the window is still settling after showing."""
        if self._editor_panel is not None or self._dialog_depth > 0 or self._tray_menu_open:
            return True
        if time.monotonic() < self._focus_grace_until:
            return True
        if QApplication.activeModalWidget() is not None or QApplication.activePopupWidget() is not None:
            return True
        active = QApplication.activeWindow()
        if active is not None and active is not self:
            return True
        for widget in QApplication.topLevelWidgets():
            if widget is self or not widget.isVisible() or widget.windowType() == Qt.WindowType.ToolTip:
                continue
            return True  # a dialog, message or menu of Dash's
        return False

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and not self.isActiveWindow():
            self._on_focus_lost()

    def _on_application_state_changed(self, state):
        if state != Qt.ApplicationState.ApplicationActive:
            self._on_focus_lost()

    def _on_focus_lost(self):
        if not self.settings.general.hide_when_focus_lost or not self.isVisible():
            return
        # Decide once activation has settled: focus may be passing to one of
        # Dash's own windows. Within the grace period, look again after it.
        delay = max(50, round((self._focus_grace_until - time.monotonic()) * 1000) + 50)
        QTimer.singleShot(delay, self._hide_if_focus_lost)

    def _hide_if_focus_lost(self):
        if not self.settings.general.hide_when_focus_lost or not self.isVisible():
            return
        if self.isActiveWindow() or self.launcher_in_use():
            return
        log.debug("Focus moved to another app; hiding the launcher")
        self.hide_launcher()

    # -- where the launcher opens ----------------------------------------------

    def _target_screen(self) -> QScreen | None:
        """The display the launcher should open on, per the launcher_screen setting.

        "mouse": the display under the pointer. "primary": the primary display.
        Anything else is a display name as shown in Settings; if that display
        is not connected right now, fall back to the one under the pointer,
        which is where the person is looking, rather than the primary one.
        """
        choice = str(self.settings.general.launcher_screen or "mouse").strip()
        primary = QApplication.primaryScreen()
        under_mouse = QApplication.screenAt(QCursor.pos()) or primary
        if choice == "mouse":
            return under_mouse
        if choice == "primary":
            return primary
        screens = QApplication.screens()
        for screen in screens:
            if screen.name().strip() == choice:
                return screen
        if choice.startswith("display:"):  # a display that reported no name, stored by position
            try:
                index = int(choice.split(":", 1)[1]) - 1
                if 0 <= index < len(screens):
                    return screens[index]
            except ValueError:
                pass
        if choice not in self._missing_screens_logged:
            self._missing_screens_logged.add(choice)
            log.info("Display %r is not connected; opening on the display under the pointer", choice)
        return under_mouse

    def _move_to_configured_screen(self):
        screen = self._target_screen()
        if screen is None:
            return
        area = screen.availableGeometry()
        move_within_screen(self, self.frameGeometry().size(), area.center(), area)

    def showEvent(self, event):
        super().showEvent(event)
        self._begin_focus_grace(FOCUS_GRACE_MS)
        self.raise_()
        self.activateWindow()
        QTimer.singleShot(100, self._force_focus)

    def _setup_tray(self):
        """Setup system tray with retry logic for Windows startup"""
        self.tray = self.setup_tray_icon()

        # Verify tray icon is visible, retry if not
        if not self.tray.isVisible() and self._tray_retry_count < TRAY_SETUP_RETRIES:
            self._tray_retry_count += 1
            log.info("Tray icon not visible, retrying (%d/%d)", self._tray_retry_count, TRAY_SETUP_RETRIES)
            QTimer.singleShot(TRAY_SETUP_RETRY_MS, self._setup_tray)
            return
        self._show_first_start_notice()
        pending, self._pending_tray_messages = self._pending_tray_messages, []
        for title, message in pending:
            self.show_tray_notice(title, message)

    def show_tray_notice(self, title: str, message: str, warning: bool = True):
        """A tray notification, or held until the tray icon is up."""
        if self.tray is None or not self.tray.isVisible():
            self._pending_tray_messages.append((title, message))
            return
        icon = QSystemTrayIcon.MessageIcon.Warning if warning else QSystemTrayIcon.MessageIcon.Information
        self.tray.showMessage(title, message, icon, 10000)

    def _on_hotkey_registration_failed(self, message: str):
        self.show_tray_notice("Dash hotkey", message)

    def _first_start_marker(self) -> Path:
        # Beside the config, which installs and updates leave alone.
        return self._settings_path().parent / "first_start_shown"

    def _tips_marker(self) -> Path:
        return self._settings_path().parent / "first_tips_shown"

    def _show_first_start_notice(self):
        """Once, on the very first start after installing: how to get back.

        Dash lives in the tray and vanishes on Esc, and nothing else on screen
        says how to open it again. Updates keep the config folder, so the
        marker written here also keeps the notice from repeating after them.
        """
        marker = self._first_start_marker()
        if marker.exists() or self.tray is None:
            return
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("shown", encoding="utf-8")
        except OSError:
            return
        # An install that already has commands predates the notice: it is an
        # update, not a first install, and gets the marker without the notice.
        if self.cmd_manager.has_user_commands():
            return
        self.tray.showMessage(
            "Dash is running",
            f"Press {self._format_shortcut(self.settings.general.hotkey)} to open it from anywhere.",
            QSystemTrayIcon.MessageIcon.Information,
            10000,
        )

    def _tips_text(self) -> str:
        edit = self._format_shortcut(self.settings.shortcuts.edit_selected_command)
        return f"Tip: Tab completes  \u00b7  {edit} edits  \u00b7  right-click for more  \u00b7  type a site's name then a space to search it"

    def _maybe_show_tips(self):
        """Once, the first time the launcher opens with commands in it: the
        keys that are easy to miss, in the footer until dismissed or closed."""
        if self._tips_checked:
            return
        marker = self._tips_marker()
        if marker.exists():
            self._tips_checked = True
            return
        if not self.cmd_manager.has_user_commands():
            return  # the empty launcher has its own offer to show first
        self._tips_checked = True
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("shown", encoding="utf-8")
        except OSError:
            log.warning("Could not record that the first-run tips were shown", exc_info=True)
            return
        self.tip_label.setText(self._tips_text())
        self.shortcut_hint_label.hide()
        self.tip_label.show()
        self.tip_dismiss_button.show()
        self._fit_footer()

    def dismiss_tips(self):
        self.tip_label.hide()
        self.tip_dismiss_button.hide()
        self.shortcut_hint_label.show()
        self._fit_footer()

    def _fit_footer(self):
        """The footer is one line, or as many as the tip needs while it shows."""
        height = self._base_footer_height()
        if not self.tip_label.isHidden():
            margins = self.footer_widget.layout().contentsMargins() if self.footer_widget.layout() else None
            spare = (margins.left() + margins.right()) if margins is not None else 0
            width = self.settings.ui.program_width - spare - self.tip_dismiss_button.sizeHint().width() - self.keys_hint_label.sizeHint().width() - 2 * self._layout_spacing
            height = max(height, self.tip_label.heightForWidth(max(80, width)) + 10)
        self.footer_height = height
        self.footer_widget.setFixedHeight(height)
        self._sync_search_view_size()

    def setup_tray_icon(self):
        """Create and configure system tray icon with menu"""
        tray = QSystemTrayIcon()
        tray.setIcon(QIcon(self.settings.paths.program_icon))
        tray.setToolTip("Dash")
        tray.setVisible(True)

        # Create context menu (right-click) - store as instance variable
        self.tray_menu = QMenu()
        self.tray_menu.setAccessibleName("Dash menu")
        self.tray_menu.aboutToShow.connect(self._on_tray_menu_shown)
        self.tray_menu.aboutToHide.connect(self._on_tray_menu_hidden)

        open_action = QAction("Open Dash", self)
        open_action.triggered.connect(self.open_from_tray)
        self.tray_menu.addAction(open_action)
        self.tray_menu.setDefaultAction(open_action)

        settings_action = QAction("Settings...", self)
        settings_action.triggered.connect(self.open_settings_from_tray)
        self.tray_menu.addAction(settings_action)

        self.tray_menu.addSeparator()

        # App-level actions; feature workflows are available from the
        # launcher and settings editor.
        install_location_action = QAction("Open Install Location", self)
        install_location_action.triggered.connect(self.open_install_location)
        self.tray_menu.addAction(install_location_action)

        config_folder_action = QAction("Open Config Folder", self)
        config_folder_action.triggered.connect(self.open_config_folder)
        self.tray_menu.addAction(config_folder_action)

        log_folder_action = QAction("Open Log Folder", self)
        log_folder_action.triggered.connect(self.open_log_folder)
        self.tray_menu.addAction(log_folder_action)

        self._check_updates_action = QAction("Check for Updates...", self)
        self._check_updates_action.triggered.connect(lambda: self.check_for_updates(manual=True))
        self.tray_menu.addAction(self._check_updates_action)

        self._download_update_action = QAction(self)
        self._download_update_action.triggered.connect(lambda: self.install_update(manual=True))
        self._refresh_update_action()
        self.tray_menu.addAction(self._download_update_action)

        self.tray_menu.addSeparator()

        # About action
        about_action = QAction("About", self)
        about_action.triggered.connect(self.show_about)
        self.tray_menu.addAction(about_action)

        # Quit action
        quit_action = QAction(QIcon("assets/icons/quit.png"), "Quit", self)
        app = QApplication.instance()
        assert app is not None
        quit_action.triggered.connect(app.quit)
        self.tray_menu.addAction(quit_action)

        tray.setContextMenu(self.tray_menu)
        tray.messageClicked.connect(self._on_update_message_clicked)

        # A click or double-click on the icon opens Dash
        tray.activated.connect(self._on_tray_activated)
        return tray

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.open_from_tray()

    def _on_tray_menu_shown(self):
        self._tray_menu_open = True

    def _on_tray_menu_hidden(self):
        self._tray_menu_open = False
        self._begin_focus_grace(DIALOG_GRACE_MS)

    def open_from_tray(self):
        """Open the launcher; if it is already open, bring it to the front."""
        if not self.isVisible():
            self.activate_launcher()
        else:
            self._begin_focus_grace(FOCUS_GRACE_MS)
            self.raise_()
            self.activateWindow()

    def open_settings_from_tray(self):
        self.open_from_tray()
        if isinstance(self._editor_panel, SettingsEditorPanel):
            return
        if self._editor_panel is not None:
            # A command is being edited: leave it be rather than lose the edit.
            return
        self.open_settings_editor()

    def open_log_folder(self):
        folder = app_log.log_dir()
        if folder is None or not Path(folder).exists():
            self.display_error_popup("Logging isn't set up, so there is no log folder yet.")
            return
        self._open_folder(Path(folder))

    # -- telling the person about problems -------------------------------------

    def _show_error_notice(self, summary: str = ""):
        """An unexpected error happened: say so plainly, without blocking,
        at most once every ERROR_NOTICE_INTERVAL_S seconds. The details are
        in the log, which app_log has already written."""
        now = time.monotonic()
        if now - self._last_error_notice < ERROR_NOTICE_INTERVAL_S:
            return
        if self._error_notice is not None and self._error_notice.isVisible():
            return
        self._last_error_notice = now
        notice = QMessageBox(self)
        notice.setWindowTitle("Dash")
        notice.setIcon(QMessageBox.Icon.Warning)
        notice.setText(UNEXPECTED_ERROR_TEXT)
        notice.setWindowModality(Qt.WindowModality.NonModal)
        notice.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        open_button = notice.addButton("Open Log Folder", QMessageBox.ButtonRole.ActionRole)
        notice.addButton(QMessageBox.StandardButton.Close)
        # An action button would close the box; keep the handler on the click.
        open_button.clicked.connect(self.open_log_folder)
        notice.destroyed.connect(lambda *_: setattr(self, "_error_notice", None))
        self._error_notice = notice
        notice.show()

    def show_load_warnings(self):
        """Show, once, what went wrong loading settings or commands (a
        damaged file set aside, entries left out), then forget it."""
        warnings: list[str] = []
        for source in (self.settings, self.cmd_manager):
            pending = getattr(source, "load_warnings", None)
            if pending:
                warnings.extend(str(message) for message in pending if str(message) not in warnings)
                pending.clear()
        if not warnings:
            return
        box = QMessageBox(self)
        box.setWindowTitle("Dash")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(warnings[0] if len(warnings) == 1 else "Dash found some problems while starting.")
        if len(warnings) > 1:
            box.setInformativeText("\n\n".join(warnings))
        box.setWindowModality(Qt.WindowModality.NonModal)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.show()

    def _show_save_error(self, title: str, error: OSError):
        """A save, delete, import or export failed: say so plainly."""
        log.error("%s failed", title, exc_info=(type(error), error, error.__traceback__))
        message = str(error) if isinstance(error, CommandsFileUnreadableError) else SAVE_FAILED_TEXT
        with self._dialog_open():
            QMessageBox.warning(self, title, message)

    def check_for_updates(self, manual: bool = False):
        if self._update_reply is not None:
            return

        self._update_check_manual = manual
        if self._check_updates_action is not None:
            self._check_updates_action.setEnabled(False)
            self._check_updates_action.setText("Checking for Updates...")

        request = QNetworkRequest(QUrl(LATEST_RELEASE_API))
        request.setRawHeader(b"Accept", b"application/vnd.github+json")
        request.setRawHeader(b"X-GitHub-Api-Version", b"2022-11-28")
        request.setRawHeader(b"User-Agent", b"Dash-Update-Checker")
        request.setTransferTimeout(10000)
        self._update_reply = self._update_network.get(request)
        self._update_reply.finished.connect(self._on_update_check_finished)

    def _on_update_check_finished(self):
        import json

        reply = self._update_reply
        if reply is None:
            return
        self._update_reply = None
        manual = self._update_check_manual
        self._update_check_manual = False

        if self._check_updates_action is not None:
            self._check_updates_action.setEnabled(True)
            self._check_updates_action.setText("Check for Updates...")

        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                raise RuntimeError(reply.errorString())
            release = parse_release(json.loads(bytes(reply.readAll()).decode("utf-8")))
            installed_version = current_version()
            if release is None:
                raise ValueError("GitHub returned incomplete release information")
        except (RuntimeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            log.warning("Update check failed: %s", error)
            if manual:
                with self._dialog_open():
                    QMessageBox.warning(
                        self,
                        "Check for Updates",
                        "Dash couldn't check for updates. Check your internet connection and try again later.",
                    )
            reply.deleteLater()
            return

        reply.deleteLater()
        if not is_newer_version(release.tag, installed_version):
            if manual:
                with self._dialog_open():
                    QMessageBox.information(self, "Check for Updates", f"Dash {installed_version} is up to date.")
            return

        self._latest_release = release
        self._refresh_update_action()

        if manual:
            self._show_update_available(release)
        elif self.tray is not None:
            action = "Click to install." if self._can_install(release) else "Click to open the release."
            self.tray.showMessage(
                "Dash Update Available",
                f"Dash {release.version} is available. {action}",
                QSystemTrayIcon.MessageIcon.Information,
                10000,
            )

    def _on_update_message_clicked(self):
        """The startup notification was clicked: ask, never install unasked."""
        release = self._latest_release
        if release is None:
            return
        if self._can_install(release):
            self._show_update_available(release)
        else:
            self.open_latest_release()

    def _can_install(self, release: ReleaseInfo | None) -> bool:
        """Silent install needs the packaged build and a verifiable installer."""
        return release is not None and release.installable and is_installed_build()

    def _refresh_update_action(self):
        action = self._download_update_action
        release = self._latest_release
        if action is None:
            return
        action.setVisible(release is not None)
        if release is None:
            return
        if self._can_install(release):
            action.setText(f"Install Dash {release.version}...")
        else:
            action.setText(f"Download Dash {release.version}...")

    def _show_update_available(self, release: ReleaseInfo):
        message_box = QMessageBox(self)
        message_box.setWindowTitle("Dash Update Available")
        message_box.setIcon(QMessageBox.Icon.Information)
        message_box.setText(f"Dash {release.version} is available.")
        install_button = None
        if self._can_install(release):
            message_box.setInformativeText("Install it now? Dash will restart when the update finishes.")
            install_button = message_box.addButton("Install and Restart", QMessageBox.ButtonRole.AcceptRole)
        else:
            message_box.setInformativeText("Open the GitHub release to download the installer?")
        open_button = message_box.addButton("Open Release", QMessageBox.ButtonRole.ActionRole)
        message_box.addButton(QMessageBox.StandardButton.Cancel)
        with self._dialog_open():
            message_box.exec()
        clicked = message_box.clickedButton()
        if install_button is not None and clicked is install_button:
            self.install_update(manual=True)
        elif clicked is open_button:
            self.open_latest_release()

    def open_latest_release(self):
        url = self._latest_release.page_url if self._latest_release is not None else LATEST_RELEASE_PAGE
        QDesktopServices.openUrl(QUrl(url))

    # -- installing updates ---------------------------------------------------

    def install_update(self, manual: bool = True):
        """Download the latest installer, verify it, then install and restart.

        Only ever runs because the user asked (the tray item, the update
        dialog, or the notification). Progress is shown throughout.
        """
        release = self._latest_release
        if release is None:
            return
        if not self._can_install(release):
            self.open_latest_release()
            return
        if self._update_downloader is not None:
            self._show_download_progress()
            return

        self._update_downloader = UpdateDownloader(self._update_network, release, self)
        self._update_downloader.finished.connect(self._on_update_downloaded)
        self._update_downloader.failed.connect(self._on_update_failed)
        self._update_downloader.progress.connect(self._on_update_progress)
        self._show_download_progress()
        self._update_downloader.start()

    def _show_download_progress(self):
        if self._update_progress is not None or self._latest_release is None:
            return
        progress = QProgressDialog(self)
        progress.setWindowTitle("Updating Dash")
        progress.setLabelText(f"Downloading Dash {self._latest_release.version}...")
        progress.setRange(0, 0)
        progress.setMinimumDuration(0)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.canceled.connect(self._cancel_update_download)
        self._update_progress = progress
        progress.show()

    def _on_update_progress(self, received: int, total: int):
        progress = self._update_progress
        if progress is None:
            return
        if total > 0:
            progress.setRange(0, 100)
            progress.setValue(int(received * 100 / total))

    def _close_download_progress(self):
        progress = self._update_progress
        self._update_progress = None
        if progress is not None:
            progress.canceled.disconnect(self._cancel_update_download)
            progress.close()
            progress.deleteLater()

    def _cancel_update_download(self):
        if self._update_downloader is not None:
            self._update_downloader.cancel()
            self._update_downloader.deleteLater()
            self._update_downloader = None
        self._close_download_progress()

    def _on_update_failed(self, message: str):
        self._update_downloader = None
        self._close_download_progress()
        log.warning("Update failed: %s", message)
        # The updater's messages are written to be shown as they are.
        with self._dialog_open():
            QMessageBox.warning(self, "Updating Dash", f"The update couldn't be installed.\n\n{message}")

    def _on_update_downloaded(self, installer_path: str):
        self._update_downloader = None
        self._close_download_progress()
        self._install_now(installer_path)

    def _install_now(self, installer_path: str):
        if not Path(installer_path).exists():
            with self._dialog_open():
                QMessageBox.warning(self, "Updating Dash", "The downloaded installer is missing.")
            return
        version = self._latest_release.version if self._latest_release else ""
        if self.tray is not None:
            self.tray.showMessage(
                "Updating Dash",
                f"Installing Dash {version}. Dash will restart in a moment.",
                QSystemTrayIcon.MessageIcon.Information,
                4000,
            )
        if not launch_installer(installer_path):
            with self._dialog_open():
                QMessageBox.warning(self, "Updating Dash", "The installer couldn't be started.")
            return
        app = QApplication.instance()
        if app is not None:
            QTimer.singleShot(300, app.quit)

    def set_hotkey_listener(self, listener):
        self._hotkey_listener = listener
        # Emitted on a zero-length timer after the listener is created, and
        # again after update_hotkey, so connecting here catches both.
        failed = getattr(listener, "registrationFailed", None)
        if failed is not None:
            failed.connect(self._on_hotkey_registration_failed)

    def _settings_path(self):
        if self._loaded_settings_path is not None:
            return self._loaded_settings_path
        if hasattr(sys, "_MEIPASS"):
            # Installed - settings in AppData
            app_data = Path(os.environ.get("APPDATA", "")) / "Dash"
            return app_data / "config" / "settings.toml"
        else:
            # Development - settings in project folder
            return Path(__file__).parent.parent / "config" / "settings.toml"

    def open_settings_from_search(self):
        """The settings shortcut, from the search view only: inside an editor
        the same keys belong to that editor's fields."""
        if hasattr(self, "view_stack") and self.view_stack.currentWidget() is not self.central_widget:
            return
        self.open_settings_editor()

    def open_settings_editor(self):
        settings_path = self._settings_path()
        previous_center = self.frameGeometry().center()
        self._editor_return_center = previous_center
        # A browser installed since Dash started should be in the list.
        try:
            browsers.refresh_installed_browsers()
        except Exception:
            log.warning("Could not refresh the list of browsers", exc_info=True)
        panel = SettingsEditorPanel(self.settings, settings_path, self)
        panel.closed.connect(self.close_editor)
        panel.saved.connect(self._apply_settings)
        panel.importProgramsRequested.connect(self.choose_recent_programs)
        panel.exportCommandsRequested.connect(self.export_commands)
        panel.importCommandsRequested.connect(self.import_commands)
        panel.manageCommandsRequested.connect(self.manage_commands)
        panel.resetRunCountsRequested.connect(self.reset_all_run_counts)
        # The style sheet is re-applied by the panel; the theme notifier
        # (connected in __init__) restyles the painted parts of the launcher.
        self._editor_panel = panel
        self.view_stack.addWidget(panel)
        self.view_stack.setCurrentWidget(panel)
        self._pre_editor_size = self.size()
        # Two columns of label/field rows need the width; the screen clamp
        # still shrinks it on a small laptop.
        editor_size = clamp_size_to_screen(
            max(880, self.settings.ui.program_width),
            max(720, self.settings.ui.editor_height),
            available_geometry_for(self),
        )
        pin_within_screen(self, editor_size, previous_center)
        panel.setFocus()

    def manage_commands(self):
        """Settings' Manage Commands: list, edit or delete every command."""
        user_commands = [c for c in self.cmd_manager.commands.values() if c.get("type") != "system"]
        dialog = ManageCommandsDialog(user_commands, self.cmd_manager, self.icon_manager, self)
        requested: list[str] = []
        dialog.editRequested.connect(requested.append)
        dialog.commandsDeleted.connect(self._on_commands_deleted)
        with self._dialog_open():
            dialog.exec()
        dialog.deleteLater()
        if requested:
            self._edit_from_manage(requested[0])

    def _edit_from_manage(self, name: str):
        """Leave Settings for the command editor: the two share the window."""
        if isinstance(self._editor_panel, SettingsEditorPanel):
            self.close_editor()
        self.open_selected_command_editor_by_name(name)

    def _on_commands_deleted(self, names: list):
        log.info("Deleted %d command(s) from Manage Commands", len(names))
        self._refresh_results()

    def _refresh_results(self):
        """Show the results for the current text again, after commands changed."""
        if self.user_text:
            self.cmd_manager.get_matching_commands(self, self.user_text)
        else:
            self._clear_and_hide_results()

    def _apply_settings(self, settings):
        previous = self.settings
        self.settings = settings
        self.setWindowOpacity(settings.ui.window_opacity)
        self.icon_manager.settings = settings
        # Settings applies the theme before it emits saved; this catches any
        # other caller that hands over a different theme.
        applied_setting = str(getattr(theme, "_state", {}).get("setting", ""))
        if settings.ui.theme != previous.ui.theme and applied_setting != settings.ui.theme:
            theme.apply_theme(QApplication.instance(), settings)
        # The command manager reads sort order and case handling from its own
        # settings reference, so hand it the new object and rebuild the trie.
        self.cmd_manager.settings = settings
        self.cmd_manager.reload_command_trie()
        hotkey_listener = self._hotkey_listener
        if hotkey_listener is not None and (
            settings.general.hotkey != previous.general.hotkey or getattr(hotkey_listener, "registration_error", None)
        ):
            # Also retried when the old key could not be registered: whatever
            # held it may have let go.
            hotkey_listener.update_hotkey(settings.general.hotkey)
        self.edit_shortcut.setKeys(_key_sequences(settings.shortcuts.edit_selected_command))
        self.new_command_shortcut.setKeys(_key_sequences(settings.shortcuts.new_command))
        self.settings_shortcut.setKeys(_key_sequences(settings.shortcuts.open_settings))
        self._apply_search_text_style()
        self._layout_scale = max(0.8, min(1.4, settings.ui.program_width / 500))
        self._layout_margin = max(10, round(10 * self._layout_scale))
        self._layout_spacing = max(10, round(10 * self._layout_scale))
        self._apply_clock_text_style()
        self.date_info_widget.setVisible(settings.ui.show_clock)
        search_layout = self.search_container_widget.layout()
        search_layout.setContentsMargins(self._layout_margin, 0, self._layout_margin, 0)
        search_layout.setSpacing(self._layout_spacing)
        footer_layout = self.footer_widget.layout()
        footer_layout.setContentsMargins(self._layout_margin, 0, self._layout_margin, 0)
        footer_layout.setSpacing(self._layout_spacing)
        self._fit_footer()
        self.search_container_widget.setFixedSize(QSize(settings.ui.program_width, self._search_height()))
        self._results_list_height = settings.ui.results_height  # re-snapped by the next show_results
        self.results_list_widget.setFixedSize(QSize(settings.ui.program_width, self._results_list_height))
        self.search_tree_widget.set_scale(self._layout_scale)
        self.search_tree_widget.setFixedSize(
            self._search_tree_width(),
            self._search_height() + self._results_list_height,
        )
        if self.user_text:
            self._update_search_tree(self.user_text)
            self.cmd_manager.get_matching_commands(self, self.user_text)
        else:
            self._update_search_tree("")
            self._clear_and_hide_results()
        self.shortcut_hint_label.setText(self._shortcut_hint_text())
        self.keys_hint_label.setToolTip("\n".join(self._keys_help_lines()))
        self.keys_hint_label.setAccessibleDescription("; ".join(self._keys_help_lines()))
        self.show_load_warnings()

    def _on_theme_changed(self, _name: str = ""):
        """Repaint what is drawn in code, and rebuild the result rows so
        their glyphs pick up the new colors."""
        self.search_tree_widget.update()
        self._apply_search_text_style()
        self._apply_clock_text_style()
        if self.view_stack.currentWidget() is self.central_widget and self.results_list_widget.count():
            self._refresh_results()

    def open_config_folder(self):
        """Open the folder holding settings.toml and commands.toml in Explorer."""
        config_path = self.cmd_manager.command_file_path.parent

        if config_path.exists():
            self._open_folder(config_path)
        else:
            self.display_error_popup(f"Config folder not found at {config_path}")

    def open_install_location(self):
        """Open the directory containing the installed executable."""
        install_path = Path(sys.executable).parent if hasattr(sys, "_MEIPASS") else Path(__file__).parent.parent

        if install_path.exists():
            self._open_folder(install_path)
        else:
            self.display_error_popup(f"Install location not found at {install_path}")

    def _open_folder(self, folder: Path):
        try:
            os.startfile(folder)
        except OSError:
            log.exception("Could not open %s", folder)
            self.display_error_popup(f"Windows couldn't open the folder:\n{folder}")

    def display_error_popup(self, text):
        error_dialog = QErrorMessage(self)
        error_dialog.showMessage(text)
        with self._dialog_open():
            error_dialog.exec()

    def on_text_change(self, text):
        # Early exit conditions
        if self.is_deleting or not self.settings.search.autocomplete:
            self._update_without_suggestion(text)
            return

        # Extract the actual user input (not including selected suggestion)
        self.user_text = self._extract_user_input(text)

        if not self.user_text:
            self._update_without_suggestion("")
            return

        self._update_search_tree(self.user_text)

        # Get matching results
        results = self.cmd_manager.get_matching_commands(self, self.user_text)

        # Apply autocomplete if we have a match
        if results:
            self._try_apply_suggestion(self.get_selected_command())
        else:
            self.current_suggestion = ""

    def _extract_user_input(self, full_text):
        """Extract user-typed text, excluding any selected suggestion"""
        if not self.search_input_widget.hasSelectedText():
            return full_text

        selected = self.search_input_widget.selectedText()
        # Only strip if selection is at the end
        if full_text.endswith(selected):
            return full_text[: -len(selected)]
        return full_text

    def _completion_for(self, command_name, typed: str) -> str | None:
        """The name or alias to complete `typed` to, or None. Only a keyword
        that starts with exactly what was typed (as the trie compares text)
        is used: a word-start match such as "code" for Visual Studio Code has
        no completion, and nothing is spliced where case folding changes the
        length of the text."""
        keyword = self.cmd_manager.completion_keyword(command_name, typed)
        if keyword is None or len(keyword) < len(typed):
            return None
        normalize = self.cmd_manager.lookup_trie.normalize
        if normalize(keyword[: len(typed)]) != normalize(typed):
            return None
        return keyword

    def _try_apply_suggestion(self, command_name):
        """Show the rest of the selected command's matching name or alias in blue."""
        keyword = self._completion_for(command_name, self.user_text)
        if keyword is None:
            self.current_suggestion = ""
            # Drop a completion left over from a row that no longer applies.
            if self.search_input_widget.text() != self.user_text:
                self.search_input_widget.blockSignals(True)
                self.search_input_widget.setText(self.user_text)
                self.search_input_widget.blockSignals(False)
            return

        completion = keyword[len(self.user_text) :].lower()

        self.search_input_widget.blockSignals(True)
        self.search_input_widget.setText(self.user_text + completion)
        self.search_input_widget.setSelection(len(self.user_text), len(completion))
        self.search_input_widget.blockSignals(False)

        self.current_suggestion = keyword

    def _follow_selection(self):
        """Complete the box from the row the user moved to, keeping what they typed."""
        if not self.settings.search.autocomplete or not self.user_text:
            return
        self._try_apply_suggestion(self.get_selected_command())

    def accept_suggestion(self):
        """Tab: fill in the selected command's name or alias, blue part or not.
        A row found by the start of a later word ("code" for Visual Studio
        Code) fills in the whole name. A search keyword also gets the space
        that starts a search, so what to search for can be typed straight
        away. Focus stays in the box."""
        box = self.search_input_widget
        typed = self.user_text if box.hasSelectedText() else box.text()
        row = self.get_selected_row()
        name = row.command_name if row is not None else None
        command = self.cmd_manager.commands.get(name) if name else None
        keyword = self._completion_for(name, typed)
        if keyword is not None:
            text = typed + keyword[len(typed) :].lower()
        elif command is not None and row.query is None and row.action is None and row.copy_value is None:
            text = str(command["name"])
        else:
            # Nothing to complete (a search already under way, a calculator row): leave the text alone.
            box.deselect()
            box.setCursorPosition(len(box.text()))
            return
        if command.get("type") == "url" and is_search_link(command.get("location", "")):
            text += " "
        box.blockSignals(True)
        box.setText(text)
        box.setCursorPosition(len(text))
        box.blockSignals(False)
        self.is_deleting = False
        self._update_without_suggestion(text)

    def _update_without_suggestion(self, text):
        """Update state without applying autocomplete"""
        self.user_text = text
        self.current_suggestion = ""
        self._update_search_tree(text)
        if text:
            self.cmd_manager.get_matching_commands(self, text)
        else:
            self._clear_and_hide_results()

    def on_enter_pressed(self):
        row = self.get_selected_row()
        if row is not None:
            self.activate_row(row)

    def activate_row(self, row: "ResultRow"):
        """Act on a results row: run its action, copy a calculator value, or run its command."""
        if row.action is not None:
            row.action()
            return
        if row.copy_value is not None:
            self._copy_and_hide(row.copy_value)
            return

        cmd = self.cmd_manager.commands.get(row.command_name)
        if cmd is None:
            # A row that only informs (a calculation that can't be done):
            # there is nothing to open, and what was typed stays put.
            return

        error = self.cmd_manager.execute_command(self, row.command_name, query=row.query)
        self._finish_launch(cmd, error)

    def _finish_launch(self, cmd: dict, error: str | None):
        if error:
            self._show_launch_failure(cmd, error)
            return

        # Opening settings swaps in an in-window panel; hiding the launcher
        # right after would hide that panel too, so leave the window shown.
        opens_panel = cmd.get("type") == "system" and cmd.get("action") == "open_settings"
        if not opens_panel:
            self.hide_launcher()

    def _copy_to_clipboard(self, text: str):
        try:
            pyperclip.copy(text)
        except Exception:
            log.warning("pyperclip could not copy; using the Qt clipboard", exc_info=True)
            clipboard = QApplication.clipboard()
            if clipboard is not None:
                clipboard.setText(text)

    def _copy_and_hide(self, text: str):
        self._copy_to_clipboard(text)
        log.info("Copied to the clipboard")
        self.hide_launcher()

    # -- launch options for the selected row -------------------------------------

    def _selected_command_row(self) -> "ResultRow | None":
        """The selected row when it is a command (not a calculation or offer)."""
        row = self.get_selected_row()
        if row is None or row.action is not None or row.copy_value is not None:
            return None
        return row if row.command_name in self.cmd_manager.commands else None

    def _can_open_new_copy(self, name: str) -> bool:
        """"Open a new copy" only means something for an app that would
        otherwise be switched to."""
        cmd = self.cmd_manager.commands.get(name)
        if cmd is None or cmd.get("type") != "file" or not self.settings.general.switch_to_open_apps:
            return False
        return self.cmd_manager.can_run_as_administrator(name)

    def open_selected_new_copy(self):
        row = self._selected_command_row()
        if row is None:
            return
        if not self._can_open_new_copy(row.command_name):
            self.activate_row(row)
            return
        cmd = self.cmd_manager.commands[row.command_name]
        error = self.cmd_manager.execute_command(self, row.command_name, query=row.query, new_instance=True)
        self._finish_launch(cmd, error)

    def run_selected_as_administrator(self):
        row = self._selected_command_row()
        if row is None or not self.cmd_manager.can_run_as_administrator(row.command_name):
            return
        cmd = self.cmd_manager.commands[row.command_name]
        self._finish_launch(cmd, self.cmd_manager.run_as_administrator(row.command_name))

    def open_selected_containing_folder(self):
        row = self._selected_command_row()
        if row is None or self.cmd_manager.containing_folder(row.command_name) is None:
            return
        cmd = self.cmd_manager.commands[row.command_name]
        self._finish_launch(cmd, self.cmd_manager.open_containing_folder(row.command_name))

    def copy_selected_path(self) -> bool:
        """Copy the selected command's path or address (or a calculator
        result). False when the selected row has nothing to copy."""
        row = self.get_selected_row()
        if row is None:
            return False
        if row.copy_value is not None:
            self._copy_and_hide(row.copy_value)
            return True
        if row.action is not None or row.command_name not in self.cmd_manager.commands:
            return False
        text = self.cmd_manager.copy_text(row.command_name, row.query)
        if not text:
            return False
        self._copy_and_hide(text)
        return True

    def show_selected_row_menu(self):
        """The Menu key or Shift+F10: the selected row's menu, under the row."""
        item = self.results_list_widget.currentItem()
        row = self.get_selected_row()
        if item is None or row is None:
            return
        rect = self.results_list_widget.visualItemRect(item)
        position = self.results_list_widget.viewport().mapToGlobal(rect.bottomLeft())
        self.show_row_menu(row, position)

    def show_row_menu(self, row: "ResultRow", global_position):
        """Everything that can be done with a result, for the right mouse
        button and the Menu key."""
        name = row.command_name
        cmd = self.cmd_manager.commands.get(name) if row.action is None and row.copy_value is None else None
        menu = QMenu(self)
        menu.setAccessibleName(f"Actions for {row.command_label.full_text()}")
        if cmd is None:
            if row.copy_value is not None:
                menu.addAction("Copy result\tCtrl+C", lambda: self._copy_and_hide(row.copy_value))
            elif row.action is not None:
                menu.addAction("Open\tEnter", lambda: self.activate_row(row))
            else:
                return
        else:
            open_action = menu.addAction("Open\tEnter", lambda: self.activate_row(row))
            menu.setDefaultAction(open_action)
            if self._can_open_new_copy(name):
                menu.addAction("Open a new copy\tShift+Enter", self.open_selected_new_copy)
            if cmd.get("type") == "file":
                admin = menu.addAction("Run as administrator\tCtrl+Shift+Enter", self.run_selected_as_administrator)
                admin.setEnabled(self.cmd_manager.can_run_as_administrator(name))
            if self.cmd_manager.containing_folder(name) is not None:
                menu.addAction("Open containing folder\tAlt+Enter", self.open_selected_containing_folder)
            if self.cmd_manager.copy_text(name, row.query):
                label = "Copy address" if cmd.get("type") == "url" else "Copy path"
                menu.addAction(f"{label}\tCtrl+C", self.copy_selected_path)
            if cmd.get("type") != "system":
                menu.addSeparator()
                edit_keys = self._format_shortcut(self.settings.shortcuts.edit_selected_command)
                menu.addAction(f"Edit\t{edit_keys}", lambda: self.open_selected_command_editor_by_name(name))
                menu.addAction("Delete...", lambda: self.delete_command_with_confirmation(name))
        with self._dialog_open():
            menu.exec(global_position)
        menu.deleteLater()

    def delete_command_with_confirmation(self, name: str):
        cmd = self.cmd_manager.commands.get(name)
        if cmd is None or cmd.get("type") == "system":
            return
        groups = {name: self.cmd_manager.groups_containing(name)}
        with self._dialog_open():
            answer = QMessageBox.question(
                self,
                "Delete Command",
                delete_confirmation_text([name], groups),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.cmd_manager.delete_command(name)
        except OSError as error:
            self._show_save_error("Delete Command", error)
            return
        try:
            self.icon_manager.delete_command_icon(cmd.get("icon"))
        except Exception:
            log.warning("Could not remove the icon of %r", name, exc_info=True)
        self._refresh_results()

    # -- rows that are not commands -----------------------------------------------

    def _search_row(self, command: dict, query: str) -> "ResultRow":
        """The row for a search keyword and what was typed after it."""
        text = query.strip()
        title = f"Search {command['name']} for “{text}”" if text else f"Search {command['name']}"
        description = "Type what to search for, or press Enter to open the site" if not text else command.get("description", "")
        row = ResultRow(
            self,
            icon_path=self.icon_manager.get_icon_path(command),
            command=title,
            command_name=command["name"],
            description=description if self.settings.search.show_descriptions or not text else "",
            editable=self.settings.search.show_edit_button,
            query=query,
        )
        return row

    def _show_web_search_row(self, text: str, even_when_off: bool = False) -> bool:
        """Offer to search the web for text nothing matched. False when there
        is nothing to search for, no usable search address, or the setting
        is off (unless `even_when_off`: then it is offered after "Add")."""
        template = str(self.settings.general.web_search or "").strip()
        text = text.strip()
        enabled = self.settings.general.web_search_enabled
        if not text or not (enabled or even_when_off) or not is_search_link(template):
            return False

        def search():
            try:
                open_in_browser(fill_query(template, text), self.settings.general.browser)
            except OSError as error:
                log.warning("Web search failed: %s", error)
                self.display_error_popup("Dash couldn't open your browser to search the web.")
                return
            self.hide_launcher()

        row = ResultRow(
            self,
            icon_path=self.settings.paths.url_command_icon,
            command=f"Search the web for “{text}”",
            description="No commands match. Press Enter to search in your browser." if enabled else "Opens in your browser",
            action=search,
        )
        self._add_row(row)
        self.results_list_widget.setCurrentRow(0)
        return True

    def _show_add_command_row(self, text: str) -> bool:
        """Offer to make what was typed into a command: as its target when
        it reads as a path or web address, otherwise as its name."""
        text = text.strip()
        if not text:
            return False
        target = target_from_text(text)
        row = ResultRow(
            self,
            icon_path=self.settings.paths.default_command_icon,
            icon_pixmap=glyph_pixmap(OutlineIcon.PLUS, ResultRow.ICON_SIZE, theme.color("text")),
            command=f"Add “{text}” as a command",
            description="Press Enter to open the new command editor with this filled in",
            action=lambda: self.open_new_command_with(name=None if target else text, target=target),
        )
        self._add_row(row)
        return True

    def _show_no_results(self, text: str):
        """Nothing matched: offer to add it as a command, and to search the
        web. With web search switched on the search comes first, as before."""
        search_first = bool(self.settings.general.web_search_enabled)
        if search_first:
            self._show_web_search_row(text)
        self._show_add_command_row(text)
        if not search_first:
            self._show_web_search_row(text, even_when_off=True)
        self.results_list_widget.setCurrentRow(0)

    def _show_calculation(self, text: str) -> bool:
        """A result row for a calculation, or its plain problem ("Can't
        divide by zero") when it is one that can't be done. False when the
        text is not a calculation, or not a finished one."""
        if not calculator.looks_like_calculation(text):
            return False
        separator = self._decimal_separator
        try:
            shown = calculator.format_result(calculator.eval_expression(text, separator), separator)
        except calculator.CalculationError as error:
            if str(error) == calculator.NOT_VALID:
                # Half typed ("12*"), or not a calculation after all.
                return False
            row = ResultRow(
                self,
                icon_path=self.settings.paths.calculator_icon,
                command=str(error),
                description="Calculator",
            )
            self._add_row(row)
            self.results_list_widget.setCurrentRow(0)
            return True
        row = ResultRow(
            self,
            icon_path=self.settings.paths.calculator_icon,
            command=f"= {shown}",
            description="Calculator result (press Enter to copy)",
            copy_value=shown,
        )
        self._add_row(row, accessible_text=f"Equals {shown}, press Enter to copy")
        self.results_list_widget.setCurrentRow(0)
        return True

    def _show_launch_failure(self, cmd: dict, reason: str):
        """A command could not be launched: say why and offer to fix it.

        The launcher stays open so the user is not left staring at the
        desktop wondering what happened.
        """
        message_box = QMessageBox(self)
        message_box.setWindowTitle("Dash")
        message_box.setIcon(QMessageBox.Icon.Warning)
        if cmd.get("type") == "group":
            message_box.setText(f"Some of the commands in {cmd.get('name', 'this group')} didn't open.")
            message_box.setInformativeText(reason)
        else:
            message_box.setText(f"Dash couldn't open {cmd.get('name', 'this command')}.")
            message_box.setInformativeText(f"{reason}\n\nThe target may have been moved, renamed or uninstalled.")
        edit_button = None
        if cmd.get("type") != "system":
            edit_button = message_box.addButton("Edit Command", QMessageBox.ButtonRole.AcceptRole)
        message_box.addButton(QMessageBox.StandardButton.Close)
        with self._dialog_open():
            message_box.exec()
        if edit_button is not None and message_box.clickedButton() is edit_button:
            self.open_editor(cmd)

    def open_selected_command_editor(self):
        if hasattr(self, "view_stack") and self.view_stack.currentWidget() is not self.central_widget:
            return
        name = self.get_selected_command()
        self.open_selected_command_editor_by_name(name)

    def open_selected_command_editor_by_name(self, name):
        command = self.cmd_manager.commands.get(name) if name else None
        if command is not None and command.get("type") != "system":
            self.open_editor(command)

    def open_new_command(self):
        """Ctrl+N: a new command, starting from what was typed. A path or
        web address becomes the target; anything else the name."""
        if hasattr(self, "view_stack") and self.view_stack.currentWidget() is not self.central_widget:
            return
        typed = self.user_text.strip() if self.isVisible() else ""
        target = target_from_text(typed)
        self.open_new_command_with(name=None if target or not typed else typed, target=target)

    def open_new_command_with(self, name: str | None = None, target: str | None = None):
        """Open the new-command editor with the name or target filled in."""
        if hasattr(self, "view_stack") and self.view_stack.currentWidget() is not self.central_widget:
            return
        if not self.isVisible():
            self.activate_launcher()
        self.open_editor(None)
        self._prefill_editor(self._editor_panel, name=name, target=target)

    @staticmethod
    def _prefill_editor(panel, name: str | None = None, target: str | None = None):
        if panel is None or not (name or target):
            return
        prefill = getattr(panel, "prefill", None)
        if callable(prefill):
            prefill(name=name, target=target)
            return
        # Editors without prefill(): fill the fields the way typing would, so
        # the editor still offers a name and description for the target.
        if target:
            from .command_editor import CommandType

            panel.command_type_selector.select(CommandType.from_command({"location": target}))
            panel.command_action.command_action_edit_box.setText(target)
        if name:
            panel.command_name_edit_box.setText(name)

    # -- drag and drop --------------------------------------------------------------

    @staticmethod
    def _dropped_target(mime) -> str | None:
        """The file, folder, shortcut or web address being dropped, if any."""
        if mime is None:
            return None
        if mime.hasUrls():
            for url in mime.urls():
                if url.isLocalFile() and url.toLocalFile():
                    return os.path.normpath(url.toLocalFile())
                if url.scheme() in ("http", "https"):
                    return url.toString()
        if mime.hasText():
            return target_from_text(mime.text())
        return None

    def _paste_copied_file(self) -> bool:
        """Open the new-command editor for a file copied to the clipboard.

        Only files and folders count: pasted text still goes into the box.
        """
        mime = QApplication.clipboard().mimeData()
        if mime is None or not mime.hasUrls():
            return False
        for url in mime.urls():
            if url.isLocalFile() and url.toLocalFile():
                target = os.path.normpath(url.toLocalFile())
                QTimer.singleShot(0, lambda: self.open_new_command_with(target=target))
                return True
        return False

    def _accepts_drop(self, event) -> bool:
        return self.view_stack.currentWidget() is self.central_widget and self._dropped_target(event.mimeData()) is not None

    def dragEnterEvent(self, event):
        if self._accepts_drop(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._accepts_drop(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if not self._accepts_drop(event):
            event.ignore()
            return
        target = self._dropped_target(event.mimeData())
        event.acceptProposedAction()
        self._begin_focus_grace(FOCUS_GRACE_MS)
        # After the drop has returned to the source app (Explorer waits on it).
        QTimer.singleShot(0, lambda: self.open_new_command_with(target=target))

    # -- finding and importing commands ---------------------------------------------

    def choose_recent_programs(self):
        if self._program_discovery_thread is not None:
            return

        progress = QProgressDialog(self)
        progress.setWindowTitle("Find Recommended Commands")
        progress.setLabelText("Looking for apps, folders and websites on this PC...")
        progress.setRange(0, 0)
        progress.setCancelButton(None)
        progress.setMinimumDuration(0)
        progress.setWindowModality(Qt.WindowModality.WindowModal)

        # The recommended websites are a fixed list, so their favicons can be
        # fetched while the rest of the PC is scanned and are usually there by
        # the time the rows are drawn.
        for _name, address, _aliases in POPULAR_WEBSITES:
            self.icon_manager._queue_favicon_download(address)

        thread = ProgramDiscoveryThread(self)
        thread.finished.connect(self._on_program_discovery_finished)
        self._program_discovery_progress = progress
        self._program_discovery_thread = thread
        self._dialog_depth += 1  # the progress dialog, until the scan finishes
        progress.show()
        thread.start()

    def _on_program_discovery_finished(self):
        thread = self._program_discovery_thread
        if thread is None:
            return
        self._program_discovery_thread = None
        self._dialog_depth = max(0, self._dialog_depth - 1)
        self._begin_focus_grace(DIALOG_GRACE_MS)

        progress = self._program_discovery_progress
        self._program_discovery_progress = None
        if progress is not None:
            progress.close()
            progress.deleteLater()

        candidates = thread.candidates
        error_message = thread.error_message
        thread.deleteLater()
        if error_message:
            self.display_error_popup("Dash couldn't finish looking for apps on this PC. Details were saved to the log.")
            return

        self._show_program_import_dialog(candidates)

    def _show_program_import_dialog(self, candidates: list[dict]):
        from .installed_programs import drop_known_names, filter_new_program_commands
        from .personal_places import drop_known_websites

        # The scan thread has already put Windows' own folders and tools first.
        existing_locations = self.cmd_manager.existing_command_locations()
        candidates = filter_new_program_commands(candidates, existing_locations)
        candidates = drop_known_websites(candidates, self.cmd_manager.existing_command_urls())
        # Also drops suggested aliases that another command or recommendation
        # already has (drop_clashing_aliases), so one never blocks an import.
        candidates = drop_known_names(candidates, set(self.cmd_manager._reserved_keywords()))
        dialog = ProgramImportDialog(candidates, existing_locations, self.icon_manager, self, command_manager=self.cmd_manager)
        with self._dialog_open():
            accepted = dialog.exec() == dialog.DialogCode.Accepted
        if not accepted:
            return

        selected = dialog.selected_candidates()
        first_import = not self.cmd_manager.has_user_commands()
        try:
            summary = self.cmd_manager.import_program_commands(selected)
            if summary["imported"]:
                self.cmd_manager.reprocess_command_icons(self.icon_manager)
        except OSError as error:
            self._show_save_error("Find Recommended Commands", error)
            return
        self._seed_run_counts(selected, summary["imported"])
        imported_count = len(summary["imported"])
        skipped_count = len(summary["skipped"])
        message = f"Added {imported_count} command"
        if imported_count != 1:
            message += "s"
        if skipped_count:
            message += f". Skipped {skipped_count} already-present or conflicting candidate"
            if skipped_count != 1:
                message += "s"
        message += "."
        dropped = summary.get("dropped_aliases") or {}
        if dropped:
            listed = "; ".join(f"{name}: {', '.join(aliases)}" for name, aliases in sorted(dropped.items()))
            message += f"\n\nSome suggested aliases were already in use, so they were left off ({listed})."
        with self._dialog_open():
            QMessageBox.information(self, "Find Recommended Commands", message + self._first_import_tip(first_import and bool(summary["imported"])))

        self.show_load_warnings()
        self._refresh_results()

    def _seed_run_counts(self, selected: list[dict], imported: list[str]):
        """Start "most used first" from how often Windows says each new
        command was opened, instead of from zero."""
        seed = getattr(self.cmd_manager, "seed_run_counts", None)
        if not callable(seed):
            return
        wanted = set(imported)
        counts = {str(c.get("name")): int(c.get("opened") or 0) for c in selected if c.get("name") in wanted and c.get("opened")}
        if not counts:
            return
        try:
            seed(counts)
        except OSError:
            log.warning("Could not save the starting run counts", exc_info=True)

    def export_commands(self):
        user_commands = [c for c in self.cmd_manager.commands.values() if c.get("type") != "system"]
        dialog = ExportCommandsDialog(user_commands, self.icon_manager, self)
        with self._dialog_open():
            accepted = dialog.exec() == dialog.DialogCode.Accepted
        if not accepted:
            return

        selected_names = dialog.selected_names()
        if not selected_names:
            return

        default_path = str(Path.home() / "dash_commands.toml")
        with self._dialog_open():
            file_path, _ = QFileDialog.getSaveFileName(self, "Export Commands", default_path, "TOML Files (*.toml)")
        if not file_path:
            return

        try:
            count = self.cmd_manager.export_commands(selected_names, Path(file_path))
        except OSError:
            log.error("Export to %s failed", file_path, exc_info=True)
            with self._dialog_open():
                QMessageBox.warning(
                    self,
                    "Export Commands",
                    "Dash couldn't write the export file. Choose another folder, or check that the drive has free space.",
                )
            return
        with self._dialog_open():
            QMessageBox.information(self, "Export Commands", f"Exported {count} command{'s' if count != 1 else ''} to {file_path}.")

    def import_commands(self):
        with self._dialog_open():
            file_path, _ = QFileDialog.getOpenFileName(self, "Import Commands", "", "TOML Files (*.toml)")
        if not file_path:
            return

        try:
            candidates = self.cmd_manager.parse_import_candidates(Path(file_path))
        except Exception:
            log.warning("Could not read commands from %s", file_path, exc_info=True)
            self.display_error_popup("Dash couldn't read that file. Choose a commands file exported from Dash.")
            return

        dialog = ImportCommandsDialog(candidates, self.cmd_manager, self.icon_manager, self)
        with self._dialog_open():
            accepted = dialog.exec() == dialog.DialogCode.Accepted
        if not accepted:
            return

        selected = dialog.selected_candidates()
        if not selected:
            return

        first_import = not self.cmd_manager.has_user_commands()
        try:
            summary = self.cmd_manager.import_commands(selected)
            if summary["imported"]:
                self.cmd_manager.reprocess_command_icons(self.icon_manager)
        except OSError as error:
            self._show_save_error("Import Commands", error)
            return
        imported_count = len(summary["imported"])
        skipped_count = len(summary["skipped"])
        message = f"Imported {imported_count} command"
        if imported_count != 1:
            message += "s"
        if skipped_count:
            message += f". Skipped {skipped_count} invalid or conflicting command"
            if skipped_count != 1:
                message += "s"
        with self._dialog_open():
            QMessageBox.information(self, "Import Commands", message + "." + self._first_import_tip(first_import and bool(summary["imported"])))
        self.show_load_warnings()
        self._refresh_results()

    def _first_import_tip(self, first_import: bool) -> str:
        """The one thing worth saying once the list stops being empty."""
        if not first_import:
            return ""
        edit = self._format_shortcut(self.settings.shortcuts.edit_selected_command)
        return f"\n\nType a few letters and press Enter to open a command. {edit} on a result edits it, including its aliases."

    def reset_all_run_counts(self):
        """Clear every command's run count after confirmation."""
        tracked = sum(1 for count in self.cmd_manager.run_counts.values() if count > 0)
        if tracked == 0:
            with self._dialog_open():
                QMessageBox.information(self, "Clear Usage History", "No commands have been opened yet.")
            return

        message_box = QMessageBox(self)
        message_box.setWindowTitle("Clear Usage History")
        message_box.setIcon(QMessageBox.Icon.Question)
        message_box.setText(f"Forget how often {tracked} command{'s have' if tracked != 1 else ' has'} been opened?")
        message_box.setInformativeText("\"Most used first\" will start over from what you open next. This cannot be undone.")
        reset_button = message_box.addButton("Clear", QMessageBox.ButtonRole.DestructiveRole)
        message_box.addButton(QMessageBox.StandardButton.Cancel)
        with self._dialog_open():
            message_box.exec()
        if message_box.clickedButton() is not reset_button:
            return

        try:
            self.cmd_manager.reset_all_run_counts()
        except OSError as error:
            self._show_save_error("Clear Usage History", error)
            return
        if self.user_text:
            self.cmd_manager.get_matching_commands(self, self.user_text)

    def open_editor(self, command):
        """Swap the search view for the command editor panel."""
        from .command_editor import CommandEditorPanel

        self._editor_return_center = self.frameGeometry().center()
        panel = CommandEditorPanel(command, self.icon_manager, self.cmd_manager, self)
        panel.closed.connect(self.close_editor)
        panel.layoutChanged.connect(self._fit_editor_panel)
        self._editor_panel = panel
        self._pre_editor_size = self.size()
        # Size the window before the editor page is shown, with painting held
        # off for the switch, so the editor never flashes at the search size.
        self.setUpdatesEnabled(False)
        try:
            self.view_stack.addWidget(panel)
            self._fit_editor_panel()
            self.view_stack.setCurrentWidget(panel)
        finally:
            self.setUpdatesEnabled(True)
        # Paint the new page now rather than on the next event-loop pass, so
        # the compositor has as little time as possible to show the old one.
        self.repaint()
        panel.command_name_edit_box.setFocus()

    def _fit_editor_panel(self, settle=True):
        """Pin the window to the editor size, but never below what the panel
        needs right now: switching to the URL type adds a row and alias chips
        wrap onto new lines, and a fixed height that is too short squeezes
        the fields until they are clipped.

        The layout applies the change on the next pass through the event
        loop, so one more measurement is taken after that pass in case a
        row wrapped differently once real geometry was in place.
        """
        panel = self._editor_panel
        if panel is None:
            return
        width = self.settings.ui.program_width
        editor_size = clamp_size_to_screen(
            width,
            max(self.settings.ui.editor_height, panel.needed_height(width)),
            available_geometry_for(self),
        )
        if editor_size != self.size():
            pin_within_screen(self, editor_size, self._editor_return_center)
        if settle:
            QTimer.singleShot(0, lambda: self._fit_editor_panel(settle=False))

    def close_editor(self):
        """Return to the search view after the editor saves in the background."""
        panel = self._editor_panel
        if panel is None:
            return
        self._editor_panel = None
        # Pin back to the search-view size. The stack keeps the editor's large
        # size hint, so releasing the constraint would let the window re-expand;
        # the search view height is constant, so a fixed size is safe here.
        # Painting is held across the page switch and the resize together.
        self.setUpdatesEnabled(False)
        try:
            self.view_stack.setCurrentWidget(self.central_widget)
            self.view_stack.removeWidget(panel)
            pin_within_screen(self, self._search_view_size, getattr(self, "_editor_return_center", None))
        finally:
            self.setUpdatesEnabled(True)
        self.repaint()
        panel.deleteLater()
        if hasattr(self, "_editor_return_center"):
            del self._editor_return_center
        # Refresh results so any edits show immediately.
        if self.user_text:
            self.cmd_manager.get_matching_commands(self, self.user_text)
        else:
            self._clear_and_hide_results()
        self.search_input_widget.setFocus()

    def get_selected_row(self) -> "ResultRow | None":
        item = self.results_list_widget.currentItem()
        if item is None:
            return None
        widget = self.results_list_widget.itemWidget(item)
        return cast(ResultRow, widget) if widget is not None else None

    def get_selected_command(self):
        row = self.get_selected_row()
        return row.command_name if row is not None else None

    def keyPressEvent(self, event):
        count = self.results_list_widget.count()
        row = self.results_list_widget.currentRow()

        if event.key() == Qt.Key.Key_Escape:
            self.hide_launcher()
        elif event.key() == Qt.Key.Key_Down and count > 0:
            self.results_list_widget.setCurrentRow((row + 1) % count)
            self._follow_selection()
        elif event.key() == Qt.Key.Key_Up and count > 0:
            self.results_list_widget.setCurrentRow((row - 1) % count)
            self._follow_selection()
        else:
            super().keyPressEvent(event)

    def _clear_and_hide_results(self):
        """Blank search box: show only the search bar, no results list.

        The one exception is a launcher with no commands at all. Rather than
        an empty box, it shows a single row offering to scan installed
        programs, so the offer appears in context when the user opens Dash
        instead of as an unprompted dialog at startup.
        """
        self.results_list_widget.clear()
        if not self.cmd_manager.has_user_commands():
            self._show_empty_state()
            return
        self.results_list_widget.hide()
        self._sync_search_view_size()

    def _show_empty_state(self):
        self.results_list_widget.show()
        self._sync_search_view_size()
        row = ResultRow(
            self,
            icon_path=self.settings.paths.program_icon,
            command="Find recommended commands",
            description="No commands added yet",
            action=self.choose_recent_programs,
        )
        self._add_row(row)
        # Settings is always there, so it is offered here too: it is where
        # the hotkey, the display and the import tools live.
        for command in self.cmd_manager.commands.values():
            if command.get("type") != "system":
                continue
            row = ResultRow(
                self,
                icon_path=self.icon_manager.get_icon_path(command),
                command=command["name"],
                description=command.get("description", "") if self.settings.search.show_descriptions else "",
            )
            self._add_row(row)
        self.results_list_widget.setCurrentRow(0)
        self._snap_results_height()

    def _description_for(self, command: dict) -> str:
        if not self.settings.search.show_descriptions:
            return ""
        description = str(command.get("description") or "")
        if not description and command.get("type") == "group":
            # "Opens: Mail, Calendar and 2 more" says what a bare group is.
            description = self.cmd_manager.group_summary(command["name"])
        return description

    def show_results(self, results):
        self.results_list_widget.show()
        self.results_list_widget.clear()
        self._sync_search_view_size()

        if not results:
            text = self.search_input_widget.text()
            if not self._show_calculation(text):
                self._show_no_results(text)
            self._snap_results_height()
            return

        # Show regular command results
        for result in results:
            icon_path = self.icon_manager.get_icon_path(result)
            description = self._description_for(result)
            query = result.get("_query")
            if query is not None:
                search_row = self._search_row(result, query)
                self._add_row(search_row)
                continue
            # Only user commands carry a counter, and only once they have been
            # used: a "0" on every row is noise rather than information.
            run_counter = ""
            times_executed = int(result.get("times_executed", 0))
            if self.settings.search.show_run_counter and result.get("type") != "system" and times_executed > 0:
                run_counter = "1 run" if times_executed == 1 else f"{times_executed} runs"
            result_widget = ResultRow(
                self,
                icon_path=icon_path,
                command=result["name"],
                description=description,
                run_counter=run_counter,
                editable=result.get("type") != "system" and self.settings.search.show_edit_button,
                # A site search: the selected row says Tab starts one.
                search_hint=result.get("type") == "url" and is_search_link(result.get("location", "")),
            )
            self._add_row(result_widget)
        self.results_list_widget.setCurrentRow(0)
        self._snap_results_height()

    def _snap_results_height(self):
        """Size the results list to whole rows, within the configured height.

        The box is as tall as the rows it holds, so a single match no longer
        leaves an empty void beneath it. Only the bottom edge moves: the window
        is pinned from its top left, so the search box stays put while typing.
        Rows are as tall as the configured fonts need (see _row_height), so
        a larger font means fewer, whole rows rather than clipped ones.
        """
        item = self.results_list_widget.item(0)
        if item is None:
            return
        row_height = item.sizeHint().height()
        if row_height <= 0:
            return
        padding = 2 * RESULTS_LIST_PADDING_V
        # Nearest whole number of rows to the configured height, never fewer than one.
        rows = max(1, round((self.settings.ui.results_height - padding) / row_height))
        # ...and never more rows than there are results to put in them.
        rows = min(rows, self.results_list_widget.count())
        height = rows * row_height + padding
        if height == self._results_list_height:
            return
        self._results_list_height = height
        self.results_list_widget.setFixedSize(QSize(self.settings.ui.program_width, height))
        self.search_tree_widget.setFixedSize(
            self._search_tree_width(),
            self._search_height() + height,
        )
        self._sync_search_view_size()

    def show_about(self):
        """Show about dialog"""
        version = current_version()

        about_box = QMessageBox()
        about_box.setWindowTitle("About Dash")
        about_box.setIconPixmap(QPixmap(self.settings.paths.program_icon).scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio))
        about_box.setText("<h2>Dash</h2>")
        keys = "\n".join(self._keys_help_lines())
        about_box.setInformativeText(
            f"Version {version}\n\n"
            "A quick command launcher for Windows.\n\n"
            "Keys:\n"
            f"{keys}\n\n"
            f"© 2025-{QDate.currentDate().year()} Calem Young"
        )
        with self._dialog_open():
            about_box.exec()


class ResultRow(QWidget):
    ICON_SIZE = 28  # glyph inside the tile
    ICON_TILE_SIZE = 42  # rounded tile drawn behind every icon (see #ResultIconTile)

    def __init__(
        self,
        main_window: MainWindow,
        icon_path,
        command,
        description,
        run_counter="",
        command_name=None,
        editable=False,
        copy_value: str | None = None,
        action=None,
        query: str | None = None,
        icon_pixmap: QPixmap | None = None,
        search_hint: bool = False,
    ):
        # Every widget in a row is created with its parent set. A parentless
        # QWidget is a top-level window, and Qt creates a native window for it
        # (with a title-bar helper window on Windows) as soon as it is styled,
        # before the layout or setItemWidget() reparents it. That cost ~45ms
        # per row, flashed a window on screen for every keystroke, and leaked
        # the helper windows for the life of the process.
        super().__init__(main_window.results_list_widget.viewport())

        # Logical name used to activate the row (defaults to the display text).
        self.command_name = command if command_name is None else command_name
        # Set for calculator rows: activating the row copies this to the clipboard.
        self.copy_value = copy_value
        # Set for informational rows that do something other than run a command.
        self.action = action
        # Set for a search keyword row: the text typed after the keyword.
        self.query = query

        # Create the main horizontal layout
        row_layout = QHBoxLayout(self)
        row_layout.setContentsMargins(4, 3, 8, 3)
        row_layout.setSpacing(8)

        # Icon on a uniform rounded tile, so icons from different sources
        # (extracted exe icons, favicons, bundled glyphs) read as one set.
        self.icon_label = QLabel(self)
        self.icon_label.setObjectName("ResultIconTile")
        pixmap = icon_pixmap if icon_pixmap is not None and not icon_pixmap.isNull() else QPixmap(icon_path)
        self.icon_label.setPixmap(
            pixmap.scaled(
                self.ICON_SIZE,
                self.ICON_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self.icon_label.setFixedSize(self.ICON_TILE_SIZE, self.ICON_TILE_SIZE)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Command label (bold, larger)
        self.command_label = ElidedLabel(command, self)
        self.command_label.setObjectName("ResultCommandLabel")
        command_font = self.command_label.font()
        command_font.setPointSize(main_window.settings.ui.result_font_size)
        self.command_label.setFont(command_font)
        self.command_label.setStyleSheet(_text_style(main_window.settings.ui.result_text_color))

        # Description label (gray, smaller)
        self.description_label = ElidedLabel(description, self)
        self.description_label.setObjectName("ResultDescriptionLabel")
        description_font = self.description_label.font()
        description_font.setPointSize(main_window.settings.ui.description_font_size)
        self.description_label.setFont(description_font)
        self.description_label.setStyleSheet(_text_style(main_window.settings.ui.description_text_color))

        # Run counter: a small pill on the right edge, styled in style.qss
        self.run_counter_label = QLabel(run_counter, self)
        self.run_counter_label.setObjectName("ResultRunCounterLabel")
        self.run_counter_label.setFont(description_font)
        self.run_counter_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.run_counter_label.setVisible(bool(run_counter))

        # Text layout (vertical - command above description). It takes all the
        # width left over by the icon, pill and pencil; the labels elide to fit.
        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)
        text_layout.addWidget(self.command_label)
        text_layout.addWidget(self.description_label)

        row_layout.addWidget(self.icon_label)
        row_layout.addLayout(text_layout, 1)

        # "Tab to search" on a site search command, shown only while the row
        # is selected so the list does not repeat it on every such row.
        self.search_hint_label = None
        if search_hint:
            self.search_hint_label = QLabel("Tab to search", self)
            self.search_hint_label.setObjectName("ResultSearchHint")
            self.search_hint_label.setFont(description_font)
            self.search_hint_label.setStyleSheet(f"color: {theme.color_name('text_hint')};")
            self.search_hint_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.search_hint_label.hide()
            row_layout.addWidget(self.search_hint_label, 0, Qt.AlignmentFlag.AlignVCenter)

        row_layout.addWidget(self.run_counter_label, 0, Qt.AlignmentFlag.AlignVCenter)

        self.main_window = main_window
        # Keep the right edge straight: while edit buttons are shown, rows
        # without one reserve the same width so counters line up in one column.
        # With edit buttons hidden, counters sit against the right edge.
        if not editable and main_window.settings.search.show_edit_button:
            row_layout.addSpacing(30)
        if editable:
            self.edit_button = QPushButton(self)
            self.edit_button.setObjectName("ResultEditButton")
            self.edit_button.setIcon(QIcon(glyph_pixmap(OutlineIcon.PENCIL, 18, theme.color("text_muted"))))
            self.edit_button.setIconSize(QSize(18, 18))
            self.edit_button.setFixedSize(30, 30)
            self.edit_button.setToolTip("Edit command")
            self.edit_button.setAccessibleName(f"Edit {self.command_name}")
            # The keyboard has the edit shortcut; tabbing stays in the search box.
            self.edit_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.edit_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.edit_button.clicked.connect(lambda: self.main_window.open_selected_command_editor_by_name(self.command_name))
            row_layout.addWidget(self.edit_button)

    def set_selected(self, selected: bool):
        if self.property("selected") == selected:
            return
        self.setProperty("selected", selected)
        if self.search_hint_label is not None:
            self.search_hint_label.setVisible(selected)
        for widget in (self.icon_label, self.run_counter_label):
            style = widget.style()
            if style is not None:
                style.unpolish(widget)
                style.polish(widget)

    def mousePressEvent(self, a0: QMouseEvent | None) -> None:
        if a0 is not None and a0.button() == Qt.MouseButton.RightButton:
            # Select it for the menu that follows; never open it.
            results = self.main_window.results_list_widget
            for index in range(results.count()):
                if results.itemWidget(results.item(index)) is self:
                    results.setCurrentRow(index)
                    break
            a0.accept()
            return
        if a0 is None or a0.button() == Qt.MouseButton.LeftButton:
            self.main_window.activate_row(self)
        return super().mousePressEvent(a0)

    def contextMenuEvent(self, a0) -> None:
        if a0 is None:
            return
        self.main_window.show_row_menu(self, a0.globalPos())
        a0.accept()
