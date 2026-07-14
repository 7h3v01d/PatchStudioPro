"""Theme tests, plus a guard that keeps retired branding out of the tree."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from patchstudio.ui import theme

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = re.compile(r"keystone|keystoneai|\bkiv\b", re.IGNORECASE)


class TestBrandGuard(unittest.TestCase):
    def test_no_retired_branding_anywhere_in_source(self):
        offenders = []
        for path in PACKAGE_ROOT.rglob("*.py"):
            if path.name == Path(__file__).name:
                continue
            if FORBIDDEN.search(path.read_text(encoding="utf-8", errors="replace")):
                offenders.append(str(path.relative_to(PACKAGE_ROOT)))
        self.assertEqual(offenders, [], f"retired branding found in: {offenders}")

    def test_theme_exposes_unbranded_entrypoint(self):
        self.assertTrue(callable(theme.apply_theme))
        self.assertFalse(hasattr(theme, "apply_keystone_branding"))
        self.assertFalse(hasattr(theme, "KEYSTONE_STYLE"))


class TestThemeTokens(unittest.TestCase):
    def test_palette_values_are_hex(self):
        for name, value in theme.PALETTE.items():
            self.assertRegex(value, r"^#[0-9a-fA-F]{6}$", f"bad token: {name}")

    def test_qss_resolves_every_placeholder(self):
        qss = theme.build_qss()
        leftovers = re.findall(r"\{p?\[?['\"]?[a-z_]+['\"]?\]?\}", qss)
        self.assertEqual(leftovers, [], f"unresolved placeholders: {leftovers}")
        self.assertNotIn("{p[", qss)
        self.assertIn(theme.PALETTE["teal"], qss)
        self.assertIn(theme.PALETTE["obsidian"], qss)

    def test_controls_are_zero_radius(self):
        self.assertNotIn("border-radius", theme.build_qss())

    def test_diff_and_syntax_tables_cover_every_row_kind(self):
        for kind in ("gutter", "context", "add", "del", "mod", "conflict", "hunk"):
            self.assertIn(kind, theme.DIFF_BG)
            self.assertIn(kind, theme.DIFF_FG)
        for token in ("kw", "str", "com", "num", "key", "md", "code", "def"):
            self.assertIn(token, theme.SYNTAX)

    def test_syntax_inks_are_legible_on_the_dark_ground(self):
        """The old theme painted near-black tokens onto a dark panel."""
        from PyQt6.QtGui import QColor

        panel = QColor(theme.PALETTE["panel"]).lightnessF()
        for token, (hex_value, _bold) in theme.SYNTAX.items():
            lightness = QColor(hex_value).lightnessF()
            self.assertGreater(
                lightness, panel + 0.12,
                f"syntax ink '{token}' is too dark to read on the panel",
            )


if __name__ == "__main__":
    unittest.main()
