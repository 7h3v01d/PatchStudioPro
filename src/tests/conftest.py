"""Test bootstrap.

Puts the package root (src/) on sys.path so `patchstudio` imports no matter
which directory pytest is invoked from. pytest.ini already does this via its
`pythonpath` setting; this is the fallback for direct `unittest` runs and for
anyone invoking pytest without the ini in scope.

SPDX-License-Identifier: Apache-2.0
Copyright (c) Leon Priest (7h3v01d)
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
