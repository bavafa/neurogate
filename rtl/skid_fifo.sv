// ============================================================================
// skid_fifo: small AXI-Stream FIFO / skid buffer at the packet input
// ============================================================================
//
// Purpose
//   Sits between the packet source (the upstream MAC) and eth_parser
//   (SPEC.md sections 2 and 3.1). It gives the parsers a stream that comes
//   out of storage flops, and it absorbs a short stall from the next block so
//   the source is not stopped for one slow cycle. A "skid buffer" is exactly
//   this: a few spare slots that let the sender keep going for a moment after
//   the receiver says "not ready".
//
//   Each slot holds one whole stream beat: tdata, tkeep, tlast and tuser
//   travel together, so a beat is never split and the tuser (bad FCS) flag
//   stays attached to the beat it came with. The block does not look at the
//   values; it only stores and returns them in order.
//
// Ports (direction, width)
//   clk        in   1          net_clk (156.25 MHz in the full chip)
//   rst_n      in   1          active-low reset, synchronous to clk
//   -- input AXI-Stream (from the MAC) --
//   s_tdata    in   DATA_W     frame bytes; first byte of a beat in [7:0]
//   s_tkeep    in   DATA_W/8   byte enables (all ones except on the last beat)
//   s_tlast    in   1          last beat of the frame
//   s_tuser    in   1          bad-FCS flag (SPEC 3.1: read on the tlast beat)
//   s_tvalid   in   1
//   s_tready   out  1          high while at least one slot is free
//   -- output AXI-Stream (to eth_parser) --
//   m_tdata, m_tkeep, m_tlast, m_tuser, m_tvalid   out  DATA_W / DATA_W/8 / 1 / 1 / 1
//   m_tready   in   1
//
// Parameters
//   DATA_W  data width in bits, a multiple of 8 (64 in the chip)
//   DEPTH   number of beat slots, a power of two, at least 2 (default 4).
//           A power of two lets the read and write pointers wrap by simply
//           overflowing. With fewer than 2 slots one beat per cycle is not
//           possible, so that is rejected at elaboration time.
//
// Clock / reset
//   Single clock domain (net_clk). rst_n clears the pointers and the beat
//   count, so the FIFO is empty after reset and m_tvalid is low. Any beats
//   stored at that moment are discarded (a reset in the middle of a frame
//   loses that frame, as in eth_parser). The slot contents themselves are not
//   cleared: they are not needed and would cost reset logic on every bit.
//   Because of that, m_tdata / m_tkeep / m_tlast / m_tuser are only
//   meaningful while m_tvalid is 1 (they can show stale data, or X in
//   simulation before the first write).
//
// Latency and throughput
//   Latency: 1 clock cycle. A beat accepted at one clock edge is on the
//   output after that edge (m_tvalid = 1), and leaves at the next edge if
//   m_tready is high.
//   Throughput: one beat per cycle in both directions as long as m_tready is
//   high. In that steady state one slot is occupied, so s_tready stays high.
//   When the FIFO is full, s_tready is low even if m_tready is high in the
//   same cycle. That is a deliberate choice: s_tready then depends only on
//   flops, with no combinational path from m_tready back to the source.
//
// Example timing, in words (DEPTH = 4, m_tready held high)
//   Cycle 0: the source presents beat A (s_tvalid = 1); s_tready = 1, so A
//            is written into slot 0 at the end of the cycle.
//   Cycle 1: A is on the output (m_tvalid = 1); the source presents beat B.
//            Both A and B move at the end of the cycle: A leaves, B is
//            stored. One slot is used throughout.
//   Now hold m_tready low: beats keep arriving, slots fill, and after DEPTH
//   beats s_tready drops. The source must hold its beat (AXI-Stream rule)
//   until s_tready rises again, which happens the cycle after m_tready is
//   high for one cycle and a slot is freed. Output beats keep their order and
//   the output data does not change while m_tvalid = 1 and m_tready = 0.
// ============================================================================
module skid_fifo #(
    parameter int unsigned DATA_W = 64,
    parameter int unsigned DEPTH  = 4
) (
    input  logic                clk,
    input  logic                rst_n,
    // input stream
    input  logic [DATA_W-1:0]   s_tdata,
    input  logic [DATA_W/8-1:0] s_tkeep,
    input  logic                s_tlast,
    input  logic                s_tuser,
    input  logic                s_tvalid,
    output logic                s_tready,
    // output stream
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
    localparam int unsigned PTR_W   = $clog2(DEPTH);
    localparam int unsigned CNT_W   = PTR_W + 1;            // counts 0..DEPTH

    // Reject a DEPTH the pointer logic cannot handle, at elaboration time.
    if (DEPTH < 2 || (DEPTH & (DEPTH - 1)) != 0) begin : g_bad_depth
        $error("skid_fifo: DEPTH must be a power of two and at least 2");
    end

    // ---- State -------------------------------------------------------------
    logic [ENTRY_W-1:0] mem [0:DEPTH-1];  // beat storage, one entry per slot
    logic [PTR_W-1:0]   wr_ptr_q;         // slot the next accepted beat goes into
    logic [PTR_W-1:0]   rd_ptr_q;         // slot holding the oldest beat
    logic [CNT_W-1:0]   count_q;          // number of occupied slots

    logic wr_en;   // a beat is accepted from the source at the next edge
    logic rd_en;   // a beat is taken by the sink at the next edge

    // Ready/valid come straight from the beat count (a flop value).
    assign s_tready = (count_q != CNT_W'(DEPTH));
    assign m_tvalid = (count_q != CNT_W'(0));
    assign wr_en    = s_tvalid && s_tready;
    assign rd_en    = m_tvalid && m_tready;

    // The oldest beat is always presented on the output: a plain read of the
    // slot the read pointer points at (first-word-fall-through).
    assign {m_tuser, m_tlast, m_tkeep, m_tdata} = mem[rd_ptr_q];

    // Storage: written only when a beat is accepted. No reset (see header).
    always_ff @(posedge clk) begin
        if (wr_en) begin
            mem[wr_ptr_q] <= {s_tuser, s_tlast, s_tkeep, s_tdata};
        end
    end

    // Pointers and count. A simultaneous write and read leaves the count as
    // it was. Pointers wrap by overflow because DEPTH is a power of two.
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            wr_ptr_q <= '0;
            rd_ptr_q <= '0;
            count_q  <= '0;
        end else begin
            if (wr_en) wr_ptr_q <= wr_ptr_q + 1'b1;
            if (rd_en) rd_ptr_q <= rd_ptr_q + 1'b1;
            if (wr_en && !rd_en)      count_q <= count_q + 1'b1;
            else if (!wr_en && rd_en) count_q <= count_q - 1'b1;
        end
    end

endmodule
