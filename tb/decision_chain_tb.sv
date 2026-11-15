// ============================================================================
// decision_chain_tb: TEST-ONLY wrapper (not part of the chip, not in rtl/)
// ============================================================================
//
// Purpose
//   The whole per-packet datapath of the chip in one clock domain, so the
//   chained test can drive raw Ethernet frames and compare the final result
//   (class, confidence, low-confidence flag) with the Python models:
//     eth_parser -> l3l4_parser -> feature_builder -> mlp_core -> decision_unit
//   The chip has the async FIFO between feature_builder and mlp_core and two
//   clocks. Here it is left out and both sides share one clock: the FIFO is
//   not what is being tested, and without it an entry that arrives while
//   mlp_core is busy is lost (feature_builder counts it in ovf_cnt). The test
//   therefore sends the next frame only after the previous result is out, so
//   no entry is lost and ovf_cnt must stay 0.
//
// Ports (direction, width)
//   clk, rst_n                  in   1     one clock and active-low reset
//   s_tdata / s_tkeep           in   64/8  frame beats (first byte in [7:0])
//   s_tlast, s_tuser, s_tvalid  in   1
//   s_tready                    out  1
//   conf_thresh                 in   7     CONF_THRESH register value
//   m_valid / m_ready           out/in 1   final result handshake
//   m_kind (2), m_pkt_len (16), m_pkt_id (16), m_ovf_cnt (16)  out
//   m_class (2), m_conf (7), m_low_conf (1)                    out
//
// Clock / reset, latency
//   One clock. Roughly 190 clocks from the last frame beat to the result for a
//   classified frame: an ESTIMATE only (178 clocks in mlp_core, measured by
//   tb/test_mlp_core.py, plus a few clocks each for the parser, feature
//   builder and decision stages, not measured here). A few clocks for a
//   dropped frame. The chained test does not assert this number.
// ============================================================================

// ROM files of mlp_core. The test passes absolute paths as -D macros (the
// simulator may start in another folder, and a missing file leaves the ROMs
// unknown (X) and the logits with them). Plain relative defaults for lint.
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

module decision_chain_tb (
    input  logic         clk,
    input  logic         rst_n,
    input  logic [63:0]  s_tdata,
    input  logic [7:0]   s_tkeep,
    input  logic         s_tlast,
    input  logic         s_tuser,
    input  logic         s_tvalid,
    output logic         s_tready,
    input  logic [6:0]   conf_thresh,
    output logic         m_valid,
    input  logic         m_ready,
    output logic [1:0]   m_kind,
    output logic [15:0]  m_pkt_len,
    output logic [15:0]  m_pkt_id,
    output logic [15:0]  m_ovf_cnt,
    output logic [1:0]   m_class,
    output logic [6:0]   m_conf,
    output logic         m_low_conf
);

    logic [63:0] e_tdata;
    logic [7:0]  e_tkeep;
    logic        e_tlast, e_tvalid, e_tready;
    logic        e_vlan, e_drop;
    logic [1:0]  e_drop_reason;
    // Sideband l3l4_parser does not need: tuser is already folded into
    // e_drop, and hdr_done / ipv4 are summarized by e_drop_reason.
    /* verilator lint_off UNUSEDSIGNAL */
    logic        e_tuser, e_hdr_done, e_ipv4;
    logic [3:0]  r_drop_reason;   // test readability only; the entry carries DROP
    /* verilator lint_on UNUSEDSIGNAL */

    logic        r_valid, r_ready, r_drop, r_is_tcp, r_syn, r_ack, r_rst_fin;
    logic [15:0] r_ip_len, r_dst_port, r_pkt_len;
    logic [7:0]  r_ttl;

    eth_parser u_eth (
        .clk, .rst_n,
        .s_tdata, .s_tkeep, .s_tlast, .s_tuser, .s_tvalid, .s_tready,
        .m_tdata(e_tdata), .m_tkeep(e_tkeep), .m_tlast(e_tlast), .m_tuser(e_tuser),
        .m_tvalid(e_tvalid), .m_tready(e_tready),
        .m_hdr_done(e_hdr_done), .m_vlan(e_vlan), .m_ipv4(e_ipv4),
        .m_drop(e_drop), .m_drop_reason(e_drop_reason)
    );

    l3l4_parser u_l3l4 (
        .clk, .rst_n,
        .s_tdata(e_tdata), .s_tkeep(e_tkeep), .s_tlast(e_tlast),
        .s_tvalid(e_tvalid), .s_tready(e_tready),
        .s_vlan(e_vlan), .s_drop(e_drop), .s_drop_reason(e_drop_reason),
        .r_valid, .r_ready, .r_drop, .r_drop_reason, .r_is_tcp, .r_syn, .r_ack,
        .r_rst_fin, .r_ip_len, .r_ttl, .r_dst_port, .r_pkt_len
    );

    // feature_builder -> mlp_core (no FIFO here, see the header)
    logic [113:0] f_tdata;
    logic         f_tvalid, f_tready;

    feature_builder u_fb (
        .clk, .rst_n,
        .s_valid(r_valid), .s_ready(r_ready), .s_drop(r_drop), .s_is_tcp(r_is_tcp),
        .s_syn(r_syn), .s_ack(r_ack), .s_rst_fin(r_rst_fin), .s_ip_len(r_ip_len),
        .s_ttl(r_ttl), .s_dst_port(r_dst_port), .s_pkt_len(r_pkt_len),
        .m_tdata(f_tdata), .m_tvalid(f_tvalid), .m_tready(f_tready)
    );

    logic               c_valid, c_ready;
    logic [1:0]         c_kind, c_class;
    logic [15:0]        c_pkt_len, c_pkt_id, c_ovf_cnt;
    logic signed [31:0] c_logit0, c_logit1, c_logit2;

    mlp_core u_mlp (
        .clk, .rst_n,
        .s_tdata(f_tdata), .s_tvalid(f_tvalid), .s_tready(f_tready),
        .m_valid(c_valid), .m_ready(c_ready), .m_kind(c_kind),
        .m_pkt_len(c_pkt_len), .m_pkt_id(c_pkt_id), .m_ovf_cnt(c_ovf_cnt),
        .m_logit0(c_logit0), .m_logit1(c_logit1), .m_logit2(c_logit2),
        .m_class(c_class)
    );

    // defparam with macros: Icarus cannot bind a parent parameter inside an
    // instance parameter list ("Unable to bind variable").
    // verilator lint_off DEFPARAM
    defparam u_mlp.W1_HEX = `ROM_W1;
    defparam u_mlp.B1_HEX = `ROM_B1;
    defparam u_mlp.W2_HEX = `ROM_W2;
    defparam u_mlp.B2_HEX = `ROM_B2;
    // verilator lint_on DEFPARAM

    decision_unit u_dec (
        .clk, .rst_n,
        .s_valid(c_valid), .s_ready(c_ready), .s_kind(c_kind),
        .s_pkt_len(c_pkt_len), .s_pkt_id(c_pkt_id), .s_ovf_cnt(c_ovf_cnt),
        .s_logit0(c_logit0), .s_logit1(c_logit1), .s_logit2(c_logit2),
        .s_class(c_class), .conf_thresh,
        .m_valid, .m_ready, .m_kind, .m_pkt_len, .m_pkt_id, .m_ovf_cnt,
        .m_class, .m_conf, .m_low_conf
    );

endmodule
