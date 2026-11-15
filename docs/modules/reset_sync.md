# reset_sync: the reset synchronizer

Source file: `rtl/reset_sync.sv`.

**What it does.** Makes a reset signal safe for one clock. The chip has one outside reset, which is not tied to either clock.

**Where it sits.** One copy sits in front of each clock domain.

**How it works.** Reset starts at once when the outside reset goes low. It ends only on a clock edge, after two clock edges, so every flip-flop in that domain leaves reset together.

**Speed.** Two clocks to release.

**How it is checked.** Checked in the whole-chip tests, which reset the chip at random moments, in the middle of a frame and with traffic in flight (simulated).
