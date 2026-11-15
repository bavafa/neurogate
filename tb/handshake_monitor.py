"""Shared cocotb helper: watch a valid/ready stream exactly as the clock edge sees it.

Why this exists (independent M2 review): a monitor that reads the signals right after
a rising edge sees the values the chip has just *updated to*, plus the ready
value the testbench has just driven. That is the transfer of the NEXT edge, not
the one that just happened. This helper fixes that. After every rising edge it
reads the settled signals (``ReadOnly``) and keeps them as a snapshot. They are
exactly what the following edge samples. On the next edge the snapshot is
judged: valid, ready and reset were all high, so one beat moved at that edge.

It also checks the AXI-Stream stall rule: once valid is high and ready is low,
valid and every payload and sideband signal must stay unchanged until ready is
high (the sender may not retract or change a beat that is waiting).
"""
from typing import Callable, Dict, List

from cocotb.triggers import ReadOnly, RisingEdge


async def monitor_handshake(clk, rst_n, valid, ready, signals: Dict[str, object],
                            on_beat: Callable[[Dict[str, int]], None],
                            errors: List[str], label: str) -> None:
    """Run forever: call ``on_beat(values)`` for each beat accepted at a clock edge.

    ``signals`` maps a name to a signal handle (payload and sideband). ``values``
    holds those signals as ints, as they were at the edge. A stall that changes
    valid or any signal appends a message to ``errors``. Cancel the task to stop it.
    """
    prev = None                                 # snapshot of the cycle before this edge
    cycle = 0
    while True:
        await RisingEdge(clk)
        await ReadOnly()
        cycle += 1
        cur = (int(rst_n.value), int(valid.value), int(ready.value),
               {k: int(v.value) for k, v in signals.items()})
        if prev is not None and prev[0]:        # the edge just passed was not in reset
            _, p_valid, p_ready, p_vals = prev
            if p_valid and p_ready:
                on_beat(p_vals)
            elif p_valid:                       # stalled: the beat must still be there
                if not cur[1]:
                    errors.append(f"{label}: valid dropped during a stall (edge {cycle})")
                else:
                    changed = [k for k in p_vals if p_vals[k] != cur[3][k]]
                    if changed:
                        errors.append(f"{label}: {', '.join(changed)} changed during a stall "
                                      f"(edge {cycle})")
        prev = cur
