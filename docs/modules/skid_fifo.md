# skid_fifo: the input queue

Source file: `rtl/skid_fifo.sv`.

**What it does.** A small queue of four slots at the chip's packet input. A packet arrives as a stream of 8-byte pieces called beats (the stream format is AXI-Stream: a sender says "valid", a receiver says "ready", and a beat moves when both are high).

**Where it sits.** It is the first block on the network clock. Packets from the network side go in here, and the Ethernet parser reads from it.

**How it works.** It gives the parsers a stream that comes straight out of storage flip-flops, and it lets the sender keep going for a moment when the next block pauses. Each beat is stored whole, together with its byte-valid bits and the bad-checksum flag, so nothing is split.

**Speed.** One clock of delay, one beat per clock when the next block is ready.

**How it is checked.** The test sends beats with random pauses on either side, a long stall and a reset in the middle of a stream, and compares what comes out with what went in (simulated).
