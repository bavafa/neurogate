# TASKS.md

Owner of this file: the project manager. The builder ticks boxes only.
Budget: about 8 to 10 hours of owner time over a weekend. Builder time is separate.
Rule for every milestone: builder finishes, independent reviews into `reviews/Mx-review.md`, builder closes all *must fix*, project manager runs the gate, owner approves, tag `Mx`.

---

## M1: Spec and scaffolding (owner time ~1.5 h)
- [x] Walk through SPEC.md section 13 with the owner. Record each answer in DECISIONS.md (D-001 onward)
- [x] Freeze SPEC.md (remove `[PROPOSED]` tags, or keep them with the decided value)
- [x] `docs/regmap.md`: final AXI-Lite register map
- [x] Repo skeleton: folders, `Makefile` with `test`, `lint`, `synth`, `sta`, `gls` targets (stubs OK)
- [x] Toolchain check script: prints versions of Verilator, Icarus, Yosys, OpenSTA, cocotb, Python, Scapy. Records them in STATUS.md
- [x] Decide and record the liberty file for OpenSTA
- [x] Write `README.md` skeleton (what it is, block diagram, status table with all rows "not started")

**Gate M1:** SPEC frozen, regmap written, `make lint` and `make test` run (even if empty), toolchain versions recorded, every open question answered in DECISIONS.md.

---

## M2: Parsers and plumbing (owner time ~2 h)
- [x] `model/traffic_gen.py`: Scapy generator for the 3 classes, fixed seeds, writes `.pcap` + label CSV
- [x] `model/features.py`: feature model (section 5), bit-exact integer arithmetic
- [x] `rtl/eth_parser.sv` + `tb/test_eth_parser.py` (VLAN, short frame, bad `tuser`)
- [x] `rtl/l3l4_parser.sv` + `tb/test_l3l4_parser.py` (IP options, UDP, fragments, non-IPv4, truncated)
- [x] `rtl/skid_fifo.sv` + test with random `tready`
- [x] `rtl/async_fifo.sv` + test with several clock ratios
- [x] Parser output cross-checked against Scapy-parsed fields on the generated traffic (simulated: 9,600 frames; all fields compared on 8,900 kept frames; keep/drop only on 6 kept frames where Scapy decodes no TCP/UDP layer; 694 dropped by both)
- [x] Lint clean for all new RTL

**Gate M2:** all M2 tests pass, lint clean, no packet loss or reorder under backpressure, parsers agree with Scapy on the generated frames (9,600 simulated: all fields on 8,900 kept frames, keep/drop only on 6 kept frames where Scapy decodes no TCP/UDP layer, 694 dropped by both).

---

## M3: Neural net matches Python (owner time ~2.5 h)
- [x] `model/train.py`: train the 8-16-3 net on the training split, fixed seed
- [x] `model/quantize.py`: int8 quantization, writes `weights.json` and ROM `.hex`
- [x] `model/golden.py`: integer-only inference (the source of truth for the RTL)
- [x] Evaluate on unseen test split: accuracy, per-class precision/recall, confusion matrix (float and int8). Save to `docs/results/`
- [x] Margin vs. measured accuracy: bin the confidence margin on a validation split carved from the training data, pick the default `CONF_THRESH` from it, then report % correct per bin (table + chart) once on the unseen test set with no further tuning (see D-002, D-008)
- [x] `rtl/feature_builder.sv` + test vs `model/features.py`, bit for bit
- [x] `rtl/mlp_core.sv` (ROM + 1 MAC/cycle) + test vs golden, bit for bit, including saturation corner cases
- [x] `rtl/decision_unit.sv` + test (ties, threshold edge)
- [x] Record cycles per inference from simulation

**Gate M3:** int8 accuracy, confusion matrix and margin-vs-accuracy table reported on unseen data (with the synthetic-data caveat), RTL equals golden with zero mismatches (simulated) on both vector sets: the 2,028-row file `data/vectors/mlp_vectors.csv` (`mlp_core` and `decision_unit`) and every classified packet of the unseen test split, 8,347 rows (`make test-full`, `mlp_core` + `decision_unit`); lint clean.

---

## M4: Full chip passes all checks (owner time ~2.5 h)
- [x] `rtl/stats_counters.sv` + test
- [x] `rtl/axil_regs.sv` + test (matches `docs/regmap.md`)
- [x] `rtl/neurogate_top.sv` wiring everything, two clocks
- [x] Top-level cocotb: end-to-end on full generated traffic, compare to golden bit for bit
- [x] Top-level bad-packet, backpressure and reset tests
- [x] CDC review: list every crossing, method, and result (honest label: not a commercial sign-off)
- [x] Verilator lint: zero warnings or listed waivers
- [x] Yosys synthesis script and report in `synth/`
- [x] Gate-level simulation with the same vectors, outputs equal RTL
- [x] OpenSTA timing report. If it fails at the target clock, add a pipeline stage, re-run, and keep before/after numbers *(setup met on both clocks (estimate); the 0.108 ns reset removal miss is reported as "not done, because there is no place-and-route step"; the 20% pin timing budget is a guess; owner-approved at Gate M4, D-041)*
- [x] Artix-7 resource estimate (`synth_xilinx`), labeled as estimate
- [x] Throughput estimate from cycle counts, labeled as simulated

**Gate M4:** every row in the SPEC section 10 table has a result or an honest "not done, because...". No *must fix* open.

---

## M5: Docs and results page (owner time ~1.5 h)
- [x] README: context first, then detail, for a reader with basic EE background. Includes "Development approach" section (AI tools under the owner's direction)
- [x] Per-module notes in `docs/`
- [x] Static results page (`docs/index.html`): accuracy, confusion matrix, margin-vs-accuracy chart, waveform screenshot, synthesis and timing tables, throughput. Every number labeled with its source
- [x] Limitations section: synthetic data, simulation only, CDC method, what was not done
- [x] Final clean-clone check: fresh checkout, follow README, `make test` passes
- [x] Scan repo for personal info and private notes before going public (tracked files fixed; history findings are owner decisions, see STATUS)

**Gate M5:** clean clone works, results page matches the actual result files, limitations written, owner approves making the repo public.

---

## Stretch (only after M5)
- CIC-IDS2017 features and retrain
- Formal proofs on the FIFOs
- LUT-based softmax module (probability output) with calibration check
