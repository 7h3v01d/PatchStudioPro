"""Patch Studio visual theme.

A single source of truth for colour, type, and geometry. Every widget, model,
and delegate in the UI pulls its colours from the tokens defined here — no
hard-coded RGB anywhere else in the package.

Design language: an obsidian instrument panel. Flat surfaces, zero-radius
controls, hairline rules, a monospace type stack, and accent rails that carry
state instead of decorating it.

SPDX-License-Identifier: Apache-2.0
Copyright (c) Leon Priest (7h3v01d)
"""

from __future__ import annotations

from typing import Dict

from PyQt6.QtGui import QColor, QFont, QPalette
from PyQt6.QtWidgets import QApplication

# --------------------------------------------------------------------------
# Tokens
# --------------------------------------------------------------------------

#: Core surface + accent palette. Accents are load-bearing: each one means
#: exactly one thing across the whole app.
PALETTE: Dict[str, str] = {
    # Surfaces (darkest to lightest)
    "void": "#080b0f",       # insets: tables, fields, gutters
    "obsidian": "#0b0f14",   # window background
    "panel": "#10161d",      # cards, docks, toolbar
    "panel_hi": "#151d26",   # hover / raised
    "row_alt": "#0d131a",    # alternating rows
    # Rules
    "rule": "#1b242d",       # hairlines
    "rule_hi": "#27343f",    # hovered / focused hairlines
    # Type
    "text": "#d7e2e6",       # primary
    "text_dim": "#7d8f99",   # secondary, labels
    "text_faint": "#54636c",  # disabled, comments
    # Accents (semantic)
    "teal": "#2fd6c3",       # focus, selection, active state, hunk headers
    "teal_deep": "#134842",  # selection fill
    "green": "#4be08a",      # success / added / armed
    "amber": "#ffb454",      # caution / modified / gated
    "red": "#ff5c66",        # failure / removed / blocked
    "ink": "#05080a",        # text on top of an accent fill
}

#: Monospace-first stack. JetBrains Mono if present, then the Windows built-ins.
FONT_STACK = '"JetBrains Mono", "Cascadia Mono", Consolas, "DejaVu Sans Mono", monospace'
FONT_FAMILIES = ["JetBrains Mono", "Cascadia Mono", "Consolas", "DejaVu Sans Mono"]
FONT_POINT_SIZE = 10

#: Row backgrounds for the aligned diff surface, keyed by row kind.
DIFF_BG: Dict[str, str] = {
    "gutter": "#080b0f",
    "context": "#0b1116",
    "add": "#0c2018",
    "del": "#20111a",
    "mod": "#1f1a0c",
    "conflict": "#2a1015",
    "hunk": "#0e1a20",
}

#: Row foregrounds for the aligned diff surface, keyed by row kind.
DIFF_FG: Dict[str, str] = {
    "gutter": PALETTE["text_faint"],
    "context": PALETTE["text"],
    "add": "#a8f0c8",
    "del": "#ffb0b6",
    "mod": "#ffd9a1",
    "conflict": PALETTE["red"],
    "hunk": PALETTE["teal"],
}

#: Syntax emphasis tokens (delegate). Tuned for a dark ground — the previous
#: theme used near-black inks here, which were invisible on the panel.
SYNTAX: Dict[str, tuple[str, bool]] = {
    "kw": (PALETTE["teal"], True),        # keywords
    "str": (PALETTE["green"], False),     # string literals
    "com": (PALETTE["text_faint"], False),  # comments
    "num": (PALETTE["amber"], False),     # numerics
    "key": ("#8fe3d7", False),            # mapping keys (json/yaml)
    "md": (PALETTE["amber"], True),       # markdown headings
    "code": (PALETTE["green"], True),     # inline code
    "def": (PALETTE["text"], False),      # fallback
}

#: Log level inks.
LEVEL_FG: Dict[str, str] = {
    "ERROR": PALETTE["red"],
    "WARN": PALETTE["amber"],
    "INFO": PALETTE["text"],
}


def color(token: str) -> QColor:
    """Resolve a palette token to a QColor."""
    return QColor(PALETTE[token])


# --------------------------------------------------------------------------
# Stylesheet
# --------------------------------------------------------------------------

def build_qss() -> str:
    """Compose the application stylesheet from the token table."""
    p = PALETTE
    return f"""
QWidget {{
    background: {p['obsidian']};
    color: {p['text']};
    font-family: {FONT_STACK};
    font-size: {FONT_POINT_SIZE}pt;
}}
QMainWindow, QDialog {{
    background: {p['obsidian']};
}}

/* ---- Menu ---- */
QMenuBar {{
    background: {p['panel']};
    color: {p['text_dim']};
    border-bottom: 1px solid {p['rule']};
}}
QMenuBar::item {{
    padding: 6px 12px;
    background: transparent;
}}
QMenuBar::item:selected {{
    background: {p['panel_hi']};
    color: {p['teal']};
}}
QMenu {{
    background: {p['panel']};
    color: {p['text']};
    border: 1px solid {p['rule_hi']};
    padding: 4px;
}}
QMenu::item {{
    padding: 7px 16px;
}}
QMenu::item:selected {{
    background: {p['teal_deep']};
    color: {p['text']};
}}

/* ---- Toolbar: flat command strip, teal underline on hover ---- */
QToolBar {{
    background: {p['panel']};
    border: 0;
    border-bottom: 1px solid {p['rule']};
    spacing: 2px;
    padding: 6px 8px;
}}
QToolBar QToolButton {{
    background: transparent;
    color: {p['text_dim']};
    border: 0;
    border-bottom: 2px solid transparent;
    padding: 7px 12px;
    font-weight: 600;
    letter-spacing: 0.6px;
}}
QToolBar QToolButton:hover {{
    color: {p['text']};
    background: {p['panel_hi']};
    border-bottom: 2px solid {p['teal']};
}}
QToolBar QToolButton:pressed {{
    background: {p['void']};
}}
QToolBar QToolButton:disabled {{
    color: {p['text_faint']};
}}

/* ---- Status bar ---- */
QStatusBar {{
    background: {p['panel']};
    border-top: 1px solid {p['rule']};
}}
QStatusBar::item {{
    border: 0;
}}
QStatusBar QLabel {{
    color: {p['text_dim']};
}}

/* ---- Docks ---- */
QDockWidget {{
    color: {p['text']};
    titlebar-close-icon: none;
    titlebar-normal-icon: none;
}}
QDockWidget::title {{
    text-align: left;
    background: {p['panel']};
    color: {p['text_dim']};
    padding: 9px 12px;
    border-top: 1px solid {p['rule']};
    border-bottom: 1px solid {p['rule']};
    font-weight: 700;
    letter-spacing: 1px;
}}

/* ---- Data surfaces ---- */
QListView, QTableView, QTreeView {{
    background: {p['void']};
    alternate-background-color: {p['row_alt']};
    color: {p['text']};
    border: 1px solid {p['rule']};
    gridline-color: {p['rule']};
    selection-background-color: {p['teal_deep']};
    selection-color: {p['text']};
    outline: 0;
}}
QListView::item {{
    padding: 6px 8px;
    border-left: 2px solid transparent;
}}
QListView::item:hover {{
    background: {p['panel']};
}}
QListView::item:selected {{
    background: {p['teal_deep']};
    border-left: 2px solid {p['teal']};
    color: {p['text']};
}}
QHeaderView::section {{
    background: {p['panel']};
    color: {p['text_dim']};
    border: 0;
    border-right: 1px solid {p['rule']};
    border-bottom: 1px solid {p['rule_hi']};
    padding: 7px 8px;
    font-weight: 700;
    letter-spacing: 1px;
}}
QTableView QTableCornerButton::section {{
    background: {p['panel']};
    border: 0;
}}

/* ---- Cards ---- */
QFrame#HeroCard {{
    background: {p['panel']};
    border: 1px solid {p['rule']};
    border-top: 2px solid {p['teal']};
}}
QFrame#PanelCard, QFrame#DialogCard {{
    background: {p['panel']};
    border: 1px solid {p['rule']};
}}
QFrame#StatCard {{
    background: {p['void']};
    border: 1px solid {p['rule']};
    border-left: 3px solid {p['rule_hi']};
    min-height: 66px;
}}
QFrame#StatCard[tone="live"]    {{ border-left: 3px solid {p['teal']}; }}
QFrame#StatCard[tone="armed"]   {{ border-left: 3px solid {p['green']}; }}
QFrame#StatCard[tone="gated"]   {{ border-left: 3px solid {p['amber']}; }}
QFrame#StatCard[tone="blocked"] {{ border-left: 3px solid {p['red']}; }}

/* ---- Type roles ---- */
QLabel#HeroTitle {{
    font-size: 19pt;
    font-weight: 700;
    color: {p['text']};
    letter-spacing: 4px;
}}
QLabel#HeroBadge {{
    background: {p['teal']};
    color: {p['ink']};
    font-size: 8pt;
    font-weight: 700;
    letter-spacing: 2px;
    padding: 3px 7px;
}}
QLabel#HeroSubtitle {{
    font-size: 9pt;
    color: {p['text_dim']};
}}
QLabel#DialogTitle {{
    font-size: 13pt;
    font-weight: 700;
    color: {p['text']};
    letter-spacing: 2px;
    padding-bottom: 2px;
    border-bottom: 1px solid {p['teal']};
}}
QLabel#SectionTitle {{
    font-size: 10pt;
    font-weight: 700;
    color: {p['text']};
    letter-spacing: 1.5px;
}}
QLabel#SectionSubtitle {{
    font-size: 9pt;
    color: {p['text_faint']};
}}
QLabel#StatLabel {{
    color: {p['text_faint']};
    font-size: 8pt;
    font-weight: 700;
    letter-spacing: 1.5px;
}}
QLabel#StatValue {{
    color: {p['text']};
    font-size: 10pt;
    font-weight: 700;
}}

/* ---- Buttons ---- */
QPushButton {{
    background: {p['panel_hi']};
    color: {p['text']};
    border: 1px solid {p['rule_hi']};
    padding: 8px 14px;
    font-weight: 700;
    letter-spacing: 0.8px;
}}
QPushButton:hover {{
    border: 1px solid {p['teal']};
    color: {p['teal']};
}}
QPushButton:pressed {{
    background: {p['void']};
}}
QPushButton:disabled {{
    background: {p['obsidian']};
    color: {p['text_faint']};
    border: 1px solid {p['rule']};
}}
QPushButton[role="primary"] {{
    background: {p['teal']};
    color: {p['ink']};
    border: 1px solid {p['teal']};
}}
QPushButton[role="primary"]:hover {{
    background: #4ee5d3;
    color: {p['ink']};
}}
QPushButton[role="primary"]:disabled {{
    background: {p['obsidian']};
    color: {p['text_faint']};
    border: 1px solid {p['rule']};
}}
QPushButton[role="danger"] {{
    background: transparent;
    color: {p['red']};
    border: 1px solid {p['red']};
}}
QPushButton[role="danger"]:hover {{
    background: {p['red']};
    color: {p['ink']};
}}
QPushButton[role="danger"]:disabled {{
    color: {p['text_faint']};
    border: 1px solid {p['rule']};
}}
QPushButton[role="ghost"] {{
    background: transparent;
    color: {p['text_dim']};
    border: 1px solid {p['rule']};
    font-weight: 600;
}}
QPushButton[role="ghost"]:hover {{
    color: {p['teal']};
    border: 1px solid {p['teal']};
}}

/* ---- Inputs ---- */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QComboBox {{
    background: {p['void']};
    color: {p['text']};
    border: 1px solid {p['rule_hi']};
    padding: 7px 9px;
    selection-background-color: {p['teal_deep']};
    selection-color: {p['text']};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
QSpinBox:focus, QComboBox:focus {{
    border: 1px solid {p['teal']};
}}
QComboBox::drop-down, QSpinBox::up-button, QSpinBox::down-button {{
    border: 0;
    background: {p['panel_hi']};
    width: 18px;
}}

/* ---- Grouping + toggles ---- */
QGroupBox {{
    border: 1px solid {p['rule']};
    margin-top: 14px;
    padding-top: 14px;
    font-weight: 700;
    letter-spacing: 1px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
    color: {p['text_dim']};
}}
QCheckBox, QRadioButton {{
    color: {p['text_dim']};
    spacing: 9px;
    padding: 2px 0;
}}
QCheckBox:hover {{
    color: {p['text']};
}}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {p['rule_hi']};
    background: {p['void']};
}}
QCheckBox::indicator:hover {{
    border: 1px solid {p['teal']};
}}
QCheckBox::indicator:checked {{
    background: {p['teal']};
    border: 1px solid {p['teal']};
}}
QToolButton {{
    color: {p['text_dim']};
    background: transparent;
    border: 1px solid transparent;
    padding: 5px 8px;
}}
QToolButton:hover {{
    color: {p['teal']};
    border: 1px solid {p['rule_hi']};
}}
QToolButton:checked {{
    color: {p['teal']};
    border: 1px solid {p['teal']};
}}

/* ---- Scrollbars: thin rails ---- */
QScrollBar:vertical {{
    background: {p['obsidian']};
    width: 12px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {p['rule_hi']};
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{
    background: {p['teal']};
}}
QScrollBar:horizontal {{
    background: {p['obsidian']};
    height: 12px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {p['rule_hi']};
    min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {p['teal']};
}}
QScrollBar::add-line, QScrollBar::sub-line,
QScrollBar::add-page, QScrollBar::sub-page {{
    background: none;
    border: none;
    height: 0;
    width: 0;
}}

QSplitter::handle {{
    background: {p['rule']};
}}
QSplitter::handle:hover {{
    background: {p['teal']};
}}

/* ---- Message boxes ---- */
QMessageBox {{
    background: {p['panel']};
}}
QMessageBox QLabel {{
    color: {p['text']};
}}

/* ---- Status pills ---- */
QLabel#StatusSummary {{
    color: {p['text']};
    font-weight: 600;
}}
QLabel#StatusState, QLabel#StatusWarn {{
    padding: 4px 10px;
    font-weight: 700;
    letter-spacing: 1px;
    border: 1px solid {p['rule_hi']};
}}
QLabel#StatusState[state="ok"] {{
    color: {p['green']};
    border: 1px solid {p['green']};
    background: {p['void']};
}}
QLabel#StatusState[state="warn"] {{
    color: {p['amber']};
    border: 1px solid {p['amber']};
    background: {p['void']};
}}
QLabel#StatusState[state="err"] {{
    color: {p['red']};
    border: 1px solid {p['red']};
    background: {p['void']};
}}
QLabel#StatusWarn {{
    color: {p['text_dim']};
    background: {p['void']};
    font-weight: 500;
    letter-spacing: 0;
}}
QToolTip {{
    background: {p['void']};
    color: {p['text']};
    border: 1px solid {p['teal']};
    padding: 6px;
}}
"""


# --------------------------------------------------------------------------
# Application wiring
# --------------------------------------------------------------------------

def build_font() -> QFont:
    """The application type face, with graceful fallback across platforms."""
    font = QFont()
    font.setFamilies(FONT_FAMILIES)
    font.setPointSize(FONT_POINT_SIZE)
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font


def build_palette() -> QPalette:
    """QPalette so native-drawn widgets match the stylesheet."""
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, color("obsidian"))
    pal.setColor(QPalette.ColorRole.WindowText, color("text"))
    pal.setColor(QPalette.ColorRole.Base, color("void"))
    pal.setColor(QPalette.ColorRole.AlternateBase, color("row_alt"))
    pal.setColor(QPalette.ColorRole.ToolTipBase, color("void"))
    pal.setColor(QPalette.ColorRole.ToolTipText, color("text"))
    pal.setColor(QPalette.ColorRole.Text, color("text"))
    pal.setColor(QPalette.ColorRole.Button, color("panel_hi"))
    pal.setColor(QPalette.ColorRole.ButtonText, color("text"))
    pal.setColor(QPalette.ColorRole.Highlight, color("teal_deep"))
    pal.setColor(QPalette.ColorRole.HighlightedText, color("text"))
    pal.setColor(QPalette.ColorRole.PlaceholderText, color("text_faint"))
    pal.setColor(QPalette.ColorRole.Link, color("teal"))
    return pal


def apply_theme(app: QApplication) -> None:
    """Apply font, palette, and stylesheet to the running application."""
    app.setFont(build_font())
    app.setPalette(build_palette())
    app.setStyleSheet(build_qss())
