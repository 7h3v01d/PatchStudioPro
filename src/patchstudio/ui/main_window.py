"""Patch Studio UI: main window.

SPDX-License-Identifier: Apache-2.0
Copyright (c) Leon Priest (7h3v01d)
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QBrush, QColor, QStandardItemModel, QStandardItem
from PyQt6.QtWidgets import (
    QMainWindow, QToolBar, QStatusBar, QSplitter,
    QListView, QTableView, QDockWidget,
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFileDialog, QMessageBox, QCheckBox, QSpinBox,
    QHeaderView, QAbstractItemView, QDialog,
    QFrame, QSizePolicy
)

from ..core.normalizer import PatchInputNormalizer
from ..core.parser import UnifiedDiffParser
from ..core.applier import PatchApplier
from ..core.diffgen import DiffGenerator
from ..core.models import PatchSet, ApplyResult
from ..core.selftests import PatchStudioSelfTests

from .models import DiffAlignmentModel, LogTableModel
from .delegates import SyntaxEmphasisDelegate
from .dialogs import PreflightReportDialog, DiagnosticsDialog
from .theme import PALETTE, build_font


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Patch Studio")
        self.resize(1460, 900)
        self.setMinimumSize(1180, 760)

        self.normalizer = PatchInputNormalizer()
        self.parser = UnifiedDiffParser()
        self.applier = PatchApplier()
        self.generator = DiffGenerator()

        # Session state
        self.root_folder: Optional[str] = None
        self.loaded_file: Optional[str] = None
        self.patch_text: str = ""
        self.patchset: Optional[PatchSet] = None
        self.preflight_report: List[Dict[str, Any]] = []
        self.preview_result: Optional[ApplyResult] = None
        self.baseline_texts: Dict[str, str] = {}

        self.setFont(build_font())

        self._build_toolbar()
        self._build_central()
        self._build_docks()
        self._build_status()

        self._refresh_actions()
        self._update_session_cards()
        self._log_info("Ready.", component="ui")

    # ---------------- UI Construction ----------------

    def _build_toolbar(self):
        tb = QToolBar("Main")
        tb.setMovable(False)
        self.addToolBar(tb)
        self.main_toolbar = tb

        self.act_open = QAction("Open File", self)
        self.act_open.triggered.connect(self._open_file)

        self.act_open_folder = QAction("Open Folder", self)
        self.act_open_folder.triggered.connect(self._open_folder)

        self.act_load_diff = QAction("Load Diff", self)
        self.act_load_diff.triggered.connect(self._load_diff)

        self.act_preflight = QAction("Preflight", self)
        self.act_preflight.triggered.connect(self._run_preflight)

        self.act_preview = QAction("Preview", self)
        self.act_preview.triggered.connect(self._run_preview)

        self.act_apply = QAction("Apply", self)
        self.act_apply.triggered.connect(self._run_apply)

        self.act_generate = QAction("Generate", self)
        self.act_generate.triggered.connect(self._run_generate)

        self.act_save_diff = QAction("Save Diff", self)
        self.act_save_diff.triggered.connect(self._save_diff)

        self.act_advanced = QAction("Advanced", self)
        self.act_advanced.triggered.connect(self._toggle_advanced)

        self.act_help = QAction("Help", self)
        self.act_help.triggered.connect(self._show_help)

        for action in [
            self.act_open, self.act_open_folder, self.act_load_diff,
            self.act_preflight, self.act_preview, self.act_apply,
            self.act_generate, self.act_save_diff, self.act_advanced, self.act_help,
        ]:
            tb.addAction(action)

    def _build_central(self):
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(14, 14, 14, 12)
        root_layout.setSpacing(12)

        hero = QFrame()
        hero.setObjectName("HeroCard")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(18, 18, 18, 18)
        hero_layout.setSpacing(14)

        hero_top = QHBoxLayout()
        hero_top.setSpacing(18)

        title_block = QVBoxLayout()
        title_block.setSpacing(6)

        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        title = QLabel("PATCH STUDIO")
        title.setObjectName("HeroTitle")
        badge = QLabel("PRO")
        badge.setObjectName("HeroBadge")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_row.addWidget(title)
        title_row.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
        title_row.addStretch(1)

        subtitle = QLabel("Deterministic patch review — preflight, dry-run preview, then a gated apply. Nothing touches disk until you say so.")
        subtitle.setObjectName("HeroSubtitle")
        title_block.addLayout(title_row)
        title_block.addWidget(subtitle)
        hero_top.addLayout(title_block, 2)

        cmd_block = QHBoxLayout()
        cmd_block.setSpacing(8)
        self.btn_open_folder = self._make_action_button("Workspace", self._open_folder)
        self.btn_load_diff = self._make_action_button("Load Patch", self._load_diff)
        self.btn_preflight = self._make_action_button("Preflight", self._run_preflight)
        self.btn_preview = self._make_action_button("Preview", self._run_preview, role="primary")
        self.btn_apply = self._make_action_button("Apply", self._run_apply, role="danger")
        for btn in [self.btn_open_folder, self.btn_load_diff, self.btn_preflight, self.btn_preview, self.btn_apply]:
            cmd_block.addWidget(btn)
        hero_top.addLayout(cmd_block, 1)
        hero_layout.addLayout(hero_top)

        cards = QHBoxLayout()
        cards.setSpacing(12)
        self.card_root, self.card_root_value = self._make_stat_card(cards, "WORKSPACE ROOT", "No folder selected")
        self.card_patch, self.card_patch_value = self._make_stat_card(cards, "PATCH SESSION", "No patch loaded")
        self.card_preview, self.card_preview_value = self._make_stat_card(cards, "PREVIEW STATE", "Idle")
        self.card_apply, self.card_apply_value = self._make_stat_card(cards, "APPLY GATE", "Blocked")
        hero_layout.addLayout(cards)
        root_layout.addWidget(hero)

        workspace_card = QFrame()
        workspace_card.setObjectName("PanelCard")
        workspace_layout = QVBoxLayout(workspace_card)
        workspace_layout.setContentsMargins(14, 14, 14, 14)
        workspace_layout.setSpacing(10)

        section_top = QHBoxLayout()
        section_titles = QVBoxLayout()
        section_titles.setSpacing(2)
        section_label = QLabel("PATCH REVIEW SURFACE")
        section_label.setObjectName("SectionTitle")
        section_sub = QLabel("Select a patch file on the left, inspect the aligned diff on the right, then review diagnostics before apply.")
        section_sub.setObjectName("SectionSubtitle")
        section_titles.addWidget(section_label)
        section_titles.addWidget(section_sub)
        section_top.addLayout(section_titles)
        section_top.addStretch(1)

        self.inline_open_btn = self._make_action_button("Open File", self._open_file, role="ghost")
        self.inline_generate_btn = self._make_action_button("Generate Diff", self._run_generate, role="ghost")
        self.inline_save_btn = self._make_action_button("Save Diff", self._save_diff, role="ghost")
        for btn in [self.inline_open_btn, self.inline_generate_btn, self.inline_save_btn]:
            section_top.addWidget(btn)
        workspace_layout.addLayout(section_top)

        splitter = QSplitter()
        splitter.setOrientation(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        left_panel = QFrame()
        left_panel.setObjectName("PanelCard")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(8)
        left_title = QLabel("PATCH FILES")
        left_title.setObjectName("SectionTitle")
        left_sub = QLabel("Resolved files, status annotations, and binary markers.")
        left_sub.setObjectName("SectionSubtitle")
        left_layout.addWidget(left_title)
        left_layout.addWidget(left_sub)

        self.file_list = QListView()
        self.file_list.setMinimumWidth(280)
        self.file_list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.file_model = QStandardItemModel()
        self.file_list.setModel(self.file_model)
        self.file_list.selectionModel().selectionChanged.connect(self._on_file_selected)
        left_layout.addWidget(self.file_list, 1)

        right_panel = QFrame()
        right_panel.setObjectName("PanelCard")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(12, 12, 12, 12)
        right_layout.setSpacing(8)
        right_title = QLabel("ALIGNED DIFF VIEW")
        right_title.setObjectName("SectionTitle")
        right_sub = QLabel("Old and new line state rendered side-by-side with syntax emphasis and hunk headers.")
        right_sub.setObjectName("SectionSubtitle")
        right_layout.addWidget(right_title)
        right_layout.addWidget(right_sub)

        self.diff_table = QTableView()
        self.diff_model = DiffAlignmentModel()
        self.diff_table.setModel(self.diff_model)
        self.diff_table.setWordWrap(False)
        self.diff_table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.diff_table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.diff_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.diff_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.diff_table.setAlternatingRowColors(False)
        self.diff_table.setShowGrid(False)
        self.diff_table.setSortingEnabled(False)
        self.diff_table.verticalHeader().setVisible(False)
        self.diff_table.setCornerButtonEnabled(False)

        hdr = self.diff_table.horizontalHeader()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.diff_table.setColumnWidth(0, 76)
        self.diff_table.setColumnWidth(2, 76)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)

        self.syntax_delegate = SyntaxEmphasisDelegate(self.diff_table)
        self.diff_table.setItemDelegateForColumn(1, self.syntax_delegate)
        self.diff_table.setItemDelegateForColumn(3, self.syntax_delegate)
        right_layout.addWidget(self.diff_table, 1)

        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([320, 980])
        workspace_layout.addWidget(splitter, 1)

        root_layout.addWidget(workspace_card, 1)
        self.setCentralWidget(root)

    def _build_docks(self):
        self.log_dock = QDockWidget("OPERATIONAL LOG", self)
        self.log_dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea)
        self.log_dock.setVisible(False)

        log_widget = QWidget()
        log_layout = QVBoxLayout(log_widget)
        log_layout.setContentsMargins(8, 8, 8, 8)
        log_layout.setSpacing(8)

        log_head = QLabel("Structured engine events, warnings, and apply diagnostics.")
        log_head.setObjectName("SectionSubtitle")
        log_layout.addWidget(log_head)

        self.log_table = QTableView()
        self.log_model = LogTableModel()
        self.log_table.setModel(self.log_model)
        self.log_table.horizontalHeader().setStretchLastSection(True)
        self.log_table.setWordWrap(False)
        self.log_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.log_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.log_table.verticalHeader().setVisible(False)
        log_layout.addWidget(self.log_table)
        self.log_dock.setWidget(log_widget)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.log_dock)

        self.adv_dock = QDockWidget("ADVANCED CONTROLS", self)
        self.adv_dock.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea)
        self.adv_dock.setVisible(False)

        adv_widget = QWidget()
        adv_layout = QVBoxLayout(adv_widget)
        adv_layout.setContentsMargins(10, 10, 10, 10)
        adv_layout.setSpacing(12)

        intro = QLabel("Gating stays conservative by default. These switches deliberately expose riskier behaviors.")
        intro.setWordWrap(True)
        intro.setObjectName("SectionSubtitle")
        adv_layout.addWidget(intro)

        safety_box = QFrame()
        safety_box.setObjectName("PanelCard")
        safety_layout = QVBoxLayout(safety_box)
        safety_layout.setContentsMargins(12, 12, 12, 12)
        safety_layout.setSpacing(8)

        self.chk_strict = QCheckBox("Strict filename match")
        self.chk_fuzzy = QCheckBox("Best-effort fuzzy apply")
        fuzzy_row = QHBoxLayout()
        fuzzy_row.addWidget(QLabel("Fuzzy window size (lines)"))
        self.spn_fuzzy = QSpinBox()
        self.spn_fuzzy.setRange(1, 5000)
        self.spn_fuzzy.setValue(200)
        fuzzy_row.addWidget(self.spn_fuzzy)
        self.chk_ignore_ws = QCheckBox("Ignore whitespace differences")
        self.chk_conflict = QCheckBox("Conflict marker mode (3-way style markers)")
        self.chk_allow_meta = QCheckBox("Allow rename/delete/mode changes")
        self.chk_partial = QCheckBox("Partial apply per-file override")
        self.chk_preserve_eol = QCheckBox("Preserve original line endings")
        self.chk_preserve_eol.setChecked(True)
        self.chk_allow_conflicted_write = QCheckBox("Allow writing conflicted output")
        self.chk_skip_bin = QCheckBox("Skip unsupported binary files")
        self.chk_skip_bin.setChecked(True)

        for widget in [
            self.chk_strict,
            self.chk_fuzzy,
            self.chk_ignore_ws,
            self.chk_conflict,
            self.chk_allow_meta,
            self.chk_partial,
            self.chk_preserve_eol,
            self.chk_allow_conflicted_write,
            self.chk_skip_bin,
        ]:
            safety_layout.addWidget(widget)
        safety_layout.addLayout(fuzzy_row)
        adv_layout.addWidget(safety_box)
        adv_layout.addStretch(1)

        self.adv_dock.setWidget(adv_widget)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.adv_dock)

        self.menu = self.menuBar().addMenu("Help")
        act_selftests = QAction("Run Self Tests", self)
        act_selftests.triggered.connect(self._run_selftests_ui)
        self.menu.addAction(act_selftests)

    def _build_status(self):
        sb = QStatusBar()
        self.setStatusBar(sb)
        self.status_summary = QLabel()
        self.status_summary.setObjectName("StatusSummary")
        self.status_state = QLabel()
        self.status_state.setObjectName("StatusState")
        self.status_warn = QLabel()
        self.status_warn.setObjectName("StatusWarn")
        sb.addWidget(self.status_summary, 1)
        sb.addPermanentWidget(self.status_state)
        sb.addPermanentWidget(self.status_warn)
        self._set_status("No patch loaded.", state="Idle", warn="Awaiting workspace selection.")

    # ---------------- UI Helpers ----------------

    def _make_action_button(self, text: str, callback, role: str | None = None) -> QPushButton:
        btn = QPushButton(text)
        btn.clicked.connect(callback)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        if role:
            btn.setProperty("role", role)
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        return btn

    def _make_stat_card(self, layout: QHBoxLayout, label_text: str, value_text: str) -> Tuple[QFrame, QLabel]:
        card = QFrame()
        card.setObjectName("StatCard")
        card.setProperty("tone", "idle")
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(4)

        label = QLabel(label_text)
        label.setObjectName("StatLabel")
        value = QLabel(value_text)
        value.setObjectName("StatValue")
        value.setWordWrap(True)
        card_layout.addWidget(label)
        card_layout.addWidget(value)
        layout.addWidget(card)
        return card, value

    def _set_card_tone(self, card: QFrame, tone: str) -> None:
        """Repaint a stat card's accent rail. Tones: idle | live | armed | gated | blocked."""
        if card.property("tone") == tone:
            return
        card.setProperty("tone", tone)
        card.style().unpolish(card)
        card.style().polish(card)

    def _truncate_middle(self, value: str, limit: int = 52) -> str:
        if len(value) <= limit:
            return value
        head = max(12, (limit // 2) - 2)
        tail = max(10, limit - head - 1)
        return f"{value[:head]}…{value[-tail:]}"

    def _update_session_cards(self) -> None:
        root_text = self._truncate_middle(self.root_folder, 54) if self.root_folder else "No folder selected"
        patch_text = "No patch loaded"
        if self.patchset:
            patch_text = f"{self.patchset.total_files()} file(s) • {self.patchset.total_hunks()} hunk(s)"

        preview_text = "Idle"
        if self.preview_result:
            preview_text = "Ready" if self.preview_result.success else "Failed"
            conflicts = len(self.preview_result.summary.get("conflicted_files", []))
            if conflicts:
                preview_text += f" • {conflicts} conflict(s)"

        apply_text = "Blocked"
        if self.preview_result and self.preview_result.success:
            apply_text = "Armed"
            if self.preview_result.summary.get("conflicted_files") and not self.chk_allow_conflicted_write.isChecked():
                apply_text = "Conflict gated"

        self.card_root_value.setText(root_text)
        self.card_patch_value.setText(patch_text)
        self.card_preview_value.setText(preview_text)
        self.card_apply_value.setText(apply_text)

        # Accent rails encode state: teal = loaded, green = go, amber = gated,
        # red = blocked, grey = nothing here yet.
        self._set_card_tone(self.card_root, "live" if self.root_folder else "idle")
        self._set_card_tone(self.card_patch, "live" if self.patchset else "idle")

        if not self.preview_result:
            preview_tone = "idle"
        elif self.preview_result.success:
            preview_tone = "gated" if self.preview_result.summary.get("conflicted_files") else "armed"
        else:
            preview_tone = "blocked"
        self._set_card_tone(self.card_preview, preview_tone)

        if apply_text == "Armed":
            apply_tone = "armed"
        elif apply_text == "Conflict gated":
            apply_tone = "gated"
        else:
            apply_tone = "blocked"
        self._set_card_tone(self.card_apply, apply_tone)

    # ---------------- Utilities ----------------

    def _options(self) -> Dict[str, Any]:
        return {
            "strict_filename_match": self.chk_strict.isChecked(),
            "best_effort_fuzzy_apply": self.chk_fuzzy.isChecked(),
            "fuzzy_window_size": int(self.spn_fuzzy.value()),
            "ignore_whitespace_differences": self.chk_ignore_ws.isChecked(),
            "conflict_marker_mode": self.chk_conflict.isChecked(),
            "allow_rename_delete_mode_changes": self.chk_allow_meta.isChecked(),
            "partial_apply_per_file_override": self.chk_partial.isChecked(),
            "preserve_original_line_endings": self.chk_preserve_eol.isChecked(),
            "allow_writing_conflicted_output": self.chk_allow_conflicted_write.isChecked(),
            "skip_unsupported_binary_files": self.chk_skip_bin.isChecked(),
        }

    def _set_status(self, summary: str, state: str, warn: str) -> None:
        self.status_summary.setText(summary or "")
        self.status_state.setText((state or "Idle").upper())
        lowered = (state or "").lower()
        if any(token in lowered for token in ("fail", "block", "error")):
            status_kind = "err"
        elif any(token in lowered for token in ("warn", "preflight", "preview")):
            status_kind = "warn"
        else:
            status_kind = "ok"
        self.status_state.setProperty("state", status_kind)
        self.status_state.style().unpolish(self.status_state)
        self.status_state.style().polish(self.status_state)
        self.status_warn.setText(warn or "No active warnings")

    def _log(self, level: str, message: str, **fields: Any) -> None:
        entry = {"ts": time.time(), "level": level, "message": message}
        entry.update(fields)
        self.log_model.append(entry)
        if level in ("ERROR", "WARN"):
            self.log_dock.setVisible(True)

    def _log_info(self, message: str, **fields: Any) -> None:
        self._log("INFO", message, **fields)

    def _log_warn(self, message: str, **fields: Any) -> None:
        self._log("WARN", message, **fields)

    def _log_error(self, message: str, **fields: Any) -> None:
        self._log("ERROR", message, **fields)

    def _refresh_actions(self):
        has_patch = self.patchset is not None and self.patchset.total_files() > 0
        has_root = bool(self.root_folder)

        self.act_preflight.setEnabled(has_patch and has_root)
        self.act_preview.setEnabled(has_patch and has_root)
        self.act_apply.setEnabled(has_patch and has_root)
        self.act_generate.setEnabled(has_patch and bool(self.preview_result and self.preview_result.summary.get("outputs")))
        self.act_save_diff.setEnabled(bool(self.patch_text))

        for button, enabled in [
            (self.btn_load_diff, True),
            (self.btn_open_folder, True),
            (self.inline_open_btn, True),
            (self.btn_preflight, has_patch and has_root),
            (self.btn_preview, has_patch and has_root),
            (self.btn_apply, has_patch and has_root),
            (self.inline_generate_btn, self.act_generate.isEnabled()),
            (self.inline_save_btn, self.act_save_diff.isEnabled()),
        ]:
            button.setEnabled(enabled)

        self._update_session_cards()

    def _clear_session(self):
        self.patch_text = ""
        self.patchset = None
        self.preflight_report = []
        self.preview_result = None
        self.file_model.clear()
        self.diff_model.set_rows([])
        self._refresh_actions()

    #: Status tag -> ink. The tag is the icon; stock Qt pixmaps looked pasted-on.
    TAG_INK = {
        "READY": PALETTE["green"],
        "MISSING": PALETTE["amber"],
        "BLOCKED": PALETTE["red"],
        "BINARY": PALETTE["text_dim"],
        "PENDING": PALETTE["text_dim"],
    }

    def _rebuild_file_list(self):
        self.file_model.clear()
        if not self.patchset:
            return

        status_by_display = {r["file"]: r for r in (self.preflight_report or [])}

        for fp in self.patchset.files:
            display = fp.display_path
            tag = "BINARY" if fp.is_binary else "PENDING"

            if display in status_by_display:
                st = status_by_display[display]["status"]
                if st == "Missing":
                    tag = "MISSING"
                elif st in ("Outside root", "Blocked", "Invalid"):
                    tag = "BLOCKED"
                elif st.startswith("Unsupported"):
                    tag = "BINARY"
                elif st in ("Ready", "OK", "Exists"):
                    tag = "READY"

            it = QStandardItem(f"{tag:<8}{display}")
            it.setEditable(False)
            it.setForeground(QBrush(QColor(self.TAG_INK.get(tag, PALETTE["text"]))))
            it.setToolTip(f"{display}\n{fp.operation} · {tag.lower()}")
            it.setData(display, Qt.ItemDataRole.UserRole)
            it.setData(fp, Qt.ItemDataRole.UserRole + 1)
            self.file_model.appendRow(it)

        if self.file_model.rowCount() > 0:
            self.file_list.setCurrentIndex(self.file_model.index(0, 0))

    def _set_current_file_ext(self, path: str) -> None:
        ext = Path(path).suffix
        self.diff_table._current_file_ext = ext

    # ---------------- Actions ----------------

    def _open_file(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Open File", "", "All Files (*.*)")
        if not fn:
            return
        try:
            p = Path(fn).resolve()
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            QMessageBox.critical(self, "Open Failed", f"Could not open file:\n{e}")
            return

        self.loaded_file = str(p)
        self.root_folder = str(p.parent)
        self.baseline_texts = {p.name: text.replace("\r\n", "\n").replace("\r", "\n")}
        self._log_info("Loaded single file into session.", file=str(p), root=self.root_folder)
        self._set_status(f"Loaded file: {p.name}", state="Ready", warn="Workspace root updated from file selection.")

        self.file_model.clear()
        it = QStandardItem(p.name)
        it.setEditable(False)
        it.setData(p.name, Qt.ItemDataRole.UserRole)
        self.file_model.appendRow(it)
        self.file_list.setCurrentIndex(self.file_model.index(0, 0))

        self._refresh_actions()

    def _open_folder(self):
        fn = QFileDialog.getExistingDirectory(self, "Select Workspace Root Folder", "")
        if not fn:
            return
        self.root_folder = str(Path(fn).resolve())
        self.loaded_file = None
        self.baseline_texts = {}
        self._log_info("Selected workspace root folder.", root=self.root_folder)
        self._set_status(f"Root folder: {self.root_folder}", state="Ready", warn="Workspace root armed for preflight.")
        self._refresh_actions()

    def _load_diff(self):
        mb = QMessageBox(self)
        mb.setWindowTitle("Load Diff")
        mb.setText("Load patch/diff from a file, or paste text?")
        file_btn = mb.addButton("From File…", QMessageBox.ButtonRole.AcceptRole)
        paste_btn = mb.addButton("Paste…", QMessageBox.ButtonRole.ActionRole)
        cancel_btn = mb.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        mb.exec()

        if mb.clickedButton() == cancel_btn:
            return

        text = ""
        if mb.clickedButton() == file_btn:
            fn, _ = QFileDialog.getOpenFileName(self, "Load Diff File", "", "Diff/Patch (*.diff *.patch *.txt);;All Files (*.*)")
            if not fn:
                return
            try:
                text = Path(fn).read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                QMessageBox.critical(self, "Load Failed", f"Could not read diff:\n{e}")
                return
        else:
            text, ok = self._multiline_input("Paste Diff/Patch", "Paste unified diff text:")
            if not ok or not text.strip():
                return

        self.patch_text = text
        self.preview_result = None
        self.preflight_report = []

        _norm_text, dialect, blocks = self.normalizer.normalize(text)
        ps = self.parser.parse(dialect, blocks)
        self.patchset = ps

        self._log_info("Loaded patch.", dialect=dialect, file_blocks=len(blocks), files=ps.total_files(), hunks=ps.total_hunks())
        self._set_status(
            f"Loaded patch: {ps.total_files()} file(s), {ps.total_hunks()} hunk(s)",
            state="Patch loaded",
            warn=f"Dialect: {dialect}",
        )

        self._rebuild_file_list()
        self._refresh_actions()

    def _run_preflight(self):
        if not self.patchset:
            return
        if not self.root_folder:
            QMessageBox.warning(self, "Preflight", "Choose a root folder first (Open Folder…).")
            return
        report = self.applier.preflight(self.patchset, self.root_folder, self._options())
        self.preflight_report = report
        self._rebuild_file_list()

        bad = [
            r for r in report
            if r["status"] in ("Missing", "Invalid", "Outside root", "Blocked")
            or (r["status"].startswith("Unsupported") and not self.chk_skip_bin.isChecked())
        ]
        if bad:
            self._log_warn("Preflight found issues.", issues=len(bad))
            self._set_status("Preflight found issues.", state="Preflight warning", warn=f"Issues: {len(bad)}")
        else:
            self._log_info("Preflight passed.", files=len(report))
            self._set_status("Preflight passed.", state="Preflight clear", warn="No blocking issues.")

        self._refresh_actions()
        dlg = PreflightReportDialog(self, report)
        dlg.exec()

    def _run_preview(self):
        if not self.patchset:
            return
        if not self.root_folder:
            QMessageBox.warning(self, "Preview", "Choose a root folder first (Open Folder…).")
            return

        report = self.applier.preflight(self.patchset, self.root_folder, self._options())
        self.preflight_report = report
        self._rebuild_file_list()

        blocking = [
            r for r in report
            if r["status"] in ("Missing", "Invalid", "Outside root", "Blocked")
            or (r["status"].startswith("Unsupported") and not self.chk_skip_bin.isChecked())
        ]
        if blocking:
            msg = QMessageBox(self)
            msg.setWindowTitle("Preview Blocked")
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.setText("Patch references files not found under the selected root folder.")
            msg.setInformativeText("Choose a different root folder or review the preflight report to see what is missing or blocked.")
            choose_btn = msg.addButton("Choose Different Root Folder…", QMessageBox.ButtonRole.AcceptRole)
            report_btn = msg.addButton("Open Preflight Report", QMessageBox.ButtonRole.ActionRole)
            msg.addButton("Close", QMessageBox.ButtonRole.RejectRole)
            msg.exec()
            if msg.clickedButton() == choose_btn:
                self._open_folder()
            elif msg.clickedButton() == report_btn:
                PreflightReportDialog(self, report).exec()
            self._set_status("Preview blocked by preflight.", state="Preview blocked", warn=f"Issues: {len(blocking)}")
            self._refresh_actions()
            return

        prev = self.applier.preview_apply(self.patchset, self.root_folder, self._options())
        self.preview_result = prev
        for entry in prev.logs:
            self._log(entry.get("level", "INFO"), entry.get("message", ""), **{k: v for k, v in entry.items() if k not in ("ts", "level", "message")})

        if not prev.success:
            self._set_status("Preview failed.", state="Preview failed", warn="Open diagnostics for exact mismatch details.")
            self._show_preview_failure(prev)
        else:
            conf = prev.summary.get("conflicted_files", [])
            warn = f"Conflicts: {len(conf)}" if conf else "No conflicts detected."
            self._set_status(
                f"Preview succeeded. Hunks applied: {prev.summary.get('hunks_applied', 0)}",
                state="Preview ready",
                warn=warn,
            )
            self._log_info(
                "Preview succeeded.",
                hunks=prev.summary.get("hunks_applied", 0),
                added=prev.summary.get("lines_added", 0),
                removed=prev.summary.get("lines_removed", 0),
            )

        self._refresh_actions()

    def _run_apply(self):
        if not self.patchset or not self.root_folder:
            return

        opts = self._options()

        if not self.preview_result or not self.preview_result.success:
            QMessageBox.warning(self, "Apply Blocked", "Apply is only enabled after a successful Preview (dry-run).")
            return

        conflicted = self.preview_result.summary.get("conflicted_files", [])
        if conflicted and not opts.get("allow_writing_conflicted_output", False):
            QMessageBox.warning(
                self,
                "Apply Blocked",
                "Preview produced conflicted output. Writing conflicted output is blocked.\n\n"
                "To proceed, enable 'Allow writing conflicted output' in Advanced (not recommended).",
            )
            return

        summ = self.preview_result.summary
        files_total = summ.get("files_total", 0)
        hunks = summ.get("hunks_applied", 0)
        added = summ.get("lines_added", 0)
        removed = summ.get("lines_removed", 0)
        backup_strategy = f"Backup folder: {Path(self.root_folder) / '.patchstudio_backups' / 'YYYYMMDD_HHMMSS'}\nSibling .bak files: best-effort"

        ops = {"modify": 0, "create": 0, "delete": 0, "rename": 0}
        for fp in self.patchset.files:
            ops[fp.operation] = ops.get(fp.operation, 0) + 1

        confirm_text = (
            "You are about to apply the patch to disk.\n\n"
            f"Files: {files_total}\n"
            f"Operations: modify={ops.get('modify',0)}, create={ops.get('create',0)}, delete={ops.get('delete',0)}, rename={ops.get('rename',0)}\n"
            f"Hunks applied (preview): {hunks}\n"
            f"Lines added/removed (preview): +{added} / -{removed}\n\n"
            f"{backup_strategy}\n\n"
            "Proceed?"
        )
        if QMessageBox.question(self, "Confirm Apply", confirm_text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return

        applied = self.applier.apply_to_disk(self.patchset, self.root_folder, self.preview_result, opts)
        for entry in applied.logs:
            self._log(entry.get("level", "INFO"), entry.get("message", ""), **{k: v for k, v in entry.items() if k not in ("ts", "level", "message")})

        if applied.success:
            self._set_status("Apply completed.", state="Apply complete", warn=f"Backup: {applied.summary.get('backup_folder','')}")
            QMessageBox.information(self, "Apply Completed", f"Apply completed.\n\nBackup folder:\n{applied.summary.get('backup_folder','')}")
        else:
            self._set_status("Apply failed.", state="Apply failed", warn="See log and diagnostics for details.")
            QMessageBox.critical(self, "Apply Failed", applied.overall_message)
        self._refresh_actions()

    def _run_generate(self):
        if not self.patchset or not self.preview_result or not self.preview_result.summary.get("outputs"):
            QMessageBox.information(self, "Generate Diff", "Run Preview first to produce patched outputs.")
            return

        baseline: Dict[str, str] = {}
        if not self.root_folder:
            return
        root = Path(self.root_folder).resolve()

        for fp in self.patchset.files:
            if fp.is_binary:
                continue
            display = fp.display_path
            rel = fp.old_path if fp.old_path != "/dev/null" else fp.new_path
            if not rel or rel == "/dev/null":
                baseline[display] = ""
                continue
            abs_path = (root / rel).resolve()
            try:
                abs_path.relative_to(root)
            except Exception:
                baseline[display] = ""
                continue
            if abs_path.exists() and abs_path.is_file():
                try:
                    txt = abs_path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    txt = ""
                baseline[display] = txt
            else:
                baseline[display] = ""

        gen = self.generator.generate_unified_patchset(baseline, self.preview_result.summary["outputs"], self.patchset)
        self.patch_text = gen
        self._log_info("Generated unified diff from baseline vs patched outputs.", bytes=len(gen))
        self._set_status("Generated diff ready.", state="Generate ready", warn="Use Save Diff to write it to disk.")
        QMessageBox.information(self, "Generate Diff", "Generated unified diff is now loaded in session.\nUse Save Diff to write it to disk.")
        self._refresh_actions()

    def _save_diff(self):
        if not self.patch_text:
            return
        fn, _ = QFileDialog.getSaveFileName(self, "Save Diff", "patch.diff", "Diff (*.diff *.patch *.txt);;All Files (*.*)")
        if not fn:
            return
        try:
            Path(fn).write_text(self.patch_text, encoding="utf-8", newline="\n")
            self._log_info("Saved diff.", path=fn, bytes=len(self.patch_text))
            self._set_status(f"Saved diff: {fn}", state="Save complete", warn="Patch artifact written to disk.")
        except Exception as e:
            QMessageBox.critical(self, "Save Failed", f"Could not save diff:\n{e}")
        self._refresh_actions()

    def _toggle_advanced(self):
        self.adv_dock.setVisible(not self.adv_dock.isVisible())

    def _show_help(self):
        QMessageBox.information(
            self,
            "Patch Studio Help",
            "Workflow:\n"
            "1) Open Folder (workspace root)\n"
            "2) Load Diff (from file or paste)\n"
            "3) Preflight (validate file references under root)\n"
            "4) Preview (dry-run apply in memory)\n"
            "5) Apply (safe backup + atomic write)\n\n"
            "Advanced settings are hidden by default (use Advanced button).",
        )

    def _run_selftests_ui(self):
        ok, report = PatchStudioSelfTests.run()
        if ok:
            QMessageBox.information(self, "Self Tests", "All self tests passed.\n\n" + report)
        else:
            QMessageBox.critical(self, "Self Tests", "One or more self tests failed.\n\n" + report)

    # ---------------- Selection Handling ----------------

    def _on_file_selected(self, *args):
        idx = self.file_list.currentIndex()
        if not idx.isValid():
            return
        it = self.file_model.itemFromIndex(idx)
        if it is None:
            return

        display = it.data(Qt.ItemDataRole.UserRole)
        fp = it.data(Qt.ItemDataRole.UserRole + 1)

        if fp is None:
            self.diff_model.set_rows([])
            self._set_current_file_ext(display)
            return

        self._set_current_file_ext(display)
        self.diff_model.build_from_filepatch(fp)
        self._set_status(f"Viewing: {display} ({fp.operation})", state="View", warn="Diff surface synchronized to selected file.")

    # ---------------- Diagnostics ----------------

    def _show_preview_failure(self, prev: ApplyResult) -> None:
        preflight = prev.summary.get("preflight", [])
        blocking = [
            r for r in preflight
            if r["status"] in ("Missing", "Invalid", "Outside root", "Blocked")
            or (r["status"].startswith("Unsupported") and not self.chk_skip_bin.isChecked())
        ]
        if blocking:
            title = "Preflight failed"
            summary_lines = ["Patch references files not found or not allowed under the selected root folder."]
            causes = [
                "Selected root folder does not match the patch paths",
                "Patch paths refer to files outside the root (blocked)",
                "Patch contains unsupported binary file changes",
            ]
            fixes = [
                "Choose a different root folder that contains the referenced files",
                "Open Preflight Report and verify the resolved paths and statuses",
                "Enable 'Skip unsupported binary files' if you want to apply other files",
            ]
            eng = {"blocking_count": len(blocking), "blocking_samples": blocking[:5]}
            DiagnosticsDialog(self, title, summary_lines, causes, fixes, eng, jump_callback=None).exec()
            return

        failing = None
        for k, v in prev.per_file.items():
            if v.get("status") == "Failed":
                failing = (k, v)
                break
        if not failing:
            QMessageBox.critical(self, "Preview Failed", prev.overall_message)
            return

        fname, info = failing
        diag = info.get("diagnostics", {})
        details = diag.get("details", [])
        first = details[0] if details else {}

        attempted_line_1b = first.get("attempted_line_1b", None)
        excerpt = first.get("actual_excerpt", [])
        exp_excerpt = first.get("expected_excerpt", [])

        title = "Hunk application failed"
        summary_lines = [
            f"File: {fname}",
            f"Attempted at line {attempted_line_1b}" if attempted_line_1b else "Attempt location unavailable",
        ]
        causes = [
            "The file content has drifted from the patch’s expected context",
            "The patch was generated against a different version of the file",
            "Whitespace differences may be preventing a strict match",
        ]
        fixes = [
            "Verify you selected the correct root folder/version of the files",
            "Try enabling 'Ignore whitespace differences' (Advanced) if appropriate",
            "If safe, enable 'Best-effort fuzzy apply' (Advanced) and review logs",
        ]

        eng = {
            "file": fname,
            "hunk_index": first.get("hunk_index"),
            "hunk_header": first.get("hunk_header"),
            "attempted_line_1b": attempted_line_1b,
            "decision": first.get("decision"),
            "expected_excerpt": exp_excerpt,
            "actual_excerpt": excerpt,
            "mismatch": first.get("mismatch", {}),
        }

        def do_jump():
            target = None
            if attempted_line_1b:
                best_row = None
                best_dist = 10**9
                for r in range(self.diff_model.rowCount()):
                    row = self.diff_model.index(r, 1).data(Qt.ItemDataRole.UserRole)
                    if not isinstance(row, dict):
                        continue
                    hint = row.get("line_hint_old")
                    if isinstance(hint, int):
                        d = abs(hint - attempted_line_1b)
                        if d < best_dist:
                            best_dist = d
                            best_row = r
                if best_row is not None:
                    target = best_row
            if target is None:
                target = 0
            self.diff_table.scrollTo(self.diff_model.index(target, 0), QAbstractItemView.ScrollHint.PositionAtCenter)

        DiagnosticsDialog(self, title, summary_lines, causes, fixes, eng, jump_callback=do_jump).exec()

    # ---------------- Dialog Helpers ----------------

    def _multiline_input(self, title: str, label: str) -> Tuple[str, bool]:
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.resize(960, 560)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)
        card = QFrame()
        card.setObjectName("DialogCard")
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(14, 14, 14, 14)
        card_lay.setSpacing(10)
        lay.addWidget(card)

        title_lbl = QLabel(label)
        title_lbl.setObjectName("SectionTitle")
        card_lay.addWidget(title_lbl)

        from PyQt6.QtWidgets import QPlainTextEdit
        edit = QPlainTextEdit()
        edit.setFont(build_font())
        edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        card_lay.addWidget(edit, 1)

        btns = QHBoxLayout()
        btns.addStretch(1)
        ok_btn = self._make_action_button("OK", dlg.accept, role="primary")
        cancel_btn = self._make_action_button("Cancel", dlg.reject)
        btns.addWidget(ok_btn)
        btns.addWidget(cancel_btn)
        card_lay.addLayout(btns)

        rc = dlg.exec()
        return edit.toPlainText(), (rc == QDialog.DialogCode.Accepted)

    # ---------------- Close Event ----------------

    def closeEvent(self, event):
        event.accept()
