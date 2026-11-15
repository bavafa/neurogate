// ============================================================================
// parser_chain_tb: test-only wrapper, eth_parser followed by l3l4_parser
// ============================================================================
//
// Purpose
//   Testbench top for tb/test_l3l4_parser.py. It wires the two header
//   parsers together exactly as the full chip will: eth_parser's output
//   stream and its m_vlan / m_drop / m_drop_reason sideband feed
//   l3l4_parser. Testing them as a pair means the per-frame record is
//   checked against the Python model with the real layer-2 sideband, not a
//   hand-written imitation of it. Not part of the design (lives in tb/).
//
// Ports (direction, width)
//   clk, rst_n                 in   1     net_clk and active-low reset
//   s_tdata/s_tkeep/s_tlast/
//   s_tuser/s_tvalid           in   64/8/1/1/1   frame input (SPEC 3.1)
//   s_tready                   out  1
//   r_*                        out  see rtl/l3l4_parser.sv (per-frame record)
//   r_ready                    in   1
//
// Clock / reset: single clock, synchronous active-low reset to both blocks.
// Latency: 1 cycle in eth_parser + 1 cycle after tlast in l3l4_parser, so a
//   record appears 2 clocks after the frame's tlast beat enters (no stalls).
// ============================================================================

module parser_chain_tb (
    input  logic        clk,
    input  logic        rst_n,
    input  logic [63:0] s_tdata,
    input  logic [7:0]  s_tkeep,
    input  logic        s_tlast,
    input  logic        s_tuser,
    input  logic        s_tvalid,
    output logic        s_tready,
    output logic        r_valid,
    input  logic        r_ready,
    output logic        r_drop,
    output logic [3:0]  r_drop_reason,
    output logic        r_is_tcp,
    output logic        r_syn,
    output logic        r_ack,
    output logic        r_rst_fin,
    output logic [15:0] r_ip_len,
    output logic [7:0]  r_ttl,
    output logic [15:0] r_dst_port,
    output logic [15:0] r_pkt_len
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
    /* verilator lint_on UNUSEDSIGNAL */

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

endmodule
