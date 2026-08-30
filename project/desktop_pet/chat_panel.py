from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QListWidget, QPushButton, QPlainTextEdit, QProgressBar, QVBoxLayout, QWidget


class MessageInput(QPlainTextEdit):
    submitted = Signal()
    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (0x01000004, 0x01000005) and not event.modifiers(): self.submitted.emit(); return
        super().keyPressEvent(event)


class ChatPanel(QWidget):
    send_requested = Signal(str); stop_requested = Signal(); attachments_added = Signal(list); new_session_requested = Signal()
    def __init__(self, parent=None) -> None:
        super().__init__(parent); self.setAcceptDrops(True); self.setMinimumWidth(390); self.setMaximumHeight(640)
        self.title = QLabel("小测 · 当前会话"); self.history = QPlainTextEdit(); self.history.setReadOnly(True)
        self.attachments = QListWidget(); self.input = MessageInput(); self.input.setMaximumHeight(100); self.input.submitted.connect(self.submit)
        self.progress = QProgressBar(); self.progress.setRange(0, 100); self.progress.setValue(0)
        send = QPushButton("发送"); send.clicked.connect(self.submit); stop = QPushButton("停止"); stop.clicked.connect(self.stop_requested); add = QPushButton("添加附件"); add.clicked.connect(self.pick_files); clear = QPushButton("清空显示"); clear.clicked.connect(self.history.clear)
        row = QHBoxLayout(); [row.addWidget(x) for x in (add, clear, stop, send)]
        layout = QVBoxLayout(self); [layout.addWidget(x) for x in (self.title, self.history, self.attachments, self.input, self.progress)]; layout.addLayout(row)
        self.setStyleSheet("QWidget{background:#f7fbff;color:#203858;border:1px solid #91baff;border-radius:12px} QPushButton{padding:6px;background:#e8f2ff}")
    def submit(self) -> None:
        text = self.input.toPlainText().strip()
        if text: self.input.clear(); self.send_requested.emit(text)
    def append(self, role: str, text: str) -> None: self.history.appendPlainText(f"{role}：{text}\n")
    def pick_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "选择附件", "", "文档 (*.docx *.pdf *.xlsx *.xls *.txt *.md)")
        if files: self.attachments_added.emit(files)
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls(): event.acceptProposedAction()
    def dropEvent(self, event) -> None: self.attachments_added.emit([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()])
