// ============================================================================
// stats_counters: packet / byte / drop / overflow counters and LAST_RESULT
// ============================================================================
//
// Purpose
//   Sits behind decision_unit (SPEC.md sections 3.2 and 7, docs/regmap.md).
//   For every finished entry it updates the counters that axil_regs shows to
//   software. A "counter" here is a register that goes up by some amount when
//   an event happens. All counters are unsigned and WRAP: after the largest
//   value (all ones) they continue at 0 (regmap, D-004).
//
//   What each entry kind does (s_kind: 0 CLASSIFY, 1 DROP, 2 OVF_ONLY):
//     CLASSIFY  class counter of s_class +1, class byte counter + s_pkt_len,
//               pkt_lowconf + s_low_conf (a flagged packet is still counted
//               under its class; LOWCONF is a subset, not a separate bucket),
//               last_result = {s_conf, s_class}.
//     DROP      pkt_dropped +1. last_result is not touched.
//     OVF_ONLY  nothing except the overflow counter below.
//     every kind: pkt_overflow + s_ovf_cnt. The 16-bit count says how many
//               frames the network-clock side threw away since the previous
//               entry (SPEC.md section 8.1); it is added to a 32-bit counter.
//   Reserved cases that mlp_core / decision_unit never produce, handled so no
//   counter moves because of something that cannot exist (D-030, owner-approved in D-041):
//     s_kind = 3        : treated like OVF_ONLY.
//     CLASSIFY, class 3 : only pkt_overflow is updated.
//
// Soft reset
//   soft_rst high at a clock edge clears every counter and last_result at that
//   edge. If an entry is accepted at that same edge, the reset wins: the entry
//   is dropped from the statistics (regmap "Soft reset precedence"). The
//   entry is still consumed, so decision_unit is never stalled.
//
// Ports (direction, width)
//   clk          in   1    core_clk
//   rst_n        in   1    active-low reset, synchronous to clk
//   soft_rst     in   1    one-cycle pulse from axil_regs (CTRL.SOFT_RST)
//   -- from decision_unit (valid/ready) --
//   s_valid      in   1
//   s_ready      out  1    always 1: this block takes one entry per clock
//   s_kind       in   2
//   s_pkt_len    in   16   frame bytes as received, without FCS
//   s_ovf_cnt    in   16   frames lost to overflow since the previous entry
//   s_class      in   2
//   s_conf       in   7    confidence (margin, not a probability)
//   s_low_conf   in   1
//   -- to axil_regs (all registered, stable between clock edges) --
//   pkt_normal, pkt_scan, pkt_syn              out  CNT_W  packets per class
//   bytes_normal, bytes_scan, bytes_syn        out  CNT_W  bytes per class
//   pkt_dropped, pkt_lowconf, pkt_overflow     out  CNT_W
//   last_result  out  32   [1:0] class, [15:8] confidence, others 0
//
// Parameters
//   CNT_W   counter width, default 32 (the register map). Smaller values are
//           only for the test, which makes wrapping reachable in simulation.
//
// Clock / reset
//   Single clock (core_clk). Synchronous active-low reset clears everything.
//
// Latency
//   1 clock. An entry accepted at edge N is visible in the outputs after
//   edge N. The adds are small (CNT_W bits), so there is no multi-cycle path.
//
// Timing in words
//   Edge 0: s_valid = 1, s_kind = CLASSIFY, class 1, pkt_len 60. After edge 0:
//   pkt_scan is one higher, bytes_scan is 60 higher, last_result shows class 1.
//   If soft_rst = 1 at edge 0, everything reads 0 after edge 0 instead.
// ============================================================================

module stats_counters #(
    parameter int CNT_W = 32
) (
    input  logic             clk,
    input  logic             rst_n,
    input  logic             soft_rst,

    input  logic             s_valid,
    output logic             s_ready,
    input  logic [1:0]       s_kind,
    input  logic [15:0]      s_pkt_len,
    input  logic [15:0]      s_ovf_cnt,
    input  logic [1:0]       s_class,
    input  logic [6:0]       s_conf,
    input  logic             s_low_conf,

    output logic [CNT_W-1:0] pkt_normal,
    output logic [CNT_W-1:0] pkt_scan,
    output logic [CNT_W-1:0] pkt_syn,
    output logic [CNT_W-1:0] bytes_normal,
    output logic [CNT_W-1:0] bytes_scan,
    output logic [CNT_W-1:0] bytes_syn,
    output logic [CNT_W-1:0] pkt_dropped,
    output logic [CNT_W-1:0] pkt_lowconf,
    output logic [CNT_W-1:0] pkt_overflow,
    output logic [31:0]      last_result
);

    localparam logic [1:0] KIND_CLASSIFY = 2'd0;
    localparam logic [1:0] KIND_DROP     = 2'd1;

    // The entry is always taken: statistics never apply backpressure.
    assign s_ready = 1'b1;

    // Zero-extended inputs, so the additions below have CNT_W bits on both
    // sides (this is what makes the wrap exact and keeps lint quiet).
    // For CNT_W < 16 the extra upper bits are cut off, which is the wrap.
    logic [CNT_W-1:0] len_ext, ovf_ext, one_ext;
    always_comb begin
        len_ext = CNT_W'(s_pkt_len);
        ovf_ext = CNT_W'(s_ovf_cnt);
        one_ext = CNT_W'(1);
    end

    wire take        = s_valid;                      // s_ready is always 1
    wire is_classify = (s_kind == KIND_CLASSIFY);
    wire is_drop     = (s_kind == KIND_DROP);
    wire cls_ok      = (s_class != 2'd3);            // class 3 does not exist

    wire inc_normal  = take && is_classify && (s_class == 2'd0);
    wire inc_scan    = take && is_classify && (s_class == 2'd1);
    wire inc_syn     = take && is_classify && (s_class == 2'd2);
    wire inc_lowconf = take && is_classify && cls_ok && s_low_conf;
    wire inc_drop    = take && is_drop;
    wire upd_last    = take && is_classify && cls_ok;

    always_ff @(posedge clk) begin
        if (!rst_n || soft_rst) begin
            // Reset and soft reset have the same effect on the counters.
            // soft_rst wins over a simultaneous entry (regmap).
            pkt_normal   <= '0;
            pkt_scan     <= '0;
            pkt_syn      <= '0;
            bytes_normal <= '0;
            bytes_scan   <= '0;
            bytes_syn    <= '0;
            pkt_dropped  <= '0;
            pkt_lowconf  <= '0;
            pkt_overflow <= '0;
            last_result  <= 32'd0;
        end else begin
            if (inc_normal) begin
                pkt_normal   <= pkt_normal + one_ext;
                bytes_normal <= bytes_normal + len_ext;
            end
            if (inc_scan) begin
                pkt_scan     <= pkt_scan + one_ext;
                bytes_scan   <= bytes_scan + len_ext;
            end
            if (inc_syn) begin
                pkt_syn      <= pkt_syn + one_ext;
                bytes_syn    <= bytes_syn + len_ext;
            end
            if (inc_lowconf) pkt_lowconf <= pkt_lowconf + one_ext;
            if (inc_drop)    pkt_dropped <= pkt_dropped + one_ext;
            // Every kind carries an overflow count (adding 0 changes nothing).
            if (take)        pkt_overflow <= pkt_overflow + ovf_ext;
            // LAST_RESULT layout: [1:0] class, [7:2] 0, [15:8] confidence,
            // [31:16] 0 (regmap). Byte-aligned so software can read bytes.
            if (upd_last)    last_result <= {16'd0, 1'b0, s_conf, 6'd0, s_class};
        end
    end

endmodule
