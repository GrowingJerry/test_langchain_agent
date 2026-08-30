from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QFileDialog,QHBoxLayout,QLabel,QListWidget,QListWidgetItem,QPushButton,QPlainTextEdit,QProgressBar,QVBoxLayout,QWidget


class MessageInput(QPlainTextEdit):
    submitted=Signal()
    def keyPressEvent(self,event:QKeyEvent)->None:
        if event.key() in (Qt.Key_Return,Qt.Key_Enter) and not (event.modifiers()&Qt.ShiftModifier): self.submitted.emit(); return
        super().keyPressEvent(event)


class ChatPanel(QWidget):
    send_requested=Signal(str,list); stop_requested=Signal(); attachments_added=Signal(list); new_session_requested=Signal()
    def __init__(self,parent=None)->None:
        super().__init__(parent); self.setAcceptDrops(True); self.setMinimumWidth(430); self.setMaximumHeight(680)
        self.title=QLabel("小测 · 当前会话"); self.history=QPlainTextEdit(); self.history.setReadOnly(True); self.attachments=QListWidget()
        self.input=MessageInput(); self.input.setMaximumHeight(100); self.input.submitted.connect(self.submit); self.progress=QProgressBar(); self.progress.setRange(0,100)
        send=QPushButton("发送"); send.clicked.connect(self.submit); stop=QPushButton("停止"); stop.clicked.connect(self.stop_requested); add=QPushButton("添加附件"); add.clicked.connect(self.pick_files); remove=QPushButton("删除附件"); remove.clicked.connect(self.remove_selected); clear=QPushButton("清空显示"); clear.clicked.connect(self.history.clear)
        row=QHBoxLayout(); [row.addWidget(x) for x in (add,remove,clear,stop,send)]; layout=QVBoxLayout(self); [layout.addWidget(x) for x in (self.title,self.history,self.attachments,self.input,self.progress)]; layout.addLayout(row)
        self.setStyleSheet("QWidget{background:#f7fbff;color:#203858;border:1px solid #91baff;border-radius:12px} QPushButton{padding:6px;background:#e8f2ff}")
    def add_attachment_record(self,record:dict)->None:
        item=QListWidgetItem(); item.setData(Qt.UserRole,record["attachment_id"]); item.setData(Qt.UserRole+1,record); self.attachments.addItem(item); self.update_attachment(record)
    def update_attachment(self,record:dict)->None:
        for index in range(self.attachments.count()):
            item=self.attachments.item(index)
            if item.data(Qt.UserRole)==record["attachment_id"]:
                item.setData(Qt.UserRole+1,record); status={"pending":"等待解析","parsing":"正在解析","success":"解析成功","failed":"解析失败"}.get(record.get("parse_status"),record.get("parse_status","")); item.setText(f"{record['original_name']} · {record['extension'].upper()} · {record['size_bytes']/1024:.1f} KB · {status}"); break
    def pending_attachment_ids(self)->list[str]: return [self.attachments.item(i).data(Qt.UserRole) for i in range(self.attachments.count())]
    def submit(self)->None:
        text=self.input.toPlainText().strip()
        if text:
            ids=self.pending_attachment_ids(); self.input.clear(); self.send_requested.emit(text,ids)
            # Attachments belong to exactly this message; the next message starts clean.
            self.attachments.clear()
    def remove_selected(self)->None:
        for item in self.attachments.selectedItems(): self.attachments.takeItem(self.attachments.row(item))
    def append(self,role:str,text:str)->None: self.history.appendPlainText(f"{role}：{text}\n")
    def pick_files(self)->None:
        files,_=QFileDialog.getOpenFileNames(self,"选择附件","","文档 (*.docx *.pdf *.xlsx *.xls *.txt *.md)")
        if files: self.attachments_added.emit(files)
    def dragEnterEvent(self,event)->None:
        if event.mimeData().hasUrls(): event.acceptProposedAction()
    def dropEvent(self,event)->None: self.attachments_added.emit([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()])
