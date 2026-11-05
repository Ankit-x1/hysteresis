"""Seeded synthetic benchmark: a noisy metric with labeled incidents.

The signal is baseline + daily seasonality + autocorrelated noise, plus
level-shift incidents of varying size. Some incidents sit close to typical
alert thresholds on purpose, because that is where flapping happens.
Every result is reproducible from (seed, parameters).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Incident:
    start: int  # step index, inclusive
    end: int  # step index, exclusive


@dataclass
class Bench:
    values: np.ndarray
    step_s: float
    incidents: list[Incident]
    seed: int = 0

    @property
    def days(self) -> float:
        return len(self.values) * self.step_s / 86400

    def slice(self, a: int, b: int) -> Bench:
        inc = [
            Incident(i.start - a, i.end - a) for i in self.incidents if i.start >= a and i.end <= b
        ]
        return Bench(self.values[a:b].copy(), self.step_s, inc, self.seed)

    def split(self, frac: float = 0.5) -> tuple[Bench, Bench]:
        """Train/test split that never cuts an incident in half."""
        idx = int(len(self.values) * frac)
        for inc in self.incidents:
            if inc.start < idx < inc.end:
                idx = inc.start
        return self.slice(0, idx), self.slice(idx, len(self.values))

    def save(self, path: str) -> None:
        np.savez_compressed(
            path,
            values=self.values,
            step_s=self.step_s,
            starts=np.array([i.start for i in self.incidents], dtype=np.int64),
            ends=np.array([i.end for i in self.incidents], dtype=np.int64),
            seed=self.seed,
        )

    @staticmethod
    def load(path: str) -> Bench:
        z = np.load(path)
        inc = [Incident(int(a), int(b)) for a, b in zip(z["starts"], z["ends"])]
        return Bench(z["values"], float(z["step_s"]), inc, int(z["seed"]))


def generate(
    seed: int = 0,
    days: float = 7,
    step_s: float = 15,
    base: float = 50.0,
    daily_amp: float = 8.0,
    noise_sd: float = 5.0,
    ar: float = 0.9,
    incidents_per_day: float = 1.0,
    shift_range: tuple[float, float] = (20.0, 40.0),
    duration_range_s: tuple[float, float] = (600.0, 2400.0),
    ramp_steps: int = 4,
) -> Bench:
    rng = np.random.default_rng(seed)
    n = int(days * 86400 / step_s)
    t = np.arange(n) * step_s
    z = rng.standard_normal(n)
    noise = np.empty(n)
    noise[0] = noise_sd * z[0]
    innov = noise_sd * np.sqrt(1 - ar**2)
    for i in range(1, n):
        noise[i] = ar * noise[i - 1] + innov * z[i]
    values = base + daily_amp * np.sin(2 * np.pi * t / 86400) + noise

    n_inc = max(1, round(days * incidents_per_day))
    slot = n // n_inc
    incidents: list[Incident] = []
    for k in range(n_inc):
        dur = int(rng.uniform(*duration_range_s) / step_s)
        margin = slot // 10
        if slot < 2 * dur + 2 * margin:
            raise ValueError("incidents too dense for this bench length")
        start = k * slot + int(rng.integers(margin, slot - dur - margin))
        shift = rng.uniform(*shift_range)
        ramp = np.clip((np.arange(dur) + 1) / ramp_steps, 0.0, 1.0)
        values[start : start + dur] += shift * ramp
        incidents.append(Incident(start, start + dur))
    return Bench(values, step_s, incidents, seed)
