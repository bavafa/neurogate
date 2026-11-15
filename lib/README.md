# lib/: third-party cell library

`NangateOpenCellLibrary_typical.lib` is the liberty (timing) file OpenSTA uses
(D-001d). It is **not committed**; fetch it with `lib/fetch_nangate45.sh`.

| Item | Value |
|---|---|
| Library | Nangate Open Cell Library (45 nm), typical corner (1.10 V, 25 C, process 1.0) |
| Source | `flow/platforms/nangate45/lib/` in OpenROAD-flow-scripts (The-OpenROAD-Project on GitHub) |
| SHA-256 | `8d540a4d4cf6d09d27c87ad067857a9c0c2eeb023ab7a56e058cd3113db4e9b1` |
| License | Copyright Nangate Inc.; distributed under the Open Cell Library license. Read the header of the file. It is a third-party file, so it is kept out of this repo. |

It is a teaching/research library, not a real foundry process. All timing
numbers derived from it are labelled *synthesized (estimate)*.
