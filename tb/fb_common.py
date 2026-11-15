"""Shared helpers for the feature_builder tests (unit test and parser-chain test).

Holds what both tests need: decoding a 114-bit async-FIFO entry (SPEC.md
section 8.1), a sampler that records every entry the "FIFO" accepts at a clock
edge, a random m_tready driver that plays a sometimes-full FIFO, and the
checker that compares the accepted entries with the model's records,
including the overflow accounting.

Runs inside the simulation Python (3.11, no Scapy): only cocotb and
model/features.py are imported.
"""
import random
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from cocotb.triggers import ReadOnly, RisingEdge

KIND_CLASSIFY, KIND_DROP, KIND_OVF_ONLY = 0, 1, 2
KIND_NAMES = {0: "CLASSIFY", 1: "DROP", 2: "OVF_ONLY", 3: "reserved"}
OVF_MAX = 0xFFFF
PKT_ID_MOD = 1 << 16


@dataclass(frozen=True)
class Entry:
    """One decoded async-FIFO entry."""

    kind: int
    ovf_cnt: int
    pkt_id: int
    pkt_len: int
    features: Tuple[int, ...]


def decode(word: int) -> Entry:
    """Split a 114-bit entry into its fields (layout: SPEC section 8.1)."""
    feats = tuple((word >> (8 * i)) & 0xFF for i in range(8))
    return Entry(kind=(word >> 112) & 0x3, ovf_cnt=(word >> 96) & 0xFFFF,
                 pkt_id=(word >> 80) & 0xFFFF, pkt_len=(word >> 64) & 0xFFFF, features=feats)


class EntrySink:
    """Plays the async FIFO write side: drives m_tready, records accepted entries.

    The entry offered in a cycle is judged at the next rising edge, with the
    values that edge sees (read in ReadOnly after the previous edge), the same
    method as tb/handshake_monitor.py. The AXI hold rule is NOT checked: by
    design (SPEC 8.1) an entry that finds the FIFO full is discarded.
    """

    def __init__(self, dut, rng: random.Random, ready_prob: float) -> None:
        """Set up; call run() as a background coroutine."""
        self.dut, self.rng, self.ready_prob = dut, rng, ready_prob
        self.entries: List[Entry] = []
        self.offered = 0           # cycles with m_tvalid high (taken or not)
        self.errors: List[str] = []

    def set_ready_prob(self, p: float) -> None:
        """Change how often the FIFO has room (1.0 = never full)."""
        self.ready_prob = p

    async def run(self) -> None:
        """Forever: drive m_tready for the next edge, then judge that edge."""
        dut = self.dut
        prev: Optional[Tuple[int, int, int, int]] = None
        while True:
            dut.m_tready.value = 1 if self.rng.random() < self.ready_prob else 0
            await ReadOnly()
            cur = (int(dut.rst_n.value), int(dut.m_tvalid.value), int(dut.m_tready.value),
                   int(dut.m_tdata.value))
            # Synchronous reset: outputs clear at the first edge with rst_n
            # low, so m_tvalid must be low from the second reset cycle on.
            if cur[0] == 0 and prev is not None and prev[0] == 0 and cur[1]:
                self.errors.append("m_tvalid high during reset")
            prev = cur
            await RisingEdge(dut.clk)
            if prev[0] and prev[1]:
                self.offered += 1
                if prev[2]:
                    self.entries.append(decode(prev[3]))


def expected_entries(records) -> List[Entry]:
    """Model records (features.FrameRecord) -> the entries a never-full FIFO gets."""
    return [Entry(r.kind, 0, r.pkt_id, r.pkt_len, tuple(r.features)) for r in records]


def check_entries(got: Sequence[Entry], exp: Sequence[Entry], name: str,
                  allow_loss: bool) -> Tuple[List[str], dict]:
    """Compare accepted entries with the expected per-frame entries.

    Without loss: the frame entries must equal ``exp`` one for one, in order,
    with ovf_cnt 0 and no OVF_ONLY entry.

    With loss (FIFO sometimes full): every frame entry must equal the expected
    entry with the same position in the stream (pkt_ids strictly increasing);
    the running sum of ovf_cnt up to and including each frame entry must equal
    the number of frames lost before it; and after the drain, the total of
    ovf_cnt must equal the total number of lost frames. ``exp`` must have
    fewer than 65536 frames (pkt_id is matched without wrap).
    Returns (errors, stats).
    """
    errs: List[str] = []
    frames = [e for e in got if e.kind != KIND_OVF_ONLY]
    ovf_only = [e for e in got if e.kind == KIND_OVF_ONLY]
    stats = {"frames_in": len(exp), "entries": len(got), "frame_entries": len(frames),
             "ovf_only": len(ovf_only), "compared": 0, "mismatches": 0,
             "classify": sum(1 for e in frames if e.kind == KIND_CLASSIFY),
             "ovf_total": sum(e.ovf_cnt for e in got)}
    for e in ovf_only:
        if e.pkt_id or e.pkt_len or any(e.features) or e.ovf_cnt == 0:
            errs.append(f"{name}: bad OVF_ONLY entry {e}")
    if not allow_loss:
        if ovf_only or any(e.ovf_cnt for e in got):
            errs.append(f"{name}: overflow reported but the FIFO was never full")
        if len(frames) != len(exp):
            errs.append(f"{name}: {len(frames)} frame entries, expected {len(exp)}")
        for i, (g, x) in enumerate(zip(frames, exp)):
            stats["compared"] += 1
            if g != x:
                stats["mismatches"] += 1
                errs.append(f"{name}: frame {i}: got {g} expected {x}")
        return errs, stats
    assert len(exp) < PKT_ID_MOD
    ovf_run, prev_id = 0, -1
    for g in got:
        ovf_run += g.ovf_cnt
        if g.kind == KIND_OVF_ONLY:
            continue
        if g.pkt_id <= prev_id or g.pkt_id >= len(exp):
            errs.append(f"{name}: pkt_id {g.pkt_id} out of order (previous {prev_id})")
            break
        stats["compared"] += 1
        want = exp[g.pkt_id]
        if g != Entry(want.kind, g.ovf_cnt, want.pkt_id, want.pkt_len, want.features):
            stats["mismatches"] += 1
            errs.append(f"{name}: pkt_id {g.pkt_id}: got {g} expected {want}")
        lost_before = g.pkt_id - stats["compared"] + 1   # ids below this one not written
        if ovf_run != lost_before:
            errs.append(f"{name}: pkt_id {g.pkt_id}: ovf_cnt running sum {ovf_run}, "
                        f"but {lost_before} frames were lost before it")
        prev_id = g.pkt_id
    lost = len(exp) - len(frames)
    stats["lost"] = lost
    if stats["ovf_total"] != lost:
        errs.append(f"{name}: ovf_cnt total {stats['ovf_total']} != frames lost {lost}")
    return errs, stats
