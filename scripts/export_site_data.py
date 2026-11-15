#!/usr/bin/env python3
"""Export the data the case-study site needs (site/data/*.json, site/test/all_packets.json).

Run from the repository root with the project's Python environment (needs NumPy and Scapy,
as the model does):

    python3 scripts/export_site_data.py

What it does
  1. Rebuilds the unseen test split exactly as ``model/dataset.py`` does (same seeds, same
     scenario order), but keeps the raw frame bytes and the 64-packet window of each packet.
     It asserts that the features and labels equal ``load_splits()["test"]``.
  2. Runs the Python golden model (``model/golden.py``) on every classified packet and checks
     the totals against ``docs/results/metrics.json`` (accuracy, confusion matrix, 653 drops).
  3. Picks about 20 packets (fixed seed) and writes all intermediate values for them.
  4. Parses the committed logs and reports (replay log, throughput log, STA, cells, GLS) into
     ``impl.json`` and ``verify.json``. Every parse asserts that it found what it expects, so a
     changed report format stops the export instead of producing a wrong number.

RTL result per packet: the committed replay log (docs/results/full_split_replay.log) holds only
totals (rows compared, mismatches), not one RTL label per packet. This script therefore records
those totals and does NOT invent per-packet RTL labels. With 0 mismatches the RTL class equals
the golden-model class on every row; the site says so in those words.

Everything here is derived from files in the repo; nothing is typed in by hand except the
sample-picking rule. All values stay *simulated* (synthetic data) or *estimate* (synthesis).
"""
from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
import sys
from collections import Counter
from typing import Any, Dict, List, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from model import golden  # noqa: E402
from model.dataset import (PACKETS_PER_SCENARIO, SHUFFLE_SEED, TEST_PER_CLASS,  # noqa: E402
                           TEST_SEED, load_splits)
from model.features import (KIND_CLASSIFY, FeatureWindow, Packet, parse_frame,  # noqa: E402
                            port_bucket, is_syn_only)
from model.traffic_gen import LABEL_NAMES, generate  # noqa: E402

SITE = os.path.join(ROOT, "site")
DATA = os.path.join(SITE, "data")
PICK_SEED = 20261115          # fixed seed for the sample choice
BYTES_SHOWN = 96              # raw bytes sent to the page per sample (frame length is kept)


def rd(path: str) -> str:
    """Read a repo file as text."""
    with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
        return fh.read()


def dump(path: str, obj: Any) -> None:
    """Write compact JSON with sorted keys (stable diffs)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, separators=(",", ":"), sort_keys=True)
        fh.write("\n")


def must(pattern: str, text: str, what: str, flags: int = 0) -> re.Match:
    """Regex search that stops the export with a clear message when the report changed."""
    m = re.search(pattern, text, flags)
    if not m:
        sys.exit(f"export_site_data: could not find {what!r} (pattern {pattern!r})")
    return m


# --------------------------------------------------------------------------- test split
def test_stream() -> List[dict]:
    """Rebuild the test split frame by frame (same order as model/dataset.py)."""
    rows = generate(TEST_SEED, TEST_PER_CLASS, PACKETS_PER_SCENARIO)
    by_scn: Dict[int, list] = {}
    for sid, _, f in rows:
        by_scn.setdefault(sid, []).append(f)
    order = sorted(by_scn)
    random.Random(SHUFFLE_SEED).shuffle(order)
    frames = [(sid, f) for sid in order for f in by_scn[sid]]
    win = FeatureWindow()
    ring: List[Packet] = []                      # last 64 valid packets, for the window strip
    out: List[dict] = []
    for n, (sid, f) in enumerate(frames):
        res = parse_frame(f.data, f.tuser)
        rec = {"index": n, "scenario": sid, "label": f.label, "tuser": f.tuser, "frame": f.data,
               "drop": res.drop_reason, "pkt": res.packet, "features": None, "window": None}
        if res.packet is not None:
            rec["features"] = list(win.push(res.packet))
            ring.append(res.packet)
            ring = ring[-64:]
            rec["window"] = list(ring)           # copy: includes the current packet last
        out.append(rec)
    return out


def header_fields(frame: bytes, pkt: Packet) -> List[dict]:
    """Byte ranges and values of the header fields the chip reads (for the byte view)."""
    f: List[dict] = []
    def add(name: str, a: int, b: int, val: str) -> None:
        f.append({"name": name, "start": a, "end": b, "value": val})
    add("dst MAC", 0, 6, frame[0:6].hex(":"))
    add("src MAC", 6, 12, frame[6:12].hex(":"))
    l3 = 14
    et = int.from_bytes(frame[12:14], "big")
    if et == 0x8100:
        add("EtherType (VLAN)", 12, 14, "0x8100")
        add("VLAN tag", 14, 16, "0x%04x" % int.from_bytes(frame[14:16], "big"))
        add("EtherType", 16, 18, "0x%04x" % int.from_bytes(frame[16:18], "big"))
        l3 = 18
    else:
        add("EtherType", 12, 14, "0x%04x" % et)
    ihl = frame[l3] & 0xF
    l4 = l3 + ihl * 4
    add("IPv4 version/IHL", l3, l3 + 1, "v%d, IHL %d" % (frame[l3] >> 4, ihl))
    add("IPv4 total length", l3 + 2, l3 + 4, str(pkt.ip_total_len))
    add("IPv4 TTL", l3 + 8, l3 + 9, str(pkt.ttl))
    add("IPv4 protocol", l3 + 9, l3 + 10, "TCP (6)" if pkt.is_tcp else "UDP (17)")
    add("L4 dst port", l4 + 2, l4 + 4, str(pkt.dst_port))
    if pkt.is_tcp:
        fl = frame[l4 + 13]
        names = [n for bit, n in ((0x02, "SYN"), (0x10, "ACK"), (0x01, "FIN"), (0x04, "RST")) if fl & bit]
        add("TCP flags", l4 + 13, l4 + 14, "0x%02x (%s)" % (fl, "+".join(names) or "none of SYN/ACK/FIN/RST"))
    return f


def pick_samples(stream: List[dict], inf: Dict[int, dict]) -> List[int]:
    """Choose ~20 stream indices: per class a confident one, a low-confidence one, plus wrong ones and drops."""
    rng = random.Random(PICK_SEED)
    kept = [r for r in stream if r["features"] is not None and r["index"] >= 100]  # window is full
    chosen: List[int] = []
    def take(cands: List[dict], k: int) -> None:
        """Add k random candidates not yet chosen."""
        cands = [c for c in cands if c["index"] not in chosen]
        chosen.extend(c["index"] for c in rng.sample(cands, min(k, len(cands))))
    for cls in range(3):
        mine = [r for r in kept if r["label"] == cls and inf[r["index"]]["class"] == cls]
        # Attack packets rarely reach a high margin on this net, so "confident" means "not flagged".
        take([r for r in mine if not inf[r["index"]]["low_conf"]], 2 if cls == 0 else 3)
        take([r for r in mine if inf[r["index"]]["low_conf"]], 1)
    wrong = [r for r in kept if inf[r["index"]]["class"] != r["label"]]
    take(wrong, 3)
    take([r for r in kept if r["label"] == 0 and r["features"][0] == 127 and not inf[r["index"]]["low_conf"]], 1)
    for reason in ("not_ipv4", "fragment", "tuser_error", "short_l4"):  # four different drop reasons
        take([r for r in stream if r["drop"] == reason and r["index"] >= 100], 1)
    return sorted(chosen)


# --------------------------------------------------------------------------- main exports
def export_packets() -> Tuple[dict, dict]:
    """Build samples.json, all_packets.json and analysis.json; return (metrics, replay)."""
    metrics = json.loads(rd("docs/results/metrics.json"))
    params = golden.load_params()
    with open(os.path.join(ROOT, "data", "model", "conf_thresh.txt")) as fh:
        thresh = int(fh.read().strip())
    assert thresh == metrics["default_conf_thresh"]

    stream = test_stream()
    test = load_splits()["test"]
    kept = [r for r in stream if r["features"] is not None]
    assert len(kept) == len(test.features) == 8347
    assert [r["features"] for r in kept] == test.features.tolist(), "features differ from model/dataset.py"
    assert [r["label"] for r in kept] == test.labels.tolist()
    assert len(stream) - len(kept) == metrics["test"]["frames_dropped_by_parser"] == 653

    inf: Dict[int, dict] = {}
    conf_mat = [[0] * 3 for _ in range(3)]
    for r in kept:
        cls, conf, low, h, z = golden.infer(r["features"], params, thresh)
        inf[r["index"]] = {"class": cls, "confidence": conf, "low_conf": int(low), "hidden": h, "logits": z}
        conf_mat[r["label"]][cls] += 1
    assert conf_mat == metrics["test"]["int8"]["confusion"], "confusion matrix differs from metrics.json"

    # all_packets.json: one row per classified packet, for the node test (not shown on the page)
    rows = [[r["label"]] + r["features"] + inf[r["index"]]["logits"]
            + [inf[r["index"]]["class"], inf[r["index"]]["confidence"], inf[r["index"]]["low_conf"]]
            for r in kept]
    dump(os.path.join(SITE, "test", "all_packets.json"),
         {"columns": "label, f0..f7, z0..z2, class, confidence, low_conf", "conf_thresh": thresh, "rows": rows})

    # samples.json
    samples = []
    for i in pick_samples(stream, inf):
        r = stream[i]
        s: Dict[str, Any] = {"stream_index": i, "true_label": LABEL_NAMES[r["label"]],
                             "frame_len": len(r["frame"]), "bytes_hex": r["frame"][:BYTES_SHOWN].hex(),
                             "tuser": r["tuser"]}
        if r["pkt"] is None:
            s.update(kind="dropped", drop_reason=r["drop"])
        else:
            pk, win = r["pkt"], r["window"]
            syn_cnt = sum(is_syn_only(p) for p in win)
            occ = len({port_bucket(p.dst_port) for p in win})
            assert min(syn_cnt * 2, 127) == r["features"][6] and min(occ * 2, 127) == r["features"][7]
            s.update(kind="classified", fields=header_fields(r["frame"], pk),
                     packet={"is_tcp": pk.is_tcp, "syn": pk.syn, "ack": pk.ack, "rst_or_fin": pk.rst_or_fin,
                             "ip_total_len": pk.ip_total_len, "ttl": pk.ttl, "dst_port": pk.dst_port},
                     window={"size": len(win), "syn_count": syn_cnt, "occupied_buckets": occ,
                             "entries": [[int(is_syn_only(p)), port_bucket(p.dst_port)] for p in win]},
                     features=r["features"], model=inf[i])
        samples.append(s)
    dump(os.path.join(DATA, "samples.json"),
         {"label": "simulated, synthetic data", "bytes_shown": BYTES_SHOWN, "conf_thresh": thresh,
          "pick_seed": PICK_SEED, "samples": samples})
    shutil.copyfile(os.path.join(ROOT, "data", "model", "weights.json"), os.path.join(DATA, "weights.json"))
    shutil.copyfile(os.path.join(ROOT, "docs", "results", "metrics.json"), os.path.join(DATA, "metrics.json"))

    # analysis.json: input side (test split)
    labels_all = Counter(LABEL_NAMES[r["label"]] for r in stream)
    drops = Counter(r["drop"] for r in stream if r["pkt"] is None)
    drops_by_label = Counter(LABEL_NAMES[r["label"]] for r in stream if r["pkt"] is None)
    feats: Dict[str, Any] = {}
    for fi in range(8):
        per: Dict[str, Any] = {}
        for cls in range(3):
            vals = [r["features"][fi] for r in kept if r["label"] == cls]
            if fi in (0, 1, 2, 5):              # binary features: share of packets with the flag set
                per[LABEL_NAMES[cls]] = {"share_set": sum(v > 0 for v in vals) / len(vals), "n": len(vals)}
            else:                               # 8 bins of width 16 over 0..127
                bins = [0] * 8
                for v in vals:
                    bins[v // 16] += 1
                per[LABEL_NAMES[cls]] = {"bins": bins, "n": len(vals)}
        feats[f"F{fi}"] = {"binary": fi in (0, 1, 2, 5), "classes": per}
    dump(os.path.join(DATA, "analysis.json"),
         {"label": "simulated, synthetic data", "frames_total": len(stream),
          "frames_by_true_label": dict(labels_all), "classified": len(kept),
          "dropped": len(stream) - len(kept), "drop_reasons": dict(drops),
          "dropped_by_true_label": dict(drops_by_label), "features": feats,
          "scenarios_per_class": TEST_PER_CLASS, "packets_per_scenario": PACKETS_PER_SCENARIO,
          "test_master_seed": TEST_SEED})

    replay = rd("docs/results/full_split_replay.log")
    runs = re.findall(r"FULL SPLIT (\w+) \(simulated\): seed=(\d+) thresh=(\d+) gap=([\d.]+) stall=([\d.]+) "
                      r"rows compared=(\d+) mismatches=(\d+)", replay)
    assert len(runs) == 2 and all(int(x[5]) == len(kept) and int(x[6]) == 0 for x in runs)
    return metrics, {"runs": [dict(zip(("name", "seed", "thresh", "gap", "stall", "rows", "mismatches"), x))
                              for x in runs]}


def export_reports(replay: dict) -> None:
    """Parse logs and synthesis reports into impl.json and verify.json."""
    sta = rd("synth/reports/sta.txt")
    before = rd("synth/reports/sta_before.txt")
    wl = rd("synth/reports/sta_wireload.txt")
    def summ(text: str) -> Dict[str, float]:
        """SUMMARY lines of an OpenSTA report -> {name: slack ns}."""
        out = {}
        for m in re.finditer(r"^SUMMARY (\S+_clk) (.+?)\s+(-?\d+\.\d+) ns (met|VIOLATED)", text, re.M):
            out[f"{m.group(1)} {m.group(2).strip()}"] = float(m.group(3))
        assert len(out) >= 12, "STA SUMMARY lines not found"
        return out
    s_after, s_before, s_wl = summ(sta), summ(before), summ(wl)
    cells = rd("synth/reports/cells.txt")
    cells_total = int(must(r"^\s*(\d+)\s+[\d.E+]+\s+cells", cells, "cell total", re.M).group(1))
    area = float(must(r"Chip area for module '\\neurogate_top': ([\d.]+)", cells, "area").group(1))
    seq = must(r"used for sequential elements: ([\d.]+) \(([\d.]+)%\)", cells, "sequential area")
    ffs = {k: int(v) for v, k in re.findall(r"^\s*(\d+)\s+[\d.E+]+\s+(DFF\w*)", cells, re.M)}
    art = rd("synth/reports/artix7_cells.txt")
    cnt = lambda name: int(must(rf"^\s*(\d+)\s+{name}\b", art, name, re.M).group(1))  # noqa: E731
    luts = sum(cnt(f"LUT{k}") for k in range(2, 7))
    ffs_a = sum(cnt(n) for n in ("FDRE", "FDCE", "FDPE", "FDSE"))
    thr = rd("docs/results/throughput_run.log")
    tm = must(r"(\d+) classified frames back to back, mean frame (\d+) bytes; input of frames 2\.\.14 took (\d+) ns "
              r"\(([\d.]+) net clocks per frame\)", thr, "throughput input line")
    gm = must(r"min (\d+), mean ([\d.]+), max (\d+) over (\d+) gaps", thr, "gap line")
    pm = must(r"(\d+) k packets/s at core_clk 200 MHz \(200e6 / ([\d.]+)\); core-limited, about (\d+) Mbit/s", thr, "pps line")
    lat = int(must(r"first-frame latency, last input beat to result beat: (\d+) core clocks", thr, "latency").group(1))
    dump(os.path.join(DATA, "impl.json"), {
        "label": "synthesized (estimate)", "sta_after": s_after, "sta_before": s_before, "sta_wireload": s_wl,
        "cells": {"total": cells_total, "area_um2": area, "seq_area_um2": float(seq.group(1)),
                  "seq_pct": float(seq.group(2)), "flip_flops": sum(ffs.values()), "ff_cells": ffs},
        "artix7": {"luts": luts, "ffs": ffs_a, "dsp48e1": cnt("DSP48E1"), "ram32m": cnt("RAM32M"),
                   "ram64m": cnt("RAM64M")},
        "throughput": {"frames": int(tm.group(1)), "mean_frame_bytes": int(tm.group(2)),
                       "input_ns": int(tm.group(3)), "net_clocks_per_frame": float(tm.group(4)),
                       "core_clocks_between_results": {"min": int(gm.group(1)), "mean": float(gm.group(2)),
                                                       "max": int(gm.group(3)), "gaps": int(gm.group(4))},
                       "kpps": int(pm.group(1)), "mbit_s": int(pm.group(3)), "first_frame_latency_clocks": lat},
        "commands": {"sta_exit": "non-zero on purpose: removal violation is reported, not fixed"}})

    gls = rd("synth/reports/gls.log")
    bad = rd("synth/reports/gls_bad.log")
    g1 = must(r"result beats (\d+) \(expected (\d+)\), mismatches (\d+), protocol errors (\d+)", gls, "gls smoke")
    g2 = must(r"result beats (\d+) \(expected (\d+)\), mismatches (\d+), protocol errors (\d+)", bad, "gls bad")
    assert "1 passed, 0 failed" in gls and "1 passed, 0 failed" in bad
    clean = rd("docs/results/clean_clone.log")
    dump(os.path.join(DATA, "verify.json"), {
        "replay": replay["runs"],
        "gls": {"smoke": {"beats": int(g1.group(1)), "expected": int(g1.group(2)), "mismatches": int(g1.group(3)),
                          "protocol_errors": int(g1.group(4))},
                "bad": {"beats": int(g2.group(1)), "expected": int(g2.group(2)), "mismatches": int(g2.group(3)),
                        "protocol_errors": int(g2.group(4))}},
        "clean_clone_commit": must(r"commit tested: (\w+)", clean, "clean clone commit").group(1),
        "lint_waivers": 4, "rtl_files": 11,   # synth/README.md "Lint" section: text, not a log
        "commit": subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                                 text=True).stdout.strip()})
    # waveform figure: the results page draws it from the VCD; reuse that SVG as is
    page = rd("docs/index.html")
    fig = must(r'<figure class="widefig"><svg viewBox="0 0 940 274".*?</figure>', page, "waveform figure", re.S)
    with open(os.path.join(DATA, "waveform.svg.html"), "w", encoding="utf-8") as fh:
        fh.write(fig.group(0))


def main() -> None:
    """Run all exports."""
    _, replay = export_packets()
    export_reports(replay)
    print("export_site_data: ok ->", os.path.relpath(DATA, ROOT))


if __name__ == "__main__":
    main()
