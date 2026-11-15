"""Regression test for the structural clock-crossing check (scripts/cdc_check.py).

Why this test exists
    RTL simulation cannot see the faults that make a clock crossing unsafe in
    silicon (metastability, bits of a bus arriving at different times). At M2,
    sending binary FIFO pointers across, or cutting each synchronizer to one
    flop, passed every FIFO simulation test (DECISIONS D-019). So the chip is
    checked structurally instead, and this test proves the structural check
    is not toothless: it must PASS on the real RTL and FAIL, with the expected
    rule, on each planted fault below.

How
    Each fault is a text edit on a COPY of rtl/ in sim_build/cdc/<name>/; the
    real rtl/ folder is never touched. Every edit must match the source exactly
    once, so a later RTL change that makes a mutation stale fails this test
    instead of silently testing nothing. Needs Yosys with the slang frontend
    (OSS CAD Suite). No randomness; SEED is printed only to follow the
    project's logging rule.
"""
import os
import shutil
import sys
from typing import List, Tuple

TB_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TB_DIR)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import cdc_check  # noqa: E402

SEED = int(os.environ.get("SEED", "12345"))
OUT = os.path.join(ROOT, "sim_build", "cdc")

# (name, what it models, [(file, old text, new text)], rule code that must fire)
Mutation = Tuple[str, str, List[Tuple[str, str, str]], str]
MUTATIONS: List[Mutation] = [
    ("binary_pointers",
     "FIFO pointers cross in binary (the M2 fault); full/empty compares changed to "
     "binary so the FIFO still works in simulation",
     [("async_fifo.sv", "wr_gray_q       <= wr_gray_next;", "wr_gray_q       <= wr_bin_next;"),
      ("async_fifo.sv", "rd_gray_q       <= rd_gray_next;", "rd_gray_q       <= rd_bin_next;"),
      ("async_fifo.sv", "assign full_next = (wr_gray_next ==", "assign full_next = (wr_bin_next =="),
      ("async_fifo.sv", "{~rd_gray_sync2_q[PTR_W-1:PTR_W-2], rd_gray_sync2_q[PTR_W-3:0]}",
       "{~rd_gray_sync2_q[PTR_W-1], rd_gray_sync2_q[PTR_W-2:0]}"),
      ("async_fifo.sv", "assign empty_next   = (rd_gray_next == wr_gray_sync2_q);",
       "assign empty_next   = (rd_bin_next == wr_gray_sync2_q);")],
     "G1"),
    ("one_flop_sync",
     "full/empty use the first synchronizer flop (the other M2 fault)",
     [("async_fifo.sv", "{~rd_gray_sync2_q[PTR_W-1:PTR_W-2], rd_gray_sync2_q[PTR_W-3:0]}",
       "{~rd_gray_sync1_q[PTR_W-1:PTR_W-2], rd_gray_sync1_q[PTR_W-3:0]}"),
      ("async_fifo.sv", "assign empty_next   = (rd_gray_next == wr_gray_sync2_q);",
       "assign empty_next   = (rd_gray_next == wr_gray_sync1_q);")],
     "S2"),
    ("logic_before_sync",
     "Gray code computed by gates on the crossing path instead of from a flop",
     [("async_fifo.sv", "wr_gray_sync1_q <= wr_gray_q;", "wr_gray_sync1_q <= bin2gray(wr_bin_q);")],
     "S1"),
    ("raw_crossing",
     "a core_clk signal (the result-stream ready input) used directly by a net_clk block",
     [("neurogate_top.sv", ".m_tdata(f_tdata), .m_tvalid(f_tvalid), .m_tready(f_tready)",
       ".m_tdata(f_tdata), .m_tvalid(f_tvalid), .m_tready(m_axis_res_tready)")],
     "S1"),
    ("output_wrong_domain",
     "the net_clk input ready driven from core_clk logic (mlp_core ready)",
     [("neurogate_top.sv", ".s_tuser(s_axis_tuser), .s_tvalid(s_axis_tvalid), .s_tready(s_axis_tready),",
       ".s_tuser(s_axis_tuser), .s_tvalid(s_axis_tvalid), .s_tready(),"),
      ("neurogate_top.sv", "    assign d_is_result       = (d_kind == KIND_CLASSIFY);",
       "    assign d_is_result       = (d_kind == KIND_CLASSIFY);\n    assign s_axis_tready = a_tready;")],
     "O1"),
    ("reset_wrong_domain",
     "FIFO read side reset by the net_clk reset synchronizer",
     [("neurogate_top.sv", ".rd_clk(core_clk), .rd_rst_n(core_rst_n),",
       ".rd_clk(core_clk), .rd_rst_n(net_rst_n),")],
     "R2"),
    ("reset_one_flop",
     "reset synchronizer output taken after its first flop",
     [("reset_sync.sv", "assign rst_n_out = sync_q[STAGES-1];", "assign rst_n_out = sync_q[0];")],
     "R1"),
]


def copy_rtl(dst: str) -> None:
    """Fresh copy of rtl/*.sv into dst."""
    shutil.rmtree(dst, ignore_errors=True)
    os.makedirs(dst)
    for f in sorted(os.listdir(os.path.join(ROOT, "rtl"))):
        if f.endswith(".sv"):
            shutil.copy(os.path.join(ROOT, "rtl", f), dst)


def apply(dst: str, edits: List[Tuple[str, str, str]]) -> None:
    """Apply text edits in dst; each old text must occur exactly once."""
    for fname, old, new in edits:
        p = os.path.join(dst, fname)
        with open(p) as fh:
            src = fh.read()
        n = src.count(old)
        assert n == 1, f"mutation is stale: '{old}' found {n} times in {fname}"
        with open(p, "w") as fh:
            fh.write(src.replace(old, new))


def check_dir(rtl_dir: str, name: str) -> Tuple[int, List[str]]:
    """Build the netlist of rtl_dir and run the check."""
    js = os.path.join(OUT, f"{name}.json")
    cdc_check.build_netlist(rtl_dir, js)
    return cdc_check.run(js)


def main() -> int:
    """Real RTL must pass; every planted fault must fail with its rule code."""
    print(f"test_cdc_check: seed={SEED} (not used: no randomness)", flush=True)
    failures = 0
    rc, lines = check_dir(os.path.join(ROOT, "rtl"), "real")
    print("\n".join(lines))
    if rc != 0:
        print("FAIL real RTL: the check reports violations")
        failures += 1
    else:
        print("PASS real RTL: no violations")
    for name, what, edits, rule in MUTATIONS:
        d = os.path.join(OUT, name)
        copy_rtl(d)
        apply(d, edits)
        rc, lines = check_dir(d, name)
        hits = [ln for ln in lines if ln.strip().startswith(f"VIOLATION {rule} ")]
        ok = rc == 1 and bool(hits)
        print(f"{'PASS' if ok else 'FAIL'} planted fault '{name}' ({what}): check exit {rc}, "
              f"{len(hits)} {rule} violation(s)")
        for ln in hits[:2]:
            print(f"    {ln.strip()}")
        failures += 0 if ok else 1
    print(f"test_cdc_check: {1 + len(MUTATIONS)} cases, {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
