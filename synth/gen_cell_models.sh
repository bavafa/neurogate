#!/bin/bash
# Writes synth/out/nangate45_cells.v: behavioral Verilog models of the Nangate45
# cells, made by Yosys from the function and flip-flop groups in the liberty file.
# Used only by gate-level simulation (`make gls`). The file is derived from the
# fetched liberty file, so it is not committed (see lib/README.md).
set -eu
cd "$(dirname "$0")/.."
. scripts/env.sh
mkdir -p synth/out
yosys -q -p "read_liberty -ignore_miss_dir -ignore_miss_func lib/NangateOpenCellLibrary_typical.lib; write_verilog -noattr synth/out/nangate45_cells.v"
echo "gen_cell_models: synth/out/nangate45_cells.v ($(grep -c '^module ' synth/out/nangate45_cells.v) cells)"
