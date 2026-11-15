# model/: the Python models and training scripts

These scripts make the traffic, define the numbers the chip must produce, and train the network. The Python results are the reference for the chip: if the chip and Python disagree, one of them is wrong and must be fixed.

- **Traffic generator** (`traffic_gen.py`): uses Scapy, a Python packet library, to make synthetic packets for three classes (normal, port scan, SYN flood), including awkward ones (VLAN tags, fragments, truncated frames, bad flags). Seeds are fixed, so the same seed gives the same files.
- **Feature model** (`features.py`): reads frames the way the chip does and computes the eight features with whole-number arithmetic. It also decides which frames are dropped.
- **Training** (`dataset.py`, `train.py`, `quantize.py`): trains the network in floating point, then converts it to int8 weights and writes the files the chip's read-only tables load.
- **Golden model** (`golden.py`): the whole-number inference and the confidence rule. It is the source of truth for the arithmetic.
- **Evaluation and test vectors** (`evaluate.py`, `export_vectors.py`): accuracy, confusion matrix and the accuracy-by-confidence table on data the training never saw, and the vector files the hardware tests replay.

All accuracy numbers are simulated and come from synthetic data, so they are not real-world accuracy.
