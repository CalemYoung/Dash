"""Small widgets shared between the launcher and the dialogs."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QSizePolicy


class ElidedLabel(QLabel):
    """Single-line label that trims long text with an ellipsis instead of
    forcing the row wider or being clipped mid-word. The full text is the
    tooltip whenever it has been trimmed."""

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self._full_text = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)

    def full_text(self) -> str:
        return self._full_text

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
