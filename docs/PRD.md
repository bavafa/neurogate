# NeuroGate: Product requirements

A one-page plan. The detailed technical spec is in the project's top folder. Everything here is simulation only.

## Problem statement
Some network cards have a small AI (artificial intelligence) engine built in. It must look at every packet as it passes and quickly decide whether it looks like an attack. In hardware that means reading packet headers, keeping a short history of recent traffic, running a small neural net in whole-number math, and passing data between parts of the chip on different clocks, without losing packets. Most public examples show only one of these pieces. Readers with basic engineering background lack a complete, small, honest example. The project demonstrates chip design and testing from start to finish.

## Proposed solution
A chip design in SystemVerilog (a language for describing hardware). It reads Ethernet frames 64 bits at a time and labels each IPv4 TCP or UDP packet as normal, port scan (probing for open services) or SYN flood (many SYN messages, the first step of opening a connection, never finished). IPv4 is version 4 of the Internet Protocol, which addresses data on the internet. TCP (connection-based) and UDP (connectionless) are the two most common ways to send data over it. Each label comes with a confidence margin: the gap between the top two scores, not a probability. A Python reference model defines the exact math, and the chip must match it bit for bit.

## Goals and non-goals
Goals:
- The chip design and a Python model it matches bit for bit.
- A full set of checks: block and whole-chip tests, a code checker, a review of every signal crossing between clocks, conversion to logic gates, simulation of the gate version, a timing check, and a size estimate for an FPGA (a reprogrammable chip).
- Plain-English docs and a results page that says where every number came from.

Non-goals:
- Running on a real FPGA or chip, or keeping up with a real network link.
- Real-world detection quality: training and testing use fake (synthetic) traffic only.
- Newer IPv6 packets, frames with two stacked VLAN tags (network labels), per-connection tracking tables, learning while running, probability output, real packet captures.
- Commercial-grade clock-crossing sign-off, or formal (mathematical) proof.

## Success metrics
Targets. Results at the end of milestone 3 (*simulated*, fake data): zero mismatches against the Python model on all 8,347 classified unseen test packets; accuracy there 95.5%, not real-world accuracy. Timing and size (*estimate*) are in the results page.
- Chip answers equal the Python model on every test packet: zero mismatches (*simulated*).
- All tests pass on a fresh copy, with fixed random seeds (*simulated*).
- The code checker (Verilator) reports no unexplained warnings, and all chip code builds in both open-source simulators (Icarus Verilog and Verilator).
- No packet is lost, duplicated or reordered when the receiver stalls at random, unless counted in the overflow counter (*simulated*).
- Accuracy, a right/wrong table, and margin versus measured accuracy are reported on test data the model never saw (*simulated*, fake data, not real-world accuracy).
- Timing at a 200 MHz target on an open 45 nm cell library (basic logic gates) is reported, with before and after numbers if extra pipeline stages were needed (*estimate*).

## Main risks
- **Model and chip round or clamp differently:** the whole-number Python model is the source of truth, and tests aim at edge cases.
- **Fake data is too easy:** add noise and overlap, split data by seed and scenario, state the caveat everywhere.
- **The neural net core is slower than packets arrive** (about 176 multiply-add steps per packet, *estimate*): losses are counted, tested and reported honestly.
- **Timing fails at 200 MHz:** add pipeline stages (extra registers that split long paths) and keep before and after numbers.
- **Owner time is short (8 to 10 hours), or a simulator rejects some code:** small spec, optional items cut, only code both simulators accept.

## Milestones
1. Plan and set up: frozen spec, register list, project skeleton.
2. Reading packets: fake traffic, header readers, buffer and clock-crossing queue.
3. Neural net: trained 8-bit model, blocks that match Python exactly.
4. Full chip: end-to-end tests, gate-level simulation, timing and size estimates.
5. Docs: README, results page, clean public release.
