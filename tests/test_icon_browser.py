"""The icon studio: theme colors, keyboard use and accessible names."""
import sys
import unittest
from unittest import mock

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QColor, QKeyEvent, QPixmap
from PyQt6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

from src import icon_browser, theme  # noqa: E402
from src.icon_browser import (  # noqa: E402
    ClickableLabel,
    IconStyleDialog,
    LibraryPickerDialog,
    OptionCard,
    OutlineIcon,
    checkerboard,
    placeholder_pixmap,
)


def press(widget, key, modifiers=Qt.KeyboardModifier.NoModifier):
    QApplication.sendEvent(widget, QKeyEvent(QEvent.Type.KeyPress, key, modifiers))


def use_palette(name):
    palette = theme.PALETTES[name]
    theme._state.update(palette=palette, name=name)
    theme.notifier().changed.emit(name)


class ThemeColorTests(unittest.TestCase):
    def tearDown(self):
        theme.reset_for_tests()

    def test_the_checker_is_drawn_in_the_active_theme(self):
        for name in ("dark", "light"):
            use_palette(name)
            image = checkerboard(20, cell=10).toImage()
            self.assertEqual(image.pixelColor(0, 0), QColor(theme.PALETTES[name]["checker_light"]), name)
            self.assertEqual(image.pixelColor(15, 5), QColor(theme.PALETTES[name]["checker_dark"]), name)

    def test_the_empty_state_uses_the_panel_color(self):
        use_palette("light")
        image = placeholder_pixmap(64).toImage()
        self.assertEqual(image.pixelColor(1, 1), QColor(theme.LIGHT["panel_bg"]))

    def test_option_cards_redraw_their_glyph_when_the_theme_changes(self):
        with mock.patch.object(icon_browser, "glyph_pixmap", return_value=QPixmap()) as glyph:
            card = OptionCard(OutlineIcon.FOLDER, "Replace from disk", "Load a file", compact=True)
            self.assertEqual(glyph.call_args.args[2], QColor(theme.DARK["text_muted"]))
            use_palette("light")
            self.assertEqual(glyph.call_args.args[2], QColor(theme.LIGHT["text_muted"]))
        self.assertEqual(card.accessibleName(), "Replace from disk")

    def test_baked_icon_colors_do_not_follow_the_theme(self):
        use_palette("light")
        self.assertEqual(icon_browser.DEFAULT_ICON_COLOR, QColor("#7aa2f7"))
        self.assertEqual(icon_browser.DEFAULT_BG_COLOR, QColor("#282b32"))


class KeyboardTests(unittest.TestCase):
    def test_a_clickable_label_answers_enter_once_focusable(self):
        label = ClickableLabel()
        clicked = mock.Mock()
        label.clicked.connect(clicked)
        press(label, Qt.Key.Key_Return)
        clicked.assert_not_called()
        label.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        press(label, Qt.Key.Key_Space)
        clicked.assert_called_once()

    def test_the_color_pickers_move_with_the_arrow_keys(self):
        base = QPixmap(32, 32)
        base.fill(QColor("black"))
        dialog = IconStyleDialog(base, "x", QColor("#808080"), None)
        square = dialog.icon_picker._square
        hue_bar = dialog.icon_picker._hue_bar
        value = square.val()
        press(square, Qt.Key.Key_Up)
        self.assertGreater(square.val(), value)
        self.assertFalse(dialog.original_colors.isChecked(), "a keyboard change counts as picking a color")
        hue = hue_bar.hue()
        press(hue_bar, Qt.Key.Key_Down)
        self.assertGreater(hue_bar.hue(), hue)
        for widget in (square, hue_bar, dialog.icon_hex, dialog.bg_hex, dialog.preview):
            self.assertTrue(widget.accessibleName(), widget)
        self.assertIn("Icon color", square.accessibleName())


class LibraryPickerTests(unittest.TestCase):
    def test_icons_have_accessible_text_and_enter_picks_one(self):
        entries = [("ARROW_LEFT", "outline", OutlineIcon.ARROW_LEFT), ("STAR", "outline", OutlineIcon.STAR)]
        picker = LibraryPickerDialog(entries)
        self.assertEqual(picker.search_box.accessibleName(), "Search icons")
        self.assertEqual(picker.list_widget.accessibleName(), "Icon library")
        item = picker.list_widget.item(0)
        self.assertEqual(item.data(Qt.ItemDataRole.AccessibleTextRole), "arrow left (outline)")
        picker.search_box.setText("star")
        self.assertTrue(picker.eventFilter(picker.search_box, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier)))
        self.assertIs(picker.list_widget.currentItem(), picker.list_widget.item(1))
        picker.list_widget.itemActivated.emit(picker.list_widget.currentItem())
        self.assertEqual(picker.selection()[0], "STAR")


if __name__ == "__main__":
    unittest.main()
