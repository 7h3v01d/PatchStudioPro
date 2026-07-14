"""Patch Studio UI: dialogs.

SPDX-License-Identifier: Apache-2.0
Copyright (c) Leon Priest (7h3v01d)
"""

from __future__ import annotations

import json
from typing import List, Dict, Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableView,
    QAbstractItemView, QHeaderView, QWidget, QToolButton, QFrame
)

from .models import PreflightTableModel, KeyValueTableModel


class PreflightReportDialog(QDialog):
    def __init__(self, parent, report_rows: List[Dict[str, Any]]):
        super().__init__(parent)
        self.setWindowTitle("Preflight Report")
        self.resize(1080, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        card = QFrame()
        card.setObjectName("DialogCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(10)
        layout.addWidget(card)

        title = QLabel("PREFLIGHT REPORT")
        title.setObjectName("DialogTitle")
        card_layout.addWidget(title)

        note = QLabel("Preflight validates that patch file references resolve under the selected root folder before any apply.")
        note.setObjectName("SectionSubtitle")
        note.setWordWrap(True)
        card_layout.addWidget(note)

        self.table = QTableView()
        self.model = PreflightTableModel(report_rows)
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        card_layout.addWidget(self.table, 1)

        btns = QHBoxLayout()
        btns.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.setProperty("role", "primary")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        btns.addWidget(close_btn)
        card_layout.addLayout(btns)


class DiagnosticsDialog(QDialog):
    def __init__(self, parent, title: str, summary_lines: List[str], causes: List[str], fixes: List[str], engineering: Dict[str, Any], jump_callback=None):
        super().__init__(parent)
        self.setWindowTitle("Diagnostics")
        self.resize(1080, 620)
        self._jump_callback = jump_callback

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        card = QFrame()
        card.setObjectName("DialogCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(10)
        layout.addWidget(card)

        title_lbl = QLabel(title.upper())
        title_lbl.setObjectName("DialogTitle")
        card_layout.addWidget(title_lbl)

        what = QLabel("<b>What happened:</b><br>" + "<br>".join(summary_lines))
        what.setWordWrap(True)
        card_layout.addWidget(what)

        if causes:
            causes_lbl = QLabel("<b>Likely causes:</b><br>• " + "<br>• ".join(causes[:3]))
            causes_lbl.setWordWrap(True)
            card_layout.addWidget(causes_lbl)
        if fixes:
            fixes_lbl = QLabel("<b>Recommended fixes:</b><br>• " + "<br>• ".join(fixes[:3]))
            fixes_lbl.setWordWrap(True)
            card_layout.addWidget(fixes_lbl)

        jump_btn = QPushButton("Jump to suspected location")
        jump_btn.setProperty("role", "primary")
        jump_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        jump_btn.setEnabled(jump_callback is not None)
        jump_btn.clicked.connect(self._on_jump)
        card_layout.addWidget(jump_btn)

        toggle = QToolButton()
        toggle.setText("▸ Engineering details")
        toggle.setCheckable(True)
        toggle.setChecked(False)
        toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)

        details_widget = QWidget()
        details_layout = QVBoxLayout(details_widget)
        details_layout.setContentsMargins(0, 0, 0, 0)

        details_table = QTableView()
        eng_rows = []
        for k, v in engineering.items():
            eng_rows.append({"k": str(k), "v": json.dumps(v, indent=2) if isinstance(v, (dict, list)) else str(v)})
        details_model = KeyValueTableModel(eng_rows)
        details_table.setModel(details_model)
        details_table.horizontalHeader().setStretchLastSection(True)
        details_table.setWordWrap(False)
        details_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        details_table.verticalHeader().setVisible(False)
        details_layout.addWidget(details_table)

        details_widget.setVisible(False)

        def on_toggle():
            details_widget.setVisible(toggle.isChecked())
            toggle.setText("▾ Engineering details" if toggle.isChecked() else "▸ Engineering details")

        toggle.toggled.connect(on_toggle)
        card_layout.addWidget(toggle)
        card_layout.addWidget(details_widget, 1)

        btns = QHBoxLayout()
        btns.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btns.addWidget(close_btn)
        card_layout.addLayout(btns)

    def _on_jump(self):
        if self._jump_callback:
            self._jump_callback()
        self.accept()
