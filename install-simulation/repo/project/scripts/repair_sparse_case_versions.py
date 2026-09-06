"""Diagnose and explicitly repair formal cases overwritten by legacy sparse versions."""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from domain.case_schema import merge_case, normalize_case, validate_case  # noqa: E402


def _load(value: str | None) -> dict:
    return json.loads(value or "{}")


def _complete(case: dict) -> bool:
    try:
        validate_case(case)
        return all(case.get(key) for key in ("case_id", "test_purpose", "prerequisites", "pass_criteria"))
    except (TypeError, ValueError):
        return False


def diagnose(conn: sqlite3.Connection, project_id: str, case_id: str | None = None) -> list[dict]:
    sql = "SELECT case_id,case_json FROM generated_cases WHERE project_id=?"
    params: tuple = (project_id,)
    if case_id:
        sql += " AND case_id=?"; params += (case_id,)
    reports=[]
    for row in conn.execute(sql, params):
        current=_load(row["case_json"]); versions=list(conn.execute(
            "SELECT version_no,case_json,acceptance_status FROM case_versions WHERE project_id=? AND case_id=? ORDER BY version_no DESC",
            (project_id,row["case_id"])))
        accepted=next((v for v in versions if v["acceptance_status"] == "accepted"),None)
        complete=next((v for v in versions if _complete(_load(v["case_json"]))),None)
        canonical=normalize_case(current)
        missing=[key for key in ("test_steps","expected_result","test_purpose","prerequisites","pass_criteria") if not canonical.get(key)]
        reports.append({"project_id":project_id,"case_id":row["case_id"],"fields":sorted(current),
            "missing_key_fields":missing,"has_legacy_steps_expected":bool(current.get("steps") or current.get("expected")),
            "accepted_version":accepted["version_no"] if accepted else None,
            "accepted_matches_generated":bool(accepted and normalize_case(_load(accepted["case_json"])) == canonical),
            "latest_complete_version":complete["version_no"] if complete else None,
            "recoverable":bool(missing and complete),
            "recommendation":"--apply 创建新的修复版本并接受" if missing and complete else "无需修复" if not missing else "无可靠完整历史版本，请人工确认"})
    return reports


def repair(database: Path, project_id: str, case_id: str) -> dict:
    stamp=datetime.now().strftime("%Y%m%d-%H%M%S")
    backup=database.with_name(f"{database.stem}.before-sparse-repair-{stamp}{database.suffix}")
    shutil.copy2(database,backup)
    conn=sqlite3.connect(database); conn.row_factory=sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        formal=conn.execute("SELECT case_json FROM generated_cases WHERE project_id=? AND case_id=?",(project_id,case_id)).fetchone()
        if not formal: raise KeyError("目标正式用例不存在")
        current=_load(formal[0]); versions=list(conn.execute("SELECT * FROM case_versions WHERE project_id=? AND case_id=? ORDER BY version_no DESC",(project_id,case_id)))
        base=next((v for v in versions if _complete(_load(v["case_json"]))),None)
        if not base: raise ValueError("无法可靠确定合并基线，请人工确认")
        merged=merge_case(_load(base["case_json"]),current); merged=validate_case(merged,case_id=case_id)
        version_no=max((int(v["version_no"]) for v in versions),default=0)+1
        changed=sorted(key for key in set(current)|set(merged) if normalize_case(current).get(key)!=merged.get(key))
        conn.execute("UPDATE case_versions SET acceptance_status='rejected' WHERE project_id=? AND case_id=? AND acceptance_status='accepted'",(project_id,case_id))
        conn.execute("INSERT INTO case_versions(project_id,case_id,version_no,parent_version_no,case_json,user_feedback,context_snapshot_json,model_name,changed_fields_json,acceptance_status,operator) VALUES(?,?,?,?,?,?,?,?,?,'accepted',?)",
            (project_id,case_id,version_no,versions[0]["version_no"] if versions else None,json.dumps(merged,ensure_ascii=False),f"修复稀疏接受版本；完整基线 v{base['version_no']}",json.dumps({"repair_from_complete_version":base["version_no"]},ensure_ascii=False),"repair-script",json.dumps(changed,ensure_ascii=False),"repair-script"))
        updated=conn.execute("UPDATE generated_cases SET case_json=? WHERE project_id=? AND case_id=?",(json.dumps(merged,ensure_ascii=False),project_id,case_id))
        if updated.rowcount != 1: raise KeyError("目标正式用例更新失败")
        saved=_load(conn.execute("SELECT case_json FROM generated_cases WHERE project_id=? AND case_id=?",(project_id,case_id)).fetchone()[0])
        validate_case(saved,case_id=case_id); conn.commit()
        return {"backup":str(backup),"new_version":version_no,"base_version":base["version_no"],"changed_fields":changed}
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()


def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--database",required=True,type=Path); parser.add_argument("--project-id",required=True); parser.add_argument("--case-id"); parser.add_argument("--apply",action="store_true"); args=parser.parse_args()
    database=args.database.resolve()
    if not database.is_file() or database.suffix.lower() not in {".db",".sqlite",".sqlite3"}: parser.error("--database 必须是已存在的具体 SQLite 文件")
    if args.apply and not args.case_id: parser.error("--apply 必须同时指定 --case-id")
    conn=sqlite3.connect(database); conn.row_factory=sqlite3.Row
    try: reports=diagnose(conn,args.project_id,args.case_id)
    finally: conn.close()
    print(json.dumps(reports,ensure_ascii=False,indent=2))
    if args.apply:
        report=next((item for item in reports if item["case_id"]==args.case_id),None)
        if not report or not report["recoverable"]: raise SystemExit("目标用例没有可靠的自动恢复候选，未修改数据库")
        print(json.dumps(repair(database,args.project_id,args.case_id),ensure_ascii=False,indent=2))
    return 0


if __name__ == "__main__": raise SystemExit(main())
