# Site checklist: numbers against files

Repo snapshot: commit `fba2882` (the only commit in the clone used). Run on 2026-11-15 with Python 3.9.6, NumPy 2.0.2, Scapy 2.8.0, Node 26.9.0. The model's own provenance block (`metrics.json`) records Python 3.13.5 and NumPy 2.5.4; the export asserts below still hold, so the data does not depend on that difference.

## Checked by a program (export script asserts or the node test)

| Number on the page | Checked against | How |
|---|---|---|
| 9,000 frames, 8,347 classified, 653 dropped | `docs/results/metrics.json` (`frames_dropped_by_parser`) and a rebuild of the test split | Export rebuilds the stream with the model's generator and parser and asserts equal counts and equal features and labels to `model/dataset.py`. |
| Confusion matrix, precision, recall, 95.50% | `docs/results/metrics.json` | Export runs `model/golden.py` on all 8,347 packets and asserts the matrix equals the file. The page reads the file itself. |
| 99.90% float and int8 agreement, float accuracy, margin bins, 97.86% and 80.93% | `docs/results/metrics.json` | Displayed straight from the file (copied to `site/data/metrics.json`). Not recomputed. |
| 653 dropped by reason (277, 170, 81, 47, 45, 33) | Model parser run | Computed by the export; sum asserted equal to 653. |
| Feature distributions per class | Model parser and feature window | Computed by the export. No reference file; internal consistency only (features equal `model/dataset.py`, F6 and F7 equal the window contents on the 15 samples). |
| JavaScript replay equals Python | `site/test/all_packets.json` | `node site/test/golden.test.mjs`: 8,347 of 8,347 rows equal (logits, class, confidence, low_conf); 15 sample packets equal (features, 16 hidden values, logits, class). Rerun after removing the rounding term: the test failed, as it should. |
| Replay "0 of 8,347 mismatches", two runs, 30% gaps and 50% stalls | `docs/results/full_split_replay.log` | Export parses the log, asserts rows = 8,347 and mismatches = 0 for both runs. |
| Gate-level 110/110 and 176/176 beats, 0 mismatches | `synth/reports/gls.log`, `gls_bad.log` | Parsed by the export. |
| 18,907 cells, 34,866 um2, 3,746 flip-flops, 48.69% sequential | `synth/reports/cells.txt` | Parsed by the export. Matches README and `synth/README.md`. |
| 2,445 LUTs, 1,710 FFs, 2 DSP48E1, 35 RAM primitives | `synth/reports/artix7_cells.txt` | Parsed by the export. Matches `synth/README.md`. |
| Timing slacks (14 checks, before, after, wire-load) | `synth/reports/sta_before.txt`, `sta.txt`, `sta_wireload.txt` | Parsed from the `SUMMARY` lines by the export. Matches the table in `synth/README.md`. |
| 179 clocks per packet, 1,117 k packets/s, 2,865 Mbit/s, 187 clocks, 42.0 net clocks per frame | `docs/results/throughput_run.log` | Parsed by the export. |

## Checked by reading, not by a program

| Statement on the page | Source read |
|---|---|
| Lint: 11 files, 4 waivers, no warnings | `synth/README.md`. No lint log is committed. |
| Clock crossings: 6 listed, structural check plus review | `docs/cdc.md` |
| Overflow behaviour | `SPEC.md` sections 2 and 8.1, header of `rtl/feature_builder.sv`, header of `rtl/neurogate_top.sv`, `docs/modules/*.md` |
| Limitations list | `README.md` Limitations |
| 10 Gb/s input port (64 bit at 156.25 MHz) | `SPEC.md` section 3.1 |
| Input-side frame rate (about 3.7 M frames/s) | **Derived on the page** from 156.25 MHz and 42.0 net clocks per frame in the log. Not a number in a repo file. Labelled "derived". |

## Could not verify, or differs from the brief

1. **Per-packet RTL labels do not exist in the repo.** `full_split_replay.log` holds totals only, and the per-packet output is gitignored. Icarus, cocotb, Verilator, Yosys and OpenSTA are not installed here, so I could not regenerate it. The page shows the recorded total (0 mismatches on 8,347 rows) and says plainly that it is not a per-packet record. If you want true per-packet RTL labels, run `make test-full` on a machine with the toolchain and add a small dump to `tb/full_split_checks.py`.
2. **The replay covers `mlp_core` and `decision_unit`, not the whole design.** The README row says "chip output vs Python model"; the log's testbench is `mlp_decision_tb`. The page states the narrower scope. The whole-design 9,000-frame result exists only as text in `STATUS.md` (no committed log).
3. **No simulation, synthesis, STA or lint was re-run.** Every RTL and tool result is the committed log or report, parsed. Only the Python model and the JavaScript replay were re-executed.
4. **Reviewer name.** The repo says "independent reviewer" and never names it. The page uses "OpenAI Codex" and "Claude Code" as your brief says. The planning agent is not named.
5. **M2 to M5 review files are not in the repo**, so the page cannot quote them. Rows are labelled as the builder's records.
6. **Milestone M5** is "in progress" in the repo snapshot (final review not recorded). The page says so.
7. **Dates.** The decision log supports Nov 13 and Nov 14. "Nov 13 to 15" is from your brief.
8. **Contact section** has the email alias and links to your portfolio. The earlier About paragraphs were removed at your request.
9. **Email alias** was given by you. I did not check that it receives mail.
10. **Vercel deploy not tested** (no account access here). The folder is plain static files. Links to GitHub and to the Evidence page returned HTTP 200 on 2026-11-15; linked repo file paths were checked to exist in the clone.
11. **Not tested:** screen readers; browsers other than the embedded one; narrow-width layout was checked at 375 px for horizontal overflow only. The feature-distribution charts use three class colours that are also labelled in the legend and in the caption text, but they have not been checked with a colour-blindness simulator.
12. **Colour palette** is sampled from the colours in use on amd.com/en/solutions/ai.html (near-black, white, grey, teal `#00c2de`). Palette only: no AMD logo, name or font. The site is dark-only. Contrast was chosen by eye and has not been run through a contrast checker.
13. **Wording pass.** At your request, self-critical phrasing was softened. Facts were kept: simulation and estimates only, synthetic data, RTL generated by Claude Code and reviewed by OpenAI Codex, the reset-removal figure, and what the replay log does and does not contain (stated in section 1 above and on the page).
