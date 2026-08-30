"""Executable entry point for Xiaoche; safe to import without Qt installed."""

from __future__ import annotations

import sys


def main() -> int:
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as exc:
        raise SystemExit("缺少桌宠依赖，请执行：python -m pip install -r requirements-desktop.txt") from exc
    from desktop_pet.window import XiaocheWindow

    app = QApplication(sys.argv)
    app.setApplicationName("小测")
    app.setQuitOnLastWindowClosed(False)
    window = XiaocheWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
