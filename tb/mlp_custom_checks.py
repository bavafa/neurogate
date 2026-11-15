"""cocotb check for one custom weight set on rtl/mlp_core.sv (started by
tb/test_mlp_core.py, one build per set; the sets are made by
tb/mlp_custom_sets.py). Reads weights.json and inputs.txt from
$MLP_CUSTOM_DIR, runs every input through the RTL, and compares the three
int32 logits and the class with model/golden.py, bit for bit. Runs twice:
back to back, and with random input gaps and output stalls.
"""
import os
import random
import sys

import cocotb

TB_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(TB_DIR, ".."))
sys.path.insert(0, TB_DIR)
from model import golden  # noqa: E402
from mlp_common import SEED, classify_jobs, run_jobs  # noqa: E402


def load_set():
    """(name, params, input vectors) of the set named in the environment."""
    d = os.environ["MLP_CUSTOM_DIR"]
    p = golden.load_params(os.path.join(d, "weights.json"))
    with open(os.path.join(d, "inputs.txt")) as fh:
        xs = [[int(v) for v in line.split()] for line in fh if line.strip()]
    return os.environ.get("MLP_CUSTOM_NAME", os.path.basename(d)), p, xs


@cocotb.test(timeout_time=200, timeout_unit="ms")
async def test_custom_set(dut) -> None:
    """Every input of the set, golden vs RTL: logits and class."""
    name, p, xs = load_set()
    jobs = classify_jobs(xs, random.Random(f"{SEED}-{name}"))
    _, s1 = await run_jobs(dut, jobs, p, f"{name}")
    _, s2 = await run_jobs(dut, jobs, p, f"{name}_backpressure", 0.3, 0.5, clock=False)
    classes = [golden.infer(x, p)[0] for x in xs]
    dut._log.info(f"custom set {name} (simulated): shift1={p.shift1} vectors={len(xs)} x 2 runs, "
                  f"logit mismatches {s1['logit_mismatch'] + s2['logit_mismatch']}, class "
                  f"mismatches {s1['class_mismatch'] + s2['class_mismatch']}; golden classes "
                  f"0/1/2 = {classes.count(0)}/{classes.count(1)}/{classes.count(2)}")
