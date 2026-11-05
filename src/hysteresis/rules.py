"""Load Prometheus-style alerting rule files into simulator rules.

v0.1 supports one expression shape: `<any expression> <op> <number>` where op is
one of > >= < <=. Anything else is reported as unsupported, never guessed.
Accepts plain rule files (`groups:`) and Kubernetes PrometheusRule (`spec.groups`).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import yaml

from .sim import Rule

_UNITS = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800, "y": 31536000}
_DUR_PART = re.compile(r"(\d+)(ms|s|m|h|d|w|y)")
_CMP = re.compile(r"^(?P<sel>.+?)\s*(?P<op>>=|<=|>|<)\s*(?P<num>-?\d+(?:\.\d+)?)\s*$", re.DOTALL)
_BAD_WORDS = re.compile(r"\b(and|or|unless|bool|on|ignoring|group_left|group_right)\b")


@dataclass
class ParsedRule:
    group: str
    alert: str
    expr: str
    for_s: float
    keep_firing_for_s: float
    rule: Rule | None  # None when unsupported
    reason: str = ""


def parse_duration(text: str | float | None) -> float:
    """Prometheus duration ('30s', '5m', '1h30m') to seconds. Missing -> 0."""
    if text is None:
        return 0.0
    if not isinstance(text, str):
        raise TypeError(f"duration must be a string with units, got {text!r}")
    text = text.strip()
    if text in ("", "0"):
        return 0.0
    pos, total = 0, 0.0
    for m in _DUR_PART.finditer(text):
        if m.start() != pos:
            raise ValueError(f"bad duration {text!r}")
        total += int(m.group(1)) * _UNITS[m.group(2)]
        pos = m.end()
    if pos != len(text):
        raise ValueError(f"bad duration {text!r}")
    return total


def format_duration(seconds: float) -> str:
    """Seconds to a compact Prometheus duration, e.g. 90 -> '1m30s'."""
    s = round(seconds)
    if s <= 0:
        return "0s"
    parts = []
    for unit, size in (("h", 3600), ("m", 60), ("s", 1)):
        q, s = divmod(s, size)
        if q:
            parts.append(f"{q}{unit}")
    return "".join(parts)


def _simple_threshold(expr: str) -> tuple[str, str, float] | None:
    m = _CMP.match(expr.strip())
    if not m:
        return None
    sel = m.group("sel")
    stripped = re.sub(r'"[^"]*"', '""', re.sub(r"\{[^}]*\}", "{}", sel))
    if re.search(r"[<>=!]", stripped) or _BAD_WORDS.search(stripped):
        return None
    return sel, m.group("op"), float(m.group("num"))


def steps_for(seconds: float, step_s: float) -> int:
    """Evaluation steps needed to cover a duration (ceil: Prometheus uses >=)."""
    return math.ceil(seconds / step_s - 1e-9)


def load_rules(path: str, step_s: float) -> list[ParsedRule]:
    with open(path) as fh:
        data = yaml.safe_load(fh) or {}
    groups = data.get("groups") or (data.get("spec") or {}).get("groups") or []
    out: list[ParsedRule] = []
    for g in groups:
        for r in g.get("rules", []) or []:
            if "alert" not in r:
                continue  # recording rule
            expr = str(r.get("expr", ""))
            for_s = parse_duration(r.get("for"))
            keep_s = parse_duration(r.get("keep_firing_for"))
            parsed = _simple_threshold(expr)
            if parsed is None:
                out.append(
                    ParsedRule(
                        g.get("name", ""),
                        r["alert"],
                        expr,
                        for_s,
                        keep_s,
                        None,
                        "expression is not '<expr> <op> <number>'",
                    )
                )
                continue
            _, op, num = parsed
            rule = Rule(
                threshold=num,
                direction="above" if op in (">", ">=") else "below",
                inclusive=op in (">=", "<="),
                pending_steps=steps_for(for_s, step_s),
                keep_firing_steps=steps_for(keep_s, step_s),
            )
            out.append(ParsedRule(g.get("name", ""), r["alert"], expr, for_s, keep_s, rule))
    return out
