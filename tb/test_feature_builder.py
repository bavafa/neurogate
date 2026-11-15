"""cocotb tests for rtl/feature_builder.sv (sliding-window features, SPEC section 5,
and the async-FIFO entry writer, SPEC section 8.1).

Run: `python3 tb/test_feature_builder.py` from .venv (or `make test`). The
seed comes from SEED (default 12345) and is printed. Exits non-zero on any
failure. Two builds run, both with Icarus, through tb/cocotb_run.py:

1. Unit tests (this module, top = feature_builder). Per-frame records are
   driven straight into the block, so field values the parsers would never
   produce (UDP with flag bits set, garbage on dropped records) are covered too:
   * directed window cases: two packets that share a destination-port bucket
     where one leaves the window and the other stays; 63 and 64 distinct
     buckets (F7 = 126 and the 127 cap); 64 and more SYNs (F6 cap); SYN+ACK
     and UDP-with-flags are not SYN; every IPv4 length 0..1500 plus values
     above (F3), every TTL (F4), ports 1023/1024 (F5); drops in between;
   * 20,000 random records with random input gaps, and 20,000 back to back
     (one record per clock);
   * FIFO sometimes full (random m_tready): frames lost, ovf_cnt accounting
     checked against the lost pkt_ids, OVF_ONLY entries, drain to zero;
   * pending overflow count saturating at 65535;
   * reset in the middle of the stream with a pending count and an entry
     in the output register: clean restart (empty window, pkt_id 0).
2. Generated traffic (tb/feature_chain_checks.py, top = tb/feature_chain_tb.sv:
   eth_parser -> l3l4_parser -> feature_builder): every frame of the default
   generated dataset (model/traffic_gen.py seed 1, 9000 frames) and of the SEED
   dataset, compared with model/features.py run_stream bit for bit; plus the
   SEED dataset again with a sometimes-full FIFO.

Expected values always come from model/features.py (the source of truth).
"""
import os
import random
import sys
from typing import List, Tuple

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ReadOnly, RisingEdge

TB_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(TB_DIR, "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, TB_DIR)
from model import features as F  # noqa: E402
from fb_common import (KIND_CLASSIFY, KIND_DROP, KIND_OVF_ONLY, OVF_MAX, Entry,  # noqa: E402
                       EntrySink, check_entries)

SEED = int(os.environ.get("SEED", "12345"))
CLK_PERIOD_NS = 6.4          # 156.25 MHz net_clk; only matters for waveforms
TEST_TIMEOUT_MS = 50         # simulated time per test; a deadlock fails instead of hanging
F3_MUL, F3_SHIFT = 22195, 18  # the RTL's multiply-and-shift for F3

# A record is what l3l4_parser hands over for one frame:
# (drop, is_tcp, syn, ack, rst_fin, ip_len, ttl, dst_port, pkt_len)
Rec = Tuple[int, int, int, int, int, int, int, int, int]


def model_entries(recs: List[Rec]) -> List[Entry]:
    """Expected entries for a record stream starting from reset (model/features.py)."""
    win = F.FeatureWindow()
    out = []
    for n, (drop, tcp, syn, ack, rf, ip_len, ttl, port, plen) in enumerate(recs):
        if drop:
            out.append(Entry(KIND_DROP, 0, n & 0xFFFF, plen, (0,) * 8))
        else:
            pkt = F.Packet(bool(tcp), bool(syn), bool(ack), bool(rf), ip_len, ttl, port)
            out.append(Entry(KIND_CLASSIFY, 0, n & 0xFFFF, plen, win.push(pkt)))
    return out


def kept(tcp: int = 1, syn: int = 0, ack: int = 1, rf: int = 0, ip_len: int = 60,
         ttl: int = 64, port: int = 80, plen: int = 64) -> Rec:
    """A kept-frame record."""
    return (0, tcp, syn, ack, rf, ip_len, ttl, port, plen)


def dropped(rng: random.Random) -> Rec:
    """A dropped-frame record with garbage in the parsed fields (must be ignored)."""
    return (1, rng.randrange(2), rng.randrange(2), rng.randrange(2), rng.randrange(2),
            rng.randrange(65536), rng.randrange(256), rng.randrange(65536), rng.randrange(65536))


def port_with_bucket(bucket: int, salt: int) -> int:
    """A destination port whose 6-bit hash is ``bucket`` (salt picks which one)."""
    hi = salt & 0x3FF                       # port bits [15:6]
    return (hi << 6) | ((bucket ^ hi) & 0x3F)


def start_clock(dut) -> None:
    """Start net_clk; call once at the top of every test."""
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())


class Env:
    """Drives records into the DUT and collects accepted entries."""

    def __init__(self, dut, rng: random.Random, ready_prob: float = 1.0) -> None:
        """Set up; nothing runs until start()."""
        self.dut, self.rng = dut, rng
        self.sink = EntrySink(dut, rng, ready_prob)
        self.tasks: list = []

    async def start(self) -> None:
        """Reset and launch the sink (the test starts the clock once, see start_clock)."""
        dut = self.dut
        self.idle_inputs()
        dut.m_tready.value = 1
        dut.rst_n.value = 0
        self.tasks = [cocotb.start_soon(self.sink.run())]
        for _ in range(4):
            await RisingEdge(dut.clk)
        dut.rst_n.value = 1

    def idle_inputs(self) -> None:
        """s_valid low, garbage on the fields (the block must ignore them)."""
        d, r = self.dut, self.rng
        d.s_valid.value = 0
        self._fields((r.randrange(2), r.randrange(2), r.randrange(2), r.randrange(2),
                      r.randrange(2), r.randrange(65536), r.randrange(256),
                      r.randrange(65536), r.randrange(65536)))

    def _fields(self, rec: Rec) -> None:
        """Put one record on the s_* inputs."""
        d = self.dut
        (d.s_drop.value, d.s_is_tcp.value, d.s_syn.value, d.s_ack.value, d.s_rst_fin.value,
         d.s_ip_len.value, d.s_ttl.value, d.s_dst_port.value, d.s_pkt_len.value) = rec

    async def send(self, recs: List[Rec], gap_prob: float = 0.0) -> None:
        """Drive records, one per clock unless a random gap is inserted.
        s_ready must be high on every edge (the block never stalls)."""
        dut = self.dut
        for rec in recs:
            while gap_prob and self.rng.random() < gap_prob:
                self.idle_inputs()
                await RisingEdge(dut.clk)
            self._fields(rec)
            dut.s_valid.value = 1
            await ReadOnly()
            assert int(dut.s_ready.value) == 1, "s_ready low: feature_builder must never stall"
            await RisingEdge(dut.clk)
        self.idle_inputs()

    async def drain(self, cycles: int = 8) -> None:
        """Let the last entry (and any OVF_ONLY entry) come out with the FIFO free."""
        self.sink.set_ready_prob(1.0)
        for _ in range(cycles):
            await RisingEdge(self.dut.clk)

    async def stop(self) -> None:
        """Stop background tasks; wait one edge (shutdown crash, DECISIONS D-012)."""
        for t in self.tasks:
            t.cancel()
        self.dut.s_valid.value = 0
        await RisingEdge(self.dut.clk)


async def run_and_check(dut, recs: List[Rec], name: str, gap_prob: float = 0.0,
                        ready_prob: float = 1.0) -> dict:
    """Fresh reset, send ``recs``, drain, compare with the model. Returns stats."""
    env = Env(dut, random.Random(f"{SEED}-{name}"), ready_prob)
    await env.start()
    await env.send(recs, gap_prob)
    await env.drain()
    await env.stop()
    errs, stats = check_entries(env.sink.entries, model_entries(recs), name,
                                allow_loss=ready_prob < 1.0)
    errs = env.sink.errors + errs
    dut._log.info(f"{name}: seed={SEED} records={len(recs)} {stats} errors={len(errs)}")
    assert not errs, "\n".join(errs[:20])
    return stats


# ---- Directed window and formula cases ------------------------------------------
def shared_bucket_case(rng: random.Random) -> List[Rec]:
    """Packet A (bucket b) enters, 62 packets in other buckets, packet B (also
    bucket b, different port) is the 64th, then one more packet pushes A out.
    Bucket b must stay occupied because B is still in the window."""
    b = 17
    a_port, b_port = port_with_bucket(b, 1), port_with_bucket(b, 300)
    assert a_port != b_port and F.port_bucket(a_port) == F.port_bucket(b_port) == b
    others = [x for x in range(64) if x != b]
    recs = [kept(port=a_port, syn=1, ack=0)]
    recs += [kept(port=port_with_bucket(others[i % 63], i + 2)) for i in range(62)]
    recs += [kept(port=b_port, syn=1, ack=0)]
    recs += [kept(port=port_with_bucket(others[0], 999))]       # A leaves here
    # Sanity on the model: after the 65th packet, bucket b still holds B.
    win = F.FeatureWindow()
    for r in recs:
        win.push(F.Packet(bool(r[1]), bool(r[2]), bool(r[3]), bool(r[4]), r[5], r[6], r[7]))
    assert win._bucket_cnt[b] == 1, "test construction: bucket b should hold one packet"
    return recs


def distinct_bucket_case() -> List[Rec]:
    """63 then 64 distinct buckets (F7 = 126, then 2*64 = 128 capped at 127),
    then the same 64 buckets again (each pushes out its own bucket: no change)."""
    recs = [kept(port=port_with_bucket(b, 5 + b)) for b in range(64)]
    recs += [kept(port=port_with_bucket(b, 700 + b)) for b in range(64)]
    return recs


def syn_case() -> List[Rec]:
    """SYN count: 70 SYNs (F6 reaches 2*64 -> 127), SYN+ACK and UDP with SYN set are
    not SYN, then 64 non-SYN packets push every SYN out (F6 back to 0)."""
    recs = [kept(syn=1, ack=0, port=1000 + i) for i in range(70)]
    recs += [kept(syn=1, ack=1), kept(tcp=0, syn=1, ack=0, rf=1), kept(tcp=0, syn=1, ack=1)]
    recs += [kept(syn=0, ack=0, rf=1, port=i) for i in range(64)]
    return recs


def field_sweep_case() -> List[Rec]:
    """F3 for every IPv4 length 0..1500 and above; F4 for every TTL; F5 port edges."""
    recs = [kept(ip_len=n, port=443) for n in range(1501)]
    recs += [kept(ip_len=n) for n in (1501, 1502, 2000, 9000, 32767, 32768, 65534, 65535)]
    recs += [kept(ttl=t, port=53, tcp=0) for t in range(256)]
    recs += [kept(port=p) for p in (0, 1, 1023, 1024, 1025, 4095, 32768, 65535)]
    recs += [kept(plen=p) for p in (0, 1, 60, 1514, 65535)]
    return recs


def test_f3_multiply_shift_equals_division() -> None:
    """The RTL's F3 multiply-and-shift equals the model's floor division for every input."""
    for n in range(1501):
        assert (n * F3_MUL) >> F3_SHIFT == n * 127 // 1500, n


@cocotb.test(timeout_time=TEST_TIMEOUT_MS, timeout_unit="ms")
async def test_directed_window(dut) -> None:
    """Shared bucket, F6/F7 caps, flag gating, field sweeps, drops in between."""
    start_clock(dut)
    test_f3_multiply_shift_equals_division()
    rng = random.Random(SEED)
    recs: List[Rec] = []
    for part in (shared_bucket_case(rng), distinct_bucket_case(), syn_case(), field_sweep_case()):
        recs += part
        recs += [dropped(rng) for _ in range(3)]
    # Also each part from reset on its own, so the shared-bucket case runs on an empty window.
    await run_and_check(dut, shared_bucket_case(rng), "shared_bucket")
    await run_and_check(dut, distinct_bucket_case(), "distinct_buckets")
    await run_and_check(dut, recs, "directed_all")


def random_records(rng: random.Random, n: int) -> List[Rec]:
    """Random record stream in phases with small and large port pools, so F6 and
    F7 move over their whole range, and about 10% drops."""
    recs: List[Rec] = []
    while len(recs) < n:
        pool = [rng.randrange(65536) for _ in range(rng.choice([1, 3, 20, 60, 500]))]
        syn_bias = rng.random()
        for _ in range(rng.randrange(50, 400)):
            if rng.random() < 0.1:
                recs.append(dropped(rng))
                continue
            tcp = int(rng.random() < 0.8)
            recs.append((0, tcp, int(rng.random() < syn_bias), rng.randrange(2), rng.randrange(2),
                         rng.choice([rng.randrange(65536), rng.randrange(1600)]),
                         rng.randrange(256), rng.choice(pool), rng.randrange(65536)))
    return recs[:n]


@cocotb.test(timeout_time=TEST_TIMEOUT_MS, timeout_unit="ms")
async def test_random_records_gaps(dut) -> None:
    """20,000 random records with random input gaps; FIFO never full."""
    start_clock(dut)
    await run_and_check(dut, random_records(random.Random(SEED + 1), 20000), "random_gaps", 0.3)


@cocotb.test(timeout_time=TEST_TIMEOUT_MS, timeout_unit="ms")
async def test_random_records_back_to_back(dut) -> None:
    """20,000 random records, one per clock (the fastest the parsers can deliver)."""
    start_clock(dut)
    await run_and_check(dut, random_records(random.Random(SEED + 2), 20000), "back_to_back")


@cocotb.test(timeout_time=TEST_TIMEOUT_MS, timeout_unit="ms")
async def test_overflow_accounting(dut) -> None:
    """FIFO full on random cycles: lost frames are counted in ovf_cnt exactly,
    OVF_ONLY entries carry counts when nothing else is sent, and after the
    drain nothing is pending. Every written entry still equals the model."""
    start_clock(dut)
    recs = random_records(random.Random(SEED + 3), 20000)
    s1 = await run_and_check(dut, recs, "overflow_back_to_back", 0.0, 0.6)
    s2 = await run_and_check(dut, recs, "overflow_gaps", 0.5, 0.5)
    assert s1["lost"] > 0 and s2["lost"] > 0, "test did not cause any overflow"
    assert s2["ovf_only"] > 0, "test never produced an OVF_ONLY entry"


@cocotb.test(timeout_time=200, timeout_unit="ms")
async def test_overflow_saturates(dut) -> None:
    """65,540 frames lost in a row: the pending count stops at 65535 (SPEC 8.1
    known limit) and is sent in one OVF_ONLY entry when the FIFO has room."""
    start_clock(dut)
    rng = random.Random(SEED + 4)
    env = Env(dut, rng, ready_prob=0.0)
    await env.start()
    n = OVF_MAX + 5
    await env.send([kept(port=rng.randrange(65536)) for _ in range(n)])
    for _ in range(5):                              # the last entry is lost too
        await RisingEdge(dut.clk)
    await env.drain()
    await env.send([kept(port=80)])
    await env.drain()
    await env.stop()
    got = env.sink.entries
    dut._log.info(f"overflow_saturates: seed={SEED} lost={n} entries={got[:3]}")
    assert not env.sink.errors, env.sink.errors[:5]
    assert len(got) == 2, f"expected 2 entries, got {len(got)}"
    assert got[0].kind == KIND_OVF_ONLY and got[0].ovf_cnt == OVF_MAX
    assert got[1].kind == KIND_CLASSIFY and got[1].ovf_cnt == 0
    assert got[1].pkt_id == n & 0xFFFF            # pkt_id counted every frame and wrapped


@cocotb.test(timeout_time=TEST_TIMEOUT_MS, timeout_unit="ms")
async def test_reset_mid_stream(dut) -> None:
    """Reset while the window is half full, a lost-frame count is pending and an
    entry sits in the output register. After reset: no stale entry, no stale
    count, pkt_id restarts at 0, the window starts empty."""
    start_clock(dut)
    rng = random.Random(SEED + 5)
    env = Env(dut, rng, ready_prob=0.3)
    await env.start()
    await env.send(random_records(rng, 100))
    dut.s_valid.value = 1                           # one more record in flight
    await RisingEdge(dut.clk)
    dut.rst_n.value = 0                             # entry register is full now
    dut.s_valid.value = 0
    for _ in range(3):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    before = len(env.sink.entries)
    env.sink.set_ready_prob(1.0)
    recs = random_records(rng, 500)
    await env.send(recs)
    await env.drain()
    await env.stop()
    after = env.sink.entries[before:]
    errs, stats = check_entries(after, model_entries(recs), "after_reset", allow_loss=False)
    errs = env.sink.errors + errs
    dut._log.info(f"reset_mid_stream: seed={SEED} {stats} errors={len(errs)}")
    assert not errs, "\n".join(errs[:20])


# ---- Runner (from .venv) ----------------------------------------------------------
DEFAULT_DATASET = (1, 10, 300)   # traffic_gen.py defaults: seed 1, 10 scenarios/class, 300 frames
SEED_DATASET = (2, 100)          # plus SEED, 2 scenarios/class x 100 frames


def write_frames(path: str, frames: List[Tuple[bytes, bool]]) -> None:
    """One line per frame: '<tuser> <hex>'."""
    with open(path, "w") as f:
        for data, tuser in frames:
            f.write(f"{int(tuser)} {data.hex()}\n")


def main() -> int:
    """Generate traffic (needs Scapy, so runs in .venv), then run both builds."""
    from model import traffic_gen as G
    from cocotb_run import run_cocotb

    print(f"test_feature_builder: seed={SEED}", flush=True)
    rc1 = run_cocotb("feature_builder", ["rtl/feature_builder.sv"], "test_feature_builder", SEED)
    out_dir = os.path.join(ROOT, "sim_build", "feature_chain_tb")
    os.makedirs(out_dir, exist_ok=True)
    paths = {}
    for name, rows in (("default", G.generate(*DEFAULT_DATASET)),
                       ("seed", G.generate(SEED, *SEED_DATASET))):
        paths[name] = os.path.join(out_dir, f"frames_{name}_seed{SEED}.txt")
        write_frames(paths[name], [(f.data, f.tuser) for _, _, f in rows])
    rc2 = run_cocotb("feature_chain_tb",
                     ["rtl/eth_parser.sv", "rtl/l3l4_parser.sv", "rtl/feature_builder.sv",
                      "tb/feature_chain_tb.sv"],
                     "feature_chain_checks", SEED,
                     {"FB_FRAMES_DEFAULT": paths["default"], "FB_FRAMES_SEED": paths["seed"]})
    return 1 if (rc1 or rc2) else 0


if __name__ == "__main__":
    sys.exit(main())
