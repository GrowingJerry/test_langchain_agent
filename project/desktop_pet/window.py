from __future__ import annotations

import webbrowser

from PySide6.QtCore import QObject, QPoint, QRunnable, QSettings, QThreadPool, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QHBoxLayout, QMenu, QSystemTrayIcon, QWidget

from config.settings import settings
from desktop_pet.chat_panel import ChatPanel
from desktop_pet.ollama_chat import chat
from desktop_pet.pet_widget import PetWidget
from desktop_pet.status import latest_generation_status


class WorkerSignals(QObject):
    complete = Signal(str); failed = Signal(str)


class ChatWorker(QRunnable):
    def __init__(self, text: str) -> None: super().__init__(); self.text = text; self.signals = WorkerSignals()
    def run(self) -> None:
        try: self.signals.complete.emit(chat(settings.ollama_base_url, settings.ollama_model, self.text, settings.xiaoche_chat_timeout))
        except Exception as exc: self.signals.failed.emit(f"本机 Ollama 暂不可用：{type(exc).__name__}: {exc}")


class XiaocheWindow(QWidget):
    def __init__(self) -> None:
        super().__init__(); self.store = QSettings("Xiaoche", "DesktopAssistant"); self.pool = QThreadPool.globalInstance(); self.worker = None; self.drag_state = "idle"
        flags = Qt.FramelessWindowHint | Qt.Tool
        if self.store.value("always_on_top", settings.xiaoche_always_on_top, bool): flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags); self.setAttribute(Qt.WA_TranslucentBackground); self.pet = PetWidget(); self.chat_panel = ChatPanel(); self.chat_panel.hide()
        layout = QHBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.addWidget(self.chat_panel); layout.addWidget(self.pet)
        self.pet.clicked.connect(self.toggle_chat); self.pet.double_clicked.connect(self.open_main); self.pet.drag_finished.connect(self.snap_to_edge)
        self.chat_panel.send_requested.connect(self.send); self.chat_panel.stop_requested.connect(self.stop); self.chat_panel.attachments_added.connect(self.add_attachments)
        saved = self.store.value("position")
        if isinstance(saved, QPoint): self.move(saved)
        self.timer = QTimer(self); self.timer.timeout.connect(self.poll_status); self.timer.start(settings.xiaoche_status_poll_ms)
        self.tray = self._create_tray(); self.poll_status()

    def toggle_chat(self) -> None: self.chat_panel.setVisible(not self.chat_panel.isVisible()); self.adjustSize()
    def open_main(self) -> None: webbrowser.open(settings.xiaoche_app_url)
    def open_page(self, page: str) -> None:
        from application.assistant.intent_router import page_url
        webbrowser.open(page_url(settings.xiaoche_app_url, page))
    def send(self, text: str) -> None:
        if self.worker is not None: return
        self.chat_panel.append("你", text); self.pet.set_state("thinking"); self.chat_panel.progress.setRange(0,0)
        worker = ChatWorker(text); worker.signals.complete.connect(self.finished); worker.signals.failed.connect(self.failed); self.worker = worker; self.pool.start(worker)
    def finished(self, text: str) -> None: self.chat_panel.append("小测", text); self.pet.set_state("success"); self.chat_panel.progress.setRange(0,100); self.chat_panel.progress.setValue(100); self.worker = None
    def failed(self, text: str) -> None: self.chat_panel.append("错误", text); self.pet.set_state("error"); self.chat_panel.progress.setRange(0,100); self.worker = None
    def stop(self) -> None:
        self.chat_panel.append("小测", "当前 Ollama HTTP 请求不支持安全中断；不会伪造已停止。")
    def add_attachments(self, files: list) -> None:
        for path in files[: settings.xiaoche_max_attachments]: self.chat_panel.attachments.addItem(path)
        self.chat_panel.append("小测", "附件已加入待处理列表；发送问题时将由本地解析器读取。")
    def poll_status(self) -> None: self.pet.set_state(latest_generation_status(__import__("pathlib").Path(__file__).resolve().parents[1]).state)
    def snap_to_edge(self) -> None:
        if not self.store.value("auto_dock", settings.xiaoche_auto_dock, bool): return
        screen = QApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen(); area = screen.availableGeometry(); x = area.left() if abs(self.x()-area.left()) < abs(area.right()-self.frameGeometry().right()) else area.right()-self.width(); self.move(x, min(max(self.y(), area.top()), area.bottom()-self.height())); self.store.setValue("position", self.pos()); self.pet.set_state("idle")
    def _create_tray(self) -> QSystemTrayIcon:
        pixmap = QPixmap(32,32); pixmap.fill(Qt.transparent); painter=QPainter(pixmap); painter.setBrush(QColor("#6fa8ff")); painter.drawRoundedRect(2,2,28,28,8,8); painter.end(); tray=QSystemTrayIcon(QIcon(pixmap), self); menu=QMenu()
        show=menu.addAction("显示小测"); show.triggered.connect(self.show); main=menu.addAction("打开主界面"); main.triggered.connect(self.open_main)
        for page in ("项目工作台","生成测试用例","用例审查","导出中心"):
            action=menu.addAction(f"打开{page}"); action.triggered.connect(lambda checked=False, value=page: self.open_page(value))
        menu.addSeparator(); toggle=menu.addAction("显示/隐藏对话框"); toggle.triggered.connect(self.toggle_chat); quit_action=menu.addAction("退出小测"); quit_action.triggered.connect(QApplication.quit)
        tray.setContextMenu(menu); tray.setToolTip("小测"); tray.show(); return tray
    def closeEvent(self, event) -> None: self.store.setValue("position", self.pos()); event.ignore(); self.hide()
