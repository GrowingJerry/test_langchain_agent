"""Capture site-specific Settings values before an in-place code upgrade."""
from __future__ import annotations
import argparse, importlib.util, json, sys
from pathlib import Path

MANAGED={
    "generation_max_seconds","generation_model_call_max_seconds","generation_idle_timeout_seconds",
    "generation_batch_max_seconds","generation_job_lease_seconds","generation_job_max_attempts",
    "generation_job_retry_delay_seconds","generation_worker_idle_exit_seconds",
    "generation_max_cases_per_model_call","data_dir","outputs_dir",
}

def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--settings-file",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True); args=parser.parse_args()
    spec=importlib.util.spec_from_file_location("preupgrade_settings",args.settings_file)
    if not spec or not spec.loader: raise RuntimeError("无法加载原 settings.py")
    module=importlib.util.module_from_spec(spec); sys.modules[spec.name]=module; spec.loader.exec_module(module)
    values=module.settings.model_dump(mode="json")
    preserved={key:value for key,value in values.items() if key not in MANAGED}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(preserved,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"model":preserved.get("test_case_model"),"ollama_base_url":preserved.get("ollama_base_url"),
                      "ollama_num_ctx":preserved.get("ollama_num_ctx"),"saved":str(args.output)},ensure_ascii=False))
    return 0
if __name__=="__main__": raise SystemExit(main())
