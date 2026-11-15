# NeuroGate AXI-Lite register map

Status: frozen at M1 (SPEC.md section 3.2, D-001e, D-004). `rtl/axil_regs.sv` must match this file, and its M4 test checks it.

## General rules

- Bus: AXI4-Lite, 32-bit data, clocked by `core_clk`, reset by the `core_clk`-domain `rst_n`.
- Addresses are byte offsets. Every register is 32 bits wide and word-aligned. Address bits [1:0] are ignored. The block decodes address bits [5:2] (16 word slots, offsets 0x00 to 0x3C).
- **Unmapped offsets** (0x30 to 0x3C): reads return 0 with response SLVERR (`2'b10`). Writes are ignored with response SLVERR.
- **Read-only registers:** writes are ignored and return OKAY.
- `WSTRB` is honored on RW registers (byte lanes not strobed keep their old value).
- **Counters** are 32-bit unsigned and wrap from 0xFFFFFFFF to 0. All counters live in `core_clk`, so a 32-bit read is always consistent (no high/low halves to tear).
- **Counter update point:** a counter changes when `decision_unit` finishes an entry from the async FIFO (SPEC.md section 7). A frame lost to overflow is counted when the next entry carries its `ovf_cnt` across (SPEC.md section 8.1).
- **Soft reset:** writing 1 to `CTRL.SOFT_RST` clears every counter and `LAST_RESULT` in the same cycle. It does not change `CONF_THRESH`. It does not reset the datapath: it does not flush the async FIFO, the entry `mlp_core` is working on, or the "pending overflow" count that `feature_builder` keeps in `net_clk`.
- **Soft reset precedence:** if `decision_unit` finishes an entry in the same `core_clk` cycle as the soft reset, the reset wins. That entry is not counted anywhere, and its `LAST_RESULT` update is discarded.
- **Accounting boundary:** a frame belongs to the counting period in which its entry is finished by `decision_unit`, not the period in which the frame arrived. Entries still queued at the moment of a soft reset (in the FIFO or in `mlp_core`) are counted after it. So are overflow drops still pending in `net_clk`, because they reach `core_clk` inside a later entry's `ovf_cnt`. Software that wants a clean baseline should stop traffic, wait for the pipeline to drain, then write `SOFT_RST`.
- **Every frame is counted once:** `PKT_NORMAL + PKT_SCAN + PKT_SYN + PKT_DROPPED + PKT_OVERFLOW` equals the number of frames received since the last reset (modulo 2^32). This holds only when all of these are true: (1) the pipeline has drained, meaning the async FIFO is empty, `mlp_core` is idle and no overflow count is still pending; (2) no `ovf_cnt` saturated (SPEC.md section 8.1); (3) no counter wrapped more than once; (4) no soft reset happened while frames were in flight. While frames are in flight the sum is smaller than the frames received. If a soft reset happens with frames in flight, the sum can differ from the frames received since the reset by the number of those frames, plus one if an entry finished in the reset cycle. The equality is exact in simulation tests that reset before traffic starts and wait for drain before reading.

## Summary

| Offset | Name | Access | Reset | Description |
|---|---|---|---|---|
| 0x00 | `CTRL` | RW | 0x0000_0000 | control |
| 0x04 | `CONF_THRESH` | RW | `CONF_THRESH_RESET` | low-confidence threshold |
| 0x08 | `PKT_NORMAL` | RO | 0 | packets classified `normal` |
| 0x0C | `PKT_SCAN` | RO | 0 | packets classified `port_scan` |
| 0x10 | `PKT_SYN` | RO | 0 | packets classified `syn_flood` |
| 0x14 | `BYTES_NORMAL` | RO | 0 | bytes of packets classified `normal` |
| 0x18 | `BYTES_SCAN` | RO | 0 | bytes of packets classified `port_scan` |
| 0x1C | `BYTES_SYN` | RO | 0 | bytes of packets classified `syn_flood` |
| 0x20 | `PKT_DROPPED` | RO | 0 | malformed or unsupported frames |
| 0x24 | `PKT_LOWCONF` | RO | 0 | classified packets with `low_conf` = 1 |
| 0x28 | `LAST_RESULT` | RO | 0 | latest classified packet |
| 0x2C | `PKT_OVERFLOW` | RO | 0 | frames lost because the async FIFO was full |

## Register details

### 0x00 `CTRL` (RW)
| Bits | Name | Access | Reset | Meaning |
|---|---|---|---|---|
| [0] | `SOFT_RST` | W1 / reads 0 | 0 | write 1 to clear all counters and `LAST_RESULT`. Self-clearing: the bit is a one-cycle pulse and always reads 0 |
| [31:1] | reserved | RO | 0 | write ignored, read 0 |

### 0x04 `CONF_THRESH` (RW)
| Bits | Name | Access | Reset | Meaning |
|---|---|---|---|---|
| [6:0] | `THRESH` | RW | `CONF_THRESH_RESET` | a packet is flagged `low_conf` when `confidence < THRESH`. 0 means no packet is ever flagged |
| [31:7] | reserved | RO | 0 | write ignored, read 0 |

`CONF_THRESH_RESET` is an `axil_regs` parameter. Its value was picked in M3 from the margin vs. measured accuracy table computed on a validation split carved from the training data (D-002, D-008), never on the test set: it is **13** (recorded in DECISIONS.md). `confidence` = `min((top logit − second logit) >> 8, 127)`: the gap between the best and second-best network output, shifted right by 8 bits and capped at 127 (SPEC section 7, D-022). It is a logit margin, not a probability.

### 0x08 / 0x0C / 0x10 `PKT_NORMAL`, `PKT_SCAN`, `PKT_SYN` (RO)
| Bits | Meaning |
|---|---|
| [31:0] | number of packets with `class` = 0 / 1 / 2. Packets flagged `low_conf` are still counted here under their class |

### 0x14 / 0x18 / 0x1C `BYTES_NORMAL`, `BYTES_SCAN`, `BYTES_SYN` (RO)
| Bits | Meaning |
|---|---|
| [31:0] | sum of `pkt_len` (frame bytes as received, without FCS) over packets with `class` = 0 / 1 / 2 |

### 0x20 `PKT_DROPPED` (RO)
| Bits | Meaning |
|---|---|
| [31:0] | frames that bypassed the neural net: too short for their headers, IHL < 5, `tuser` set, non-IPv4, non-TCP/UDP, non-first fragment (SPEC.md section 4) |

### 0x24 `PKT_LOWCONF` (RO)
| Bits | Meaning |
|---|---|
| [31:0] | classified packets with `confidence < CONF_THRESH`. This is a subset of the class counters, not a separate bucket |

### 0x28 `LAST_RESULT` (RO)
| Bits | Name | Meaning |
|---|---|---|
| [1:0] | `CLASS` | class of the latest classified packet |
| [7:2] | reserved | 0 |
| [15:8] | `CONF` | its confidence (0..127, shifted logit margin, see `CONF_THRESH`) |
| [31:16] | reserved | 0 |

Updated once per classified packet. Dropped and overflowed frames do not change it. Reads 0 after reset or soft reset, so 0 cannot be told apart from "normal, margin 0"; check the class counters to see whether any packet has been classified.

### 0x2C `PKT_OVERFLOW` (RO)
| Bits | Meaning |
|---|---|
| [31:0] | frames dropped because the async FIFO was full when their entry was ready (D-001e). The input is never stalled for this reason. Counted in `core_clk` from the `ovf_cnt` field of FIFO entries (D-004) |
