"""Export integer test vectors for the RTL tests. Run: ``python -m model.export_vectors``

Writes ``data/vectors/mlp_vectors.csv``. Each row is one input and the golden
model's answer, so the ``mlp_core`` and ``decision_unit`` tests can compare the
RTL against it bit for bit. Columns:
  kind, f0..f7 (features, 0..127), z0..z2 (int32 logits), class, confidence, low_conf
``low_conf`` uses the default CONF_THRESH from ``data/model/conf_thresh.txt``.

Rows (fixed seed, so the file is reproducible):
  * ``directed``  all zeros, all 127, one-hot per feature, alternating patterns
  * ``corner``    found by random search: logit ties, rounding-half cases in
                  layer 1, hidden saturation (activation capped at 127), all
                  hidden units zero, confidence capped at 127, max-|logit|
  * ``random``    uniform random features
  * ``test``      the first 1500 classified packets of the unseen test split (exact duplicates removed)
Rows never repeat.

``--full`` writes a second file, ``data/vectors/mlp_vectors_full.csv``: every
classified packet of the unseen test split in stream order, duplicates kept
(8,347 rows). ``make test-full`` replays it through ``mlp_core`` +
``decision_unit``. Features outside 0..127 are not exported: the feature
builder cannot produce them.
"""
from __future__ import annotations

import argparse
import csv
import os
from typing import List, Sequence, Tuple

import numpy as np

from model import golden
from model.dataset import load_splits

OUT_PATH = os.path.join("data", "vectors", "mlp_vectors.csv")
FULL_PATH = os.path.join("data", "vectors", "mlp_vectors_full.csv")
SEED = 4242
N_RANDOM, N_TEST, N_SEARCH = 800, 1500, 300000


def _vec_arith(p: golden.QParams, xs: np.ndarray) -> dict:
    """Vectorised int64 copy of the golden arithmetic, used only to SEARCH for corner cases."""
    w1, w2 = np.array(p.w1, dtype=np.int64), np.array(p.w2, dtype=np.int64)
    acc = xs @ w1.T + np.array(p.b1, dtype=np.int64)
    half = (1 << (p.shift1 - 1)) if p.shift1 else 0
    pre = (acc + half) >> p.shift1
    h = np.clip(pre, 0, 127)
    z = h @ w2.T + np.array(p.b2, dtype=np.int64)
    zs = np.sort(z, axis=1)
    return {"acc": acc, "pre": pre, "h": h, "z": z, "margin": zs[:, -1] - zs[:, -2]}


def corner_inputs(p: golden.QParams, rng: np.random.Generator) -> List[List[int]]:
    """Random-search for inputs that hit the arithmetic corner cases (see module doc)."""
    xs = rng.integers(0, 128, (N_SEARCH, 8))
    xs[::7] = rng.choice([0, 127], size=(len(xs[::7]), 8))      # extra weight on extremes
    a = _vec_arith(p, xs)
    found: List[int] = []
    low = (1 << p.shift1) - 1
    half = 1 << (p.shift1 - 1) if p.shift1 else 0
    masks = {
        "tie": a["margin"] == 0,
        "round_half": ((a["acc"] & low) == half).any(axis=1) if p.shift1 else np.zeros(len(xs), bool),
        "saturate": (a["pre"] > 127).sum(axis=1) >= 1,
        "all_zero_hidden": (a["h"] == 0).all(axis=1),
        "conf_cap": (a["margin"] >> p.conf_shift) >= 127,
        "conf_just_under": (a["margin"] >> p.conf_shift) == 126,
    }
    for m in masks.values():
        found += np.flatnonzero(m)[:6].tolist()
    # the most extreme hidden saturation and the largest |logit|
    found += [int((a["pre"] > 127).sum(axis=1).argmax()), int(np.abs(a["z"]).max(axis=1).argmax())]
    return [xs[i].tolist() for i in found]


def directed_inputs() -> List[List[int]]:
    """Hand-written patterns: zeros, maxima, one-hot and alternating."""
    v = [[0] * 8, [127] * 8, [1] * 8, [64] * 8, [0, 127] * 4, [127, 0] * 4]
    for i in range(8):
        e = [0] * 8
        e[i] = 127
        v.append(e)
        e = [127] * 8
        e[i] = 0
        v.append(e)
    return v


def build_rows(p: golden.QParams, thresh: int) -> List[Tuple[str, List[int]]]:
    """Return unique (kind, features) rows in a fixed order."""
    rng = np.random.default_rng(SEED)
    rows: List[Tuple[str, List[int]]] = [("directed", x) for x in directed_inputs()]
    rows += [("corner", x) for x in corner_inputs(p, rng)]
    rows += [("random", x) for x in rng.integers(0, 128, (N_RANDOM, 8)).tolist()]
    rows += [("test", x.tolist()) for x in load_splits()["test"].features[:N_TEST]]
    seen, out = set(), []
    for kind, x in rows:
        if tuple(x) not in seen:
            seen.add(tuple(x))
            out.append((kind, x))
    return out


def write_csv(path: str, rows: List[Tuple[str, List[int]]], p: golden.QParams, thresh: int) -> None:
    """Write rows with the golden model's answer for each (columns in the module doc)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["kind"] + [f"f{i}" for i in range(8)] + [f"z{k}" for k in range(3)]
                   + ["class", "confidence", "low_conf"])
        for kind, x in rows:
            cls, conf, low, _, z = golden.infer(x, p, thresh)
            w.writerow([kind] + x + z + [cls, conf, int(low)])


def main() -> None:
    """Write the CSV (or, with --full, the full test-split CSV) and print row counts."""
    ap = argparse.ArgumentParser(description="export golden-model test vectors")
    ap.add_argument("--full", action="store_true",
                    help="write every classified test-split packet to mlp_vectors_full.csv")
    args = ap.parse_args()
    p = golden.load_params()
    with open(os.path.join("data", "model", "conf_thresh.txt")) as fh:
        thresh = int(fh.read().strip())
    if args.full:
        rows = [("test", x.tolist()) for x in load_splits()["test"].features]
        write_csv(FULL_PATH, rows, p, thresh)
        print(f"wrote {len(rows)} vectors (all classified test-split packets, duplicates kept) "
              f"to {FULL_PATH} (conf_thresh={thresh})")
        return
    rows = build_rows(p, thresh)
    write_csv(OUT_PATH, rows, p, thresh)
    counts = {k: sum(1 for kk, _ in rows if kk == k) for k in ("directed", "corner", "random", "test")}
    print(f"wrote {len(rows)} vectors {counts} to {OUT_PATH} (conf_thresh={thresh})")


if __name__ == "__main__":
    main()
