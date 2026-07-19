"""Hard stop before making the repository public.

Run this and get a clean pass BEFORE flipping the repo to public::

    python tools/preflight_public.py

Exits non-zero if anything would leak.  Checks both the working tree *and*
git history — a secret deleted in a later commit is still readable by anyone
who clones a public repo and runs ``git log -p``.
"""

import json
import os
import re
import subprocess
import sys

# Windows defaults stdout to cp1252, which cannot encode the accented and
# non-Latin characters that appear in real deal titles (e.g. "ń", "—", emoji).
# Redirecting to a file then raises UnicodeEncodeError and loses the run.
# Force UTF-8 so output is identical on every platform.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Files that must never be tracked by git.
FORBIDDEN_TRACKED = [".env", "data/subscribers.json"]

# Environment variable names whose *values* are secrets.
SECRET_VARS = [
    "TELEGRAM_BOT_TOKEN",
    "TAVILY_API_KEY",
    "GH_PAT",
    "WEBHOOK_SECRET",
    "ADMIN_CHAT_ID",
    "BRAVE_API_KEY",
]

# Shapes of real credentials, used to scan file contents.
SECRET_PATTERNS = [
    (r"\b\d{8,10}:AA[A-Za-z0-9_-]{30,}\b", "Telegram bot token"),
    (r"\btvly-[A-Za-z0-9_-]{20,}\b", "Tavily API key"),
    (r"\bgh[pousr]_[A-Za-z0-9]{30,}\b", "GitHub personal access token"),
    (r"\bBSA[A-Za-z0-9_-]{20,}\b", "Brave API key"),
    (r"\bsk-[A-Za-z0-9]{20,}\b", "OpenAI-style API key"),
]

# Known-dead credentials: revoked via the provider, so the copy in git history
# cannot be used.  Each entry is a short git SHA that introduced the leak.
# Adding a commit here does NOT hide live secrets — only those SHAs are
# exempted when reporting history hits for the matching pattern label.
#
# Telegram token in 51d2d10 / 1aa9ca9: revoked via BotFather (operator-confirmed).
# Revocation is sufficient for a hobby bot; history rewrite is optional cosmetics.
REVOKED_HISTORY_COMMITS: dict[str, frozenset[str]] = {
    "Telegram bot token": frozenset({"51d2d10", "1aa9ca9"}),
}

GREEN, RED, YELLOW, RESET = "\033[92m", "\033[91m", "\033[93m", "\033[0m"


def _run(args: list[str]) -> str:
    """Run a git command and return stdout, or '' on failure.

    Decoding is pinned to UTF-8 with ``errors="replace"``.  ``text=True``
    alone uses the platform default — cp1252 on Windows — which raises
    UnicodeDecodeError on any non-Latin-1 byte in the commit history (emoji
    in a deal title is enough).  The exception surfaces inside subprocess's
    reader thread, ``stdout`` comes back as ``None``, and every caller then
    fails on a None regex input.

    Never returns None, so callers can treat the result as a string
    unconditionally.
    """
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, ValueError):
        return ""
    return result.stdout or ""


def check_forbidden_tracked() -> list[str]:
    """Fail if a secret-bearing file is tracked in the working tree."""
    problems = []
    for path in FORBIDDEN_TRACKED:
        # Same UTF-8 pinning as _run: only returncode is used here, but
        # text=True would still raise on a cp1252-undecodable byte in stderr.
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", path],
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0:
            problems.append(f"{path} is TRACKED by git — it must be gitignored")
    return problems


def check_working_tree_contents() -> list[str]:
    """Scan every tracked file for credential-shaped strings."""
    problems = []
    files = [f for f in _run(["git", "ls-files"]).splitlines() if f]
    for path in files:
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                content = fh.read()
        except OSError:
            continue
        for pattern, label in SECRET_PATTERNS:
            if re.search(pattern, content):
                problems.append(f"{path} contains what looks like a {label}")
    return problems


def _sha_is_allowlisted(sha: str, allow: frozenset[str]) -> bool:
    """True when *sha* matches any prefix in the revoked-commit allowlist."""
    s = sha.strip().lower()
    return any(s.startswith(a.lower()) or a.lower().startswith(s[:7]) for a in allow)


def check_git_history() -> list[str]:
    """Scan the full commit history for secrets.

    This is the check people forget.  Deleting a file in a later commit does
    not remove it from history; anyone who clones a public repo can recover it.

    Known-revoked credentials (see ``REVOKED_HISTORY_COMMITS``) only block when
    they appear *outside* the allowlisted commits — a live leak still fails.
    Revocation via the provider is sufficient for an inert hobby-bot token;
    rewriting history is optional cosmetics, not required for safety.
    """
    problems = []
    all_revoked: set[str] = set()
    for shas in REVOKED_HISTORY_COMMITS.values():
        all_revoked |= set(shas)

    for path in FORBIDDEN_TRACKED:
        log = _run(["git", "log", "--oneline", "--all", "--", path])
        if not log.strip():
            continue
        commits = [line.split()[0] for line in log.splitlines() if line.strip()]
        if path == ".env" and commits and all(
            _sha_is_allowlisted(c, frozenset(all_revoked)) for c in commits
        ):
            # Acknowledged revoked .env history only — do not block.
            continue
        shown = ", ".join(c[:7] for c in commits[:5])
        problems.append(
            f"{path} EXISTS IN GIT HISTORY (commits: {shown}) — "
            f"a public clone can read it"
        )

    # Full patch history once; classify hits per pattern.
    full = _run(["git", "log", "-p", "--all", "--no-color"])
    for pattern, label in SECRET_PATTERNS:
        if not re.search(pattern, full):
            continue
        allow = REVOKED_HISTORY_COMMITS.get(label, frozenset())
        # Commits that touch a line matching the pattern (pickaxe).
        hit_shas = [
            s.strip()
            for s in _run(
                ["git", "log", "--all", "--pretty=format:%h", "-G", pattern]
            ).splitlines()
            if s.strip()
        ]
        if not hit_shas:
            # Pattern present in combined diff but -G found nothing (rename/
            # binary edge cases) — still block unless an allowlist exists and
            # we cannot prove a non-allowlisted hit.
            if not allow:
                problems.append(f"git history contains what looks like a {label}")
            continue
        offending = [s for s in hit_shas if not _sha_is_allowlisted(s, allow)]
        if offending:
            problems.append(
                f"git history contains what looks like a {label} "
                f"(commits: {', '.join(offending[:5])})"
            )
        # else: only allowlisted (revoked) commits — acknowledged, pass

    return problems


def check_committed_state_for_chat_ids() -> list[str]:
    """Fail if a committed state file contains raw Telegram chat IDs.

    Some state files must be tracked so they survive the ephemeral CI runner
    (the /run cooldown, the update cursor).  Those files are therefore public,
    and must never carry subscriber identities.  Telegram chat IDs are 6-12
    digit integers; keys in these files should be opaque hashes instead.
    """
    problems = []
    for path in ["data/run_quota.json", "data/telegram_offset.json"]:
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                blob = json.load(fh)
        except (OSError, ValueError):
            continue
        for key in (blob.get("users") or {}):
            if re.fullmatch(r"-?\d{6,12}", str(key)):
                problems.append(
                    f"{path} contains a raw Telegram chat ID as a key ({key!r}) "
                    f"— it must be hashed before this repo goes public"
                )
    return problems


def check_gitignore() -> list[str]:
    """Warn when the gitignore is missing entries that protect secrets."""
    warnings = []
    try:
        with open(".gitignore", encoding="utf-8") as fh:
            ignored = fh.read()
    except OSError:
        return [".gitignore is missing entirely"]

    for needed in [".env", "data/"]:
        if needed not in ignored:
            warnings.append(f".gitignore does not cover {needed!r}")
    return warnings


def main() -> None:
    print("\n  Pre-flight check: is this repo safe to make public?\n")

    blocking: list[str] = []
    warnings: list[str] = []

    for name, fn, is_blocking in [
        ("Secret files not tracked", check_forbidden_tracked, True),
        ("No secrets in tracked files", check_working_tree_contents, True),
        ("No secrets in git history", check_git_history, True),
        ("No chat IDs in committed state", check_committed_state_for_chat_ids, True),
        ("Gitignore covers secrets", check_gitignore, False),
    ]:
        found = fn()
        target = blocking if is_blocking else warnings
        if found:
            print(f"  {RED}FAIL{RESET}  {name}")
            for item in found:
                print(f"          - {item}")
            target.extend(found)
        else:
            print(f"  {GREEN}PASS{RESET}  {name}")

    print()
    if blocking:
        print(f"  {RED}DO NOT MAKE THIS REPO PUBLIC.{RESET}")
        print(f"  {len(blocking)} blocking problem(s) above.\n")
        print("  If a secret is in git history, the fastest safe fix is to")
        print("  REVOKE AND REGENERATE the credential — that makes the leaked")
        print("  copy worthless without rewriting history:")
        print("    - Telegram token : message @BotFather -> /revoke")
        print("    - Tavily key     : tavily.com dashboard -> regenerate")
        print("    - GitHub PAT     : github.com/settings/tokens -> revoke\n")
        sys.exit(1)

    if warnings:
        print(f"  {YELLOW}Passed with {len(warnings)} warning(s).{RESET}\n")
    else:
        print(f"  {GREEN}All checks passed. Safe to make public.{RESET}\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
