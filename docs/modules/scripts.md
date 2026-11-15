# scripts/: helper scripts

Small tools that support the build.

- **Environment** (`env.sh`): puts the tools installed in the user's home directory on the search path.
- **Tool check** (`toolchain_check.sh`): prints the version of every tool in use, so a log can say what produced it.
- **Clock-crossing check** (`cdc_check.py`, run by `make cdc-check`): reads the synthesized structure of the whole chip and checks that every signal crossing between the two clocks is one of the three allowed kinds: a Gray-coded queue pointer through two flip-flops, queue storage protected by the queue's rules, or a reset synchronizer. It is a structural check plus a written review, not a formal proof and not a commercial sign-off. The review is in `docs/cdc.md`.
- **Results page** (`build_results_page.py`, run by `make results-page`): builds the one-page results site, `docs/index.html`, from the committed result files. It types no number itself: each value is read from a result file, and each table and figure names that file and says whether it is simulated or an estimate. `make results-check` rebuilds the page and fails if the committed page differs, so a number changed by hand is caught.
- **Replay log saver** (`save_replay_log.py`, run by `make results-replay-log`): after the slow test run, copies only the result lines of the full test-packet replay into a small committed log. It takes the random seed from the run itself and refuses to write if the run did not record whether the chip code and tests were modified, so the log cannot claim more than the run showed.
- **Path stripper** (`strip_paths.sh`, used by `make gls`): replaces the build machine's folder names in saved logs with placeholders, so published logs do not reveal where they were produced.
- **Waveform capture** (`capture_waveform.py`, run by `make results-capture`): re-runs the whole-chip throughput simulation with signal recording on. It keeps the test's result lines and a trimmed copy of the recorded signals in `docs/results/`, which the results page draws from.
