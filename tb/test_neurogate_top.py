"""Top-level smoke test of neurogate_top (two clocks), started by `make test`.

Writes a small generated frame file (Scapy runs here, in .venv), then runs the
cocotb checks in tb/neurogate_top_checks.py against all RTL with Icarus.
The frame set is fixed: traffic_gen seed 1, one scenario per class, 40 packets
each (120 frames; the Python models classify some as each of the three classes
and drop 10). SEED drives the clock phase, input lane garbage and result-stream
stalls, and is printed in the log. All results are *simulated*.
"""
import os
import sys

TB_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TB_DIR)
sys.path.insert(0, ROOT)
sys.path.insert(0, TB_DIR)

SEED = int(os.environ.get("SEED", "12345"))
TRAFFIC_SEED, PER_SCENARIO = 1, 40


def main() -> int:
    """Write the frames, run the cocotb smoke test, return its exit status."""
    from cocotb_run import run_cocotb
    from model import traffic_gen as G
    print(f"test_neurogate_top: seed={SEED} traffic seed={TRAFFIC_SEED} "
          f"packets per scenario={PER_SCENARIO}", flush=True)
    out_dir = os.path.join(ROOT, "sim_build", "neurogate_top")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"frames_seed{TRAFFIC_SEED}_{PER_SCENARIO}.txt")
    with open(path, "w") as f:
        for _, _, fr in G.generate(TRAFFIC_SEED, 1, PER_SCENARIO):
            f.write(f"{int(fr.tuser)} {fr.data.hex()}\n")
    srcs = sorted(os.path.join("rtl", f) for f in os.listdir(os.path.join(ROOT, "rtl"))
                  if f.endswith(".sv"))
    return run_cocotb("neurogate_top", srcs, "neurogate_top_checks", SEED,
                      {"TOP_FRAMES": path},
                      defines={n: os.path.join(ROOT, "data", "model", f) for n, f in
                               (("ROM_W1", "rom_w1.hex"), ("ROM_B1", "rom_b1.hex"),
                                ("ROM_W2", "rom_w2.hex"), ("ROM_B2", "rom_b2.hex"))})


if __name__ == "__main__":
    sys.exit(main())
