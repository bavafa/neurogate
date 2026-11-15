"""Run the full test-split replay (`make test-full`). Not named test_*.py so that
`make test` skips it; see tb/full_split_checks.py for what is compared.

Run from .venv: `python3 tb/full_split_run.py` (seed from SEED, default 12345).
"""
import os
import subprocess
import sys

TB_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TB_DIR)
sys.path.insert(0, TB_DIR)
from cocotb_run import run_cocotb  # noqa: E402


def main() -> int:
    """Export the vectors with the golden model (needs NumPy/Scapy from .venv), then simulate."""
    seed = int(os.environ.get("SEED", "12345"))
    print(f"full_split_run: seed={seed}", flush=True)
    rc = subprocess.run([sys.executable, "-m", "model.export_vectors", "--full"], cwd=ROOT).returncode
    if rc:
        return rc
    roms = {n: os.path.join(ROOT, "data", "model", f) for n, f in
            (("ROM_W1", "rom_w1.hex"), ("ROM_B1", "rom_b1.hex"),
             ("ROM_W2", "rom_w2.hex"), ("ROM_B2", "rom_b2.hex"))}
    return run_cocotb("mlp_decision_tb", ["rtl/mlp_core.sv", "rtl/decision_unit.sv", "tb/mlp_decision_tb.sv"],
                      "full_split_checks", seed, defines=roms)


if __name__ == "__main__":
    sys.exit(main())
