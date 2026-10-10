"""Make ``tests`` importable however pytest is invoked.

pyjab's own suite needs ``python -m pytest tests/`` because a test module imports
``tests.conftest``, which needs the repository root on ``sys.path``.  A ``conftest.py``
that puts the root there is one line of the same thing and does not depend on *how* pytest
was started -- bare ``pytest`` collects this file before any test module, so the imports in
``tests/_fakebackend.py`` resolve either way.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
