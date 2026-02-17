"""Patch Studio core: generate unified diffs from baseline vs outputs."""

from __future__ import annotations

import difflib
from typing import Dict

from .models import PatchSet

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


