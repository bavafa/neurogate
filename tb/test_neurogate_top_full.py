"""Top-level end-to-end test on generated traffic, started by `make test` and `make test-full`.

Writes generated traffic (traffic_gen seed 1: 30 scenarios, normal, port scan and SYN
flood, with VLAN, UDP, ICMP, ARP, fragments, truncated frames and tuser errors) to a
frame file, then runs the cocotb checks in tb/neurogate_top_full_checks.py against all
RTL with Icarus.
Frames per scenario come from the environment variable FULL_PER_SCENARIO:
  `make test`      default 30  -> 900 frames (short; still compares every result beat
                   and every counter with the models, so a real mismatch fails it)
  `make test-full` 300         -> 9000 frames (the whole default data set, about 3 minutes)
SEED drives the clock phase, input lane garbage, input stalls and result-stream
stalls, and is printed in the log. All results are *simulated*, on synthetic data.
"""
import os
import sys

TB_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TB_DIR)
sys.path.insert(0, ROOT)
sys.path.insert(0, TB_DIR)

SEED = int(os.environ.get("SEED", "12345"))
TRAFFIC_SEED, SCEN_PER_CLASS = 1, 10                         # 3 classes x 10 = 30 scenarios
PER_SCENARIO = int(os.environ.get("FULL_PER_SCENARIO", "30"))   # 300 = the default data set


def main() -> int:
    """Write the frames, run the cocotb test, return its exit status."""
    from cocotb_run import run_cocotb
    from model import traffic_gen as G
    print(f"test_neurogate_top_full: seed={SEED} traffic seed={TRAFFIC_SEED} "
          f"scenarios={3 * SCEN_PER_CLASS} x {PER_SCENARIO} frames", flush=True)
    out_dir = os.path.join(ROOT, "sim_build", "neurogate_top_full")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"frames_seed{TRAFFIC_SEED}_{PER_SCENARIO}.txt")
    with open(path, "w") as f:
        for _, _, fr in G.generate(TRAFFIC_SEED, SCEN_PER_CLASS, PER_SCENARIO):
            f.write(f"{int(fr.tuser)} {fr.data.hex()}\n")
    srcs = sorted(os.path.join("rtl", f) for f in os.listdir(os.path.join(ROOT, "rtl"))
                  if f.endswith(".sv"))
    return run_cocotb("neurogate_top", srcs, "neurogate_top_full_checks", SEED,
                      {"TOP_FRAMES": path}, build_name=("neurogate_top_full" if PER_SCENARIO == 30
                                else f"neurogate_top_full_{PER_SCENARIO}"),
                      defines={n: os.path.join(ROOT, "data", "model", f) for n, f in
                               (("ROM_W1", "rom_w1.hex"), ("ROM_B1", "rom_b1.hex"),
                                ("ROM_W2", "rom_w2.hex"), ("ROM_B2", "rom_b2.hex"))})


if __name__ == "__main__":
    sys.exit(main())
