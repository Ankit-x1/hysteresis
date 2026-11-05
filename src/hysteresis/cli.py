"""hysteresis command line: bench | score | tune | check."""

from __future__ import annotations

import argparse
import dataclasses
import os
import sys

from .bench import Bench, generate
from .report import suggestion, table
from .rules import ParsedRule, load_rules, steps_for
from .score import score
from .sim import simulate
from .tune import tune


def _supported(path: str, step_s: float, only: str | None) -> list[ParsedRule]:
    parsed = load_rules(path, step_s)
    out = []
    for p in parsed:
        if only and p.alert != only:
            continue
        if p.rule is None:
            print(f"- skipped `{p.alert}`: {p.reason}\n")
            continue
        out.append(p)
    if not out:
        print("No supported alert rules found.", file=sys.stderr)
    return out


def cmd_bench(a: argparse.Namespace) -> int:
    b = generate(seed=a.seed, days=a.days, step_s=a.step)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    b.save(a.out)
    print(f"wrote {a.out}: {len(b.values)} samples, {len(b.incidents)} incidents, seed {a.seed}")
    return 0


def cmd_score(a: argparse.Namespace) -> int:
    bench = Bench.load(a.bench)
    for p in _supported(a.rules, bench.step_s, a.alert):
        s = score(simulate(bench.values, p.rule), bench)
        print(f"### {p.alert}\n\n`{p.expr}` for {p.for_s:.0f}s\n\n{table([('As written', s)])}")
    return 0


def cmd_tune(a: argparse.Namespace) -> int:
    bench = Bench.load(a.bench)
    train, test = bench.split(a.train_frac)
    print(
        f"Tuned on first {train.days:.1f} days, reported on held-out {test.days:.1f} days "
        f"(seed {bench.seed}).\n"
    )
    for p in _supported(a.rules, bench.step_s, a.alert):
        res = tune(
            train,
            p.rule,
            trials=a.trials,
            seed=a.seed,
            allow_band=a.allow_band,
            max_delay_s=a.max_delay,
        )
        typical = dataclasses.replace(p.rule, pending_steps=steps_for(300, bench.step_s))
        none = dataclasses.replace(
            p.rule, pending_steps=0, keep_firing_steps=0, clear_threshold=None
        )
        rows = [
            ("No pending", score(simulate(test.values, none), test)),
            ("As written", score(simulate(test.values, p.rule), test)),
            ("Typical (for 5m)", score(simulate(test.values, typical), test)),
            ("Tuned", score(simulate(test.values, res.rule), test)),
        ]
        print(f"### {p.alert}\n\n{table(rows)}\n{suggestion(p.alert, res.rule, bench.step_s)}\n")
    return 0


def cmd_check(a: argparse.Namespace) -> int:
    bench = Bench.load(a.bench)
    failed = False
    for p in _supported(a.rules, bench.step_s, a.alert):
        s = score(simulate(bench.values, p.rule), bench)
        problems = []
        if s.missed > a.max_missed:
            problems.append(f"missed {s.missed} > {a.max_missed}")
        if s.extra_pages > a.max_extra_pages:
            problems.append(f"extra pages {s.extra_pages} > {a.max_extra_pages}")
        if a.max_delay is not None and (s.mean_delay_s or 0) > a.max_delay:
            problems.append(f"mean delay {s.mean_delay_s:.0f}s > {a.max_delay:.0f}s")
        status = "FAIL: " + "; ".join(problems) if problems else "ok"
        print(f"### {p.alert}: {status}\n\n{table([('As written', s)])}")
        failed = failed or bool(problems)
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hysteresis", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("bench", help="generate a seeded synthetic benchmark")
    b.add_argument("--out", default="bench/default.npz")
    b.add_argument("--seed", type=int, default=0)
    b.add_argument("--days", type=float, default=7)
    b.add_argument("--step", type=float, default=15, help="evaluation interval, seconds")
    b.set_defaults(fn=cmd_bench)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("rules", help="Prometheus rule file")
        p.add_argument("--bench", required=True)
        p.add_argument("--alert", help="only this alert name")

    s = sub.add_parser("score", help="score rules against a benchmark")
    common(s)
    s.set_defaults(fn=cmd_score)

    t = sub.add_parser("tune", help="search pending/keep-firing(/band) settings")
    common(t)
    t.add_argument("--trials", type=int, default=300)
    t.add_argument("--seed", type=int, default=0)
    t.add_argument("--allow-band", action="store_true", help="also tune a recovery threshold")
    t.add_argument("--max-delay", type=float, default=300, help="mean detection delay budget, s")
    t.add_argument("--train-frac", type=float, default=0.5)
    t.set_defaults(fn=cmd_tune)

    c = sub.add_parser("check", help="exit 1 if a rule scores worse than the limits")
    common(c)
    c.add_argument("--max-missed", type=int, default=0)
    c.add_argument("--max-extra-pages", type=int, default=10)
    c.add_argument("--max-delay", type=float, default=None)
    c.set_defaults(fn=cmd_check)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
