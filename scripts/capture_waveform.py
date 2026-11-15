"""Capture the throughput simulation's log and waveform for the results page.

What it does (all *simulated*, RTL, Icarus via cocotb):
  1. Runs ``tb/test_neurogate_top_throughput.py`` (the same test ``make test`` runs:
     14 classified frames back to back into ``neurogate_top``, result stream always
     ready) with ``NG_WAVES=1``, so Icarus records every top-level signal to an FST
     dump in ``sim_build/neurogate_top_throughput/``.
  2. Keeps the test's own result lines in ``docs/results/throughput_run.log`` (with
     the command, seed and git state), so the throughput numbers on the results page
     come from a committed file.
  3. Converts the dump to VCD (``fst2vcd`` from the OSS CAD Suite) and keeps only the
     signals the page draws (listed in ``SIGNALS``), unchanged, in
     ``docs/results/wave_throughput.vcd``.

The results page generator (``scripts/build_results_page.py``) only reads these
committed files; it never runs a simulator. Run this script again only when the
design or the test changes:

    source .venv/bin/activate; . scripts/env.sh
    python3 scripts/capture_waveform.py      # about 2 s
"""
import os
import re
import subprocess
import sys
from typing import Dict, List, Set

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "docs", "results")
LOG_OUT = os.path.join(OUT_DIR, "throughput_run.log")
VCD_OUT = os.path.join(OUT_DIR, "wave_throughput.vcd")
FST_IN = os.path.join(ROOT, "sim_build", "neurogate_top_throughput", "neurogate_top.fst")
SEED = 12345

# Top-level signals of neurogate_top kept in the trimmed dump (names as in the RTL).
SIGNALS = [
    "net_clk", "s_axis_tvalid", "s_axis_tready", "s_axis_tlast",  # packet input
    "f_tvalid", "f_tready",          # feature entry into the clock-crossing queue
    "core_clk", "a_tvalid", "a_tready",  # entry taken out of the queue by the network core
    "m_axis_res_tvalid", "m_axis_res_tready", "m_axis_res_tdata",  # result stream
]


def git_state() -> str:
    """Return 'commit <hash>' plus whether rtl/ and tb/ had uncommitted changes."""
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                          text=True, check=False).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--", "rtl", "tb"], cwd=ROOT,
                           capture_output=True, text=True, check=False).stdout.strip()
    return f"commit {head}; rtl/ and tb/ {'MODIFIED' if dirty else 'unmodified'}"


def run_test() -> List[str]:
    """Run the throughput test with the wave dump on; return its output lines."""
    env = dict(os.environ, NG_WAVES="1", SEED=str(SEED))
    res = subprocess.run([sys.executable, os.path.join(ROOT, "tb",
                                                        "test_neurogate_top_throughput.py")],
                         cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    lines = (res.stdout + res.stderr).splitlines()
    if res.returncode != 0:
        print("\n".join(lines[-30:]))
        raise SystemExit(f"capture: throughput test failed (exit {res.returncode})")
    return lines


def write_log(lines: List[str]) -> None:
    """Keep the result lines of the test (no tool paths) plus provenance."""
    keep = [ln.strip() for ln in lines
            if "throughput (simulated" in ln or re.search(r"\d+ passed, \d+ failed", ln)]
    keep = [re.sub(r"\s{2,}", " ", k) for k in keep]
    with open(LOG_OUT, "w") as f:
        f.write("# Throughput test output, kept for the results page (simulated, RTL, Icarus).\n")
        f.write(f"# command: NG_WAVES=1 SEED={SEED} python3 tb/test_neurogate_top_throughput.py\n")
        f.write(f"# source: {git_state()}\n")
        f.write("# written by scripts/capture_waveform.py; only result lines are kept\n")
        for k in keep:
            f.write(k + "\n")


def trim_vcd(text: str, keep: Set[str]) -> str:
    """Return a VCD with only the top-scope signals named in ``keep``.

    Value changes are copied unchanged; a time stamp is kept only if a kept signal
    changes at it. Signals in sub-modules are dropped.
    """
    out: List[str] = []
    ids: Dict[str, str] = {}
    depth = 0
    lines = text.splitlines()
    i = 0
    # Header: copy date/version/timescale, keep only wanted $var lines in the top scope.
    while i < len(lines):
        ln = lines[i]
        i += 1
        tok = ln.split()
        if not tok:
            continue
        if tok[0] == "$scope":
            depth += 1
            if depth == 1:
                out.append(ln)
            continue
        if tok[0] == "$upscope":
            if depth == 1:
                out.append(ln)
            depth -= 1
            continue
        if tok[0] == "$var":
            if depth == 1 and tok[4] in keep:
                ids[tok[3]] = tok[4]
                out.append(ln)
            continue
        if tok[0] == "$enddefinitions":
            out.append(ln)
            break
        if depth == 0 and tok[0] in ("$timescale", "$version", "$date"):
            # Copy the block up to its $end (may span lines). The date of the
            # conversion is dropped so the file does not change on every capture.
            block = [ln]
            while "$end" not in block[-1]:
                block.append(lines[i])
                i += 1
            if tok[0] != "$date":
                out.extend(block)
    missing = keep - set(ids.values())
    if missing:
        raise SystemExit(f"capture: signals not found in the dump: {sorted(missing)}")
    # Body.
    pending_time = None
    for ln in lines[i:]:
        s = ln.strip()
        if not s or s.startswith("$dumpvars") or s == "$end":
            continue
        if s.startswith("#"):
            pending_time = s
            continue
        if s[0] in "bBrR":
            code = s.split()[1]
        else:
            code = s[1:]
        if code in ids:
            if pending_time is not None:
                out.append(pending_time)
                pending_time = None
            out.append(s)
    return "\n".join(out) + "\n"


def main() -> int:
    """Run the test, write the log and the trimmed VCD."""
    lines = run_test()
    write_log(lines)
    vcd = subprocess.run(["fst2vcd", FST_IN], capture_output=True, text=True, check=True).stdout
    with open(VCD_OUT, "w") as f:
        f.write(trim_vcd(vcd, set(SIGNALS)))
    print(f"capture: wrote {os.path.relpath(LOG_OUT, ROOT)} and {os.path.relpath(VCD_OUT, ROOT)}"
          f" ({os.path.getsize(VCD_OUT)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
