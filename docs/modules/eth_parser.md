# eth_parser: the Ethernet header reader

Source file: `rtl/eth_parser.sv`.

**What it does.** Reads the first bytes of each frame (the link-level header) and decides three things: is there a VLAN tag (a 4-byte label some networks add), is the next layer IPv4, and must this frame be dropped at this level?

**Where it sits.** Second block on the network clock, after the input queue and before the IPv4 reader.

**How it works.** A frame is dropped here if the upstream device flagged a bad checksum, if it is too short to hold its header, or if it is not IPv4 (ARP, IPv6 and others are out of scope). The frame bytes continue unchanged for the next block.

**Speed.** One clock of delay, one beat per clock.

**How it is checked.** The test covers VLAN-tagged frames, short frames, wrong EtherTypes (the 2-byte field that names the next layer), bad flags, reset in the middle of a frame, and random pauses (simulated).
