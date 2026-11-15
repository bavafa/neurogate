#!/bin/bash
# Static timing analysis with OpenSTA on the Nangate45 netlist from `make synth`.
# Called by `make sta`. Writes:
#   synth/reports/sta.txt           main result: no wire load (net load = pin capacitance only)
#   synth/reports/sta_wireload.txt  same, with the liberty's default wire-load model
#                                   (5K_hvratio_1_1) as a rough check of wire delay
# Every number is an estimate (Nangate45 typical liberty, ideal clocks, no place
# and route). Exit status: 1 if OpenSTA fails or if the main run has any negative
# slack (setup, hold, recovery or removal), or if check_setup prints any constraint
# problem (unconstrained endpoint, missing clock, loop), any OpenSTA warning, or a
# summary check with no path; 0 otherwise.
set -u
cd "$(dirname "$0")/.."
. scripts/env.sh
STA=${STA:-$(command -v sta || echo "${NG_TOOLS:-$HOME/tools}/opensta/bin/sta")}
NETLIST=${STA_NETLIST:-synth/out/neurogate_top_netlist.v}
[ -x "$STA" ] || { echo "sta: OpenSTA not found at $STA"; exit 1; }
[ -f "$NETLIST" ] || { echo "sta: $NETLIST missing; run make synth first"; exit 1; }
mkdir -p synth/reports
rc=0
for wl in none 5K_hvratio_1_1; do
  out=synth/reports/sta.txt; [ "$wl" = none ] || out=synth/reports/sta_wireload.txt
  STA_NETLIST=$NETLIST STA_WIRELOAD=$wl "$STA" -no_init -exit synth/sta.tcl > "$out" 2>&1
  s=$?
  echo "sta: OpenSTA exit $s, wire load $wl, report $out"
  [ $s -eq 0 ] || rc=1
  grep -q "^Error" "$out" && { echo "sta: OpenSTA reported an error:"; grep "^Error" "$out"; rc=1; }
  grep "^SUMMARY\|^worst slack\|^tns" "$out"
  # Constraint problems make the numbers incomplete, so they fail the run: anything
  # printed under "check_setup" (unconstrained endpoints, no clock, loops, ...), any
  # SDC warning, and any summary line that found no path to time.
  problems=$(awk '/^==== check_setup/{f=1;next} /^====/{f=0} f && NF' "$out")
  [ -z "$problems" ] || { echo "sta: constraint problems (check_setup) in $out:"; echo "$problems"; rc=1; }
  grep -q "^Warning" "$out" && { echo "sta: OpenSTA warnings in $out:"; grep "^Warning" "$out" | head; rc=1; }
  grep -q "^SUMMARY.*no path" "$out" && { echo "sta: a SUMMARY check found no path to time:"; grep "^SUMMARY.*no path" "$out"; rc=1; }
  if [ "$wl" = none ] && grep -q "VIOLATED" "$out"; then
    echo "sta: at least one check is VIOLATED in the main run (see $out)"; rc=1
  fi
done
echo "sta: exit $rc (label: estimate, Nangate45 typical liberty, ideal clocks, no place and route)"
exit $rc
