"""Patch Studio core: unified diff parsing (classic/git/index dialects)."""

from __future__ import annotations

import re
from typing import List, Optional, Dict, Any

from .normalizer import PatchInputNormalizer
from .models import Hunk, FilePatch, PatchSet

class UnifiedDiffParser:
    """
    Parses normalized "file patch blocks" into PatchSet/FilePatch/Hunk.
    """

    RE_DIFF_GIT = re.compile(r"^diff --git (.+?) (.+?)\s*$")
    RE_HUNK = re.compile(r"^@@\s+\-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@(.*)$")

    def parse(self, dialect: str, file_blocks: List[Dict[str, Any]]) -> PatchSet:
        patchset = PatchSet(dialect=dialect, files=[])

        for block in file_blocks:
            text = block["text"]
            lines = text.split("\n")
            if not lines:
                continue

            if dialect == PatchInputNormalizer.DIALECT_GIT:
                fp = self._parse_git_block(lines)
            elif dialect == PatchInputNormalizer.DIALECT_INDEX:
                fp = self._parse_index_block(lines, block.get("index_path"))
            else:
                fp = self._parse_classic_block(lines)

            if fp is not None:
                patchset.files.append(fp)

        return patchset

    def _strip_prefix_ab(self, p: str) -> str:
        p = p.strip()
        if p.startswith("a/") and len(p) > 2:
            return p[2:]
        if p.startswith("b/") and len(p) > 2:
            return p[2:]
        return p

    def _parse_path_from_header_line(self, line: str, prefix: str) -> str:
        # line starts with prefix ("--- " or "+++ ")
        rest = line[len(prefix):]
        # Path ends at first TAB if present; otherwise full remainder (trimmed)
        if "\t" in rest:
            path = rest.split("\t", 1)[0].strip()
        else:
            path = rest.strip()
        return path

    def _infer_operation(self, old_path: str, new_path: str, metadata: Dict[str, Any]) -> str:
        # If metadata implies rename/create/delete, reflect it; else infer from /dev/null and path inequality.
        if metadata.get("new_file_mode") or old_path == "/dev/null":
            return "create"
        if metadata.get("deleted_file_mode") or new_path == "/dev/null":
            return "delete"
        if metadata.get("rename_from") or metadata.get("rename_to"):
            return "rename"
        if old_path != new_path and old_path != "/dev/null" and new_path != "/dev/null":
            return "rename"
        return "modify"

    def _parse_git_block(self, lines: List[str]) -> Optional[FilePatch]:
        m = self.RE_DIFF_GIT.match(lines[0] if lines else "")
        if not m:
            return None

        a_path = m.group(1).strip()
        b_path = m.group(2).strip()
        metadata: Dict[str, Any] = {"diff_git": lines[0]}
        old_path = self._strip_prefix_ab(a_path)
        new_path = self._strip_prefix_ab(b_path)

        # Parse metadata until ---/+++ and hunks
        i = 1
        old_hdr = None
        new_hdr = None

        # binary indicators (graceful handling)
        for ln in lines:
            if ln.startswith("GIT binary patch"):
                display = self._strip_prefix_ab(new_path or old_path)
                fp = FilePatch(old_path=old_path, new_path=new_path, display_path=display, operation="modify", hunks=[],
                               is_binary=True, binary_reason="GIT binary patch unsupported", metadata=metadata)
                return fp
            if ln.startswith("Binary files "):
                display = self._strip_prefix_ab(new_path or old_path)
                fp = FilePatch(old_path=old_path, new_path=new_path, display_path=display, operation="modify", hunks=[],
                               is_binary=True, binary_reason="Binary files differ (unsupported)", metadata=metadata)
                return fp

        while i < len(lines):
            ln = lines[i]
            if ln.startswith("index "):
                metadata["index"] = ln.strip()
            elif ln.startswith("old mode "):
                metadata["old_mode"] = ln.strip()
            elif ln.startswith("new mode "):
                metadata["new_mode"] = ln.strip()
            elif ln.startswith("new file mode "):
                metadata["new_file_mode"] = ln.strip()
            elif ln.startswith("deleted file mode "):
                metadata["deleted_file_mode"] = ln.strip()
            elif ln.startswith("similarity index "):
                metadata["similarity_index"] = ln.strip()
            elif ln.startswith("rename from "):
                metadata["rename_from"] = ln[len("rename from "):].strip()
            elif ln.startswith("rename to "):
                metadata["rename_to"] = ln[len("rename to "):].strip()
            elif ln.startswith("--- "):
                old_hdr = self._parse_path_from_header_line(ln, "--- ")
                i += 1
                break
            i += 1

        if old_hdr is not None:
            # Expect +++ soon
            while i < len(lines):
                ln = lines[i]
                if ln.startswith("+++ "):
                    new_hdr = self._parse_path_from_header_line(ln, "+++ ")
                    i += 1
                    break
                i += 1

        if old_hdr is not None:
            old_path = self._strip_prefix_ab(old_hdr) if old_hdr != "/dev/null" else "/dev/null"
        if new_hdr is not None:
            new_path = self._strip_prefix_ab(new_hdr) if new_hdr != "/dev/null" else "/dev/null"

        op = self._infer_operation(old_path, new_path, metadata)
        display = self._strip_prefix_ab(new_path if new_path != "/dev/null" else old_path)
        fp = FilePatch(old_path=old_path, new_path=new_path, display_path=display, operation=op, hunks=[], metadata=metadata)

        # Parse hunks
        fp.hunks = self._parse_hunks_from(lines[i:])
        return fp

    def _parse_index_block(self, lines: List[str], index_path: Optional[str]) -> Optional[FilePatch]:
        # Index style may contain separators "====" and possibly "RCS file:" etc.
        metadata: Dict[str, Any] = {"index_path": index_path}
        # binary indicator
        for ln in lines:
            if ln.startswith("GIT binary patch"):
                display = index_path or "(unknown)"
                return FilePatch(old_path=display, new_path=display, display_path=display, operation="modify", hunks=[],
                                 is_binary=True, binary_reason="GIT binary patch unsupported", metadata=metadata)
            if ln.startswith("Binary files "):
                display = index_path or "(unknown)"
                return FilePatch(old_path=display, new_path=display, display_path=display, operation="modify", hunks=[],
                                 is_binary=True, binary_reason="Binary files differ (unsupported)", metadata=metadata)

        old_path = None
        new_path = None
        i = 0
        while i < len(lines):
            ln = lines[i]
            if ln.startswith("--- "):
                old_path = self._parse_path_from_header_line(ln, "--- ")
                i += 1
                break
            i += 1
        if old_path is None:
            return None
        while i < len(lines):
            ln = lines[i]
            if ln.startswith("+++ "):
                new_path = self._parse_path_from_header_line(ln, "+++ ")
                i += 1
                break
            i += 1
        if new_path is None:
            return None

        old_path = self._strip_prefix_ab(old_path) if old_path != "/dev/null" else "/dev/null"
        new_path = self._strip_prefix_ab(new_path) if new_path != "/dev/null" else "/dev/null"
        op = self._infer_operation(old_path, new_path, metadata)
        display = self._strip_prefix_ab(new_path if new_path != "/dev/null" else old_path)
        fp = FilePatch(old_path=old_path, new_path=new_path, display_path=display, operation=op, hunks=[], metadata=metadata)
        fp.hunks = self._parse_hunks_from(lines[i:])
        return fp

    def _parse_classic_block(self, lines: List[str]) -> Optional[FilePatch]:
        metadata: Dict[str, Any] = {}
        # binary indicator
        for ln in lines:
            if ln.startswith("GIT binary patch"):
                # Try to find paths anyway
                display = "(unknown)"
                return FilePatch(old_path=display, new_path=display, display_path=display, operation="modify", hunks=[],
                                 is_binary=True, binary_reason="GIT binary patch unsupported", metadata=metadata)
            if ln.startswith("Binary files "):
                display = "(unknown)"
                return FilePatch(old_path=display, new_path=display, display_path=display, operation="modify", hunks=[],
                                 is_binary=True, binary_reason="Binary files differ (unsupported)", metadata=metadata)

        if not lines or not lines[0].startswith("--- "):
            # best-effort: locate first --- / +++
            idx = 0
            while idx < len(lines) and not lines[idx].startswith("--- "):
                idx += 1
            if idx >= len(lines):
                return None
            lines = lines[idx:]

        old_hdr = self._parse_path_from_header_line(lines[0], "--- ")
        old_path = self._strip_prefix_ab(old_hdr) if old_hdr != "/dev/null" else "/dev/null"
        i = 1
        new_path = None
        while i < len(lines):
            if lines[i].startswith("+++ "):
                new_hdr = self._parse_path_from_header_line(lines[i], "+++ ")
                new_path = self._strip_prefix_ab(new_hdr) if new_hdr != "/dev/null" else "/dev/null"
                i += 1
                break
            i += 1
        if new_path is None:
            return None

        op = self._infer_operation(old_path, new_path, metadata)
        display = self._strip_prefix_ab(new_path if new_path != "/dev/null" else old_path)
        fp = FilePatch(old_path=old_path, new_path=new_path, display_path=display, operation=op, hunks=[], metadata=metadata)
        fp.hunks = self._parse_hunks_from(lines[i:])
        return fp

    def _parse_hunks_from(self, lines: List[str]) -> List[Hunk]:
        hunks: List[Hunk] = []
        i = 0
        current: Optional[Hunk] = None

        while i < len(lines):
            ln = lines[i]
            m = self.RE_HUNK.match(ln)
            if m:
                if current is not None:
                    hunks.append(current)
                old_start = int(m.group(1))
                old_count = int(m.group(2)) if m.group(2) is not None else 1
                new_start = int(m.group(3))
                new_count = int(m.group(4)) if m.group(4) is not None else 1
                header_tail = m.group(5) or ""
                header = ln.strip()
                current = Hunk(old_start=old_start, old_count=old_count, new_start=new_start, new_count=new_count, header=header, lines=[])
                i += 1
                continue

            if current is not None:
                if ln.startswith("\\ No newline at end of file"):
                    # ignore marker
                    i += 1
                    continue
                if ln == "":
                    # empty line in diff content is valid; tag still required in unified, but best-effort accept as context
                    current.lines.append((" ", ""))
                    i += 1
                    continue
                tag = ln[0]
                if tag in (" ", "+", "-"):
                    current.lines.append((tag, ln[1:]))
                else:
                    # best-effort: treat as context
                    current.lines.append((" ", ln))
            i += 1

        if current is not None:
            hunks.append(current)
        return hunks


