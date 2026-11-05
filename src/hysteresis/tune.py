"""Random-search tuner for pending / keep-firing / recovery-threshold settings.

Plain seeded random search: four numbers per rule do not justify gradients.
The user's current rule is always candidate #0, so the result is never worse
than the starting point on the data it was tuned on. Always report results on
held-out data (Bench.split), not on the data you tuned with.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

import numpy as np

from .bench import Bench
from .rules import steps_for
from .score import Score, score
from .sim import Rule, simulate


@dataclass
class TuneResult:
    rule: Rule
    score: Score
    loss: float
    base_score: Score
    base_loss: float
    trials: int


def loss_of(s: Score, max_delay_s: float, w_missed: float, w_extra: float, w_delay: float) -> float:
    delay = s.mean_delay_s or 0.0
    excess_min = max(0.0, delay - max_delay_s) / 60.0
    return w_missed * s.missed + w_extra * s.extra_pages + w_delay * excess_min


def tune(
    bench: Bench,
    base: Rule,
    *,
    trials: int = 300,
    seed: int = 0,
    allow_band: bool = False,
    max_delay_s: float = 300.0,
    max_pending_s: float = 1800.0,
    max_keep_s: float = 3600.0,
    w_missed: float = 10.0,
    w_extra: float = 1.0,
    w_delay: float = 1.0,
) -> TuneResult:
    rng = np.random.default_rng(seed)
    sd = float(np.std(bench.values))
    max_p = steps_for(max_pending_s, bench.step_s)
    max_k = steps_for(max_keep_s, bench.step_s)

    def evaluate(r: Rule) -> tuple[Score, float]:
        s = score(simulate(bench.values, r), bench)
        return s, loss_of(s, max_delay_s, w_missed, w_extra, w_delay)

    base_score, base_loss = evaluate(base)
    best, best_score, best_loss = base, base_score, base_loss
    for _ in range(trials):
        clear = None
        if allow_band:
            width = float(rng.uniform(0.0, 1.5 * sd))
            clear = base.threshold - width if base.direction == "above" else base.threshold + width
        cand = dataclasses.replace(
            base,
            pending_steps=int(rng.integers(0, max_p + 1)),
            keep_firing_steps=int(rng.integers(0, max_k + 1)),
            clear_threshold=clear if allow_band else base.clear_threshold,
        )
        s, l_ = evaluate(cand)
        better = l_ < best_loss - 1e-12
        # Equal loss: prefer lower delay, then the smaller (less intrusive) settings.
        key = (s.mean_delay_s or 0.0, cand.pending_steps + cand.keep_firing_steps)
        best_key = (best_score.mean_delay_s or 0.0, best.pending_steps + best.keep_firing_steps)
        tie = abs(l_ - best_loss) <= 1e-12 and key < best_key
        if better or tie:
            best, best_score, best_loss = cand, s, l_
    return TuneResult(best, best_score, best_loss, base_score, base_loss, trials + 1)
