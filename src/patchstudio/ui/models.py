"""Patch Studio UI: Qt models for tables and aligned diff rendering."""

from __future__ import annotations

import json
from typing import List, Dict, Any, Optional, Tuple

from PyQt6.QtCore import Qt, QAbstractTableModel, QModelIndex
from PyQt6.QtGui import QBrush, QColor

from ..core.models import FilePatch

class DiffAlignmentModel(QAbstractTableModel):
    """
    Aligned rows, 4 columns:
      0 old line number (or blank)
      1 old text (or blank)
      2 new line number (or blank)
      3 new text (or blank)
    """

    COL_OLD_NO = 0
    COL_OLD_TXT = 1
    COL_NEW_NO = 2
    COL_NEW_TXT = 3

    KIND_CONTEXT = "context"
    KIND_ADD = "add"
    KIND_DEL = "del"
    KIND_MOD = "mod"
    KIND_CONFLICT = "conflict"
    KIND_HUNK = "hunk"

    def __init__(self):
        super().__init__()
        self._rows: List[Dict[str, Any]] = []
        self._header = ["Old", "Old Text", "New", "New Text"]

        # Soft colors (explicit RGB)
        self._bg_context = QBrush(QColor(255, 255, 255))
        self._bg_line_no = QBrush(QColor(242, 242, 242))
        self._bg_add = QBrush(QColor(228, 246, 228))
        self._bg_del = QBrush(QColor(246, 228, 228))
        self._bg_mod = QBrush(QColor(252, 246, 220))
        self._bg_conflict = QBrush(QColor(238, 226, 246))
        self._bg_hunk = QBrush(QColor(248, 248, 248))

        self._fg_default = QBrush(QColor(20, 20, 20))

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else 4

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return self._header[section] if 0 <= section < len(self._header) else ""
        return str(section + 1)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        r = index.row()
        c = index.column()
        row = self._rows[r]

        if role == Qt.ItemDataRole.DisplayRole:
            if c == 0:
                return row.get("old_no", "")
            if c == 1:
                return row.get("old_text", "")
            if c == 2:
                return row.get("new_no", "")
            if c == 3:
                return row.get("new_text", "")
            return ""
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if c in (0, 2):
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        if role == Qt.ItemDataRole.BackgroundRole:
            kind = row.get("kind", self.KIND_CONTEXT)
            if c in (0, 2):
                return self._bg_line_no
            if kind == self.KIND_CONTEXT:
                return self._bg_context
            if kind == self.KIND_HUNK:
                return self._bg_hunk
            if kind == self.KIND_ADD:
                # only new columns green
                return self._bg_add if c in (2, 3) else self._bg_context
            if kind == self.KIND_DEL:
                # only old columns red
                return self._bg_del if c in (0, 1) else self._bg_context
            if kind == self.KIND_MOD:
                return self._bg_mod
            if kind == self.KIND_CONFLICT:
                return self._bg_conflict
            return self._bg_context

        if role == Qt.ItemDataRole.ForegroundRole:
            return self._fg_default

        if role == Qt.ItemDataRole.ToolTipRole:
            # Provide minimal tooltip; UI uses diagnostics panel for details
            if row.get("kind") == self.KIND_HUNK:
                return row.get("hunk_header", "")
            return None

        if role == Qt.ItemDataRole.UserRole:
            # expose metadata for jump
            return row

        return None

    def set_rows(self, rows: List[Dict[str, Any]]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def build_from_filepatch(self, fp: FilePatch) -> None:
        rows: List[Dict[str, Any]] = []
        for h_idx, h in enumerate(fp.hunks):
            # hunk header row
            rows.append({
                "old_no": "",
                "old_text": h.header,
                "new_no": "",
                "new_text": h.header,
                "kind": self.KIND_HUNK,
                "hunk_header": h.header,
                "hunk_index": h_idx,
                "line_hint_old": h.old_start,
                "line_hint_new": h.new_start,
            })
            old_ln = h.old_start
            new_ln = h.new_start

            # walk hunk lines, aligning change blocks
            del_buf: List[str] = []
            add_buf: List[str] = []

            def flush_change():
                nonlocal old_ln, new_ln
                if not del_buf and not add_buf:
                    return
                m = max(len(del_buf), len(add_buf))
                for i in range(m):
                    d = del_buf[i] if i < len(del_buf) else None
                    a = add_buf[i] if i < len(add_buf) else None
                    if d is not None and a is not None:
                        kind = self.KIND_MOD if d != a else self.KIND_CONTEXT
                        rows.append({
                            "old_no": str(old_ln),
                            "old_text": d,
                            "new_no": str(new_ln),
                            "new_text": a,
                            "kind": kind if kind != self.KIND_CONTEXT else self.KIND_MOD,
                            "hunk_index": h_idx,
                            "line_hint_old": old_ln,
                            "line_hint_new": new_ln,
                        })
                        old_ln += 1
                        new_ln += 1
                    elif d is not None:
                        rows.append({
                            "old_no": str(old_ln),
                            "old_text": d,
                            "new_no": "",
                            "new_text": "",
                            "kind": self.KIND_DEL,
                            "hunk_index": h_idx,
                            "line_hint_old": old_ln,
                            "line_hint_new": new_ln,
                        })
                        old_ln += 1
                    elif a is not None:
                        rows.append({
                            "old_no": "",
                            "old_text": "",
                            "new_no": str(new_ln),
                            "new_text": a,
                            "kind": self.KIND_ADD,
                            "hunk_index": h_idx,
                            "line_hint_old": old_ln,
                            "line_hint_new": new_ln,
                        })
                        new_ln += 1
                del_buf.clear()
                add_buf.clear()

            for tag, text in h.lines:
                if tag == " ":
                    flush_change()
                    rows.append({
                        "old_no": str(old_ln),
                        "old_text": text,
                        "new_no": str(new_ln),
                        "new_text": text,
                        "kind": self.KIND_CONTEXT,
                        "hunk_index": h_idx,
                        "line_hint_old": old_ln,
                        "line_hint_new": new_ln,
                    })
                    old_ln += 1
                    new_ln += 1
                elif tag == "-":
                    del_buf.append(text)
                elif tag == "+":
                    add_buf.append(text)
                else:
                    # ignore
                    pass

            flush_change()

        self.set_rows(rows)



class LogTableModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self._rows: List[Dict[str, Any]] = []
        self._header = ["Time", "Level", "Message"]

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else 3

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return self._header[section]
        return str(section + 1)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        c = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if c == 0:
                ts = row.get("ts", 0.0)
                return time.strftime("%H:%M:%S", time.localtime(ts))
            if c == 1:
                return row.get("level", "")
            if c == 2:
                return row.get("message", "")
        if role == Qt.ItemDataRole.ToolTipRole:
            # JSON details
            det = {k: v for k, v in row.items() if k not in ("ts", "level", "message")}
            if det:
                try:
                    return json.dumps(det, indent=2)
                except Exception:
                    return str(det)
        return None

    def append(self, entry: Dict[str, Any]) -> None:
        self.beginInsertRows(QModelIndex(), len(self._rows), len(self._rows))
        self._rows.append(entry)
        self.endInsertRows()

    def clear(self) -> None:
        self.beginResetModel()
        self._rows = []
        self.endResetModel()



class PreflightTableModel(QAbstractTableModel):
    def __init__(self, rows: List[Dict[str, Any]]):
        super().__init__()
        self._rows = rows
        self._header = ["File", "Operation", "Resolved Path", "Status", "Suggested Fix"]

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else 5

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return self._header[section]
        return str(section + 1)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        r = self._rows[index.row()]
        c = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if c == 0:
                return r.get("file", "")
            if c == 1:
                return r.get("operation", "")
            if c == 2:
                return r.get("resolved", "")
            if c == 3:
                return r.get("status", "")
            if c == 4:
                return r.get("suggested", "")
        return None



class KeyValueTableModel(QAbstractTableModel):
    def __init__(self, rows: List[Dict[str, str]]):
        super().__init__()
        self._rows = rows
        self._header = ["Field", "Value"]

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else 2

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return self._header[section]
        return str(section + 1)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return row["k"] if index.column() == 0 else row["v"]
        return None


