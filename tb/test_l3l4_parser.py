"""cocotb tests for rtl/l3l4_parser.sv (IPv4 + TCP/UDP header parser, SPEC section 4).

Run: `python3 tb/test_l3l4_parser.py` from .venv (or `make test`). It generates
traffic with Scapy, dissects it with Scapy, then builds the testbench top
tb/parser_chain_tb.sv (eth_parser feeding l3l4_parser, wired as in the chip)
with Icarus and runs every test below through tb/cocotb_run.py. Exits non-zero
on any failure. The seed comes from SEED (default 12345) and is printed.

What is checked for every frame: exactly one result record comes out, in
order, and every field equals the Python model (model/features.py
parse_frame, the source of truth): keep/drop, drop reason, TCP/UDP, SYN, ACK,
RST-or-FIN, IPv4 total length, TTL, destination port, and frame length.
Parsed fields must be zero for dropped frames.

Tests: hand-built frames (IP options IHL 5..15, UDP, VLAN, fragments,
non-IPv4, IP version != 4, IHL < 5, other protocols, field edge values,
tuser) each also cut short at every possible length; the same with random
input gaps and random result backpressure; random header fuzz; frames longer
than 65535 bytes (pkt_len saturation); reset in the middle of a frame and
while a record is waiting; and the cross-check: all frames of the default
generated dataset (model/traffic_gen.py, seed 1, 9000 frames) plus the
SEED dataset, against both the model and Scapy's own dissection.
"""
import os
import random
import struct
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

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
CLK_PERIOD_NS = 6.4  # 156.25 MHz net_clk; only matters for waveforms

# r_drop_reason codes (rtl/l3l4_parser.sv) for each model drop reason.
REASON_CODE: Dict[str, int] = {
    "": 0, "tuser_error": 1, "short_ethernet": 2, "short_vlan": 2, "not_ipv4": 3,
    "short_ip": 4, "bad_ip_version": 5, "bad_ihl": 6, "short_ip_options": 7,
    "fragment": 8, "unsupported_l4": 9, "short_l4": 10,
}
FIELDS = ("drop", "reason", "is_tcp", "syn", "ack", "rst_fin", "ip_len", "ttl", "dst_port", "pkt_len")


# ---- Expected values -------------------------------------------------------------
@dataclass(frozen=True)
class Record:
    """One per-frame result record (the r_* outputs)."""

    drop: int
    reason: int
    is_tcp: int
    syn: int
    ack: int
    rst_fin: int
    ip_len: int
    ttl: int
    dst_port: int
    pkt_len: int


def expect(frame: bytes, tuser: bool) -> Record:
    """Expected record from the Python model (fields zero when dropped)."""
    r = F.parse_frame(frame, tuser)
    pkt_len = min(len(frame), F.PKT_LEN_MAX)
    p = r.packet
    if p is None:
        return Record(1, REASON_CODE[r.drop_reason], 0, 0, 0, 0, 0, 0, 0, pkt_len)
    return Record(0, 0, int(p.is_tcp), int(p.syn), int(p.ack), int(p.rst_or_fin),
                  p.ip_total_len, p.ttl, p.dst_port, pkt_len)


def diff(got: Record, exp: Record) -> str:
    """Readable list of the fields that differ."""
    return ", ".join(f"{n} {getattr(got, n)} != {getattr(exp, n)}" for n in FIELDS
                     if getattr(got, n) != getattr(exp, n))


# ---- Frame builders (struct only; Scapy is not available inside the simulator) ---
def eth(ethertype: int, payload: bytes, vlan: Optional[int] = None) -> bytes:
    """Ethernet frame with fixed MACs and an optional single 802.1Q tag."""
    hdr = bytes.fromhex("020000000001020000000002")
    if vlan is not None:
        hdr += struct.pack("!HH", 0x8100, vlan)
    return hdr + struct.pack("!H", ethertype) + payload


def ipv4(proto: int, l4: bytes, ihl: int = 5, ttl: int = 64, total_len: Optional[int] = None,
         frag: int = 0, version: int = 4) -> bytes:
    """IPv4 header (options filled with a counting pattern) followed by ``l4``."""
    opts = bytes((0x40 + i) & 0xFF for i in range(max(ihl - 5, 0) * 4))
    tl = (20 + len(opts) + len(l4)) if total_len is None else total_len
    hdr = struct.pack("!BBHHHBBH4s4s", (version << 4) | ihl, 0, tl & 0xFFFF, 0x1234, frag,
                      ttl, proto, 0, bytes([10, 0, 0, 1]), bytes([10, 0, 0, 2]))
    return hdr + opts + l4


def tcp(dport: int, flags: int, sport: int = 40000, payload: bytes = b"") -> bytes:
    """20-byte TCP header plus payload."""
    return struct.pack("!HHIIBBHHH", sport, dport, 1, 0, 0x50, flags, 512, 0, 0) + payload


def udp(dport: int, sport: int = 5353, payload: bytes = b"") -> bytes:
    """8-byte UDP header plus payload."""
    return struct.pack("!HHHH", sport, dport, 8 + len(payload), 0) + payload


# ---- Testbench plumbing ----------------------------------------------------------
class Env:
    """Drives frames in, randomizes r_ready, and collects result records."""

    def __init__(self, dut, rng: random.Random, gap_prob: float, stall_prob: float) -> None:
        """Set up handles and random knobs; nothing runs until start()."""
        self.dut, self.rng = dut, rng
        self.gap_prob, self.stall_prob = gap_prob, stall_prob
        self.records: List[Record] = []
        self.errors: List[str] = []
        self.tasks: list = []

    async def start(self) -> None:
        """Start the clock, reset, and launch the background coroutines."""
        dut = self.dut
        cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
        for sig in (dut.s_tvalid, dut.s_tdata, dut.s_tkeep, dut.s_tlast, dut.s_tuser):
            sig.value = 0
        dut.r_ready.value = 1
        await self.reset(4)
        self.tasks = [cocotb.start_soon(self._ready_driver()),
                      cocotb.start_soon(self._monitor())]

    async def stop(self) -> None:
        """Stop background coroutines and wait one edge so no write is still
        queued when the test ends (avoids the shutdown crash, DECISIONS D-012)."""
        for t in self.tasks:
            t.cancel()
        self.dut.s_tvalid.value = 0
        await RisingEdge(self.dut.clk)

    async def reset(self, cycles: int) -> None:
        """Hold rst_n low; r_valid must stay low meanwhile."""
        self.dut.rst_n.value = 0
        for _ in range(cycles):
            await RisingEdge(self.dut.clk)
            await ReadOnly()
            if int(self.dut.r_valid.value) != 0:
                self.errors.append("r_valid high during reset")
        await RisingEdge(self.dut.clk)
        self.dut.rst_n.value = 1

    async def _ready_driver(self) -> None:
        """Random backpressure on the result output."""
        while True:
            await RisingEdge(self.dut.clk)
            self.dut.r_ready.value = 0 if self.rng.random() < self.stall_prob else 1

    async def _monitor(self) -> None:
        """Collect every result record accepted at a clock edge. Uses
        tb/handshake_monitor.py, which also checks that valid and every result
        field stay unchanged while r_ready is low."""
        d = self.dut
        names = ("drop", "drop_reason", "is_tcp", "syn", "ack", "rst_fin", "ip_len", "ttl",
                 "dst_port", "pkt_len")
        sigs = {n: getattr(d, "r_" + n) for n in names}
        await monitor_handshake(d.clk, d.rst_n, d.r_valid, d.r_ready, sigs,
                                lambda v: self.records.append(Record(*(v[n] for n in names))),
                                self.errors, "l3l4_parser result")

    async def send(self, frame: bytes, tuser: bool) -> None:
        """Drive one frame as 8-byte beats with random idle gaps. Unused lanes
        carry garbage and tuser carries noise outside the last beat: the
        parsers must ignore both."""
        dut = self.dut
        nbeats = (len(frame) + BYTES_PER_BEAT - 1) // BYTES_PER_BEAT
        for b in range(nbeats):
            while self.gap_prob and self.rng.random() < self.gap_prob:
                dut.s_tvalid.value = 0
                await RisingEdge(dut.clk)
            chunk = frame[b * BYTES_PER_BEAT:(b + 1) * BYTES_PER_BEAT]
            last = b == nbeats - 1
            pad = bytes(self.rng.randrange(256) for _ in range(BYTES_PER_BEAT - len(chunk)))
            dut.s_tdata.value = int.from_bytes(chunk + pad, "little")
            dut.s_tkeep.value = (1 << len(chunk)) - 1
            dut.s_tlast.value = int(last)
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

    async def drain(self, n: int, timeout_cycles: int = 50000) -> None:
        """Wait until n records have been collected."""
        for _ in range(timeout_cycles):
            if len(self.records) >= n:
                return
            await RisingEdge(self.dut.clk)
        raise AssertionError(f"timeout: {len(self.records)} of {n} records came out")


def compare(got: List[Record], frames: List[Tuple[bytes, bool]], name: str) -> List[str]:
    """Compare the record stream with the model, frame by frame."""
    errs: List[str] = []
    if len(got) != len(frames):
        errs.append(f"{name}: {len(got)} records for {len(frames)} frames")
    for i, (g, (frame, tuser)) in enumerate(zip(got, frames)):
        e = expect(frame, tuser)
        if g != e:
            errs.append(f"{name} frame {i} (len {len(frame)}, tuser {int(tuser)}, model "
                        f"'{F.parse_frame(frame, tuser).drop_reason}'): {diff(g, e)}")
    return errs


async def run_frames(dut, frames: List[Tuple[bytes, bool]], rng: random.Random,
                     gap_prob: float, stall_prob: float, name: str) -> List[Record]:
    """Send frames through a fresh DUT, check against the model, return the records."""
    env = Env(dut, rng, gap_prob, stall_prob)
    await env.start()
    for frame, tuser in frames:
        await env.send(frame, tuser)
    await env.drain(len(frames))
    await env.stop()
    errs = env.errors + compare(env.records, frames, name)
    hist = [0] * 11
    for r in env.records:
        hist[r.reason] += 1
    dut._log.info(f"{name}: seed={SEED} frames={len(frames)} errors={len(errs)} "
                  f"reason histogram [0..10] = {hist}")
    assert not errs, "\n".join(errs[:20])
    return env.records


# ---- Directed frames ---------------------------------------------------------------
def base_frames() -> List[Tuple[bytes, bool]]:
    """Complete hand-built frames, one per case."""
    fr: List[Tuple[bytes, bool]] = []
    for flags in (0x02, 0x12, 0x10, 0x18, 0x01, 0x04, 0x11, 0x14, 0x00, 0xFF):
        fr.append((eth(0x0800, ipv4(6, tcp(80, flags))), False))      # TCP flag mixes
    for ihl in range(5, 16):                                          # IP options, every IHL
        fr.append((eth(0x0800, ipv4(6, tcp(1000 + ihl, 0x02), ihl=ihl)), False))
        fr.append((eth(0x0800, ipv4(17, udp(2000 + ihl), ihl=ihl)), False))
        fr.append((eth(0x0800, ipv4(6, tcp(3000 + ihl, 0x12), ihl=ihl), vlan=ihl), False))
    fr += [
        (eth(0x0800, ipv4(17, udp(53, payload=bytes(30)))), False),   # UDP with payload
        (eth(0x0800, ipv4(17, udp(123)), vlan=100), False),           # VLAN + UDP
        (eth(0x0800, ipv4(6, tcp(0, 0x02), ttl=0, total_len=0)), False),          # edge values
        (eth(0x0800, ipv4(6, tcp(65535, 0x10), ttl=255, total_len=65535)), False),
        (eth(0x0800, ipv4(17, udp(1023), ttl=1, total_len=1500)), False),
        (eth(0x0800, ipv4(6, tcp(443, 0x02), frag=0x2000)), False),   # MF set, offset 0: kept
        (eth(0x0800, ipv4(6, tcp(443, 0x02), frag=0x4000)), False),   # DF set: kept
        (eth(0x0800, ipv4(6, tcp(443, 0x02), frag=0x0001)), False),   # offset 1: fragment
        (eth(0x0800, ipv4(17, udp(53), frag=0x1F00)), False),         # offset high bits only
        (eth(0x0800, ipv4(6, bytes(40), frag=0x20B9), vlan=3), False),  # VLAN + fragment
        (eth(0x0800, ipv4(1, bytes(36))), False),                     # ICMP: unsupported
        (eth(0x0800, ipv4(47, bytes(36))), False),                    # GRE: unsupported
        (eth(0x0800, ipv4(0x86, bytes(36))), False),                  # other protocol
        (eth(0x0800, ipv4(6, tcp(80, 0x02), version=6)), False),      # version 6 under 0x0800
        (eth(0x0800, ipv4(6, tcp(80, 0x02), version=0)), False),
        (eth(0x0800, ipv4(6, tcp(80, 0x02), version=15, ihl=7)), False),
        (eth(0x86DD, bytes(60)), False),                              # IPv6 EtherType
        (eth(0x0806, bytes(28), vlan=2), False),                      # VLAN + ARP
        (eth(0x0800, ipv4(6, tcp(80, 0x02))), True),                  # tuser
        (eth(0x0800, ipv4(6, tcp(80, 0x02), version=6)), True),      # tuser beats version
    ]
    for ihl in range(0, 5):                                           # IHL < 5
        fr.append((eth(0x0800, ipv4(6, tcp(80, 0x02), ihl=ihl)), False))
    return fr


def truncation_frames() -> List[Tuple[bytes, bool]]:
    """Frames cut at every length from 1 byte to full, for a few header shapes.
    This puts the end of the frame on every lane of every header beat, so each
    length check is hit on both sides, and fields straddle beats in many ways."""
    shapes = [
        eth(0x0800, ipv4(6, tcp(8080, 0x02))),                        # TCP, IHL 5
        eth(0x0800, ipv4(17, udp(53))),                               # UDP, IHL 5
        eth(0x0800, ipv4(6, tcp(22, 0x12), ihl=15), vlan=7),          # VLAN + max options + TCP
        eth(0x0800, ipv4(17, udp(5060), ihl=6), vlan=8),              # VLAN + options + UDP
    ]
    return [(s[:n], False) for s in shapes for n in range(1, len(s) + 1)]


def directed_frames() -> List[Tuple[bytes, bool]]:
    """All hand-built cases."""
    return base_frames() + truncation_frames()


# ---- Tests ---------------------------------------------------------------------------
@cocotb.test(timeout_time=TEST_TIMEOUT_MS, timeout_unit="ms")
async def test_directed(dut) -> None:
    """Hand-built and truncated frames, no gaps, no backpressure."""
    await run_frames(dut, directed_frames(), random.Random(SEED), 0.0, 0.0, "directed")


@cocotb.test(timeout_time=TEST_TIMEOUT_MS, timeout_unit="ms")
async def test_directed_backpressure(dut) -> None:
    """Same frames with random input gaps and random result stalls."""
    await run_frames(dut, directed_frames(), random.Random(SEED + 1), 0.3, 0.5,
                     "directed_backpressure")


@cocotb.test(timeout_time=TEST_TIMEOUT_MS, timeout_unit="ms")
async def test_random_fuzz(dut) -> None:
    """Random headers: IHL 0..15, version mostly 4, protocol TCP/UDP/other,
    random fragment field, random VLAN, random cut point, random tuser."""
    rng = random.Random(SEED + 2)
    frames = []
    for _ in range(600):
        ihl = rng.choice([5, 5, 5, rng.randrange(16)])
        ver = 4 if rng.random() < 0.9 else rng.randrange(16)
        proto = rng.choice([6, 6, 17, 17, 1, rng.randrange(256)])
        frag = rng.choice([0, 0, 0, 0x4000, 0x2000, rng.randrange(65536)])
        l4 = bytes(rng.randrange(256) for _ in range(rng.choice([4, 14, 20, 40])))
        f = eth(0x0800, ipv4(proto, l4, ihl=ihl, ttl=rng.randrange(256),
                             total_len=rng.randrange(65536), frag=frag, version=ver),
                vlan=rng.randrange(4096) if rng.random() < 0.3 else None)
        if rng.random() < 0.3:
            f = f[:rng.randrange(1, len(f) + 1)]
        frames.append((f, rng.random() < 0.05))
    await run_frames(dut, frames, rng, 0.2, 0.3, "random_fuzz")


@cocotb.test(timeout_time=TEST_TIMEOUT_MS, timeout_unit="ms")
async def test_long_frames(dut) -> None:
    """pkt_len saturates at 65535: frames of 65534, 65535, 65536 and 70000 bytes,
    then a normal frame to show the length counter restarts."""
    good = eth(0x0800, ipv4(6, tcp(80, 0x02)))
    frames = [(good + bytes(n - len(good)), False) for n in (65534, 65535, 65536, 70000)]
    frames.append((good, False))
    await run_frames(dut, frames, random.Random(SEED + 5), 0.0, 0.0, "long_frames")


@cocotb.test(timeout_time=TEST_TIMEOUT_MS, timeout_unit="ms")
async def test_reset_mid_frame(dut) -> None:
    """Reset after 1..7 beats of a frame, and once while a finished record is
    waiting on a stalled r_ready: no record from the cut frame (or the waiting
    one) comes out, and the following frames are parsed correctly."""
    rng = random.Random(SEED + 4)
    env = Env(dut, rng, 0.0, 0.0)
    await env.start()
    tagged = eth(0x0800, ipv4(6, tcp(22, 0x02), ihl=6), vlan=9)       # 66 bytes, 9 beats
    after = [(eth(0x0800, ipv4(17, udp(53)))[:20], False),            # short: no state leaks in
             (eth(0x0800, ipv4(17, udp(53))), False),
             (tagged, False)]
    for cut_after in list(range(1, 8)) + ["waiting"]:
        n_before = len(env.records)
        if cut_after == "waiting":
            env.stall_prob = 1.0                                       # hold the record
            await env.send(tagged, False)
            for _ in range(3):
                await RisingEdge(dut.clk)
        else:
            task = cocotb.start_soon(env.send(tagged, False))
            for _ in range(cut_after):
                await RisingEdge(dut.clk)
            task.cancel()
        dut.s_tvalid.value = 0
        await env.reset(3)
        env.stall_prob = 0.0
        if len(env.records) != n_before:
            env.errors.append(f"cut {cut_after}: a record came out across the reset")
        start = len(env.records)
        for frame, tuser in after:
            await env.send(frame, tuser)
        await env.drain(start + len(after))
        env.errors += compare(env.records[start:], after, f"cut {cut_after}")
    await env.stop()
    dut._log.info(f"reset_mid_frame: seed={SEED} errors={len(env.errors)}")
    assert not env.errors, "\n".join(env.errors[:20])


@cocotb.test(timeout_time=TEST_TIMEOUT_MS, timeout_unit="ms")
async def test_generated_vs_model_and_scapy(dut) -> None:
    """Cross-check on generated traffic: every frame of the default dataset
    (traffic_gen seed 1, 9000 frames) and of the SEED dataset. The record must
    equal the model, and must agree with Scapy's dissection (prepared by main(),
    see scapy_expect): same keep/drop, and the same fields when Scapy decoded
    the whole TCP/UDP header."""
    path = os.environ.get("L3L4_GEN_FRAMES", "")
    assert path and os.path.exists(path), f"generated frame file missing: '{path}'"
    frames, scapy = read_frames(path)
    records = await run_frames(dut, frames, random.Random(SEED + 3), 0.05, 0.1, "generated")
    # RTL vs Scapy (the model was already compared above, inside run_frames).
    counts = {"fields": 0, "drop": 0, "keep_only": 0}
    errs = []
    for i, (rec, sc) in enumerate(zip(records, scapy)):
        kind = sc[0]
        if kind == "drop":
            ok = rec.drop == 1
        elif kind == "keep_only":                  # Scapy could not name the L4 layer
            ok = rec.drop == 0
        elif kind == "short":                      # cut inside the header: chip must drop
            ok = rec.drop == 1
            kind = "drop"
        else:                                      # full fields from Scapy
            want = tuple(int(x) for x in sc[1:])
            ok = rec.drop == 0 and (rec.is_tcp, rec.syn, rec.ack, rec.rst_fin, rec.ip_len,
                                    rec.ttl, rec.dst_port) == want
        counts[kind] += 1
        if not ok:
            errs.append(f"generated frame {i}: RTL {rec} vs Scapy {sc}")
    agree = len(records) - len(errs)
    dut._log.info(f"RTL vs Scapy (simulated): {agree} of {len(records)} frames agree "
                  f"({counts['fields']} kept with all fields compared, {counts['keep_only']} kept "
                  f"(Scapy has no L4 layer, keep decision compared), {counts['drop']} dropped)")
    dut._log.info(f"RTL vs model (simulated): {len(records)} of {len(frames)} frames agree")
    assert not errs, "\n".join(errs[:20])


# ---- Frame file and Scapy expectations (prepared in .venv by main) ------------------
def write_frames(path: str, frames: List[Tuple[bytes, bool]], scapy: List[List[str]]) -> None:
    """One line per frame: '<tuser> <hex> <scapy verdict ...>'."""
    with open(path, "w") as f:
        for (data, tuser), sc in zip(frames, scapy):
            f.write(f"{int(tuser)} {data.hex()} {' '.join(sc)}\n")


def read_frames(path: str) -> Tuple[List[Tuple[bytes, bool]], List[List[str]]]:
    """Read the file written by write_frames."""
    frames, scapy = [], []
    with open(path) as f:
        for line in f:
            parts = line.split()
            frames.append((bytes.fromhex(parts[1]), parts[0] == "1"))
            scapy.append(parts[2:])
    return frames, scapy


def scapy_expect(frame: bytes, tuser: bool) -> List[str]:
    """What Scapy's own dissection says about a frame (runs in .venv only).

    Returns one of:
      ['drop']            not IPv4, non-first fragment, not TCP/UDP, or tuser
      ['short']           IPv4 TCP/UDP but cut before the bytes the chip needs
                          (TCP: 14 header bytes, UDP: 4; SPEC section 4)
      ['keep_only']       long enough for the chip, but cut inside the
                          TCP/UDP header so Scapy decodes no L4 layer
      ['fields', is_tcp, syn, ack, rst_fin, ip_len, ttl, dst_port]
    """
    from scapy.all import IP, TCP, UDP, Dot1Q, Ether  # type: ignore
    s = Ether(frame)
    ip = s[Dot1Q].payload if s.haslayer(Dot1Q) else s.payload
    if tuser or not isinstance(ip, IP) or ip.version != 4 or ip.frag != 0 \
            or ip.proto not in (6, 17):
        return ["drop"]
    l4_bytes = len(bytes(ip)) - ip.ihl * 4
    need = 14 if ip.proto == 6 else 4
    if l4_bytes < need:
        return ["short"]
    l4 = ip.payload
    if not isinstance(l4, (TCP, UDP)):
        return ["keep_only"]
    if isinstance(l4, TCP):
        fl = int(l4.flags)
        flags = [1, int(bool(fl & 0x02)), int(bool(fl & 0x10)), int(bool(fl & 0x05))]
    else:
        flags = [0, 0, 0, 0]
    return ["fields"] + [str(x) for x in flags + [ip.len, ip.ttl, l4.dport]]


DEFAULT_DATASET = (1, 10, 300)   # traffic_gen.py defaults: seed 1, 10 scenarios/class, 300 frames
SEED_DATASET = (2, 100)          # plus SEED, 2 scenarios/class x 100 frames


def main() -> int:
    """Generate traffic and Scapy expectations (needs Scapy, so runs in .venv),
    then run the cocotb tests in the simulation Python. 0 only if all pass."""
    from model import traffic_gen as G
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from cocotb_run import run_cocotb

    print(f"test_l3l4_parser: seed={SEED}", flush=True)
    rows = G.generate(*DEFAULT_DATASET) + G.generate(SEED, *SEED_DATASET)
    frames = [(f.data, f.tuser) for _, _, f in rows]
    scapy = [scapy_expect(d, t) for d, t in frames]
    # Model vs Scapy, before any RTL runs (the model is the reference).
    bad = 0
    for (d, t), sc in zip(frames, scapy):
        e = expect(d, t)
        if sc[0] in ("drop", "short"):
            bad += e.drop != 1
        elif sc[0] == "keep_only":
            bad += e.drop != 0
        else:
            bad += (e.drop, e.is_tcp, e.syn, e.ack, e.rst_fin, e.ip_len, e.ttl, e.dst_port) != \
                (0, *(int(x) for x in sc[1:]))
    print(f"model vs Scapy: {len(frames) - bad} of {len(frames)} generated frames agree", flush=True)
    out_dir = os.path.join(ROOT, "sim_build", "parser_chain_tb")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"generated_frames_seed{SEED}.txt")
    write_frames(path, frames, scapy)
    rc = run_cocotb("parser_chain_tb",
                    ["rtl/eth_parser.sv", "rtl/l3l4_parser.sv", "tb/parser_chain_tb.sv"],
                    "test_l3l4_parser", SEED, {"L3L4_GEN_FRAMES": path})
    return 1 if bad else rc


if __name__ == "__main__":
    sys.exit(main())
