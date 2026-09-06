"""Fail-fast verification for an upgraded offline installation."""
from __future__ import annotations
import argparse, json, sqlite3, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from application.services.project_service import ProjectManager
from config.settings import settings
def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--expected-model",default="")
    args=parser.parse_args()
    db=settings.output_sqlite_dir/"project_workspace.db"; ProjectManager(db)
    with sqlite3.connect(db) as conn:
        tables={x[0] for x in conn.execute("select name from sqlite_master where type='table'")}
    required={"generation_jobs","generation_job_events","coverage_test_points","atomic_generation_checkpoints","case_test_point_links"}
    missing=sorted(required-tables)
    model_ok=not args.expected_model or settings.test_case_model == args.expected_model
    result={"ok":not missing and model_ok,"model":settings.test_case_model,
        "expected_model":args.expected_model,"model_ok":model_ok,"ollama_base_url":settings.ollama_base_url,
        "ollama_num_ctx":settings.ollama_num_ctx,"max_cases_per_call":settings.generation_max_cases_per_model_call,
        "database":str(db),"missing_tables":missing}
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result["ok"] else 1
if __name__=="__main__": raise SystemExit(main())
