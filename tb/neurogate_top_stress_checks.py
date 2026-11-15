"""cocotb stress tests of the whole chip: bad packets, backpressure, overflow, resets.

Started by tb/test_neurogate_top_stress.py (which writes the frame files). All
results are *simulated*, on synthetic data. Every test powers the chip up itself
(random core-clock phase, random reset release) and compares with the Python models
(model/features.py + golden model model/golden.py) bit for bit.

Tests
  test_bad_packets        ~450 malformed frames mixed with good ones, input stalls,
                          random result stalls: beats and counters equal the model.
  test_result_backpressure  result stream held low for hundreds of clocks; nothing lost,
                          no beat changes while waiting, beats and counters equal.
  test_overflow           frames sent back to back with the result stream blocked, so
                          the async FIFO overflows: surviving beats equal the model,
                          every lost frame is counted once (including the tail, which
                          travels in an overflow-only entry). Exact in the all-kept
                          phase; in the mixed phase the dropped/overflow split uses the
                          chip's own counts, checked only by sum and a lower bound.
  test_hard_reset_midstream  rst_n pulled low (random length and phase) mid-frame,
                          mid-inference, while stalled, and during an AXI-Lite read:
                          everything clears, no stale result appears, the chip then
                          matches the model on a fresh stream.
  test_soft_reset_midstream  soft reset over AXI-Lite while traffic flows: counters
                          equal the model beats finished after the last reset.
  test_monitor_after_clear  regression: a protocol fault forced on the result stream
                          after Chip.clear_beats() must still be reported (the list
                          the monitor writes to must be the one the tests read).
  test_soft_reset_same_cycle  a result beat and the soft reset in the same clock: the
                          reset wins (docs/regmap.md).
"""
import os
import random

import cocotb
from cocotb.handle import Force, Release
from cocotb.triggers import ReadOnly, RisingEdge, Timer

from top_common import (COUNTERS, CORE_PS, CTRL, NET_PS, SEED, AxiLite, Chip, frame_sum,
                        model_counters, model_frames, read_frames, read_thresh, send_frame,
                        send_paced)

LONG = dict(timeout_time=60, timeout_unit="ms")


def frames_of(var: str):
    """Frames from the file named by an environment variable."""
    path = os.environ.get(var, "")
    assert path and os.path.exists(path), f"frame file missing: {var}='{path}'"
    return read_frames(path)


async def start(dut, tag: str):
    """Seeded rng, powered-up chip with the model threshold written, monitor running."""
    rng = random.Random(f"{SEED}-{tag}")
    chip = Chip(dut, rng)
    await chip.power_up()
    thresh = read_thresh()
    assert await chip.bus.write(4, thresh) == 0
    dut._log.info(f"{tag} (simulated): seed={SEED} core_clk phase={chip.phase} ps")
    return chip, thresh


async def settle_counters(chip, total: int, limit: int = 60):
    """Read the counters until the five frame counters add up to `total` (the pipeline
    has drained) or the limit is reached; returns the last reading."""
    cnt = await chip.bus.counters()
    for _ in range(limit):
        if frame_sum(cnt) == total:
            break
        for _ in range(500):
            await RisingEdge(chip.dut.core_clk)
        cnt = await chip.bus.counters()
    return cnt


def check_exact(chip, want, frames, cnt) -> None:
    """Beats, counters and protocol equal the model; no overflow."""
    want_beats = [b for b in want if b is not None]
    want_cnt = model_counters(want_beats, want.count(None))
    mism = [f"beat {i}: got {g} expected {w}"
            for i, (g, w) in enumerate(zip(chip.beats, want_beats)) if g != w]
    bad = {k: (cnt[k], want_cnt[k]) for k in COUNTERS if cnt[k] != want_cnt[k]}
    chip.dut._log.info(f"result beats {len(chip.beats)} (expected {len(want_beats)}), "
                       f"mismatches {len(mism)}, protocol errors {len(chip.errors)}; counters "
                       + ", ".join(f"{k}={v}" for k, v in cnt.items()))
    assert not chip.errors, "\n".join(chip.errors[:10])
    assert len(chip.beats) == len(want_beats), f"{len(chip.beats)} beats, expected {len(want_beats)}"
    assert not mism, "\n".join(mism[:5])
    assert cnt["PKT_OVERFLOW"] == 0, f"PKT_OVERFLOW {cnt['PKT_OVERFLOW']} with paced input"
    assert not bad, f"counter (got, expected): {bad}"
    assert frame_sum(cnt) == len(frames)


@cocotb.test(**LONG)
async def test_bad_packets(dut) -> None:
    """Malformed, truncated, wrong-EtherType and tuser-error frames among good ones."""
    frames = frames_of("BAD_FRAMES")
    chip, thresh = await start(dut, "bad")
    want = model_frames(frames, thresh)
    dut._log.info(f"bad packets: {len(frames)} frames, model drops {want.count(None)}, "
                  f"classifies {len(frames) - want.count(None)}")
    chip.p_low = 0.3
    chip.start_monitor()
    await send_paced(chip, frames, want, stall_p=0.2)
    await chip.drain(len(frames) - want.count(None))
    chip.stop_monitor()
    await RisingEdge(dut.core_clk)
    check_exact(chip, want, frames, await chip.bus.counters())
    await RisingEdge(dut.core_clk)


@cocotb.test(**LONG)
async def test_result_backpressure(dut) -> None:
    """Result tready held low for long stretches; the pipeline backs up and recovers."""
    frames = frames_of("GOOD_FRAMES")
    chip, thresh = await start(dut, "backpressure")
    want = model_frames(frames, thresh)
    stats = {"longest": 0}

    async def stall_pattern() -> None:
        """Alternate long full stalls with random-ready stretches; track the longest
        stretch with tvalid high and tready low."""
        run = 0
        n = 0
        while True:
            hold = chip.rng.randrange(200, 1500) if n % 2 == 0 else chip.rng.randrange(30, 200)
            chip.force_low = n % 2 == 0
            chip.p_low = 0.7
            for _ in range(hold):
                await ReadOnly()
                run = run + 1 if (int(dut.m_axis_res_tvalid.value) and
                                  not int(dut.m_axis_res_tready.value)) else 0
                stats["longest"] = max(stats["longest"], run)
                await RisingEdge(dut.core_clk)
            n += 1

    chip.start_monitor()
    pat = cocotb.start_soon(stall_pattern())
    await send_paced(chip, frames, want, stall_p=0.15, max_inflight=3)
    chip.force_low = False
    pat.cancel()
    chip.p_low = 0.5
    await chip.drain(len(frames) - want.count(None))
    chip.stop_monitor()
    await RisingEdge(dut.core_clk)
    dut._log.info(f"longest stall with tvalid high and tready low: {stats['longest']} core clocks")
    assert stats["longest"] >= 200, "no long stall was exercised"
    check_exact(chip, want, frames, await chip.bus.counters())
    await RisingEdge(dut.core_clk)


@cocotb.test(**LONG)
async def test_overflow(dut) -> None:
    """Back-to-back frames with the result stream blocked: the async FIFO overflows."""
    kept = frames_of("KEPT_FRAMES")
    mixed = frames_of("MIXED_FRAMES")
    chip, thresh = await start(dut, "overflow")
    allf = kept + mixed
    want = model_frames(allf, thresh)          # one continuous stream: ids and window carry on
    chip.start_monitor()

    async def burst(frames) -> None:
        """Send back to back (no pacing) with the result stream blocked, then release."""
        chip.force_low = True
        for fr, tu in frames:
            await send_frame(dut, chip.rng, fr, tu)
        for _ in range(2000):
            await RisingEdge(dut.core_clk)
        chip.force_low = False
        chip.p_low = 0.3

    # Phase A: only kept frames, so every lost frame is a classified frame: exact.
    await burst(kept)
    cntA = await settle_counters(chip, len(kept))
    ids = [b[0] for b in chip.beats]
    lostA = len(kept) - len(chip.beats)
    dut._log.info(f"overflow A: {len(kept)} kept frames in, {len(chip.beats)} result beats, "
                  f"PKT_OVERFLOW={cntA['PKT_OVERFLOW']}")
    assert not chip.errors, "\n".join(chip.errors[:10])
    assert lostA >= 200 and cntA["PKT_OVERFLOW"] == lostA, (lostA, cntA["PKT_OVERFLOW"])
    assert ids == sorted(set(ids)) and ids[0] == 0, "beats must be in order; frame 0 cannot be lost"
    assert len(kept) - 1 not in ids, "last frame survived: the overflow-only entry was not exercised"
    assert all(b == want[b[0]] for b in chip.beats), "a surviving beat differs from the model"
    assert cntA == model_counters(chip.beats, 0, lostA), (cntA, "vs beats")
    assert await chip.bus.write(CTRL, 1) == 0
    assert all(v == 0 for v in (await chip.bus.counters()).values())

    # Phase B: mixed frames (some are dropped by the chip): sums and per-beat checks.
    chip.clear_beats()
    await burst(mixed)
    cntB = await settle_counters(chip, len(mixed))
    ids = [b[0] - len(kept) for b in chip.beats]
    model_drops = sum(1 for w in want[len(kept):] if w is None)
    dut._log.info(f"overflow B: {len(mixed)} mixed frames in (model drops {model_drops}), "
                  f"{len(chip.beats)} beats, PKT_DROPPED={cntB['PKT_DROPPED']}, "
                  f"PKT_OVERFLOW={cntB['PKT_OVERFLOW']}")
    assert not chip.errors, "\n".join(chip.errors[:10])
    assert ids == sorted(set(ids)) and all(b == want[b[0]] for b in chip.beats)
    assert cntB["PKT_OVERFLOW"] > 0 and cntB["PKT_DROPPED"] <= model_drops
    assert frame_sum(cntB) == len(mixed), f"sum {frame_sum(cntB)} for {len(mixed)} frames"
    ref = model_counters(chip.beats, cntB["PKT_DROPPED"], cntB["PKT_OVERFLOW"])
    assert cntB == ref, (cntB, ref)
    assert len(chip.beats) + cntB["PKT_DROPPED"] + cntB["PKT_OVERFLOW"] == len(mixed)
    # Independent of the chip's own counts: the model says how many frames are
    # classified, so every classified frame without a beat was lost to overflow.
    # Only this lower bound is independent; how the remaining overflow splits between
    # dropped-type and classified frames cannot be told apart from the outside (a lost
    # entry carries no kind), so the exact split is NOT claimed for this phase.
    lost_classified = (len(mixed) - model_drops) - len(chip.beats)
    assert cntB["PKT_OVERFLOW"] >= lost_classified, (cntB["PKT_OVERFLOW"], lost_classified)
    assert cntB["PKT_DROPPED"] + cntB["PKT_OVERFLOW"] - lost_classified == model_drops, \
        "model-dropped frames are not all accounted for as dropped or overflowed"
    chip.stop_monitor()
    await RisingEdge(dut.core_clk)


@cocotb.test(**LONG)
async def test_hard_reset_midstream(dut) -> None:
    """External reset at random moments and lengths; then a fresh stream matches the model."""
    frames = frames_of("GOOD_FRAMES")
    chip, thresh = await start(dut, "hardreset")
    rng = chip.rng
    chip.start_monitor()
    for rnd in range(8):
        # Frames before the reset: a slice that is cut off. After the reset the chip
        # must behave as if powered up: ids from 0, empty window, zero counters.
        pre = frames[rnd * 7:rnd * 7 + 30]
        want_pre = model_frames(pre, thresh)
        chip.clear_beats()
        chip.force_low = rnd % 3 == 1                   # every third round: result stalled
        sender = cocotb.start_soon(send_paced(chip, pre, want_pre, stall_p=0.1))
        await Timer(rng.randrange(1, 60) * NET_PS * (1 + 6 * (rnd % 2)) + rng.randrange(1, 997), unit="ps")
        rd = None
        if rnd % 2 == 1:                                  # a register read in flight at reset
            rd = cocotb.start_soon(chip.bus.read(0x08))
            await RisingEdge(dut.core_clk)
        sender.cancel()
        if rd is not None:
            rd.cancel()
        low_ps = [2, 3000, 90_000, rng.randrange(1000, 300_000)][rnd % 4]
        dut.s_axis_tvalid.value = 0
        await chip.hard_reset(low_ps)
        chip.force_low = False
        chip.bus = AxiLite(dut)                           # the bus master restarts too
        chip.clear_beats()
        # The synchronized reset stays low for two core edges after rst_n rises, and a
        # read just after an edge shows the values from before that edge. So wait for
        # the reset to be released (plus one edge) before looking at the outputs.
        for _ in range(10):
            await RisingEdge(dut.core_clk)
            if int(dut.core_rst_n.value) == 1:
                break
        assert int(dut.core_rst_n.value) == 1, "core reset never released"
        await RisingEdge(dut.core_clk)
        for _ in range(600):                              # quiet: no stale entry may come out
            await RisingEdge(dut.core_clk)
            assert int(dut.m_axis_res_tvalid.value) == 0, f"round {rnd}: stale result after reset"
        assert not chip.beats, f"round {rnd}: {len(chip.beats)} stale beats after reset"
        cnt = await chip.bus.counters()
        assert all(v == 0 for v in cnt.values()), f"round {rnd}: counters after reset {cnt}"
        assert (await chip.bus.read(4)) == (13, 0), f"round {rnd}: CONF_THRESH not back to reset value"
        assert await chip.bus.write(4, thresh) == 0
        # Fresh stream: bit for bit against a model that starts from scratch.
        post = frames[rnd * 11 + 3:rnd * 11 + 28]
        want = model_frames(post, thresh)
        chip.p_low = 0.3
        await send_paced(chip, post, want, stall_p=0.1)
        await chip.drain(len(post) - want.count(None))
        dut._log.info(f"round {rnd}: reset low {low_ps} ps after {chip.sent} ... "
                      f"cut {len(pre)}-frame stream; fresh stream of {len(post)} frames")
        check_exact(chip, want, post, await chip.bus.counters())
    chip.stop_monitor()
    await RisingEdge(dut.core_clk)


@cocotb.test(**LONG)
async def test_soft_reset_midstream(dut) -> None:
    """Soft resets over AXI-Lite during traffic: counters = beats finished after the last one."""
    frames = frames_of("KEPT_FRAMES")[:150]
    chip, thresh = await start(dut, "softreset")
    want = model_frames(frames, thresh)
    chip.p_low = 0.3
    chip.start_monitor()
    sender = cocotb.start_soon(send_paced(chip, frames, want, stall_p=0.1))
    last_ps = 0
    for k in range(3):
        for _ in range(chip.rng.randrange(1500, 6000)):
            await RisingEdge(dut.core_clk)
        assert await chip.bus.write(CTRL, 1) == 0
        last_ps = chip.bus.last_accept_ps
    await sender
    await chip.drain(len(frames))
    chip.stop_monitor()
    await RisingEdge(dut.core_clk)
    after = [b for b, t in zip(chip.beats, chip.beat_ps) if t > last_ps]
    before = len(chip.beats) - len(after)
    cnt = await chip.bus.counters()
    ref = model_counters(after, 0)
    dut._log.info(f"soft reset mid-stream: {len(chip.beats)} beats, {before} before the last "
                  f"reset, {len(after)} after; counters " + ", ".join(f"{k}={v}" for k, v in cnt.items()))
    assert not chip.errors, "\n".join(chip.errors[:10])
    assert len(chip.beats) == len(frames) and all(b == w for b, w in zip(chip.beats, want))
    assert before > 0 and after, "reset must fall inside the traffic"
    assert cnt == ref, (cnt, ref)
    await RisingEdge(dut.core_clk)


@cocotb.test(**LONG)
async def test_soft_reset_same_cycle(dut) -> None:
    """A result beat leaves in the very clock the soft reset is accepted: reset wins."""
    frames = frames_of("KEPT_FRAMES")[:2]
    chip, thresh = await start(dut, "samecycle")
    want = model_frames(frames, thresh)
    chip.start_monitor(drive_ready=False)
    dut.m_axis_res_tready.value = 0

    async def wait_valid() -> None:
        """Wait until the result beat has been offered and held for 6 clocks."""
        seen = 0
        for _ in range(5000):
            if seen >= 6:
                return
            await ReadOnly()
            seen = seen + 1 if int(dut.m_axis_res_tvalid.value) else 0
            await RisingEdge(dut.core_clk)
        raise AssertionError("the result beat was not held while tready was low")

    # Control: frame 0 leaves with no reset; it must be counted.
    await send_frame(dut, chip.rng, *frames[0])
    await wait_valid()
    dut.m_axis_res_tready.value = 1
    for _ in range(10):
        await RisingEdge(dut.core_clk)
    cnt = await chip.bus.counters()
    assert cnt == model_counters([want[0]], 0), cnt
    assert await chip.bus.write(CTRL, 1) == 0
    # Collision: frame 1 waits at the output; tready=1 and the soft-reset write go in
    # together, so both happen at the same core clock edge.
    dut.m_axis_res_tready.value = 0
    await send_frame(dut, chip.rng, *frames[1])
    await wait_valid()
    dut.m_axis_res_tready.value = 1
    assert await chip.bus.write(CTRL, 1) == 0
    for _ in range(10):
        await RisingEdge(dut.core_clk)
    cnt = await chip.bus.counters()
    dut._log.info(f"same-cycle soft reset: beat taken at {chip.beat_ps[-1]} ps, reset accepted at "
                  f"{chip.bus.last_accept_ps} ps; counters after: {cnt}")
    assert chip.beat_ps[-1] == chip.bus.last_accept_ps, "beat and reset were not in the same clock"
    assert chip.beats[-1] == want[1], "the beat itself must still go out"
    assert all(v == 0 for v in cnt.values()), f"reset must win over the entry: {cnt}"
    assert not chip.errors
    chip.stop_monitor()
    await RisingEdge(dut.core_clk)


@cocotb.test(**LONG)
async def test_monitor_after_clear(dut) -> None:
    """Regression: protocol errors raised after clear_beats() are still seen.

    Chip.clear_beats() once built a new error list while the running monitor kept
    appending to the old one, so later stall violations were lost. Here the stall
    rule is broken on purpose, after a clear: the testbench forces result tvalid high
    for two clocks with tready low, then drops it (a valid that disappears during a
    stall). The shared monitor must report it in chip.errors."""
    chip, _ = await start(dut, "monitor")
    chip.start_monitor(drive_ready=False)
    dut.m_axis_res_tready.value = 0
    for _ in range(5):
        await RisingEdge(dut.core_clk)
    chip.clear_beats()                      # the call under test
    assert not chip.errors
    dut.m_axis_res_tvalid.value = Force(1)
    for _ in range(3):
        await RisingEdge(dut.core_clk)
    dut.m_axis_res_tvalid.value = Force(0)  # valid drops while tready was low: a fault
    for _ in range(3):
        await RisingEdge(dut.core_clk)
    dut.m_axis_res_tvalid.value = Release()
    dut._log.info(f"monitor after clear (simulated): errors seen = {list(chip.errors)}")
    assert any("valid dropped during a stall" in e for e in chip.errors), \
        "a protocol fault forced after clear_beats() was not reported"
    chip.stop_monitor()
    await RisingEdge(dut.core_clk)
