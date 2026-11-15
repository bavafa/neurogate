"""Custom weight sets for the mlp_core corner-case builds (run from .venv by
tb/test_mlp_core.py). Each set is a full parameter set that golden.validate()
accepts, plus the input vectors to run on it. Each is written to its own folder
as weights.json + the four ROM .hex files (same writer as the real model,
model/quantize.py) + inputs.txt (one vector of 8 features per line).

Why custom weights: the trained network never reaches some arithmetic corners
(every hidden unit zero, the int32 limits, exact rounding halves with a small
shift). These sets force them, and the writer asserts that each corner is
really hit (using the golden model), so a set cannot silently miss its point.
"""
import os
import random
from typing import Callable, Dict, List, Sequence, Tuple

from model import golden
from model.golden import INT32_MAX, INT32_MIN, MAX_DOT1, MAX_DOT2, QParams
from model.quantize import write_files

Vec = List[int]


def _acc1(x: Sequence[int], p: QParams) -> List[int]:
    """Layer-1 accumulators (before requantize), for coverage checks."""
    return [p.b1[j] + sum(p.w1[j][i] * x[i] for i in range(8)) for j in range(16)]


def _rows(rng: random.Random, lo: int, hi: int, n: int) -> Tuple[Tuple[int, ...], ...]:
    """``n`` rows of random ints in lo..hi (16 x 8 or 3 x 16 weight matrices)."""
    width = 8 if n == 16 else 16
    return tuple(tuple(rng.randint(lo, hi) for _ in range(width)) for _ in range(n))


def _inputs(rng: random.Random, n: int, hi: int = 127) -> List[Vec]:
    """All-zero, all-max, and ``n`` random input vectors in 0..hi."""
    return [[0] * 8, [127] * 8] + [[rng.randint(0, hi) for _ in range(8)] for _ in range(n)]


def build_sets(seed: int) -> Dict[str, Tuple[QParams, List[Vec], Callable[[QParams, List[Vec]], None]]]:
    """name -> (params, inputs, coverage check). Fixed seed, so builds repeat exactly."""
    rng = random.Random(seed)
    sets = {}

    # 1. int32 maximum: acc1 = 2^31 - 1 exactly at x = 127, and with shift 7 the
    #    rounding add (acc + 64) passes the int32 maximum. Logits reach the int32
    #    maximum (class 0), maximum - 1 (class 1) and the int32 minimum (class 2).
    p = QParams(((127,) * 8,) * 16, (INT32_MAX - MAX_DOT1,) * 16,
                ((127,) * 16, (127,) * 16, (-127,) * 16),
                (INT32_MAX - MAX_DOT2, INT32_MAX - MAX_DOT2 - 1, INT32_MIN + MAX_DOT2), 7, 8)

    def cov_max(p: QParams, xs: List[Vec]) -> None:
        assert max(_acc1([127] * 8, p)) == INT32_MAX
        assert golden.logits(golden.hidden([127] * 8, p), p) == [INT32_MAX, INT32_MAX - 1, INT32_MIN]
    sets["int32_max"] = (p, _inputs(rng, 30), cov_max)

    # 2. int32 minimum in layer 1: acc1 = -2^31 exactly at x = 127 (ReLU gives 0),
    #    so every hidden unit is zero and the logits equal the biases, which sit
    #    at the edges of their allowed range.
    p = QParams(((-127,) * 8,) * 16, (INT32_MIN + MAX_DOT1,) * 16, _rows(rng, -127, 127, 3),
                (INT32_MIN + MAX_DOT2, INT32_MAX - MAX_DOT2, 5), 7, 8)

    def cov_min(p: QParams, xs: List[Vec]) -> None:
        assert min(_acc1([127] * 8, p)) == INT32_MIN
        assert golden.hidden([127] * 8, p) == [0] * 16
    sets["int32_min"] = (p, _inputs(rng, 30), cov_min)

    # 3. Every hidden unit zero for every input (large negative layer-1 bias), so
    #    the logits are exactly the layer-2 biases. Bias patterns give each tie:
    #    classes 0 and 1 tie for the top, 1 and 2 tie, all three tie, and a
    #    strict order with class 2 on top.
    for name, b2 in (("allzero_tie01", (7, 7, 3)), ("allzero_tie12", (-5, 9, 9)),
                     ("allzero_tie_all", (4, 4, 4)), ("allzero_top2", (1, 2, 3))):
        p = QParams(_rows(rng, -127, 127, 16), (-10 ** 6,) * 16, _rows(rng, -127, 127, 3), b2, 7, 8)

        def cov_zero(p: QParams, xs: List[Vec]) -> None:
            assert all(golden.hidden(x, p) == [0] * 16 for x in xs)
        sets[name] = (p, _inputs(rng, 20), cov_zero)

    # 4. Round half up with shift 2: small weights and biases make many layer-1
    #    sums land exactly on a half (acc mod 4 == 2). Layer-2 weights are all
    #    different, so a hidden value off by one changes a logit.
    p = QParams(_rows(rng, -3, 3, 16), tuple(rng.randint(-8, 8) for _ in range(16)),
                ((1,) * 16, tuple(range(1, 17)), tuple((-1) ** j * (16 - j) for j in range(16))),
                (0, 0, 0), 2, 8)

    def cov_half(p: QParams, xs: List[Vec]) -> None:
        halves = sum(1 for x in xs for a in _acc1(x, p) if a >= 0 and a % 4 == 2)
        assert halves >= 200, f"only {halves} exact halves"
    sets["round_half_shift2"] = (p, _inputs(rng, 200, 7) + _inputs(rng, 100), cov_half)

    # 5. Shift 0: no rounding term at all (the RTL's HALF constant is 0).
    p = QParams(_rows(rng, -2, 2, 16), tuple(rng.randint(-50, 50) for _ in range(16)),
                _rows(rng, -127, 127, 3), (0, 100, -100), 0, 8)

    def cov_shift0(p: QParams, xs: List[Vec]) -> None:
        mids = sum(1 for x in xs for a in _acc1(x, p) if 0 < a < 127)
        assert mids >= 200, f"only {mids} unsaturated hidden values"
    sets["shift0"] = (p, _inputs(rng, 200, 20), cov_shift0)

    # 6. Saturation: big weights and biases push many hidden units past 127
    #    (and many below 0), with random layer-2 biases.
    p = QParams(_rows(rng, -127, 127, 16), tuple(rng.randint(-20000, 20000) for _ in range(16)),
                _rows(rng, -127, 127, 3), tuple(rng.randint(-3000, 3000) for _ in range(3)), 7, 8)

    def cov_sat(p: QParams, xs: List[Vec]) -> None:
        hs = [h for x in xs for h in golden.hidden(x, p)]
        assert hs.count(127) >= 200 and hs.count(0) >= 200, (hs.count(127), hs.count(0))
    sets["saturate"] = (p, _inputs(rng, 200), cov_sat)

    # 7. Random weights with other shifts, biases scaled to the shift (shift 30
    #    uses biases across nearly the whole allowed int32 range).
    for s in (1, 5, 12, 30):
        lim = min((127 << s) if s < 24 else INT32_MAX, INT32_MAX - MAX_DOT1)
        p = QParams(_rows(rng, -127, 127, 16), tuple(rng.randint(-lim, lim) for _ in range(16)),
                    _rows(rng, -127, 127, 3), tuple(rng.randint(-3000, 3000) for _ in range(3)),
                    s, 8)
        sets[f"random_shift{s}"] = (p, _inputs(rng, 120), lambda p, xs: None)
    return sets


def write_all(out_root: str, seed: int) -> List[Tuple[str, str, int]]:
    """Write every set; return (name, folder, shift1) for each build."""
    out = []
    for name, (p, xs, cov) in build_sets(seed).items():
        golden.validate(p)
        cov(p, xs)
        d = os.path.join(out_root, name)
        os.makedirs(d, exist_ok=True)
        write_files(p, d)
        with open(os.path.join(d, "inputs.txt"), "w") as fh:
            fh.write("".join(" ".join(map(str, x)) + "\n" for x in xs))
        out.append((name, d, p.shift1))
    print(f"mlp custom weight sets: {len(out)} written to {out_root}", flush=True)
    return out
