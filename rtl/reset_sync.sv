// ============================================================================
// reset_sync: reset synchronizer (asynchronous assert, synchronous release)
// ============================================================================
//
// Purpose
//   The chip has one external active-low reset, rst_n, which is not tied to
//   either clock. Every block inside a clock domain needs a reset whose
//   RELEASE (the 0 -> 1 edge) lines up with that domain's clock. If the
//   release came at a random moment, some flops could leave reset on one
//   clock edge and others on the next, or a flop could go metastable because
//   its reset was released too close to its clock edge.
//   This block gives one domain such a reset (SPEC section 8):
//     - assert: as soon as rst_n goes low, rst_n_out goes low too, without
//       waiting for a clock edge (so reset works even if the clock is
//       stopped);
//     - release: after rst_n goes high, rst_n_out goes high only on the
//       STAGES-th rising edge of clk. The first flop may go metastable when
//       rst_n rises close to a clock edge; the extra flop(s) give it a full
//       clock period to settle before anything else sees the value.
//   neurogate_top uses one instance per clock domain.
//
// Ports (direction, width)
//   clk        in   1   clock of the domain this reset is for
//   rst_n      in   1   external active-low reset, asynchronous to clk
//   rst_n_out  out  1   active-low reset for the domain: goes low at once
//                       with rst_n, goes high STAGES clk edges after rst_n
//                       goes high
//
// Parameters
//   STAGES     number of flops in the chain, at least 2 (default 2)
//
// Clock / reset
//   Flops clocked by clk, cleared asynchronously by rst_n. The first flop's
//   D input is the constant 1; each further flop takes the previous one.
//
// Latency
//   Assert: immediate (no clock). Release: STAGES rising edges of clk.
//
// Timing in words (STAGES = 2)
//   rst_n falls at any time: both flops clear at once, rst_n_out = 0.
//   rst_n rises between edges 0 and 1. Edge 1: flop 1 = 1 (maybe late to
//   settle). Edge 2: flop 2 = 1, rst_n_out = 1. Blocks with a synchronous
//   reset see at least two edges with rst_n_out = 0 after rst_n rises.
// ============================================================================

module reset_sync #(
    parameter int unsigned STAGES = 2
) (
    input  logic clk,
    input  logic rst_n,
    output logic rst_n_out
);

    if (STAGES < 2) begin : g_bad_stages
        $error("reset_sync: STAGES must be at least 2");
    end

    // sync_q[0] is the flop that may go metastable; sync_q[STAGES-1] drives
    // the output. Nothing but the next flop reads the earlier stages.
    // Waiver (SYNCASYNCNET): in neurogate_top the output is used both as an
    // asynchronous reset (async_fifo, so both FIFO sides clear at the same
    // instant, D-019) and as a synchronous reset (all other blocks). Both
    // uses are intended: the release is synchronous to clk either way.
    /* verilator lint_off SYNCASYNCNET */
    logic [STAGES-1:0] sync_q;
    /* verilator lint_on SYNCASYNCNET */

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            sync_q <= '0;
        end else begin
            sync_q <= {sync_q[STAGES-2:0], 1'b1};
        end
    end

    assign rst_n_out = sync_q[STAGES-1];

endmodule
