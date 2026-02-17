"""Patch Studio application entrypoint (GUI + CLI selftest)."""

from __future__ import annotations

import sys
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication

from .ui.main_window import MainWindow
from .core.selftests import PatchStudioSelfTests

def _run_selftests_cli() -> int:
    ok, report = PatchStudioSelfTests.run()
    print(report)
    return 0 if ok else 2

def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if "--selftest" in argv:
        return _run_selftests_cli()

    app = QApplication(argv)
    app.setFont(QFont("Consolas", 10))
    w = MainWindow()
    w.show()
    return app.exec()

if __name__ == "__main__":
    raise SystemExit(main())
