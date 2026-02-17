"""Patch Studio UI: main window."""

from __future__ import annotations

import time
import sys
import json
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QStandardItemModel, QStandardItem, QFont
from PyQt6.QtWidgets import (
    QMainWindow, QToolBar, QStatusBar, QSplitter,
    QListView, QTableView, QDockWidget,
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFileDialog, QMessageBox, QCheckBox, QSpinBox, QComboBox, QTextEdit,
    QGroupBox, QFormLayout, QLineEdit,
    QHeaderView, QAbstractItemView, QDialog, QStyle
)

from ..core.normalizer import PatchInputNormalizer
from ..core.parser import UnifiedDiffParser
from ..core.applier import PatchApplier
from ..core.diffgen import DiffGenerator
from ..core.models import PatchSet, FilePatch, ApplyResult
from ..core.selftests import PatchStudioSelfTests

from .models import DiffAlignmentModel, LogTableModel
from .delegates import SyntaxEmphasisDelegate
from .dialogs import PreflightReportDialog, DiagnosticsDialog

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Patch Studio v1.4")
        self.resize(1200, 720)

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
        self.baseline_texts: Dict[str, str] = {}  # display_path -> text

        # Global font
        app_font = QFont("Consolas", 10)
        self.setFont(app_font)

        self._build_toolbar()
        self._build_central()
        self._build_docks()
        self._build_status()

        self._refresh_actions()
        self._log_info("Ready.", component="ui")

    # ---------------- UI Construction ----------------

    def _build_toolbar(self):
        tb = QToolBar("Main")
        tb.setMovable(False)
        self.addToolBar(tb)

        self.act_open = QAction("Open", self)
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

        for a in [
            self.act_open, self.act_open_folder, self.act_load_diff,
            self.act_preflight, self.act_preview, self.act_apply,
            self.act_generate, self.act_save_diff, self.act_advanced, self.act_help
        ]:
            tb.addAction(a)

    def _build_central(self):
        splitter = QSplitter()
        splitter.setOrientation(Qt.Orientation.Horizontal)

        # File list
        self.file_list = QListView()
        self.file_list.setMinimumWidth(260)
        self.file_list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.file_model = QStandardItemModel()
        self.file_list.setModel(self.file_model)
        self.file_list.selectionModel().selectionChanged.connect(self._on_file_selected)

        # Diff table
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

        hdr = self.diff_table.horizontalHeader()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)

        # Column widths as specified
        self.diff_table.setColumnWidth(0, 60)
        self.diff_table.setColumnWidth(2, 60)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)

        # Syntax emphasis delegate for text columns 1 and 3
        self.syntax_delegate = SyntaxEmphasisDelegate(self.diff_table)
        self.diff_table.setItemDelegateForColumn(1, self.syntax_delegate)
        self.diff_table.setItemDelegateForColumn(3, self.syntax_delegate)

        splitter.addWidget(self.file_list)
        splitter.addWidget(self.diff_table)
        splitter.setSizes([260, 940])

        self.setCentralWidget(splitter)

    def _build_docks(self):
        # Bottom dock: Log / Diagnostics (hidden by default)
        self.log_dock = QDockWidget("Log / Diagnostics", self)
        self.log_dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea)
        self.log_dock.setVisible(False)

        log_widget = QWidget()
        log_layout = QVBoxLayout(log_widget)
        log_layout.setContentsMargins(4, 4, 4, 4)

        self.log_table = QTableView()
        self.log_model = LogTableModel()
        self.log_table.setModel(self.log_model)
        self.log_table.horizontalHeader().setStretchLastSection(True)
        self.log_table.setWordWrap(False)
        self.log_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.log_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

        log_layout.addWidget(self.log_table)
        self.log_dock.setWidget(log_widget)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.log_dock)

        # Right dock: Advanced panel (hidden by default)
        self.adv_dock = QDockWidget("Advanced", self)
        self.adv_dock.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea)
        self.adv_dock.setVisible(False)

        adv_widget = QWidget()
        form = QFormLayout(adv_widget)
        form.setContentsMargins(8, 8, 8, 8)

        self.chk_strict = QCheckBox("Strict filename match")
        self.chk_fuzzy = QCheckBox("Best-effort fuzzy apply")
        self.spn_fuzzy = QSpinBox()
        self.spn_fuzzy.setRange(1, 5000)
        self.spn_fuzzy.setValue(200)
        self.chk_ignore_ws = QCheckBox("Ignore whitespace differences")
        self.chk_conflict = QCheckBox("Conflict marker mode (3-way style markers)")
        self.chk_allow_meta = QCheckBox("Allow rename/delete/mode changes")
        self.chk_partial = QCheckBox("Partial apply per-file override")
        self.chk_preserve_eol = QCheckBox("Preserve original line endings")
        self.chk_preserve_eol.setChecked(True)
        self.chk_allow_conflicted_write = QCheckBox("Allow writing conflicted output")
        self.chk_skip_bin = QCheckBox("Skip unsupported binary files")
        self.chk_skip_bin.setChecked(True)

        form.addRow(self.chk_strict)
        form.addRow(self.chk_fuzzy)
        form.addRow("Fuzzy window size (lines)", self.spn_fuzzy)
        form.addRow(self.chk_ignore_ws)
        form.addRow(self.chk_conflict)
        form.addRow(self.chk_allow_meta)
        form.addRow(self.chk_partial)
        form.addRow(self.chk_preserve_eol)
        form.addRow(self.chk_allow_conflicted_write)
        form.addRow(self.chk_skip_bin)

        self.adv_dock.setWidget(adv_widget)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.adv_dock)

        # Help menu with self tests
        self.menu = self.menuBar().addMenu("Help")
        act_selftests = QAction("Run Self Tests", self)
        act_selftests.triggered.connect(self._run_selftests_ui)
        self.menu.addAction(act_selftests)

    def _build_status(self):
        sb = QStatusBar()
        self.setStatusBar(sb)
        self._set_status("No patch loaded.", state="Idle", warn="")

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
        self.statusBar().showMessage(f"{summary}    |    State: {state}    |    {warn}".strip())

    def _log(self, level: str, message: str, **fields: Any) -> None:
        entry = {"ts": time.time(), "level": level, "message": message}
        entry.update(fields)
        self.log_model.append(entry)
        # Keep log dock optional; if an error occurs, show it
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
        has_preview_ok = bool(self.preview_result and self.preview_result.success)

        self.act_preflight.setEnabled(has_patch and has_root)
        self.act_preview.setEnabled(has_patch and has_root)
        # Apply enabled after preview success unless partial override AND user chooses; we still gate in handler.
        self.act_apply.setEnabled(has_patch and has_root)
        self.act_generate.setEnabled(has_patch and bool(self.preview_result and self.preview_result.summary.get("outputs")))
        self.act_save_diff.setEnabled(bool(self.patch_text))

    def _clear_session(self):
        self.patch_text = ""
        self.patchset = None
        self.preflight_report = []
        self.preview_result = None
        self.file_model.clear()
        self.diff_model.set_rows([])
        self._refresh_actions()

    def _rebuild_file_list(self):
        self.file_model.clear()
        if not self.patchset:
            return
        icon_warn = self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning)
        icon_stop = self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxCritical)
        icon_info = self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation)

        # Map preflight status for display prefixes/icons
        status_by_display = {r["file"]: r for r in (self.preflight_report or [])}

        for fp in self.patchset.files:
            display = fp.display_path
            it = QStandardItem(display)
            it.setEditable(False)

            # annotate with preflight status if available
            if fp.is_binary:
                it.setText(f"(Binary) {display}")
                it.setIcon(icon_info)

            if display in status_by_display:
                st = status_by_display[display]["status"]
                if st == "Missing":
                    it.setText(f"(Missing) {display}")
                    it.setIcon(icon_warn)
                elif st in ("Outside root", "Blocked"):
                    it.setText(f"(Blocked) {display}")
                    it.setIcon(icon_stop)
                elif st.startswith("Unsupported"):
                    it.setText(f"(Binary) {display}")
                    it.setIcon(icon_info)

            # store display path + filepatch
            it.setData(display, Qt.ItemDataRole.UserRole)
            it.setData(fp, Qt.ItemDataRole.UserRole + 1)
            self.file_model.appendRow(it)

        # auto-select first
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
        self._set_status(f"Loaded file: {p.name}", state="Ready", warn="")

        # Build file list as a minimal session, even before patch is loaded
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
        self._set_status(f"Root folder: {self.root_folder}", state="Ready", warn="")
        self._refresh_actions()

    def _load_diff(self):
        # Offer file or paste, deterministic via a simple choice dialog
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
            # Paste dialog
            text, ok = self._multiline_input("Paste Diff/Patch", "Paste unified diff text:")
            if not ok or not text.strip():
                return

        self.patch_text = text
        self.preview_result = None
        self.preflight_report = []

        norm_text, dialect, blocks = self.normalizer.normalize(text)
        ps = self.parser.parse(dialect, blocks)
        self.patchset = ps

        self._log_info("Loaded patch.", dialect=dialect, file_blocks=len(blocks), files=ps.total_files(), hunks=ps.total_hunks())
        self._set_status(f"Loaded patch: {ps.total_files()} file(s), {ps.total_hunks()} hunk(s)", state="Patch loaded", warn=f"Dialect: {dialect}")

        # Preflight markers will be updated after running preflight, but list can be built now
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

        # Summarize
        bad = [r for r in report if r["status"] in ("Missing", "Invalid", "Outside root", "Blocked") or (r["status"].startswith("Unsupported") and not self.chk_skip_bin.isChecked())]
        if bad:
            self._log_warn("Preflight found issues.", issues=len(bad))
            self._set_status("Preflight found issues.", state="Preflight", warn=f"Issues: {len(bad)}")
        else:
            self._log_info("Preflight passed.", files=len(report))
            self._set_status("Preflight passed.", state="Preflight", warn="")

        dlg = PreflightReportDialog(self, report)
        dlg.exec()

    def _run_preview(self):
        if not self.patchset:
            return
        if not self.root_folder:
            QMessageBox.warning(self, "Preview", "Choose a root folder first (Open Folder…).")
            return

        # Always run preflight first as required
        report = self.applier.preflight(self.patchset, self.root_folder, self._options())
        self.preflight_report = report
        self._rebuild_file_list()

        blocking = [r for r in report if r["status"] in ("Missing", "Invalid", "Outside root", "Blocked") or (r["status"].startswith("Unsupported") and not self.chk_skip_bin.isChecked())]
        if blocking:
            # Friendly top-level message + actions
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
            self._set_status("Preview blocked by preflight.", state="Preview", warn=f"Issues: {len(blocking)}")
            return

        prev = self.applier.preview_apply(self.patchset, self.root_folder, self._options())
        self.preview_result = prev
        for entry in prev.logs:
            self._log(entry.get("level", "INFO"), entry.get("message", ""), **{k: v for k, v in entry.items() if k not in ("ts", "level", "message")})

        if not prev.success:
            self._set_status("Preview failed.", state="Preview", warn="See Diagnostics.")
            self._show_preview_failure(prev)
        else:
            conf = prev.summary.get("conflicted_files", [])
            warn = f"Conflicts: {len(conf)}" if conf else ""
            self._set_status(f"Preview succeeded. Hunks applied: {prev.summary.get('hunks_applied', 0)}", state="Preview", warn=warn)
            self._log_info("Preview succeeded.", hunks=prev.summary.get("hunks_applied", 0), added=prev.summary.get("lines_added", 0), removed=prev.summary.get("lines_removed", 0))

        self._refresh_actions()

    def _run_apply(self):
        if not self.patchset or not self.root_folder:
            return

        opts = self._options()

        # Enforce safety contract:
        # - no file modified unless preflight passes and preview succeeds unless overridden (partial override is not "skip preview")
        # Here: require preview_result.success unless user explicitly enables Partial apply per-file override AND confirms they accept risk.
        if not self.preview_result or not self.preview_result.success:
            # If they haven't previewed or preview failed, block unless user explicitly chooses to proceed (advanced override not specified as a toggle, so we block).
            QMessageBox.warning(self, "Apply Blocked", "Apply is only enabled after a successful Preview (dry-run).")
            return

        conflicted = self.preview_result.summary.get("conflicted_files", [])
        if conflicted and not opts.get("allow_writing_conflicted_output", False):
            QMessageBox.warning(
                self, "Apply Blocked",
                "Preview produced conflicted output. Writing conflicted output is blocked.\n\n"
                "To proceed, enable 'Allow writing conflicted output' in Advanced (not recommended)."
            )
            return

        # Confirmation dialog summary
        summ = self.preview_result.summary
        files_total = summ.get("files_total", 0)
        hunks = summ.get("hunks_applied", 0)
        added = summ.get("lines_added", 0)
        removed = summ.get("lines_removed", 0)
        backup_strategy = f"Backup folder: {Path(self.root_folder) / '.patchstudio_backups' / 'YYYYMMDD_HHMMSS'}\nSibling .bak files: best-effort"

        # Per-operation counts from patchset
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
            self._set_status("Apply completed.", state="Apply", warn=f"Backup: {applied.summary.get('backup_folder','')}")
            QMessageBox.information(self, "Apply Completed", f"Apply completed.\n\nBackup folder:\n{applied.summary.get('backup_folder','')}")
        else:
            self._set_status("Apply failed.", state="Apply", warn="See Log/Diagnostics.")
            QMessageBox.critical(self, "Apply Failed", applied.overall_message)

    def _run_generate(self):
        if not self.patchset or not self.preview_result or not self.preview_result.summary.get("outputs"):
            QMessageBox.information(self, "Generate Diff", "Run Preview first to produce patched outputs.")
            return

        # Build baseline mapping deterministically from disk for referenced files, using display_path keys
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
        self._set_status("Generated diff ready.", state="Generate", warn="")
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
            self._set_status(f"Saved diff: {fn}", state="Save Diff", warn="")
        except Exception as e:
            QMessageBox.critical(self, "Save Failed", f"Could not save diff:\n{e}")

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
            "Advanced settings are hidden by default (use Advanced button)."
        )

    def _run_selftests_ui(self):
        ok, report = PatchStudioSelfTests.run()
        if ok:
            QMessageBox.information(self, "Self Tests", "All self tests passed.\n\n" + report)
        else:
            QMessageBox.critical(self, "Self Tests", "One or more self tests failed.\n\n" + report)

    # ---------------- Selection Handling ----------------

    def _on_file_selected(self):
        idx = self.file_list.currentIndex()
        if not idx.isValid():
            return
        it = self.file_model.itemFromIndex(idx)
        if it is None:
            return

        display = it.data(Qt.ItemDataRole.UserRole)
        fp = it.data(Qt.ItemDataRole.UserRole + 1)

        if fp is None:
            # single-file session with no patch: clear diff view
            self.diff_model.set_rows([])
            self._set_current_file_ext(display)
            return

        # Set extension for syntax emphasis
        self._set_current_file_ext(display)

        # Build diff alignment from file patch
        self.diff_model.build_from_filepatch(fp)

        # Provide a concise status
        self._set_status(f"Viewing: {display} ({fp.operation})", state="View", warn="")

    # ---------------- Diagnostics ----------------

    def _show_preview_failure(self, prev: ApplyResult) -> None:
        # Determine likely root cause tiering:
        preflight = prev.summary.get("preflight", [])
        blocking = [r for r in preflight if r["status"] in ("Missing", "Invalid", "Outside root", "Blocked") or (r["status"].startswith("Unsupported") and not self.chk_skip_bin.isChecked())]
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

        # Otherwise: content mismatch / hunk apply failure
        # Find first failing file diagnostics
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
            f"Attempted at line {attempted_line_1b}" if attempted_line_1b else "Attempt location unavailable"
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
            # Jump to suspected location: approximate by scanning diff rows for nearest old/new line hint
            target = None
            if attempted_line_1b:
                # find a row with line_hint_old close
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
        dlg.resize(900, 520)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(label))

        # Use a QTableView-like control is overkill for paste; a simple input dialog is acceptable for a modal paste dialog.
        # Implement with QFileDialog-like minimal widget: use QTextEdit is not allowed as primary diff rendering; here it is a dialog helper.
        # To stay strict, we implement a plain QWidget with a QPlainTextEdit-equivalent is avoided. Use a QLineEdit multi-line substitute via QComboBox is poor.
        # Practical deterministic approach: use a QFileDialog open-from-clipboard is not feasible.
        # Use QMessageBox with details also is limited.
        # Therefore, use a QDialog with a QTableView-based single-cell editor is heavy.
        # Use a simple multi-line input using a minimal internal widget:
        from PyQt6.QtWidgets import QPlainTextEdit  # local import; not used for diff rendering
        edit = QPlainTextEdit()
        edit.setFont(QFont("Consolas", 10))
        edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        lay.addWidget(edit)

        btns = QHBoxLayout()
        btns.addStretch(1)
        ok_btn = QPushButton("OK")
        cancel_btn = QPushButton("Cancel")
        btns.addWidget(ok_btn)
        btns.addWidget(cancel_btn)
        lay.addLayout(btns)

        ok_btn.clicked.connect(dlg.accept)
        cancel_btn.clicked.connect(dlg.reject)

        rc = dlg.exec()
        return edit.toPlainText(), (rc == QDialog.DialogCode.Accepted)

    # ---------------- Close Event ----------------

    def closeEvent(self, event):
        event.accept()


