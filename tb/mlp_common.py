"""Shared helpers for the mlp_core tests (trained weights and custom corner-case weights).

Encodes async-FIFO entries (SPEC.md section 8.1), drives them into mlp_core
with the AXI-Stream rule (hold a beat until s_tready), collects results with
tb/handshake_monitor.py (which also checks that a stalled result does not
change), and compares each result with model/golden.py, the source of truth.

Runs inside the simulation Python (3.11, no NumPy, no Scapy): only cocotb and
the pure-Python golden model are imported.
"""
import os
import random
import sys
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ReadOnly, RisingEdge

TB_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(TB_DIR, ".."))
sys.path.insert(0, TB_DIR)
from model import golden  # noqa: E402
from handshake_monitor import monitor_handshake  # noqa: E402

SEED = int(os.environ.get("SEED", "12345"))
CLK_PERIOD_NS = 5.0          # 200 MHz core_clk; only matters for waveforms
KIND_CLASSIFY, KIND_DROP, KIND_OVF_ONLY = 0, 1, 2
SEND_STALL_LIMIT = 5000      # an inference takes ~180 cycles; thousands means a hang


def s32(v: int) -> int:
    """Unsigned 32-bit pattern -> signed int."""
    return v - (1 << 32) if v & (1 << 31) else v


@dataclass(frozen=True)
class Job:
    """One entry sent into mlp_core."""

    kind: int
    features: Tuple[int, ...]
    pkt_len: int
    pkt_id: int
    ovf_cnt: int

    def word(self) -> int:
        """The 114-bit entry (SPEC 8.1)."""
        w = 0
        for i, f in enumerate(self.features):
            w |= (f & 0xFF) << (8 * i)
        return (w | (self.pkt_len << 64) | (self.pkt_id << 80) | (self.ovf_cnt << 96)
                | (self.kind << 112))


@dataclass(frozen=True)
class Result:
    """One mlp_core output beat."""

    kind: int
    pkt_len: int
    pkt_id: int
    ovf_cnt: int
    logits: Tuple[int, int, int]
    cls: int


def expected(job: Job, p: golden.QParams) -> Result:
    """What mlp_core must output for ``job`` (golden model for CLASSIFY, else pass-through)."""
    if job.kind == KIND_CLASSIFY:
        cls, _, _, _, z = golden.infer(list(job.features), p)
        return Result(job.kind, job.pkt_len, job.pkt_id, job.ovf_cnt, tuple(z), cls)
    return Result(job.kind, job.pkt_len, job.pkt_id, job.ovf_cnt, (0, 0, 0), 0)


class MlpEnv:
    """Drives jobs, randomizes m_ready, collects results and timing."""

    def __init__(self, dut, rng: random.Random, gap_prob: float = 0.0,
                 stall_prob: float = 0.0) -> None:
        """Set up; nothing runs until start()."""
        self.dut, self.rng = dut, rng
        self.gap_prob, self.stall_prob = gap_prob, stall_prob
        self.results: List[Result] = []
        self.result_edges: List[int] = []   # edge number at which each result was taken
        self.accept_edges: List[int] = []   # edge number at which each job was taken
        self.errors: List[str] = []
        self.edge = 0
        self.tasks: list = []

    async def start(self, clock: bool = True) -> None:
        """Start the clock (once per test), reset, launch background tasks."""
        dut = self.dut
        if clock:
            cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
        dut.s_tvalid.value = 0
        dut.s_tdata.value = 0
        dut.m_ready.value = 1
        dut.rst_n.value = 0
        for _ in range(3):
            await RisingEdge(dut.clk)
        dut.rst_n.value = 1
        self.tasks = [cocotb.start_soon(self._edge_counter()),
                      cocotb.start_soon(self._ready_driver()),
                      cocotb.start_soon(self._monitor())]

    async def _edge_counter(self) -> None:
        """Count rising edges (timestamps for the cycle measurements)."""
        while True:
            await RisingEdge(self.dut.clk)
            self.edge += 1

    async def _ready_driver(self) -> None:
        """Random backpressure on the result output."""
        while True:
            await RisingEdge(self.dut.clk)
            self.dut.m_ready.value = 0 if self.rng.random() < self.stall_prob else 1

    async def _monitor(self) -> None:
        """Collect each result beat at the edge that takes it (and check stalls)."""
        d = self.dut
        sigs = {"kind": d.m_kind, "pkt_len": d.m_pkt_len, "pkt_id": d.m_pkt_id,
                "ovf_cnt": d.m_ovf_cnt, "z0": d.m_logit0, "z1": d.m_logit1, "z2": d.m_logit2,
                "cls": d.m_class}

        def on_beat(v: dict) -> None:
            self.results.append(Result(v["kind"], v["pkt_len"], v["pkt_id"], v["ovf_cnt"],
                                       (s32(v["z0"]), s32(v["z1"]), s32(v["z2"])), v["cls"]))
            self.result_edges.append(self.edge)
        await monitor_handshake(d.clk, d.rst_n, d.m_valid, d.m_ready, sigs, on_beat,
                                self.errors, "mlp_core result")

    async def send(self, job: Job) -> None:
        """Offer one entry and hold it until s_tready (AXI-Stream rule)."""
        dut = self.dut
        while self.gap_prob and self.rng.random() < self.gap_prob:
            dut.s_tvalid.value = 0
            await RisingEdge(dut.clk)
        dut.s_tdata.value = job.word()
        dut.s_tvalid.value = 1
        stuck = 0
        while True:
            await ReadOnly()
            fire = int(dut.s_tready.value) == 1
            await RisingEdge(dut.clk)
            if fire:
                self.accept_edges.append(self.edge)
                break
            stuck += 1
            assert stuck < SEND_STALL_LIMIT, "deadlock: s_tready low too long"
        dut.s_tvalid.value = 0

    async def wait_results(self, n: int, timeout_cycles: int = 400000) -> None:
        """Wait until ``n`` results have been collected."""
        for _ in range(timeout_cycles):
            if len(self.results) >= n:
                return
            await RisingEdge(self.dut.clk)
        raise AssertionError(f"timeout: {len(self.results)} of {n} results came out")

    async def stop(self) -> None:
        """Stop background tasks; wait one edge (shutdown crash, DECISIONS D-012)."""
        for t in self.tasks:
            t.cancel()
        self.dut.s_tvalid.value = 0
        await RisingEdge(self.dut.clk)


def compare(got: Sequence[Result], exp: Sequence[Result], name: str) -> Tuple[List[str], dict]:
    """Compare results one for one. Returns (errors, counts)."""
    errs: List[str] = []
    stats = {"compared": 0, "logit_mismatch": 0, "class_mismatch": 0, "meta_mismatch": 0}
    if len(got) != len(exp):
        errs.append(f"{name}: {len(got)} results, expected {len(exp)}")
    for i, (g, e) in enumerate(zip(got, exp)):
        stats["compared"] += 1
        bad = []
        if g.logits != e.logits:
            stats["logit_mismatch"] += 1
            bad.append(f"logits {g.logits} != {e.logits}")
        if g.cls != e.cls:
            stats["class_mismatch"] += 1
            bad.append(f"class {g.cls} != {e.cls}")
        if (g.kind, g.pkt_len, g.pkt_id, g.ovf_cnt) != (e.kind, e.pkt_len, e.pkt_id, e.ovf_cnt):
            stats["meta_mismatch"] += 1
            bad.append(f"meta {g} != {e}")
        if bad:
            errs.append(f"{name} #{i}: " + "; ".join(bad))
    return errs, stats


async def run_jobs(dut, jobs: List[Job], p: golden.QParams, name: str, gap_prob: float = 0.0,
                   stall_prob: float = 0.0, clock: bool = True,
                   exp: Optional[List[Result]] = None) -> Tuple[MlpEnv, dict]:
    """Reset, send all jobs, compare every result with the golden model."""
    env = MlpEnv(dut, random.Random(f"{SEED}-{name}"), gap_prob, stall_prob)
    await env.start(clock)
    for j in jobs:
        await env.send(j)
    await env.wait_results(len(jobs))
    for _ in range(3):                                # nothing extra may come out
        await RisingEdge(dut.clk)
    await env.stop()
    exp = exp if exp is not None else [expected(j, p) for j in jobs]
    errs, stats = compare(env.results, exp, name)
    errs = env.errors + errs
    dut._log.info(f"{name} (simulated): seed={SEED} jobs={len(jobs)} {stats} errors={len(errs)}")
    assert not errs, "\n".join(errs[:20])
    return env, stats


def classify_jobs(rows: Sequence[Sequence[int]], rng: random.Random) -> List[Job]:
    """CLASSIFY jobs for a list of feature vectors, with random metadata."""
    return [Job(KIND_CLASSIFY, tuple(x), rng.randrange(65536), n & 0xFFFF, rng.randrange(65536))
            for n, x in enumerate(rows)]
