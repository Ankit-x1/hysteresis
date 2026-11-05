"""Check the simulator against the real Prometheus engine using promtool.

For random rules and random series, build a `promtool test rules` file whose
expectations come from the simulator, and run promtool. Any mismatch means the
simulator is wrong or Prometheus does something the docs do not say.

    python tools/promtool_fidelity.py --trials 200
    python tools/promtool_fidelity.py --promtool /path/to/promtool
    python tools/promtool_fidelity.py --docker prom/prometheus:v3.15.1

Exit code 0 only if every trial agrees.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import yaml

from hysteresis.sim import FIRING, RECOVERING, Rule, simulate

STEP = 15
LEVELS = [60.0, 80.0, 100.0]  # 80 is exactly the threshold, to probe >= vs >


def make_trial(rng: np.random.Generator, n: int) -> tuple[Rule, str, np.ndarray, str]:
    op = str(rng.choice([">", ">=", "<", "<="]))
    for_steps = int(rng.integers(0, 5))
    keep_steps = int(rng.integers(0, 5))
    rule = Rule(
        threshold=80.0,
        direction="above" if op in (">", ">=") else "below",
        inclusive=op in (">=", "<="),
        pending_steps=for_steps,
        keep_firing_steps=keep_steps,
    )
    vals = np.empty(n)
    toks: list[str] = []
    cur = rng.choice(LEVELS)
    for i in range(n):
        if rng.random() < 0.35:
            cur = rng.choice(LEVELS)
        if rng.random() < 0.05:  # staleness marker: series is absent at this step
            vals[i] = np.nan
            toks.append("stale")
        else:
            vals[i] = cur
            toks.append(f"{cur:g}")
    return rule, op, vals, " ".join(toks)


def write_case(d: Path, rule: Rule, op: str, vals: np.ndarray, series: str) -> None:
    alert: dict = {"alert": "Fid", "expr": f"x {op} 80"}
    if rule.pending_steps:
        alert["for"] = f"{rule.pending_steps * STEP}s"
    if rule.keep_firing_steps:
        alert["keep_firing_for"] = f"{rule.keep_firing_steps * STEP}s"
    (d / "rules.yml").write_text(yaml.safe_dump({"groups": [{"name": "g", "rules": [alert]}]}))

    states = simulate(vals, rule)
    checks = []
    for i, st in enumerate(states):
        firing = st in (FIRING, RECOVERING)
        checks.append(
            {
                "eval_time": f"{i * STEP}s",
                "alertname": "Fid",
                "exp_alerts": [{"exp_labels": {"k": "v"}}] if firing else [],
            }
        )
    test = {
        "rule_files": ["rules.yml"],
        "evaluation_interval": f"{STEP}s",
        "tests": [
            {
                "interval": f"{STEP}s",
                "input_series": [{"series": 'x{k="v"}', "values": series}],
                "alert_rule_test": checks,
            }
        ],
    }
    (d / "test.yml").write_text(yaml.safe_dump(test, sort_keys=False))


def run_promtool(cmd: list[str], d: Path, docker: str | None) -> subprocess.CompletedProcess:
    if docker:
        full = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{d}:/t",
            "-w",
            "/t",
            "--entrypoint",
            "promtool",
            docker,
            "test",
            "rules",
            "test.yml",
        ]
    else:
        full = [*cmd, "test", "rules", "test.yml"]
    return subprocess.run(full, cwd=d, capture_output=True, text=True, check=False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=100)
    ap.add_argument("--length", type=int, default=40)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--promtool", default=shutil.which("promtool"))
    ap.add_argument("--docker", help="image to use instead of a local promtool")
    a = ap.parse_args()
    if not a.promtool and not a.docker:
        print("need --promtool PATH or --docker IMAGE", file=sys.stderr)
        return 2

    rng = np.random.default_rng(a.seed)
    bad = 0
    for t in range(a.trials):
        rule, op, vals, series = make_trial(rng, a.length)
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            write_case(d, rule, op, vals, series)
            r = run_promtool([a.promtool] if a.promtool else [], d, a.docker)
            if r.returncode != 0:
                bad += 1
                if bad <= 3:
                    print(
                        f"--- MISMATCH trial {t}: expr 'x {op} 80', for={rule.pending_steps} "
                        f"steps, keep_firing_for={rule.keep_firing_steps} steps"
                    )
                    print(f"series: {series}")
                    print((r.stdout + r.stderr)[:1500])
    print(f"{a.trials - bad}/{a.trials} trials agree with promtool")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
