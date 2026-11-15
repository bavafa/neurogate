"""Golden model: integer-only inference of the 8-16-3 net (SPEC sections 6 and 7).

THIS FILE IS THE SOURCE OF TRUTH for the chip's arithmetic. The RTL
(``mlp_core`` and ``decision_unit``) must match it bit for bit. If they
disagree, find out why and fix the wrong one.

Arithmetic, all in Python integers (no floats anywhere in inference):
  1. Input: 8 features, each int 0..127 (from ``model/features.py``).
  2. Layer 1: ``acc1[j] = b1[j] + sum_i w1[j][i] * x[i]``. Weights are int8
     (-127..127), biases are int32, the accumulator is int32. It cannot
     overflow: ``validate`` only accepts biases at least 8*127*127 away from
     the int32 limits (and 16*127*127 for layer 2).
  3. Requantize: ``h[j] = clamp((acc1[j] + 2**(s1-1)) >> s1, 0, 127)``.
     ``>>`` is an arithmetic right shift (floor), so adding half before the
     shift gives round-half-up. The clamp at 0 is the ReLU; the clamp at 127
     is the saturation. If ``s1 == 0`` there is no shift and no rounding term.
  4. Layer 2: ``z[k] = b2[k] + sum_j w2[k][j] * h[j]`` (int32, no ReLU).
  5. Decision: ``class = argmax(z)``, ties go to the lowest class index.
     ``margin = top - second`` (>= 0). ``confidence = min(margin >> conf_shift, 127)``.
     ``low_conf = confidence < conf_thresh``. Confidence is a logit margin,
     not a probability.

``conf_shift`` (8 for the trained model) is the owner-approved confidence rule (D-022,
SPEC section 7): with raw int32 logits the margin is almost always far above 127, so
the 0..127 confidence would be flat without the shift. ``conf_shift = 0`` gives the
unshifted difference.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import List, Sequence, Tuple

N_IN, N_HID, N_OUT = 8, 16, 3
ACT_MAX = 127            # hidden activations and confidence saturate here
INT8_MAX = 127
INT32_MIN, INT32_MAX = -(2 ** 31), 2 ** 31 - 1
# Largest possible |sum of weight * input| per output of each layer:
# inputs are 0..127 and weights -127..127, 8 inputs to layer 1, 16 to layer 2.
MAX_DOT1 = N_IN * INT8_MAX * ACT_MAX      # 129,032
MAX_DOT2 = N_HID * INT8_MAX * ACT_MAX     # 258,064
WEIGHTS_PATH = os.path.join("data", "model", "weights.json")


@dataclass(frozen=True)
class QParams:
    """Quantized network parameters (what the ROMs hold, plus two shift amounts)."""

    w1: Tuple[Tuple[int, ...], ...]   # 16 x 8, int8
    b1: Tuple[int, ...]               # 16, int32
    w2: Tuple[Tuple[int, ...], ...]   # 3 x 16, int8
    b2: Tuple[int, ...]               # 3, int32
    shift1: int                       # layer-1 requantize right shift
    conf_shift: int                   # confidence right shift (8, SPEC section 7)


def validate(p: QParams) -> None:
    """Raise ValueError if a parameter is outside its hardware width."""
    if len(p.w1) != N_HID or any(len(r) != N_IN for r in p.w1):
        raise ValueError("w1 must be 16 x 8")
    if len(p.w2) != N_OUT or any(len(r) != N_HID for r in p.w2):
        raise ValueError("w2 must be 3 x 16")
    if len(p.b1) != N_HID or len(p.b2) != N_OUT:
        raise ValueError("bias lengths must be 16 and 3")
    for w in [w for r in p.w1 + p.w2 for w in r]:
        if not -INT8_MAX <= w <= INT8_MAX:   # -128 is never used (symmetric range)
            raise ValueError(f"weight {w} outside -127..127")
    # Every accumulator (bias plus any weighted sum) must fit in int32, the
    # width of the hardware accumulator. So a bias may not come closer to the
    # int32 limits than the largest weighted sum of its layer. Inside this
    # range the int32 hardware never wraps and equals the unbounded Python
    # integers used here (D-025). Real trained biases are far inside it.
    for biases, dot in ((p.b1, MAX_DOT1), (p.b2, MAX_DOT2)):
        for b in biases:
            if not INT32_MIN + dot <= b <= INT32_MAX - dot:
                raise ValueError(f"bias {b} too close to the int32 limits (layer sum up to {dot})")
    if not 0 <= p.shift1 <= 30 or not 0 <= p.conf_shift <= 30:
        raise ValueError("shifts must be 0..30")


def to_json_dict(p: QParams) -> dict:
    """Plain-dict form of the parameters, as stored in weights.json."""
    return {"w1": [list(r) for r in p.w1], "b1": list(p.b1),
            "w2": [list(r) for r in p.w2], "b2": list(p.b2),
            "shift1": p.shift1, "conf_shift": p.conf_shift}


def from_json_dict(d: dict) -> QParams:
    """Build (and validate) ``QParams`` from a weights.json dict."""
    p = QParams(tuple(tuple(r) for r in d["w1"]), tuple(d["b1"]),
                tuple(tuple(r) for r in d["w2"]), tuple(d["b2"]),
                int(d["shift1"]), int(d["conf_shift"]))
    validate(p)
    return p


def load_params(path: str = WEIGHTS_PATH) -> QParams:
    """Read weights.json."""
    with open(path) as fh:
        return from_json_dict(json.load(fh))


def hidden(x: Sequence[int], p: QParams) -> List[int]:
    """Layer 1 plus requantize and ReLU: 8 features -> 16 activations in 0..127."""
    if len(x) != N_IN or any(not 0 <= v <= ACT_MAX for v in x):
        raise ValueError("features must be 8 ints in 0..127")
    out = []
    for j in range(N_HID):
        acc = p.b1[j] + sum(p.w1[j][i] * x[i] for i in range(N_IN))
        if p.shift1:
            acc = (acc + (1 << (p.shift1 - 1))) >> p.shift1   # round half up
        out.append(max(0, min(ACT_MAX, acc)))                 # ReLU + saturate
    return out


def logits(h: Sequence[int], p: QParams) -> List[int]:
    """Layer 2: 16 activations -> 3 int32 logits (no ReLU, no requantize)."""
    return [p.b2[k] + sum(p.w2[k][j] * h[j] for j in range(N_HID)) for k in range(N_OUT)]


def decide(z: Sequence[int], conf_shift: int, conf_thresh: int) -> Tuple[int, int, bool]:
    """Logits -> (class, confidence 0..127, low_conf). Ties: lowest index wins."""
    cls = max(range(N_OUT), key=lambda k: (z[k], -k))
    top = z[cls]
    second = max(z[k] for k in range(N_OUT) if k != cls)
    conf = min((top - second) >> conf_shift, ACT_MAX)
    return cls, conf, conf < conf_thresh


def infer(x: Sequence[int], p: QParams, conf_thresh: int = 0) -> Tuple[int, int, bool, List[int], List[int]]:
    """Full inference. Returns (class, confidence, low_conf, hidden, logits)."""
    h = hidden(x, p)
    z = logits(h, p)
    cls, conf, low = decide(z, p.conf_shift, conf_thresh)
    return cls, conf, low, h, z
