// ============================================================================
// mlp_core: int8 8-16-3 neural network, one multiply-accumulate per clock
// ============================================================================
//
// Purpose
//   Reads one 114-bit entry at a time from the async FIFO read side (SPEC.md
//   section 8.1). For a CLASSIFY entry it runs the network of SPEC section 6
//   on the 8 features and outputs the 3 raw int32 output values ("logits")
//   and the index of the largest one. DROP and OVF_ONLY entries skip the
//   network and pass straight through, so the counters behind this block
//   see every entry in order.
//   The reference for every bit is model/golden.py. The cocotb test compares
//   the two on every row of data/vectors/mlp_vectors.csv and on corner cases.
//
// Arithmetic (exactly the golden model)
//   Layer 1, for each hidden unit j = 0..15:
//     acc = b1[j] + sum over i = 0..7 of w1[j*8+i] * x[i]          (int32)
//     h[j] = clamp((acc + 2^(SHIFT1-1)) >>> SHIFT1, 0, 127)
//   ">>>" is an arithmetic (sign-keeping) right shift, which rounds down, so
//   adding half first gives round-half-up. Clamping at 0 is the ReLU, at 127
//   the saturation. With SHIFT1 = 0 there is no rounding term.
//   Layer 2, for each class k = 0..2:
//     z[k] = b2[k] + sum over j = 0..15 of w2[k*16+j] * h[j]        (int32)
//   class = index of the largest z; on a tie the lowest index wins.
//   No confidence here: m_logit* are raw, so the decision unit can apply
//   whichever confidence rule the owner picks (SPEC section 7 or the
//   proposed right shift).
//
// Why int32 never wraps
//   golden.validate() only accepts biases at least 8*127*127 (layer 1) and
//   16*127*127 (layer 2) away from the int32 limits, so no partial or final
//   sum can leave the int32 range. The rounding add is done 33 bits wide,
//   because acc + 2^(SHIFT1-1) can pass 2^31 - 1 when acc is at the top.
//
// Ports (direction, width)
//   clk          in   1    core_clk (200 MHz in the full chip)
//   rst_n        in   1    active-low reset, synchronous to clk
//   -- entries from the async FIFO read side --
//   s_tdata      in   114  entry: [63:0] features (F0 in [7:0]),
//                          [79:64] pkt_len, [95:80] pkt_id,
//                          [111:96] ovf_cnt, [113:112] kind
//   s_tvalid     in   1
//   s_tready     out  1    high only while idle: one entry at a time
//   -- result for the decision unit (valid/ready, held while stalled) --
//   m_valid      out  1
//   m_ready      in   1
//   m_kind       out  2    entry kind (0 CLASSIFY, 1 DROP, 2 OVF_ONLY)
//   m_pkt_len    out  16   } copied from the entry
//   m_pkt_id     out  16   }
//   m_ovf_cnt    out  16   }
//   m_logit0..2  out  32   signed int32 z[0..2]; 0 unless kind = CLASSIFY
//   m_class      out  2    argmax of the logits (ties: lowest); 0 unless CLASSIFY
//
// Parameters
//   SHIFT1   layer-1 right shift (0..30); must equal "shift1" in
//            data/model/weights.json (the test checks the default).
//   W1_HEX, B1_HEX, W2_HEX, B2_HEX   ROM init files, one value per line in
//            hex, two's complement (written by model/quantize.py):
//            w1: 128 x 8 bit, address j*8+i; b1: 16 x 32 bit;
//            w2: 48 x 8 bit, address k*16+j; b2: 3 x 32 bit.
//            Paths are relative to where the simulator or synthesis runs.
//
// Clock / reset
//   Single clock (core_clk). Synchronous active-low reset: back to idle,
//   result cleared, any inference in progress abandoned (no partial result
//   ever comes out). The ROMs are constants and are not reset.
//
// How the 176 multiply-accumulates are scheduled
//   A step counter walks through 176 steps: steps 0..127 are layer 1
//   (j = step / 8, i = step % 8), steps 128..175 are layer 2
//   (k = (step - 128) / 16, j = (step - 128) % 16). Two pipeline stages:
//     fetch (F): the ROM word and bias for the step are read into
//       registers (synchronous ROM read, the way FPGA block RAM works), and
//       the matching input (x[i] or h[j]) is registered with them.
//     MAC   (M): acc <= (first step of this output ? bias : acc) + w * a.
//       On the last step of an output the finished sum is requantized into
//       h[j] (layer 1) or stored as z[k] and compared for argmax (layer 2).
//   Layer 2 can start right after layer 1 with no bubble: h[j] is first read
//   at step 128 + j, long after it was written (h[15] is written at the MAC
//   of step 127, h[15] is first needed by the fetch of step 143).
//
// Latency (cycle counts are measured in simulation by the test)
//   CLASSIFY: the entry is taken at clock edge 0. Steps 0..175 are fetched
//   at edges 1..176, their MACs happen at edges 2..177, and the result
//   register is loaded at edge 177, so m_valid is high in the cycle after
//   edge 177 and, with m_ready high, the result is taken at edge 178.
//   The core takes a new entry one edge after its result is taken, so back
//   to back it starts one inference every 179 clocks (simulated).
//   DROP / OVF_ONLY: m_valid is high in the cycle after the entry is taken
//   (taken at edge 1); back to back, one entry every 2 clocks.
//
// Example timing, in words
//   Edge 0: s_tvalid = 1 with a CLASSIFY entry, core idle: features latched.
//   Edge 1: w1[0], b1[0] and x[0] in the fetch registers.
//   Edge 2: acc = b1[0] + w1[0]*x[0].   ...   Edge 9: h[0] written.
//   Edge 177: z[2] done, m_valid = 1 with all three logits and the class.
// ============================================================================

module mlp_core #(
    parameter int unsigned SHIFT1 = 7,
    parameter string       W1_HEX = "data/model/rom_w1.hex",
    parameter string       B1_HEX = "data/model/rom_b1.hex",
    parameter string       W2_HEX = "data/model/rom_w2.hex",
    parameter string       B2_HEX = "data/model/rom_b2.hex"
) (
    input  logic                clk,
    input  logic                rst_n,

    input  logic [113:0]        s_tdata,
    input  logic                s_tvalid,
    output logic                s_tready,

    output logic                m_valid,
    input  logic                m_ready,
    output logic [1:0]          m_kind,
    output logic [15:0]         m_pkt_len,
    output logic [15:0]         m_pkt_id,
    output logic [15:0]         m_ovf_cnt,
    output logic signed [31:0]  m_logit0,
    output logic signed [31:0]  m_logit1,
    output logic signed [31:0]  m_logit2,
    output logic [1:0]          m_class
);

    // ---- Constants -------------------------------------------------------
    localparam int unsigned N_IN   = 8;
    localparam int unsigned N_HID  = 16;
    localparam int unsigned N_OUT  = 3;
    localparam int unsigned L1_STEPS = N_IN * N_HID;          // 128
    localparam int unsigned STEPS  = L1_STEPS + N_HID * N_OUT; // 176
    localparam int unsigned STEP_W = 8;                        // 0..175
    localparam logic [1:0]  KIND_CLASSIFY = 2'd0;
    localparam logic signed [32:0] HALF =                      // rounding term
        (SHIFT1 == 0) ? 33'sd0 : 33'sd1 <<< (SHIFT1 - 1);
    localparam logic signed [32:0] ACT_MAX = 33'sd127;

    if (SHIFT1 > 30) begin : g_bad_shift
        $error("mlp_core: SHIFT1 must be 0..30");
    end

    // ---- ROMs (constants, loaded from the .hex files) --------------------
    logic signed [7:0]  rom_w1 [L1_STEPS];
    logic signed [31:0] rom_b1 [N_HID];
    logic signed [7:0]  rom_w2 [N_HID * N_OUT];
    logic signed [31:0] rom_b2 [N_OUT];

    initial begin
        $readmemh(W1_HEX, rom_w1);
        $readmemh(B1_HEX, rom_b1);
        $readmemh(W2_HEX, rom_w2);
        $readmemh(B2_HEX, rom_b2);
    end

    // ---- Control state ---------------------------------------------------
    typedef enum logic [1:0] {IDLE, RUN, DONE} state_t;
    state_t state;

    logic [STEP_W-1:0] step;              // next step to fetch
    logic              fetching;          // a step is fetched this cycle
    logic [6:0]        x [N_IN];          // features of the entry (0..127)
    logic [6:0]        h [N_HID];         // hidden activations (0..127)
    logic [1:0]        kind_r;
    logic [15:0]       pkt_len_r, pkt_id_r, ovf_r;

    assign s_tready = (state == IDLE);
    assign fetching = (state == RUN) && (step < STEP_W'(STEPS));

    // Step decode. Layer 1: step = j*8 + i. Layer 2: step - 128 = k*16 + j.
    logic              f_layer2;
    logic [5:0]        l2_idx;            // step - 128, 0..47
    logic [3:0]        f_j1;              // layer-1 hidden unit
    logic [2:0]        f_i1;              // layer-1 input
    logic [1:0]        f_k2;              // layer-2 class
    logic [3:0]        f_j2;              // layer-2 hidden unit

    assign f_layer2 = step[7];            // step >= 128
    assign l2_idx   = step[5:0];          // valid when f_layer2 (step[6] is 0 there)
    assign f_j1     = step[6:3];
    assign f_i1     = step[2:0];
    assign f_k2     = l2_idx[5:4];
    assign f_j2     = l2_idx[3:0];

    // ---- Fetch stage (F) -------------------------------------------------
    // ROM reads are registered (synchronous), like FPGA block RAM.
    logic signed [7:0]  fw1, fw2;         // ROM words read this step
    logic signed [31:0] fb1, fb2;         // biases read this step
    logic        [6:0]  fa;               // activation (x[i] or h[j])
    logic               fv, fl2, ffirst, flast;
    logic        [3:0]  fout;             // output index: j (layer 1) or k (layer 2)

    always_ff @(posedge clk) begin
        fw1 <= rom_w1[step[6:0]];
        fw2 <= rom_w2[l2_idx];
        fb1 <= rom_b1[f_j1];
        fb2 <= rom_b2[f_k2];
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            fv     <= 1'b0;
            fl2    <= 1'b0;
            ffirst <= 1'b0;
            flast  <= 1'b0;
            fa     <= '0;
            fout   <= '0;
        end else begin
            fv  <= fetching;
            fl2 <= f_layer2;
            if (f_layer2) begin
                fa     <= h[f_j2];
                ffirst <= (f_j2 == 4'd0);
                flast  <= (f_j2 == 4'(N_HID - 1));
                fout   <= {2'b00, f_k2};
            end else begin
                fa     <= x[f_i1];
                ffirst <= (f_i1 == 3'd0);
                flast  <= (f_i1 == 3'(N_IN - 1));
                fout   <= f_j1;
            end
        end
    end

    // ---- MAC stage (M) ---------------------------------------------------
    logic signed [31:0] acc;
    logic signed [15:0] prod;             // int8 * (0..127) fits 16 bits
    logic signed [31:0] sum;
    logic signed [32:0] rq;               // requantize, 33 bits (see header)
    logic        [6:0]  h_new;

    // Continuous assignments (not always_comb): Icarus does not fully
    // support constant part-selects inside always_comb.
    logic signed [7:0] mw;                // weight for this MAC
    assign mw    = fl2 ? fw2 : fw1;
    assign prod  = 16'(mw) * 16'($signed({1'b0, fa}));
    assign sum   = (ffirst ? (fl2 ? fb2 : fb1) : acc) + 32'(prod);
    assign rq    = ($signed({sum[31], sum}) + HALF) >>> SHIFT1;
    assign h_new = (rq < 33'sd0)   ? 7'd0                // ReLU
                 : (rq > ACT_MAX)  ? 7'd127              // saturate
                 :                   rq[6:0];

    // Result registers and argmax
    logic signed [31:0] z [N_OUT];
    logic signed [31:0] best_val;
    logic        [1:0]  best_k;
    logic               last_mac;         // the MAC of step 175 happens now

    assign last_mac = fv && fl2 && flast && (fout[1:0] == 2'(N_OUT - 1));

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            acc      <= '0;
            best_val <= '0;
            best_k   <= '0;
            for (int k = 0; k < N_OUT; k++) z[k] <= '0;
            for (int j = 0; j < N_HID; j++) h[j] <= '0;
        end else if (fv) begin
            acc <= sum;
            if (flast && !fl2) h[fout] <= h_new;
            if (flast && fl2) begin
                z[fout[1:0]] <= sum;
                // strict ">" keeps the lower index on a tie
                if (fout[1:0] == 2'd0 || sum > best_val) begin
                    best_val <= sum;
                    best_k   <= fout[1:0];
                end
            end
        end
    end

    // ---- Control FSM and entry capture -----------------------------------
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            state     <= IDLE;
            step      <= '0;
            kind_r    <= '0;
            pkt_len_r <= '0;
            pkt_id_r  <= '0;
            ovf_r     <= '0;
            for (int i = 0; i < N_IN; i++) x[i] <= '0;
        end else begin
            case (state)
                IDLE: if (s_tvalid) begin
                    kind_r    <= s_tdata[113:112];
                    ovf_r     <= s_tdata[111:96];
                    pkt_id_r  <= s_tdata[95:80];
                    pkt_len_r <= s_tdata[79:64];
                    // Features are 0..127 (bit 7 of each byte is always 0).
                    for (int i = 0; i < N_IN; i++) x[i] <= s_tdata[8*i +: 7];
                    step  <= '0;
                    if (s_tdata[113:112] == KIND_CLASSIFY) state <= RUN;
                    else                                   state <= DONE;
                end
                RUN: begin
                    if (fetching) step <= step + STEP_W'(1);
                    if (last_mac) state <= DONE;
                end
                DONE: if (m_ready) state <= IDLE;
                default: state <= IDLE;
            endcase
        end
    end

    // ---- Outputs ---------------------------------------------------------
    // In DONE the result registers are stable; a pass-through entry shows
    // zero logits and class 0.
    logic is_cls;
    assign is_cls    = (kind_r == KIND_CLASSIFY);
    assign m_valid   = (state == DONE);
    assign m_kind    = kind_r;
    assign m_pkt_len = pkt_len_r;
    assign m_pkt_id  = pkt_id_r;
    assign m_ovf_cnt = ovf_r;
    assign m_logit0  = is_cls ? z[0] : 32'sd0;
    assign m_logit1  = is_cls ? z[1] : 32'sd0;
    assign m_logit2  = is_cls ? z[2] : 32'sd0;
    assign m_class   = is_cls ? best_k : 2'd0;

endmodule
