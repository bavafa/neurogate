// ============================================================================
// axil_regs: AXI4-Lite register block (control, threshold, read-only statistics)
// ============================================================================
//
// Purpose
//   The software-facing side of NeuroGate. AXI4-Lite is a simple bus that
//   software (or a testbench) uses to read and write 32-bit registers, one at
//   a time. This block implements the map in docs/regmap.md (that file is the
//   reference; SPEC.md section 3.2 has a summary):
//     0x00 CTRL         RW  bit 0 SOFT_RST: write 1 -> one-cycle soft_rst pulse
//     0x04 CONF_THRESH  RW  bits [6:0], reset value CONF_THRESH_RESET
//     0x08..0x2C        RO  counters and LAST_RESULT, values come from
//                           stats_counters (see the order in the port list)
//     0x30..0x3C        unmapped: read 0 + SLVERR, write ignored + SLVERR
//   Address bits [1:0] are ignored; the block decodes bits [5:2] and ignores
//   anything above (so 0x40 aliases 0x00, as the regmap says).
//   Writes to read-only registers are ignored and answered OKAY.
//   WSTRB (write strobes, one bit per byte lane) is honored: a lane that is not
//   strobed keeps its old value. CONF_THRESH only has byte lane 0, and only
//   bits [6:0] of it are stored. SOFT_RST needs WSTRB[0] = 1 and WDATA[0] = 1.
//
// Handshake choice (D-030, owner-approved in D-041)
//   One transaction at a time per direction. The write address and write data
//   are accepted together, in one cycle, only when both are valid and the
//   previous write response has been taken (so AWREADY = WREADY depends on both
//   valids; AXI allows a ready to depend on valid, not the reverse). The read
//   address is accepted when no read response is waiting. Read data is
//   registered, so RDATA never changes while RVALID is high and RREADY is low.
//
// Soft reset
//   soft_rst is combinational: high in the cycle in which the write is accepted,
//   so stats_counters clears at the clock edge that completes the write. A read
//   issued after the write response always sees cleared counters. soft_rst does
//   not change conf_thresh and does not flush any pipeline (regmap).
//
// Ports (direction, width)
//   clk, rst_n         in   1     core_clk, active-low synchronous reset
//   s_axil_awaddr      in   6     write address (byte offset, bits [1:0] ignored)
//   s_axil_awvalid     in   1
//   s_axil_awready     out  1
//   s_axil_wdata       in   32
//   s_axil_wstrb       in   4
//   s_axil_wvalid      in   1
//   s_axil_wready      out  1
//   s_axil_bresp       out  2     0 OKAY, 2 SLVERR
//   s_axil_bvalid      out  1
//   s_axil_bready      in   1
//   s_axil_araddr      in   6     read address
//   s_axil_arvalid     in   1
//   s_axil_arready     out  1
//   s_axil_rdata       out  32
//   s_axil_rresp       out  2     0 OKAY, 2 SLVERR
//   s_axil_rvalid      out  1
//   s_axil_rready      in   1
//   soft_rst           out  1     to stats_counters
//   conf_thresh        out  7     to decision_unit
//   pkt_normal ... last_result    in   32 each: values of stats_counters
//                                  (pkt_normal, pkt_scan, pkt_syn, bytes_normal,
//                                  bytes_scan, bytes_syn, pkt_dropped,
//                                  pkt_lowconf, last_result, pkt_overflow)
//
// Parameters
//   CONF_THRESH_RESET  reset value of CONF_THRESH (0..127), default 13: chosen in
//                      M3 from the validation split (never the test set).
//
// Clock / reset
//   Single clock (core_clk); the whole block lives in that domain, so there is
//   no clock crossing here. Synchronous active-low reset: CONF_THRESH returns
//   to CONF_THRESH_RESET and both response channels go idle.
//
// Latency
//   Write: accepted at edge N (needs both AW and W valid); BVALID high after
//   edge N; the register has its new value after edge N. Read: accepted at
//   edge N; RVALID and RDATA after edge N (one clock). Reads show the counter
//   values as they are before edge N.
//
// Timing in words
//   Edge 0: AWVALID, WVALID high, BVALID low -> both accepted, CONF_THRESH
//   updated. After edge 0: BVALID = 1, BRESP = OKAY. Edge 1 with BREADY = 1:
//   response taken, BVALID falls, the next write may be accepted at edge 1 or
//   later (not at edge 1 itself: BVALID was still high before that edge).
// ============================================================================

module axil_regs #(
    parameter logic [6:0] CONF_THRESH_RESET = 7'd13
) (
    input  logic        clk,
    input  logic        rst_n,

    // Waiver (UNUSEDSIGNAL): the bus has fixed widths, but the register map
    // ignores address bits [1:0] (word-aligned registers), write data above
    // bit 6 (only CONF_THRESH[6:0] and SOFT_RST[0] are writable) and strobe
    // lanes 1..3 (no register has more than one writable byte). Those bits
    // are intentionally unused.
    /* verilator lint_off UNUSEDSIGNAL */
    input  logic [5:0]  s_axil_awaddr,
    input  logic        s_axil_awvalid,
    output logic        s_axil_awready,
    input  logic [31:0] s_axil_wdata,
    input  logic [3:0]  s_axil_wstrb,
    input  logic        s_axil_wvalid,
    output logic        s_axil_wready,
    output logic [1:0]  s_axil_bresp,
    output logic        s_axil_bvalid,
    input  logic        s_axil_bready,

    input  logic [5:0]  s_axil_araddr,
    /* verilator lint_on UNUSEDSIGNAL */
    input  logic        s_axil_arvalid,
    output logic        s_axil_arready,
    output logic [31:0] s_axil_rdata,
    output logic [1:0]  s_axil_rresp,
    output logic        s_axil_rvalid,
    input  logic        s_axil_rready,

    output logic        soft_rst,
    output logic [6:0]  conf_thresh,

    input  logic [31:0] pkt_normal,
    input  logic [31:0] pkt_scan,
    input  logic [31:0] pkt_syn,
    input  logic [31:0] bytes_normal,
    input  logic [31:0] bytes_scan,
    input  logic [31:0] bytes_syn,
    input  logic [31:0] pkt_dropped,
    input  logic [31:0] pkt_lowconf,
    input  logic [31:0] last_result,
    input  logic [31:0] pkt_overflow
);

    localparam logic [1:0] RESP_OKAY   = 2'b00;
    localparam logic [1:0] RESP_SLVERR = 2'b10;

    // Word-slot numbers (address bits [5:2]).
    localparam logic [3:0] SLOT_CTRL        = 4'h0;
    localparam logic [3:0] SLOT_CONF_THRESH = 4'h1;
    localparam logic [3:0] SLOT_PKT_NORMAL  = 4'h2;
    localparam logic [3:0] SLOT_PKT_SCAN    = 4'h3;
    localparam logic [3:0] SLOT_PKT_SYN     = 4'h4;
    localparam logic [3:0] SLOT_BYTES_NORM  = 4'h5;
    localparam logic [3:0] SLOT_BYTES_SCAN  = 4'h6;
    localparam logic [3:0] SLOT_BYTES_SYN   = 4'h7;
    localparam logic [3:0] SLOT_PKT_DROPPED = 4'h8;
    localparam logic [3:0] SLOT_PKT_LOWCONF = 4'h9;
    localparam logic [3:0] SLOT_LAST_RESULT = 4'hA;
    localparam logic [3:0] SLOT_PKT_OVERFLW = 4'hB;
    // Slots 0xC..0xF (offsets 0x30..0x3C) are unmapped.

    // ------------------------------------------------------------------
    // Write channel
    // ------------------------------------------------------------------
    // The write is accepted when address and data are both valid and no
    // response is waiting. Both ready signals rise together.
    wire wr_fire = s_axil_awvalid && s_axil_wvalid && !s_axil_bvalid;
    assign s_axil_awready = wr_fire;
    assign s_axil_wready  = wr_fire;

    wire [3:0] wr_slot   = s_axil_awaddr[5:2];
    wire       wr_mapped = (wr_slot <= SLOT_PKT_OVERFLW);

    // Soft reset pulse: only CTRL, only byte lane 0 strobed, only bit 0 = 1.
    assign soft_rst = wr_fire && (wr_slot == SLOT_CTRL) && s_axil_wstrb[0] && s_axil_wdata[0];

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            conf_thresh   <= CONF_THRESH_RESET;
            s_axil_bvalid <= 1'b0;
            s_axil_bresp  <= RESP_OKAY;
        end else begin
            if (s_axil_bvalid && s_axil_bready) s_axil_bvalid <= 1'b0;
            if (wr_fire) begin
                s_axil_bvalid <= 1'b1;
                s_axil_bresp  <= wr_mapped ? RESP_OKAY : RESP_SLVERR;
                // CONF_THRESH: byte lane 0 only; bit 7 is reserved and not stored.
                if (wr_slot == SLOT_CONF_THRESH && s_axil_wstrb[0])
                    conf_thresh <= s_axil_wdata[6:0];
            end
        end
    end

    // ------------------------------------------------------------------
    // Read channel
    // ------------------------------------------------------------------
    assign s_axil_arready = !s_axil_rvalid;
    wire rd_fire = s_axil_arvalid && !s_axil_rvalid;

    wire [3:0] rd_slot = s_axil_araddr[5:2];
    logic [31:0] rd_data;
    logic        rd_mapped;

    always_comb begin
        rd_data   = 32'd0;
        rd_mapped = 1'b1;
        case (rd_slot)
            SLOT_CTRL:        rd_data = 32'd0;                    // SOFT_RST always reads 0
            SLOT_CONF_THRESH: rd_data = {25'd0, conf_thresh};
            SLOT_PKT_NORMAL:  rd_data = pkt_normal;
            SLOT_PKT_SCAN:    rd_data = pkt_scan;
            SLOT_PKT_SYN:     rd_data = pkt_syn;
            SLOT_BYTES_NORM:  rd_data = bytes_normal;
            SLOT_BYTES_SCAN:  rd_data = bytes_scan;
            SLOT_BYTES_SYN:   rd_data = bytes_syn;
            SLOT_PKT_DROPPED: rd_data = pkt_dropped;
            SLOT_PKT_LOWCONF: rd_data = pkt_lowconf;
            SLOT_LAST_RESULT: rd_data = last_result;
            SLOT_PKT_OVERFLW: rd_data = pkt_overflow;
            default: begin                                        // unmapped: 0 + SLVERR
                rd_data   = 32'd0;
                rd_mapped = 1'b0;
            end
        endcase
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            s_axil_rvalid <= 1'b0;
            s_axil_rdata  <= 32'd0;
            s_axil_rresp  <= RESP_OKAY;
        end else begin
            if (s_axil_rvalid && s_axil_rready) s_axil_rvalid <= 1'b0;
            if (rd_fire) begin
                s_axil_rvalid <= 1'b1;
                s_axil_rdata  <= rd_data;
                s_axil_rresp  <= rd_mapped ? RESP_OKAY : RESP_SLVERR;
            end
        end
    end

endmodule
