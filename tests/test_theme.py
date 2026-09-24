"""The light and dark themes: choosing one, rendering style.qss, and contrast."""
import sys
import unittest
from pathlib import Path
from unittest import mock

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

from src import theme  # noqa: E402
from src.settings import Settings  # noqa: E402

STYLE_TEMPLATE = (Path(__file__).resolve().parent.parent / "style.qss").read_text(encoding="utf-8")

# (foreground, background, minimum ratio). Backgrounds that are translucent
# are composited over the surface they sit on (third element of the tuple).
TEXT = 4.5
LARGE_OR_UI = 3.0
KEY_PAIRS = [
    # Body text on every surface it appears on
    ("text", "window_bg", TEXT),
    ("text", "panel_bg", TEXT),
    ("text", "raised_bg", TEXT),
    ("text", "raised_hover_bg", TEXT),
    ("text", "input_bg", TEXT),
    ("text", "button_bg", TEXT),
    ("text", "button_hover_bg", TEXT),
    ("text", "tooltip_bg", TEXT),
    ("text", "list_hover_bg", TEXT),
    ("text", "list_selected_bg", TEXT),
    ("text", "chip_bg", TEXT),
    ("text", "chip_hover_bg", TEXT),
    ("text_strong", "row_selected_bg", TEXT),
    ("text_muted", "window_bg", TEXT),
    ("text_muted", "panel_bg", TEXT),
    ("text_muted", "raised_bg", TEXT),
    ("text_muted", "input_bg", TEXT),
    ("text_muted", "list_hover_bg", TEXT),
    ("text_subtle", "input_bg", TEXT),
    ("text_subtle", "button_bg", TEXT),
    ("text_hint", "panel_bg", TEXT),
    ("text_hover", "panel_bg", TEXT),
    # Accents used as text, and text on accents
    ("accent", "panel_bg", TEXT),
    ("accent", "raised_bg", TEXT),
    ("accent_hover", "panel_bg", TEXT),
    ("accent_text", "accent", TEXT),
    ("accent_text", "accent_hover", TEXT),
    ("accent_strong_text", "accent_strong", LARGE_OR_UI),  # selected text in the 24pt search box
    ("danger", "panel_bg", TEXT),
    ("error_text", "input_bg", TEXT),
    ("destructive", "panel_bg", TEXT),
    # Launcher
    ("search_text", "search_bg", TEXT),
    ("search_text", "search_focus_bg", TEXT),
    ("search_text", "search_edit_bg", TEXT),
    ("search_placeholder", "search_bg", TEXT),
    ("clock_day_text", "search_bg", TEXT),
    ("clock_date_text", "search_bg", TEXT),
    ("clock_date_text", "search_edit_bg", TEXT),
    ("result_text", "results_bg", TEXT),
    ("result_text", "results_edit_bg", TEXT),
    ("result_text", "row_hover_bg", TEXT),
    ("result_text", "row_selected_bg", TEXT),
    ("description_text", "results_bg", TEXT),
    ("description_text", "results_edit_bg", TEXT),
    ("description_text", "row_hover_bg", TEXT),
    ("description_text", "row_selected_bg", TEXT),
    ("pill_text", "tile_bg", TEXT),
    ("pill_selected_text", "tile_selected_bg", TEXT),
    ("launcher_muted", "footer_bg", TEXT),
    ("tree_text", "tree_bg", TEXT),
    ("tree_muted", "tree_bg", TEXT),
    ("tree_accent", "tree_bg", TEXT),
    ("tree_ok", "tree_bg", TEXT),
    ("tree_error", "tree_bg", TEXT),
    ("alias_placeholder", "alias_grid_bg", TEXT),
    # Focus rings and controls that must be findable
    ("accent", "input_bg", LARGE_OR_UI),
    ("accent_strong", "panel_bg", LARGE_OR_UI),
    ("check_border", "panel_bg", LARGE_OR_UI),
    ("scroll_handle", "scroll_track", LARGE_OR_UI),
]
# Field borders are only held to 3:1 in the light theme: the dark theme
# keeps its long-standing low-key borders, and its fields differ from the
# panel by their fill.
LIGHT_ONLY_PAIRS = [
    ("input_border", "input_bg", LARGE_OR_UI),
    ("input_border", "panel_bg", LARGE_OR_UI),
    ("alias_grid_border", "alias_grid_bg", LARGE_OR_UI),
]


class PaletteTests(unittest.TestCase):
    def test_both_palettes_define_the_same_tokens(self):
        self.assertEqual(set(theme.DARK), set(theme.LIGHT))

    def test_every_value_is_a_color(self):
        for name, palette in theme.PALETTES.items():
            for token, value in palette.items():
                with self.subTest(theme=name, token=token):
                    self.assertTrue(theme.to_qcolor(value).isValid(), value)

    def test_key_pairs_meet_wcag_aa(self):
        for name, palette in theme.PALETTES.items():
            pairs = KEY_PAIRS + (LIGHT_ONLY_PAIRS if name == "light" else [])
            for foreground, background, minimum in pairs:
                with self.subTest(theme=name, pair=(foreground, background)):
                    back = theme.to_qcolor(palette[background])
                    if back.alpha() < 255:
                        back = theme.blend(back, palette["panel_bg"])
                    ratio = theme.contrast_ratio(theme.to_qcolor(palette[foreground]), back)
                    self.assertGreaterEqual(ratio, minimum, f"{foreground} on {background}: {ratio:.2f}")

    def test_contrast_ratio_matches_the_wcag_reference_values(self):
        self.assertAlmostEqual(theme.contrast_ratio("#000000", "#ffffff"), 21.0, places=2)
        self.assertAlmostEqual(theme.contrast_ratio("#777777", "#ffffff"), 4.48, places=2)
        self.assertAlmostEqual(theme.contrast_ratio("#ffffff", "#ffffff"), 1.0, places=2)

    def test_rgba_values_parse_with_their_alpha(self):
        color = theme.to_qcolor("rgba(122, 162, 247, 0.04)")
        self.assertEqual((color.red(), color.green(), color.blue()), (122, 162, 247))
        self.assertAlmostEqual(color.alphaF(), 0.04, places=2)


class RenderTests(unittest.TestCase):
    def test_the_template_uses_only_known_tokens(self):
        self.assertTrue(theme.template_tokens(STYLE_TEMPLATE))
        self.assertLessEqual(theme.template_tokens(STYLE_TEMPLATE), set(theme.DARK))

    def test_rendering_leaves_no_placeholders_in_either_theme(self):
        for name, palette in theme.PALETTES.items():
            with self.subTest(theme=name):
                rendered = theme.render_stylesheet(STYLE_TEMPLATE, palette)
                self.assertNotIn("{{", rendered)
                self.assertNotIn("}}", rendered)
                self.assertIn(palette["panel_bg"], rendered)

    def test_the_dark_theme_keeps_the_look_dash_shipped_with(self):
        rendered = theme.render_stylesheet(STYLE_TEMPLATE, theme.DARK)
        self.assertIn("background-color: #202228", rendered)
        self.assertIn("background-color: #24272e", rendered)

    def test_the_template_holds_no_literal_colors(self):
        import re

        self.assertEqual(re.findall(r"#[0-9a-fA-F]{6}\b|rgba?\(", STYLE_TEMPLATE), [])

    def test_a_missing_token_falls_back_to_the_dark_value(self):
        with self.assertLogs("src.theme", level="WARNING"):
            rendered = theme.render_stylesheet("a { color: {{text}}; }", {})
        self.assertEqual(rendered, f"a {{ color: {theme.DARK['text']}; }}")


class ResolveTests(unittest.TestCase):
    def tearDown(self):
        theme.reset_for_tests()

    def test_a_fixed_choice_is_used_as_it_is(self):
        self.assertEqual(theme.resolve_theme("light"), "light")
        self.assertEqual(theme.resolve_theme("Dark"), "dark")

    def test_system_follows_the_reported_scheme(self):
        with mock.patch.object(theme, "system_scheme", return_value="light"):
            self.assertEqual(theme.resolve_theme("system"), "light")
        with mock.patch.object(theme, "system_scheme", return_value="dark"):
            self.assertEqual(theme.resolve_theme("system"), "dark")

    def test_system_defaults_to_dark_when_nothing_can_tell(self):
        with mock.patch.object(theme, "system_scheme", return_value=None):
            self.assertEqual(theme.resolve_theme("system"), "dark")
            self.assertEqual(theme.resolve_theme("nonsense"), "dark")

    def test_the_registry_is_the_fallback_when_qt_cannot_tell(self):
        from PyQt6.QtCore import Qt

        hints = mock.Mock()
        hints.colorScheme.return_value = Qt.ColorScheme.Unknown
        with mock.patch.object(theme.QGuiApplication, "styleHints", return_value=hints):
            with mock.patch.object(theme, "_registry_prefers_light", return_value=True):
                self.assertEqual(theme.system_scheme(), "light")
            with mock.patch.object(theme, "_registry_prefers_light", return_value=False):
                self.assertEqual(theme.system_scheme(), "dark")
            with mock.patch.object(theme, "_registry_prefers_light", return_value=None):
                self.assertIsNone(theme.system_scheme())

    def test_high_contrast_takes_the_system_palette(self):
        qpalette = QPalette()
        qpalette.setColor(QPalette.ColorRole.Window, QColor("#000000"))
        qpalette.setColor(QPalette.ColorRole.WindowText, QColor("#ffff00"))
        palette = theme.palette_from_qpalette(qpalette)
        self.assertEqual(set(palette), set(theme.DARK))
        self.assertEqual(palette["panel_bg"], "#000000")
        self.assertEqual(palette["text"], "#ffff00")
        with mock.patch.object(theme, "is_high_contrast", return_value=True):
            name, _ = theme.palette_for("light")
        self.assertEqual(name, theme.HIGH_CONTRAST)

    def test_apply_theme_sets_the_sheet_and_tells_listeners(self):
        settings = Settings()
        settings.ui.theme = "light"
        target = mock.Mock()
        heard = []
        theme.on_theme_changed(heard.append)
        with mock.patch.object(theme, "is_high_contrast", return_value=False):
            name = theme.apply_theme(target, settings, "a { color: {{text}}; }")
        self.assertEqual(name, "light")
        target.setStyleSheet.assert_called_once_with(f"a {{ color: {theme.LIGHT['text']}; }}")
        self.assertEqual(heard, ["light"])
        self.assertEqual(theme.color("text"), QColor(theme.LIGHT["text"]))

        # Saving Settings re-applies with the remembered template.
        settings.ui.theme = "dark"
        with mock.patch.object(theme, "is_high_contrast", return_value=False):
            theme.apply_theme(target, settings)
        self.assertEqual(target.setStyleSheet.call_args.args[0], f"a {{ color: {theme.DARK['text']}; }}")
        self.assertEqual(heard, ["light", "dark"])

    def test_a_system_change_is_ignored_when_the_theme_is_fixed(self):
        settings = Settings()
        settings.ui.theme = "dark"
        target = mock.Mock()
        with mock.patch.object(theme, "is_high_contrast", return_value=False):
            theme.apply_theme(target, settings, "x")
            target.setStyleSheet.reset_mock()
            theme._on_system_scheme_changed()
        target.setStyleSheet.assert_not_called()




class HighContrastTests(unittest.TestCase):
    def test_selected_rows_stay_readable_in_both_high_contrast_schemes(self):
        for window, text, highlight in (("#000000", "#ffffff", "#1aebff"), ("#ffffff", "#000000", "#37006e")):
            qpalette = QPalette()
            qpalette.setColor(QPalette.ColorRole.Window, QColor(window))
            qpalette.setColor(QPalette.ColorRole.WindowText, QColor(text))
            qpalette.setColor(QPalette.ColorRole.Highlight, QColor(highlight))
            palette = theme.palette_from_qpalette(qpalette)
            with self.subTest(window=window):
                self.assertNotEqual(palette["row_selected_bg"], window)
                self.assertGreaterEqual(theme.contrast_ratio(palette["result_text"], palette["row_selected_bg"]), 7.0)


if __name__ == "__main__":
    unittest.main()
