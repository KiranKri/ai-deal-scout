"""Root conftest — make src/ importable for all test modules.

Adds the ``src/`` directory to ``sys.path`` so that both the legacy
``import filter as f`` pattern (used by existing tests) and the new
``import src.notifier`` pattern (used by V2 tests) work simultaneously.
"""

import glob
import hashlib
import os
import sys

import pytest

# src/ needs to be in path so intra-package imports inside src/*.py
# (e.g. ``from config import ...``) resolve correctly when the module
# is imported as ``src.notifier`` from the project root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

_ROOT = os.path.dirname(os.path.abspath(__file__))


def _fingerprint() -> dict[str, str]:
    """MD5 every real state file under data/ and logs/."""
    prints: dict[str, str] = {}
    for path in sorted(
        glob.glob(os.path.join(_ROOT, "data", "*"))
        + glob.glob(os.path.join(_ROOT, "logs", "*"))
    ):
        if os.path.isfile(path):
            with open(path, "rb") as fh:
                prints[path] = hashlib.md5(fh.read()).hexdigest()
    return prints


@pytest.fixture(autouse=True, scope="session")
def real_files_untouched():
    """Tripwire: no test may write to the real data/ or logs/ files.

    A test that escaped its tmp_path once cost 89 live deals (stale chat IDs
    written to the real subscribers.json).  This converts the next isolation
    regression from a production incident into a test failure.
    """
    before = _fingerprint()
    yield
    after = _fingerprint()
    changed = sorted(
        set(before) ^ set(after)
        | {p for p in before.keys() & after.keys() if before[p] != after[p]}
    )
    assert not changed, (
        f"tests wrote to real state files: {changed} — every test must "
        f"redirect writes to tmp_path (see existing isolation fixtures)"
    )
