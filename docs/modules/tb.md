# tb/: the hardware tests

Every hardware block has a test written with cocotb, a Python framework that drives a simulated chip. Icarus Verilog runs the simulation. Each test uses a fixed seed that is printed in its log.

- **Block tests** (`test_<block>.py`): one per block. They include bad input (short frames, wrong EtherType, bad flags, reset in the middle of a frame) and random pauses.
- **Whole-chip tests** (`test_neurogate_top*.py`): a smoke test, traffic from the generator (900 frames in `make test`, 9,000 in `make test-full`), bad packets, backpressure on the result stream, a full queue, resets at random moments, and a speed measurement.
- **Gate-level run** (`gls_run.py`): runs the same checks on the synthesized gate netlist.
- **Clock-crossing check test** (`test_cdc_check.py`): plants known clock-crossing faults in copies of the design and checks that the structural check catches each one.
- **Evidence** (`evidence/`): logs that show a test fails when a fault is planted on purpose.

Everything is compared with the Python models. All results are simulated.
