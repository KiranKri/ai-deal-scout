"""Root conftest — make src/ importable for all test modules.

Adds the ``src/`` directory to ``sys.path`` so that both the legacy
``import filter as f`` pattern (used by existing tests) and the new
``import src.notifier`` pattern (used by V2 tests) work simultaneously.
"""

import os
import sys

# src/ needs to be in path so intra-package imports inside src/*.py
# (e.g. ``from config import ...``) resolve correctly when the module
# is imported as ``src.notifier`` from the project root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
