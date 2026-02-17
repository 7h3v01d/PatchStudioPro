"""Patch Studio UI: item delegates (syntax emphasis)."""

from __future__ import annotations

import re
from typing import Optional

from PyQt6.QtCore import Qt, QSize, QModelIndex
from PyQt6.QtGui import (
    QFont, QFontMetrics, QTextLayout, QTextOption,
    QBrush, QColor, QPainter, QPen
)
from PyQt6.QtWidgets import QStyledItemDelegate, QStyle

class SyntaxEmphasisDelegate(QStyledItemDelegate):
    """
    Lightweight syntax emphasis for columns 1 and 3.
    Adjusts foreground and font weight only; backgrounds remain from model.
    Performance:
      - cache tokenization per (ext, text)
      - do not re-tokenize on every paint
    """

    PY_KEYWORDS = {
        "False", "None", "True", "and", "as", "assert", "async", "await",
        "break", "class", "continue", "def", "del", "elif", "else", "except",
        "finally", "for", "from", "global", "if", "import", "in", "is",
        "lambda", "nonlocal", "not", "or", "pass", "raise", "return",
        "try", "while", "with", "yield", "match", "case"
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cache: Dict[Tuple[str, str], List[Tuple[int, int, str]]] = {}
        # token style key -> (QColor, bold)
        self._styles = {
            "kw": (QColor(30, 45, 120), True),
            "str": (QColor(0, 90, 0), False),
            "com": (QColor(90, 90, 90), False),
            "num": (QColor(100, 60, 0), False),
            "key": (QColor(60, 0, 100), True),
            "md": (QColor(30, 30, 30), True),
            "code": (QColor(0, 0, 0), True),
            "def": (QColor(0, 0, 0), False),
        }

    def _ext_for_row(self, index: QModelIndex) -> str:
        model = index.model()
        # The view will set a property containing current file extension.
        view = self.parent()
        ext = ""
        if view is not None:
            ext = getattr(view, "_current_file_ext", "") or ""
        return ext.lower()

    def _tokenize(self, ext: str, text: str) -> List[Tuple[int, int, str]]:
        key = (ext, text)
        if key in self._cache:
            return self._cache[key]

        spans: List[Tuple[int, int, str]] = []
        if not text:
            self._cache[key] = spans
            return spans

        # Default: comments (# or //), quoted strings
        def add_span(s: int, e: int, kind: str) -> None:
            if e > s:
                spans.append((s, e, kind))

        if ext in (".py", "py"):
            # Very lightweight: find comments, strings, keywords
            # Comments: from first unquoted # to end
            comment_pos = self._find_unquoted_hash(text)
            if comment_pos is not None:
                add_span(comment_pos, len(text), "com")
                prefix = text[:comment_pos]
            else:
                prefix = text

            # Strings: simple single/double quotes without escapes deep parsing (best-effort)
            spans.extend(self._find_string_spans(prefix, "str"))

            # Keywords: word tokens not inside string spans
            string_mask = [False] * len(prefix)
            for s, e, _k in spans:
                if _k == "str":
                    for i in range(s, min(e, len(prefix))):
                        string_mask[i] = True

            for m in re.finditer(r"\b[A-Za-z_][A-Za-z_0-9]*\b", prefix):
                w = m.group(0)
                if w in self.PY_KEYWORDS:
                    s, e = m.start(), m.end()
                    if not any(string_mask[s:e]):
                        add_span(s, e, "kw")

        elif ext in (".json", "json", ".yml", "yml", ".yaml", "yaml"):
            # JSON/YAML: keys (before :), strings, numbers
            # strings
            spans.extend(self._find_string_spans(text, "str"))
            # numbers
            for m in re.finditer(r"\b-?\d+(?:\.\d+)?\b", text):
                add_span(m.start(), m.end(), "num")
            # keys best-effort: "key": or key:
            for m in re.finditer(r'(?:"([^"]+)"\s*:)|(?:^|\s)([A-Za-z0-9_\-]+)\s*:', text):
                s = m.start()
                e = m.end()
                add_span(s, e, "key")

        elif ext in (".md", "md", ".markdown", "markdown"):
            # Markdown: headings, inline code markers
            if text.startswith("#"):
                add_span(0, len(text), "md")
            # inline code: `...`
            for m in re.finditer(r"`[^`]+`", text):
                add_span(m.start(), m.end(), "code")
        else:
            # default comments
            for m in re.finditer(r"(#|//).*$", text):
                add_span(m.start(), len(text), "com")
                break
            spans.extend(self._find_string_spans(text, "str"))

        # Merge / sort deterministically
        spans.sort(key=lambda x: (x[0], x[1], x[2]))
        self._cache[key] = spans
        return spans

    def _find_unquoted_hash(self, s: str) -> Optional[int]:
        in_single = False
        in_double = False
        esc = False
        for i, ch in enumerate(s):
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == "'" and not in_double:
                in_single = not in_single
            elif ch == '"' and not in_single:
                in_double = not in_double
            elif ch == "#" and not in_single and not in_double:
                return i
        return None

    def _find_string_spans(self, s: str, kind: str) -> List[Tuple[int, int, str]]:
        spans: List[Tuple[int, int, str]] = []
        i = 0
        while i < len(s):
            ch = s[i]
            if ch in ("'", '"'):
                q = ch
                j = i + 1
                esc = False
                while j < len(s):
                    cj = s[j]
                    if esc:
                        esc = False
                        j += 1
                        continue
                    if cj == "\\":
                        esc = True
                        j += 1
                        continue
                    if cj == q:
                        spans.append((i, j + 1, kind))
                        i = j + 1
                        break
                    j += 1
                else:
                    # unterminated; still mark to end
                    spans.append((i, len(s), kind))
                    i = len(s)
            else:
                i += 1
        return spans

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        # Base background and selection handling by default delegate, then draw text ourselves
        painter.save()

        # Draw background (respect selection)
        bg = index.data(Qt.ItemDataRole.BackgroundRole)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
        else:
            if isinstance(bg, QBrush):
                painter.fillRect(option.rect, bg)

        # Prepare text
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if text is None:
            text = ""
        text = str(text)

        # Determine ext
        ext = self._ext_for_row(index)

        # Foreground default / selection
        if option.state & QStyle.StateFlag.State_Selected:
            base_pen = QPen(option.palette.highlightedText().color())
        else:
            base_pen = QPen(option.palette.text().color())

        painter.setPen(base_pen)
        painter.setFont(option.font)

        # Clip region
        painter.setClipRect(option.rect)

        # Padding
        fm = painter.fontMetrics()
        x = option.rect.x() + 6
        y = option.rect.y()
        h = option.rect.height()
        baseline = y + (h + fm.ascent() - fm.descent()) // 2

        # If no spans or not a text column, draw plainly
        spans = self._tokenize(ext, text) if index.column() in (1, 3) else []

        if not spans:
            elided = fm.elidedText(text, Qt.TextElideMode.ElideRight, option.rect.width() - 10)
            painter.drawText(x, baseline, elided)
            painter.restore()
            return

        # Draw with spans; best-effort eliding: if text too wide, we still draw clipped; table provides horizontal scroll.
        # We draw sequentially; if selection, keep selection foreground (subtle emphasis suppressed).
        if option.state & QStyle.StateFlag.State_Selected:
            painter.setPen(base_pen)
            painter.drawText(x, baseline, text)
            painter.restore()
            return

        # Build a map of positions to style
        # We draw in segments between span boundaries.
        boundaries = {0, len(text)}
        for s, e, _k in spans:
            boundaries.add(max(0, min(len(text), s)))
            boundaries.add(max(0, min(len(text), e)))
        b = sorted(boundaries)

        # Determine style for each segment: choose highest-priority span that covers it (deterministic)
        def style_at(pos: int) -> Optional[str]:
            # last matching span in sorted order = deterministic but not necessarily "highest"; enforce priority
            # Priority order: com > str > kw > key > num > md > code
            pri = {"com": 60, "str": 50, "kw": 40, "key": 35, "num": 30, "md": 25, "code": 20}
            best = None
            bestp = -1
            for s, e, k in spans:
                if s <= pos < e:
                    p = pri.get(k, 0)
                    if p > bestp:
                        bestp = p
                        best = k
            return best

        cur_x = x
        for i in range(len(b) - 1):
            s = b[i]
            e = b[i + 1]
            seg = text[s:e]
            if not seg:
                continue
            k = style_at(s)
            if k and k in self._styles:
                col, bold = self._styles[k]
                painter.setPen(QPen(col))
                f = QFont(option.font)
                f.setBold(bold)
                painter.setFont(f)
            else:
                painter.setPen(base_pen)
                painter.setFont(option.font)

            painter.drawText(cur_x, baseline, seg)
            cur_x += fm.horizontalAdvance(seg)

        painter.restore()


