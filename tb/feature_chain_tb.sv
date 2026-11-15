// ============================================================================
// feature_chain_tb: TEST-ONLY wrapper (not part of the chip, not in rtl/)
// ============================================================================
//
// Purpose
//   Wires eth_parser -> l3l4_parser -> feature_builder exactly as the full
//   chip does in net_clk (SPEC.md section 2, minus the input skid FIFO), so
//   the feature builder test can drive raw Ethernet frames and compare the
//   async-FIFO entries with model/features.py run_stream, bit for bit.
//
// Ports (direction, width)
//   clk, rst_n                  in   1     net_clk and its active-low reset
//   s_tdata / s_tkeep           in   64/8  frame beats (first byte in [7:0])
//   s_tlast, s_tuser, s_tvalid  in   1
//   s_tready                    out  1
//   m_tdata                     out  114   entry for the async FIFO (SPEC 8.1)
//   m_tvalid                    out  1
//   m_tready                    in   1     the test plays the async FIFO:
//                                          low = FIFO full, entry is lost
//
// Clock / reset, latency
//   One clock. An entry is offered two clocks after a frame's tlast beat
//   enters l3l4_parser (one clock for its record register, one for the
//   feature_builder entry register), plus eth_parser's one-clock delay.
// ============================================================================

module feature_chain_tb (
    input  logic         clk,
    input  logic         rst_n,
    input  logic [63:0]  s_tdata,
    input  logic [7:0]   s_tkeep,
    input  logic         s_tlast,
    input  logic         s_tuser,
    input  logic         s_tvalid,
    output logic         s_tready,
    output logic [113:0] m_tdata,
    output logic         m_tvalid,
    input  logic         m_tready
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

    feature_builder u_fb (
        .clk, .rst_n,
        .s_valid(r_valid), .s_ready(r_ready), .s_drop(r_drop), .s_is_tcp(r_is_tcp),
        .s_syn(r_syn), .s_ack(r_ack), .s_rst_fin(r_rst_fin), .s_ip_len(r_ip_len),
        .s_ttl(r_ttl), .s_dst_port(r_dst_port), .s_pkt_len(r_pkt_len),
        .m_tdata, .m_tvalid, .m_tready
    );

endmodule
