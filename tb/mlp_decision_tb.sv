// ============================================================================
// mlp_decision_tb: TEST-ONLY wrapper (not part of the chip, not in rtl/)
// ============================================================================
//
// Purpose
//   mlp_core feeding decision_unit, with feature vectors going in directly as
//   114-bit entries (SPEC 8.1). Used by the full test-split replay
//   (tb/full_split_checks.py) so that every unseen test packet passes through
//   both blocks. The mlp_core -> decision_unit link is also tapped, so the
//   test compares the raw logits and the class as well as the final result.
//
// Ports (direction, width)
//   clk, rst_n                 in   1     one clock, active-low reset
//   s_tdata / s_tvalid / s_tready   114 / 1 / 1   entry in (in, in, out)
//   conf_thresh                in   7     CONF_THRESH register value
//   t_valid                    out  1     high on a clock where mlp_core hands a result to decision_unit
//   t_logit0..2 (32, signed), t_class (2)   out   that result, for the test
//   m_valid / m_ready          out/in 1   final result handshake
//   m_kind (2), m_pkt_len (16), m_pkt_id (16), m_ovf_cnt (16)  out
//   m_class (2), m_conf (7), m_low_conf (1)                    out
//
// Clock / reset, latency
//   One clock. 178 clocks from accepting an entry to the mlp_core result (see
//   mlp_core), plus 1 for decision_unit.
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

module mlp_decision_tb (
    input  logic                clk,
    input  logic                rst_n,
    input  logic [113:0]        s_tdata,
    input  logic                s_tvalid,
    output logic                s_tready,
    input  logic [6:0]          conf_thresh,
    output logic                t_valid,
    output logic signed [31:0]  t_logit0,
    output logic signed [31:0]  t_logit1,
    output logic signed [31:0]  t_logit2,
    output logic [1:0]          t_class,
    output logic                m_valid,
    input  logic                m_ready,
    output logic [1:0]          m_kind,
    output logic [15:0]         m_pkt_len,
    output logic [15:0]         m_pkt_id,
    output logic [15:0]         m_ovf_cnt,
    output logic [1:0]          m_class,
    output logic [6:0]          m_conf,
    output logic                m_low_conf
);

    logic               c_valid, c_ready;
    logic [1:0]         c_kind, c_class;
    logic [15:0]        c_pkt_len, c_pkt_id, c_ovf_cnt;
    logic signed [31:0] c_logit0, c_logit1, c_logit2;

    mlp_core u_mlp (
        .clk, .rst_n,
        .s_tdata, .s_tvalid, .s_tready,
        .m_valid(c_valid), .m_ready(c_ready), .m_kind(c_kind),
        .m_pkt_len(c_pkt_len), .m_pkt_id(c_pkt_id), .m_ovf_cnt(c_ovf_cnt),
        .m_logit0(c_logit0), .m_logit1(c_logit1), .m_logit2(c_logit2),
        .m_class(c_class)
    );

    // defparam with macros: see decision_chain_tb.sv for why.
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

    // Tap: a result moves from mlp_core to decision_unit when valid and ready are both high.
    assign t_valid  = c_valid & c_ready;
    assign t_logit0 = c_logit0;
    assign t_logit1 = c_logit1;
    assign t_logit2 = c_logit2;
    assign t_class  = c_class;

endmodule
