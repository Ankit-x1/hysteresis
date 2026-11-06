"""Load a deliberately narrow, safe subset of Prometheus alerting rules.

v0.1 accepts a bare metric selector and a numeric comparison:
``<metric_name> <op> <number>`` where op is >, >=, <, or <=. Label matchers,
functions, aggregations, vector matching and compound expressions are rejected.
The metric name must match the metric stored in the benchmark.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .sim import Rule

_UNITS = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800, "y": 31536000}
_DUR_PART = re.compile(r"(\d+)(ms|s|m|h|d|w|y)")
_METRIC = r"[a-zA-Z_:][a-zA-Z0-9_:]*"
_NUMBER = r"-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_CMP = re.compile(rf"^\s*(?P<sel>{_METRIC})\s*(?P<op>>=|<=|>|<)\s*(?P<num>{_NUMBER})\s*$")


@dataclass
class ParsedRule:
    group: str
    alert: str
    expr: str
    for_s: float
    keep_firing_for_s: float
    rule: Rule | None
    reason: str = ""
    selector: str | None = None


def parse_duration(text: str | None) -> float:
    """Parse a Prometheus duration such as '30s' or '1h30m'. Missing -> 0."""
    if text is None:
        return 0.0
    if not isinstance(text, str):
        raise TypeError(f"duration must be a string with units, got {text!r}")
    text = text.strip()
    if text in ("", "0"):
        return 0.0
    pos, total = 0, 0.0
    for match in _DUR_PART.finditer(text):
        if match.start() != pos:
            raise ValueError(f"bad duration {text!r}")
        total += int(match.group(1)) * _UNITS[match.group(2)]
        pos = match.end()
    if pos != len(text) or not math.isfinite(total):
        raise ValueError(f"bad duration {text!r}")
    return total


def format_duration(seconds: float) -> str:
    """Format non-negative seconds as a compact Prometheus duration."""
    if not math.isfinite(float(seconds)) or seconds < 0:
        raise ValueError("duration must be a finite non-negative number")
    remaining_ms = round(seconds * 1000)
    if remaining_ms <= 0:
        return "0s"
    parts = []
    units = (
        ("y", 31_536_000_000),
        ("w", 604_800_000),
        ("d", 86_400_000),
        ("h", 3_600_000),
        ("m", 60_000),
        ("s", 1_000),
        ("ms", 1),
    )
    for unit, size in units:
        count, remaining_ms = divmod(remaining_ms, size)
        if count:
            parts.append(f"{count}{unit}")
    return "".join(parts)


def _simple_threshold(expr: str) -> tuple[str, str, float] | None:
    match = _CMP.fullmatch(expr)
    if not match:
        return None
    number = float(match.group("num"))
    if not math.isfinite(number):
        return None
    return match.group("sel"), match.group("op"), number


def steps_for(seconds: float, step_s: float) -> int:
    """Convert a non-negative duration to evaluation steps, rounding upward."""
    if not math.isfinite(float(seconds)) or seconds < 0:
        raise ValueError("duration must be finite and non-negative")
    if not math.isfinite(float(step_s)) or step_s <= 0:
        raise ValueError("evaluation interval must be finite and greater than zero")
    return math.ceil(seconds / step_s - 1e-12)


def load_rules(path: str | Path, step_s: float) -> list[ParsedRule]:
    steps_for(0, step_s)  # Validate the evaluation interval even for an empty file.
    try:
        with open(path, encoding="utf-8") as fh:
            data: Any = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML in rule file {path!s}: {exc}") from exc
    if data is None:
        raise ValueError(f"rule file {path!s} is empty")
    if not isinstance(data, dict):
        raise TypeError("rule file root must be a YAML mapping")
    if "groups" in data:
        groups = data["groups"]
    else:
        spec = data.get("spec", {})
        if not isinstance(spec, dict):
            raise TypeError("Kubernetes PrometheusRule 'spec' must be a mapping")
        groups = spec.get("groups", [])
    if not isinstance(groups, list):
        raise TypeError("rule file 'groups' must be a list")

    out: list[ParsedRule] = []
    seen_alerts: set[str] = set()
    for group_index, group in enumerate(groups):
        if not isinstance(group, dict):
            raise TypeError(f"group at index {group_index} must be a mapping")
        rules = group.get("rules", [])
        if not isinstance(rules, list):
            raise TypeError(f"rules in group {group.get('name', group_index)!r} must be a list")
        for rule_index, raw_rule in enumerate(rules):
            if not isinstance(raw_rule, dict):
                raise TypeError(
                    f"rule at index {rule_index} in group {group_index} must be a mapping"
                )
            if "alert" not in raw_rule:
                continue  # Recording rule.
            alert = raw_rule["alert"]
            if not isinstance(alert, str):
                raise TypeError(f"alert name at index {rule_index} must be a string")
            if not alert.strip():
                raise ValueError(f"alert name at index {rule_index} must be a non-empty string")
            alert = alert.strip()
            if alert in seen_alerts:
                raise ValueError(f"duplicate alert name {alert!r} in rule file {path!s}")
            seen_alerts.add(alert)
            expr_value = raw_rule.get("expr", "")
            if not isinstance(expr_value, str):
                raise TypeError(f"expression for alert {alert!r} must be a string")
            expr = expr_value.strip()
            for_s = parse_duration(raw_rule.get("for"))
            keep_s = parse_duration(raw_rule.get("keep_firing_for"))
            parsed = _simple_threshold(expr)
            group_name = str(group.get("name", ""))
            if parsed is None:
                out.append(
                    ParsedRule(
                        group_name,
                        alert,
                        expr,
                        for_s,
                        keep_s,
                        None,
                        "only a bare metric selector followed by >, >=, <, or <= "
                        "and a number is supported",
                    )
                )
                continue
            selector, op, number = parsed
            rule = Rule(
                threshold=number,
                direction="above" if op in (">", ">=") else "below",
                inclusive=op in (">=", "<="),
                pending_steps=steps_for(for_s, step_s),
                keep_firing_steps=steps_for(keep_s, step_s),
            )
            out.append(
                ParsedRule(
                    group_name,
                    alert,
                    expr,
                    for_s,
                    keep_s,
                    rule,
                    selector=selector,
                )
            )
    return out
