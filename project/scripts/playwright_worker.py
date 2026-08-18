"""Isolated Playwright worker. Never imported by Streamlit."""
from __future__ import annotations
import argparse
import json
import logging
from pathlib import Path
from application.services.offline_site_service import explore_site

def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--request",required=True)
    parser.add_argument("--output",required=True)
    args=parser.parse_args()
    try:
        request=json.loads(Path(args.request).read_text("utf-8-sig"))
        result=explore_site(Path(request["root"]),request["entry"],request["plan"],Path(request["evidence_dir"]))
        Path(args.output).write_text(json.dumps({"ok":True,"result":result},ensure_ascii=False),"utf-8")
        return 0
    except BaseException as exc:
        logging.exception("Playwright worker failed")
        Path(args.output).write_text(json.dumps({"ok":False,"error":f"{type(exc).__name__}: {exc}"},ensure_ascii=False),"utf-8")
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
