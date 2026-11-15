"""cocotb tests for rtl/axil_regs.sv, checked against docs/regmap.md.

Run: `python3 tb/test_axil_regs.py` from .venv (or `make test`). The seed comes
from SEED (default 12345) and is printed. Exits non-zero on any failure.

The register list (offset, name, access, reset value) is READ FROM
docs/regmap.md (the "Summary" table), not typed in again, so the test follows
the document. It first checks that the document has the 12 registers this
test knows how to exercise and that CONF_THRESH_RESET matches the value the
regmap text states.

What is checked (all *simulated*):
  * every register: reset value, read value, access type (RW / RO), and for each
    writable field the exact bits that are stored (WSTRB honored, reserved bits
    read 0);
  * every read-only register: reads the value of its counter input (random and
    corner values) and ignores writes (OKAY response, no side effect);
  * CTRL.SOFT_RST: a one-cycle soft_rst pulse in the same cycle the write is
    accepted, only for WSTRB[0] = 1 and WDATA[0] = 1; the bit reads 0; soft
    reset does not change CONF_THRESH;
  * unmapped offsets 0x30..0x3C: read 0 + SLVERR, write ignored + SLVERR, no side
    effect; address bits [1:0] ignored;
  * handshake: AW before W, W before AW, response stall (BREADY low), read stall
    (RREADY low: RDATA stays stable while counters change); the AXI stability
    rule (an offered response must not change until taken) is checked on the B and R
    channels in every cycle of every test;
  * 4,000-cycle random mix of reads and writes with random valid gaps and ready
    stalls against a reference model, counter inputs changing every cycle;
  * reset in the middle of a transaction.
Not checked here: the connection to stats_counters (neurogate_top, later).
"""
import os
import random
import re
import sys
from typing import Dict, List, Optional, Tuple

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge, Timer

TB_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(TB_DIR, ".."))

SEED = int(os.environ.get("SEED", "12345"))
OKAY, SLVERR = 0, 2
REGMAP = os.path.join(ROOT, "docs", "regmap.md")
THRESH_FILE = os.path.join(ROOT, "data", "model", "conf_thresh.txt")

# Input pin of axil_regs that feeds each read-only register.
RO_PIN = {"PKT_NORMAL": "pkt_normal", "PKT_SCAN": "pkt_scan", "PKT_SYN": "pkt_syn",
          "BYTES_NORMAL": "bytes_normal", "BYTES_SCAN": "bytes_scan",
          "BYTES_SYN": "bytes_syn", "PKT_DROPPED": "pkt_dropped",
          "PKT_LOWCONF": "pkt_lowconf", "LAST_RESULT": "last_result",
          "PKT_OVERFLOW": "pkt_overflow"}
EXPECTED_NAMES = ["CTRL", "CONF_THRESH"] + list(RO_PIN)
UNMAPPED = [0x30, 0x34, 0x38, 0x3C]


def parse_regmap(conf_thresh_reset: int) -> List[Dict[str, object]]:
    """Read the Summary table of docs/regmap.md -> list of {offset, name, access, reset}."""
    rows: List[Dict[str, object]] = []
    in_summary = False
    with open(REGMAP, encoding="utf-8") as f:
        for line in f:
            if line.startswith("## "):
                in_summary = line.strip() == "## Summary"
                continue
            if not in_summary or not line.startswith("| 0x"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            off, name, acc, rst = cells[0], cells[1].strip("`"), cells[2], cells[3].strip("`")
            val = conf_thresh_reset if rst == "CONF_THRESH_RESET" else int(rst.replace("_", ""), 0)
            rows.append({"offset": int(off, 16), "name": name, "access": acc, "reset": val})
    return rows


def regmap_states_thresh() -> int:
    """The default CONF_THRESH_RESET value the regmap text gives ('it is **13**')."""
    with open(REGMAP, encoding="utf-8") as f:
        m = re.search(r"it is \*\*(\d+)\*\*", f.read())
    assert m, "regmap.md no longer states the CONF_THRESH_RESET value"
    return int(m.group(1))


class Bus:
    """Cycle-exact AXI-Lite master. Inputs change at a falling edge; ready/valid
    outputs are sampled just before the next rising edge (what that edge sees)."""

    def __init__(self, dut) -> None:
        """Bind to the DUT."""
        self.dut = dut
        self.cycles = 0
        self.pulses = 0                       # soft_rst pulses seen so far
        self.errors: List[str] = []

    async def start(self) -> None:
        """Clock and reset."""
        d = self.dut
        cocotb.start_soon(Clock(d.clk, 10, unit="ns").start())
        self.idle()
        d.rst_n.value = 0
        for name in ("pkt_normal", "pkt_scan", "pkt_syn", "bytes_normal", "bytes_scan",
                     "bytes_syn", "pkt_dropped", "pkt_lowconf", "last_result", "pkt_overflow"):
            getattr(d, name).value = 0
        for _ in range(3):
            await RisingEdge(d.clk)
        await FallingEdge(d.clk)
        d.rst_n.value = 1
        self.prev: Optional[Dict[str, int]] = None   # last pre-edge snapshot
        self.prev_ready = (1, 1)

    def idle(self) -> None:
        """Master drives nothing; ready signals high."""
        d = self.dut
        d.s_axil_awvalid.value = 0
        d.s_axil_wvalid.value = 0
        d.s_axil_arvalid.value = 0
        d.s_axil_bready.value = 1
        d.s_axil_rready.value = 1
        d.s_axil_awaddr.value = 0
        d.s_axil_araddr.value = 0
        d.s_axil_wdata.value = 0
        d.s_axil_wstrb.value = 0

    async def tick(self, aw: Optional[int] = None, w: Optional[Tuple[int, int]] = None,
                   ar: Optional[int] = None, bready: int = 1, rready: int = 1) -> Dict[str, int]:
        """One clock. Present AW (address), W ((data, strb)) and AR (address) as given
        (None = valid low), set the ready pins, return what the edge sees:
        awready, wready, arready, bvalid, bresp, rvalid, rdata, rresp, soft_rst."""
        d = self.dut
        d.s_axil_awvalid.value = int(aw is not None)
        d.s_axil_awaddr.value = (aw or 0) & 0x3F
        d.s_axil_wvalid.value = int(w is not None)
        d.s_axil_wdata.value = w[0] if w else 0
        d.s_axil_wstrb.value = w[1] if w else 0
        d.s_axil_arvalid.value = int(ar is not None)
        d.s_axil_araddr.value = (ar or 0) & 0x3F
        d.s_axil_bready.value = bready
        d.s_axil_rready.value = rready
        await Timer(4, unit="ns")                       # settle; clock edge is 1 ns later
        pre = {k: int(getattr(d, n).value) for k, n in (
            ("awready", "s_axil_awready"), ("wready", "s_axil_wready"),
            ("arready", "s_axil_arready"), ("bvalid", "s_axil_bvalid"),
            ("bresp", "s_axil_bresp"), ("rvalid", "s_axil_rvalid"),
            ("rdata", "s_axil_rdata"), ("rresp", "s_axil_rresp"), ("soft_rst", "soft_rst"))}
        # AXI stability rule: a response that was offered and not taken must stay as it was.
        if self.prev is not None:
            p = self.prev
            if p["bvalid"] and not self.prev_ready[0]:
                if not (pre["bvalid"] and pre["bresp"] == p["bresp"]):
                    self.errors.append(f"B changed during a stall (cycle {self.cycles})")
            if p["rvalid"] and not self.prev_ready[1]:
                if not (pre["rvalid"] and pre["rdata"] == p["rdata"] and pre["rresp"] == p["rresp"]):
                    self.errors.append(f"R changed during a stall (cycle {self.cycles})")
        self.prev, self.prev_ready = pre, (bready, rready)
        await RisingEdge(d.clk)
        self.cycles += 1
        self.pulses += pre["soft_rst"]
        await FallingEdge(d.clk)
        return pre

    async def write(self, addr: int, data: int, strb: int = 0xF, aw_delay: int = 0,
                    w_delay: int = 0, b_stall: int = 0) -> int:
        """Full write. AW and W are presented after their own delays and held until
        accepted; the response is taken after ``b_stall`` extra cycles. Returns BRESP."""
        aw_done = w_done = False
        t = 0
        while not (aw_done and w_done):
            aw = addr if (not aw_done and t >= aw_delay) else None
            w = (data, strb) if (not w_done and t >= w_delay) else None
            pre = await self.tick(aw=aw, w=w, bready=0)
            if aw is not None and pre["awready"]:
                aw_done = True
            if w is not None and pre["wready"]:
                w_done = True
            # AXI-Lite rule for this slave: both are accepted together.
            assert pre["awready"] == pre["wready"], "AWREADY and WREADY differ"
            if (aw is not None) != (w is not None):
                assert not pre["awready"], "write accepted with only one of AW/W valid"
            t += 1
            assert t < 200, "write never accepted"
        for _ in range(b_stall):
            pre = await self.tick(bready=0)
            assert pre["bvalid"], "BVALID dropped while BREADY low"
        pre = await self.tick(bready=1)
        assert pre["bvalid"], "no write response"
        return pre["bresp"]

    async def read(self, addr: int, r_stall: int = 0) -> Tuple[int, int]:
        """Full read; returns (data, resp)."""
        t = 0
        while True:
            pre = await self.tick(ar=addr, rready=0)
            if pre["arready"]:
                break
            t += 1
            assert t < 200, "read never accepted"
        for _ in range(r_stall):
            pre = await self.tick(rready=0)
        pre = await self.tick(rready=1)
        assert pre["rvalid"], "no read response"
        return pre["rdata"], pre["rresp"]

    def clean(self) -> None:
        """Fail if a monitor saw a handshake rule violation."""
        assert not self.errors, f"handshake monitor: {self.errors[:3]}"

    async def stop(self) -> None:
        """Quiet the bus and let the last edge pass (D-012)."""
        self.idle()
        await RisingEdge(self.dut.clk)


def thresh_param() -> int:
    """CONF_THRESH_RESET the DUT was built with (the test passes it by environment)."""
    return int(os.environ.get("NG_THRESH_RESET", str(regmap_states_thresh())))


@cocotb.test()
async def test_regmap_matches_test_knowledge(dut) -> None:
    """docs/regmap.md has exactly the registers this test exercises, and the default
    CONF_THRESH_RESET equals the value in data/model/conf_thresh.txt (M3 choice)."""
    rows = parse_regmap(thresh_param())
    assert [r["name"] for r in rows] == EXPECTED_NAMES, [r["name"] for r in rows]
    assert [r["offset"] for r in rows] == [4 * i for i in range(12)]
    assert regmap_states_thresh() == 13, "regmap text and this test disagree on the default value"
    if os.path.exists(THRESH_FILE):
        with open(THRESH_FILE, encoding="utf-8") as f:
            assert int(f.read().split()[0]) == regmap_states_thresh(), "regmap vs data/model/conf_thresh.txt"
    for r in rows:
        assert r["access"] == ("RW" if r["name"] in ("CTRL", "CONF_THRESH") else "RO"), r
    # The register-map text says unmapped offsets are 0x30..0x3C.
    with open(REGMAP, encoding="utf-8") as f:
        assert "0x30 to 0x3C" in f.read()


@cocotb.test()
async def test_reset_values_and_ro(dut) -> None:
    """Every register: reset value; read-only ones follow their input pin and ignore writes."""
    rows = parse_regmap(thresh_param())
    bus = Bus(dut)
    await bus.start()
    rng = random.Random(SEED + 11)
    print(f"test_reset_values_and_ro: seed={SEED + 11}")
    for r in rows:                                    # reset values (inputs are 0 at reset)
        data, resp = await bus.read(int(r["offset"]))
        assert (data, resp) == (r["reset"], OKAY), f"{r['name']} reset: {data:#x}, resp {resp}"
    assert int(dut.conf_thresh.value) == thresh_param()
    for r in rows:
        if r["access"] != "RO":
            continue
        pin = getattr(dut, RO_PIN[str(r["name"])])
        off = int(r["offset"])
        for val in (0xFFFFFFFF, 1, 0x80000000, 0x00AB5503, rng.randrange(1 << 32), rng.randrange(1 << 32)):
            pin.value = val
            await bus.tick()
            data, resp = await bus.read(off, r_stall=rng.randrange(3))
            assert (data, resp) == (val, OKAY), f"{r['name']}: read {data:#x} want {val:#x}"
            # Write to a RO register: OKAY, ignored, no side effect.
            th = int(dut.conf_thresh.value)
            p0 = bus.pulses
            resp = await bus.write(off, rng.randrange(1 << 32), 0xF)
            assert resp == OKAY, f"{r['name']}: write to RO returned resp {resp}"
            data, _ = await bus.read(off)
            assert data == val, f"{r['name']}: changed by a write"
            assert int(dut.conf_thresh.value) == th and bus.pulses == p0
        pin.value = 0
    bus.clean()
    await bus.stop()


@cocotb.test()
async def test_ctrl_soft_rst(dut) -> None:
    """CTRL: reads 0; soft_rst pulses for one cycle exactly when WSTRB[0] = 1 and WDATA[0] = 1;
    the pulse comes in the cycle the write is accepted; CONF_THRESH is unaffected."""
    bus = Bus(dut)
    await bus.start()
    await bus.write(0x04, 0x55)                              # a non-reset threshold
    cases = [  # (wdata, wstrb, expect pulse)
        (0x1, 0xF, 1), (0x1, 0x1, 1), (0xFFFFFFFF, 0xF, 1), (0xFFFFFFFF, 0x3, 1),
        (0x0, 0xF, 0), (0xFFFFFFFE, 0xF, 0), (0x1, 0x0, 0), (0x1, 0xE, 0),
        (0x100, 0x2, 0), (0x80000001, 0x8, 0), (0x3, 0x1, 1),
    ]
    for data, strb, pulse in cases:
        p0 = bus.pulses
        # Present AW and W together; watch soft_rst in the cycle of acceptance.
        pre = await bus.tick(aw=0x00, w=(data, strb), bready=0)
        assert pre["awready"] and pre["wready"]
        assert pre["soft_rst"] == pulse, f"({data:#x}, strb {strb:#x}): soft_rst {pre['soft_rst']}"
        pre = await bus.tick(bready=1)                      # response cycle: no pulse any more
        assert pre["soft_rst"] == 0 and pre["bvalid"] and pre["bresp"] == OKAY
        await bus.tick()
        assert bus.pulses - p0 == pulse
        assert int(dut.soft_rst.value) == 0
        assert (await bus.read(0x00)) == (0, OKAY), "CTRL must read 0"
        assert int(dut.conf_thresh.value) == 0x55, "soft reset changed CONF_THRESH"
    # AW and W apart: no pulse before both are valid.
    p0 = bus.pulses
    await bus.tick(aw=0x00, bready=0)
    await bus.tick(w=(1, 0xF), bready=0)                   # AW dropped after one cycle: no accept
    assert bus.pulses == p0
    await bus.write(0x00, 1, 0xF, aw_delay=3, w_delay=0)
    await bus.write(0x00, 1, 0xF, aw_delay=0, w_delay=4)
    assert bus.pulses == p0 + 2
    # Address bits [1:0] are ignored: 0x03 is CTRL.
    p0 = bus.pulses
    await bus.write(0x03, 1, 0xF)
    assert bus.pulses == p0 + 1
    bus.clean()
    await bus.stop()


@cocotb.test()
async def test_conf_thresh(dut) -> None:
    """CONF_THRESH: stores bits [6:0] of byte lane 0 only; reserved bits read 0; WSTRB honored."""
    bus = Bus(dut)
    await bus.start()
    assert (await bus.read(0x04)) == (thresh_param(), OKAY)
    for val in (0, 1, 13, 64, 127, 0x7F, 0x55, 0x2A):
        assert await bus.write(0x04, val, 0xF) == OKAY
        assert (await bus.read(0x04)) == (val, OKAY)
        assert int(dut.conf_thresh.value) == val
    # Reserved bits: write all ones -> only [6:0] stored, bit 7 and [31:8] read 0.
    await bus.write(0x04, 0xFFFFFFFF, 0xF)
    assert (await bus.read(0x04)) == (0x7F, OKAY)
    await bus.write(0x04, 0x80, 0xF)                        # only the reserved bit 7
    assert (await bus.read(0x04)) == (0, OKAY)
    # Strobes: lane 0 low -> no change. Upper lanes alone -> no change.
    await bus.write(0x04, 0x21, 0xF)
    for strb in (0x0, 0x2, 0x4, 0x8, 0xE):
        await bus.write(0x04, 0x7F, strb)
        assert (await bus.read(0x04)) == (0x21, OKAY), f"strobe {strb:#x} changed the value"
    await bus.write(0x04, 0x1234_5622, 0x1)
    assert (await bus.read(0x04)) == (0x22, OKAY)
    # Address bits [1:0] ignored.
    await bus.write(0x07, 0x11, 0xF)
    assert (await bus.read(0x05)) == (0x11, OKAY)
    # Neighbours unaffected: CTRL still 0.
    assert (await bus.read(0x00)) == (0, OKAY)
    bus.clean()
    await bus.stop()


@cocotb.test()
async def test_unmapped(dut) -> None:
    """0x30..0x3C: read 0 + SLVERR; write ignored + SLVERR, no side effect."""
    bus = Bus(dut)
    await bus.start()
    dut.pkt_normal.value = 0xDEADBEEF
    dut.pkt_overflow.value = 0x12345678
    await bus.write(0x04, 0x2B)
    p0 = bus.pulses
    for off in UNMAPPED:
        for a in (off, off + 1, off + 3):
            assert (await bus.read(a)) == (0, SLVERR), f"read {a:#x}"
            assert await bus.write(a, 0xFFFFFFFF, 0xF) == SLVERR, f"write {a:#x}"
    assert bus.pulses == p0, "a write to an unmapped offset caused a soft reset"
    assert int(dut.conf_thresh.value) == 0x2B
    assert (await bus.read(0x08)) == (0xDEADBEEF, OKAY)
    assert (await bus.read(0x2C)) == (0x12345678, OKAY)     # last mapped slot is still mapped
    # After an error response a normal access works.
    assert await bus.write(0x04, 0x01) == OKAY
    bus.clean()
    await bus.stop()


@cocotb.test()
async def test_handshake(dut) -> None:
    """AW/W in either order, response and read stalls, ready rules, simultaneous read+write."""
    bus = Bus(dut)
    await bus.start()
    # Not ready without both: AW alone and W alone never accepted.
    for _ in range(3):
        pre = await bus.tick(aw=0x04)
        assert not pre["awready"] and not pre["wready"]
    for _ in range(3):
        pre = await bus.tick(w=(5, 0xF))
        assert not pre["awready"] and not pre["wready"]
    assert int(dut.conf_thresh.value) == thresh_param()
    # Every combination of delays.
    for awd in (0, 1, 3):
        for wd in (0, 1, 3):
            for bs in (0, 2):
                v = (awd * 7 + wd * 5 + bs + 1) & 0x7F
                assert await bus.write(0x04, v, 0xF, aw_delay=awd, w_delay=wd, b_stall=bs) == OKAY
                assert int(dut.conf_thresh.value) == v
    # A second write cannot be accepted while the response waits.
    await bus.tick(aw=0x04, w=(0x33, 0xF), bready=0)            # accepted
    pre = await bus.tick(aw=0x04, w=(0x44, 0xF), bready=0)       # BVALID high: must wait
    assert pre["bvalid"] and not pre["awready"] and not pre["wready"]
    pre = await bus.tick(aw=0x04, w=(0x44, 0xF), bready=1)       # response taken at this edge
    assert pre["bvalid"] and not pre["awready"]
    pre = await bus.tick(aw=0x04, w=(0x44, 0xF), bready=1)
    assert pre["awready"], "second write must be accepted once the response is gone"
    await bus.tick()
    assert int(dut.conf_thresh.value) == 0x44
    # Read stall: RDATA holds although the counter input changes.
    dut.pkt_scan.value = 0x1111
    await bus.tick(ar=0x0C, rready=0)
    dut.pkt_scan.value = 0x2222
    for _ in range(4):
        pre = await bus.tick(ar=0x0C, rready=0)               # AR offered again: must not be taken
        assert pre["rvalid"] and pre["rdata"] == 0x1111 and not pre["arready"]
    pre = await bus.tick(rready=1)
    assert pre["rvalid"] and pre["rdata"] == 0x1111
    assert (await bus.read(0x0C)) == (0x2222, OKAY)
    # Simultaneous read and write in one cycle: the read sees the OLD threshold.
    await bus.write(0x04, 0x10)
    pre = await bus.tick(aw=0x04, w=(0x20, 0xF), ar=0x04)
    assert pre["awready"] and pre["arready"]
    pre = await bus.tick()
    assert pre["rvalid"] and pre["rdata"] == 0x10 and pre["bvalid"]
    assert int(dut.conf_thresh.value) == 0x20
    bus.clean()
    await bus.stop()


@cocotb.test()
async def test_random_mix(dut) -> None:
    """4,000 cycles of random reads and writes (all offsets 0x00..0x3F, random strobes, gaps,
    stalls) against a reference model; counter inputs change every cycle."""
    seed = SEED + 12
    rng = random.Random(seed)
    print(f"test_random_mix: seed={seed}")
    bus = Bus(dut)
    await bus.start()
    rows = {int(r["offset"]): r for r in parse_regmap(thresh_param())}
    th = thresh_param()                                # model of CONF_THRESH
    pins = {off: getattr(dut, RO_PIN[str(r["name"])]) for off, r in rows.items() if r["name"] in RO_PIN}
    cur = {off: 0 for off in pins}
    aw: Optional[int] = None
    wv: Optional[Tuple[int, int]] = None
    ar: Optional[int] = None
    exp_b: List[int] = []                              # expected BRESP of responses not yet taken
    exp_r: List[Tuple[int, int]] = []                  # expected (rdata, rresp) not yet taken
    pulses_exp = 0
    n_w = n_r = n_b = n_rd = 0
    for _ in range(4000):
        for off, pin in pins.items():                  # counters move every cycle
            cur[off] = rng.randrange(1 << 32)
            pin.value = cur[off]
        # New requests appear at random and are held until accepted.
        if aw is None and rng.random() < 0.3:
            aw = rng.randrange(64)
        if wv is None and rng.random() < 0.3:
            wv = (rng.randrange(1 << 32), rng.choice([0xF, 0xF, 0x1, 0x0, 0x2, 0x3, rng.randrange(16)]))
        if ar is None and rng.random() < 0.4:
            ar = rng.randrange(64)
        bready, rready = int(rng.random() < 0.6), int(rng.random() < 0.6)
        pre = await bus.tick(aw=aw, w=wv, ar=ar, bready=bready, rready=rready)
        # Responses taken at this edge: compare with the queue.
        if pre["bvalid"] and bready:
            assert exp_b and pre["bresp"] == exp_b.pop(0), "wrong BRESP"
            n_b += 1
        if pre["rvalid"] and rready:
            want = exp_r.pop(0)
            assert (pre["rdata"], pre["rresp"]) == want, f"read {pre['rdata']:#x}/{pre['rresp']}, want {want}"
            n_rd += 1
        # Accepted requests: the read sees the register values BEFORE this edge's write.
        if ar is not None and pre["arready"]:
            slot = ar >> 2
            if slot > 11:
                exp_r.append((0, SLVERR))
            elif slot == 0:
                exp_r.append((0, OKAY))
            elif slot == 1:
                exp_r.append((th, OKAY))
            else:
                exp_r.append((cur[slot * 4], OKAY))
            ar = None
            n_r += 1
        if aw is not None and wv is not None and pre["awready"]:
            slot = aw >> 2
            if slot > 11:
                exp_b.append(SLVERR)
            else:
                exp_b.append(OKAY)
                if slot == 0 and wv[1] & 1 and wv[0] & 1:
                    pulses_exp += 1
                if slot == 1 and wv[1] & 1:
                    th = wv[0] & 0x7F
            aw, wv = None, None
            n_w += 1
        assert int(dut.conf_thresh.value) == th, "conf_thresh differs from the model"
    # Let the last responses drain, then compare the soft_rst count.
    for _ in range(6):
        await bus.tick()
    assert bus.pulses == pulses_exp, f"soft_rst pulses {bus.pulses}, model {pulses_exp}"
    assert n_w > 300 and n_r > 500 and n_b > 200 and n_rd > 400, (n_w, n_r, n_b, n_rd)
    dut._log.info(f"random mix: {n_w} writes, {n_r} reads, {pulses_exp} soft resets (simulated)")
    bus.clean()
    await bus.stop()


@cocotb.test()
async def test_reset_mid_transaction(dut) -> None:
    """Hardware reset while a response waits: CONF_THRESH back to its reset value,
    BVALID and RVALID low, and the bus works again afterwards."""
    bus = Bus(dut)
    await bus.start()
    await bus.write(0x04, 0x6E)
    dut.pkt_syn.value = 0x77
    await bus.tick(aw=0x04, w=(0x7F, 0xF), ar=0x10, bready=0, rready=0)   # both responses pending
    pre = await bus.tick(bready=0, rready=0)
    assert pre["bvalid"] and pre["rvalid"]
    dut.rst_n.value = 0
    await bus.tick(bready=0, rready=0)
    dut.rst_n.value = 1
    bus.prev = None                       # the stability rule does not span a reset
    pre = await bus.tick()
    assert not pre["bvalid"] and not pre["rvalid"]
    assert int(dut.conf_thresh.value) == thresh_param()
    assert (await bus.read(0x04)) == (thresh_param(), OKAY)
    assert (await bus.read(0x10)) == (0x77, OKAY)
    assert await bus.write(0x04, 0x01) == OKAY
    bus.clean()
    await bus.stop()


def main() -> int:
    """Build with Icarus (default parameter, and CONF_THRESH_RESET = 99) and run the tests."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from cocotb_run import run_cocotb

    print(f"test_axil_regs: seed={SEED}")
    rc = run_cocotb("axil_regs", ["rtl/axil_regs.sv"], "test_axil_regs", SEED)
    # A second build proves the reset value really comes from the parameter.
    rc |= run_cocotb("axil_regs", ["rtl/axil_regs.sv"], "test_axil_regs", SEED,
                     extra_env={"NG_THRESH_RESET": "99"}, parameters={"CONF_THRESH_RESET": "99"},
                     build_name="axil_regs_t99")
    return rc


if __name__ == "__main__":
    sys.exit(main())
