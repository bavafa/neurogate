# Timing constraints (SDC) for neurogate_top, used by OpenSTA in `make sta`.
#
# What this file is: a list of rules that tell the timing tool how fast each
# clock runs and which paths it should (or should not) check. The numbers it
# produces are *synthesized (estimate)*: Nangate45 typical corner, ideal clocks,
# no wire load, no place and route.
#
# The chip has two clocks (SPEC section 8), with no fixed relation between them:
#   net_clk   156.25 MHz (period 6.4 ns): packet input, parsers, feature builder,
#             write side of the asynchronous FIFO.
#   core_clk  200 MHz    (period 5.0 ns): read side of the FIFO, neural-net core,
#             decision unit, counters, result stream AND the AXI-Lite register
#             bus. The register bus has no clock of its own; it runs on core_clk.

# ---------------------------------------------------------------------------
# 1. Clocks. Ideal clocks: no clock tree, no skew, no jitter, no uncertainty
#    margin. A real chip would subtract some margin from every slack below.
# ---------------------------------------------------------------------------
set NET_PERIOD  6.4
set CORE_PERIOD 5.0
create_clock -name net_clk  -period $NET_PERIOD  [get_ports net_clk]
create_clock -name core_clk -period $CORE_PERIOD [get_ports core_clk]

# ---------------------------------------------------------------------------
# 2. Paths between the two clocks (see docs/cdc.md, "Every crossing").
#
#    By default a timing tool assumes the two clocks are related and checks a
#    path from one to the other against the closest pair of edges (here that is
#    as little as 0.2 ns apart). That is meaningless for unrelated clocks: the
#    edges can be any distance apart, and the synchronizer flops exist exactly
#    to absorb that. So the default check is replaced with these rules:
#
#    a) Setup (data must arrive in time): every crossing path must take at most
#       one core clock period, 5.0 ns, the shorter of the two periods.
#       - Gray-coded FIFO pointers (crossings 1 and 2): only one bit changes per
#         step, so the receiving side reads either the old or the new value.
#         That only holds if all the bits arrive within about one clock period
#         of each other; a bit that is much later than the others could be read
#         together with the next change. The 5.0 ns limit bounds that spread.
#       - FIFO storage read (crossing 3): a slot is written on net_clk, and
#         core_clk reads it only after the pointer has passed two synchronizer
#         flops, so the data has been stable for at least two core clock edges.
#         Limiting the path to one core period is therefore on the safe side.
#       This uses plain set_max_delay from clock to clock. The OpenSTA build
#       used here rejects the `-datapath_only` flag ("not a known keyword or
#       flag"). That flag tells the tool to ignore the clock-tree delay to the
#       two flops. The clocks here are ideal (zero clock-tree delay), so plain
#       set_max_delay times the same path; after place and route, with a real
#       clock tree, the limit would have to be re-checked.
#       The structural check
#       (`make cdc-check`) shows these FIFO paths are the only ones that cross.
#
#    b) Hold (data must not change too soon after the clock edge): not
#       checked between the two clocks. With unrelated clocks there is no
#       fixed edge to measure against; a value that changes right at the
#       receiving edge is the metastability case the two-flop synchronizers
#       are there for.
# ---------------------------------------------------------------------------
set_max_delay $CORE_PERIOD -from [get_clocks net_clk]  -to [get_clocks core_clk]
set_max_delay $CORE_PERIOD -from [get_clocks core_clk] -to [get_clocks net_clk]
set_false_path -hold -from [get_clocks net_clk]  -to [get_clocks core_clk]
set_false_path -hold -from [get_clocks core_clk] -to [get_clocks net_clk]

# ---------------------------------------------------------------------------
# 3. External reset. rst_n is asynchronous: it can change at any time and goes
#    straight to the reset synchronizers (crossings 4 and 5), which re-time its
#    release to each clock. So no path from the rst_n pin is timed. The outputs
#    of the reset synchronizers ARE timed (normal setup/hold, and
#    recovery/removal where they drive a flop's asynchronous reset pin),
#    because they are made by a flop on the same clock they feed.
# ---------------------------------------------------------------------------
set_false_path -from [get_ports rst_n]

# ---------------------------------------------------------------------------
# 4. Input and output pins. SPEC gives no outside timing, so this is a guess:
#    the logic outside the chip uses 20% of the clock period on each side,
#    leaving 80% for the chip. Inputs are driven by a small buffer (BUF_X1);
#    each output drives 3 fF (about two small gate inputs).
#    Each pin is tied to the clock it belongs to (SPEC section 3):
#      net_clk:  s_axis_*            (packet input)
#      core_clk: m_axis_res_*        (result stream), s_axil_* (register bus)
# ---------------------------------------------------------------------------
set IO_FRACTION 0.2
set net_in   [get_ports {s_axis_tdata* s_axis_tkeep* s_axis_tlast s_axis_tuser s_axis_tvalid}]
set net_out  [get_ports {s_axis_tready}]
set core_in  [get_ports {m_axis_res_tready s_axil_awaddr* s_axil_awvalid s_axil_wdata* \
                         s_axil_wstrb* s_axil_wvalid s_axil_bready s_axil_araddr* \
                         s_axil_arvalid s_axil_rready}]
set core_out [get_ports {m_axis_res_tdata* m_axis_res_tvalid s_axil_awready s_axil_wready \
                         s_axil_bresp* s_axil_bvalid s_axil_arready s_axil_rdata* \
                         s_axil_rresp* s_axil_rvalid}]
set_input_delay  [expr {$IO_FRACTION * $NET_PERIOD}]  -clock net_clk  $net_in
set_output_delay [expr {$IO_FRACTION * $NET_PERIOD}]  -clock net_clk  $net_out
set_input_delay  [expr {$IO_FRACTION * $CORE_PERIOD}] -clock core_clk $core_in
set_output_delay [expr {$IO_FRACTION * $CORE_PERIOD}] -clock core_clk $core_out
set_driving_cell -lib_cell BUF_X1 -pin Z [concat $net_in $core_in [get_ports rst_n]]
set_load 3.0 [concat $net_out $core_out]
