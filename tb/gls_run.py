"""Gate-level simulation of neurogate_top (`make gls`, *simulated*).

`python3 tb/gls_run.py`      the 120-frame smoke set (below)
`python3 tb/gls_run.py bad`  the bad-packet set of tb/test_neurogate_top_stress.py
                             (truncated, wrong-EtherType, bad IP header, tuser-error and
                             random frames among good ones, random result backpressure),
                             with the same checks as the RTL bad-packet test
                             (tb/gls_bad_checks.py imports test_bad_packets).

Smoke run: runs the SAME cocotb checks as the RTL smoke test (tb/neurogate_top_checks.py,
same 120 generated frames, same seed handling) against the Yosys netlist
synth/out/neurogate_top_netlist.v plus the Nangate45 cell models
synth/out/nangate45_cells.v, with Icarus. The checks compare every result beat
and every AXI-Lite counter with the Python golden models, which is the same
reference the RTL run uses, so passing here means netlist outputs equal RTL
outputs on these vectors.

The file is called gls_run.py, not test_*.py, so `make test` does not run it
(it needs `make synth` first).
"""
import os
import sys

TB_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TB_DIR)
sys.path.insert(0, ROOT)
sys.path.insert(0, TB_DIR)

SEED = int(os.environ.get("SEED", "12345"))
TRAFFIC_SEED, PER_SCENARIO = 1, 40
NETLIST = "synth/out/neurogate_top_netlist.v"
CELLS = "synth/out/nangate45_cells.v"


def main_bad() -> int:
    """Write the bad-packet frames (same file as the RTL test), run them on the netlist."""
    from cocotb_run import run_cocotb
    from model import traffic_gen as G
    from test_neurogate_top_stress import bad_set, write
    for p in (NETLIST, CELLS):
        if not os.path.exists(os.path.join(ROOT, p)):
            print(f"gls: missing {p}; run `make synth` first")
            return 1
    import random
    rows2 = [(fr.data, fr.tuser) for _, _, fr in G.generate(2, 1, 40)]     # same as the RTL test
    bad = bad_set(random.Random("bad-set-fixed"), rows2)
    out_dir = os.path.join(ROOT, "sim_build", "neurogate_top_gls")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "bad.txt")
    write(path, bad)
    print(f"gls_run bad: seed={SEED} bad-packet set of {len(bad)} frames", flush=True)
    return run_cocotb("neurogate_top", [NETLIST, CELLS], "gls_bad_checks", SEED,
                      {"BAD_FRAMES": path}, build_name="neurogate_top_gls_bad")


def main() -> int:
    """Write the frames, run the smoke checks on the netlist, return the exit status."""
    if sys.argv[1:] == ["bad"]:
        return main_bad()
    from cocotb_run import run_cocotb
    from model import traffic_gen as G
    for p in (NETLIST, CELLS):
        if not os.path.exists(os.path.join(ROOT, p)):
            print(f"gls: missing {p}; run `make synth` first")
            return 1
    print(f"gls_run: seed={SEED} traffic seed={TRAFFIC_SEED} packets per scenario={PER_SCENARIO}",
          flush=True)
    out_dir = os.path.join(ROOT, "sim_build", "neurogate_top_gls")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"frames_seed{TRAFFIC_SEED}_{PER_SCENARIO}.txt")
    with open(path, "w") as f:
        for _, _, fr in G.generate(TRAFFIC_SEED, 1, PER_SCENARIO):
            f.write(f"{int(fr.tuser)} {fr.data.hex()}\n")
    return run_cocotb("neurogate_top", [NETLIST, CELLS], "neurogate_top_checks", SEED,
                      {"TOP_FRAMES": path}, build_name="neurogate_top_gls")


if __name__ == "__main__":
    sys.exit(main())
