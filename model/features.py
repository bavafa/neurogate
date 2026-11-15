"""NeuroGate feature model (SPEC section 5), integer math only.

What this file is
-----------------
A plain-Python reference for what the chip's header parsers and
``feature_builder`` must compute. The RTL is checked against it bit for bit.
Everything here is integer arithmetic (no floats), so there is no rounding
surprise: every formula is a shift, a multiply, a floor division or a min.

Three layers
------------
1. ``parse_frame``  : raw Ethernet frame bytes -> header fields, or a drop reason
                      (SPEC section 4 parsing rules).
2. ``FeatureWindow``: sliding window over the last 64 valid IPv4 TCP/UDP packets.
                      Turns each packet into 8 int8 features (SPEC section 5).
3. ``run_stream``   : a list of frames -> one ``FrameRecord`` per frame, in the
                      shape of one async-FIFO entry (SPEC section 8.1, D-004).

Nothing here depends on clocks or backpressure. Features depend only on the
order of the packets (D-004).
"""

from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

# ---- Constants (all from SPEC.md; named so a reader can find them) ----------
WINDOW_DEPTH = 64          # last 64 valid IPv4 TCP/UDP packets, current included
NUM_BUCKETS = 64           # destination-port buckets (6-bit index)
FEATURE_MAX = 127          # every feature is an int8 value in 0..127
NUM_FEATURES = 8
LEN_CAP = 1500             # F3 saturates at this IPv4 total length
WELL_KNOWN_PORT_LIMIT = 1024

# Entry kinds (SPEC section 8.1). OVF_ONLY (2) never comes from this model: it
# only exists in hardware when the async FIFO overflows.
KIND_CLASSIFY = 0
KIND_DROP = 1

# Parser constants
ETH_HDR_LEN = 14
VLAN_TAG_LEN = 4
ETYPE_IPV4 = 0x0800
ETYPE_VLAN = 0x8100
IPV4_MIN_HDR = 20
PROTO_TCP = 6
PROTO_UDP = 17
TCP_FLAGS_END = 14         # TCP flags byte is at offset 13, so 14 bytes needed
UDP_PORTS_END = 4          # UDP: src port + dst port = first 4 bytes needed
FLAG_FIN, FLAG_SYN, FLAG_RST, FLAG_ACK = 0x01, 0x02, 0x04, 0x10
PKT_LEN_MAX = 0xFFFF       # pkt_len field is 16 bits and saturates
PKT_ID_MASK = 0xFFFF       # pkt_id is 16 bits and wraps


# ---- Layer 1: parser ---------------------------------------------------------
@dataclass(frozen=True)
class Packet:
    """Header fields the feature builder needs from one valid IPv4 TCP/UDP frame."""

    is_tcp: bool
    syn: bool          # SYN flag (TCP only; False for UDP)
    ack: bool          # ACK flag (TCP only)
    rst_or_fin: bool   # RST or FIN flag (TCP only)
    ip_total_len: int  # IPv4 "total length" header field, 0..65535
    ttl: int           # 0..255
    dst_port: int      # 0..65535


@dataclass(frozen=True)
class ParseResult:
    """Either a ``Packet`` (``drop_reason`` is "") or a drop reason (``packet`` is None)."""

    packet: Optional[Packet]
    drop_reason: str


def _drop(reason: str) -> ParseResult:
    """Build a ParseResult for a frame the chip counts in PKT_DROPPED."""
    return ParseResult(None, reason)


def parse_frame(frame: bytes, tuser: bool = False) -> ParseResult:
    """Parse one Ethernet frame (no preamble, no FCS) following SPEC section 4.

    Malformed and unsupported frames both end up in the chip's DROP kind. They
    get different reason strings here only to make test failures readable.

    Args:
        frame: the frame bytes as received.
        tuser: True if the upstream MAC flagged a bad FCS (frame is dropped).

    Returns:
        ParseResult with a Packet, or a drop reason.
    """
    if tuser:
        return _drop("tuser_error")
    if len(frame) < ETH_HDR_LEN:
        return _drop("short_ethernet")

    l3 = ETH_HDR_LEN
    ethertype = (frame[12] << 8) | frame[13]
    if ethertype == ETYPE_VLAN:               # one 802.1Q tag; QinQ is out of scope
        if len(frame) < ETH_HDR_LEN + VLAN_TAG_LEN:
            return _drop("short_vlan")
        ethertype = (frame[16] << 8) | frame[17]
        l3 += VLAN_TAG_LEN
    if ethertype != ETYPE_IPV4:
        return _drop("not_ipv4")

    if len(frame) < l3 + IPV4_MIN_HDR:
        return _drop("short_ip")
    version = frame[l3] >> 4
    ihl = frame[l3] & 0xF
    if version != 4:                          # builder assumption, see DECISIONS D-011
        return _drop("bad_ip_version")
    if ihl < 5:
        return _drop("bad_ihl")
    l4 = l3 + ihl * 4                         # IP options are skipped
    if len(frame) < l4:
        return _drop("short_ip_options")

    total_len = (frame[l3 + 2] << 8) | frame[l3 + 3]
    frag_offset = ((frame[l3 + 6] << 8) | frame[l3 + 7]) & 0x1FFF
    ttl = frame[l3 + 8]
    proto = frame[l3 + 9]
    if frag_offset != 0:                      # non-first fragment: no L4 header
        return _drop("fragment")
    if proto not in (PROTO_TCP, PROTO_UDP):
        return _drop("unsupported_l4")

    need = TCP_FLAGS_END if proto == PROTO_TCP else UDP_PORTS_END
    if len(frame) < l4 + need:
        return _drop("short_l4")
    dst_port = (frame[l4 + 2] << 8) | frame[l4 + 3]
    if proto == PROTO_TCP:
        flags = frame[l4 + 13]
        return ParseResult(
            Packet(True, bool(flags & FLAG_SYN), bool(flags & FLAG_ACK),
                   bool(flags & (FLAG_RST | FLAG_FIN)), total_len, ttl, dst_port), "")
    return ParseResult(Packet(False, False, False, False, total_len, ttl, dst_port), "")


# ---- Layer 2: sliding window and features ------------------------------------
def port_bucket(dst_port: int) -> int:
    """Hash a destination port to a 6-bit bucket: (p ^ (p >> 6)) & 0x3F."""
    return (dst_port ^ (dst_port >> 6)) & (NUM_BUCKETS - 1)


def is_syn_only(pkt: Packet) -> bool:
    """True for a TCP packet with SYN set and ACK clear (the F0 test)."""
    return pkt.is_tcp and pkt.syn and not pkt.ack


class FeatureWindow:
    """Sliding window over the last ``WINDOW_DEPTH`` valid packets (SPEC section 5).

    State mirrors the hardware: a ring of (is_syn, bucket), one counter per
    bucket, and a SYN count. A bucket is "occupied" while its counter is above
    zero, so two packets sharing a bucket are handled correctly when one of
    them leaves the window (a plain bitmap could not do that).
    """

    def __init__(self) -> None:
        """Create an empty window (the state after reset)."""
        self.reset()

    def reset(self) -> None:
        """Empty the window, as the hardware does on reset."""
        self._ring: List[Tuple[bool, int]] = [(False, 0)] * WINDOW_DEPTH
        self._wr = 0                          # next slot to write (= oldest entry when full)
        self._count = 0                       # entries currently in the window
        self._bucket_cnt = [0] * NUM_BUCKETS  # per-bucket counters (max 64, fits 7 bits)
        self._occupied = 0                    # buckets whose counter is > 0
        self._syn_count = 0                   # ring entries with is_syn = 1

    def push(self, pkt: Packet) -> Tuple[int, ...]:
        """Add one valid packet and return its 8 features (F0..F7).

        Order of operations (SPEC section 5): the oldest entry leaves if the
        window is full, then the packet enters, then F6 and F7 are computed.
        """
        if self._count == WINDOW_DEPTH:       # window full: oldest entry leaves
            old_syn, old_bucket = self._ring[self._wr]
            self._bucket_cnt[old_bucket] -= 1
            if self._bucket_cnt[old_bucket] == 0:
                self._occupied -= 1
            self._syn_count -= int(old_syn)
        else:
            self._count += 1

        syn, bucket = is_syn_only(pkt), port_bucket(pkt.dst_port)
        self._ring[self._wr] = (syn, bucket)  # current packet enters
        self._wr = (self._wr + 1) % WINDOW_DEPTH
        if self._bucket_cnt[bucket] == 0:
            self._occupied += 1
        self._bucket_cnt[bucket] += 1
        self._syn_count += int(syn)

        full = FEATURE_MAX
        return (
            full if syn else 0,                                        # F0
            full if (pkt.is_tcp and pkt.ack) else 0,                   # F1
            full if (pkt.is_tcp and pkt.rst_or_fin) else 0,            # F2
            min(pkt.ip_total_len, LEN_CAP) * FEATURE_MAX // LEN_CAP,   # F3
            pkt.ttl >> 1,                                              # F4
            full if pkt.dst_port < WELL_KNOWN_PORT_LIMIT else 0,       # F5
            min(self._syn_count * 2, FEATURE_MAX),                     # F6
            min(self._occupied * 2, FEATURE_MAX),                      # F7
        )


def pack_features(features: Iterable[int]) -> int:
    """Pack 8 features into the 64-bit entry field: F0 in [7:0] ... F7 in [63:56]."""
    word = 0
    for i, f in enumerate(features):
        word |= (f & 0xFF) << (8 * i)
    return word


# ---- Layer 3: frame stream -> FIFO-entry-shaped records ----------------------
@dataclass(frozen=True)
class FrameRecord:
    """What the net_clk side hands to the async FIFO for one frame (SPEC 8.1).

    ``features`` is all zeros unless ``kind`` is KIND_CLASSIFY. ``drop_reason``
    is model-only debugging text, not part of the hardware entry.
    """

    pkt_id: int
    kind: int
    features: Tuple[int, ...]
    pkt_len: int
    drop_reason: str


def run_stream(frames: Iterable[Tuple[bytes, bool]]) -> List[FrameRecord]:
    """Run the parser and the feature window over a stream of (frame, tuser) pairs.

    Every frame gets a ``pkt_id`` (counts from 0, wraps at 16 bits). Dropped
    frames never enter the window. Overflow drops are not modelled here: they
    depend on FIFO timing, and the feature values do not (D-004).
    """
    window = FeatureWindow()
    records: List[FrameRecord] = []
    for n, (frame, tuser) in enumerate(frames):
        pkt_id = n & PKT_ID_MASK
        pkt_len = min(len(frame), PKT_LEN_MAX)
        result = parse_frame(frame, tuser)
        if result.packet is None:
            records.append(FrameRecord(pkt_id, KIND_DROP, (0,) * NUM_FEATURES,
                                       pkt_len, result.drop_reason))
        else:
            records.append(FrameRecord(pkt_id, KIND_CLASSIFY, window.push(result.packet),
                                       pkt_len, ""))
    return records
