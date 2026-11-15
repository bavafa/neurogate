"""cocotb checks for the whole datapath eth_parser -> l3l4_parser -> feature_builder
-> mlp_core -> decision_unit on generated traffic (top = tb/decision_chain_tb.sv).
Started by tb/test_decision_unit.py, which writes the frame file first (Scapy runs
in .venv; this module runs in the simulation Python and needs no Scapy).

Every final result is compared with the Python models run on the same frames:
model/features.py run_stream gives kind, pkt_id, pkt_len and the 8 features;
model/golden.py infer gives class, confidence and low_conf (the source of truth).
The log line gives the number of vectors compared and the mismatches.

The next frame is sent only after the previous result is out (no async FIFO in
this wrapper, see tb/decision_chain_tb.sv), so nothing may be lost: ovf_cnt must
be 0 on every result.
"""
import os
import random
import sys
from typing import List, Tuple

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ReadOnly, RisingEdge

TB_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(TB_DIR, ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, TB_DIR)
from model import features as F  # noqa: E402
from model import golden  # noqa: E402
from handshake_monitor import monitor_handshake  # noqa: E402

SEED = int(os.environ.get("SEED", "12345"))
WEIGHTS = os.path.join(ROOT, "data", "model", "weights.json")
THRESH_FILE = os.path.join(ROOT, "data", "model", "conf_thresh.txt")
BYTES_PER_BEAT = 8


def read_frames(path: str) -> List[Tuple[bytes, bool]]:
    """Read the '<tuser> <hex>' file written by test_decision_unit.main()."""
    out = []
    with open(path) as f:
        for line in f:
            t, h = line.split()
            out.append((bytes.fromhex(h), t == "1"))
    return out


async def send_frame(dut, rng: random.Random, frame: bytes, tuser: bool) -> None:
    """Drive one frame as 8-byte beats. Unused lanes carry garbage and tuser carries
    noise outside the last beat (both must be ignored)."""
    nbeats = (len(frame) + BYTES_PER_BEAT - 1) // BYTES_PER_BEAT
    for b in range(nbeats):
        chunk = frame[b * BYTES_PER_BEAT:(b + 1) * BYTES_PER_BEAT]
        last = b == nbeats - 1
        pad = bytes(rng.randrange(256) for _ in range(BYTES_PER_BEAT - len(chunk)))
        dut.s_tdata.value = int.from_bytes(chunk + pad, "little")
        dut.s_tkeep.value = (1 << len(chunk)) - 1
        dut.s_tlast.value = int(last)
        dut.s_tuser.value = int(tuser) if last else rng.randrange(2)
        dut.s_tvalid.value = 1
        stuck = 0
        while True:
            await ReadOnly()
            fire = int(dut.s_tready.value) == 1
            await RisingEdge(dut.clk)
            if fire:
                break
            stuck += 1
            assert stuck < 2000, "deadlock: s_tready low while sending"
    dut.s_tvalid.value = 0


async def run_dataset(dut, path_env: str, name: str, limit: int, stall_prob: float) -> None:
    """Reset, send frames one at a time, compare every result with the models."""
    path = os.environ.get(path_env, "")
    assert path and os.path.exists(path), f"frame file missing: '{path}'"
    frames = read_frames(path)[:limit] if limit else read_frames(path)
    rng = random.Random(f"{SEED}-{name}")
    p = golden.load_params(WEIGHTS)
    with open(THRESH_FILE) as fh:
        thresh = int(fh.read().split()[0])
    cocotb.start_soon(Clock(dut.clk, 5.0, unit="ns").start())
    for sig in (dut.s_tvalid, dut.s_tdata, dut.s_tkeep, dut.s_tlast, dut.s_tuser):
        sig.value = 0
    dut.conf_thresh.value = thresh
    dut.m_ready.value = 1
    dut.rst_n.value = 0
    for _ in range(4):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1

    got: List[tuple] = []
    errors: List[str] = []
    sigs = {"kind": dut.m_kind, "len": dut.m_pkt_len, "id": dut.m_pkt_id, "ovf": dut.m_ovf_cnt,
            "cls": dut.m_class, "conf": dut.m_conf, "low": dut.m_low_conf}

    def on_beat(v: dict) -> None:
        got.append((v["kind"], v["len"], v["id"], v["ovf"], v["cls"], v["conf"], v["low"]))

    async def ready_driver() -> None:
        while True:
            await RisingEdge(dut.clk)
            dut.m_ready.value = 0 if rng.random() < stall_prob else 1

    tasks = [cocotb.start_soon(ready_driver()),
             cocotb.start_soon(monitor_handshake(dut.clk, dut.rst_n, dut.m_valid, dut.m_ready,
                                                 sigs, on_beat, errors, "chain result"))]
    for n, (frame, tuser) in enumerate(frames):
        await send_frame(dut, rng, frame, tuser)
        for _ in range(100000):               # wait for this frame's result
            if len(got) > n:
                break
            await RisingEdge(dut.clk)
        assert len(got) > n, f"frame {n}: no result"
    for _ in range(10):
        await RisingEdge(dut.clk)
    for t in tasks:
        t.cancel()
    await RisingEdge(dut.clk)

    recs = F.run_stream(frames)
    mism, classify, low_n, bad_examples = 0, 0, 0, []
    assert len(got) == len(recs), f"{len(got)} results for {len(recs)} frames"
    for r, g in zip(recs, got):
        if r.kind == 0:                       # KIND_CLASSIFY
            cls, conf, low, _, _ = golden.infer(list(r.features), p, thresh)
            want = (0, r.pkt_len, r.pkt_id, 0, cls, conf, int(low))
            classify += 1
            low_n += int(low)
        else:
            want = (r.kind, r.pkt_len, r.pkt_id, 0, 0, 0, 0)
        if g != want:
            mism += 1
            if len(bad_examples) < 5:
                bad_examples.append(f"pkt_id {r.pkt_id}: got {g} expected {want}")
    dut._log.info(f"{name} (simulated): seed={SEED} frames={len(frames)} vectors compared="
                  f"{len(recs)} (classified {classify}, dropped {len(recs) - classify}, "
                  f"low_conf {low_n}) mismatches={mism} protocol errors={len(errors)} "
                  f"conf_thresh={thresh}")
    assert not errors, "\n".join(errors[:10])
    assert not mism, "\n".join(bad_examples)


@cocotb.test(timeout_time=900, timeout_unit="ms")
async def test_chain_default_dataset(dut) -> None:
    """The default generated dataset (traffic_gen seed 1), every frame, output always ready."""
    await run_dataset(dut, "DC_FRAMES_DEFAULT", "chain_default", 0, 0.0)


@cocotb.test(timeout_time=300, timeout_unit="ms")
async def test_chain_seed_dataset_stalled(dut) -> None:
    """The SEED dataset (600 frames) with the final result stalled 50% of the cycles."""
    await run_dataset(dut, "DC_FRAMES_SEED", "chain_seed_stalled", 0, 0.5)
