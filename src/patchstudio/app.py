"""Patch Studio application entrypoint (GUI + CLI selftest).

SPDX-License-Identifier: Apache-2.0
Copyright (c) Leon Priest (7h3v01d)
"""

from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication

from .ui.main_window import MainWindow
from .ui.theme import apply_theme
from .core.selftests import PatchStudioSelfTests

APP_NAME = "Patch Studio"
APP_VERSION = "1.0.0"


def _run_selftests_cli() -> int:
    ok, report = PatchStudioSelfTests.run()
    print(report)
    return 0 if ok else 2


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if "--selftest" in argv:
        return _run_selftests_cli()

    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    apply_theme(app)

    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
