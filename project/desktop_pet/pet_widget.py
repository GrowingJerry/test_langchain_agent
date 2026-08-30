from __future__ import annotations

from PySide6.QtCore import QRectF, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget


class PetWidget(QWidget):
    clicked = Signal(); double_clicked = Signal(); drag_finished = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent); self.state = "idle"; self.phase = 0; self._dragged = False; self._offset = None
        self.setFixedSize(176, 190); timer = QTimer(self); timer.timeout.connect(self._animate); timer.start(160)

    def set_state(self, state: str) -> None: self.state = state; self.update()
    def _animate(self) -> None: self.phase = (self.phase + 1) % 30; self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self); painter.setRenderHint(QPainter.Antialiasing)
        y = 9 + (1 if self.phase < 15 else 0); colors = {"success":"#63d69b", "warning":"#ffb84d", "error":"#ff7373", "sleeping":"#9baac4", "dragging":"#77a7ff"}
        painter.setPen(QPen(QColor("#28466f"), 4)); painter.setBrush(QColor(colors.get(self.state, "#6fa8ff")))
        painter.drawRoundedRect(QRectF(18, y + 28, 140, 130), 42, 42); painter.setBrush(QColor("#f7fbff")); painter.drawRoundedRect(QRectF(36, y + 52, 104, 70), 28, 28)
        painter.setBrush(QColor("#24436b")); blink = self.state == "sleeping" or self.phase == 0
        if blink: painter.drawLine(61, y + 83, 76, y + 83); painter.drawLine(101, y + 83, 116, y + 83)
        else: painter.drawEllipse(QRectF(64, y + 75, 12, 17)); painter.drawEllipse(QRectF(102, y + 75, 12, 17))
        painter.setBrush(QColor("#ffb33b")); painter.drawEllipse(QRectF(80, y + 7, 16, 16)); painter.drawLine(88, y + 28, 88, y + 20)
        if self.state == "reviewing": painter.drawText(QRectF(42, y + 128, 92, 28), 0x84, "请审核")

    def mousePressEvent(self, event) -> None:
        if event.button().value == 1: self._offset = event.globalPosition().toPoint() - self.window().pos(); self._dragged = False
    def mouseMoveEvent(self, event) -> None:
        if self._offset is not None: self._dragged = True; self.set_state("dragging"); self.window().move(event.globalPosition().toPoint() - self._offset)
    def mouseReleaseEvent(self, event) -> None:
        if self._offset is not None:
            self._offset = None
            if self._dragged: self.drag_finished.emit()
            else: self.clicked.emit()
    def mouseDoubleClickEvent(self, event) -> None: self.double_clicked.emit()
