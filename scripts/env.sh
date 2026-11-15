#!/usr/bin/env bash
# Source this file (". scripts/env.sh") to put the NeuroGate toolchain on PATH.
# NG_TOOLS selects the tool install folder (default: $HOME/tools). Entries
# that do not exist are skipped.
# It does not modify anything outside the current shell.

_ng_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for _d in \
    "$_ng_root/.venv/bin" \
    "${NG_TOOLS:-$HOME/tools}/bin" \
    "${NG_TOOLS:-$HOME/tools}/opensta/bin" \
    "${NG_TOOLS:-$HOME/tools}/oss-cad-suite/bin"; do
  [ -d "$_d" ] && case ":$PATH:" in *":$_d:"*) ;; *) PATH="$_d:$PATH" ;; esac
done
export PATH
unset _ng_root _d
