"""Python tests for the golden model, quantization, data splits and exported vectors (M3).

All pytest, no RTL. Run: ``python3 tb/test_golden.py`` (also run by ``make test``).
The golden model (model/golden.py) is plain Python integers. To avoid checking
it against itself, the main checks compare it with an independent NumPy int64
implementation written here, and with a float version of the same network.
"""
import csv
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from model import dataset, evaluate, export_vectors, golden, train  # noqa: E402
from model.golden import QParams  # noqa: E402

SEED = int(os.environ.get("SEED", "12345"))
print(f"test_golden: SEED={SEED}")


@pytest.fixture(scope="module")
def params() -> QParams:
    """The committed quantized weights."""
    return golden.load_params()


@pytest.fixture(scope="module")
def test_feats() -> np.ndarray:
    """Every classified packet of the unseen test split (features, N x 8)."""
    return dataset.load_splits()["test"].features


def _np_forward(p: QParams, xs: np.ndarray):
    """Independent vectorised int64 inference: returns (hidden, logits, class, confidence)."""
    acc = xs @ np.array(p.w1, dtype=np.int64).T + np.array(p.b1, dtype=np.int64)
    pre = (acc + (1 << (p.shift1 - 1))) >> p.shift1 if p.shift1 else acc
    h = np.clip(pre, 0, 127)
    z = h @ np.array(p.w2, dtype=np.int64).T + np.array(p.b2, dtype=np.int64)
    cls = z.argmax(axis=1)                      # numpy argmax: first max wins = lowest index
    zs = np.sort(z, axis=1)
    conf = np.minimum((zs[:, -1] - zs[:, -2]) >> p.conf_shift, 127)
    return h, z, cls, conf


def test_golden_equals_numpy_on_all_test_vectors(params, test_feats):
    """Golden == independent int64 NumPy model on every test-split vector (class, confidence, logits, hidden)."""
    h, z, cls, conf = _np_forward(params, test_feats)
    for i, x in enumerate(test_feats.tolist()):
        c, cf, _, gh, gz = golden.infer(x, params)
        assert gh == h[i].tolist() and gz == z[i].tolist(), f"row {i}"
        assert (c, cf) == (cls[i], conf[i]), f"row {i}"
    assert len(test_feats) > 8000


def test_golden_vs_quantized_float(params, test_feats):
    """Same int8 weights in float math (no rounding): logit error stays inside the rounding bound; class agrees."""
    w1, w2 = np.array(params.w1, float), np.array(params.w2, float)
    acc = test_feats @ w1.T + np.array(params.b1, float)
    h_f = np.clip(acc / 2 ** params.shift1, 0, 127)          # no rounding of the hidden layer
    z_f = h_f @ w2.T + np.array(params.b2, float)
    _, z, cls, _ = _np_forward(params, test_feats)
    # rounding moves each hidden value by <= 0.5, and clip is 1-Lipschitz, so
    # each logit moves by at most 0.5 * sum|w2| (+ float noise).
    bound = 0.5 * np.abs(w2).sum(axis=1) + 1e-6
    assert (np.abs(z - z_f) <= bound).all()
    agree = (cls == z_f.argmax(axis=1)).mean()
    print(f"golden vs quantized float: class agreement {agree:.5f} on {len(cls)} vectors")
    assert agree >= 0.995


def test_golden_vs_trained_float_net(params, test_feats):
    """The int8 net and the float net it came from agree on at least 99% of test packets."""
    fl = train.load()
    f_cls = train.forward_float(fl, test_feats)[1].argmax(1)
    g_cls = _np_forward(params, test_feats)[2]
    agree = (f_cls == g_cls).mean()
    print(f"golden vs trained float: class agreement {agree:.5f}")
    assert agree >= 0.99


def _tiny(w1_val=0, b1=0, w2_val=0, b2=(0, 0, 0), shift1=0, conf_shift=0) -> QParams:
    """A uniform-weight net for corner-case tests."""
    return QParams(tuple((w1_val,) * 8 for _ in range(16)), (b1,) * 16,
                   tuple((w2_val,) * 16 for _ in range(3)), tuple(b2), shift1, conf_shift)


def test_extreme_weights_fit_int32_and_saturate():
    """Worst-case weights and inputs: accumulators stay inside int32 and hidden units cap at 127."""
    p = _tiny(w1_val=127, b1=2 ** 31 - 1 - 8 * 127 * 127, w2_val=127, shift1=0)
    x = [127] * 8
    acc = p.b1[0] + 8 * 127 * 127
    assert acc == 2 ** 31 - 1                                   # exactly the int32 maximum
    h = golden.hidden(x, p)
    assert h == [127] * 16                                      # saturated
    z = golden.logits(h, p)
    assert z == [16 * 127 * 127] * 3 and max(z) < 2 ** 31
    neg = _tiny(w1_val=-127, b1=-(2 ** 31) + 8 * 127 * 127, shift1=0)
    assert golden.hidden(x, neg) == [0] * 16                    # ReLU at the other end


def test_requantize_round_half_up():
    """Right shift rounds half up: 1.5 -> 2, 2.5 -> 3, 1.49 -> 1; negatives go to 0 via ReLU."""
    def h_of(acc: int, s: int) -> int:
        return golden.hidden([0] * 8, _tiny(b1=acc, shift1=s))[0]
    assert h_of(6, 2) == 2          # 1.5 -> 2
    assert h_of(10, 2) == 3         # 2.5 -> 3
    assert h_of(5, 2) == 1          # 1.25 -> 1
    assert h_of(2, 2) == 1          # 0.5 -> 1 (half rounds up)
    assert h_of(1, 2) == 0          # 0.25 -> 0
    assert h_of(-2, 2) == 0 and h_of(-100, 2) == 0
    assert h_of(127 * 4 + 1, 2) == 127 and h_of(10 ** 6, 2) == 127   # saturate
    assert h_of(3, 0) == 3          # shift 0: no rounding term


def test_argmax_ties_lowest_index_and_confidence_cap():
    """Ties go to the lowest class; confidence is margin >> shift, capped at 127, never negative."""
    assert golden.decide([5, 5, 5], 0, 0) == (0, 0, False)
    assert golden.decide([1, 5, 5], 0, 0)[0] == 1
    assert golden.decide([1, 2, 5], 0, 0)[:2] == (2, 3)
    assert golden.decide([-7, -3, -9], 0, 0)[:2] == (1, 4)
    assert golden.decide([0, 10 ** 6, 0], 0, 0)[1] == 127
    assert golden.decide([0, 1 << 10, 0], 3, 0)[1] == 127       # (1024 >> 3) = 128 -> cap
    assert golden.decide([0, 1016, 0], 3, 0)[1] == 127          # 1016 >> 3 = 127
    assert golden.decide([0, 1008, 0], 3, 0)[1] == 126


def test_low_conf_threshold_edge():
    """low_conf is confidence < thresh: equal is not low; thresh 0 never flags."""
    assert golden.decide([0, 10, 0], 0, 10)[2] is False
    assert golden.decide([0, 10, 0], 0, 11)[2] is True
    assert golden.decide([0, 0, 0], 0, 0)[2] is False


def test_validate_rejects_bad_params(params):
    """Out-of-range weights, biases, shapes and shifts are refused."""
    bad_w = QParams(((-128,) * 8,) + params.w1[1:], params.b1, params.w2, params.b2, 7, 8)
    with pytest.raises(ValueError):
        golden.validate(bad_w)
    with pytest.raises(ValueError):
        golden.validate(QParams(params.w1, (2 ** 31,) + params.b1[1:], params.w2, params.b2, 7, 8))
    with pytest.raises(ValueError):
        golden.validate(QParams(params.w1[:15], params.b1, params.w2, params.b2, 7, 8))
    with pytest.raises(ValueError):
        golden.validate(QParams(params.w1, params.b1, params.w2, params.b2, 31, 8))
    with pytest.raises(ValueError):
        golden.hidden([128] + [0] * 7, params)                  # feature out of range


def test_validate_bias_keeps_accumulators_inside_int32(params):
    """A bias closer to the int32 limits than the layer's largest weighted sum is
    refused (the hardware accumulator is int32 and must never wrap); the exact
    limits are accepted."""
    lim1 = 2 ** 31 - 1 - golden.MAX_DOT1
    lim2 = 2 ** 31 - 1 - golden.MAX_DOT2
    ok = [QParams(params.w1, (lim1,) + params.b1[1:], params.w2, params.b2, 7, 8),
          QParams(params.w1, (-(2 ** 31) + golden.MAX_DOT1,) + params.b1[1:], params.w2, params.b2, 7, 8),
          QParams(params.w1, params.b1, params.w2, (lim2,) + params.b2[1:], 7, 8),
          QParams(params.w1, params.b1, params.w2, (-(2 ** 31) + golden.MAX_DOT2,) + params.b2[1:], 7, 8)]
    for p in ok:
        golden.validate(p)
    bad = [QParams(params.w1, (lim1 + 1,) + params.b1[1:], params.w2, params.b2, 7, 8),
           QParams(params.w1, params.b1, params.w2, (lim2 + 1,) + params.b2[1:], 7, 8),
           QParams(params.w1, params.b1, params.w2, (-(2 ** 31) + golden.MAX_DOT2 - 1,) + params.b2[1:], 7, 8)]
    for p in bad:
        with pytest.raises(ValueError):
            golden.validate(p)


def _read_hex(name: str, digits: int):
    """Parse a ROM hex file back into signed ints."""
    out = []
    with open(os.path.join("data", "model", name)) as fh:
        for line in fh:
            line = line.strip()
            assert len(line) == digits
            v = int(line, 16)
            out.append(v - (1 << (4 * digits)) if v >= 1 << (4 * digits - 1) else v)
    return out


def test_rom_hex_files_match_weights_json(params):
    """The four .hex ROM files decode to exactly the parameters in weights.json, in the documented address order."""
    assert _read_hex("rom_w1.hex", 2) == [w for r in params.w1 for w in r]
    assert _read_hex("rom_b1.hex", 8) == list(params.b1)
    assert _read_hex("rom_w2.hex", 2) == [w for r in params.w2 for w in r]
    assert _read_hex("rom_b2.hex", 8) == list(params.b2)


def test_exported_vectors_replay_through_golden(params):
    """Every row of data/vectors/mlp_vectors.csv equals a fresh golden run; corner rows include ties and saturation."""
    with open(export_vectors.OUT_PATH) as fh:
        rows = list(csv.DictReader(fh))
    with open(os.path.join("data", "model", "conf_thresh.txt")) as fh:
        thresh = int(fh.read())
    assert len(rows) > 1500
    kinds = {r["kind"] for r in rows}
    assert kinds == {"directed", "corner", "random", "test"}
    saw_tie = saw_sat = False
    for r in rows:
        x = [int(r[f"f{i}"]) for i in range(8)]
        c, cf, low, h, z = golden.infer(x, params, thresh)
        assert [int(r[f"z{k}"]) for k in range(3)] == z
        assert (int(r["class"]), int(r["confidence"]), int(r["low_conf"])) == (c, cf, int(low))
        saw_tie |= len(set(z)) < 3 and sorted(z)[-1] == sorted(z)[-2]
        saw_sat |= 127 in h
    assert saw_tie and saw_sat


def test_exported_vectors_are_reproducible(params):
    """Building the vector rows twice gives the same rows (fixed seed)."""
    a = export_vectors.build_rows(params, 13)
    b = export_vectors.build_rows(params, 13)
    assert a == b


def test_training_is_reproducible():
    """Same seed, same weights, bit for bit (short run)."""
    p1, _ = train.train(seed=train.SEED, epochs=25)
    p2, _ = train.train(seed=train.SEED, epochs=25)
    assert all(np.array_equal(p1[k], p2[k]) for k in p1)


def test_splits_are_disjoint_by_scenario_seed():
    """Train/validation scenarios come from the training pool; test scenarios from a different master seed."""
    assert dataset.TEST_SEED != dataset.TRAIN_SEED
    pool_seeds = {dataset.TRAIN_SEED * 100003 + i for i in range(3 * dataset.TRAIN_POOL_PER_CLASS)}
    test_seeds = {dataset.TEST_SEED * 100003 + i for i in range(3 * dataset.TEST_PER_CLASS)}
    assert not pool_seeds & test_seeds
    assert dataset.TRAIN_PER_CLASS < dataset.TRAIN_POOL_PER_CLASS      # validation is non-empty
    sp = dataset.load_splits()
    assert all(len(s.features) > 1000 for s in sp.values())
    assert all(s.frames_dropped > 0 for s in sp.values())              # bad frames exist and are excluded
    assert all(s.features.min() >= 0 and s.features.max() <= 127 for s in sp.values())


def test_train_and_validation_scenarios_are_disjoint():
    """No scenario is in both the training and the validation split.

    Checked directly on the scenario ids (generator numbers them class by class),
    then on the packet streams: the generator must give each scenario its own
    seed, so no two scenarios (and so no two splits) share a packet stream.
    """
    pool = dataset.generate(dataset.TRAIN_SEED, dataset.TRAIN_POOL_PER_CLASS, dataset.PACKETS_PER_SCENARIO)
    ids = {r[0] for r in pool}
    train_ids = {i for i in ids if i % dataset.TRAIN_POOL_PER_CLASS < dataset.TRAIN_PER_CLASS}
    val_ids = {i for i in ids if i % dataset.TRAIN_POOL_PER_CLASS >= dataset.TRAIN_PER_CLASS}
    assert train_ids and val_ids
    assert not train_ids & val_ids
    assert train_ids | val_ids == ids                                  # nothing lost, nothing used twice
    assert len(train_ids) == 3 * dataset.TRAIN_PER_CLASS
    assert len(val_ids) == 3 * (dataset.TRAIN_POOL_PER_CLASS - dataset.TRAIN_PER_CLASS)
    frames = {i: [f.data for s, _, f in pool if s == i] for i in ids}
    first = {i: frames[i][0] for i in ids}
    # Different scenarios have different first frames (their own seeds), so a
    # train scenario cannot be a copy of a validation scenario.
    assert len(set(first.values())) == len(ids)
    for t in train_ids:
        for v in val_ids:
            assert frames[t] != frames[v]


def test_pick_threshold_and_metrics_helpers():
    """Threshold rule and metrics on a toy example."""
    conf = np.array([0, 1, 2, 10, 20, 30, 40])
    ok = np.array([0, 0, 1, 1, 1, 1, 1], dtype=bool)
    assert evaluate.pick_threshold(conf, ok) == 2              # first T where kept packets are 100% correct
    m = evaluate.metrics(np.array([0, 1, 2, 2]), np.array([0, 1, 2, 0]))
    assert m["accuracy"] == 0.75 and m["confusion"][2] == [1, 0, 1]
    assert m["recall"][2] == 0.5 and m["precision"][0] == 0.5


def test_default_threshold_chosen_on_validation_not_test(params):
    """Re-derive the stored threshold from the validation split alone."""
    val = dataset.load_splits()["val"]
    r = evaluate.run_int8(val, params)
    t = evaluate.pick_threshold(r["conf"], r["cls"] == val.labels)
    with open(os.path.join("data", "model", "conf_thresh.txt")) as fh:
        assert int(fh.read()) == t


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-s", "-p", "no:cacheprovider"]))
