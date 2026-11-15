// ============================================================================
// async_fifo: dual-clock AXI-Stream FIFO (the net_clk -> core_clk crossing)
// ============================================================================
//
// Purpose
//   Moves stream beats from one clock domain (the write side, net_clk in the
//   chip) to another clock domain that runs at an unrelated frequency and
//   phase (the read side, core_clk). SPEC.md section 8: this is the only
//   place packet data crosses between the two clocks.
//
//   Each slot holds one whole beat: tdata, tkeep, tlast and tuser travel
//   together (the same fields as skid_fifo). The FIFO never looks at the
//   values. In the full chip the 114-bit entry of SPEC section 8.1 is
//   carried in tdata (DATA_W = 114); the other fields are then tied off.
//
// How the crossing is made safe (the standard Gray-pointer method)
//   * Each side keeps its own pointer: the write pointer lives only in the
//     write clock domain, the read pointer only in the read clock domain.
//     Pointers are one bit wider than needed to address DEPTH slots; the
//     extra top bit tells "full" (pointers differ by DEPTH) from "empty"
//     (pointers equal).
//   * A pointer is sent to the other side in Gray code, from a flop. Gray
//     code changes exactly one bit per step. If the other clock samples it
//     while that bit is changing, it reads either the old or the new value,
//     never a mix of bits that is far from both. An old value is safe: the
//     FIFO just looks a little fuller (write side) or emptier (read side)
//     than it really is, for a few cycles.
//   * Each received Gray pointer goes through two flops in the receiving
//     clock (a "two-flop synchronizer"). If the first flop goes metastable
//     (an undecided level because the input changed right at the clock edge),
//     it has a full clock period to settle before the second flop uses it.
//   * The memory itself is written by the write clock and read by the read
//     clock. That is safe because a slot is only read after its write has
//     been seen through the synchronizer (at least two read clock edges
//     later), and a slot is only rewritten after its read has been seen
//     through the other synchronizer.
//   RTL simulation cannot show metastability; it only shows the logic is
//   right for the clock ratios simulated. See the CDC note in DECISIONS.md.
//
// Ports (direction, width)
//   -- write side, clock wr_clk --
//   wr_clk     in   1          write clock (net_clk, 156.25 MHz in the chip)
//   wr_rst_n   in   1          active-low reset for the write side (see below)
//   s_tdata    in   DATA_W     beat data
//   s_tkeep    in   DATA_W/8   byte enables
//   s_tlast    in   1          last beat of the frame
//   s_tuser    in   1          bad-FCS flag (carried, not used)
//   s_tvalid   in   1
//   s_tready   out  1          high while the FIFO is known not to be full
//   -- read side, clock rd_clk --
//   rd_clk     in   1          read clock (core_clk, 200 MHz in the chip)
//   rd_rst_n   in   1          active-low reset for the read side (see below)
//   m_tdata, m_tkeep, m_tlast, m_tuser, m_tvalid   out  DATA_W / DATA_W/8 / 1 / 1 / 1
//   m_tready   in   1
//
// Parameters
//   DATA_W  data width in bits, at least 8 (64 by default, like skid_fifo)
//   DEPTH   number of slots, a power of two, at least 4 (default 16, the
//           AFIFO_DEPTH of SPEC section 8.1). Gray-code pointers only wrap
//           correctly when DEPTH is a power of two, and the full compare
//           below needs pointers of at least 3 bits; anything else stops
//           elaboration with an error.
//
// Clock / reset
//   Two clocks with no fixed relation. Each side has its own active-low
//   reset. Both resets must come from the same chip reset through one reset
//   synchronizer per domain (asynchronous assert, synchronous release; SPEC
//   section 8), so they go low at the same moment and come back high on an
//   edge of their own clock. Inside the FIFO the reset is asynchronous
//   (flops clear as soon as the reset goes low, without a clock edge). This
//   matters: both sides clear at the same instant, so neither side ever
//   compares its pointer with a stale pointer from the other side that is
//   still pre-reset. Releasing one side before the other is safe; both
//   pointers are zero until the released side moves.
//   During reset: m_tvalid = 0 and s_tready = 0. s_tready rises one write
//   clock after wr_rst_n is released. Beats stored at reset are discarded.
//   The slot memory has no reset, so the m_* data fields are only meaningful
//   while m_tvalid = 1.
//
// Latency and throughput
//   A beat written at a write clock edge shows up on the read side (m_tvalid
//   = 1) after 2 to 3 read clock edges: two edges through the synchronizer
//   and one for the registered empty flag. The "full" flag clears the same
//   way, 2 to 3 write clock edges after a read.
//   Each side moves one beat per own clock cycle when it can: the input
//   accepts one beat per wr_clk while not full; the output delivers one beat
//   per rd_clk while not empty. Long-run rate = the slower of the two.
//
// Example timing, in words (DEPTH = 16)
//   The write side accepts beat A at a wr_clk edge. Its Gray write pointer
//   steps by one at that edge. Over the next two rd_clk edges the step passes
//   through the synchronizer; at the third rd_clk edge the empty flag clears
//   and A appears on m_tdata with m_tvalid = 1. If m_tready is high, A leaves
//   at the next rd_clk edge. If the read side stops (m_tready low), the write
//   side fills all 16 slots, then s_tready drops and the source must hold its
//   beat. When the reader takes a beat, s_tready rises again a few write
//   clocks later, after the new read pointer has crossed over.
// ============================================================================
module async_fifo #(
    parameter int unsigned DATA_W = 64,
    parameter int unsigned DEPTH  = 16
) (
    // write side
    input  logic                wr_clk,
    input  logic                wr_rst_n,
    input  logic [DATA_W-1:0]   s_tdata,
    input  logic [DATA_W/8-1:0] s_tkeep,
    input  logic                s_tlast,
    input  logic                s_tuser,
    input  logic                s_tvalid,
    output logic                s_tready,
    // read side
    input  logic                rd_clk,
    input  logic                rd_rst_n,
    output logic [DATA_W-1:0]   m_tdata,
    output logic [DATA_W/8-1:0] m_tkeep,
    output logic                m_tlast,
    output logic                m_tuser,
    output logic                m_tvalid,
    input  logic                m_tready
);

    // ---- Constants ---------------------------------------------------------
    localparam int unsigned KEEP_W  = DATA_W / 8;
    localparam int unsigned ENTRY_W = DATA_W + KEEP_W + 2;  // + tlast + tuser
    localparam int unsigned ADDR_W  = $clog2(DEPTH);        // slot address bits
    localparam int unsigned PTR_W   = ADDR_W + 1;           // + wrap bit

    // Reject parameters the pointer logic cannot handle, at elaboration time.
    if (DEPTH < 4 || (DEPTH & (DEPTH - 1)) != 0) begin : g_bad_depth
        $error("async_fifo: DEPTH must be a power of two and at least 4");
    end
    if (DATA_W < 8) begin : g_bad_width
        $error("async_fifo: DATA_W must be at least 8");
    end

    // Binary to Gray: neighbouring values differ in exactly one bit.
    function automatic logic [PTR_W-1:0] bin2gray(input logic [PTR_W-1:0] b);
        return b ^ (b >> 1);
    endfunction

    // ---- Storage (written in wr_clk, read in rd_clk) -----------------------
    logic [ENTRY_W-1:0] mem [0:DEPTH-1];

    // The two Gray pointers that cross between the domains. Each is a flop
    // output in its own domain (never a combinational value, which could
    // glitch). Declared here because each side reads the other's.
    logic [PTR_W-1:0] wr_gray_q;       // write pointer in Gray code (wr_clk flop)
    logic [PTR_W-1:0] rd_gray_q;       // read pointer in Gray code (rd_clk flop)

    // ======================================================================
    // Write side (wr_clk)
    // ======================================================================
    logic [PTR_W-1:0] wr_bin_q;        // write pointer, binary (addresses mem)
    logic [PTR_W-1:0] rd_gray_sync1_q; // read pointer, 1st synchronizer flop
    logic [PTR_W-1:0] rd_gray_sync2_q; // read pointer, 2nd flop (safe to use)
    logic             full_q;          // registered full flag

    logic             wr_en;
    logic [PTR_W-1:0] wr_bin_next;
    logic [PTR_W-1:0] wr_gray_next;
    logic             full_next;

    assign s_tready     = !full_q;
    assign wr_en        = s_tvalid && !full_q;
    assign wr_bin_next  = wr_bin_q + PTR_W'(wr_en);
    assign wr_gray_next = bin2gray(wr_bin_next);
    // Full: the next write pointer is exactly DEPTH ahead of the (synchronized)
    // read pointer. In Gray code that is "top two bits inverted, rest equal".
    assign full_next = (wr_gray_next ==
                        {~rd_gray_sync2_q[PTR_W-1:PTR_W-2], rd_gray_sync2_q[PTR_W-3:0]});

    // Memory write. No reset (see header).
    always_ff @(posedge wr_clk) begin
        if (wr_en) begin
            mem[wr_bin_q[ADDR_W-1:0]] <= {s_tuser, s_tlast, s_tkeep, s_tdata};
        end
    end

    // Pointer, synchronizer and flag flops. full_q resets to 1 so s_tready is
    // low during reset and the source cannot hand over a beat that would be
    // thrown away.
    always_ff @(posedge wr_clk or negedge wr_rst_n) begin
        if (!wr_rst_n) begin
            wr_bin_q        <= '0;
            wr_gray_q       <= '0;
            rd_gray_sync1_q <= '0;
            rd_gray_sync2_q <= '0;
            full_q          <= 1'b1;
        end else begin
            wr_bin_q        <= wr_bin_next;
            wr_gray_q       <= wr_gray_next;
            rd_gray_sync1_q <= rd_gray_q;        // clock-domain crossing (Gray)
            rd_gray_sync2_q <= rd_gray_sync1_q;
            full_q          <= full_next;
        end
    end

    // ======================================================================
    // Read side (rd_clk)
    // ======================================================================
    logic [PTR_W-1:0] rd_bin_q;        // read pointer, binary (addresses mem)
    logic [PTR_W-1:0] wr_gray_sync1_q; // write pointer, 1st synchronizer flop
    logic [PTR_W-1:0] wr_gray_sync2_q; // write pointer, 2nd flop (safe to use)
    logic             empty_q;         // registered empty flag

    logic             rd_en;
    logic [PTR_W-1:0] rd_bin_next;
    logic [PTR_W-1:0] rd_gray_next;
    logic             empty_next;

    assign m_tvalid     = !empty_q;
    assign rd_en        = m_tready && !empty_q;
    assign rd_bin_next  = rd_bin_q + PTR_W'(rd_en);
    assign rd_gray_next = bin2gray(rd_bin_next);
    // Empty: the next read pointer has caught up with the synchronized write pointer.
    assign empty_next   = (rd_gray_next == wr_gray_sync2_q);

    // First-word-fall-through: the oldest beat is always on the output.
    assign {m_tuser, m_tlast, m_tkeep, m_tdata} = mem[rd_bin_q[ADDR_W-1:0]];

    always_ff @(posedge rd_clk or negedge rd_rst_n) begin
        if (!rd_rst_n) begin
            rd_bin_q        <= '0;
            rd_gray_q       <= '0;
            wr_gray_sync1_q <= '0;
            wr_gray_sync2_q <= '0;
            empty_q         <= 1'b1;
        end else begin
            rd_bin_q        <= rd_bin_next;
            rd_gray_q       <= rd_gray_next;
            wr_gray_sync1_q <= wr_gray_q;        // clock-domain crossing (Gray)
            wr_gray_sync2_q <= wr_gray_sync1_q;
            empty_q         <= empty_next;
        end
    end

endmodule
