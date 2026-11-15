"""Shared cocotb helpers for the top-level tests of neurogate_top (all *simulated*).

Used by tb/neurogate_top_checks.py (smoke), tb/neurogate_top_full_checks.py (full
generated traffic) and tb/neurogate_top_stress_checks.py (bad packets, backpressure,
overflow, resets). This module holds no test, so cocotb does not collect anything
from it.

What is here
    Chip           clocks at a random phase, power-up and hard reset, a result-stream
                   monitor with a controllable ready, and an AXI-Lite master
    send_frame     drives one frame as 8-byte beats on net_clk, with optional stalls
    model_frames   the Python models' answer for each frame (bit-exact reference)
    model_counters counters the models expect for a given list of result beats

Clocks: net_clk 6.4 ns (156.25 MHz), core_clk 5.0 ns (200 MHz) started at a random
odd number of picoseconds, so the two clocks never line up in a fixed pattern.
"""
import os
import random
import sys
from typing import Dict, List, Optional, Tuple

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ReadOnly, RisingEdge, Timer
from cocotb.utils import get_sim_time

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
NET_PS, CORE_PS = 6400, 5000
PACE_CLASSIFY = 170            # net clocks after a classified frame (> one inference, ~190 core clocks)
PACE_DROP = 2                  # net clocks after a frame the chip drops (no inference needed)

# Register offsets (docs/regmap.md)
CTRL, CONF_THRESH = 0x00, 0x04
COUNTERS = {"PKT_NORMAL": 0x08, "PKT_SCAN": 0x0C, "PKT_SYN": 0x10, "BYTES_NORMAL": 0x14,
            "BYTES_SCAN": 0x18, "BYTES_SYN": 0x1C, "PKT_DROPPED": 0x20, "PKT_LOWCONF": 0x24,
            "LAST_RESULT": 0x28, "PKT_OVERFLOW": 0x2C}
CLASS_NAMES = ("NORMAL", "SCAN", "SYN")
Beat = Tuple[int, int, int, int, int]      # pkt_id, pkt_len, class, confidence, low_conf


def read_frames(path: str) -> List[Tuple[bytes, bool]]:
    """Read a '<tuser> <hex>' file (one frame per line) written by a test_*.py runner."""
    out = []
    with open(path) as f:
        for line in f:
            t, h = line.split()
            out.append((bytes.fromhex(h), t == "1"))
    return out


def read_thresh() -> int:
    """The CONF_THRESH value the model was tuned with (data/model/conf_thresh.txt)."""
    with open(THRESH_FILE) as fh:
        return int(fh.read().split()[0])


class AxiLite:
    """Minimal AXI-Lite master on core_clk. Handshakes are judged on the settled
    values just before a clock edge (ReadOnly), then the edge is awaited."""

    def __init__(self, dut) -> None:
        """Keep the handle and drive an idle bus."""
        self.dut = dut
        self.last_accept_ps = 0          # sim time of the edge that accepted the last write
        for n in ("awvalid", "wvalid", "arvalid", "awaddr", "araddr", "wdata", "wstrb"):
            getattr(dut, f"s_axil_{n}").value = 0
        dut.s_axil_bready.value = 1
        dut.s_axil_rready.value = 1

    async def write(self, addr: int, data: int) -> int:
        """One write with all byte lanes; returns BRESP. ``last_accept_ps`` is set to
        the time of the core_clk edge at which the write was accepted."""
        d = self.dut
        d.s_axil_awaddr.value, d.s_axil_wdata.value, d.s_axil_wstrb.value = addr, data, 0xF
        d.s_axil_awvalid.value, d.s_axil_wvalid.value = 1, 1
        for _ in range(100):
            await ReadOnly()
            acc = int(d.s_axil_awready.value) and int(d.s_axil_wready.value)
            await RisingEdge(d.core_clk)
            if acc:
                self.last_accept_ps = get_sim_time("ps")
                break
        else:
            raise AssertionError("AXI-Lite write never accepted")
        d.s_axil_awvalid.value, d.s_axil_wvalid.value = 0, 0
        for _ in range(100):
            await ReadOnly()
            bv, resp = int(d.s_axil_bvalid.value), int(d.s_axil_bresp.value)
            await RisingEdge(d.core_clk)
            if bv:
                return resp
        raise AssertionError("no AXI-Lite write response")

    async def read(self, addr: int) -> Tuple[int, int]:
        """One read; returns (RDATA, RRESP)."""
        d = self.dut
        d.s_axil_araddr.value, d.s_axil_arvalid.value = addr, 1
        for _ in range(100):
            await ReadOnly()
            acc = int(d.s_axil_arready.value)
            await RisingEdge(d.core_clk)
            if acc:
                break
        else:
            raise AssertionError("AXI-Lite read never accepted")
        d.s_axil_arvalid.value = 0
        for _ in range(100):
            await ReadOnly()
            rv = int(d.s_axil_rvalid.value)
            data, resp = int(d.s_axil_rdata.value), int(d.s_axil_rresp.value)
            await RisingEdge(d.core_clk)
            if rv:
                return data, resp
        raise AssertionError("no AXI-Lite read response")

    async def counters(self) -> Dict[str, int]:
        """Read every counter register; each must answer OKAY."""
        out = {}
        for name, a in COUNTERS.items():
            v, resp = await self.read(a)
            assert resp == 0, f"{name}: RRESP {resp}"
            out[name] = v
        return out


class _ResetAwareErrors(list):
    """Error list that ignores messages raised while the chip is in reset.

    A reset that arrives between clock edges makes valid fall without a stall rule
    being broken; the stall check of the shared monitor must not report that."""

    def __init__(self, rst_signal) -> None:
        """Remember the reset signal to look at."""
        super().__init__()
        self._rst = rst_signal

    def append(self, item) -> None:
        """Store the message only if the core-domain reset is released."""
        if int(self._rst.value) == 1:
            super().append(item)


class Chip:
    """The device under test with its clocks, result monitor and AXI-Lite master."""

    def __init__(self, dut, rng: random.Random) -> None:
        """Keep the handles; nothing is driven until power_up()."""
        self.dut, self.rng = dut, rng
        self.bus: Optional[AxiLite] = None
        self.phase = 0
        self.p_low = 0.3               # chance per core clock that result tready is low
        self.force_low = False         # True: tready held low (a long stall)
        self.beats: List[Beat] = []
        self.beat_ps: List[int] = []   # sim time (ps) of the core_clk edge that took each beat
        self.errors = _ResetAwareErrors(dut.core_rst_n)
        self._tasks: list = []
        self.sent = 0                  # frames fully sent by send_paced()
        self.sent_classified = 0       # of those, frames the models classify

    async def power_up(self, hold_ps: int = 50_000) -> None:
        """Idle inputs, reset asserted, clocks started (core clock at a random odd
        phase), reset released at a random time; then 10 clocks of quiet."""
        d = self.dut
        d.rst_n.value = 0
        for sig in (d.s_axis_tvalid, d.s_axis_tdata, d.s_axis_tkeep, d.s_axis_tlast,
                    d.s_axis_tuser):
            sig.value = 0
        d.m_axis_res_tready.value = 1
        self.bus = AxiLite(d)
        self.phase = self.rng.randrange(1, CORE_PS, 2)
        cocotb.start_soon(Clock(d.net_clk, NET_PS, unit="ps").start())
        await Timer(self.phase, unit="ps")
        cocotb.start_soon(Clock(d.core_clk, CORE_PS, unit="ps").start())
        await Timer(hold_ps + self.rng.randrange(1, 7_000), unit="ps")
        d.rst_n.value = 1
        for _ in range(10):
            await RisingEdge(d.core_clk)

    def start_monitor(self, drive_ready: bool = True) -> None:
        """Start the result-stream monitor and (unless drive_ready is False, when the
        test drives tready itself) the random-ready driver."""
        self._tasks = ([cocotb.start_soon(self._ready_driver())] if drive_ready else []) + [
                       cocotb.start_soon(monitor_handshake(
                           self.dut.core_clk, self.dut.core_rst_n, self.dut.m_axis_res_tvalid,
                           self.dut.m_axis_res_tready, {"tdata": self.dut.m_axis_res_tdata},
                           self._on_beat, self.errors, "result stream"))]

    def stop_monitor(self) -> None:
        """Stop both tasks and leave tready high."""
        for t in self._tasks:
            t.cancel()
        self._tasks = []
        self.dut.m_axis_res_tready.value = 1

    async def _ready_driver(self) -> None:
        """Drive result tready: held low while force_low, else low at random."""
        while True:
            await RisingEdge(self.dut.core_clk)
            low = self.force_low or self.rng.random() < self.p_low
            self.dut.m_axis_res_tready.value = 0 if low else 1

    def _on_beat(self, v: Dict[str, int]) -> None:
        """Record one accepted result beat. The shared monitor judges a beat at the edge it moves at, so the time now is that edge."""
        if int(self.dut.core_rst_n.value) == 0:
            return                      # reset hit between the edges: nothing was transferred
        t = v["tdata"]
        assert (t >> 3) & 0x1F == 0, f"result bits [7:3] not zero: {t:#x}"
        assert (t >> 15) & 1 == 0, f"confidence above 127: {t:#x}"
        self.beats.append(((t >> 32) & 0xFFFF, (t >> 16) & 0xFFFF, t & 3, (t >> 8) & 0xFF,
                           (t >> 2) & 1))
        self.beat_ps.append(get_sim_time("ps"))

    def clear_beats(self) -> None:
        """Forget the beats and errors seen so far (used after a reset)."""
        self.beats, self.beat_ps = [], []
        # Clear in place: the running monitor holds a reference to this very list,
        # so a new list would leave the monitor writing where nobody looks.
        self.errors.clear()

    async def hard_reset(self, low_ps: int) -> None:
        """Pull the external rst_n low for low_ps (random phase against both clocks:
        the time is even, so it never lands on a core edge, and is moved off a net
        edge), then release it. rst_n is asynchronous to both clocks."""
        if low_ps % NET_PS == 0:
            low_ps += 2
        self.dut.rst_n.value = 0
        await Timer(low_ps, unit="ps")
        self.dut.rst_n.value = 1

    async def drain(self, want: int, max_core_clocks: int = 40000) -> None:
        """Wait until `want` result beats are seen (or the time limit), then 400 more
        clocks so an extra, unexpected beat would show up."""
        for _ in range(max_core_clocks):
            if len(self.beats) >= want:
                break
            await RisingEdge(self.dut.core_clk)
        for _ in range(400):
            await RisingEdge(self.dut.core_clk)


async def send_frame(dut, rng: random.Random, frame: bytes, tuser: bool,
                     stall_p: float = 0.0, progress: Optional[list] = None) -> float:
    """Drive one frame as 8-byte beats on net_clk. Unused lanes carry garbage and
    tuser carries noise outside the last beat (both must be ignored). With
    stall_p > 0, tvalid goes low for 1..3 net clocks before a beat with that chance
    (an input stall). `progress`, if given, gets the number of beats taken so far
    (so a test can see where a frame was when it cut it off). Returns the
    simulation time (ns) of the clock edge that took the last beat."""
    nbeats = (len(frame) + BYTES_PER_BEAT - 1) // BYTES_PER_BEAT
    pad_mode = rng.randrange(3)
    for b in range(nbeats):
        if stall_p and rng.random() < stall_p:
            dut.s_axis_tvalid.value = 0
            for _ in range(rng.randrange(1, 4)):
                await RisingEdge(dut.net_clk)
        chunk = frame[b * BYTES_PER_BEAT:(b + 1) * BYTES_PER_BEAT]
        last = b == nbeats - 1
        npad = BYTES_PER_BEAT - len(chunk)
        # Unused lanes: random, all zero, or 0x45 (what an IPv4 header starts with). A
        # chip that wrongly reads a lane tkeep marks unused must be fooled sometimes.
        pad = bytes(rng.randrange(256) for _ in range(npad)) if pad_mode == 0 else \
            bytes([0x00 if pad_mode == 1 else 0x45]) * npad
        dut.s_axis_tdata.value = int.from_bytes(chunk + pad, "little")
        dut.s_axis_tkeep.value = (1 << len(chunk)) - 1
        dut.s_axis_tlast.value = int(last)
        dut.s_axis_tuser.value = int(tuser) if last else rng.randrange(2)
        dut.s_axis_tvalid.value = 1
        for _ in range(20000):
            await ReadOnly()
            fire = int(dut.s_axis_tready.value) == 1
            await RisingEdge(dut.net_clk)
            if fire:
                break
        else:
            raise AssertionError("deadlock: s_axis_tready low while sending")
        if progress is not None:
            progress.append(b + 1)
    dut.s_axis_tvalid.value = 0
    return get_sim_time("ns")


def model_frames(frames: List[Tuple[bytes, bool]], thresh: int) -> List[Optional[Beat]]:
    """For each frame: the result beat the chip must produce, or None when the chip
    must drop the frame (no result beat). From model/features.py run_stream and the
    golden model model/golden.py infer (source of truth for the arithmetic)."""
    p = golden.load_params(WEIGHTS)
    out: List[Optional[Beat]] = []
    for r in F.run_stream(frames):
        if r.kind == F.KIND_CLASSIFY:
            cls, conf, low, _, _ = golden.infer(list(r.features), p, thresh)
            out.append((r.pkt_id, r.pkt_len, cls, conf, int(low)))
        else:
            out.append(None)
    return out


def model_counters(beats: List[Beat], dropped: int, overflow: int = 0) -> Dict[str, int]:
    """Counter values the models expect after these result beats (in order), this
    many dropped frames and this many frames lost to overflow (docs/regmap.md)."""
    cnt = {k: 0 for k in COUNTERS}
    for pkt_id, pkt_len, cls, conf, low in beats:
        cnt[f"PKT_{CLASS_NAMES[cls]}"] += 1
        cnt[f"BYTES_{CLASS_NAMES[cls]}"] += pkt_len
        cnt["PKT_LOWCONF"] += low
        cnt["LAST_RESULT"] = (conf << 8) | cls
    cnt["PKT_DROPPED"] = dropped
    cnt["PKT_OVERFLOW"] = overflow
    return cnt


def frame_sum(cnt: Dict[str, int]) -> int:
    """Sum of the five frame counters (equals frames received after a drain)."""
    return (cnt["PKT_NORMAL"] + cnt["PKT_SCAN"] + cnt["PKT_SYN"] + cnt["PKT_DROPPED"]
            + cnt["PKT_OVERFLOW"])


async def send_paced(chip: Chip, frames: List[Tuple[bytes, bool]],
                     want: List[Optional[Beat]], stall_p: float = 0.0,
                     max_inflight: int = 0) -> None:
    """Send frames one after another, paced so the async FIFO cannot overflow.

    After a frame the models say will be classified the sender waits PACE_CLASSIFY
    net clocks (a little more than one inference); after a dropped frame, 2.
    With max_inflight > 0 it also waits while that many classified frames are
    still waiting for their result beat (used with long result-stream stalls).
    Updates chip.sent (frames fully sent) and chip.sent_classified."""
    chip.sent, chip.sent_classified = 0, 0
    for n, (frame, tuser) in enumerate(frames):
        waited = 0
        while max_inflight and chip.sent_classified - len(chip.beats) >= max_inflight:
            await RisingEdge(chip.dut.net_clk)
            waited += 1
            assert waited < 100_000, "result beats never arrived (a beat was lost or stuck)"
        await send_frame(chip.dut, chip.rng, frame, tuser, stall_p)
        chip.sent += 1
        if want[n] is not None:
            chip.sent_classified += 1
        for _ in range(PACE_CLASSIFY if want[n] is not None else PACE_DROP):
            await RisingEdge(chip.dut.net_clk)
