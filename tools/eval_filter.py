"""Measure the relevance filter against the labeled evaluation set.

Usage::

    python tools/eval_filter.py                 # full report
    python tools/eval_filter.py --quiet         # scores only
    python tools/eval_filter.py --baseline      # write scores to data/eval_baseline.json
    python tools/eval_filter.py --compare       # diff against the saved baseline

Run this before and after every filter change.  The false-negative and
false-positive lists at the bottom of the report matter more than the
headline numbers: they tell you *what* to fix next.
"""

import argparse
import csv
import json
import os
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

from config import DROP_STALE  # noqa: E402
from filter import is_relevant, is_stale, score_deal  # noqa: E402

EVAL_PATH = os.path.join("data", "eval_set.csv")
BASELINE_PATH = os.path.join("data", "eval_baseline.json")


def load_rows(path: str = EVAL_PATH) -> list[dict]:
    """Load the labeled evaluation set, skipping unlabeled rows."""
    if not os.path.exists(path):
        sys.exit(f"No eval set at {path}. See docs/ACTION_PLAN.md step 3.")
    with open(path, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        r["label"] = int(r["label"])
    return rows


def evaluate(rows: list[dict]) -> dict:
    """Run the filter over every row and return metrics plus error lists."""
    tp = fp = tn = fn = 0
    false_neg: list[dict] = []
    false_pos: list[dict] = []

    for r in rows:
        # Mirror the pipeline exactly: main.py applies the staleness drop
        # after is_relevant.  Measuring is_relevant alone would score a
        # filter that does not exist in production.
        predicted = is_relevant(r["title"], "", 0, r.get("url", ""))
        if predicted and DROP_STALE and is_stale(r["title"], ""):
            predicted = False
        actual = bool(r["label"])
        if predicted and actual:
            tp += 1
        elif predicted and not actual:
            fp += 1
            false_pos.append(r)
        elif not predicted and actual:
            fn += 1
            false_neg.append(r)
        else:
            tn += 1

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    return {
        "total": len(rows),
        "positives": tp + fn,
        "negatives": tn + fp,
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "_false_neg": false_neg,
        "_false_pos": false_pos,
    }


def report(m: dict, quiet: bool = False) -> None:
    """Print the evaluation report."""
    print()
    print(f"  eval set   {m['total']} rows  ({m['positives']} real deals / {m['negatives']} noise)")
    print()
    print(f"  precision  {m['precision']:.3f}   of what we send, how much is real")
    print(f"  recall     {m['recall']:.3f}   of real deals, how many we catch")
    print(f"  F1         {m['f1']:.3f}")
    print()
    print(f"  TP {m['tp']:4}   FP {m['fp']:4}   TN {m['tn']:4}   FN {m['fn']:4}")

    if quiet:
        return

    if m["_false_neg"]:
        print(f"\n--- FALSE NEGATIVES ({len(m['_false_neg'])}) — real deals we MISSED ---")
        print("    these are your recall problem; fix these first\n")
        for r in m["_false_neg"][:40]:
            print(f"    score={score_deal(r['title']):3}  {r['title'][:95]}")

    if m["_false_pos"]:
        print(f"\n--- FALSE POSITIVES ({len(m['_false_pos'])}) — noise we would SEND ---")
        print("    these cost subscriber trust; keep this list short\n")
        for r in m["_false_pos"][:40]:
            print(f"    score={score_deal(r['title']):3}  {r['title'][:95]}")

    unreviewed = sum(1 for r in m["_false_pos"] + m["_false_neg"] if "REVIEW ME" in r.get("notes", ""))
    if unreviewed:
        print(f"\n  note: {unreviewed} of the above are still labeled 'REVIEW ME' "
              f"— hand-check them or the scores are unreliable.")
    print()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true", help="scores only")
    ap.add_argument("--baseline", action="store_true", help="save current scores as the baseline")
    ap.add_argument("--compare", action="store_true", help="diff against the saved baseline")
    args = ap.parse_args()

    metrics = evaluate(load_rows())
    report(metrics, quiet=args.quiet)

    if args.baseline:
        clean = {k: v for k, v in metrics.items() if not k.startswith("_")}
        with open(BASELINE_PATH, "w", encoding="utf-8") as fh:
            json.dump(clean, fh, indent=2)
        print(f"  baseline written to {BASELINE_PATH}\n")

    if args.compare:
        if not os.path.exists(BASELINE_PATH):
            sys.exit("No baseline yet. Run with --baseline first.")
        with open(BASELINE_PATH, encoding="utf-8") as fh:
            base = json.load(fh)
        print("  vs baseline:")
        for key in ("precision", "recall", "f1"):
            delta = metrics[key] - base[key]
            arrow = "▲" if delta > 0 else ("▼" if delta < 0 else "=")
            print(f"    {key:10} {base[key]:.3f} → {metrics[key]:.3f}  {arrow} {delta:+.3f}")
        print()


if __name__ == "__main__":
    main()
