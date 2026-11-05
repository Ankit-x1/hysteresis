"""Repeat the tune-then-evaluate experiment over many benchmark seeds.

One seed is an anecdote. This reports totals over held-out halves across seeds,
so the README claim is "across N seeds", not "on one lucky run".

    python tools/seed_sweep.py --seeds 20 --threshold 70
"""

from __future__ import annotations

import argparse
import dataclasses

from hysteresis.bench import generate
from hysteresis.rules import steps_for
from hysteresis.score import score
from hysteresis.sim import Rule, simulate
from hysteresis.tune import tune


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--threshold", type=float, default=70.0)
    ap.add_argument("--days", type=float, default=14)
    ap.add_argument("--trials", type=int, default=200)
    ap.add_argument("--noise-sd", type=float, default=5.0)
    a = ap.parse_args()

    names = ["No pending", "Typical (for 5m)", "Tuned"]
    tot = {n: {"missed": 0, "incidents": 0, "extra": 0, "delay": []} for n in names}
    for seed in range(a.seeds):
        b = generate(seed=seed, days=a.days, noise_sd=a.noise_sd)
        train, test = b.split(0.5)
        base = Rule(a.threshold)
        tuned = tune(train, base, trials=a.trials, seed=seed).rule
        typical = dataclasses.replace(base, pending_steps=steps_for(300, b.step_s))
        for n, r in zip(names, [base, typical, tuned]):
            s = score(simulate(test.values, r), test)
            t = tot[n]
            t["missed"] += s.missed
            t["incidents"] += s.incidents
            t["extra"] += s.extra_pages
            if s.mean_delay_s is not None:
                t["delay"].append(s.mean_delay_s)

    print(f"Held-out totals over {a.seeds} seeds (threshold {a.threshold:g}):\n")
    print("| Setting | Missed | Extra alert episodes | Mean delay |\n|---|---|---|---|")
    for n in names:
        t = tot[n]
        d = sum(t["delay"]) / len(t["delay"]) if t["delay"] else float("nan")
        print(f"| {n} | {t['missed']}/{t['incidents']} | {t['extra']} | {d:.0f}s |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
