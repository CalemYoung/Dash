"""Keep Dash windows on the screen they open on.

Dash windows are frameless, so the window manager never places or corrects
them - every position is one Dash chose. Nothing else pulls a window back from
an edge, which is how a dialog sized for a desktop monitor ends up hanging off
a laptop screen. All placement goes through here.
"""

from PyQt6.QtCore import QPoint, QRect, QSize
from PyQt6.QtWidgets import QApplication, QWIDGETSIZE_MAX

# Breathing room between a window and the edge of the work area.
SCREEN_MARGIN = 16

# Never shrink a window into something unusable, even on a tiny work area.
MIN_WINDOW_SIDE = 240


def available_geometry_for(widget) -> QRect:
    """Work area (screen minus taskbar) of the screen `widget` sits on."""
    screen = widget.screen() or QApplication.primaryScreen()
    if screen is None:
        return QRect(0, 0, 1024, 768)
    return screen.availableGeometry()


def clamp_size_to_screen(width: int, height: int, area: QRect, margin: int = SCREEN_MARGIN) -> QSize:
    """Cap a window size to what actually fits inside `area`."""
    return QSize(
        min(width, max(MIN_WINDOW_SIDE, area.width() - 2 * margin)),
        min(height, max(MIN_WINDOW_SIDE, area.height() - 2 * margin)),
    )


def move_within_screen(widget, size: QSize | None = None, anchor_center: QPoint | None = None, area: QRect | None = None):
    """Centre `widget` on `anchor_center`, then pull it fully back on screen.

    Pass `size` whenever the widget was just resized. Qt applies a resize
    lazily, so frameGeometry() straight after setFixedSize() still reports the
    old size - position it from that and a window that just grew is placed as
    though it were still small, leaving it overhanging the bottom right.

    `area` is the work area to stay within; it defaults to the screen the
    widget is currently on, so pass it when moving to a different screen.
    """
    if area is None:
        area = available_geometry_for(widget)
    if size is None:
        size = widget.frameGeometry().size()
    widget.move(position_within_screen(size, anchor_center, area))


def position_within_screen(size: QSize, anchor_center: QPoint | None, area: QRect) -> QPoint:
    """Top-left corner that centres `size` on `anchor_center` and keeps it
    fully inside `area`."""
    frame = QRect(QPoint(0, 0), size)
    frame.moveCenter(anchor_center if anchor_center is not None else area.center())

    # Clamping low last means a window taller than the work area sits flush
    # with the top edge and overflows downwards, rather than the reverse.
    x = max(area.left(), min(frame.left(), area.left() + area.width() - size.width()))
    y = max(area.top(), min(frame.top(), area.top() + area.height() - size.height()))
    return QPoint(x, y)


def pin_within_screen(widget, size: QSize, anchor_center: QPoint | None = None, area: QRect | None = None):
    """Give a visible, fixed-size window a new size and position in one step.

    A resize followed by a move is two native window changes, and between
    them the window can be drawn at the new size in the old place. Releasing
    the fixed size, setting the full geometry once, then pinning the size
    again keeps it to a single change.
    """
    if area is None:
        area = available_geometry_for(widget)
    position = position_within_screen(size, anchor_center, area)
    target = QRect(position, size)
    if widget.geometry() != target:
        widget.setMinimumSize(0, 0)
        widget.setMaximumSize(QWIDGETSIZE_MAX, QWIDGETSIZE_MAX)
        widget.setGeometry(target)
    widget.setFixedSize(size)


def fit_within_screen(widget, anchor_center: QPoint | None = None, margin: int = SCREEN_MARGIN):
    """Shrink `widget` to fit its screen, then place it on screen.

    Used by the dialogs, whose design-time minimum sizes assume a desktop
    monitor and are taller than the work area of a small laptop.
    """
    area = available_geometry_for(widget)
    size = clamp_size_to_screen(widget.width(), widget.height(), area, margin)

    # A minimum size larger than the screen would silently veto the resize.
    minimum = widget.minimumSize()
    if minimum.width() > size.width() or minimum.height() > size.height():
        widget.setMinimumSize(min(minimum.width(), size.width()), min(minimum.height(), size.height()))

    if size != widget.size():
        widget.resize(size)
    move_within_screen(widget, size, anchor_center)
