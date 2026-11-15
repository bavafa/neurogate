# feature_builder: from headers to eight numbers

Source file: `rtl/feature_builder.sv`.

**What it does.** Turns each parsed frame into the eight small numbers (features) the neural network reads, and writes one entry per frame to the clock-crossing queue.

**Where it sits.** Last block on the network clock. It keeps a sliding window of the last 64 kept packets, so it can estimate how many different destination ports and how many connection requests were seen recently. These two counts are what separate a port scan and a SYN flood from normal traffic.

**How it works.** The port count is an approximation. Each destination port is mapped to one of 64 buckets by a hash, and the chip counts occupied buckets. Two ports can share a bucket (a collision), so the count can be lower than the true number of different ports. The connection-request count includes only SYN packets without ACK (the first step of a connection), not every packet with the SYN flag. Dropped frames also get an entry (with zero features), so the counters on the other side still count them. If the queue is full, the entry is thrown away and a lost-frame count is added to the next entry that does get through; the input is never stalled. All arithmetic is integer, with no divider.

**Speed.** One record in per clock. The entry is offered one clock later and is written, or thrown away if the queue is full, on the next edge.

**How it is checked.** Compared bit for bit with the Python feature model on generated traffic, with and without gaps between records, including lost-frame counting and saturation (simulated).
