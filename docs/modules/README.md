# Module notes

One short note per hardware block and per group of scripts. Each note says what the part does, where it sits, how fast it is and how it is checked. Every result is simulated or an estimate. The test data is synthetic.

Packet path, in order:

1. [skid_fifo](skid_fifo.md): the input queue
2. [eth_parser](eth_parser.md): Ethernet header reader
3. [l3l4_parser](l3l4_parser.md): IPv4 and TCP/UDP header reader
4. [feature_builder](feature_builder.md): eight features per frame
5. [async_fifo](async_fifo.md): the clock-crossing queue
6. [mlp_core](mlp_core.md): the neural network
7. [decision_unit](decision_unit.md): confidence and flag

Around the path:

- [stats_counters](stats_counters.md): counters
- [axil_regs](axil_regs.md): register bus
- [reset_sync](reset_sync.md): reset synchronizer
- [neurogate_top](neurogate_top.md): the whole chip

Script groups:

- [model](model.md): Python models and training
- [tb](tb.md): hardware tests
- [synth](synth.md): synthesis, gate-level run, timing
- [scripts](scripts.md): helper scripts
