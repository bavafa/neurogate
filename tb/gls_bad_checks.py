"""Gate-level run of the bad-packet test (started by `python3 tb/gls_run.py bad`).

It re-uses test_bad_packets from tb/neurogate_top_stress_checks.py unchanged, so the
netlist gets exactly the checks the RTL gets: every result beat and every counter equals
the Python models, no protocol error, PKT_OVERFLOW 0. *Simulated*, on synthetic frames.
The import below is all this file does: cocotb finds the test through the import.
"""
from neurogate_top_stress_checks import test_bad_packets  # noqa: F401  (test is run from here)
