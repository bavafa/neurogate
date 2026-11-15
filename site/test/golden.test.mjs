// Compares site/js/golden.js with the Python golden model on every classified test packet.
// Run: node site/test/golden.test.mjs   (exit code 0 = all equal)
// Inputs written by scripts/export_site_data.py:
//   site/test/all_packets.json   8,347 rows: label, f0..f7, z0..z2, class, confidence, low_conf (Python)
//   site/data/samples.json       the ~20 packets shown on the page, with Python hidden values and features
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";
import { infer, features } from "../js/golden.js";

const here = (p) => new URL(p, import.meta.url);
const load = (p) => JSON.parse(readFileSync(here(p), "utf8"));
const params = load("../data/weights.json");
const all = load("./all_packets.json");
const samples = load("../data/samples.json");

let bad = 0;
for (const [n, r] of all.rows.entries()) {
  const x = r.slice(1, 9), z = r.slice(9, 12);
  const got = infer(x, params, all.conf_thresh);
  const ok = got.logits.every((v, k) => v === z[k]) && got.cls === r[12] &&
    got.confidence === r[13] && Number(got.lowConf) === r[14];
  if (!ok && ++bad <= 5) console.error("MISMATCH row", n, { x, python: r.slice(9), js: got });
}
assert.equal(all.rows.length, 8347, "expected 8,347 classified packets");
assert.equal(bad, 0, `${bad} rows differ from the Python model`);
console.log(`PASS: ${all.rows.length} packets, logits, class, confidence and low_conf equal to Python (0 mismatches)`);

let n = 0;
for (const s of samples.samples.filter((s) => s.kind === "classified")) {
  const f = features(s.packet, s.window.syn_count, s.window.occupied_buckets);
  assert.deepEqual(f, s.features, `features of stream packet ${s.stream_index}`);
  const got = infer(f, params, samples.conf_thresh);
  assert.deepEqual(got.hidden, s.model.hidden, `hidden of packet ${s.stream_index}`);
  assert.deepEqual(got.logits, s.model.logits);
  assert.equal(got.cls, s.model.class);
  n++;
}
console.log(`PASS: ${n} sample packets, header fields -> features -> hidden values equal to Python`);

// Input validation matches golden.hidden: features must be ints in 0..127.
assert.throws(() => infer([0, 0, 0, 0, 0, 0, 0, 128], params, 13), RangeError);
console.log("PASS: out-of-range feature rejected");
