#!/usr/bin/env bash
# toolchain_check.sh: print the version of every tool NeuroGate needs.
#
# Usage: scripts/toolchain_check.sh
# Exit code: 0 if every tool ran and printed a usable version and the liberty
# file was found, 1 otherwise (missing tool, failing command, or error output).
# Output is one "name: value" line per tool so it can be pasted into STATUS.md.
# Each result file in the project should record these versions (SPEC section 10).

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(dirname "$here")"
# shellcheck source=env.sh
. "$here/env.sh"

missing=0

# report NAME CMD...: run CMD, show its first line of output, or flag a problem.
# A tool counts as failed (missing=1) if it is not on PATH, if the version
# command exits non-zero, if it prints nothing, or if its output looks like an
# error (for example Icarus printing "Error opening temporary file" when it
# cannot write to its temp directory).
report() {
  local name="$1"; shift
  if ! command -v "$1" >/dev/null 2>&1; then
    printf '%-10s MISSING (%s not on PATH)\n' "$name:" "$1"
    missing=1
    return
  fi
  local out rc first
  out="$("$@" 2>&1)"; rc=$?   # capture the full output so the exit code is not lost in a pipe
  first="$(printf '%s\n' "$out" | head -n 1)"
  if [ "$rc" -ne 0 ]; then
    printf '%-10s FAILED (exit %d): %s\n' "$name:" "$rc" "$first"
    missing=1
  elif [ -z "$first" ]; then
    printf '%-10s FAILED (no version output)\n' "$name:"
    missing=1
  elif printf '%s\n' "$out" | grep -Eqi 'error|cannot|unable|fatal|no such file'; then
    printf '%-10s UNUSABLE OUTPUT: %s\n' "$name:" "$first"
    missing=1
  else
    printf '%-10s %s\n' "$name:" "$first"
  fi
}

report "make"      make --version
report "Verilator" verilator --version
report "Icarus"    iverilog -V
report "Yosys"     yosys -V
report "OpenSTA"   sta -version
report "Python"    python3 --version

# Python packages are checked in the project venv (.venv) found through PATH.
pyver() {
  python3 -c "import importlib.metadata as m; print(m.version('$1'))" 2>/dev/null
}
for pkg in cocotb scapy numpy; do
  v="$(pyver "$pkg")"
  if [ -n "$v" ]; then printf '%-10s %s\n' "$pkg:" "$v"
  else printf '%-10s MISSING (not importable by python3)\n' "$pkg:"; missing=1; fi
done

lib="$root/lib/NangateOpenCellLibrary_typical.lib"
if [ -f "$lib" ]; then
  printf '%-10s %s\n' "liberty:" "present, sha256 $(sha256sum "$lib" | cut -c1-16)..."
else
  printf '%-10s MISSING (run lib/fetch_nangate45.sh)\n' "liberty:"; missing=1
fi
exit "$missing"
