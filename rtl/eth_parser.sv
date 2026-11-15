// ============================================================================
// eth_parser: Ethernet (layer 2) header parser for NeuroGate
// ============================================================================
//
// Purpose
//   Reads each Ethernet frame as it streams past and works out three things
//   about its layer-2 header (SPEC.md section 4):
//     1. Does the frame carry one 802.1Q VLAN tag (EtherType 0x8100)?
//        If so the IPv4 header starts at byte 18 instead of byte 14.
//     2. Is the EtherType after the optional tag IPv4 (0x0800)?
//     3. Must the frame be dropped for a layer-2 reason? Reasons, checked in
//        this priority order (same order as model/features.py parse_frame):
//          - tuser set on the last beat (upstream MAC saw a bad FCS)
//          - frame too short to hold its Ethernet header
//            (< 14 bytes, or < 18 bytes when it has a VLAN tag)
//          - EtherType is not IPv4 (ARP, IPv6, QinQ, ...)
//   The frame bytes themselves pass through unchanged, one register stage
//   later, so the next block (l3l4_parser) can read the IPv4 / TCP / UDP
//   fields. This block does not touch layer 3; a frame that is IPv4 here can
//   still be dropped later (for example an IPv4 header cut short).
//
// Ports (direction, width)
//   clk            in   1   net_clk (156.25 MHz in the full chip)
//   rst_n          in   1   active-low reset, synchronous to clk (the
//                           top-level reset synchronizer provides this)
//   -- input AXI-Stream (from skid_fifo) --
//   s_tdata        in   64  frame bytes; first byte of a beat in [7:0]
//   s_tkeep        in   8   byte enables; all ones except on the last beat,
//                           where they are contiguous from bit 0 (SPEC 3.1)
//   s_tlast        in   1   last beat of the frame
//   s_tuser        in   1   bad-FCS flag, only looked at on the tlast beat
//   s_tvalid       in   1
//   s_tready       out  1
//   -- output AXI-Stream (to l3l4_parser): same beats, plus sideband --
//   m_tdata, m_tkeep, m_tlast, m_tuser, m_tvalid   out  64/8/1/1/1
//   m_tready       in   1
//   m_hdr_done     out  1   the whole Ethernet header (incl. any VLAN tag)
//                           has been seen by this beat. m_vlan and m_ipv4
//                           are only meaningful while this is 1.
//   m_vlan         out  1   frame carries one VLAN tag (L3 starts at byte 18)
//   m_ipv4         out  1   EtherType after the optional tag is 0x0800
//   m_drop         out  1   on the tlast beat only: drop this frame (L2 reason)
//   m_drop_reason  out  2   on the tlast beat only: 0 none, 1 tuser,
//                           2 short header, 3 not IPv4. Zero on other beats.
//
// Clock / reset
//   Single clock domain (net_clk). All flops reset by rst_n (active low).
//   A reset in the middle of a frame clears the output register and the
//   per-frame state; the first beat after reset is treated as byte 0 of a
//   new frame. Nothing from the interrupted frame comes out after reset.
//
// Latency and throughput
//   1 clock cycle from an input beat to the same beat on the output, with
//   its sideband. One beat per cycle (no bubbles) while m_tready is high.
//   Backpressure: s_tready = !m_tvalid || m_tready, a standard one-entry
//   pipeline register. (In the full chip nothing downstream stalls, but the
//   block is still AXI-Stream correct and is tested with random m_tready.)
//
// Example timing, in words (untagged IPv4 frame, 60 bytes = 8 beats)
//   Cycle 0: beat 0 (bytes 0-7, MAC addresses) is accepted.
//   Cycle 1: beat 0 appears on m_*, m_hdr_done = 0. Beat 1 (bytes 8-15,
//            EtherType in bytes 12-13) is accepted.
//   Cycle 2: beat 1 appears with m_hdr_done = 1, m_vlan = 0, m_ipv4 = 1.
//   ...      later beats keep the same flags.
//   Cycle 8: beat 7 appears with m_tlast = 1, m_drop = 0, m_drop_reason = 0.
//   With a VLAN tag, beat 1 shows m_vlan = 1, m_hdr_done = 0, and beat 2
//   (inner EtherType in bytes 16-17) is the first with m_hdr_done = 1.
// ============================================================================

module eth_parser (
    input  logic        clk,
    input  logic        rst_n,

    input  logic [63:0] s_tdata,
    input  logic [7:0]  s_tkeep,
    input  logic        s_tlast,
    input  logic        s_tuser,
    input  logic        s_tvalid,
    output logic        s_tready,

    output logic [63:0] m_tdata,
    output logic [7:0]  m_tkeep,
    output logic        m_tlast,
    output logic        m_tuser,
    output logic        m_tvalid,
    input  logic        m_tready,
    output logic        m_hdr_done,
    output logic        m_vlan,
    output logic        m_ipv4,
    output logic        m_drop,
    output logic [1:0]  m_drop_reason
);

    // ---- Constants ---------------------------------------------------------
    // Byte positions are frame offsets. With 8 bytes per beat, byte N sits in
    // beat N/8, lane N%8, i.e. tdata[8*(N%8) +: 8].
    localparam int unsigned BYTES_PER_BEAT = 8;
    localparam int unsigned ETYPE_BYTE     = 12;  // EtherType: bytes 12 (high), 13 (low)
    localparam int unsigned INNER_BYTE     = 16;  // EtherType after a VLAN tag: bytes 16, 17
    localparam int unsigned ETYPE_BEAT     = ETYPE_BYTE / BYTES_PER_BEAT;   // = 1
    localparam int unsigned ETYPE_LANE     = ETYPE_BYTE % BYTES_PER_BEAT;   // = 4
    localparam int unsigned INNER_BEAT     = INNER_BYTE / BYTES_PER_BEAT;   // = 2
    localparam int unsigned INNER_LANE     = INNER_BYTE % BYTES_PER_BEAT;   // = 0

    localparam logic [15:0] ETYPE_IPV4 = 16'h0800;
    localparam logic [15:0] ETYPE_VLAN = 16'h8100;

    // Drop reason codes (m_drop_reason)
    localparam logic [1:0] DROP_NONE     = 2'd0;
    localparam logic [1:0] DROP_TUSER    = 2'd1;
    localparam logic [1:0] DROP_SHORT    = 2'd2;
    localparam logic [1:0] DROP_NOT_IPV4 = 2'd3;

    // Beat counter only needs to tell beats 0, 1, 2 and "3 or later" apart:
    // the header is finished by beat 2. It saturates instead of wrapping so a
    // long frame never looks like it is back at beat 1.
    localparam int unsigned BEAT_CNT_W = 2;
    localparam logic [BEAT_CNT_W-1:0] BEAT_MAX = '1;

    // ---- Per-frame state (describes the frame currently streaming in) -----
    logic [BEAT_CNT_W-1:0] beat_q;      // index of the next input beat in its frame
    logic                  hdr_done_q;  // Ethernet header fully seen
    logic                  vlan_q;      // frame has a VLAN tag
    logic                  ipv4_q;      // final EtherType is IPv4

    // Values after taking the current input beat into account
    logic                  hdr_done_d, vlan_d, ipv4_d;
    logic [15:0]           etype_outer, etype_inner;
    logic [1:0]            drop_reason_d;

    logic in_fire;   // an input beat is accepted this cycle
    assign s_tready = !m_tvalid || m_tready;
    assign in_fire  = s_tvalid && s_tready;

    // Big-endian 16-bit fields from the current beat (network byte order:
    // the lower-numbered byte is the high byte).
    assign etype_outer = {s_tdata[8*ETYPE_LANE     +: 8], s_tdata[8*(ETYPE_LANE+1) +: 8]};
    assign etype_inner = {s_tdata[8*INNER_LANE     +: 8], s_tdata[8*(INNER_LANE+1) +: 8]};

    // The low byte of each EtherType is present in this beat (tkeep). Pulled
    // out of the always_comb so Icarus does not warn about constant selects.
    logic etype_outer_ok, etype_inner_ok;
    assign etype_outer_ok = s_tkeep[ETYPE_LANE+1];
    assign etype_inner_ok = s_tkeep[INNER_LANE+1];

    // Header decode for the current beat. A field is used only if its last
    // byte is really present (tkeep), so a short frame never reads stale
    // bytes from tdata.
    always_comb begin
        hdr_done_d = hdr_done_q;
        vlan_d     = vlan_q;
        ipv4_d     = ipv4_q;
        if (beat_q == BEAT_CNT_W'(0)) begin
            // First beat of a frame: forget everything about the last frame.
            hdr_done_d = 1'b0;
            vlan_d     = 1'b0;
            ipv4_d     = 1'b0;
        end else if (beat_q == BEAT_CNT_W'(ETYPE_BEAT) && etype_outer_ok) begin
            vlan_d     = (etype_outer == ETYPE_VLAN);
            ipv4_d     = (etype_outer == ETYPE_IPV4);
            hdr_done_d = (etype_outer != ETYPE_VLAN);   // a tag needs 4 more bytes
        end else if (beat_q == BEAT_CNT_W'(INNER_BEAT) && vlan_q && etype_inner_ok) begin
            ipv4_d     = (etype_inner == ETYPE_IPV4);   // QinQ (0x8100 again) is not IPv4
            hdr_done_d = 1'b1;
        end

        // Drop decision, used only on the tlast beat. Priority matches the
        // Python model: tuser, then short header, then not IPv4.
        if (s_tuser)          drop_reason_d = DROP_TUSER;
        else if (!hdr_done_d) drop_reason_d = DROP_SHORT;
        else if (!ipv4_d)     drop_reason_d = DROP_NOT_IPV4;
        else                  drop_reason_d = DROP_NONE;
    end

    // Per-frame state update on every accepted beat
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            beat_q     <= '0;
            hdr_done_q <= 1'b0;
            vlan_q     <= 1'b0;
            ipv4_q     <= 1'b0;
        end else if (in_fire) begin
            hdr_done_q <= hdr_done_d;
            vlan_q     <= vlan_d;
            ipv4_q     <= ipv4_d;
            if (s_tlast)                 beat_q <= '0;  // next beat starts a new frame
            else if (beat_q != BEAT_MAX) beat_q <= beat_q + 1'b1;  // saturate
        end
    end

    // Output register: valid flag (reset) and payload (loaded on accept)
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            m_tvalid <= 1'b0;
        end else if (s_tready) begin
            // Either the output is empty or it is being taken this cycle,
            // so it can be refilled (or emptied if no input beat arrives).
            m_tvalid <= s_tvalid;
        end
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            m_tdata       <= '0;
            m_tkeep       <= '0;
            m_tlast       <= 1'b0;
            m_tuser       <= 1'b0;
            m_hdr_done    <= 1'b0;
            m_vlan        <= 1'b0;
            m_ipv4        <= 1'b0;
            m_drop        <= 1'b0;
            m_drop_reason <= DROP_NONE;
        end else if (in_fire) begin
            m_tdata       <= s_tdata;
            m_tkeep       <= s_tkeep;
            m_tlast       <= s_tlast;
            m_tuser       <= s_tuser;
            m_hdr_done    <= hdr_done_d;
            m_vlan        <= vlan_d;
            m_ipv4        <= ipv4_d;
            m_drop        <= s_tlast && (drop_reason_d != DROP_NONE);
            m_drop_reason <= s_tlast ? drop_reason_d : DROP_NONE;
        end
    end

endmodule
