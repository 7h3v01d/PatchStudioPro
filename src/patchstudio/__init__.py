"""Patch Studio (refactored).

SPDX-License-Identifier: Apache-2.0
Copyright (c) Leon Priest (7h3v01d)
"""

from __future__ import annotations


def main(argv=None):
    from .app import main as _main
    return _main(argv)


__all__ = ["main"]
