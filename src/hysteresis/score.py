"""Score an alert's behavior against labeled incidents.

Definitions (all computed from the state array):
  episode            contiguous run of FIRING/RECOVERING = one fire + one resolve page.
  detected incident  at least one FIRING step inside the incident window.
  delay              steps from incident start to first FIRING step, in seconds.
  false episode      an episode with no FIRING step inside any incident window.
  redundant episode  a second or later episode attached to an incident that already
                     had one (the flapping you get paged for twice).
  extra pages        false + redundant episodes: pages that carried no new information.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from .bench import Bench
from .sim import FIRING, episodes


@dataclass
class Score:
    incidents: int
    detected: int
    missed: int
    episodes: int
    false_episodes: int
    redundant_episodes: int
    extra_pages: int
    mean_delay_s: float | None
    flaps_per_day: float
    notifications: int
    precision: float
    recall: float

    def as_dict(self) -> dict:
        return asdict(self)


def score(states: np.ndarray, bench: Bench) -> Score:
    fire = (states == FIRING).astype(np.int64)
    cum = np.concatenate([[0], np.cumsum(fire)])
    eps = episodes(states)

    def firing_in(lo: int, hi: int) -> int:
        return int(cum[hi] - cum[lo]) if hi > lo else 0

    delays: list[float] = []
    detected = 0
    for inc in bench.incidents:
        if firing_in(inc.start, inc.end) > 0:
            detected += 1
            first = inc.start + int(np.argmax(fire[inc.start : inc.end]))
            delays.append((first - inc.start) * bench.step_s)

    seen = [False] * len(bench.incidents)
    false_eps = redundant = 0
    for s, e in eps:
        hit = None
        for k, inc in enumerate(bench.incidents):
            if firing_in(max(s, inc.start), min(e, inc.end)) > 0:
                hit = k
                break
        if hit is None:
            false_eps += 1
        elif seen[hit]:
            redundant += 1
        else:
            seen[hit] = True

    n_inc, n_eps = len(bench.incidents), len(eps)
    extra = false_eps + redundant
    return Score(
        incidents=n_inc,
        detected=detected,
        missed=n_inc - detected,
        episodes=n_eps,
        false_episodes=false_eps,
        redundant_episodes=redundant,
        extra_pages=extra,
        mean_delay_s=float(np.mean(delays)) if delays else None,
        flaps_per_day=n_eps / bench.days if bench.days else 0.0,
        notifications=2 * n_eps,
        precision=(n_eps - extra) / n_eps if n_eps else 1.0,
        recall=detected / n_inc if n_inc else 1.0,
    )
