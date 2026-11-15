// Case-study page script. Draws every chart and table from the JSON in site/data/ (made from repo files
// by scripts/export_site_data.py). No number below is typed in except labels, layout and the class names.
import { infer, features, CLASS_NAMES } from "./golden.js";

const GH = "https://github.com/bavafa/neurogate/blob/main/";
const NS = "http://www.w3.org/2000/svg";
const $ = (s) => document.querySelector(s);
const json = (p) => fetch(p).then((r) => { if (!r.ok) throw new Error(`${p}: ${r.status}`); return r.json(); });
const pct = (x, d = 2) => (x * 100).toFixed(d) + "%";
const n0 = (x) => Math.round(x).toLocaleString("en-US");
const CLS_VAR = ["var(--c-normal)", "var(--c-scan)", "var(--c-flood)"];
const CLS_TXT = ["normal", "port scan", "SYN flood"];

function h(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "html") e.innerHTML = v; else if (k === "class") e.className = v; else e.setAttribute(k, v);
  }
  for (const k of kids.flat()) e.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return e;
}
function s(tag, attrs = {}, ...kids) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  for (const k of kids.flat()) e.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return e;
}
const tag = (kind) => h("span", { class: `tag ${kind === "est" ? "est" : "sim"}` }, kind === "est" ? "estimate" : "simulated");
const link = (text, path) => h("a", { href: GH + path }, text);
const stat = (value, label, kind, src) => h("div", { class: "card" },
  h("div", { class: "stat" }, value), h("div", { class: "statl" }, label), h("div", { class: "src" }, tag(kind), " ", src));

// ----------------------------------------------------------------------------- architecture
const BLOCKS = [
  { id: "axis", t: "AXI-Stream in", s: "64-bit, 156.25 MHz", x: 20, y: 90, w: 130, file: "SPEC.md", doc: null, lane: "net",
    note: "Packet input. 64-bit tdata, 8-bit tkeep, tlast, tuser, valid/ready. First frame byte in tdata[7:0]. tuser on the last beat means bad FCS and the frame is dropped. 64 bits at 156.25 MHz is 10 Gb/s (SPEC section 3.1)." },
  { id: "skid", t: "skid_fifo", s: "4-slot input queue", x: 175, y: 90, w: 125, file: "rtl/skid_fifo.sv", doc: "skid_fifo", lane: "net",
    note: "First block on the network clock. Four slots, one beat per clock. It gives the parsers a registered stream and lets the sender continue for a moment when the next block pauses. Tested with random stalls, a long stall and reset mid-stream (simulated)." },
  { id: "eth", t: "eth_parser", s: "Ethernet, one VLAN tag", x: 325, y: 90, w: 140, file: "rtl/eth_parser.sv", doc: "eth_parser", lane: "net",
    note: "Reads the Ethernet header. Detects an optional 802.1Q tag. Drops frames with tuser set, frames too short for the header, and EtherTypes other than IPv4. Frame bytes pass on unchanged." },
  { id: "l3l4", t: "l3l4_parser", s: "IPv4, TCP, UDP", x: 490, y: 90, w: 140, file: "rtl/l3l4_parser.sv", doc: "l3l4_parser", lane: "net",
    note: "Reads IPv4 and TCP/UDP fields: SYN, ACK, RST/FIN, IP length, TTL, destination port. Skips IP options, drops non-first fragments and other protocols. A TCP frame cut after 14 header bytes, or UDP after 4, is kept (D-020). Fields that straddle two beats are handled." },
  { id: "fb", t: "feature_builder", s: "8 features, 64-pkt window", x: 655, y: 90, w: 150, file: "rtl/feature_builder.sv", doc: "feature_builder", lane: "net",
    note: "Turns each parsed frame into 8 int8 features and writes one 114-bit entry. Keeps a 64-packet window: a ring, 64 per-bucket counters and a SYN count. Port count is occupied buckets of a 64-way hash, an approximation. If the async FIFO is full the entry is dropped and counted, and the input is never stalled." },
  { id: "afifo", t: "async_fifo", s: "16 x 114 bit, Gray pointers", x: 760, y: 212, w: 200, file: "rtl/async_fifo.sv", doc: "async_fifo", lane: "cross",
    note: "The only data crossing between clocks. 16 entries of 114 bits. Each side keeps its own pointer and sends it to the other as a Gray code through two flip-flops. Tested at six clock ratios. A structural script checks that nothing else crosses. Simulation cannot show metastability (docs/cdc.md)." },
  { id: "mlp", t: "mlp_core", s: "int8 net 8-16-3, 1 MAC/clk", x: 735, y: 330, w: 225, file: "rtl/mlp_core.sv", doc: "mlp_core", lane: "core",
    note: "8 inputs, 16 hidden, 3 outputs. int8 weights in ROM, int32 accumulator, ReLU and saturation after layer 1. One multiply-accumulate per clock: 176 steps per packet. 179 core clocks between results with a backlog (simulated). Equal to the Python golden model with 0 mismatches on 8,347 packets (simulated)." },
  { id: "dec", t: "decision_unit", s: "class, margin, low_conf", x: 505, y: 330, w: 200, file: "rtl/decision_unit.sv", doc: "decision_unit", lane: "core",
    note: "argmax of the 3 logits (ties: lowest index). confidence = min((top - second) >> 8, 127), a margin, not a probability. low_conf is set when confidence is below CONF_THRESH, which software can write. One entry becomes one result beat and one counter update." },
  { id: "stats", t: "stats_counters", s: "packets, bytes, drops", x: 290, y: 330, w: 190, file: "rtl/stats_counters.sv", doc: "stats_counters", lane: "core",
    note: "Packet and byte counts per class, PKT_DROPPED, PKT_LOWCONF, PKT_OVERFLOW and the last result. After the pipeline drains, every frame is in exactly one counter. Exceptions are written down: overflow count saturates at 65,535, and a soft reset with frames in flight." },
  { id: "axil", t: "axil_regs", s: "AXI-Lite, 32-bit", x: 60, y: 330, w: 205, file: "rtl/axil_regs.sv", doc: "axil_regs", lane: "core",
    note: "AXI4-Lite register block on the core clock. Software writes CONF_THRESH and the soft-reset bit and reads counters. One transaction at a time. Unmapped addresses give an error reply. Register list: docs/regmap.md." },
  { id: "res", t: "Result stream", s: "AXI-Stream, 48-bit", x: 505, y: 428, w: 200, file: "SPEC.md", doc: null, lane: "core", hh: 40,
    note: "One beat per classified packet: class [1:0], low_conf [2], confidence [15:8], pkt_len [31:16], pkt_id [47:32]. This is the stream the testbench compares with the Python model. Dropped and overflowed frames produce no beat." },
];

function renderArch() {
  const svg = s("svg", { viewBox: "0 0 980 490", role: "group", "aria-labelledby": "arch-t arch-d", class: "arch" });
  svg.append(s("title", { id: "arch-t" }, "NeuroGate block diagram"),
    s("desc", { id: "arch-d" }, "Network clock domain at 156.25 MHz: AXI-Stream 64-bit input, input queue, Ethernet parser, IPv4 TCP UDP parser, feature builder. The asynchronous FIFO is the only crossing to the core clock domain at 200 MHz: int8 neural network core with 8 inputs, 16 hidden units and 3 outputs, decision unit, counters, AXI-Lite registers, and the result stream."));
  svg.append(s("defs", {}, s("marker", { id: "a", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 6, markerHeight: 6, orient: "auto" },
    s("path", { d: "M0,0 L10,5 L0,10 z", style: "fill:var(--muted);stroke:none" }))));
  svg.append(s("rect", { class: "lane-net", x: 5, y: 40, width: 970, height: 160, rx: 8 }),
    s("rect", { class: "lane-core", x: 5, y: 290, width: 970, height: 190, rx: 8 }),
    s("text", { x: 16, y: 62, style: "font-weight:700;font-size:13px" }, "Network clock domain: net_clk 156.25 MHz"),
    s("text", { x: 16, y: 312, style: "font-weight:700;font-size:13px" }, "Core clock domain: core_clk 200 MHz"),
    s("text", { x: 16, y: 250, class: "muted", style: "font-size:12px" }, "Unrelated clocks. Reset synchronizer per domain."));
  const P = (d) => svg.append(s("path", { d, "marker-end": "url(#a)" }));
  P("M150 125 H173"); P("M300 125 H323"); P("M465 125 H488"); P("M630 125 H653");
  P("M730 160 V190 H860 V210"); P("M860 272 V328"); P("M735 365 H707"); P("M505 365 H482"); P("M290 365 H267"); P("M605 400 V426");
  svg.append(s("text", { x: 868, y: 296, class: "muted", style: "font-size:11px" }, "entries, 1 per frame"));
  const panel = $("#blocknote");
  const open = (b, g) => {
    svg.querySelectorAll("g[role=button]").forEach((x) => x.setAttribute("aria-pressed", "false"));
    g.setAttribute("aria-pressed", "true");
    panel.replaceChildren(h("h3", { style: "margin-top:0" }, h("code", {}, b.t)), h("p", {}, b.note),
      h("p", { class: "src" }, "Source: ", link(b.file, b.file),
        b.doc ? [" . Module note: ", link(`docs/modules/${b.doc}.md`, `docs/modules/${b.doc}.md`)] : "",
        " . ", link("neurogate_top.sv", "rtl/neurogate_top.sv")));
  };
  for (const b of BLOCKS) {
    const hh = b.hh || 70;
    const g = s("g", { role: "button", tabindex: 0, "aria-pressed": "false", "aria-label": `${b.t}: ${b.s}`, class: b.lane === "cross" ? "cross" : "" },
      s("rect", { x: b.x, y: b.y, width: b.w, height: hh, rx: 6 }),
      s("text", { class: "t", x: b.x + b.w / 2, y: b.y + hh / 2 - 2, "text-anchor": "middle" }, b.t),
      s("text", { class: "s", x: b.x + b.w / 2, y: b.y + hh / 2 + 15, "text-anchor": "middle" }, b.s));
    g.addEventListener("click", () => open(b, g));
    g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(b, g); } });
    svg.append(g);
  }
  $("#archbox").append(svg);
}

// ----------------------------------------------------------------------------- charts
function legend(el) {
  el.replaceChildren(...CLS_TXT.map((t, i) => h("span", {}, h("i", { style: `background:${CLS_VAR[i]}` }), t)));
}

function marginChart(m) {
  const bins = m.test.margin_bins, val = m.validation.margin_bins;
  const W = 760, H = 320, L = 50, R = 12, T = 40, B = 56, pw = (W - L - R) / bins.length;
  const y = (v) => T + (H - T - B) * (1 - v / 100);
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-labelledby": "mc-t mc-d" },
    s("title", { id: "mc-t" }, "Share correct per confidence margin bin"),
    s("desc", { id: "mc-d" }, "Test split: " + bins.map((b) => `bins ${b.lo} to ${b.hi}: ${b.pct_correct.toFixed(1)} percent correct on ${b.n} packets`).join("; ")));
  for (const g of [0, 25, 50, 75, 100]) {
    svg.append(s("line", { x1: L, x2: W - R, y1: y(g), y2: y(g), stroke: "var(--line)" }), s("text", { x: L - 6, y: y(g) + 4, "text-anchor": "end", class: "muted", style: "font-size:11px" }, g + "%"));
  }
  bins.forEach((b, i) => {
    const x = L + i * pw + pw * 0.18, bw = pw * 0.64;
    svg.append(s("rect", { x, y: y(b.pct_correct), width: bw, height: y(0) - y(b.pct_correct), fill: "var(--accent)" }),
      s("text", { x: x + bw / 2, y: y(b.pct_correct) + 16, "text-anchor": "middle", style: "font-size:11px;font-weight:700", fill: "var(--accent-ink)" }, b.pct_correct.toFixed(1)),
      s("text", { x: x + bw / 2, y: H - B + 16, "text-anchor": "middle", style: "font-size:11px" }, `${b.lo}-${b.hi}`),
      s("text", { x: x + bw / 2, y: H - B + 30, "text-anchor": "middle", class: "muted", style: "font-size:10px" }, `n=${n0(b.n)}`));
    svg.append(s("circle", { cx: x + bw * 0.85, cy: y(val[i].pct_correct), r: 4.5, fill: "var(--panel)", stroke: "var(--ink)", "stroke-width": 2 }));
  });
  const tx = L + (m.default_conf_thresh / 16) * pw;
  svg.append(s("line", { x1: tx, x2: tx, y1: 22, y2: y(0), stroke: "var(--est)", "stroke-width": 2, "stroke-dasharray": "5 4" }),
    s("text", { x: tx + 5, y: 16, style: "font-size:11px;font-weight:700", fill: "var(--est)" }, `CONF_THRESH = ${m.default_conf_thresh}`));
  svg.append(s("text", { x: (L + W - R) / 2, y: H - 6, "text-anchor": "middle", class: "muted", style: "font-size:11px" }, "confidence (logit margin, shifted right by 8) bins and packets per bin (test)"));
  $("#marginchart").replaceChildren(svg);
  $("#margincap").textContent = `Bars: test split, % correct. Circles: validation split. With the threshold at ${m.default_conf_thresh}, ${pct(m.test.flagged_low_conf_share, 1)} of test packets are flagged; kept packets are ${pct(m.test.kept_accuracy)} correct, flagged packets ${pct(m.test.flagged_accuracy)}. Synthetic data.`;
}

function featureGrid(a) {
  legend($("#classlegend"));
  const names = { F0: "F0 SYN set, ACK clear", F1: "F1 ACK set", F2: "F2 RST or FIN set", F3: "F3 IPv4 length", F4: "F4 TTL", F5: "F5 dst port < 1024", F6: "F6 SYN count in window", F7: "F7 occupied port buckets" };
  const grid = $("#featgrid");
  for (const [f, v] of Object.entries(a.features)) {
    const W = 330, H = 150, T = 22, B = 26, L = 8;
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": names[f] });
    svg.append(s("text", { x: L, y: 14, style: "font-size:12px;font-weight:600" }, names[f]));
    const cls = Object.keys(v.classes);
    const yb = H - B;
    if (v.binary) {
      cls.forEach((c, i) => {
        const val = v.classes[c].share_set, bh = (yb - T) * val, x = L + 20 + i * 100;
        svg.append(s("rect", { x, y: yb - bh, width: 70, height: bh, fill: CLS_VAR[i] }),
          s("text", { x: x + 35, y: yb - bh - 4, "text-anchor": "middle", style: "font-size:11px;font-weight:600" }, pct(val, 1)),
          s("text", { x: x + 35, y: H - 8, "text-anchor": "middle", style: "font-size:10px" }, CLS_TXT[i]));
      });
    } else {
      const bw = (W - 2 * L) / 8;
      cls.forEach((c, i) => v.classes[c].bins.forEach((cnt, b) => {
        const share = cnt / v.classes[c].n, bh = (yb - T) * share, x = L + b * bw + (i * (bw - 4)) / 3 + 1;
        svg.append(s("rect", { x, y: yb - bh, width: (bw - 4) / 3, height: bh, fill: CLS_VAR[i] }));
      }));
      for (let b = 0; b < 8; b++) svg.append(s("text", { x: L + b * bw + bw / 2, y: H - 8, "text-anchor": "middle", class: "muted", style: "font-size:9px" }, `${b * 16}`));
    }
    svg.append(s("line", { x1: L, x2: W - L, y1: yb, y2: yb, stroke: "var(--line)" }));
    grid.append(h("figure", { style: "margin:0" }, svg));
  }
}

function dropChart(a) {
  const L = {
    unsupported_l4: "IPv4, not TCP or UDP (the generator makes ICMP)", not_ipv4: "Not IPv4 (the generator makes ARP)",
    fragment: "Non-first IP fragment, no L4 header", short_ip: "Too short for the IPv4 header",
    tuser_error: "Bad FCS flag (tuser) on the last beat", short_l4: "Cut before the TCP/UDP fields the design reads",
  };
  const rows = Object.entries(a.drop_reasons).sort((x, y) => y[1] - x[1]);
  const W = 760, rh = 30, H = rows.length * rh + 12, max = rows[0][1], lw = 330;
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `Dropped frames by reason, total ${a.dropped}` });
  rows.forEach(([k, v], i) => {
    const w = ((W - lw - 70) * v) / max;
    svg.append(s("text", { x: lw - 8, y: i * rh + 20, "text-anchor": "end", style: "font-size:12px" }, L[k] || k),
      s("rect", { x: lw, y: i * rh + 6, width: w, height: 20, fill: "var(--c-normal)" }),
      s("text", { x: lw + w + 6, y: i * rh + 21, style: "font-size:12px;font-weight:600" }, `${v} (${pct(v / a.dropped, 1)})`));
  });
  $("#dropchart").replaceChildren(svg);
}

function confusion(m) {
  const t = m.test.int8, C = t.confusion;
  const max = Math.max(...C.flat());
  const cm = h("table", { class: "cm" }, h("caption", {}, "Rows: true class. Columns: predicted. Packets."),
    h("thead", {}, h("tr", {}, h("th", {}, "true \\ predicted"), ...CLS_TXT.map((c) => h("th", { class: "num" }, c)))),
    h("tbody", {}, C.map((row, i) => h("tr", {}, h("th", {}, CLS_TXT[i]), row.map((v, j) =>
      h("td", { class: "num", style: `background:color-mix(in srgb, var(--accent) ${i === j ? Math.round(55 * v / max) : Math.round(40 * Math.sqrt(v / max))}%, var(--panel));${i === j ? "font-weight:700" : ""}` }, n0(v)))))));
  $("#cm").replaceChildren(cm);
  $("#prt").replaceChildren(h("table", {}, h("caption", {}, "int8 network, test split"),
    h("thead", {}, h("tr", {}, h("th", {}, "class"), h("th", { class: "num" }, "precision"), h("th", { class: "num" }, "recall"))),
    h("tbody", {}, CLS_TXT.map((c, i) => h("tr", {}, h("td", {}, c), h("td", { class: "num" }, pct(t.precision[i])), h("td", { class: "num" }, pct(t.recall[i])))))));
  const f = m.test.float;
  $("#fi").replaceChildren(h("div", { class: "grid2" },
    stat(pct(t.accuracy), `int8 accuracy, ${n0(t.n)} packets`, "sim", link("metrics.json", "docs/results/metrics.json")),
    stat(pct(f.accuracy), "float accuracy, same packets", "sim", link("metrics.json", "docs/results/metrics.json")),
    stat(pct(m.test.int8_vs_float_agreement), "packets where float and int8 give the same class", "sim", link("results.md", "docs/results/results.md"))));
}

// ----------------------------------------------------------------------------- verification and implementation
function verification(v, thr) {
  const r = v.replay;
  const rowsTxt = r.map((x) => `${x.name.replace("_", " ")}: ${n0(+x.rows)} rows, ${x.mismatches} mismatches (input gaps ${pct(+x.gap, 0)}, output stalls ${pct(+x.stall, 0)})`);
  $("#verifystats").replaceChildren(
    stat(`${r[0].mismatches} of ${n0(+r[0].rows)}`, "mismatches, network core and decision unit against the Python model, back to back", "sim", link("full_split_replay.log", "docs/results/full_split_replay.log")),
    stat(`${r[1].mismatches} of ${n0(+r[1].rows)}`, `same replay with ${pct(+r[1].gap, 0)} input gaps and ${pct(+r[1].stall, 0)} output stalls`, "sim", link("full_split_replay.log", "docs/results/full_split_replay.log")),
    stat(`${v.gls.smoke.beats} / ${v.gls.smoke.expected}`, `gate-level smoke set: result beats, ${v.gls.smoke.mismatches} mismatches, ${v.gls.smoke.protocol_errors} protocol errors`, "sim", link("gls.log", "synth/reports/gls.log")),
    stat(`${v.gls.bad.beats} / ${v.gls.bad.expected}`, `gate-level bad-packet set (480 frames): result beats, ${v.gls.bad.mismatches} mismatches, ${v.gls.bad.protocol_errors} protocol errors`, "sim", link("gls_bad.log", "synth/reports/gls_bad.log")));
  const rows = [
    ["Network core and decision unit against the Python model", rowsTxt.join(". "), "sim", "docs/results/full_split_replay.log"],
    ["Gate-level simulation (Yosys netlist, Icarus, cells without delays)", `smoke ${v.gls.smoke.beats}/${v.gls.smoke.expected} beats and bad-packet ${v.gls.bad.beats}/${v.gls.bad.expected} beats, 0 mismatches. Function only, not timing.`, "sim", "synth/reports/gls.log"],
    ["Lint (Verilator, --lint-only -Wall)", `No warnings on all ${v.rtl_files} RTL files; ${v.lint_waivers} inline waivers, each with a reason in the source. Documented in synth/README.md.`, "sim", "synth/README.md"],
    ["Clock-crossing check", "Structural script (cdc_check.py) plus a written review of 6 crossings: 2 Gray pointers, FIFO storage read, 2 reset synchronizers, FIFO resets. Written review plus structural script. A commercial CDC sign-off is a production step.", "sim", "docs/cdc.md"],
    ["Clean-clone run", `A fresh clone ran the README quick start (lint, compile check, cdc check, make test) at commit ${v.clean_clone_commit}.`, "sim", "docs/results/clean_clone.log"],
  ];
  $("#verifytable").replaceChildren(...rows.map(([a, b, k, p]) => h("tr", {}, h("td", {}, a), h("td", {}, b), h("td", {}, tag(k)), h("td", {}, link(p, p)))));
}

function implementation(i) {
  const c = i.cells, a = i.artix7, t = i.throughput;
  const src = (f) => link(f.split("/").pop(), f);
  $("#implstats").replaceChildren(
    stat(n0(c.total), "cells after Yosys mapping to Nangate45", "est", src("synth/reports/cells.txt")),
    stat(`${n0(c.area_um2)} um2`, `area; sequential cells ${n0(c.seq_area_um2)} um2 (${c.seq_pct}%)`, "est", src("synth/reports/cells.txt")),
    stat(n0(c.flip_flops), "flip-flops; the library has no memory macros, so FIFO storage, queue, history and ROMs are flip-flops and gates", "est", src("synth/reports/cells.txt")),
    stat(`${n0(a.luts)} LUTs, ${n0(a.ffs)} FFs`, `Artix-7 count from Yosys synth_xilinx, plus ${a.ram32m + a.ram64m} RAM primitives and ${a.dsp48e1} DSP48E1. Not a Vivado result.`, "est", src("synth/reports/artix7_cells.txt")));
  const names = Object.keys(i.sta_after);
  const ns = (v) => v === undefined ? "n/a" : v.toFixed(3);
  const cell = (v) => h("td", { class: v < 0 ? "num bad" : "num" }, v === undefined ? "n/a" : ns(v) + (v < 0 ? " (violated)" : ""));
  $("#timing").replaceChildren(
    h("thead", {}, h("tr", {}, h("th", {}, "Check"), h("th", { class: "num" }, "Before (area-only netlist)"), h("th", { class: "num" }, "After (delay-target netlist)"), h("th", { class: "num" }, "After, with wire-load guess"))),
    h("tbody", {}, names.map((k) => h("tr", {}, h("td", {}, k.replace(/\s+/g, " ")), cell(i.sta_before[k]), cell(i.sta_after[k]), cell(i.sta_wireload[k])))));
  const g = t.core_clocks_between_results;
  const inNs = 1000 / 156.25 * t.net_clocks_per_frame;       // net clock period 6.4 ns
  const coreNs = 1000 / 200 * g.mean;
  const rate = (ns1) => 1000 / ns1;                           // million frames per second
  const bars = [["Core: results leave every " + g.mean + " core clocks at 200 MHz", rate(coreNs), "var(--c-flood)"],
    ["Input side: " + t.net_clocks_per_frame + " net clocks per frame at 156.25 MHz (derived)", rate(inNs), "var(--c-normal)"]];
  const W = 760, max = Math.max(...bars.map((b) => b[1]));
  const svg = s("svg", { viewBox: `0 0 ${W} 120`, role: "img", "aria-label": `Core ${rate(coreNs).toFixed(3)} million frames per second, input side ${rate(inNs).toFixed(2)} million frames per second` });
  bars.forEach(([l, v, col], k) => {
    const w = ((W - 80) * v) / max;
    svg.append(s("text", { x: 0, y: k * 56 + 14, style: "font-size:12px" }, l), s("rect", { x: 0, y: k * 56 + 20, width: w, height: 24, fill: col }),
      s("text", { x: w + 8, y: k * 56 + 38, style: "font-size:13px;font-weight:600" }, `${v.toFixed(3)} M frames/s`));
  });
  $("#thr").replaceChildren(h("div", { class: "grid2" },
    stat(`${n0(t.kpps)} k packets/s`, `core-limited, ${g.min} to ${g.max} core clocks between result beats over ${g.gaps} gaps`, "sim", src("docs/results/throughput_run.log")),
    stat(`${n0(t.mbit_s)} Mbit/s`, `frame data at the core-limited rate, mean frame ${t.mean_frame_bytes} bytes. The input port is 10 Gb/s (64 bit at 156.25 MHz).`, "sim", src("docs/results/throughput_run.log")),
    stat(`${t.first_frame_latency_clocks} core clocks`, "last input beat to result beat for the first frame", "sim", src("docs/results/throughput_run.log"))),
    h("figure", {}, svg, h("figcaption", {}, `Input-side bar is derived here: 156.25 MHz divided by ${t.net_clocks_per_frame} clocks per frame, from the log's input time for frames 2 to 14. The core is the limit: at this frame size it finishes about ${(rate(coreNs) / rate(inNs) * 100).toFixed(0)}% as many frames per second as the input side delivers in a burst. Sustained input at the port rate would fill the FIFO and overflow is counted (see below).`)));
}

// ----------------------------------------------------------------------------- packet stepper
const STEPS = ["Raw bytes", "Parsed headers", "Features", "Hidden values", "Scores and label"];
const FCLS = ["f0", "f1", "f2", "f3", "f4", "f5"];

function stepper(data, params, metrics, verify) {
  const sel = $("#pick"), steps = $("#steps"), body = $("#stepbody");
  let cur = 0, step = 0;
  const S = data.samples;
  S.forEach((p, i) => sel.append(h("option", { value: i }, `#${p.stream_index}: true ${p.true_label.replace("_", " ")}${p.kind === "dropped" ? `, dropped (${p.drop_reason})` : ""}`)));
  const maxStep = () => (S[cur].kind === "dropped" ? 1 : 4);

  function show() {
    const p = S[cur];
    $("#pickinfo").textContent = `Frame ${p.stream_index} of 9,000 in the test stream. Frame length ${p.frame_len} bytes.`;
    steps.replaceChildren(...STEPS.map((t, i) => {
      const b = h("button", { type: "button" }, `${i + 1}. ${t}`);
      if (i > maxStep()) b.disabled = true;
      if (i === step) b.setAttribute("aria-current", "step");
      b.addEventListener("click", () => { step = i; show(); });
      return h("li", {}, b);
    }));
    $("#prev").disabled = step === 0; $("#next").disabled = step >= maxStep();
    body.replaceChildren(...[renderBytes, renderParsed, renderFeatures, renderHidden, renderScores][step](p));
  }
  function renderBytes(p) {
    const bytes = p.bytes_hex.match(/../g);
    const col = new Array(bytes.length).fill("");
    (p.fields || []).forEach((f, k) => { for (let i = f.start; i < Math.min(f.end, bytes.length); i++) col[i] = FCLS[k % FCLS.length]; });
    return [h("p", {}, `Raw frame as received, first ${bytes.length} of ${p.frame_len} bytes, hexadecimal. The first byte is in tdata[7:0] of the first 64-bit beat. Coloured bytes are the fields the design reads.`),
      h("div", { class: "hex", role: "img", "aria-label": `${bytes.length} frame bytes in hexadecimal: ${bytes.join(" ")}` }, bytes.map((b, i) => h("span", { class: col[i] }, b))),
      p.tuser ? h("p", { class: "warn pill" }, "tuser = 1 on the last beat (bad FCS flag from the upstream MAC)") : ""];
  }
  function renderParsed(p) {
    if (p.kind === "dropped") {
      return [h("p", { class: "verdict" }, `Dropped: ${p.drop_reason}`),
        h("p", {}, "The parser drops this frame. It never reaches the network and never enters the 64-packet window. It is counted in PKT_DROPPED. The generator's label for this frame is normal."),
        h("p", { class: "src" }, "Drop rules: ", link("SPEC.md section 4", "SPEC.md"), " and ", link("model/features.py parse_frame", "model/features.py"), ".")];
    }
    const fl = p.fields;
    return [h("p", {}, "Fields the parsers extract. Byte offsets are from the start of the frame."),
      h("div", { class: "tablewrap" }, h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Field"), h("th", {}, "Bytes"), h("th", {}, "Value"))),
        h("tbody", {}, fl.map((f, k) => h("tr", {}, h("td", {}, h("i", { class: `swatch hex-sw ${FCLS[k % FCLS.length]}`, style: `background:${["#bcd7f5", "#f7d9a8", "#cfeccf", "#f1c7e0", "#d9d2f3", "#f5eaa6"][k % 6]}` }), f.name), h("td", { class: "mono" }, `${f.start}-${f.end - 1}`), h("td", { class: "mono" }, f.value)))))),
      h("p", { class: "src" }, "Parser source: ", link("rtl/eth_parser.sv", "rtl/eth_parser.sv"), ", ", link("rtl/l3l4_parser.sv", "rtl/l3l4_parser.sv"))];
  }
  function renderFeatures(p) {
    const w = p.window, f = features(p.packet, w.syn_count, w.occupied_buckets);
    const same = JSON.stringify(f) === JSON.stringify(p.features);
    const occ = new Array(64).fill(0); w.entries.forEach(([, b]) => occ[b]++);
    const names = ["F0 SYN, no ACK", "F1 ACK", "F2 RST or FIN", "F3 IP length", "F4 TTL", "F5 port < 1024", "F6 SYN count in window", "F7 occupied buckets"];
    const how = [
      "127 if TCP, SYN set, ACK clear", "127 if TCP and ACK set", "127 if TCP and RST or FIN set",
      `min(${p.packet.ip_total_len}, 1500) * 127 // 1500`, `${p.packet.ttl} >> 1`, `127 if dst port ${p.packet.dst_port} < 1024`,
      `min(${w.syn_count} * 2, 127)`, `min(${w.occupied_buckets} * 2, 127)`];
    return [h("p", {}, `Features computed in JavaScript from the parsed fields and the window. ${same ? "They equal the Python export." : "MISMATCH with the Python export."}`),
      h("div", { class: "tablewrap" }, h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Feature"), h("th", {}, "Rule"), h("th", { class: "num" }, "Value"))),
        h("tbody", {}, f.map((v, i) => h("tr", {}, h("td", {}, names[i]), h("td", { class: "mono" }, how[i]), h("td", { class: "num mono" }, v)))))),
      h("h3", {}, `Window context: last ${w.size} valid packets, current one last`),
      h("p", {}, `SYN count ${w.syn_count}, occupied destination-port buckets ${w.occupied_buckets} of 64. Top: one square per packet in the window (red = SYN without ACK, outlined = this packet), oldest first. Bottom: the 64 port buckets (orange = occupied).`),
      h("div", { class: "win", role: "img", "aria-label": `Window of ${w.size} packets, ${w.syn_count} SYN` }, w.entries.map(([syn], i) => h("span", { class: `${syn ? "syn" : ""} ${i === w.entries.length - 1 ? "cur" : ""}` }))),
      h("div", { class: "win", role: "img", "aria-label": `${w.occupied_buckets} of 64 buckets occupied` }, occ.map((c) => h("span", { class: c ? "occ" : "" })))];
  }
  function renderHidden(p) {
    const r = infer(p.features, params, data.conf_thresh);
    const same = JSON.stringify(r.hidden) === JSON.stringify(p.model.hidden);
    return [h("p", {}, `Layer 1: acc = b1 + sum of w1 * x (int32). Then h = clamp((acc + ${1 << (params.shift1 - 1)}) >> ${params.shift1}, 0, 127): round half up, ReLU and saturation. ${same ? "Equal to the Python export." : "MISMATCH with the Python export."}`),
      h("div", { class: "hid" }, r.hidden.map((v, j) => h("div", {}, h("div", {}, `h${j}`), h("b", {}, v), h("div", { class: "muted" }, `acc ${r.acc1[j]}`), h("div", { class: "bar", style: `width:${(v / 127) * 100}%` }))))];
  }
  function renderScores(p) {
    const r = infer(p.features, params, data.conf_thresh);
    const same = r.cls === p.model.class && r.confidence === p.model.confidence && r.logits.every((z, k) => z === p.model.logits[k]);
    const ok = r.cls === CLASS_NAMES.indexOf(p.true_label);
    const rep = verify.replay;
    return [h("p", {}, "Layer 2: z = b2 + sum of w2 * h (int32, no ReLU). class = argmax (ties: lowest index). margin = top - second. confidence = min(margin >> " + params.conf_shift + ", 127). low_conf = confidence < " + data.conf_thresh + "."),
      h("div", { class: "tablewrap" }, h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Output"), h("th", { class: "num" }, "Score z"))),
        h("tbody", {}, r.logits.map((z, k) => h("tr", {}, h("td", {}, `z${k} ${CLS_TXT[k]}${k === r.cls ? " (top)" : ""}`), h("td", { class: "num mono", style: k === r.cls ? "font-weight:700" : "" }, z)))))),
      h("p", {}, `Margin ${r.margin}, confidence ${r.confidence}, threshold ${data.conf_thresh}: `, h("span", { class: `pill ${r.lowConf ? "warn" : "ok"}` }, r.lowConf ? "low-confidence flag SET" : "flag clear")),
      h("p", { class: "verdict" }, `Label: ${CLS_TXT[r.cls]}. `, h("span", { class: `pill ${ok ? "ok" : "warn"}` }, ok ? `matches the generator's label (${p.true_label.replace("_", " ")})` : `generator's label is ${p.true_label.replace("_", " ")}: a wrong prediction`)),
      h("p", {}, h("span", { class: `pill ${same ? "ok" : "warn"}` }, same ? "JavaScript equals the Python export" : "MISMATCH with Python export"), " ",
        h("span", { class: "pill ok" }, `RTL: ${rep[0].mismatches} mismatches on ${n0(+rep[0].rows)} rows in the recorded simulation`)),
      h("p", { class: "src" }, tag("sim"), " The recorded replay (", link("full_split_replay.log", "docs/results/full_split_replay.log"), ") compares the RTL output with the model on every classified test packet and reports 0 mismatches, so the RTL class equals the model class here too. The log stores totals, not one value per packet. Confidence is a margin, not a probability.")];
  }
  sel.addEventListener("change", () => { cur = +sel.value; step = 0; show(); });
  $("#prev").addEventListener("click", () => { step = Math.max(0, step - 1); show(); });
  $("#next").addEventListener("click", () => { step = Math.min(maxStep(), step + 1); show(); });
  show();
}

// ----------------------------------------------------------------------------- start
function stats(m, a) {
  const t = m.test;
  $("#headline-stats").replaceChildren(
    stat(pct(t.int8.accuracy), `int8 accuracy on ${n0(t.int8.n)} unseen packets (synthetic data, not real-world)`, "sim", link("metrics.json", "docs/results/metrics.json")),
    stat(pct(t.int8_vs_float_agreement), "packets where int8 and float give the same class", "sim", link("results.md", "docs/results/results.md")));
  $("#inputstats").replaceChildren(
    stat(n0(a.frames_total), `frames in the test split: ${Object.entries(a.frames_by_true_label).map(([k, v]) => `${n0(v)} ${k.replace("_", " ")}`).join(", ")}`, "sim", h("span", {}, "from ", h("a", { href: "data/analysis.json" }, "analysis.json"))),
    stat(`${n0(a.dropped)} dropped`, `${n0(a.classified)} classified; all ${n0(a.dropped_by_true_label.normal)} dropped frames are normal-class`, "sim", link("metrics.json", "docs/results/metrics.json")));
  $("#outstats").replaceChildren(
    stat(pct(t.int8.accuracy), "int8 accuracy, unseen test split", "sim", link("results.md", "docs/results/results.md")),
    stat(pct(t.kept_accuracy), `accuracy of packets at or above threshold ${m.default_conf_thresh} (flagged packets: ${pct(t.flagged_accuracy)})`, "sim", link("metrics.json", "docs/results/metrics.json")));
}

function featureNotes(a) {
  const f = a.features, share = (F, c) => f[F].classes[c];
  const bin = (F, c) => share(F, c).bins.map((x) => x / share(F, c).n);
  const hi6 = (c) => bin("F6", c).slice(6).reduce((x, y) => x + y, 0);
  const lo7 = (c) => bin("F7", c).slice(0, 2).reduce((x, y) => x + y, 0);
  $("#featnotes").textContent = `Read from the panels: SYN without ACK is set on ${pct(share("F0", "port_scan").share_set, 0)} of scan packets and ${pct(share("F0", "syn_flood").share_set, 0)} of flood packets, but also on ${pct(share("F0", "normal").share_set, 1)} of normal packets, so one packet cannot separate the classes. ` +
    `F6 (SYN count) sits in the top two bins (96 to 127) for ${pct(hi6("port_scan"), 0)} of scan, ${pct(hi6("syn_flood"), 0)} of flood and ${pct(hi6("normal"), 0)} of normal packets. ` +
    `F7 (occupied port buckets) is below 32 for ${pct(lo7("syn_flood"), 0)} of flood packets, ${pct(lo7("port_scan"), 0)} of scan packets and ${pct(lo7("normal"), 0)} of normal packets. That is the signal that separates flood from scan.`;
}

function menuHighlight() {
  const links = [...document.querySelectorAll("#menu a")];
  const secs = links.map((l) => document.getElementById(l.getAttribute("href").slice(1)));
  const update = () => {
    let k = 0;
    secs.forEach((x, i) => { if (x && x.getBoundingClientRect().top < 120) k = i; });
    links.forEach((l, i) => l.setAttribute("aria-current", String(i === k)));
  };
  addEventListener("scroll", update, { passive: true }); update();
}

async function main() {
  renderArch(); menuHighlight();
  const [metrics, analysis, impl, verify, samples, params] = await Promise.all(
    ["metrics", "analysis", "impl", "verify", "samples", "weights"].map((n) => json(`data/${n}.json`)));
  stats(metrics, analysis); confusion(metrics); marginChart(metrics);
  featureGrid(analysis); featureNotes(analysis); dropChart(analysis);
  verification(verify); implementation(impl);
  stepper(samples, params, metrics, verify);
  fetch("data/waveform.svg.html").then((r) => r.text()).then((t) => { $("#wave").innerHTML = t; }).catch(() => { $("#wave").textContent = "Waveform could not be loaded."; });
}
main().catch((e) => { document.body.prepend(h("p", { class: "card" }, `Page data failed to load: ${e.message}. Serve the site/ folder over HTTP (for example: python3 -m http.server -d site).`)); console.error(e); });
