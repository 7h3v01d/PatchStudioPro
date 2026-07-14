# tests/test_core_apply_to_disk.py
import tempfile
import unittest
from pathlib import Path

from patchstudio.core.normalizer import PatchInputNormalizer
from patchstudio.core.parser import UnifiedDiffParser
from patchstudio.core.applier import PatchApplier
from patchstudio.core.diffgen import DiffGenerator

OPTS = {
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


class TestApplyToDisk(unittest.TestCase):
    def setUp(self):
        self.norm = PatchInputNormalizer()
        self.parser = UnifiedDiffParser()
        self.applier = PatchApplier()

    def _parse(self, patch_text: str):
        _, dialect, blocks = self.norm.normalize(patch_text)
        return self.parser.parse(dialect, blocks)

    def test_apply_blocks_conflicted_write_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.txt").write_text("one\n", encoding="utf-8", newline="\n")

            patch = (
                "--- a.txt\n"
                "+++ a.txt\n"
                "@@ -1,1 +1,1 @@\n"
                "-ONE\n"
                "+uno\n"
            )
            ps = self._parse(patch)

            # preview with conflict marker mode => success but conflicted
            opts = dict(OPTS)
            opts["conflict_marker_mode"] = True
            prev = self.applier.preview_apply(ps, str(root), opts)
            self.assertTrue(prev.success)
            self.assertTrue(prev.summary["conflicted_files"])

            # apply must be blocked unless allow_writing_conflicted_output
            applied = self.applier.apply_to_disk(ps, str(root), prev, opts)
            self.assertFalse(applied.success)

            opts["allow_writing_conflicted_output"] = True
            applied2 = self.applier.apply_to_disk(ps, str(root), prev, opts)
            self.assertTrue(applied2.success)
            self.assertTrue((root / ".patchstudio_backups").exists())

    def test_preserve_eol_keeps_crlf(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            # force CRLF file on disk
            (root / "a.txt").write_bytes(b"one\r\ntwo\r\n")

            # Generate a correct patch (LF canonical strings; engine normalizes internally).
            gen = DiffGenerator()
            patch = gen.generate_unified_for_file(
                old_text="one\ntwo\n",
                new_text="ONE\ntwo\n",
                old_path="a.txt",
                new_path="a.txt",
            )
            ps = self._parse(patch)

            prev = self.applier.preview_apply(ps, str(root), dict(OPTS))
            self.assertTrue(prev.success)

            applied = self.applier.apply_to_disk(ps, str(root), prev, dict(OPTS))
            self.assertTrue(applied.success)

            data = (root / "a.txt").read_bytes()
            self.assertIn(b"\r\n", data)  # still CRLF somewhere
            self.assertNotIn(b"\n", data.replace(b"\r\n", b""))  # no bare LF mixed in


if __name__ == "__main__":
    unittest.main()