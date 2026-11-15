// ============================================================================
// neurogate_top: the whole chip, two clock domains (SPEC.md sections 2, 3, 8)
// ============================================================================
//
// Purpose
//   Connects every block of NeuroGate. A packet enters as an AXI-Stream
//   (a simple valid/ready data stream) on the network clock, is parsed,
//   turned into 8 features, sent across to the core clock through the
//   asynchronous FIFO, classified by the small neural network, and leaves as
//   one result beat. Counters for software are read over AXI-Lite (a simple
//   register bus) on the core clock.
//
//     net_clk:  skid_fifo -> eth_parser -> l3l4_parser -> feature_builder
//               -> async_fifo (write side)
//     core_clk: async_fifo (read side) -> mlp_core -> decision_unit
//               -> result stream + stats_counters -> axil_regs
//
// Clock-domain crossings (full list and method in docs/cdc.md)
//   1. Packet entries net_clk -> core_clk: ONLY through async_fifo
//      (Gray-coded pointers, two synchronizer flops on each side).
//   2. External rst_n into each domain: one reset_sync per domain
//      (asynchronous assert, release after two edges of that domain's clock).
//   No other signal crosses. CONF_THRESH and the soft-reset pulse are made
//   and used in core_clk only. feature_builder (net_clk) needs nothing from
//   core_clk: overflow is decided from the FIFO's own write-side full flag.
//   `make cdc-check` (scripts/cdc_check.py) checks this structurally.
//
// Ports (direction, width)
//   net_clk            in   1    network clock, 156.25 MHz in SPEC
//   core_clk           in   1    core clock, 200 MHz in SPEC; asynchronous
//                                to net_clk
//   rst_n              in   1    external active-low reset, asynchronous to
//                                both clocks
//   -- packet input, AXI-Stream, net_clk (SPEC 3.1) --
//   s_axis_tdata       in   64   first frame byte in [7:0] of the first beat
//   s_axis_tkeep       in   8    valid bytes; all ones except the last beat
//   s_axis_tlast       in   1    last beat of the frame
//   s_axis_tuser       in   1    on the last beat: bad FCS, drop the frame
//   s_axis_tvalid      in   1
//   s_axis_tready      out  1    low only when the input skid FIFO is full
//                                (never because the async FIFO is full)
//   -- result output, AXI-Stream without tlast, core_clk (SPEC 3.3) --
//   m_axis_res_tdata   out  48   [1:0] class, [2] low_conf, [7:3] 0,
//                                [15:8] confidence (0..127, a logit margin,
//                                not a probability), [31:16] pkt_len,
//                                [47:32] pkt_id
//   m_axis_res_tvalid  out  1    one beat per classified packet only
//   m_axis_res_tready  in   1    may stall; the stall backs up into the core
//                                clock pipeline and then the async FIFO
//   -- control, AXI-Lite, core_clk (docs/regmap.md) --
//   s_axil_aw*/w*/b*/ar*/r*      6-bit byte address, 32-bit data; see
//                                axil_regs for the handshake rules
//
// Parameters
//   AFIFO_DEPTH        async FIFO entries (power of two, >= 4; SPEC: 16)
//   CONF_THRESH_RESET  CONF_THRESH value after reset (regmap: 13)
//
// ROM files
//   mlp_core reads its weights from four hex files. Their paths are text
//   macros ROM_W1, ROM_B1, ROM_W2, ROM_B2 (give them with -D, for example as
//   absolute paths when the simulator runs in another folder). Default:
//   data/model/rom_*.hex relative to the run folder. A macro is used, not a
//   parameter, because Icarus cannot pass a parent parameter into
//   mlp_core's string parameters ("Unable to bind variable").
//
// Clock / reset
//   Two clocks as above. rst_n goes through one reset_sync per domain:
//   every block in a domain is reset by that domain's synchronized reset.
//   The async FIFO gets both (write side net, read side core); it uses them
//   as asynchronous resets, so both of its sides clear the moment rst_n
//   falls (D-019). The other blocks use them as synchronous resets; the
//   synchronizer holds its output low for at least two clock edges after
//   rst_n rises, so they are reset as long as their clock runs.
//
// Latency (simulated, measured by tb/test_neurogate_top.py, not asserted)
//   187 to 191 core clocks from the edge that takes the last input beat of a
//   classified frame to the edge that takes its result beat, with paced
//   input (no queueing) and random result-stream stalls. Most of it is the
//   176 multiply-accumulate steps of mlp_core; the rest is the parser and
//   feature stages, the async FIFO crossing and the decision stage. A frame
//   that arrives while mlp_core is busy also waits in the async FIFO.
//
// Glue logic (the only logic here besides wiring; why it is needed)
//   decision_unit has one output; it feeds two consumers:
//     - stats_counters takes every entry and never stalls (s_ready = 1);
//     - the result stream takes only CLASSIFY entries and may stall.
//   So decision_unit's ready = result stream ready for a CLASSIFY entry,
//   and 1 for a DROP or OVF_ONLY entry (no result beat for those, SPEC 3.3).
//   An entry is counted in the same cycle it leaves on the result stream,
//   so counters never get ahead of the stream. tvalid does not depend on
//   tready (AXI-Stream rule).
// ============================================================================

`ifndef ROM_W1
`define ROM_W1 "data/model/rom_w1.hex"
`endif
`ifndef ROM_B1
`define ROM_B1 "data/model/rom_b1.hex"
`endif
`ifndef ROM_W2
`define ROM_W2 "data/model/rom_w2.hex"
`endif
`ifndef ROM_B2
`define ROM_B2 "data/model/rom_b2.hex"
`endif

module neurogate_top #(
    parameter int unsigned AFIFO_DEPTH       = 16,
    parameter logic [6:0]  CONF_THRESH_RESET = 7'd13
) (
    input  logic        net_clk,
    input  logic        core_clk,
    input  logic        rst_n,

    // packet input (net_clk)
    input  logic [63:0] s_axis_tdata,
    input  logic [7:0]  s_axis_tkeep,
    input  logic        s_axis_tlast,
    input  logic        s_axis_tuser,
    input  logic        s_axis_tvalid,
    output logic        s_axis_tready,

    // result output (core_clk)
    output logic [47:0] m_axis_res_tdata,
    output logic        m_axis_res_tvalid,
    input  logic        m_axis_res_tready,

    // AXI-Lite control (core_clk)
    input  logic [5:0]  s_axil_awaddr,
    input  logic        s_axil_awvalid,
    output logic        s_axil_awready,
    input  logic [31:0] s_axil_wdata,
    input  logic [3:0]  s_axil_wstrb,
    input  logic        s_axil_wvalid,
    output logic        s_axil_wready,
    output logic [1:0]  s_axil_bresp,
    output logic        s_axil_bvalid,
    input  logic        s_axil_bready,
    input  logic [5:0]  s_axil_araddr,
    input  logic        s_axil_arvalid,
    output logic        s_axil_arready,
    output logic [31:0] s_axil_rdata,
    output logic [1:0]  s_axil_rresp,
    output logic        s_axil_rvalid,
    input  logic        s_axil_rready
);

    localparam int unsigned ENTRY_W       = 114;   // SPEC 8.1
    localparam int unsigned ENTRY_KEEP_W  = ENTRY_W / 8;
    localparam logic [1:0]  KIND_CLASSIFY = 2'd0;

    // ======================================================================
    // Reset synchronizers (crossing 2 in the header)
    // ======================================================================
    logic net_rst_n;    // reset for every net_clk block
    logic core_rst_n;   // reset for every core_clk block

    reset_sync u_net_rst  (.clk(net_clk),  .rst_n(rst_n), .rst_n_out(net_rst_n));
    reset_sync u_core_rst (.clk(core_clk), .rst_n(rst_n), .rst_n_out(core_rst_n));

    // ======================================================================
    // net_clk domain
    // ======================================================================
    // skid_fifo -> eth_parser
    logic [63:0] k_tdata;
    logic [7:0]  k_tkeep;
    logic        k_tlast, k_tuser, k_tvalid, k_tready;

    skid_fifo #(.DATA_W(64)) u_skid (
        .clk(net_clk), .rst_n(net_rst_n),
        .s_tdata(s_axis_tdata), .s_tkeep(s_axis_tkeep), .s_tlast(s_axis_tlast),
        .s_tuser(s_axis_tuser), .s_tvalid(s_axis_tvalid), .s_tready(s_axis_tready),
        .m_tdata(k_tdata), .m_tkeep(k_tkeep), .m_tlast(k_tlast),
        .m_tuser(k_tuser), .m_tvalid(k_tvalid), .m_tready(k_tready)
    );

    // eth_parser -> l3l4_parser
    logic [63:0] e_tdata;
    logic [7:0]  e_tkeep;
    logic        e_tlast, e_tvalid, e_tready;
    logic        e_vlan, e_drop;
    logic [1:0]  e_drop_reason;
    // Waiver (UNUSEDSIGNAL): l3l4_parser does not need these. tuser is
    // already folded into e_drop, and hdr_done / ipv4 are summarized by
    // e_drop_reason. r_drop_reason (why a frame was dropped) is debug
    // detail: the entry only carries kind = DROP (SPEC 8.1).
    /* verilator lint_off UNUSEDSIGNAL */
    logic        e_tuser, e_hdr_done, e_ipv4;
    logic [3:0]  r_drop_reason;
    /* verilator lint_on UNUSEDSIGNAL */

    eth_parser u_eth (
        .clk(net_clk), .rst_n(net_rst_n),
        .s_tdata(k_tdata), .s_tkeep(k_tkeep), .s_tlast(k_tlast), .s_tuser(k_tuser),
        .s_tvalid(k_tvalid), .s_tready(k_tready),
        .m_tdata(e_tdata), .m_tkeep(e_tkeep), .m_tlast(e_tlast), .m_tuser(e_tuser),
        .m_tvalid(e_tvalid), .m_tready(e_tready),
        .m_hdr_done(e_hdr_done), .m_vlan(e_vlan), .m_ipv4(e_ipv4),
        .m_drop(e_drop), .m_drop_reason(e_drop_reason)
    );

    // l3l4_parser -> feature_builder (one record per frame)
    logic        r_valid, r_ready, r_drop, r_is_tcp, r_syn, r_ack, r_rst_fin;
    logic [15:0] r_ip_len, r_dst_port, r_pkt_len;
    logic [7:0]  r_ttl;

    l3l4_parser u_l3l4 (
        .clk(net_clk), .rst_n(net_rst_n),
        .s_tdata(e_tdata), .s_tkeep(e_tkeep), .s_tlast(e_tlast),
        .s_tvalid(e_tvalid), .s_tready(e_tready),
        .s_vlan(e_vlan), .s_drop(e_drop), .s_drop_reason(e_drop_reason),
        .r_valid(r_valid), .r_ready(r_ready), .r_drop(r_drop),
        .r_drop_reason(r_drop_reason), .r_is_tcp(r_is_tcp), .r_syn(r_syn),
        .r_ack(r_ack), .r_rst_fin(r_rst_fin), .r_ip_len(r_ip_len), .r_ttl(r_ttl),
        .r_dst_port(r_dst_port), .r_pkt_len(r_pkt_len)
    );

    // feature_builder -> async_fifo (114-bit entry, SPEC 8.1). An entry that
    // finds the FIFO full is dropped and counted inside feature_builder.
    logic [ENTRY_W-1:0] f_tdata;
    logic               f_tvalid, f_tready;

    feature_builder u_fb (
        .clk(net_clk), .rst_n(net_rst_n),
        .s_valid(r_valid), .s_ready(r_ready), .s_drop(r_drop), .s_is_tcp(r_is_tcp),
        .s_syn(r_syn), .s_ack(r_ack), .s_rst_fin(r_rst_fin), .s_ip_len(r_ip_len),
        .s_ttl(r_ttl), .s_dst_port(r_dst_port), .s_pkt_len(r_pkt_len),
        .m_tdata(f_tdata), .m_tvalid(f_tvalid), .m_tready(f_tready)
    );

    // ======================================================================
    // The crossing: async_fifo (crossing 1 in the header)
    // ======================================================================
    // The entry travels in tdata. tkeep, tlast and tuser have no meaning for
    // an entry, so they are tied to constants here (D-019).
    logic [ENTRY_W-1:0] a_tdata;
    logic               a_tvalid, a_tready;
    // Waiver (UNUSEDSIGNAL): the constant tkeep/tlast/tuser come back out
    // of the FIFO unchanged and nothing needs them.
    /* verilator lint_off UNUSEDSIGNAL */
    logic [ENTRY_KEEP_W-1:0] a_tkeep;
    logic                    a_tlast, a_tuser;
    /* verilator lint_on UNUSEDSIGNAL */

    async_fifo #(.DATA_W(ENTRY_W), .DEPTH(AFIFO_DEPTH)) u_afifo (
        .wr_clk(net_clk), .wr_rst_n(net_rst_n),
        .s_tdata(f_tdata), .s_tkeep({ENTRY_KEEP_W{1'b1}}), .s_tlast(1'b1),
        .s_tuser(1'b0), .s_tvalid(f_tvalid), .s_tready(f_tready),
        .rd_clk(core_clk), .rd_rst_n(core_rst_n),
        .m_tdata(a_tdata), .m_tkeep(a_tkeep), .m_tlast(a_tlast), .m_tuser(a_tuser),
        .m_tvalid(a_tvalid), .m_tready(a_tready)
    );

    // ======================================================================
    // core_clk domain
    // ======================================================================
    // AXI-Lite register block: CONF_THRESH and the soft-reset pulse are
    // produced here, in core_clk, and used only by core_clk blocks.
    logic        soft_rst;
    logic [6:0]  conf_thresh;
    logic [31:0] cnt_pkt_normal, cnt_pkt_scan, cnt_pkt_syn;
    logic [31:0] cnt_bytes_normal, cnt_bytes_scan, cnt_bytes_syn;
    logic [31:0] cnt_pkt_dropped, cnt_pkt_lowconf, cnt_last_result, cnt_pkt_overflow;

    // mlp_core -> decision_unit
    logic               c_valid, c_ready;
    logic [1:0]         c_kind, c_class;
    logic [15:0]        c_pkt_len, c_pkt_id, c_ovf_cnt;
    logic signed [31:0] c_logit0, c_logit1, c_logit2;

    mlp_core #(
        .W1_HEX(`ROM_W1), .B1_HEX(`ROM_B1), .W2_HEX(`ROM_W2), .B2_HEX(`ROM_B2)
    ) u_mlp (
        .clk(core_clk), .rst_n(core_rst_n),
        .s_tdata(a_tdata), .s_tvalid(a_tvalid), .s_tready(a_tready),
        .m_valid(c_valid), .m_ready(c_ready), .m_kind(c_kind),
        .m_pkt_len(c_pkt_len), .m_pkt_id(c_pkt_id), .m_ovf_cnt(c_ovf_cnt),
        .m_logit0(c_logit0), .m_logit1(c_logit1), .m_logit2(c_logit2),
        .m_class(c_class)
    );

    // decision_unit -> result stream + stats_counters
    logic        d_valid, d_ready;
    logic [1:0]  d_kind, d_class;
    logic [15:0] d_pkt_len, d_pkt_id, d_ovf_cnt;
    logic [6:0]  d_conf;
    logic        d_low_conf;

    decision_unit u_dec (
        .clk(core_clk), .rst_n(core_rst_n),
        .s_valid(c_valid), .s_ready(c_ready), .s_kind(c_kind),
        .s_pkt_len(c_pkt_len), .s_pkt_id(c_pkt_id), .s_ovf_cnt(c_ovf_cnt),
        .s_logit0(c_logit0), .s_logit1(c_logit1), .s_logit2(c_logit2),
        .s_class(c_class), .conf_thresh(conf_thresh),
        .m_valid(d_valid), .m_ready(d_ready), .m_kind(d_kind),
        .m_pkt_len(d_pkt_len), .m_pkt_id(d_pkt_id), .m_ovf_cnt(d_ovf_cnt),
        .m_class(d_class), .m_conf(d_conf), .m_low_conf(d_low_conf)
    );

    // Fork to the two consumers (see "Glue logic" in the header).
    logic d_is_result;   // this entry produces a result beat
    logic st_ready;      // stats_counters ready (constant 1 by design)

    assign d_is_result       = (d_kind == KIND_CLASSIFY);
    assign m_axis_res_tvalid = d_valid && d_is_result && st_ready;
    assign d_ready           = st_ready && (!d_is_result || m_axis_res_tready);
    assign m_axis_res_tdata  = {d_pkt_id, d_pkt_len, 1'b0, d_conf, 5'b0, d_low_conf, d_class};

    stats_counters #(.CNT_W(32)) u_stats (
        .clk(core_clk), .rst_n(core_rst_n), .soft_rst(soft_rst),
        .s_valid(d_valid && d_ready), .s_ready(st_ready),
        .s_kind(d_kind), .s_pkt_len(d_pkt_len), .s_ovf_cnt(d_ovf_cnt),
        .s_class(d_class), .s_conf(d_conf), .s_low_conf(d_low_conf),
        .pkt_normal(cnt_pkt_normal), .pkt_scan(cnt_pkt_scan), .pkt_syn(cnt_pkt_syn),
        .bytes_normal(cnt_bytes_normal), .bytes_scan(cnt_bytes_scan),
        .bytes_syn(cnt_bytes_syn), .pkt_dropped(cnt_pkt_dropped),
        .pkt_lowconf(cnt_pkt_lowconf), .pkt_overflow(cnt_pkt_overflow),
        .last_result(cnt_last_result)
    );

    axil_regs #(.CONF_THRESH_RESET(CONF_THRESH_RESET)) u_regs (
        .clk(core_clk), .rst_n(core_rst_n),
        .s_axil_awaddr, .s_axil_awvalid, .s_axil_awready,
        .s_axil_wdata, .s_axil_wstrb, .s_axil_wvalid, .s_axil_wready,
        .s_axil_bresp, .s_axil_bvalid, .s_axil_bready,
        .s_axil_araddr, .s_axil_arvalid, .s_axil_arready,
        .s_axil_rdata, .s_axil_rresp, .s_axil_rvalid, .s_axil_rready,
        .soft_rst(soft_rst), .conf_thresh(conf_thresh),
        .pkt_normal(cnt_pkt_normal), .pkt_scan(cnt_pkt_scan), .pkt_syn(cnt_pkt_syn),
        .bytes_normal(cnt_bytes_normal), .bytes_scan(cnt_bytes_scan),
        .bytes_syn(cnt_bytes_syn), .pkt_dropped(cnt_pkt_dropped),
        .pkt_lowconf(cnt_pkt_lowconf), .last_result(cnt_last_result),
        .pkt_overflow(cnt_pkt_overflow)
    );

endmodule
