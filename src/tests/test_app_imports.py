import unittest

class TestAppImports(unittest.TestCase):
    def test_import_app_main(self):
        from patchstudio.app import main  # noqa: F401

    def test_import_core(self):
        from patchstudio.core import (
            PatchInputNormalizer, UnifiedDiffParser, PatchApplier, DiffGenerator
        )
        self.assertTrue(PatchInputNormalizer)
        self.assertTrue(UnifiedDiffParser)
        self.assertTrue(PatchApplier)
        self.assertTrue(DiffGenerator)

if __name__ == "__main__":
    unittest.main()