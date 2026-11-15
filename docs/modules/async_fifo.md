# async_fifo: the clock-crossing queue

Source file: `rtl/async_fifo.sv`.

**What it does.** A 16-slot queue whose two ends run on different clocks: the network clock writes, the core clock reads. Each slot holds one 114-bit entry.

**Where it sits.** It is the only place packet data moves from one clock to the other.

**How it works.** Each side keeps its own position counter. The counter is sent to the other side in Gray code (only one bit changes per step) through two flip-flops in a row, so a counter read at the wrong instant is either the old or the new value, never garbage. The docs page on clock crossings explains this in full.

**Speed.** A few clocks of delay for a pointer to cross. It is not a speed limit for this chip.

**How it is checked.** The test runs six clock ratios (including the chip's own), fills the queue to full and drains it, uses random pauses on both sides, and resets in the middle of a stream. A structural check of the whole chip also confirms nothing else crosses. Simulation cannot show metastability (the undecided state of a flip-flop caught mid-change) at all (simulated).
