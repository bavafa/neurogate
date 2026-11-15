// ============================================================================
// l3l4_parser: IPv4 + TCP/UDP header parser for NeuroGate
// ============================================================================
//
// Purpose
//   Sits right after eth_parser. Reads the frame bytes as they stream past,
//   picks out the IPv4 and TCP/UDP header fields the feature builder needs,
//   and emits ONE result record per frame, after the frame's last beat:
//     - keep or drop, and why (drop rules of SPEC.md section 4, in the same
//       order as parse_frame() in model/features.py, which is the reference)
//     - for kept frames: TCP or UDP, the SYN / ACK / RST-or-FIN flags, the
//       IPv4 total-length field, TTL and the destination port
//     - for every frame: the frame length in bytes (pkt_len, SPEC 8.1)
//   The frame bytes are consumed here; nothing downstream needs them.
//
//   Drop reasons, highest priority first (r_drop_reason code in brackets):
//     [1] tuser set              ] passed through from eth_parser
//     [2] Ethernet header short  ]   (its m_drop_reason 1..3 keeps the
//     [3] EtherType not IPv4     ]    same code here)
//     [4] frame too short for a 20-byte IPv4 header
//     [5] IPv4 version field is not 4 (conservative reading, DECISIONS D-011)
//     [6] IHL < 5 (header length field smaller than the minimum header)
//     [7] frame ends inside the IPv4 options (IHL > 5, options cut short)
//     [8] non-first fragment (fragment offset != 0, so no TCP/UDP header)
//     [9] protocol is neither TCP (6) nor UDP (17)
//    [10] frame too short for the L4 fields: TCP needs 14 bytes (up to and
//         including the flags byte), UDP needs 4 (the two ports)
//   IP options (IHL 6..15) are skipped: the TCP/UDP header is found at
//   byte  L3 + 4*IHL, where L3 = 14 (untagged) or 18 (one VLAN tag).
//
// Ports (direction, width)
//   clk             in   1   net_clk (156.25 MHz in the full chip)
//   rst_n           in   1   active-low reset, synchronous to clk
//   -- input AXI-Stream, from eth_parser (same beats it received) --
//   s_tdata         in   64  frame bytes; first byte of a beat in [7:0]
//   s_tkeep         in   8   byte enables, contiguous from bit 0 (SPEC 3.1)
//   s_tlast         in   1   last beat of the frame
//   s_tvalid        in   1
//   s_tready        out  1
//   s_vlan          in   1   eth_parser m_vlan: frame has one VLAN tag, so
//                            the IPv4 header starts at byte 18, not 14.
//                            Valid from beat 1 on (the beat with bytes 8-15).
//   s_drop          in   1   eth_parser m_drop (tlast beat only)
//   s_drop_reason   in   2   eth_parser m_drop_reason (tlast beat only)
//   -- per-frame result (valid/ready, one record per frame) --
//   r_valid         out  1
//   r_ready         in   1
//   r_drop          out  1   drop this frame (count it in PKT_DROPPED)
//   r_drop_reason   out  4   0 = kept, else the code listed above
//   r_is_tcp        out  1   } parsed fields, meaningful only when
//   r_syn           out  1   }   r_drop = 0; all forced to 0 when dropped.
//   r_ack           out  1   }   The three flags are 0 for UDP.
//   r_rst_fin       out  1   }   (RST or FIN set)
//   r_ip_len        out  16  }   IPv4 total-length header field
//   r_ttl           out  8   }
//   r_dst_port      out  16  }   TCP/UDP destination port
//   r_pkt_len       out  16  frame length in bytes (sum of tkeep bits),
//                            saturates at 65535. Valid for every frame.
//
// Clock / reset
//   Single clock domain (net_clk). Synchronous active-low reset. A reset in
//   the middle of a frame clears the byte position, the length count and the
//   result register, so no record for the cut frame ever comes out, and the
//   next beat after reset is byte 0 of a new frame.
//
// Latency and throughput
//   The record appears on r_* one clock after the frame's tlast beat is
//   accepted. One beat per cycle in. Backpressure: s_tready = !r_valid ||
//   r_ready, so the input stalls only while a finished record is waiting
//   (in the full chip the feature builder never stalls, so this never
//   happens there; the block is still tested with random r_ready).
//
// How fields are found (why there is no big shift register)
//   A beat counter gives the frame offset of every byte lane:
//   pos = 8*beat + lane. A field byte is captured when its offset matches
//   (and its tkeep bit is set, so a short frame never captures stale lane
//   bytes). Offsets past 127 never matter: the furthest byte needed is the
//   TCP flags byte at 18 + 60 + 13 = 91. The IHL value used to find the L4
//   header is the registered one; the IHL byte always arrives at least two
//   beats before any L4 byte (L4 starts 20 or more bytes after it).
//   Length checks use the running byte count including the tlast beat, so
//   "frame too short for X" means the bytes of X were never all received.
//
// Example timing, in words (untagged IPv4 + TCP SYN, 54 bytes = 7 beats)
//   Beat 1 (bytes 8-15): byte 14 (version/IHL = 0x45) is captured.
//   Beat 2 (bytes 16-23): total length (16-17), fragment (20-21), TTL (22),
//                         protocol (23) are captured.
//   Beat 4 (bytes 32-39): IHL = 5, so TCP starts at 34; dst port is 36-37.
//   Beat 5 (bytes 40-47): TCP flags byte is 34 + 13 = 47.
//   Beat 6 (bytes 48-53, tlast): checks run; next clock r_valid = 1 with
//                         r_drop = 0, r_pkt_len = 54.
// ============================================================================

module l3l4_parser (
    input  logic        clk,
    input  logic        rst_n,

    input  logic [63:0] s_tdata,
    input  logic [7:0]  s_tkeep,
    input  logic        s_tlast,
    input  logic        s_tvalid,
    output logic        s_tready,
    input  logic        s_vlan,
    input  logic        s_drop,
    input  logic [1:0]  s_drop_reason,

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

    // ---- Constants ---------------------------------------------------------
    localparam int unsigned LANES     = 8;     // bytes per beat
    localparam int unsigned LANE_W    = 3;     // log2(LANES)
    localparam int unsigned BEAT_W    = 4;     // beat counter, saturates at 15
    localparam int unsigned POS_W     = BEAT_W + LANE_W;  // byte offset 0..127
    localparam int unsigned LEN_W     = 16;    // pkt_len width (SPEC 8.1)

    // Where the IPv4 header starts (byte offset in the frame)
    localparam logic [POS_W-1:0] L3_UNTAGGED = POS_W'(14);
    localparam logic [POS_W-1:0] L3_TAGGED   = POS_W'(18);

    // Byte offsets inside the IPv4 header (RFC 791)
    localparam logic [POS_W-1:0] IP_VER_IHL = POS_W'(0);
    localparam logic [POS_W-1:0] IP_LEN_HI  = POS_W'(2);
    localparam logic [POS_W-1:0] IP_LEN_LO  = POS_W'(3);
    localparam logic [POS_W-1:0] IP_FRAG_HI = POS_W'(6);   // low 5 bits = offset[12:8]
    localparam logic [POS_W-1:0] IP_FRAG_LO = POS_W'(7);   // offset[7:0]
    localparam logic [POS_W-1:0] IP_TTL     = POS_W'(8);
    localparam logic [POS_W-1:0] IP_PROTO   = POS_W'(9);
    localparam logic [POS_W-1:0] IP_MIN_HDR = POS_W'(20);  // bytes

    // Byte offsets inside the TCP/UDP header (dst port is at 2-3 in both)
    localparam logic [POS_W-1:0] L4_DPORT_HI = POS_W'(2);
    localparam logic [POS_W-1:0] L4_DPORT_LO = POS_W'(3);
    localparam logic [POS_W-1:0] TCP_FLAGS   = POS_W'(13);
    localparam logic [POS_W-1:0] TCP_NEED    = POS_W'(14); // bytes through the flags byte
    localparam logic [POS_W-1:0] UDP_NEED    = POS_W'(4);  // bytes through the dst port

    localparam logic [3:0] IP_VERSION_4 = 4'd4;
    localparam logic [3:0] IHL_MIN      = 4'd5;
    localparam logic [7:0] PROTO_TCP    = 8'd6;
    localparam logic [7:0] PROTO_UDP    = 8'd17;
    // TCP flag bits inside the flags byte
    localparam int unsigned FLAG_FIN = 0;
    localparam int unsigned FLAG_SYN = 1;
    localparam int unsigned FLAG_RST = 2;
    localparam int unsigned FLAG_ACK = 4;

    // r_drop_reason codes. 1..3 are eth_parser's codes, passed through.
    localparam logic [3:0] DROP_NONE       = 4'd0;
    localparam logic [3:0] DROP_SHORT_IP   = 4'd4;
    localparam logic [3:0] DROP_IP_VERSION = 4'd5;
    localparam logic [3:0] DROP_BAD_IHL    = 4'd6;
    localparam logic [3:0] DROP_SHORT_OPTS = 4'd7;
    localparam logic [3:0] DROP_FRAGMENT   = 4'd8;
    localparam logic [3:0] DROP_L4_PROTO   = 4'd9;
    localparam logic [3:0] DROP_SHORT_L4   = 4'd10;

    localparam logic [BEAT_W-1:0] BEAT_MAX = '1;
    localparam logic [LEN_W-1:0]  LEN_MAX  = '1;

    // ---- Per-frame state ---------------------------------------------------
    logic [BEAT_W-1:0] beat_q;       // index of the next input beat in its frame
    logic [LEN_W-1:0]  len_q;        // bytes received so far in this frame
    // Captured header bytes. Each is overwritten whenever its offset goes
    // past, so a value is from the current frame whenever the frame is long
    // enough to pass the matching length check.
    logic [7:0]        ver_ihl_q, len_hi_q, len_lo_q, frag_hi_q, frag_lo_q;
    logic [7:0]        ttl_q, proto_q, dport_hi_q, dport_lo_q, flags_q;
    // Same, after taking the current beat into account
    logic [7:0]        ver_ihl_d, len_hi_d, len_lo_d, frag_hi_d, frag_lo_d;
    logic [7:0]        ttl_d, proto_d, dport_hi_d, dport_lo_d, flags_d;

    logic in_fire;
    assign s_tready = !r_valid || r_ready;
    assign in_fire  = s_tvalid && s_tready;

    // Start of the IPv4 header and of the TCP/UDP header (byte offsets).
    // l4_base uses the registered IHL (see header: it is always known by the
    // time an L4 byte arrives). Max 18 + 4*15 = 78, fits in 7 bits.
    logic [POS_W-1:0] l3_base, l4_base;
    assign l3_base = s_vlan ? L3_TAGGED : L3_UNTAGGED;
    assign l4_base = l3_base + {1'b0, ver_ihl_q[3:0], 2'b00};

    // Byte capture: for each lane present in this beat, compare its frame
    // offset with every field offset.
    always_comb begin
        logic [POS_W-1:0] pos;
        logic [7:0]       b;
        ver_ihl_d  = ver_ihl_q;
        len_hi_d   = len_hi_q;
        len_lo_d   = len_lo_q;
        frag_hi_d  = frag_hi_q;
        frag_lo_d  = frag_lo_q;
        ttl_d      = ttl_q;
        proto_d    = proto_q;
        dport_hi_d = dport_hi_q;
        dport_lo_d = dport_lo_q;
        flags_d    = flags_q;
        for (int i = 0; i < LANES; i++) begin
            pos = {beat_q, LANE_W'(i)};
            b   = s_tdata[8*i +: 8];
            if (s_tkeep[i]) begin
                if (pos == l3_base + IP_VER_IHL)  ver_ihl_d  = b;
                if (pos == l3_base + IP_LEN_HI)   len_hi_d   = b;
                if (pos == l3_base + IP_LEN_LO)   len_lo_d   = b;
                if (pos == l3_base + IP_FRAG_HI)  frag_hi_d  = b;
                if (pos == l3_base + IP_FRAG_LO)  frag_lo_d  = b;
                if (pos == l3_base + IP_TTL)      ttl_d      = b;
                if (pos == l3_base + IP_PROTO)    proto_d    = b;
                if (pos == l4_base + L4_DPORT_HI) dport_hi_d = b;
                if (pos == l4_base + L4_DPORT_LO) dport_lo_d = b;
                if (pos == l4_base + TCP_FLAGS)   flags_d    = b;
            end
        end
    end

    // Frame length including this beat (tkeep is contiguous, so the number
    // of set bits is the byte count). Saturates at 65535 (SPEC 8.1).
    logic [3:0]       beat_bytes;
    logic [LEN_W:0]   len_sum;
    logic [LEN_W-1:0] len_d;
    always_comb begin
        beat_bytes = '0;
        for (int i = 0; i < LANES; i++) beat_bytes = beat_bytes + 4'(s_tkeep[i]);
    end
    assign len_sum = {1'b0, len_q} + {{(LEN_W-3){1'b0}}, beat_bytes};
    assign len_d   = len_sum[LEN_W] ? LEN_MAX : len_sum[LEN_W-1:0];

    // Drop decision for a frame ending on this beat. Same order as the
    // model's parse_frame(). Lengths are compared in LEN_W bits.
    logic [3:0]       ihl, ip_version;
    logic             is_tcp_d, frag_nz;
    logic [POS_W-1:0] l4_now, l4_need;
    logic [3:0]       reason_d;
    // Version and IHL are split out here, not inside the always_comb below,
    // so Icarus does not warn about constant part-selects in always_comb.
    assign ihl        = ver_ihl_d[3:0];
    assign ip_version = ver_ihl_d[7:4];
    assign is_tcp_d = (proto_d == PROTO_TCP);
    assign frag_nz  = (frag_hi_d[4:0] != 5'd0) || (frag_lo_d != 8'd0);
    assign l4_now   = l3_base + {1'b0, ihl, 2'b00};
    assign l4_need  = l4_now + (is_tcp_d ? TCP_NEED : UDP_NEED);

    always_comb begin
        if (s_drop)
            reason_d = {2'b00, s_drop_reason};              // layer-2 reason wins
        else if (len_d < LEN_W'(l3_base + IP_MIN_HDR))
            reason_d = DROP_SHORT_IP;
        else if (ip_version != IP_VERSION_4)
            reason_d = DROP_IP_VERSION;
        else if (ihl < IHL_MIN)
            reason_d = DROP_BAD_IHL;
        else if (len_d < LEN_W'(l4_now))
            reason_d = DROP_SHORT_OPTS;
        else if (frag_nz)
            reason_d = DROP_FRAGMENT;
        else if (proto_d != PROTO_TCP && proto_d != PROTO_UDP)
            reason_d = DROP_L4_PROTO;
        else if (len_d < LEN_W'(l4_need))
            reason_d = DROP_SHORT_L4;
        else
            reason_d = DROP_NONE;
    end

    // ---- Per-frame state update --------------------------------------------
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            beat_q <= '0;
            len_q  <= '0;
        end else if (in_fire) begin
            if (s_tlast) begin                // next beat starts a new frame
                beat_q <= '0;
                len_q  <= '0;
            end else begin
                if (beat_q != BEAT_MAX) beat_q <= beat_q + 1'b1;  // saturate
                len_q <= len_d;
            end
        end
    end

    // Captured bytes need no reset value: each one is only used when the
    // length checks prove it was received in the current frame. Reset keeps
    // simulation free of X anyway.
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            ver_ihl_q  <= '0;
            len_hi_q   <= '0;
            len_lo_q   <= '0;
            frag_hi_q  <= '0;
            frag_lo_q  <= '0;
            ttl_q      <= '0;
            proto_q    <= '0;
            dport_hi_q <= '0;
            dport_lo_q <= '0;
            flags_q    <= '0;
        end else if (in_fire) begin
            ver_ihl_q  <= ver_ihl_d;
            len_hi_q   <= len_hi_d;
            len_lo_q   <= len_lo_d;
            frag_hi_q  <= frag_hi_d;
            frag_lo_q  <= frag_lo_d;
            ttl_q      <= ttl_d;
            proto_q    <= proto_d;
            dport_hi_q <= dport_hi_d;
            dport_lo_q <= dport_lo_d;
            flags_q    <= flags_d;
        end
    end

    // ---- Result register (one record per frame) ----------------------------
    logic keep_d;
    assign keep_d = (reason_d == DROP_NONE);

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            r_valid <= 1'b0;
        end else if (in_fire && s_tlast) begin
            r_valid <= 1'b1;                  // a new record (s_tready implies the old one left)
        end else if (r_ready) begin
            r_valid <= 1'b0;
        end
    end

    // Parsed fields are forced to zero for dropped frames, so a dropped
    // frame can never leak half-parsed values downstream.
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            r_drop        <= 1'b0;
            r_drop_reason <= DROP_NONE;
            r_is_tcp      <= 1'b0;
            r_syn         <= 1'b0;
            r_ack         <= 1'b0;
            r_rst_fin     <= 1'b0;
            r_ip_len      <= '0;
            r_ttl         <= '0;
            r_dst_port    <= '0;
            r_pkt_len     <= '0;
        end else if (in_fire && s_tlast) begin
            r_drop        <= !keep_d;
            r_drop_reason <= reason_d;
            r_is_tcp      <= keep_d && is_tcp_d;
            r_syn         <= keep_d && is_tcp_d && flags_d[FLAG_SYN];
            r_ack         <= keep_d && is_tcp_d && flags_d[FLAG_ACK];
            r_rst_fin     <= keep_d && is_tcp_d && (flags_d[FLAG_RST] || flags_d[FLAG_FIN]);
            r_ip_len      <= keep_d ? {len_hi_d, len_lo_d}     : 16'd0;
            r_ttl         <= keep_d ? ttl_d                    : 8'd0;
            r_dst_port    <= keep_d ? {dport_hi_d, dport_lo_d} : 16'd0;
            r_pkt_len     <= len_d;
        end
    end

endmodule
