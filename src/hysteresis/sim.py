"""Alert state machine, one sample per evaluation step.

States: NORMAL -> PENDING -> FIRING -> RECOVERING -> NORMAL.
A NaN sample represents a missing sample and makes the condition false for
that evaluation. This is a discrete benchmark model, not a full PromQL engine.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import numpy as np

NORMAL, PENDING, FIRING, RECOVERING = 0, 1, 2, 3
STATE_NAMES = {NORMAL: "normal", PENDING: "pending", FIRING: "firing", RECOVERING: "recovering"}


@dataclass(frozen=True)
class Rule:
    threshold: float
    direction: str = "above"
    inclusive: bool = False
    clear_threshold: float | None = None
    pending_steps: int = 0
    keep_firing_steps: int = 0

    def __post_init__(self) -> None:
        try:
            threshold = float(self.threshold)
        except (TypeError, ValueError) as exc:
            raise ValueError("threshold must be a finite number") from exc
        if not math.isfinite(threshold):
            raise ValueError("threshold must be finite")
        object.__setattr__(self, "threshold", threshold)
        if self.direction not in ("above", "below"):
            raise ValueError("direction must be 'above' or 'below'")
        for name, value in (
            ("pending_steps", self.pending_steps),
            ("keep_firing_steps", self.keep_firing_steps),
        ):
            if not isinstance(value, (int, np.integer)) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
            object.__setattr__(self, name, int(value))
        if not isinstance(self.inclusive, bool):
            raise ValueError("inclusive must be a boolean")
        if self.clear_threshold is not None:
            try:
                clear_threshold = float(self.clear_threshold)
            except (TypeError, ValueError) as exc:
                raise ValueError("clear_threshold must be a finite number") from exc
            if not math.isfinite(clear_threshold):
                raise ValueError("clear_threshold must be finite")
            object.__setattr__(self, "clear_threshold", clear_threshold)
            if self.direction == "above" and self.clear_threshold > self.threshold:
                raise ValueError("for direction 'above', clear_threshold must be <= threshold")
            if self.direction == "below" and self.clear_threshold < self.threshold:
                raise ValueError("for direction 'below', clear_threshold must be >= threshold")


def simulate(x: np.ndarray, rule: Rule) -> np.ndarray:
    """Return the alert state at every evaluation step (int8 array)."""
    x = np.asarray(x, dtype=float)
    if x.ndim != 1:
        raise ValueError("input signal must be a one-dimensional array")
    if np.isinf(x).any():
        raise ValueError("input signal may contain NaN for missing data, but not infinity")
    if not isinstance(rule, Rule):
        raise TypeError("rule must be a Rule instance")

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

    out = np.zeros(len(x), dtype=np.int8)
    state, pend, keep = NORMAL, 0, 0
    for i in range(len(x)):
        if state in (NORMAL, PENDING):
            if breach[i]:
                pend += 1
                state = FIRING if pend > rule.pending_steps else PENDING
            else:
                pend, state = 0, NORMAL
        else:
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
    """Return contiguous active alert episodes as [start, end) step ranges."""
    active = np.isin(states, (FIRING, RECOVERING)).astype(np.int8)
    d = np.diff(active, prepend=0, append=0)
    starts = np.flatnonzero(d == 1)
    ends = np.flatnonzero(d == -1)
    return list(zip(starts.tolist(), ends.tolist(), strict=True))
