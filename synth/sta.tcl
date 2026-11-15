# OpenSTA static timing analysis of the Nangate45 gate netlist (`make sta`).
# Run from the repository root by synth/run_sta.sh.
#
# Inputs (environment variables):
#   STA_NETLIST   gate netlist from `make synth` (default synth/out/neurogate_top_netlist.v)
#   STA_WIRELOAD  "none" (default): net load = sum of the input pins on the net only.
#                 Or a wire-load model name from the liberty file (for example
#                 5K_hvratio_1_1, the library default), which adds an estimated wire
#                 capacitance per net from its fanout. Used as a sensitivity check.
#
# Every number printed is *synthesized (estimate)*: Nangate45 typical corner,
# ideal clocks, no place and route, no real wires.

set lib     lib/NangateOpenCellLibrary_typical.lib
set netlist [expr {[info exists ::env(STA_NETLIST)] ? $::env(STA_NETLIST) : "synth/out/neurogate_top_netlist.v"}]
set wl      [expr {[info exists ::env(STA_WIRELOAD)] ? $::env(STA_WIRELOAD) : "none"}]

read_liberty $lib
read_verilog $netlist
link_design neurogate_top
read_sdc synth/neurogate_top.sdc
if {$wl ne "none"} { set_wire_load_model -name $wl }

puts "STA: netlist $netlist"
puts "STA: liberty $lib (typical corner), wire load: $wl, clocks ideal"
puts "STA: label: estimate (Nangate45 typical liberty, ideal clocks, no place and route)"
puts ""

# Unconstrained endpoints or other constraint problems would make the numbers
# below incomplete, so list them first.
puts "==== check_setup (constraint problems; empty means none) ===="
check_setup -verbose -unconstrained_endpoints -multiple_clock -no_clock -loops -generated_clocks
puts ""

# Summary: worst slack per clock and kind of path. Slack is the margin left:
# positive = met, negative = violated. "setup" = data arrives in time for the next
# clock edge; "hold" = data does not change too soon after the edge. Paths are split
# into: flop to flop on the same clock, input/output pins, clock-crossing paths
# (limited by set_max_delay, see the SDC), and asynchronous reset pins
# (recovery = setup-like, removal = hold-like).
proc worst {kind args} {
  set ends [find_timing_paths -path_delay $kind -group_path_count 1 -sort_by_slack {*}$args]
  set best ""
  foreach e $ends {
    set s [get_property $e slack]
    if {$best eq "" || $s < $best} { set best $s }
  }
  if {$best eq ""} { return "no path" }
  return [format "%7.3f ns %s" $best [expr {$best < 0 ? "VIOLATED" : "met"}]]
}
set inputs  [all_inputs -no_clocks]
set outputs [all_outputs]
puts "==== SUMMARY (estimate; ns; positive slack = met) ===="
foreach clk {net_clk core_clk} {
  set other [expr {$clk eq "net_clk" ? "core_clk" : "net_clk"}]
  foreach kind {max min} {
    set k [expr {$kind eq "max" ? "setup" : "hold "}]
    puts "SUMMARY $clk $k flop-to-flop   [worst $kind -from [get_clocks $clk] -to [get_clocks $clk] -path_group $clk]"
  }
  puts "SUMMARY $clk setup from inputs    [worst max -from $inputs -to [get_clocks $clk]]"
  puts "SUMMARY $clk setup to outputs     [worst max -from [get_clocks $clk] -to $outputs]"
  puts "SUMMARY $clk setup from $other [worst max -from [get_clocks $other] -to [get_clocks $clk]] (crossing, limit 5.0 ns)"
  puts "SUMMARY $clk recovery (reset pin) [worst max -to [get_clocks $clk] -path_group asynchronous]"
  puts "SUMMARY $clk removal  (reset pin) [worst min -to [get_clocks $clk] -path_group asynchronous]"
}
puts "SUMMARY whole design (max = setup and recovery, min = hold and removal; tns = sum of all negative slacks):"
report_worst_slack -max -digits 3
report_tns -max -digits 3
report_worst_slack -min -digits 3
report_tns -min -digits 3
puts ""

set fields {fanout cap slew net}
puts "==== Critical setup path per clock (paths that start and end on the same clock) ===="
foreach clk {net_clk core_clk} {
  puts "---- $clk ----"
  report_checks -path_delay max -from [get_clocks $clk] -to [get_clocks $clk] -path_group $clk -fields $fields -digits 3
}
puts "==== Critical hold path per clock ===="
foreach clk {net_clk core_clk} {
  puts "---- $clk ----"
  report_checks -path_delay min -from [get_clocks $clk] -to [get_clocks $clk] -path_group $clk -fields $fields -digits 3
}
puts "==== Clock-crossing paths (limited to 5.0 ns by set_max_delay; worst 3 each way) ===="
report_checks -path_delay max -from [get_clocks net_clk] -to [get_clocks core_clk] \
  -group_path_count 3 -endpoint_path_count 1 -format end -digits 3
report_checks -path_delay max -from [get_clocks core_clk] -to [get_clocks net_clk] \
  -group_path_count 3 -endpoint_path_count 1 -format end -digits 3
puts "==== Asynchronous reset pins (recovery = setup-like, removal = hold-like) ===="
report_checks -path_delay max -path_group asynchronous -fields $fields -digits 3
report_checks -path_delay min -path_group asynchronous -fields $fields -digits 3
puts "==== Design rule checks from the liberty (max slew / capacitance / fanout); violators only, worst 20 each ===="
report_check_types -max_slew -max_capacitance -max_fanout -violators -max_count 20 -digits 3
