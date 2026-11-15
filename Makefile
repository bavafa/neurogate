# NeuroGate Makefile. Simulation-only project; see the contributor rules and SPEC.md.
#
# Targets (M1 state: lint, compile-check and test run on an empty tree and exit 0;
# synth, sta and gls are stubs filled in at M4):
#   make toolchain      print tool versions (scripts/toolchain_check.sh)
#   make lint           verilator --lint-only -Wall on every rtl/*.sv
#   make compile-check  compile every rtl/*.sv with Icarus and with Verilator (D-001c)
#   make cdc-check      structural clock-crossing check of neurogate_top
#                       (Yosys netlist + scripts/cdc_check.py; see docs/cdc.md)
#   make test           run every tb/test_*.py (fixed seed, printed in the log)
#   make test-full      slower checks, not part of make test: (1) replay all classified
#                       unseen test-split packets through mlp_core + decision_unit,
#                       (2) the whole-chip end-to-end test on all 9000 generated frames
#                       (make test runs the same test on 900 frames)
#   make synth          Yosys -> Nangate45 gate netlist + reports in synth/ (*synthesized (estimate)*)
#   make synth-xilinx   Yosys synth_xilinx Artix-7 resource count (*estimate*, not Vivado)
#   make gls            gate-level simulation: Yosys netlist + Nangate45 cell models, Icarus,
#                       same smoke vectors as the RTL run (needs `make synth` first)
#   make results-capture  re-run the throughput test with a wave dump; writes the
#                       committed log and trimmed waveform used by the results page
#   make results-replay-log  keep the split-replay result lines (after make test-full) for the page
#   make results-page   regenerate docs/index.html from committed result files
#   make results-check  fail if docs/index.html differs from what the result files give
#   make sta            OpenSTA timing of the Nangate45 netlist (needs `make synth` first),
#                       constraints in synth/neurogate_top.sdc (*synthesized (estimate)*)
#
# Tool locations come from scripts/env.sh (adds .venv and the tool install folder to PATH).

SHELL      := /bin/bash
ENV        := source scripts/env.sh
RTL        := $(sort $(wildcard rtl/*.sv))
TESTS      := $(sort $(wildcard tb/test_*.py))
SEED       ?= 12345

.PHONY: all toolchain lint compile-check cdc-check test test-full synth synth-xilinx sta gls clean \
        results-capture results-replay-log results-page results-check

all: lint compile-check cdc-check test results-check

toolchain:
	@scripts/toolchain_check.sh

# One module per file, file name = module name (the contributor rules), so the top module
# of each lint/compile run is the file's base name.
lint:
	@$(ENV); \
	if [ -z "$(RTL)" ]; then echo "lint: no RTL files yet (nothing to check)"; exit 0; fi; \
	rc=0; for f in $(RTL); do m=$$(basename $$f .sv); \
	  echo "lint: $$f"; verilator --lint-only -Wall --top-module $$m $(RTL) || rc=1; \
	done; echo "lint: waivers in the RTL (each has a reason in a comment above it):"; \
	grep -n "lint_off" rtl/*.sv; echo "lint: exit $$rc (0 = no warning other than the waivers above)"; exit $$rc

compile-check:
	@$(ENV); \
	if [ -z "$(RTL)" ]; then echo "compile-check: no RTL files yet (nothing to check)"; exit 0; fi; \
	rc=0; for f in $(RTL); do m=$$(basename $$f .sv); \
	  echo "compile-check: $$f (iverilog)"; iverilog -g2012 -s $$m -o /dev/null $(RTL) || rc=1; \
	  echo "compile-check: $$f (verilator)"; verilator --lint-only --top-module $$m $(RTL) || rc=1; \
	done; exit $$rc

# Structural CDC check of the whole chip (self-check, not a commercial sign-off).
# Yosys (slang frontend) elaborates rtl/*.sv into a JSON netlist; the script
# checks every crossing (Gray pointers, two-flop synchronizers, reset
# synchronizers) and exits 1 on any violation. tb/test_cdc_check.py (run by
# `make test`) plants known CDC faults in copies of the RTL and checks that
# each one makes this check fail.
cdc-check:
	@$(ENV); python3 scripts/cdc_check.py sim_build/cdc/neurogate_top.json --rtl rtl

# Each tb/test_*.py is a self-running script; the seed is exported and printed.
# cocotb tests start from .venv and run the simulation in the OSS CAD Suite
# Python through tb/cocotb_run.py (D-012); tb/cocotb_run.py itself is not a test.
# Logs are kept per run: results/<commit>[-dirty]/<suite>.log (+ <suite>.xml from
# cocotb, and run_info.txt with seed, commit and tool versions). The folder is
# named by the git commit, so a log can be tied to the code that produced it;
# "-dirty" means uncommitted changes were present. results/ is not committed.
test:
	@$(ENV); \
	if [ -z "$(TESTS)" ]; then echo "test: no tests yet (nothing to run)"; exit 0; fi; \
	rev=$$(git rev-parse --short=12 HEAD 2>/dev/null || echo nogit); \
	if [ -n "$$(git status --porcelain --untracked-files=no 2>/dev/null)" ]; then rev=$$rev-dirty; fi; \
	dir=results/$$rev; mkdir -p $$dir; export NG_RESULTS_DIR=$$PWD/$$dir; \
	{ echo "commit: $$(git rev-parse HEAD 2>/dev/null)"; echo "state: $$rev"; echo "seed: $(SEED)"; \
	  echo "date: $$(date -u +%FT%TZ)"; scripts/toolchain_check.sh 2>&1; } > $$dir/run_info.txt; \
	echo "test: seed=$(SEED) logs in $$dir"; rc=0; for t in $(TESTS); do \
	  n=$$(basename $$t .py); echo "test: $$t"; \
	  SEED=$(SEED) python3 $$t 2>&1 | tee $$dir/$$n.log; \
	  s=$${PIPESTATUS[0]}; echo "exit: $$s" >> $$dir/$$n.log; [ $$s -eq 0 ] || rc=1; \
	done; echo "test: overall exit $$rc" | tee $$dir/summary.txt; exit $$rc

# (1) Full unseen test split (8,347 rows) through mlp_core + decision_unit, vs the
# golden model. (2) The whole-chip end-to-end test with FULL_PER_SCENARIO=300, i.e.
# all 9000 generated frames (about 3 minutes of CPU). Both run even if the first
# fails; exit 1 if either fails. Same results folder naming as `make test`.
test-full:
	@$(ENV); \
	rev=$$(git rev-parse --short=12 HEAD 2>/dev/null || echo nogit); \
	if [ -n "$$(git status --porcelain --untracked-files=no 2>/dev/null)" ]; then rev=$$rev-dirty; fi; \
	dir=results/$$rev; mkdir -p $$dir; export NG_RESULTS_DIR=$$PWD/$$dir; \
	echo "test-full: seed=$(SEED) log in $$dir/full_split.log"; \
	echo "rtl_tb_at_run: $$(if [ -n "$$(git status --porcelain --untracked-files=no -- rtl tb 2>/dev/null)" ]; then echo modified; else echo unmodified; fi)" > $$dir/full_split.log; \
	SEED=$(SEED) python3 tb/full_split_run.py 2>&1 | tee -a $$dir/full_split.log; \
	s1=$${PIPESTATUS[0]}; echo "exit: $$s1" >> $$dir/full_split.log; \
	echo "test-full: 9000-frame whole-chip test, log in $$dir/test_neurogate_top_full_9000.log"; \
	FULL_PER_SCENARIO=300 SEED=$(SEED) python3 tb/test_neurogate_top_full.py 2>&1 | tee $$dir/test_neurogate_top_full_9000.log; \
	s2=$${PIPESTATUS[0]}; echo "exit: $$s2" >> $$dir/test_neurogate_top_full_9000.log; \
	echo "test-full: split replay exit $$s1, 9000-frame chip test exit $$s2"; \
	[ $$s1 -eq 0 ] && [ $$s2 -eq 0 ]

# Yosys maps neurogate_top to the Nangate45 typical-corner library (D-001d). Writes
# synth/out/neurogate_top_netlist.v (not committed) and synth/reports/ (cell and area
# report, check result, full Yosys log). Fetches the liberty file first if missing.
synth:
	@synth/run_synth.sh

# Artix-7 resource count from Yosys synth_xilinx. An estimate, not a Vivado result.
synth-xilinx:
	@$(ENV); mkdir -p synth/reports; \
	yosys -m slang -l synth/reports/artix7_yosys.log synth/synth_artix7.ys > /dev/null; rc=$$?; \
	echo "synth-xilinx: yosys exit $$rc"; \
	[ $$rc -eq 0 ] && sed -n '/cells$$/,$$p' synth/reports/artix7_cells.txt; exit $$rc

# OpenSTA timing of the netlist from `make synth` against synth/neurogate_top.sdc
# (both clocks at the SPEC frequencies). Reports in synth/reports/sta*.txt.
# *synthesized (estimate)*: Nangate45 typical liberty, ideal clocks, no place and
# route. Exits 1 if any check in the main (no wire load) run is violated.
sta:
	@synth/run_sta.sh

# Gate-level simulation of the netlist from `make synth` (*simulated*). Two runs:
# the same 120 frames and checks as the RTL smoke test tb/test_neurogate_top.py,
# then the bad-packet set of tb/test_neurogate_top_stress.py (same checks as the RTL
# bad-packet test). Logs: synth/reports/gls.log and gls_bad.log. Exit 1 if either fails.
gls:
	@$(ENV); [ -f synth/out/neurogate_top_netlist.v ] || { echo "gls: run make synth first"; exit 1; }; \
	synth/gen_cell_models.sh && SEED=$(SEED) python3 tb/gls_run.py 2>&1 | scripts/strip_paths.sh | tee synth/reports/gls.log; \
	s1=$${PIPESTATUS[0]}; \
	SEED=$(SEED) python3 tb/gls_run.py bad 2>&1 | scripts/strip_paths.sh | tee synth/reports/gls_bad.log; \
	s2=$${PIPESTATUS[0]}; echo "gls: smoke exit $$s1, bad-packet exit $$s2"; \
	[ $$s1 -eq 0 ] && [ $$s2 -eq 0 ]

# Results page (docs/index.html). The page script reads only committed files
# (docs/results/, synth/reports/, synth/neurogate_top.sdc) and never runs a tool, so
# results-check is fast. It fails if the committed page differs from a fresh build,
# which catches a number edited by hand or a result file changed without a rebuild.
# results-capture runs a real simulation (the throughput test with NG_WAVES=1) and
# rewrites docs/results/throughput_run.log and docs/results/wave_throughput.vcd.
results-capture:
	@$(ENV); python3 scripts/capture_waveform.py

# results-replay-log copies the result lines of the newest `make test-full` split replay
# into docs/results/full_split_replay.log (run `make test-full` first).
results-replay-log:
	@python3 scripts/save_replay_log.py

results-page:
	@python3 scripts/build_results_page.py

results-check:
	@python3 scripts/build_results_page.py --check

clean:
	rm -rf sim_build results.xml __pycache__ tb/__pycache__ model/__pycache__
