# tests/test_core_preflight_and_preview.py
import tempfile
import unittest
from pathlib import Path

from patchstudio.core.normalizer import PatchInputNormalizer
from patchstudio.core.parser import UnifiedDiffParser
from patchstudio.core.applier import PatchApplier
from patchstudio.core.diffgen import DiffGenerator

DEFAULT_OPTS = {
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


class TestPreflightAndPreview(unittest.TestCase):
    def setUp(self):
        self.norm = PatchInputNormalizer()
        self.parser = UnifiedDiffParser()
        self.applier = PatchApplier()

    def _parse(self, patch_text: str):
        _, dialect, blocks = self.norm.normalize(patch_text)
        return self.parser.parse(dialect, blocks)

    def test_preflight_blocks_outside_root(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ps = self._parse(
                "--- ../evil.txt\n"
                "+++ ../evil.txt\n"
                "@@ -1,1 +1,1 @@\n"
                "-a\n"
                "+b\n"
            )
            report = self.applier.preflight(ps, str(root), DEFAULT_OPTS)
            self.assertEqual(report[0]["status"], "Outside root")

    def test_strict_filename_match_rejects_absolute(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            opts = dict(DEFAULT_OPTS)
            opts["strict_filename_match"] = True

            ps = self._parse(
                "--- /abs.txt\n"
                "+++ /abs.txt\n"
                "@@ -1,1 +1,1 @@\n"
                "-a\n"
                "+b\n"
            )
            report = self.applier.preflight(ps, str(root), opts)
            self.assertEqual(report[0]["status"], "Invalid")

    def test_preview_ignore_whitespace_allows_match(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            # Actual file on disk has multiple spaces
            (root / "a.txt").write_text("hello   world\n", encoding="utf-8", newline="\n")

            # Generate a syntactically correct patch (old/new canonical),
            # then apply it against a whitespace-variant file on disk.
            gen = DiffGenerator()
            patch = gen.generate_unified_for_file(
                old_text="hello world\n",
                new_text="hello world!\n",
                old_path="a.txt",
                new_path="a.txt",
            )
            ps = self._parse(patch)

            # strict match should fail
            prev1 = self.applier.preview_apply(ps, str(root), dict(DEFAULT_OPTS))
            self.assertFalse(prev1.success)

            # ignore whitespace should allow it
            opts = dict(DEFAULT_OPTS)
            opts["ignore_whitespace_differences"] = True
            prev2 = self.applier.preview_apply(ps, str(root), opts)
            self.assertTrue(prev2.success)
            self.assertIn("world!", prev2.summary["outputs"]["a.txt"])

    def test_preview_conflict_marker_mode_does_not_fail(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.txt").write_text("one\ntwo\nthree\n", encoding="utf-8", newline="\n")

            # deliberately mismatching context
            patch = (
                "--- a.txt\n"
                "+++ a.txt\n"
                "@@ -1,1 +1,1 @@\n"
                "-ONE\n"
                "+uno\n"
            )
            ps = self._parse(patch)

            opts = dict(DEFAULT_OPTS)
            opts["conflict_marker_mode"] = True
            prev = self.applier.preview_apply(ps, str(root), opts)
            self.assertTrue(prev.success)
            out = prev.summary["outputs"]["a.txt"]
            self.assertIn("<<<<<<< ORIGINAL", out)
            self.assertIn(">>>>>>> PATCH", out)


if __name__ == "__main__":
    unittest.main()