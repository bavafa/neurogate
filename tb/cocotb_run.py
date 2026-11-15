"""Shared helper: build one RTL module with Icarus and run its cocotb tests.

Why this file exists (DECISIONS D-012): cocotb loads Python *inside* the
simulator, so it needs a shared ``libpython``. The project ``.venv`` uses the
system Python 3.13, whose shared library is not installed on the build
machine (and system packages cannot be added). The OSS CAD Suite ships its
own Python 3.11 (``tabbypy3``) with a shared libpython and cocotb, built to
match its Icarus. So the testbench scripts start from ``.venv`` (where Scapy
lives, for traffic generation) and hand the simulation to that Python.

Usage from a test script::

    from cocotb_run import run_cocotb
    sys.exit(run_cocotb("eth_parser", ["rtl/eth_parser.sv"], "test_eth_parser", seed))

Can also be run directly (this is what ``run_cocotb`` does under the hood)::

    tabbypy3 tb/cocotb_run.py --toplevel eth_parser --source rtl/eth_parser.sv \
        --module test_eth_parser --seed 12345
"""
import argparse
import os
import shutil
import subprocess
import sys
from typing import Dict, List, Optional

TB_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TB_DIR)


def sim_python() -> str:
    """Python used to run cocotb: $NG_SIM_PYTHON, else OSS CAD Suite's tabbypy3."""
    env = os.environ.get("NG_SIM_PYTHON")
    if env:
        return env
    found = shutil.which("tabbypy3")
    if found:
        return found
    default = os.path.join(os.environ.get("NG_TOOLS", os.path.join(os.path.expanduser("~"), "tools")),
                           "oss-cad-suite", "bin", "tabbypy3")
    if os.path.exists(default):
        return default
    raise FileNotFoundError("no simulation Python: set NG_SIM_PYTHON or put tabbypy3 on PATH")


def run_cocotb(toplevel: str, sources: List[str], test_module: str, seed: int,
               extra_env: Optional[Dict[str, str]] = None,
               parameters: Optional[Dict[str, str]] = None, build_name: str = "",
               defines: Optional[Dict[str, str]] = None) -> int:
    """Run the cocotb tests of ``test_module`` against ``toplevel`` in a child process.

    Returns 0 if every test passed, 1 otherwise (including build errors).
    ``extra_env`` is passed to the test module as environment variables.
    ``parameters`` overrides HDL parameters (values are passed to Icarus as
    written, so a string parameter needs its own double quotes).
    ``build_name`` (default: the toplevel) names the build folder under
    sim_build/ and the results file, so several builds of one toplevel with
    different parameters keep separate evidence.
    ``defines`` are Verilog text macros (-D) given to the build, values as written.
    """
    cmd = [sim_python(), os.path.abspath(__file__), "--toplevel", toplevel,
           "--module", test_module, "--seed", str(seed)]
    for s in sources:
        cmd += ["--source", os.path.join(ROOT, s)]
    for k, v in (parameters or {}).items():
        cmd += ["--param", f"{k}={v}"]
    if build_name:
        cmd += ["--build-name", build_name]
    for k, v in (defines or {}).items():
        cmd += ["--define", f"{k}={v}"]
    env = dict(os.environ)
    env.update(extra_env or {})
    env["SEED"] = str(seed)
    # The child Python must not pick up .venv's 3.13 packages.
    env.pop("PYTHONPATH", None)
    env.pop("VIRTUAL_ENV", None)
    return subprocess.run(cmd, env=env, check=False).returncode


def _child(toplevel: str, sources: List[str], test_module: str, seed: int,
           parameters: Dict[str, str], build_name: str, defines: Dict[str, str]) -> int:
    """Runs inside the simulation Python: build with Icarus, run tests, count failures."""
    from cocotb_tools.check_results import get_results
    from cocotb_tools.runner import get_runner

    build_name = build_name or toplevel
    build_dir = os.path.join(ROOT, "sim_build", build_name)
    # NG_WAVES=1 records every signal of the toplevel to sim_build/<build>/<toplevel>.fst
    # (used only by scripts/capture_waveform.py for the results page; off by default
    # because a dump slows the run and is large).
    waves = os.environ.get("NG_WAVES", "0") == "1"
    runner = get_runner("icarus")
    runner.build(sources=sources, hdl_toplevel=toplevel, build_dir=build_dir,
                 always=True, timescale=("1ns", "1ps"), parameters=parameters, defines=defines,
                 waves=waves)
    # With NG_RESULTS_DIR set (make test does this), each suite writes its own
    # xUnit file there, so one run never overwrites another suite's evidence.
    out_dir = os.environ.get("NG_RESULTS_DIR")
    xml_name = test_module if build_name == toplevel else f"{test_module}-{build_name}"
    xml = os.path.join(out_dir, f"{xml_name}.xml") if out_dir else None
    results = runner.test(hdl_toplevel=toplevel, test_module=test_module, test_dir=TB_DIR,
                          build_dir=build_dir, seed=seed, extra_env={"SEED": str(seed)},
                          results_xml=xml, waves=waves)
    total, failed = get_results(results)
    tag = "" if build_name == toplevel else f" [{build_name}]"
    print(f"{test_module}{tag}: {total - failed} passed, {failed} failed (seed={seed})")
    return 1 if failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--toplevel", required=True)
    ap.add_argument("--source", action="append", required=True)
    ap.add_argument("--module", required=True)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--param", action="append", default=[], help="NAME=VALUE HDL parameter")
    ap.add_argument("--build-name", default="")
    ap.add_argument("--define", action="append", default=[], help="NAME=VALUE text macro")
    a = ap.parse_args()
    params = dict(p.split("=", 1) for p in a.param)
    defs = dict(d.split("=", 1) for d in a.define)
    sys.exit(_child(a.toplevel, a.source, a.module, a.seed, params, a.build_name, defs))
