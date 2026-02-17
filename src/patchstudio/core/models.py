"""Patch Studio core: shared data models."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Tuple, Dict, Any

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


