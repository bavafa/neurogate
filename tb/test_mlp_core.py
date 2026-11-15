"""cocotb tests for rtl/mlp_core.sv (int8 8-16-3 network, one multiply-accumulate
per clock, SPEC.md section 6). The reference for every bit is model/golden.py.

Run: `python3 tb/test_mlp_core.py` from .venv (or `make test`). The seed comes
from SEED (default 12345) and is printed. Exits non-zero on any failure.

Build 1, trained weights (this module; ROMs from data/model/rom_*.hex, SHIFT1
left at its RTL default, which must equal shift1 in data/model/weights.json):
  * every row of data/vectors/mlp_vectors.csv: the three int32 logits and the
    class must equal the CSV, and the CSV must equal golden.infer run now
    (so a stale CSV cannot hide a difference); back to back, then again with
    random input gaps and random output stalls;
  * DROP / OVF_ONLY entries mixed with CLASSIFY entries: pass-through order
    and metadata, zero logits for non-CLASSIFY, garbage features ignored;
  * cycles per inference (simulated): latency and back-to-back rate;
  * reset in the middle of an inference and while a result waits on a stall.
Builds 2..N, custom weights (tb/mlp_custom_checks.py, one build per weight
set, written by main() into sim_build/): int32 extremes (accumulator exactly
at the int32 maximum with the rounding add passing it; logits at the int32
maximum and minimum), round-half-up with shift 2, shift 0 (no rounding term),
hidden saturation at 127, every hidden unit zero (logits = biases, with ties
between classes), and random weights with several shifts.
"""
import csv
import os
import random
import re
import sys
from typing import List, Tuple

import cocotb
from cocotb.triggers import RisingEdge

TB_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(TB_DIR, ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, TB_DIR)
from model import golden  # noqa: E402
from mlp_common import (KIND_CLASSIFY, KIND_DROP, KIND_OVF_ONLY, SEED, Job, MlpEnv,  # noqa: E402
                        Result, classify_jobs, compare, expected, run_jobs)

VECTORS = os.path.join(ROOT, "data", "vectors", "mlp_vectors.csv")
WEIGHTS = os.path.join(ROOT, "data", "model", "weights.json")
TEST_TIMEOUT_MS = 200
# Cycle counts measured in simulation and pinned here, so a change is noticed
# (see the RTL header for where they come from).
EXPECT_LATENCY = 178          # accept edge -> edge at which the result is taken
EXPECT_INTERVAL = 179         # accept edge -> next accept edge, back to back
EXPECT_PASS_LATENCY = 1       # DROP / OVF_ONLY


def load_vectors() -> List[Tuple[str, List[int], Tuple[int, int, int], int]]:
    """Rows of mlp_vectors.csv as (kind, features, logits, class)."""
    rows = []
    with open(VECTORS) as fh:
        for r in csv.DictReader(fh):
            rows.append((r["kind"], [int(r[f"f{i}"]) for i in range(8)],
                         (int(r["z0"]), int(r["z1"]), int(r["z2"])), int(r["class"])))
    return rows


def csv_expected(rows, jobs: List[Job], p: golden.QParams) -> List[Result]:
    """Expected results from the CSV, after checking the CSV against golden.infer."""
    exp = []
    for (kind, x, z, cls), j in zip(rows, jobs):
        g = expected(j, p)
        assert (g.logits, g.cls) == (z, cls), f"CSV row {x} disagrees with golden.infer: {g}"
        exp.append(Result(j.kind, j.pkt_len, j.pkt_id, j.ovf_cnt, z, cls))
    return exp


@cocotb.test(timeout_time=TEST_TIMEOUT_MS, timeout_unit="ms")
async def test_all_vectors(dut) -> None:
    """All rows of mlp_vectors.csv, back to back, no output stalls. Also measures
    the cycles per inference (simulated)."""
    p = golden.load_params(WEIGHTS)
    rows = load_vectors()
    jobs = classify_jobs([x for _, x, _, _ in rows], random.Random(SEED))
    env, stats = await run_jobs(dut, jobs, p, "all_vectors", exp=csv_expected(rows, jobs, p))
    kinds = {}
    for k, _, _, _ in rows:
        kinds[k] = kinds.get(k, 0) + 1
    lat = [r - a for a, r in zip(env.accept_edges, env.result_edges)]
    ii = [b - a for a, b in zip(env.accept_edges, env.accept_edges[1:])]
    dut._log.info(f"mlp_vectors.csv rows by kind: {kinds}; total {len(rows)}; "
                  f"logit mismatches {stats['logit_mismatch']}, class mismatches "
                  f"{stats['class_mismatch']}")
    dut._log.info(f"cycles per inference (simulated, core_clk): latency min {min(lat)} max {max(lat)}; "
                  f"back-to-back interval min {min(ii)} max {max(ii)}")
    assert set(lat) == {EXPECT_LATENCY}, f"latency {set(lat)} != {EXPECT_LATENCY}"
    assert set(ii) == {EXPECT_INTERVAL}, f"interval {set(ii)} != {EXPECT_INTERVAL}"


@cocotb.test(timeout_time=TEST_TIMEOUT_MS, timeout_unit="ms")
async def test_all_vectors_backpressure(dut) -> None:
    """All rows again, with random input gaps (30%) and output stalls (50%)."""
    p = golden.load_params(WEIGHTS)
    rows = load_vectors()
    jobs = classify_jobs([x for _, x, _, _ in rows], random.Random(SEED + 1))
    await run_jobs(dut, jobs, p, "all_vectors_backpressure", 0.3, 0.5,
                   exp=csv_expected(rows, jobs, p))


@cocotb.test(timeout_time=TEST_TIMEOUT_MS, timeout_unit="ms")
async def test_passthrough_mixed(dut) -> None:
    """DROP and OVF_ONLY entries (with garbage in the feature bits) mixed with
    CLASSIFY entries: same order out, metadata copied, zero logits and class 0
    for the pass-through kinds. Pass-through latency is pinned too."""
    p = golden.load_params(WEIGHTS)
    rng = random.Random(SEED + 2)
    rows = load_vectors()
    jobs: List[Job] = []
    for n in range(600):
        kind = rng.choice([KIND_CLASSIFY, KIND_DROP, KIND_DROP, KIND_OVF_ONLY])
        feats = tuple(rows[rng.randrange(len(rows))][1]) if kind == KIND_CLASSIFY \
            else tuple(rng.randrange(256) for _ in range(8))
        jobs.append(Job(kind, feats, rng.randrange(65536), rng.randrange(65536), rng.randrange(65536)))
    env, _ = await run_jobs(dut, jobs, p, "passthrough_mixed", 0.2, 0.3)
    # Latency of pass-through entries, measured without stalls.
    env2, _ = await run_jobs(dut, [Job(KIND_DROP, (0,) * 8, 60, i, 0) for i in range(20)], p,
                             "passthrough_latency", clock=False)
    lat = {r - a for a, r in zip(env2.accept_edges, env2.result_edges)}
    ii = {b - a for a, b in zip(env2.accept_edges, env2.accept_edges[1:])}
    dut._log.info(f"pass-through (simulated): latency {lat}, back-to-back interval {ii} cycles")
    assert lat == {EXPECT_PASS_LATENCY}, lat


@cocotb.test(timeout_time=TEST_TIMEOUT_MS, timeout_unit="ms")
async def test_reset_mid_inference(dut) -> None:
    """Reset at several points of an inference, and while a finished result waits
    on a stalled output. Nothing from the abandoned entry may come out; the
    next entries are computed correctly from a clean start."""
    p = golden.load_params(WEIGHTS)
    rows = load_vectors()
    rng = random.Random(SEED + 3)
    first = True
    for cut in (1, 2, 9, 60, 127, 128, 129, 150, 176, 177, 178, 400):
        env = MlpEnv(dut, rng)
        await env.start(clock=first)
        first = False
        dut.m_ready.value = 0
        env.stall_prob = 1.0 if cut == 400 else 0.0     # 400: result waits, stalled
        x = rows[rng.randrange(len(rows))][1]
        await env.send(Job(KIND_CLASSIFY, tuple(x), 1, 0xDEAD, 0))
        for _ in range(cut - 1):
            await RisingEdge(dut.clk)
        dut.rst_n.value = 0
        await RisingEdge(dut.clk)
        await RisingEdge(dut.clk)
        dut.rst_n.value = 1
        env.stall_prob = 0.0
        taken_before = len(env.results)
        jobs = classify_jobs([rows[rng.randrange(len(rows))][1] for _ in range(3)], rng)
        for j in jobs:
            await env.send(j)
        await env.wait_results(taken_before + 3)
        for _ in range(5):
            await RisingEdge(dut.clk)
        await env.stop()
        after = env.results[taken_before:]
        errs, _ = compare(after, [expected(j, p) for j in jobs], f"reset_at_{cut}")
        # Only cut >= 178 may have delivered the first result (before the reset).
        early = env.results[:taken_before]
        if cut < EXPECT_LATENCY + 1 or cut == 400:
            if early:
                errs.append(f"reset_at_{cut}: abandoned entry produced {early}")
        errs = env.errors + errs
        assert not errs, "\n".join(errs[:10])
    dut._log.info(f"reset_mid_inference: seed={SEED} 12 reset points, no partial result, "
                  f"clean restart each time")


# ---- Runner (from .venv) ----------------------------------------------------------
def check_shift_default() -> None:
    """The RTL default SHIFT1 must equal shift1 in weights.json (build 1 relies on it)."""
    src = open(os.path.join(ROOT, "rtl", "mlp_core.sv")).read()
    m = re.search(r"parameter\s+int\s+unsigned\s+SHIFT1\s*=\s*(\d+)", src)
    want = golden.load_params(WEIGHTS).shift1
    assert m and int(m.group(1)) == want, f"mlp_core SHIFT1 default != weights.json shift1 ({want})"
    print(f"mlp_core SHIFT1 default = {m.group(1)} = weights.json shift1", flush=True)


def rom_params(rom_dir: str, shift1: int = -1) -> dict:
    """HDL parameters pointing the ROMs at ``rom_dir`` (absolute, quoted for Icarus)."""
    p = {name: f'"{os.path.join(rom_dir, f)}"' for name, f in
         (("W1_HEX", "rom_w1.hex"), ("B1_HEX", "rom_b1.hex"),
          ("W2_HEX", "rom_w2.hex"), ("B2_HEX", "rom_b2.hex"))}
    if shift1 >= 0:
        p["SHIFT1"] = str(shift1)
    return p


def main() -> int:
    """Run build 1 (trained weights) and one build per custom weight set."""
    from cocotb_run import run_cocotb
    import mlp_custom_sets

    print(f"test_mlp_core: seed={SEED}", flush=True)
    check_shift_default()
    rc = run_cocotb("mlp_core", ["rtl/mlp_core.sv"], "test_mlp_core", SEED,
                    parameters=rom_params(os.path.join(ROOT, "data", "model")))
    for name, d, shift1 in mlp_custom_sets.write_all(os.path.join(ROOT, "sim_build", "mlp_custom"), SEED):
        rc |= run_cocotb("mlp_core", ["rtl/mlp_core.sv"], "mlp_custom_checks", SEED,
                         {"MLP_CUSTOM_DIR": d, "MLP_CUSTOM_NAME": name},
                         parameters=rom_params(d, shift1), build_name=f"mlp_core_{name}")
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
