"""cocotb smoke test of the whole chip (top = rtl/neurogate_top.sv), two clocks.

Started by tb/test_neurogate_top.py, which writes the frame file first (Scapy
runs in .venv; this module runs in the simulation Python and needs no Scapy).

What it shows (all *simulated*)
    The wiring works end to end: frames go in on net_clk, cross through the
    async FIFO, are classified on core_clk, and come out as result beats; the
    counters are read back over AXI-Lite. Every result beat and every counter
    is compared with the Python models on the same frames:
    model/features.py run_stream (kind, pkt_id, pkt_len, features) and
    model/golden.py infer (class, confidence, low_conf; source of truth).

What it does NOT cover (later M4 session)
    The full generated traffic, input bursts that overflow the async FIFO,
    bad-packet mixes beyond what the generator makes, reset in mid-frame or
    mid-inference. Frames here are paced (a gap after each one) so the async
    FIFO never overflows, and the test asserts PKT_OVERFLOW = 0.

Clocks: net_clk 6.4 ns (156.25 MHz), core_clk 5.0 ns (200 MHz) started at a
random odd number of picoseconds, so the edges of the two clocks never line
up in a fixed pattern. The external reset is released at a random time.
"""
import os
import random
from typing import Dict, List, Tuple

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Timer
from cocotb.utils import get_sim_time

from handshake_monitor import monitor_handshake  # noqa: E402
from top_common import (BYTES_PER_BEAT, COUNTERS, CONF_THRESH, CORE_PS, CTRL, NET_PS, SEED,  # noqa: E402,F401
                        AxiLite, model_counters, model_frames, read_frames, read_thresh,
                        send_frame)

PACE_NET_CYCLES = 300          # gap after each frame: > one inference (~180 core clocks)


def expected(frames: List[Tuple[bytes, bool]], thresh: int) -> Tuple[list, Dict[str, int]]:
    """Model results: the list of result beats (pkt_id, pkt_len, class, conf, low)
    and the counter values after all frames, with no overflow."""
    per_frame = model_frames(frames, thresh)
    beats = [b for b in per_frame if b is not None]
    return beats, model_counters(beats, per_frame.count(None))


@cocotb.test(timeout_time=20, timeout_unit="ms")
async def test_smoke_two_clocks(dut) -> None:
    """Paced frames in; result beats and AXI-Lite counters equal the models; then a
    soft reset over AXI-Lite clears the counters."""
    path = os.environ.get("TOP_FRAMES", "")
    assert path and os.path.exists(path), f"frame file missing: '{path}'"
    frames = read_frames(path)
    rng = random.Random(f"{SEED}-top-smoke")
    thresh = read_thresh()
    want_beats, want_cnt = expected(frames, thresh)

    # Inputs idle, reset asserted from time 0.
    dut.rst_n.value = 0
    for sig in (dut.s_axis_tvalid, dut.s_axis_tdata, dut.s_axis_tkeep, dut.s_axis_tlast,
                dut.s_axis_tuser):
        sig.value = 0
    dut.m_axis_res_tready.value = 1
    bus = AxiLite(dut)
    phase = rng.randrange(1, CORE_PS, 2)               # odd ps: edges never coincide
    cocotb.start_soon(Clock(dut.net_clk, NET_PS, unit="ps").start())
    await Timer(phase, unit="ps")
    cocotb.start_soon(Clock(dut.core_clk, CORE_PS, unit="ps").start())
    await Timer(50_000 + rng.randrange(1, 7_000), unit="ps")   # release at a random time
    dut.rst_n.value = 1
    dut._log.info(f"top smoke (simulated): seed={SEED} core_clk phase={phase} ps, "
                  f"frames={len(frames)} conf_thresh={thresh}")
    for _ in range(10):
        await RisingEdge(dut.core_clk)

    # CONF_THRESH: reset value is 13 (regmap); write the model's threshold anyway
    # so the AXI-Lite write path into decision_unit is exercised.
    v, resp = await bus.read(CONF_THRESH)
    assert (v, resp) == (13, 0), f"CONF_THRESH after reset: {v:#x} resp {resp}"
    assert await bus.write(CONF_THRESH, thresh) == 0
    assert (await bus.read(CONF_THRESH))[0] == thresh

    got: List[tuple] = []
    errors: List[str] = []
    in_ns: Dict[int, float] = {}       # pkt_id -> time the last beat went in
    out_ns: Dict[int, float] = {}      # pkt_id -> time the result beat was taken

    def on_beat(v: Dict[str, int]) -> None:
        t = v["tdata"]
        assert (t >> 3) & 0x1F == 0, f"result bits [7:3] not zero: {t:#x}"
        assert (t >> 15) & 1 == 0, f"confidence above 127: {t:#x}"
        got.append(((t >> 32) & 0xFFFF, (t >> 16) & 0xFFFF, t & 3, (t >> 8) & 0xFF, (t >> 2) & 1))
        out_ns[(t >> 32) & 0xFFFF] = get_sim_time("ns")

    async def ready_driver() -> None:
        while True:
            await RisingEdge(dut.core_clk)
            dut.m_axis_res_tready.value = 0 if rng.random() < 0.3 else 1

    tasks = [cocotb.start_soon(ready_driver()),
             cocotb.start_soon(monitor_handshake(dut.core_clk, dut.core_rst_n,
                                                 dut.m_axis_res_tvalid, dut.m_axis_res_tready,
                                                 {"tdata": dut.m_axis_res_tdata}, on_beat,
                                                 errors, "result stream"))]
    for n, (frame, tuser) in enumerate(frames):
        in_ns[n] = await send_frame(dut, rng, frame, tuser)   # pkt_id = frame number
        for _ in range(PACE_NET_CYCLES):
            await RisingEdge(dut.net_clk)
    for _ in range(20000):                               # drain
        if len(got) >= len(want_beats):
            break
        await RisingEdge(dut.core_clk)
    for _ in range(400):                                 # let any extra beat show up
        await RisingEdge(dut.core_clk)
    tasks[0].cancel()
    dut.m_axis_res_tready.value = 1
    await RisingEdge(dut.core_clk)

    mism = [f"beat {i}: got {g} expected {w}" for i, (g, w) in enumerate(zip(got, want_beats))
            if g != w]
    cnt = await bus.counters()
    cnt_bad = {k: (cnt[k], want_cnt[k]) for k in COUNTERS
               if k != "PKT_OVERFLOW" and cnt[k] != want_cnt[k]}
    total = cnt["PKT_NORMAL"] + cnt["PKT_SCAN"] + cnt["PKT_SYN"] + cnt["PKT_DROPPED"] \
        + cnt["PKT_OVERFLOW"]
    dut._log.info(f"top smoke (simulated): result beats {len(got)} (expected {len(want_beats)}), "
                  f"mismatches {len(mism)}, protocol errors {len(errors)}")
    dut._log.info("top smoke (simulated): counters read over AXI-Lite: "
                  + ", ".join(f"{k}={v}" for k, v in cnt.items())
                  + f"; sum of the five frame counters = {total} for {len(frames)} frames")
    lat = sorted((out_ns[i] - in_ns[i]) / (CORE_PS / 1000) for i in out_ns if i in in_ns)
    if lat:
        dut._log.info(f"top smoke (simulated, not asserted): last input beat to result beat "
                      f"{lat[0]:.0f}..{lat[-1]:.0f} core clocks over {len(lat)} classified "
                      "frames (includes random result-stream stalls)")
    assert not errors, "\n".join(errors[:10])
    assert len(got) == len(want_beats), f"{len(got)} result beats, expected {len(want_beats)}"
    assert not mism, "\n".join(mism[:5])
    assert cnt["PKT_OVERFLOW"] == 0, f"PKT_OVERFLOW {cnt['PKT_OVERFLOW']} with paced input"
    assert not cnt_bad, f"counter (got, expected): {cnt_bad}"
    assert total == len(frames), f"frame counters sum to {total}, sent {len(frames)}"

    # Soft reset over AXI-Lite: every counter and LAST_RESULT read 0 afterwards;
    # CONF_THRESH keeps its value; CTRL reads 0 (self-clearing).
    assert await bus.write(CTRL, 1) == 0
    after = await bus.counters()
    assert all(v == 0 for v in after.values()), f"after soft reset: {after}"
    assert (await bus.read(CONF_THRESH))[0] == thresh
    assert (await bus.read(CTRL))[0] == 0
    dut._log.info("top smoke (simulated): soft reset over AXI-Lite cleared all counters; "
                  "CONF_THRESH kept")
    for t in tasks:
        t.cancel()
    await RisingEdge(dut.core_clk)
