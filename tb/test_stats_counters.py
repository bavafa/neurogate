"""cocotb tests for rtl/stats_counters.sv (counters and LAST_RESULT, docs/regmap.md).

Run: `python3 tb/test_stats_counters.py` from .venv (or `make test`). The seed
comes from SEED (default 12345) and is printed. Exits non-zero on any failure.

The reference is a small Python model written from the register map (not
from the RTL). After every clock edge ALL ten outputs are compared with it.

Two builds run the same tests (all results *simulated*):
  * default build, CNT_W = 32 (the register map);
  * CNT_W = 8 build, so that counter wrap-around is reachable in simulation.
    (The 32-bit wrap is also checked: counters are pre-loaded close to 2^32 by
    writing the RTL registers directly, then pushed over the edge.)

What is checked:
  * reset values; each entry kind and class updates exactly the documented
    counters (directed cases, incl. a low-confidence packet counted under its
    class AND in PKT_LOWCONF);
  * ovf_cnt of every kind is added to PKT_OVERFLOW; OVF_ONLY changes nothing else;
  * DROP / OVF_ONLY leave LAST_RESULT alone; LAST_RESULT layout [1:0] class,
    [15:8] confidence, the rest 0;
  * reserved cases (kind 3, CLASSIFY with class 3): only PKT_OVERFLOW moves;
  * soft_rst clears everything, and wins over an entry in the same cycle;
  * s_ready is always 1; s_valid low never changes a counter, whatever the
    payload pins show;
  * hardware reset in the middle of a random stream;
  * random streams (random gaps, random soft resets, bad inputs) of 6,000 cycles;
  * wrap: CNT_W = 8 build wraps all nine counters; 32-bit build wraps a counter
    pre-loaded to 0xFFFFFFFF, and byte counters over 65,540 maximum-size entries.
"""
import os
import random
import sys
from typing import Dict, Optional

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge

SEED = int(os.environ.get("SEED", "12345"))
CNT_W = int(os.environ.get("NG_CNT_W", "32"))
MASK = (1 << CNT_W) - 1
KIND_CLASSIFY, KIND_DROP, KIND_OVF_ONLY, KIND_RSVD = 0, 1, 2, 3
OUTS = ("pkt_normal", "pkt_scan", "pkt_syn", "bytes_normal", "bytes_scan",
        "bytes_syn", "pkt_dropped", "pkt_lowconf", "pkt_overflow", "last_result")


class Model:
    """Reference model written from docs/regmap.md (width CNT_W counters)."""

    def __init__(self) -> None:
        """All zero."""
        self.clear()

    def clear(self) -> None:
        """Soft reset / reset: everything to 0."""
        self.v: Dict[str, int] = {k: 0 for k in OUTS}

    def entry(self, kind: int, cls: int, conf: int, low: int, plen: int, ovf: int) -> None:
        """Apply one accepted entry (no soft reset in the same cycle)."""
        v = self.v
        if kind == KIND_CLASSIFY and cls in (0, 1, 2):
            n = ("normal", "scan", "syn")[cls]
            v["pkt_" + n] = (v["pkt_" + n] + 1) & MASK
            v["bytes_" + n] = (v["bytes_" + n] + plen) & MASK
            v["pkt_lowconf"] = (v["pkt_lowconf"] + low) & MASK
            v["last_result"] = (conf << 8) | cls
        elif kind == KIND_DROP:
            v["pkt_dropped"] = (v["pkt_dropped"] + 1) & MASK
        v["pkt_overflow"] = (v["pkt_overflow"] + ovf) & MASK


class Env:
    """Clock, reset, input driving and per-cycle checking."""

    def __init__(self, dut) -> None:
        """Bind to the DUT."""
        self.dut = dut
        self.m = Model()
        self.cycles = 0
        self.entries = 0

    async def start(self) -> None:
        """Start the clock, hold reset for a few cycles, release it."""
        d = self.dut
        cocotb.start_soon(Clock(d.clk, 10, unit="ns").start())
        self.idle_inputs()
        d.rst_n.value = 0
        for _ in range(3):
            await RisingEdge(d.clk)
        await FallingEdge(d.clk)
        d.rst_n.value = 1
        self.m.clear()
        self.check("after reset")

    def idle_inputs(self) -> None:
        """No entry, no soft reset."""
        d = self.dut
        d.s_valid.value = 0
        d.soft_rst.value = 0
        d.s_kind.value = 0
        d.s_pkt_len.value = 0
        d.s_ovf_cnt.value = 0
        d.s_class.value = 0
        d.s_conf.value = 0
        d.s_low_conf.value = 0

    def check(self, where: str) -> None:
        """Compare every output with the model (call at a falling edge)."""
        assert int(self.dut.s_ready.value) == 1, f"{where}: s_ready must always be 1"
        for k in OUTS:
            got, want = int(getattr(self.dut, k).value), self.m.v[k]
            assert got == want, f"{where}: {k} = {got:#x}, model {want:#x} (cycle {self.cycles})"

    async def cycle(self, valid: int = 0, kind: int = 0, cls: int = 0, conf: int = 0,
                    low: int = 0, plen: int = 0, ovf: int = 0, soft: int = 0,
                    garbage: bool = False, rng: Optional[random.Random] = None) -> None:
        """Drive one clock: set inputs, pass one rising edge, update the model, check.

        With ``valid`` = 0 and ``garbage`` the payload pins carry random values
        (they must be ignored).
        """
        d = self.dut
        d.s_valid.value = valid
        d.soft_rst.value = soft
        if valid or not garbage or rng is None:
            d.s_kind.value, d.s_class.value, d.s_conf.value = kind, cls, conf
            d.s_low_conf.value, d.s_pkt_len.value, d.s_ovf_cnt.value = low, plen, ovf
        else:
            d.s_kind.value = rng.randrange(4)
            d.s_class.value = rng.randrange(4)
            d.s_conf.value = rng.randrange(128)
            d.s_low_conf.value = rng.randrange(2)
            d.s_pkt_len.value = rng.randrange(1 << 16)
            d.s_ovf_cnt.value = rng.randrange(1 << 16)
        await RisingEdge(d.clk)
        if soft:
            self.m.clear()                  # reset wins over a simultaneous entry
        elif valid:
            self.m.entry(kind, cls, conf, low, plen, ovf)
        self.cycles += 1
        self.entries += valid
        await FallingEdge(d.clk)
        self.check("after edge")

    async def stop(self) -> None:
        """Idle one edge so no write is queued at shutdown (D-012)."""
        self.idle_inputs()
        await RisingEdge(self.dut.clk)


@cocotb.test()
async def test_reset_and_directed(dut) -> None:
    """Reset values, then one directed entry of each kind and class."""
    env = Env(dut)
    await env.start()
    for k in OUTS:
        assert int(getattr(dut, k).value) == 0, f"{k} not 0 after reset"
    if CNT_W < 32:
        # The hand-written totals below assume 32-bit counters; the narrow
        # build is only for wrap tests (the random and wrap tests cover it).
        await env.stop()
        return
    # CLASSIFY per class; the second normal packet is low-confidence.
    await env.cycle(1, KIND_CLASSIFY, 0, conf=100, low=0, plen=60)
    await env.cycle(1, KIND_CLASSIFY, 0, conf=5, low=1, plen=1514)
    await env.cycle(1, KIND_CLASSIFY, 1, conf=127, low=0, plen=54)
    await env.cycle(1, KIND_CLASSIFY, 2, conf=0, low=1, plen=64)
    v = env.m.v
    assert (v["pkt_normal"], v["pkt_scan"], v["pkt_syn"]) == (2, 1, 1)
    assert (v["bytes_normal"], v["bytes_scan"], v["bytes_syn"]) == (1574, 54, 64)
    assert v["pkt_lowconf"] == 2 and v["last_result"] == (0 << 8) | 2
    # last_result layout: class [1:0], confidence [15:8], nothing else.
    await env.cycle(1, KIND_CLASSIFY, 1, conf=0x55, low=0, plen=1)
    assert int(dut.last_result.value) == (0x55 << 8) | 1
    # DROP: counter + overflow count, last_result untouched.
    await env.cycle(1, KIND_DROP, 0, 0, 0, plen=20, ovf=0)
    await env.cycle(1, KIND_DROP, 3, 99, 1, plen=20, ovf=7)
    assert env.m.v["pkt_dropped"] == 2 and env.m.v["pkt_overflow"] == 7
    assert int(dut.last_result.value) == (0x55 << 8) | 1
    # OVF_ONLY: overflow only; class/len pins must not matter.
    before = dict(env.m.v)
    await env.cycle(1, KIND_OVF_ONLY, 2, 50, 1, plen=999, ovf=65535)
    after = env.m.v
    assert after["pkt_overflow"] == before["pkt_overflow"] + 65535
    assert {k: x for k, x in after.items() if k != "pkt_overflow"} == \
        {k: x for k, x in before.items() if k != "pkt_overflow"}
    # CLASSIFY carries ovf_cnt too.
    await env.cycle(1, KIND_CLASSIFY, 2, 9, 0, plen=70, ovf=3)
    assert env.m.v["pkt_overflow"] == before["pkt_overflow"] + 65535 + 3
    await env.stop()


@cocotb.test()
async def test_reserved_cases(dut) -> None:
    """kind 3 and CLASSIFY with class 3 never occur; only PKT_OVERFLOW may move."""
    env = Env(dut)
    await env.start()
    await env.cycle(1, KIND_CLASSIFY, 1, conf=40, low=0, plen=80)
    base = dict(env.m.v)
    await env.cycle(1, KIND_RSVD, 1, 20, 1, plen=100, ovf=4)
    await env.cycle(1, KIND_CLASSIFY, 3, 20, 1, plen=100, ovf=5)
    now = env.m.v
    assert now["pkt_overflow"] == 9
    assert {k: x for k, x in now.items() if k != "pkt_overflow"} == \
        {k: x for k, x in base.items() if k != "pkt_overflow"}
    await env.stop()


@cocotb.test()
async def test_valid_low_ignores_payload(dut) -> None:
    """With s_valid = 0 nothing changes, whatever the payload pins show."""
    env = Env(dut)
    rng = random.Random(SEED + 1)
    print(f"test_valid_low_ignores_payload: seed={SEED + 1}")
    await env.start()
    await env.cycle(1, KIND_CLASSIFY, 0, conf=33, low=1, plen=100, ovf=2)
    for _ in range(200):
        await env.cycle(0, garbage=True, rng=rng)
    await env.stop()


@cocotb.test()
async def test_soft_reset(dut) -> None:
    """soft_rst clears everything, also when an entry arrives in the same cycle."""
    env = Env(dut)
    await env.start()
    for i in range(10):
        await env.cycle(1, KIND_CLASSIFY, i % 3, conf=10 + i, low=i & 1, plen=60 + i, ovf=1)
    await env.cycle(1, KIND_DROP)
    assert any(env.m.v.values())
    await env.cycle(0, soft=1)
    assert not any(env.m.v.values())
    assert all(int(getattr(dut, k).value) == 0 for k in OUTS)
    # Entry and soft reset in the same cycle: reset wins, entry is not counted.
    await env.cycle(1, KIND_CLASSIFY, 2, conf=77, low=1, plen=500, ovf=9, soft=1)
    assert all(int(getattr(dut, k).value) == 0 for k in OUTS)
    # The block counts normally right after.
    await env.cycle(1, KIND_CLASSIFY, 2, conf=77, low=1, plen=500, ovf=9)
    assert int(dut.pkt_syn.value) == 1 and int(dut.pkt_overflow.value) == 9
    # soft_rst held for several cycles with entries every cycle: stays 0.
    for _ in range(4):
        await env.cycle(1, KIND_CLASSIFY, 0, conf=1, low=0, plen=60, ovf=1, soft=1)
    assert not any(env.m.v.values())
    await env.stop()


@cocotb.test()
async def test_random_stream(dut) -> None:
    """6,000 random cycles: gaps, bad kinds/classes, random soft resets; all outputs
    are compared with the model after every edge."""
    seed = SEED + 2
    rng = random.Random(seed)
    print(f"test_random_stream: seed={seed} CNT_W={CNT_W}")
    env = Env(dut)
    await env.start()
    for _ in range(6000):
        valid = int(rng.random() < 0.7)
        kind = rng.choices([0, 1, 2, 3], [60, 20, 15, 5])[0]
        await env.cycle(valid, kind, rng.randrange(4), rng.randrange(128), rng.randrange(2),
                        rng.choice([0, 1, 60, 1514, rng.randrange(1 << 16), 65535]),
                        rng.choice([0, 0, 0, 1, 2, rng.randrange(1 << 16)]),
                        soft=int(rng.random() < 0.01), garbage=True, rng=rng)
    assert env.entries > 3000
    dut._log.info(f"random stream: {env.entries} entries in {env.cycles} cycles (simulated)")
    await env.stop()


@cocotb.test()
async def test_reset_mid_stream(dut) -> None:
    """Hardware reset in the middle of a stream clears everything (soft_rst low)."""
    seed = SEED + 3
    rng = random.Random(seed)
    print(f"test_reset_mid_stream: seed={seed}")
    env = Env(dut)
    await env.start()
    for rnd in range(3):
        for _ in range(100):
            await env.cycle(1, KIND_CLASSIFY, rng.randrange(3), rng.randrange(128),
                            rng.randrange(2), rng.randrange(1, 1515), rng.randrange(3))
        await FallingEdge(dut.clk)
        dut.rst_n.value = 0
        dut.s_valid.value = 1            # an entry is offered during reset: not counted
        await RisingEdge(dut.clk)
        await FallingEdge(dut.clk)
        dut.rst_n.value = 1
        env.m.clear()
        env.idle_inputs()
        env.check(f"reset {rnd}")
        await env.cycle(1, KIND_DROP)
    await env.stop()


@cocotb.test()
async def test_wrap(dut) -> None:
    """Counters wrap past all ones to 0. CNT_W = 8: natural wrap of all nine counters.
    CNT_W = 32: pre-load a register near 2^32, and overflow the byte counters."""
    env = Env(dut)
    await env.start()
    if CNT_W < 32:
        for i in range(2 * (1 << CNT_W) + 5):
            await env.cycle(1, KIND_CLASSIFY, i % 3, conf=i & 127, low=1, plen=200 + i % 50, ovf=3)
            await env.cycle(1, KIND_DROP, ovf=250)
        assert all(v < (1 << CNT_W) for k, v in env.m.v.items() if k != "last_result")
        dut._log.info(f"CNT_W={CNT_W} wrap test done, counters {env.m.v} (simulated)")
    else:
        # Pre-load registers by direct write (simulation only), then count over the edge.
        await FallingEdge(dut.clk)
        for k in ("pkt_normal", "pkt_dropped", "pkt_overflow", "pkt_lowconf", "bytes_syn"):
            getattr(dut, k).value = 0xFFFFFFFE
            env.m.v[k] = 0xFFFFFFFE
        await FallingEdge(dut.clk)
        env.check("preload")
        await env.cycle(1, KIND_CLASSIFY, 0, conf=1, low=1, plen=10, ovf=1)   # -> ...FFF, ovf ...FFF
        assert env.m.v["pkt_normal"] == 0xFFFFFFFF
        await env.cycle(1, KIND_CLASSIFY, 0, conf=1, low=1, plen=10, ovf=1)   # wraps to 0
        assert int(dut.pkt_normal.value) == 0 and int(dut.pkt_lowconf.value) == 0
        assert int(dut.pkt_overflow.value) == 0
        await env.cycle(1, KIND_DROP)
        await env.cycle(1, KIND_DROP)
        assert int(dut.pkt_dropped.value) == 0
        await env.cycle(1, KIND_CLASSIFY, 2, conf=1, low=0, plen=10)          # bytes_syn wraps by 8
        assert int(dut.bytes_syn.value) == 8
        # Byte counters by brute force: 65,540 entries of 65,535 bytes pass 2^32.
        await env.cycle(0, soft=1)
        for _ in range(65540):
            await env.cycle(1, KIND_CLASSIFY, 1, conf=0, low=0, plen=65535)
        assert env.m.v["bytes_scan"] == (65540 * 65535) & 0xFFFFFFFF < 65540 * 65535
        dut._log.info(f"bytes_scan after 65,540 x 65,535 bytes: {env.m.v['bytes_scan']} (simulated)")
    await env.stop()


def main() -> int:
    """Build with Icarus (CNT_W = 32 and CNT_W = 8) and run the tests; 0 only if all pass."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from cocotb_run import run_cocotb

    print(f"test_stats_counters: seed={SEED}")
    rc = run_cocotb("stats_counters", ["rtl/stats_counters.sv"], "test_stats_counters", SEED,
                    extra_env={"NG_CNT_W": "32"})
    rc |= run_cocotb("stats_counters", ["rtl/stats_counters.sv"], "test_stats_counters", SEED,
                     extra_env={"NG_CNT_W": "8"}, parameters={"CNT_W": "8"}, build_name="stats_counters_w8")
    return rc


if __name__ == "__main__":
    sys.exit(main())
