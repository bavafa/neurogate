// Replay of the Python reference model (model/golden.py, model/features.py) in JavaScript.
// Integer arithmetic only. Every value stays far inside the int32 range (golden.py `validate`
// guarantees it), so JS numbers and the `>>` operator (32-bit arithmetic shift, floor) match
// Python's unbounded integers and `>>`. Run `node site/test/golden.test.mjs` to compare this
// file with the Python outputs on all 8,347 test packets.

export const N_IN = 8, N_HID = 16, N_OUT = 3, ACT_MAX = 127;
export const CLASS_NAMES = ["normal", "port_scan", "syn_flood"];

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

/** Layer 1 + requantize + ReLU: 8 features (0..127) -> 16 activations (0..127). */
export function hidden(x, p) {
  if (x.length !== N_IN || x.some((v) => !Number.isInteger(v) || v < 0 || v > ACT_MAX)) {
    throw new RangeError("features must be 8 ints in 0..127");
  }
  const acc = [], out = [];
  for (let j = 0; j < N_HID; j++) {
    let a = p.b1[j];
    for (let i = 0; i < N_IN; i++) a += p.w1[j][i] * x[i];
    acc.push(a);
    // round half up: add half, then arithmetic shift right (floor). No rounding term if shift is 0.
    const pre = p.shift1 ? (a + (1 << (p.shift1 - 1))) >> p.shift1 : a;
    out.push(clamp(pre, 0, ACT_MAX));         // clamp at 0 is the ReLU, clamp at 127 is the saturation
  }
  return { h: out, acc };
}

/** Layer 2: 16 activations -> 3 int32 logits (no ReLU, no requantize). */
export function logits(h, p) {
  const z = [];
  for (let k = 0; k < N_OUT; k++) {
    let a = p.b2[k];
    for (let j = 0; j < N_HID; j++) a += p.w2[k][j] * h[j];
    z.push(a);
  }
  return z;
}

/** Logits -> class (ties: lowest index), margin, confidence = min(margin >> conf_shift, 127), low_conf. */
export function decide(z, confShift, confThresh) {
  let cls = 0;
  for (let k = 1; k < N_OUT; k++) if (z[k] > z[cls]) cls = k;     // strict ">" keeps the lowest index on ties
  let second = -Infinity;
  for (let k = 0; k < N_OUT; k++) if (k !== cls && z[k] > second) second = z[k];
  const margin = z[cls] - second;
  const confidence = Math.min(margin >> confShift, ACT_MAX);
  return { cls, margin, confidence, lowConf: confidence < confThresh };
}

/** Full inference. Returns every intermediate value the page shows. */
export function infer(x, p, confThresh) {
  const { h, acc } = hidden(x, p);
  const z = logits(h, p);
  return { ...decide(z, p.conf_shift, confThresh), hidden: h, acc1: acc, logits: z };
}

/** The 8 features from parsed header fields and window counts (model/features.py FeatureWindow.push).
 *  pkt: {is_tcp, syn, ack, rst_or_fin, ip_total_len, ttl, dst_port}; synCount/occupied: window state
 *  after the current packet entered. */
export function features(pkt, synCount, occupied) {
  const full = 127;
  const synOnly = pkt.is_tcp && pkt.syn && !pkt.ack;
  return [
    synOnly ? full : 0,
    pkt.is_tcp && pkt.ack ? full : 0,
    pkt.is_tcp && pkt.rst_or_fin ? full : 0,
    Math.floor((Math.min(pkt.ip_total_len, 1500) * full) / 1500),
    pkt.ttl >> 1,
    pkt.dst_port < 1024 ? full : 0,
    Math.min(synCount * 2, full),
    Math.min(occupied * 2, full),
  ];
}
