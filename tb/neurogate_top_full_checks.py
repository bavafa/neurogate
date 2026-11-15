"""cocotb end-to-end test of the whole chip on generated traffic.

Started by tb/test_neurogate_top_full.py (which writes the frame file).

What it shows (all *simulated*, synthetic traffic)
    The generated frames (900 in `make test`, all 9000 in `make test-full`) go in on net_clk (8-byte beats, random garbage in
    unused lanes, random 10% input stalls inside frames), cross the async FIFO,
    are classified on core_clk, and leave as result beats while the result
    stream stalls at random (30% of clocks). Then:
      * every result beat (pkt_id, pkt_len, class, confidence, low_conf) equals
        the Python models (model/features.py + the golden model model/golden.py),
        in order, with no missing and no extra beat;
      * every counter read over AXI-Lite equals the model counters (class
        counts, byte counts, dropped, low-confidence, LAST_RESULT), overflow is 0,
        and the five frame counters sum to the frames sent;
      * the result stream obeys the valid/ready stall rule.
    Frames are paced (see top_common.send_paced) so the async FIFO cannot
    overflow; the overflow path has its own test (neurogate_top_stress_checks).
"""
import os
import random

import cocotb
from cocotb.triggers import RisingEdge
from cocotb.utils import get_sim_time

from top_common import (COUNTERS, CORE_PS, CTRL, SEED, Chip, frame_sum, model_counters,
                        model_frames, read_frames, read_thresh, send_paced)


@cocotb.test(timeout_time=60, timeout_unit="ms")
async def test_full_traffic_end_to_end(dut) -> None:
    """All generated frames in (900 in make test, 9000 in make test-full); beats and counters equal the models."""
    path = os.environ.get("TOP_FRAMES", "")
    assert path and os.path.exists(path), f"frame file missing: '{path}'"
    frames = read_frames(path)
    rng = random.Random(f"{SEED}-top-full")
    thresh = read_thresh()
    want = model_frames(frames, thresh)
    want_beats = [b for b in want if b is not None]
    want_cnt = model_counters(want_beats, want.count(None))

    chip = Chip(dut, rng)
    await chip.power_up()
    dut._log.info(f"top full (simulated): seed={SEED} core_clk phase={chip.phase} ps, "
                  f"frames={len(frames)} (classified {len(want_beats)}, dropped "
                  f"{want.count(None)}), conf_thresh={thresh}")
    bus = chip.bus
    assert await bus.write(4, thresh) == 0
    chip.p_low = 0.3
    chip.start_monitor()
    t0 = get_sim_time("us")
    await send_paced(chip, frames, want, stall_p=0.1)
    await chip.drain(len(want_beats))
    chip.stop_monitor()
    await RisingEdge(dut.core_clk)

    mism = [f"beat {i}: got {g} expected {w}"
            for i, (g, w) in enumerate(zip(chip.beats, want_beats)) if g != w]
    cnt = await bus.counters()
    cnt_bad = {k: (cnt[k], want_cnt[k]) for k in COUNTERS if cnt[k] != want_cnt[k]}
    dut._log.info(f"top full (simulated): {chip.sent} frames sent in "
                  f"{get_sim_time('us') - t0:.0f} us of simulated time; result beats "
                  f"{len(chip.beats)} (expected {len(want_beats)}), mismatches {len(mism)}, "
                  f"protocol errors {len(chip.errors)}")
    dut._log.info("top full (simulated): counters read over AXI-Lite: "
                  + ", ".join(f"{k}={v}" for k, v in cnt.items())
                  + f"; sum of the five frame counters = {frame_sum(cnt)} for {len(frames)} frames")
    assert not chip.errors, "\n".join(chip.errors[:10])
    assert len(chip.beats) == len(want_beats), \
        f"{len(chip.beats)} result beats, expected {len(want_beats)}"
    assert not mism, "\n".join(mism[:5])
    assert cnt["PKT_OVERFLOW"] == 0, f"PKT_OVERFLOW {cnt['PKT_OVERFLOW']} with paced input"
    assert not cnt_bad, f"counter (got, expected): {cnt_bad}"
    assert frame_sum(cnt) == len(frames), f"frame counters sum to {frame_sum(cnt)}"
    assert await bus.write(CTRL, 1) == 0
    assert all(v == 0 for v in (await bus.counters()).values()), "soft reset did not clear"
    await RisingEdge(dut.core_clk)
