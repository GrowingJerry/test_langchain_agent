# -*- coding: utf-8 -*-
"""本地测试用例库：导入、标准化、SQLite + FTS5 检索。"""

import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from models.schemas import LibraryCaseItem

# 列名别名映射到统一字段
COLUMN_ALIASES = {
    "case_id": [
        "case_id",
        "用例编号",
        "用例编号",
        "编号",
        "id",
        "caseid",
        "tc_id",
    ],
    "case_name": [
        "case_name",
        "用例名称",
        "用例名称",
        "名称",
        "标题",
        "标题",
    ],
    "requirement_text": [
        "requirement_text",
        "需求",
        "需求描述",
        "requirement",
        "需求文本",
    ],
    "six_quality_attribute": [
        "six_quality_attribute",
        "六性",
        "质量特性",
        "质量特性",
        "六性类别",
    ],
    "test_object": ["test_object", "测试对象", "测试对象", "对象"],
    "test_type": ["test_type", "测试类型", "测试类型", "类型"],
    "test_method": ["test_method", "测试方法", "测试方法", "方法"],
    "test_condition": ["test_condition", "测试条件", "测试条件"],
    "test_environment": ["test_environment", "测试环境", "测试环境", "环境"],
    "test_steps": ["test_steps", "测试步骤", "测试步骤", "步骤"],
    "expected_result": ["expected_result", "预期结果", "预期结果"],
    "pass_criteria": ["pass_criteria", "通过准则", "通过准则", "判据"],
    "record_items": ["record_items", "记录项", "记录项"],
    "test_result_template": ["test_result_template", "结果模板", "结果模板"],
    "source_file": ["source_file", "来源", "来源"],
    "tags": ["tags", "标签", "标签"],
}


def _norm_col(name: str) -> str:
    """列名规范化：去空格、小写。"""
    return re.sub(r"\s+", "", str(name).strip().lower())


def _load_pandas():
    """Load pandas only when importing tabular case-library files."""
    try:
        import pandas as pd
    except Exception as exc:
        raise ImportError(
            "导入 Excel/CSV 用例库需要可用的 pandas 环境。"
            "请检查 numpy、pandas、numexpr、bottleneck 版本是否匹配。"
        ) from exc
    return pd


def _find_column(df: Any, unified: str) -> Optional[str]:
    """在 DataFrame 中查找对应统一字段的实际列名。"""
    aliases = COLUMN_ALIASES.get(unified, [unified])
    lower_map = {_norm_col(c): c for c in df.columns}
    for a in aliases:
        key = _norm_col(a)
        if key in lower_map:
            return lower_map[key]
    return None


def normalize_case(row: Dict[str, Any], source_file: str = "") -> LibraryCaseItem:
    """将一行字典标准化为 LibraryCaseItem。"""

    def g(*keys: str, default: str = "") -> str:
        for k in keys:
            if (
                k in row
                and row[k] is not None
                and str(row[k]).strip()
                and str(row[k]).lower() != "nan"
            ):
                return str(row[k]).strip()
        return default

    return LibraryCaseItem(
        case_id=g("case_id") or g("id") or "LIB-UNKNOWN",
        case_name=g("case_name") or g("case_id"),
        requirement_text=g("requirement_text"),
        six_quality_attribute=g("six_quality_attribute"),
        test_object=g("test_object"),
        test_type=g("test_type"),
        test_method=g("test_method"),
        test_condition=g("test_condition"),
        test_environment=g("test_environment"),
        test_steps=g("test_steps"),
        expected_result=g("expected_result"),
        pass_criteria=g("pass_criteria"),
        record_items=g("record_items"),
        test_result_template=g("test_result_template"),
        source_file=source_file or g("source_file"),
        tags=g("tags"),
    )


def _row_to_dict_from_df(df: Any, idx: int, source_file: str) -> LibraryCaseItem:
    """将 DataFrame 一行映射为统一字段。"""
    raw = {str(c): df.at[idx, c] for c in df.columns}
    mapped: Dict[str, Any] = {}
    for unified in COLUMN_ALIASES:
        col = _find_column(df, unified)
        if col is not None:
            mapped[unified] = raw.get(col, "")
    return normalize_case(mapped, source_file=source_file)


class CaseLibraryManager:
    """用例库管理：导入、建索引、检索。"""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        """建立数据库连线。"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """初始化表与 FTS5 虚表。"""
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS library_cases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id TEXT NOT NULL,
                    case_name TEXT,
                    requirement_text TEXT,
                    six_quality_attribute TEXT,
                    test_object TEXT,
                    test_type TEXT,
                    test_method TEXT,
                    test_condition TEXT,
                    test_environment TEXT,
                    test_steps TEXT,
                    expected_result TEXT,
                    pass_criteria TEXT,
                    record_items TEXT,
                    test_result_template TEXT,
                    source_file TEXT,
                    tags TEXT,
                    UNIQUE(case_id)
                )
                """
            )
            cur.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS library_cases_fts
                USING fts5(case_id UNINDEXED, search_blob, tokenize = 'unicode61')
                """
            )
            conn.commit()
        finally:
            conn.close()

    def _build_search_blob(self, item: LibraryCaseItem) -> str:
        """拼接可检索文本。"""
        parts = [
            item.case_id,
            item.case_name,
            item.requirement_text,
            item.six_quality_attribute,
            item.test_object,
            item.test_type,
            item.test_method,
            item.test_condition,
            item.test_environment,
            item.test_steps,
            item.expected_result,
            item.tags,
        ]
        return "\n".join(p for p in parts if p)

    def _fts_rebuild_for_row(
        self, conn: sqlite3.Connection, case_id: str, blob: str
    ) -> None:
        """更新 FTS 行：先删后插。"""
        cur = conn.cursor()
        cur.execute("DELETE FROM library_cases_fts WHERE case_id = ?", (case_id,))
        cur.execute(
            "INSERT INTO library_cases_fts(case_id, search_blob) VALUES (?, ?)",
            (case_id, blob),
        )

    def build_sqlite_index(self) -> None:
        """若表已存在则仅保证结构；索引在导入时维护。"""
        self._init_db()

    def import_excel(self, path: Path, source_label: Optional[str] = None) -> int:
        """从 Excel 导入用例，返回导入条数。"""
        pd = _load_pandas()
        df = pd.read_excel(path)
        label = source_label or path.name
        return self._import_dataframe(df, label)

    def import_csv(
        self,
        path: Path,
        encoding: str = "utf-8-sig",
        source_label: Optional[str] = None,
    ) -> int:
        """从 CSV 导入。"""
        pd = _load_pandas()
        df = pd.read_csv(path, encoding=encoding)
        label = source_label or path.name
        return self._import_dataframe(df, label)

    def import_json(self, path: Path, source_label: Optional[str] = None) -> int:
        """从 JSON 导入（数组或 {cases:[]}）。"""
        raw = json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
        if isinstance(raw, dict) and "cases" in raw:
            raw = raw["cases"]
        if not isinstance(raw, list):
            raise ValueError("JSON 顶层须为数组或包含 cases 数组")
        label = source_label or path.name
        count = 0
        conn = self._connect()
        try:
            for obj in raw:
                if not isinstance(obj, dict):
                    continue
                item = normalize_case(obj, source_file=label)
                self._upsert_item(conn, item)
                count += 1
            conn.commit()
        finally:
            conn.close()
        return count

    def _import_dataframe(self, df: Any, source_file: str) -> int:
        """DataFrame 批量导入。"""
        count = 0
        conn = self._connect()
        try:
            for idx in range(len(df)):
                item = _row_to_dict_from_df(df, idx, source_file)
                if not item.case_id or item.case_id == "LIB-UNKNOWN":
                    item.case_id = f"LIB-IMP-{idx + 1:05d}"
                self._upsert_item(conn, item)
                count += 1
            conn.commit()
        finally:
            conn.close()
        return count

    def _upsert_item(self, conn: sqlite3.Connection, item: LibraryCaseItem) -> None:
        """插入或替换单条用例并更新 FTS。"""
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO library_cases (
                case_id, case_name, requirement_text, six_quality_attribute,
                test_object, test_type, test_method, test_condition, test_environment,
                test_steps, expected_result, pass_criteria, record_items,
                test_result_template, source_file, tags
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(case_id) DO UPDATE SET
                case_name=excluded.case_name,
                requirement_text=excluded.requirement_text,
                six_quality_attribute=excluded.six_quality_attribute,
                test_object=excluded.test_object,
                test_type=excluded.test_type,
                test_method=excluded.test_method,
                test_condition=excluded.test_condition,
                test_environment=excluded.test_environment,
                test_steps=excluded.test_steps,
                expected_result=excluded.expected_result,
                pass_criteria=excluded.pass_criteria,
                record_items=excluded.record_items,
                test_result_template=excluded.test_result_template,
                source_file=excluded.source_file,
                tags=excluded.tags
            """,
            (
                item.case_id,
                item.case_name,
                item.requirement_text,
                item.six_quality_attribute,
                item.test_object,
                item.test_type,
                item.test_method,
                item.test_condition,
                item.test_environment,
                item.test_steps,
                item.expected_result,
                item.pass_criteria,
                item.record_items,
                item.test_result_template,
                item.source_file,
                item.tags,
            ),
        )
        blob = self._build_search_blob(item)
        self._fts_rebuild_for_row(conn, item.case_id, blob)

    def get_case_by_id(self, case_id: str) -> Optional[LibraryCaseItem]:
        """按 case_id 查询单条。"""
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM library_cases WHERE case_id = ?", (case_id,))
            row = cur.fetchone()
            if not row:
                return None
            return LibraryCaseItem(
                case_id=row["case_id"],
                case_name=row["case_name"] or "",
                requirement_text=row["requirement_text"] or "",
                six_quality_attribute=row["six_quality_attribute"] or "",
                test_object=row["test_object"] or "",
                test_type=row["test_type"] or "",
                test_method=row["test_method"] or "",
                test_condition=row["test_condition"] or "",
                test_environment=row["test_environment"] or "",
                test_steps=row["test_steps"] or "",
                expected_result=row["expected_result"] or "",
                pass_criteria=row["pass_criteria"] or "",
                record_items=row["record_items"] or "",
                test_result_template=row["test_result_template"] or "",
                source_file=row["source_file"] or "",
                tags=row["tags"] or "",
            )
        finally:
            conn.close()

    def count_cases(self) -> int:
        """用例总数。"""
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM library_cases")
            return int(cur.fetchone()[0])
        finally:
            conn.close()

    def _tokenize_query(self, q: str) -> List[str]:
        """简单分词：中英数片段。"""
        if not q:
            return []
        parts = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9]+", q)
        return [p for p in parts if len(p) >= 2 or (p.isalnum() and len(p) >= 1)]

    def search_similar_cases(
        self,
        query_text: str,
        six_filter: Optional[str] = None,
        method_filter: Optional[str] = None,
        scenario_keywords: str = "",
        top_k: int = 5,
    ) -> List[Tuple[LibraryCaseItem, float, str]]:
        """
        检索相似用例：FTS + 过滤 + 简单评分。
        返回 [(LibraryCaseItem, score, reason), ...]
        """
        conn = self._connect()
        results: List[Tuple[LibraryCaseItem, float, str]] = []
        try:
            cur = conn.cursor()
            tokens = self._tokenize_query(
                " ".join([query_text or "", scenario_keywords or ""])
            )
            if not tokens:
                tokens = [query_text[:20]] if query_text else []

            # FTS MATCH：拼接为 OR
            fts_query = " OR ".join(
                '"{}"'.format(t.replace('"', "")) for t in tokens[:12]
            )
            case_ids_ordered: List[str] = []
            if fts_query:
                try:
                    cur.execute(
                        """
                        SELECT case_id, rank
                        FROM library_cases_fts
                        WHERE library_cases_fts MATCH ?
                        ORDER BY rank
                        LIMIT ?
                        """,
                        (fts_query, max(top_k * 8, 20)),
                    )
                    for r in cur.fetchall():
                        case_ids_ordered.append(r["case_id"])
                except sqlite3.OperationalError:
                    case_ids_ordered = []

            if not case_ids_ordered:
                cur.execute(
                    "SELECT case_id FROM library_cases LIMIT ?", (max(top_k * 4, 20),)
                )
                case_ids_ordered = [r["case_id"] for r in cur.fetchall()]

            six_f = (six_filter or "").strip()
            meth_f = (method_filter or "").strip()

            for cid in case_ids_ordered:
                item = self.get_case_by_id(cid)
                if not item:
                    continue
                if six_f and six_f not in (item.six_quality_attribute or ""):
                    continue
                if meth_f and meth_f not in (item.test_method or ""):
                    continue

                score, reason = self._score_match(
                    item, query_text, scenario_keywords, tokens
                )
                results.append((item, score, reason))

            results.sort(key=lambda x: x[1], reverse=True)
            return results[:top_k]
        finally:
            conn.close()

    def _score_match(
        self,
        item: LibraryCaseItem,
        query_text: str,
        scenario_keywords: str,
        tokens: List[str],
    ) -> Tuple[float, str]:
        """简单相似度：关键词命中加权。"""
        text = "\n".join(
            [
                item.requirement_text,
                item.case_name,
                item.test_method,
                item.test_steps,
                item.tags or "",
            ]
        )
        tl = text.lower()
        q = (query_text or "").lower()
        sk = (scenario_keywords or "").lower()
        score = 0.0
        reasons = []

        if q and q[:80] in tl:
            score += 30
            reasons.append("需求短语命中")
        for t in tokens:
            if len(t) >= 2 and t.lower() in tl:
                score += 8
        if sk:
            for w in re.findall(r"[\u4e00-\u9fff]{2,}", sk):
                if w in text:
                    score += 5
                    reasons.append("场景词:%s" % w)

        if not reasons:
            reasons.append("关键词/FTS 弱匹配")
        return score, ";".join(reasons[:3])
