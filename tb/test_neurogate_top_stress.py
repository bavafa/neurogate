"""Top-level bad-packet, backpressure, overflow and reset tests, started by `make test`.

Writes four frame files, then runs tb/neurogate_top_stress_checks.py (cocotb, Icarus,
all RTL, two clocks):
  bad.txt     malformed frames made from generated frames: every truncation length
              from 1 to 70 bytes of a TCP, a UDP and a VLAN-tagged frame; wrong
              EtherTypes; broken IP version / header length; tuser errors; random
              garbage; a 1514-byte frame; all mixed with good frames
  good.txt    120 generated frames (seed 2; mix of kept and dropped)
  kept.txt    generated frames the model keeps (valid IPv4 TCP/UDP), for the overflow
              and soft-reset tests, where dropped frames would hide in the counters
  mixed.txt   300 generated frames from three scenarios (seed 3)
Everything is fixed by seeds (traffic seed here; SEED for timing and stalls in the
test). All results are *simulated*, on synthetic data.
"""
import os
import random
import sys
from typing import List, Tuple

TB_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TB_DIR)
sys.path.insert(0, ROOT)
sys.path.insert(0, TB_DIR)

SEED = int(os.environ.get("SEED", "12345"))
Frame = Tuple[bytes, bool]


def ip_offset(frame: bytes) -> int:
    """Byte offset of the IPv4 header (14, or 18 behind one VLAN tag)."""
    return 18 if frame[12:14] == b"\x81\x00" else 14


def bad_set(rng: random.Random, good: List[Frame]) -> List[Frame]:
    """Build the malformed frames (and keep some good ones between them)."""
    tcp = next(f for f, t in good if f[12:14] == b"\x08\x00" and f[23] == 6 and len(f) > 70 and not t)
    udp = next(f for f, t in good if f[12:14] == b"\x08\x00" and f[23] == 17 and len(f) > 70 and not t)
    vlan = next((f for f, t in good if f[12:14] == b"\x81\x00" and len(f) > 74), tcp[:12] + b"\x81\x00\x00\x05" + tcp[12:])
    out: List[Frame] = []
    for base in (tcp, udp, vlan):                          # every truncation length 1..70
        out += [(base[:n], False) for n in range(1, 71)]
    out += [(tcp[:13], False)] * 12 + [(vlan[:17], False)] * 12   # last EtherType byte missing
    for et in (b"\x08\x06", b"\x86\xdd", b"\x00\x00", b"\x88\xa8", b"\x08\x01", b"\xff\xff"):
        out.append((tcp[:12] + et + tcp[14:], False))      # wrong EtherType, rest valid
    out.append((tcp[:12] + b"\x81\x00\x00\x05\x86\xdd" + tcp[14:], False))   # VLAN then IPv6
    out.append((tcp[:12] + b"\x81\x00\x00\x05" + b"\x81\x00\x00\x06" + tcp[12:], False))  # two tags
    for ihl in (0, 1, 2, 3, 4, 6, 15):                     # bad / long IP header length
        f = bytearray(tcp)
        f[14] = (4 << 4) | ihl
        out.append((bytes(f), False))
    for ver in (0, 5, 6, 15):                              # wrong IP version
        f = bytearray(tcp)
        f[14] = (ver << 4) | 5
        out.append((bytes(f), False))
    for base in (tcp, udp, vlan):                          # tuser error on good and short frames
        out += [(base, True), (base[:30], True), (base[:13], True)]
    out.append((tcp + bytes(rng.randrange(256) for _ in range(1514 - len(tcp))), False))
    out.append((tcp + bytes(rng.randrange(256) for _ in range(1514 - len(tcp))), True))
    for _ in range(60):                                    # random garbage, lengths 1..120
        out.append((bytes(rng.randrange(256) for _ in range(rng.randrange(1, 121))), False))
    for _ in range(60):                                    # good frame, random bytes cut off the end
        f = rng.choice(good)[0]
        out.append((f[:rng.randrange(1, len(f))], rng.random() < 0.2))
    rng.shuffle(out)
    mixed: List[Frame] = []
    for i, fr in enumerate(out):                           # a good frame every ~4 bad ones
        mixed.append(fr)
        if i % 4 == 3:
            mixed.append(rng.choice(good))
    return mixed


def write(path: str, frames: List[Frame]) -> None:
    """Write '<tuser> <hex>' lines."""
    with open(path, "w") as f:
        for data, tuser in frames:
            f.write(f"{int(tuser)} {data.hex()}\n")


def main() -> int:
    """Write the frame files, run the cocotb tests, return their exit status."""
    from cocotb_run import run_cocotb
    from model import features as F
    from model import traffic_gen as G
    rows2 = [(fr.data, fr.tuser) for _, _, fr in G.generate(2, 1, 40)]     # 120 frames
    rows3 = [(fr.data, fr.tuser) for _, _, fr in G.generate(3, 1, 100)]    # 300 frames
    kept_src = [(fr.data, fr.tuser) for _, _, fr in G.generate(4, 4, 100)]
    kept_all = [fr for fr in kept_src if F.parse_frame(fr[0], fr[1]).packet is not None]
    kept = kept_all[::3][:300]          # every third kept frame: all three traffic types mixed
    assert len(kept) == 300
    rng = random.Random("bad-set-fixed")      # the frame file content is fixed
    bad = bad_set(rng, rows2)
    out_dir = os.path.join(ROOT, "sim_build", "neurogate_top_stress")
    os.makedirs(out_dir, exist_ok=True)
    files = {"BAD_FRAMES": ("bad.txt", bad), "GOOD_FRAMES": ("good.txt", rows2),
             "KEPT_FRAMES": ("kept.txt", kept), "MIXED_FRAMES": ("mixed.txt", rows3)}
    env = {}
    for var, (name, frames) in files.items():
        write(os.path.join(out_dir, name), frames)
        env[var] = os.path.join(out_dir, name)
    print(f"test_neurogate_top_stress: seed={SEED} bad={len(bad)} good={len(rows2)} "
          f"kept={len(kept)} mixed={len(rows3)} frames", flush=True)
    srcs = sorted(os.path.join("rtl", f) for f in os.listdir(os.path.join(ROOT, "rtl"))
                  if f.endswith(".sv"))
    return run_cocotb("neurogate_top", srcs, "neurogate_top_stress_checks", SEED, env,
                      build_name="neurogate_top_stress",
                      defines={n: os.path.join(ROOT, "data", "model", f) for n, f in
                               (("ROM_W1", "rom_w1.hex"), ("ROM_B1", "rom_b1.hex"),
                                ("ROM_W2", "rom_w2.hex"), ("ROM_B2", "rom_b2.hex"))})


if __name__ == "__main__":
    sys.exit(main())
