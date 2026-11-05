"""Deterministic synthetic benchmark generation and storage.

A benchmark contains exactly one metric series. Rules evaluated against it must
reference ``metric_name``; this prevents unrelated PromQL expressions from
being scored against the same array by accident.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re

import numpy as np

_METRIC_NAME = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")


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
    metric_name: str = "cpu_usage"

    def __post_init__(self) -> None:
        values = np.asarray(self.values, dtype=float)
        if values.ndim != 1:
            raise ValueError("benchmark values must be a one-dimensional array")
        if values.size == 0:
            raise ValueError("benchmark must contain at least one sample")
        if np.isinf(values).any():
            raise ValueError("benchmark values may contain NaN for missing data, but not infinity")
        try:
            step_s = float(self.step_s)
        except (TypeError, ValueError) as exc:
            raise ValueError("step_s must be a finite number greater than zero") from exc
        if not math.isfinite(step_s) or step_s <= 0:
            raise ValueError("step_s must be a finite number greater than zero")
        if not isinstance(self.seed, (int, np.integer)) or isinstance(self.seed, bool):
            raise ValueError("seed must be an integer")
        if not isinstance(self.metric_name, str) or not _METRIC_NAME.fullmatch(self.metric_name):
            raise ValueError("metric_name must be a valid Prometheus metric name")

        previous_end = 0
        for index, incident in enumerate(self.incidents):
            if not isinstance(incident.start, (int, np.integer)) or not isinstance(
                incident.end, (int, np.integer)
            ):
                raise ValueError(f"incident {index} boundaries must be integers")
            if not (0 <= incident.start < incident.end <= values.size):
                raise ValueError(
                    f"incident {index} must satisfy 0 <= start < end <= {values.size}"
                )
            if index and incident.start < previous_end:
                raise ValueError("incident windows must be sorted and non-overlapping")
            previous_end = incident.end

        self.values = values
        self.step_s = step_s
        self.seed = int(self.seed)
        self.incidents = list(self.incidents)

    @property
    def days(self) -> float:
        return len(self.values) * self.step_s / 86400

    def slice(self, a: int, b: int) -> Bench:
        if not (0 <= a <= b <= len(self.values)):
            raise ValueError("slice bounds must satisfy 0 <= start <= end <= sample count")
        inc = [
            Incident(i.start - a, i.end - a)
            for i in self.incidents
            if i.start >= a and i.end <= b
        ]
        return Bench(self.values[a:b].copy(), self.step_s, inc, self.seed, self.metric_name)

    def split(self, frac: float = 0.5) -> tuple[Bench, Bench]:
        """Chronologically split without cutting an incident in half."""
        if not math.isfinite(frac) or not 0 < frac < 1:
            raise ValueError("split fraction must be strictly between 0 and 1")
        idx = int(len(self.values) * frac)
        for incident in self.incidents:
            if incident.start < idx < incident.end:
                boundaries = [
                    boundary
                    for boundary in (incident.start, incident.end)
                    if 0 < boundary < len(self.values)
                ]
                if not boundaries:
                    raise ValueError(
                        "an incident spans the full benchmark; "
                        "no non-empty incident-safe split exists"
                    )
                idx = min(boundaries, key=lambda boundary: abs(boundary - idx))
                break
        if idx == 0 or idx == len(self.values):
            raise ValueError("incident-safe split would produce an empty train or test partition")
        return self.slice(0, idx), self.slice(idx, len(self.values))

    def save(self, path: str) -> None:
        np.savez_compressed(
            path,
            values=self.values,
            step_s=self.step_s,
            starts=np.array([i.start for i in self.incidents], dtype=np.int64),
            ends=np.array([i.end for i in self.incidents], dtype=np.int64),
            seed=self.seed,
            metric_name=np.array(self.metric_name),
            schema_version=np.array(1, dtype=np.int64),
        )

    @staticmethod
    def load(path: str) -> Bench:
        try:
            with np.load(path, allow_pickle=False) as data:
                required = {"values", "step_s", "starts", "ends", "seed"}
                missing = required.difference(data.files)
                if missing:
                    missing_fields = ", ".join(sorted(missing))
                    raise ValueError(f"benchmark archive is missing fields: {missing_fields}")
                starts = np.asarray(data["starts"])
                ends = np.asarray(data["ends"])
                if (
                    starts.ndim != 1
                    or ends.ndim != 1
                    or len(starts) != len(ends)
                    or starts.dtype.kind not in "iu"
                    or ends.dtype.kind not in "iu"
                ):
                    raise ValueError(
                        "benchmark incident start/end arrays must be matching integer vectors"
                    )
                incidents = [
                    Incident(int(start), int(end))
                    for start, end in zip(starts, ends, strict=True)
                ]
                schema_version = (
                    int(np.asarray(data["schema_version"]).item())
                    if "schema_version" in data.files
                    else 0
                )
                if schema_version not in (0, 1):
                    raise ValueError(f"unsupported benchmark schema version: {schema_version}")
                seed_value = np.asarray(data["seed"]).item()
                if not isinstance(seed_value, (int, np.integer)) or isinstance(seed_value, bool):
                    raise ValueError("benchmark seed must be an integer")
                metric_name = (
                    str(np.asarray(data["metric_name"]).item())
                    if "metric_name" in data.files
                    else "cpu_usage"
                )
                bench = Bench(
                    values=np.asarray(data["values"]),
                    step_s=float(np.asarray(data["step_s"]).item()),
                    incidents=incidents,
                    seed=int(seed_value),
                    metric_name=metric_name,
                )
        except (OSError, KeyError, TypeError, ValueError, EOFError) as exc:
            if isinstance(exc, ValueError) and str(exc).startswith("benchmark "):
                raise
            raise ValueError(f"could not load benchmark {path!r}: {exc}") from exc
        return bench


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
    metric_name: str = "cpu_usage",
) -> Bench:
    """Generate a seeded seasonal signal with autocorrelated noise and incidents."""
    if not isinstance(metric_name, str) or not _METRIC_NAME.fullmatch(metric_name):
        raise ValueError("metric_name must be a valid Prometheus metric name")
    finite_values = {
        "days": days,
        "step_s": step_s,
        "base": base,
        "daily_amp": daily_amp,
        "noise_sd": noise_sd,
        "ar": ar,
        "incidents_per_day": incidents_per_day,
    }
    for name, value in finite_values.items():
        if not math.isfinite(float(value)):
            raise ValueError(f"{name} must be finite")
    if days <= 0:
        raise ValueError("days must be greater than zero")
    if step_s <= 0:
        raise ValueError("step_s must be greater than zero")
    if daily_amp < 0 or noise_sd < 0:
        raise ValueError("daily_amp and noise_sd must be non-negative")
    if not -1 < ar < 1:
        raise ValueError("ar must be strictly between -1 and 1")
    if incidents_per_day <= 0:
        raise ValueError("incidents_per_day must be greater than zero")
    if not isinstance(seed, (int, np.integer)) or isinstance(seed, bool):
        raise ValueError("seed must be an integer")
    seed = int(seed)
    if not isinstance(ramp_steps, int) or isinstance(ramp_steps, bool) or ramp_steps < 1:
        raise ValueError("ramp_steps must be an integer greater than or equal to one")
    if len(shift_range) != 2 or not all(math.isfinite(float(v)) for v in shift_range):
        raise ValueError("shift_range must contain two finite numbers")
    if shift_range[0] < 0 or shift_range[1] < shift_range[0]:
        raise ValueError("shift_range must satisfy 0 <= minimum <= maximum")
    if len(duration_range_s) != 2 or not all(math.isfinite(float(v)) for v in duration_range_s):
        raise ValueError("duration_range_s must contain two finite numbers")
    if duration_range_s[0] <= 0 or duration_range_s[1] < duration_range_s[0]:
        raise ValueError("duration_range_s must satisfy 0 < minimum <= maximum")

    n_float = days * 86400 / step_s
    if not math.isfinite(n_float) or n_float < 2 or n_float > 5_000_000:
        raise ValueError("requested benchmark must contain between 2 and 5,000,000 samples")
    n = int(n_float)
    rng = np.random.default_rng(seed)
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
        dur = max(1, int(np.ceil(rng.uniform(*duration_range_s) / step_s)))
        margin = slot // 10
        if slot <= dur + 2 * margin:
            raise ValueError("incidents too dense or too long for this benchmark length")
        start = k * slot + int(rng.integers(margin, slot - dur - margin))
        shift = rng.uniform(*shift_range)
        ramp = np.clip((np.arange(dur) + 1) / ramp_steps, 0.0, 1.0)
        values[start : start + dur] += shift * ramp
        incidents.append(Incident(start, start + dur))
    return Bench(values, step_s, incidents, seed, metric_name)
