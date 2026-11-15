# decision_unit: confidence and the low-confidence flag

Source file: `rtl/decision_unit.sv`.

**What it does.** Turns the three raw scores into a class, a confidence value (0 to 127), and a low-confidence flag.

**Where it sits.** Directly behind the neural network.

**How it works.** Confidence is the gap between the top score and the second score, divided by 256 and capped at 127. It is a margin, not a probability. The flag is set when the confidence is below a threshold that software can change.

**Speed.** One clock of delay.

**How it is checked.** The test covers ties between scores, the exact margin and threshold edges, a threshold sweep, random scores, entries that skip the network, and output pauses (simulated).
