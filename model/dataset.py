"""Build the train / validation / test feature sets from the synthetic traffic generator.

Why this file exists: training, quantization, evaluation and the test-vector
export all need the same three data splits, so the split rules live in one place.

Split rules (SPEC section 9, D-008). All data is synthetic.
  * Training pool: generator master seed ``TRAIN_SEED``, ``TRAIN_POOL_PER_CLASS``
    scenarios per class. Per class, the first ``TRAIN_PER_CLASS`` scenarios are
    the training split and the rest are the validation split. Whole scenarios
    are never split, so no packet stream is shared between splits.
  * Test split: a different master seed (``TEST_SEED``), so every test scenario
    has a seed that no training scenario has. It is used only for final reports.

Each split is run as one continuous frame stream (scenarios in a fixed shuffled
order) through the same parser and feature window the chip uses
(``model/features.py``). Dropped frames never reach the neural net, so they are
counted but have no feature row.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np

from model.features import KIND_CLASSIFY, run_stream
from model.traffic_gen import Frame, generate

TRAIN_SEED = 1            # generator master seed of the training pool
TEST_SEED = 2             # generator master seed of the unseen test split
TRAIN_POOL_PER_CLASS = 14  # scenarios per class in the training pool
TRAIN_PER_CLASS = 10      # of those, used for training (rest: validation)
TEST_PER_CLASS = 10
PACKETS_PER_SCENARIO = 300
SHUFFLE_SEED = 7          # fixed order of scenarios inside a stream


@dataclass(frozen=True)
class Split:
    """One data split: features (N, 8) int, labels (N,), plus frame counts."""

    name: str
    features: np.ndarray   # int64, shape (N, 8), values 0..127; only classified frames
    labels: np.ndarray     # int64, shape (N,), 0 normal / 1 port_scan / 2 syn_flood
    frames_total: int      # every frame in the stream
    frames_dropped: int    # frames the parser dropped (not classified)


def _stream_to_split(name: str, rows: List[Tuple[int, int, Frame]]) -> Split:
    """Order scenarios, run the chip's parser + window over them, keep classified frames."""
    by_scn: Dict[int, List[Frame]] = {}
    for sid, _, f in rows:
        by_scn.setdefault(sid, []).append(f)
    order = sorted(by_scn)
    random.Random(SHUFFLE_SEED).shuffle(order)
    frames = [f for sid in order for f in by_scn[sid]]
    recs = run_stream([(f.data, f.tuser) for f in frames])
    feats, labs = [], []
    for f, r in zip(frames, recs):
        if r.kind == KIND_CLASSIFY:
            feats.append(r.features)
            labs.append(f.label)
    return Split(name, np.array(feats, dtype=np.int64).reshape(-1, 8),
                 np.array(labs, dtype=np.int64), len(frames), len(frames) - len(feats))


def load_splits() -> Dict[str, Split]:
    """Return ``{"train": ..., "val": ..., "test": ...}`` (deterministic, no files needed)."""
    pool = generate(TRAIN_SEED, TRAIN_POOL_PER_CLASS, PACKETS_PER_SCENARIO)
    # generate() numbers scenarios class by class: id = class * pool_per_class + k.
    train = [r for r in pool if r[0] % TRAIN_POOL_PER_CLASS < TRAIN_PER_CLASS]
    val = [r for r in pool if r[0] % TRAIN_POOL_PER_CLASS >= TRAIN_PER_CLASS]
    test = generate(TEST_SEED, TEST_PER_CLASS, PACKETS_PER_SCENARIO)
    return {"train": _stream_to_split("train", train),
            "val": _stream_to_split("val", val),
            "test": _stream_to_split("test", test)}
