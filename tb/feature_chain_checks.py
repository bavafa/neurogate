"""cocotb checks for the net_clk chain eth_parser -> l3l4_parser -> feature_builder
on generated traffic (top = tb/feature_chain_tb.sv). Started by
tb/test_feature_builder.py, which writes the frame files first (Scapy runs in
.venv; this module runs in the simulation Python and does not need Scapy).

Every async-FIFO entry is compared with model/features.py run_stream, the
source of truth, bit for bit: kind, pkt_id, pkt_len, ovf_cnt and all 8
features. The log line gives the number of entries compared and mismatches.
"""
import os
import random
import sys
from typing import List, Tuple

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ReadOnly, RisingEdge

TB_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(TB_DIR, ".."))
sys.path.insert(0, TB_DIR)
from model import features as F  # noqa: E402
from fb_common import EntrySink, check_entries, expected_entries  # noqa: E402

SEED = int(os.environ.get("SEED", "12345"))
CLK_PERIOD_NS = 6.4
BYTES_PER_BEAT = 8
SEND_STALL_LIMIT = 2000


def read_frames(path: str) -> List[Tuple[bytes, bool]]:
    """Read the '<tuser> <hex>' file written by test_feature_builder.main()."""
    out = []
    with open(path) as f:
        for line in f:
            t, h = line.split()
            out.append((bytes.fromhex(h), t == "1"))
    return out


async def send_frame(dut, rng: random.Random, frame: bytes, tuser: bool, gap_prob: float) -> None:
    """Drive one frame as 8-byte beats with random idle gaps. Unused lanes carry
    garbage and tuser carries noise outside the last beat (both must be ignored)."""
    nbeats = (len(frame) + BYTES_PER_BEAT - 1) // BYTES_PER_BEAT
    for b in range(nbeats):
        while gap_prob and rng.random() < gap_prob:
            dut.s_tvalid.value = 0
            await RisingEdge(dut.clk)
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
            assert stuck < SEND_STALL_LIMIT, "deadlock: s_tready low while sending"
    dut.s_tvalid.value = 0


async def run_dataset(dut, path_env: str, name: str, ready_prob: float) -> dict:
    """Reset, send every frame of one generated dataset, compare with run_stream."""
    path = os.environ.get(path_env, "")
    assert path and os.path.exists(path), f"frame file missing: '{path}'"
    frames = read_frames(path)
    rng = random.Random(f"{SEED}-{name}")
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
    for sig in (dut.s_tvalid, dut.s_tdata, dut.s_tkeep, dut.s_tlast, dut.s_tuser):
        sig.value = 0
    sink = EntrySink(dut, rng, ready_prob)
    dut.rst_n.value = 0
    task = cocotb.start_soon(sink.run())
    for _ in range(4):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    for frame, tuser in frames:
        await send_frame(dut, rng, frame, tuser, 0.05)
    sink.set_ready_prob(1.0)
    for _ in range(20):
        await RisingEdge(dut.clk)
    task.cancel()
    await RisingEdge(dut.clk)
    exp = expected_entries(F.run_stream(frames))
    errs, stats = check_entries(sink.entries, exp, name, allow_loss=ready_prob < 1.0)
    errs = sink.errors + errs
    dut._log.info(f"{name} (simulated): seed={SEED} frames={len(frames)} entries compared="
                  f"{stats['compared']} mismatches={stats['mismatches']} classify="
                  f"{stats['classify']} drop={stats['frame_entries'] - stats['classify']} "
                  f"lost={stats.get('lost', 0)} ovf_only={stats['ovf_only']} errors={len(errs)}")
    assert not errs, "\n".join(errs[:20])
    return stats


@cocotb.test(timeout_time=200, timeout_unit="ms")
async def test_generated_default_dataset(dut) -> None:
    """All 9000 frames of the default generated dataset (traffic_gen seed 1), FIFO never full."""
    await run_dataset(dut, "FB_FRAMES_DEFAULT", "generated_default", 1.0)


@cocotb.test(timeout_time=50, timeout_unit="ms")
async def test_generated_seed_dataset(dut) -> None:
    """The SEED dataset (600 frames), FIFO never full."""
    await run_dataset(dut, "FB_FRAMES_SEED", "generated_seed", 1.0)


@cocotb.test(timeout_time=50, timeout_unit="ms")
async def test_generated_seed_dataset_fifo_full(dut) -> None:
    """The SEED dataset with the FIFO full on 70% of cycles: written entries equal
    the model, and every lost frame is counted once in ovf_cnt."""
    stats = await run_dataset(dut, "FB_FRAMES_SEED", "generated_seed_fifo_full", 0.3)
    assert stats["lost"] > 0, "no frame was lost; the overflow path was not exercised"
