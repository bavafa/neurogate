"""cocotb throughput measurement of neurogate_top (all *simulated*, RTL, two clocks).

Started by tb/test_neurogate_top_throughput.py (which writes the frame file).

What it does
    Sends 14 frames the models classify back to back on net_clk (no pacing; the
    async FIFO holds 16 entries, so none may be lost), with the result stream always
    ready. The core then works through a backlog, so the time between result beats
    is the time the core needs per packet when it is the bottleneck. Checks that no
    frame was lost (PKT_OVERFLOW 0) and every beat equals the model, then logs:
      * core clocks per result beat once the backlog has built up (min / mean / max),
      * the packets per second that gives at core_clk 200 MHz (a *simulated
        estimate*: cycle counts from this simulation times the SPEC clock; it says
        nothing about timing closure, which is a separate OpenSTA step),
      * the first-frame latency: last input beat to its result beat.
    The numbers are logged, not asserted (only no-loss and model equality are).
"""
import os
import random
from typing import List

import cocotb
from cocotb.utils import get_sim_time

from top_common import (COUNTERS, CORE_PS, NET_PS, SEED, Chip, model_frames, read_frames,
                        read_thresh, send_frame)


@cocotb.test(timeout_time=5, timeout_unit="ms")
async def test_throughput(dut) -> None:
    """Back-to-back classified frames; measure core clocks per result beat."""
    path = os.environ.get("TOP_FRAMES", "")
    assert path and os.path.exists(path), f"frame file missing: '{path}'"
    frames = read_frames(path)
    thresh = read_thresh()
    want = model_frames(frames, thresh)
    assert all(w is not None for w in want), "frame file must hold classified frames only"
    rng = random.Random(f"{SEED}-top-throughput")
    chip = Chip(dut, rng)
    await chip.power_up()
    chip.p_low = 0.0                                   # result stream always ready
    assert await chip.bus.write(0x04, thresh) == 0
    chip.start_monitor()
    last_in_ps: List[int] = []
    for frame, tuser in frames:
        await send_frame(dut, rng, frame, tuser)       # no gap between frames
        last_in_ps.append(int(get_sim_time("ps")))
    await chip.drain(len(frames))
    chip.stop_monitor()
    assert not chip.errors, f"protocol errors: {chip.errors[:3]}"
    ovf = (await chip.bus.read(COUNTERS["PKT_OVERFLOW"]))[0]
    assert ovf == 0, f"PKT_OVERFLOW {ovf}: frames were lost, burst too long for the FIFO"
    assert chip.beats == want, "result beats differ from the model"

    gaps = [(b - a) / CORE_PS for a, b in zip(chip.beat_ps, chip.beat_ps[1:])]
    sat = gaps[2:] if len(gaps) > 4 else gaps          # skip the start-up beats
    mean = sum(sat) / len(sat)
    in_span_ns = (last_in_ps[-1] - last_in_ps[0]) / 1000
    mean_len = sum(len(f) for f, _ in frames) / len(frames)
    dut._log.info(
        f"throughput (simulated): {len(frames)} classified frames back to back, mean frame "
        f"{mean_len:.0f} bytes; input of frames 2..{len(frames)} took {in_span_ns:.0f} ns "
        f"({in_span_ns / (len(frames) - 1) / (NET_PS / 1000):.1f} net clocks per frame)")
    dut._log.info(
        f"throughput (simulated): core clocks between result beats with a backlog: "
        f"min {min(sat):.0f}, mean {mean:.1f}, max {max(sat):.0f} over {len(sat)} gaps")
    dut._log.info(
        f"throughput (simulated estimate): {200e6 / mean / 1e3:.0f} k packets/s at "
        f"core_clk 200 MHz (200e6 / {mean:.1f}); core-limited, about "
        f"{200e6 / mean * mean_len * 8 / 1e6:.0f} Mbit/s of frame data at this mean frame size")
    dut._log.info(
        f"throughput (simulated): first-frame latency, last input beat to result beat: "
        f"{(chip.beat_ps[0] - last_in_ps[0]) / CORE_PS:.0f} core clocks")
