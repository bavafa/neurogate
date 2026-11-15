# synth/: synthesis, gate-level run and timing

Synthesis turns the design into a netlist, which is a list of simple gates and flip-flops from a cell library. This project uses the open Nangate45 teaching library. There is no place and route step, so every number here is an estimate.

- **Synthesis** (`make synth`): Yosys maps the design to the library and writes area and cell reports.
- **Gate-level simulation** (`make gls`): runs the netlist with cell models in Icarus and compares with the Python models, on a smoke set and on the bad-packet set.
- **Timing** (`make sta`): OpenSTA adds up delays along every path. The constraints file explains each limit. Reset removal at the flip-flops of the queue between the clocks (a FIFO, first in first out) misses by 0.108 ns (estimate); it is reported, not fixed, because fixing it needs a place and route step.
- **Artix-7 count** (`make synth-xilinx`): a rough resource count for one FPGA (reprogrammable chip) family, not a Vivado result.

The `synth/README.md` file has every number and its label.
