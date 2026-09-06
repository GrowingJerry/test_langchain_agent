"""Process-safe runtime logging and crash diagnostics."""
from __future__ import annotations

import faulthandler
import logging
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parents[1] / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
APP_LOG = LOG_DIR / "streamlit.log"
_crash_file = None


def configure_runtime_logging() -> logging.Logger:
    global _crash_file
    logger = logging.getLogger("test_agent")
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        handler = logging.FileHandler(APP_LOG, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
    try:
        if _crash_file is None:
            _crash_file = (LOG_DIR / "faulthandler.log").open("a", encoding="utf-8")
        faulthandler.enable(file=_crash_file, all_threads=True)
    except Exception:
        logger.exception("Unable to enable faulthandler")
    return logger


logger = configure_runtime_logging()
