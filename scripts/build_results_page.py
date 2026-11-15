"""Build the static results page ``docs/index.html`` from committed result files.

No number on the page is typed by hand. Every value is read from one of the files
in ``SOURCES`` (all committed), and every table, chart and figure on the page
names its source file and says whether it is *simulated* or an *estimate*.
Charts and the waveform are drawn here as inline SVG, so the page is a single
self-contained file: no scripts, no external fonts, no trackers. It works opened
from disk and on GitHub Pages.

Usage::

    python3 scripts/build_results_page.py           # write docs/index.html
    python3 scripts/build_results_page.py --check   # exit 1 if docs/index.html differs
                                                    # from what the sources give

``make results-page`` and ``make results-check`` call these. The check is the guard
against a hand-edited number: change any number in ``docs/index.html`` and the
check fails until the page is regenerated from the sources.

The waveform comes from ``docs/results/wave_throughput.vcd``, a trimmed copy of the
dump of a real simulation run (``scripts/capture_waveform.py``). This script only
draws what is in that file. It also measures the beat spacing and first-frame
latency from the dump and stops with an error if they disagree with the numbers the
test itself logged.

Only the Python standard library is used.
"""
import argparse
import difflib
import html
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "index.html")
REPO_URL = "https://github.com/bavafa/neurogate/blob/main/"

# Every file the page reads, with the label that applies to its numbers.
SOURCES = {
    "metrics": ("docs/results/metrics.json", "simulated"),
    "cells": ("synth/reports/cells.txt", "estimate"),
    "artix": ("synth/reports/artix7_cells.txt", "estimate"),
    "sta": ("synth/reports/sta.txt", "estimate"),
    "sdc": ("synth/neurogate_top.sdc", "estimate"),
    "tput": ("docs/results/throughput_run.log", "simulated"),
    "vcd": ("docs/results/wave_throughput.vcd", "simulated"),
    "replay": ("docs/results/full_split_replay.log", "simulated"),
    "gls": ("synth/reports/gls.log", "simulated"),
    "gls_bad": ("synth/reports/gls_bad.log", "simulated"),
}
CLASSES = ["normal", "port scan", "SYN flood"]


class SourceError(Exception):
    """A result file is missing a value the page needs, or two sources disagree."""


def read(key: str) -> str:
    """Return the text of the source file registered under ``key``."""
    path = os.path.join(ROOT, SOURCES[key][0])
    with open(path) as f:
        return f.read()


def find(pattern: str, text: str, key: str, flags: int = 0) -> re.Match:
    """Return the first regex match in a source text, or raise naming the file."""
    m = re.search(pattern, text, flags)
    if not m:
        raise SourceError(f"{SOURCES[key][0]}: pattern not found: {pattern}")
    return m


def esc(s: object) -> str:
    """HTML-escape any value."""
    return html.escape(str(s), quote=True)


def pct(x: float, digits: int = 2) -> str:
    """Format a fraction 0..1 as a percentage string."""
    return f"{100.0 * x:.{digits}f}%"


def num(n: float) -> str:
    """Format an integer-valued number with thousands separators."""
    return f"{int(round(n)):,}"


# ---------------------------------------------------------------------------
# Source readers
# ---------------------------------------------------------------------------

def load_metrics() -> dict:
    """Read the accuracy results (M3 evaluation, Python golden model)."""
    return json.loads(read("metrics"))


def load_nangate() -> Dict[str, float]:
    """Read cell count, area, sequential share and flip-flop count from the Yosys report."""
    t = read("cells")
    cells = int(find(r"^\s*(\d+)\s+\S+\s+cells\s*$", t, "cells", re.M).group(1))
    area = float(find(r"Chip area for module '\\neurogate_top': ([\d.]+)", t, "cells").group(1))
    seq = find(r"sequential elements: ([\d.]+) \(([\d.]+)%\)", t, "cells")
    ffs = sum(int(m.group(1)) for m in re.finditer(r"^\s*(\d+)\s+\S+\s+DFF\w*_X\d+\s*$", t, re.M))
    if ffs == 0:
        raise SourceError("cells.txt: no flip-flop cells found")
    return {"cells": cells, "area": area, "seq_area": float(seq.group(1)),
            "seq_pct": float(seq.group(2)), "ffs": ffs}


def load_artix() -> Dict[str, int]:
    """Read the Artix-7 resource counts (Yosys synth_xilinx, not Vivado)."""
    t = read("artix")
    counts: Dict[str, int] = {}
    for m in re.finditer(r"^\s*(\d+)\s+([A-Z][A-Z0-9_]+)\s*$", t, re.M):
        counts[m.group(2)] = counts.get(m.group(2), 0) + int(m.group(1))
    luts = sum(v for k, v in counts.items() if re.fullmatch(r"LUT\d", k))
    ffs = sum(v for k, v in counts.items() if re.fullmatch(r"FD[A-Z]E", k))
    lutram = sum(v for k, v in counts.items() if k.startswith("RAM") and k[3:5].isdigit())
    bram = sum(v for k, v in counts.items() if k.startswith("RAMB"))
    dsp = sum(v for k, v in counts.items() if k.startswith("DSP"))
    if luts == 0 or ffs == 0:
        raise SourceError("artix7_cells.txt: no LUT or flip-flop counts found")
    return {"luts": luts, "ffs": ffs, "lutram": lutram, "bram": bram, "dsp": dsp}


@dataclass
class StaRow:
    """One SUMMARY line of the OpenSTA report."""
    clock: str
    check: str
    slack: float
    status: str
    note: str


def load_sta() -> Tuple[List[StaRow], Dict[str, str]]:
    """Read the SUMMARY block of the main timing report (no wire load)."""
    t = read("sta")
    rows = []
    for m in re.finditer(r"^SUMMARY (net_clk|core_clk) (.+?)\s+(-?[\d.]+) ns (met|VIOLATED)(.*)$",
                         t, re.M):
        rows.append(StaRow(m.group(1), re.sub(r"\s+", " ", m.group(2)).strip(),
                           float(m.group(3)), m.group(4), m.group(5).strip()))
    if len(rows) < 10:
        raise SourceError(f"sta.txt: expected the SUMMARY block, found {len(rows)} lines")
    hdr = {
        "label": find(r"^STA: label: (.+)$", t, "sta", re.M).group(1),
        "tns_min": find(r"^tns min (-?[\d.]+)", t, "sta", re.M).group(1),
    }
    return rows, hdr


def load_sdc() -> Dict[str, float]:
    """Read clock periods and the pin budget fraction from the timing constraints."""
    t = read("sdc")
    return {
        "net": float(find(r"^set NET_PERIOD\s+([\d.]+)", t, "sdc", re.M).group(1)),
        "core": float(find(r"^set CORE_PERIOD\s+([\d.]+)", t, "sdc", re.M).group(1)),
        "io": float(find(r"^set IO_FRACTION\s+([\d.]+)", t, "sdc", re.M).group(1)),
    }


def load_tput() -> Dict[str, str]:
    """Read the throughput test's result lines (kept by scripts/capture_waveform.py)."""
    t = read("tput")
    m1 = find(r"(\d+) classified frames back to back, mean frame (\d+) bytes; input of "
              r"frames 2\.\.\d+ took \d+ ns \(([\d.]+) net clocks per frame\)", t, "tput")
    m2 = find(r"core clocks between result beats with a backlog: min (\d+), mean ([\d.]+), "
              r"max (\d+) over (\d+) gaps", t, "tput")
    m3 = find(r"(\d+) k packets/s at core_clk (\d+) MHz", t, "tput")
    m4 = find(r"first-frame latency, last input beat to result beat: (\d+) core clocks", t, "tput")
    m5 = find(r"(\d+) passed, (\d+) failed \(seed=(\d+)\)", t, "tput")
    cmd = find(r"^# command: (.+)$", t, "tput", re.M).group(1)
    srcl = find(r"^# source: (.+)$", t, "tput", re.M).group(1)
    return {"frames": m1.group(1), "mean_bytes": m1.group(2), "in_clocks": m1.group(3),
            "min": m2.group(1), "mean": m2.group(2), "max": m2.group(3), "gaps": m2.group(4),
            "kpps": m3.group(1), "mhz": m3.group(2), "latency": m4.group(1),
            "passed": m5.group(1), "failed": m5.group(2), "seed": m5.group(3),
            "cmd": cmd, "source": srcl}


def load_replay() -> Dict[str, object]:
    """Read the full test-split replay result lines (kept by scripts/save_replay_log.py)."""
    t = read("replay")
    runs = []
    for name in ("back_to_back", "backpressure"):
        m = find(rf"FULL SPLIT {name} \(simulated\): seed=(\d+) thresh=(\d+) gap=([\d.]+) stall=([\d.]+) "
                 r"rows compared=(\d+) "
                 r"mismatches=(\d+) .* handshake errors=(\d+)", t, "replay")
        runs.append({"name": name, "seed": m.group(1), "thresh": m.group(2),
                     "gap": m.group(3), "stall": m.group(4), "rows": m.group(5),
                     "mismatches": m.group(6), "hs": m.group(7)})
    m = find(r"full_split_checks: (\d+) passed, (\d+) failed \(seed=(\d+)\)", t, "replay")
    ex = find(r"^exit: (\d+)$", t, "replay", re.M).group(1)
    src = find(r"^# source: (.+)$", t, "replay", re.M).group(1)
    return {"runs": runs, "passed": m.group(1), "failed": m.group(2), "exit": ex, "source": src}


def load_gls() -> Dict[str, str]:
    """Read the gate-level smoke and bad-packet results."""
    a = find(r"result beats (\d+) \(expected (\d+)\), mismatches (\d+), protocol errors (\d+)",
             read("gls"), "gls")
    t = read("gls_bad")
    b = find(r"result beats (\d+) \(expected (\d+)\), mismatches (\d+), protocol errors (\d+)",
             t, "gls_bad")
    c = find(r"bad packets: (\d+) frames, model drops (\d+), classifies (\d+)", t, "gls_bad")
    f = find(r"top smoke \(simulated\): seed=\d+ .* frames=(\d+) ", read("gls"), "gls")
    return {"s_frames": f.group(1), "s_beats": a.group(1), "s_exp": a.group(2), "s_mis": a.group(3), "s_err": a.group(4),
            "b_beats": b.group(1), "b_exp": b.group(2), "b_mis": b.group(3), "b_err": b.group(4),
            "b_frames": c.group(1), "b_drop": c.group(2)}


# ---------------------------------------------------------------------------
# VCD reader and measurements
# ---------------------------------------------------------------------------

@dataclass
class Signal:
    """One signal of the dump: name, width and its value changes (time in ps, value)."""
    name: str
    width: int
    changes: List[Tuple[int, str]] = field(default_factory=list)

    def before(self, t: int) -> str:
        """Value just before time ``t`` (what a flip-flop samples at an edge at ``t``)."""
        v = "x"
        for ct, cv in self.changes:
            if ct >= t:
                break
            v = cv
        return v

    def at(self, t: int) -> str:
        """Value at time ``t`` after all changes at ``t``."""
        v = "x"
        for ct, cv in self.changes:
            if ct > t:
                break
            v = cv
        return v

    def rising(self) -> List[int]:
        """Times of 0 -> 1 transitions (clock rising edges)."""
        out, prev = [], "x"
        for ct, cv in self.changes:
            if cv == "1" and prev == "0":
                out.append(ct)
            prev = cv
        return out


def load_vcd() -> Tuple[Dict[str, Signal], int]:
    """Parse the trimmed VCD; return signals by name and the last time stamp (ps)."""
    t = read("vcd")
    if not re.search(r"\$timescale\s+1ps\s+\$end", t):
        raise SourceError("wave_throughput.vcd: expected a 1 ps timescale")
    by_id: Dict[str, Signal] = {}
    head, body = t.split("$enddefinitions $end", 1)
    for m in re.finditer(r"\$var\s+\w+\s+(\d+)\s+(\S+)\s+(\w+)", head):
        by_id[m.group(2)] = Signal(m.group(3), int(m.group(1)))
    now = 0
    for ln in body.split("\n"):
        ln = ln.strip()
        if not ln:
            continue
        if ln[0] == "#":
            now = int(ln[1:])
        elif ln[0] in "bB":
            val, code = ln[1:].split()
            by_id[code].changes.append((now, val))
        else:
            by_id[ln[1:]].changes.append((now, ln[0]))
    return {s.name: s for s in by_id.values()}, now


def handshakes(sig: Dict[str, Signal], clk: str, names: Sequence[str]) -> List[int]:
    """Rising edges of ``clk`` where every signal in ``names`` is 1 just before the edge."""
    out = []
    for e in sig[clk].rising():
        if all(sig[n].before(e) == "1" for n in names):
            out.append(e)
    return out


def tdata_fields(v: str) -> Dict[str, int]:
    """Split a 48-bit result word (binary string from the VCD) into its fields."""
    x = int(v, 2)
    return {"class": x & 3, "low_conf": (x >> 2) & 1, "conf": (x >> 8) & 0x7F,
            "pkt_len": (x >> 16) & 0xFFFF, "pkt_id": (x >> 32) & 0xFFFF}


# ---------------------------------------------------------------------------
# Small HTML helpers
# ---------------------------------------------------------------------------

def badge(label: str) -> str:
    """A text badge: 'simulated' or 'estimate' (text, not color alone)."""
    cls = "sim" if label.startswith("simulated") else "est"
    return f'<span class="badge {cls}">{esc(label)}</span>'


def src_link(*keys: str) -> str:
    """Links to the source files, by key into SOURCES."""
    return ", ".join(f'<a href="{REPO_URL}{esc(SOURCES[k][0])}"><code>{esc(SOURCES[k][0])}'
                     f'</code></a>' for k in keys)


def source_line(label: str, *keys: str, extra: str = "") -> str:
    """The 'Source: ... | label' line under each table and figure."""
    return (f'<p class="source">{badge(label)} Source: {src_link(*keys)}'
            f'{(". " + extra) if extra else ""}</p>')


# ---------------------------------------------------------------------------
# Charts (inline SVG)
# ---------------------------------------------------------------------------

def margin_chart(bins: List[dict], thresh: int) -> str:
    """Bar chart: share of correct packets per confidence-margin bin (test split)."""
    W, H = 720, 320
    L, R, T, B = 56, 16, 50, 54
    pw, ph = W - L - R, H - T - B
    xmax = bins[-1]["hi"] + 1

    def xs(v: float) -> float:
        return L + pw * v / xmax

    def ys(p: float) -> float:
        return T + ph * (1 - p / 100.0)

    parts = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-labelledby="mc-t mc-d" class="chart">',
             '<title id="mc-t">Share of packets classified correctly, per confidence-margin bin</title>',
             '<desc id="mc-d">Bar chart over the unseen test split. ' +
             "; ".join(f"margin {b['lo']} to {b['hi']}: {b['pct_correct']:.1f}% correct of "
                       f"{b['n']} packets" for b in bins) +
             f". A dashed line marks the default threshold {thresh}.</desc>"]
    for p in (0, 25, 50, 75, 100):
        y = ys(p)
        parts.append(f'<line class="grid" x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}"/>')
        parts.append(f'<text class="tick" x="{L - 8}" y="{y + 4:.1f}" text-anchor="end">{p}%</text>')
    for b in bins:
        x0, x1 = xs(b["lo"]) + 1, xs(b["hi"] + 1) - 1
        y = ys(b["pct_correct"])
        parts.append(
            f'<path class="bar" d="M{x0:.1f},{ys(0):.1f} V{y + 4:.1f} Q{x0:.1f},{y:.1f} {x0 + 4:.1f},'
            f'{y:.1f} H{x1 - 4:.1f} Q{x1:.1f},{y:.1f} {x1:.1f},{y + 4:.1f} V{ys(0):.1f} Z">'
            f'<title>margin {b["lo"]}-{b["hi"]}: {b["n"]:,} packets, {b["pct_correct"]:.1f}% '
            f'correct</title></path>')
        if b["pct_correct"] < 99.95:
            parts.append(f'<text class="val" x="{(x0 + x1) / 2:.1f}" y="{y - 6:.1f}" '
                         f'text-anchor="middle">{b["pct_correct"]:.1f}%</text>')
    full = [b for b in bins if b["pct_correct"] >= 99.95]
    if full:
        xa, xb = xs(full[0]["lo"]), xs(full[-1]["hi"] + 1)
        parts.append(f'<text class="val" x="{(xa + xb) / 2:.1f}" y="{ys(100) - 8:.1f}" '
                     f'text-anchor="middle">100.0% in every bin from {full[0]["lo"]} up</text>')
    for b in bins:
        parts.append(f'<text class="tick" x="{xs(b["lo"]):.1f}" y="{ys(0) + 16:.1f}" '
                     f'text-anchor="middle">{b["lo"]}</text>')
    parts.append(f'<text class="tick" x="{xs(xmax):.1f}" y="{ys(0) + 16:.1f}" '
                 f'text-anchor="middle">{xmax}</text>')
    xt = xs(thresh)
    parts.append(f'<line class="thresh" x1="{xt:.1f}" x2="{xt:.1f}" y1="{T - 40}" y2="{ys(0):.1f}"/>')
    parts.append(f'<text class="tick strong" x="{xt + 5:.1f}" y="{T - 32}">default threshold '
                 f'{thresh} (packets left of the line are flagged)</text>')
    parts.append(f'<line class="axis" x1="{L}" x2="{W - R}" y1="{ys(0):.1f}" y2="{ys(0):.1f}"/>')
    parts.append(f'<text class="label" x="{L + pw / 2:.1f}" y="{H - 10}" text-anchor="middle">'
                 'confidence margin (top output minus second output, scaled; not a probability)'
                 '</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def fmt_time(ps: float) -> str:
    """Short time label for a waveform axis (signed when negative, '0' at zero)."""
    if ps == 0:
        return "0"
    if abs(ps) >= 1_000_000:
        return f"{ps / 1e6:g} µs"
    return f"{'+' if ps > 0 else ''}{ps / 1e3:g} ns".replace("-", "−")


@dataclass
class Row:
    """One row of a waveform drawing."""
    label: str
    sub: str
    kind: str                      # "bit", "clock", "and" (AND of several bits), "bus"
    names: Tuple[str, ...]


def bit_intervals(sig: Dict[str, Signal], names: Sequence[str], t0: int, t1: int
                  ) -> List[Tuple[int, int, str]]:
    """Piecewise value ('0', '1' or 'x') of the AND of ``names`` over [t0, t1]."""
    times = sorted({t0, t1} | {ct for n in names for ct, _ in sig[n].changes if t0 < ct < t1})
    segs: List[Tuple[int, int, str]] = []
    for a, b in zip(times, times[1:]):
        vals = [sig[n].at(a) for n in names]
        if any(v == "0" for v in vals):
            v = "0"
        elif all(v == "1" for v in vals):
            v = "1"
        else:
            v = "x"
        if segs and segs[-1][2] == v:
            segs[-1] = (segs[-1][0], b, v)
        else:
            segs.append((a, b, v))
    return segs


def waveform(sig: Dict[str, Signal], rows: List[Row], t0: int, t1: int, tick: int, ident: str,
             title: str, desc: str, marks: Sequence[Tuple[int, str]] = (),
             spans: Sequence[Tuple[int, int, str]] = (), t_ref: int = 0) -> str:
    """Draw signals between ``t0`` and ``t1`` (ps) as a timing diagram in SVG.

    Axis ticks sit at ``t_ref`` plus whole multiples of ``tick`` and are labelled
    relative to ``t_ref``.
    """
    LW, W, RH, TOP = 210, 940, 34, 30
    pw = W - LW - 12
    extra = 44 if spans else 0
    H = TOP + RH * len(rows) + 30 + extra

    def x(t: float) -> float:
        return LW + pw * (t - t0) / (t1 - t0)

    p = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-labelledby="{ident}-t {ident}-d" '
         f'class="wave">', f'<title id="{ident}-t">{esc(title)}</title>',
         f'<desc id="{ident}-d">{esc(desc)}</desc>']
    # time grid
    k = t_ref + -((t_ref - t0) // tick) * tick
    while k <= t1:
        p.append(f'<line class="grid" x1="{x(k):.1f}" x2="{x(k):.1f}" y1="{TOP - 6}" '
                 f'y2="{TOP + RH * len(rows)}"/>')
        p.append(f'<text class="tick" x="{x(k):.1f}" y="{TOP - 10}" text-anchor="middle">'
                 f'{fmt_time(k - t_ref)}</text>')
        k += tick
    for i, r in enumerate(rows):
        yt = TOP + RH * i + 7
        yb = TOP + RH * (i + 1) - 7
        p.append(f'<text class="sig" x="{LW - 10}" y="{yt + 9}" text-anchor="end">{esc(r.label)}'
                 f'</text>')
        p.append(f'<text class="sigsub" x="{LW - 10}" y="{yt + 21}" text-anchor="end">'
                 f'{esc(r.sub)}</text>')
        if r.kind == "bus":
            # names[0] is the bus; names[1], if given, is its valid signal. Stretches
            # where valid is 0 are drawn grey and labelled "not valid", because the
            # word means nothing there.
            s = sig[r.names[0]]
            vsig = sig[r.names[1]] if len(r.names) > 1 else None
            cuts = {ct for ct, _ in s.changes if t0 < ct < t1}
            if vsig is not None:
                cuts |= {ct for ct, _ in vsig.changes if t0 < ct < t1}
            times = sorted({t0, t1} | cuts)
            for a, b in zip(times, times[1:]):
                v = s.at(a)
                valid = vsig is None or vsig.at(a) == "1"
                xa, xb = x(a), x(b)
                ym = (yt + yb) / 2
                d = min(3.0, (xb - xa) / 2)
                cls = "busx" if ("x" in v or not valid) else "bus"
                p.append(f'<path class="{cls}" d="M{xa:.1f},{ym:.1f} L{xa + d:.1f},{yt} '
                         f'L{xb - d:.1f},{yt} L{xb:.1f},{ym:.1f} L{xb - d:.1f},{yb} '
                         f'L{xa + d:.1f},{yb} Z"/>')
                if not valid and xb - xa > 60:
                    p.append(f'<text class="busval" x="{(xa + xb) / 2:.1f}" y="{ym + 4:.1f}" '
                             f'text-anchor="middle">not valid</text>')
                elif "x" not in v and xb - xa > 150:
                    f = tdata_fields(v)
                    txt = (f"pkt_id {f['pkt_id']}, {CLASSES[f['class']] if f['class'] < 3 else '?'}"
                           f", confidence {f['conf']}, low_conf {f['low_conf']}")
                    p.append(f'<text class="busval" x="{(xa + xb) / 2:.1f}" y="{ym + 4:.1f}" '
                             f'text-anchor="middle">{esc(txt)}</text>')
            continue
        segs = bit_intervals(sig, r.names, t0, t1)
        d: List[str] = []
        for a, b, v in segs:
            xa, xb = x(a), x(b)
            if v == "1" and xb - xa < 1.6:          # keep one-clock pulses visible
                xb = xa + 1.6
            if v == "x":
                p.append(f'<rect class="xfill" x="{xa:.1f}" y="{yt}" width="{max(xb - xa, 0.5):.1f}"'
                         f' height="{yb - yt}"/>')
                continue
            y = yt if v == "1" else yb
            if not d:
                d.append(f"M{xa:.1f},{y}")
            else:
                d.append(f"V{y}")
            d.append(f"H{xb:.1f}")
            if v == "1":
                p.append(f'<rect class="hifill" x="{xa:.1f}" y="{yt}" width="{xb - xa:.1f}" '
                         f'height="{yb - yt}"/>')
        if d:
            p.append(f'<path class="{"clk" if r.kind == "clock" else "trace"}" d="{" ".join(d)}"/>')
    yb_all = TOP + RH * len(rows)
    for t, txt in marks:
        p.append(f'<text class="mark" x="{x(t):.1f}" y="{yb_all + 14}" text-anchor="middle">'
                 f'{esc(txt)}</text>')
    for a, b, txt in spans:
        ys = yb_all + 34 + (0 if not marks else 6)
        p.append(f'<path class="span" d="M{x(a):.1f},{ys - 5} V{ys + 5} M{x(a):.1f},{ys} '
                 f'H{x(b):.1f} M{x(b):.1f},{ys - 5} V{ys + 5}"/>')
        p.append(f'<text class="mark strong" x="{(x(a) + x(b)) / 2:.1f}" y="{ys - 5}" '
                 f'text-anchor="middle">{esc(txt)}</text>')
    p.append("</svg>")
    return "\n".join(p)


# ---------------------------------------------------------------------------
# Page sections
# ---------------------------------------------------------------------------

def conf_cell(n: int, row_total: int) -> str:
    """One confusion-matrix cell, shaded by its share of the row (one hue, light to dark)."""
    share = n / row_total
    steps = [(0.05, "c0"), (0.20, "c1"), (0.50, "c2"), (0.80, "c3"), (1.01, "c4")]
    cls = next(c for lim, c in steps if share < lim)
    return f'<td class="{cls}">{num(n)}<span class="share">{pct(share, 1)}</span></td>'


def build() -> str:
    """Read every source, cross-check, and return the full HTML page."""
    m = load_metrics()
    nan = load_nangate()
    art = load_artix()
    sta, sta_hdr = load_sta()
    sdc = load_sdc()
    tp = load_tput()
    gls = load_gls()
    rp = load_replay()
    sig, t_end = load_vcd()

    test = m["test"]
    i8, fl = test["int8"], test["float"]
    thresh = m["default_conf_thresh"]
    n_cls = i8["n"]

    # --- measurements from the dump, cross-checked against the test's own log ---
    core_ps = int(round(sdc["core"] * 1000))
    beats = handshakes(sig, "core_clk", ["m_axis_res_tvalid", "m_axis_res_tready"])
    ins_last = handshakes(sig, "net_clk", ["s_axis_tvalid", "s_axis_tready", "s_axis_tlast"])
    takes = handshakes(sig, "core_clk", ["a_tvalid", "a_tready"])
    writes = handshakes(sig, "net_clk", ["f_tvalid", "f_tready"])
    if len(beats) != int(tp["frames"]) or len(ins_last) != int(tp["frames"]):
        raise SourceError(f"dump has {len(beats)} result beats and {len(ins_last)} last input "
                          f"beats; the log says {tp['frames']} frames")
    gaps = [(b - a) / core_ps for a, b in zip(beats, beats[1:])]
    sat = gaps[2:] if len(gaps) > 4 else gaps
    if not (min(sat) == float(tp["min"]) and max(sat) == float(tp["max"])):
        raise SourceError(f"dump beat gaps {min(sat)}..{max(sat)} differ from the log "
                          f"{tp['min']}..{tp['max']}")
    lat_vcd = (beats[0] - ins_last[0]) / core_ps
    if abs(lat_vcd - int(tp["latency"])) > 1:
        raise SourceError(f"dump first-frame latency {lat_vcd} differs from the log {tp['latency']}")
    first_beat = sig["m_axis_res_tdata"].before(beats[0] + 1)
    fb = tdata_fields(first_beat)

    S: List[str] = []
    w = S.append

    # ---------------- header and context ----------------
    w('<header class="hero"><div class="wrap">')
    w('<p class="eyebrow">NeuroGate · results</p>')
    w('<h1>A small chip design that labels network packets</h1>')
    w('<p class="lede">NeuroGate is a hardware design, written in SystemVerilog (a language for '
      'describing digital circuits), that reads network packets and labels each one as '
      '<strong>normal</strong>, a <strong>port scan</strong> (one machine probing many ports '
      'to find open ones) or a <strong>SYN flood</strong> (a burst of connection requests that '
      'are never finished, meant to overload a server; SYN, short for synchronize, is the '
      'first message of a connection). A tiny neural network with 8-bit '
      'integer weights makes the call from eight numbers about each packet and the recent '
      'traffic. This page collects the measured results.</p>')
    w('<div class="notice" role="note"><strong>Read this first.</strong> Every number on this '
      'page is either <em>simulated</em> (from a simulation run in this repository) or an '
      '<em>estimate</em> (from a synthesis or timing tool, with no place and route, the '
      'step that lays out and wires a real chip). Nothing ran on an FPGA (field-programmable '
      'gate array, a reprogrammable chip) or in silicon. <strong>All network traffic is synthetic</strong>, made by the '
      "project's own generator, so the accuracy here is not real-world accuracy. Each table "
      'and figure names the committed file its numbers come from.</div>')
    w('<nav aria-label="Sections"><ul>' + "".join(
        f'<li><a href="#{a}">{t}</a></li>' for a, t in (
            ("summary", "Summary"), ("accuracy", "Accuracy"), ("confusion", "Confusion matrix"),
            ("margin", "Confidence margin"), ("waveform", "Waveform"),
            ("synthesis", "Synthesis"), ("timing", "Timing"), ("throughput", "Throughput"),
            ("replay", "Chip vs model"), ("gatelevel", "Gate-level"), ("limits", "Limitations"))) + "</ul></nav>")
    w("</div></header>")
    w('<main class="wrap">')

    # ---------------- summary tiles ----------------
    worst_setup = min(r.slack for r in sta if r.check == "setup flop-to-flop")
    w('<section id="summary" aria-labelledby="summary-h"><h2 id="summary-h">Summary</h2>')
    w('<ul class="tiles">')
    tiles = [
        (pct(i8["accuracy"]), "accuracy of the int8 network on "
         f"{num(n_cls)} unseen synthetic packets", "simulated", "metrics"),
        (f"{int(tp['kpps']) / 1000:.2f} M", f"packets per second at {tp['mhz']} MHz "
         f"({tp['mean']} core clocks per packet)", "simulated estimate", "tput"),
        (f"{num(nan['area'])} µm²", f"area in square micrometres in the Nangate45 cell library, "
         f"{num(nan['cells'])} cells", "estimate", "cells"),
        (f"+{worst_setup:.3f} ns", "worst setup slack over both clocks (positive means met)",
         "estimate", "sta"),
    ]
    for big, small, lab, key in tiles:
        w(f'<li class="tile"><span class="big">{esc(big)}</span><span class="small">'
          f'{esc(small)}</span><span class="meta">{badge(lab)} {src_link(key)}</span></li>')
    w("</ul></section>")

    # ---------------- accuracy ----------------
    w('<section id="accuracy" aria-labelledby="acc-h"><h2 id="acc-h">Accuracy</h2>')
    w('<p class="why">How often the label is right, on packets the network never saw during '
      'training. The numbers come from the Python golden model, the bit-exact reference that '
      'the chip is tested against.</p>')
    w(f'<p>The int8 network (the arithmetic the chip uses) is right on '
      f'<strong>{pct(i8["accuracy"])}</strong> of {num(n_cls)} test packets. The floating-point '
      f'network it was converted from scores {pct(fl["accuracy"])}; the two give the same label '
      f'on {pct(test["int8_vs_float_agreement"])} of packets. Of the {num(test["frames_total"])} '
      f'test frames, {num(test["frames_dropped_by_parser"])} are dropped by the header readers '
      f'as malformed or unsupported and are not counted here.</p>')
    w('<div class="tablewrap"><table><caption>Precision and recall per class, int8 network, '
      'test split. Precision: of the packets given this label, the share that truly are this '
      'class. Recall: of the packets of this class, the share that got this label.</caption>')
    w('<thead><tr><th scope="col">Class</th><th scope="col">Precision</th>'
      '<th scope="col">Recall</th><th scope="col">Packets in class</th></tr></thead><tbody>')
    for c, name in enumerate(CLASSES):
        w(f'<tr><th scope="row">{name}</th><td>{pct(i8["precision"][c])}</td>'
          f'<td>{pct(i8["recall"][c])}</td><td>{num(sum(i8["confusion"][c]))}</td></tr>')
    w("</tbody></table></div>")
    w(source_line("simulated, synthetic data", "metrics",
                  extra="Fields <code>test.int8</code>, <code>test.float</code>"))
    w("</section>")

    # ---------------- confusion matrix ----------------
    w('<section id="confusion" aria-labelledby="cm-h"><h2 id="cm-h">Confusion matrix</h2>')
    w('<p class="why">Where the mistakes go. Each row is the true class; each column is the '
      'label the int8 network gave. The diagonal is correct. The small number is the share '
      'of the row; stronger shading means a larger share.</p>')
    w('<div class="tablewrap"><table class="cm"><caption>Confusion matrix, int8 network, '
      f'{num(n_cls)} test packets (counts and row share)</caption>')
    w('<thead><tr><td></td><th scope="col" colspan="3" class="axis-h">Label given by the network'
      '</th></tr><tr><th scope="col">True class</th>' +
      "".join(f'<th scope="col">{c}</th>' for c in CLASSES) + "</tr></thead><tbody>")
    for r, name in enumerate(CLASSES):
        row = i8["confusion"][r]
        w(f'<tr><th scope="row">{name}</th>' + "".join(conf_cell(v, sum(row)) for v in row) +
          "</tr>")
    w("</tbody></table></div>")
    w(source_line("simulated, synthetic data", "metrics",
                  extra="Field <code>test.int8.confusion</code>"))
    w("</section>")

    # ---------------- margin ----------------
    bins = test["margin_bins"]
    w('<section id="margin" aria-labelledby="mg-h"><h2 id="mg-h">Confidence margin vs accuracy'
      '</h2>')
    w('<p class="why">With each label the chip reports a <em>confidence</em>: the gap between '
      'the two largest network outputs, scaled to 0-127. It is a margin, not a probability. '
      'Packets below a threshold are flagged as low confidence, so software can treat them '
      'with care. This chart shows whether a bigger margin really means a more reliable '
      'label.</p>')
    w('<figure class="widefig">' + margin_chart(bins, thresh))
    w(f'<figcaption>Share of test packets labelled correctly in each margin bin (int8 network). '
      f'The default threshold of {thresh} was chosen on a separate validation split '
      f'({m["threshold_rule"]}) and then measured once on the test split shown here. At that '
      f'threshold {pct(test["flagged_low_conf_share"], 1)} of packets are flagged; unflagged '
      f'packets are {pct(test["kept_accuracy"])} correct and flagged ones '
      f'{pct(test["flagged_accuracy"])}.</figcaption></figure>')
    w('<details><summary>Show the chart data as a table</summary><div class="tablewrap"><table>'
      '<caption>Packets and share correct per margin bin, test split</caption><thead><tr>'
      '<th scope="col">Margin bin</th><th scope="col">Packets</th><th scope="col">Correct</th>'
      '</tr></thead><tbody>')
    for b in bins:
        w(f'<tr><th scope="row">{b["lo"]}-{b["hi"]}</th><td>{num(b["n"])}</td>'
          f'<td>{b["pct_correct"]:.1f}%</td></tr>')
    w("</tbody></table></div></details>")
    w(source_line("simulated, synthetic data", "metrics",
                  extra="Fields <code>test.margin_bins</code>, <code>default_conf_thresh</code>, "
                        "<code>test.kept_accuracy</code>, <code>test.flagged_accuracy</code>"))
    w("</section>")

    # ---------------- waveform ----------------
    lab_cls = {0: "normal", 1: "scan", 2: "SYN"}
    marks = []
    for b in beats:
        f = tdata_fields(sig["m_axis_res_tdata"].before(b + 1))
        marks.append((b, lab_cls.get(f["class"], "?")))
    t_a1 = min(t_end, beats[-1] + 300_000)
    rows_a = [
        Row("Packet data in", "s_axis_tvalid (net_clk)", "bit", ("s_axis_tvalid",)),
        Row("Last beat of a frame", "s_axis_tlast & tvalid", "and", ("s_axis_tlast", "s_axis_tvalid")),
        Row("Entry into crossing queue", "f_tvalid & f_tready", "and", ("f_tvalid", "f_tready")),
        Row("Core takes an entry", "a_tvalid & a_tready (core_clk)", "and", ("a_tvalid", "a_tready")),
        Row("Result out", "m_axis_res_tvalid & tready", "and",
            ("m_axis_res_tvalid", "m_axis_res_tready")),
    ]
    spans = [(ins_last[0], beats[0], f"first frame: {lat_vcd:.0f} core clocks"),
             (beats[3], beats[4], f"{gaps[3]:.0f} clocks")]
    wa = waveform(sig, rows_a, 0, t_a1, 2_000_000, "wa",
                  "Timing diagram of the whole throughput run",
                  f"{tp['frames']} frames enter on the network clock in about the first "
                  f"{(ins_last[-1]) / 1e6:.1f} microseconds. Result beats then leave one at a "
                  f"time, {tp['min']} core clocks apart, labelled with the class of each.",
                  marks=marks, spans=spans)
    zc = beats[0]
    z0, z1 = zc - 8 * core_ps - core_ps // 2, zc + 8 * core_ps + core_ps // 2
    rows_b = [
        Row("Core clock", "core_clk, 200 MHz", "clock", ("core_clk",)),
        Row("Result valid", "m_axis_res_tvalid", "bit", ("m_axis_res_tvalid",)),
        Row("Result ready", "m_axis_res_tready", "bit", ("m_axis_res_tready",)),
        Row("Result word", "m_axis_res_tdata[47:0]", "bus",
            ("m_axis_res_tdata", "m_axis_res_tvalid")),
    ]
    wb = waveform(sig, rows_b, z0, z1, 4 * core_ps, "wb",
                  "Zoom on the first result beat, clock by clock",
                  f"The result valid signal goes high for one core clock while ready is high; "
                  f"the result word then reads packet id {fb['pkt_id']}, class "
                  f"{CLASSES[fb['class']]}, confidence {fb['conf']}.", t_ref=zc)
    w('<section id="waveform" aria-labelledby="wf-h"><h2 id="wf-h">Waveform from a real '
      'simulation</h2>')
    w('<p class="why">A timing diagram shows signals over time, as a logic analyser would. '
      'These two are drawn by the page script straight from the signal dump of a real '
      'simulation run; nothing is drawn by hand.</p>')
    w(f'<p>The run is the throughput test <code>tb/test_neurogate_top_throughput.py</code> '
      f'(seed {tp["seed"]}, Icarus Verilog simulator, the whole chip at the source-code level): '
      f'{tp["frames"]} generated frames are sent back to back on the network clock and the '
      f'result output is always ready. Signals are shown with their names in the design.</p>')
    w('<p class="hint">On a narrow screen, scroll the diagrams sideways.</p>')
    w(f'<figure class="widefig">{wa}<figcaption>Whole run. Frames arrive much faster than the '
      f'network core can label them, so entries wait in the clock-crossing queue and results '
      f'leave every {tp["min"]} core clocks. The labels under the result row are the class of '
      f'each result. Thin pulses are one clock wide and are widened slightly so they stay '
      f'visible.</figcaption></figure>')
    w(f'<figure class="widefig">{wb}<figcaption>Zoom on the first result beat (16 core clocks '
      f'around it; time 0 is the clock edge that takes the result). The result is taken on the rising clock edge where valid and ready are both '
      f'high. The word is decoded with the field layout from the register map: class in bits '
      f'1-0, low-confidence flag in bit 2, confidence in bits 15-8, packet length in bits '
      f'31-16, packet id in bits 47-32. Grey stretches of the word are times when valid is '
      f'low and the word carries no result.</figcaption></figure>')
    w(source_line("simulated", "vcd", "tput",
                  extra=f"The dump is a trimmed copy (only the signals shown) of the run's full "
                        f"dump, made by <code>scripts/capture_waveform.py</code>. The page "
                        f"script measures the result spacing ({min(sat):.0f} to {max(sat):.0f} "
                        f"core clocks) and the first-frame delay ({lat_vcd:.0f} core clocks) "
                        f"from the dump and stops if they differ from the test's log"))
    w("</section>")

    # ---------------- synthesis ----------------
    w('<section id="synthesis" aria-labelledby="syn-h"><h2 id="syn-h">Synthesis (size)</h2>')
    w('<p class="why">Synthesis turns the design into a list of logic gates and flip-flops '
      'from a cell library. The count gives a first idea of size. These are tool estimates '
      'from Yosys (an open-source synthesis tool), with no place and route.</p>')
    w('<div class="tablewrap"><table><caption>Nangate45 (an open 45 nm teaching cell library, '
      'typical corner)</caption><thead><tr><th scope="col">Measure</th><th scope="col">Value'
      '</th></tr></thead><tbody>')
    for k, v in (("Cells", num(nan["cells"])),
                 ("Area (square micrometres)", f"{nan['area']:,.0f} µm²"),
                 ("Share of area in flip-flops", f"{nan['seq_pct']:.2f}%"),
                 ("Flip-flops", num(nan["ffs"]))):
        w(f'<tr><th scope="row">{k}</th><td>{v}</td></tr>')
    w("</tbody></table></div>")
    w(source_line("synthesized (estimate)", "cells"))
    w('<div class="tablewrap"><table><caption>Artix-7 FPGA resource estimate (Yosys '
      '<code>synth_xilinx</code>, not the vendor tool Vivado; Vivado would give different '
      'numbers)</caption><thead><tr><th scope="col">Resource</th><th scope="col">Count</th>'
      '</tr></thead><tbody>')
    for k, v in (("Lookup tables (LUTs)", art["luts"]),
                 ("Flip-flops", art["ffs"]),
                 ("Distributed RAM blocks (random-access memory built from LUTs)", art["lutram"]),
                 ("Block RAM", art["bram"]),
                 ("DSP slices (digital signal processing blocks, used as multipliers)", art["dsp"])):
        w(f'<tr><th scope="row">{k}</th><td>{num(v)}</td></tr>')
    w("</tbody></table></div>")
    w(source_line("estimate", "artix", extra="LUTs are the sum of the <code>LUT2</code> to "
                  "<code>LUT6</code> cells, flip-flops the sum of the <code>FDCE</code>, "
                  "<code>FDPE</code>, <code>FDRE</code> and <code>FDSE</code> cells"))
    w("</section>")

    # ---------------- timing ----------------
    w('<section id="timing" aria-labelledby="tm-h"><h2 id="tm-h">Timing</h2>')
    w(f'<p class="why">Static timing analysis adds up gate delays along every path between '
      f'flip-flops and checks that signals arrive in time for the next clock edge. '
      f'<em>Slack</em> is the time to spare: positive means the check is met. The tool is '
      f'OpenSTA with the Nangate45 typical library, ideal clocks and no wires (no place and '
      f'route). Clock targets: network clock {sdc["net"]:g} ns ({1000 / sdc["net"]:g} MHz), '
      f'core clock {sdc["core"]:g} ns ({1000 / sdc["core"]:g} MHz).</p>')
    w('<div class="tablewrap"><table><caption>Worst slack per check, in nanoseconds</caption>'
      '<thead><tr><th scope="col">Check</th><th scope="col">Network clock</th>'
      '<th scope="col">Core clock</th><th scope="col" class="txt">Result</th></tr></thead><tbody>')
    names = {
        "setup flop-to-flop": "Setup, flip-flop to flip-flop",
        "hold flop-to-flop": "Hold, flip-flop to flip-flop",
        "setup from inputs": "Setup, from input pins",
        "setup to outputs": "Setup, to output pins",
        "recovery (reset pin)": "Reset release, recovery",
        "removal (reset pin)": "Reset release, removal",
    }
    by = {(r.clock, r.check): r for r in sta}
    order = ["setup flop-to-flop", "hold flop-to-flop", "setup from inputs", "setup to outputs",
             "crossing", "recovery (reset pin)", "removal (reset pin)"]
    for chk in order:
        if chk == "crossing":
            rn = by[("net_clk", "setup from core_clk")]
            rc = by[("core_clk", "setup from net_clk")]
            title = "Clock crossing into this clock " + esc(re.sub(r"[()]", "", rn.note)
                                                             .replace("crossing, ", "("))
            title += ")" if "(" in title else ""
        else:
            rn, rc = by[("net_clk", chk)], by[("core_clk", chk)]
            title = names[chk]
        bad = [r for r in (rn, rc) if r.status != "met"]
        if not bad:
            res = "met (estimate)"
        elif chk == "removal (reset pin)":
            res = (f"<strong>missed by {abs(min(r.slack for r in bad)):.3f} ns</strong>: not done, "
                   "because there is no place-and-route step")
        else:
            res = "<strong>violated</strong>"
        w(f'<tr><th scope="row">{title}</th><td>{rn.slack:+.3f}</td><td>{rc.slack:+.3f}</td>'
          f'<td class="txt">{res}</td></tr>')
    w("</tbody></table></div>")
    w(f'<ul class="notes"><li><strong>Setup is met on both clocks (estimate).</strong> Real '
      f'wires, clock-tree delay and other operating corners would reduce these margins.</li>'
      f'<li><strong>Reset removal:</strong> the reset-release check at the asynchronous reset '
      f'pins of the clock-crossing queue misses by '
      f'{abs(by[("core_clk", "removal (reset pin)")].slack):.3f} ns on both clocks. It is '
      f'reported as not done, because there is no place-and-route step, which is where '
      f'such a short path is normally padded with delay.</li>'
      f'<li><strong>Pin budget is a guess:</strong> the design has no stated outside timing, so '
      f'the constraints assume the logic outside the chip uses {sdc["io"] * 100:.0f}% of the '
      f'clock period on each side.</li></ul>')
    w(source_line("synthesized (estimate)", "sta", "sdc",
                  extra=f"Report label: “{esc(sta_hdr['label'])}”"))
    w("</section>")

    # ---------------- throughput ----------------
    lat_ns = int(tp["latency"]) * sdc["core"]
    w('<section id="throughput" aria-labelledby="tp-h"><h2 id="tp-h">Throughput</h2>')
    w('<p class="why">How many packets per second the chip could label. This is a cycle count '
      'from simulation multiplied by the target clock, not a measured line rate.</p>')
    w('<div class="tablewrap"><table><caption>Throughput test, whole chip, source-code level'
      '</caption><thead><tr><th scope="col">Measure</th><th scope="col">Value</th></tr></thead>'
      '<tbody>')
    for k, v in (
            ("Core clocks per packet when packets are waiting",
             f"{tp['mean']} (min {tp['min']}, max {tp['max']}, over {tp['gaps']} gaps)"),
            (f"Packets per second at {tp['mhz']} MHz", f"about {num(int(tp['kpps']) * 1000)}"),
            ("Delay of the first packet, last input beat to result",
             f"{tp['latency']} core clocks ({lat_ns:g} ns at {tp['mhz']} MHz)"),
            ("Input side, network clocks per frame",
             f"{tp['in_clocks']} (mean frame {tp['mean_bytes']} bytes)")):
        w(f'<tr><th scope="row">{esc(k)}</th><td>{esc(v)}</td></tr>')
    w("</tbody></table></div>")
    w('<p>The network core (one multiply-add unit doing all the work) is the bottleneck; the '
      'input side is faster. Whether the core really reaches 200 MHz is the timing estimate '
      'above, not this test.</p>')
    w(source_line("simulated estimate", "tput",
                  extra=f"Command <code>{esc(tp['cmd'])}</code>; test result {tp['passed']} "
                        f"passed, {tp['failed']} failed; ns value computed here as clocks "
                        f"times {sdc['core']:g} ns"))
    w("</section>")

    # ---------------- replay of the whole test split ----------------
    w('<section id="replay" aria-labelledby="rp-h"><h2 id="rp-h">Chip vs Python model, every '
      'test packet</h2>')
    w('<p class="why">Every classified packet of the unseen test split was fed through the '
      'chip\'s network core and decision step in simulation, and each output was compared with '
      'the Python golden model. Any difference would be a mismatch.</p>')
    w('<div class="tablewrap"><table><caption>Replay of the test split through the network '
      'core and decision step (source-code level)</caption><thead><tr>'
      '<th scope="col">Run</th><th scope="col">Packets compared</th>'
      '<th scope="col">Mismatches vs Python model</th><th scope="col">Handshake errors</th>'
      '</tr></thead><tbody>')
    for r in rp["runs"]:
        gap, stall = round(float(r["gap"]) * 100), round(float(r["stall"]) * 100)
        label = ("Back to back" if gap == 0 and stall == 0 else
                 f"With input gaps ({gap}%) and output stalls ({stall}%)")
        w(f'<tr><th scope="row">{label}</th><td>{num(int(r["rows"]))}</td>'
          f'<td>{r["mismatches"]}</td><td>{r["hs"]}</td></tr>')
    w("</tbody></table></div>")
    w(source_line("simulated", "replay",
                  extra=f"Command <code>make test-full</code> (seed {rp['runs'][0]['seed']}); "
                        f"test result {rp['passed']} passed, {rp['failed']} failed, "
                        f"exit {rp['exit']}. {esc(str(rp['source']))}. Synthetic traffic"))
    w("</section>")

    # ---------------- gate level ----------------
    w('<section id="gatelevel" aria-labelledby="gl-h"><h2 id="gl-h">Gate-level simulation</h2>')
    w('<p class="why">The synthesized gate list was simulated with the same checks as the '
      'source code, to catch differences between the two. The gate models have no delays, so '
      'this checks function, not timing.</p>')
    w('<div class="tablewrap"><table><caption>Gate-level runs on the Nangate45 netlist</caption>'
      '<thead><tr><th scope="col">Run</th><th scope="col">Result beats as expected</th>'
      '<th scope="col">Mismatches vs Python model</th><th scope="col">Protocol errors</th>'
      '</tr></thead><tbody>')
    w(f'<tr><th scope="row">Smoke set ({gls["s_frames"]} frames)</th><td>{gls["s_beats"]} of {gls["s_exp"]}'
      f'</td><td>{gls["s_mis"]}</td><td>{gls["s_err"]}</td></tr>')
    w(f'<tr><th scope="row">Bad-packet set ({gls["b_frames"]} frames, {gls["b_drop"]} dropped '
      f'as expected)</th><td>{gls["b_beats"]} of {gls["b_exp"]}</td><td>{gls["b_mis"]}</td>'
      f'<td>{gls["b_err"]}</td></tr>')
    w("</tbody></table></div>")
    w(source_line("simulated", "gls", "gls_bad",
                  extra="The longer traffic, overflow and reset tests were not run on the gates"))
    w("</section>")

    # ---------------- limitations ----------------
    w('<section id="limits" aria-labelledby="lim-h"><div class="limits">'
      '<h2 id="lim-h">Limitations</h2><ul>'
      '<li><strong>Synthetic data.</strong> All traffic comes from this repository\'s own '
      'generator. Accuracy here is not real-world accuracy.</li>'
      '<li><strong>Simulation only.</strong> No FPGA or silicon run. Throughput is a cycle '
      'count times the target clock.</li>'
      '<li><strong>Timing is a pre-layout estimate.</strong> Ideal clocks, no wires, one '
      'operating corner, a guessed pin budget, and one open reset-removal miss.</li>'
      '<li><strong>Confidence is a margin, not a probability.</strong></li>'
      '<li><strong>Clock crossings</strong> were checked by a structural script and a written '
      'review, not by a commercial tool or a formal proof.</li></ul>'
      f'<p>The full list is in the <a href="{REPO_URL}README.md#limitations">Limitations section '
      'of the README</a>.</p></div></section>')
    w("</main>")
    w('<footer class="wrap"><p>This page is generated by <code>scripts/build_results_page.py'
      '</code> from the committed result files named in each section. '
      '<code>make results-check</code> rebuilds it and fails if this file differs, so a number '
      'edited by hand is caught. AI (artificial intelligence) tools were used to build the project under the owner\'s '
      'direction; see the README.</p></footer>')
    body = "\n".join(S)
    return PAGE.replace("@@BODY@@", body)


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NeuroGate results (simulated and estimated)</title>
<meta name="description" content="Results of NeuroGate, a simulation-only chip design that labels network packets with a small int8 neural network. Every number is simulated or a tool estimate, on synthetic traffic.">
<!-- Generated by scripts/build_results_page.py. Do not edit by hand: make results-check fails. -->
<style>
:root{color-scheme:light;--bg:#fcfcfb;--panel:#f3f3f0;--ink:#0b0b0b;--ink2:#52514e;--rule:#dcdbd5;
--accent:#2a78d6;--accent-ink:#1d5fae;--hi:rgba(42,120,214,.13);--x:#c9c8c1;--warn-bg:#fff6e0;--warn-rule:#c98500;
--c0:#f1f6fd;--c1:#d6e5f8;--c2:#a9c9ef;--c3:#2563ad;--c4:#173f73;--c-hi-ink:#fff}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;--bg:#1a1a19;--panel:#242422;--ink:#f0efec;--ink2:#c3c2b7;
--rule:#3d3c39;--accent:#3987e5;--accent-ink:#8ab8f2;--hi:rgba(57,135,229,.22);--x:#55544f;--warn-bg:#2d2715;--warn-rule:#c98500;
--c0:#22272e;--c1:#263548;--c2:#2d4d74;--c3:#6a9be0;--c4:#a8cbf7;--c-hi-ink:#0b0b0b}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:62rem;margin:0 auto;padding:0 1.25rem}
a{color:var(--accent-ink)}a:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
code{font:0.88em ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
h1{font-size:clamp(1.6rem,4vw,2.3rem);line-height:1.2;margin:.2rem 0 1rem}
h2{font-size:1.4rem;margin:0 0 .5rem;padding-top:.5rem}
.hero{border-bottom:1px solid var(--rule);padding:2rem 0 1rem;background:var(--panel)}
.eyebrow{margin:0;color:var(--ink2);font-weight:600;letter-spacing:.04em;text-transform:uppercase;font-size:.8rem}
.lede{font-size:1.08rem;max-width:48rem}
.notice{background:var(--warn-bg);border-left:4px solid var(--warn-rule);padding:.8rem 1rem;border-radius:4px;max-width:48rem}
nav ul{list-style:none;padding:0;margin:1.2rem 0 0;display:flex;flex-wrap:wrap;gap:.4rem .9rem;font-size:.95rem}
section{padding:1.8rem 0;border-bottom:1px solid var(--rule)}
.why{color:var(--ink2);max-width:48rem}
.tiles{list-style:none;padding:0;margin:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(13rem,1fr));gap:.8rem}
.tile{background:var(--panel);border:1px solid var(--rule);border-radius:8px;padding:.9rem 1rem;display:flex;flex-direction:column;gap:.3rem}
.tile .big{font-size:1.7rem;font-weight:650;font-variant-numeric:tabular-nums}
.tile .small{color:var(--ink2);font-size:.92rem}
.tile .meta{font-size:.8rem;color:var(--ink2);margin-top:auto;overflow-wrap:anywhere}
.badge{display:inline-block;font-size:.75rem;font-weight:600;padding:.05rem .45rem;border-radius:999px;border:1px solid;margin-right:.3rem;white-space:nowrap}
.badge.sim{color:var(--accent-ink);border-color:var(--accent)}
.badge.est{color:var(--ink);border-color:var(--warn-rule);background:var(--warn-bg)}
.source{font-size:.85rem;color:var(--ink2);overflow-wrap:anywhere}
.tablewrap{overflow-x:auto;margin:1rem 0 .3rem}
table{border-collapse:collapse;font-variant-numeric:tabular-nums;min-width:20rem}
caption{text-align:left;font-weight:600;padding-bottom:.4rem;max-width:48rem}
th,td{padding:.45rem .8rem;border-bottom:1px solid var(--rule);text-align:right;vertical-align:top}
th[scope=row],thead th:first-child,.txt{text-align:left}
thead th{border-bottom:2px solid var(--ink2)}
.cm td{text-align:center;min-width:7rem;border:2px solid var(--bg)}
.cm .share{display:block;font-size:.8rem}
.cm .axis-h{text-align:center;border-bottom:1px solid var(--rule);font-weight:600}
.c0{background:var(--c0)}.c1{background:var(--c1)}.c2{background:var(--c2)}
.c3{background:var(--c3);color:var(--c-hi-ink)}.c4{background:var(--c4);color:var(--c-hi-ink)}
figure{margin:1rem 0}figcaption{color:var(--ink2);font-size:.92rem;max-width:48rem;margin-top:.4rem}
svg{display:block;width:100%;height:auto;font-family:inherit}
.chart{max-width:46rem;min-width:34rem}
.widefig{overflow-x:auto}.widefig svg.wave{min-width:52rem}
.hint{display:none;font-size:.85rem;color:var(--ink2)}
.grid{stroke:var(--rule);stroke-width:1}
.axis{stroke:var(--ink2);stroke-width:1}
.tick{fill:var(--ink2);font-size:11px}
.strong{font-weight:600;fill:var(--ink)}
.val{fill:var(--ink);font-size:12px;font-weight:600}
.label{fill:var(--ink2);font-size:12px}
.bar{fill:var(--accent)}.bar:hover{opacity:.8}
.thresh{stroke:var(--ink);stroke-width:1.5;stroke-dasharray:4 3}
.sig{fill:var(--ink);font-size:12px;font-weight:600}
.sigsub{fill:var(--ink2);font-size:10px;font-family:ui-monospace,Menlo,Consolas,monospace}
.trace,.clk{fill:none;stroke:var(--accent);stroke-width:1.5}
.clk{stroke:var(--ink2);stroke-width:1}
.hifill{fill:var(--hi)}.xfill{fill:var(--x)}
.bus{fill:var(--hi);stroke:var(--accent);stroke-width:1.2}
.busx{fill:var(--x);stroke:var(--ink2);stroke-width:1}
.busval{fill:var(--ink);font-size:11px;font-family:ui-monospace,Menlo,Consolas,monospace}
.mark{fill:var(--ink2);font-size:10px}
.span{stroke:var(--ink);stroke-width:1.2;fill:none}
details{margin:.6rem 0}summary{cursor:pointer;color:var(--accent-ink)}
.notes{max-width:48rem}
.limits{background:var(--warn-bg);border-left:4px solid var(--warn-rule);padding:1rem 1.2rem;border-radius:4px}
.limits h2{padding-top:0}
footer{padding:1.5rem 1.25rem 3rem;color:var(--ink2);font-size:.88rem}
@media (max-width:40rem){.hint{display:block}th,td{padding:.35rem .45rem}.cm td{min-width:4.2rem}body{font-size:15px}}
@media print{nav{display:none}section{break-inside:avoid}}
</style>
</head>
<body>
@@BODY@@
</body>
</html>
"""


def main() -> int:
    """Write the page, or with --check compare it to the committed copy."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="do not write; exit 1 if docs/index.html differs from the sources")
    a = ap.parse_args()
    try:
        page = build()
    except SourceError as e:
        print(f"results page: SOURCE ERROR: {e}")
        return 1
    rel = os.path.relpath(OUT, ROOT)
    if a.check:
        old = open(OUT).read() if os.path.exists(OUT) else ""
        if old == page:
            print(f"results-check: PASS ({rel} matches the result files)")
            return 0
        diff = list(difflib.unified_diff(old.splitlines(), page.splitlines(),
                                         f"{rel} (committed)", f"{rel} (from sources)", n=0,
                                         lineterm=""))
        print(f"results-check: FAIL ({rel} differs from what the result files give; "
              f"run make results-page). First differences:")
        for ln in diff[:20]:
            print("  " + ln[:300])
        return 1
    with open(OUT, "w") as f:
        f.write(page)
    print(f"results page: wrote {rel} ({len(page)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
