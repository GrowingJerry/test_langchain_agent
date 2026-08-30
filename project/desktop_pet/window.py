from __future__ import annotations

import uuid
import webbrowser
from pathlib import Path

from PySide6.QtCore import QObject,QPoint,QRunnable,QSettings,QThreadPool,QTimer,Qt,Signal
from PySide6.QtGui import QColor,QIcon,QPainter,QPixmap
from PySide6.QtWidgets import QApplication,QHBoxLayout,QMenu,QSystemTrayIcon,QWidget

from application.container import ApplicationContainer
from application.services.project_service import DEFAULT_PROJECT_DB
from config.settings import settings
from desktop_pet.chat_panel import ChatPanel
from desktop_pet.pet_widget import PetWidget
from desktop_pet.status import latest_generation_status


class WorkerSignals(QObject):
    complete=Signal(object); failed=Signal(str)
class MessageWorker(QRunnable):
    def __init__(self,gateway,session_id:str,project_id:str,text:str,attachment_ids:list[str])->None: super().__init__(); self.gateway=gateway; self.args=(session_id,project_id,text,attachment_ids); self.signals=WorkerSignals()
    def run(self)->None:
        try: self.signals.complete.emit(self.gateway.handle_message(*self.args))
        except Exception as exc: self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
class AttachmentWorker(QRunnable):
    def __init__(self,gateway,session_id:str,path:str)->None: super().__init__(); self.gateway=gateway; self.session_id=session_id; self.path=path; self.signals=WorkerSignals()
    def run(self)->None:
        try:
            record=self.gateway.upload_attachment(self.session_id,Path(self.path)); self.signals.complete.emit(record)
            self.gateway.parse_attachment(self.session_id,record["attachment_id"]); self.signals.complete.emit(self.gateway.sessions.get_attachment(self.session_id,record["attachment_id"]))
        except Exception as exc: self.signals.failed.emit(f"{Path(self.path).name}: {type(exc).__name__}: {exc}")


class XiaocheWindow(QWidget):
    def __init__(self)->None:
        super().__init__(); self.store=QSettings("Xiaoche","DesktopAssistant"); self.pool=QThreadPool.globalInstance(); self.worker=None; self.session_id=str(self.store.value("session_id") or uuid.uuid4().hex); self.store.setValue("session_id",self.session_id)
        self.gateway=ApplicationContainer().build_assistant_gateway(DEFAULT_PROJECT_DB); self.current_project_id=""
        flags=Qt.FramelessWindowHint|Qt.Tool
        if self.store.value("always_on_top",settings.xiaoche_always_on_top,bool): flags|=Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags); self.setAttribute(Qt.WA_TranslucentBackground); self.pet=PetWidget(); self.chat_panel=ChatPanel(); self.chat_panel.hide(); layout=QHBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.addWidget(self.chat_panel); layout.addWidget(self.pet)
        self.pet.clicked.connect(self.toggle_chat); self.pet.double_clicked.connect(self.open_main); self.pet.drag_finished.connect(self.snap_to_edge); self.chat_panel.send_requested.connect(self.send); self.chat_panel.stop_requested.connect(self.stop); self.chat_panel.attachments_added.connect(self.add_attachments)
        saved=self.store.value("position");
        if isinstance(saved,QPoint): self.move(saved)
        self._restore_history(); self.timer=QTimer(self); self.timer.timeout.connect(self.poll_status); self.timer.start(settings.xiaoche_status_poll_ms); self.tray=self._create_tray(); self.poll_status()
    def _restore_history(self)->None:
        for row in self.gateway.sessions.page(self.session_id,50): self.chat_panel.append("你" if row["role"]=="user" else "小测",row["content"])
    def toggle_chat(self)->None: self.chat_panel.setVisible(not self.chat_panel.isVisible()); self.adjustSize()
    def open_main(self)->None: webbrowser.open(settings.xiaoche_app_url)
    def open_page(self,page:str)->None:
        from application.assistant.intent_router import page_url
        webbrowser.open(page_url(settings.xiaoche_app_url,page))
    def send(self,text:str,attachment_ids:list[str])->None:
        if self.worker is not None: return
        self.chat_panel.append("你",text); self.pet.set_state("thinking"); self.chat_panel.progress.setRange(0,0); worker=MessageWorker(self.gateway,self.session_id,self.current_project_id,text,attachment_ids); worker.signals.complete.connect(self.finished); worker.signals.failed.connect(self.failed); self.worker=worker; self.pool.start(worker)
    def finished(self,response)->None:
        self.chat_panel.append("小测",response.message)
        for output in response.output_files: self.chat_panel.append("文件",f"{output['filename']}\n打开文件：{output['absolute_path']}\n打开所在目录：{Path(output['absolute_path']).parent}")
        self.pet.set_state("success" if response.status=="success" else "error"); self.chat_panel.progress.setRange(0,100); self.chat_panel.progress.setValue(100); self.worker=None
    def failed(self,text:str)->None: self.chat_panel.append("错误",f"失败阶段：后台任务\n错误类型：{text}\n建议：查看 logs/assistant/ 后重试"); self.pet.set_state("error"); self.chat_panel.progress.setRange(0,100); self.worker=None
    def stop(self)->None: self.chat_panel.append("小测","当前任务不支持安全中断；不会伪造已停止。")
    def add_attachments(self,files:list)->None:
        remaining=max(0,settings.xiaoche_max_attachments-self.chat_panel.attachments.count())
        for path in files[:remaining]:
            worker=AttachmentWorker(self.gateway,self.session_id,path); worker.signals.complete.connect(self._attachment_updated); worker.signals.failed.connect(self.failed); self.pool.start(worker)
    def _attachment_updated(self,record:dict)->None:
        existing=self.chat_panel.pending_attachment_ids()
        if record["attachment_id"] in existing: self.chat_panel.update_attachment(record)
        else: self.chat_panel.add_attachment_record(record)
    def poll_status(self)->None: self.pet.set_state(latest_generation_status(Path(__file__).resolve().parents[1]).state)
    def snap_to_edge(self)->None:
        if not self.store.value("auto_dock",settings.xiaoche_auto_dock,bool): return
        screen=QApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen(); area=screen.availableGeometry(); x=area.left() if abs(self.x()-area.left())<abs(area.right()-self.frameGeometry().right()) else area.right()-self.width(); self.move(x,min(max(self.y(),area.top()),area.bottom()-self.height())); self.store.setValue("position",self.pos()); self.pet.set_state("idle")
    def _create_tray(self)->QSystemTrayIcon:
        pixmap=QPixmap(32,32); pixmap.fill(Qt.transparent); painter=QPainter(pixmap); painter.setBrush(QColor("#6fa8ff")); painter.drawRoundedRect(2,2,28,28,8,8); painter.end(); tray=QSystemTrayIcon(QIcon(pixmap),self); menu=QMenu(); show=menu.addAction("显示小测"); show.triggered.connect(self.show); main=menu.addAction("打开主界面"); main.triggered.connect(self.open_main)
        for page in ("项目工作台","生成测试用例","用例审查","导出中心"):
            action=menu.addAction(f"打开{page}"); action.triggered.connect(lambda checked=False,value=page:self.open_page(value))
        menu.addSeparator(); toggle=menu.addAction("显示/隐藏对话框"); toggle.triggered.connect(self.toggle_chat); quit_action=menu.addAction("退出小测"); quit_action.triggered.connect(QApplication.quit); tray.setContextMenu(menu); tray.setToolTip("小测"); tray.show(); return tray
    def closeEvent(self,event)->None: self.store.setValue("position",self.pos()); event.ignore(); self.hide()
