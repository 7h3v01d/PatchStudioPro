import unittest

from patchstudio.core.models import PatchSet, FilePatch
from patchstudio.core.normalizer import PatchInputNormalizer

class TestUIRegressions(unittest.TestCase):
    def test_rebuild_file_list_does_not_crash_on_selection_change(self):
        # Importing Qt inside the test keeps headless runs a bit safer.
        from PyQt6.QtWidgets import QApplication
        from patchstudio.ui.main_window import MainWindow

        app = QApplication.instance() or QApplication([])

        w = MainWindow()

        # Minimal patchset with one file so rebuild will auto-select row 0
        fp = FilePatch(
            old_path="a.txt",
            new_path="a.txt",
            display_path="a.txt",
            operation="modify",
            hunks=[],
            metadata={},
        )
        w.patchset = PatchSet(dialect=PatchInputNormalizer.DIALECT_CLASSIC, files=[fp])
        w.preflight_report = []

        # If _on_file_selected has the wrong signature, this line can raise TypeError
        w._rebuild_file_list()

        # Sanity: selection exists
        idx = w.file_list.currentIndex()
        self.assertTrue(idx.isValid())

if __name__ == "__main__":
    unittest.main()