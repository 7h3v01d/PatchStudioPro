"""Patch Studio UI: dialogs."""

from __future__ import annotations

import json
from typing import List, Dict, Any, Optional, Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableView,
    QAbstractItemView, QHeaderView, QWidget, QToolButton
)

from .models import PreflightTableModel, KeyValueTableModel

class PreflightReportDialog(QDialog):
    def __init__(self, parent, report_rows: List[Dict[str, Any]]):
        super().__init__(parent)
        self.setWindowTitle("Preflight Report")
        self.resize(980, 420)

        layout = QVBoxLayout(self)
        note = QLabel("Preflight validates that patch file references resolve under the selected root folder before any apply.")
        layout.addWidget(note)

        self.table = QTableView()
        self.model = PreflightTableModel(report_rows)
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        layout.addWidget(self.table)

        btns = QHBoxLayout()
        btns.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btns.addWidget(close_btn)
        layout.addLayout(btns)



class DiagnosticsDialog(QDialog):
    def __init__(self, parent, title: str, summary_lines: List[str], causes: List[str], fixes: List[str], engineering: Dict[str, Any], jump_callback=None):
        super().__init__(parent)
        self.setWindowTitle("Diagnostics")
        self.resize(980, 520)
        self._jump_callback = jump_callback

        layout = QVBoxLayout(self)

        title_lbl = QLabel(f"<b>{title}</b>")
        layout.addWidget(title_lbl)

        what = QLabel("<b>What happened:</b><br>" + "<br>".join(summary_lines))
        layout.addWidget(what)

        if causes:
            causes_lbl = QLabel("<b>Likely causes:</b><br>• " + "<br>• ".join(causes[:3]))
            layout.addWidget(causes_lbl)
        if fixes:
            fixes_lbl = QLabel("<b>Recommended fixes:</b><br>• " + "<br>• ".join(fixes[:3]))
            layout.addWidget(fixes_lbl)

        # Jump button (requirement)
        jump_btn = QPushButton("Jump to suspected location")
        jump_btn.setEnabled(jump_callback is not None)
        jump_btn.clicked.connect(self._on_jump)
        layout.addWidget(jump_btn)

        # Engineering details (collapsed by default)
        toggle = QToolButton()
        toggle.setText("Engineering Details")
        toggle.setCheckable(True)
        toggle.setChecked(False)
        toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)

        details_widget = QWidget()
        details_layout = QVBoxLayout(details_widget)
        details_layout.setContentsMargins(0, 0, 0, 0)

        details_table = QTableView()
        eng_rows = []
        # Flatten for table
        for k, v in engineering.items():
            eng_rows.append({"k": str(k), "v": json.dumps(v, indent=2) if isinstance(v, (dict, list)) else str(v)})
        details_model = KeyValueTableModel(eng_rows)
        details_table.setModel(details_model)
        details_table.horizontalHeader().setStretchLastSection(True)
        details_table.setWordWrap(False)
        details_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        details_layout.addWidget(details_table)

        details_widget.setVisible(False)

        def on_toggle():
            details_widget.setVisible(toggle.isChecked())

        toggle.toggled.connect(on_toggle)
        layout.addWidget(toggle)
        layout.addWidget(details_widget)

        btns = QHBoxLayout()
        btns.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btns.addWidget(close_btn)
        layout.addLayout(btns)

    def _on_jump(self):
        if self._jump_callback:
            self._jump_callback()
        self.accept()


