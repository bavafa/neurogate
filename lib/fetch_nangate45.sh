#!/usr/bin/env bash
# fetch_nangate45.sh: download the Nangate Open Cell Library (typical corner)
# liberty file used by OpenSTA (D-001d) and verify its checksum.
# The file is not committed (6.7 MB, third-party license); see lib/README.md.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
out="$here/NangateOpenCellLibrary_typical.lib"
url="https://raw.githubusercontent.com/The-OpenROAD-Project/OpenROAD-flow-scripts/master/flow/platforms/nangate45/lib/NangateOpenCellLibrary_typical.lib"
sha="8d540a4d4cf6d09d27c87ad067857a9c0c2eeb023ab7a56e058cd3113db4e9b1"
curl -sSfL -o "$out" "$url"
echo "$sha  $out" | sha256sum -c -
