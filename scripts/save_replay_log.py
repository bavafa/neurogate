"""Keep the result lines of the full test-split replay as a small committed log.

``make test-full`` replays every classified unseen test packet (8,347 of them)
through the network core and decision unit, twice (back to back, then with input
gaps and output stalls), and compares each result with the Python golden model.
Its full log lives in ``results/<commit>/full_split.log``, which is gitignored
(large, and different on every run). This script copies only the result lines
into ``docs/results/full_split_replay.log`` so the results page can show them
and a reader can check them in the repository.

Usage (after ``make test-full``):

    python3 scripts/save_replay_log.py [results/<commit>/full_split.log]

With no argument it uses the newest ``results/*/full_split.log``. It refuses to
write if the run did not end with exit 0 or the log lacks the three result lines. It copies
what the log says; it never edits a number.
"""
import glob
import os
import re
import sys
from typing import List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "results", "full_split_replay.log")


def newest_log() -> str:
    """Return the newest results/*/full_split.log, or exit with a message."""
    logs = sorted(glob.glob(os.path.join(ROOT, "results", "*", "full_split.log")),
                  key=os.path.getmtime)
    if not logs:
        sys.exit("save_replay_log: no results/*/full_split.log; run make test-full first")
    return logs[-1]


def main(argv: List[str]) -> int:
    """Copy the replay's result lines into docs/results/full_split_replay.log."""
    src: Optional[str] = argv[1] if len(argv) > 1 else None
    path = os.path.abspath(src) if src else newest_log()
    rev = os.path.basename(os.path.dirname(path))
    text = open(path, encoding="utf-8").read()
    keep = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()
            if "FULL SPLIT" in ln or ln.startswith("full_split_checks:")]
    # The runner appends "exit: N" itself.
    ex = re.findall(r"^exit: (\d+)$", text, re.M)
    if len(keep) != 3 or not ex or ex[-1] != "0":
        sys.exit(f"save_replay_log: unexpected log (result lines {len(keep)}, exit {ex}); "
                 "not writing")
    # Facts recorded when the run happened (the seed is printed by the test itself,
    # the rtl/ and tb/ state by `make test-full` on the first line of the log).
    seeds = set(re.findall(r"seed=(\d+)", text))
    state = re.findall(r"^rtl_tb_at_run: (\w+)$", text, re.M)
    if len(seeds) != 1 or not state:
        sys.exit(f"save_replay_log: need one seed and an rtl_tb_at_run line (seeds {seeds}, "
                 f"state {state}); rerun make test-full; not writing")
    header = [
        "# Full test-split replay output, kept for the results page (simulated, RTL, Icarus).",
        f"# command: make test-full (first part: python3 tb/full_split_run.py), SEED={seeds.pop()}",
        f"# source: run on commit {rev} (a -dirty suffix means uncommitted edits at the time);"
        f" rtl/ and tb/ {state[0]} at run time",
        "# written by scripts/save_replay_log.py; only result lines are kept",
    ]
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(header + keep + [f"exit: {ex[-1]}"]) + "\n")
    print(f"wrote {os.path.relpath(OUT, ROOT)} from results/{rev}/full_split.log")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
