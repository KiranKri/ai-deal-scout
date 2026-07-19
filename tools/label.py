"""Label deals with help from an AI, and fold the answers into the eval set.

Two steps, both copy-paste.

    python tools/label.py export > label_me.txt      # paste into Grok/Claude
    python tools/label.py import labels.txt          # paste its answer back

Why bother: `tools/eval_filter.py` can only measure the filter against
labelled examples.  Every keyword change so far has been justified by eyeball
rather than measurement, which is how the filter drifted into passing "tips on
using Claude" as a deal.  More labels means changes can be proven instead of
argued.

The export deliberately includes the *current* verdict so the reviewer is
grading the filter, not starting from scratch — and disagreements are the
interesting rows.
"""

import argparse
import csv
import os
import re
import sys

# Windows defaults stdout to cp1252, which cannot encode the accented and
# non-Latin characters that appear in real deal titles (e.g. "ń", "—", emoji).
# Redirecting to a file then raises UnicodeEncodeError and loses the run.
# Force UTF-8 so output is identical on every platform.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

EVAL_PATH = os.path.join("data", "eval_set.csv")
LOG_PATH = os.path.join("logs", "latest_deals.md")

PROMPT = """\
You are grading a deal-finding bot. It looks for genuine deals on AI tools —
discounts, promo codes, free trials, student offers, free credits.

For each numbered item below, answer 1 or 0.

  1 = a real, currently claimable offer on an AI tool
  0 = anything else

Answer 0 for all of these, even though they mention deals or free things:
  - pricing pages and plan documentation ("Copilot Plans & pricing")
  - support threads and complaints ("Why did I not get the coupon?")
  - questions asking whether a deal exists ("ChatGPT Plus trial?")
  - product launches and Show HN posts with no explicit offer
  - news and commentary about AI business deals or funding
  - tutorials and guides
  - navigation or FAQ pages
  - offers that have clearly expired or are out of season

Answer 1 for:
  - a specific discount, percentage off, or promo code
  - a free trial, free month, or free tier explicitly being offered
  - student, educator, or startup programmes with real benefits
  - free credits being given away
  - curated lists of currently-live AI deals

Reply with ONLY the numbers and verdicts, one per line, nothing else:

1: 0
2: 1
3: 1

Items to grade:

"""


def _read_last_run() -> list[tuple[str, str, str]]:
    """Return (title, url, source) for the most recent run in the history log."""
    if not os.path.exists(LOG_PATH):
        sys.exit(f"No log at {LOG_PATH}. Run: python src/main.py --dry-run")

    blocks, current = [], []
    with open(LOG_PATH, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("## Run:"):
                if current:
                    blocks.append(current)
                current = []
            m = re.match(r"- \[(.*?)\]\((.*?)\) \| (\w+) \|", line)
            if m:
                current.append(m.groups())
    if current:
        blocks.append(current)

    if not blocks:
        sys.exit("No deals found in the log.")
    return blocks[-1]


def _already_labelled() -> set[str]:
    """Titles already in the eval set, so we only grade new material."""
    if not os.path.exists(EVAL_PATH):
        return set()
    with open(EVAL_PATH, encoding="utf-8") as fh:
        return {row["title"] for row in csv.DictReader(fh)}


def do_export() -> None:
    """Print the AI prompt followed by numbered, unlabelled deals."""
    rows = _read_last_run()
    known = _already_labelled()
    new = [r for r in rows if r[0] not in known]

    if not new:
        print("Everything in the last run is already labelled.", file=sys.stderr)
        return

    print(PROMPT)
    for i, (title, url, source) in enumerate(new, 1):
        print(f"{i}. [{source}] {title}")
        print(f"   {url}")

    print(f"\n({len(new)} items; {len(rows) - len(new)} already labelled)",
          file=sys.stderr)


def _parse_verdicts(path: str) -> dict[int, int]:
    """Extract ``N: 0|1`` verdicts from one model's reply."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    return {
        int(m.group(1)): int(m.group(2))
        for m in re.finditer(r"^\s*(\d+)\s*[:.)]\s*([01])\s*$", text, re.M)
    }


def do_merge(paths: list[str], accept_split: bool = False) -> None:
    """Combine several models' verdicts by vote.

    One model's labels are one model's opinion.  Several models labelling the
    same items independently gives two useful things: agreement, which is
    reasonable evidence the label is right, and disagreement, which reliably
    marks the genuinely ambiguous cases.

    Unanimous and clear-majority items are written straight to the eval set.
    Split decisions are printed for a human rather than silently averaged —
    an eval set is only worth having if its labels are trustworthy.
    """
    rows = _read_last_run()
    known = _already_labelled()
    new = [r for r in rows if r[0] not in known]

    votes: dict[str, dict[int, int]] = {}
    for path in paths:
        name = os.path.basename(path).replace("labels_", "").replace(".txt", "")
        v = _parse_verdicts(path)
        if not v:
            print(f"  warning: no verdicts parsed from {path}", file=sys.stderr)
            continue
        votes[name] = v

    if not votes:
        sys.exit("No verdicts parsed from any file.")

    models = sorted(votes)
    print(f"\n  merging {len(models)} model(s): {', '.join(models)}\n")

    agreed: list[tuple[tuple[str, str, str], int, str]] = []
    split: list[tuple[int, tuple[str, str, str], dict[str, int]]] = []

    for i, row in enumerate(new, 1):
        cast = {m: votes[m][i] for m in models if i in votes[m]}
        if not cast:
            continue
        ones = sum(cast.values())
        total = len(cast)

        if ones == total:
            agreed.append((row, 1, f"consensus {total}/{total}"))
        elif ones == 0:
            agreed.append((row, 0, f"consensus {total}/{total}"))
        elif ones * 2 > total:
            agreed.append((row, 1, f"majority {ones}/{total}"))
        elif ones * 2 < total:
            agreed.append((row, 0, f"majority {total - ones}/{total}"))
        else:
            split.append((i, row, cast))

    if split:
        print(f"  {len(split)} SPLIT decision(s) — models disagree evenly.")
        print("  These are the ambiguous ones; decide them yourself.\n")
        for i, (title, url, source) in [(i, r) for i, r, _ in split]:
            cast = next(c for j, _, c in split if j == i)
            detail = "  ".join(f"{m}={v}" for m, v in sorted(cast.items()))
            print(f"    [{i}] {title[:78]}")
            print(f"        {detail}")
        print()

    header = ["title", "url", "source", "label", "notes"]
    exists = os.path.exists(EVAL_PATH)
    with open(EVAL_PATH, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=header)
        if not exists:
            writer.writeheader()
        for (title, url, source), label, note in agreed:
            writer.writerow({
                "title": title, "url": url, "source": source,
                "label": label, "notes": note,
            })

    pos = sum(1 for _, l, _ in agreed if l == 1)
    unanimous = sum(1 for _, _, n in agreed if n.startswith("consensus"))
    print(f"  added {len(agreed)} rows ({pos} deals / {len(agreed) - pos} noise)")
    print(f"    {unanimous} unanimous, {len(agreed) - unanimous} by majority")
    if split:
        print(f"    {len(split)} left out — label those by hand if you want them")

    # Agreement rate is a sanity check on the labels themselves: if the models
    # rarely agree, the task definition is unclear and the labels are weak.
    if len(models) > 1 and (len(agreed) + len(split)):
        rate = 100 * unanimous / (len(agreed) + len(split))
        print(f"\n  unanimous agreement: {rate:.0f}%")
        if rate < 60:
            print("  Low agreement — the grading prompt is probably ambiguous.")

    print("\n  Now measure:\n    python tools/eval_filter.py --compare\n")


def do_import(path: str) -> None:
    """Merge ``N: 0|1`` verdicts into the eval set."""
    rows = _read_last_run()
    known = _already_labelled()
    new = [r for r in rows if r[0] not in known]

    with open(path, encoding="utf-8") as fh:
        text = fh.read()

    verdicts: dict[int, int] = {}
    for match in re.finditer(r"^\s*(\d+)\s*[:.)]\s*([01])\s*$", text, re.M):
        verdicts[int(match.group(1))] = int(match.group(2))

    if not verdicts:
        sys.exit(
            "No verdicts found. Expected lines like '1: 0'. "
            "Paste only the AI's answer, not its explanation."
        )

    missing = [i for i in range(1, len(new) + 1) if i not in verdicts]
    if missing:
        print(f"  warning: no verdict for item(s) {missing[:10]}"
              f"{'...' if len(missing) > 10 else ''} — skipping those",
              file=sys.stderr)

    header = ["title", "url", "source", "label", "notes"]
    exists = os.path.exists(EVAL_PATH)
    added = 0
    with open(EVAL_PATH, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=header)
        if not exists:
            writer.writeheader()
        for i, (title, url, source) in enumerate(new, 1):
            if i not in verdicts:
                continue
            writer.writerow({
                "title": title, "url": url, "source": source,
                "label": verdicts[i], "notes": "ai-labelled",
            })
            added += 1

    pos = sum(1 for v in verdicts.values() if v == 1)
    print(f"\n  added {added} rows ({pos} deals / {added - pos} noise)")
    print("\n  Now measure the effect:")
    print("    python tools/eval_filter.py --compare\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("export", help="print deals + prompt for an AI to grade")
    imp = sub.add_parser("import", help="merge one AI's verdicts into the eval set")
    imp.add_argument("file", help="file containing the AI's reply")
    mrg = sub.add_parser("merge", help="combine several models' verdicts by vote")
    mrg.add_argument("files", nargs="+", help="labels_<model>.txt files")
    args = ap.parse_args()

    if args.cmd == "export":
        do_export()
    elif args.cmd == "merge":
        do_merge(args.files)
    else:
        do_import(args.file)


if __name__ == "__main__":
    main()
