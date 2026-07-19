"""Guard the drain job against dependencies it does not install.

``requirements-bot.txt`` is deliberately smaller than ``requirements.txt``:
the drain job runs ~96 times a day and pip install dominates its runtime.
That optimisation is only safe if nothing on the drain import path needs a
package the slim file omits.

It was not safe once: ``bot_server`` imported Flask at module scope, so the
drain job died with ModuleNotFoundError before reading a single update — and
because the failure was at import, none of the in-process tests saw it.  The
full test environment has Flask installed, which is exactly why the local
suite stayed green while CI broke.

These tests re-run the import with the missing packages blocked.
"""

import subprocess
import sys
import textwrap

# Everything in requirements.txt but NOT in requirements-bot.txt.  Importing
# any of these from the drain path is the bug this file exists to catch.
ABSENT_IN_SLIM_ENV = ["flask", "bs4", "feedparser", "gunicorn"]

_BLOCK_AND_IMPORT = textwrap.dedent(
    """
    import sys

    BLOCKED = {blocked!r}

    class _Blocker:
        \"\"\"Make the blocked packages look uninstalled, as they are in CI.\"\"\"
        def find_module(self, name, path=None):
            return self if name.split(".")[0] in BLOCKED else None

        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in BLOCKED:
                raise ImportError(f"blocked for test: {{name}}")
            return None

    sys.meta_path.insert(0, _Blocker())
    for mod in list(sys.modules):
        if mod.split(".")[0] in BLOCKED:
            del sys.modules[mod]

    import {module}
    print("IMPORT_OK")
    """
)


def _import_with_blocked(module: str) -> subprocess.CompletedProcess:
    """Import *module* in a subprocess where the slim-env gaps are missing."""
    return subprocess.run(
        [sys.executable, "-c",
         _BLOCK_AND_IMPORT.format(blocked=ABSENT_IN_SLIM_ENV, module=module)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60,
    )


def test_drain_imports_without_flask():
    """bot/drain.py must import using only requirements-bot.txt."""
    result = _import_with_blocked("bot.drain")
    assert "IMPORT_OK" in (result.stdout or ""), (
        "bot/drain.py cannot be imported in the slim CI environment.\n"
        f"stderr:\n{result.stderr}"
    )


def test_bot_server_handlers_import_without_flask():
    """The command handlers must not depend on the web framework."""
    result = _import_with_blocked("bot.bot_server")
    assert "IMPORT_OK" in (result.stdout or ""), (
        "bot/bot_server.py needs Flask at import time; the drain job does not "
        f"install it.\nstderr:\n{result.stderr}"
    )


def test_poll_imports_without_flask():
    """poll.py shares handlers with drain.py and must stay importable too."""
    result = _import_with_blocked("bot.poll")
    assert "IMPORT_OK" in (result.stdout or ""), (
        f"bot/poll.py cannot be imported without Flask.\nstderr:\n{result.stderr}"
    )
