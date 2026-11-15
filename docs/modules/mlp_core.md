# mlp_core: the neural network

Source file: `rtl/mlp_core.sv`.

**What it does.** A small neural network (8 inputs, 16 hidden units, 3 outputs) with whole-number (int8) weights, run by one multiply-accumulate unit that does one multiply and add per clock.

**Where it sits.** First block on the core clock. It takes one entry at a time from the clock-crossing queue and produces three raw scores (one per traffic class) plus the index of the largest.

**How it works.** It needs 176 multiply-accumulate steps per packet: 128 for the first layer and 48 for the second. The weights sit in read-only tables loaded from files made by the training script. Dropped and lost-frame entries skip the network.

**Speed.** 179 core clocks per packet when packets queue up, 187 from last input beat to result for one packet (simulated). At the 200 MHz target clock that is about 1.1 million packets per second (simulated estimate).

**How it is checked.** The Python golden model is the reference. The chip description (RTL, the register-transfer source code) matches it with zero differences on a 2,028-row vector file and on all 8,347 classified packets of the unseen test split (simulated, synthetic data).
