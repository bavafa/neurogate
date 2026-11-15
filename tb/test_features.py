"""Unit tests for model/features.py (pytest). Run: `python tb/test_features.py` or `make test`.

Covers: every feature formula and its edge values, the parser rules of SPEC
section 4 (VLAN, IP options, UDP, fragments, short and bad frames), the sliding
window (fill, saturation, two packets sharing a bucket), and a random
comparison of the incremental window against a slow recompute-from-scratch
reference. Hand-derived expected values are used wherever possible so the
tests do not just repeat the model's own code.
"""
import os
import random
import struct
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from model import features as F  # noqa: E402

SEED = int(os.environ.get("SEED", "12345"))
print(f"test_features: seed={SEED}")


def eth_ip(proto: int, l4: bytes, ihl: int = 5, ttl: int = 64, total_len: int = 0,
           frag: int = 0, vlan: bool = False, ethertype: int = 0x0800, version: int = 4) -> bytes:
    """Hand-build a frame with struct (no Scapy), so parser tests are independent of Scapy."""
    opts = bytes(4 * (ihl - 5)) if ihl >= 5 else b""
    ip = struct.pack("!BBHHHBBH4s4s", (version << 4) | ihl, 0, total_len or (ihl * 4 + len(l4)),
                     1, frag, ttl, proto, 0, bytes([10, 0, 0, 1]), bytes([10, 0, 0, 2])) + opts
    eth = bytes(12) + (struct.pack("!HH", 0x8100, 5) if vlan else b"") + struct.pack("!H", ethertype)
    return eth + ip + l4


def tcp(dport: int, flags: int, sport: int = 1234) -> bytes:
    """20-byte TCP header with the given destination port and flag byte."""
    return struct.pack("!HHIIBBHHH", sport, dport, 0, 0, 5 << 4, flags, 0, 0, 0)


def udp(dport: int) -> bytes:
    """8-byte UDP header."""
    return struct.pack("!HHHH", 1234, dport, 8, 0)


def pkt(syn=False, ack=False, rstfin=False, tcp_=True, length=40, ttl=64, port=80) -> F.Packet:
    """Shortcut for a Packet."""
    return F.Packet(tcp_, syn, ack, rstfin, length, ttl, port)


# ---- feature formulas -------------------------------------------------------
@pytest.mark.parametrize("length,expect", [(0, 0), (1, 0), (12, 1), (750, 63), (1499, 126),
                                           (1500, 127), (1501, 127), (65535, 127)])
def test_f3_length_scaling(length, expect):
    """F3 = min(len,1500)*127//1500, floor. 750*127/1500 = 63.5 -> 63."""
    assert F.FeatureWindow().push(pkt(length=length))[3] == expect


@pytest.mark.parametrize("ttl,expect", [(0, 0), (1, 0), (2, 1), (64, 32), (255, 127)])
def test_f4_ttl(ttl, expect):
    """F4 = ttl >> 1."""
    assert F.FeatureWindow().push(pkt(ttl=ttl))[4] == expect


@pytest.mark.parametrize("port,expect", [(0, 127), (1023, 127), (1024, 0), (65535, 0)])
def test_f5_well_known(port, expect):
    """F5 is 127 for ports below 1024."""
    assert F.FeatureWindow().push(pkt(port=port))[5] == expect


def test_flag_features():
    """F0 needs SYN and not ACK; F1 ACK; F2 RST or FIN. UDP gives zeros even with flags set."""
    cases = [(pkt(syn=True), (127, 0, 0)), (pkt(syn=True, ack=True), (0, 127, 0)),
             (pkt(ack=True), (0, 127, 0)), (pkt(rstfin=True), (0, 0, 127)),
             (pkt(rstfin=True, ack=True), (0, 127, 127)), (pkt(), (0, 0, 0)),
             (pkt(syn=True, ack=True, rstfin=True, tcp_=False), (0, 0, 0))]
    for p, expect in cases:
        assert F.FeatureWindow().push(p)[:3] == expect


def test_port_bucket_values():
    """bucket = (p ^ (p>>6)) & 63. 1 and 64 collide in bucket 1; 100 -> 37."""
    assert [F.port_bucket(p) for p in (0, 1, 64, 100, 65535)] == [0, 1, 1, 37, 0]
    assert F.port_bucket(1) == F.port_bucket(64) == 1 and F.port_bucket(100) == 37


# ---- sliding window ---------------------------------------------------------
def test_window_fills_and_saturates():
    """64 SYNs to 64 distinct buckets: F6 and F7 reach 2*64=128, clamped to 127."""
    w = F.FeatureWindow()
    ports = [next(p for p in range(65536) if F.port_bucket(p) == b) for b in range(64)]
    for n, port in enumerate(ports, start=1):
        f = w.push(pkt(syn=True, port=port))
        assert f[6] == min(2 * n, 127) and f[7] == min(2 * n, 127)
    assert f[6] == f[7] == 127


def test_window_oldest_leaves():
    """65th packet pushes the first out: SYN count stays 64*2 clamped; then drops as SYNs age out."""
    w = F.FeatureWindow()
    w.push(pkt(syn=True))                       # the only SYN
    for _ in range(62):
        w.push(pkt(ack=True))
    assert w.push(pkt(ack=True))[6] == 2        # 64th packet: SYN still in window
    assert w.push(pkt(ack=True))[6] == 0        # 65th packet: SYN has left


def test_shared_bucket_one_leaves_one_stays():
    """SPEC M3 case, derived by hand. Ports 1 and 64 share bucket 1; filler port 100 is bucket 37.

    A(1), B(64), then 62 fillers -> window full, occupied {1,37} -> F7 = 4.
    65th packet: A leaves, B stays -> bucket 1 still occupied -> F7 = 4.
    66th packet: B leaves -> bucket 1 empty -> F7 = 2.
    """
    w = F.FeatureWindow()
    w.push(pkt(port=1))
    w.push(pkt(port=64))
    for _ in range(61):
        w.push(pkt(port=100))
    assert w.push(pkt(port=100))[7] == 4        # 64th packet
    assert w.push(pkt(port=100))[7] == 4        # A left, B stays
    assert w.push(pkt(port=100))[7] == 2        # B left


def test_reset_empties_window():
    """After reset the window forgets everything."""
    w = F.FeatureWindow()
    for _ in range(10):
        w.push(pkt(syn=True, port=22))
    w.reset()
    assert w.push(pkt(ack=True))[6:] == (0, 2)


def naive(history, cur):
    """Reference: recompute F6/F7 from scratch from the last 64 packets (no incremental state)."""
    win = (history + [cur])[-64:]
    syn = sum(F.is_syn_only(p) for p in win)
    occ = len({F.port_bucket(p.dst_port) for p in win})
    return min(2 * syn, 127), min(2 * occ, 127)


def test_random_stream_matches_naive_reference():
    """5000 random packets over few ports (many collisions): incremental == recompute."""
    rng = random.Random(SEED)
    ports = [1, 64, 65, 0, 100, 37, 1023, 8080, 443, 22] + [rng.randrange(65536) for _ in range(40)]
    w, hist = F.FeatureWindow(), []
    for _ in range(5000):
        p = pkt(syn=rng.random() < 0.4, ack=rng.random() < 0.3, tcp_=rng.random() < 0.8,
                length=rng.randrange(65536), ttl=rng.randrange(256), port=rng.choice(ports))
        f = w.push(p)
        assert f[6:] == naive(hist, p)
        assert all(0 <= x <= 127 for x in f)
        hist.append(p)


# ---- parser -----------------------------------------------------------------
def test_parse_tcp_syn():
    """Plain TCP SYN is parsed with the right fields."""
    r = F.parse_frame(eth_ip(6, tcp(443, 0x02), ttl=77, total_len=60))
    assert r.packet == F.Packet(True, True, False, False, 60, 77, 443)


def test_parse_flags_rst_fin():
    """RST and FIN both set rst_or_fin."""
    assert F.parse_frame(eth_ip(6, tcp(1, 0x04))).packet.rst_or_fin
    assert F.parse_frame(eth_ip(6, tcp(1, 0x11))).packet.rst_or_fin


def test_parse_udp():
    """UDP: no flags, dst port read."""
    r = F.parse_frame(eth_ip(17, udp(53)))
    assert r.packet == F.Packet(False, False, False, False, 28, 64, 53)


def test_parse_vlan():
    """802.1Q tag shifts L3 by 4 bytes."""
    assert F.parse_frame(eth_ip(6, tcp(22, 0x02), vlan=True)).packet.dst_port == 22


def test_parse_ip_options():
    """IHL 7 means 8 option bytes to skip before the TCP header."""
    assert F.parse_frame(eth_ip(6, tcp(8080, 0x02), ihl=7)).packet.dst_port == 8080


DROP_CASES = [
    ("tuser", eth_ip(6, tcp(80, 2)), True, "tuser_error"),
    ("empty", b"", False, "short_ethernet"),
    ("13 bytes", bytes(13), False, "short_ethernet"),
    ("vlan cut", bytes(12) + b"\x81\x00\x00", False, "short_vlan"),
    ("arp", eth_ip(6, tcp(80, 2), ethertype=0x0806), False, "not_ipv4"),
    ("ipv6", eth_ip(6, tcp(80, 2), ethertype=0x86DD), False, "not_ipv4"),
    ("qinq", eth_ip(6, tcp(80, 2), ethertype=0x88A8), False, "not_ipv4"),
    ("ip cut", eth_ip(6, b"")[:14 + 19], False, "short_ip"),
    ("ihl 4", eth_ip(6, tcp(80, 2), ihl=4), False, "bad_ihl"),
    ("version 6", eth_ip(6, tcp(80, 2), version=6), False, "bad_ip_version"),
    ("options cut", eth_ip(6, b"", ihl=15)[:14 + 40], False, "short_ip_options"),
    ("fragment", eth_ip(17, udp(53), frag=100), False, "fragment"),
    ("icmp", eth_ip(1, bytes(8)), False, "unsupported_l4"),
    ("tcp cut at 13", eth_ip(6, tcp(80, 2)[:13]), False, "short_l4"),
    ("udp cut at 3", eth_ip(17, udp(53)[:3]), False, "short_l4"),
]


@pytest.mark.parametrize("name,frame,tuser,reason", DROP_CASES, ids=[c[0] for c in DROP_CASES])
def test_parse_drops(name, frame, tuser, reason):
    """Every bad or unsupported input gives a drop with the expected reason."""
    r = F.parse_frame(frame, tuser)
    assert r.packet is None and r.drop_reason == reason, name


def test_parse_first_fragment_and_edge_lengths():
    """First fragment (offset 0, MF set) is parsed; TCP cut after flags byte (14 bytes) and UDP with 4 bytes still parse."""
    assert F.parse_frame(eth_ip(6, tcp(80, 2), frag=0x2000)).packet is not None
    assert F.parse_frame(eth_ip(6, tcp(80, 2)[:14])).packet is not None
    assert F.parse_frame(eth_ip(17, udp(53)[:4])).packet.dst_port == 53


# ---- stream -----------------------------------------------------------------
def test_run_stream_records():
    """Drops get zero features and skip the window; ids count every frame; pkt_len saturates at 65535."""
    good = eth_ip(6, tcp(22, 0x02))
    recs = F.run_stream([(good, False), (b"\x00" * 5, False), (good, True), (good, False), (bytes(70000), True)])
    assert [r.pkt_id for r in recs] == [0, 1, 2, 3, 4]
    assert [r.kind for r in recs] == [0, 1, 1, 0, 1]
    assert recs[1].features == (0,) * 8 and recs[2].drop_reason == "tuser_error"
    assert recs[3].features[6] == 4             # second SYN in window; drops did not enter it
    assert recs[4].pkt_len == 65535 and recs[0].pkt_len == len(good)


def test_pack_features():
    """F0 lands in bits [7:0], F7 in [63:56]."""
    assert F.pack_features([1, 2, 3, 4, 5, 6, 7, 8]) == 0x0807060504030201


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-p", "no:cacheprovider"]))
