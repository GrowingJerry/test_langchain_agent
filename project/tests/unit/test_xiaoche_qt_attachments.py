from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication
from desktop_pet.chat_panel import ChatPanel


def test_qt_send_signal_carries_attachment_ids_and_clears_pending_list()->None:
    app=QApplication.instance() or QApplication([]); panel=ChatPanel(); captured=[]; panel.send_requested.connect(lambda text,ids:captured.append((text,ids)))
    panel.add_attachment_record({"attachment_id":"att-1","original_name":"需求.docx","extension":".docx","size_bytes":100,"parse_status":"success"}); panel.input.setPlainText("这是什么"); panel.submit(); app.processEvents()
    assert captured==[("这是什么",["att-1"])] and panel.pending_attachment_ids()==[]


def test_qt_next_message_does_not_reuse_previous_attachment()->None:
    app=QApplication.instance() or QApplication([]); panel=ChatPanel(); captured=[]; panel.send_requested.connect(lambda text,ids:captured.append((text,ids)))
    panel.add_attachment_record({"attachment_id":"att-1","original_name":"需求.docx","extension":".docx","size_bytes":100,"parse_status":"success"}); panel.input.setPlainText("解析附件"); panel.submit(); panel.input.setPlainText("普通聊天"); panel.submit(); app.processEvents(); assert captured[1]==("普通聊天",[])
