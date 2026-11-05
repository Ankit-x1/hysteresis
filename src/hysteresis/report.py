"""Markdown output for scores and tuning results."""

from __future__ import annotations

from .rules import format_duration
from .score import Score
from .sim import Rule


def _metric(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _delay(score: Score) -> str:
    return "n/a" if score.mean_delay_s is None else f"{score.mean_delay_s:.0f}s"


def table(rows: list[tuple[str, Score]]) -> str:
    head = (
        "| Setting | Missed | Extra alert episodes | Episodes/day | Mean delay | "
        "Precision | Recall |\n"
        "|---|---|---|---|---|---|---|\n"
    )
    body = "".join(
        f"| {name} | {item.missed}/{item.incidents} | {item.extra_episodes} "
        f"(false {item.false_episodes}, repeat {item.redundant_episodes}) | "
        f"{item.episodes_per_day:.1f} | {_delay(item)} | "
        f"{_metric(item.precision)} | {_metric(item.recall)} |\n"
        for name, item in rows
    )
    return head + body


def suggestion(alert: str, rule: Rule, step_s: float) -> str:
    lines = [f"Suggested settings for `{alert}`:"]
    lines.append(f"  for: {format_duration(rule.pending_steps * step_s)}")
    lines.append(f"  keep_firing_for: {format_duration(rule.keep_firing_steps * step_s)}")
    if rule.clear_threshold is not None:
        lines.append(
            f"  recovery threshold: {rule.clear_threshold:.2f} "
            "(Hysteresis simulation setting; not emitted as a Prometheus rule field)"
        )
    return "\n".join(lines)
