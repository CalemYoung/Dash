"""Load an icon from disk or from the Tabler library, then set its colours."""
import sys
import warnings
from pathlib import Path
from typing import cast

from PIL.Image import Image as PILImage
from PyQt6.QtCore import Qt, QSize, QPoint, QPointF, QRectF, QTimer, pyqtSignal
from PyQt6.QtGui import QPixmap, QIcon, QImage, QPainter, QColor, QLinearGradient, QPen
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QStackedWidget
from PyQt6.QtWidgets import QFrame, QDialog, QDialogButtonBox, QFileDialog, QLabel, QCheckBox, QPushButton
from PyQt6.QtSvg import QSvgRenderer

with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore",
        message="pkg_resources is deprecated as an API.*",
        category=UserWarning,
        module="pygame.pkgdata",
    )
    from pytablericons import TablerIcons, OutlineIcon, FilledIcon

ICON_SIZE = 28
CELL_SIZE = 48  # square item box; the grid adds the spacing around it
LIST_RENDER_SIZE = 56  # 2x the displayed size, so the picker grid stays crisp
WORK_RENDER_SIZE = 256  # master pixmap for the loaded icon, everything scales down from this
HOME_PREVIEW_SIZE = 200
DIALOG_PREVIEW_SIZE = 120
EXPORT_SIZE = 256

IMAGE_FILTER = "Images (*.svg *.png *.jpg *.jpeg *.webp *.bmp);;All files (*)"
DEFAULT_SUBTITLE = "Pick an icon, then set its colours"

# The rocket icon shown by default; the browser always opens with this loaded
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ICON_PATH = PROJECT_ROOT / "assets" / "icons" / "icon.png"

# Item data roles for the library picker grid
ROLE_MEMBER = Qt.ItemDataRole.UserRole
ROLE_NAME = Qt.ItemDataRole.UserRole + 1
ROLE_RENDERED = Qt.ItemDataRole.UserRole + 2

# Colours for the parts drawn with QPainter, which a stylesheet cannot reach.
# Everything else is styled in style.qss; keep these few in sync with it.
SURFACE = "#202228"
BORDER = "#3a414d"
MUTED = "#8b929e"
ACCENT = "#7aa2f7"
CHECKER_LIGHT = "#2a2e36"
CHECKER_DARK = "#202228"

DEFAULT_ICON_COLOR = QColor(ACCENT)
DEFAULT_BG_COLOR = QColor("#282b32")

STYLESHEET_FILE = "style.qss"


def load_stylesheet(path=None):
    """Read style.qss from beside this script."""
    path = Path(path) if path else PROJECT_ROOT / STYLESHEET_FILE
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        print(f"warning: could not read {path}, running unstyled", file=sys.stderr)
        return ""


# --------------------------------------------------------------------------- #
# pixmap helpers
# --------------------------------------------------------------------------- #

def render_icon(member, size):
    """Render a pytablericons member to a QPixmap, or None if it fails to load."""
    try:
        img = cast(PILImage, TablerIcons.load(member, size=size)).convert("RGBA")
    except Exception:
        return None
    # Straight from PIL to QImage, skipping a PNG encode/decode round trip.
    # The explicit stride keeps this correct for any width, and .copy() detaches
    # the QImage from the temporary bytes object.
    buffer = img.tobytes()
    qimage = QImage(buffer, img.width, img.height, img.width * 4, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(qimage.copy())


_LIBRARY_ICON_CACHE = {}


def library_members():
    """(name, variant, member) for every Tabler icon. Nothing is rendered here."""
    return [
        (member.name, variant_name, member)
        for variant_name, enum_cls in (("outline", OutlineIcon), ("filled", FilledIcon))
        for member in enum_cls
    ]


def library_icon(member):
    """Grid-sized QIcon for one library member, rendered once and kept."""
    if member in _LIBRARY_ICON_CACHE:
        return _LIBRARY_ICON_CACHE[member]
    pixmap = render_icon(member, LIST_RENDER_SIZE)
    icon = None if pixmap is None else QIcon(smooth_scale(pixmap, ICON_SIZE))
    _LIBRARY_ICON_CACHE[member] = icon
    return icon


def load_icon_file(path, size=WORK_RENDER_SIZE):
    """Load an SVG or raster image from disk as a square RGBA pixmap."""
    if path.lower().endswith(".svg"):
        if QSvgRenderer is None:
            return None
        renderer = QSvgRenderer(path)
        if not renderer.isValid():
            return None
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        # Fit the artwork to the square without distorting its aspect ratio
        default = renderer.defaultSize()
        if default.width() > 0 and default.height() > 0:
            scale = min(size / default.width(), size / default.height())
            width, height = default.width() * scale, default.height() * scale
        else:
            width = height = size
        painter = QPainter(pixmap)
        renderer.render(painter, QRectF((size - width) / 2, (size - height) / 2, width, height))
        painter.end()
        return pixmap

    pixmap = QPixmap(path)
    if pixmap.isNull():
        return None
    return smooth_scale(pixmap, size)


def smooth_scale(pixmap, size):
    return pixmap.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def extract_icon_colors(pixmap):
    """Detect an icon's foreground colour, optional background colour, and whether
    it is monochrome. Multi-coloured artwork must not be tinted, it would collapse
    to a flat silhouette."""
    if pixmap is None or pixmap.isNull():
        return DEFAULT_ICON_COLOR, None, True

    image = pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    w, h = image.width(), image.height()
    if w == 0 or h == 0:
        return DEFAULT_ICON_COLOR, None, True

    # Check 4 corners for a solid background
    corners = [
        image.pixelColor(0, 0),
        image.pixelColor(w - 1, 0),
        image.pixelColor(0, h - 1),
        image.pixelColor(w - 1, h - 1),
    ]
    has_bg = all(c.alpha() >= 200 for c in corners)
    bg_color = None
    if has_bg:
        c0 = corners[0]
        if all(
            abs(c.red() - c0.red()) < 15
            and abs(c.green() - c0.green()) < 15
            and abs(c.blue() - c0.blue()) < 15
            for c in corners
        ):
            bg_color = c0

    total_r = 0
    total_g = 0
    total_b = 0
    total_weight = 0
    samples = []

    step_x = max(1, w // 64)
    step_y = max(1, h // 64)

    for y in range(0, h, step_y):
        for x in range(0, w, step_x):
            c = image.pixelColor(x, y)
            alpha = c.alpha()
            if alpha < 50:
                continue
            if bg_color is not None:
                diff = (
                    abs(c.red() - bg_color.red())
                    + abs(c.green() - bg_color.green())
                    + abs(c.blue() - bg_color.blue())
                )
                if diff < 40:
                    continue
            weight = alpha
            total_r += c.red() * weight
            total_g += c.green() * weight
            total_b += c.blue() * weight
            total_weight += weight
            samples.append((c, weight))

    if total_weight > 0:
        icon_color = QColor(
            round(total_r / total_weight),
            round(total_g / total_weight),
            round(total_b / total_weight),
        )
    else:
        icon_color = DEFAULT_ICON_COLOR

    return icon_color, bg_color, is_monochrome(samples, icon_color, total_weight)


def is_monochrome(samples, mean_color, total_weight, threshold=28):
    """True when every sampled pixel sits close to the mean colour, i.e. the icon
    is a single-colour glyph that can safely be re-tinted."""
    if not samples or total_weight <= 0:
        return True
    spread = sum(
        weight
        * max(
            abs(c.red() - mean_color.red()),
            abs(c.green() - mean_color.green()),
            abs(c.blue() - mean_color.blue()),
        )
        for c, weight in samples
    )
    return spread / total_weight <= threshold


def strip_background(pixmap, bg_color, threshold=40):
    """Erase a solid baked-in background so only the artwork's own alpha remains.

    Without this, re-tinting an icon that was previously flattened with a
    background colour would recolour the whole opaque square instead of just
    the glyph, since tint_pixmap relies on the alpha channel to know what to
    paint.
    """
    if bg_color is None:
        return pixmap

    image = pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    w, h = image.width(), image.height()
    changed = False
    for y in range(h):
        for x in range(w):
            c = image.pixelColor(x, y)
            if c.alpha() == 0:
                continue
            diff = (
                abs(c.red() - bg_color.red())
                + abs(c.green() - bg_color.green())
                + abs(c.blue() - bg_color.blue())
            )
            if diff < threshold:
                image.setPixelColor(x, y, QColor(0, 0, 0, 0))
                changed = True
    return QPixmap.fromImage(image) if changed else pixmap


def tint_pixmap(pixmap, color):
    """Recolour artwork by scaling the chosen colour by each pixel's own
    luminance (relative to the brightest opaque pixel), rather than flat-
    filling the whole silhouette. A genuinely flat glyph still comes out as
    one solid colour, but shading (like a darker gear on a lighter body)
    stays visible instead of being erased.
    """
    image = pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    w, h = image.width(), image.height()
    out = QImage(image.size(), QImage.Format.Format_ARGB32)
    out.fill(Qt.GlobalColor.transparent)

    pixels = []
    max_luminance = 0.0
    for y in range(h):
        for x in range(w):
            c = image.pixelColor(x, y)
            alpha = c.alpha()
            if alpha == 0:
                continue
            luminance = (0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()) / 255.0
            pixels.append((x, y, alpha, luminance))
            if luminance > max_luminance:
                max_luminance = luminance

    r, g, b = color.red(), color.green(), color.blue()
    for x, y, alpha, luminance in pixels:
        relative = (luminance / max_luminance) if max_luminance > 0.02 else 1.0
        out.setPixelColor(
            x,
            y,
            QColor(round(r * relative), round(g * relative), round(b * relative), alpha),
        )
    return QPixmap.fromImage(out)


def checkerboard(size, cell=10):
    """Classic transparency checker, so 'no background' reads as transparent."""
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(CHECKER_LIGHT))
    painter = QPainter(pixmap)
    for y in range(0, size, cell):
        for x in range(0, size, cell):
            if (x // cell + y // cell) % 2:
                painter.fillRect(x, y, cell, cell, QColor(CHECKER_DARK))
    painter.end()
    return pixmap


def compose(base_pixmap, icon_color, bg_color, size, transparent_checker=True):
    """Tint an icon and place it on a background.

    A None icon_color keeps the artwork's own colours. With no background colour
    the result is genuinely transparent, unless transparent_checker is set, in
    which case a checker is drawn for display.
    """
    artwork = base_pixmap if icon_color is None else tint_pixmap(base_pixmap, icon_color)
    icon = smooth_scale(artwork, int(size * 0.72))
    if bg_color is not None:
        canvas = QPixmap(size, size)
        canvas.fill(bg_color)
    elif transparent_checker:
        canvas = checkerboard(size)
    else:
        canvas = QPixmap(size, size)
        canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.drawPixmap((size - icon.width()) // 2, (size - icon.height()) // 2, icon)
    painter.end()
    return canvas


def export_png(base_pixmap, icon_color, bg_color, path, size=EXPORT_SIZE):
    """Write the coloured icon to disk. No UI; call this from any automation."""
    composed = compose(base_pixmap, icon_color, bg_color, size, transparent_checker=False)
    return composed.save(path, "PNG")


def placeholder_pixmap(size):
    """Empty-state artwork for when nothing has been loaded yet."""
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(SURFACE))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(BORDER), 2, Qt.PenStyle.DashLine))
    painter.drawRoundedRect(8, 8, size - 16, size - 16, 14, 14)
    painter.setPen(QColor(MUTED))
    font = painter.font()
    font.setPointSize(11)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "No icon loaded")
    painter.end()
    return pixmap


def section_label(text):
    label = QLabel(text)
    label.setObjectName("sectionLabel")
    return label


class ClickableLabel(QLabel):
    """QLabel that reports left clicks."""

    clicked = pyqtSignal()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.clicked.emit()


def glyph_pixmap(member, size, color):
    """A single tinted Tabler glyph, for the home-screen option cards."""
    base = render_icon(member, size)
    if base is None:
        return QPixmap()
    return tint_pixmap(smooth_scale(base, size), color)


class OptionCard(QFrame):
    """A large, clickable way in: a glyph, a title and one line of context.

    Two of these carry the home screen, in place of two small buttons stranded
    in a lot of empty space.
    """

    clicked = pyqtSignal()

    def __init__(self, glyph_member, title, description, compact=False):
        super().__init__()
        self.setObjectName("optionCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        glyph = QLabel()
        glyph.setObjectName("optionGlyph")
        glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        glyph.setPixmap(glyph_pixmap(glyph_member, 24 if compact else 36, QColor(MUTED)))

        title_label = QLabel(title)
        title_label.setObjectName("optionTitle")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout = QVBoxLayout(self)
        if compact:
            layout.setContentsMargins(12, 10, 12, 10)
            layout.setSpacing(4)
        else:
            layout.setContentsMargins(16, 22, 16, 22)
            layout.setSpacing(10)
        layout.addStretch()
        layout.addWidget(glyph)
        layout.addWidget(title_label)

        # The one-line description reads as clutter on the compact cards
        if not compact:
            desc_label = QLabel(description)
            desc_label.setObjectName("optionDesc")
            desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            desc_label.setWordWrap(True)
            layout.addWidget(desc_label)
        layout.addStretch()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
        else:
            super().keyPressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.clicked.emit()


# --------------------------------------------------------------------------- #
# colour picker
# --------------------------------------------------------------------------- #

class _SVSquare(QWidget):
    """Saturation/value picker for a given hue."""

    svChanged = pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(200, 200)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._hue = 0.0
        self._sat = 0.0
        self._val = 1.0

    def setHue(self, hue):
        self._hue = hue
        self.update()

    def setSV(self, sat, val):
        self._sat, self._val = sat, val
        self.update()

    def sat(self):
        return self._sat

    def val(self):
        return self._val

    def paintEvent(self, event):
        painter = QPainter(self)
        rect = self.rect()
        painter.fillRect(rect, QColor.fromHsvF(self._hue, 1.0, 1.0))

        white_grad = QLinearGradient(QPointF(rect.topLeft()), QPointF(rect.topRight()))
        white_grad.setColorAt(0.0, QColor(255, 255, 255, 255))
        white_grad.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillRect(rect, white_grad)

        black_grad = QLinearGradient(QPointF(rect.topLeft()), QPointF(rect.bottomLeft()))
        black_grad.setColorAt(0.0, QColor(0, 0, 0, 0))
        black_grad.setColorAt(1.0, QColor(0, 0, 0, 255))
        painter.fillRect(rect, black_grad)

        x = int(self._sat * (rect.width() - 1))
        y = int((1 - self._val) * (rect.height() - 1))
        painter.setPen(QPen(Qt.GlobalColor.white, 2))
        painter.drawEllipse(QPoint(x, y), 6, 6)
        painter.setPen(QPen(Qt.GlobalColor.black, 1))
        painter.drawEllipse(QPoint(x, y), 6, 6)

    def _update_from_pos(self, pos):
        x = min(max(pos.x(), 0), self.width() - 1)
        y = min(max(pos.y(), 0), self.height() - 1)
        self._sat = x / max(self.width() - 1, 1)
        self._val = 1 - y / max(self.height() - 1, 1)
        self.update()
        self.svChanged.emit(self._sat, self._val)

    def mousePressEvent(self, event):
        self._update_from_pos(event.position().toPoint())

    def mouseMoveEvent(self, event):
        self._update_from_pos(event.position().toPoint())


class _HueBar(QWidget):
    """Vertical hue slider, 0..1."""

    hueChanged = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(24)
        self.setMinimumHeight(200)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self._hue = 0.0

    def setHue(self, hue):
        self._hue = hue
        self.update()

    def hue(self):
        return self._hue

    def paintEvent(self, event):
        painter = QPainter(self)
        rect = self.rect()
        grad = QLinearGradient(QPointF(rect.topLeft()), QPointF(rect.bottomLeft()))
        for i in range(7):
            grad.setColorAt(i / 6, QColor.fromHsvF(i / 6, 1.0, 1.0))
        painter.fillRect(rect, grad)
        y = int(self._hue * (rect.height() - 1))
        painter.setPen(QPen(Qt.GlobalColor.black, 2))
        painter.drawRect(0, max(0, y - 2), rect.width() - 1, 4)

    def _update_from_pos(self, pos):
        y = min(max(pos.y(), 0), self.height() - 1)
        self._hue = y / max(self.height() - 1, 1)
        self.update()
        self.hueChanged.emit(self._hue)

    def mousePressEvent(self, event):
        self._update_from_pos(event.position().toPoint())

    def mouseMoveEvent(self, event):
        self._update_from_pos(event.position().toPoint())


class ColorPickerWidget(QWidget):
    """Compact SV-square + hue-bar colour picker (native PyQt6, no external deps)."""

    colorChanged = pyqtSignal(QColor)

    def __init__(self, color=None, parent=None):
        super().__init__(parent)
        color = color or QColor(255, 0, 0)
        hue, sat, val, _ = color.getHsvF()
        hue = max(hue or 0.0, 0.0)

        self._square = _SVSquare()
        self._square.setHue(hue)
        self._square.setSV(sat, val)
        self._square.svChanged.connect(self._on_sv_changed)

        self._hue_bar = _HueBar()
        self._hue_bar.setHue(hue)
        self._hue_bar.hueChanged.connect(self._on_hue_changed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._square)
        layout.addWidget(self._hue_bar)

    def _on_sv_changed(self, _sat, _val):
        self.colorChanged.emit(self.currentColor())

    def _on_hue_changed(self, hue):
        self._square.setHue(hue)
        self.colorChanged.emit(self.currentColor())

    def currentColor(self):
        return QColor.fromHsvF(self._hue_bar.hue(), self._square.sat(), self._square.val())

    def setColor(self, color):
        hue, sat, val, _ = color.getHsvF()
        hue = max(hue or 0.0, 0.0)
        self._hue_bar.setHue(hue)
        self._square.setHue(hue)
        self._square.setSV(sat, val)


# --------------------------------------------------------------------------- #
# frameless window helpers
# --------------------------------------------------------------------------- #

# setWindowFlags REPLACES the flag set, window type included. A parented QDialog
# that keeps only FramelessWindowHint loses Qt.Dialog, stops being a window, and
# is reparented as a child widget inside its parent, so it never appears. The
# window type has to stay in the mask.
FRAMELESS_WINDOW = (
    Qt.WindowType.Window
    | Qt.WindowType.FramelessWindowHint
    | Qt.WindowType.WindowStaysOnTopHint
)
FRAMELESS_DIALOG = (
    Qt.WindowType.Dialog
    | Qt.WindowType.FramelessWindowHint
    | Qt.WindowType.WindowStaysOnTopHint
)


class DragToMoveMixin:
    """Frameless windows get no title bar, so drag any bare area to move them."""

    _drag_offset = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None


# --------------------------------------------------------------------------- #
# library picker
# --------------------------------------------------------------------------- #

class LibraryPickerDialog(DragToMoveMixin, QDialog):
    """Searchable grid of the Tabler library. Picking here never alters the
    library itself, it just hands a member back to the caller."""

    def __init__(self, entries, parent=None):
        super().__init__(parent)
        self.setObjectName("LibraryPickerDialog")
        self.setWindowTitle("Choose from Library")
        self.setWindowFlags(FRAMELESS_DIALOG)
        self.resize(660, 560)
        self._selection = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search icons...")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self._filter)
        layout.addWidget(self.search_box)

        self.list_widget = QListWidget()
        self.list_widget.setViewMode(QListWidget.ViewMode.IconMode)
        self.list_widget.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list_widget.setMovement(QListWidget.Movement.Static)
        self.list_widget.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self.list_widget.setGridSize(QSize(CELL_SIZE, CELL_SIZE))
        self.list_widget.setSpacing(4)
        self.list_widget.setUniformItemSizes(True)
        self.list_widget.itemDoubleClicked.connect(self._accept_item)
        layout.addWidget(self.list_widget)

        self._rows = []  # (name, variant, QListWidgetItem)
        self._unrenderable = set()  # ids of items whose glyph failed to render
        blank = QPixmap(ICON_SIZE, ICON_SIZE)
        blank.fill(Qt.GlobalColor.transparent)
        blank_icon = QIcon(blank)
        for name, variant, member in entries:
            item = QListWidgetItem(blank_icon, "")
            item.setSizeHint(QSize(CELL_SIZE, CELL_SIZE))
            item.setToolTip(f"{name} ({variant})")
            item.setData(ROLE_NAME, name)
            item.setData(ROLE_MEMBER, member)
            self.list_widget.addItem(item)
            self._rows.append((name, variant, item))

        # Rendering the whole library up front costs seconds, so only the cells
        # actually on screen are rendered, in small chunks between events.
        self._render_timer = QTimer(self)
        self._render_timer.setInterval(0)
        self._render_timer.timeout.connect(self._render_visible_chunk)
        self.list_widget.verticalScrollBar().valueChanged.connect(self._queue_render)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_current)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def showEvent(self, event):
        # Frameless windows are not placed or focused by the window manager
        super().showEvent(event)
        if self.parent() is not None:
            center = self.parent().frameGeometry().center()
            self.move(center - self.rect().center())
        self.raise_()
        self.activateWindow()
        self.search_box.setFocus()
        self._queue_render()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._queue_render()

    def hideEvent(self, event):
        self._render_timer.stop()
        super().hideEvent(event)

    def _queue_render(self, *_args):
        if not self._render_timer.isActive():
            self._render_timer.start()

    def _render_visible_chunk(self):
        """Render at most a handful of on-screen, still-blank cells per tick."""
        viewport = self.list_widget.viewport().rect()
        budget = 24
        rendered = 0
        for _name, _variant, item in self._rows:
            if item.isHidden() or item.data(ROLE_RENDERED):
                continue
            rect = self.list_widget.visualItemRect(item)
            if rect.top() > viewport.bottom():
                break
            if rect.bottom() < viewport.top():
                continue
            item.setData(ROLE_RENDERED, True)
            icon = library_icon(item.data(ROLE_MEMBER))
            if icon is None:
                self._unrenderable.add(id(item))
                item.setHidden(True)
                continue
            item.setIcon(icon)
            rendered += 1
            if rendered >= budget:
                return
        if rendered == 0:
            self._render_timer.stop()

    def selection(self):
        """(name, member) of the chosen icon, or None."""
        return self._selection

    def reset_view(self):
        self.search_box.clear()
        self.list_widget.clearSelection()
        self.list_widget.scrollToTop()
        self._selection = None
        self._queue_render()

    def _accept_item(self, item):
        self._selection = (item.data(ROLE_NAME), item.data(ROLE_MEMBER))
        self.accept()

    def _accept_current(self):
        item = self.list_widget.currentItem()
        if item is not None and not item.isHidden():
            self._accept_item(item)
        else:
            self.reject()

    def _filter(self, text):
        text = text.strip().lower()
        for name, variant, item in self._rows:
            hidden = bool(text) and text not in name.lower() and text not in variant
            item.setHidden(hidden or id(item) in self._unrenderable)
        self.list_widget.scrollToTop()
        self._queue_render()


# --------------------------------------------------------------------------- #
# colour dialog
# --------------------------------------------------------------------------- #

class IconStyleDialog(DragToMoveMixin, QDialog):
    """Set the icon colour and background colour for the loaded icon.

    Background transparency is a state you leave by touching the background
    picker, rather than a checkbox that greys the picker out.
    """

    def __init__(self, base_pixmap, source_name, icon_color, bg_color, parent=None):
        super().__init__(parent)
        self.setObjectName("IconStyleDialog")
        self._base = base_pixmap
        self._initial_icon_color = QColor(icon_color) if icon_color is not None else None
        self._initial_bg_color = QColor(bg_color) if bg_color is not None else None

        self.setWindowTitle("Set Icon Colour")
        self.setWindowFlags(FRAMELESS_DIALOG)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(12)

        header = QLabel("Set Icon Colour")
        header.setObjectName("dialogTitle")
        outer.addWidget(header)

        content = QHBoxLayout()
        content.setSpacing(24)

        # icon colour column
        icon_col = QVBoxLayout()
        icon_col.addWidget(section_label("ICON COLOUR"))
        self.icon_picker = ColorPickerWidget(self._initial_icon_color or DEFAULT_ICON_COLOR)
        icon_col.addWidget(self.icon_picker)
        self.icon_hex = QLineEdit(self.icon_picker.currentColor().name())
        icon_col.addWidget(self.icon_hex)
        self.original_colors = QCheckBox("Keep original colours")
        self.original_colors.setChecked(self._initial_icon_color is None)
        self.original_colors.setToolTip("Picking an icon colour turns this off automatically")
        icon_col.addWidget(self.original_colors)
        icon_col.addStretch()
        content.addLayout(icon_col)

        # background colour column
        bg_col = QVBoxLayout()
        bg_col.addWidget(section_label("BACKGROUND COLOUR"))
        self.bg_picker = ColorPickerWidget(self._initial_bg_color or DEFAULT_BG_COLOR)
        bg_col.addWidget(self.bg_picker)
        self.bg_hex = QLineEdit(self.bg_picker.currentColor().name())
        bg_col.addWidget(self.bg_hex)
        self.transparent = QCheckBox("No background (transparent)")
        self.transparent.setChecked(self._initial_bg_color is None)
        self.transparent.setToolTip("Picking a background colour turns this off automatically")
        bg_col.addWidget(self.transparent)
        bg_col.addStretch()
        content.addLayout(bg_col)

        # live preview column
        preview_col = QVBoxLayout()
        preview_col.addWidget(section_label("PREVIEW"))
        self.preview = QLabel()
        self.preview.setFixedSize(DIALOG_PREVIEW_SIZE, DIALOG_PREVIEW_SIZE)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setObjectName("dialogPreview")
        preview_col.addWidget(self.preview)
        preview_col.addStretch()
        content.addLayout(preview_col)

        outer.addLayout(content)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Reset
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Reset).clicked.connect(self._reset)
        outer.addWidget(buttons)

        self.icon_picker.colorChanged.connect(self._on_icon_color_changed)
        self.bg_picker.colorChanged.connect(self._on_bg_color_changed)
        self.icon_hex.textEdited.connect(self._on_icon_hex_edited)
        self.bg_hex.textEdited.connect(self._on_bg_hex_edited)
        self.transparent.toggled.connect(lambda _checked: self._refresh())
        self.original_colors.toggled.connect(lambda _checked: self._refresh())

        self._refresh()

    def showEvent(self, event):
        super().showEvent(event)
        if self.parent() is not None:
            center = self.parent().frameGeometry().center()
            self.move(center - self.rect().center())
        self.raise_()
        self.activateWindow()

    # -- results ------------------------------------------------------------ #

    def icon_color(self):
        return None if self.original_colors.isChecked() else self.icon_picker.currentColor()

    def background_color(self):
        return None if self.transparent.isChecked() else self.bg_picker.currentColor()

    # -- internals ---------------------------------------------------------- #

    @staticmethod
    def _set_hex_silently(line_edit, color):
        line_edit.blockSignals(True)
        line_edit.setText(color.name())
        line_edit.blockSignals(False)

    def _clear_transparent(self):
        """Touching the background picker means the user wants a background."""
        if self.transparent.isChecked():
            self.transparent.blockSignals(True)
            self.transparent.setChecked(False)
            self.transparent.blockSignals(False)

    def _clear_original(self):
        """Touching the icon picker means the user wants a tint."""
        if self.original_colors.isChecked():
            self.original_colors.blockSignals(True)
            self.original_colors.setChecked(False)
            self.original_colors.blockSignals(False)

    def _on_icon_color_changed(self, color):
        self._clear_original()
        self._set_hex_silently(self.icon_hex, color)
        self._refresh()

    def _on_bg_color_changed(self, color):
        self._clear_transparent()
        self._set_hex_silently(self.bg_hex, color)
        self._refresh()

    def _on_icon_hex_edited(self, text):
        color = QColor(text.strip())
        if color.isValid():
            self._clear_original()
            self.icon_picker.setColor(color)
            self._refresh()

    def _on_bg_hex_edited(self, text):
        color = QColor(text.strip())
        if color.isValid():
            self._clear_transparent()
            self.bg_picker.setColor(color)
            self._refresh()

    def _reset(self):
        icon = self._initial_icon_color or DEFAULT_ICON_COLOR
        self.icon_picker.setColor(icon)
        self._set_hex_silently(self.icon_hex, icon)
        self.original_colors.blockSignals(True)
        self.original_colors.setChecked(self._initial_icon_color is None)
        self.original_colors.blockSignals(False)
        bg = self._initial_bg_color or DEFAULT_BG_COLOR
        self.bg_picker.setColor(bg)
        self._set_hex_silently(self.bg_hex, bg)
        self.transparent.blockSignals(True)
        self.transparent.setChecked(self._initial_bg_color is None)
        self.transparent.blockSignals(False)
        self._refresh()

    def _refresh(self):
        self.preview.setPixmap(
            compose(self._base, self.icon_color(), self.background_color(), DIALOG_PREVIEW_SIZE)
        )


# --------------------------------------------------------------------------- #
# main window
# --------------------------------------------------------------------------- #

class IconStudio(DragToMoveMixin, QMainWindow):
    """Home screen: the loaded icon, large, plus the two ways to load one."""

    accepted = pyqtSignal()
    rejected = pyqtSignal()

    def __init__(self, standalone=False, initial_path=None, initial_icon=None):
        super().__init__()
        self._standalone = standalone
        self.setWindowTitle("Modify Icon")
        self.setWindowFlags(FRAMELESS_WINDOW)
        self.setMinimumSize(440, 480)
        self.resize(460, 520)

        # working state
        self._base = None  # master pixmap of the loaded icon
        self._source_name = None
        self._icon_color = QColor(DEFAULT_ICON_COLOR)
        self._bg_color = None
        self._picker = None

        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)

        panel = QFrame()
        panel.setObjectName("homePanel")
        outer.addWidget(panel)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(24, 18, 24, 20)
        layout.setSpacing(12)

        title_row = QHBoxLayout()
        title = QLabel("Modify Icon")
        title.setObjectName("appTitle")
        title_row.addWidget(title)
        title_row.addStretch()
        layout.addLayout(title_row)

        # Kept as an off-screen status sink so _status() stays a no-op display
        self.subtitle = QLabel(DEFAULT_SUBTITLE)
        self.subtitle.setObjectName("hintText")
        self.subtitle.hide()

        # The body only ever shows the loaded icon; it always starts with the
        # default rocket, so there is no empty state to fall back to.
        self.body = QStackedWidget()
        layout.addWidget(self.body, 1)

        # -- loaded state: the icon, and a way to swap it out --------------- #
        loaded = QWidget()
        loaded_layout = QVBoxLayout(loaded)
        loaded_layout.setContentsMargins(0, 0, 0, 0)
        loaded_layout.setSpacing(8)
        loaded_layout.addStretch()

        self.preview = ClickableLabel()
        self.preview.setFixedSize(HOME_PREVIEW_SIZE, HOME_PREVIEW_SIZE)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setObjectName("iconPreview")
        self.preview.setCursor(Qt.CursorShape.PointingHandCursor)
        self.preview.clicked.connect(self._edit_colors)
        loaded_layout.addWidget(self.preview, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.source_label = QLabel()
        self.source_label.setObjectName("sourceName")
        loaded_layout.addWidget(self.source_label, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.source_label.hide()

        self.hint_label = QLabel("Click the preview to change colours")
        self.hint_label.setObjectName("hintText")
        loaded_layout.addWidget(self.hint_label, alignment=Qt.AlignmentFlag.AlignHCenter)

        loaded_layout.addStretch()

        # Visual divider to clearly separate optional icon replacement actions
        divider_row = QHBoxLayout()
        divider_row.setSpacing(12)
        left_line = QFrame()
        left_line.setFrameShape(QFrame.Shape.HLine)
        left_line.setObjectName("dividerLine")
        right_line = QFrame()
        right_line.setFrameShape(QFrame.Shape.HLine)
        right_line.setObjectName("dividerLine")
        replace_label = QLabel("OR REPLACE ICON")
        replace_label.setObjectName("sectionLabel")
        divider_row.addWidget(left_line, 1)
        divider_row.addWidget(replace_label, alignment=Qt.AlignmentFlag.AlignCenter)
        divider_row.addWidget(right_line, 1)
        loaded_layout.addLayout(divider_row)
        loaded_layout.addSpacing(2)

        change_row = QHBoxLayout()
        change_row.setSpacing(10)
        disk_card = OptionCard(
            OutlineIcon.FOLDER_OPEN, "Replace from disk", "Load an SVG or image file", compact=True
        )
        disk_card.clicked.connect(self._load_from_disk)
        change_row.addWidget(disk_card)

        library_card = OptionCard(
            OutlineIcon.LAYOUT_GRID, "Replace from library", "Browse the Tabler icon set", compact=True
        )
        library_card.clicked.connect(self._load_from_library)
        change_row.addWidget(library_card)
        loaded_layout.addLayout(change_row)
        self.body.addWidget(loaded)

        # Clear OK / Cancel actions, always visible below the body
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self._cancel)
        layout.addWidget(buttons)

        self._load_default_icon(initial_path, initial_icon)
        self._refresh()

    def _accept(self):
        self.accepted.emit()
        if self._standalone:
            QApplication.exit(0)
        self.close()

    def _cancel(self):
        self.rejected.emit()
        if self._standalone:
            QApplication.exit(1)
        self.close()

    def keyPressEvent(self, event):
        # No system close button on a frameless window
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def _status(self, text):
        """The subtitle line is the status line; repaint so it shows during work."""
        self.subtitle.setText(text)
        QApplication.processEvents()

    # -- sources ------------------------------------------------------------ #

    def _detect_and_set_colors(self, pixmap):
        """Detect colours and strip any baked-in background, returning the
        cleaned pixmap so the caller stores artwork, not a flattened square."""
        icon_color, bg_color, monochrome = extract_icon_colors(pixmap)
        self._bg_color = bg_color
        if bg_color is not None:
            pixmap = strip_background(pixmap, bg_color)
        if not monochrome:
            # Tinting multi-coloured artwork would flatten it to a silhouette
            self._icon_color = None
            return pixmap
        is_black = (
            icon_color.red() < 30
            and icon_color.green() < 30
            and icon_color.blue() < 30
            and bg_color is None
        )
        # A black glyph on transparent would be invisible on the dark UI
        self._icon_color = DEFAULT_ICON_COLOR if is_black else icon_color
        return pixmap

    def _load_from_disk(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load icon from disk", "", IMAGE_FILTER)
        if not path:
            return
        pixmap = load_icon_file(path)
        if pixmap is None:
            self._status("Could not load that file")
            return
        name = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        pixmap = self._detect_and_set_colors(pixmap)
        self._set_icon(pixmap, name)

    def _load_from_library(self):
        if self._picker is not None and self._picker.isVisible():
            return

        if self._picker is None:
            self._picker = LibraryPickerDialog(library_members(), parent=self)
        else:
            self._picker.reset_view()

        if self._picker.exec() != QDialog.DialogCode.Accepted:
            self._status(DEFAULT_SUBTITLE)
            return
        chosen = self._picker.selection()
        if chosen is None:
            return
        name, member = chosen
        # Render fresh at working size. The library grid keeps its own original
        # renders, so colouring here never touches the library.
        pixmap = render_icon(member, WORK_RENDER_SIZE)
        if pixmap is None:
            self._status(f"Could not render '{name}'")
            return
        # Library glyphs are black on transparent, so they always need a theme tint.
        self._icon_color = QColor(DEFAULT_ICON_COLOR)
        self._set_icon(pixmap, name)

    def closeEvent(self, event):
        # A picker left open would otherwise outlive this window
        if self._picker is not None:
            self._picker.reject()
            self._picker = None
        super().closeEvent(event)

    def _set_icon(self, pixmap, name):
        self._base = pixmap
        self._source_name = name
        self._refresh()
        self._status(f"Loaded '{name}'")

    def _load_default_icon(self, initial_path=None, initial_icon=None):
        """Open with the current icon, or the rocket for a new command."""
        if initial_icon is not None and not initial_icon.isNull():
            pixmap = initial_icon.pixmap(QSize(WORK_RENDER_SIZE, WORK_RENDER_SIZE))
            if not pixmap.isNull():
                pixmap = self._detect_and_set_colors(pixmap)
                self._set_icon(pixmap, "Current icon")
                return
        path = Path(initial_path) if initial_path and Path(initial_path).exists() else DEFAULT_ICON_PATH
        pixmap = load_icon_file(str(path))
        if pixmap is not None:
            pixmap = self._detect_and_set_colors(pixmap)
            self._set_icon(pixmap, path.name)

    # -- colours ------------------------------------------------------------ #

    def _edit_colors(self):
        if self._base is None:
            self._status("Load an icon first")
            return

        dialog = IconStyleDialog(
            self._base, self._source_name, self._icon_color, self._bg_color, parent=self
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._icon_color = dialog.icon_color()
        self._bg_color = dialog.background_color()
        self._refresh()

        foreground = "original colours" if self._icon_color is None else self._icon_color.name()
        background = "transparent" if self._bg_color is None else self._bg_color.name()
        self._status(f"Icon set to {foreground} on {background}")

    # -- export hook -------------------------------------------------------- #

    def save_png(self, path, size=EXPORT_SIZE):
        """Write the current icon out. Wired for automation, no button attached."""
        if self._base is None:
            return False
        return export_png(self._base, self._icon_color, self._bg_color, path, size)

    # -- view --------------------------------------------------------------- #

    def _refresh(self):
        if self._base is None:
            return

        self.body.setCurrentIndex(0)
        self.preview.setPixmap(
            compose(self._base, self._icon_color, self._bg_color, HOME_PREVIEW_SIZE)
        )
        self.source_label.setText(self._source_name)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")  # consistent base across platforms for the stylesheet
    app.setStyleSheet(load_stylesheet())
    window = IconStudio(standalone=True)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()