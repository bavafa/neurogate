"""Evaluate float and int8 nets and pick the default CONF_THRESH. Run: ``python -m model.evaluate``

Order of work (SPEC sections 7 and 9, D-002, D-008):
  1. Run the int8 golden model on the VALIDATION split (carved from training data).
  2. Bin the confidence margin there, and pick the default ``CONF_THRESH`` by one
     fixed rule: the smallest threshold for which packets with confidence >=
     threshold are at least ``TARGET_KEPT_ACC`` correct on validation.
  3. Report accuracy, per-class precision/recall and the confusion matrix on the
     unseen TEST split, for the float net and the int8 net, and the share
     correct per margin bin, ONCE, with no further tuning.
Writes ``docs/results/metrics.json``, ``docs/results/results.md``,
``docs/results/margin_vs_accuracy.png`` and ``data/model/conf_thresh.txt``.

Every number is simulated on SYNTHETIC traffic. Synthetic data is easier than
real traffic; this is not real-world accuracy. Confidence is a logit margin,
not a probability.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
from typing import Dict, List

import numpy as np

from model import golden
from model import export_vectors
from model import train as train_mod
from model.dataset import SHUFFLE_SEED, TEST_SEED, TRAIN_SEED, Split, load_splits
from model.train import forward_float, load as load_float

RESULTS_DIR = os.path.join("docs", "results")
THRESH_PATH = os.path.join("data", "model", "conf_thresh.txt")
CLASS_NAMES = ("normal", "port_scan", "syn_flood")
TARGET_KEPT_ACC = 0.98     # rule for the default CONF_THRESH (validation split only)
BIN_WIDTH = 16             # 8 bins over confidence 0..127 (last bin includes 127)
N_BINS = 8


def provenance() -> dict:
    """Where the numbers came from (SPEC section 10): commit, seeds, tool versions.

    ``commit`` is the git commit the code was at. ``code_modified`` is true if
    tracked files outside ``docs/results/`` and ``data/model/`` (the files this
    script and training write) differ from that commit, so a clean value means
    the commit alone reproduces the result.
    """
    def git(*args: str) -> str:
        """Run git, return stripped stdout ('unknown' if git is unavailable)."""
        try:
            return subprocess.run(["git", *args], capture_output=True, text=True,
                                  check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return "unknown"
    import scapy
    dirty = git("status", "--porcelain", "--untracked-files=no", "--", ".",
                ":!docs/results", ":!data/model")
    return {"commit": git("rev-parse", "HEAD"), "code_modified": bool(dirty and dirty != "unknown"),
            "seeds": {"train_init": train_mod.SEED, "train_generator_master": TRAIN_SEED,
                      "test_generator_master": TEST_SEED, "scenario_shuffle": SHUFFLE_SEED,
                      "vector_export": export_vectors.SEED},
            "tools": {"python": platform.python_version(), "numpy": np.__version__,
                      "scapy": scapy.__version__}}


def run_int8(split: Split, p: golden.QParams) -> Dict[str, np.ndarray]:
    """Golden-model class and confidence for every classified frame of a split."""
    r = [golden.infer(x.tolist(), p) for x in split.features]
    return {"cls": np.array([a[0] for a in r]), "conf": np.array([a[1] for a in r])}


def confusion(labels: np.ndarray, pred: np.ndarray) -> np.ndarray:
    """3x3 matrix, rows = true class, columns = predicted class."""
    m = np.zeros((3, 3), dtype=np.int64)
    np.add.at(m, (labels, pred), 1)
    return m


def metrics(labels: np.ndarray, pred: np.ndarray) -> dict:
    """Accuracy, per-class precision/recall and the confusion matrix."""
    cm = confusion(labels, pred)
    prec = [float(cm[k, k] / cm[:, k].sum()) if cm[:, k].sum() else 0.0 for k in range(3)]
    rec = [float(cm[k, k] / cm[k, :].sum()) if cm[k, :].sum() else 0.0 for k in range(3)]
    return {"accuracy": float((labels == pred).mean()), "precision": prec, "recall": rec,
            "confusion": cm.tolist(), "n": int(len(labels))}


def pick_threshold(conf: np.ndarray, ok: np.ndarray) -> int:
    """Smallest T in 0..127 with kept-packet (confidence >= T) accuracy >= target."""
    for t in range(128):
        kept = conf >= t
        if kept.any() and ok[kept].mean() >= TARGET_KEPT_ACC:
            return t
    return 127


def bin_table(conf: np.ndarray, ok: np.ndarray) -> List[dict]:
    """Per confidence bin: packet count and share correct."""
    rows = []
    for b in range(N_BINS):
        lo, hi = b * BIN_WIDTH, (b + 1) * BIN_WIDTH - 1
        m = (conf >= lo) & (conf <= hi)
        rows.append({"lo": lo, "hi": hi, "n": int(m.sum()),
                     "pct_correct": float(100 * ok[m].mean()) if m.any() else None})
    return rows


def _plot(test_rows: List[dict], thresh: int, path: str) -> None:
    """Bar chart: share correct per margin bin on the test split."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(7, 3.8), dpi=150)
    xs = [f"{r['lo']}-{r['hi']}" for r in test_rows]
    ys = [r["pct_correct"] or 0 for r in test_rows]
    bars = ax.bar(xs, ys, color="#2a6f97")
    for bar, r in zip(bars, test_rows):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1,
                f"{r['pct_correct']:.1f}%\nn={r['n']}" if r["n"] else "n=0",
                ha="center", va="bottom", fontsize=7)
    ax.axvline(thresh / BIN_WIDTH - 0.5, color="#c1121f", ls="--", lw=1)
    ax.text(thresh / BIN_WIDTH - 0.45, 40, f"default CONF_THRESH = {thresh}\n(chosen on validation)",
            color="#c1121f", fontsize=7)
    ax.set_ylim(0, 115)
    ax.set_xlabel("confidence (logit margin, 0..127; not a probability)")
    ax.set_ylabel("% of packets classified correctly")
    ax.set_title("Margin vs. measured accuracy, unseen synthetic test split (simulated)", fontsize=9)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(path)


def _cm_md(cm: List[List[int]]) -> str:
    """Markdown confusion matrix."""
    head = "| true \\ predicted | " + " | ".join(CLASS_NAMES) + " |\n|---|---|---|---|\n"
    return head + "".join(f"| {CLASS_NAMES[i]} | " + " | ".join(str(v) for v in row) + " |\n"
                          for i, row in enumerate(cm))


def _metrics_md(title: str, m: dict) -> str:
    """Markdown block for one metrics dict."""
    s = f"### {title}\n\nAccuracy: **{100 * m['accuracy']:.2f}%** on {m['n']} classified packets.\n\n"
    s += "| class | precision | recall |\n|---|---|---|\n"
    s += "".join(f"| {CLASS_NAMES[k]} | {100 * m['precision'][k]:.2f}% | {100 * m['recall'][k]:.2f}% |\n"
              for k in range(3))
    return s + "\n" + _cm_md(m["confusion"]) + "\n"


def _bins_md(rows: List[dict]) -> str:
    """Markdown margin-vs-accuracy table."""
    s = "| confidence bin | packets | % correct |\n|---|---|---|\n"
    return s + "".join(f"| {r['lo']}-{r['hi']} | {r['n']} | "
                       f"{'-' if r['pct_correct'] is None else format(r['pct_correct'], '.1f') + '%'} |\n"
                       for r in rows)


def main() -> None:
    """Run the full evaluation and write the result files."""
    sp = load_splits()
    q, fl = golden.load_params(), load_float()
    # Step 1-2: validation split only.
    val = sp["val"]
    v = run_int8(val, q)
    v_ok = v["cls"] == val.labels
    thresh = pick_threshold(v["conf"], v_ok)
    val_rows = bin_table(v["conf"], v_ok)
    # Step 3: test split, once.
    te = sp["test"]
    t = run_int8(te, q)
    t_ok = t["cls"] == te.labels
    t_float = forward_float(fl, te.features)[1].argmax(1)
    m_int8, m_float = metrics(te.labels, t["cls"]), metrics(te.labels, t_float)
    test_rows = bin_table(t["conf"], t_ok)
    kept = t["conf"] >= thresh
    out = {"label": "simulated, synthetic data", "provenance": provenance(),
           "default_conf_thresh": thresh,
           "threshold_rule": f"smallest threshold with validation accuracy of kept packets >= {TARGET_KEPT_ACC}",
           "test": {"float": m_float, "int8": m_int8, "int8_vs_float_agreement":
                    float((t["cls"] == t_float).mean()),
                    "frames_total": te.frames_total, "frames_dropped_by_parser": te.frames_dropped,
                    "margin_bins": test_rows,
                    "flagged_low_conf_share": float(1 - kept.mean()),
                    "kept_accuracy": float(t_ok[kept].mean()),
                    "flagged_accuracy": float(t_ok[~kept].mean()) if (~kept).any() else None},
           "validation": {"int8_accuracy": float(v_ok.mean()), "n": int(len(v_ok)), "margin_bins": val_rows},
           "model": {"shift1": q.shift1, "conf_shift": q.conf_shift}}
    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(os.path.join(RESULTS_DIR, "metrics.json"), "w") as fh:
        json.dump(out, fh, indent=1)
        fh.write("\n")
    with open(THRESH_PATH, "w") as fh:
        fh.write(f"{thresh}\n")
    _plot(test_rows, thresh, os.path.join(RESULTS_DIR, "margin_vs_accuracy.png"))
    drop = 100 * (m_float["accuracy"] - m_int8["accuracy"])
    md = f"""# M3 results: neural net accuracy

All numbers are **simulated** (Python models) on **synthetic** traffic from the project's own
generator. Synthetic data is easier than real traffic, so this is not real-world accuracy.
The test split uses scenario seeds that training never saw. Regenerate with
`python -m model.train && python -m model.quantize && python -m model.evaluate`.

The test split has {te.frames_total} frames; the parser drops {te.frames_dropped} of them (malformed or
unsupported), so {m_int8['n']} reach the neural net. Dropped frames are not in the tables below.

{_metrics_md('int8 (the chip arithmetic, golden model), test split', m_int8)}
{_metrics_md('float (training model, before quantization), test split', m_float)}
Float to int8 accuracy change on the test split: **{drop:+.2f} points** (float minus int8).
The two nets give the same class on {100 * out['test']['int8_vs_float_agreement']:.2f}% of test packets.

## Margin vs. measured accuracy

Confidence is a logit margin (top output minus second output, shifted down by {q.conf_shift} bits and capped
at 127), not a probability. The default `CONF_THRESH` of **{thresh}** was chosen on the validation split
(carved from training data) with one rule: the smallest threshold for which packets at or above it are at
least {100 * TARGET_KEPT_ACC:.0f}% correct. Validation table used for the choice
(int8, {len(v_ok)} packets, accuracy {100 * v_ok.mean():.2f}%):

{_bins_md(val_rows)}
The same table on the unseen test split, reported once with no further tuning:

{_bins_md(test_rows)}
![Share correct per margin bin on the test split](margin_vs_accuracy.png)

With `CONF_THRESH` = {thresh} on the test split: {100 * (1 - kept.mean()):.1f}% of packets are flagged low confidence;
the unflagged packets are {100 * out['test']['kept_accuracy']:.2f}% correct and the flagged ones
{100 * (out['test']['flagged_accuracy'] or 0):.2f}% correct (all simulated, synthetic data).
"""
    with open(os.path.join(RESULTS_DIR, "results.md"), "w") as fh:
        fh.write(md)
    print(md)


if __name__ == "__main__":
    main()
