# neurogate_top: the whole chip

Source file: `rtl/neurogate_top.sv`.

**What it does.** Connects every block above into one chip with two clocks.

**Where it sits.** A packet comes in on the network clock, is parsed, turned into features, crosses to the core clock, is classified, and leaves as one result beat. Counters are read over the register bus.

**How it works.** It adds three small gates that split the decision unit's output between the result stream (only classified packets, can pause) and the counters (every entry, never pause), and two reset synchronizers.

**Speed.** 187 to 191 core clocks from the last input beat to the result beat for one packet (simulated).

**How it is checked.** A smoke test, a test on generated traffic (900 frames in the short run, 9,000 in the long run), bad-packet, backpressure, overflow and reset tests, and a gate-level run on the synthesized netlist, all against the Python models (simulated).
