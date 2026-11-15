"""Tests for model/traffic_gen.py (pytest). Run: `python tb/test_traffic_gen.py` or `make test`.

Checks: same seed gives byte-identical files, different seed differs, pcap and
CSV agree with each other, all three classes appear, attack frames really are
SYNs, the Python parser agrees with Scapy's own dissection on 100% of generated
frames, and the expected feature separation (flood: low F7, scan: high F7).
Synthetic data only.
"""
import csv
import os
import sys
import tempfile

import pytest
from scapy.all import IP, TCP, UDP, Dot1Q, Ether, rdpcap  # type: ignore

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from model import features as F  # noqa: E402
from model import traffic_gen as G  # noqa: E402

SEED = int(os.environ.get("SEED", "12345"))
print(f"test_traffic_gen: seed={SEED}")


@pytest.fixture(scope="module")
def data():
    """Generate once into a temp dir: returns (rows, pcap frames, csv rows)."""
    rows = G.generate(SEED, 4, 200)
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "t")
        G.write_files(rows, out)
        frames = [bytes(p) for p in rdpcap(out + ".pcap")]
        with open(out + ".csv") as fh:
            csv_rows = list(csv.DictReader(fh))
    return rows, frames, csv_rows


def test_reproducible_and_seed_sensitive():
    """Same seed -> identical pcap and CSV bytes; another seed -> different."""
    with tempfile.TemporaryDirectory() as d:
        for name, seed in (("a", SEED), ("b", SEED), ("c", SEED + 1)):
            G.write_files(G.generate(seed, 2, 100), os.path.join(d, name))
        read = lambda n, e: open(os.path.join(d, n + e), "rb").read()  # noqa: E731
        for ext in (".pcap", ".csv"):
            assert read("a", ext) == read("b", ext)
            assert read("a", ext) != read("c", ext)


def test_pcap_matches_csv_and_frames(data):
    """Row count, frame bytes and lengths agree across generator, pcap and CSV."""
    rows, frames, csv_rows = data
    assert len(rows) == len(frames) == len(csv_rows) == 3 * 4 * 200
    assert frames == [f.data for _, _, f in rows]
    assert [int(r["frame_len"]) for r in csv_rows] == [len(x) for x in frames]
    assert [int(r["label"]) for r in csv_rows] == [f.label for _, _, f in rows]


def test_classes_scenarios_and_bad_input(data):
    """All classes present; 12 scenarios; some tuser, short and non-IPv4 frames exist."""
    rows, _, _ = data
    labels = {f.label for _, _, f in rows}
    assert labels == {G.NORMAL, G.PORT_SCAN, G.SYN_FLOOD}
    assert len({sid for sid, _, _ in rows}) == 12
    assert any(f.tuser for _, _, f in rows)
    reasons = {F.parse_frame(f.data, f.tuser).drop_reason for _, _, f in rows}
    assert {"tuser_error", "not_ipv4", "fragment", "short_l4"} <= reasons


def test_attack_frames_are_valid_syns(data):
    """Every attack frame parses to a TCP SYN without ACK."""
    for _, _, f in data[0]:
        if f.label != G.NORMAL:
            p = F.parse_frame(f.data, f.tuser).packet
            assert p is not None and F.is_syn_only(p)


def scapy_view(frame: bytes, tuser: bool):
    """What Scapy's own dissection says: None if the frame should be dropped, else
    (ip, l4, l4_bytes_present). Scapy does not know about tuser, so that is applied here."""
    s = Ether(frame)
    ip = s[Dot1Q].payload if s.haslayer(Dot1Q) else s.payload
    if tuser or not isinstance(ip, IP) or ip.frag != 0 or ip.proto not in (6, 17):
        return None
    l4_bytes = len(bytes(ip)) - ip.ihl * 4
    # Scapy needs a full 20-byte TCP / 8-byte UDP header; if it gave up (Raw),
    # the frame is cut inside the header and l4 is None.
    l4 = ip.payload if isinstance(ip.payload, (TCP, UDP)) else None
    return ip, l4, l4_bytes


def test_parser_agrees_with_scapy_on_all_frames(data):
    """Python parser vs Scapy's dissection on every generated frame.

    Same keep/drop decision and the same fields. Allowed differences, both
    for frames cut inside the L4 header: Scapy cannot name the layer but the
    chip has enough bytes (fields not compared), or the chip drops it as too
    short (must be a real truncation).
    """
    kept = dropped = truncated = 0
    for _, _, f in data[0]:
        r = F.parse_frame(f.data, f.tuser)
        v = scapy_view(f.data, f.tuser)
        if v is None:
            assert r.packet is None
            dropped += 1
            continue
        ip, l4, l4_bytes = v
        need = 14 if ip.proto == 6 else 4     # bytes of L4 header the chip needs (SPEC section 4)
        if r.packet is None:
            assert r.drop_reason == "short_l4" and l4_bytes < need
            truncated += 1
            continue
        if l4 is None:                        # cut inside the header but long enough for the chip
            assert l4_bytes >= need
            kept += 1
            continue
        p = r.packet
        assert (p.dst_port, p.ttl, p.ip_total_len) == (l4.dport, ip.ttl, ip.len)
        assert p.is_tcp == isinstance(l4, TCP)
        if isinstance(l4, TCP):
            fl = int(l4.flags)
            assert (p.syn, p.ack, p.rst_or_fin) == (bool(fl & 2), bool(fl & 16), bool(fl & 5))
        kept += 1
    print(f"parser vs Scapy: kept={kept} dropped={dropped} truncated={truncated}")
    assert kept > 2000 and dropped > 0 and truncated > 0


def test_feature_separation(data):
    """After the window fills: mean F7 (distinct ports) is higher for scans than for floods in every scenario pair."""
    rows, _, _ = data
    sid_label, frames = {}, {}
    for sid, _, f in rows:
        frames.setdefault(sid, []).append((f.data, f.tuser))
        if f.label != G.NORMAL:
            sid_label[sid] = f.label
    mean_f7 = {G.PORT_SCAN: [], G.SYN_FLOOD: []}
    for sid, kind in sid_label.items():
        f7 = [r.features[7] for r in F.run_stream(frames[sid])[64:] if r.kind == F.KIND_CLASSIFY]
        mean_f7[kind].append(sum(f7) / len(f7))
    print("mean F7 per scenario: scan", [round(x) for x in mean_f7[G.PORT_SCAN]],
          "flood", [round(x) for x in mean_f7[G.SYN_FLOOD]])
    assert min(mean_f7[G.PORT_SCAN]) > max(mean_f7[G.SYN_FLOOD])


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-s", "-p", "no:cacheprovider"]))
