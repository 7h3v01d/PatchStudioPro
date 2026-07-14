import unittest

from patchstudio.core.normalizer import PatchInputNormalizer
from patchstudio.core.parser import UnifiedDiffParser

class TestNormalizerAndParser(unittest.TestCase):
    def setUp(self):
        self.norm = PatchInputNormalizer()
        self.parser = UnifiedDiffParser()

    def test_bom_and_crlf_normalization(self):
        raw = "\ufeff--- a.txt\r\n+++ a.txt\r\n@@ -1 +1 @@\r\n-a\r\n+b\r\n"
        norm_text, dialect, blocks = self.norm.normalize(raw)
        self.assertNotIn("\ufeff", norm_text)
        self.assertNotIn("\r", norm_text)
        ps = self.parser.parse(dialect, blocks)
        self.assertEqual(ps.total_files(), 1)
        self.assertEqual(ps.total_hunks(), 1)

    def test_git_create_delete_operations(self):
        patch_create = (
            "diff --git a/new.txt b/new.txt\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/new.txt\n"
            "@@ -0,0 +1,1 @@\n"
            "+hello\n"
        )
        _, d, b = self.norm.normalize(patch_create)
        ps = self.parser.parse(d, b)
        self.assertEqual(ps.files[0].operation, "create")

        patch_delete = (
            "diff --git a/old.txt b/old.txt\n"
            "deleted file mode 100644\n"
            "--- a/old.txt\n"
            "+++ /dev/null\n"
            "@@ -1,1 +0,0 @@\n"
            "-bye\n"
        )
        _, d2, b2 = self.norm.normalize(patch_delete)
        ps2 = self.parser.parse(d2, b2)
        self.assertEqual(ps2.files[0].operation, "delete")

    def test_binary_indicator_marks_file_binary(self):
        patch = (
            "diff --git a/bin.dat b/bin.dat\n"
            "GIT binary patch\n"
            "literal 0\n"
        )
        _, d, b = self.norm.normalize(patch)
        ps = self.parser.parse(d, b)
        self.assertEqual(ps.total_files(), 1)
        self.assertTrue(ps.files[0].is_binary)

if __name__ == "__main__":
    unittest.main()