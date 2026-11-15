# NeuroGate: Epics and user stories

NeuroGate is a small chip design that labels each network packet as normal, a port scan (probing for open services) or a SYN flood (many connection-opening SYN messages, never finished). One epic per stage. Results are *simulated* or *estimates*; nothing runs on real hardware.

## Epic 1: Plan and set up

This epic agrees what to build and prepares the tools. Without it, later work gets redone.

**Problem:** code built on a moving plan or untested tools gets rewritten.

**What we'll build:** a frozen design, a register list, a project skeleton.

**Acceptance criteria:**
- The spec has no open questions.
- All project commands run on recorded tool versions.

### Settle the design (owner)
**Problem:** Open questions cause rework later.

**What we'll build:** written answers, a final spec.

**Acceptance criteria:**
- Each answer is in the decision log.
- No spec item is still "proposed".
- The cell library (basic logic gates for timing estimates) is chosen.

### Register list (software writer)
**Problem:** Software must know what the chip shows.

**What we'll build:** a list of registers (numbered slots software reads).

**Acceptance criteria:**
- Every register is listed.
- One counts packets lost to a full queue.

### Project skeleton (builder)
**Problem:** Tool problems found late waste time.

**What we'll build:** main commands, a tool check, a first README (front page).

**Acceptance criteria:**
- Test, code check, gate build, timing check and gate simulation run, even if empty.
- Tool versions are recorded.
- The README has a block diagram and status table.

## Epic 2: Reading packets and moving data

This epic builds the parts that read packet headers and pass data safely between the chip's two clocks. A chip that handles junk badly is useless.

**Problem:** one bad packet must never freeze the chip.

**What we'll build:** fake traffic, header readers, safe data movement.

**Acceptance criteria:**
- All three stories pass.
- New chip code passes the code checker cleanly.

### Test traffic and reference (tester)
**Problem:** Blocks need known answers to be tested.

**What we'll build:** labelled fake IPv4 packets (the internet's addressing scheme, version 4) carrying TCP (reliable) or UDP (fast) data, and a Python measurement model.

**Acceptance criteria:**
- Packets and labels are the same every run (fixed random seeds).
- The model computes every spec measurement in whole-number math.

### Header readers (chip designer)
**Problem:** VLAN tags (extra network labels), optional IP (Internet Protocol, the addressing layer) header fields, fields split across data words, and broken frames can stall a chip.

**What we'll build:** blocks that read header fields.

**Acceptance criteria:**
- Tests pass, including split fields.
- Output matches Scapy (a standard Python packet tool) on every field of every packet Scapy can decode. Six cut-off frames it cannot decode are checked only for keep or drop.

### Buffer and clock-crossing queue (chip designer)
**Problem:** A busy receiver, or crossing between the chip's two clocks, can lose data.

**What we'll build:** a small buffer and a two-clock queue.

**Acceptance criteria:**
- The buffer passes tests where the receiver says "wait" at random.
- The queue passes tests at several clock-speed ratios.

## Epic 3: Neural net matches Python

This epic builds the small neural net that labels packets. The hardware must give the same answers as the Python reference.

**Problem:** hardware math is trusted only if it matches a reference exactly.

**What we'll build:** a tiny neural net in 8-bit integers, and blocks that run it.

**Acceptance criteria:**
- Chip and Python math match exactly (simulated).
- Accuracy is published with the fake-data caveat.

### Trained, shrunk model (reader)
**Problem:** readers need honest accuracy; fake data says little about real networks.

**What we'll build:** a trained net and its 8-bit version, fixed seeds.

**Acceptance criteria:**
- Accuracy, precision and recall (share of alarms that are real, share of attacks caught) are published for both versions.
- So is a right/wrong table.

### Margin meaning (chip user)
**Problem:** the confidence margin (gap between the top two scores, not a probability) means little alone.

**What we'll build:** a measured table and chart.

**Acceptance criteria:**
- They show how often the label was right per margin range.
- The default alarm threshold is chosen on validation data only; the results are reported on unseen test data.

### Bit-exact blocks (tester)
**Problem:** rounding and clamping differences hide in rare cases.

**What we'll build:** a whole-number Python reference; measurement, net and decision blocks.

**Acceptance criteria:**
- Zero mismatches, including two packets sharing one slot of the short traffic history (chosen by destination port), one aged out, one still in; clamped values; tied scores.
- Cycles per decision are measured in simulation (design estimate about 176, one per multiply-add).

## Epic 4: Full chip passes all checks

This epic joins every block into one chip and tests it hard. Parts that work alone can still fail together.

**Problem:** working blocks can fail together; cost and speed are unknown.

**What we'll build:** the joined chip, stress tests, size and speed estimates.

**Acceptance criteria:**
- Full-chip labels match the Python model (simulated).
- Timing and size are reported as estimates.

### Counters and registers (software writer)
**Problem:** monitoring software needs registers matching the list.

**What we'll build:** counter and register blocks.

**Acceptance criteria:**
- Counter tests pass against the register list.
- Register tests pass against it too.

### End-to-end tests (tester)
**Problem:** blocks must work together under stress.

**What we'll build:** whole-chip tests.

**Acceptance criteria:**
- Labels match the Python model on all generated traffic (simulated).
- Tests pass for bad packets, a randomly stalling receiver, resets and a full queue.
- Every clock crossing is reviewed; the code checker is clean.

### Size and speed (reader)
**Problem:** cost and speed are unknown.

**What we'll build:** a logic-gate version and estimate reports.

**Acceptance criteria:**
- Simulating the gates gives the same answers.
- Timing on an open 45 nm cell library and size on an FPGA (a reprogrammable chip, Xilinx Artix-7) are reported as estimates.
- Packets per second are estimated from simulated cycle counts.

## Epic 5: Docs and results page

This epic explains the project so a newcomer can read, repeat and trust it.

**Problem:** an unreadable, unrepeatable project has little value.

**What we'll build:** reader docs and a release check.

**Acceptance criteria:**
- A newcomer understands it without chip code.
- A fresh copy passes all tests.

### README and results page (engineering reader)
**Problem:** chip code alone does not explain the project.

**What we'll build:** README, block notes, results page.

**Acceptance criteria:**
- The README explains the background first and how AI (artificial intelligence) tools were used.
- Every result names its source; a limitations section exists.

### Clean public release (owner)
**Problem:** anyone should repeat the results safely.

**What we'll build:** a fresh-copy check and a privacy check.

**Acceptance criteria:**
- A fresh copy, set up from the README, passes all tests.
- No personal information or private notes remain.
