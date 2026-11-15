# Toolchain setup

Everything is installed in user space (no root). `scripts/env.sh` puts these on `PATH`;
the Makefile sources it.

| Tool | Used for | Notes |
|---|---|---|
| GNU make | `Makefile` | Any 4.x |
| Verilator | lint (`-Wall`) and compile check | from OSS CAD Suite |
| Icarus Verilog | RTL and gate-level simulation, compile check | from OSS CAD Suite |
| Yosys | synthesis | from OSS CAD Suite |
| OpenSTA (`sta`) | static timing | built from source (github.com/The-OpenROAD-Project/OpenSTA), needs CMake, Tcl, SWIG, Bison, Flex, Eigen, CUDD |
| Python venv `.venv` | cocotb, Scapy, NumPy | `python3 -m venv .venv && .venv/bin/pip install cocotb scapy numpy` |
| Nangate45 liberty | timing library | `lib/fetch_nangate45.sh`, see `lib/README.md` |

Run `make toolchain` to print the versions found.
