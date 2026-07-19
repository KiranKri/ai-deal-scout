"""Precision floor against data/eval_set.csv.

Fails when keyword edits silently destroy precision.  Floor is set from the
honest post-fix baseline (see data/eval_baseline.json) with a small buffer.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tools.eval_filter import evaluate, load_rows  # noqa: E402

# Floor chosen after Priority 1–2 fixes + populated eval set.  Do not lower
# without an explicit product decision — silent precision collapse is the bug.
PRECISION_FLOOR = 0.55
RECALL_FLOOR = 0.70
MIN_POSITIVES = 40


def test_eval_set_has_enough_positives():
    rows = load_rows()
    positives = sum(1 for r in rows if r["label"] == 1)
    assert positives >= MIN_POSITIVES, (
        f"eval set has only {positives} positives; need >= {MIN_POSITIVES}"
    )


def test_filter_precision_above_floor():
    m = evaluate(load_rows())
    assert m["precision"] >= PRECISION_FLOOR, (
        f"precision {m['precision']:.3f} < floor {PRECISION_FLOOR}; "
        f"FP={m['fp']} TP={m['tp']}. Inspect tools/eval_filter.py output."
    )


def test_filter_recall_above_floor():
    m = evaluate(load_rows())
    assert m["recall"] >= RECALL_FLOOR, (
        f"recall {m['recall']:.3f} < floor {RECALL_FLOOR}; "
        f"FN={m['fn']} TP={m['tp']}. Inspect tools/eval_filter.py output."
    )
