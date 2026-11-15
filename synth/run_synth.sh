#!/bin/bash
# Runs the Nangate45 synthesis and writes synth/out/ (netlist) and synth/reports/.
# Called by `make synth`. Exits non-zero if Yosys fails or `check -assert` finds
# a problem (undriven wire, combinational loop, multiple drivers).
set -u
cd "$(dirname "$0")/.."
. scripts/env.sh
[ -f lib/NangateOpenCellLibrary_typical.lib ] || lib/fetch_nangate45.sh || exit 1
mkdir -p synth/out synth/reports
yosys -m slang -l synth/reports/yosys.log synth/synth_nangate45.ys > /dev/null
rc=$?
echo "synth: yosys exit $rc; log synth/reports/yosys.log"
[ $rc -eq 0 ] && { echo "synth: netlist synth/out/neurogate_top_netlist.v"; sed -n '/Number of cells/,$p' synth/reports/cells.txt | tail -40; }
exit $rc
