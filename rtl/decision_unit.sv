// ============================================================================
// decision_unit: confidence and low-confidence flag for each finished entry
// ============================================================================
//
// Purpose
//   Sits behind mlp_core (SPEC.md section 7). For a CLASSIFY entry it turns
//   the three raw int32 network outputs ("logits") and the class chosen by
//   mlp_core into
//     confidence = min((top - second) >> CONF_SHIFT, 127)       (0..127)
//     low_conf   = confidence < conf_thresh
//   where "top" is the logit of the chosen class and "second" is the larger
//   of the other two logits. The shift is needed because the raw difference
//   of int8-network logits is usually in the thousands, which would sit
//   above 127 for almost every packet and tell the reader nothing. Shifting
//   right by 8 means one confidence step = 256 raw logit units.
//   Confidence is a logit margin, NOT a probability.
//   For DROP and OVF_ONLY entries it only passes the metadata through
//   (confidence 0, low_conf 0, class 0): no packet was classified, so
//   stats_counters must not count a class or a low-confidence packet.
//   The reference for every bit is model/golden.py (decide()).
//
// Who picks the class
//   mlp_core already outputs the argmax class (ties: lowest index; D-025,
//   D-026). This block trusts it and does not repeat the comparison; it only
//   needs "top" and "second". Cheap safety rule: if a caller ever gave a
//   class that is not the largest logit, top - second would be negative and
//   the result is clamped to confidence 0 (SPEC section 7 says clamp to
//   0..127). Class value 3 does not exist (3 outputs); it is treated as an
//   entry with confidence 0.
//
// Ports (direction, width)
//   clk          in   1    core_clk
//   rst_n        in   1    active-low reset, synchronous to clk
//   -- from mlp_core (valid/ready) --
//   s_valid      in   1
//   s_ready      out  1    high when the output register is empty or being
//                          emptied this cycle (no combinational path from
//                          m_ready to the inputs except this one term)
//   s_kind       in   2    0 CLASSIFY, 1 DROP, 2 OVF_ONLY
//   s_pkt_len    in   16   } metadata, copied unchanged
//   s_pkt_id     in   16   }
//   s_ovf_cnt    in   16   }
//   s_logit0..2  in   32   signed int32 network outputs (raw)
//   s_class      in   2    argmax class from mlp_core
//   conf_thresh  in   7    CONF_THRESH register value (0..127). 0 = never
//                          flag. Read when the entry is accepted, so a change
//                          applies to entries accepted after it.
//   -- to stats_counters / result register (valid/ready, held while stalled) --
//   m_valid      out  1
//   m_ready      in   1
//   m_kind       out  2
//   m_pkt_len    out  16
//   m_pkt_id     out  16
//   m_ovf_cnt    out  16
//   m_class      out  2    0 unless CLASSIFY
//   m_conf       out  7    0..127, 0 unless CLASSIFY
//   m_low_conf   out  1    0 unless CLASSIFY
//
// Parameters
//   CONF_SHIFT   right shift of the logit difference (0..31). Must equal
//                "conf_shift" in data/model/weights.json; the test checks the
//                default (8). With 0 it is the literal "top - second" rule.
//
// Clock / reset
//   Single clock (core_clk). Synchronous active-low reset: output register
//   cleared (m_valid low), nothing is output for an abandoned entry.
//
// Latency
//   1 clock. The result is registered, so m_valid is high in the cycle after
//   the entry is accepted. One entry per clock when m_ready stays high. A
//   stalled output holds all its values until m_ready is high.
//
// Timing in words
//   Edge 0: s_valid = 1 and s_ready = 1, entry taken; confidence and flag
//   are computed in the cycle before this edge from the input wires and
//   stored. After edge 0: m_valid = 1 and all outputs valid. If m_ready = 1
//   at edge 1 the result is taken there and a new entry may be taken at the
//   same edge.
// ============================================================================

module decision_unit #(
    parameter int CONF_SHIFT = 8
) (
    input  logic               clk,
    input  logic               rst_n,

    input  logic               s_valid,
    output logic               s_ready,
    input  logic [1:0]         s_kind,
    input  logic [15:0]        s_pkt_len,
    input  logic [15:0]        s_pkt_id,
    input  logic [15:0]        s_ovf_cnt,
    input  logic signed [31:0] s_logit0,
    input  logic signed [31:0] s_logit1,
    input  logic signed [31:0] s_logit2,
    input  logic [1:0]         s_class,
    input  logic [6:0]         conf_thresh,

    output logic               m_valid,
    input  logic               m_ready,
    output logic [1:0]         m_kind,
    output logic [15:0]        m_pkt_len,
    output logic [15:0]        m_pkt_id,
    output logic [15:0]        m_ovf_cnt,
    output logic [1:0]         m_class,
    output logic [6:0]         m_conf,
    output logic               m_low_conf
);

    localparam logic [1:0] KIND_CLASSIFY = 2'd0;
    localparam logic [6:0] CONF_MAX      = 7'd127;

    // Parameter check at elaboration (both tools accept an elaboration-time
    // generate-if with $error).
    if (CONF_SHIFT < 0 || CONF_SHIFT > 31) begin : g_bad_shift
        $error("decision_unit: CONF_SHIFT must be 0..31");
    end

    // ------------------------------------------------------------------
    // Top and second logit.
    // top    = logit of the chosen class.
    // second = the larger of the other two.
    // ------------------------------------------------------------------
    logic signed [31:0] top, other_a, other_b, second;

    always_comb begin
        case (s_class)
            2'd0:    begin top = s_logit0; other_a = s_logit1; other_b = s_logit2; end
            2'd1:    begin top = s_logit1; other_a = s_logit0; other_b = s_logit2; end
            2'd2:    begin top = s_logit2; other_a = s_logit0; other_b = s_logit1; end
            // Class 3 does not exist. Make top equal second so the
            // confidence is 0 (never an unexplained value).
            default: begin top = s_logit0; other_a = s_logit0; other_b = s_logit0; end
        endcase
        second = (other_a > other_b) ? other_a : other_b;
    end

    // ------------------------------------------------------------------
    // margin = top - second. Both are int32, so the difference needs 33
    // signed bits (top = 2^31-1, second = -2^31 gives 2^32-1).
    // ------------------------------------------------------------------
    logic signed [32:0] margin;
    assign margin = 33'(top) - 33'(second);

    // Shift the margin right (arithmetic, but margin >= 0 in normal use).
    // Then clamp: negative -> 0 (safety, see header), above 127 -> 127.
    logic signed [32:0] shifted;
    logic               shifted_neg;     // sign bit
    logic [6:0]         shifted_lo;      // low 7 bits
    logic [6:0]         conf_next;

    assign shifted     = margin >>> CONF_SHIFT;
    assign shifted_neg = shifted[32];
    assign shifted_lo  = shifted[6:0];

    // (Selects are taken with assigns, not inside always_comb: Icarus does
    // not fully support constant part-selects in always_* processes.)
    always_comb begin
        if (shifted_neg)                conf_next = 7'd0;        // negative
        else if (shifted > 33'sd127)    conf_next = CONF_MAX;    // cap
        else                            conf_next = shifted_lo;
    end

    wire is_classify = (s_kind == KIND_CLASSIFY);

    // ------------------------------------------------------------------
    // Output register with valid/ready.
    // s_ready = register empty, or it is being emptied this cycle.
    // ------------------------------------------------------------------
    assign s_ready = !m_valid || m_ready;

    wire take = s_valid && s_ready;

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            m_valid    <= 1'b0;
            m_kind     <= 2'd0;
            m_pkt_len  <= 16'd0;
            m_pkt_id   <= 16'd0;
            m_ovf_cnt  <= 16'd0;
            m_class    <= 2'd0;
            m_conf     <= 7'd0;
            m_low_conf <= 1'b0;
        end else begin
            if (m_valid && m_ready) m_valid <= 1'b0;
            if (take) begin
                m_valid    <= 1'b1;
                m_kind     <= s_kind;
                m_pkt_len  <= s_pkt_len;
                m_pkt_id   <= s_pkt_id;
                m_ovf_cnt  <= s_ovf_cnt;
                m_class    <= is_classify ? s_class : 2'd0;
                m_conf     <= is_classify ? conf_next : 7'd0;
                m_low_conf <= is_classify && (conf_next < conf_thresh);
            end
        end
    end

endmodule
