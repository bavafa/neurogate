"""cocotb tests for rtl/eth_parser.sv (Ethernet header parser, SPEC section 4).

Run: `python3 tb/test_eth_parser.py` from .venv. It generates traffic with
Scapy, then builds with Icarus and runs every test below through
tb/cocotb_run.py, and exits non-zero on any failure. The seed comes from the SEED environment
variable (default 12345) and is printed in the log.

What is checked, for every frame that goes through:
  * the frame bytes, tkeep, tlast and tuser come out unchanged and in order;
  * m_hdr_done rises on the right beat (beat 1 without a VLAN tag, beat 2 with
    one, never for a frame too short to hold its header), and m_vlan / m_ipv4
    stay constant once it is up;
  * on the tlast beat, m_drop / m_drop_reason match the Python model
    (model/features.py parse_frame); on other beats both are zero.

Tests: hand-built frames (good, VLAN, ARP, IPv6, QinQ, every short length,
tuser), random-byte fuzz frames, ~600 generated frames from
model/traffic_gen.py checked against model/features.py, all with random input
gaps and random output backpressure; and reset in the middle of a frame.
"""
import os
import random
import struct
import sys
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ReadOnly, RisingEdge

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from model import features as F  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from handshake_monitor import monitor_handshake  # noqa: E402

SEED = int(os.environ.get("SEED", "12345"))
BYTES_PER_BEAT = 8
SEND_STALL_LIMIT = 2000  # cycles s_tready may stay low before the test fails
TEST_TIMEOUT_MS = 50     # simulated time per test; a deadlock fails instead of hanging
CLK_PERIOD_NS = 6.4  # 156.25 MHz net_clk; the value only matters for waveforms

# m_drop_reason codes (rtl/eth_parser.sv)
DROP_NONE, DROP_TUSER, DROP_SHORT, DROP_NOT_IPV4 = 0, 1, 2, 3

# Model drop reasons that belong to layer 2, mapped to the RTL code. Any other
# model drop reason is a layer-3/4 problem, so eth_parser must pass the frame.
L2_REASON = {"tuser_error": DROP_TUSER, "short_ethernet": DROP_SHORT,
             "short_vlan": DROP_SHORT, "not_ipv4": DROP_NOT_IPV4}


# ---- Expected values ---------------------------------------------------------
@dataclass
class Expect:
    """What eth_parser must report for one frame."""

    reason: int                 # m_drop_reason on the tlast beat
    hdr_beat: Optional[int]     # first beat with m_hdr_done = 1, None if never
    vlan: bool                  # m_vlan once the header is done
    ipv4: bool                  # m_ipv4 once the header is done


def expect_for(frame: bytes, tuser: bool) -> Expect:
    """Expected parser output. The drop reason comes from the Python model (the
    source of truth); beat timing and flags are derived by hand from SPEC 3.1/4."""
    res = F.parse_frame(frame, tuser)
    reason = L2_REASON.get(res.drop_reason, DROP_NONE)
    etype = (frame[12] << 8 | frame[13]) if len(frame) >= 14 else None
    vlan = etype == 0x8100
    if etype is None or (vlan and len(frame) < 18):
        return Expect(reason, None, False, False)
    inner = (frame[16] << 8 | frame[17]) if vlan else etype
    return Expect(reason, 2 if vlan else 1, vlan, inner == 0x0800)


# ---- Frame builders (struct only, no Scapy) -----------------------------------
def eth(ethertype: int, payload: bytes, vlan: Optional[int] = None) -> bytes:
    """Ethernet frame: fixed MACs, optional one 802.1Q tag, EtherType, payload."""
    hdr = bytes.fromhex("020000000001") + bytes.fromhex("020000000002")
    if vlan is not None:
        hdr += struct.pack("!HH", 0x8100, vlan)
    return hdr + struct.pack("!H", ethertype) + payload


def ipv4_tcp(dport: int = 80) -> bytes:
    """Minimal IPv4 + TCP SYN header (40 bytes)."""
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 40, 1, 0, 64, 6, 0,
                     bytes([10, 0, 0, 1]), bytes([10, 0, 0, 2]))
    tcp = struct.pack("!HHIIBBHHH", 1234, dport, 0, 0, 0x50, 0x02, 0, 0, 0)
    return ip + tcp


# ---- Testbench plumbing ------------------------------------------------------
@dataclass
class OutFrame:
    """One frame as seen on the output, rebuilt from its beats."""

    data: bytearray = field(default_factory=bytearray)
    beats: List[Tuple[int, int, int, int, int, int]] = field(default_factory=list)
    tuser: int = 0
    drop: int = 0
    reason: int = 0


class Env:
    """Drives the input stream, randomizes m_tready and collects output frames."""

    def __init__(self, dut, rng: random.Random, gap_prob: float, stall_prob: float) -> None:
        """Set up handles and random knobs; nothing runs until start()."""
        self.dut, self.rng = dut, rng
        self.gap_prob, self.stall_prob = gap_prob, stall_prob
        self.frames: List[OutFrame] = []
        self.cur = OutFrame()
        self.errors: List[str] = []
        self.tasks: list = []

    async def start(self) -> None:
        """Start the clock, reset the DUT and launch the sink coroutines."""
        dut = self.dut
        cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
        dut.s_tvalid.value = 0
        dut.s_tdata.value = 0
        dut.s_tkeep.value = 0
        dut.s_tlast.value = 0
        dut.s_tuser.value = 0
        dut.m_tready.value = 1
        await self.reset(4)
        self.tasks = [cocotb.start_soon(self._ready_driver()),
                      cocotb.start_soon(self._monitor())]

    async def stop(self) -> None:
        """Stop the background coroutines, then wait one clock edge so that no
        signal write is still queued when the test ends. (Ending a test with a
        queued write makes this cocotb/Icarus build segfault at shutdown, after
        the results are written; see DECISIONS D-012.)"""
        for t in self.tasks:
            t.cancel()
        self.dut.s_tvalid.value = 0
        await RisingEdge(self.dut.clk)

    async def reset(self, cycles: int) -> None:
        """Hold rst_n low for some cycles; check the output stays empty meanwhile."""
        self.dut.rst_n.value = 0
        for _ in range(cycles):
            await RisingEdge(self.dut.clk)
            await ReadOnly()
            if int(self.dut.m_tvalid.value) != 0:
                self.errors.append("m_tvalid high during reset")
        await RisingEdge(self.dut.clk)
        self.dut.rst_n.value = 1
        self.cur = OutFrame()  # beats from before the reset do not count

    async def _ready_driver(self) -> None:
        """Random backpressure on the output."""
        while True:
            await RisingEdge(self.dut.clk)
            self.dut.m_tready.value = 0 if self.rng.random() < self.stall_prob else 1

    async def _monitor(self) -> None:
        """Collect every output beat accepted at a clock edge and rebuild frames.
        Uses tb/handshake_monitor.py, which also checks that valid, payload and
        sideband stay unchanged while the output is stalled."""
        dut = self.dut
        sigs = {"tdata": dut.m_tdata, "tkeep": dut.m_tkeep, "tlast": dut.m_tlast,
                "tuser": dut.m_tuser, "hdr_done": dut.m_hdr_done, "vlan": dut.m_vlan,
                "ipv4": dut.m_ipv4, "drop": dut.m_drop, "reason": dut.m_drop_reason}
        await monitor_handshake(dut.clk, dut.rst_n, dut.m_tvalid, dut.m_tready, sigs,
                                self._on_beat, self.errors, "eth_parser output")

    def _on_beat(self, v: dict) -> None:
        """Add one accepted output beat to the frame being rebuilt."""
        nbytes = bin(v["tkeep"]).count("1")
        if v["tkeep"] != (1 << nbytes) - 1:
            self.errors.append(f"non-contiguous tkeep {v['tkeep']:#x}")
        self.cur.data += v["tdata"].to_bytes(8, "little")[:nbytes]
        self.cur.beats.append((v["hdr_done"], v["vlan"], v["ipv4"], v["drop"], v["reason"],
                               v["tlast"]))
        if v["tlast"]:
            self.cur.tuser, self.cur.drop, self.cur.reason = v["tuser"], v["drop"], v["reason"]
            self.frames.append(self.cur)
            self.cur = OutFrame()

    async def send(self, frame: bytes, tuser: bool) -> None:
        """Drive one frame as 8-byte beats, with random idle gaps between beats."""
        dut = self.dut
        nbeats = (len(frame) + BYTES_PER_BEAT - 1) // BYTES_PER_BEAT
        for b in range(nbeats):
            while self.rng.random() < self.gap_prob:      # idle cycle (tvalid low)
                dut.s_tvalid.value = 0
                await RisingEdge(dut.clk)
            chunk = frame[b * BYTES_PER_BEAT:(b + 1) * BYTES_PER_BEAT]
            last = b == nbeats - 1
            # Garbage in unused lanes: the parser must ignore bytes with tkeep = 0.
            pad = bytes(self.rng.randrange(256) for _ in range(BYTES_PER_BEAT - len(chunk)))
            dut.s_tdata.value = int.from_bytes(chunk + pad, "little")
            dut.s_tkeep.value = (1 << len(chunk)) - 1
            dut.s_tlast.value = int(last)
            # tuser is only defined on the last beat; drive noise elsewhere.
            dut.s_tuser.value = int(tuser) if last else self.rng.randrange(2)
            dut.s_tvalid.value = 1
            stuck = 0
            while True:
                await ReadOnly()
                fire = int(dut.s_tready.value) == 1
                await RisingEdge(dut.clk)
                if fire:
                    break
                stuck += 1
                # Watchdog: random output stalls last a few cycles, never thousands.
                # A parser that never raises s_tready fails here instead of hanging.
                assert stuck < SEND_STALL_LIMIT, \
                    f"deadlock: s_tready low for {SEND_STALL_LIMIT} cycles while sending"
        dut.s_tvalid.value = 0

    async def drain(self, n_frames: int, timeout_cycles: int = 20000) -> None:
        """Wait until n_frames full frames have been collected."""
        for _ in range(timeout_cycles):
            if len(self.frames) >= n_frames:
                return
            await RisingEdge(self.dut.clk)
        raise AssertionError(f"timeout: {len(self.frames)} of {n_frames} frames came out")


def check_frame(got: OutFrame, frame: bytes, tuser: bool, tag: str) -> List[str]:
    """Compare one output frame with the input frame and the expected flags."""
    errs: List[str] = []
    exp = expect_for(frame, tuser)
    if bytes(got.data) != frame:
        errs.append(f"{tag}: data changed ({len(got.data)} bytes out, {len(frame)} in)")
    if got.tuser != int(tuser):
        errs.append(f"{tag}: tuser {got.tuser} != {int(tuser)}")
    if got.reason != exp.reason or got.drop != int(exp.reason != DROP_NONE):
        errs.append(f"{tag}: drop/reason {got.drop}/{got.reason}, expected reason {exp.reason} "
                    f"(model: '{F.parse_frame(frame, tuser).drop_reason}')")
    hdr_beat = next((i for i, b in enumerate(got.beats) if b[0]), None)
    if hdr_beat != exp.hdr_beat:
        errs.append(f"{tag}: m_hdr_done first at beat {hdr_beat}, expected {exp.hdr_beat}")
    for i, (hdr, vlan, ipv4, drop, reason, last) in enumerate(got.beats):
        if hdr_beat is not None and i >= hdr_beat:
            if not hdr or vlan != int(exp.vlan) or ipv4 != int(exp.ipv4):
                errs.append(f"{tag}: beat {i} hdr/vlan/ipv4 {hdr}/{vlan}/{ipv4}, "
                            f"expected 1/{int(exp.vlan)}/{int(exp.ipv4)}")
        if not last and (drop or reason):
            errs.append(f"{tag}: m_drop/m_drop_reason set on non-last beat {i}")
    return errs


async def run_frames(dut, frames: List[Tuple[bytes, bool]], rng: random.Random,
                     gap_prob: float, stall_prob: float, name: str) -> None:
    """Send all frames through a fresh DUT and check every one of them."""
    env = Env(dut, rng, gap_prob, stall_prob)
    await env.start()
    for frame, tuser in frames:
        await env.send(frame, tuser)
    await env.drain(len(frames))
    await env.stop()
    errs = list(env.errors)
    if len(env.frames) != len(frames):
        errs.append(f"{len(env.frames)} frames out, {len(frames)} in")
    for i, (got, (frame, tuser)) in enumerate(zip(env.frames, frames)):
        errs += check_frame(got, frame, tuser, f"{name} frame {i}")
    reasons = [f.reason for f in env.frames]
    dut._log.info(f"{name}: seed={SEED} frames={len(frames)} errors={len(errs)} "
                  f"reasons none/tuser/short/not_ipv4 = "
                  f"{[reasons.count(r) for r in range(4)]}")
    assert not errs, "\n".join(errs[:20])


# ---- Tests ---------------------------------------------------------------------
def directed_frames() -> List[Tuple[bytes, bool]]:
    """Hand-built cases: good, VLAN, wrong EtherType, QinQ, every short length, tuser."""
    good = eth(0x0800, ipv4_tcp())
    tagged = eth(0x0800, ipv4_tcp(), vlan=5)
    frames = [
        (good, False),                                   # untagged IPv4: pass
        (tagged, False),                                 # VLAN + IPv4: pass
        (eth(0x0806, bytes(28)), False),                 # ARP: not IPv4
        (eth(0x86DD, bytes(40)), False),                 # IPv6: not IPv4
        (eth(0x8100, eth(0x0800, b"")[12:] + ipv4_tcp(), vlan=5), False),  # QinQ: not IPv4
        (eth(0x0806, bytes(28), vlan=7), False),         # VLAN + ARP: not IPv4
        (good, True),                                    # bad FCS: tuser
        (good[:10], True),                               # short and tuser: tuser wins
        (eth(0x0800, b""), False),                       # 14 bytes, IPv4 type: L2 passes
        (eth(0x0800, b"", vlan=1), False),               # 18 bytes, VLAN + IPv4: L2 passes
        (bytes(range(64)), False),                       # 64 bytes of 0..63: type 0x0C0D
    ]
    frames += [(good[:n], False) for n in range(1, 16)]       # short untagged, 1..15 bytes
    frames += [(tagged[:n], False) for n in range(12, 20)]    # tag cut short, 12..19 bytes
    return frames


@cocotb.test(timeout_time=TEST_TIMEOUT_MS, timeout_unit="ms")
async def test_directed(dut) -> None:
    """Hand-built frames, no gaps, no backpressure."""
    await run_frames(dut, directed_frames(), random.Random(SEED), 0.0, 0.0, "directed")


@cocotb.test(timeout_time=TEST_TIMEOUT_MS, timeout_unit="ms")
async def test_directed_backpressure(dut) -> None:
    """Same frames with random input gaps and random output stalls."""
    await run_frames(dut, directed_frames() * 3, random.Random(SEED + 1), 0.3, 0.4,
                     "directed_backpressure")


@cocotb.test(timeout_time=TEST_TIMEOUT_MS, timeout_unit="ms")
async def test_random_fuzz(dut) -> None:
    """Random frames: random bytes, EtherType biased to IPv4/VLAN/ARP, random length and tuser."""
    rng = random.Random(SEED + 2)
    frames = []
    for _ in range(400):
        n = rng.randrange(1, 90)
        b = bytearray(rng.randrange(256) for _ in range(n))
        if n >= 14:
            b[12:14] = rng.choice([b"\x08\x00", b"\x81\x00", b"\x08\x06", bytes(b[12:14])])
        if n >= 18 and b[12:14] == b"\x81\x00":
            b[16:18] = rng.choice([b"\x08\x00", b"\x81\x00", b"\x86\xdd"])
        frames.append((bytes(b), rng.random() < 0.1))
    await run_frames(dut, frames, rng, 0.2, 0.3, "random_fuzz")


@cocotb.test(timeout_time=TEST_TIMEOUT_MS, timeout_unit="ms")
async def test_generated_traffic_vs_model(dut) -> None:
    """Generated traffic (all three classes, incl. VLAN, ARP, truncated and tuser
    frames) checked against model/features.py, with random gaps and stalls.
    The frames are written by main() before the simulation starts (Scapy is
    not available inside the simulator's Python, see tb/cocotb_run.py)."""
    path = os.environ.get("ETH_GEN_FRAMES", "")
    assert path and os.path.exists(path), f"generated frame file missing: '{path}'"
    frames = read_frames(path)
    n_vlan = sum(1 for d, _ in frames if len(d) >= 14 and d[12:14] == b"\x81\x00")
    dut._log.info(f"generated: seed={SEED} frames={len(frames)} vlan_tagged={n_vlan}")
    await run_frames(dut, frames, random.Random(SEED + 3), 0.1, 0.2, "generated")


@cocotb.test(timeout_time=TEST_TIMEOUT_MS, timeout_unit="ms")
async def test_reset_mid_frame(dut) -> None:
    """Reset in the middle of a frame (and while the output is stalled): nothing
    of the cut frame comes out afterwards, and later frames are parsed correctly."""
    rng = random.Random(SEED + 4)
    env = Env(dut, rng, 0.0, 0.0)
    await env.start()
    good = eth(0x0800, ipv4_tcp())
    tagged = eth(0x0800, ipv4_tcp(), vlan=9)
    for cut_after in (1, 2, 3):                          # reset after 1, 2 or 3 beats
        env.stall_prob = 1.0 if cut_after == 3 else 0.0  # third case: output stalled
        task = cocotb.start_soon(env.send(tagged, False))
        for _ in range(cut_after):
            await RisingEdge(dut.clk)
        task.cancel()
        dut.s_tvalid.value = 0
        n_before = len(env.frames)
        await env.reset(3)
        env.stall_prob = 0.0
        if len(env.frames) != n_before:
            env.errors.append(f"cut {cut_after}: a frame completed across the reset")
        # The next frames start cleanly; the first one is short to make sure no
        # VLAN state from the cut frame leaks into it.
        after = [(good[:13], False), (good, False), (tagged, False)]
        start = len(env.frames)
        for frame, tuser in after:
            await env.send(frame, tuser)
        await env.drain(start + len(after))
        for i, (frame, tuser) in enumerate(after):
            env.errors += check_frame(env.frames[start + i], frame, tuser, f"cut {cut_after} frame {i}")
    await env.stop()
    dut._log.info(f"reset_mid_frame: seed={SEED} errors={len(env.errors)}")
    assert not env.errors, "\n".join(env.errors[:20])


# ---- Stand-alone runner --------------------------------------------------------
GEN_SCENARIOS_PER_CLASS = 2   # 3 classes x 2 scenarios x 100 frames = 600 frames
GEN_FRAMES_PER_SCENARIO = 100


def write_frames(path: str, frames: List[Tuple[bytes, bool]]) -> None:
    """Write frames as text lines '<tuser 0/1> <hex bytes>'."""
    with open(path, "w") as f:
        for data, tuser in frames:
            f.write(f"{int(tuser)} {data.hex()}\n")


def read_frames(path: str) -> List[Tuple[bytes, bool]]:
    """Read frames written by write_frames."""
    with open(path) as f:
        return [(bytes.fromhex(h), t == "1") for t, h in (line.split() for line in f)]


def main() -> int:
    """Generate traffic (needs Scapy, so runs in .venv), then run the cocotb tests
    in the simulation Python (tb/cocotb_run.py). Returns 0 only if all pass."""
    from model import traffic_gen as G
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from cocotb_run import run_cocotb

    print(f"test_eth_parser: seed={SEED}")
    rows = G.generate(SEED, GEN_SCENARIOS_PER_CLASS, GEN_FRAMES_PER_SCENARIO)
    out_dir = os.path.join(ROOT, "sim_build", "eth_parser")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"generated_frames_seed{SEED}.txt")
    write_frames(path, [(f.data, f.tuser) for _, _, f in rows])
    return run_cocotb("eth_parser", ["rtl/eth_parser.sv"], "test_eth_parser", SEED,
                      {"ETH_GEN_FRAMES": path})


if __name__ == "__main__":
    sys.exit(main())
