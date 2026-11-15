# STATUS.md (read first)

Builder: update this at the end of every session. Keep it short and true.

| Field | Value |
|---|---|
| Current milestone | M5 session 4 (final M5 fix session, independent M5 review must-fix and should-fix items). Gate M4 passed (D-041). M5 left: Gate M5 and owner decisions listed below |
| Active builder session | none |
| Last updated | 2026-11-15 (M5 session 4b, branch `m5-s4`, off `m5-s3`; one non-draft PR holds all M5 work) |
| Last command run and result | `SEED=12345 make lint compile-check cdc-check test test-full synth gls sta` on commit 65476f0ea7e6 (temporary run log `m5s4b.log`, run in separate foreground-polled targets): `lint` exit 0; `compile-check` exit 0; `cdc-check` exit 0; `test` exit 0 (pytest groups 47, 17, 6 passed; whole-chip 900 frames: 840 beats, 0 mismatches); `test-full` exit 0 (split replay 2 passed, 8,347 rows each, 0 mismatches; 9000-frame chip test 8,349 beats, 0 mismatches; *simulated*); `synth` exit 0; `gls` exit 0 (smoke 110 of 110 beats, bad-packet 176 of 176, 0 mismatches); `sta`: make exit 2 from the accepted removal miss (-0.108 ns main report, -0.115 ns in the wire-load report, setup met, *estimate*). Then `make results-replay-log`, `make results-page`, `make results-check`: PASS. Hand edits of replay gap, replay stall and smoke frame count each make `results-check` FAIL (log `tb/evidence/results_check_log_values_hand_edit.log`); reverted, PASS. |

## Done
- Earlier M5 cleanup: replaced process-tool names and machine paths with generic wording, renamed the committed review, and moved the contributor rules to the ignored private folder. No RTL logic, testbench logic or result values changed. The simulation runner now reads the existing NG_TOOLS setting for its fallback tool location.
  - Validation at that cleanup: lint and compile-check passed; the combined `make lint compile-check results-check` command exited 2. Both results-page and results-check failed because the committed replay log lacked the gap/stall fields required by the existing generator. Result files were preserved.
  - At that cleanup, Git metadata was mounted read-only; untracking, staging and committing were blocked, and the contributor-rules move and review rename remained unstaged.
- M5 session 4 and 4b (automated builder medium; branch `m5-s4` off `m5-s3`; decisions D-046). **No RTL logic change** (two comments only).
  - Must fix 1: the results page reads the replay gap and stall rates from the replay log (the testbench now prints them) and the smoke frame count from the gate-level log. Proof that `make results-check` catches a hand edit of each: `tb/evidence/results_check_log_values_hand_edit.log`.
  - Must fix 2: parser note (cut-off frame kept if at least 14 TCP or 4 UDP header bytes), feature builder note and README (64-bucket hash, SYN without ACK only) now match the RTL.
  - Should fix and nits: replay-log saver records seed and rtl/tb state from the run (`make test-full` writes the state on the first log line); stats note has the two exceptions; README says the timing step exits non-zero by design; `[PROPOSED]` removed from two RTL comments; scripts note lists the replay-log and path helpers; stale PRD line fixed; acronyms spelled out.
  - Replay log was saved from a run on commit 65476f0ea7e6 with a `-dirty` suffix: tracked files other than rtl/ and tb/ were modified at that time (the log says rtl/ and tb/ were unmodified). `synth/reports/gls*.log` differ from before only in wall-clock timing.
  - Not done: history and identity items stay owner decisions; no second-reader check of the README.
- M5 session 3 (automated builder medium; branch `m5-s3` off `m5-s2`; instruction D-044, decisions D-045). **No RTL change, no testbench change.**
  - Session 3 run record (2026-11-14): `SEED=12345 make -k lint compile-check cdc-check test test-full synth gls sta results-check` on commit 039ebdf387c7 (tree clean at start; log `m5s3_run2.log`): `lint: exit 0`; `compile-check` no error; `cdc_check: 0 violation(s): PASS`; `test: overall exit 0` (whole-chip 900 frames: 840 beats, 0 mismatches); `test-full`: split replay 2 passed (8,347 rows each, 0 mismatches), 9000-frame chip test 8,349 beats, 0 mismatches, both exit 0; `synth: yosys exit 0`; `gls`: smoke and bad-packet both exit 0 (0 mismatches); `sta`: exit 1 from the known removal violation (-0.108 ns, setup met, *estimate*; chain exit 2); `results-check` in that chain: **FAIL**, because I refreshed `docs/results/full_split_replay.log` (from this run's own split replay) after the page was built; after `make results-page` it is **PASS** (rerun, exit 0).
  - **1. Replay evidence.** `docs/results/full_split_replay.log` holds the result lines of the `make test-full` split replay (*simulated*, seed 12345, commit 039ebdf387c7): back to back and with gaps/stalls, each 8,347 rows compared, 0 mismatches, 0 handshake errors, 2 passed, exit 0. `make results-replay-log` (`scripts/save_replay_log.py`) copies it from the ignored `results/<commit>/full_split.log`. The page has a new section "Chip vs Python model, every test packet" with source link; `make results-check` PASS.
  - **2. Clean-clone check** (log `docs/results/clean_clone.log`). Fresh `git clone` of the pushed branch into an empty temp folder outside the repo, README quick start as written. First attempt (commit df7094d) found two README gaps: no step created `.venv`, and `make toolchain` was listed before the cell-library download (exit 2: "liberty MISSING"). Fixed in README (also names the tool prerequisites), pushed (5e168c4), redone from a **fresh clone** of 5e168c4: venv + `pip install cocotb scapy numpy` exit 0 (pytest arrives as a cocotb dependency, so the pip line needs no change); `lib/fetch_nangate45.sh` exit 0; `make toolchain` exit 0; `make lint compile-check cdc-check test`: lint exit 0, cdc_check 0 violations, every suite "0 failed", pytest groups 47, 17 and 6 passed, `test: overall exit 0`, command exit 0. **Not repeated in the clone:** `make test-full`, `synth`, `gls`, `sta` (only `make test` was asked for). The tools (make, Verilator, Icarus, Yosys, OpenSTA) were already installed on this machine in the tool install folder; the clone does not install them, so this proves the repo steps, not the tool installation.
  - **3. Personal-info scan** (tracked files at HEAD and `git log --all -p`, all 65 commits on every branch that existed before this session's own commits, 25 remote branches, no tags).
    - Home or checkout paths in tracked files (fixed): `synth/reports/gls.log`, `gls_bad.log`, `tb/evidence/clear_beats_monitor.log`, `synth/run_sta.sh` (hard-coded tool path), two lines in DECISIONS.md, one in STATUS.md. Now `<repo>`/`<tools>`; `make gls` strips paths from its logs (`scripts/strip_paths.sh`). A home and checkout path scan of tracked files (page excluded; it has none) now finds nothing.
    - `private/` is in `.gitignore` (line 1; `git check-ignore` confirms) and **no file under `private/` or `results/` was ever added in any commit on any branch** (`git log --all --diff-filter=A` and `git rev-list --objects --all` checked).
    - No e-mail addresses in any tracked file or added line in history, other than the GitHub noreply address in commit metadata. No API keys, tokens or private-key blocks (patterns: ghp_, github_pat_, sk-, AKIA, BEGIN ... PRIVATE KEY). No absolute home paths. No chat transcripts or `.env`/`.pem` files.
    - Process-tool references identified during the scan were scheduled for generic wording in the cleanup.
    - **Owner decisions (history is not rewritten):**
      1. Commit author identity: 58 commits are authored and committed as `bavafa <52782802+bavafa@users.noreply.github.com>` (GitHub noreply address, not a personal mailbox); 7 merge commits (PRs #6, #21 to #27, #31, #37) are authored as `Meysam Bavafa <52782802+bavafa@users.noreply.github.com>` and committed by `GitHub <noreply@github.com>`. So the full name appears in history. 55 commit messages carry contributor trailers. History changes require owner approval.
      2. History contains an absolute home-directory tool path (the build machine's login name; the exact text is in the diffs of these commits and in the PR description, not repeated here) in: 0562340 (STATUS.md), 18e2bbc (`synth/run_sta.sh`), 0594360 (`synth/reports/gls.log`), bc1b0ed (`gls_bad.log`), 637707e and e887bec (DECISIONS.md). An absolute checkout path is in 0562340 (STATUS.md) and 2332d26 (`tb/evidence/clear_beats_monitor.log`). It is a user-space tool directory on a build machine, not a personal path, but it is a machine name. Rewriting history needs owner approval.
      3. History contains earlier audience wording (commits 3f0a9a1, 7524200) and private-process wording in the contributor rules (7a35cb0). History changes require owner approval.
      4. History contains `private/pm/...` file-path mentions (commits 01f9625, 0562340, 0c1abde, 7c337a4, 85a0163, f001957 and others in STATUS.md); only paths, no content from `private/`. HEAD now says "kept outside the repository".
      5. Local clone only: untracked `private/` exists in the working copy (ignored). Build-machine logs in `results/` are ignored too and contain absolute paths; they are not tracked.
  - Not done: second-reader check of the README (the new quick-start lines are only checked by the clean clone); tool installation on a machine without the tool install folder was not tested; the results page in a browser after the new section was not re-rendered (the section is plain table markup like the others; only the generator and `results-check` ran).

- M5 session 2 (automated builder medium; branch `m5-s2` off `m5-s1`; instruction D-042, decisions D-043). **No RTL change.**
  - `docs/index.html`: one self-contained page (inline CSS and SVG, no scripts, no external requests), written by `scripts/build_results_page.py` from committed files only. Sections: context and a "simulated or estimate, synthetic traffic" notice; four summary tiles; accuracy (precision/recall); confusion matrix (real table, shaded by row share); margin-vs-accuracy bar chart (with a data table under "Show the chart data"); two waveform diagrams drawn from a real dump; synthesis (Nangate45, Artix-7); timing (owner wording for the removal miss and the 20% pin guess); throughput; gate-level; limitations box linking to the README. Each table and figure has a simulated/estimate badge and links its source file.
  - Waveform: `make results-capture` (`scripts/capture_waveform.py`) re-ran `tb/test_neurogate_top_throughput.py` (seed 12345, Icarus, 14 frames) with `NG_WAVES=1` on commit 0b753fa (rtl/ and tb/ unmodified). Kept: `docs/results/throughput_run.log` (result lines) and `docs/results/wave_throughput.vcd` (12 top-level signals: net_clk, s_axis_tvalid/tready/tlast, f_tvalid/f_tready, core_clk, a_tvalid/a_tready, m_axis_res_tvalid/tready/tdata). The page script measures 179 core clocks between results and 187 core clocks first-frame latency from the dump and checks them against the log (agree). Same numbers as the M4 run (*simulated*).
  - Check: `make results-check` rebuilds the page and fails on any difference. Shown in `tb/evidence/results_check_hand_edit.log`: committed page PASS; accuracy tile 95.50% -> 96.50% FAIL (exit 2 from make); removal slack -0.108 -> -0.018 FAIL; after `git checkout docs/index.html` PASS.
  - Rendered with headless Chrome at 1280 px and 400 px, light and dark scheme, and looked at: fixed a label collision in the chart, odd zoom-axis ticks, dark-mode cell contrast, small waveform text on phones (diagrams now scroll sideways with a hint). Not checked with a screen reader or an automated accessibility tool.
  - README: FPGA, SYN, TCP, UDP, IPv4, VLAN, PRD and AI spelled out on first use; link to the results page; status and limitation lines no longer say the page is to do. `wc -w` 1,459 -> 1,537 (additions, not a rewrite).
  - `docs/modules/scripts.md`: two new entries for the page scripts. TASKS: results-page line ticked (the "waveform screenshot" is an SVG drawing from the dump, not a screen capture of a viewer).
  - Not on the page: the 0-mismatch replay of 8,347 packets (`make test-full`), because its log is not committed (D-043).
- M5 session 1 (automated builder medium; branch `m5-s1`; owner instruction in D-041). **No RTL change.**
  - Part A: D-041 logged (owner approved all 8 Gate M4 items on 2026-11-14 3:42 PM ET). D-030, D-032, D-034, D-036, D-038 items now tagged owner-approved. SPEC section 2 and Timing row reworded; "pending owner decision" notes removed from SPEC, TASKS, `synth/README.md`, `docs/cdc.md`. TASKS: OpenSTA line ticked with the owner's wording (setup met on both clocks (estimate); 0.108 ns removal miss "not done, because there is no place-and-route step"; 20% pin budget is a guess).
  - Test split: `make test` runs `tb/test_neurogate_top_full.py` on 900 frames (`FULL_PER_SCENARIO`, default 30; 840 classified, 60 dropped, about 19 s). `make test-full` also runs it on 9000 frames (`FULL_PER_SCENARIO=300`, about 3 minutes) after the split replay. Planted fault (`conf_next <= conf_thresh` in `rtl/decision_unit.sv`): 900-frame run FAILED with 43 beat mismatches; reverted; log `tb/evidence/short_full_test_planted_fault.log` (*simulated*, seed 12345).
  - `make gls` now also runs the 480-frame bad-packet set on the netlist (`python3 tb/gls_run.py bad`, `tb/gls_bad_checks.py` reuses `test_bad_packets`): 176 of 176 beats, 304 dropped as the model says, 0 mismatches, 0 protocol errors (*simulated*). No fault was planted in the netlist for this run (smoke-run fault log unchanged).
  - Part B: README rewritten (context first, results table with labels, limitations, development approach); `docs/modules/` (one note per block plus four script groups, index in `docs/modules/README.md`).
  - Not done on purpose: results page `docs/index.html`, clean-clone check, personal-info scan (TASKS M5 lines stay unticked). independent M4 review: the file is outside the repository, not in `reviews/`; I did not re-open it.
  - README and module notes have not been checked by a second reader; the words "plain English, no unexplained acronyms" were applied by hand, not by a tool.
- M4 session 6 (automated builder, stacked on `m4-s5`; instructions D-039, decisions D-040). **No RTL change.**
  - Must fix 1: `Chip.clear_beats()` now clears the error list in place; new test `test_monitor_after_clear`; committed log `tb/evidence/clear_beats_monitor.log` (old code: test FAIL; fixed: PASS; fault planted in `test_overflow` after a clear: FAIL with "valid dropped during a stall"; plant reverted: 7 of 7 PASS, *simulated*, seed 12345).
  - Must fix 2: D-032 and D-038 stay **[PROPOSED], pending owner decision at Gate M4**. Notes added in SPEC (section 2 and the Timing row of section 10, requirement text unchanged), TASKS (OpenSTA line stays unticked), `synth/README.md`, DECISIONS (D-040), this file.
  - `docs/cdc.md`: the three D-032 gates reviewed; all inputs and consumers are on the core clock (checked in `rtl/neurogate_top.sv`), so no crossing. The result `tready` pin is assumed synchronous to the core clock (interface definition, not checkable).
  - `synth/neurogate_top.sdc`: this OpenSTA build rejects `-datapath_only`; plain `set_max_delay` kept and the comment explains it. Reports re-run: numbers identical.
  - `synth/run_sta.sh`: check_setup text, OpenSTA warnings and "no path" summaries now fail the run. Planted-fault proof: `synth/reports/sta_constraint_check.log`. The known removal violation (-0.108 ns, *estimate*) still makes `make sta` exit 1.
  - Mixed overflow phase: independent lower bound added; the exact dropped/overflow split is not claimed there (uses the chip's own counts); exact only in the all-kept phase.
  - Evidence: gate-level fault demonstration repeated, log `synth/reports/gls_fault.log`. The seven manual RTL plants listed under session 3 have no kept logs; the claim is reworded there.
  - `docs/stories.md`: Scapy wording, validation-only threshold, IP explained, epic intros (file grew by about 135 words).
- M4 session 5 (automated builder, stacked on `m4-s4`; instructions D-037, decisions D-038). **No RTL change.** Details in `synth/README.md` ("Timing").
  - New: `synth/neurogate_top.sdc` (every constraint explained in a comment), `synth/sta.tcl`, `synth/run_sta.sh`, `make sta`, `synth/abc.constr`. Reports committed: `synth/reports/sta.txt` (main, no wire load), `sta_wireload.txt` (library default wire-load model), `sta_before.txt` (the M4 session 4 netlist under the same constraints).
  - Constraints: `net_clk` 6.4 ns, `core_clk` 5.0 ns (the AXI-Lite bus runs on `core_clk`; there is no separate register clock), ideal clocks. Crossings: `set_max_delay 5.0` both ways, hold not checked across clocks. `rst_n` false path. Pins: 20% of the period outside the chip ([PROPOSED]).
  - Before (area-only netlist), *estimate*, no wire load: `core_clk` setup **-6.186 ns (fail)**, TNS -465.721 ns; `net_clk` setup 1.802 ns. Cause: FIFO read-address bit `u_afifo.rd_bin_q[1]` drove 573 inputs, an inverter after it 419 more, unbuffered (9.85 ns in two cells). (An early guess in this session that it was a reset net was wrong; checked against the netlist.)
  - Fix: ABC mapping with `-D 5000 -constr synth/abc.constr` (delay target, buffering, sizing) in `synth/synth_nangate45.ys`; no pipeline stage, latency unchanged ([PROPOSED], D-038). Cells 17,582 -> 18,907; area 33,821 -> 34,866 um^2; flip-flops unchanged (3,746); `check -assert` 0 problems.
  - After, *estimate (Nangate45 typical liberty, ideal clocks, no wire load, no place and route)*:
    | Check | net_clk | core_clk |
    |---|---|---|
    | setup, flop to flop | 4.694 ns | 2.649 ns |
    | hold, flop to flop | 0.080 ns | 0.080 ns |
    | crossing into this clock (limit 5.0 ns) | 4.856 ns | 4.627 ns |
    | inputs / outputs | 4.814 / 4.946 ns | 2.810 / 2.931 ns |
    | recovery / removal at reset pins | 6.261 / **-0.108 ns** | 4.861 / **-0.108 ns** |
    With the default wire-load model: setup 4.547 / 2.405 ns, removal -0.115 ns. Critical setup paths: `core_clk` mlp_core output register (`c_kind`) through decision_unit margin logic to `d_low_conf` (62 cells); `net_clk` eth_parser output (`e_tkeep`) through l3l4_parser byte-position logic to `r_ip_len` (38 cells).
  - **Not fixed:** removal check fails by 0.108 ns on 40 asynchronous reset/set pins in the async FIFO (TNS hold -4.220 ns). Clock-to-output 0.18 ns of the reset synchronizer vs 0.288 ns library removal time, ideal clocks. Independent of clock frequency; normally fixed by hold buffering in place and route. `make sta` exits 1 because of it. Also 2 max-capacitance violations remain (were 1,319 max-slew + 129 max-cap before).
  - Re-run after the netlist change: `make gls` 110/110 beats, 0 mismatches, 0 protocol errors, counters equal the model (*simulated*, same as RTL). All RTL tests re-run (RTL unchanged): all pass.
  - `docs/cdc.md` "Not checked" timing item updated with the crossing constraint and result. README status rows updated.
  - TASKS.md: OpenSTA line **not ticked**: the report exists and setup is met, but the remedy differs from "add a pipeline stage" and a removal violation is open; both need the owner (D-038 [PROPOSED]).
- M4 session 4 (automated builder, stacked on `m4-s3`; instructions D-035, decisions D-036). No RTL change: lint and synthesis exposed no bug. Details and tables in `synth/README.md`.
  - Lint: `make lint` (existing) now also lists the 4 inline waivers; 0 warnings on 11 files. Line ticked.
  - Synthesis (`make synth`, `synth/synth_nangate45.ys`): whole chip to Nangate45 typical, *synthesized (estimate)*: 17,582 cells, 33,821 um^2 (50.2% sequential), 3,746 flip-flops, 0 latch cells, `check -assert` 0 problems. Reports committed in `synth/reports/`; netlist and full Yosys log are gitignored and regenerated. Needed `read_liberty -lib` so `check` knows library cell ports (first try gave false "no driver" problems).
  - Gate-level simulation (`make gls`, `tb/gls_run.py`): Yosys netlist + cell models Yosys generated from the liberty file, Icarus, same 120 smoke frames and checks as the RTL: 110/110 beats, 0 mismatches, 0 protocol errors, counters equal the model (*simulated*, no cell delays, function only). Only the smoke vectors were run on the netlist (about 75 s), not the 9000-frame or stress sets. Test can fail: 36 of 398 AND2 cells changed to OR2 in a copy of the netlist made it fail ("AXI-Lite write never accepted"); netlist restored.
  - Artix-7 (`make synth-xilinx`), *estimate*, not Vivado: 2,445 LUTs (+35 distributed-RAM primitives), 1,710 flip-flops, 0 block RAM, 2 DSP48E1.
  - Throughput (`tb/test_neurogate_top_throughput.py`, in `make test`), *simulated*: 179 core clocks between result beats with a backlog; about 1.117 M packets/s at 200 MHz (cycle count times SPEC clock; timing not checked); first-frame latency 187 core clocks.
  - TASKS.md: ticked lint, synthesis, gate-level, Artix-7, throughput. OpenSTA line left unticked as instructed.
- M4 session 3 (automated builder, stacked on `m4-s2`; instructions D-033, design D-034). No RTL change: no test exposed an RTL bug.
  - `tb/top_common.py` (new): shared helpers (random-phase clocks, power-up, hard reset, AXI-Lite master that records the accepting clock edge, result monitor with controllable ready, frame driver with input stalls, model wrappers). `tb/neurogate_top_checks.py` (smoke) now imports them; same result as before (110 beats, 243,990 ns).
  - `tb/test_neurogate_top_full.py` + `tb/neurogate_top_full_checks.py` (new): all 9,000 generated frames (traffic seed 1: classified 8,349, dropped 651) through both clocks, 10% input stalls, 30% result stalls, paced so the async FIFO cannot overflow. Result (*simulated*, synthetic traffic): 8,349 result beats, 0 mismatches against the models, 0 protocol errors; counters over AXI-Lite equal the model (PKT_NORMAL 3553, PKT_SCAN 2564, PKT_SYN 2232, BYTES 1394507 / 138456 / 120528, PKT_DROPPED 651, PKT_LOWCONF 624, LAST_RESULT 5378, PKT_OVERFLOW 0; the five frame counters sum to 9000); soft reset cleared them. 10.77 ms of simulated time; about 3 minutes CPU, 7 minutes wall on a loaded machine.
  - `tb/test_neurogate_top_stress.py` + `tb/neurogate_top_stress_checks.py` (new), 6 tests, all pass, also with seeds 7, 4242, 1, 2, 3:
    - `test_bad_packets`: 480 frames (every truncation length 1..70 of a TCP, a UDP and a VLAN frame; wrong EtherTypes; bad IP version and header length; `tuser`; random garbage; 1514-byte frames; frames with the last EtherType byte missing; mixed with good ones); the model drops 304 and classifies 176; the 176 result beats and all counters equal the model.
    - `test_result_backpressure`: result stream held low up to 1,406 clocks with `tvalid` high; no beat lost or changed; equal to the model.
    - `test_overflow`: 300 kept frames back to back with the result stream blocked: 18 beats, `PKT_OVERFLOW` 282 (= 300 - 18, exact), the last frame lost so the overflow-only entry carried the tail; then 300 mixed frames: 15 beats, `PKT_DROPPED` 3, `PKT_OVERFLOW` 282; every surviving beat equals the model for its `pkt_id`.
    - `test_hard_reset_midstream`: 8 rounds of `rst_n` low for 2 ps, 3 ns, 90 ns or up to 300 ns, at random phase, mid-frame, result stalled, or with a register read in flight. After each: no stale result in 600 clocks, counters 0, `CONF_THRESH` back to 13, then a fresh stream equal to a model started from scratch.
    - `test_soft_reset_midstream`: 3 soft resets during traffic; counters equal the model beats taken after the last reset edge (109 of 150 beats).
    - `test_soft_reset_same_cycle`: a result beat and the soft-reset write in the same clock edge (checked by time): the beat leaves, the counters stay 0 (reset wins).
  - Test mistakes found and fixed (not RTL bugs): (1) beat time was one clock too early (the shared monitor judges a beat at the edge it moves), which made the same-cycle test fail; (2) after a hard reset the output was checked while the chip was still inside its two-edge reset hold (a read just after an edge shows pre-edge values), which failed seeds 7 and 4242 in round 1.
  - Checked that each test can fail (bug planted in `rtl/`, stress or full test run, then `git checkout rtl`; `git status` clean afterwards). Results (seed 12345). **Evidence note:** these seven manual plants were run once by hand and their logs were not kept (they were in `/tmp`), so the list below is the builder's report, not committed evidence. Durable, committed evidence exists only for: the stress-monitor fix (`tb/evidence/clear_beats_monitor.log` plus the regression test `test_monitor_after_clear`), the gate-level fault (`synth/reports/gls_fault.log`), the timing-constraint fault (`synth/reports/sta_constraint_check.log`) and the clock-crossing faults (`tb/test_cdc_check.py` in `make test`).
    - `rtl/l3l4_parser.sv` `IHL_MIN` 5 -> 4: `test_bad_packets` FAIL, other 5 pass.
    - `rtl/neurogate_top.sv` result-ready ignored (`d_ready = st_ready`): all 6 stress tests FAIL (beats lost under stall: "valid dropped during a stall", missing beats).
    - `rtl/stats_counters.sv` overflow count added only for classified entries (`take && is_classify`): `test_overflow` FAIL, other 5 pass.
    - `rtl/stats_counters.sv` entry wins over soft reset (`soft_rst && !take`): `test_soft_reset_same_cycle` FAIL, other 5 pass.
    - `rtl/stats_counters.sv` `bytes_normal` not cleared by soft reset: `test_overflow`, `test_soft_reset_midstream`, `test_soft_reset_same_cycle` FAIL, 3 pass.
    - `rtl/reset_sync.sv` synchronous assert (no `negedge rst_n`): `test_hard_reset_midstream` FAIL ("round 0: stale result after reset"), other 5 pass.
    - `rtl/feature_builder.sv` `pkt_id` wraps at 8 bits: `test_neurogate_top_full` FAIL (8,139 mismatches, first at beat 210 = frame 256); the 120-frame smoke test cannot see this.
    - Not shown to fail by a hand-planted bug: a first `eth_parser` mutant (last EtherType byte ignores `tkeep`) was NOT caught at the top level and was dropped: the downstream parser also drops a 13-byte frame, so it is invisible there (it is caught by the eth_parser unit test, M2). `pkt_id` left unreset was caught only because the value is X from power-up, so it was not used as the hard-reset demonstration.
  - Verilator lint: unchanged RTL; clean (0 warnings). Lint task NOT ticked, as instructed.
  - TASKS.md: ticked "Top-level cocotb: end-to-end ..." and "Top-level bad-packet, backpressure and reset tests".
- M4 session 2 (automated builder, stacked on `m4-s1`; instructions D-031, design D-032):
  - `rtl/reset_sync.sv` (new): reset synchronizer, 2 flops, asynchronous assert, synchronous release. One per clock domain.
  - `rtl/neurogate_top.sv` (new): the whole chip on two clocks (SPEC sections 2, 3, 8). Only glue: the fork from `decision_unit` to the result stream (CLASSIFY only, stallable) and the counters (every entry), plus result packing ([PROPOSED] SPEC wording in D-032). Lint and compile-check clean. Measured latency 187..191 core clocks from the last input beat to the result beat (*simulated*, paced input, not asserted).
  - `docs/cdc.md` (new): every crossing (2 Gray pointers, FIFO storage read, 2 reset synchronizers, FIFO resets), its method and result, labelled as a self-review. Lists what is not checked (crossing-path timing constraints, reconvergence, synchronizer failure rate, no formal proof).
  - `scripts/cdc_check.py` + `make cdc-check` (new): structural check on a Yosys netlist (slang frontend) of the whole chip, rules C1, S1, S2, G1, M1, O1, R1, R2. Result on the real design: 0 violations.
  - Planted the two M2 faults by hand in `rtl/async_fifo.sv`, once each, then restored with `git checkout rtl/async_fifo.sv`:
    - binary pointers (`wr_gray_q <= wr_bin_next`, `rd_gray_q <= rd_bin_next`, binary full/empty compares): `python3 scripts/cdc_check.py sim_build/cdc/break1.json --rtl rtl` printed `VIOLATION G1 u_afifo.rd_gray_q -> u_afifo.rd_gray_sync1_q (core_clk -> net_clk): 5 bits cross from one register that is not Gray-coded (D inputs come from ['$add'], not from one xor encoder)`, the same for the write pointer, and an M1 line; `cdc_check: 3 violation(s): FAIL`, exit 1. `tb/test_async_fifo.py` with the fault: `TESTS=8 PASS=8 FAIL=0` (simulation does not see it).
    - one-flop synchronizers (full/empty compare `*_sync1_q`): 10 `VIOLATION S2 ... first synchronizer flop output feeds ...; it must feed only the D input of a second flop (one-flop synchronizer)` plus M1; `cdc_check: 11 violation(s): FAIL`, exit 1. `tb/test_async_fifo.py` with the fault: `TESTS=8 PASS=8 FAIL=0`.
    - After restoring: `cdc_check: 0 violation(s): PASS`.
  - `tb/test_cdc_check.py` (new, in `make test`): real RTL must pass; 7 faults planted in copies of `rtl/` must each fail with their rule (binary pointers G1, one-flop S2, logic before synchronizer S1, raw crossing S1, output from the wrong domain O1, FIFO reset from the wrong domain R2, one-flop reset synchronizer R1). 8 of 8 cases as expected.
  - `tb/test_neurogate_top.py` + `tb/neurogate_top_checks.py` (new): smoke test on two clocks (156.25 / 200 MHz, random core clock phase, random reset release). 120 generated frames (traffic seed 1, 40 per scenario), paced so the FIFO cannot overflow, 30% random result stalls. Result: 110 result beats, 0 mismatches against the models; counters over AXI-Lite all equal to the model (PKT_NORMAL 43, PKT_SCAN 63, PKT_SYN 4, PKT_DROPPED 10, PKT_LOWCONF 45, PKT_OVERFLOW 0; sum 120); soft reset over AXI-Lite cleared them. *Simulated, synthetic traffic.* Checked it can fail: with the result-stream ready ignored in the fork, the test failed (81 mismatches, 22 protocol errors); restored.
  - README status rows updated (M3 approved, M4 in progress).
- M4 session 1 (automated builder, stacked on `m3-s4`; Gate M3 decisions logged as D-029, design in D-030):
  - `rtl/stats_counters.sv` + `tb/test_stats_counters.py`: ten outputs (9 counters, `LAST_RESULT`), one entry per clock, never stalls, soft reset wins over a same-cycle entry. Test: Python reference model compared after every edge; directed cases, reserved cases, `s_valid` low with garbage pins, soft reset, 6,000-cycle random stream, reset mid-stream, wrap (8-bit build, and 32-bit with pre-loaded registers plus 65,540 maximum-size entries). *Simulated.*
  - `rtl/axil_regs.sv` + `tb/test_axil_regs.py`: 12 registers plus SLVERR on 0x30..0x3C. The test reads the register list from `docs/regmap.md` (so it follows the document) and checks reset value, read value, access type, write strobes, reserved bits, the soft-reset pulse timing, handshake stalls, a 4,000-cycle random mix against a model, and reset mid-transaction. A second build with another `CONF_THRESH_RESET` proves the parameter is used. *Simulated.*
  - Checked that the tests can fail: four deliberate bugs (soft reset ignoring `WDATA`, unmapped range shifted by one, soft reset removed from the counters, one byte-counter add off by one) were each caught; originals restored.
  - Lint waiver (inline, with reason): four unused input bit groups of `axil_regs` that the register map ignores.
  - Regmap vs SPEC: no disagreement found (SPEC section 3.2 is a summary of the regmap and matches it).
- Kickoff kit written: the contributor rules, SPEC.md, TASKS.md, STATUS.md, DECISIONS.md. Folders and `.gitignore` in place.
- M1 session 1 (spec and planning, docs only, no RTL):
  - DECISIONS.md: D-001 placeholder replaced with the owner's D-001 and D-001a..e text. Added D-003 (OWNER: optional Wireshark captures cut, owner time budget) and D-004 (BUILDER: async FIFO entry format, overflow accounting, `pkt_id` on the result stream).
  - SPEC.md frozen per D-001a..e: sections 2 (new block order and domains), 3.1 (64-bit stream details), 5 (per-bucket counters for F7), 8 (domain lists, new 8.1 async FIFO entry: 114 bits = features 64 + `pkt_len` 16 + `pkt_id` 16 + `ovf_cnt` 16 + `kind` 2), 10 (compile check, Nangate45, overflow and shared-bucket tests), 13 (resolved answers). `PKT_OVERFLOW` added at 0x2C. Section 9 Wireshark line removed (D-003).
  - `docs/regmap.md`: final AXI-Lite map, 12 registers, including `PKT_OVERFLOW`.
  - `docs/PRD.md` (one page) and `docs/stories.md` (epic per milestone, stories mapped to TASKS.md).
  - TASKS.md: ticked section 13 walkthrough, liberty decision, and regmap (finished this session). Removed the M2 Wireshark line (owner instruction, D-003).
  - the contributor rules: added the rule that RTL must use the SystemVerilog subset both Icarus and Verilator accept.

- M1 session 2 (toolchain and scaffolding, no RTL):
  - Installed in user space in the tool install folder (apt-get not usable): GNU make 4.4.1 (Debian .deb unpacked), g++ 14 + Bison + Flex + Tcl/Eigen/zlib headers (unpacked debs), CUDD, and **OpenSTA 3.2.0 built from source** (`$NG_TOOLS/opensta/bin/sta`). `.venv` has cocotb, Scapy, NumPy, plus CMake/Ninja/SWIG used for the OpenSTA build.
  - `scripts/env.sh` (PATH setup), `scripts/toolchain_check.sh`, `Makefile` (`lint`, `compile-check`, `test`, `synth`, `sta`, `gls`, `toolchain`, `clean`), `README.md` skeleton, `docs/toolchain.md`, `lib/` with fetch script.
  - Nangate45 typical liberty file downloaded to `lib/NangateOpenCellLibrary_typical.lib` (gitignored, see D-005). OpenSTA reportedly loaded it (1 library, 135 cells): unverified, no saved output.
  - TASKS.md: ticked Repo skeleton, Toolchain check script, README skeleton.

- M1 session 3 (spec final, docs only): D-006 logged (bias int32, owner). SPEC.md section 6 now states int32 biases; `grep -c PROPOSED SPEC.md` = 0 (was 1 before). No tag needed an owner decision. TASKS.md "Freeze SPEC.md" ticked.

- M1 session 4 (docs only, owner instruction D-007): `docs/stories.md` and `docs/PRD.md` rewritten in plain English (no decision IDs, task tags, unexplained file names or acronyms). Scope unchanged. the contributor rules gain a "Writing docs" section. Word counts (`wc -w`): stories 838 → 979, PRD 519 → 733. The counts went up because each term is now explained in place; the stories file is 72 short non-blank lines (about 1.5 pages). No TASKS.md box covers this work, so none was ticked.

- M1 session 5 (fix independent M1 review, branch `m1-review-fixes`): review copied unchanged to `reviews/M1-review.md`. Findings and fixes:
  - Must fix 1 (counter guarantee): `docs/regmap.md` and SPEC.md section 8.1 now define soft-reset precedence (reset wins over an entry finishing in the same cycle), the accounting boundary (a frame counts in the period where `decision_unit` finishes it; queued entries and pending overflow counts are not flushed and count after the reset), and the conditions for the sum equality (drained pipeline, no `ovf_cnt` saturation, no counter wrap, no soft reset with frames in flight).
  - Must fix 2 (threshold vs test set): SPEC.md sections 7 and 9 now choose `CONF_THRESH` on a validation split carved from the training data. New D-008 (PM recommendation, pending owner confirmation at the M1 gate) refines D-002 without rewriting it.
  - Must fix 3 (toolchain script): `scripts/toolchain_check.sh` now captures each version command's exit code and output, and flags non-zero exit, empty output and error-looking output; any flag gives exit 1. Checked: with `TMPDIR=/nonexistent`, Icarus prints "Error opening temporary file" and the script reports `Icarus: FAILED (exit 1)` and exits 1; a fake `yosys` that exits 3 gives `FAILED (exit 3)` and exit 1. In the normal environment `/tmp` is writable (13% used) and Icarus works, so the earlier `/tmp` error did **not** reproduce in this session; its cause is not known (a transient or reviewer-sandbox issue is plausible, not confirmed).
  - Should fix / nits: stories history-slot wording now matches the SPEC test (destination-port bucket, one packet aged out, one still in window); AI, IP, IPv4, TCP, UDP explained on first use in PRD and stories; unevidenced claims below marked "unverified, no saved output"; SPEC and README use planned wording for the golden model and verification; 176 cycles labelled an architectural estimate; README M1 status no longer says "not started"; resume branch fixed.

- M1 session 6 (docs only, owner instruction D-009, branch `m1-docs-pm-format`): `docs/stories.md` and `docs/PRD.md` rewritten in product-manager format. Stories: each epic and story has Problem, What we'll build, Acceptance criteria (2-4 bullets); same 5 epics, 13 stories, order and scope; the user is named in each story heading instead of an "As a ..." line (needed to stay within the word limit). PRD: Problem statement, Proposed solution, Goals and non-goals, Success metrics (stated as targets, each labelled simulated or estimate), Main risks (5, was 6: simulator-support risk merged into the owner-time risk), Milestones. Word counts (`wc -w`): stories 1031 → 1028, PRD 767 → 722. the contributor rules "Writing docs" gains the format. No TASKS.md box covers this work, so none was ticked.

- M2 session 1 (branch `m2-s1`):
  - Owner items (D-010): Gate M1 approved. D-008 marked owner-confirmed. TASKS.md M3 margin line reworded (validation split, then report once on test). PRD sentence with the SPEC file name reworded.
  - `model/traffic_gen.py`: Scapy generator, three classes, one seed gives byte-identical `.pcap` and `.csv` (checked: two runs, `cmp` equal). Default 30 scenarios x 300 frames = 9000 frames (seed 1: normal 4323, port_scan 2501, syn_flood 2176; synthetic data). Attack scenarios mix in 0-50% background packets so classes overlap. Normal traffic includes VLAN, UDP, ICMP, ARP, fragments, truncated frames and `tuser` errors (D-011).
  - `model/features.py`: byte-level parser (SPEC section 4), `FeatureWindow` (SPEC section 5, integer math only, per-bucket counters), `run_stream` producing FIFO-entry-shaped records (D-004).
  - Tests: every feature formula with edge values; parser cases (VLAN, IP options, UDP, fragments, short, bad IHL, wrong EtherType, `tuser`); hand-derived shared-bucket case (one packet leaves, one stays); 5000-packet random run compared with a slow recompute-from-scratch reference; generator reproducibility; Python parser vs Scapy's own dissection on all 2400 frames of a test run (kept 2228, dropped 166, truncated 6, zero disagreements).
  - Generated data (`data/traffic*`) is gitignored; regenerate with `python model/traffic_gen.py --seed 1 --out data/traffic`.
  - Not done: no RTL, no cocotb test, no FIFO. M2 tasks 3-8 are open. Model-vs-RTL equality has not been tested because there is no RTL yet.

- M2 session 2 (branch `m2-s2`, stacked on `m2-s1`):
  - `rtl/eth_parser.sv`: Ethernet header parser. Passes the frame through with 1 cycle latency and full valid/ready. Flags each beat with "header done", "VLAN tag" and "IPv4"; on the last beat flags a drop for tuser, short header or non-IPv4 EtherType, in the model's priority order (D-012).
  - `tb/test_eth_parser.py` (cocotb, Icarus, simulated): 5 tests. Directed frames (good, VLAN, ARP, IPv6, QinQ, every short length 1-15 untagged and 12-19 tagged, tuser, tuser on a short frame); the same with random input gaps and output stalls; 400 random fuzz frames; 600 generated frames (`traffic_gen.generate(SEED, 2, 100)`, 21 VLAN-tagged) checked against `model/features.py`; reset after 1, 2 and 3 beats of a frame, including with the output stalled. Every frame checks: bytes/tkeep/tlast/tuser unchanged, header-done beat, VLAN/IPv4 flags, drop reason vs the model. Garbage is driven in lanes with tkeep = 0, and noise on tuser outside the last beat.
  - Mutation check (by hand, RTL restored after, `cmp` equal): VLAN EtherType changed to 0x8101 → 5 of 5 tests fail; tkeep ignored for the EtherType byte → 4 of 5 fail (the generated-traffic test passes, because generated traffic has no frame shorter than 14 bytes; short L2 frames are covered by the directed and fuzz tests).
  - `tb/cocotb_run.py`: shared cocotb runner. cocotb runs in the OSS CAD Suite Python because `.venv`'s Python 3.13 has no shared libpython (D-012). A shutdown segfault in this cocotb/Icarus build is avoided by ending each test one clock after its last write (D-012).
  - Commands run and real output (seed 12345):
    ```
    source .venv/bin/activate; . scripts/env.sh
    SEED=12345 python3 tb/test_eth_parser.py   -> TESTS=5 PASS=5 FAIL=0; "test_eth_parser: 5 passed, 0 failed (seed=12345)"; exit 0
    make lint compile-check                    -> exit 0; verilator --lint-only -Wall on rtl/eth_parser.sv: no warnings; iverilog -g2012: no messages
    make test                                  -> exit 0; eth_parser 5 passed; "47 passed in 0.12s" (features); "6 passed in 2.87s" (traffic_gen); 7.0 s wall
    ```
  - Lint: `rtl/eth_parser.sv` is clean under `verilator --lint-only -Wall` (no waivers). The M2 box "Lint clean for all new RTL" is not ticked: l3l4_parser, skid_fifo and async_fifo do not exist yet.
  - `make`: not on the default PATH (`command -v make` in a clean shell: not found). GNU Make 4.4.1 exists in user space at `$NG_TOOLS/bin/make` and is found after `. scripts/env.sh`. The `make` results above used that binary. Nothing was installed this session.
  - TASKS.md: ticked the eth_parser line only.

- M2 session 3 (branch `m2-s3`, stacked on `m2-s2`):
  - `rtl/l3l4_parser.sv`: IPv4 + TCP/UDP header parser after eth_parser. It finds each header byte from its offset in the frame (beat counter plus lane number), so IP options (IHL 6-15), VLAN offsets and fields split across two beats all go through the same logic. It emits one record per frame, one clock after the last beat: keep/drop, drop reason (same order as the model, incl. D-011 version != 4), TCP/UDP, SYN, ACK, RST-or-FIN, IPv4 total length, TTL, dst port and frame length (saturates at 65535). Interface in D-014.
  - `tb/parser_chain_tb.sv` (test-only wrapper: eth_parser feeding l3l4_parser, as in the chip) and `tb/test_l3l4_parser.py` (cocotb, Icarus, simulated). 6 tests:
    1. Directed: 312 frames. TCP flag mixes, every IHL 5-15 for TCP, UDP and VLAN+TCP, edge values (TTL 0/255, length 0/65535, port 0/65535), fragments (MF only and DF only are kept, offset 1 and high-bits-only offset are dropped), ICMP/GRE/other protocols, version 0/6/15, IHL 0-4, IPv6, VLAN+ARP, tuser. Four header shapes are also cut at every length from 1 byte to full.
    2. The same 312 frames with random input gaps (30%) and result stalls (50%).
    3. 600 random-header fuzz frames.
    4. Frames of 65534 / 65535 / 65536 / 70000 bytes (pkt_len saturation).
    5. Reset after 1-7 beats of a frame, and while a finished record waits on a stalled output.
    6. Generated-traffic cross-check.
    Every record is compared with `model/features.py` on all fields. Unused lanes carry garbage, and tuser carries noise outside the last beat.
  - **Cross-check (simulated, synthetic traffic):** all frames of the default dataset (`traffic_gen` seed 1, 10 scenarios per class x 300 = 9000 frames, what `python model/traffic_gen.py` writes) plus the seed-12345 set (600 frames) = 9600 frames, run through eth_parser + l3l4_parser:
    - RTL vs model: **9600 of 9600 agree** (all record fields).
    - RTL vs Scapy: **9600 of 9600 agree**. 8900 kept with all fields compared; 6 kept, cut inside the TCP/UDP header, so Scapy decodes no L4 layer and only keep/drop is compared; 694 dropped.
    - Model vs Scapy (computed in `.venv` before simulation): 9600 of 9600 agree.
    - eth_parser's own outputs (VLAN/IPv4 flags) are checked directly only in its unit test (600 generated frames). In the chain they are checked through their effect on every record.
    - On this basis the TASKS.md box "Parser output cross-checked against Scapy-parsed fields" is ticked.
  - **Mutation check** (by hand; the RTL was restored after each one, `cmp` equal to the saved copy). Each broken version was run with `SEED=12345 python3 tb/test_l3l4_parser.py`:
    - IHL ignored (L4 always at L3+20): 4 of 6 fail.
    - Fragment check sees only the low offset byte: 3 of 6 fail.
    - UDP needs 8 bytes instead of 4: 3 of 6 fail.
    - TCP flags read from byte 12 instead of 13: 6 of 6 fail.
    The generated-traffic test catches only the last one. Generated traffic has no IP options, its fragment offsets are 1-99 (low byte only), and it has no UDP frames cut short. The directed and fuzz tests cover those cases. This is a limit of the generated data, not of the check.
  - Commands run and real output (seed 12345):
    ```
    source .venv/bin/activate; . scripts/env.sh
    SEED=12345 python3 tb/test_l3l4_parser.py  -> "model vs Scapy: 9600 of 9600 generated frames agree"; TESTS=6 PASS=6 FAIL=0; "test_l3l4_parser: 6 passed, 0 failed (seed=12345)"; exit 0; ~33 s wall
    make lint compile-check                    -> exit 0; verilator --lint-only -Wall: no warnings on rtl/eth_parser.sv and rtl/l3l4_parser.sv; iverilog -g2012: no messages
    make test                                  -> exit 0, 38.9 s wall; eth_parser 5 passed; "47 passed" (features); l3l4_parser 6 passed; "6 passed" (traffic_gen)
    ```
  - `tb/parser_chain_tb.sv` is not covered by `make lint` (that target lints `rtl/` only). Checked by hand: `verilator --lint-only -Wall --top-module parser_chain_tb tb/parser_chain_tb.sv rtl/*.sv` → exit 0, no warnings. The wrapper has one waiver: unused eth_parser sideband (tuser, hdr_done, ipv4), with the reason given inline. `iverilog -g2012` → exit 0.
  - First Icarus compile of l3l4_parser printed "sorry: constant selects in always_* processes are not fully supported". Fixed the same way as in eth_parser (version field split out with an `assign`). Now silent.
  - TASKS.md: ticked the l3l4_parser line and the Scapy cross-check line. "Lint clean for all new RTL" is not ticked: skid_fifo and async_fifo do not exist yet.
  - PM correction logged (D-015): `make` at `$NG_TOOLS/bin/make` after `. scripts/env.sh` was fine in M2 session 1.

- M2 session 4 (branch `m2-s4`, stacked on `m2-s3`):
  - `rtl/skid_fifo.sv`: AXI-Stream FIFO at the input. 4 slots by default (`DEPTH`, power of two, at least 2), each slot holds one whole beat (tdata, tkeep, tlast, tuser). Latency 1 cycle, one beat per cycle while `m_tready` is high. `s_tready` is low only when all slots are used (flops only, no path from `m_tready`). Reset empties it; beats stored at that moment are discarded. Design choices in D-017.
  - `tb/test_skid_fifo.py` (cocotb, Icarus, simulated). One cycle-by-cycle loop with a scoreboard. Every output beat is compared on all four fields with the oldest accepted beat (sequence number in tdata[31:0] makes loss, duplicates and reordering visible). Each cycle also checks `m_tvalid` and `s_tready` against an exact model of the fill level, and that a stalled output does not change. The source holds a beat until it is accepted. 6 tests plus a final summary test: always-ready throughput; input gaps 20/50/80%; random `tready` 20/50/90% stalled; both random (6 pairs); long stall (300 and 300 cycles blocked) and a starved input; reset mid-stream (6 plans, resets of 1-5 cycles, while empty, filling, full and stalled).
  - **Beats checked (simulated, seed 12345): 37556 output beats** compared against the scoreboard in one run, 0 errors. 20 beats were stored inside the FIFO when a reset hit; they are discarded by design and counted, not compared.
  - **Throughput (simulated, `tready` always high, input offered every cycle, 4000 beats):** 4000 accepted in 4000 cycles = 1.000 beats/cycle in, 4000 delivered in 4000 cycles = 1.000 beats/cycle out, latency 1 cycle, `s_tready` never low, 1 slot used. This is a count of simulated clock cycles, not a measured line rate.
  - **Mutation check** (by hand; RTL restored after, `cmp` equal to the saved copy). Each run: `SEED=12345 python3 tb/test_skid_fifo.py`:
    - `s_tready` forced high (overwrites when full): 4 of 7 fail (the two tests that never fill the FIFO still pass, as they should).
    - tuser stored from tlast: 6 of 7 fail.
    - reset leaves the beat count alone: 6 of 7 fail. Weak mutation: it fails at cycle 0 because the count is X without a reset, not through a logic error.
    - a write is blocked when a read empties the last slot in the same cycle (loses full rate): 6 of 7 fail.
    The 7th test is the summary line, which cannot fail.
  - Commands run and real output (seed 12345):
    ```
    source .venv/bin/activate; . scripts/env.sh
    SEED=12345 python3 tb/test_skid_fifo.py  -> TESTS=7 PASS=7 FAIL=0; "test_skid_fifo: 7 passed, 0 failed (seed=12345)"; exit 0; ~7 s wall
    make lint compile-check                  -> exit 0; verilator --lint-only -Wall: no warnings on all 3 RTL files; iverilog -g2012: no messages
    make test                                -> exit 0, 47 s wall; eth_parser 5 passed; features "47 passed"; l3l4_parser 6 passed; skid_fifo 7 passed; traffic_gen "6 passed"
    ```
  - Not tested: `DEPTH` other than 4 (the test models fill level for 4). A `DEPTH` of 3 was checked by hand to give an elaboration error in both Verilator and Icarus. No gate-level or timing check yet (M4).
  - TASKS.md: ticked the skid_fifo line only. "Lint clean for all new RTL" stays open until async_fifo exists. async_fifo not started.

- M2 session 5 (branch `m2-s5`, stacked on `m2-s4`):
  - PM note on `m2-s4` closed: the skid_fifo "summary" test could not fail, so it is gone. Its number is now a plain log line after every run ("scoreboard running total", last value 37556 beats, simulated, same as before). skid_fifo now counts **6 tests**, all real checks (D-018).
  - `rtl/async_fifo.sv`: dual-clock AXI-Stream FIFO, same beat fields as skid_fifo, `DEPTH` 16 by default (power of two, at least 4). Gray-coded pointers sent from flops, two-flop synchronizers on each side, registered full/empty flags, first-word-fall-through output. Reset: one reset input per side, asynchronous inside the FIFO so both sides clear at the same instant; `s_tready` and `m_tvalid` are low during reset. Design and reasons in D-019.
  - `tb/test_async_fifo.py` (cocotb, Icarus, simulated), 8 tests: one per clock ratio (write : read = 250:100 MHz, 100:250, 156.25:156.25 with random phase, 200:142.86 (7:5), 142.86:200 (5:7), 156.25:200 = the chip's clocks), each with 6 traffic mixes of 1000 beats (input idle 0-70%, output stall 0-80%); fill-to-full then drain at every ratio; reset mid-stream (3 resets per ratio, both sides together, released in random order). Checked every cycle: data on all four fields, output stable while stalled, no overflow (`s_tready` = 1 only with fewer than 16 beats stored), no underflow (`m_tvalid` = 1 only with a written beat waiting), flags not stuck longer than 3 edges of their own clock, `s_tready`/`m_tvalid` low in reset, and a watchdog against deadlock. The full-speed mix also checks the rate: **1.000 beats per cycle of the slower clock at every ratio** (simulated, 1000 beats; a count of simulated cycles, not a measured line rate).
  - **Beats checked per ratio (simulated, seed 12345, 0 errors):**

    | Ratio (write : read) | ratio test | fill/drain | reset test (+ discarded by reset) | total checked |
    |---|---|---|---|---|
    | 250 : 100 MHz | 6000 | 24 | 553 (+46) | 6577 |
    | 100 : 250 MHz | 6000 | 24 | 590 (+7) | 6614 |
    | 156.25 : 156.25 MHz | 6000 | 24 | 558 (+40) | 6582 |
    | 7 : 5 (200 : 142.86 MHz) | 6000 | 24 | 554 (+45) | 6578 |
    | 5 : 7 (142.86 : 200 MHz) | 6000 | 24 | 565 (+32) | 6589 |
    | chip, 156.25 : 200 MHz | 6000 | 24 | 561 (+37) | 6585 |
    | **all** | 36000 | 144 | 3381 (+207) | **39525** |

    In every fill/drain run the FIFO took exactly 16 beats, then held `s_tready` low (42-141 write cycles), then delivered all 24 and ended empty with `s_tready` high.
  - First full run: 7 of 8 passed. The reset test failed at 7:5 with "m_tvalid low but 1 beats were written more than 3 read clocks ago". Cause: a **test** bug. Beats written while the read side was still held in reset are only seen 3 read clocks after its release, and the check did not allow for that. The check now starts 3 read clocks after release (same rule on the write side). No RTL change. A deadlock in a mutation run (below) also showed the test had no timeout; a simulation-time watchdog was added.
  - **Mutation check** (by hand; the RTL was restored after each, `cmp` equal to the saved copy every time). Each run: `SEED=12345 python3 tb/test_async_fifo.py`:
    - Binary instead of Gray pointers (Gray encoder removed, compare unchanged): **8 of 8 fail** (overflow: `s_tready` high with 16 of 16 slots used).
    - Full flag from the current write pointer instead of the next one (full one write late): **8 of 8 fail** (overflow).
    - Empty flag from the current read pointer (empty one read late): **8 of 8 fail** (wrong beat delivered / underflow).
    - `full_q` resets to 0 (`s_tready` high during reset): 1 of 8 fail (only the reset test watches `s_tready` during reset; correct).
    - **Not caught, as expected:** binary pointers crossing with a matching binary full compare: 8 of 8 pass. Synchronizers cut to one flop: 8 of 8 pass.
    The non-zero exit of failing runs was 245 in some cases (the D-012 shutdown segfault after failed tests); the failures themselves are in the cocotb summary.
  - **What this does and does not show (clock-domain crossing).** RTL simulation has no metastability and no skew between bits of a bus. The tests show the FIFO logic (pointer arithmetic, full/empty, ordering, reset) is right at the six ratios simulated. They do not show that the crossing is safe in hardware: the last two mutations above are real CDC bugs and pass every test. That the pointers are Gray-coded from flops and go through two flops with no logic in between is checked by reading the code and goes into the M4 CDC review. Synchronizer timing constraints are M4 work; not done. Not a commercial CDC sign-off.
  - **Lint clean for all RTL:** `make lint compile-check` → exit 0, no warnings, on `async_fifo`, `eth_parser`, `l3l4_parser`, `skid_fifo`; no waivers in `rtl/`. By hand: `verilator --lint-only -Wall --top-module parser_chain_tb tb/parser_chain_tb.sv rtl/*.sv` → exit 0, no warnings (one waiver, unchanged from session 3, reason inline); `iverilog -g2012` on the same → exit 0. On this basis the TASKS.md box "Lint clean for all new RTL" is ticked.
  - Commands run and real output (seed 12345):
    ```
    source .venv/bin/activate; . scripts/env.sh
    SEED=12345 python3 tb/test_async_fifo.py  -> TESTS=8 PASS=8 FAIL=0; "test_async_fifo: 8 passed, 0 failed (seed=12345)"; exit 0; ~13 s
    SEED=12345 python3 tb/test_skid_fifo.py   -> TESTS=6 PASS=6 FAIL=0; "test_skid_fifo: 6 passed, 0 failed (seed=12345)"; exit 0
    make lint compile-check                   -> exit 0; 0 lines containing "warning"; 4 files linted, 4 x 2 compiles
    make test                                 -> exit 0, 61 s wall; async_fifo 8 passed; eth_parser 5 passed; features "47 passed"; l3l4_parser 6 passed; skid_fifo 6 passed; traffic_gen "6 passed"
    ```
  - Not tested: `DEPTH` other than 16 and `DATA_W` other than 64 in simulation (bad `DEPTH` values 2, 6, 12 checked by hand to stop elaboration in both tools). The 114-bit chip configuration is only exercised at M4. No gate-level or timing check yet (M4).
  - README status row for M2 updated (FIFOs no longer "not started").
  - TASKS.md: ticked the async_fifo line and "Lint clean for all new RTL". All 8 M2 boxes are now ticked.

- M2 session 6 (branch `m2-s6`, fixes for the independent M2 review; the review file is `M2-review.md` (kept outside the repository), not edited):
  - **Must fix 1 (truncated-header policy).** SPEC.md section 4 now has a bullet tagged `[CLARIFICATION, pending owner OK at Gate M2]`: TCP is kept with 14 header bytes, UDP with 4, which is what the model and RTL already do. No behaviour, test or RTL changed. Logged as D-020. The owner confirms or rejects it at the gate. If rejected, the model, RTL and tests must change together.
  - **Must fix 2 (Scapy claim).** README, TASKS.md tick and this file now give the breakdown: of 9,600 generated frames, all fields compared on 8,900 kept frames; keep/drop only on 6 kept frames where Scapy decodes no TCP/UDP layer (cut inside the transport header); 694 dropped by both. Model vs Scapy was computed the same way. TASKS.md Gate line 32 still says "100% of generated frames"; proposed rewording below (the file belongs to the project manager).
  - **Should fix, monitors.** New `tb/handshake_monitor.py`: judges each beat at the clock edge where it moved (signals read after the previous edge, judged on the next) and fails if valid, payload or sideband change during a stall. Used by the eth_parser and l3l4_parser tests. The stricter monitors found **no RTL bug**: all tests still pass. Proved they can fail (each break restored, `cmp` identical to the saved copy, `git status` clean for `rtl/`): (a) `eth_parser` loading the next beat during a stall (`in_fire || m_tvalid`) → "tdata changed during a stall", 3 of 5 tests failed; (b) `l3l4_parser` clearing `r_valid` without `r_ready` → "valid dropped during a stall", 4 of 6 failed. A first break, `m_tvalid` no longer held by `s_tready`, did not trip the stall check (the sender holds its beat, so valid stays up); it failed other tests only. I do not claim the stall check covers it.
  - **Should fix, watchdog.** `send()` in both tests fails after 2000 cycles of low `s_tready`, and every test has a 50 ms simulated-time limit. Checked with `s_tready` tied low in `eth_parser`: tests end with "deadlock: s_tready low for 2000 cycles while sending" instead of hanging.
  - **Should fix, README.** "neither exists yet" and "no RTL yet" replaced; quick start now starts with `source .venv/bin/activate` and `. scripts/env.sh`.
  - **Should fix, SPEC verification row.** The confidence row now says `CONF_THRESH` is chosen on a validation split from training data (section 7, D-008) and the test set only reports.
  - **Should fix, evidence.** `make test` writes `results/<commit>[-dirty]/<suite>.log`, per-suite cocotb `.xml`, `run_info.txt` (commit, seed, tool versions) and `summary.txt`. `results/` is gitignored. Mutation runs from earlier sessions were never saved this way; only the summaries in this file exist for them. Not redone.
  - **Nits.** stories wording for IP, TCP, UDP (word count 1028 before and after); stale lines in this file; D-019 wording (TASKS.md does not require skid-FIFO fields); skid reset summary now says "(simulated)".
  - Not done: independent reviewer's note that 114-bit FIFO and top-level reset synchronizers have no evidence is an M4 item. `docs/regmap.md` line 50 still says `CONF_THRESH_RESET` comes from the margin table (D-002); D-008 refines this. Not changed (outside the review).
  - Commands (all after `source .venv/bin/activate; . scripts/env.sh`): `make lint compile-check` exit 0; `make test` exit 0 (numbers in the table above).

- M3 session 1 (branch `m3-s1`, Python side only; automated builder 5.5, no RTL started):
  - Owner queue (D-021): Gate M2 approval logged; SPEC section 4 clarification tag now owner-confirmed; TASKS.md Gate M2 line reworded to the 8,900 / 694 / 6 breakdown; `docs/regmap.md` threshold line fixed (validation split, never the test set). IPv4-only drop and Python 3.11 runtime stand.
  - `model/dataset.py` (splits), `train.py`, `quantize.py`, `golden.py` (integer-only source of truth), `evaluate.py`, `export_vectors.py`; committed outputs in `data/model/` (weights.json, 4 ROM `.hex`, conf_thresh.txt) and `data/vectors/mlp_vectors.csv` (2,028 vectors: 22 directed, 16 corner, 800 random, 1,190 test-split). Design choices in D-022.
  - Results (all *simulated*, **synthetic data, not real-world accuracy**), unseen test split, 8,347 classified packets (653 of 9,000 frames dropped by the parser): **int8 accuracy 95.50%**, float 95.45% (int8 vs float classes agree on 99.90%). Per-class int8 recall: normal 94.45%, port_scan 95.50%, syn_flood 97.24%; precision 98.53% / 94.57% / 91.81%. Full tables, confusion matrices and chart: `docs/results/results.md`, `metrics.json`, `margin_vs_accuracy.png`.
  - Margin vs accuracy: default `CONF_THRESH` = **13**, chosen on the validation split (rule: smallest threshold whose kept packets are >= 98% correct there). Reported once on test: 13.9% of packets flagged; unflagged 97.86% correct (just under the 98% validation target), flagged 80.93%. Bins from confidence 32 up were 100% correct on both splits; the 0-15 bin was 84.6% on test.
  - Tests: `tb/test_golden.py`, 15 tests: golden vs an independent NumPy int64 model on all 8,347 test vectors (zero mismatches in logits, hidden values, class, confidence); golden vs the same weights in float math (logit error inside the rounding bound; class agreement 99.94%); int32 extremes; round-half-up; tie-break; confidence cap; threshold edge; ROM `.hex` round trip; exported vectors replay; reproducibility. Mutation check: removing the round-half-up term makes 3 tests fail.
  - Not done / limits: no RTL, so "RTL equals golden" is untested. The exported vectors contain 3 logit-tie cases found by random search and no all-hidden-zero case (random search never hit one; it is covered in the unit test with a custom net only). Two small deviations from the owner's plan: PR base is `main` but the branch is stacked on `m2-s6` (see D-021 note); the `conf_shift` is a proposal needing owner OK (D-022). `pip install matplotlib` into `.venv` (3.11.2) for the chart; nothing system-wide.

- M3 session 2 (branch `m3-s2` off `m3-s1`, automated builder; owner instruction D-023):
  - `origin/main` (PR #27) merged into `m3-s2`: no conflicts, no file changed.
  - **`rtl/feature_builder.sv`** (D-024): takes l3l4_parser's per-frame record, never stalls, updates the 64-packet window in one clock (ring + 64 bucket counters + running totals), computes F0-F7 (F3 by multiply-and-shift, exact for all 1,501 inputs), assigns `pkt_id`, writes the 114-bit entry, and handles async-FIFO overflow (16-bit saturating pending count, `ovf_cnt`, OVF_ONLY entries).
  - `tb/test_feature_builder.py` (unit, 6 tests) + `tb/feature_chain_checks.py` (3 tests on `tb/feature_chain_tb.sv` = eth_parser -> l3l4_parser -> feature_builder). Results (*simulated*, synthetic traffic):
    - Generated default dataset (traffic_gen seed 1): **9,000 entries compared, 0 mismatches** (8,349 CLASSIFY, 651 DROP), all fields: kind, pkt_id, pkt_len, ovf_cnt, 8 features.
    - SEED dataset: **600 compared, 0 mismatches**. Same set with the FIFO full on ~70% of cycles: 177 written entries equal the model, 423 lost frames all accounted for in `ovf_cnt` (395 OVF_ONLY entries).
    - Unit: shared-bucket case (one leaves, one stays), 63/64 buckets (F7 126/127), SYN cap, flag gating (UDP with flags), every IPv4 length 0..1500 and above, every TTL, port edges; 20,000 random records with gaps and 20,000 back to back (0 mismatches each); overflow accounting on 2 x 20,000 records (8,023 and 9,966 lost, running sum of `ovf_cnt` exact); pending count saturates at 65,535 after 65,540 losses (known limit, SPEC 8.1); reset mid-stream.
    - Mutation check (RTL restored after each, `cmp` identical): same-bucket case removed → 5 of 6 unit + 1 of 3 chain fail; F3 constant 22194 → 5 of 6 unit fail (chain passes: generated traffic does not hit the affected lengths); pending count not cleared → 2 unit + 1 chain fail; SYN without TCP gating → 5 of 6 unit fail (chain passes: the parser already zeroes UDP flags).
  - **`rtl/mlp_core.sv`** (D-025): 1 MAC per clock, 176 steps, registered ROM read + MAC pipeline, ROMs from `data/model/rom_*.hex`, `SHIFT1` = 7 (checked equal to weights.json). Outputs raw int32 logits plus argmax class (ties to the lowest index); DROP/OVF_ONLY pass through. No confidence (left to `decision_unit`).
  - `tb/test_mlp_core.py` (4 tests, trained weights) + `tb/mlp_custom_checks.py` (13 builds, custom weights from `tb/mlp_custom_sets.py`). Results (*simulated*):
    - **All 2,028 rows of `data/vectors/mlp_vectors.csv`** (22 directed, 16 corner, 800 random, 1,190 test split): **0 logit mismatches, 0 class mismatches**, back to back and again with 30% input gaps and 50% output stalls. The CSV was also re-checked against `golden.infer` in the same run.
    - Custom weight sets (each back to back and with stalls, 0 mismatches): int32_max (accumulator exactly 2^31-1 with shift 7, logits at int32 max, max-1 and min), int32_min (accumulator exactly -2^31, all hidden zero), four all-hidden-zero sets with logit ties 0=1, 1=2, all three, and a strict order, round_half_shift2 (>= 200 exact halves), shift0, saturate (>= 200 hidden at 127 and at 0), random weights with shifts 1, 5, 12, 30.
    - Pass-through: 600 mixed DROP/OVF_ONLY/CLASSIFY entries with garbage features in order, metadata exact. Reset at 12 points of an inference (incl. a stalled finished result): no partial output, next results correct.
    - Mutation check (restored, `cmp` identical): no rounding term → 4/4 trained + 6 custom builds fail; 32-bit instead of 33-bit rounding add → int32_max and random_shift30 fail (trained vectors do not catch it); tie to the higher class → 2/4 trained + 3 tie builds fail; saturation at 255 → 4/4 trained + 5 custom fail.
  - **Cycles per inference (simulated, core_clk)**: **178 clocks** from the edge that takes a CLASSIFY entry to the edge that hands over its result; back to back, **one inference every 179 clocks**. DROP/OVF_ONLY: 1 clock, one every 2 clocks. (SPEC estimate: 176 MAC cycles plus overhead.) Throughput in packets per second is the M4 task; not computed here.
  - **Golden model**: `validate()` now refuses biases whose layer sum could leave int32 (D-025). Precondition only; arithmetic unchanged; trained weights unaffected. New test in `tb/test_golden.py` (16 tests now).
  - `tb/cocotb_run.py`: optional HDL parameters and a per-build folder name (results XML per build).
  - README status updated. Nothing installed.
  - Not done: `decision_unit` (owner instruction). Lint/compile only, no synthesis or timing yet (M4); the combinational path of the MAC stage (8x8 multiply + 32-bit add + requantize + compare) is untested for 200 MHz.

- M3 session 3 (branch `m3-s3` off `m3-s2`, automated builder 5.5; owner instruction D-026):
  - **Part A, confidence rule (owner-approved, D-022/D-026).** SPEC section 7 now says `confidence = min((top − second) >> 8, 127)` (top = chosen class, second = larger of the other two; margin not probability; no longer proposed). D-022 marked owner-approved. `docs/regmap.md` states the formula and that the reset value of `CONF_THRESH` is 13. `model/golden.py` docstring updated (it no longer calls the shift a proposal; arithmetic unchanged). The golden model, `docs/results/` and `data/vectors/` already used shift 8 and `CONF_THRESH` 13: after `python -m model.evaluate` and `python -m model.export_vectors` re-run, `git status` showed no changed file, so nothing was re-exported. `pytest tb/test_golden.py` → 16 passed.
  - **Part B, `rtl/decision_unit.sv`** (D-027). Input: `mlp_core` result (valid/ready, raw int32 logits, class). Output: kind, `pkt_len`, `pkt_id`, `ovf_cnt`, class, 7-bit confidence, `low_conf` (valid/ready, 1 registered stage, 1 clock latency, held while stalled). `CONF_SHIFT` parameter (default 8; the test script refuses to run if it differs from `conf_shift` in the weights file). `conf_thresh` is a 7-bit input. DROP / OVF_ONLY: metadata passes, class/confidence/flag 0. **The class stays in `mlp_core`** (D-025 kept; reasons in D-027).
  - `tb/test_decision_unit.py` (7 tests, *simulated*, seed 12345): (1) **all 2,028 rows of `data/vectors/mlp_vectors.csv`: 2,028 compared, 0 mismatches** (confidence, `low_conf`, class), also with 30% input gaps and 50% output stalls (2,028 compared, 0 mismatches); the CSV is also re-checked against `golden.decide` first. (2) Edge cases, 729 jobs, 0 mismatches: margins at c×256 −1/0/+1 for many c, 255/256/257, 0/1/2, the cap (126×256−1 … 129×256, 2^20, 2^31−1, 2^31, 2^32−2, 2^32−1), ties (two equal at top, all three equal, tie among the lower two, at base values incl. int32 max/min), int32 extremes (top 2^31−1, second −2^31), each with thresholds confidence−1, confidence, confidence+1, 0 and 127. (3) Exhaustive sweep: every confidence 0..127 × every threshold 0..127 = 16,384 combinations, 0 mismatches. (4) 20,000 random logit triples (full int32 range and small ranges), random thresholds, gaps and stalls, 0 mismatches. (5) 1,500 jobs: DROP / OVF_ONLY with garbage logits and class give zeros; a class that is not the largest logit clamps to confidence 0; class 3 gives 0. (6) Reset with a result waiting on a stalled output (4 cut points). Handshake monitor checks that a stalled output does not change.
  - **Chained test** (`tb/decision_chain_tb.sv` + `tb/decision_chain_checks.py`, started by `test_decision_unit.py`): raw frames → eth_parser → l3l4_parser → feature_builder → mlp_core → decision_unit, compared with `model/features.py` + `model/golden.py`. **Practical, done.** Default dataset (traffic_gen seed 1): **9,000 vectors compared, 0 mismatches** (8,349 classified, 651 dropped; 624 flagged `low_conf` at threshold 13). SEED dataset with the final result stalled 50% of cycles: **600 compared, 0 mismatches** (557 classified, 43 dropped, 110 flagged). Compared fields: kind, `pkt_len`, `pkt_id`, `ovf_cnt` (must be 0), class, confidence, `low_conf`. Limit: no async FIFO and one clock in this wrapper (the FIFO is not under test here and without it an entry arriving while `mlp_core` is busy is lost), so the test sends one frame at a time and waits for its result. It checks the chain logic, not throughput or clock crossing.
  - **Mutation check** (by hand; RTL restored after, `cmp` identical to the saved copy, `git status` shows only the new file). Each run: unit tests only. `low_conf` with `<=`: 0 of 7 pass; cap at 128: 5 of 7 fail; no cap: 6 of 7 fail; shift 7: 7 of 7 fail; "second" = smaller of the others: 7 of 7 fail; margin computed in 32 bits (wraps at int32 extremes): `margin_edges` fails; class not zeroed for DROP: 1 of 7 fails; `low_conf` not gated by kind: 1 of 7 fails. My first "32-bit margin" mutation, `33'(top - second)`, passed all tests: Icarus evaluates the inside of a size cast at 33 bits, so it was not a real mutation. The second version, with an explicit 32-bit wire, was caught.
  - **Found on the way (test setup, not RTL):** a missing ROM file is only an error line from `$readmemh` in Icarus and the simulation continues with X logits. The chain test caught this through the X values (relative ROM paths do not resolve from the simulator's folder). Fixed in the test with absolute paths as `-D` macros (`tb/cocotb_run.py` got an optional `defines` argument). Not changed in the RTL; worth a check at M4 (gate-level run).
  - **Lint:** `make lint compile-check` → exit 0, 0 warnings, 7 RTL files, no waivers in `rtl/`. By hand: `verilator --lint-only -Wall --top-module decision_chain_tb tb/decision_chain_tb.sv rtl/*.sv` → no warnings (one `DEFPARAM` waiver in the test wrapper, reason inline); `iverilog -g2012` → no messages.
  - **Not done:** no synthesis or timing of `decision_unit` (M4; its path is a 33-bit subtract, shift, clamp, compare, one register stage). `stats_counters` is M4. `make test` now takes about 7 minutes because of the 9,000-frame chained run (about 3 minutes CPU on its own; wall time varied from 3 to 13 minutes on this machine).

- M3 session 4 (branch `m3-s4`, review fixes before Gate M3). independent M3 review (`M3-review.md` (kept outside the repository)): no must fix. Each item:
  1. Stale statements fixed: README (decision step now exists; M3 row; `make test-full`), SPEC section 5 (feature formulas live in `model/features.py`, `model/golden.py` is the arithmetic reference), PRD (results so far instead of "no results exist"), STATUS (Gate M2 was approved, D-021).
  2. **Evidence gap closed.** New `make test-full`: exports all 8,347 classified packets of the unseen test split (`python -m model.export_vectors --full`, duplicates kept, so the count is exactly the Python test-split count, 8,347) and replays them through `mlp_core` + `decision_unit` (`tb/mlp_decision_tb.sv`, `tb/full_split_checks.py`). Compared per row: three int32 logits and class at the `mlp_core` output, class, confidence, `low_conf` and metadata at the `decision_unit` output. Real output (*simulated*, seed 12345, threshold 13): back to back `rows compared=8347 mismatches=0`, with 30% input gaps and 50% output stalls `rows compared=8347 mismatches=0`, handshake errors 0. About 2 minutes per pass; the whole target about 4 minutes, so it is a separate target and `make test` keeps the 2,028-row set. Gate M3 line in TASKS.md now says exactly this (D-028). Limit: this replay feeds feature vectors straight into `mlp_core`; the raw-frame chain (9,000 + 600 frames) is separate.
  3. Provenance: `docs/results/metrics.json` has a `provenance` block (commit, `code_modified`, seeds, Python/NumPy/Scapy versions), written by `model/evaluate.py`; regenerated from a clean commit (`ef7148d7c010`, `code_modified: false`). Numbers unchanged: the only diff is the new block (accuracy not re-run beyond this regeneration; same values). RTL logs above come from the clean commit `34667d7de1a4`.
  4. `tb/test_golden.py::test_train_and_validation_scenarios_are_disjoint` added (scenario ids and packet streams).
  5. Nits: SYN and IPv4 explained on first use in stories and PRD (word counts: PRD 721 -> 716, stories 1028 -> 1026, not longer); obsolete "0 = literal SPEC" comment fixed in `model/golden.py`; the "about 190 clocks" in `tb/decision_chain_tb.sv` is now labelled an estimate (no measured chain-latency assertion added).
  - Not done: the full replay is not mutation-tested (one-off hand mutation checks were done for the unit tests earlier; this test reuses the same comparison logic with the golden model). No fresh independent rerun by independent reviewer (its sandbox blocked simulation).
  - PR: one PR from `m3-s4` with base `main` contains all M3 work and supersedes #28, #29 and #30 (to be closed unmerged by the owner).

### Gate M3 check (TASKS.md line): which parts are met, with evidence
All numbers *simulated* on *synthetic* traffic; this is not real-world accuracy.
1. **Int8 accuracy on unseen data: met.** Test split (generator seed 2, never used for tuning), 8,347 classified packets: int8 **95.50%**, float 95.45%; int8 and float agree on 99.90% of classes. Source: `docs/results/results.md`, `metrics.json` (regenerated this session: files unchanged).
2. **Confusion matrix: met.** Float and int8 matrices, per-class precision and recall in `docs/results/results.md` (int8 recall: normal 94.45%, port_scan 95.50%, syn_flood 97.24%).
3. **Margin-vs-accuracy table: met.** Bins and chart in `docs/results/` (`margin_vs_accuracy.png`). `CONF_THRESH` = 13 chosen on the validation split only; reported once on the test split: 13.9% of packets flagged, unflagged 97.86% correct (just under the 98% validation target), flagged 80.93%. The synthetic-data caveat is on the first lines of `results.md`. Confidence is called a margin, not a probability, there and in SPEC, README and regmap.
4. **RTL equals golden, zero mismatches (*simulated*): met for both sets named in the TASKS.md Gate M3 line.** (a) `data/vectors/mlp_vectors.csv`, 2,028 rows (1,190 from the test split, the rest directed, corner and random cases): `mlp_core` logits and class 2,028 of 2,028; `decision_unit` confidence, `low_conf` and class 2,028 of 2,028. (b) Every classified packet of the unseen test split, 8,347 rows, through `mlp_core` + `decision_unit` (`make test-full`): 8,347 of 8,347, 0 mismatches, also with stalls. Also: feature_builder entries 9,000 + 600 generated frames vs `model/features.py`; full raw-frame chain 9,000 + 600 vs the models, 0 mismatches (generated data differs from the test split; the chain run uses no async FIFO).
5. **Lint clean: met.** `make lint compile-check` exit 0, 0 warnings on all 7 RTL files.
independent M3 review: no must fix; should fix and nits closed in session 4. Open before the gate: PM gate run. The review file is held in `M3-review.md` (kept outside the repository) (not in `reviews/`).

### Toolchain versions (real output of `scripts/toolchain_check.sh`, 2026-11-13, session 5, exit 0)
```
make:      GNU Make 4.4.1
Verilator: Verilator 5.055 devel rev v5.054-219-g12ce86083 (mod)
Icarus:    Icarus Verilog version 14.0 (devel) (s20260301-966-g3992ea6a3-dirty)
Yosys:     Yosys 0.70+385 (git sha1 098e46a36, Release, Clang /usr/bin/clang++ 21.1.8)
OpenSTA:   3.2.0
Python:    Python 3.13.5
cocotb:    2.1.0
scapy:     2.8.0
numpy:     2.5.4
liberty:   present, sha256 8d540a4d4cf6d09d...
```
Reported in session 2: a throwaway RTL module (deleted, not committed) built in `make lint` and `make compile-check` with Verilator and Icarus: unverified, no saved output. Since then the RTL exists and `make lint compile-check` has been run on it (see the session logs above and M2 session 6).

## Next
1. Owner: look at `docs/index.html` (open from disk) and decide on GitHub Pages (serve from `/docs` on `main`). The source links point at `github.com/bavafa/neurogate/blob/main/...` and only resolve once the repo is public.
2. Owner: decide the five history/identity items under M5 session 3 (nothing was rewritten).
3. Gate M5 (PM), owner decision on going public. One PR from `m5-s4` holds all M5 work and supersedes draft PRs #38, #39, #40.

## Open problems / blockers
- `make` is not on the default PATH. GNU Make 4.4.1 is at `$NG_TOOLS/bin/make` (user space); run `. scripts/env.sh` first (PM confirmed this works, D-015). Without it, run the tests directly: `source .venv/bin/activate; SEED=12345 python3 tb/test_<name>.py` for each file.
- cocotb runs in the OSS CAD Suite Python 3.11, not `.venv` (no shared libpython for 3.13). Anything imported inside the simulator must work on 3.11 without Scapy (D-012).
- This cocotb/Icarus build segfaults at shutdown if a test ends with a signal write still queued. Testbenches must stop background coroutines and wait one clock edge before returning (D-012).
- Nangate45 liberty: resolved, see D-005. The file is gitignored; a fresh clone must run `lib/fetch_nangate45.sh`.
- OpenSTA was built from source from the GitHub master branch (no commit pinned; version string 3.2.0). A later rebuild may differ.
- The `-dirty` / `devel` Verilator and Icarus builds come from the OSS CAD Suite nightly; versions are recorded above.
- `make test` runs every `tb/test_*.py`; cocotb flow decided in D-012.
- None on the spec: bias width resolved by D-006 (owner, int32).
- D-004 is a builder decision filling in details D-001e left open (FIFO entry layout, overflow count carried in entries, `pkt_id` added to the result stream, SLVERR on unmapped addresses, wrapping counters). Owner may override.

## Assumptions (headless session, no owner available)
- Ticked the regmap box as well as the two boxes the owner named, because the contributor rules say to tick finished boxes.
- Removed the Wireshark line from SPEC.md section 9 too, so SPEC and TASKS agree (logged in D-003).
- Treated the clock values 156.25 / 200 MHz as decided: D-001a and D-001d state them and D-001 removes all `[PROPOSED]` tags in section 8.
- `LAST_RESULT` layout chosen byte-aligned: class in [1:0], confidence in [15:8].
- The independent M1 review is in `reviews/M1-review.md`. The independent M2 review is held in `M2-review.md` (kept outside the repository) (the builder does not edit `reviews/`); session 6 answers it.

## Proposed task changes (M4 session 5)
- TASKS.md M4 OpenSTA line says "add a pipeline stage" when timing fails. Suggested wording: "fix the critical path (pipeline stage, or buffering/sizing in synthesis when the cause is fanout)". Not applied.

## Proposed task changes
- M1 "Repo skeleton": the line should name `compile-check` (D-001c). Implemented anyway.
- M1 "Toolchain check script": the script also checks GNU make, NumPy and the liberty file (beyond the listed tools).

## Proposed task changes (session 5)
- TASKS.md M3 line "Margin vs. measured accuracy: bin the confidence margin on the unseen test set ... pick the default `CONF_THRESH` from it (see D-002)": reword to pick the threshold on a validation split carved from training data, then report the table once on the test set (D-008). Not applied; the file belongs to the project manager.
- TASKS.md M3 should include a task to create the validation split.

## Proposed task changes (M2 session 6)
- TASKS.md line 32 (Gate M2) says "parsers agree with Scapy on 100% of generated frames". Suggested wording: "parsers agree with the Python model on 100% of generated frames and with Scapy on every frame Scapy decodes fully; frames Scapy cannot decode are compared on keep/drop only".

## Proposed task changes (M2 session 1)
- Still open from session 5: add an M3 task to create the validation split (D-008). The builder may not add tasks (the contributor rules), so it is not added.
- The generator CSV already carries `scenario` and `scenario_seed` for the split.

## Milestone table
| Milestone | State | independent review | Gate |
|---|---|---|---|
| M1 | gate approved by owner 2026-11-13 (tag `M1`: owner/PM to confirm) | `reviews/M1-review.md`: 3 must-fix addressed | approved |
| M2 | gate approved by owner (D-021); merged to `main` in PR #27 | `M2-review.md`: 2 must fix, 5 should fix, 5 nits addressed | approved |
| M3 | 9 of 9 tasks done; independent review: no must fix, should fix and nits closed (session 4); Gate M3 evidence below; waiting for the PM gate | `M3-review.md` (kept outside the repository): 0 must fix | |
| M4 | gate approved by owner (D-041); merged in PR #37 | `M4-review.md` (kept outside the repository) | approved |
| M5 | in progress: README, module notes, limitations (session 1) and results page (session 2) done; clean-clone check, scan open | | |

## How to resume
```
cd <your clone of the repository>
git checkout m5-s4  # holds all M5 work (supersedes draft PRs #38, #39, #40)
make results-replay-log                          # after make test-full: refresh the replay log, then make results-page
make results-check                               # < 1 s: docs/index.html equals a fresh build from result files
make results-capture && make results-page        # only if the design or the throughput test changed
python -m model.train && python -m model.quantize && python -m model.evaluate && python -m model.export_vectors   # regenerate M3 outputs (~1 min)
SEED=12345 python3 tb/test_golden.py     # ~50 s
source .venv/bin/activate
. scripts/env.sh    # puts ${NG_TOOLS:-$HOME/tools}/bin (make), OSS CAD Suite and OpenSTA on PATH
make toolchain && make lint compile-check cdc-check test
make synth && make gls && make synth-xilinx     # ~10 s, ~75 s, ~10 s
make sta                                        # ~2 s; reports in synth/reports/sta*.txt; exits 1 (removal violation, D-038)
make cdc-check                                  # structural clock-crossing check (docs/cdc.md)
SEED=12345 python3 tb/test_cdc_check.py          # ~10 s: real RTL passes, 7 planted faults fail
SEED=12345 python3 tb/test_neurogate_top.py      # ~10 s: two-clock smoke test of the whole chip
SEED=12345 python3 tb/test_neurogate_top_stress.py   # ~30 s: bad packets, backpressure, overflow, hard and soft reset
SEED=12345 python3 tb/test_neurogate_top_full.py     # ~19 s: 900 generated frames (FULL_PER_SCENARIO=300 for all 9,000, ~3 min)
# or without make:
SEED=12345 python3 tb/test_eth_parser.py
SEED=12345 python3 tb/test_l3l4_parser.py   # ~35 s, includes the 9600-frame cross-check
SEED=12345 python3 tb/test_skid_fifo.py
SEED=12345 python3 tb/test_feature_builder.py   # ~50 s, unit + 9,600-frame chain
SEED=12345 python3 tb/test_mlp_core.py          # ~100 s, 2,028 vectors + 13 custom builds
make test-full                                  # ~4 min: all 8,347 unseen test packets, mlp_core + decision_unit
SEED=12345 python3 tb/test_decision_unit.py     # ~4-13 min: unit tests in seconds, then the 9,600-frame chain
SEED=12345 python3 tb/test_async_fifo.py   # ~13 s
SEED=12345 python3 tb/test_features.py
SEED=12345 python3 tb/test_traffic_gen.py
cat STATUS.md
```

## Proposed task changes (M2 session 5)
- The M4 "CDC review" task could name what simulation cannot show for the async FIFO (Gray encoding from a flop, two synchronizer flops with no logic between, max-delay constraints on the Gray buses), since tests pass with those broken (D-019). Not applied; the file belongs to the project manager.

## Proposed task changes (M3 session 1)
- Add an M3 task for the `conf_shift` (confidence right shift) once the owner decides: it changes SPEC section 7 and `decision_unit`.
- The M3 gate line says "RTL equals golden on all test vectors"; the vector file is `data/vectors/mlp_vectors.csv` (2,028 rows) and the full test-split features can be regenerated with `model/dataset.py`.

## Proposed task changes (M3 session 2)
- M3 `decision_unit` line: once the owner decides D-022, say which confidence rule it implements. `mlp_core` already outputs the argmax class (D-025); the line could read "confidence, `low_conf`, ties and threshold edge".
- M3 gate "RTL equals golden on all test vectors": so far `mlp_core` (2,028 vectors) and `feature_builder` (9,600 frames); confidence and `low_conf` are only checked once `decision_unit` exists.

## Open questions for the PM (M4 session 1)
- None blocking. Register map and SPEC agree. Builder choices to confirm or override (D-030, marked [PROPOSED]): reserved entry kind 3 and CLASSIFY with class 3 only add the overflow count; one outstanding AXI-Lite transaction per direction (write needs address and data valid together).
- The regmap says an address above bit 5 is not decoded; the register block therefore has a 6-bit address input and the top level must connect only `awaddr[5:0]` / `araddr[5:0]`.

## Proposed task changes (M4 session 1)
- None.

## Proposed task changes (M3 session 3)
- M3 `decision_unit` line could read "confidence (`min((top - second) >> 8, 127)`), `low_conf`, ties and threshold edge" now that the rule is approved.
- M3 gate: "RTL equals golden on all test vectors" could name what was compared: the 2,028-row vector file for `mlp_core` and `decision_unit`, plus a chained run on generated traffic.
- M4: add a gate-level check that a missing ROM file fails loudly (Icarus only prints an error and goes on with X).

## Proposed task changes (M4 session 2)
- M4 "Verilator lint: zero warnings or listed waivers": lint is clean now on all 11 RTL files, with inline waivers (each with a reason). Not ticked, because later M4 work may still add RTL (a pipeline stage for timing). Suggest ticking it at the end of M4 together with a listed waiver table.
- SPEC section 2 says the top has no logic beyond reset synchronizers; the top needs a small fork from `decision_unit` to the result stream and the counters. Proposed wording in D-032 [PROPOSED].
- Suggestion (not built): the M4 timing task could name the crossing-path constraints (max delay on the two Gray buses and the FIFO data path) that `docs/cdc.md` lists as not done.

## Proposed task changes (M4 session 3)
- None to the task list. [PROPOSED] D-034: the full-traffic test costs about 3 minutes CPU (7 minutes wall seen on a loaded machine) inside `make test`; the owner may prefer to move it to `make test-full`.
- Suggestion (not built): a top-level test that predicts overflow exactly would need a cycle model of the FIFO and `mlp_core`; the current checks are exact only for the sum of counters and for every surviving beat.
