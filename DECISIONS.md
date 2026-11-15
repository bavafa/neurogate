# DECISIONS.md

Append-only log. Newest at the bottom. Owner instructions override the project manager and are marked `OWNER`.

Format:
```
## D-NNN: short title
- Date: YYYY-MM-DD
- Source: OWNER | PROJECT-MANAGER | BUILDER | REVIEW-FINDING
- Decision: what was decided
- Why: reason
- Effect: files / tasks changed
```

---

## D-000: Project setup
- Date: 2026-11-13
- Source: OWNER
- Decision: NeuroGate is a simulation-only RTL project with 10 modules and 5 milestones (see SPEC.md, TASKS.md). Only one automated build session builds at a time. An independent reviews read-only at each milestone. The owner's instructions override the project manager.
- Why: one-weekend scope, keeps responsibilities clear.
- Effect: the contributor rules, TASKS.md written.

## D-001: Open design questions resolved (SPEC.md section 13)
- Date: 2026-11-13
- Source: OWNER
- Decision: All 5 questions answered, see D-001a to D-001e.
- Why: M1 needs a frozen spec before any RTL.
- Effect: SPEC.md sections 2, 3.1, 5, 8, 10 and 13 updated in M1. All [PROPOSED] tags in those sections are removed.

## D-001a: AXI-Stream input width is 64 bits
- Date: 2026-11-13
- Source: OWNER
- Decision: Packet input is 64-bit AXI-Stream with tkeep, tlast, tvalid/tready and 1-bit tuser. No 8-bit option.
- Why: 64 bits at 156.25 MHz is 10 Gb/s, the standard 10G MAC interface. The extra parser work is builder time, not owner time.
- Effect: SPEC.md 3.1. eth_parser and l3l4_parser handle header fields split across beats. M2 tests include fields that straddle two beats.

## D-001b: Window 64 packets, feature table confirmed, F7 uses per-bucket counters
- Date: 2026-11-13
- Source: OWNER
- Decision: Window is the last 64 IPv4 TCP/UDP packets. Features F0 to F7 as in SPEC.md section 5. F7 uses 64 per-bucket 7-bit counters (add 1 on entry, subtract 1 on exit, a bucket counts while above 0) instead of a 64-bit bitmap.
- Why: A bitmap can't clear a bucket correctly when a packet leaves the window, because another packet may share the bucket. Counters stay exact and are easy to match in Python.
- Effect: SPEC.md section 5, model/features.py, rtl/feature_builder.sv. M3 test adds a case where two packets share a bucket and one leaves the window.

## D-001c: Simulators: Verilator lint, Icarus + cocotb for RTL and gate-level sim
- Date: 2026-11-13
- Source: OWNER
- Decision: Verilator for lint only. Icarus Verilog with cocotb for RTL and gate-level simulation. All RTL must compile in both, checked by `make compile-check` from M1.
- Why: Icarus reads Yosys netlists easily and pairs well with cocotb. Verilator is the strictest free linter. Icarus only supports part of SystemVerilog, so checking both early stops late surprises.
- Effect: SPEC.md section 10. the contributor rules: use the SystemVerilog subset both tools accept (no interfaces or classes). Makefile gets a compile-check target in M1.

## D-001d: OpenSTA uses the Nangate45 liberty file, typical corner
- Date: 2026-11-13
- Source: OWNER
- Decision: OpenSTA runs on the Yosys netlist mapped to the Nangate45 Open Cell Library at the typical corner, with a 200 MHz target on core_clk.
- Why: It's free, a single file, the most common pairing with Yosys and OpenSTA, and 200 MHz is realistic. sky130 would likely need extra pipeline stages and a large PDK install.
- Effect: SPEC.md section 10. Closes the M1 task "decide and record the liberty file". The file path goes in STATUS.md. Timing results are labelled "synthesized (estimate)".

## D-001e: skid_fifo sits at the input, before eth_parser
- Date: 2026-11-13
- Source: OWNER
- Decision: Block order is input, skid_fifo, eth_parser, l3l4_parser, feature_builder (all on net_clk), then async_fifo, then mlp_core and decision_unit (core_clk).
- Why: The parsers see a clean, registered stream and never handle mid-packet stalls. Backpressure is absorbed in one place, and the long tready path is broken for timing.
- Effect: SPEC.md section 2 block diagram and section 8 clock domains are updated in M1.
- Consequence 1: feature_builder moves to net_clk, before async_fifo. The async FIFO carries the 8 features plus metadata per packet (a packet ID, plus whatever decision_unit and stats_counters need), not raw headers. The entry width is fixed in SPEC.md during M1.
- Consequence 2: When async_fifo is full at the moment a packet's features are ready, that packet is dropped and the PKT_OVERFLOW counter goes up by 1. The input is never stalled because of a full async_fifo. PKT_OVERFLOW is added to docs/regmap.md and stats_counters. M4 adds a test that overfills the FIFO and checks the drop count matches the counter.

## D-002: Confidence stays a margin on the chip; meaning is measured in Python
- Date: 2026-11-13
- Source: OWNER
- Decision: No softmax or probability output in RTL. `confidence` stays the logit margin (top minus second, clamped 0..127). Python measures what each margin range means (share of packets actually correct per margin bin, on the unseen test set) and the results page shows it. Default `CONF_THRESH` is chosen from that table. A LUT softmax module is a stretch item after M5.
- Why: a probability needs softmax hardware plus calibration, adds risk to the bit-exact path, and shows no extra skill. The measured table answers "what does this number mean" honestly.
- Effect: SPEC.md sections 7 and 10, TASKS.md M3 and M5 and Stretch.

## D-003: Optional Wireshark sample captures cut from M2
- Date: 2026-11-13
- Source: OWNER
- Decision: The optional M2 task "parse 1 to 2 real Wireshark sample captures" is removed. Parsers are checked against Scapy-parsed fields on generated traffic only.
- Why: Owner time budget. The task was optional and does not feed any gate.
- Effect: TASKS.md M2 line removed. SPEC.md section 9 "Optional: real Wireshark captures" line removed. Parser coverage of real-world frame quirks is listed as a limitation instead.

## D-004: Async FIFO entry format, drop accounting and result stream fields
- Date: 2026-11-13
- Source: BUILDER (fills in details left open by D-001e; owner may override)
- Decision:
  - Every finished frame gets a 16-bit `pkt_id` (wraps) in net_clk, and goes to exactly one of: a class counter, `PKT_DROPPED`, or `PKT_OVERFLOW`.
  - Async FIFO entry is 114 bits: `kind[1:0]`, `ovf_cnt[15:0]`, `pkt_id[15:0]`, `pkt_len[15:0]`, `features[63:0]` (layout in SPEC.md section 8.1). `kind` is CLASSIFY, DROP (malformed or unsupported, no inference) or OVF_ONLY (carries only an overflow count).
  - Overflow drops are counted in net_clk in a 16-bit saturating "pending" counter. The next entry written (a real packet, or an OVF_ONLY entry when there is space and nothing else to send) carries that count across, then the pending counter clears. So `PKT_OVERFLOW` lives in core_clk, is cleared by the same soft reset as the other counters, and no extra clock crossing is needed.
  - The result stream carries `pkt_id` in addition to the SPEC fields, so the testbench can match results to the model when overflow drops leave gaps. Only CLASSIFY entries produce a result beat.
  - The feature window updates for every valid IPv4 TCP/UDP packet, whether or not that packet is later lost to overflow. Features therefore depend only on the packet stream, never on backpressure timing, and the golden model needs no timing input.
  - Counters are 32 bits and wrap. Unmapped AXI-Lite addresses return SLVERR. Writes to read-only registers are ignored with OKAY.
- Why: Keeps the async FIFO the only crossing for packet data (SPEC section 8), keeps every counter in one domain under one soft reset, and keeps the bit-exact comparison independent of timing.
- Effect: SPEC.md sections 2, 3.3, 5, 8; docs/regmap.md.

## D-005: Liberty file is fetched, not committed; tools installed in user space
- Date: 2026-11-13
- Source: BUILDER (M1 session 2, headless; owner may override)
- Decision: `lib/NangateOpenCellLibrary_typical.lib` (6.7 MB, copyright Nangate Inc.) is gitignored. `lib/fetch_nangate45.sh` downloads it from OpenROAD-flow-scripts and checks the SHA-256 recorded in `lib/README.md`. GNU make, a C++ toolchain, OpenSTA and its build dependencies were installed in the tool install folder (Debian packages unpacked with `dpkg -x`, OpenSTA built from source, CMake and SWIG from PyPI in `.venv`).
- Why: The file's header is a third-party license notice and the repo goes public later. A pinned checksum keeps results reproducible. `apt-get` is unavailable on the build machine.
- Effect: D-001d path is `lib/NangateOpenCellLibrary_typical.lib` after running the fetch script. If the owner wants the file committed, remove the `.gitignore` line after checking the license.

## D-006: Biases are int32
- Date: 2026-11-13
- Source: OWNER (approved Nov 13 2026, 4:59 PM ET)
- Instruction: Bias width is int32, matching the int32 accumulator.
- Decision: Layer biases are stored as int32 and added directly to the int32 accumulator. Weights stay int8.
- Effect: SPEC.md section 6 `[PROPOSED]` tag replaced by the decided value; no `[PROPOSED]` tags remain in SPEC.md. TASKS.md "Freeze SPEC.md" ticked. Bias ROM and the weights file use 32-bit entries for biases.

## D-007: Reader-facing docs in plain English
- Date: 2026-11-13
- Source: OWNER
- Instruction: docs/stories.md is unreadable (decision IDs, task tags, file names, jargon). Rewrite it in plain English for a reader with basic engineering knowledge who has never seen the repo, check docs/PRD.md against the same rules, and add a "Writing docs" rule to the contributor rules.
- Decision: Stories become one line each ("As a ..., I want ..., so that ...") with 1-3 "Done when" checks a non-expert can verify. Each epic opens with an everyday summary. No decision IDs, task tags, unexplained file names or unexplained acronyms. Meaning and scope unchanged.
- Effect: docs/stories.md and docs/PRD.md rewritten. the contributor rules gain a "Writing docs" section. The mapping from stories to TASKS.md lines is no longer shown in the stories file; each epic still matches one milestone in the same order.

## D-008: Default CONF_THRESH is chosen on a validation split, not the test set
- Date: 2026-11-13
- Source: PM recommendation, **CONFIRMED by owner** (Nov 13 2026, 8:08 PM ET, at the M1 gate; raised by the independent M1 review, must-fix 2; builder applied it in SPEC.md so the spec is not self-contradictory)
- Refines: D-002. D-002's text is left as written. It says the margin table is measured "on the unseen test set" and that the default `CONF_THRESH` is chosen from it. That conflicts with SPEC.md section 9 ("the test set is never used for any tuning").
- Decision: A validation split is carved from the training scenarios (by generator seed and scenario, like the train/test split). The margin-vs-measured-accuracy table used to pick the default `CONF_THRESH` is computed on the validation split. The test set is used only for final reporting: the same table is reported once on it, with no further tuning.
- Why: choosing a threshold from test-set results makes the reported test numbers optimistic. The test set must stay untouched.
- Effect: SPEC.md sections 7 and 9. TASKS.md M3 line "Margin vs. measured accuracy ... on the unseen test set ... (see D-002)" still says test set. TASKS.md belongs to the project manager, so the proposed rewording is in STATUS.md, not applied. Owner confirmed, so SPEC.md sections 7 and 9 stand. TASKS.md M3 line reworded in M2 session 1 (D-010).

## D-009: Stories and PRD in product-manager format
- Date: 2026-11-13
- Source: OWNER (instruction Nov 13 2026, 7:06 PM ET)
- Instruction: Rewrite docs/stories.md and docs/PRD.md in real product-manager format, keeping the plain-English rules and not longer than now. Epics and stories each get Problem (1-2 lines), What we'll build (1-2 lines) and Acceptance criteria (2-4 checkable bullets); stories may keep the "As a ..." line. PRD sections: Problem statement; Proposed solution; Goals and non-goals; Success metrics (labelled simulated/estimate); Main risks (3-5 one-liners with mitigation); Milestones (one line each); about 1 page. Add the format to the contributor rules "Writing docs".
- Decision: Both files rewritten. Same five epics (one per milestone), same 13 stories, same order and scope. To stay within the word limit, the "As a ..." lines were dropped and the user is named in each story heading instead. The PRD "Who it is for" content moved into the problem statement. The PRD had six risks; the "simulator rejects some features" risk was merged with the owner-time risk to fit the 3-5 limit, mitigation unchanged. Success metrics are stated as targets, since no results exist yet.
- Effect: docs/stories.md (1031 → 1028 words, `wc -w`), docs/PRD.md (767 → 722 words). the contributor rules "Writing docs" gains the format rules. Supersedes the "Done when" story format from D-007; D-007's plain-English rules still apply.

## D-010: Gate M1 approved; D-008 confirmed; M3 task reworded; PRD nit
- Date: 2026-11-13
- Source: OWNER (instruction Nov 13 2026, 8:08 PM ET)
- Instruction: Gate M1 approved. D-008 confirmed (threshold chosen on a validation split from training data, never the test set). Apply the TASKS.md M3 rewording proposed in STATUS.md. Remove the one "SPEC.md" file-name mention from the PRD.
- Decision: D-008 marked owner-confirmed. TASKS.md M3 "Margin vs. measured accuracy" line reworded as proposed. PRD sentence reworded to "The full technical spec, a detailed design document, ...". The second proposal (a new M3 task for the validation split) adds a task, which the contributor rules forbid the builder to do, so it stays under "Proposed task changes" in STATUS.md.
- Effect: DECISIONS.md (D-008), TASKS.md (M3, one line), docs/PRD.md (one sentence).

## D-011: Feature model details, test layout, generated data
- Date: 2026-11-13
- Source: BUILDER (M2 session 1; owner may override)
- Decision:
  - `model/features.py` also holds a byte-level frame parser (`parse_frame`) that follows SPEC section 4, because the feature model needs parsed fields and the M2 parser RTL will be checked against it. An IPv4 version field other than 4 is dropped (SPEC only names IHL < 5; this is the conservative reading). TCP needs 14 bytes after the IP header (through the flags byte), UDP needs 4 (the ports).
  - `model/features.py` follows D-004: `pkt_id` counts every frame from 0 and wraps at 16 bits; `pkt_len` saturates at 65535; dropped frames never enter the window.
  - Python unit tests are pytest files in `tb/` (`tb/test_features.py`, `tb/test_traffic_gen.py`). Each ends with a `pytest.main` call, so the existing `make test` loop (`python3 tb/test_*.py`) runs them unchanged.
  - The generator writes the `tuser` flag only in the CSV (a pcap cannot hold it). Generated `data/traffic*` files (about 2 MB for the default 9000 frames) are gitignored and rebuilt from the seed. The train/validation/test split by scenario is M3 work; the CSV carries `scenario` and `scenario_seed` for it.
- Why: Keeps the model small, with one place for the arithmetic. Reproducible files from a seed avoid committing binary data.
- Effect: model/features.py, model/traffic_gen.py, tb/test_features.py, tb/test_traffic_gen.py, .gitignore.

## D-012: cocotb flow, eth_parser interface
- Date: 2026-11-13
- Source: BUILDER (M2 session 2; owner may override)
- Decision:
  - **cocotb flow.** Each `tb/test_<module>.py` is started from `.venv` (`python3 tb/test_<module>.py`, so the `make test` loop is unchanged). It prepares any input files that need Scapy, then calls `tb/cocotb_run.py`, which builds the module with Icarus and runs the cocotb tests in the OSS CAD Suite Python (`tabbypy3`, Python 3.11, cocotb 2.1.0.dev0). Reason: cocotb runs Python inside the simulator and needs a shared libpython. The `.venv` Python 3.13 has none (`libpython3.13.so` is not installed and system packages cannot be added); `tabbypy3` ships one built to match its Icarus. `NG_SIM_PYTHON` overrides the interpreter. Consequence: code imported inside the simulator (the test module and `model/features.py`) must run on Python 3.11 and must not need Scapy.
  - **Known simulator issue and workaround.** With this cocotb/Icarus build, a test that ends while a signal write is still queued makes `vvp` crash with signal 11 at shutdown, after all tests have finished and passed (reproduced with a 6-line test: write `rst_n`, end the test). The runner then exits non-zero. Testbenches stop their background coroutines and wait one clock edge before returning (`Env.stop()` in `tb/test_eth_parser.py`). Not a design bug; not reported upstream.
  - **eth_parser interface.** The frame passes through unchanged with one register stage (standard valid/ready pipeline register, `s_tready = !m_tvalid || m_tready`). Sideband on every output beat: `m_hdr_done` (whole Ethernet header seen), `m_vlan`, `m_ipv4`. On the tlast beat only: `m_drop` and a 2-bit `m_drop_reason` (1 tuser, 2 short header, 3 not IPv4), in the same priority order as `model/features.py`. A short VLAN-tagged frame (< 18 bytes) and a short untagged frame (< 14 bytes) share reason 2. QinQ (a second 0x8100) is "not IPv4". eth_parser does not look at layer 3; l3l4_parser does, using `m_vlan` to find the IPv4 header. The reason code is kept for test readability and costs 2 flops.
- Why: Keeps `make test` one command without installing system packages, and keeps each parser small and checkable on its own against the model.
- Effect: rtl/eth_parser.sv, tb/test_eth_parser.py, tb/cocotb_run.py, Makefile comment.

## D-013: Report exact commands; no system packages
- Date: 2026-11-13
- Source: OWNER (session instruction for M2 session 2, relaying the PM review of session 1)
- Instruction: The PM found `make` not installed, so "make test exited 0" could not be true as written; the PM re-ran the tests directly (47 and 6 passed). From now on report the exact command run and its real output, and say plainly if a tool is missing. Do not install system packages (apt is broken); use `.venv` and `<tools>/oss-cad-suite/bin`. Branch `m2-s2` off `m2-s1`; PR with base `m2-s1`.
- Decision: Followed. Finding this session: `make` is not on the default PATH (`command -v make` in a clean shell finds nothing). GNU Make 4.4.1 is present in user space at `$NG_TOOLS/bin/make` (symlink into `$NG_TOOLS/make`, unpacked in M1 session 2) and is found only after `. scripts/env.sh`. STATUS.md now lists both the direct `python3 tb/test_*.py` commands and the `make` commands with their output. Nothing was installed this session.
- Effect: STATUS.md reporting format; no file changes from the instruction itself.

## D-014: l3l4_parser interface; parsers tested as a chain
- Date: 2026-11-13
- Source: BUILDER (M2 session 3; owner may override)
- Decision:
  - **Interface.** `l3l4_parser` takes eth_parser's output stream plus its `m_vlan`, `m_drop` and `m_drop_reason`. It consumes the frame bytes and emits one record per frame (valid/ready, one clock after the tlast beat): `r_drop`, a 4-bit `r_drop_reason`, `r_is_tcp`, `r_syn`, `r_ack`, `r_rst_fin`, `r_ip_len`, `r_ttl`, `r_dst_port`, and `r_pkt_len`. Parsed fields are forced to 0 for dropped frames. `s_tready = !r_valid || r_ready`. In the full chip the feature builder never stalls, so this costs nothing.
  - **pkt_len is counted here** (sum of `tkeep` bits, saturating at 65535, SPEC 8.1). This block already sees every beat, so the feature builder does not have to.
  - **Drop reason codes**, same priority as `model/features.py parse_frame`: 0 kept, 1 tuser, 2 short Ethernet header (untagged or tagged), 3 not IPv4 (codes 1-3 passed through from eth_parser), 4 short IPv4 header, 5 IP version != 4 (D-011), 6 IHL < 5, 7 options cut short, 8 non-first fragment, 9 not TCP/UDP, 10 short TCP/UDP header. The code is kept for test readability (4 flops); the async FIFO entry only carries DROP.
  - **Field capture by byte offset.** A beat counter gives each byte lane its frame offset. A header byte is captured when its offset matches and its `tkeep` bit is set. There is no header shift register. The L4 offset uses the registered IHL, which always arrives at least two beats before any L4 byte.
  - **Test top is the two parsers chained** (`tb/parser_chain_tb.sv`, test-only, not in `rtl/`). So the l3l4 test drives real eth_parser sideband, not an imitation. eth_parser keeps its own unit test.
  - **Scapy cross-check categories.** Scapy decodes each generated frame in `.venv` before the simulation. Frames Scapy fully decodes are compared field by field. Frames cut inside the TCP/UDP header that the chip can still use (it needs 14 TCP or 4 UDP bytes) are compared on keep/drop only, because Scapy decodes no L4 layer for them. Frames cut before those bytes, and non-IPv4, fragment, other-protocol or tuser frames, must be dropped.
- Why: one record per frame is what the feature builder needs. Testing the chain checks the real hand-off between the two parsers.
- Effect: rtl/l3l4_parser.sv, tb/parser_chain_tb.sv, tb/test_l3l4_parser.py.

## D-015: PM correction on make; M2 session 3 instructions
- Date: 2026-11-13
- Source: OWNER (session instruction for M2 session 3, relaying the PM)
- Instruction: The PM re-ran lint, compile-check and `make test` on `m2-s2` and all passed. Correction: `make` exists at `$NG_TOOLS/bin/make` after `. scripts/env.sh`, so the M2 session 1 "make test" claim was fine. Branch `m2-s3` off `m2-s2` (stacked), PR with base `m2-s2`. Build l3l4_parser and its test, cross-check the parsers against the model and Scapy on all generated frames, prove the tests can fail, report real output.
- Decision: Followed. The D-013 statement that the PM "found make not installed" is superseded by this correction. D-013 is left as written; `make` was on the `. scripts/env.sh` path all along.
- Effect: STATUS.md; no change to tooling.

## D-016: M2 session 4 instructions
- Date: 2026-11-13
- Source: OWNER (session instruction for M2 session 4, relaying the PM)
- Instruction: The PM re-ran `make lint compile-check` and `make test` on `m2-s3` and all passed (eth_parser 5/5, l3l4_parser 6/6, features 47, traffic_gen 6). Branch `m2-s4` off `m2-s3` (stacked), PR with base `m2-s3`, title "M2: skid FIFO". Build `rtl/skid_fifo.sv` and its cocotb test (random `tready`, random `tvalid` gaps, always-ready, long stall, reset mid-stream, scoreboard with beat count, throughput with `tready` high), prove the tests can fail, report real output. Do not start `async_fifo`; do not tick "Lint clean for all new RTL".
- Decision: Followed. Not merged, not pushed to main.
- Effect: rtl/skid_fifo.sv, tb/test_skid_fifo.py, TASKS.md (skid_fifo ticked), STATUS.md.

## D-017: skid_fifo design
- Date: 2026-11-13
- Source: BUILDER (M2 session 4; owner may override)
- Decision:
  - **Small FIFO, not the two-register skid.** `DEPTH` slots (default 4, power of two, at least 2; anything else stops elaboration with an error), each slot holds one whole beat: tdata, tkeep, tlast, tuser together. First-word-fall-through read: the output is the slot the read pointer points at (a 4-to-1 mux of storage flops). A pure registered output was not required by SPEC; the SPEC wording is "a clean, registered stream", and every bit still comes from flops.
  - **Latency 1 cycle, one beat per cycle** while `m_tready` is high (steady state uses one slot).
  - **`s_tready = (count != DEPTH)`**, flops only. When the FIFO is full, `s_tready` is low even if `m_tready` is high in that cycle. Reason: no combinational path from `m_tready` back to the source. It costs no throughput in the chip, where nothing downstream stalls.
  - **Reset** clears pointers and count only. Beats stored at reset are discarded (a frame cut by reset is lost, as in the parsers). The slot memory has no reset, so the output data lines are only meaningful while `m_tvalid` is high (stale or X before the first write).
  - **Test** models the FIFO fill level exactly (`m_tvalid` and `s_tready` are checked every cycle against the number of queued beats), so the test is tied to `DEPTH = 4`, the default. Other `DEPTH` values are not simulated; the runner builds with the default only. Lint and compile checks run with the default only; a bad `DEPTH` (3) was checked by hand to stop elaboration in both Verilator and Icarus.
- Why: Simplest block that meets SPEC (clean stream, no loss, full rate) and is easy to check exactly.
- Effect: rtl/skid_fifo.sv, tb/test_skid_fifo.py.

## D-018: M2 session 5 instructions
- Date: 2026-11-13
- Source: OWNER (session instruction for M2 session 5, relaying the PM)
- Instruction: The PM re-ran `make lint compile-check` and `make test` on `m2-s4`: all passed (eth_parser 5/5, l3l4_parser 6/6, skid_fifo 7/7, features 47, traffic_gen 6). PM review note: the skid_fifo "summary" test cannot fail, so it must not count as a test; make it a log line or a real assertion, and report counts accordingly. Branch `m2-s5` off `m2-s4` (stacked), PR with base `m2-s4`, title "M2: async FIFO and M2 lint clean". Build `rtl/async_fifo.sv` and its cocotb test (several clock ratios including a non-integer one, random gaps and `tready`, fill-to-full then drain, full/empty never allow overflow or underflow, beats checked per ratio). Then make "Lint clean for all new RTL" true. Prove the tests can fail (at least a binary pointer and a wrong full flag). Be honest about what RTL simulation can and cannot show about clock-domain crossing. Commit, push `m2-s5`, open the PR; never push to main, never merge; no M3 work.
- Decision: Followed. The skid_fifo summary test was removed; its total is now a plain log line printed after every run (`scoreboard running total`). skid_fifo now reports 6 tests, all real checks.
- Effect: rtl/async_fifo.sv, tb/test_async_fifo.py, tb/test_skid_fifo.py, TASKS.md (async_fifo and lint boxes ticked), STATUS.md, README.md status row.

## D-019: async_fifo design, reset choice and what simulation shows
- Date: 2026-11-13
- Source: BUILDER (M2 session 5; owner may override)
- Decision:
  - **Interface.** Same beat fields as skid_fifo (tdata, tkeep, tlast, tuser), parameter `DATA_W` (default 64) and `DEPTH` (default 16 = `AFIFO_DEPTH`). Write side on `wr_clk`, read side on `rd_clk`, each with its own active-low reset. In the chip, the 114-bit entry of SPEC 8.1 goes in tdata (`DATA_W = 114`, tkeep is then 14 bits) and tkeep/tlast/tuser are tied to constants. That costs 16 storage bits per slot unless synthesis removes them; to be checked at M4. A separate entry-only variant was not built. TASKS.md only says "`rtl/async_fifo.sv` + test with several clock ratios"; reusing the skid_fifo beat fields was a builder choice, made to keep the two FIFOs interchangeable and the code small.
  - **Structure.** Standard Gray-pointer FIFO: pointers one bit wider than the address; each pointer is sent to the other side in Gray code from a flop, through two synchronizer flops (`*_sync1_q`, `*_sync2_q`); full and empty flags are registered and computed from the *next* pointer. First-word-fall-through read (output = oldest slot). `DEPTH` must be a power of two and at least 4 (the Gray full compare needs pointers of 3 bits or more); other values stop elaboration (checked by hand with 2, 6, 12 in Verilator and Icarus).
  - **Reset (SPEC section 8 says one reset synchronizer per domain, asynchronous assert, synchronous release; it does not say how the FIFO resets).** Choice: two reset inputs, one per side, each driven by its domain's reset synchronizer, and **asynchronous reset inside the FIFO** (`always_ff @(posedge clk or negedge rst_n)`). Reason: with a synchronous reset, one side could clear its pointer a few cycles before the other, and the other side would briefly compare its pointer with a cleared one and see a FIFO that is not empty (garbage read) or not full. With asynchronous assert both sides clear at the same instant. Release order does not matter (both pointers are zero until the released side moves). The other blocks (skid_fifo, parsers) keep their synchronous reset; neurogate_top (M4) must feed both FIFO resets from the same external reset through the two synchronizers. `full_q` resets to 1, so `s_tready` is low during reset and for one write clock after release; `empty_q` resets to 1. Beats stored at reset are discarded. Slot memory has no reset.
  - **Latency.** A write shows on the read side after 2-3 read clock edges; the full flag clears 2-3 write clock edges after a read. One beat per own clock on each side; long-run rate = slower clock.
  - **Test.** Six clock ratios (write : read = 5:2, 2:5, 1:1 with random phase, 7:5, 5:7, and the chip's 156.25 : 200 MHz); the read clock starts at a random odd number of ps so edges of the two clocks never coincide. The scoreboard checks every beat on all fields, and checks every cycle that `s_tready` = 1 only with fewer than DEPTH beats stored (no overflow), `m_tvalid` = 1 only with a written beat waiting (no underflow), and that neither flag stays pessimistic longer than three edges of its own clock after the other side moved (catches a full or empty flag that is wrong in the safe direction). A simulation-time watchdog turns a deadlock into a failure.
  - **What simulation shows and does not show.** RTL simulation has no metastability: a flop always samples a clean 0 or 1, and every bit of a bus arrives at the same instant. So the tests show that the pointer arithmetic, flags, ordering and reset are right for the ratios simulated. They do not show that the crossing is safe in silicon. Shown by mutation (see STATUS.md): sending binary pointers across with a matching binary full compare, or cutting each synchronizer to one flop, both **pass every test**. These are exactly the faults that only matter with metastability or skew between bits. Their absence is checked by reading the code (Gray encoding from a flop, two flops in the receiving clock, no logic between them) and will be listed in the M4 CDC review. Timing constraints for the synchronizer paths (max delay / skew on the Gray buses) are an M4 item; not done.
- Why: The textbook dual-clock FIFO, small and easy to review; asynchronous reset removes the one-side-reset hazard.
- Effect: rtl/async_fifo.sv, tb/test_async_fifo.py.

## D-020: independent M2 review fixes; truncated-header clarification
- Date: 2026-11-13
- Source: OWNER (session instruction for M2 session 6, relaying the PM) for the fix list; the clarification text is BUILDER, pending owner OK
- Instruction: Fix every must fix and should fix item of the independent M2 review and the nits. SPEC is frozen, so do not change behaviour for the truncated-header finding: add a marked clarification instead.
- Decision: SPEC.md section 4 gains a bullet tagged `[CLARIFICATION, pending owner OK at Gate M2]` stating the rule already in D-011 and implemented in `model/features.py` and `rtl/l3l4_parser.sv`: TCP is kept when 14 header bytes are present, UDP when 4. The owner confirms or rejects it at Gate M2. If rejected, model, RTL and tests change together. Other items: monitors judge handshakes at the clock edge, stall stability asserted, watchdogs added; `make test` keeps per-suite logs under `results/<commit>/`; the Scapy claim is stated with its breakdown (8,900 fields compared, 6 keep/drop only, 694 dropped by both); the SPEC verification row follows D-008. No finding was rejected.
- Effect: SPEC.md, README.md, TASKS.md (tick wording only), docs/stories.md, Makefile, tb/cocotb_run.py, tb/handshake_monitor.py, tb/test_eth_parser.py, tb/test_l3l4_parser.py, tb/test_skid_fifo.py, .gitignore, STATUS.md, DECISIONS.md (D-019 wording).

## D-021: Gate M2 approved; M3 authorized
- Date: 2026-11-13
- Source: OWNER (instruction Nov 13 2026, 10:55 PM ET)
- Instruction: (1) Gate M2 passes. (2) The truncated-header clarification in SPEC section 4 is confirmed: a TCP frame cut after at least 14 header bytes, or a UDP frame cut after at least 4, is kept and parsed. (3) The Scapy criterion is accepted as met with the breakdown 8,900 / 694 / 6: of 9,600 generated frames, all fields compared on 8,900 kept frames, 694 dropped by both, and keep/drop only on 6 kept frames that Scapy cannot decode as TCP/UDP. The TASKS.md gate line is reworded to match. (4) The owner merges the M2 pull requests (#21-#26) themself. (5) M3 is authorized: automated builder at medium effort for the training and quantization scripts, automated builder at medium effort for the MLP core.
- Decision: Done. The builder's earlier calls stand: IPv4-only drop (other EtherTypes and IP versions are dropped) and Python 3.11 as the test runtime.
- Effect: SPEC.md section 4 tag changed from pending to owner-confirmed (no behaviour change). TASKS.md Gate M2 line reworded. `docs/regmap.md` CONF_THRESH paragraph now says the threshold is picked on a validation split, never on the test set (D-008).
- Note: when this session started, `main` held only PR #21 (the later PRs were merged into each other's branches, not into `main`). The branch `m3-s1` was therefore stacked on `m2-s6`, as the owner's note allowed. The PR to `main` shows the M2 commits too until #22-#26 reach `main`.

## D-022: M3 Python model: data splits, quantization scheme, confidence shift, threshold rule
- Date: 2026-11-13
- Source: BUILDER (M3 session 1). **The confidence shift and `CONF_THRESH` = 13 were APPROVED by the owner on 2026-11-13 (11:39 PM ET)**; see D-026. The rule is now written into SPEC section 7.
- Decision:
  - **Splits** (`model/dataset.py`): training pool = generator seed 1, 14 scenarios per class; the first 10 per class train, the last 4 per class are validation. Test = generator seed 2, 10 scenarios per class (different scenario seeds). Whole scenarios, 300 packets each. Each split is run as one stream through the real parser and window; frames the parser drops have no feature row (train 8,344 / val 3,334 / test 8,347 classified frames; 656 / 266 / 653 dropped).
  - **Float net** (`model/train.py`): 8-16-3, full-batch Adam, 1500 epochs, seed 20260101, input = feature / 127; the best epoch is chosen on validation. Plain NumPy (no torch or scikit-learn installed; only `matplotlib` was added to `.venv` with pip, for the chart).
  - **Quantization** (`model/quantize.py`): symmetric per-tensor int8 weights in -127..127 (-128 is never used), int32 biases, layer-1 right shift `shift1` = 7 (smallest shift that fits the 99.9th percentile of positive training accumulators into 127), round-half-up as SPEC section 6 says, no requantize on layer 2. Calibration uses only the training split.
  - **Confidence shift (proposal).** SPEC section 7 says `confidence = clamp(top - second, 0, 127)` on the raw int32 logits. With int8 weights the raw margin is in the thousands for almost every packet, so the 0..127 confidence would sit at 127 and carry no information. The golden model therefore has `conf_shift`: `confidence = min((top - second) >> conf_shift, 127)`, with `conf_shift` = 8 (smallest shift that fits the 95th percentile training margin into 127). The class (argmax) is unchanged; only the confidence scale changes, so `decision_unit` needs one constant shift of the logit difference. `conf_shift = 0` gives the literal SPEC rule. Owner approved it (D-026).
  - **Threshold rule** (fixed before the test split was read): the default `CONF_THRESH` is the smallest value for which validation packets with confidence at or above it are at least 98% correct. Result: **13**. The test split is used once for reporting.
  - **Files:** `data/model/` (weights.json, four ROM .hex files, conf_thresh.txt, float_weights.json) and `data/vectors/mlp_vectors.csv` are committed (small). ROM address order: `rom_w1` = neuron*8 + input, `rom_w2` = class*16 + hidden.
  - `.gitignore`: the pattern `results/` also hid `docs/results/`; it is now `/results/` (the test-log folder in the repo root only).
- Why: Keeps the arithmetic simple enough for one MAC per cycle, and keeps the test set untouched until the one final report.
- Effect: model/dataset.py, train.py, quantize.py, golden.py, evaluate.py, export_vectors.py, tb/test_golden.py, data/model/, data/vectors/, docs/results/, .gitignore, TASKS.md (5 M3 boxes), README.md status.

## D-023: M3 session 2 instructions
- Date: 2026-11-13
- Source: OWNER (session instruction for M3 session 2)
- Instruction: PR #27 (all M2 work) is merged into `main`; PR #28 (`m3-s1`) holds the M3 Python model. Branch `m3-s2` off `m3-s1`, merge `origin/main` into it first, open a PR with base `m3-s1` (stacked), title "M3: feature builder and MLP core RTL, bit-exact vs golden". Build, in order: `rtl/feature_builder.sv` + test vs `model/features.py` bit for bit on generated traffic; `rtl/mlp_core.sv` (ROMs from the .hex files, 1 MAC per cycle) + test vs `model/golden.py` on all of `data/vectors/mlp_vectors.csv` (logits and class) plus saturation, int32 extreme, round-half-up and all-hidden-zero corner cases (custom weights allowed); record cycles per inference (simulated). Do NOT build `decision_unit` (the confidence shift of D-022 waits for the owner); `mlp_core` must output raw int32 logits; do not change SPEC for D-022. Install nothing system-wide. Never push to main, never merge.
- Decision: Followed. `origin/main` merged into `m3-s2` with no conflicts and no content change (main's commits were already contained in `m3-s1`'s history by content). `decision_unit` not started. SPEC.md not changed.
- Effect: see D-024, D-025; TASKS.md (3 boxes), STATUS.md, README.md status.

## D-024: feature_builder design
- Date: 2026-11-14
- Source: BUILDER (M3 session 2; owner may override)
- Decision:
  - **Input** is l3l4_parser's one-record-per-frame output (its `r_*` signals). `s_ready` is tied to 1: the block takes one record per clock and never stalls the parsers (SPEC 8.1).
  - **Window update in one clock**: a 64-slot ring of {is_syn, bucket}, 64 seven-bit bucket counters, and running totals for occupied buckets and SYN packets. When the leaving and the entering packet share a bucket, the counter is left unchanged (same result as the model's decrement-then-increment). Bucket hash = `dst_port[5:0] ^ dst_port[11:6]`, equal to the SPEC formula.
  - **F3 without a divider**: `min(len,1500)*127//1500` is computed as `(min(len,1500) * 22195) >> 18`. Checked equal for every input 0..1500 (in the test, and through the RTL with every length 0..1500).
  - **Overflow write port**: the entry is offered for one cycle. If the async FIFO is full (`m_tready` low) the entry is discarded and the 16-bit saturating pending count goes up (SPEC 8.1). So the port does not follow the AXI-Stream "hold until taken" rule; this is on purpose and the async FIFO only writes on valid && ready. `m_tvalid` does not depend on `m_tready` combinationally.
  - **OVF_ONLY entries** carry zero features, `pkt_len` 0 and `pkt_id` 0; only `ovf_cnt` is meaningful.
  - **Latency**: the entry is offered one clock after the record is taken.
- Why: Keeps the parsers stall-free and the features independent of FIFO timing (D-004), with no divider in hardware.
- Effect: rtl/feature_builder.sv, tb/test_feature_builder.py, tb/fb_common.py, tb/feature_chain_checks.py, tb/feature_chain_tb.sv (test-only chain: eth_parser -> l3l4_parser -> feature_builder).

## D-025: mlp_core design; golden model bias range tightened to keep int32 exact
- Date: 2026-11-14
- Source: BUILDER (M3 session 2; owner may override)
- Decision:
  - **Interface**: input is the 114-bit async FIFO entry (AXI-Stream, `s_tready` high only when idle). Output (valid/ready, held while stalled) carries kind, pkt_len, pkt_id, ovf_cnt, the three **raw int32 logits**, and `m_class` (argmax, ties to the lowest index). DROP and OVF_ONLY pass through with zero logits and class 0. No confidence is computed here, so either confidence rule (SPEC section 7 literal, or the D-022 shift) can be applied in `decision_unit`.
  - **Argmax moved into mlp_core.** The argmax rule of SPEC section 7 is unchanged; it is computed here, as each logit finishes, at no extra cycle cost. `decision_unit` will still compute confidence and `low_conf`, and can use `m_class` (or recompute it from the logits). The owner asked for the class to be compared with the golden model in this session, which needs it in RTL. If the owner prefers the argmax in `decision_unit`, it is a small move.
  - **Schedule**: one multiply-accumulate per clock, 176 steps (128 layer 1, 48 layer 2), two pipeline stages (registered ROM read, then MAC). Layer 2 follows layer 1 with no bubble. Requantize and argmax happen in the MAC cycle of each output's last step.
  - **ROMs** are loaded with `$readmemh` from parameters `W1_HEX`, `B1_HEX`, `W2_HEX`, `B2_HEX` (default `data/model/rom_*.hex`). `SHIFT1` is a parameter, default 7; the test checks the default equals `shift1` in `data/model/weights.json`.
  - **Cycles per inference (simulated, core_clk)**: 178 clocks from the edge that takes a CLASSIFY entry to the edge that hands over its result; back to back, one inference starts every 179 clocks. DROP / OVF_ONLY: 1 clock latency, one every 2 clocks. The SPEC's architectural estimate was 176 MAC cycles plus a few cycles of overhead.
  - **Golden model change (precondition only, no arithmetic change)**: `golden.validate()` now refuses a bias closer to the int32 limits than the largest weighted sum of its layer (8*127*127 = 129,032 for layer 1, 16*127*127 = 258,064 for layer 2). Before, a bias near the int32 limit was accepted and the golden model (unbounded Python integers) would have produced values outside int32 that a 32-bit accumulator cannot hold, so RTL and model could disagree. With the bound, no partial or final sum leaves int32. The trained weights are far inside the bound (all existing tests pass). The rounding add `acc + 2^(shift1-1)` is done 33 bits wide in RTL, because it can pass 2^31-1 when acc is at the top; a custom test hits exactly that case.
- Why: Keeps the int32 accumulator of SPEC section 6 exact for every parameter set the model accepts, and keeps the RTL independent of the open confidence decision.
- Effect: rtl/mlp_core.sv, model/golden.py (validate, docstring), tb/test_golden.py (one new test), tb/test_mlp_core.py, tb/mlp_common.py, tb/mlp_custom_sets.py, tb/mlp_custom_checks.py, tb/cocotb_run.py (HDL parameters and per-build folders).

## D-026: M3 session 3 instructions; confidence rule approved
- Date: 2026-11-14
- Source: OWNER (approval of D-022 given 2026-11-13 11:39 PM ET; session instruction for M3 session 3)
- Instruction: (1) D-022 is approved: `confidence = min((top - second) >> 8, 127)` instead of the raw logit margin; `CONF_THRESH` = 13 (picked with shift 8 on the validation split) stands. Mark D-022 owner-approved, write the rule into SPEC section 7 (no longer proposed), update `docs/regmap.md` if it states the formula. (2) Build `rtl/decision_unit.sv` + cocotb test: threshold edge (== threshold, +/- 1), ties, cap at 127, margin exactly at multiples of 256; compare bit for bit with `model/golden.py` on all of `data/vectors/mlp_vectors.csv`. Keep the class in `mlp_core` (D-025) unless there is a clear reason to move it, and log the choice. If practical, one chained test feature_builder -> mlp_core -> decision_unit against golden on generated traffic. (3) Confirm the Gate M3 line in `TASKS.md`. Branch `m3-s3` off `m3-s2`; PR with base `m3-s2` (stacked), title "M3: confidence rule (owner-approved) and decision unit RTL". Never push to main, never merge.
- Decision: Followed. SPEC section 7 states the rule (with `CONF_SHIFT` = `conf_shift` in the weights file). `docs/regmap.md` now states the formula and that the reset value of `CONF_THRESH` is 13. `model/golden.py` docstring no longer calls the shift a proposal; the golden model and the committed results already used shift 8: re-running `python -m model.evaluate` and `python -m model.export_vectors` left every committed file unchanged, so no vectors were re-exported.
- Effect: SPEC.md section 7, docs/regmap.md, model/golden.py (docstring only), DECISIONS.md (D-022 source line), rtl/decision_unit.sv (D-027), TASKS.md, STATUS.md, README.md status.

## D-027: decision_unit design; the class stays in mlp_core
- Date: 2026-11-14
- Source: BUILDER (M3 session 3; owner may override)
- Decision:
  - **Class stays in `mlp_core`** (D-025 unchanged). Reasons: it costs no extra cycle there (computed as each logit finishes), the class is already compared with the golden model in the `mlp_core` test, and `decision_unit` still needs only the class index to pick "top" and "second". Moving it would add a three-way compare to the confidence path for no gain. `decision_unit` trusts the class, but does not trust it blindly: if a class is not the largest logit (never produced by `mlp_core`), the margin would be negative and is clamped to confidence 0; class value 3 gives confidence 0. Both are tested.
  - **Arithmetic** (matches `golden.decide`): `top` = logit of the chosen class, `second` = larger of the other two, `margin = top - second` in 33 bits (two int32 values can differ by up to 2^32 - 1), arithmetic shift right by `CONF_SHIFT` (parameter, default 8; the test checks it equals `conf_shift` in `data/model/weights.json`), then clamp to 0..127. `low_conf` = `confidence < conf_thresh` (7-bit input, from the `CONF_THRESH` register; 0 never flags; strict "less than", so a packet exactly at the threshold is not flagged).
  - **Interface**: valid/ready in (from `mlp_core`) and out (to `stats_counters`), one registered stage: 1 clock latency, one entry per clock, output held while stalled. `s_ready` = output empty or being emptied (one combinational term from `m_ready`). DROP and OVF_ONLY entries pass their metadata through with class 0, confidence 0, `low_conf` 0, so the counters never see a class or flag for a frame that was not classified.
  - **`conf_thresh` is sampled when an entry is accepted** by `decision_unit`, not when it is classified; a register write applies to the next entry taken.
  - **Test-only chain** `tb/decision_chain_tb.sv`: eth_parser -> l3l4_parser -> feature_builder -> mlp_core -> decision_unit in one clock, no async FIFO (not what is being tested; with no FIFO an entry that arrives while `mlp_core` is busy would be lost). The test sends the next frame only after the previous result is out, so nothing is lost (`ovf_cnt` must be 0). The ROM paths are passed as `-D` macros with `defparam` because Icarus cannot bind a parent parameter inside an instance parameter list; `tb/cocotb_run.py` gained an optional `defines` argument.
  - **Found on the way:** a missing ROM file is only a `$readmemh` error line in Icarus; the simulation goes on with unknown ROM contents and produces X logits. The chain test caught it through the X (relative paths do not work from the simulator's folder). Not changed in the RTL.
- Why: Keeps the confidence rule one small, exact block that is easy to compare with the model, and keeps the earlier class decision.
- Effect: rtl/decision_unit.sv, tb/test_decision_unit.py, tb/decision_chain_tb.sv, tb/decision_chain_checks.py, tb/cocotb_run.py.

## D-028: M3 session 4 (review fixes, one PR per milestone); what "all test vectors" means
- Date: 2026-11-14
- Source: OWNER (session instruction for M3 session 4, relayed by the PM) for the fix list and the single-PR rule; the wording choices below are BUILDER
- Instruction: Fix every should-fix and nit of the independent M3 review. Close the evidence gap on "RTL equals golden on all test vectors" by replaying the full unseen test split through `mlp_core` + `decision_unit`. Record commit, seeds and tool versions with the results. Open exactly one PR per milestone: from `m3-s4`, base `main`, containing all of #28, #29 and #30.
- Effect:
  - New `make test-full` (`tb/full_split_run.py`, `tb/full_split_checks.py`, `tb/mlp_decision_tb.sv`, `python -m model.export_vectors --full`). It replays all 8,347 classified packets of the test split (duplicates kept; `data/vectors/mlp_vectors_full.csv`, generated, gitignored), back to back and with random gaps and stalls. The full run takes about 2 minutes per pass in simulation, so it is a separate target to keep `make test` from growing; the 2,028-row set stays in `make test`.
  - TASKS.md Gate M3 line now names both sets and the row counts (wording only, no task added or removed).
  - `docs/results/metrics.json` gets a `provenance` block (commit, whether tracked code outside `docs/results/` and `data/model/` was modified, seeds, Python / NumPy / Scapy versions). Numbers are unchanged.
  - `tb/test_golden.py`: new direct check that train and validation scenarios are disjoint.
  - RTL logs for the gate were produced from a clean, committed tree.
  - Stale statements fixed in README, SPEC section 5, PRD and STATUS; SYN and IPv4 explained on first use in PRD and stories; obsolete comment removed from `model/golden.py`; "about 190 clocks" in `tb/decision_chain_tb.sv` labelled an estimate (a measured chain-latency assertion was not added).

## D-029: Gate M3 approved; M4 authorized; M4 rules
- Date: 2026-11-14
- Source: OWNER (Gate M3 decision, relayed in the M4 session 1 instruction; approval given 2026-11-14 7:50 AM ET)
- Instruction:
  1. Gate M3 passes.
  2. The owner merges only PR #31 (`m3-s4` into `main`). PRs #28, #29 and #30 are closed unmerged as superseded.
  3. M4 is authorized. Model use: automated builder at medium effort for counters, registers, tests and scripts; automated builder at medium effort for top-level wiring, the clock-crossing review and timing fixes.
  4. The M4 builder may try a user-space OpenSTA install under `<tools>`. If that fails, timing is reported as "not done, because ..." and is never invented. M4 ends in ONE pull request to `main` holding all M4 work.
- Effect: M4 session 1 branches `m4-s1` off `m3-s4` (PR #31 is not merged yet) and opens a draft pull request with base `m3-s4`. Top-level wiring and the clock-crossing review are left for a later session.

## D-030: stats_counters and axil_regs design (M4 session 1)
- Date: 2026-11-14
- Source: BUILDER (owner may override). Items marked [OWNER-APPROVED, D-041] go beyond what SPEC and the register map spell out; the owner approved them at Gate M4.
- Decision:
  - **Split.** `stats_counters` holds the counters and `LAST_RESULT` and takes one finished entry per clock from `decision_unit`; it never stalls it (`s_ready` is constant 1). `axil_regs` holds `CTRL`, `CONF_THRESH`, the bus logic and the read mux. `axil_regs` drives a one-cycle `soft_rst` pulse and `conf_thresh`; `stats_counters` receives the pulse.
  - **What each entry kind updates.** CLASSIFY: class count +1, class bytes + `pkt_len`, `PKT_LOWCONF` + `low_conf`, `LAST_RESULT`, and `PKT_OVERFLOW` + `ovf_cnt`. DROP: `PKT_DROPPED` +1 and `PKT_OVERFLOW` + `ovf_cnt`. OVF_ONLY: `PKT_OVERFLOW` + `ovf_cnt` only. All counters are 32 bits and wrap (regmap).
  - [OWNER-APPROVED, D-041] Reserved kind 3 (never produced): treated like OVF_ONLY (only `ovf_cnt` is added). A CLASSIFY entry with class 3 (never produced): only `PKT_OVERFLOW` is updated, no class counter and no `LAST_RESULT` change. Reason: no counter is bumped by something that cannot exist, and the overflow count is not lost.
  - **Soft reset precedence.** `soft_rst` is combinational from the AXI-Lite write handshake (address 0x00, `WSTRB[0]` = 1, `WDATA[0]` = 1), so the counters clear at the same clock edge that accepts the write. A read issued after the write response therefore always sees the cleared value. If an entry arrives in that same cycle, the reset wins and the entry is not counted (regmap).
  - **AXI-Lite handshake.** [OWNER-APPROVED, D-041] One transaction at a time per direction. A write is accepted only when address and data are both valid and no write response is waiting (`AWREADY` = `WREADY` = both valid and not busy). A read is accepted when no read response is waiting. Read data is registered. This is legal AXI-Lite (a slave may wait for both address and data) and keeps the block small.
  - Address bits [1:0] ignored; bits above [5] ignored (the regmap says the block decodes bits [5:2]).
- Effect: rtl/stats_counters.sv, rtl/axil_regs.sv, tb/test_stats_counters.py, tb/test_axil_regs.py.

## D-031: M4 session 2 instructions
- Date: 2026-11-14
- Source: OWNER (session instruction for M4 session 2)
- Instruction: PR #31 (all M3) is merged into `main`; `m4-s1` is done and PM-verified. Branch `m4-s2` off `m4-s1` and merge `origin/main` in. Build `rtl/neurogate_top.sv` with the two clocks; every crossing goes through a proper method (Gray pointers in async FIFOs, two-flop synchronizers for single bits, handshake for multi-bit control), never a raw multi-bit crossing. Write the clock-crossing review `docs/cdc.md`, labelled as a self-review. Add a structural check run by make that fails on a binary-pointer or one-flop crossing, and prove it by planting each break once, showing the failure, then restoring. Add a first top-level smoke test. No synthesis, gate-level simulation or timing this session. Open a DRAFT PR from `m4-s2` with base `m4-s1`; do not touch other PRs; never push to `main`, never merge.
- Effect: this session's scope; D-032.

## D-032: Top-level wiring and the structural clock-crossing check
- Date: 2026-11-14
- Source: BUILDER (owner may override). Items marked [OWNER-APPROVED, D-041] were approved by the owner at Gate M4.
- Decision:
  - **Reset synchronizer as its own module.** `rtl/reset_sync.sv` (2 flops by default, asynchronous assert, synchronous release), one instance per clock domain in `neurogate_top`. SPEC section 8 asks for these; a separate file follows "one module per file". The synchronized reset is used as an asynchronous reset by `async_fifo` (D-019) and as a synchronous reset by every other block. Verilator flags that double use (SYNCASYNCNET); waived inline in `reset_sync.sv` with this reason.
  - [OWNER-APPROVED, D-041] **Glue logic in `neurogate_top`.** SPEC section 2 says the top "has no logic of its own beyond reset synchronizers". The `decision_unit` output must feed two consumers (counters always ready; result stream only for CLASSIFY entries and stallable), so the top has three gates and the result packing: `decision_unit` ready = result-stream ready for a CLASSIFY entry, 1 otherwise; result `tvalid` = valid and CLASSIFY; counters take an entry on the same cycle it leaves. Result `tdata` packs SPEC 3.3 (confidence padded to 8 bits). Proposed SPEC wording: "no logic of its own beyond reset synchronizers and the fork from `decision_unit` to the result stream and counters".
  - **Port names.** `s_axis_*` (packet input, net_clk), `m_axis_res_*` (result, core_clk), `s_axil_*` (AXI-Lite, core_clk). Parameters `AFIFO_DEPTH` (16) and `CONF_THRESH_RESET` (13).
  - **ROM file paths** are text macros `ROM_W1`, `ROM_B1`, `ROM_W2`, `ROM_B2` (default `data/model/rom_*.hex`), as the chain testbench already does: Icarus cannot pass a parent parameter into `mlp_core`'s string parameters ("Unable to bind variable"; tried both typed and untyped parameters).
  - **`async_fifo` in the top** carries the 114-bit entry in `tdata`; `tkeep`/`tlast`/`tuser` are tied to constants and their outputs unused (lint waiver with reason). 16 constant bits per slot stay in the storage until synthesis shows whether they are removed (D-019 note).
  - **Structural CDC check** (`scripts/cdc_check.py`, `make cdc-check`). Yosys with the slang frontend elaborates all RTL (`proc; flatten; opt_clean; memory_collect`, no technology mapping) into JSON; the script applies rules C1, S1, S2, G1, M1, O1, R1, R2 (listed in `docs/cdc.md`). Yosys's own Verilog reader cannot parse the RTL (fails on a `return` in a function), so the slang frontend shipped in the OSS CAD Suite is used. G1 recognises the Gray encoder by its structure (`N ^ (N >> 1)` into the crossing register); a different but correct coding style would be reported as a violation (fails safe). The FIFO storage crossing (M1) is accepted on a protocol argument, not proven.
  - **Proof of the check.** `tb/test_cdc_check.py` (in `make test`) plants 7 faults in copies of `rtl/` and requires each to fail with its rule, and the real RTL to pass. The two M2 faults were also planted once by hand in `rtl/async_fifo.sv`, shown to fail the check while the FIFO simulation still passed 8/8, then restored with `git checkout` (STATUS.md has the output).
  - `make cdc-check` is added to `make all`; it is not inside `make lint` (lint stays Verilator only). `make test` covers it through `tb/test_cdc_check.py`.
  - **Smoke test** (`tb/test_neurogate_top.py` + `tb/neurogate_top_checks.py`): 120 frames (traffic seed 1, 40 per scenario), paced so the async FIFO cannot overflow, 30% random result-stream stalls, random clock phase and reset release. Every result beat and all counters (read over AXI-Lite) must equal the Python models; the frame counters must sum to 120; then a soft reset over AXI-Lite must clear them. Burst/overflow, bad-packet mixes and mid-frame reset at the top level are left to the later top-level test session.
- Why: smallest wiring that meets SPEC sections 2, 3 and 8; the M2 review showed simulation cannot catch CDC faults, so a structural check with its own planted-fault regression was needed.
- Effect: rtl/reset_sync.sv, rtl/neurogate_top.sv, scripts/cdc_check.py, tb/test_cdc_check.py, tb/test_neurogate_top.py, tb/neurogate_top_checks.py, docs/cdc.md, Makefile, README.md, TASKS.md (ticks), STATUS.md.

## D-033: M4 session 3 instructions
- Date: 2026-11-14
- Source: OWNER (session instruction for M4 session 3)
- Instruction: Branch `m4-s3` off `m4-s2`. Write the top-level end-to-end test on the full generated traffic through both clocks (every result equal to the Python models bit for bit; counters read over the register bus equal to the model). Write top-level bad-packet, backpressure, overflow and reset tests (hard reset and soft reset mid-stream, clocks at a random phase). Show once per new test that it can fail by planting a bug, then restore. Do not change the RTL unless a test exposes a real bug. No synthesis, gate-level simulation or timing. Leave the Verilator lint line unticked. Open a DRAFT PR with base `m4-s2`; do not touch other PRs.
- Effect: this session's scope; D-034.

## D-034: Top-level test design (M4 session 3)
- Date: 2026-11-14
- Source: BUILDER (owner may override). Items marked [OWNER-APPROVED, D-041] were approved by the owner at Gate M4.
- Decision:
  - **No RTL change.** No test exposed an RTL bug. Two test-side mistakes were found and fixed in the tests (see STATUS.md): a beat time off by one clock, and an output check made while the chip was still inside its two-edge reset hold.
  - **Shared helpers** are in `tb/top_common.py` (clock start at a random phase, power-up, hard reset, AXI-Lite master, result monitor, frame driver, model wrappers). The smoke test imports them; its result is unchanged.
  - **Full test** (`tb/test_neurogate_top_full.py`): the whole default generated set, 9000 frames, with input stalls (10%) and result stalls (30%). Paced so the async FIFO cannot overflow: 170 network clocks after a frame the model classifies, 2 after a dropped one. `PKT_OVERFLOW` must be 0.
  - **Overflow checking without a timing model.** Which frames are lost depends on FIFO timing, so the test does not predict them. It checks: each surviving beat equals the model result for its `pkt_id` (the feature window sees every frame, lost or not); beats arrive in order; frame 0 survives and the last frame is lost (so the overflow-only entry is used); and for all-kept traffic `PKT_OVERFLOW` = frames sent minus beats exactly. With dropped frames mixed in, the five frame counters must add up to the frames sent, `PKT_DROPPED` must not exceed the model's drops, and the class and byte counters must equal the surviving beats.
  - **Soft reset mid-stream** is checked against beat times: the counters must equal the beats taken after the clock edge that accepted the last reset write. Kept frames only, so no unseen drop entry changes the counters. A separate directed test makes a result beat and the reset write land in the same clock edge and requires the reset to win (regmap).
  - **Hard reset** uses reset low times of 2 ps, 3 ns, 90 ns and random up to 300 ns at random phase, mid-frame, with the result stream stalled, and with an AXI-Lite read in flight. After each: no stale result, counters 0, `CONF_THRESH` back to 13, then a fresh stream must equal a model started from scratch.
  - Unused data lanes of a short last beat carry random bytes, zeros or 0x45 (chosen per frame), so a chip that reads a lane `tkeep` marks as unused can be fooled.
  - [OWNER-APPROVED, D-041] The full test needs about 3 minutes of CPU (7 minutes wall time measured on a loaded machine) and is inside `make test`. The owner may prefer to move it to `make test-full` and keep a shorter frame count in `make test`.
- Why: the owner asked for end-to-end, bad-packet, backpressure, overflow and reset coverage at the top, with proof that each test can fail.
- Effect: tb/top_common.py, tb/neurogate_top_checks.py (refactor), tb/test_neurogate_top_full.py, tb/neurogate_top_full_checks.py, tb/test_neurogate_top_stress.py, tb/neurogate_top_stress_checks.py, TASKS.md (two ticks), STATUS.md, README.md.

## D-035: M4 session 4 instructions
- Date: 2026-11-14
- Source: OWNER (session instruction for M4 session 4)
- Instruction: Branch `m4-s4` off `m4-s3`. Verilator lint with zero warnings or listed waivers (make target). Yosys script mapping to Nangate45, with netlist, cell and area report and log, and a make target. Gate-level simulation of the netlist against the same vectors, or "not done, because ...". Artix-7 estimate with `synth_xilinx`. Throughput estimate from simulation cycle counts. Do NOT run timing (OpenSTA) and add no pipeline stages; leave the OpenSTA line unticked. No RTL change unless lint or synthesis exposes a real bug. Open a DRAFT PR with base `m4-s3`.
- Effect: this session's scope; D-036.

## D-036: Synthesis, gate-level simulation and estimates (M4 session 4)
- Date: 2026-11-14
- Source: BUILDER (owner may override). Items marked [OWNER-APPROVED, D-041] were approved by the owner at Gate M4.
- Decision:
  - **No RTL change.** Lint was already clean and synthesis exposed no bug.
  - **Lint**: `make lint` already existed; it now also prints the waivers (4, each with its reason in the RTL) and its exit status. Waiver table in `synth/README.md`.
  - **Synthesis** (`synth/synth_nangate45.ys`): Yosys with the slang frontend (same as `make cdc-check`), flattened, memories mapped to flip-flops and gates (no RAM cells in the library), flip-flops mapped by `dfflibmap`, logic by ABC, `check -assert` must find 0 problems. The liberty file is also read with `read_liberty -lib` so the checker knows every cell's port directions (without it, `check` reports false "no driver" problems on library cells). No timing constraints are given to ABC, so this is an area-oriented map; timing is a later session.
  - **Gate-level simulation** uses Verilog cell models generated by Yosys from the liberty file (`synth/gen_cell_models.sh`), not downloaded models. Vectors: the 120-frame smoke set of the RTL top-level test, same checks (`tb/gls_run.py`, started by `make gls`). The larger RTL tests were not repeated on the netlist (the smoke run takes about 75 s; the full 9000-frame run would take far longer). [OWNER-APPROVED, D-041] Also run the bad-packet set on the netlist at the gate.
  - **Generated files**: the netlist (2 MB) and full Yosys logs (0.7 MB) are gitignored and regenerated by `make synth`; the small reports are committed.
  - **Throughput** is measured by a new RTL test in `make test` (`tb/test_neurogate_top_throughput.py`): 14 frames back to back, 179 core clocks between result beats, label *simulated estimate*.
  - **Artix-7**: `make synth-xilinx`, *estimate*, no Vivado.
- Why: the owner asked for these results with honest labels.
- Effect: Makefile, synth/, tb/gls_run.py, tb/test_neurogate_top_throughput.py, tb/neurogate_top_throughput_checks.py, .gitignore, TASKS.md (five ticks; OpenSTA line left unticked), STATUS.md, README.md.

## D-037: M4 session 5 instructions
- Date: 2026-11-14
- Source: OWNER (session instruction for M4 session 5)
- Instruction: Branch `m4-s5` off `m4-s4` (merge `origin/main` if it moved; it had not). Last M4 task: OpenSTA timing report. Write an SDC with both clocks at the SPEC frequencies, the two clocks asynchronous, clock-crossing paths constrained as `docs/cdc.md` says, each constraint explained in plain English. Run STA on the Nangate45 netlist with the Nangate45 liberty; report worst setup slack, worst hold slack and the critical path per clock. Add a make target and commit the reports in `synth/reports/`. If the design fails at the target clock, add a pipeline stage where the critical path is, re-run all tests (RTL and gate-level) and keep before/after numbers. Label every timing number honestly; never invent one. Leave the 3-minute full-traffic test in `make test`. Do not change unrelated RTL; any RTL change goes in DECISIONS.md. Open a DRAFT PR with base `m4-s4`; do not touch other PRs.
- Effect: this session's scope; D-038.

## D-038: Timing constraints, timing-driven mapping instead of a pipeline stage (M4 session 5)
- Date: 2026-11-14
- Source: BUILDER (owner may override). Items marked [OWNER-APPROVED, D-041] were approved by the owner at Gate M4.
- Decision:
  - **No RTL change.** `rtl/` is untouched this session.
  - **Clocks.** The chip has two clocks: `net_clk` 156.25 MHz and `core_clk` 200 MHz. The session instruction names "the AXI-Lite/register clock" as a second clock; in this design the register bus runs on `core_clk` (SPEC section 2, `docs/cdc.md`), so it is covered by the `core_clk` constraint. Ideal clocks, no uncertainty margin.
  - **Crossings.** `set_max_delay 5.0` from each clock to the other (one core period, the shorter one) replaces the default related-clock check; `set_false_path -hold` between the clocks. Not `set_clock_groups -asynchronous`, because that would leave the crossing paths with no limit at all. The structural check shows the FIFO pointers and storage are the only crossing paths.
  - **Reset pin** `rst_n`: false path (asynchronous, goes only to the synchronizers). Synchronizer outputs stay timed, including recovery/removal.
  - [OWNER-APPROVED, D-041] **Pins**: SPEC gives no outside timing. Input and output delays are 20% of the clock period; inputs driven by `BUF_X1`; outputs load 3 fF.
  - **Main result without wire load**; the library default wire-load model (`5K_hvratio_1_1`) is a second report as a sensitivity check. OpenSTA applies no wire load unless asked (checked: worst setup slack changes only when a model is set explicitly).
  - [OWNER-APPROVED, D-041] **Fix for the setup failure: timing-driven logic mapping, not a pipeline stage.** On the M4 session 4 netlist, `core_clk` failed setup by 6.186 ns. The cause was an unbuffered high-fanout net: FIFO read-address bit `u_afifo.rd_bin_q[1]` drove 573 inputs of the storage read multiplexer, and an inverter after it 419 more (9.85 ns in those two cells). The logic behind them was short. A pipeline stage would add latency without removing the fanout, so the synthesis script now runs ABC with `-D 5000 -constr synth/abc.constr` (delay target plus buffering and gate sizing). Result: `core_clk` setup slack +2.649 ns, `net_clk` +4.694 ns, latency unchanged. TASKS.md says "add a pipeline stage"; this is a different remedy for the same goal, so the owner should confirm it.
  - **Gate-level check after the netlist change.** The new netlist was re-run through `make gls` (same 120-frame smoke vectors) because the netlist changed; the RTL tests were re-run in full as the instruction asks, although the RTL did not change.
  - [OWNER-APPROVED, D-041] **Open removal violation, reported not fixed.** 40 asynchronous reset/set pins in the async FIFO fail the removal check by 0.108 ns (synchronizer clock-to-output 0.18 ns, library removal 0.288 ns) with ideal clocks. It is independent of clock frequency, so a pipeline stage cannot fix it; a place-and-route flow fixes it by hold buffering after the clock tree. `make sta` exits 1 because of it. Options for the owner: accept it as a known pre-layout limitation (documented in `synth/README.md`), or ask for a netlist-level delay-cell insertion step.
  - **TASKS.md OpenSTA line left unticked** until the owner decides the two [OWNER-APPROVED, D-041] items above (pipeline-stage wording, open removal violation). *(Owner decided at Gate M4: D-041, item 4. The line is now ticked.)*
- Why: the owner asked for an honest timing report at the SPEC clocks with documented constraints.
- Effect: synth/neurogate_top.sdc, synth/sta.tcl, synth/run_sta.sh, synth/abc.constr, synth/synth_nangate45.ys, Makefile (`make sta`), synth/reports/sta.txt, sta_wireload.txt, sta_before.txt, cells.txt, gls.log, synth/README.md, docs/cdc.md, STATUS.md.

## D-039: M4 session 6 instructions
- Date: 2026-11-14
- Source: OWNER (session instruction for M4 session 6, the final M4 fix session)
- Instruction: Branch `m4-s6` off `m4-s5`. Fix the independent M4 review: (must fix) the stress monitor loses protocol errors after `clear_beats()`, with a demonstration that a fault planted after it fails the test; do not approve D-032 or D-038 (they stay [PROPOSED] for the owner at Gate M4) and make SPEC, TASKS, STATUS, DECISIONS and `synth/README.md` say the same; leave the timing line unticked. (should fix) review the three D-032 gates in `docs/cdc.md`; use `set_max_delay -datapath_only` if supported; make unconstrained endpoints fail `make sta`; qualify or strengthen the mixed overflow check; keep durable evidence for fault demonstrations. Nits in `docs/stories.md`. Keep the 3-minute test in `make test`. No unrelated RTL change. Open ONE non-draft PR `m4-s6` -> `main` titled "M4: full chip passes all checks", superseding PRs #32-#36.
- Effect: this session's scope; D-040.

## D-040: M4 review fixes (M4 session 6)
- Date: 2026-11-14
- Source: BUILDER (owner may override). Items marked [PROPOSED] need owner approval.
- Decision:
  - **No RTL change.** Only testbench, scripts, SDC comments and docs changed.
  - **Monitor fix.** `Chip.clear_beats()` now clears `self.errors` in place (`tb/top_common.py`), so the running monitor and the test assertions use one list. New regression test `test_monitor_after_clear` forces a protocol fault (result `tvalid` dropped during a stall) after a clear and requires it to be reported. Durable evidence: `tb/evidence/clear_beats_monitor.log` (old code fails the test; fixed code passes; the same fault planted in `test_overflow` phase B fails that test; all 7 stress tests pass after the plant is reverted).
  - *(Superseded by D-041: the owner approved these items at Gate M4.)* **Pending items stay pending.** D-032 (three small gates in the top level) and D-038 (buffering instead of a pipeline stage, open removal violation, 20% pin budget) remain [PROPOSED]. SPEC section 2 and section 10 (Timing row), TASKS (OpenSTA line, unticked) and `synth/README.md` now carry a short "pending owner decision at Gate M4" note. The frozen requirement text itself is not changed.
  - **`-datapath_only` is not supported** by this OpenSTA build ("not a known keyword or flag"), so the SDC keeps plain `set_max_delay`; the SDC comment now says why this is equivalent with ideal clocks and what to re-check after place and route. No timing number changed (reports re-run, identical).
  - **`make sta` fails on constraint problems**: any text under `check_setup`, any OpenSTA warning, or a summary line with "no path" gives exit 1. Shown with a planted fault (all output delays removed): `synth/reports/sta_constraint_check.log`. The known removal violation is still reported as before.
  - **Mixed overflow phase** still uses the chip's dropped and overflow counts for the exact split (a lost entry carries no kind, so an outside reference cannot split them). Added an independent lower bound (overflow >= model-classified frames without a beat) and an accounting check against the model's drop count; the claim is worded as sum plus lower bound, exact only in the all-kept phase.
  - **Evidence.** The gate-level fault demonstration was repeated and its output committed (`synth/reports/gls_fault.log`). The seven manual RTL plants of session 3 were not repeated; STATUS says their logs were not kept.
  - **Stories.** Scapy sentence, validation-only threshold, IP explained, one-sentence intro per epic. The file grew (1,026 to about 1,160 words) because of these requested additions.
- Why: close the independent M4 review without pre-empting owner decisions.
- Effect: tb/top_common.py, tb/neurogate_top_stress_checks.py, tb/evidence/, synth/run_sta.sh, synth/neurogate_top.sdc, synth/reports/sta_constraint_check.log, synth/reports/gls_fault.log, SPEC.md (notes only), TASKS.md (note only), docs/cdc.md, docs/stories.md, synth/README.md, STATUS.md.

## D-041: Gate M4 decisions (owner) and M5 authorization
- Date: 2026-11-14
- Source: OWNER (Gate M4 decision, relayed in the M5 session 1 instruction; approval given 2026-11-14 3:42 PM ET)
- Instruction: the owner approved all eight items.
  1. Gate M4 passes.
  2. Only PR #37 (`m4-s6` into `main`) was merged. PRs #32 to #36 are closed unmerged as superseded.
  3. The three small gates in the top level (D-032) are accepted. SPEC wording "no logic beyond reset synchronizers" is updated to allow them. The "pending owner decision" notes in SPEC, TASKS, STATUS, `synth/README.md` (and `docs/cdc.md`) are removed.
  4. Timing (D-038): buffering instead of a pipeline stage is accepted. The timing task in TASKS is ticked with this wording: setup met on both clocks (estimate); the 0.108 ns reset removal miss is reported as "not done, because there is no place-and-route step"; the 20% pin timing budget is a guess.
  5. Reserved entry kind 3 and class 3 only add to the overflow count; one register-bus transaction at a time per direction (D-030).
  6. The full-traffic end-to-end test (about 3 minutes) moves from `make test` to `make test-full`; `make test` keeps a shorter frame count. `make test` must still catch a real mismatch, shown with a planted fault that fails and is then reverted.
  7. The bad-packet set is added to the gate-level run (`make gls`) and real results are reported (D-036).
  8. M5 is authorized.
- Effect: D-030, D-032, D-034 (item on the full test), D-036 and D-038 items tagged [PROPOSED] are now tagged [OWNER-APPROVED, D-041]. SPEC section 2 wording and the Timing row note, TASKS (OpenSTA line ticked, wording above), `synth/README.md`, `docs/cdc.md` and STATUS updated. Makefile: `make test-full` also runs the 9000-frame chip test (`FULL_PER_SCENARIO=300`); `make test` runs it with 900 frames. Planted-fault evidence: `tb/evidence/short_full_test_planted_fault.log`. `make gls` also runs the bad-packet set (`tb/gls_run.py bad`). M5 session 1 builds the README, module notes and limitations; the results page waits for a later session. D-039 and D-040 are history and keep their original wording.

## D-042: M5 session 2 instructions (results page)
- Date: 2026-11-14
- Source: OWNER (session instruction for M5 session 2)
- Instruction: Branch `m5-s2` off `m5-s1`. Build the static results page `docs/index.html`; the automated builder designs the UI/UX (no separate designer). Audience: an engineer with basic EE background arriving from GitHub. One self-contained page (inline CSS, no external CDNs or trackers, works from disk and on GitHub Pages), mobile-friendly and accessible. Content: context first, then accuracy, confusion matrix, margin-vs-accuracy chart, a waveform from a real simulation dump (say which test and signals), synthesis table (Nangate45 and Artix-7 estimate), timing table (setup met on both clocks as an estimate; the 0.108 ns removal miss "not done, because there is no place-and-route step"; 20% pin budget is a guess), throughput, and a short limitations box linking to the README. Every number comes from a committed result file and is labelled with its source and as simulated or estimate; no hand-typed numbers: a generator script writes the page. Add a check that fails when the page differs from its sources, and show it failing on a hand-edited number. README: spell out FPGA, VLAN, SYN, PRD, TCP, UDP (and other acronyms) on first use. No RTL change. Run lint, compile-check, cdc-check, test, test-full, synth, gls, sta and the new check. Draft PR `m5-s2` -> `m5-s1`, titled "M5 session 2: results page".
- Effect: this session's scope; D-043.

## D-043: Results page design and how it is kept honest (M5 session 2)
- Date: 2026-11-14
- Source: BUILDER (owner may override)
- Decision:
  - **No RTL change.** One testbench helper changed: `tb/cocotb_run.py` records an FST dump when `NG_WAVES=1` (default off, so `make test` is unchanged).
  - **Generator, not hand-written HTML.** `scripts/build_results_page.py` (standard library only) reads `docs/results/metrics.json`, `synth/reports/cells.txt`, `artix7_cells.txt`, `sta.txt` (SUMMARY block), `gls.log`, `gls_bad.log`, `synth/neurogate_top.sdc` (clock periods, 20% pin fraction), `docs/results/throughput_run.log` and `docs/results/wave_throughput.vcd`, and writes `docs/index.html`. Charts and waveforms are inline SVG drawn by the script; no PNG, no JavaScript, no web fonts. Every table and figure carries a "simulated" or "estimate" badge and links to its source file.
  - **Check.** `make results-check` rebuilds the page in memory and fails if `docs/index.html` differs (prints the first differing lines). It needs no simulator, so it is fast; it is part of `make all` but not of `make test` (which runs only `tb/test_*.py`). Evidence: `tb/evidence/results_check_hand_edit.log` (two hand edits fail, revert passes).
  - **Throughput source.** The throughput numbers were only in an uncommitted log (`results/`, gitignored) and in prose in `synth/README.md`. `scripts/capture_waveform.py` (`make results-capture`) re-runs the same throughput test with the wave dump on and commits its result lines (with command, seed and commit) as `docs/results/throughput_run.log`. Same numbers as the M4 run (179 core clocks, 1117 k packets/s, 187 clocks first-frame latency).
  - **Waveform.** From the same run: `fst2vcd`, then 12 top-level signals kept unchanged in `docs/results/wave_throughput.vcd` (131 kB, committed through a `.gitignore` exception; other dumps stay ignored). The page draws two diagrams from it: the whole 14-frame run (input valid, last beat, queue write, core takes an entry, result handshakes, class of each result) and a 16-clock zoom on the first result beat (core clock, result valid/ready, decoded result word; stretches with valid low are grey and say "not valid"). One-clock pulses are drawn at least 1.6 px wide so they stay visible; the caption says so. The script measures the result spacing and first-frame latency from the dump and stops with an error if they differ from the test's log (they agree: 179 and 187 core clocks).
  - **Left off the page:** "0 mismatches on all 8,347 test packets" (the `make test-full` replay) because its log is not committed. The README still states it.
  - **Links** to source files and to the README limitations use `https://github.com/bavafa/neurogate/blob/main/...`, so they work from GitHub Pages and from disk (online); they only resolve once the repo is public and merged to `main`.
  - **README growth.** Spelling out acronyms and the page link added about 80 words (1,459 to 1,537 by `wc -w`). This is an addition asked for by the PM, not a rewrite.
- Why: the owner asked for a results page where every number is traceable and cannot drift from the result files.
- Effect: scripts/build_results_page.py, scripts/capture_waveform.py, tb/cocotb_run.py, Makefile (`results-capture`, `results-page`, `results-check`, `all`), .gitignore, docs/index.html, docs/results/throughput_run.log, docs/results/wave_throughput.vcd, tb/evidence/results_check_hand_edit.log, README.md, TASKS.md, STATUS.md.

## D-044: M5 session 3 instructions (replay evidence, clean-clone check, personal-info scan)
- Date: 2026-11-14
- Source: OWNER (session instruction for M5 session 3)
- Instruction: Branch `m5-s3` off `m5-s2`. (1) Commit durable evidence for "0 mismatches on all 8,347 test packets" (the full test-split replay in `make test-full`) as a small log under `docs/results/`, and have the results page generator read and show it, labelled simulated with its source; `make results-check` keeps passing. (2) Clean-clone check: clone the pushed branch into a fresh folder outside the repo, follow the README setup exactly, fix the README or toolchain doc if a step is missing or wrong and redo from a fresh clone, run `make test`, record what was done and the result in STATUS and in a short committed log with commands and exit codes and no personal paths. (3) Personal-information scan of every tracked file and the full git history (home paths, names, e-mail addresses, keys, machine names, private or PM notes, chat transcripts); confirm `private/` is ignored and never committed; fix tracked files; do not rewrite history; list anything in history in STATUS as an owner decision; report the commit author identity. No RTL change. Draft PR `m5-s3` -> `m5-s2`.
- Effect: this session's scope; D-045.

## D-045: Replay log, path stripping, README setup fix (M5 session 3)
- Date: 2026-11-14
- Source: BUILDER (owner may override)
- Decision:
  - **No RTL change.** No testbench change.
  - **Replay evidence.** `scripts/save_replay_log.py` (`make results-replay-log`, run after `make test-full`) copies only the result lines of the split replay (two runs, 8,347 rows each, mismatches, handshake errors, test summary, exit code) from the ignored `results/<commit>/full_split.log` into `docs/results/full_split_replay.log`. It refuses to write if the run did not exit 0 or the three result lines are missing; it never edits a number. The page generator reads this file and adds a section "Chip vs Python model, every test packet" (table, *simulated* badge, source link). The 9,000-frame whole-chip run is not added to the page: only the replay was asked for.
  - **Machine paths.** `scripts/strip_paths.sh` replaces the checkout, tool and home directories with `<repo>`, `<tools>`, `<home>`; `make gls` pipes its two logs through it. Existing tracked logs (`synth/reports/gls.log`, `gls_bad.log`, `tb/evidence/clear_beats_monitor.log`) and two DECISIONS lines were edited to use the same placeholders; only the path text changed. `synth/run_sta.sh` no longer hard-codes a home directory (uses `sta` from `PATH`, else `$NG_TOOLS/opensta/bin/sta`). STATUS and DECISIONS no longer point at `private/pm/...`; they say "kept outside the repository".
  - **README setup.** The clean-clone attempt found two gaps: the quick start never created `.venv`, and `make toolchain` came before the cell-library download and so exited non-zero on a fresh clone. The quick start now creates the environment first, fetches the library before `make toolchain`, and names the tool prerequisites. `pytest` is not in the pip line and is not needed: it is installed as a dependency of cocotb (seen in the pip output).
  - **Not changed, owner decision:** commit author identity and anything found in git history (listed in STATUS).
- Why: the owner asked for traceable evidence for the 8,347-packet claim and a check that the repo works for a stranger and carries no private data.
- Effect: scripts/save_replay_log.py, scripts/strip_paths.sh, scripts/build_results_page.py, Makefile, synth/run_sta.sh, README.md, docs/index.html, docs/results/full_split_replay.log, docs/results/clean_clone.log, DECISIONS.md, STATUS.md, TASKS.md, tracked logs listed above.

## D-046: Fixes from the independent M5 review (M5 session 4)
- Date: 2026-11-14
- Source: PM instruction (owner may override)
- Decision:
  - **No RTL logic change.** Two RTL comments only: `[PROPOSED]` in `rtl/axil_regs.sv` and `rtl/stats_counters.sv` now says owner-approved in D-041.
  - **Testbench message change (one line).** `tb/full_split_checks.py` now prints the gap and stall rates in its result lines, so the results page reads them from the committed replay log instead of typing 30% and 50%. The page also reads the gate-level smoke frame count (120) from `synth/reports/gls.log`. Test checks are unchanged.
  - **Replay log provenance.** `make test-full` writes whether `rtl/` and `tb/` were modified at run time as the first line of its log; `scripts/save_replay_log.py` takes the seed and that line from the run and refuses to write without them.
  - **Notes match RTL.** Parser note: a cut-off frame with at least 14 TCP or 4 UDP header bytes is kept and parsed. Feature builder note and README: ports are counted as occupied buckets of a 64-way hash (approximation), SYN-without-ACK only. Stats note: overflow-count saturation and soft reset with entries in flight. README: timing step is expected to exit non-zero. Scripts note: replay-log and path helpers. PRD stale line fixed. Acronyms spelled out.
  - **Not changed, owner decisions:** git history, commit author identity, process-tool mentions (listed in STATUS).
- Why: independent M5 review must-fix items 1 and 2, plus its should-fix items and nits.
- Effect: scripts/build_results_page.py, scripts/save_replay_log.py, tb/full_split_checks.py, Makefile, README.md, docs/PRD.md, docs/modules/*.md, docs/index.html, docs/results/full_split_replay.log, rtl comments, STATUS.md, DECISIONS.md.
