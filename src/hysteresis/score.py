"""Score an alert's behavior against labeled incidents.

An episode is a contiguous run of FIRING/RECOVERING states. A detected
incident must contain at least one FIRING sample. Precision/recall are None
when their denominator is zero rather than being reported as perfect scores.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from .bench import Bench
from .sim import FIRING, NORMAL, PENDING, RECOVERING, episodes


@dataclass
class Score:
    incidents: int
    detected: int
    missed: int
    episodes: int
    false_episodes: int
    redundant_episodes: int
    extra_episodes: int
    mean_delay_s: float | None
    episodes_per_day: float
    notifications: int
    precision: float | None
    recall: float | None

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def extra_pages(self) -> int:
        """Deprecated compatibility alias; extra_pages are counted as episodes."""
        return self.extra_episodes

    @property
    def flaps_per_day(self) -> float:
        """Deprecated compatibility alias; this is episodes/day, not true flaps."""
        return self.episodes_per_day


def score(states: np.ndarray, bench: Bench) -> Score:
    states = np.asarray(states)
    if states.ndim != 1 or len(states) != len(bench.values):
        raise ValueError("state array must be one-dimensional and match benchmark sample count")
    allowed = np.isin(states, (NORMAL, PENDING, FIRING, RECOVERING))
    if not allowed.all():
        raise ValueError("state array contains an unknown alert state")

    fire = (states == FIRING).astype(np.int64)
    cum = np.concatenate([[0], np.cumsum(fire)])
    alert_episodes = episodes(states)

    def firing_in(lo: int, hi: int) -> int:
        lo, hi = max(0, lo), min(len(states), hi)
        return int(cum[hi] - cum[lo]) if hi > lo else 0

    delays: list[float] = []
    detected = 0
    for incident in bench.incidents:
        if firing_in(incident.start, incident.end) > 0:
            detected += 1
            first = incident.start + int(np.argmax(fire[incident.start : incident.end]))
            delays.append((first - incident.start) * bench.step_s)

    seen = [False] * len(bench.incidents)
    false_episodes = redundant_episodes = 0
    for start, end in alert_episodes:
        matching_incident = None
        for index, incident in enumerate(bench.incidents):
            if firing_in(max(start, incident.start), min(end, incident.end)) > 0:
                matching_incident = index
                break
        if matching_incident is None:
            false_episodes += 1
        elif seen[matching_incident]:
            redundant_episodes += 1
        else:
            seen[matching_incident] = True

    incident_count = len(bench.incidents)
    episode_count = len(alert_episodes)
    extra = false_episodes + redundant_episodes
    return Score(
        incidents=incident_count,
        detected=detected,
        missed=incident_count - detected,
        episodes=episode_count,
        false_episodes=false_episodes,
        redundant_episodes=redundant_episodes,
        extra_episodes=extra,
        mean_delay_s=float(np.mean(delays)) if delays else None,
        episodes_per_day=episode_count / bench.days,
        notifications=2 * episode_count,
        precision=(episode_count - extra) / episode_count if episode_count else None,
        recall=detected / incident_count if incident_count else None,
    )
