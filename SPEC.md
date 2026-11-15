# NeuroGate: Specification

Status: **FROZEN at M1** (pending the M1 gate). Decisions behind this version: D-001 to D-004 and D-006 in DECISIONS.md. From now on this file changes only through a DECISIONS.md entry. No open proposals remain.

## 1. What this is

NeuroGate is a small chip design written in SystemVerilog and tested in simulation only. It reads Ethernet packets, runs a tiny neural network on each one, and labels it:

| Class | Label | Meaning |
|---|---|---|
| 0 | `normal` | ordinary traffic |
| 1 | `port_scan` | one source probing many ports |
| 2 | `syn_flood` | many TCP SYNs, no handshake completion |

Every label comes with a confidence score. There is no real hardware, no FPGA board and no fabricated chip. Every number in this repo comes from simulation or from synthesis tools, and is labeled that way.

**Why it is interesting:** AI networking chips (SmartNICs, DPUs) need to inspect packets at line rate. This project shows the whole path on a small scale: parsing, feature extraction, fixed-point inference, flow control, clock crossing, and a register interface. It is verified against a software model.

## 2. Block diagram

Block order and clock domains are fixed by D-001e.

```
 net_clk domain (156.25 MHz)
 AXI-Stream in
   │
 ┌─▼────────┐  ┌──────────┐  ┌────────────┐  ┌────────────────┐
 │skid_fifo ├─►│eth_parser├─►│l3l4_parser ├─►│feature_builder │
 └──────────┘  └──────────┘  └────────────┘  └───────┬────────┘
                                                     │ 114-bit entries
                                              ┌──────▼──────┐
                                              │ async_fifo  │  only data crossing
                                              └──────┬──────┘
 core_clk domain (200 MHz)                           │
                                              ┌──────▼──────┐
                                              │  mlp_core   │
                                              └──────┬──────┘
                                              ┌──────▼────────┐
                              result stream ◄─┤ decision_unit │
                                              └──────┬────────┘
        ┌───────────────┐   ┌────────────────┐       │
 AXI-L ◄┤ axil_regs     │◄──┤ stats_counters │◄──────┘
        └───────────────┘   └────────────────┘
```

- `skid_fifo` sits at the input so the parsers see a clean, registered stream and never handle mid-packet stalls (D-001e).
- `feature_builder` runs in `net_clk`. It sends one entry per finished frame into `async_fifo`: features plus metadata, never raw headers (section 8.1).
- If `async_fifo` is full when an entry is ready, that frame is dropped and counted in `PKT_OVERFLOW`. The input is never stalled because the async FIFO is full (D-001e).
- `neurogate_top` wires the blocks together. It has no logic of its own beyond reset synchronizers and three small gates that split the `decision_unit` output between the result stream and the counters (D-032, owner-approved in D-041).

## 3. Interfaces

### 3.1 Packet input (AXI-Stream, `net_clk`)
- 64-bit `tdata`, 8-bit `tkeep`, `tlast`, `tvalid`/`tready`, 1-bit `tuser` (D-001a). 64 bits at 156.25 MHz is 10 Gb/s, the usual 10G MAC interface width.
- Byte order: the first byte of the frame is in `tdata[7:0]` of the first beat.
- `tkeep` is all ones on every beat except the last. On the last beat it is contiguous from bit 0. This is assumed from the upstream MAC and not checked.
- `tuser` is sampled on the `tlast` beat. When set, the upstream MAC saw a bad FCS and the frame is dropped.
- Input is a complete Ethernet frame without preamble and without FCS.
- Packets arrive back to back. Idle gaps are allowed.
- Header fields can straddle two beats (for example the IPv4 header starts at byte 14, which is byte 6 of beat 1). The parsers handle this, and the M2 tests include fields split across beats.

### 3.2 Control (AXI-Lite, `core_clk`)
32-bit registers at word-aligned byte offsets. The full map, with reset values and bit fields, is in [`docs/regmap.md`](docs/regmap.md). That file is the reference; this table is a summary.

| Offset | Name | Access | Notes |
|---|---|---|---|
| 0x00 | `CTRL` | RW | bit0 = soft reset of counters (self-clearing) |
| 0x04 | `CONF_THRESH` | RW | confidence threshold (0..127) |
| 0x08 | `PKT_NORMAL` | RO | packet count per class |
| 0x0C | `PKT_SCAN` | RO | |
| 0x10 | `PKT_SYN` | RO | |
| 0x14..0x1C | `BYTES_*` | RO | byte count per class |
| 0x20 | `PKT_DROPPED` | RO | malformed or unsupported frames |
| 0x24 | `PKT_LOWCONF` | RO | packets below threshold |
| 0x28 | `LAST_RESULT` | RO | class and confidence of the latest classified packet |
| 0x2C | `PKT_OVERFLOW` | RO | frames lost because `async_fifo` was full |

### 3.3 Result output (AXI-Stream, `core_clk`)
One beat per classified packet (`tvalid`/`tready`, no `tlast`). `tdata` is 48 bits:

| Bits | Field |
|---|---|
| [1:0] | `class` |
| [2] | `low_conf` |
| [7:3] | zero |
| [15:8] | `confidence` |
| [31:16] | `pkt_len` (frame bytes as received) |
| [47:32] | `pkt_id` |

This stream is what the testbench compares against the Python model. `pkt_id` (D-004) lets the testbench line up results with the model when overflow drops leave gaps. Dropped and overflowed frames produce no result beat; they show up only in the counters.

## 4. Parsing rules

Keep this narrow. Anything outside it is counted in `PKT_DROPPED` and bypasses the neural net. It never hangs the pipeline.

- **Ethernet:** dst MAC, src MAC, optional single 802.1Q VLAN tag (EtherType 0x8100). QinQ is out of scope.
- **L3:** IPv4 only (EtherType 0x0800). IHL > 5 (IP options) is parsed and skipped. IPv6, ARP and others are counted as unsupported.
- **L4:** TCP and UDP. Other protocols are counted as unsupported.
- **Fragments:** a non-first fragment has no L4 header. Treat it as unsupported.
- **Bad input:** frame shorter than needed for its headers, IHL < 5, or `tuser` set. Count as dropped and keep going.
- **[CLARIFICATION, owner-confirmed at Gate M2]** "Shorter than needed for its headers" means shorter than the bytes the chip actually reads, not the full header. Ethernet and IPv4 (including options) must be complete. For the transport header the chip needs only the first 14 bytes of TCP (through the flags byte) and the first 4 bytes of UDP (the ports). A TCP frame cut after 14 or more header bytes, or a UDP frame cut after 4 or more, is kept and parsed. A shorter one is dropped. This is the rule already used by the Python model and the parser (DECISIONS D-011), not a change of behaviour. The owner confirms or rejects it at Gate M2.
- **Reset mid-packet:** the pipeline must return to idle and not emit a partial result.

## 5. Feature builder

One packet alone cannot tell a port scan from a SYN flood. Both are mostly SYN packets. So the feature builder keeps a small amount of state over a sliding window. It runs in `net_clk` (D-001e).

**Window (D-001b):** the last **64 valid IPv4 TCP/UDP packets**, including the current one. Dropped frames do not enter the window. Packets later lost to `async_fifo` overflow *do* enter it, so features depend only on the packet stream and never on backpressure timing (D-004). After reset the window is empty and fills up over the first 64 packets.

**State:**
- a 64-entry ring of `(is_syn, bucket[5:0])`, where `is_syn` = SYN set and ACK clear (same test as F0), and `bucket = (dst_port ^ (dst_port >> 6)) & 0x3F`;
- 64 per-bucket 7-bit counters. When a packet enters the window its bucket counter goes up by 1. When it leaves (pushed out by the 65th packet) its bucket counter goes down by 1. A bucket is "occupied" while its counter is above 0. A bitmap is not used, because it cannot clear a bucket correctly when another packet in the window shares it (D-001b);
- a 7-bit SYN count (number of ring entries with `is_syn` = 1).

Order of operations per packet: the oldest entry leaves (if the window is full), then the current packet enters, then F6 and F7 are computed.

**Features:** 8 int8 values, each in 0..127:

| # | Feature | Scaling |
|---|---|---|
| F0 | SYN set and ACK clear (TCP only, 0 for UDP) | 127 or 0 |
| F1 | ACK set (TCP only) | 127 or 0 |
| F2 | RST or FIN set (TCP only) | 127 or 0 |
| F3 | IPv4 total length field | `min(len, 1500) * 127 // 1500` (integer floor) |
| F4 | TTL | `ttl >> 1` |
| F5 | dst port is well-known (< 1024) | 127 or 0 |
| F6 | SYN count in window | `min(syn_count * 2, 127)` |
| F7 | occupied dst-port buckets in window | `min(occupied_buckets * 2, 127)` |

Expected separation: flood = high F6, low F7. Scan = high F6, high F7. Normal = low F6.
The exact integer formulas live in the Python feature model (`model/features.py`); the Python golden model (`model/golden.py`) is the source of truth for the network arithmetic. The M3 test includes two packets that share a bucket where one leaves the window and the other stays.

## 6. Neural network

- Topology: **8 → 16 → 3**, ReLU after layer 1, none after layer 2.
- Weights and biases: int8 weights in ROM. Biases are **int32**, matching the int32 accumulator (D-006).
- Accumulate in int32. Requantize layer 1 output with an arithmetic right shift (shift amount fixed per layer), then ReLU and saturate to 0..127. Rounding rule is **round-half-up**, written down once in the golden model.
- 1 multiply-accumulate per cycle. Latency = 8×16 + 16×3 = **176 MAC cycles** per packet plus a few overhead cycles. This is an architectural estimate from the design, not a measured latency; M3 simulation will measure it.
- Training happens in Python (float, then post-training quantization to int8). The RTL does inference only.
- `.mem` / `.hex` files for ROM init are generated by a script from the same weights file the golden model reads.
- `mlp_core` runs inference only for CLASSIFY entries. DROP and OVF_ONLY entries pass straight through to `decision_unit` so the counters stay in order.

## 7. Decision unit

- `class` = argmax of the 3 output logits. Tie-break: lowest class index wins.
- `confidence` = `min((top_logit − second_logit) >> 8, 127)`, where `top_logit` is the logit of the chosen class and `second_logit` is the larger of the other two (so the difference is never negative). The shift of 8 bits is needed because the raw difference of the int8 network's logits is usually in the thousands: without it almost every packet would sit at 127 and the number would carry no information. One confidence step is 256 raw logit units. The shift is a fixed constant (`CONF_SHIFT`), equal to `conf_shift` in the model's weights file. This is a margin, not a probability. Do not call it a probability in any doc. (Owner-approved, D-022.)
- `low_conf` = `confidence < CONF_THRESH`. The class is still reported. Only the flag changes.
- No softmax in hardware (D-002). To tell users what a margin means, the Python side measures it: bin the margin values and report the share of packets that were actually correct in each bin (a "margin vs. measured accuracy" table and chart). The default `CONF_THRESH` is chosen from this table computed on a **validation split carved from the training data** (D-008, which refines D-002), never on the test set. The same table is then reported once on the test set to show how well the choice holds up. Label both as measured on synthetic data.
- For every entry it finishes, `decision_unit` tells `stats_counters` the entry kind, class, `low_conf`, `pkt_len` and `ovf_cnt`.

## 8. Clocks and reset

- `net_clk` 156.25 MHz, `core_clk` 200 MHz. Asynchronous to each other in the testbench (non-integer ratio, random phase).
- **net_clk blocks:** `skid_fifo`, `eth_parser`, `l3l4_parser`, `feature_builder`, write side of `async_fifo`.
- **core_clk blocks:** read side of `async_fifo`, `mlp_core`, `decision_unit`, `stats_counters`, `axil_regs`.
- The async FIFO is the **only** crossing for packet data (Gray-coded pointers, 2-flop synchronizers). The AXI-Lite block runs fully in `core_clk`. All counters, including `PKT_OVERFLOW`, live in `core_clk` (D-004).
- Reset: one external active-low `rst_n`. Each domain has its own reset synchronizer: asynchronous assert, synchronous release. Both are documented in `neurogate_top` and listed in the CDC review.

### 8.1 Async FIFO entry (D-001e, D-004)

Depth: parameter `AFIFO_DEPTH`, default 16 entries. Width: **114 bits**.

| Bits | Field | Meaning |
|---|---|---|
| [63:0] | `features` | F0 in [7:0], F1 in [15:8], ... F7 in [63:56]. Zero unless kind = CLASSIFY |
| [79:64] | `pkt_len` | frame length in bytes as received (sum of `tkeep` bits), saturates at 65535 |
| [95:80] | `pkt_id` | 16-bit frame number, counts every finished frame, wraps |
| [111:96] | `ovf_cnt` | number of frames lost to overflow since the previous entry |
| [113:112] | `kind` | 0 = CLASSIFY, 1 = DROP (malformed or unsupported), 2 = OVF_ONLY, 3 = reserved |

How overflow is counted: when an entry is ready but the FIFO is full, `feature_builder` throws the entry away and adds 1 to a 16-bit saturating "pending overflow" counter in `net_clk`. The next entry that is written carries the pending count in `ovf_cnt`, and the pending counter clears. If the pending count is non-zero, the FIFO has space, and no packet entry is waiting, `feature_builder` writes an OVF_ONLY entry so the count reaches `core_clk` without waiting for the next packet. Known limit: more than 65535 overflow drops between two successful writes saturate the count. This is documented, not hidden.

Every frame lands in exactly one counter: a class counter (`PKT_NORMAL`/`PKT_SCAN`/`PKT_SYN`), `PKT_DROPPED`, or `PKT_OVERFLOW`. Counters change when `decision_unit` finishes an entry, so the sum of all five equals the frames received only after the pipeline has drained (async FIFO empty, `mlp_core` idle, no pending overflow count), with no `ovf_cnt` saturation and no counter wrap past one full turn. Tests check the sum at that point, not mid-stream.

Soft reset (`CTRL.SOFT_RST`) clears the counters and `LAST_RESULT` only. It does not flush the async FIFO, `mlp_core`, or the pending overflow count. Entries queued at that moment are counted after the reset, and if `decision_unit` finishes an entry in the same cycle as the reset, the reset wins and that entry is not counted. A frame belongs to the counting period in which it is finished, not the one in which it arrived. The sum equality is guaranteed only if no soft reset happens while frames are in flight. Full wording: `docs/regmap.md`.

## 9. Data and model

- **Synthetic traffic** from Scapy: normal (mixed TCP/UDP/ICMP/ARP, some VLAN), port scans (sequential and random ports, varied pacing), SYN floods (one or many sources, one target port). Add jitter, noise and overlap so the task is not trivially separable.
- Labels are per packet, and packets are generated as ordered streams (the feature window depends on order).
- **Train/test split by generator seed and scenario**, never by shuffling packets from the same stream. The test set is never used for any tuning, including the choice of `CONF_THRESH`. A validation split is carved from the training scenarios (also by seed and scenario), and all tuning decisions use it (D-008).
- Report: accuracy, per-class precision/recall, confusion matrix on the unseen test set, and the accuracy drop from float to int8.
- Be honest in docs: synthetic data is easier than real traffic. High accuracy here does not mean it would work in the wild.
- Parsers are checked against Scapy on generated traffic only. Real captures are not used (D-003).
- Next step, not in this scope: CIC-IDS2017.

## 10. Verification plan

Simulators (D-001c): Verilator for lint only. Icarus Verilog + cocotb for RTL and gate-level simulation.

| Check | Tool | Pass condition |
|---|---|---|
| Compile check | Icarus + Verilator (`make compile-check`) | every RTL file compiles in both tools |
| Golden model match | Python model + cocotb | RTL class, confidence and every feature equal the model, bit for bit, over all test vectors |
| Parser tests | cocotb | good frames, VLAN, IP options, UDP, truncated frames, bad `tuser`, non-IPv4, fragments, header fields split across two beats |
| Feature window | cocotb vs `model/features.py` | bit-exact, including two packets sharing a bucket where one leaves the window |
| Backpressure | cocotb | random `tready` stalls on output. Zero packets lost or duplicated, order preserved, as long as `async_fifo` does not overflow |
| Overflow | cocotb | overfill `async_fifo`. `PKT_OVERFLOW` equals the number of missing `pkt_id`s, and every frame lands in exactly one counter |
| Reset | cocotb | reset mid-frame and mid-inference. Clean restart, no partial output |
| FIFO | cocotb | async FIFO with different clock ratios, full/empty edge cases, random traffic |
| AXI-Lite | cocotb | read/write, bad address (SLVERR), read during counting |
| Confidence meaning | Python on unseen test set | margin-bin table + chart written to `docs/results/`. `CONF_THRESH` is chosen beforehand on a validation split from the training data (section 7, D-008); the test set only reports the result |
| Lint | Verilator `--lint-only -Wall` | zero warnings, or each waiver is listed with a reason |
| CDC | structural review + random-phase simulation + lint | Honest label: this is not a commercial CDC sign-off. List every crossing and its method |
| Synthesis | Yosys | clean synth, no latches, no unintended memories |
| Gate-level sim | Yosys netlist + cell models, Icarus | same vectors, same outputs as RTL |
| Timing | OpenSTA on the Yosys netlist | Nangate45 Open Cell Library, typical corner (D-001d). Targets: `core_clk` 200 MHz, `net_clk` 156.25 MHz. If timing fails, add pipeline stages and record before/after. *(Result, owner-approved in D-041: setup was fixed by timing-driven mapping and buffering instead of a pipeline stage; the small reset-removal violation is reported as not done, because there is no place-and-route step; see D-038.)* Label: synthesized (estimate) |
| FPGA estimate | Yosys `synth_xilinx` (Artix-7) | LUT/FF/BRAM/DSP counts. Label: estimate, not a Vivado result |
| Throughput | cycle counts from simulation | packets/s at the chosen clock. Label: simulated estimate |

Fixed-seed regression runs from one command (`make test`). Every result file records the git commit, seed and tool versions.

## 11. Honesty rules for results

- Every table, chart and sentence with a number says where it came from: *simulated*, *synthesized (estimate)*, or *measured in sim*.
- Never say "tested on hardware" or "line rate" without the qualifier "in simulation".
- A known limitation goes in the docs. It is not hidden.

## 12. Out of scope

Real FPGA or hardware, GPU, full OpenLane flow, MACsec, formal proofs (stretch only, after M5), IPv6, QinQ, flow tables, online learning, real packet captures (D-003).

## 13. Resolved questions (M1)

All answered by the owner in D-001.

1. Stream width: **64-bit AXI-Stream**, no 8-bit option (D-001a).
2. Window and features: **64 packets**, feature table confirmed, F7 uses **per-bucket counters** instead of a bitmap (D-001b).
3. Simulators: **Verilator** for lint, **Icarus Verilog + cocotb** for RTL and gate-level sim. RTL must compile in both (D-001c).
4. Liberty file: **Nangate45**, typical corner, 200 MHz target on `core_clk` (D-001d).
5. `skid_fifo` placement: **at the input, before `eth_parser`**. `feature_builder` moves to `net_clk`, and the async FIFO carries features plus metadata (D-001e, entry format in section 8.1 per D-004).
