"""Patch Studio core: patch text normalization & dialect detection."""

from __future__ import annotations

import re
from typing import List, Tuple, Dict, Any, Optional

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


