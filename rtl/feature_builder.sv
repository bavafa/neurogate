// ============================================================================
// feature_builder: sliding-window features + async-FIFO entry writer
// ============================================================================
//
// Purpose
//   Takes the one-record-per-frame output of l3l4_parser and turns every
//   frame into one 114-bit entry for the async FIFO (SPEC.md section 8.1):
//     - kept frames (valid IPv4 TCP/UDP) become CLASSIFY entries carrying the
//       8 int8 features of SPEC section 5;
//     - dropped frames become DROP entries (features all zero), so the
//       counters on the other side of the clock crossing still see them.
//   It also gives every frame its 16-bit pkt_id, and counts frames lost
//   because the async FIFO was full (the "pending overflow" count, sent
//   across inside the next entry, or in an OVF_ONLY entry).
//   The reference for every bit is model/features.py (FeatureWindow and
//   run_stream). The cocotb test compares the two bit for bit.
//
// The sliding window (SPEC section 5), as it is built here
//   * ring[64]: one 7-bit slot per packet in the window, {is_syn, bucket}.
//     is_syn = TCP with SYN set and ACK clear; bucket = 6-bit hash of the
//     destination port, dst_port[5:0] XOR dst_port[11:6] (the same as
//     (p ^ (p >> 6)) & 0x3F in the model).
//   * bcnt[64]: one 7-bit counter per bucket = how many window packets
//     have that bucket (0..64). A bucket is "occupied" while its counter is
//     above zero. Counters (not a bitmap) are needed because two window
//     packets can share a bucket: when one leaves, the bucket stays occupied.
//   * occ, syn_cnt: running totals (occupied buckets, SYN packets), so F6 and
//     F7 never need a 64-way sum.
//   Per kept packet, all in ONE clock (the next packet may come right after):
//   the oldest slot leaves (if the window holds 64 packets), the new packet
//   enters, and F6/F7 use the totals AFTER both steps. If the leaving and the
//   entering packet share a bucket, that counter does not change at all.
//
// Feature formulas (SPEC section 5, all integer)
//   F0 = 127 if TCP SYN without ACK      F1 = 127 if TCP ACK
//   F2 = 127 if TCP RST or FIN           F5 = 127 if dst_port < 1024
//   F3 = min(ip_len, 1500) * 127 // 1500, computed as
//        (min(ip_len, 1500) * 22195) >> 18. The multiply-and-shift equals the
//        floor division for every input 0..1500 (checked exhaustively in the
//        test), and avoids a hardware divider.
//   F4 = ttl >> 1
//   F6 = min(2 * syn_cnt, 127)           F7 = min(2 * occ, 127)
//
// Ports (direction, width)
//   clk          in   1    net_clk (156.25 MHz in the full chip)
//   rst_n        in   1    active-low reset, synchronous to clk
//   -- per-frame record from l3l4_parser (its r_* outputs) --
//   s_valid      in   1    one record this cycle
//   s_ready      out  1    always 1: this block never stalls the parsers
//   s_drop       in   1    frame is malformed or unsupported
//   s_is_tcp     in   1    } parsed header fields, used only when
//   s_syn        in   1    }   s_drop = 0
//   s_ack        in   1    }
//   s_rst_fin    in   1    }   RST or FIN set
//   s_ip_len     in   16   }   IPv4 total-length field
//   s_ttl        in   8    }
//   s_dst_port   in   16   }
//   s_pkt_len    in   16   frame length in bytes (saturated at 65535)
//   -- entry stream into the async FIFO write side --
//   m_tdata      out  114  entry (layout below)
//   m_tvalid     out  1    an entry is offered this cycle
//   m_tready     in   1    async FIFO s_tready (low = full)
//
// Entry layout (SPEC section 8.1)
//   [63:0] features (F0 in [7:0] .. F7 in [63:56]), [79:64] pkt_len,
//   [95:80] pkt_id, [111:96] ovf_cnt, [113:112] kind
//   kind: 0 CLASSIFY, 1 DROP, 2 OVF_ONLY. An OVF_ONLY entry has zero
//   features, pkt_len and pkt_id; only its ovf_cnt matters.
//
// Overflow (why m_tvalid does not wait for m_tready)
//   SPEC section 8.1 says the input is never stalled because the async FIFO
//   is full. So an entry that finds m_tready low is thrown away, and a
//   16-bit saturating pending counter goes up by 1. The next entry that IS
//   written carries the pending count in ovf_cnt, and the counter clears.
//   When nothing else is offered and the count is non-zero, an OVF_ONLY
//   entry is offered to carry it. This port therefore does not follow the
//   AXI-Stream rule "hold a valid beat until it is taken": an offered beat
//   is either taken at this clock edge or gone. The async FIFO only writes
//   on tvalid && tready, so that is safe. m_tvalid never depends on
//   m_tready combinationally (no loop between the two blocks).
//
// Clock / reset
//   Single clock (net_clk). Synchronous active-low reset: empties the window,
//   restarts pkt_id at 0, clears the pending overflow count and any entry
//   not yet written. The window state has no reset other than this one
//   (a soft reset of the counters does not touch it, SPEC 8.1).
//
// Latency and throughput
//   One record per clock in. The entry is offered one clock after the record
//   is taken (it sits in an output register), and is written or discarded
//   at the next clock edge.
//
// Example timing, in words
//   Cycle 0: s_valid = 1 for a kept TCP SYN to port 80, window empty.
//   Edge 1: ring[0] = {1, bucket(80) = 16}, bcnt[16] = 1, occ = 1,
//           syn_cnt = 1; the entry register holds features
//           {127, 0, 0, F3, F4, 127, 2, 2}, pkt_id 0, kind CLASSIFY.
//   Cycle 1: m_tvalid = 1. If m_tready = 1 the FIFO takes it at edge 2;
//           if not, the entry is gone and the pending count becomes 1.
// ============================================================================

module feature_builder #(
    parameter int unsigned WINDOW = 64      // window depth; SPEC fixes 64
) (
    input  logic         clk,
    input  logic         rst_n,

    input  logic         s_valid,
    output logic         s_ready,
    input  logic         s_drop,
    input  logic         s_is_tcp,
    input  logic         s_syn,
    input  logic         s_ack,
    input  logic         s_rst_fin,
    input  logic [15:0]  s_ip_len,
    input  logic [7:0]   s_ttl,
    input  logic [15:0]  s_dst_port,
    input  logic [15:0]  s_pkt_len,

    output logic [113:0] m_tdata,
    output logic         m_tvalid,
    input  logic         m_tready
);

    // ---- Constants -------------------------------------------------------
    localparam int unsigned NBUCKET  = 64;            // 6-bit port hash
    localparam int unsigned BKT_W    = 6;
    localparam int unsigned PTR_W    = $clog2(WINDOW);
    localparam int unsigned CNT_W    = PTR_W + 1;     // holds 0..WINDOW
    localparam logic [7:0]  FMAX     = 8'd127;        // every feature is 0..127
    localparam logic [15:0] LEN_CAP  = 16'd1500;      // F3 saturates here
    localparam logic [14:0] F3_MUL   = 15'd22195;     // 127/1500 * 2^18, see header
    localparam int unsigned F3_SHIFT = 18;
    localparam logic [1:0]  KIND_CLASSIFY = 2'd0;
    localparam logic [1:0]  KIND_DROP     = 2'd1;
    localparam logic [1:0]  KIND_OVF_ONLY = 2'd2;
    localparam logic [15:0] OVF_MAX  = 16'hFFFF;

    // The bucket hash and the 7-bit totals are written for a 64-packet window.
    if (WINDOW != 64) begin : g_bad_window
        $error("feature_builder: WINDOW must be 64 (SPEC section 5)");
    end

    // The parsers are never stalled (SPEC 8.1): every record is taken.
    assign s_ready = 1'b1;

    // ---- Window state ----------------------------------------------------
    logic [BKT_W:0]   ring [WINDOW];      // {is_syn, bucket}
    logic [CNT_W-1:0] bcnt [NBUCKET];     // packets per bucket, 0..64
    logic [PTR_W-1:0] wr_ptr;             // next slot = oldest slot when full
    logic [CNT_W-1:0] fill;               // packets in the window, 0..64
    logic [CNT_W-1:0] occ;                // buckets with bcnt > 0
    logic [CNT_W-1:0] syn_cnt;            // window packets with is_syn

    // ---- Combinational window update for the incoming packet -------------
    logic             take;               // a kept packet enters this cycle
    logic             win_full;
    logic             new_syn, old_syn;
    logic [BKT_W-1:0] new_bkt, old_bkt;
    logic [CNT_W-1:0] cnt_new, cnt_old;   // bucket counters before the update
    logic             same_bkt;
    logic             leave_empties;      // oldest packet's bucket becomes empty
    logic             enter_fills;        // new packet's bucket was empty
    logic [CNT_W-1:0] occ_next, syn_next;

    // Part-selects of ports are kept in continuous assignments: Icarus does
    // not fully support constant part-selects inside always_comb.
    assign new_bkt = s_dst_port[5:0] ^ s_dst_port[11:6];

    always_comb begin
        take     = s_valid && !s_drop;
        win_full = (fill == CNT_W'(WINDOW));
        new_syn  = s_is_tcp && s_syn && !s_ack;
        {old_syn, old_bkt} = ring[wr_ptr];
        cnt_new  = bcnt[new_bkt];
        cnt_old  = bcnt[old_bkt];
        same_bkt = (old_bkt == new_bkt);
        // Same bucket leaving and entering: its counter is unchanged and stays
        // >= 1, so the occupied count is unchanged too (the model's
        // "decrement, then increment" gives the same result).
        leave_empties = win_full && !same_bkt && (cnt_old == CNT_W'(1));
        enter_fills   = (cnt_new == '0);
        occ_next = occ - CNT_W'(leave_empties) + CNT_W'(enter_fills);
        syn_next = syn_cnt - CNT_W'(win_full && old_syn) + CNT_W'(new_syn);
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            wr_ptr  <= '0;
            fill    <= '0;
            occ     <= '0;
            syn_cnt <= '0;
            for (int b = 0; b < NBUCKET; b++) bcnt[b] <= '0;
            // ring[] needs no reset: a slot is only read once fill == WINDOW,
            // i.e. after it has been written since the reset.
        end else if (take) begin
            ring[wr_ptr] <= {new_syn, new_bkt};
            wr_ptr       <= wr_ptr + PTR_W'(1);
            if (!win_full) fill <= fill + CNT_W'(1);
            occ     <= occ_next;
            syn_cnt <= syn_next;
            if (!win_full) begin
                bcnt[new_bkt] <= cnt_new + CNT_W'(1);
            end else if (!same_bkt) begin
                bcnt[old_bkt] <= cnt_old - CNT_W'(1);
                bcnt[new_bkt] <= cnt_new + CNT_W'(1);
            end
        end
    end

    // ---- Features (combinational, from the totals after the update) ------
    logic [10:0] len_capped;              // min(ip_len, 1500), 0..1500
    logic [25:0] f3_prod;                 // 1500 * 22195 < 2^26
    logic [7:0]  feat [8];

    // min(2 * n, 127) for a 0..64 count
    function automatic logic [7:0] twice_sat(input logic [CNT_W-1:0] n);
        logic [CNT_W:0] d;
        d = {n, 1'b0};
        return (d > (CNT_W+1)'(FMAX)) ? FMAX : 8'(d);
    endfunction

    logic        well_known;              // dst_port < 1024

    assign len_capped = (s_ip_len > LEN_CAP) ? LEN_CAP[10:0] : s_ip_len[10:0];
    assign f3_prod    = 26'(len_capped) * 26'(F3_MUL);
    assign well_known = (s_dst_port[15:10] == 6'd0);

    always_comb begin
        feat[0] = new_syn                ? FMAX : 8'd0;
        feat[1] = (s_is_tcp && s_ack)    ? FMAX : 8'd0;
        feat[2] = (s_is_tcp && s_rst_fin) ? FMAX : 8'd0;
        feat[3] = 8'(f3_prod >> F3_SHIFT);              // 0..127
        feat[4] = s_ttl >> 1;
        feat[5] = well_known             ? FMAX : 8'd0;
        feat[6] = twice_sat(syn_next);
        feat[7] = twice_sat(occ_next);
    end

    // ---- Entry register, pkt_id and pending overflow count ---------------
    logic        e_valid;                 // entry register holds a frame entry
    logic [1:0]  e_kind;
    logic [63:0] e_feat;
    logic [15:0] e_pkt_len, e_pkt_id;
    logic [15:0] pkt_id;                  // id of the next frame
    logic [15:0] pend;                    // frames lost to overflow, not yet sent
    logic        send_ovf;                // offer an OVF_ONLY entry
    logic        written;                 // the offered entry goes into the FIFO

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            e_valid   <= 1'b0;
            e_kind    <= KIND_DROP;
            e_feat    <= '0;
            e_pkt_len <= '0;
            e_pkt_id  <= '0;
            pkt_id    <= '0;
        end else begin
            e_valid <= s_valid;
            if (s_valid) begin
                e_kind    <= s_drop ? KIND_DROP : KIND_CLASSIFY;
                e_feat    <= s_drop ? 64'd0
                           : {feat[7], feat[6], feat[5], feat[4], feat[3], feat[2], feat[1], feat[0]};
                e_pkt_len <= s_pkt_len;
                e_pkt_id  <= pkt_id;
                pkt_id    <= pkt_id + 16'd1;   // wraps (SPEC 8.1)
            end
        end
    end

    always_comb begin
        send_ovf = !e_valid && (pend != '0);
        m_tvalid = e_valid || send_ovf;
        written  = m_tvalid && m_tready;
        m_tdata  = send_ovf ? {KIND_OVF_ONLY, pend, 16'd0, 16'd0, 64'd0}
                            : {e_kind, pend, e_pkt_id, e_pkt_len, e_feat};
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            pend <= '0;
        end else if (written) begin
            pend <= '0;                                   // count went out in ovf_cnt
        end else if (e_valid && pend != OVF_MAX) begin
            pend <= pend + 16'd1;                         // FIFO full: entry lost
        end
    end

endmodule
