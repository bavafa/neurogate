"""Post-training int8 quantization (SPEC section 6). Run: ``python -m model.quantize``

Reads ``data/model/float_weights.json`` (from train.py) and writes, all under
``data/model/``:
  * ``weights.json``   integer parameters, read by golden.py and the RTL tests
  * ``rom_w1.hex``     128 lines, 2 hex digits (int8, two's complement), address = j*8 + i
  * ``rom_b1.hex``     16 lines, 8 hex digits (int32 two's complement)
  * ``rom_w2.hex``     48 lines, 2 hex digits, address = k*16 + j
  * ``rom_b2.hex``     3 lines, 8 hex digits
The ``.hex`` files are one value per line, the format ``$readmemh`` reads.

Scheme (symmetric, per-tensor scales, power-of-two requantize shift):
  * input   x_q = feature (0..127); float input is x_q / 127.
  * weights w_q = round(w / s_w), s_w = max|w| / 127, so w_q is in -127..127.
  * acc1 unit u1 = s_w1 / 127 float units; b1_q = round(b1 / u1).
  * s1 = smallest shift so the 99.9th percentile positive acc1 on the TRAINING
    split fits 127 after the shift. Hidden activation unit = u1 * 2**s1.
  * layer 2 unit u2 = s_w2 * (u1 * 2**s1); b2_q = round(b2 / u2).
  * conf_shift = smallest shift so the 95th percentile training margin fits 127.
Only the training split is used for calibration (no validation or test data).
"""
from __future__ import annotations

import math
import os
from typing import List

import numpy as np

from model import golden
from model.dataset import load_splits
from model.train import FEATURE_SCALE, load as load_float, Params

OUT_DIR = os.path.join("data", "model")
HID_PCT, MARGIN_PCT = 99.9, 95.0


def _ceil_log2_ratio(value: float, limit: float) -> int:
    """Smallest s >= 0 with value / 2**s <= limit."""
    return 0 if value <= limit else int(math.ceil(math.log2(value / limit)))


def quantize(p: Params, train_feats: np.ndarray) -> golden.QParams:
    """Quantize float parameters; calibrate shifts on ``train_feats`` (N, 8 ints)."""
    sw1, sw2 = np.abs(p["w1"]).max() / 127, np.abs(p["w2"]).max() / 127
    w1 = np.clip(np.rint(p["w1"] / sw1), -127, 127).astype(np.int64)
    w2 = np.clip(np.rint(p["w2"] / sw2), -127, 127).astype(np.int64)
    u1 = sw1 / FEATURE_SCALE                                  # float value of one acc1 unit
    b1 = np.rint(p["b1"] / u1).astype(np.int64)
    acc1 = train_feats @ w1.T + b1
    pos = acc1[acc1 > 0]
    shift1 = _ceil_log2_ratio(float(np.percentile(pos, HID_PCT)), 127.0)
    u2 = sw2 * u1 * (2 ** shift1)                             # float value of one logit unit
    b2 = np.rint(p["b2"] / u2).astype(np.int64)
    base = golden.QParams(tuple(map(tuple, w1.tolist())), tuple(b1.tolist()),
                          tuple(map(tuple, w2.tolist())), tuple(b2.tolist()), shift1, 0)
    margins: List[int] = []
    for x in train_feats:
        z = sorted(golden.logits(golden.hidden(x.tolist(), base), base))
        margins.append(z[-1] - z[-2])
    conf_shift = _ceil_log2_ratio(float(np.percentile(margins, MARGIN_PCT)), 127.0)
    return golden.QParams(base.w1, base.b1, base.w2, base.b2, shift1, conf_shift)


def _hex(v: int, digits: int) -> str:
    """Two's-complement hex of ``v`` in ``digits`` hex digits."""
    return format(v & ((1 << (4 * digits)) - 1), f"0{digits}x")


def write_files(p: golden.QParams, out_dir: str = OUT_DIR) -> None:
    """Write weights.json and the four ROM hex files."""
    import json
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "weights.json"), "w") as fh:
        json.dump(golden.to_json_dict(p), fh, indent=1)
        fh.write("\n")
    roms = {"rom_w1.hex": ([w for r in p.w1 for w in r], 2), "rom_b1.hex": (list(p.b1), 8),
            "rom_w2.hex": ([w for r in p.w2 for w in r], 2), "rom_b2.hex": (list(p.b2), 8)}
    for name, (vals, digits) in roms.items():
        with open(os.path.join(out_dir, name), "w") as fh:
            fh.write("".join(_hex(v, digits) + "\n" for v in vals))


def main() -> None:
    """Quantize the saved float weights and write the output files."""
    p = quantize(load_float(), load_splits()["train"].features)
    golden.validate(p)
    write_files(p)
    print(f"shift1={p.shift1} conf_shift={p.conf_shift} "
          f"w1 range {min(map(min, p.w1))}..{max(map(max, p.w1))} -> {OUT_DIR}/weights.json + 4 hex files")


if __name__ == "__main__":
    main()
