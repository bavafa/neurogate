"""Train the 8-16-3 float neural net on the training split (SPEC section 6).

Run from the repo root:  ``python -m model.train``

What it does: full-batch Adam on softmax cross-entropy, plain NumPy, fixed seed.
Input is the 8 integer features divided by 127 (so 0..1). Layer 1 has ReLU,
layer 2 has none (the chip takes the argmax of the raw outputs). The best
epoch is chosen on the validation split, never on the test split.

Output: ``data/model/float_weights.json`` (float weights, read by quantize.py).
All numbers are from synthetic traffic.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Tuple

import numpy as np

from model.dataset import load_splits

N_IN, N_HID, N_OUT = 8, 16, 3
SEED = 20260101          # fixed seed: initial weights
EPOCHS = 1500
LR = 0.01
WEIGHT_DECAY = 1e-4      # small L2 pull on weights, keeps int8 ranges tame
FEATURE_SCALE = 127.0    # float input = integer feature / 127
OUT_PATH = os.path.join("data", "model", "float_weights.json")

Params = Dict[str, np.ndarray]


def init_params(seed: int) -> Params:
    """Return He-initialised weights for the 8-16-3 net from a fixed seed."""
    rng = np.random.default_rng(seed)
    return {"w1": rng.normal(0, np.sqrt(2 / N_IN), (N_HID, N_IN)), "b1": np.zeros(N_HID),
            "w2": rng.normal(0, np.sqrt(2 / N_HID), (N_OUT, N_HID)), "b2": np.zeros(N_OUT)}


def forward_float(p: Params, feats: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Float forward pass. ``feats`` is integer (N, 8). Returns (hidden, logits)."""
    x = feats / FEATURE_SCALE
    h = np.maximum(x @ p["w1"].T + p["b1"], 0.0)
    return h, h @ p["w2"].T + p["b2"]


def _loss_and_grads(p: Params, feats: np.ndarray, labels: np.ndarray) -> Tuple[float, Params]:
    """Cross-entropy loss and gradients (hand-written backprop for the 2-layer net)."""
    x = feats / FEATURE_SCALE
    n = len(labels)
    h, z = forward_float(p, feats)
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    pr = e / e.sum(axis=1, keepdims=True)
    loss = float(-np.log(pr[np.arange(n), labels] + 1e-12).mean())
    dz = pr.copy()
    dz[np.arange(n), labels] -= 1.0
    dz /= n
    g = {"w2": dz.T @ h, "b2": dz.sum(0)}
    dh = (dz @ p["w2"]) * (h > 0)
    g["w1"] = dh.T @ x
    g["b1"] = dh.sum(0)
    for k in ("w1", "w2"):
        g[k] = g[k] + WEIGHT_DECAY * p[k]
    return loss, g


def accuracy(p: Params, feats: np.ndarray, labels: np.ndarray) -> float:
    """Float-model accuracy (fraction correct) on a split."""
    return float((forward_float(p, feats)[1].argmax(1) == labels).mean())


def train(seed: int = SEED, epochs: int = EPOCHS) -> Tuple[Params, List[Tuple[int, float, float]]]:
    """Train and return (best-on-validation params, log of (epoch, train loss, val acc))."""
    sp = load_splits()
    tr, va = sp["train"], sp["val"]
    p = init_params(seed)
    m = {k: np.zeros_like(v) for k, v in p.items()}
    v2 = {k: np.zeros_like(v) for k, v in p.items()}
    best, best_acc, log = {k: v.copy() for k, v in p.items()}, -1.0, []
    for t in range(1, epochs + 1):
        loss, g = _loss_and_grads(p, tr.features, tr.labels)
        for k in p:                                   # Adam update
            m[k] = 0.9 * m[k] + 0.1 * g[k]
            v2[k] = 0.999 * v2[k] + 0.001 * g[k] ** 2
            p[k] = p[k] - LR * (m[k] / (1 - 0.9 ** t)) / (np.sqrt(v2[k] / (1 - 0.999 ** t)) + 1e-8)
        if t % 25 == 0 or t == epochs:
            acc = accuracy(p, va.features, va.labels)
            log.append((t, loss, acc))
            if acc > best_acc:
                best_acc, best = acc, {k: v.copy() for k, v in p.items()}
    return best, log


def save(p: Params, path: str = OUT_PATH) -> None:
    """Write float weights as JSON (lists of floats, exact round trip)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump({k: v.tolist() for k, v in p.items()}, fh, indent=1)


def load(path: str = OUT_PATH) -> Params:
    """Read float weights written by ``save``."""
    with open(path) as fh:
        return {k: np.array(v, dtype=np.float64) for k, v in json.load(fh).items()}


def main() -> None:
    """Train, print the log, save the weights."""
    p, log = train()
    sp = load_splits()
    for ep, loss, acc in log[::10] + log[-1:]:
        print(f"epoch {ep:5d} train_loss {loss:.4f} val_acc {acc:.4f}")
    print(f"float accuracy (simulated, synthetic data): "
          f"train {accuracy(p, sp['train'].features, sp['train'].labels):.4f} "
          f"val {accuracy(p, sp['val'].features, sp['val'].labels):.4f}")
    save(p)
    print(f"seed={SEED} saved {OUT_PATH}")


if __name__ == "__main__":
    main()
