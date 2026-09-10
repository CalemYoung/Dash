from PyQt6.QtCore import QEasingCurve, QPointF, QRectF, QSize, Qt, QThread, QTimer, QPropertyAnimation, QUrl
from PyQt6.QtWidgets import QMainWindow, QLineEdit, QVBoxLayout, QHBoxLayout, QWidget, QStackedWidget, QPushButton, QSizePolicy
from PyQt6.QtWidgets import QListWidget, QMessageBox, QSystemTrayIcon, QMenu, QApplication, QErrorMessage, QLabel, QListWidgetItem
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QFileDialog, QProgressDialog
from PyQt6.QtGui import QBrush, QIcon, QAction, QDesktopServices, QMouseEvent, QPainter, QPen, QPixmap, QCursor, QScreen, QKeySequence, QShortcut, QColor
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from .settings import Settings
from .settings_editor import ExportCommandsDialog, ImportCommandsDialog, ProgramImportDialog, SettingsEditorPanel
from .window_placement import available_geometry_for, clamp_size_to_screen, move_within_screen, pin_within_screen
from .icon_manager import IconManager
from .command_trie import TrieSnapshot
from typing import cast
from .calculator import eval_expression
from .icon_browser import glyph_pixmap, OutlineIcon
from .version import current_version, is_newer_version
from .keys import format_shortcut, key_sequences
from .updater import ReleaseInfo, UpdateDownloader, is_installed_build, launch_installer, parse_release
import os
import sys
from pathlib import Path
import win32gui
import win32con
import datetime
import pyperclip

import win32process
import win32api


LATEST_RELEASE_API = "https://api.github.com/repos/CalemYoung/Dash/releases/latest"
LATEST_RELEASE_PAGE = "https://github.com/CalemYoung/Dash/releases/latest"

# Windows may not have the notification area ready when Dash starts at login.
TRAY_SETUP_RETRIES = 20
TRAY_SETUP_RETRY_MS = 3000

# Vertical padding of #ResultsList in style.qss; the list height is sized to
# whole rows so the last visible row is never cut through its text.
RESULTS_LIST_PADDING_V = 4


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

            from .installed_programs import discover_recent_program_commands

            self.candidates = discover_recent_program_commands(days=365)
        except Exception as error:
            self.error_message = str(error)
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
        accent = QColor("#5b9cff")
        text = QColor("#e7e9ee")
        muted = QColor("#7d8490")
        line = QColor("#464d59")
        error = QColor("#ff776d")
        terminal = QColor("#3fb950")  # green: what was typed is exactly a command

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

        painter.setPen(QPen(QColor("#69717e"), max(1.0, 1.2 * scale)))
        painter.setBrush(QBrush(QColor("#303640")))
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
            active_fill = QColor("#57363a")
        elif snapshot.is_terminal:
            active_color = terminal
            active_fill = QColor("#274a33")
        else:
            active_color = accent
            active_fill = QColor("#293c57")

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
            painter.setBrush(QBrush(QColor("#293c57")))
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
            painter.setBrush(QBrush(QColor("#303640")))
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
        self.cmd_manager.reprocess_command_icons(self.icon_manager)
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

    @staticmethod
    def _format_shortcut(shortcut):
        return format_shortcut(shortcut)

    def _shortcut_hint_text(self):
        return "  |  ".join(
            (
                f"New: {self._format_shortcut(self.settings.shortcuts.new_command)}",
                f"Edit: {self._format_shortcut(self.settings.shortcuts.edit_selected_command)}",
            )
        )

    def _setup_window(self):
        self.setWindowTitle("Dash")
        self.setWindowIcon(QIcon(self.settings.paths.default_command_icon))
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowOpacity(self.settings.ui.window_opacity)

    def _setup_widgets(self):
        # 1: Create main widget
        self.central_widget = QWidget()
        self.central_widget.setObjectName("MainWidget")
        self._layout_scale = max(0.8, min(1.4, self.settings.ui.program_width / 500))
        self._layout_margin = max(10, round(10 * self._layout_scale))
        self._layout_spacing = max(10, round(10 * self._layout_scale))

        # 2.1: Create overall search box container widget -> contains text input + multiline info widgets
        self.search_container_widget = QWidget()
        self.search_container_widget.setFixedSize(QSize(self.settings.ui.program_width, self.settings.ui.search_height))

        # 2.2: Create search box input widget for user searching
        self.search_input_widget = QLineEdit()
        self.search_input_widget.setObjectName("SearchInput")
        self.search_input_widget.setPlaceholderText("Type a command...")
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
        self._apply_clock_text_style()

        # 3: Create results widget
        self._results_list_height = self.settings.ui.results_height
        self.results_list_widget = QListWidget()
        self.results_list_widget.setObjectName("ResultsList")
        self.results_list_widget.setFixedSize(QSize(self.settings.ui.program_width, self._results_list_height))
        self.results_list_widget.currentItemChanged.connect(self._on_current_result_changed)
        # Arrow keys and the wheel still scroll; the bar itself only cluttered the inset cards.
        self.results_list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results_list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results_list_widget.hide()
        self.search_tree_widget = SearchTreeWidget(self._layout_scale)
        self.search_tree_widget.setFixedSize(
            self._search_tree_width(),
            self.settings.ui.search_height + self._results_list_height,
        )
        self.search_tree_widget.hide()
        self.shortcut_hint_label = QLabel(self._shortcut_hint_text())
        self.shortcut_hint_label.setObjectName("ShortcutHint")
        self.shortcut_hint_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.footer_height = max(24, round(24 * self._layout_scale))
        self._search_view_size = QSize(
            self.settings.ui.program_width,
            self.settings.ui.search_height + self.footer_height,
        )

    def _setup_layout(self):
        date_info_layout = QVBoxLayout()
        date_info_layout.setContentsMargins(0, 0, 0, 0)
        date_info_layout.setSpacing(0)
        date_info_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        date_info_layout.addWidget(self.date_info_day_label)
        date_info_layout.addWidget(self.date_info_date_label)
        self.date_info_widget.setLayout(date_info_layout)

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
        self.footer_widget = QWidget()
        self.footer_widget.setObjectName("LauncherFooter")
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

    def _sync_search_view_size(self):
        tree_width = 0 if self.search_tree_widget.isHidden() else self.search_tree_widget.width()
        results_height = 0 if self.results_list_widget.isHidden() else self._results_list_height
        self._search_view_size = QSize(
            self.settings.ui.program_width + tree_width,
            self.settings.ui.search_height + results_height + self.footer_height,
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

    def _apply_search_text_style(self):
        self.search_input_widget.setStyleSheet(
            _text_style(self.settings.ui.search_text_color, self.settings.ui.search_font_size)
        )

    def _apply_clock_text_style(self):
        day_size = self.settings.ui.clock_font_size
        date_size = max(6, day_size - 1)
        self.date_info_day_label.setStyleSheet(_text_style(self.settings.ui.clock_day_text_color, day_size))
        self.date_info_date_label.setStyleSheet(_text_style(self.settings.ui.clock_date_text_color, date_size))
        self.date_info_day_label.setFixedHeight(self.date_info_day_label.fontMetrics().height() + 2)
        self.date_info_date_label.setFixedHeight(self.date_info_date_label.fontMetrics().height() + 2)
        text_width = max(
            self.date_info_day_label.fontMetrics().horizontalAdvance("Wednesday"),
            self.date_info_date_label.fontMetrics().horizontalAdvance("30 September"),
        )
        self.date_info_widget.setFixedWidth(max(round(88 * self._layout_scale), text_width + 12))

    def eventFilter(self, obj, event):
        """Catch key presses on the input box"""
        if obj == self.search_input_widget and event.type() == event.Type.KeyPress:
            # Check if user is deleting
            if event.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
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

    def activate_launcher(self):
        was_visible = self.isVisible()
        todays_date = datetime.datetime.now()
        self.date_info_day_label.setText(todays_date.strftime("%A"))
        self.date_info_date_label.setText(todays_date.strftime("%d %B"))

        # Opening on an empty box: normally nothing to show, but a launcher
        # with no commands yet surfaces its offer to scan for programs here.
        if not self.search_input_widget.text():
            self._clear_and_hide_results()

        self.adjustSize()
        self._move_to_configured_screen()
        self.show()
        if was_visible:
            self.raise_()
            self.activateWindow()
            QTimer.singleShot(100, self._force_focus)

    def _force_focus(self):
        """Force focus on the window with proper Windows API handling"""
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
            print(f"Warning: Could not force focus: {e}")
            # Fallback to Qt methods
            self.raise_()
            self.activateWindow()
            self.search_input_widget.setFocus()

    def _target_screen(self) -> QScreen | None:
        """The display the launcher should open on, per the launcher_screen setting.

        "mouse": the display under the pointer. "primary": the primary display.
        Anything else is a display name as shown in Settings; if that display
        is not connected right now, fall back to the primary one rather than
        wherever the window happened to be last.
        """
        choice = str(self.settings.general.launcher_screen or "mouse").strip()
        primary = QApplication.primaryScreen()
        if choice == "mouse":
            return QApplication.screenAt(QCursor.pos()) or primary
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
        return primary

    def _move_to_configured_screen(self):
        screen = self._target_screen()
        if screen is None:
            return
        area = screen.availableGeometry()
        move_within_screen(self, self.frameGeometry().size(), area.center(), area)

    def showEvent(self, event):
        super().showEvent(event)
        self.raise_()
        self.activateWindow()
        QTimer.singleShot(100, self._force_focus)

    def _setup_tray(self):
        """Setup system tray with retry logic for Windows startup"""
        self.tray = self.setup_tray_icon()

        # Verify tray icon is visible, retry if not
        if not self.tray.isVisible() and self._tray_retry_count < TRAY_SETUP_RETRIES:
            self._tray_retry_count += 1
            print(f"Tray icon not visible, retrying... ({self._tray_retry_count}/{TRAY_SETUP_RETRIES})")
            QTimer.singleShot(TRAY_SETUP_RETRY_MS, self._setup_tray)

    def setup_tray_icon(self):
        """Create and configure system tray icon with menu"""
        tray = QSystemTrayIcon()
        tray.setIcon(QIcon(self.settings.paths.program_icon))
        tray.setVisible(True)

        # Create context menu (right-click) - store as instance variable
        self.tray_menu = QMenu()

        # Keep the tray menu focused on app-level actions; feature workflows are
        # available from the launcher and settings editor.
        install_location_action = QAction("Open Install Location", self)
        install_location_action.triggered.connect(self.open_install_location)
        self.tray_menu.addAction(install_location_action)

        config_folder_action = QAction("Open Config Folder", self)
        config_folder_action.triggered.connect(self.open_config_folder)
        self.tray_menu.addAction(config_folder_action)

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

        # Left click - show Dash
        tray.activated.connect(lambda reason: self.activate_launcher() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        return tray

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
            if manual:
                QMessageBox.warning(self, "Check for Updates", f"Dash could not check for updates.\n\n{error}")
            reply.deleteLater()
            return

        reply.deleteLater()
        if not is_newer_version(release.tag, installed_version):
            if manual:
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
        print(f"Update failed: {message}")
        QMessageBox.warning(self, "Updating Dash", f"The update could not be installed.\n\n{message}")

    def _on_update_downloaded(self, installer_path: str):
        self._update_downloader = None
        self._close_download_progress()
        self._install_now(installer_path)

    def _install_now(self, installer_path: str):
        if not Path(installer_path).exists():
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
            QMessageBox.warning(self, "Updating Dash", "The installer could not be started.")
            return
        app = QApplication.instance()
        if app is not None:
            QTimer.singleShot(300, app.quit)

    def set_hotkey_listener(self, listener):
        self._hotkey_listener = listener

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

    def open_settings_editor(self):
        settings_path = self._settings_path()
        previous_center = self.frameGeometry().center()
        self._editor_return_center = previous_center
        panel = SettingsEditorPanel(self.settings, settings_path, self)
        panel.closed.connect(self.close_editor)
        panel.saved.connect(self._apply_settings)
        panel.importProgramsRequested.connect(self.choose_recent_programs)
        panel.exportCommandsRequested.connect(self.export_commands)
        panel.importCommandsRequested.connect(self.import_commands)
        panel.resetRunCountsRequested.connect(self.reset_all_run_counts)
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

    def _apply_settings(self, settings):
        self.settings = settings
        self.setWindowOpacity(settings.ui.window_opacity)
        self.icon_manager.settings = settings
        # The command manager reads sort order and case handling from its own
        # settings reference, so hand it the new object and rebuild the trie.
        self.cmd_manager.settings = settings
        self.cmd_manager.reload_command_trie()
        if self._hotkey_listener is not None:
            self._hotkey_listener.update_hotkey(settings.general.hotkey)
        self.edit_shortcut.setKeys(_key_sequences(settings.shortcuts.edit_selected_command))
        self.new_command_shortcut.setKeys(_key_sequences(settings.shortcuts.new_command))
        self._apply_search_text_style()
        self._layout_scale = max(0.8, min(1.4, settings.ui.program_width / 500))
        self._layout_margin = max(10, round(10 * self._layout_scale))
        self._layout_spacing = max(10, round(10 * self._layout_scale))
        self._apply_clock_text_style()
        self.footer_height = max(24, round(24 * self._layout_scale))
        self.footer_widget.setFixedHeight(self.footer_height)
        search_layout = self.search_container_widget.layout()
        search_layout.setContentsMargins(self._layout_margin, 0, self._layout_margin, 0)
        search_layout.setSpacing(self._layout_spacing)
        footer_layout = self.footer_widget.layout()
        footer_layout.setContentsMargins(self._layout_margin, 0, self._layout_margin, 0)
        footer_layout.setSpacing(self._layout_spacing)
        self.search_container_widget.setFixedSize(QSize(settings.ui.program_width, settings.ui.search_height))
        self._results_list_height = settings.ui.results_height  # re-snapped by the next show_results
        self.results_list_widget.setFixedSize(QSize(settings.ui.program_width, self._results_list_height))
        self.search_tree_widget.set_scale(self._layout_scale)
        self.search_tree_widget.setFixedSize(
            self._search_tree_width(),
            settings.ui.search_height + self._results_list_height,
        )
        if self.user_text:
            self._update_search_tree(self.user_text)
            self.cmd_manager.get_matching_commands(self, self.user_text)
        else:
            self._update_search_tree("")
            self._clear_and_hide_results()
        self.shortcut_hint_label.setText(self._shortcut_hint_text())

    def open_config_folder(self):
        """Open the folder holding settings.toml and commands.toml in Explorer."""
        config_path = self.cmd_manager.command_file_path.parent

        if config_path.exists():
            os.startfile(config_path)
        else:
            self.display_error_popup(f"Config folder not found at {config_path}")

    def open_install_location(self):
        """Open the directory containing the installed executable."""
        install_path = Path(sys.executable).parent if hasattr(sys, "_MEIPASS") else Path(__file__).parent.parent

        if install_path.exists():
            os.startfile(install_path)
        else:
            self.display_error_popup(f"Install location not found at {install_path}")

    def display_error_popup(self, text):
        error_dialog = QErrorMessage(self)
        error_dialog.showMessage(text)
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

    def _try_apply_suggestion(self, suggestion):
        """Apply autocomplete suggestion if it matches user input"""
        if not suggestion.lower().startswith(self.user_text.lower()):
            self.current_suggestion = ""
            return

        completion = suggestion[len(self.user_text) :].lower()

        self.search_input_widget.blockSignals(True)
        self.search_input_widget.setText(self.user_text + completion)
        self.search_input_widget.setSelection(len(self.user_text), len(completion))
        self.search_input_widget.blockSignals(False)

        self.current_suggestion = suggestion

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
            pyperclip.copy(row.copy_value)
            print(f"Result: {row.copy_value} (copied to clipboard)")
            self.hide_launcher()
            return

        cmd = self.cmd_manager.commands.get(row.command_name)
        if cmd is None:
            # "No results found" and similar informational rows: Enter just cancels.
            self.hide_launcher()
            return

        error = self.cmd_manager.execute_command(self, row.command_name)
        if error:
            self._show_launch_failure(cmd, error)
            return

        # Opening settings swaps in an in-window panel; hiding the launcher
        # right after would hide that panel too, so leave the window shown.
        opens_panel = cmd.get("type") == "system" and cmd.get("action") == "open_settings"
        if not opens_panel:
            self.hide_launcher()

    def _show_launch_failure(self, cmd: dict, reason: str):
        """A command could not be launched: say why and offer to fix it.

        The launcher stays open so the user is not left staring at the
        desktop wondering what happened.
        """
        message_box = QMessageBox(self)
        message_box.setWindowTitle("Dash")
        message_box.setIcon(QMessageBox.Icon.Warning)
        message_box.setText(f"Dash couldn't open {cmd.get('name', 'this command')}.")
        message_box.setInformativeText(f"{reason}\n\nThe target may have been moved, renamed or uninstalled.")
        edit_button = None
        if cmd.get("type") != "system":
            edit_button = message_box.addButton("Edit Command", QMessageBox.ButtonRole.AcceptRole)
        message_box.addButton(QMessageBox.StandardButton.Close)
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
        if hasattr(self, "view_stack") and self.view_stack.currentWidget() is not self.central_widget:
            return
        if not self.isVisible():
            self.activate_launcher()
        self.open_editor(None)

    def choose_recent_programs(self):
        if self._program_discovery_thread is not None:
            return

        progress = QProgressDialog(self)
        progress.setWindowTitle("Auto-Populate Commands")
        progress.setLabelText("Scanning installed programs...")
        progress.setRange(0, 0)
        progress.setCancelButton(None)
        progress.setMinimumDuration(0)
        progress.setWindowModality(Qt.WindowModality.WindowModal)

        thread = ProgramDiscoveryThread(self)
        thread.finished.connect(self._on_program_discovery_finished)
        self._program_discovery_progress = progress
        self._program_discovery_thread = thread
        progress.show()
        thread.start()

    def _on_program_discovery_finished(self):
        thread = self._program_discovery_thread
        if thread is None:
            return
        self._program_discovery_thread = None

        progress = self._program_discovery_progress
        self._program_discovery_progress = None
        if progress is not None:
            progress.close()
            progress.deleteLater()

        candidates = thread.candidates
        error_message = thread.error_message
        thread.deleteLater()
        if error_message:
            self.display_error_popup(f"Could not scan installed programs: {error_message}")
            return

        self._show_program_import_dialog(candidates)

    def _show_program_import_dialog(self, candidates: list[dict]):
        from .installed_programs import discover_windows_suggestions, filter_new_program_commands, merge_program_candidates

        # Suggestions lead: they are the handful of entries most people want,
        # and they would be lost partway down a list of installed programs.
        # Cheap enough to resolve here rather than on the scan thread.
        candidates = merge_program_candidates(discover_windows_suggestions(), candidates)
        existing_locations = self.cmd_manager.existing_command_locations()
        candidates = filter_new_program_commands(candidates, existing_locations)
        dialog = ProgramImportDialog(candidates, existing_locations, self.icon_manager, self, command_manager=self.cmd_manager)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return

        summary = self.cmd_manager.import_program_commands(dialog.selected_candidates())
        if summary["imported"]:
            self.cmd_manager.reprocess_command_icons(self.icon_manager)
        imported_count = len(summary["imported"])
        skipped_count = len(summary["skipped"])
        message = f"Imported {imported_count} installed program command"
        if imported_count != 1:
            message += "s"
        if skipped_count:
            message += f". Skipped {skipped_count} already-present or conflicting candidate"
            if skipped_count != 1:
                message += "s"
        QMessageBox.information(self, "Add Installed Programs", message + ".")

        if self.user_text:
            self.cmd_manager.get_matching_commands(self, self.user_text)
        else:
            self._clear_and_hide_results()

    def export_commands(self):
        user_commands = [c for c in self.cmd_manager.commands.values() if c.get("type") != "system"]
        dialog = ExportCommandsDialog(user_commands, self.icon_manager, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return

        selected_names = dialog.selected_names()
        if not selected_names:
            return

        default_path = str(Path.home() / "dash_commands.toml")
        file_path, _ = QFileDialog.getSaveFileName(self, "Export Commands", default_path, "TOML Files (*.toml)")
        if not file_path:
            return

        count = self.cmd_manager.export_commands(selected_names, Path(file_path))
        QMessageBox.information(self, "Export Commands", f"Exported {count} command(s) to {file_path}.")

    def import_commands(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Import Commands", "", "TOML Files (*.toml)")
        if not file_path:
            return

        try:
            candidates = self.cmd_manager.parse_import_candidates(Path(file_path))
        except Exception as e:
            self.display_error_popup(f"Could not read commands file: {e}")
            return

        dialog = ImportCommandsDialog(candidates, self.cmd_manager, self.icon_manager, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return

        selected = dialog.selected_candidates()
        if not selected:
            return

        summary = self.cmd_manager.import_commands(selected)
        if summary["imported"]:
            self.cmd_manager.reprocess_command_icons(self.icon_manager)
        imported_count = len(summary["imported"])
        skipped_count = len(summary["skipped"])
        message = f"Imported {imported_count} command"
        if imported_count != 1:
            message += "s"
        if skipped_count:
            message += f". Skipped {skipped_count} invalid or conflicting command"
            if skipped_count != 1:
                message += "s"
        QMessageBox.information(self, "Import Commands", message + ".")

        if self.user_text:
            self.cmd_manager.get_matching_commands(self, self.user_text)
        else:
            self._clear_and_hide_results()

    def reset_all_run_counts(self):
        """Clear every command's run count after confirmation."""
        tracked = sum(1 for count in self.cmd_manager.run_counts.values() if count > 0)
        if tracked == 0:
            QMessageBox.information(self, "Reset Run Counts", "No commands have been run yet.")
            return

        message_box = QMessageBox(self)
        message_box.setWindowTitle("Reset Run Counts")
        message_box.setIcon(QMessageBox.Icon.Question)
        message_box.setText(f"Reset the run count of {tracked} command{'s' if tracked != 1 else ''} to zero?")
        message_box.setInformativeText("Results sorted by popularity will start from scratch. This cannot be undone.")
        reset_button = message_box.addButton("Reset", QMessageBox.ButtonRole.DestructiveRole)
        message_box.addButton(QMessageBox.StandardButton.Cancel)
        message_box.exec()
        if message_box.clickedButton() is not reset_button:
            return

        self.cmd_manager.reset_all_run_counts()
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
        elif event.key() == Qt.Key.Key_Up and count > 0:
            self.results_list_widget.setCurrentRow((row - 1) % count)
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
        item = QListWidgetItem(self.results_list_widget)
        new_command = self._format_shortcut(self.settings.shortcuts.new_command)
        row = ResultRow(
            self,
            icon_path=self.settings.paths.program_icon,
            command="Add your installed programs",
            description=f"Nothing here yet. Press Enter to scan this PC, or {new_command} to add a command by hand.",
            action=self.choose_recent_programs,
        )
        item.setSizeHint(row.sizeHint())
        self.results_list_widget.setItemWidget(item, row)
        # Settings is always there, so it is offered here too: it is where
        # the hotkey, the display and the import tools live.
        for command in self.cmd_manager.commands.values():
            if command.get("type") != "system":
                continue
            item = QListWidgetItem(self.results_list_widget)
            row = ResultRow(
                self,
                icon_path=self.icon_manager.get_icon_path(command),
                command=command["name"],
                description=command.get("description", "") if self.settings.search.show_descriptions else "",
            )
            item.setSizeHint(row.sizeHint())
            self.results_list_widget.setItemWidget(item, row)
        self.results_list_widget.setCurrentRow(0)
        self._snap_results_height()

    def show_results(self, results):
        self.results_list_widget.show()
        self.results_list_widget.clear()
        self._sync_search_view_size()

        if not results:
            # Try calculator fallback
            try:
                result = eval_expression(self.search_input_widget.text())
                # Show calculator result
                item = QListWidgetItem(self.results_list_widget)
                result_widget = ResultRow(
                    self,
                    icon_path=self.settings.paths.calculator_icon,
                    command=f"= {result}",
                    description="Calculator result (press Enter to copy)",
                    copy_value=str(result),
                )
                item.setSizeHint(result_widget.sizeHint())
                self.results_list_widget.setItemWidget(item, result_widget)
                self.results_list_widget.setCurrentRow(0)
                print(f"Calculator: {result}")
            except ValueError:
                # Show "No results" message using ResultRow
                item = QListWidgetItem(self.results_list_widget)
                no_results_widget = ResultRow(
                    self,
                    icon_path=self.settings.paths.no_result_icon,
                    command="No results found",
                    description="(press Enter to cancel)",
                )
                item.setSizeHint(no_results_widget.sizeHint())
                self.results_list_widget.setItemWidget(item, no_results_widget)
                self.results_list_widget.setCurrentRow(0)
            self._snap_results_height()
            return

        # Show regular command results
        for result in results:
            item = QListWidgetItem(self.results_list_widget)
            icon_path = self.icon_manager.get_icon_path(result)
            description = result["description"] if self.settings.search.show_descriptions else ""
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
            )
            item.setSizeHint(result_widget.sizeHint())
            self.results_list_widget.setItemWidget(item, result_widget)
        self.results_list_widget.setCurrentRow(0)
        self._snap_results_height()

    def _snap_results_height(self):
        """Size the results list to whole rows, within the configured height.

        The box is as tall as the rows it holds, so a single match no longer
        leaves an empty void beneath it. Only the bottom edge moves: the window
        is pinned from its top left, so the search box stays put while typing.
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
            self.settings.ui.search_height + height,
        )
        self._sync_search_view_size()

    def show_about(self):
        """Show about dialog"""
        version = current_version()

        about_box = QMessageBox()
        about_box.setWindowTitle("About Dash")
        about_box.setIconPixmap(QPixmap(self.settings.paths.program_icon).scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio))
        about_box.setText("<h2>Dash</h2>")
        hotkey = self._format_shortcut(self.settings.general.hotkey)
        new_command = self._format_shortcut(self.settings.shortcuts.new_command)
        edit_command = self._format_shortcut(self.settings.shortcuts.edit_selected_command)
        about_box.setInformativeText(
            f"Version {version}\n\n"
            "A quick command launcher for Windows.\n\n"
            "Keyboard shortcuts:\n"
            f"{hotkey}  Open Dash\n"
            f"{new_command}  New command\n"
            f"{edit_command}  Edit selected command\n\n"
            "© 2025 Calem Young"
        )
        about_box.exec()


class ElidedLabel(QLabel):
    """Single-line label that trims long text with an ellipsis instead of
    forcing the row wider or being clipped mid-word."""

    def __init__(self, text, parent):
        super().__init__(text, parent)
        self._full_text = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)

    def setText(self, text):
        self._full_text = text
        self._apply_elision()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_elision()

    def _apply_elision(self):
        available = max(0, self.width())
        elided = self.fontMetrics().elidedText(self._full_text, Qt.TextElideMode.ElideRight, available)
        if elided != super().text():
            super().setText(elided)
        self.setToolTip(self._full_text if elided != self._full_text else "")


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

        # Create the main horizontal layout
        row_layout = QHBoxLayout(self)
        row_layout.setContentsMargins(4, 3, 8, 3)
        row_layout.setSpacing(8)

        # Icon on a uniform rounded tile, so icons from different sources
        # (extracted exe icons, favicons, bundled glyphs) read as one set.
        self.icon_label = QLabel(self)
        self.icon_label.setObjectName("ResultIconTile")
        pixmap = QPixmap(icon_path)
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
        row_layout.addWidget(self.run_counter_label, 0, Qt.AlignmentFlag.AlignVCenter)

        self.main_window = main_window
        # Keep the right edge straight: rows without an edit button reserve the
        # same width so counters line up in one column.
        if not editable:
            row_layout.addSpacing(30)
        if editable:
            self.edit_button = QPushButton(self)
            self.edit_button.setObjectName("ResultEditButton")
            self.edit_button.setIcon(QIcon(glyph_pixmap(OutlineIcon.PENCIL, 18, QColor("#8b929e"))))
            self.edit_button.setIconSize(QSize(18, 18))
            self.edit_button.setFixedSize(30, 30)
            self.edit_button.setToolTip("Edit command")
            self.edit_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.edit_button.clicked.connect(lambda: self.main_window.open_selected_command_editor_by_name(self.command_name))
            row_layout.addWidget(self.edit_button)

    def set_selected(self, selected: bool):
        if self.property("selected") == selected:
            return
        self.setProperty("selected", selected)
        for widget in (self.icon_label, self.run_counter_label):
            style = widget.style()
            if style is not None:
                style.unpolish(widget)
                style.polish(widget)

    def mousePressEvent(self, a0: QMouseEvent | None) -> None:
        self.main_window.activate_row(self)
        return super().mousePressEvent(a0)