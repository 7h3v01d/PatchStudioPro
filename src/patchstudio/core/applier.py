"""Patch Studio core: preview/apply patchsets to memory and disk."""

from __future__ import annotations

import re
import os
import shutil
import time
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from .models import PatchSet, FilePatch, Hunk, ApplyResult

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


