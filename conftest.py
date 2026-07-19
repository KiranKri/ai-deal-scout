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


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def no_production_state(monkeypatch):
    """Never let the test suite touch the real private data repo.

    .env carries a working GH_PAT/GH_REPO_DATA for local development, and
    anything that imports the pipeline loads it.  Without this fixture the
    suite reads and WRITES production subscriber and seen-deal state — tests
    would silently overwrite live data on a developer machine, and pass while
    doing it.  Tests that genuinely exercise the GitHub backend set these
    explicitly on the module under test.
    """
    monkeypatch.delenv("GH_PAT", raising=False)
    monkeypatch.delenv("GH_REPO_DATA", raising=False)


@pytest.fixture(autouse=True)
def reset_dedup_cache():
    """Clear dedup's in-memory store between tests.

    dedup caches the seen-deals store in a module global so a run makes one
    read instead of one per deal.  Without this fixture that cache leaks
    across tests: one test's hashes suppress another's deals, and a test that
    writes to a tmp_path store still sees the previous file's contents.
    """
    try:
        import dedup
        dedup.reset_cache()
        yield
        dedup.reset_cache()
    except ImportError:  # dedup not importable in some test contexts
        yield
