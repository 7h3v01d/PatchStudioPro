import sys
import os
import re
import io
import json
import time
import shutil
import difflib
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any


# =============================================================================
# Patch Engine (UI-independent; unit-testable)
# =============================================================================

class PatchInputNormalizer:
    """
    Responsibilities:
      - Normalize line endings to \n internally.
      - Strip UTF-8 BOM if present.
      - Detect dialect deterministically.
      - Output normalized "file patch blocks" for UnifiedDiffParser.
    """

    DIALECT_CLASSIC = "Classic Unified"
    DIALECT_GIT = "Git Unified"
    DIALECT_INDEX = "Index style"

    BIN_PATTERNS = (
        "GIT binary patch",
        "Binary files ",
    )

    def normalize(self, raw_text: str) -> Tuple[str, str, List[Dict[str, Any]]]:
        """
        Returns: (normalized_text, detected_dialect, file_blocks)
          file_blocks: list of dicts:
            {
              "header": {...},
              "text": "...\n",
              "dialect": "...",
              "index_path": Optional[str]
            }
        """
        if raw_text.startswith("\ufeff"):
            raw_text = raw_text.lstrip("\ufeff")

        # Normalize to \n
        raw_text = raw_text.replace("\r\n", "\n").replace("\r", "\n")

        lines = raw_text.split("\n")
        has_hunk = any(l.startswith("@@") for l in lines)
        has_git = any(l.startswith("diff --git ") for l in lines)
        has_index = any(l.startswith("Index: ") for l in lines)
        has_headers = any(l.startswith("--- ") for l in lines) and any(l.startswith("+++ ") for l in lines)

        if has_git:
            dialect = self.DIALECT_GIT
        elif has_index:
            dialect = self.DIALECT_INDEX
        elif has_headers and has_hunk:
            dialect = self.DIALECT_CLASSIC
        elif has_hunk:
            # Unified-family patch without clear headers; treat as classic best-effort
            dialect = self.DIALECT_CLASSIC
        else:
            # Not a unified-family patch; still pass through for parser to error cleanly
            dialect = self.DIALECT_CLASSIC

        file_blocks: List[Dict[str, Any]] = []
        if has_git:
            file_blocks = self._split_git_blocks(raw_text)
        elif has_index:
            file_blocks = self._split_index_blocks(raw_text)
        else:
            file_blocks = self._split_classic_blocks(raw_text)

        # Mark binary indicators at block level (parser will also detect)
        for b in file_blocks:
            b["has_binary_indicator"] = self._has_binary_indicator(b["text"])

        return raw_text, dialect, file_blocks

    def _has_binary_indicator(self, text: str) -> bool:
        for pat in self.BIN_PATTERNS:
            if pat in text:
                return True
        return False

    def _split_git_blocks(self, text: str) -> List[Dict[str, Any]]:
        lines = text.split("\n")
        blocks = []
        cur = []
        cur_header = {}
        for line in lines:
            if line.startswith("diff --git "):
                if cur:
                    blocks.append({"header": cur_header, "text": "\n".join(cur) + "\n", "dialect": self.DIALECT_GIT, "index_path": None})
                cur = [line]
                cur_header = {"diff_git": line}
            else:
                if cur:
                    cur.append(line)
                else:
                    # Preamble lines before first diff --git; ignore but preserve deterministically as separate block-less preamble
                    pass
        if cur:
            blocks.append({"header": cur_header, "text": "\n".join(cur) + "\n", "dialect": self.DIALECT_GIT, "index_path": None})
        return blocks

    def _split_index_blocks(self, text: str) -> List[Dict[str, Any]]:
        lines = text.split("\n")
        blocks = []
        cur = []
        cur_header = {}
        cur_index_path = None
        for line in lines:
            if line.startswith("Index: "):
                if cur:
                    blocks.append({"header": cur_header, "text": "\n".join(cur) + "\n", "dialect": self.DIALECT_INDEX, "index_path": cur_index_path})
                cur_index_path = line[len("Index: "):].strip()
                cur = [line]
                cur_header = {"index": cur_index_path}
            else:
                if cur:
                    cur.append(line)
                else:
                    # Ignore preamble
                    pass
        if cur:
            blocks.append({"header": cur_header, "text": "\n".join(cur) + "\n", "dialect": self.DIALECT_INDEX, "index_path": cur_index_path})
        return blocks

    def _split_classic_blocks(self, text: str) -> List[Dict[str, Any]]:
        """
        Classic unified diffs may omit diff --git and just have repeated ---/+++ headers.
        We split by occurrences of --- lines that are likely file headers.
        """
        lines = text.split("\n")
        blocks = []
        cur = []
        cur_header = {}
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.startswith("--- "):
                # Look ahead for +++ soon; if none, it's not a file header (best-effort).
                j = i + 1
                found_ppp = False
                while j < min(i + 60, len(lines)):
                    if lines[j].startswith("+++ "):
                        found_ppp = True
                        break
                    if lines[j].startswith("@@ "):
                        # hunks without +++ is invalid; still treat as a block
                        break
                    j += 1
                if found_ppp:
                    if cur:
                        blocks.append({"header": cur_header, "text": "\n".join(cur) + "\n", "dialect": self.DIALECT_CLASSIC, "index_path": None})
                    cur = [line]
                    cur_header = {"classic_header": True}
                else:
                    if cur:
                        cur.append(line)
                    else:
                        # ignore leading noise
                        pass
            else:
                if cur:
                    cur.append(line)
            i += 1
        if cur:
            blocks.append({"header": cur_header, "text": "\n".join(cur) + "\n", "dialect": self.DIALECT_CLASSIC, "index_path": None})
        return blocks


@dataclass
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str
    lines: List[Tuple[str, str]] = field(default_factory=list)  # (tag, text)


@dataclass
class FilePatch:
    old_path: str
    new_path: str
    display_path: str
    operation: str  # create/modify/delete/rename
    hunks: List[Hunk] = field(default_factory=list)
    is_binary: bool = False
    binary_reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PatchSet:
    dialect: str
    files: List[FilePatch] = field(default_factory=list)

    def total_hunks(self) -> int:
        return sum(len(fp.hunks) for fp in self.files)

    def total_files(self) -> int:
        return len(self.files)


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


@dataclass
class ApplyResult:
    success: bool
    overall_message: str
    per_file: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    summary: Dict[str, Any] = field(default_factory=dict)
    logs: List[Dict[str, Any]] = field(default_factory=list)

    def add_log(self, level: str, message: str, **fields: Any) -> None:
        entry = {"ts": time.time(), "level": level, "message": message}
        entry.update(fields)
        self.logs.append(entry)


class PatchApplier:
    """
    Implements:
      - Preflight validate file references against selected root
      - Preview apply (dry-run)
      - Safe apply to disk: confirm + backup + atomic write

    Patch logic only; UI handles dialogs/confirmations.
    """

    def preflight(self, patchset: PatchSet, root_folder: Optional[str], options: Dict[str, Any]) -> List[Dict[str, Any]]:
        strict_names = bool(options.get("strict_filename_match", False))
        allow_rename = bool(options.get("allow_rename_delete_mode_changes", False))

        report: List[Dict[str, Any]] = []

        root = Path(root_folder).resolve() if root_folder else None

        for fp in patchset.files:
            display = fp.display_path
            op = fp.operation
            is_bin = fp.is_binary
            status = "Found"
            suggested = ""
            resolved = ""

            if root is None:
                status = "Invalid"
                suggested = "Choose a root folder (Open Folder…) that contains the referenced files."
                resolved = ""
            else:
                candidate_rel = fp.new_path if fp.new_path != "/dev/null" else fp.old_path
                if candidate_rel in (None, "", "/dev/null"):
                    status = "Invalid"
                    suggested = "Patch file header paths are missing or invalid."
                    resolved = ""
                else:
                    rel = candidate_rel
                    # strict filename match option can be used to refuse weird paths; here it is used as a stricter normalization gate
                    if strict_names:
                        if rel.startswith(("/", "\\")) or re.search(r"[:*?\"<>|]", rel):
                            status = "Invalid"
                            suggested = "Disable Strict filename match or fix patch paths to be relative and valid."
                            resolved = ""
                        else:
                            resolved_path = (root / rel).resolve()
                            resolved = str(resolved_path)
                    else:
                        resolved_path = (root / rel).resolve()
                        resolved = str(resolved_path)

                    if resolved:
                        # outside root rejection
                        try:
                            resolved_path.relative_to(root)
                        except Exception:
                            status = "Outside root"
                            suggested = "Choose a different root folder or fix patch paths (path resolves outside root)."
                        else:
                            if is_bin:
                                status = "Unsupported (binary)"
                                suggested = "Enable 'Skip unsupported binary files' to apply other files; binary patch itself cannot be applied."
                            else:
                                if op == "modify":
                                    if not resolved_path.exists():
                                        status = "Missing"
                                        suggested = "Select a root folder that contains this file, or verify patch paths."
                                elif op == "create":
                                    # ok if parent exists
                                    if not resolved_path.parent.exists():
                                        status = "Missing"
                                        suggested = "Create the destination folders or choose a different root folder."
                                elif op == "delete":
                                    if not resolved_path.exists():
                                        status = "Missing"
                                        suggested = "Select a root folder that contains the file to delete."
                                elif op == "rename":
                                    if not allow_rename:
                                        status = "Blocked"
                                        suggested = "Enable 'Allow rename/delete/mode changes' in Advanced to proceed."
                                    else:
                                        # require old exists
                                        old_rel = fp.old_path
                                        if old_rel and old_rel != "/dev/null":
                                            old_abs = (root / old_rel).resolve()
                                            try:
                                                old_abs.relative_to(root)
                                            except Exception:
                                                status = "Outside root"
                                                suggested = "Rename source resolves outside root; choose a different root folder."
                                            else:
                                                if not old_abs.exists():
                                                    status = "Missing"
                                                    suggested = "Rename source file not found; choose a different root folder."

            report.append({
                "file": display,
                "operation": op,
                "resolved": resolved,
                "status": status,
                "suggested": suggested,
                "filepatch": fp,
            })

        return report

    def preview_apply(self, patchset: PatchSet, root_folder: str, options: Dict[str, Any]) -> ApplyResult:
        res = ApplyResult(success=False, overall_message="Preview failed.")
        report = self.preflight(patchset, root_folder, options)

        # Determine preflight gating
        skip_bin = bool(options.get("skip_unsupported_binary_files", False))
        allow_partial = bool(options.get("partial_apply_per_file_override", False))

        blocking = []
        for r in report:
            st = r["status"]
            if st in ("Missing", "Invalid", "Outside root", "Blocked"):
                blocking.append(r)
            if st.startswith("Unsupported") and not skip_bin:
                blocking.append(r)

        if blocking:
            res.overall_message = "Patch references files not found under the selected root folder."
            res.add_log("WARN", "Preflight failed; blocking preview.", blocking=len(blocking))
            # store report for UI use
            res.summary["preflight"] = report
            res.success = False
            return res

        res.summary["preflight"] = report
        res.add_log("INFO", "Preflight passed for preview.", files=len(report))

        total_hunks = 0
        total_added = 0
        total_removed = 0
        outputs: Dict[str, str] = {}
        conflicted_files: List[str] = []
        failed_files: List[str] = []

        for r in report:
            fp: FilePatch = r["filepatch"]
            st = r["status"]
            display = fp.display_path

            if st.startswith("Unsupported"):
                if skip_bin:
                    res.per_file[display] = {"status": "Skipped (binary unsupported)", "applied": False}
                    res.add_log("INFO", "Skipped unsupported binary file.", file=display)
                    continue
                else:
                    res.per_file[display] = {"status": "Blocked (binary unsupported)", "applied": False}
                    failed_files.append(display)
                    continue

            resolved = r["resolved"]
            op = fp.operation

            try:
                new_text, stats, diag = self._apply_filepatch_in_memory(fp, resolved, root_folder, options)
            except Exception as e:
                failed_files.append(display)
                res.per_file[display] = {"status": "Failed", "applied": False, "error": str(e)}
                res.add_log("ERROR", "Exception during preview apply.", file=display, error=str(e))
                if not allow_partial:
                    break
                continue

            if diag.get("failed") and not (options.get("conflict_marker_mode", False) and diag.get("conflicted", False)):
                failed_files.append(display)
                res.per_file[display] = {"status": "Failed", "applied": False, "diagnostics": diag}
                res.add_log("ERROR", "Hunk application failed.", file=display, diagnostics=diag)
                if not allow_partial:
                    break
                continue

            if diag.get("conflicted"):
                conflicted_files.append(display)

            # For delete, represent output as None marker; we still store for downstream diff generation
            if op == "delete":
                outputs[display] = ""  # indicates delete target
            else:
                outputs[display] = new_text

            total_hunks += stats["hunks_applied"]
            total_added += stats["lines_added"]
            total_removed += stats["lines_removed"]
            res.per_file[display] = {
                "status": "OK" if not diag.get("conflicted") else "Conflicted",
                "applied": True,
                "stats": stats,
                "diagnostics": diag if diag else {},
                "operation": op,
                "resolved": resolved,
            }

        # Determine success criteria
        if failed_files and not allow_partial:
            res.success = False
            res.overall_message = "Preview failed due to one or more files."
        else:
            # success even with conflicts (preview), but disk write is blocked unless allowed
            res.success = True

        res.summary.update({
            "files_total": len(report),
            "hunks_applied": total_hunks,
            "lines_added": total_added,
            "lines_removed": total_removed,
            "outputs": outputs,
            "conflicted_files": conflicted_files,
            "failed_files": failed_files,
        })

        if conflicted_files:
            res.add_log("WARN", "Preview produced conflicted output for some files.", conflicted=len(conflicted_files))

        if res.success:
            res.overall_message = "Preview succeeded."
        return res

    def apply_to_disk(self, patchset: PatchSet, root_folder: str, preview: ApplyResult, options: Dict[str, Any]) -> ApplyResult:
        """
        Performs safe apply: backup + atomic write.
        Requires UI to have confirmed and to have ensured preflight/preview gating.
        """
        res = ApplyResult(success=False, overall_message="Apply failed.")
        allow_write_conflicts = bool(options.get("allow_writing_conflicted_output", False))
        preserve_eol = bool(options.get("preserve_original_line_endings", True))
        allow_rename = bool(options.get("allow_rename_delete_mode_changes", False))
        allow_partial = bool(options.get("partial_apply_per_file_override", False))
        skip_bin = bool(options.get("skip_unsupported_binary_files", False))

        # Preflight again deterministically
        report = self.preflight(patchset, root_folder, options)
        res.summary["preflight"] = report

        blocking = []
        for r in report:
            st = r["status"]
            if st in ("Missing", "Invalid", "Outside root", "Blocked"):
                blocking.append(r)
            if st.startswith("Unsupported") and not skip_bin:
                blocking.append(r)

        if blocking:
            res.overall_message = "Patch references files not found under the selected root folder."
            res.add_log("ERROR", "Preflight failed; blocking apply.", blocking=len(blocking))
            res.success = False
            return res

        conflicted = preview.summary.get("conflicted_files", []) if preview else []
        if conflicted and not allow_write_conflicts:
            res.overall_message = "Conflicted output was produced; writing to disk is blocked."
            res.add_log("ERROR", "Conflicted output blocks disk write.", conflicted=len(conflicted))
            res.success = False
            return res

        # Create backup session folder
        root = Path(root_folder).resolve()
        backup_root = root / ".patchstudio_backups" / time.strftime("%Y%m%d_%H%M%S")
        backup_root.mkdir(parents=True, exist_ok=True)
        res.add_log("INFO", "Created backup folder.", backup=str(backup_root))

        outputs: Dict[str, str] = preview.summary.get("outputs", {}) if preview else {}

        files_applied = 0
        for r in report:
            fp: FilePatch = r["filepatch"]
            display = fp.display_path
            st = r["status"]
            if st.startswith("Unsupported"):
                if skip_bin:
                    res.per_file[display] = {"status": "Skipped (binary unsupported)"}
                    continue
                res.per_file[display] = {"status": "Blocked (binary unsupported)"}
                if not allow_partial:
                    res.overall_message = "Apply failed due to blocked binary patch."
                    return res
                continue

            resolved = r["resolved"]
            op = fp.operation

            # Determine absolute path(s)
            target_rel = fp.new_path if fp.new_path != "/dev/null" else fp.old_path
            if not target_rel:
                res.per_file[display] = {"status": "Failed", "error": "Invalid target path."}
                if not allow_partial:
                    return res
                continue

            target_abs = (root / target_rel).resolve()
            try:
                target_abs.relative_to(root)
            except Exception:
                res.per_file[display] = {"status": "Failed", "error": "Resolved path outside root."}
                if not allow_partial:
                    return res
                continue

            # Backups
            def backup_file(src: Path) -> None:
                if src.exists() and src.is_file():
                    rel = src.relative_to(root)
                    dest = backup_root / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(src), str(dest))

            try:
                if op == "delete":
                    backup_file(target_abs)
                    # also sibling .bak if feasible
                    self._try_make_sibling_bak(target_abs)
                    if target_abs.exists():
                        target_abs.unlink()
                    res.per_file[display] = {"status": "Deleted", "path": str(target_abs)}
                    files_applied += 1

                elif op == "create":
                    # backup not needed (file doesn't exist), but ensure parent exists
                    target_abs.parent.mkdir(parents=True, exist_ok=True)
                    content = outputs.get(display, "")
                    eol = "\n"
                    if preserve_eol and target_abs.exists():
                        eol = self._detect_eol(str(target_abs))
                    self._atomic_write_text(target_abs, content, eol=eol)
                    res.per_file[display] = {"status": "Created", "path": str(target_abs)}
                    files_applied += 1

                elif op == "rename":
                    if not allow_rename:
                        res.per_file[display] = {"status": "Blocked", "error": "Rename not allowed (Advanced)."}
                        if not allow_partial:
                            return res
                        continue
                    old_rel = fp.old_path
                    if not old_rel or old_rel == "/dev/null":
                        res.per_file[display] = {"status": "Failed", "error": "Invalid rename source."}
                        if not allow_partial:
                            return res
                        continue
                    old_abs = (root / old_rel).resolve()
                    try:
                        old_abs.relative_to(root)
                    except Exception:
                        res.per_file[display] = {"status": "Failed", "error": "Rename source outside root."}
                        if not allow_partial:
                            return res
                        continue
                    # backup both
                    backup_file(old_abs)
                    backup_file(target_abs)
                    self._try_make_sibling_bak(old_abs)
                    self._try_make_sibling_bak(target_abs)
                    target_abs.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(str(old_abs), str(target_abs))
                    res.per_file[display] = {"status": "Renamed", "from": str(old_abs), "to": str(target_abs)}
                    files_applied += 1

                else:  # modify
                    backup_file(target_abs)
                    self._try_make_sibling_bak(target_abs)
                    content = outputs.get(display)
                    if content is None:
                        # If preview didn't include, compute now
                        content, _, _ = self._apply_filepatch_in_memory(fp, str(target_abs), root_folder, options)
                    eol = "\n"
                    if preserve_eol and target_abs.exists():
                        eol = self._detect_eol(str(target_abs))
                    self._atomic_write_text(target_abs, content, eol=eol)
                    res.per_file[display] = {"status": "Modified", "path": str(target_abs)}
                    files_applied += 1

            except Exception as e:
                res.per_file[display] = {"status": "Failed", "error": str(e)}
                res.add_log("ERROR", "Disk apply failed for file.", file=display, error=str(e))
                if not allow_partial:
                    res.overall_message = "Apply failed due to one or more files."
                    res.success = False
                    return res

        res.success = True
        res.overall_message = "Apply completed."
        res.summary["files_applied"] = files_applied
        res.summary["backup_folder"] = str(backup_root)
        return res

    def _detect_eol(self, path: str) -> str:
        # Detect dominant EOL by reading bytes; default \n.
        try:
            data = Path(path).read_bytes()
        except Exception:
            return "\n"
        crlf = data.count(b"\r\n")
        lf = data.count(b"\n")
        if crlf > 0 and crlf >= (lf - crlf):
            return "\r\n"
        return "\n"

    def _try_make_sibling_bak(self, target: Path) -> None:
        try:
            if target.exists() and target.is_file():
                bak = target.with_suffix(target.suffix + ".bak")
                if bak.exists():
                    bak = target.with_suffix(target.suffix + f".{time.strftime('%Y%m%d_%H%M%S')}.bak")
                shutil.copy2(str(target), str(bak))
        except Exception:
            # best-effort; do not fail main operation
            pass

    def _atomic_write_text(self, path: Path, text: str, eol: str = "\n") -> None:
        # Write atomically: temp then replace. Ensure internal \n is converted to requested eol.
        data = text.replace("\n", eol)
        tmp = path.with_name(path.name + f".patchstudio_tmp_{os.getpid()}_{int(time.time()*1000)}")
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(data)
        os.replace(str(tmp), str(path))

    def _normalize_match_line(self, s: str, ignore_ws: bool) -> str:
        # Ignore whitespace differences precisely:
        # - always trim trailing whitespace
        # - if ignore_ws: collapse runs of whitespace to single space
        s = s.rstrip("\r\n")
        s = s.rstrip()
        if ignore_ws:
            s = re.sub(r"\s+", " ", s)
        return s

    def _apply_filepatch_in_memory(self, fp: FilePatch, resolved_path: str, root_folder: str, options: Dict[str, Any]) -> Tuple[str, Dict[str, int], Dict[str, Any]]:
        """
        Apply fp hunks to the file at resolved_path (or treat as empty for create).
        Returns: (new_text, stats, diagnostics)
        """
        ignore_ws = bool(options.get("ignore_whitespace_differences", False))
        fuzzy = bool(options.get("best_effort_fuzzy_apply", False))
        fuzzy_window = int(options.get("fuzzy_window_size", 200))
        conflict_mode = bool(options.get("conflict_marker_mode", False))

        op = fp.operation

        original_text = ""
        if op == "create":
            original_lines = []
        elif op == "delete":
            # Still validate hunks against file content (strict); but a delete patch typically includes deletions.
            original_text = Path(resolved_path).read_text(encoding="utf-8", errors="replace")
            original_text = original_text.replace("\r\n", "\n").replace("\r", "\n")
            original_lines = original_text.split("\n")
        else:
            original_text = Path(resolved_path).read_text(encoding="utf-8", errors="replace")
            original_text = original_text.replace("\r\n", "\n").replace("\r", "\n")
            original_lines = original_text.split("\n")

        # Preserve exact last empty line semantics by using splitlines? Here we keep a trailing "" if file ends with \n.
        # A deterministic approach:
        if original_text.endswith("\n"):
            if original_lines and original_lines[-1] != "":
                original_lines.append("")
        else:
            # split created last element already; ok.
            pass

        out_lines = list(original_lines)

        stats = {"hunks_applied": 0, "lines_added": 0, "lines_removed": 0}
        diagnostics: Dict[str, Any] = {"failed": False, "conflicted": False, "details": []}

        # Apply sequentially; maintain an offset relative to original.
        # Note: if fuzzy is enabled, we search within a window.
        line_offset = 0

        for h_idx, h in enumerate(fp.hunks):
            expected_pos = max(0, (h.old_start - 1) + line_offset)
            apply_pos, decision = self._locate_hunk_position(out_lines, h, expected_pos, ignore_ws, fuzzy, fuzzy_window)

            if apply_pos is None:
                # Hunk could not be located
                diag_entry = self._build_mismatch_diag(out_lines, h, expected_pos, decision, hunk_index=h_idx)
                diagnostics["details"].append(diag_entry)

                if conflict_mode:
                    # Insert conflict markers at attempted location using 2-way markers (acceptable)
                    self._insert_conflict_markers(out_lines, expected_pos, h, ignore_ws)
                    diagnostics["conflicted"] = True
                    stats["hunks_applied"] += 1
                    continue

                diagnostics["failed"] = True
                return "\n".join(out_lines), stats, diagnostics

            # Attempt strict application at apply_pos
            ok, new_out, delta, mismatch = self._apply_hunk_at(out_lines, h, apply_pos, ignore_ws)
            if not ok:
                diag_entry = self._build_mismatch_diag(out_lines, h, apply_pos, decision, hunk_index=h_idx, mismatch=mismatch)
                diagnostics["details"].append(diag_entry)

                if conflict_mode:
                    self._insert_conflict_markers(out_lines, apply_pos, h, ignore_ws)
                    diagnostics["conflicted"] = True
                    stats["hunks_applied"] += 1
                    continue

                diagnostics["failed"] = True
                return "\n".join(out_lines), stats, diagnostics

            out_lines = new_out
            line_offset += delta
            stats["hunks_applied"] += 1
            stats["lines_added"] += self._count_tag(h.lines, "+")
            stats["lines_removed"] += self._count_tag(h.lines, "-")

        # Operation-specific return
        if fp.operation == "delete":
            # A delete operation implies file removal; output text is not used for writing.
            return "\n".join(out_lines), stats, diagnostics

        return "\n".join(out_lines), stats, diagnostics

    def _count_tag(self, hunk_lines: List[Tuple[str, str]], tag: str) -> int:
        return sum(1 for t, _ in hunk_lines if t == tag)

    def _locate_hunk_position(
        self,
        lines: List[str],
        hunk: Hunk,
        expected_pos: int,
        ignore_ws: bool,
        fuzzy: bool,
        fuzzy_window: int
    ) -> Tuple[Optional[int], Dict[str, Any]]:
        """
        Returns (position or None, decision dict).
        Strict: return expected_pos (bounded) if anchors match.
        Fuzzy: search within ±N lines for matching context anchors.
        """
        decision: Dict[str, Any] = {"mode": "strict", "expected_pos": expected_pos}

        # Quick strict check (bounded)
        pos = min(max(expected_pos, 0), len(lines))
        if self._hunk_anchors_match(lines, hunk, pos, ignore_ws):
            decision["matched_at"] = pos
            return pos, decision

        if not fuzzy:
            decision["mode"] = "strict"
            decision["reason"] = "Anchors did not match at expected location."
            return None, decision

        decision["mode"] = "fuzzy"
        start = max(0, pos - fuzzy_window)
        end = min(len(lines), pos + fuzzy_window)

        candidates = []
        for p in range(start, end + 1):
            if self._hunk_anchors_match(lines, hunk, p, ignore_ws):
                candidates.append(p)

        if not candidates:
            decision["reason"] = "No anchor match found within fuzzy window."
            return None, decision

        # Deterministic: choose closest to expected; if tie, choose smallest
        candidates.sort(key=lambda x: (abs(x - pos), x))
        chosen = candidates[0]
        decision["matched_at"] = chosen
        decision["delta_from_expected"] = chosen - pos
        decision["candidates"] = len(candidates)
        if len(candidates) > 1 and abs(candidates[0] - pos) == abs(candidates[1] - pos):
            decision["ambiguity"] = "Multiple equally-close matches; deterministic tie-break by earliest."
        return chosen, decision

    def _hunk_anchors_match(self, lines: List[str], hunk: Hunk, pos: int, ignore_ws: bool) -> bool:
        """
        Match context/deletions sequence at a candidate position.
        We only require that the sequence of non-addition lines matches in order.
        """
        seq = [(t, s) for (t, s) in hunk.lines if t in (" ", "-")]
        if not seq:
            return True
        if pos < 0 or pos > len(lines):
            return False

        idx = pos
        for (t, s) in seq:
            if idx >= len(lines):
                return False
            want = self._normalize_match_line(s, ignore_ws)
            have = self._normalize_match_line(lines[idx], ignore_ws)
            if want != have:
                return False
            idx += 1
        return True

    def _apply_hunk_at(
        self,
        lines: List[str],
        hunk: Hunk,
        pos: int,
        ignore_ws: bool
    ) -> Tuple[bool, List[str], int, Dict[str, Any]]:
        """
        Apply hunk at pos. Returns (ok, new_lines, delta_offset, mismatch_info).
        delta_offset: net change in line count for downstream hunks.
        """
        i = pos
        out = []
        out.extend(lines[:pos])
        mismatch_info: Dict[str, Any] = {}

        for (t, s) in hunk.lines:
            if t == " ":
                if i >= len(lines):
                    mismatch_info = {"reason": "Context beyond EOF", "at": i}
                    return False, lines, 0, mismatch_info
                want = self._normalize_match_line(s, ignore_ws)
                have = self._normalize_match_line(lines[i], ignore_ws)
                if want != have:
                    mismatch_info = {"reason": "Context mismatch", "at": i, "expected": s, "actual": lines[i]}
                    return False, lines, 0, mismatch_info
                out.append(lines[i])
                i += 1
            elif t == "-":
                if i >= len(lines):
                    mismatch_info = {"reason": "Deletion beyond EOF", "at": i}
                    return False, lines, 0, mismatch_info
                want = self._normalize_match_line(s, ignore_ws)
                have = self._normalize_match_line(lines[i], ignore_ws)
                if want != have:
                    mismatch_info = {"reason": "Deletion mismatch", "at": i, "expected": s, "actual": lines[i]}
                    return False, lines, 0, mismatch_info
                # delete: skip original line
                i += 1
            elif t == "+":
                # insert line
                out.append(s)
            else:
                # ignore unknown
                pass

        out.extend(lines[i:])
        delta = self._count_tag(hunk.lines, "+") - self._count_tag(hunk.lines, "-")
        return True, out, delta, mismatch_info

    def _build_mismatch_diag(
        self,
        lines: List[str],
        hunk: Hunk,
        attempted_pos: int,
        decision: Dict[str, Any],
        hunk_index: int,
        mismatch: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        # Special requirement: show "Attempted at line X" (1-based) and excerpt around it.
        attempted_line_1b = attempted_pos + 1
        excerpt_start = max(0, attempted_pos - 2)
        excerpt_end = min(len(lines), attempted_pos + 3)
        excerpt = lines[excerpt_start:excerpt_end]

        expected_seq = [s for (t, s) in hunk.lines if t in (" ", "-")]
        expected_excerpt = expected_seq[:5]

        diag = {
            "file": None,
            "hunk_index": hunk_index,
            "hunk_header": hunk.header,
            "attempted_line_1b": attempted_line_1b,
            "attempted_pos_0b": attempted_pos,
            "decision": decision,
            "expected_excerpt": expected_excerpt,
            "actual_excerpt": excerpt,
            "mismatch": mismatch or {},
        }
        return diag

    def _insert_conflict_markers(self, lines: List[str], pos: int, hunk: Hunk, ignore_ws: bool) -> None:
        # Build ORIGINAL and PATCH content from hunk lines
        original_part = [s for (t, s) in hunk.lines if t in (" ", "-")]
        patch_part = [s for (t, s) in hunk.lines if t in (" ", "+")]

        markers = []
        markers.append("<<<<<<< ORIGINAL")
        markers.extend(original_part)
        markers.append("=======")
        markers.extend(patch_part)
        markers.append(">>>>>>> PATCH")
        insert_at = min(max(pos, 0), len(lines))
        lines[insert_at:insert_at] = markers


class DiffGenerator:
    """
    Generate unified diffs:
      - single file: Original vs Edited/Patched
      - multi-file: Workspace baseline vs Patched outputs
    """

    def generate_unified_for_file(
        self,
        old_text: str,
        new_text: str,
        old_path: str,
        new_path: str
    ) -> str:
        old_lines = old_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        new_lines = new_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

        # Ensure deterministic trailing newline handling
        if old_text.endswith("\n") and (not old_lines or old_lines[-1] != ""):
            old_lines.append("")
        if new_text.endswith("\n") and (not new_lines or new_lines[-1] != ""):
            new_lines.append("")

        diff = difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=old_path,
            tofile=new_path,
            lineterm="\n"
        )
        return "".join(diff)

    def generate_unified_patchset(self, baseline: Dict[str, str], outputs: Dict[str, str], patchset: PatchSet) -> str:
        # Generate classic unified diff blocks for each file in patchset order.
        buf = []
        for fp in patchset.files:
            display = fp.display_path
            op = fp.operation
            old_rel = fp.old_path
            new_rel = fp.new_path

            if fp.is_binary:
                # Skip generation for binary (unsupported)
                continue

            if op == "delete":
                old_text = baseline.get(display, "")
                new_text = ""
                buf.append(self.generate_unified_for_file(old_text, new_text, old_rel, "/dev/null"))
            elif op == "create":
                old_text = ""
                new_text = outputs.get(display, "")
                buf.append(self.generate_unified_for_file(old_text, new_text, "/dev/null", new_rel))
            else:
                old_text = baseline.get(display, "")
                new_text = outputs.get(display, old_text)
                buf.append(self.generate_unified_for_file(old_text, new_text, old_rel, new_rel))
            if buf and not buf[-1].endswith("\n"):
                buf[-1] += "\n"
        return "".join(buf)


class PatchStudioSelfTests:
    """
    In-process self tests using temporary directories and embedded patch strings.
    """

    @staticmethod
    def run() -> Tuple[bool, str]:
        normalizer = PatchInputNormalizer()
        parser = UnifiedDiffParser()
        applier = PatchApplier()
        generator = DiffGenerator()

        report_lines = []
        ok = True

        def fail(msg: str) -> None:
            nonlocal ok
            ok = False
            report_lines.append("FAIL: " + msg)

        def pass_(msg: str) -> None:
            report_lines.append("OK: " + msg)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            # Baseline files
            f1 = root / "hello.txt"
            f1.write_text("one\ntwo\nthree\n", encoding="utf-8", newline="\n")

            f2 = root / "data.json"
            f2.write_text('{"a": 1, "b": 2}\n', encoding="utf-8", newline="\n")

            # 1) Classic unified diff (only ---/+++ + hunks)
            patch1 = (
                "--- hello.txt\t2020-01-01\n"
                "+++ hello.txt\t2020-01-02\n"
                "@@ -1,3 +1,4 @@\n"
                " one\n"
                "+one-and-a-half\n"
                " two\n"
                " three\n"
            )
            _, dialect, blocks = normalizer.normalize(patch1)
            ps = parser.parse(dialect, blocks)
            if ps.total_files() != 1 or ps.total_hunks() != 1:
                fail("Classic unified parsing counts incorrect.")
            else:
                pass_("Classic unified parsing.")

            opts = {
                "strict_filename_match": False,
                "best_effort_fuzzy_apply": False,
                "fuzzy_window_size": 200,
                "ignore_whitespace_differences": False,
                "conflict_marker_mode": False,
                "allow_rename_delete_mode_changes": False,
                "partial_apply_per_file_override": False,
                "preserve_original_line_endings": True,
                "allow_writing_conflicted_output": False,
                "skip_unsupported_binary_files": True,
            }
            prev = applier.preview_apply(ps, str(root), opts)
            if not prev.success:
                fail("Classic unified preview apply failed.")
            else:
                out = prev.summary["outputs"].get("hello.txt", "")
                if "one-and-a-half" not in out:
                    fail("Classic unified output missing inserted line.")
                else:
                    pass_("Classic unified preview apply.")

            # 2) Git diff with diff --git + metadata + hunks
            patch2 = (
                "diff --git a/data.json b/data.json\n"
                "index 123..456 100644\n"
                "--- a/data.json\n"
                "+++ b/data.json\n"
                "@@ -1 +1 @@\n"
                "-{\"a\": 1, \"b\": 2}\n"
                "+{\"a\": 1, \"b\": 3}\n"
            )
            _, dialect2, blocks2 = normalizer.normalize(patch2)
            ps2 = parser.parse(dialect2, blocks2)
            if ps2.dialect != PatchInputNormalizer.DIALECT_GIT or ps2.total_files() != 1 or ps2.total_hunks() != 1:
                fail("Git unified parsing counts incorrect.")
            else:
                pass_("Git unified parsing.")
            prev2 = applier.preview_apply(ps2, str(root), opts)
            if not prev2.success:
                fail("Git unified preview apply failed.")
            else:
                out2 = prev2.summary["outputs"].get("data.json", "")
                if '"b": 3' not in out2:
                    fail("Git unified output incorrect.")
                else:
                    pass_("Git unified preview apply.")

            # 3) /dev/null create/delete
            patch3_create = (
                "diff --git a/new.txt b/new.txt\n"
                "new file mode 100644\n"
                "--- /dev/null\n"
                "+++ b/new.txt\n"
                "@@ -0,0 +1,2 @@\n"
                "+alpha\n"
                "+beta\n"
            )
            _, d3, b3 = normalizer.normalize(patch3_create)
            ps3 = parser.parse(d3, b3)
            prev3 = applier.preview_apply(ps3, str(root), opts)
            if not prev3.success:
                fail("/dev/null create preview failed.")
            else:
                if "alpha" not in prev3.summary["outputs"].get("new.txt", ""):
                    fail("/dev/null create output missing.")
                else:
                    pass_("/dev/null create preview.")

            # create a file then delete it
            to_del = root / "todelete.txt"
            to_del.write_text("x\ny\n", encoding="utf-8", newline="\n")
            patch3_delete = (
                "--- todelete.txt\n"
                "+++ /dev/null\n"
                "@@ -1,2 +0,0 @@\n"
                "-x\n"
                "-y\n"
            )
            _, d3d, b3d = normalizer.normalize(patch3_delete)
            ps3d = parser.parse(d3d, b3d)
            prev3d = applier.preview_apply(ps3d, str(root), opts)
            if not prev3d.success:
                fail("/dev/null delete preview failed.")
            else:
                pass_("/dev/null delete preview.")

            # 4) Index/SVN style boundary + hunks
            patch4 = (
                "Index: hello.txt\n"
                "===================================================================\n"
                "--- hello.txt\t(old)\n"
                "+++ hello.txt\t(new)\n"
                "@@ -2,1 +2,1 @@\n"
                "-two\n"
                "+TWO\n"
            )
            _, d4, b4 = normalizer.normalize(patch4)
            ps4 = parser.parse(d4, b4)
            if ps4.total_files() != 1 or ps4.total_hunks() != 1:
                fail("Index style parsing incorrect.")
            else:
                pass_("Index style parsing.")
            prev4 = applier.preview_apply(ps4, str(root), opts)
            if not prev4.success or "TWO" not in prev4.summary["outputs"].get("hello.txt", ""):
                fail("Index style preview apply failed.")
            else:
                pass_("Index style preview apply.")

            # 5) Binary patch indicator: file marked unsupported; parsing continues for others
            patch5 = (
                "diff --git a/bin.dat b/bin.dat\n"
                "GIT binary patch\n"
                "literal 0\n"
                "\n"
                "diff --git a/hello.txt b/hello.txt\n"
                "--- a/hello.txt\n"
                "+++ b/hello.txt\n"
                "@@ -1,1 +1,1 @@\n"
                "-one\n"
                "+ONE\n"
            )
            _, d5, b5 = normalizer.normalize(patch5)
            ps5 = parser.parse(d5, b5)
            if ps5.total_files() != 2:
                fail("Binary indicator did not preserve other file parsing.")
            else:
                if not ps5.files[0].is_binary:
                    fail("Binary file not marked as binary.")
                else:
                    pass_("Binary indicator handled; parsing continues.")
            prev5 = applier.preview_apply(ps5, str(root), opts)
            if not prev5.success:
                fail("Preview apply with binary skip failed.")
            else:
                pass_("Preview apply with binary skip.")

            # 6) Round-trip: generate diff then re-apply equals expected output
            # Use prev2 outputs for data.json change; generate diff and reapply
            baseline = {"data.json": f2.read_text(encoding="utf-8")}
            outputs = {"data.json": prev2.summary["outputs"]["data.json"]}
            # build a minimal patchset for generator
            fp_rt = FilePatch(old_path="data.json", new_path="data.json", display_path="data.json", operation="modify", hunks=[], metadata={})
            ps_rt = PatchSet(dialect=PatchInputNormalizer.DIALECT_CLASSIC, files=[fp_rt])
            gen_text = generator.generate_unified_patchset(baseline, outputs, ps_rt)
            _, d_rt, b_rt = normalizer.normalize(gen_text)
            ps_parsed = parser.parse(d_rt, b_rt)
            prev_rt = applier.preview_apply(ps_parsed, str(root), opts)
            if not prev_rt.success:
                fail("Round-trip preview apply failed.")
            else:
                out_rt = prev_rt.summary["outputs"].get("data.json", "")
                if out_rt != outputs["data.json"].replace("\r\n", "\n").replace("\r", "\n"):
                    fail("Round-trip output mismatch.")
                else:
                    pass_("Round-trip generate+parse+apply.")

        return ok, "\n".join(report_lines)


# =============================================================================
# UI (PyQt6)
# =============================================================================

from PyQt6.QtCore import (
    Qt, QAbstractTableModel, QModelIndex, QVariant, QSize
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QToolBar, QStatusBar, QSplitter,
    QListView, QTableView, QDockWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QFileDialog, QMessageBox, QDialog, QLabel, QFormLayout,
    QCheckBox, QSpinBox, QComboBox, QStyledItemDelegate, QStyle,
    QAbstractItemView, QHeaderView, QToolButton
)

from PyQt6.QtGui import (
    QAction, QFont, QColor, QBrush, QIcon, QPainter, QPen,
    QStandardItemModel, QStandardItem
)


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


def _run_selftests_cli() -> int:
    ok, report = PatchStudioSelfTests.run()
    print(report)
    return 0 if ok else 2


def main():
    if "--selftest" in sys.argv:
        sys.exit(_run_selftests_cli())

    app = QApplication(sys.argv)
    # Apply global app font
    app.setFont(QFont("Consolas", 10))

    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
