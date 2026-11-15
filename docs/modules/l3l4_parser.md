# l3l4_parser: the IPv4 and TCP/UDP header reader

Source file: `rtl/l3l4_parser.sv`.

**What it does.** Reads the IP (Internet Protocol) header and the TCP (reliable connections) or UDP (simple, connectionless) header (the network and transport layers) and reports one record per frame: keep or drop, the SYN (connection request), ACK (acknowledgement) and RST (reset) and FIN (finish) connection flags, the IP length, the time-to-live, the destination port and the frame length.

**Where it sits.** Third block on the network clock. Its output record goes to the feature builder.

**How it works.** It skips IP options, drops fragments that carry no port numbers, drops anything that is not TCP or UDP, and drops frames that end before the fields it needs. A cut-off frame is still kept and parsed if it holds at least 14 bytes of the TCP header (enough to include the flags) or at least 4 bytes of the UDP header (the two port numbers); the rest of the header is not needed. Every drop has a numbered reason. It finds each field by counting bytes, not by shifting the whole frame around.

**Speed.** The record appears one clock after the frame's last beat.

**How it is checked.** The test feeds frames with IP options, UDP, fragments, non-IPv4 and truncated headers. On the generated traffic it is also compared with fields read independently by Scapy, a Python packet library (simulated).
