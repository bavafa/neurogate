"""cocotb replay of the FULL unseen test split through mlp_core + decision_unit.

Not a make-test file (it is slower than the rest): run it with `make test-full`
(tb/full_split_run.py), which first exports data/vectors/mlp_vectors_full.csv
with `python -m model.export_vectors --full`.

Every row of that file is one classified packet of the unseen test split (all
8,347, duplicates kept). Each row is sent into tb/mlp_decision_tb.sv as an
entry; the expected logits, class, confidence and low_conf come from
model/golden.py, computed here again (the CSV columns are checked against it
too). Compared (all *simulated*): the three int32 logits and the class at the
mlp_core output, then class, 7-bit confidence and low_conf at the
decision_unit output, plus pkt_id order and metadata. One run back to back
and one with random input gaps (30%) and output stalls (50%).
"""
import csv
import os
import random
import sys

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ReadOnly, RisingEdge

TB_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(TB_DIR, ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, TB_DIR)
from model import golden  # noqa: E402
from handshake_monitor import monitor_handshake  # noqa: E402

SEED = int(os.environ.get("SEED", "12345"))
VECTORS = os.path.join(ROOT, "data", "vectors", "mlp_vectors_full.csv")
THRESH_FILE = os.path.join(ROOT, "data", "model", "conf_thresh.txt")
WEIGHTS = os.path.join(ROOT, "data", "model", "weights.json")


def s32(v: int) -> int:
    """Unsigned 32-bit pattern -> signed int."""
    return v - (1 << 32) if v & (1 << 31) else v


async def replay(dut, rows, p, thresh: int, gap: float, stall: float, name: str) -> None:
    """Send every row, collect both result streams, compare with golden.infer."""
    rng = random.Random(f"{SEED}-{name}")
    dut.s_tvalid.value = 0
    dut.s_tdata.value = 0
    dut.m_ready.value = 1
    dut.conf_thresh.value = thresh
    dut.rst_n.value = 0
    for _ in range(3):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    errors, taps, finals = [], [], []

    def on_final(v: dict) -> None:
        finals.append(v)

    async def tap() -> None:
        """Record the mlp_core -> decision_unit hand-over as the clock edge sees it."""
        prev = None
        while True:
            await RisingEdge(dut.clk)
            await ReadOnly()
            if prev is not None:
                taps.append(prev)
            prev = ((int(dut.t_logit0.value), int(dut.t_logit1.value), int(dut.t_logit2.value),
                     int(dut.t_class.value)) if int(dut.t_valid.value) and int(dut.rst_n.value) else None)

    async def ready() -> None:
        while True:
            await RisingEdge(dut.clk)
            dut.m_ready.value = 0 if rng.random() < stall else 1

    sigs = {"kind": dut.m_kind, "len": dut.m_pkt_len, "id": dut.m_pkt_id, "ovf": dut.m_ovf_cnt,
            "cls": dut.m_class, "conf": dut.m_conf, "low": dut.m_low_conf}
    tasks = [cocotb.start_soon(tap()), cocotb.start_soon(ready()),
             cocotb.start_soon(monitor_handshake(dut.clk, dut.rst_n, dut.m_valid, dut.m_ready,
                                                 sigs, on_final, errors, "final result"))]
    meta = []
    for n, (x, _, _, _) in enumerate(rows):
        while gap and rng.random() < gap:
            dut.s_tvalid.value = 0
            await RisingEdge(dut.clk)
        pkt_len, ovf = rng.randrange(65536), rng.randrange(65536)
        meta.append((pkt_len, n & 0xFFFF, ovf))
        w = 0
        for i, f in enumerate(x):
            w |= f << (8 * i)
        dut.s_tdata.value = w | (pkt_len << 64) | ((n & 0xFFFF) << 80) | (ovf << 96)   # kind 0 = CLASSIFY
        dut.s_tvalid.value = 1
        stuck = 0
        while True:
            await ReadOnly()
            fire = int(dut.s_tready.value) == 1
            await RisingEdge(dut.clk)
            if fire:
                break
            stuck += 1
            assert stuck < 5000, "deadlock: s_tready low too long"
        dut.s_tvalid.value = 0
    for _ in range(2_000_000):
        if len(finals) >= len(rows):
            break
        await RisingEdge(dut.clk)
    for _ in range(3):                                  # nothing extra may come out
        await RisingEdge(dut.clk)
    for t in tasks:
        t.cancel()
    await RisingEdge(dut.clk)

    assert len(finals) == len(rows), f"{len(finals)} final results for {len(rows)} rows"
    mism = {"logits": 0, "class": 0, "conf": 0, "low_conf": 0, "meta": 0, "tap": 0}
    taps = [t for t in taps if t is not None]
    assert len(taps) == len(rows), f"{len(taps)} mlp_core results for {len(rows)} rows"
    for n, ((x, z_csv, cls_csv, conf_csv), fin, tp, (pl, pid, ovf)) in enumerate(
            zip(rows, finals, taps, meta)):
        cls, conf, low, _, z = golden.infer(list(x), p, thresh)
        assert (tuple(z), cls, conf) == (z_csv, cls_csv, conf_csv), f"row {n}: CSV != golden.infer"
        tz = (s32(tp[0]), s32(tp[1]), s32(tp[2]))
        if tz != tuple(z):
            mism["logits"] += 1
        if tp[3] != cls:
            mism["tap"] += 1
        if fin["cls"] != cls:
            mism["class"] += 1
        if fin["conf"] != conf:
            mism["conf"] += 1
        if fin["low"] != int(low):
            mism["low_conf"] += 1
        if (fin["kind"], fin["len"], fin["id"], fin["ovf"]) != (0, pl, pid, ovf):
            mism["meta"] += 1
    total = sum(mism.values())
    dut._log.info(f"FULL SPLIT {name} (simulated): seed={SEED} thresh={thresh} gap={gap:g} stall={stall:g} rows compared="
                  f"{len(rows)} mismatches={total} {mism} handshake errors={len(errors)}")
    assert not errors, "\n".join(errors[:10])
    assert total == 0, f"{total} mismatches {mism}"


def load_rows():
    """Rows of the full CSV as (features, logits, class, confidence)."""
    rows = []
    with open(VECTORS) as fh:
        for r in csv.DictReader(fh):
            rows.append(([int(r[f"f{i}"]) for i in range(8)],
                         (int(r["z0"]), int(r["z1"]), int(r["z2"])), int(r["class"]),
                         int(r["confidence"])))
    return rows


def setup(dut):
    """Start the clock (cocotb stops it at the end of each test, so every test calls this)."""
    cocotb.start_soon(Clock(dut.clk, 5.0, unit="ns").start())


@cocotb.test(timeout_time=1000, timeout_unit="ms")
async def test_full_split_back_to_back(dut) -> None:
    """All classified unseen test packets, no stalls."""
    setup(dut)
    p = golden.load_params(WEIGHTS)
    thresh = int(open(THRESH_FILE).read().strip())
    await replay(dut, load_rows(), p, thresh, 0.0, 0.0, "back_to_back")


@cocotb.test(timeout_time=1000, timeout_unit="ms")
async def test_full_split_backpressure(dut) -> None:
    """The same rows with input gaps (30%) and output stalls (50%)."""
    setup(dut)
    p = golden.load_params(WEIGHTS)
    thresh = int(open(THRESH_FILE).read().strip())
    await replay(dut, load_rows(), p, thresh, 0.3, 0.5, "backpressure")
