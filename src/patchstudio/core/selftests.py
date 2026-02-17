"""Patch Studio core: in-process self tests."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Tuple

from .normalizer import PatchInputNormalizer
from .parser import UnifiedDiffParser
from .applier import PatchApplier
from .diffgen import DiffGenerator

from .models import FilePatch, PatchSet

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
