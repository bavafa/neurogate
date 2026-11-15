# NeuroGate

NeuroGate is a small chip design that looks at network packets and labels each one as
**normal**, **port scan** or **SYN flood**. A port scan is one machine knocking on many
doors (ports) to see which are open. A SYN flood is a burst of connection requests that are
never finished, meant to overload a server. (SYN, short for "synchronize", is the first
message of a TCP connection. TCP, the Transmission Control Protocol, is the usual way two
computers set up a reliable connection.)

> **Everything here is simulation or estimate.** There is no FPGA (field-programmable gate array, a reprogrammable chip)
> and no silicon result. The traffic is synthetic (made by a script), so the accuracy numbers
> are not real-world accuracy. See [Limitations](#limitations).

## The idea in one minute

Network equipment has to decide quickly what to do with each packet. A small neural network
(a short chain of multiply-and-add steps with learned weights) can make that call from a few
numbers about the recent traffic. NeuroGate is a design of that whole path in hardware
description code (SystemVerilog): from the bytes of a packet arriving, to a label coming out.

The design is checked against a Python model, bit for bit. The Python model is the reference
for the arithmetic. If the two disagree, one of them is wrong and gets fixed.

## How a packet travels through the chip

The chip has two clocks that are not tied to each other: a network clock (156.25 MHz) and a
core clock (200 MHz). Packet data crosses from one to the other in exactly one place, a queue
built for that job. The header readers understand IPv4 (Internet Protocol version 4), TCP and
UDP (User Datagram Protocol, a simpler send-and-forget protocol).

```
 network clock (156.25 MHz)
 packet bytes in -> input queue -> Ethernet header reader -> IPv4/TCP/UDP header reader
                                                                      |
                                                       feature builder (8 numbers per packet)
                                                                      |
                                                  clock-crossing queue   <- the only data crossing
                                                                      |
 core clock (200 MHz)                                  neural network (int8, 1 multiply-add per clock)
                                                                      |
                              label out  <----  confidence and flag
                                                                      |
                              register bus <-> counters
```

1. **Read the headers.** Two parsers pick the needed fields out of each frame (an Ethernet
   frame is the unit sent on a local network) and drop frames that are malformed or not
   supported.
2. **Make eight numbers.** The feature builder turns each kept packet into eight small
   integers. Two of them come from a sliding window of the last 64 packets: how many different
   destination ports were hit, and how many connection requests were seen. These are approximations: ports are sorted into 64 buckets by a hash and the chip counts occupied buckets (two ports can share one, so the count can be too low), and only requests without an acknowledgement (SYN without ACK) are counted, not every SYN packet.
3. **Cross the clocks.** The queue between the two clocks moves one entry per packet.
4. **Classify.** The neural network has 8 inputs, 16 hidden units and 3 outputs, with 8-bit
   whole-number (int8) weights. One multiply-add unit does all the work, so one packet takes
   179 core clocks.
5. **Report.** The chip outputs the label and a **confidence**. The confidence is a margin
   between the top two network outputs. It is not a probability. A packet whose confidence is
   below a threshold is flagged as low confidence. Software can read packet and byte counters
   and set the threshold over a simple register bus.

The full requirements are in [SPEC.md](SPEC.md). The register list is in
[docs/regmap.md](docs/regmap.md). A short note on every block is in
[docs/modules/](docs/modules/README.md).

## Results so far

Each number says where it comes from. *Simulated* means a simulation run on this repo.
*Estimate* means a tool estimate with no place and route (no real wiring and layout).

| What | Result | Label |
|---|---|---|
| Accuracy of the int8 network, on 8,347 packets it never saw in training | 95.50% | *simulated*, synthetic data |
| Packets the chip drops as malformed or unsupported, same data | 653 of 9,000 frames | *simulated*, synthetic data |
| Chip output vs Python model, on all 8,347 classified test packets | 0 mismatches | *simulated* (log: [full_split_replay.log](docs/results/full_split_replay.log)) |
| Packets that are correct when confidence is at or above the default threshold (13) | 97.86% (flagged packets: 80.93%) | *simulated*, synthetic data |
| Speed | 179 core clocks per packet, about 1.1 million packets per second at 200 MHz | *simulated estimate* |
| Size after synthesis (Yosys, Nangate45 teaching library) | 18,907 cells, 34,866 square micrometres | *synthesized (estimate)* |
| Timing at 200 MHz and 156.25 MHz, worst setup slack (positive = met) | +2.649 ns and +4.694 ns | *synthesized (estimate)*, ideal clocks |
| Reset-release check at the queue flip-flops | misses by 0.108 ns; not done, because there is no place-and-route step | *synthesized (estimate)* |
| Rough FPGA size (Artix-7, from Yosys, not Vivado) | 2,445 lookup tables, 1,710 flip-flops | *estimate* |
| Gate-level simulation (netlist instead of source code) | smoke set and bad-packet set both match the Python model | *simulated* |

The [results page](docs/index.html) shows these numbers with charts, a waveform from a real
simulation run and the source file of each number. More detail:
[accuracy and confusion matrix](docs/results/results.md),
[synthesis, timing and gate-level notes](synth/README.md),
[clock-crossing review](docs/cdc.md).

## Project status

| Milestone | Item | Status |
|---|---|---|
| M1 | Spec, register map, scaffolding | done (gate approved) |
| M2 | Traffic generator, feature model, parsers, queues | done (gate approved) |
| M3 | Trained int8 net, golden model, network core, decision step | done (gate approved) |
| M4 | Full chip, synthesis, gate-level simulation, timing estimates | done (gate approved) |
| M5 | Docs, results page, final checks | in progress: README, module notes, limitations, results page, clean-clone check and personal-information scan done; second-model review and owner gate still to do |

## Quick start

```
python3 -m venv .venv && .venv/bin/pip install cocotb scapy numpy   # one-time: Python packages
source .venv/bin/activate    # use them in this shell
. scripts/env.sh             # puts make, the simulators and the timing tool on PATH
lib/fetch_nangate45.sh       # one-time: download the cell library used for timing
make toolchain               # print tool versions (reports the cell library as missing until the line above has run)
make lint compile-check cdc-check test
make test-full               # slower: all test packets, and 9,000 frames through the whole chip
make synth gls sta           # synthesis, gate-level simulation, timing (needs the library above); the timing step is expected to exit non-zero because of the accepted reset-release miss (0.108 ns, see below)
```

- `make lint` checks the source with Verilator (a checker and simulator) in its strictest mode.
- `make test` runs every simulation suite with a fixed seed, which is printed in the log.
  The whole-chip traffic test uses 900 frames here, so `make test` stays short.
- `make test-full` also runs the 9,000-frame whole-chip test (about 3 minutes) and the replay
  of every classified test packet through the network core.
- `make cdc-check` checks clock crossings (see Limitations for what that does and does not prove).

Logs are kept in `results/<git commit>/`; a `-dirty` suffix means uncommitted changes.
Tool setup is in [docs/toolchain.md](docs/toolchain.md).

## Limitations

- **Synthetic data.** All traffic comes from this repo's own generator. Real traffic is messier.
  The accuracy numbers are not real-world accuracy, and nothing here was tested on real captures.
- **Simulation only.** Nothing ran on an FPGA or in silicon. "Throughput" is a cycle count from
  simulation times the target clock. It is a simulated estimate, not a measured line rate.
- **Clock crossings: structural check plus review, no formal proof.** A script checks that
  every signal crossing between the two clocks is one of the three allowed kinds, and the
  builder wrote a review of each crossing. This is not a commercial clock-crossing tool and
  not a proof. Simulation cannot show metastability (a flip-flop stuck between 0 and 1 when
  its input changes at the wrong moment) at all.
- **Timing without place and route.** The timing tool (OpenSTA) used ideal clocks, no wires
  beyond one rough guess, one typical operating condition, and a guessed 20% budget for logic
  outside the chip. Setup is met on both clocks (estimate). The reset-release miss of
  0.108 ns is reported as not done, because there is no place-and-route step to fix it.
  These numbers say nothing about a real chip.
- **Gate-level simulation is partial.** The netlist ran the 120-frame smoke set and the
  480-frame bad-packet set. The longer traffic, overflow and reset tests were not repeated on it.
  The gate models have no delays, so this checks function, not timing.
- **Not done.** No FPGA or silicon run, no Vivado result, no power estimate, no formal proofs,
  no real-world traffic, no manufacturing flow. A clean-checkout run of `make test` passed (simulated;
  see [docs/results/clean_clone.log](docs/results/clean_clone.log)); the slower checks were not repeated there.
- **Small design on purpose.** Only IPv4 with TCP or UDP and at most one VLAN tag is
  understood (a VLAN, or virtual local area network, tag is a label that splits one physical
  network into separate logical ones). Everything else is counted as dropped.

## Case-study site

`site/` holds a static page that presents this project (architecture, a step-by-step packet
walk-through, charts built from `docs/results/metrics.json`, verification and timing reports).
Regenerate its data with `python3 scripts/export_site_data.py`, check the JavaScript replay of the
golden model with `node site/test/golden.test.mjs`, and deploy the `site/` folder to Vercel as a
static site (Root Directory `site`, no build command). Details: [site/README.md](site/README.md).
Every number on the page comes from a file in this repository; the tags *simulated* and *estimate* mean the same as above.

## Development approach

The project uses an automated builder and an independent reviewer under the owner's direction.
The builder writes the code and tests. The reviewer checks each milestone and writes its findings in a separate file, which the
builder may not edit. The owner approves each milestone gate, and every owner decision is logged
in [DECISIONS.md](DECISIONS.md).

## Where to look next

- [SPEC.md](SPEC.md): what the chip must do.
- [docs/modules/](docs/modules/README.md): one short note per block and per script group.
- [docs/PRD.md](docs/PRD.md) and [docs/stories.md](docs/stories.md): the product requirements document (PRD) and the user stories.
- [STATUS.md](STATUS.md) and [TASKS.md](TASKS.md): current progress and the task list.
