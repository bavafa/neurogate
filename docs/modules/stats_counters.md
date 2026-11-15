# stats_counters: packet and byte counters

Source file: `rtl/stats_counters.sv`.

**What it does.** Counts packets and bytes per class, dropped frames, low-confidence packets and lost frames, and remembers the latest result.

**Where it sits.** Behind the decision unit, on the core clock. The register block shows these values to software.

**How it works.** Once the pipeline has emptied, every frame lands in exactly one of the five frame counters. There are exceptions: the lost-frame count carried by each entry stops growing at its maximum (65,535), so frames lost beyond that are not counted; and a soft reset while frames are still in the pipeline can leave the sum off by the number of those frames (plus one if a result finished in the reset clock). Counters wrap to zero at the maximum value. A soft reset from software clears them; if a result is finished in the same clock as the reset, the reset wins.

**Speed.** One clock of delay.

**How it is checked.** The test checks every counter, wrap-around, soft reset, a reset in the middle of a stream, reserved entry types and that the counters ignore the payload when no entry is valid (simulated).
