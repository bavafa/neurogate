"""Synthetic traffic generator for NeuroGate (SPEC section 9). Simulation data only.

Writes two files from one seed:
  <out>.pcap : the frames, in order (Ethernet, no FCS), timestamps in seconds
  <out>.csv  : one row per frame: frame index, scenario id, scenario seed,
               label (0 normal, 1 port_scan, 2 syn_flood), label name,
               timestamp, frame length, tuser

A pcap file cannot carry the chip's ``tuser`` (bad-FCS) flag, so the CSV does.

Streams are made of *scenarios*. Each scenario is one ordered stream of one
type. Attack scenarios mix in some normal background packets (labelled
normal) so the classes overlap and the task is not trivially separable. The
scenario id and scenario seed are recorded so M3 can split train / validation
/ test by scenario, never by shuffling packets of one stream.

Everything is deterministic: the same ``--seed`` and counts give byte-identical
files. Random numbers come only from ``random.Random`` instances seeded here.
This is synthetic data: results on it say nothing about real-world traffic.

Usage:  python model/traffic_gen.py --seed 1 --out data/traffic
"""

import argparse
import csv
import random
from dataclasses import dataclass
from typing import List, Tuple

from scapy.all import ARP, ICMP, IP, TCP, UDP, Dot1Q, Ether, Raw, wrpcap  # type: ignore

LABEL_NAMES = ("normal", "port_scan", "syn_flood")
NORMAL, PORT_SCAN, SYN_FLOOD = 0, 1, 2
SERVER_MAC, CLIENT_MAC = "02:00:00:00:00:01", "02:00:00:00:00:02"  # fixed: no random MACs
COMMON_PORTS = (80, 443, 22, 53, 25, 8080, 123)


@dataclass
class Frame:
    """One generated frame with its label and the chip's bad-FCS flag."""

    data: bytes
    label: int
    tuser: bool
    time: float


def _ip(rng: random.Random, net: int) -> str:
    """Random host address in 10.<net>.x.y."""
    return f"10.{net}.{rng.randrange(256)}.{rng.randrange(1, 255)}"


def _normal_frame(rng: random.Random) -> Tuple[bytes, bool]:
    """One background packet. Returns (frame bytes, tuser).

    Mix: TCP data/ACK, TCP handshake start (a SYN), FIN/RST, UDP, ICMP, ARP,
    VLAN-tagged, rare IP fragments, and rare bad frames (truncated, tuser).
    """
    pick = rng.random()
    eth = Ether(src=CLIENT_MAC, dst=SERVER_MAC)
    ip = IP(src=_ip(rng, 1), dst=_ip(rng, 2), ttl=rng.choice((57, 64, 117, 128, 255)), id=rng.randrange(65536))
    sport = rng.randrange(1024, 65536)
    dport = rng.choice(COMMON_PORTS) if rng.random() < 0.9 else rng.randrange(1024, 65536)
    payload = Raw(bytes(rng.randrange(256) for _ in range(rng.choice((0, 0, 20, 100, 400, 1000, 1400)))))
    if pick < 0.40:        # TCP established: ACK / PSH-ACK
        pkt = eth / ip / TCP(sport=sport, dport=dport, flags=rng.choice(("A", "PA")), seq=rng.randrange(2**32)) / payload
    elif pick < 0.50:      # TCP connection start: a normal SYN (looks like attack traffic on purpose)
        pkt = eth / ip / TCP(sport=sport, dport=dport, flags="S", seq=rng.randrange(2**32))
    elif pick < 0.58:      # TCP end of connection
        pkt = eth / ip / TCP(sport=sport, dport=dport, flags=rng.choice(("FA", "R", "RA")))
    elif pick < 0.78:      # UDP
        pkt = eth / ip / UDP(sport=sport, dport=rng.choice((53, 123, 5060, dport))) / payload
    elif pick < 0.84:      # ICMP echo (unsupported by the chip: counted as dropped)
        pkt = eth / ip / ICMP(id=rng.randrange(65536), seq=rng.randrange(65536)) / Raw(bytes(32))
    elif pick < 0.88:      # ARP (unsupported)
        pkt = eth / ARP(psrc=ip.src, pdst=ip.dst, hwsrc=CLIENT_MAC)
    elif pick < 0.95:      # VLAN-tagged TCP/UDP
        pkt = Ether(src=CLIENT_MAC, dst=SERVER_MAC) / Dot1Q(vlan=rng.randrange(1, 4095)) / ip / \
            TCP(sport=sport, dport=dport, flags="A") / payload
    elif pick < 0.97:      # non-first IP fragment (no L4 header)
        ip.flags, ip.frag = 0, rng.randrange(1, 100)
        pkt = eth / ip / Raw(bytes(rng.randrange(256) for _ in range(64)))
    elif pick < 0.99:      # truncated frame: cut inside the TCP header
        data = bytes(eth / ip / TCP(sport=sport, dport=dport, flags="A"))
        return data[: rng.randrange(14, len(data) - 1)], False
    else:                  # bad FCS flagged by the MAC
        return bytes(eth / ip / TCP(sport=sport, dport=dport, flags="A") / payload), True
    return bytes(pkt), False


def _syn(rng: random.Random, src: str, dst: str, sport: int, dport: int, ttl: int) -> bytes:
    """One bare TCP SYN frame (what both scans and floods are made of)."""
    pkt = Ether(src=CLIENT_MAC, dst=SERVER_MAC) / IP(src=src, dst=dst, ttl=ttl, id=rng.randrange(65536)) / \
        TCP(sport=sport, dport=dport, flags="S", seq=rng.randrange(2**32))
    return bytes(pkt)


def make_scenario(kind: int, seed: int, n_packets: int) -> List[Frame]:
    """Build one ordered scenario of ``n_packets`` frames with per-packet labels.

    Attack scenarios mix in background (normal) packets with a probability
    chosen per scenario from the seed, so some scenarios are "pure" and some
    are heavily diluted. Timestamps start at 0 and step by a jittered gap.
    """
    rng = random.Random(seed)
    frames: List[Frame] = []
    t = 0.0
    p_bg = 1.0 if kind == NORMAL else rng.choice((0.0, 0.1, 0.25, 0.4, 0.5))
    gap = rng.choice((0.0005, 0.002, 0.01, 0.05))          # mean gap: attack pacing varies
    attacker, target = _ip(rng, 66), _ip(rng, 2)
    ttl = rng.choice((49, 64, 128))
    scan_random = rng.random() < 0.5
    scan_lo = rng.choice((1, 1, 20, 1000))
    next_port = scan_lo
    flood_port = rng.choice((80, 443, 8080))
    many_sources = rng.random() < 0.6
    for _ in range(n_packets):
        t += rng.expovariate(1.0 / gap)                    # jitter: exponential gaps
        if kind == NORMAL or rng.random() < p_bg:
            data, tuser = _normal_frame(rng)
            frames.append(Frame(data, NORMAL, tuser, t))
        elif kind == PORT_SCAN:                             # one source, one target, many ports
            if scan_random:
                dport = rng.randrange(1, 65536)
            else:
                dport, next_port = next_port, next_port % 65535 + 1
            frames.append(Frame(_syn(rng, attacker, target, rng.randrange(1024, 65536), dport, ttl),
                                PORT_SCAN, False, t))
        else:                                               # SYN flood: one target port, many sources
            src = _ip(rng, rng.randrange(60, 70)) if many_sources else attacker
            frames.append(Frame(_syn(rng, src, target, rng.randrange(1024, 65536), flood_port,
                                     rng.choice((ttl, rng.randrange(30, 255)))), SYN_FLOOD, False, t))
    return frames


def generate(seed: int, scenarios_per_class: int, packets_per_scenario: int) -> List[Tuple[int, int, Frame]]:
    """Generate all scenarios. Returns (scenario_id, scenario_seed, frame) rows in stream order.

    Scenario i uses seed ``seed * 100003 + i`` so every scenario has its own
    reproducible seed. Scenarios are laid end to end; each starts after the
    previous one ends, so pcap timestamps increase.
    """
    rows: List[Tuple[int, int, Frame]] = []
    offset = 0.0
    sid = 0
    for kind in (NORMAL, PORT_SCAN, SYN_FLOOD):
        for _ in range(scenarios_per_class):
            sseed = seed * 100003 + sid
            frames = make_scenario(kind, sseed, packets_per_scenario)
            for f in frames:
                f.time += offset
                rows.append((sid, sseed, f))
            offset = frames[-1].time + 1.0
            sid += 1
    return rows


def write_files(rows: List[Tuple[int, int, Frame]], out: str) -> None:
    """Write ``<out>.pcap`` and ``<out>.csv`` (see the module docstring for columns)."""
    pkts = []
    for _, _, f in rows:
        p = Ether(f.data)
        p.time = f.time
        pkts.append(p)
    wrpcap(out + ".pcap", pkts)
    with open(out + ".csv", "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["index", "scenario", "scenario_seed", "label", "label_name", "time_s", "frame_len", "tuser"])
        for i, (sid, sseed, f) in enumerate(rows):
            w.writerow([i, sid, sseed, f.label, LABEL_NAMES[f.label], f"{f.time:.6f}", len(f.data), int(f.tuser)])


def main() -> None:
    """Command-line entry point."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seed", type=int, default=1, help="master seed (default 1)")
    ap.add_argument("--scenarios-per-class", type=int, default=10)
    ap.add_argument("--packets-per-scenario", type=int, default=300)
    ap.add_argument("--out", default="data/traffic", help="output path without extension")
    a = ap.parse_args()
    rows = generate(a.seed, a.scenarios_per_class, a.packets_per_scenario)
    write_files(rows, a.out)
    counts = [sum(1 for _, _, f in rows if f.label == k) for k in range(3)]
    print(f"seed={a.seed} frames={len(rows)} normal={counts[0]} port_scan={counts[1]} "
          f"syn_flood={counts[2]} -> {a.out}.pcap, {a.out}.csv")


if __name__ == "__main__":
    main()
