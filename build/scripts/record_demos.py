"""Record the README's demo GIFs by driving the real launcher.

The frames are the app itself: a MainWindow built on a throwaway config, typed
into with real key events, grabbed after each one. Nothing is mocked up, so a
GIF can never show a UI that no longer exists -- re-run this after changing the
launcher and the demos follow.

    .\\.venv\\Scripts\\python.exe build\\scripts\\record_demos.py

Takes the scene names to record (search, site, group, editor, tree) or none for
all of them, and writes each one into assets/.
"""

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

# Twice the logical size, so the GIFs stay crisp where the README scales them
# down. The app still lays itself out at 1x -- scaling Qt itself would halve
# the screen it thinks it has, and the editor would be squeezed to fit it.
SCALE = 2
# Frames are one size, but the window is not: it grows a row at a time as
# results appear. The space around it is left transparent so a short frame
# reads as a short window rather than as an empty box, whatever colour the
# page behind it is.
TRANSPARENT_INDEX = 255
PAD = 8 * SCALE

WORKDIR = Path(os.environ.get("TEMP", ".")) / "dash-demo-recording"

# Commands with recipe icons: a library glyph and colours render the same on
# any machine, with no favicon to download and no app that has to be installed.
DEMO_COMMANDS = """
[[command]]
name = "GitHub"
aliases = ["gh"]
location = "https://github.com/search?q={query}"
description = "Opens github.com"
type = "url"
icon_glyph = "outline:BRAND_GITHUB"
icon_color = "#f3f4f7"
icon_background = "#2f3444"

[[command]]
name = "Gmail"
aliases = ["mail"]
location = "https://mail.google.com/"
description = "Opens mail.google.com"
type = "url"
icon_glyph = "outline:BRAND_GMAIL"
icon_color = "#ea4335"
icon_background = "#3a2a2a"

[[command]]
name = "Spotify"
aliases = []
location = "https://open.spotify.com/"
description = "Opens open.spotify.com"
type = "url"
icon_glyph = "outline:BRAND_SPOTIFY"
icon_color = "#1ed760"
icon_background = "#1e2b23"

[[command]]
name = "Slack"
aliases = []
location = "https://app.slack.com/"
description = "Opens app.slack.com"
type = "url"
icon_glyph = "outline:BRAND_SLACK"
icon_color = "#e01e5a"
icon_background = "#38242c"

[[command]]
name = "Figma"
aliases = []
location = "https://www.figma.com/files"
description = "Opens figma.com"
type = "url"
icon_glyph = "outline:BRAND_FIGMA"
icon_color = "#a259ff"
icon_background = "#2e2740"

[[command]]
name = "Screenshots"
aliases = []
location = "~/Pictures/Screenshots"
description = "Opens the Screenshots folder"
type = "file"
icon_glyph = "outline:FOLDER"
icon_color = "#7aa2f7"
icon_background = "#242c3d"

[[command]]
name = "Steam"
aliases = []
location = "https://store.steampowered.com/"
description = "Opens store.steampowered.com"
type = "url"
icon_glyph = "outline:BRAND_STEAM"
icon_color = "#66c0f4"
icon_background = "#23303f"

[[command]]
name = "Start work"
aliases = []
location = ""
description = "Opens Slack, Spotify and Screenshots"
type = "group"
targets = ["Slack", "Spotify", "Screenshots"]
icon_glyph = "outline:STACK_2"
icon_color = "#f3f4f7"
icon_background = "#4a3f66"
"""


def prepare_environment():
    """Point the app at a throwaway profile before anything imports it.

    The icon store lives under APPDATA, and the app writes rendered icons into
    it on startup: without this the demo's commands would overwrite the icons
    of the real ones that happen to share a name.
    """
    if WORKDIR.exists():
        shutil.rmtree(WORKDIR, ignore_errors=True)
    (WORKDIR / "appdata").mkdir(parents=True, exist_ok=True)
    os.environ["APPDATA"] = str(WORKDIR / "appdata")
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))


def write_config(show_command_tree=False):
    config = WORKDIR / "config"
    config.mkdir(parents=True, exist_ok=True)
    settings_text = (ROOT / "config/settings.default.toml").read_text(encoding="utf-8")
    # Nothing that reaches the network or the tray during a recording.
    settings_text = settings_text.replace("check_updates_on_startup = true", "check_updates_on_startup = false")
    if show_command_tree:
        settings_text = settings_text.replace("show_command_tree = false", "show_command_tree = true")
    settings_path = config / f"settings{'-tree' if show_command_tree else ''}.toml"
    settings_path.write_text(settings_text, encoding="utf-8")
    commands_path = config / "commands.toml"
    commands_path.write_text(DEMO_COMMANDS, encoding="utf-8")
    return settings_path, commands_path


class Recorder:
    """Drives one launcher window and collects (image, duration) frames."""

    def __init__(self, app, window):
        self._app = app
        self._window = window
        self.frames = []

    # -- time ------------------------------------------------------------- #

    def pump(self, ms=60):
        """Let the app run for a while: layouts settle on the next pass and
        the editor sizes itself from a single-shot timer."""
        from PyQt6.QtCore import QEventLoop

        deadline = time.monotonic() + ms / 1000
        while time.monotonic() < deadline:
            self._app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 10)
            time.sleep(0.005)

    # -- frames ------------------------------------------------------------ #

    def frame(self, hold=140, settle=40):
        """Render the window at SCALE times its size and keep it as a frame."""
        from PIL import Image
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QPixmap

        self.pump(settle)
        pixmap = QPixmap(self._window.size() * SCALE)
        pixmap.setDevicePixelRatio(SCALE)
        # The launcher is a translucent frameless window: its rounded corners
        # have to stay transparent for the backdrop to show through cleanly.
        pixmap.fill(Qt.GlobalColor.transparent)
        self._window.render(pixmap)
        image = pixmap.toImage()
        buffer = image.constBits()
        buffer.setsize(image.sizeInBytes())
        frame = Image.frombuffer(
            "RGBA", (image.width(), image.height()), bytes(buffer), "raw", "BGRA", image.bytesPerLine(), 1
        )
        self.frames.append([frame, hold])

    def hold(self, ms):
        """Linger on what is already on screen."""
        if self.frames:
            self.frames[-1][1] += ms

    # -- input -------------------------------------------------------------- #

    def type(self, widget, text, per_char=130):
        from PyQt6.QtTest import QTest

        for character in text:
            QTest.keyClicks(widget, character)
            self.frame(per_char)

    def enter_text(self, widget, text, hold=900):
        """Type without a frame per keystroke, for setting a scene up."""
        from PyQt6.QtTest import QTest

        QTest.keyClicks(widget, text)
        self.frame(hold)

    def press(self, widget, key, modifier=None, hold=260):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest

        QTest.keyClick(widget, key, modifier or Qt.KeyboardModifier.NoModifier)
        self.frame(hold)

    def clear(self, widget, hold=220):
        widget.clear()
        self.frame(hold)


def save_gif(frames, path, colors=255):
    """Write the frames as a GIF sized to the largest of them, with whatever
    the window does not cover left transparent.

    One palette is built from every frame, so the colours do not shift as the
    window grows, and its last index is kept for the transparent area.
    """
    from PIL import Image

    width = max(frame.width for frame, _ in frames) + PAD * 2
    height = max(frame.height for frame, _ in frames) + PAD * 2

    padded = []
    for frame, _ in frames:
        # Pinned to the top left: the search box stays put while the results
        # list and the tree panel grow down and to the right.
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        canvas.paste(frame, (PAD, PAD))
        padded.append(canvas)

    strip = Image.new("RGB", (width, height * len(padded)))
    for index, canvas in enumerate(padded):
        strip.paste(canvas.convert("RGB"), (0, index * height))
    palette = strip.quantize(colors=colors)

    quantized = []
    for canvas in padded:
        # The window's rounded corners fade out over a pixel or two, and a GIF
        # is either transparent or not: anything half covered stays painted.
        clear = canvas.getchannel("A").point(lambda alpha: 255 if alpha < 128 else 0)
        frame = canvas.convert("RGB").quantize(palette=palette, dither=Image.Dither.NONE)
        frame.paste(TRANSPARENT_INDEX, clear)
        quantized.append(frame)

    quantized[0].save(
        path,
        save_all=True,
        append_images=quantized[1:],
        duration=[duration for _, duration in frames],
        loop=0,
        # Left unoptimized on purpose: the optimizer crops frames to what
        # changed and drops their transparency flag, which leaves a renderer
        # free to paint the area around a short window rather than clear it.
        optimize=False,
        transparency=TRANSPARENT_INDEX,
        # Clear back to transparent between frames, so the taller frames do
        # not leave their results behind under the shorter ones.
        disposal=2,
    )
    print(f"{path.name}: {len(frames)} frames, {path.stat().st_size / 1024:.0f} KB, {width}x{height}")


def launcher(app, show_command_tree=False):
    from src.command_manager import CommandManager
    from src.launcher_gui import MainWindow
    from src.settings import Settings

    settings_path, commands_path = write_config(show_command_tree)
    settings = Settings.load_or_create_default(settings_path)
    cmd_manager = CommandManager(commands_path, settings=settings)
    window = MainWindow(cmd_manager, settings, settings_path=settings_path)
    window.activate_launcher()
    return window


# ---------------------------------------------------------------- the scenes


def scene_search(app):
    """Prefix match, then an alias, then a site search, then a calculation."""
    window = launcher(app)
    search = window.search_input_widget
    recorder = Recorder(app, window)

    recorder.frame(700)
    recorder.type(search, "sp")
    recorder.hold(1100)

    recorder.clear(search)
    recorder.type(search, "gh")
    recorder.hold(1400)

    recorder.clear(search)
    # Typed in one go: a half-written expression shows "No results found",
    # which reads as a glitch rather than as the point being made.
    recorder.enter_text(search, "12*8", hold=2200)

    window.close()
    return recorder.frames


def scene_site_search(app):
    """A website command with {query}: the name opens it, a space searches it."""
    window = launcher(app)
    search = window.search_input_widget
    recorder = Recorder(app, window)

    recorder.type(search, "gh")
    recorder.hold(1200)

    # The space is what turns the command into a search of the site.
    recorder.type(search, " ", per_char=200)
    recorder.hold(1400)

    recorder.type(search, "dash", per_char=160)
    recorder.hold(2000)

    window.close()
    return recorder.frames


def scene_group(app):
    """A group: one command whose targets are other commands."""
    window = launcher(app)
    search = window.search_input_widget
    recorder = Recorder(app, window)

    recorder.type(search, "start", per_char=160)
    recorder.hold(1800)

    window.open_editor(window.cmd_manager.find_command("Start work"))
    recorder.pump(500)
    recorder.frame(2600)

    window.close()
    return recorder.frames


def scene_editor(app):
    """Ctrl+Enter on a result opens it in place; an alias is added and saved."""
    from PyQt6.QtCore import Qt

    window = launcher(app)
    search = window.search_input_widget
    recorder = Recorder(app, window)

    recorder.enter_text(search, "gith", hold=1100)

    window.open_editor(window.cmd_manager.find_command("GitHub"))
    recorder.pump(400)
    recorder.frame(1300)

    panel = window._editor_panel
    alias_box = panel.alias_box.enter_box
    alias_box.setFocus()
    recorder.frame(500)
    recorder.type(alias_box, "hub", per_char=150)
    # The chip lands on a new line and the panel regrows: let it before the frame.
    recorder.press(alias_box, Qt.Key.Key_Return, hold=100)
    recorder.pump(500)
    recorder.frame(2000)

    window.close()
    return recorder.frames


def scene_tree(app):
    """The tree panel narrowing from one letter to one command."""
    window = launcher(app, show_command_tree=True)
    search = window.search_input_widget
    recorder = Recorder(app, window)

    recorder.type(search, "s", per_char=1400)
    recorder.type(search, "t", per_char=1400)
    recorder.type(search, "a", per_char=1000)
    recorder.hold(1600)

    window.close()
    return recorder.frames


SCENES = {
    "search": ("launcher-search.gif", scene_search),
    "site": ("site-search.gif", scene_site_search),
    "group": ("command-group.gif", scene_group),
    "editor": ("command-editor.gif", scene_editor),
    "tree": ("command-tree.gif", scene_tree),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenes", nargs="*", choices=[*SCENES, []], help="which demos to record (default: all)")
    args = parser.parse_args()
    wanted = args.scenes or list(SCENES)

    prepare_environment()

    from PyQt6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setStyleSheet((ROOT / "style.qss").read_text(encoding="utf-8"))

    for name in wanted:
        filename, scene = SCENES[name]
        frames = scene(app)
        save_gif(frames, ROOT / "assets" / filename)


if __name__ == "__main__":
    main()
