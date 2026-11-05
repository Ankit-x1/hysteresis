"""Alert state machine, one sample per evaluation step.

States: NORMAL -> PENDING -> FIRING -> RECOVERING -> NORMAL.

Semantics (checked against promtool by tools/promtool_fidelity.py):
  * An alert fires on the first evaluation where the condition has been true
    for at least `for` (pending_steps evaluations after the first true one).
  * After the condition stops being true, a firing alert keeps firing for
    `keep_firing_for` (keep_firing_steps evaluations) before resolving.
  * `clear_threshold` is a recovery threshold (Grafana feature, not Prometheus):
    once firing, the condition is considered true until the value crosses it.
  * A missing sample (NaN) counts as "condition false".
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

NORMAL, PENDING, FIRING, RECOVERING = 0, 1, 2, 3
STATE_NAMES = {NORMAL: "normal", PENDING: "pending", FIRING: "firing", RECOVERING: "recovering"}


@dataclass(frozen=True)
class Rule:
    threshold: float
    direction: str = "above"  # "above": value > threshold. "below": value < threshold.
    inclusive: bool = False  # True for >= / <=
    clear_threshold: float | None = None  # None -> same as threshold (no hysteresis band)
    pending_steps: int = 0
    keep_firing_steps: int = 0

    def __post_init__(self) -> None:
        if self.direction not in ("above", "below"):
            raise ValueError("direction must be 'above' or 'below'")
        if self.pending_steps < 0 or self.keep_firing_steps < 0:
            raise ValueError("step counts must be >= 0")
        if self.clear_threshold is not None:
            if self.direction == "above" and self.clear_threshold > self.threshold:
                raise ValueError("for direction 'above', clear_threshold must be <= threshold")
            if self.direction == "below" and self.clear_threshold < self.threshold:
                raise ValueError("for direction 'below', clear_threshold must be >= threshold")


def simulate(x: np.ndarray, rule: Rule) -> np.ndarray:
    """Return the alert state at every evaluation step (int8 array)."""
    x = np.asarray(x, dtype=float)
    sign = 1.0 if rule.direction == "above" else -1.0
    v = sign * x
    thr = sign * rule.threshold
    clear = thr if rule.clear_threshold is None else sign * rule.clear_threshold
    if rule.inclusive:
        breach = v >= thr
        held = v >= clear
    else:
        breach = v > thr
        held = v > clear
    # NaN compares False, so a missing sample is "condition false".

    out = np.zeros(len(x), dtype=np.int8)
    state, pend, keep = NORMAL, 0, 0
    for i in range(len(x)):
        if state == NORMAL or state == PENDING:
            if breach[i]:
                pend += 1
                state = FIRING if pend > rule.pending_steps else PENDING
            else:
                pend, state = 0, NORMAL
        else:  # FIRING or RECOVERING
            if held[i]:
                state, keep = FIRING, 0
            else:
                keep += 1
                if keep > rule.keep_firing_steps:
                    state, pend, keep = NORMAL, 0, 0
                else:
                    state = RECOVERING
        out[i] = state
    return out


def episodes(states: np.ndarray) -> list[tuple[int, int]]:
    """Alert episodes as [start, end) step ranges.

    An episode is a contiguous run of FIRING/RECOVERING. Each one costs one
    "firing" and one "resolved" notification.
    """
    active = ((states == FIRING) | (states == RECOVERING)).astype(np.int8)
    d = np.diff(active, prepend=0, append=0)
    starts = np.flatnonzero(d == 1)
    ends = np.flatnonzero(d == -1)
    return list(zip(starts.tolist(), ends.tolist()))
