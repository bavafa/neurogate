"""Top-level throughput measurement, started by `make test` (RTL, *simulated*).

Writes a frame file of 14 generated frames that the Python models classify (traffic
seed 1, scenarios in order, first 14 kept), then runs tb/neurogate_top_throughput_checks.py.
"""
import os
import sys

TB_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TB_DIR)
sys.path.insert(0, ROOT)
sys.path.insert(0, TB_DIR)

SEED = int(os.environ.get("SEED", "12345"))
N_FRAMES = 14


def main() -> int:
    """Pick the frames, run the cocotb measurement, return its exit status."""
    from cocotb_run import run_cocotb
    from model import traffic_gen as G
    from top_common import model_frames, read_thresh
    cand = [(fr.data, bool(fr.tuser)) for _, _, fr in G.generate(1, 1, 40)]
    want = model_frames(cand, read_thresh())
    frames = [c for c, w in zip(cand, want) if w is not None][:N_FRAMES]
    out_dir = os.path.join(ROOT, "sim_build", "neurogate_top_throughput")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "frames.txt")
    with open(path, "w") as f:
        for data, tuser in frames:
            f.write(f"{int(tuser)} {data.hex()}\n")
    print(f"test_neurogate_top_throughput: seed={SEED} frames={len(frames)}", flush=True)
    srcs = sorted(os.path.join("rtl", f) for f in os.listdir(os.path.join(ROOT, "rtl"))
                  if f.endswith(".sv"))
    return run_cocotb("neurogate_top", srcs, "neurogate_top_throughput_checks", SEED,
                      {"TOP_FRAMES": path}, build_name="neurogate_top_throughput",
                      defines={n: os.path.join(ROOT, "data", "model", f) for n, f in
                               (("ROM_W1", "rom_w1.hex"), ("ROM_B1", "rom_b1.hex"),
                                ("ROM_W2", "rom_w2.hex"), ("ROM_B2", "rom_b2.hex"))})


if __name__ == "__main__":
    sys.exit(main())
