#!/usr/bin/env python3
"""Structural clock-domain-crossing (CDC) check on a Yosys netlist of the chip.

What this is
    A self-written structural check, not a commercial CDC sign-off tool. It
    reads the JSON netlist that Yosys writes for ``neurogate_top`` after
    elaboration (``proc; flatten; opt_clean; memory_collect``; no technology
    mapping, so the netlist still has one cell per RTL operator) and checks
    every place where a signal made in one clock domain is used in the other.

Rules (each violation makes the script exit with status 1)
    C1  Clocks: every flop and memory write port is clocked directly by a
        clock input port (``net_clk`` or ``core_clk``). No derived clocks.
    S1  No logic before a synchronizer: a flop that samples a signal from the
        other domain must take it straight from a flop output (one wire, no
        gates between), into its D input only (not an enable or a reset),
        and must itself be a plain flop (no enable, no synchronous reset).
    S2  Two flops: the output of that first synchronizer flop may feed only
        one thing, the D input of a second plain flop in the same domain. A
        first flop whose output feeds logic is a one-flop synchronizer.
    G1  Gray code: if several bits from one source register cross together,
        the source register must load a Gray code of some value N
        (bit i = N[i] xor N[i+1], top bit = N[top]), built by an xor cell
        straight into its D inputs. A binary counter sent across fails here.
    M1  Storage read: data read from a memory written in the other domain is
        allowed only if (a) the read address comes from the reading domain
        and (b) the same block instance has passing Gray-pointer crossings in
        both directions (the FIFO protocol makes the data stable before the
        reader can see it). This part is protocol-based and NOT proven here.
    R1  External reset: the asynchronous reset port feeds only asynchronous
        reset pins of reset-synchronizer flops: a chain whose first flop
        loads the constant 1 and whose output is at least two flops deep.
    O1  Outputs: every output port is driven only by logic of its own domain
        (``DOMAIN_OF_PORT``); the external reset may not reach an output.
    R2  Domain resets: every asynchronous reset pin is driven straight by the
        reset-synchronizer output of the flop's OWN domain; the external
        reset port never appears in data, enable or synchronous reset logic.

Limits (also in docs/cdc.md)
    - G1 checks that the crossing register holds a Gray encoding of N. It
      does not prove that N changes by at most 1 per clock (that is what makes
      Gray code safe); for the FIFO pointers that is shown by reading the code
      and by the FIFO simulation tests.
    - Two separately synchronized single bits that are later combined
      (reconvergence) are not detected. The chip has no such crossing.
    - Timing of the crossing paths (max delay, bit skew) is not checked.
    - Ports are assigned to a domain by name (``DOMAIN_OF_PORT``); the check
      trusts that the outside world drives them from that clock.

Usage
    python3 scripts/cdc_check.py <netlist.json> [--rtl rtl] [--top neurogate_top]
    (with --rtl, Yosys first elaborates every rtl/*.sv into <netlist.json>;
    `make cdc-check` does this. tb/test_cdc_check.py plants known CDC bugs in
    copies of the RTL and checks that each one is caught.)
"""
import argparse
import json
import sys
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

CLOCK_PORTS = ("net_clk", "core_clk")
ASYNC_RESET_PORT = "rst_n"
# Port name prefix -> clock domain (SPEC sections 3.1 to 3.3).
DOMAIN_OF_PORT = (("s_axis_", "net_clk"), ("m_axis_res_", "core_clk"), ("s_axil_", "core_clk"))

FLOP_TYPES = {"$dff", "$dffe", "$adff", "$adffe", "$aldff", "$aldffe", "$sdff", "$sdffe",
              "$sdffce", "$dffsr", "$dffsre"}
PLAIN_FLOPS = {"$dff", "$adff", "$aldff"}         # no enable, no synchronous reset
LATCH_TYPES = {"$dlatch", "$adlatch", "$dlatchsr", "$sr"}
SYNC_PINS = ("D", "EN", "SRST")                    # sampled at the clock edge
ASYNC_PINS = ("ARST", "ALOAD", "SET", "CLR")       # act without a clock
MEM_TYPES = {"$mem_v2"}

Bit = object                 # int (net id) or str ("0", "1", "x", "z")
Leaf = Tuple                 # ("ff", cell, bit index) | ("mem", cell) | ("port", name, bit)


class Netlist:
    """Index of one flattened Yosys module: drivers and loads of every net bit."""

    def __init__(self, module: dict) -> None:
        """Build driver and load maps from the module's ports and cells."""
        self.cells: Dict[str, dict] = module["cells"]
        self.ports: Dict[str, dict] = module["ports"]
        self.driver: Dict[int, Tuple] = {}
        self.loads: Dict[int, List[Tuple]] = defaultdict(list)
        self.name_of: Dict[int, str] = {}
        # Readable name per bit: prefer public names, then the fewest hierarchy
        # levels (a top-level wire over the same net seen inside an instance).
        rank: Dict[int, Tuple[int, int, int]] = {}
        for n, v in module["netnames"].items():
            for i, b in enumerate(v["bits"]):
                if not isinstance(b, int):
                    continue
                r = (int(n[0] == "$"), n.count("."), len(n))
                if b not in rank or r < rank[b]:
                    rank[b] = r
                    self.name_of[b] = f"{n}[{i}]"
        for pname, p in self.ports.items():
            for i, b in enumerate(p["bits"]):
                if not isinstance(b, int):
                    continue
                if p["direction"] == "input":
                    self.driver[b] = ("port", pname, i)
                else:
                    self.loads[b].append(("port", pname, i))
        for cname, c in self.cells.items():
            dirs = c.get("port_directions", {})
            for pin, bits in c["connections"].items():
                for i, b in enumerate(bits):
                    if not isinstance(b, int):
                        continue
                    if dirs.get(pin) == "output":
                        self.driver[b] = ("cell", cname, pin, i)
                    else:
                        self.loads[b].append(("cell", cname, pin, i))
        self._memo: Dict[int, frozenset] = {}

    def nm(self, b: Bit) -> str:
        """Readable name of a net bit."""
        return self.name_of.get(b, str(b)) if isinstance(b, int) else f"const {b}"

    def leaves(self, b: Bit) -> frozenset:
        """Sources (flop outputs, memories, input ports) in the combinational cone of a bit.

        Constants contribute nothing. A memory read port contributes the memory
        itself plus the cone of its read address (both shape the read data)."""
        if not isinstance(b, int):
            return frozenset()
        if b in self._memo:
            return self._memo[b]
        out: Set[Leaf] = set()
        stack, seen = [b], set()
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            if x in self._memo and x != b:
                out |= self._memo[x]
                continue
            d = self.driver.get(x)
            if d is None:
                continue                                   # undriven: no source
            if d[0] == "port":
                out.add(("port", d[1], d[2]))
                continue
            _, cname, pin, idx = d
            c = self.cells[cname]
            if c["type"] in FLOP_TYPES:
                out.add(("ff", cname, idx))
                continue
            if c["type"] in MEM_TYPES:
                out.add(("mem", cname))
                for a in c["connections"].get("RD_ADDR", []):
                    if isinstance(a, int):
                        stack.append(a)
                continue
            for p, bits in c["connections"].items():       # combinational cell
                if c.get("port_directions", {}).get(p) == "input":
                    stack.extend(v for v in bits if isinstance(v, int))
        res = frozenset(out)
        self._memo[b] = res
        return res


def port_domain(name: str) -> Optional[str]:
    """Clock domain of a data input port, or None for clocks and the async reset."""
    for prefix, dom in DOMAIN_OF_PORT:
        if name.startswith(prefix):
            return dom
    return None


def check(module: dict) -> Tuple[List[str], List[str]]:
    """Run all rules on one flattened module. Returns (report lines, violations)."""
    nl = Netlist(module)
    report: List[str] = []
    errs: List[str] = []

    def clock_domain(cname: str, clk_bit: Bit) -> Optional[str]:
        d = nl.driver.get(clk_bit) if isinstance(clk_bit, int) else None
        if d and d[0] == "port" and d[1] in CLOCK_PORTS:
            return d[1]
        errs.append(f"C1 {cname}: clock {nl.nm(clk_bit)} is not a clock input port")
        return None

    # ---- domains of flops and memories (rule C1) ---------------------------
    flop_dom: Dict[str, Optional[str]] = {}
    mem_dom: Dict[str, Set[str]] = {}
    for cname, c in nl.cells.items():
        t = c["type"]
        if t in LATCH_TYPES:
            errs.append(f"C1 {cname}: latch ({t}) found")
        elif t in FLOP_TYPES:
            flop_dom[cname] = clock_domain(cname, c["connections"]["CLK"][0])
        elif t in MEM_TYPES:
            p = c["parameters"]
            doms = set()
            for i, b in enumerate(c["connections"].get("WR_CLK", [])):
                doms.add(clock_domain(cname, b))
            rd_en = p.get("RD_CLK_ENABLE", "0")
            if "1" in str(rd_en):
                errs.append(f"C1 {cname}: clocked memory read port (not supported by this check)")
            mem_dom[cname] = {d for d in doms if d}

    def leaf_domain(lf: Leaf) -> Optional[str]:
        if lf[0] == "ff":
            return flop_dom.get(lf[1])
        if lf[0] == "port":
            return "async" if lf[1] == ASYNC_RESET_PORT else port_domain(lf[1])
        return None

    def q_bit(cname: str, idx: int) -> Bit:
        return nl.cells[cname]["connections"]["Q"][idx]

    # ---- reset synchronizer chains (rule R1) --------------------------------
    rst_bits = set()
    for p in nl.ports.get(ASYNC_RESET_PORT, {"bits": []})["bits"]:
        rst_bits.add(p)
    chain: Dict[Tuple[str, int], int] = {}            # (cell, bit) -> stage number
    chain_out: Dict[int, str] = {}                     # net of a chain output -> domain
    for b in rst_bits:
        for ld in nl.loads.get(b, []):
            if ld[0] != "cell" or nl.cells[ld[1]]["type"] not in FLOP_TYPES \
                    or ld[2] not in ASYNC_PINS:
                where = f"{ld[1]}.{ld[2]}" if ld[0] == "cell" else f"port {ld[1]}"
                errs.append(f"R1 external reset {ASYNC_RESET_PORT} drives {where}: only "
                            "asynchronous reset pins of reset-synchronizer flops are allowed")
                continue
            c = nl.cells[ld[1]]
            for i in range(len(c["connections"]["Q"])):
                chain[(ld[1], i)] = 0
    # Stage numbers: stage 1 loads constant 1, stage k loads stage k-1 (same clock).
    changed = True
    while changed:
        changed = False
        for (cn, i), st in chain.items():
            if st:
                continue
            dbit = nl.cells[cn]["connections"]["D"][i]
            if dbit == "1":
                chain[(cn, i)] = 1
                changed = True
                continue
            drv = nl.driver.get(dbit) if isinstance(dbit, int) else None
            if drv and drv[0] == "cell" and drv[2] == "Q" and (drv[1], drv[3]) in chain:
                prev = chain[(drv[1], drv[3])]
                if prev and flop_dom.get(drv[1]) == flop_dom.get(cn):
                    chain[(cn, i)] = prev + 1
                    changed = True
    for (cn, i), st in sorted(chain.items()):
        q = q_bit(cn, i)
        if not st:
            errs.append(f"R1 {cn}[{i}]: reset-synchronizer flop does not load 1 or the "
                        "previous stage of the same clock")
            continue
        others = [ld for ld in nl.loads.get(q, []) if not (
            ld[0] == "cell" and ld[2] == "D" and (ld[1], ld[3]) in chain)]
        if others:
            if st < 2:
                errs.append(f"R1 {nl.nm(q)}: reset synchronizer output after only {st} flop")
            else:
                chain_out[q] = flop_dom.get(cn)
                report.append(f"reset synchronizer: {ASYNC_RESET_PORT} -> {nl.nm(q)} "
                              f"({flop_dom.get(cn)}), {st} flops, asynchronous assert, "
                              "synchronous release")

    # ---- asynchronous reset pins of every other flop (rule R2) --------------
    rst_users: Dict[str, Set[str]] = defaultdict(set)
    for cname, dom in flop_dom.items():
        c = nl.cells[cname]
        for pin in ASYNC_PINS:
            for b in c["connections"].get(pin, []):
                if not isinstance(b, int):
                    continue
                if b in rst_bits and (cname, 0) in chain:
                    continue
                if b in chain_out and chain_out[b] == dom:
                    rst_users[nl.nm(b)].add(dom)
                    continue
                errs.append(f"R2 {cname}.{pin} ({dom}): asynchronous reset driven by "
                            f"{nl.nm(b)}, not by the {dom} reset synchronizer output")
    for net, doms in sorted(rst_users.items()):
        report.append(f"asynchronous reset use: {net} -> async reset pins in {sorted(doms)} "
                      "(same domain)")

    # ---- crossings into synchronous pins (rules S1, S2, G1, M1, R2) ---------
    stage1: Dict[Tuple[str, int], Tuple[str, int]] = {}   # (dst cell, bit) -> (src cell, bit)
    mem_cross: List[Tuple[str, str, str]] = []             # (dst cell, mem, dst domain)
    for cname, dom in flop_dom.items():
        if dom is None:
            continue
        c = nl.cells[cname]
        for pin in SYNC_PINS:
            for i, b in enumerate(c["connections"].get(pin, [])):
                for lf in nl.leaves(b):
                    ld = leaf_domain(lf)
                    if lf[0] == "mem":
                        if mem_dom.get(lf[1]) and dom not in mem_dom[lf[1]]:
                            mem_cross.append((cname, lf[1], dom))
                        continue
                    if ld == "async":
                        errs.append(f"R2 {cname}.{pin}: external reset {ASYNC_RESET_PORT} "
                                    "used in synchronous logic")
                        continue
                    if ld is None or ld == dom:
                        continue
                    src = f"{lf[1]}[{lf[2]}]"
                    direct = nl.driver.get(b) == ("cell", lf[1], "Q", lf[2]) \
                        if lf[0] == "ff" else nl.driver.get(b) == lf
                    if pin != "D":
                        errs.append(f"S1 {cname}.{pin} ({dom}) depends on {src} ({ld}): "
                                    "a signal from the other domain reaches an enable or "
                                    "synchronous reset")
                    elif not direct:
                        errs.append(f"S1 {nl.nm(c['connections']['Q'][i])} ({dom}) samples "
                                    f"{src} ({ld}) through logic; a synchronizer must take "
                                    "a flop output directly")
                    elif c["type"] not in PLAIN_FLOPS:
                        errs.append(f"S1 {cname} ({dom}): first synchronizer flop has an "
                                    f"enable or synchronous reset ({c['type']})")
                    else:
                        stage1[(cname, i)] = (lf[1], lf[2]) if lf[0] == "ff" else (lf[1], -1)
    # Memory-read crossings: rows into memories from the other domain (not flops).
    for cname, c in nl.cells.items():
        if c["type"] not in MEM_TYPES:
            continue
        wdoms = mem_dom.get(cname, set())
        for pin in ("WR_EN", "WR_ADDR", "WR_DATA"):
            for b in c["connections"].get(pin, []):
                for lf in nl.leaves(b):
                    ld = leaf_domain(lf)
                    if lf[0] == "mem":
                        for wd in wdoms:
                            if mem_dom.get(lf[1]) and wd not in mem_dom[lf[1]]:
                                mem_cross.append((cname, lf[1], wd))
                    elif ld == "async":
                        errs.append(f"R2 memory {cname}.{pin}: external reset used as data")
                    elif ld and ld not in wdoms:
                        errs.append(f"S1 memory {cname}.{pin} ({sorted(wdoms)}) depends on "
                                    f"{lf[1]} ({ld}) without a synchronizer")

    # ---- S2: each first synchronizer flop feeds exactly one second flop -----
    groups: Dict[Tuple[str, str], List[int]] = defaultdict(list)   # (src, dst) -> dst bits
    s2_ok: Dict[Tuple[str, str], List[str]] = defaultdict(list)
    for (dcell, i), (scell, si) in sorted(stage1.items()):
        q = q_bit(dcell, i)
        lds = nl.loads.get(q, [])
        dom = flop_dom[dcell]
        good = len(lds) == 1 and lds[0][0] == "cell" and lds[0][2] == "D" \
            and nl.cells[lds[0][1]]["type"] in FLOP_TYPES and flop_dom.get(lds[0][1]) == dom
        if not good:
            used = ", ".join(f"{x[1]}.{x[2]}" for x in lds) or "nothing"
            errs.append(f"S2 {nl.nm(q)} ({dom}): first synchronizer flop output feeds "
                        f"{used}; it must feed only the D input of a second flop "
                        "(one-flop synchronizer)")
        else:
            s2_ok[(scell, dcell)].append(nl.nm(nl.cells[lds[0][1]]["connections"]["Q"][lds[0][3]]))
        groups[(scell, dcell)].append(i)

    # ---- G1: multi-bit groups must come from a Gray-encoded register --------
    gray_dirs: Dict[str, Set[Tuple[str, str]]] = defaultdict(set)   # instance -> (from, to)
    for (scell, dcell), bits in sorted(groups.items()):
        sdom, ddom = flop_dom.get(scell), flop_dom[dcell]
        width = len(bits)
        method = "two-flop synchronizer, 1 bit"
        ok = len(s2_ok[(scell, dcell)]) == width          # every bit passed S2
        if width > 1:
            g_ok, why = is_gray_register(nl, scell)
            method = f"Gray-coded bus, {width} bits, two-flop synchronizer"
            if not g_ok:
                ok = False
                errs.append(f"G1 {scell} -> {dcell} ({sdom} -> {ddom}): {width} bits cross "
                            f"from one register that is not Gray-coded ({why})")
        if ok:
            gray_dirs[inst_of(scell)].add((sdom, ddom))
        second = sorted({n.rsplit("[", 1)[0] for n in s2_ok[(scell, dcell)]}) or ["(none)"]
        report.append(f"crossing {sdom} -> {ddom}: {scell} -> {dcell} -> {', '.join(second)}: "
                      f"{method}: {'PASS' if ok else 'FAIL'}")

    # ---- M1: memory read crossings ------------------------------------------
    seen = set()
    for dcell, mem, ddom in mem_cross:
        if (mem, ddom) in seen:
            continue
        seen.add((mem, ddom))
        c = nl.cells[mem]
        wdoms = mem_dom[mem]
        addr_doms = {leaf_domain(lf) for a in c["connections"].get("RD_ADDR", [])
                     for lf in nl.leaves(a)} - {None}
        inst = inst_of(mem)
        need = {(w, ddom) for w in wdoms} | {(ddom, w) for w in wdoms}
        problems = []
        if addr_doms - {ddom}:
            problems.append(f"read address depends on {sorted(addr_doms - {ddom})}")
        if not need <= gray_dirs.get(inst, set()):
            problems.append(f"no passing Gray-pointer crossings both ways in '{inst}'")
        if problems:
            errs.append(f"M1 memory {mem} written in {sorted(wdoms)} is read in {ddom}: "
                        + "; ".join(problems))
        report.append(f"crossing {sorted(wdoms)} -> {ddom}: storage read {mem} "
                      f"(read address from {sorted(addr_doms)}; protected by the FIFO "
                      f"pointer protocol, not proven here): {'FAIL' if problems else 'PASS'}")

    # ---- O1: output ports are driven only from their own domain ------------
    for pname, p in nl.ports.items():
        if p["direction"] != "output":
            continue
        pdom = port_domain(pname)
        for i, b in enumerate(p["bits"]):
            for lf in nl.leaves(b):
                ld = leaf_domain(lf) if lf[0] != "mem" else None
                if lf[0] == "mem":
                    ld = next(iter(mem_dom.get(lf[1]) or {None}))
                if ld is not None and ld != pdom:
                    errs.append(f"O1 output {pname}[{i}] ({pdom}) depends on {lf[1]} ({ld})")

    if not any(r.startswith("crossing") for r in report):
        report.append("no clock-domain crossings found")
    return report, errs


def inst_of(cname: str) -> str:
    """Instance path of a flattened cell name ('$driver$u_afifo.wr_gray_q' -> 'u_afifo')."""
    n = short(cname)
    return n.rsplit(".", 1)[0] if "." in n else ""


def short(cname: str) -> str:
    """Cell name without the '$driver$' prefix the Yosys slang frontend adds to flops."""
    return cname[len("$driver$"):] if cname.startswith("$driver$") else cname


def is_gray_register(nl: Netlist, cname: str) -> Tuple[bool, str]:
    """True if the register's D bits are N[i] xor N[i+1] (top bit N[top]) from one xor cell."""
    d = nl.cells[cname]["connections"]["D"]
    w = len(d)
    drv = [nl.driver.get(b) if isinstance(b, int) else None for b in d]
    xors = {x[1] for x in drv if x and x[0] == "cell" and nl.cells[x[1]]["type"] == "$xor"}
    if len(xors) != 1:
        kinds = sorted({nl.cells[x[1]]["type"] if x and x[0] == "cell" else str(x and x[0])
                        for x in drv})
        return False, f"D inputs come from {kinds}, not from one xor encoder"
    xc = nl.cells[xors.pop()]
    a, bb = xc["connections"]["A"], xc["connections"]["B"]
    y = xc["connections"]["Y"]
    for n, s in ((a, bb), (bb, a)):                    # xor is symmetric
        good = True
        for i in range(w):
            db = d[i]
            if i < w - 1:
                good &= (len(n) > i + 1 and y[i] == db and s[i] == n[i + 1])
            else:                                       # top bit: N[top] xor 0, or N[top]
                good &= (db == n[i]) or (y[i] == db and s[i] == "0")
        if good:
            return True, "gray"
    return False, "xor inputs are not a value and the same value shifted right by one"


def short_all(text: str) -> str:
    """Remove every '$driver$' prefix from a message."""
    return text.replace("$driver$", "")


def build_netlist(rtl_dir: str, out_json: str, top: str = "neurogate_top") -> None:
    """Elaborate every rtl_dir/*.sv with Yosys (slang frontend) and write a JSON netlist.

    Runs from the repository root so the ROM files of mlp_core are found. No
    optimization beyond removing unused wires: the netlist keeps one cell per
    RTL operator, which is what the rules look at."""
    import glob
    import os
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    srcs = sorted(glob.glob(os.path.join(os.path.abspath(rtl_dir), "*.sv")))
    if not srcs:
        raise FileNotFoundError(f"no .sv files in {rtl_dir}")
    os.makedirs(os.path.dirname(os.path.abspath(out_json)), exist_ok=True)
    script = (f"read_slang {' '.join(srcs)} --top {top}; proc; flatten; opt_clean; "
              f"memory_collect; opt_clean; write_json {os.path.abspath(out_json)}")
    r = subprocess.run(["yosys", "-m", "slang", "-q", "-p", script], cwd=root,
                       capture_output=True, text=True, check=False)
    if r.returncode != 0:
        raise RuntimeError(f"yosys failed ({r.returncode}):\n{r.stdout}\n{r.stderr}")


def run(netlist: str, top: str = "neurogate_top") -> Tuple[int, List[str]]:
    """Check one netlist file. Returns (exit status, printed lines)."""
    with open(netlist) as f:
        mod = json.load(f)["modules"][top]
    report, errs = check(mod)
    errs = [short_all(e) for e in dict.fromkeys(errs)]      # drop repeats, keep order
    lines = [f"cdc_check: top={top} (structural self-check, not a commercial CDC sign-off)"]
    lines += [f"  {short_all(r)}" for r in report]
    lines += [f"  VIOLATION {e}" for e in errs]
    lines.append(f"cdc_check: {len(errs)} violation(s): {'FAIL' if errs else 'PASS'}")
    return (1 if errs else 0), lines


def main() -> int:
    """Build (with --rtl) or load the netlist, run the check, print, return 1 on a violation."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("netlist", help="JSON netlist (written first when --rtl is given)")
    ap.add_argument("--rtl", help="folder of .sv files to elaborate with Yosys first")
    ap.add_argument("--top", default="neurogate_top")
    args = ap.parse_args()
    if args.rtl:
        build_netlist(args.rtl, args.netlist, args.top)
    rc, lines = run(args.netlist, args.top)
    print("\n".join(lines))
    return rc


if __name__ == "__main__":
    sys.exit(main())
