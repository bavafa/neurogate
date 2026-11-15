"""cocotb tests for rtl/decision_unit.sv (confidence rule and low-confidence flag,
SPEC.md section 7). The reference for every bit is model/golden.py decide().

Run: `python3 tb/test_decision_unit.py` from .venv (or `make test`). The seed
comes from SEED (default 12345) and is printed. Exits non-zero on any failure.

What is checked (all simulated):
  * every row of data/vectors/mlp_vectors.csv (logits + class in, confidence
    + low_conf out, default CONF_THRESH from data/model/conf_thresh.txt), also
    re-checked against golden.decide run now; back to back and with random
    input gaps and output stalls;
  * edge cases built from chosen margins: margin exactly at multiples of 256
    (and -1 / +1 around them), confidence cap at 127 (margins 126*256 ..
    129*256 and up to 2^32 - 1), logit ties (two or three equal logits), int32
    extremes (top = 2^31-1, second = -2^31); for each, thresholds at
    confidence - 1, confidence, confidence + 1, plus 0 and 127;
  * exhaustive sweep: every confidence 0..127 against every threshold 0..127
    (16,384 combinations, each at a random remainder inside the 256 step);
  * DROP / OVF_ONLY entries: metadata passes, class, confidence and flag are 0
    even when the logit inputs carry garbage;
  * a class that is not the largest logit (never produced by mlp_core) clamps
    to confidence 0; class value 3 gives confidence 0;
  * the output holds still while stalled; reset in the middle of a stream.
"""
import csv
import os
import random
import sys
from dataclasses import dataclass
from typing import List, Optional, Tuple

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ReadOnly, RisingEdge

TB_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(TB_DIR, ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, TB_DIR)
from model import golden  # noqa: E402
from handshake_monitor import monitor_handshake  # noqa: E402

SEED = int(os.environ.get("SEED", "12345"))
VECTORS = os.path.join(ROOT, "data", "vectors", "mlp_vectors.csv")
WEIGHTS = os.path.join(ROOT, "data", "model", "weights.json")
THRESH_FILE = os.path.join(ROOT, "data", "model", "conf_thresh.txt")
KIND_CLASSIFY, KIND_DROP, KIND_OVF_ONLY = 0, 1, 2
I32_MIN, I32_MAX = -(1 << 31), (1 << 31) - 1
SEND_STALL_LIMIT = 200


@dataclass(frozen=True)
class Job:
    """One entry offered to decision_unit."""

    kind: int
    z: Tuple[int, int, int]
    cls: int
    thresh: int
    pkt_len: int = 0
    pkt_id: int = 0
    ovf_cnt: int = 0


@dataclass(frozen=True)
class Out:
    """What decision_unit must output for a job."""

    kind: int
    pkt_len: int
    pkt_id: int
    ovf_cnt: int
    cls: int
    conf: int
    low: int


def s32(v: int) -> int:
    """Unsigned 32-bit pattern -> signed int."""
    return v - (1 << 32) if v & (1 << 31) else v


def u32(v: int) -> int:
    """Signed int32 -> 32-bit pattern."""
    return v & 0xFFFFFFFF


def argmax(z: Tuple[int, int, int]) -> int:
    """Class rule of SPEC section 7: largest logit, lowest index on a tie."""
    return max(range(3), key=lambda k: (z[k], -k))


def expect(j: Job, shift: int) -> Out:
    """Expected output: golden.decide for a consistent CLASSIFY entry, the
    documented clamp for an inconsistent class, zeros for pass-through kinds."""
    if j.kind != KIND_CLASSIFY:
        return Out(j.kind, j.pkt_len, j.pkt_id, j.ovf_cnt, 0, 0, 0)
    if j.cls == argmax(j.z):
        cls, conf, low = golden.decide(j.z, shift, j.thresh)
        assert cls == j.cls
    elif j.cls == 3:
        return Out(j.kind, j.pkt_len, j.pkt_id, j.ovf_cnt, 3, 0, int(0 < j.thresh))
    else:
        others = [j.z[k] for k in range(3) if k != j.cls]
        conf = max(0, min((j.z[j.cls] - max(others)) >> shift, 127))
        low = conf < j.thresh
    return Out(j.kind, j.pkt_len, j.pkt_id, j.ovf_cnt, j.cls, conf, int(low))


class Env:
    """Drives jobs, randomizes m_ready, collects outputs."""

    def __init__(self, dut, rng: random.Random, gap: float = 0.0, stall: float = 0.0) -> None:
        """Set up; call start()."""
        self.dut, self.rng, self.gap, self.stall = dut, rng, gap, stall
        self.out: List[Out] = []
        self.errors: List[str] = []
        self.tasks: list = []

    async def start(self, clock: bool = True) -> None:
        """Start the clock (once per test), reset, launch monitor and ready driver."""
        d = self.dut
        if clock:
            cocotb.start_soon(Clock(d.clk, 5.0, unit="ns").start())
        d.s_valid.value = 0
        d.m_ready.value = 1
        d.conf_thresh.value = 0
        for s in (d.s_kind, d.s_pkt_len, d.s_pkt_id, d.s_ovf_cnt, d.s_logit0, d.s_logit1,
                  d.s_logit2, d.s_class):
            s.value = 0
        d.rst_n.value = 0
        for _ in range(3):
            await RisingEdge(d.clk)
        d.rst_n.value = 1
        sigs = {"kind": d.m_kind, "len": d.m_pkt_len, "id": d.m_pkt_id, "ovf": d.m_ovf_cnt,
                "cls": d.m_class, "conf": d.m_conf, "low": d.m_low_conf}

        def on_beat(v: dict) -> None:
            self.out.append(Out(v["kind"], v["len"], v["id"], v["ovf"], v["cls"], v["conf"], v["low"]))
        self.tasks = [cocotb.start_soon(self._ready()),
                      cocotb.start_soon(monitor_handshake(d.clk, d.rst_n, d.m_valid, d.m_ready,
                                                          sigs, on_beat, self.errors, "decision_unit out"))]

    async def _ready(self) -> None:
        """Random backpressure on the output."""
        while True:
            await RisingEdge(self.dut.clk)
            self.dut.m_ready.value = 0 if self.rng.random() < self.stall else 1

    async def send(self, j: Job) -> None:
        """Offer one entry (with its threshold) and hold it until s_ready."""
        d = self.dut
        while self.gap and self.rng.random() < self.gap:
            d.s_valid.value = 0
            await RisingEdge(d.clk)
        d.s_kind.value, d.s_pkt_len.value, d.s_pkt_id.value = j.kind, j.pkt_len, j.pkt_id
        d.s_ovf_cnt.value, d.s_class.value, d.conf_thresh.value = j.ovf_cnt, j.cls, j.thresh
        d.s_logit0.value, d.s_logit1.value, d.s_logit2.value = (u32(v) for v in j.z)
        d.s_valid.value = 1
        stuck = 0
        while True:
            await ReadOnly()
            fire = int(d.s_ready.value) == 1
            await RisingEdge(d.clk)
            if fire:
                break
            stuck += 1
            assert stuck < SEND_STALL_LIMIT, "deadlock: s_ready low too long"
        d.s_valid.value = 0

    async def finish(self, n: int) -> None:
        """Wait for n outputs, then check nothing extra comes out, and stop."""
        for _ in range(100000):
            if len(self.out) >= n:
                break
            await RisingEdge(self.dut.clk)
        for _ in range(3):
            await RisingEdge(self.dut.clk)
        for t in self.tasks:
            t.cancel()
        self.dut.s_valid.value = 0
        await RisingEdge(self.dut.clk)     # shutdown crash workaround (D-012)


async def run_jobs(dut, jobs: List[Job], name: str, shift: int, gap: float = 0.0,
                   stall: float = 0.0, clock: bool = True) -> dict:
    """Reset, send every job, compare every output with the model. Returns counts."""
    env = Env(dut, random.Random(f"{SEED}-{name}"), gap, stall)
    await env.start(clock)
    for j in jobs:
        await env.send(j)
    await env.finish(len(jobs))
    exp = [expect(j, shift) for j in jobs]
    errs = list(env.errors)
    if len(env.out) != len(exp):
        errs.append(f"{name}: {len(env.out)} outputs, expected {len(exp)}")
    bad = 0
    for i, (g, e) in enumerate(zip(env.out, exp)):
        if g != e:
            bad += 1
            errs.append(f"{name} #{i}: got {g} expected {e} (job {jobs[i]})")
    dut._log.info(f"{name} (simulated): seed={SEED} rows compared={min(len(env.out), len(exp))} "
                  f"mismatches={bad} errors={len(errs)}")
    assert not errs, "\n".join(errs[:20])
    return {"compared": len(exp), "mismatches": bad}


def conf_shift() -> int:
    """conf_shift of the trained model; must equal the RTL default (8)."""
    return golden.load_params(WEIGHTS).conf_shift


def csv_jobs(rng: random.Random) -> List[Job]:
    """One job per CSV row, threshold = the default CONF_THRESH; the CSV is
    first checked against golden.decide so a stale CSV cannot hide a difference."""
    shift = conf_shift()
    with open(THRESH_FILE) as fh:
        thresh = int(fh.read().split()[0])
    jobs = []
    with open(VECTORS) as fh:
        for r in csv.DictReader(fh):
            z = (int(r["z0"]), int(r["z1"]), int(r["z2"]))
            cls, conf, low = golden.decide(z, shift, thresh)
            assert (cls, conf, int(low)) == (int(r["class"]), int(r["confidence"]), int(r["low_conf"])), \
                f"CSV row disagrees with golden.decide: {r}"
            jobs.append(Job(KIND_CLASSIFY, z, cls, thresh, rng.randrange(65536), len(jobs) & 0xFFFF,
                            rng.randrange(65536)))
    return jobs


def zs_with_margin(rng: random.Random, margin: int, tie_high: bool = False) -> Tuple[int, int, int]:
    """Three logits whose top - second equals ``margin`` at a random class position
    and a random base, kept inside int32. The third logit is at or below second."""
    lo = I32_MIN
    hi = I32_MAX - margin
    second = rng.randrange(lo, hi + 1) if hi >= lo else lo
    top = second + margin
    third = rng.randrange(lo, second + 1)
    vals = [top, second, third]
    pos = list(range(3))
    rng.shuffle(pos)
    z = [0, 0, 0]
    for v, p in zip(vals, pos):
        z[p] = v
    return (z[0], z[1], z[2])


def tie_aware_class(z: Tuple[int, int, int]) -> int:
    """The class mlp_core would give."""
    return argmax(z)


def thresholds_around(conf: int) -> List[int]:
    """conf - 1, conf, conf + 1 (kept in 0..127), plus 0 and 127."""
    return sorted({max(0, min(127, t)) for t in (conf - 1, conf, conf + 1, 0, 127)})


@cocotb.test(timeout_time=100, timeout_unit="ms")
async def test_all_vectors(dut) -> None:
    """All rows of mlp_vectors.csv back to back, default CONF_THRESH."""
    jobs = csv_jobs(random.Random(SEED))
    r = await run_jobs(dut, jobs, "csv_back_to_back", conf_shift())
    dut._log.info(f"mlp_vectors.csv: rows compared={r['compared']} mismatches={r['mismatches']}")


@cocotb.test(timeout_time=100, timeout_unit="ms")
async def test_all_vectors_backpressure(dut) -> None:
    """All CSV rows again, random input gaps (30%) and output stalls (50%)."""
    jobs = csv_jobs(random.Random(SEED + 1))
    await run_jobs(dut, jobs, "csv_backpressure", conf_shift(), 0.3, 0.5)


@cocotb.test(timeout_time=100, timeout_unit="ms")
async def test_margin_edges(dut) -> None:
    """Margins at and around multiples of 256, the cap at 127, ties and int32
    extremes, each with thresholds at conf-1, conf, conf+1, 0 and 127."""
    shift, rng = conf_shift(), random.Random(SEED + 2)
    margins = {0, 1, 2, 255, 256, 257, 511, 512, 513}
    for c in (1, 2, 12, 13, 14, 63, 64, 125, 126, 127, 128, 129, 200, 1000):
        for d in (-1, 0, 1):
            margins.add(c * 256 + d)
    margins |= {126 * 256 - 1, 126 * 256, 127 * 256 - 1, 127 * 256, 128 * 256 - 1, 128 * 256,
                1 << 20, (1 << 31) - 1, 1 << 31, (1 << 32) - 2, (1 << 32) - 1}
    jobs: List[Job] = []
    for m in sorted(margins):
        for _ in range(3):                         # three random positions / bases per margin
            z = zs_with_margin(rng, m)
            conf = min(m >> shift, 127)
            for t in thresholds_around(conf):
                jobs.append(Job(KIND_CLASSIFY, z, tie_aware_class(z), t))
    # Ties: two equal at the top (margin 0), all three equal, tie among the lower two.
    for base in (0, 5, -7, I32_MAX, I32_MIN, 12345):
        for z in ((base, base, base), (base, base, I32_MIN), (I32_MIN, base, base),
                  (base, I32_MIN, base), (base, I32_MIN, I32_MIN)):
            for t in (0, 1, 13, 127):
                jobs.append(Job(KIND_CLASSIFY, z, tie_aware_class(z), t))
    # Largest possible difference, and the other extreme positions.
    for z in ((I32_MAX, I32_MIN, I32_MIN), (I32_MIN, I32_MAX, I32_MIN), (I32_MIN, I32_MIN, I32_MAX),
              (I32_MAX, I32_MAX - 1, I32_MAX - 2), (I32_MIN, I32_MIN + 1, I32_MIN + 2)):
        for t in (0, 13, 127):
            jobs.append(Job(KIND_CLASSIFY, z, tie_aware_class(z), t))
    r = await run_jobs(dut, jobs, "margin_edges", shift)
    dut._log.info(f"margin edge jobs: {r['compared']} compared, {r['mismatches']} mismatches")


@cocotb.test(timeout_time=200, timeout_unit="ms")
async def test_threshold_sweep(dut) -> None:
    """Every confidence 0..127 against every threshold 0..127 (16,384 combinations)."""
    shift, rng = conf_shift(), random.Random(SEED + 3)
    jobs = []
    for c in range(128):
        for t in range(128):
            m = (c << shift) + rng.randrange(1 << shift)
            z = zs_with_margin(rng, m)
            jobs.append(Job(KIND_CLASSIFY, z, tie_aware_class(z), t))
    r = await run_jobs(dut, jobs, "threshold_sweep", shift)
    # The model agrees with the stated rule at the edge (sanity check of this test itself):
    assert all(expect(j, shift).low == int(expect(j, shift).conf < j.thresh) for j in jobs)
    dut._log.info(f"threshold sweep: {r['compared']} combinations, {r['mismatches']} mismatches")


@cocotb.test(timeout_time=100, timeout_unit="ms")
async def test_random_logits(dut) -> None:
    """20,000 random logit triples (full int32 range and small ranges), random
    thresholds, with gaps and stalls."""
    shift, rng = conf_shift(), random.Random(SEED + 4)
    jobs = []
    for n in range(20000):
        span = rng.choice([1 << 31, 1 << 20, 1 << 14, 1 << 10, 300])
        z = tuple(max(I32_MIN, min(I32_MAX, rng.randrange(-span, span))) for _ in range(3))
        jobs.append(Job(KIND_CLASSIFY, z, tie_aware_class(z), rng.randrange(128),
                        rng.randrange(65536), n & 0xFFFF, rng.randrange(65536)))
    await run_jobs(dut, jobs, "random_logits", shift, 0.2, 0.3)


@cocotb.test(timeout_time=100, timeout_unit="ms")
async def test_passthrough_and_bad_class(dut) -> None:
    """DROP / OVF_ONLY with garbage logits and class -> zeros; metadata copied.
    A class that is not the largest logit clamps to 0; class 3 gives 0."""
    shift, rng = conf_shift(), random.Random(SEED + 5)
    jobs = []
    for n in range(1500):
        kind = rng.choice([KIND_CLASSIFY, KIND_DROP, KIND_OVF_ONLY])
        z = tuple(rng.randrange(I32_MIN, I32_MAX + 1) for _ in range(3))
        if kind == KIND_CLASSIFY:
            cls = rng.choice([argmax(z), argmax(z), (argmax(z) + 1) % 3, (argmax(z) + 2) % 3, 3])
        else:
            cls = rng.randrange(4)
        jobs.append(Job(kind, z, cls, rng.randrange(128), rng.randrange(65536), rng.randrange(65536),
                        rng.randrange(65536)))
    await run_jobs(dut, jobs, "passthrough_bad_class", shift, 0.2, 0.3)


@cocotb.test(timeout_time=100, timeout_unit="ms")
async def test_reset_mid_stream(dut) -> None:
    """Reset while a result waits on a stalled output and while entries stream:
    nothing from before the reset comes out; the next entries are correct."""
    shift, rng = conf_shift(), random.Random(SEED + 6)
    first = True
    for cut in (1, 2, 3, 7):
        env = Env(dut, rng)
        await env.start(first)
        first = False
        dut.m_ready.value = 0                    # hold the first result
        env.stall = 1.0                          # _ready keeps it low
        z = zs_with_margin(rng, 5000)
        await env.send(Job(KIND_CLASSIFY, z, argmax(z), 13, 1, 1, 1))
        for _ in range(cut):
            await RisingEdge(dut.clk)
        dut.rst_n.value = 0
        for _ in range(2):
            await RisingEdge(dut.clk)
        env.out.clear()
        dut.rst_n.value = 1
        env.stall = 0.0
        await RisingEdge(dut.clk)
        for _ in range(4):
            await RisingEdge(dut.clk)
        assert not env.out, f"reset cut {cut}: output after reset {env.out}"
        jobs = []
        for n in range(20):
            z = zs_with_margin(rng, rng.randrange(40000))
            jobs.append(Job(KIND_CLASSIFY, z, argmax(z), rng.randrange(128), n, n, 0))
        for j in jobs:
            await env.send(j)
        await env.finish(len(jobs))
        exp = [expect(j, shift) for j in jobs]
        assert env.out == exp and not env.errors, f"cut {cut}: {env.out[:3]} vs {exp[:3]} {env.errors[:3]}"
    dut._log.info("reset mid-stream (simulated): 4 cut points, all clean")


def main() -> int:
    """Check the RTL default shift equals the model, run the unit tests, then the
    chained test (frames come from Scapy, so they are written here in .venv)."""
    from cocotb_run import run_cocotb
    from model import golden as g
    from model import traffic_gen as G
    shift = g.load_params(WEIGHTS).conf_shift
    src = open(os.path.join(ROOT, "rtl", "decision_unit.sv")).read()
    assert f"parameter int CONF_SHIFT = {shift}" in src, \
        f"RTL default CONF_SHIFT differs from conf_shift={shift} in weights.json"
    print(f"test_decision_unit: seed={SEED} conf_shift={shift}", flush=True)
    rc1 = run_cocotb("decision_unit", ["rtl/decision_unit.sv"], "test_decision_unit", SEED)
    out_dir = os.path.join(ROOT, "sim_build", "decision_chain_tb")
    os.makedirs(out_dir, exist_ok=True)
    paths = {}
    for name, rows in (("default", G.generate(1, 10, 300)), ("seed", G.generate(SEED, 2, 100))):
        paths[name] = os.path.join(out_dir, f"frames_{name}_seed{SEED}.txt")
        with open(paths[name], "w") as f:
            for _, _, fr in rows:
                f.write(f"{int(fr.tuser)} {fr.data.hex()}\n")
    rc2 = run_cocotb("decision_chain_tb",
                     ["rtl/eth_parser.sv", "rtl/l3l4_parser.sv", "rtl/feature_builder.sv",
                      "rtl/mlp_core.sv", "rtl/decision_unit.sv", "tb/decision_chain_tb.sv"],
                     "decision_chain_checks", SEED,
                     {"DC_FRAMES_DEFAULT": paths["default"], "DC_FRAMES_SEED": paths["seed"]},
                     defines={n: os.path.join(ROOT, "data", "model", f) for n, f in
                              (("ROM_W1", "rom_w1.hex"), ("ROM_B1", "rom_b1.hex"),
                               ("ROM_W2", "rom_w2.hex"), ("ROM_B2", "rom_b2.hex"))})
    return 1 if (rc1 or rc2) else 0


if __name__ == "__main__":
    sys.exit(main())
