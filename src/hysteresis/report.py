"""Markdown output for scores and tuning results."""

from __future__ import annotations

from .rules import format_duration
from .score import Score
from .sim import Rule


def _delay(s: Score) -> str:
    return "n/a" if s.mean_delay_s is None else f"{s.mean_delay_s:.0f}s"


def table(rows: list[tuple[str, Score]]) -> str:
    head = (
        "| Setting | Missed | Extra pages | Pages/day | Mean delay | Precision | Recall |\n"
        "|---|---|---|---|---|---|---|\n"
    )
    body = "".join(
        f"| {name} | {s.missed}/{s.incidents} | {s.extra_pages} "
        f"(false {s.false_episodes}, repeat {s.redundant_episodes}) | {s.flaps_per_day:.1f} | "
        f"{_delay(s)} | {s.precision:.2f} | {s.recall:.2f} |\n"
        for name, s in rows
    )
    return head + body


def suggestion(alert: str, rule: Rule, step_s: float) -> str:
    lines = [f"Suggested settings for `{alert}`:"]
    lines.append(f"  for: {format_duration(rule.pending_steps * step_s)}")
    lines.append(f"  keep_firing_for: {format_duration(rule.keep_firing_steps * step_s)}")
    if rule.clear_threshold is not None:
        lines.append(
            f"  recovery threshold: {rule.clear_threshold:.2f} "
            "(Grafana-managed rules only; Prometheus rule files have no recovery threshold)"
        )
    return "\n".join(lines)
