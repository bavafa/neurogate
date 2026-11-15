"""cocotb tests for rtl/skid_fifo.sv (AXI-Stream skid FIFO, SPEC section 3.1).

Run: `python3 tb/test_skid_fifo.py` from .venv. Builds with Icarus and runs the
tests through tb/cocotb_run.py (DECISIONS D-012); exits non-zero on any failure.
The seed comes from the SEED environment variable (default 12345) and is printed.

How the check works
  One loop (run_stream) drives the DUT one clock cycle at a time and watches
  every cycle. It keeps a scoreboard: a queue of the beats the DUT has
  accepted and not yet delivered. Every beat is {tdata, tkeep, tlast, tuser};
  the low 32 bits of tdata carry a running sequence number, so a lost,
  duplicated or reordered beat is easy to see. Per cycle it checks:
    * every beat that leaves the DUT equals the oldest queued beat (nothing
      lost, duplicated, reordered or altered, all four fields);
    * m_tvalid is high exactly when the queue is not empty, and s_tready is
      high exactly when the queue holds fewer than DEPTH beats (an exact model
      of the FIFO fill level, so a full/empty error shows up at once);
    * while m_tvalid = 1 and m_tready = 0, the output holds the same beat in
      the next cycle (AXI-Stream stability rule).
  The input driver also follows the AXI-Stream rule: once s_tvalid is high the
  beat is held until s_tready is seen high.
  Reset: when rst_n is low the queue is cleared (beats stored at that moment
  are discarded by design); the output must be empty afterwards, and later
  beats must come out as new beats only.

Tests: always-ready full-rate throughput; random input gaps; random output
tready; both at once (several probabilities); long stalls (output blocked for
hundreds of cycles, then input starved); reset in the middle of a stream.
"""
import os
import random
import sys
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, List, Optional, Sequence, Tuple

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ReadOnly, RisingEdge

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

SEED = int(os.environ.get("SEED", "12345"))
CLK_PERIOD_NS = 6.4   # 156.25 MHz net_clk; only matters for waveforms
DEPTH = 4             # default DEPTH of rtl/skid_fifo.sv (the runner uses the default)
BYTES_PER_BEAT = 8

# One stream beat: (tdata, tkeep, tlast, tuser)
Beat = Tuple[int, int, int, int]

# Beats compared against the scoreboard over the whole run (printed at the end).
TOTAL_CHECKED = 0


@dataclass
class Stats:
    """What one run_stream call saw (all numbers are simulated clock cycles / beats)."""

    cycles: int = 0
    offered: int = 0                 # beats the source tried to send
    accepted: int = 0                # beats the DUT took in
    delivered: int = 0               # beats checked on the output
    lost_to_reset: int = 0           # beats stored in the DUT when a reset hit
    max_occupancy: int = 0
    first_accept: Optional[int] = None
    last_accept: int = 0
    first_out: Optional[int] = None
    last_out: int = 0
    s_ready_low_cycles: int = 0      # cycles with a beat offered but s_tready low
    errors: List[str] = field(default_factory=list)


class BeatSource:
    """Makes frames of 1-5 beats with a running sequence number in tdata[31:0].

    tkeep follows SPEC 3.1 (all ones, contiguous from bit 0 on the last beat);
    tuser is random on every beat (the FIFO must carry it per beat)."""

    def __init__(self, rng: random.Random) -> None:
        """Start a new source; the sequence number never repeats across resets."""
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


def fmt(b: Beat) -> str:
    """Short text for a beat, for error messages."""
    return f"data={b[0]:016x} keep={b[1]:02x} last={b[2]} user={b[3]}"


async def setup(dut) -> None:
    """Start the clock, idle the inputs and apply a 4-cycle reset."""
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
    dut.s_tvalid.value = 0
    dut.s_tdata.value = 0
    dut.s_tkeep.value = 0
    dut.s_tlast.value = 0
    dut.s_tuser.value = 0
    dut.m_tready.value = 1
    dut.rst_n.value = 0
    for _ in range(4):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1


async def finish(dut) -> None:
    """Idle the inputs and wait two clock edges so no signal write is still
    queued when the test ends (this cocotb/Icarus build segfaults at shutdown
    otherwise; DECISIONS D-012)."""
    await RisingEdge(dut.clk)
    dut.s_tvalid.value = 0
    dut.m_tready.value = 1
    await RisingEdge(dut.clk)


async def run_stream(dut, rng: random.Random, n_beats: int, gap_prob: float,
                     ready_fn: Callable[[int], bool],
                     resets: Sequence[Tuple[int, int]] = (),
                     max_cycles: int = 200000) -> Stats:
    """Drive n_beats through the DUT and check every cycle (see module docstring).

    gap_prob   chance of an idle (s_tvalid low) cycle before each new beat.
    ready_fn   cycle number -> value of m_tready in that cycle.
    resets     (start_cycle, length) pairs: rst_n is held low for that many cycles.
    The run ends when every offered beat has been delivered or discarded by a
    reset (or fails after max_cycles).
    """
    global TOTAL_CHECKED
    st = Stats()
    src = BeatSource(rng)
    expected: Deque[Beat] = deque()
    pending: Optional[Beat] = None       # beat currently held on the input
    prev_hold: Optional[Beat] = None     # output beat that must not change
    prev_rst = False
    cycle = 0

    def in_reset(c: int) -> bool:
        return any(a <= c < a + n for a, n in resets)

    while cycle < max_cycles:
        await RisingEdge(dut.clk)
        rst = in_reset(cycle)
        dut.rst_n.value = 0 if rst else 1
        dut.m_tready.value = int(ready_fn(cycle))
        # Source: keep a pending beat until accepted; else maybe start a new one.
        if pending is None and st.offered < n_beats and rng.random() >= gap_prob:
            pending = src.next()
            st.offered += 1
        if pending is not None:
            dut.s_tdata.value, dut.s_tkeep.value = pending[0], pending[1]
            dut.s_tlast.value, dut.s_tuser.value = pending[2], pending[3]
            dut.s_tvalid.value = 1
        else:
            dut.s_tvalid.value = 0

        await ReadOnly()
        s_valid, s_ready = int(dut.s_tvalid.value), int(dut.s_tready.value)
        m_valid, m_ready = int(dut.m_tvalid.value), int(dut.m_tready.value)
        out: Optional[Beat] = None
        if m_valid:
            out = (int(dut.m_tdata.value), int(dut.m_tkeep.value),
                   int(dut.m_tlast.value), int(dut.m_tuser.value))

        if rst:
            # Reset takes effect at the next edge; whatever is stored is lost by design.
            st.lost_to_reset += len(expected)
            expected.clear()
            if prev_rst and m_valid:
                st.errors.append(f"cycle {cycle}: m_tvalid high while in reset")
            if pending is not None:      # the source gives up the held beat
                pending = None
            prev_hold = None
            prev_rst = True
            cycle += 1
            if cycle >= max(a + n for a, n in resets) and st.offered >= n_beats:
                break
            continue
        prev_rst = False

        # ---- Fill-level model: exact valid and ready --------------------------
        if m_valid != int(len(expected) > 0):
            st.errors.append(f"cycle {cycle}: m_tvalid={m_valid} but {len(expected)} beats queued")
        if s_ready != int(len(expected) < DEPTH):
            st.errors.append(f"cycle {cycle}: s_tready={s_ready} with {len(expected)} of {DEPTH} slots used")
        # ---- AXI-Stream stability on the output -------------------------------
        if prev_hold is not None and out != prev_hold:
            st.errors.append(f"cycle {cycle}: output changed while stalled: "
                             f"{fmt(prev_hold)} -> {fmt(out) if out else 'tvalid low'}")
        prev_hold = out if (m_valid and not m_ready) else None

        # ---- Transfers happen at the next clock edge ---------------------------
        if m_valid and m_ready and expected:
            want = expected.popleft()
            st.delivered += 1
            TOTAL_CHECKED += 1
            if out != want:
                st.errors.append(f"cycle {cycle}: beat #{st.delivered} wrong: got {fmt(out)}, want {fmt(want)}")
            if st.first_out is None:
                st.first_out = cycle
            st.last_out = cycle
        if s_valid and not s_ready:
            st.s_ready_low_cycles += 1
        if s_valid and s_ready:
            expected.append(pending)
            pending = None
            st.accepted += 1
            if st.first_accept is None:
                st.first_accept = cycle
            st.last_accept = cycle
        st.max_occupancy = max(st.max_occupancy, len(expected))
        cycle += 1
        if st.offered >= n_beats and pending is None and not expected:
            break
        if len(st.errors) > 20:
            break
    else:
        st.errors.append(f"timeout: {st.delivered} of {n_beats} beats delivered in {max_cycles} cycles")
    st.cycles = cycle
    await finish(dut)
    return st


def report(dut, name: str, st: Stats) -> None:
    """Log one line per run, and fail on the first scoreboard problems."""
    dut._log.info(f"{name}: seed={SEED} offered={st.offered} accepted={st.accepted} "
                  f"checked={st.delivered} cycles={st.cycles} max_occupancy={st.max_occupancy} "
                  f"lost_to_reset={st.lost_to_reset} errors={len(st.errors)} (simulated)")
    # Running total, a plain log line (not a test): the last one in the log is
    # the number of output beats the scoreboard checked in the whole run.
    dut._log.info(f"scoreboard running total: {TOTAL_CHECKED} output beats checked "
                  f"(seed={SEED}, DEPTH={DEPTH}), simulated")
    assert not st.errors, "\n".join(st.errors[:20])
    assert st.accepted == st.delivered + st.lost_to_reset, (
        f"{name}: accepted {st.accepted} != delivered {st.delivered} + lost to reset {st.lost_to_reset}")


# ---- Tests ---------------------------------------------------------------------
@cocotb.test()
async def test_throughput_always_ready(dut) -> None:
    """Output always ready, input offered every cycle: one beat in and one beat out
    per clock, 1 cycle latency. Simulated; no gaps, no stalls."""
    await setup(dut)
    n = 4000
    st = await run_stream(dut, random.Random(SEED), n, 0.0, lambda c: True)
    report(dut, "throughput", st)
    window_in = st.last_accept - st.first_accept + 1
    window_out = st.last_out - st.first_out + 1
    latency = st.first_out - st.first_accept
    dut._log.info(f"throughput (simulated): {st.accepted} beats accepted in {window_in} cycles "
                  f"({st.accepted / window_in:.3f} beats/cycle), {st.delivered} delivered in "
                  f"{window_out} cycles ({st.delivered / window_out:.3f} beats/cycle), "
                  f"latency {latency} cycle, s_tready low cycles {st.s_ready_low_cycles}, "
                  f"max occupancy {st.max_occupancy}")
    assert st.accepted == n and st.delivered == n
    assert window_in == n, f"input took {window_in} cycles for {n} beats (a bubble)"
    assert window_out == n, f"output took {window_out} cycles for {n} beats (a bubble)"
    assert st.s_ready_low_cycles == 0, "s_tready dropped while the output was always ready"
    assert latency == 1, f"latency {latency} cycles, expected 1"
    assert st.max_occupancy == 1, f"steady state should use 1 slot, saw {st.max_occupancy}"


@cocotb.test()
async def test_input_gaps_ready_always(dut) -> None:
    """Random tvalid gaps on the input (20%, 50%, 80% idle), output always ready."""
    await setup(dut)
    for i, gap in enumerate((0.2, 0.5, 0.8)):
        st = await run_stream(dut, random.Random(SEED + 10 + i), 1500, gap, lambda c: True)
        report(dut, f"gaps_{int(gap * 100)}", st)
        assert st.max_occupancy == 1, "a free-running output should never hold more than one beat"


@cocotb.test()
async def test_random_tready(dut) -> None:
    """Random tready on the output (20%, 50%, 90% stalled), input offered every cycle.
    The FIFO must fill up and push back on the source without losing a beat."""
    await setup(dut)
    for i, stall in enumerate((0.2, 0.5, 0.9)):
        rng = random.Random(SEED + 20 + i)
        st = await run_stream(dut, random.Random(SEED + 30 + i), 2000, 0.0,
                              lambda c, r=rng, s=stall: r.random() >= s)
        report(dut, f"tready_stall_{int(stall * 100)}", st)
        assert st.max_occupancy == DEPTH, f"FIFO never filled (max {st.max_occupancy}); the test is too weak"
        assert st.s_ready_low_cycles > 0


@cocotb.test()
async def test_random_both(dut) -> None:
    """Random input gaps and random output stalls together, 6 probability pairs."""
    await setup(dut)
    pairs = [(0.1, 0.1), (0.3, 0.5), (0.5, 0.3), (0.5, 0.5), (0.7, 0.7), (0.0, 0.5)]
    for i, (gap, stall) in enumerate(pairs):
        rng = random.Random(SEED + 40 + i)
        st = await run_stream(dut, random.Random(SEED + 50 + i), 3000, gap,
                              lambda c, r=rng, s=stall: r.random() >= s)
        report(dut, f"both_gap{int(gap * 100)}_stall{int(stall * 100)}", st)


@cocotb.test()
async def test_long_stall(dut) -> None:
    """Output blocked for 300 cycles while the source keeps offering beats: the FIFO
    takes exactly DEPTH beats and then pushes back, nothing is lost. Then a long
    input pause (output ready) drains it, and a second long stall repeats the cycle."""
    await setup(dut)
    # tready low in cycles 5..304 and 700..999; high otherwise. Input has a long
    # pause too (gap 0.98 makes most cycles idle) in the second half.
    stall_windows = [(5, 305), (700, 1000)]
    ready = lambda c: not any(a <= c < b for a, b in stall_windows)  # noqa: E731
    st = await run_stream(dut, random.Random(SEED + 60), 1200, 0.0, ready)
    report(dut, "long_stall", st)
    assert st.max_occupancy == DEPTH
    assert st.s_ready_low_cycles >= 250, f"only {st.s_ready_low_cycles} cycles of pushback in 300 stalled cycles"
    # Second part: a very sparse input after a stall.
    st2 = await run_stream(dut, random.Random(SEED + 61), 300, 0.98, lambda c: c > 40)
    report(dut, "long_starve", st2)


@cocotb.test()
async def test_reset_mid_stream(dut) -> None:
    """Reset hits while traffic is flowing: while empty, half full, full and with
    the output stalled. After each reset the FIFO is empty (m_tvalid low), and only
    beats sent after the reset come out, in order. Beats stored at the reset are
    discarded by design and are counted, not compared."""
    await setup(dut)
    lost_total = 0
    # (reset start cycle, reset length, tready stall probability)
    plans = [(3, 1, 0.0), (20, 1, 0.0), (37, 3, 0.5), (50, 2, 0.9), (64, 5, 1.0), (101, 1, 0.7)]
    for i, (start, length, stall) in enumerate(plans):
        rng = random.Random(SEED + 70 + i)
        st = await run_stream(dut, random.Random(SEED + 80 + i), 600, 0.1,
                              lambda c, r=rng, s=stall: (r.random() >= s) if c < start + length + 40 else True,
                              resets=[(start, length), (start + 200, 2)])
        report(dut, f"reset_{i}_at{start}_len{length}_stall{int(stall * 100)}", st)
        lost_total += st.lost_to_reset
    dut._log.info(f"reset_mid_stream: beats discarded by resets in total: {lost_total}")
    assert lost_total > 0, "no reset ever hit a non-empty FIFO; the test is too weak"


# ---- Stand-alone runner --------------------------------------------------------
def main() -> int:
    """Build with Icarus and run the cocotb tests; 0 only if all pass."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from cocotb_run import run_cocotb

    print(f"test_skid_fifo: seed={SEED}")
    return run_cocotb("skid_fifo", ["rtl/skid_fifo.sv"], "test_skid_fifo", SEED)


if __name__ == "__main__":
    sys.exit(main())
