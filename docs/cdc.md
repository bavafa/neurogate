# Clock-crossing review (self-review, not a commercial sign-off)

NeuroGate runs on two clocks that have no fixed relation to each other: the network clock
(`net_clk`, 156.25 MHz in the spec) and the core clock (`core_clk`, 200 MHz). A signal that is
made on one clock and read on the other can be caught mid-change. The reading flop may then
settle slowly (metastability), or the bits of a bus may be read from two different values.
This page lists every place a signal crosses between the two clocks, how each crossing is made
safe, and how that was checked.

**Honest label.** This is a review written by the builder and checked by a home-made script
and by simulation. It is not a sign-off from a commercial clock-crossing tool, and nothing here
was measured on hardware. Simulation cannot show metastability at all (see "What simulation
shows" below).

Reviewed design: the top-level module `neurogate_top` (the whole chip) as of M4 session 2.

## Which blocks run on which clock

- Network clock: input queue, Ethernet parser, IPv4/TCP/UDP parser, feature builder, and the
  write side of the asynchronous FIFO (a queue whose two ends run on different clocks).
- Core clock: read side of the asynchronous FIFO, neural-network core, decision unit, counters,
  and the register block that software reads over AXI-Lite (a simple register bus).

## Every crossing

| # | What crosses | Direction | Method | Checked by | Result |
|---|---|---|---|---|---|
| 1 | FIFO write pointer, 5 bits | network to core | Gray code (only one bit changes per step), sent from a flop, two synchronizer flops on the core clock | structural check rules S1, S2, G1; FIFO tests at 6 clock ratios | pass |
| 2 | FIFO read pointer, 5 bits | core to network | same as 1, mirrored | same as 1 | pass |
| 3 | FIFO contents (one 114-bit packet entry per slot; 16 more bits per slot are constants) | network to core | not synchronized; protected by the FIFO protocol: a slot is written on the network clock before the write pointer moves, and the core side reads it only after the moved pointer has passed its two synchronizer flops, so the slot is stable for at least two core clock edges before it is read | structural check rule M1 (read address made on the core clock; passing Gray pointers both ways in the same FIFO); FIFO tests; whole-chip smoke test | pass (protocol argument, not proven by the script) |
| 4 | External reset into the network clock | outside to network | reset synchronizer: two flops, reset asserts at once, releases on the second network clock edge after the external reset is released | rule R1 | pass |
| 5 | External reset into the core clock | outside to core | same as 4, on the core clock | rule R1 | pass |
| 6 | Reset of the two FIFO sides | each side uses its own clock's reset | asynchronous reset inside the FIFO, each side from its own domain's synchronizer (both sides clear at the same moment the external reset falls; release order does not matter) | rule R2 | pass |

### What does not cross, and why

- The confidence threshold and the soft-reset pulse are made by the register block on the core
  clock and used only by core-clock blocks (decision unit and counters).
- All counters, including the count of frames lost when the FIFO is full, live on the core
  clock. The network side counts lost frames itself and sends the count *inside* the next FIFO
  entry, so that count crosses through crossing 3, not on its own wires.
- The feature builder needs nothing from the core clock: it decides "FIFO full" from the
  FIFO's own write-side flag, which is made on the network clock.
- The result stream and the register bus are both on the core clock.

So no multi-bit control value crosses outside the FIFO, and no request/acknowledge handshake
is needed. If one is ever added (for example a network-side setting written by software), it
must use a handshake, and the check below would flag a raw crossing.

### The three small gates in the top level

Besides the reset synchronizers, the top level has three small gates after the decision unit
(the owner accepted this at the M4 gate). They split one decision-unit output between the
result stream and the counters. Review, from the RTL of the top level:

| Gate | Inputs (all on the core clock) | Output goes to (all core clock) |
|---|---|---|
| Result valid = valid, and the entry is a result, and the counters are ready | decision-unit valid and kind, counter-block ready | result stream `tvalid` pin |
| Decision-unit ready = counters ready, and (not a result, or the result stream is ready) | counter-block ready, decision-unit kind, result stream `tready` pin | decision unit |
| Counter accepts an entry = valid and ready of the decision unit in the same clock | decision-unit valid and the gate above | counter block |

Result: every input comes from a block on the core clock, and every consumer is on the core
clock, so these gates add no crossing. The one input that comes from outside, the result
stream `tready` pin, belongs to the core clock by the interface definition; the design cannot
check that the outside logic honours this. The structural check passes with the gates in
place (rule O1 also confirms the result pins are driven from the core clock).

## The structural check

`make cdc-check` builds a gate-free netlist of the whole chip with Yosys (an open-source
synthesis tool; one netlist cell per operator in the code) and runs a Python script over it
(`scripts/cdc_check.py`). The script finds every flop on one clock whose input comes from the
other clock and applies these rules. Any violation makes it fail.

| Rule | What it requires |
|---|---|
| C1 | Every flop and memory is clocked straight from one of the two clock inputs; no latches. |
| S1 | A flop that samples the other clock takes the signal straight from a flop output, with no gates between, into its data input only, and has no enable or synchronous reset. |
| S2 | That first synchronizer flop feeds only one thing: the data input of a second flop on the same clock. Otherwise it is a one-flop synchronizer. |
| G1 | When several bits cross together from one register, that register loads a Gray code (bit i is N[i] xor N[i+1]) from one xor encoder. A binary counter sent across fails. |
| M1 | Data read from a memory written on the other clock is allowed only if the read address is made on the reading clock and the same block has passing Gray pointers in both directions. |
| O1 | Each output pin is driven only by logic of its own clock. |
| R1 | The external reset feeds only reset synchronizers: a chain whose first flop loads 1 and whose output is at least two flops deep. |
| R2 | Every asynchronous reset pin is driven by the synchronizer output of its own clock; the external reset is never used as data. |

Result on the current design (`make cdc-check`): 2 reset synchronizers, 2 Gray pointer
crossings, 1 protected storage read, **0 violations**.

### Proof that the check catches real faults

At M2 two faults were planted in the FIFO by hand: binary pointers sent across, and each
synchronizer cut to one flop. **Both passed every FIFO simulation test.** This session planted
them again, once by hand in the real source file (then restored), and the check failed on both:

| Planted fault | Check result | FIFO simulation (8 tests) |
|---|---|---|
| Binary pointers cross (full and empty compares changed to binary so the FIFO still works) | fail: 2 G1 violations, plus M1 | 8 of 8 pass (fault not seen) |
| One-flop synchronizers (full and empty use the first synchronizer flop) | fail: 10 S2 violations, plus M1 | 8 of 8 pass (fault not seen) |

The regression test `tb/test_cdc_check.py` (run by `make test`) keeps this proof alive. It
copies the source files to a scratch folder, plants each fault below, and requires the check to
fail with the named rule. It also requires the unmodified design to pass.

| Planted fault | Rule that must fire |
|---|---|
| Binary FIFO pointers | G1 |
| One-flop synchronizers | S2 |
| Gray code made by gates on the crossing path instead of in a flop | S1 |
| A core-clock signal used directly by a network-clock block | S1 |
| A network-clock output pin driven from core-clock logic | O1 |
| FIFO read side reset by the network-clock reset | R2 |
| Reset synchronizer output taken after one flop | R1 |

## What simulation shows, and what it does not

Simulated: the FIFO tests run 6 clock ratios with edges that never line up, and the whole-chip
smoke test starts the core clock at a random phase and releases reset at a random time. These
show that pointer arithmetic, full and empty flags, ordering and reset are right. In RTL
simulation a flop always samples a clean 0 or 1 and every bit of a bus arrives at once, so
simulation cannot show metastability or bit skew. That is why the faults above pass simulation
and why the structural check exists.

## Not checked (limits)

- **Timing of the crossing paths** (constrained since M4 session 5, estimate only). Every
  path from one clock to the other is limited to 5.0 ns (one core clock period) in the timing
  constraints (`synth/neurogate_top.sdc`): this keeps the Gray pointer bits arriving close
  together and bounds the FIFO data path. Hold is not checked across the clocks (no fixed
  edge relation; the synchronizers cover it). Result on the gate netlist: worst crossing
  path 0.331 ns against a 4.958 ns limit (5.0 ns minus the flop setup time), slack 4.627 ns (*synthesized (estimate)*, ideal
  clocks, no wires, no place and route). Without place and route the real wire delay, and
  so the real skew between pointer bits, is not known.
- **Gray property.** Rule G1 checks that the register holds a Gray encoding of a value N. It
  does not prove that N changes by at most one per clock (true for the FIFO pointers by reading
  the code, and checked by the FIFO tests).
- **Reconvergence.** Two single bits synchronized separately and then combined are not detected.
  The chip has no such crossing.
- **Storage read.** Rule M1 relies on the FIFO protocol; the script does not prove it.
- **Mean time between failures** of the synchronizers is not computed (no silicon data).
- **Ports.** Input and output pins are assigned to a clock by name; the check trusts the outside
  world to drive them from that clock. The external reset is assumed free of glitches.
- No formal (mathematical) proof was done; that is a stretch goal after M5.

## How to rerun

```
source .venv/bin/activate; . scripts/env.sh
make cdc-check                          # the check on the current design
SEED=12345 python3 tb/test_cdc_check.py # the planted-fault regression (also in make test)
```
