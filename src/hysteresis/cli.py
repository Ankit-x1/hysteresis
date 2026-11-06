"""Command-line interface: generate, score, tune and gate alert rules."""

from __future__ import annotations

import argparse
import dataclasses
import math
import os
import sys

import yaml

from .bench import Bench, generate
from .report import suggestion, table
from .rules import ParsedRule, load_rules, steps_for
from .score import score
from .sim import simulate
from .tune import tune


class EvaluationError(ValueError):
    """Input cannot be evaluated safely with the current Hysteresis contract."""


def _supported(path: str, bench: Bench, only: str | None) -> list[ParsedRule]:
    parsed = load_rules(path, bench.step_s)
    alerts = [rule for rule in parsed]
    if not alerts:
        raise EvaluationError("rule file contains no alerting rules")
    selected = [rule for rule in alerts if only is None or rule.alert == only]
    if not selected:
        if only:
            raise EvaluationError(f"alert {only!r} was not found in {path!r}")
        raise EvaluationError("no alerting rules were selected")

    unsupported = [rule for rule in selected if rule.rule is None]
    if unsupported:
        descriptions = "; ".join(f"{r.alert}: {r.reason}" for r in unsupported)
        raise EvaluationError(
            f"refusing partial evaluation; unsupported alert rule(s): {descriptions}"
        )

    mismatched = [rule for rule in selected if rule.selector != bench.metric_name]
    if mismatched:
        descriptions = "; ".join(
            f"{r.alert} references {r.selector!r}, but benchmark contains {bench.metric_name!r}"
            for r in mismatched
        )
        raise EvaluationError(f"metric selector does not match benchmark: {descriptions}")
    return selected


def cmd_bench(args: argparse.Namespace) -> int:
    bench = generate(
        seed=args.seed,
        days=args.days,
        step_s=args.step,
        metric_name=args.metric_name,
    )
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    bench.save(args.out)
    print(
        f"wrote {args.out}: {len(bench.values)} samples, {len(bench.incidents)} incidents, "
        f"metric {bench.metric_name!r}, seed {args.seed}"
    )
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    bench = Bench.load(args.bench)
    for parsed in _supported(args.rules, bench, args.alert):
        result = score(simulate(bench.values, parsed.rule), bench)
        print(
            f"### {parsed.alert}\n\n`{parsed.expr}` for {parsed.for_s:.0f}s\n\n"
            f"{table([('As written', result)])}"
        )
    return 0


def cmd_tune(args: argparse.Namespace) -> int:
    if not 0 < args.train_frac < 1:
        raise EvaluationError("--train-frac must be strictly between 0 and 1")
    if args.max_delay < 0 or not math.isfinite(args.max_delay):
        raise EvaluationError("--max-delay must be finite and non-negative")
    bench = Bench.load(args.bench)
    selected = _supported(args.rules, bench, args.alert)
    train, test = bench.split(args.train_frac)
    if not train.incidents or not test.incidents:
        raise EvaluationError(
            "tuning needs labeled incidents in both train and held-out partitions; "
            "use a longer benchmark or adjust --train-frac"
        )
    print(
        f"Tuned on first {train.days:.2f} days, reported on held-out {test.days:.2f} days "
        f"(seed {bench.seed}).\n"
    )
    for parsed in selected:
        result = tune(
            train,
            parsed.rule,
            trials=args.trials,
            seed=args.seed,
            allow_band=args.allow_band,
            max_delay_s=args.max_delay,
            max_pending_s=args.max_pending,
            max_keep_s=args.max_keep,
            w_missed=args.weight_missed,
            w_extra=args.weight_extra,
            w_delay=args.weight_delay,
        )
        typical = dataclasses.replace(parsed.rule, pending_steps=steps_for(300, bench.step_s))
        no_pending = dataclasses.replace(
            parsed.rule, pending_steps=0, keep_firing_steps=0, clear_threshold=None
        )
        rows = [
            ("No pending", score(simulate(test.values, no_pending), test)),
            ("As written", score(simulate(test.values, parsed.rule), test)),
            ("Typical (for 5m)", score(simulate(test.values, typical), test)),
            ("Tuned", score(simulate(test.values, result.rule), test)),
        ]
        print(
            f"### {parsed.alert}\n\n{table(rows)}\n"
            f"Training loss: {result.base_loss:.3f} -> {result.loss:.3f} "
            f"({result.trials} candidates evaluated)\n\n"
            f"{suggestion(parsed.alert, result.rule, bench.step_s)}\n"
        )
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    for name in ("max_missed", "max_extra_episodes"):
        if getattr(args, name) < 0:
            raise EvaluationError(f"--{name.replace('_', '-')} must be non-negative")
    if args.max_delay is not None and (args.max_delay < 0 or not math.isfinite(args.max_delay)):
        raise EvaluationError("--max-delay must be finite and non-negative")

    bench = Bench.load(args.bench)
    selected = _supported(args.rules, bench, args.alert)
    failed = False
    for parsed in selected:
        result = score(simulate(bench.values, parsed.rule), bench)
        problems = []
        if result.missed > args.max_missed:
            problems.append(f"missed {result.missed} > {args.max_missed}")
        if result.extra_episodes > args.max_extra_episodes:
            problems.append(
                f"extra alert episodes {result.extra_episodes} > {args.max_extra_episodes}"
            )
        if args.max_delay is not None:
            if result.mean_delay_s is None and result.incidents:
                problems.append("no incidents were detected; mean detection delay is undefined")
            elif result.mean_delay_s is not None and result.mean_delay_s > args.max_delay:
                problems.append(f"mean delay {result.mean_delay_s:.0f}s > {args.max_delay:.0f}s")
        status = "FAIL: " + "; ".join(problems) if problems else "PASS"
        print(f"### {parsed.alert}: {status}\n\n{table([('As written', result)])}")
        failed = failed or bool(problems)
    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hysteresis", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    bench_parser = sub.add_parser("bench", help="generate a seeded synthetic benchmark")
    bench_parser.add_argument("--out", default="bench/default.npz")
    bench_parser.add_argument("--seed", type=int, default=0)
    bench_parser.add_argument("--days", type=float, default=7)
    bench_parser.add_argument(
        "--step", type=float, default=15, help="evaluation interval in seconds"
    )
    bench_parser.add_argument("--metric-name", default="cpu_usage")
    bench_parser.set_defaults(fn=cmd_bench)

    def add_common(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("rules", help="Prometheus alert rule file")
        subparser.add_argument("--bench", required=True, help="Hysteresis benchmark .npz file")
        subparser.add_argument("--alert", help="evaluate only this alert name")

    score_parser = sub.add_parser("score", help="score supported rules against labeled history")
    add_common(score_parser)
    score_parser.set_defaults(fn=cmd_score)

    tune_parser = sub.add_parser("tune", help="search pending/keep-firing/recovery settings")
    add_common(tune_parser)
    tune_parser.add_argument("--trials", type=int, default=300)
    tune_parser.add_argument("--seed", type=int, default=0)
    tune_parser.add_argument("--allow-band", action="store_true")
    tune_parser.add_argument("--max-delay", type=float, default=300)
    tune_parser.add_argument("--max-pending", type=float, default=1800)
    tune_parser.add_argument("--max-keep", type=float, default=3600)
    tune_parser.add_argument("--train-frac", type=float, default=0.5)
    tune_parser.add_argument("--weight-missed", type=float, default=10)
    tune_parser.add_argument("--weight-extra", type=float, default=1)
    tune_parser.add_argument("--weight-delay", type=float, default=1)
    tune_parser.set_defaults(fn=cmd_tune)

    check_parser = sub.add_parser(
        "check", help="fail CI when evaluation is invalid or quality budgets are exceeded"
    )
    add_common(check_parser)
    check_parser.add_argument("--max-missed", type=int, default=0)
    check_parser.add_argument(
        "--max-extra-episodes",
        "--max-extra-pages",
        dest="max_extra_episodes",
        type=int,
        default=10,
        help="maximum false plus redundant alert episodes (legacy alias: --max-extra-pages)",
    )
    check_parser.add_argument("--max-delay", type=float, default=None)
    check_parser.set_defaults(fn=cmd_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except (OSError, ValueError, TypeError, KeyError, EOFError, yaml.YAMLError) as exc:
        print(f"hysteresis: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
