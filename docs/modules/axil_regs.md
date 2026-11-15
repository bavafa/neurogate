# axil_regs: the register bus

Source file: `rtl/axil_regs.sv`.

**What it does.** The block software uses to talk to the chip. The bus is AXI4-Lite, a simple bus for reading and writing 32-bit registers one at a time.

**Where it sits.** On the core clock, next to the counters. Software writes the confidence threshold and the soft-reset bit, and reads the counters.

**How it works.** One read or write at a time. An address that does not exist gives an error reply. The register list is in the register map page.

**Speed.** One clock for a read, one for a write.

**How it is checked.** The test checks reset values, read-only registers, the soft-reset bit, the threshold, unmapped addresses, the bus handshake, a random mix of accesses and a reset in the middle of a transaction (simulated).
