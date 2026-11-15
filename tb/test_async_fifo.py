"""cocotb tests for rtl/async_fifo.sv (dual-clock AXI-Stream FIFO, SPEC section 8).

Run: `python3 tb/test_async_fifo.py` from .venv. Builds with Icarus and runs the
tests through tb/cocotb_run.py (DECISIONS D-012); exits non-zero on any failure.
The seed comes from the SEED environment variable (default 12345) and is printed.

Clocks
  The write clock (wr_clk) and read clock (rd_clk) are two independent cocotb
  clocks. The read clock starts after a random, odd number of picoseconds, so
  its edges never line up with write clock edges (all periods are even
  numbers of ps). Ratios tested (write : read frequency):
    wr_faster  250 MHz : 100 MHz  (5 : 2)
    rd_faster  100 MHz : 250 MHz  (2 : 5)
    equal      156.25 MHz : 156.25 MHz, random phase
    7to5       200 MHz : 142.9 MHz (7 : 5, not an integer ratio)
    5to7       142.9 MHz : 200 MHz (5 : 7)
    chip       156.25 MHz : 200 MHz (net_clk : core_clk, 25 : 32)

How the check works
  A writer coroutine runs on wr_clk and a reader coroutine on rd_clk. They
  share a scoreboard: a queue of beats the FIFO has taken in and not yet
  given out. A beat counts as written at the wr_clk edge where the handshake
  happens, and as read at the rd_clk edge where it leaves; the time (ps) of
  every write and read is kept. Each beat is {tdata, tkeep, tlast, tuser};
  tdata[31:0] is a running sequence number, so loss, duplication and
  reordering show up at once. Checks, every cycle of the relevant clock:
    * Data: every beat on the output (m_tvalid = 1) equals the oldest queued
      beat in all four fields; a stalled output does not change.
    * No overflow: whenever s_tready = 1, fewer than DEPTH beats are stored
      (so the next write cannot overwrite an unread slot).
    * No underflow: whenever m_tvalid = 1, at least one written beat is
      waiting (so the output never shows a slot that was not written).
    * Flags not stuck: a pointer step needs up to three edges of the other
      clock to cross (two synchronizer flops, then the registered flag).
      s_tready = 0 is only allowed if the FIFO held DEPTH beats counting only
      reads older than 3 write clock periods; m_tvalid = 0 is only allowed if
      no beat written more than 3 read clock periods ago is waiting.
    * Reset: during reset s_tready = 0 and m_tvalid = 0. Beats stored when
      reset hits are discarded by design and counted, not compared.
  The writer follows the AXI-Stream rule: a beat on the input is held until
  it is accepted.

What this does NOT show
  RTL simulation has no metastability and no real wire delays: a flop always
  samples a clean 0 or 1. So these tests show the FIFO logic is right for the
  clock ratios simulated (Gray coding, full/empty, ordering, reset), not that
  the crossing is safe in silicon. That needs a CDC review (planned for M4)
  and, for a real chip, timing constraints on the synchronizer paths.
"""
import bisect
import os
import random
import sys
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, List, Optional, Tuple

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ReadOnly, RisingEdge, Timer
from cocotb.utils import get_sim_time

SEED = int(os.environ.get("SEED", "12345"))
DEPTH = 16            # default DEPTH of rtl/async_fifo.sv (the runner uses the default)
BYTES_PER_BEAT = 8
SYNC_EDGES = 3        # edges of the receiving clock for a pointer step to reach a flag

# (name, write clock period ps, read clock period ps)
RATIOS: List[Tuple[str, int, int]] = [
    ("wr_faster", 4000, 10000),
    ("rd_faster", 10000, 4000),
    ("equal", 6400, 6400),
    ("7to5", 5000, 7000),
    ("5to7", 7000, 5000),
    ("chip", 6400, 5000),
]

# One stream beat: (tdata, tkeep, tlast, tuser)
Beat = Tuple[int, int, int, int]


def fmt(b: Optional[Beat]) -> str:
    """Short text for a beat, for error messages."""
    if b is None:
        return "none"
    return f"seq={b[0] & 0xFFFFFFFF} data={b[0]:016x} keep={b[1]:02x} last={b[2]} user={b[3]}"


def now_ps() -> int:
    """Current simulation time in ps."""
    return int(get_sim_time(unit="ps"))


class BeatSource:
    """Makes frames of 1-5 beats with a running sequence number in tdata[31:0]."""

    def __init__(self, rng: random.Random) -> None:
        """Start a new source; the sequence number never repeats within a test."""
        self.rng, self.seq, self.left_in_frame = rng, 0, 0

    def next(self) -> Beat:
        """Return the next beat of the current frame (starting a frame if needed)."""
        if self.left_in_frame == 0:
            self.left_in_frame = self.rng.randrange(1, 6)
        self.left_in_frame -= 1
        last = self.left_in_frame == 0
        keep = ((1 << self.rng.randrange(1, BYTES_PER_BEAT + 1)) - 1) if last else 0xFF
        data = (self.rng.getrandbits(32) << 32) | (self.seq & 0xFFFFFFFF)
        self.seq += 1
        return data, keep, int(last), self.rng.randrange(2)


@dataclass
class Stats:
    """What one scenario saw. All numbers are simulated."""

    offered: int = 0            # beats the writer started to present
    accepted: int = 0           # beats the FIFO took in (handshake at a wr_clk edge)
    delivered: int = 0          # beats checked on the output
    lost_to_reset: int = 0      # beats stored in the FIFO when a reset hit
    max_occupancy: int = 0
    s_ready_low_cycles: int = 0  # wr cycles with a beat offered and s_tready low
    first_out_ps: Optional[int] = None
    last_out_ps: int = 0
    errors: List[str] = field(default_factory=list)


class Env:
    """Shared state of one test: clocks, scoreboard and reset bookkeeping."""

    def __init__(self, dut, wr_ps: int, rd_ps: int, rng: random.Random) -> None:
        """Remember the DUT and the clock periods; nothing starts yet."""
        self.dut, self.wr_ps, self.rd_ps, self.rng = dut, wr_ps, rd_ps, rng
        self.src = BeatSource(rng)
        self.expected: Deque[Beat] = deque()
        self.wr_times: List[int] = []   # time of every write since the last reset
        self.rd_times: List[int] = []   # time of every read since the last reset
        self.epoch = 0                  # bumped by every reset; stale handshakes are dropped
        self.wr_in_reset = self.rd_in_reset = False
        self.wr_release_ps = self.rd_release_ps = 0
        self.writer_done = False
        self.write_in_flight = False
        self.deadline_ps = 0            # watchdog: a stuck FIFO fails instead of hanging
        self.st = Stats()
        self.clocks: List[Clock] = []

    def occupancy(self) -> int:
        """Beats written and not yet read, as of the last committed edges."""
        return len(self.wr_times) - len(self.rd_times)

    def error(self, msg: str) -> None:
        """Record a failure (the test asserts on the list at the end)."""
        if len(self.st.errors) < 30:
            self.st.errors.append(f"t={now_ps()} ps: {msg}")

    async def start(self) -> None:
        """Start both clocks (read clock at a random odd ps offset) and reset both sides."""
        d = self.dut
        d.s_tvalid.value = 0
        d.s_tdata.value = 0
        d.s_tkeep.value = 0
        d.s_tlast.value = 0
        d.s_tuser.value = 0
        d.m_tready.value = 0
        d.wr_rst_n.value = 0
        d.rd_rst_n.value = 0
        wclk = Clock(d.wr_clk, self.wr_ps, unit="ps")
        wclk.start()
        phase = 2 * self.rng.randrange(self.rd_ps // 2) + 1   # odd: never on a write edge
        await Timer(phase, unit="ps")
        rclk = Clock(d.rd_clk, self.rd_ps, unit="ps")
        rclk.start()
        self.clocks = [wclk, rclk]
        d._log.info(f"clocks: wr {self.wr_ps} ps, rd {self.rd_ps} ps, rd phase {phase} ps")
        for _ in range(4):
            await RisingEdge(d.rd_clk)
        await self.release()

    async def release(self) -> None:
        """Release the two resets, each on an edge of its own clock (synchronous release)."""
        d = self.dut
        if self.rng.random() < 0.5:
            await RisingEdge(d.wr_clk)
            d.wr_rst_n.value = 1
            self.wr_in_reset, self.wr_release_ps = False, now_ps()
            for _ in range(self.rng.randrange(0, 4)):
                await RisingEdge(d.rd_clk)
            await RisingEdge(d.rd_clk)
            d.rd_rst_n.value = 1
            self.rd_in_reset, self.rd_release_ps = False, now_ps()
        else:
            await RisingEdge(d.rd_clk)
            d.rd_rst_n.value = 1
            self.rd_in_reset, self.rd_release_ps = False, now_ps()
            for _ in range(self.rng.randrange(0, 4)):
                await RisingEdge(d.wr_clk)
            await RisingEdge(d.wr_clk)
            d.wr_rst_n.value = 1
            self.wr_in_reset, self.wr_release_ps = False, now_ps()

    async def reset_pulse(self, hold_rd_cycles: int) -> None:
        """Assert both resets at the same moment (asynchronous assert, between
        clock edges), clear the scoreboard, hold, then release each side."""
        d = self.dut
        await RisingEdge(d.wr_clk)
        await Timer(2, unit="ps")   # even offset from a write edge: not on any edge
        d.wr_rst_n.value = 0
        d.rd_rst_n.value = 0
        self.wr_in_reset = self.rd_in_reset = True
        self.epoch += 1
        self.st.lost_to_reset += len(self.expected)
        self.expected.clear()
        self.wr_times.clear()
        self.rd_times.clear()
        for _ in range(hold_rd_cycles):
            await RisingEdge(d.rd_clk)
        await self.release()

    async def stop(self) -> None:
        """Idle the inputs, let a few edges pass, stop the clocks, and end one
        step later, so no signal write is queued when the test ends (this
        cocotb/Icarus build segfaults at shutdown otherwise; DECISIONS D-012)."""
        d = self.dut
        await RisingEdge(d.wr_clk)
        d.s_tvalid.value = 0
        await RisingEdge(d.rd_clk)
        d.m_tready.value = 0
        await RisingEdge(d.rd_clk)
        await RisingEdge(d.wr_clk)
        for c in self.clocks:
            c.stop()
        await Timer(20, unit="ns")


async def writer(env: Env, n: int, gap: float, rng: random.Random) -> None:
    """Offer n beats on the write side with random idle cycles (probability gap)."""
    d = env.dut
    st = env.st
    pending: Optional[Beat] = None
    commit: Optional[Tuple[Beat, int]] = None   # handshake seen, happens at the next edge
    while True:
        await RisingEdge(d.wr_clk)
        t = now_ps()
        if commit is not None:
            beat, epoch = commit
            if epoch == env.epoch:          # no reset since the handshake was seen
                env.expected.append(beat)
                env.wr_times.append(t)
                st.accepted += 1
                st.max_occupancy = max(st.max_occupancy, env.occupancy())
            commit = None
        env.write_in_flight = False
        if pending is None and st.offered >= n:
            break
        if t > env.deadline_ps:
            env.error(f"watchdog: writer stuck, {st.accepted} of {n} beats accepted")
            break
        if pending is None and rng.random() >= gap:
            pending = env.src.next()
            st.offered += 1
        if pending is not None:
            d.s_tdata.value, d.s_tkeep.value = pending[0], pending[1]
            d.s_tlast.value, d.s_tuser.value = pending[2], pending[3]
            d.s_tvalid.value = 1
        else:
            d.s_tvalid.value = 0

        await ReadOnly()
        s_valid, s_ready = int(d.s_tvalid.value), int(d.s_tready.value)
        if env.wr_in_reset or int(d.wr_rst_n.value) == 0:
            if s_ready:
                env.error("s_tready high during write-side reset")
            continue
        occ = env.occupancy()
        if s_ready and occ >= DEPTH:
            env.error(f"overflow risk: s_tready high with {occ} of {DEPTH} slots used")
        if not s_ready and t - env.wr_release_ps >= SYNC_EDGES * env.wr_ps:
            # Count only reads old enough to have crossed to the write side.
            old_reads = bisect.bisect_right(env.rd_times, t - SYNC_EDGES * env.wr_ps)
            if len(env.wr_times) - old_reads < DEPTH:
                env.error(f"s_tready low but only {len(env.wr_times) - old_reads} beats stored "
                          f"counting reads older than {SYNC_EDGES} write clocks")
        if s_valid and not s_ready:
            st.s_ready_low_cycles += 1
        if s_valid and s_ready:
            commit = (pending, env.epoch)
            env.write_in_flight = True
            pending = None
    d.s_tvalid.value = 0
    env.writer_done = True


async def reader(env: Env, ready_fn: Callable[[int], bool]) -> None:
    """Take beats on the read side; m_tready in rd cycle c is ready_fn(c)."""
    d = env.dut
    st = env.st
    pop_epoch: Optional[int] = None          # read seen, happens at the next edge
    hold: Optional[Beat] = None              # output beat that must not change
    cycle = 0
    while True:
        await RisingEdge(d.rd_clk)
        t = now_ps()
        if pop_epoch is not None and pop_epoch == env.epoch:
            env.expected.popleft()
            env.rd_times.append(t)
            st.delivered += 1
            if st.first_out_ps is None:
                st.first_out_ps = t
            st.last_out_ps = t
        pop_epoch = None
        if env.writer_done and not env.write_in_flight and not env.expected:
            break
        if len(st.errors) >= 30 or t > env.deadline_ps:
            env.error(f"watchdog: reader stopped with {len(env.expected)} beats queued, "
                      f"{st.delivered} delivered")
            break
        d.m_tready.value = int(ready_fn(cycle))
        cycle += 1

        await ReadOnly()
        m_valid, m_ready = int(d.m_tvalid.value), int(d.m_tready.value)
        if env.rd_in_reset or int(d.rd_rst_n.value) == 0:
            if m_valid:
                env.error("m_tvalid high during read-side reset")
            hold = None
            continue
        out: Optional[Beat] = None
        if m_valid:
            out = (int(d.m_tdata.value), int(d.m_tkeep.value),
                   int(d.m_tlast.value), int(d.m_tuser.value))
            if env.occupancy() <= 0:
                env.error(f"underflow: m_tvalid high with nothing written (output {fmt(out)})")
            elif out != env.expected[0]:
                env.error(f"beat #{st.delivered + 1} wrong: got {fmt(out)}, want {fmt(env.expected[0])}")
        elif t - env.rd_release_ps >= SYNC_EDGES * env.rd_ps:
            # Writes made while the read side was still in reset are only seen
            # after its synchronizer runs again, hence the release condition.
            old_writes = bisect.bisect_right(env.wr_times, t - SYNC_EDGES * env.rd_ps)
            if old_writes - len(env.rd_times) > 0:
                env.error(f"m_tvalid low but {old_writes - len(env.rd_times)} beats were written "
                          f"more than {SYNC_EDGES} read clocks ago")
        if hold is not None and out != hold:
            env.error(f"output changed while stalled: {fmt(hold)} -> {fmt(out)}")
        hold = out if (m_valid and not m_ready) else None
        if m_valid and m_ready:
            pop_epoch = env.epoch


async def run_scenario(env: Env, name: str, n: int, gap: float, stall: float,
                       seed: int, ready_fn: Optional[Callable[[int], bool]] = None,
                       resets: Tuple[Tuple[int, int], ...] = ()) -> Stats:
    """Run one writer/reader pair to completion and log its numbers.

    stall    chance that m_tready is low in a read cycle (unless ready_fn is given).
    resets   (start in write cycles from the scenario start, hold in read cycles).
    """
    env.st = Stats()
    env.writer_done = False
    # Generous: 50 slow-clock cycles per beat, plus reset time.
    env.deadline_ps = now_ps() + max(env.wr_ps, env.rd_ps) * (50 * n + 2000)
    rng_w, rng_r = random.Random(seed), random.Random(seed + 1)
    if ready_fn is None:
        ready_fn = lambda c: rng_r.random() >= stall  # noqa: E731
    tasks = [cocotb.start_soon(writer(env, n, gap, rng_w)),
             cocotb.start_soon(reader(env, ready_fn))]

    async def resetter() -> None:
        last = 0
        for start, hold in resets:
            for _ in range(start - last):
                await RisingEdge(env.dut.wr_clk)
            last = start
            await env.reset_pulse(hold)

    if resets:
        tasks.append(cocotb.start_soon(resetter()))
    for tk in tasks:
        await tk
    st = env.st
    env.dut._log.info(
        f"{name}: seed={seed} offered={st.offered} accepted={st.accepted} checked={st.delivered} "
        f"lost_to_reset={st.lost_to_reset} max_occupancy={st.max_occupancy} "
        f"s_tready_low_cycles={st.s_ready_low_cycles} errors={len(st.errors)} (simulated)")
    assert not st.errors, f"{name}:\n" + "\n".join(st.errors)
    assert st.accepted == st.delivered + st.lost_to_reset, (
        f"{name}: accepted {st.accepted} != delivered {st.delivered} + lost {st.lost_to_reset}")
    return st


# ---- Tests ---------------------------------------------------------------------
# (input idle probability, output stall probability) for the clock-ratio tests.
MIXES = [(0.0, 0.0), (0.5, 0.0), (0.0, 0.5), (0.3, 0.3), (0.7, 0.1), (0.1, 0.8)]
BEATS_PER_MIX = 1000


async def ratio_test(dut, idx: int) -> None:
    """Shared body of the clock-ratio tests: six traffic mixes at one ratio."""
    name, wr_ps, rd_ps = RATIOS[idx]
    seed = SEED + 1000 * (idx + 1)
    env = Env(dut, wr_ps, rd_ps, random.Random(seed))
    await env.start()
    checked, filled, rate = 0, False, 0.0
    for j, (gap, stall) in enumerate(MIXES):
        st = await run_scenario(env, f"{name}_gap{int(gap * 100)}_stall{int(stall * 100)}",
                                BEATS_PER_MIX, gap, stall, seed + 10 * j)
        checked += st.delivered
        filled = filled or st.max_occupancy == DEPTH
        if gap == 0.0 and stall == 0.0:
            # Long-run rate on the output vs the slower clock (one beat per cycle).
            span = st.last_out_ps - st.first_out_ps
            rate = (st.delivered - 1) * max(wr_ps, rd_ps) / span
            dut._log.info(f"{name}: throughput (simulated) {st.delivered} beats, "
                          f"{rate:.3f} beats per cycle of the slower clock")
    await env.stop()
    dut._log.info(f"ratio {name} (write {1e6 / wr_ps:.2f} MHz, read {1e6 / rd_ps:.2f} MHz): "
                  f"{checked} beats checked, 0 errors (simulated, seed={seed})")
    assert rate >= 0.98, f"{name}: full-speed rate {rate:.3f} beats per slow-clock cycle, expected about 1"
    assert filled, f"{name}: the FIFO never filled up; the test is too weak"


@cocotb.test()
async def test_ratio_wr_faster(dut) -> None:
    """Write clock 2.5x the read clock: the reader is the bottleneck, FIFO fills."""
    await ratio_test(dut, 0)


@cocotb.test()
async def test_ratio_rd_faster(dut) -> None:
    """Read clock 2.5x the write clock: the FIFO runs near empty."""
    await ratio_test(dut, 1)


@cocotb.test()
async def test_ratio_equal(dut) -> None:
    """Same frequency on both sides, random phase."""
    await ratio_test(dut, 2)


@cocotb.test()
async def test_ratio_7to5(dut) -> None:
    """Write : read frequency 7 : 5, a non-integer ratio."""
    await ratio_test(dut, 3)


@cocotb.test()
async def test_ratio_5to7(dut) -> None:
    """Write : read frequency 5 : 7, a non-integer ratio."""
    await ratio_test(dut, 4)


@cocotb.test()
async def test_ratio_chip(dut) -> None:
    """The chip's clocks: net_clk 156.25 MHz writes, core_clk 200 MHz reads."""
    await ratio_test(dut, 5)


@cocotb.test()
async def test_fill_to_full_then_drain(dut) -> None:
    """For every ratio: block the reader, offer beats every write cycle until the
    FIFO is full (exactly DEPTH beats taken, then s_tready stays low), then let the
    reader drain everything; the FIFO must end empty with s_tready high."""
    for idx, (name, wr_ps, rd_ps) in enumerate(RATIOS):
        seed = SEED + 7000 + idx
        env = Env(dut, wr_ps, rd_ps, random.Random(seed))
        await env.start()
        n = DEPTH + 8
        # Reader blocked for long enough that the writer fills the FIFO and then
        # waits for many cycles; afterwards it is always ready.
        blocked_rd = (DEPTH + 40) * max(wr_ps, rd_ps) // rd_ps
        st = await run_scenario(env, f"fill_drain_{name}", n, 0.0, 0.0, seed,
                                ready_fn=lambda c, b=blocked_rd: c >= b)
        assert st.max_occupancy == DEPTH, f"{name}: max occupancy {st.max_occupancy}, expected {DEPTH}"
        assert st.s_ready_low_cycles >= 20, f"{name}: s_tready low for only {st.s_ready_low_cycles} cycles"
        assert st.delivered == n
        for _ in range(8):
            await RisingEdge(dut.rd_clk)
        await ReadOnly()
        assert int(dut.m_tvalid.value) == 0, f"{name}: m_tvalid high after the drain"
        assert int(dut.s_tready.value) == 1, f"{name}: s_tready low after the drain"
        await env.stop()


@cocotb.test()
async def test_reset_mid_stream(dut) -> None:
    """For every ratio: both resets hit together while traffic flows (output
    stalled half the time, so the FIFO holds beats), three times per ratio,
    released in random order on each side's own clock. After each reset only
    beats sent afterwards come out, in order; stored beats are discarded."""
    lost_total = 0
    for idx, (name, wr_ps, rd_ps) in enumerate(RATIOS):
        seed = SEED + 8000 + idx
        rng = random.Random(seed)
        env = Env(dut, wr_ps, rd_ps, rng)
        await env.start()
        resets = tuple((40 + 60 * k + rng.randrange(20), 1 + rng.randrange(6)) for k in range(3))
        st = await run_scenario(env, f"reset_{name}", 600, 0.2, 0.5, seed, resets=resets)
        lost_total += st.lost_to_reset
        await env.stop()
    dut._log.info(f"reset_mid_stream: beats discarded by resets in total: {lost_total} (simulated)")
    assert lost_total > 0, "no reset ever hit a non-empty FIFO; the test is too weak"


# ---- Stand-alone runner --------------------------------------------------------
def main() -> int:
    """Build with Icarus and run the cocotb tests; 0 only if all pass."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from cocotb_run import run_cocotb

    print(f"test_async_fifo: seed={SEED}")
    return run_cocotb("async_fifo", ["rtl/async_fifo.sv"], "test_async_fifo", SEED)


if __name__ == "__main__":
    sys.exit(main())
