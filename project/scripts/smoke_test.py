"""Final-system smoke tests using temporary project storage."""

from __future__ import annotations

import ast
import sys
import tempfile
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


EXPECTED_PAGES = [
    "项目工作台",
    "资料与需求",
    "生成测试用例",
    "用例审查",
    "导出中心",
    "系统设置",
]


def _app_pages() -> List[str]:
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "PAGES" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("app.py 未定义 PAGES")


def main() -> None:
    import application.services.project_service as project_manager_module
    from application.services.generation_context import ContextBuilder
    from infrastructure.documents.ingestor import save_and_ingest_document
    from infrastructure.retrieval.project_knowledge import search_project_chunks
    from application.services.project_service import ProjectManager
    from workflows.scenario.card_extractor import extract_and_save_scenario_cards

    assert _app_pages() == EXPECTED_PAGES, "主导航不是整合后的六页面"
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "st.tabs" not in app_source
    assert "project_pages" not in app_source
    with tempfile.TemporaryDirectory(prefix="final-system-smoke-") as temp:
        root = Path(temp)
        project_manager_module.PROJECT_OUTPUT_ROOT = root / "projects"
        manager = ProjectManager(root / "workspace.db")
        project_a = manager.create_project("隔离验证 A")
        project_b = manager.create_project("隔离验证 B")
        save_and_ingest_document(
            manager,
            project_a["project_id"],
            "a.txt",
            "订单系统应接收订单数据。".encode("utf-8"),
        )
        save_and_ingest_document(
            manager,
            project_b["project_id"],
            "b.txt",
            "告警系统应接收告警数据。".encode("utf-8"),
        )
        assert search_project_chunks(manager, project_a["project_id"], "订单", 5)
        assert not search_project_chunks(manager, project_a["project_id"], "告警", 5)
        assert search_project_chunks(manager, project_b["project_id"], "告警", 5)
        assert not search_project_chunks(manager, project_b["project_id"], "订单", 5)
        conn = manager._connect()
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        conn.close()
        required = {
            "projects",
            "project_documents",
            "project_chunks",
            "scenario_cards",
            "case_generation_contexts",
            "generated_cases",
            "case_quality_scores",
            "trace_sources",
        }
        assert required <= tables, required - tables
        assert (
            ContextBuilder and extract_and_save_scenario_cards
        )
    print("final-system-smoke: OK")
    print("pages:", " / ".join(EXPECTED_PAGES))
    print("project-isolation: OK")
    print("database-schema: OK")


if __name__ == "__main__":
    main()
