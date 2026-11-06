"""Compare the simulator with Prometheus using ``promtool test rules``.

Each generated case supplies a Prometheus ``alert_rule_test`` assertion at every
sample timestamp. For Prometheus 3.15.1, a successful command returns 0 with
``SUCCESS`` on stdout and empty stderr; a mismatch returns nonzero with a
``FAILED`` block containing expected and actual alerts. Other output is
unverified. Harness failures exit 2; semantic disagreements exit 1.

    uv run python tools/promtool_fidelity.py --trials 200 --docker prom/prometheus:v3.15.1
    uv run python tools/promtool_fidelity.py --promtool /path/to/promtool
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import numpy as np
import yaml

from hysteresis.sim import FIRING, RECOVERING, STATE_NAMES, Rule, simulate

STEP = 15
LEVELS = [60.0, 80.0, 100.0]
DEFAULT_TIMEOUT_S = 120.0
MAX_OUTPUT_CHARS = 3000
PROMTOOL_VERSION_PATTERN = re.compile(r"\bpromtool,\s+version\s+([^\s(]+)", re.IGNORECASE)


class FidelityHarnessError(RuntimeError):
    """The generated case or reference-engine invocation could not be trusted."""

    def __init__(
        self,
        message: str,
        *,
        command: list[str] | None = None,
        result: subprocess.CompletedProcess[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.command = command
        self.result = result


class FidelityMismatch(FidelityHarnessError):
    """Prometheus evaluated a generated case and rejected its expected alerts."""

    def __init__(self, message: str, result: subprocess.CompletedProcess[str]) -> None:
        super().__init__(message, command=result.args, result=result)


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer greater than zero") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number greater than zero") from exc
    if not np.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be finite and greater than zero")
    return parsed


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
    tokens: list[str] = []
    current = rng.choice(LEVELS)
    for index in range(n):
        if rng.random() < 0.35:
            current = rng.choice(LEVELS)
        if rng.random() < 0.05:
            vals[index] = np.nan
            tokens.append("stale")
        else:
            vals[index] = current
            tokens.append(f"{current:g}")
    return rule, op, vals, " ".join(tokens)


def _make_documents(
    rule: Rule, op: str, vals: np.ndarray, series: str
) -> tuple[str, str, np.ndarray]:
    if op not in (">", ">=", "<", "<="):
        raise FidelityHarnessError(f"unsupported generated comparison operator: {op!r}")
    expected_direction = "above" if op in (">", ">=") else "below"
    expected_inclusive = op in (">=", "<=")
    if (
        rule.threshold != 80.0
        or rule.direction != expected_direction
        or rule.inclusive != expected_inclusive
    ):
        raise FidelityHarnessError("generated Prometheus expression does not match simulator rule")
    if vals.ndim != 1 or len(vals) == 0 or len(series.split()) != len(vals):
        raise FidelityHarnessError(
            "generated series must contain samples and have one token per one-dimensional sample"
        )

    alert: dict[str, str] = {"alert": "Fid", "expr": f"x {op} 80"}
    if rule.pending_steps:
        alert["for"] = f"{rule.pending_steps * STEP}s"
    if rule.keep_firing_steps:
        alert["keep_firing_for"] = f"{rule.keep_firing_steps * STEP}s"
    rules_doc = {"groups": [{"name": "g", "rules": [alert]}]}

    states = simulate(vals, rule)
    checks = []
    for index, state in enumerate(states):
        firing = state in (FIRING, RECOVERING)
        checks.append(
            {
                "eval_time": f"{index * STEP}s",
                "alertname": "Fid",
                "exp_alerts": [{"exp_labels": {"k": "v"}}] if firing else [],
            }
        )
    test_doc = {
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

    try:
        rules_text = yaml.safe_dump(rules_doc, sort_keys=False)
        test_text = yaml.safe_dump(test_doc, sort_keys=False)
        if yaml.safe_load(rules_text) != rules_doc or yaml.safe_load(test_text) != test_doc:
            raise FidelityHarnessError("generated Prometheus YAML did not round-trip")
    except yaml.YAMLError as exc:
        raise FidelityHarnessError(f"could not serialize generated Prometheus YAML: {exc}") from exc
    return rules_text, test_text, states


def _make_world_readable(path: Path, mode: int, *, directory: bool) -> None:
    try:
        path.chmod(mode)
        actual = stat.S_IMODE(path.stat().st_mode)
    except OSError as exc:
        raise FidelityHarnessError(
            f"could not set Docker-readable permissions on {path}: {exc}"
        ) from exc
    needed = (stat.S_IROTH | stat.S_IXOTH) if directory else stat.S_IROTH
    if actual & needed != needed:
        kind = "directory traversal/read" if directory else "file read"
        raise FidelityHarnessError(
            f"{path} is not accessible to the container user; expected mode "
            f"{mode:04o}, found {actual:04o} ({kind} required)"
        )


def write_case(d: Path, rule: Rule, op: str, vals: np.ndarray, series: str) -> np.ndarray:
    """Write validated YAML and make only this synthetic temporary case readable."""
    rules_text, test_text, states = _make_documents(rule, op, vals, series)
    try:
        d.mkdir(parents=True, exist_ok=True)
        _make_world_readable(d, 0o755, directory=True)
        rules_path = d / "rules.yml"
        test_path = d / "test.yml"
        rules_path.write_text(rules_text, encoding="utf-8")
        test_path.write_text(test_text, encoding="utf-8")
        _make_world_readable(rules_path, 0o644, directory=False)
        _make_world_readable(test_path, 0o644, directory=False)
    except OSError as exc:
        raise FidelityHarnessError(
            f"could not write generated Prometheus case in {d}: {exc}"
        ) from exc
    return states


def _combined_output(result: subprocess.CompletedProcess[str]) -> str:
    return f"{result.stdout or ''}\n{result.stderr or ''}".strip()


def _output_excerpt(result: subprocess.CompletedProcess[str]) -> str:
    sections = []
    for label, content in (("stdout", result.stdout), ("stderr", result.stderr)):
        if content and content.strip():
            sections.append(f"{label}:\n{content.strip()}")
    output = "\n".join(sections)
    return output[:MAX_OUTPUT_CHARS] or "(no stdout or stderr)"


def _timeout_excerpt(exc: subprocess.TimeoutExpired) -> str:
    sections = []
    for label, content in (("stdout", exc.stdout), ("stderr", exc.stderr)):
        if content:
            if isinstance(content, bytes):
                content = content.decode("utf-8", errors="replace")
            sections.append(f"{label}:\n{content.strip()}")
    output = "\n".join(sections)
    return output[:MAX_OUTPUT_CHARS] or "(no partial stdout or stderr)"


def _looks_like_engine_mismatch(output: str) -> bool:
    lowered = output.lower()
    if any(
        marker in lowered
        for marker in (
            "permission denied",
            "no such file",
            "cannot connect to the docker daemon",
            "error during connect",
            "failed to mount",
            "mounts denied",
            "oci runtime",
            "error parsing",
            "error loading",
            "failed to parse",
            "invalid rule",
            "yaml:",
        )
    ):
        return False
    return bool(
        re.search(r"\bfailed:\s*", lowered)
        and re.search(r"\balertname:\s*[^,\r\n]+,\s*time:\s*\S+", lowered)
        and re.search(r"\bexp:\s*\[", lowered)
        and re.search(r"\bgot:\s*\[", lowered)
    )


def _is_success_result(result: subprocess.CompletedProcess[str]) -> bool:
    return (
        result.returncode == 0
        and (result.stdout or "").strip() == "SUCCESS"
        and not (result.stderr or "").strip()
    )


def _build_command(
    cmd: list[str], directory: Path, docker: str | None, container_name: str | None = None
) -> list[str]:
    if docker:
        source = str(directory.resolve(strict=True))
        if "," in source:
            raise FidelityHarnessError(
                f"Docker bind-mount path contains a comma and cannot be represented safely: {source}"
            )
        return [
            "docker",
            "run",
            "--rm",
            "--name",
            container_name or f"hysteresis-promtool-{uuid.uuid4().hex}",
            "--mount",
            f"type=bind,source={source},target=/t,readonly",
            "--workdir",
            "/t",
            "--entrypoint",
            "promtool",
            docker,
            "test",
            "rules",
            "test.yml",
        ]
    if not cmd:
        raise FidelityHarnessError("no promtool executable was configured")
    return [*cmd, "test", "rules", "test.yml"]


def _version_command(cmd: list[str], docker: str | None) -> list[str]:
    if docker:
        return [
            "docker",
            "run",
            "--rm",
            "--name",
            f"hysteresis-promtool-version-{uuid.uuid4().hex}",
            "--entrypoint",
            "promtool",
            docker,
            "--version",
        ]
    if not cmd:
        raise FidelityHarnessError("no promtool executable was configured")
    return [*cmd, "--version"]


def _remove_container(name: str) -> str:
    try:
        result = subprocess.run(
            ["docker", "rm", "--force", name],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=10,
        )
        if result.returncode != 0 and "no such container" not in _combined_output(result).lower():
            return f"; container cleanup failed: {_output_excerpt(result)}"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"; container cleanup failed: {exc}"
    return ""


def get_promtool_version(
    cmd: list[str], docker: str | None, timeout_s: float = DEFAULT_TIMEOUT_S
) -> tuple[str, list[str], subprocess.CompletedProcess[str]]:
    """Return the version string and exact argv after a successful version probe."""
    full = _version_command(cmd, docker)
    executable = "Docker" if docker else "promtool"
    try:
        result = subprocess.run(
            full,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout_s,
        )
    except FileNotFoundError as exc:
        raise FidelityHarnessError(
            f"{executable} executable was not found (command: {json.dumps(full)}): {exc}",
            command=full,
        ) from exc
    except subprocess.TimeoutExpired as exc:
        cleanup_message = ""
        if docker:
            cleanup_message = _remove_container(full[full.index("--name") + 1])
        raise FidelityHarnessError(
            f"{executable} version probe timed out after {timeout_s:g}s "
            f"(command: {json.dumps(full)}); partial output: {_timeout_excerpt(exc)}"
            f"{cleanup_message}",
            command=full,
        ) from exc
    except OSError as exc:
        raise FidelityHarnessError(
            f"could not execute {executable} version probe (command: {json.dumps(full)}): {exc}",
            command=full,
        ) from exc

    match = PROMTOOL_VERSION_PATTERN.search(result.stdout or "")
    if result.returncode != 0 or match is None:
        raise FidelityHarnessError(
            f"{executable} version probe failed (exit {result.returncode}, "
            f"command: {json.dumps(full)}): {_output_excerpt(result)}",
            command=full,
            result=result,
        )
    return match.group(1), full, result


def run_promtool(
    cmd: list[str],
    d: Path,
    docker: str | None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> subprocess.CompletedProcess[str]:
    container_name = f"hysteresis-promtool-{uuid.uuid4().hex}" if docker else None
    full = _build_command(cmd, d, docker, container_name)
    executable = "Docker" if docker else "promtool"
    try:
        result = subprocess.run(
            full,
            cwd=None if docker else d,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout_s,
        )
    except FileNotFoundError as exc:
        raise FidelityHarnessError(
            f"{executable} executable was not found (command: {json.dumps(full)}): {exc}",
            command=full,
        ) from exc
    except subprocess.TimeoutExpired as exc:
        cleanup_message = ""
        if docker and container_name:
            cleanup_message = _remove_container(container_name)
        raise FidelityHarnessError(
            f"{executable} timed out after {timeout_s:g}s "
            f"(command: {json.dumps(full)}); partial output: {_timeout_excerpt(exc)}"
            f"{cleanup_message}",
            command=full,
        ) from exc
    except OSError as exc:
        raise FidelityHarnessError(
            f"could not execute {executable} (command: {json.dumps(full)}): {exc}",
            command=full,
        ) from exc

    output = _combined_output(result)
    if result.returncode != 0:
        if _looks_like_engine_mismatch(output):
            raise FidelityMismatch(
                f"Prometheus rejected expected alert states (exit {result.returncode}, "
                f"command: {json.dumps(full)}): {_output_excerpt(result)}",
                result,
            )
        raise FidelityHarnessError(
            f"{executable} execution failed (exit {result.returncode}, "
            f"command: {json.dumps(full)}): {_output_excerpt(result)}",
            command=full,
            result=result,
        )
    if not _is_success_result(result):
        raise FidelityHarnessError(
            f"{executable} exited successfully but did not return the exact completed-test "
            f"marker ('SUCCESS' on stdout and empty stderr); command: {json.dumps(full)}; "
            f"output: {_output_excerpt(result)}",
            command=full,
            result=result,
        )
    return result


def _save_failure(
    root: Path,
    trial: int,
    case_dir: Path,
    rule: Rule,
    op: str,
    series: str,
    states: np.ndarray,
    result: subprocess.CompletedProcess[str],
    seed: int,
    promtool_version: str,
    version_command: list[str],
    version_result: subprocess.CompletedProcess[str],
) -> Path:
    destination = root / f"trial-{trial:04d}"
    try:
        destination.mkdir(parents=True, exist_ok=False)
        shutil.copy2(case_dir / "rules.yml", destination / "rules.yml")
        shutil.copy2(case_dir / "test.yml", destination / "test.yml")
        (destination / "series.txt").write_text(series + "\n", encoding="utf-8")
        (destination / "simulator_states.csv").write_text(
            "index,time_seconds,state,state_name\n"
            + "".join(
                f"{index},{index * STEP},{int(state)},{STATE_NAMES[int(state)]}\n"
                for index, state in enumerate(states)
            ),
            encoding="utf-8",
        )
        (destination / "stdout.txt").write_text(result.stdout or "", encoding="utf-8")
        (destination / "stderr.txt").write_text(result.stderr or "", encoding="utf-8")
        (destination / "promtool-version.stdout.txt").write_text(
            version_result.stdout or "", encoding="utf-8"
        )
        (destination / "promtool-version.stderr.txt").write_text(
            version_result.stderr or "", encoding="utf-8"
        )
        (destination / "trial.json").write_text(
            json.dumps(
                {
                    "trial": trial,
                    "seed": seed,
                    "expression": f"x {op} 80",
                    "for_steps": rule.pending_steps,
                    "keep_firing_steps": rule.keep_firing_steps,
                    "step_seconds": STEP,
                    "promtool_version": promtool_version,
                    "version_command": version_command,
                    "test_command": result.args,
                    "returncode": result.returncode,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise FidelityHarnessError(
            f"could not save mismatch reproduction to {destination}: {exc}"
        ) from exc
    return destination


def _save_unverified_trial(
    root: Path,
    trial: int,
    case_dir: Path,
    series: str,
    states: np.ndarray | None,
    error: FidelityHarnessError,
    seed: int,
    promtool_version: str,
    version_command: list[str],
    version_result: subprocess.CompletedProcess[str],
) -> Path:
    destination = root / f"trial-{trial:04d}"
    try:
        destination.mkdir(parents=True, exist_ok=False)
        for filename in ("rules.yml", "test.yml"):
            source = case_dir / filename
            if source.is_file():
                shutil.copy2(source, destination / filename)
        (destination / "series.txt").write_text(series + "\n", encoding="utf-8")
        if states is not None:
            (destination / "simulator_states.csv").write_text(
                "index,time_seconds,state,state_name\n"
                + "".join(
                    f"{index},{index * STEP},{int(state)},{STATE_NAMES[int(state)]}\n"
                    for index, state in enumerate(states)
                ),
                encoding="utf-8",
            )
        (destination / "error.txt").write_text(str(error) + "\n", encoding="utf-8")
        result = error.result
        (destination / "stdout.txt").write_text(
            result.stdout or "" if result is not None else "", encoding="utf-8"
        )
        (destination / "stderr.txt").write_text(
            result.stderr or "" if result is not None else "", encoding="utf-8"
        )
        (destination / "promtool-version.stdout.txt").write_text(
            version_result.stdout or "", encoding="utf-8"
        )
        (destination / "promtool-version.stderr.txt").write_text(
            version_result.stderr or "", encoding="utf-8"
        )
        (destination / "trial.json").write_text(
            json.dumps(
                {
                    "status": "unverified",
                    "trial": trial,
                    "seed": seed,
                    "promtool_version": promtool_version,
                    "version_command": version_command,
                    "test_command": error.command,
                    "returncode": result.returncode if result is not None else None,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise FidelityHarnessError(
            f"could not save unverified trial diagnostics to {destination}: {exc}"
        ) from exc
    return destination


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=_positive_int, default=100)
    ap.add_argument("--length", type=_positive_int, default=40)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--promtool")
    ap.add_argument("--docker", help="image to use instead of a local promtool")
    ap.add_argument("--timeout", type=_positive_float, default=DEFAULT_TIMEOUT_S)
    ap.add_argument(
        "--failure-dir",
        default="promtool-failures",
        help="directory for mismatch reproductions and harness-error trial diagnostics",
    )
    args = ap.parse_args(argv)
    if args.promtool and args.docker:
        ap.error("select only one of --promtool or --docker")
    if not args.promtool and not args.docker:
        args.promtool = shutil.which("promtool")
    if not args.promtool and not args.docker:
        ap.error("need --promtool PATH or --docker IMAGE (or install promtool on PATH)")
    if args.seed < 0:
        ap.error("--seed must be non-negative")

    try:
        promtool_version, version_command, version_result = get_promtool_version(
            [args.promtool] if args.promtool else [], args.docker, args.timeout
        )
    except FidelityHarnessError as exc:
        print(f"HARNESS ERROR before trial 0; 0 trials completed: {exc}", file=sys.stderr)
        return 2

    rng = np.random.default_rng(args.seed)
    mismatches = 0
    completed = 0
    failure_root = Path(args.failure_dir)
    for trial in range(args.trials):
        rule, op, vals, series = make_trial(rng, args.length)
        try:
            with tempfile.TemporaryDirectory(prefix="hysteresis-promtool-") as td:
                case_dir = Path(td)
                states: np.ndarray | None = None
                try:
                    states = write_case(case_dir, rule, op, vals, series)
                    run_promtool(
                        [args.promtool] if args.promtool else [],
                        case_dir,
                        args.docker,
                        args.timeout,
                    )
                except FidelityMismatch as exc:
                    mismatches += 1
                    completed += 1
                    reproduction = _save_failure(
                        failure_root,
                        trial,
                        case_dir,
                        rule,
                        op,
                        series,
                        states,
                        exc.result,
                        args.seed,
                        promtool_version,
                        version_command,
                        version_result,
                    )
                    if mismatches <= 3:
                        print(
                            f"--- MISMATCH trial {trial}: expr 'x {op} 80', "
                            f"for={rule.pending_steps} steps, "
                            f"keep_firing_for={rule.keep_firing_steps} steps"
                        )
                        print(f"series: {series}")
                        print(f"simulator states: {states.tolist()}")
                        print(f"Prometheus output:\n{_output_excerpt(exc.result)}")
                        print(f"reproduction saved to {reproduction}")
                except FidelityHarnessError as exc:
                    reproduction = _save_unverified_trial(
                        failure_root / "harness-errors",
                        trial,
                        case_dir,
                        series,
                        states,
                        exc,
                        args.seed,
                        promtool_version,
                        version_command,
                        version_result,
                    )
                    print(
                        f"HARNESS ERROR at trial {trial}; {completed} trials completed "
                        f"({completed - mismatches} agreed, {mismatches} mismatches): "
                        f"{exc}; unverified trial data saved to {reproduction}",
                        file=sys.stderr,
                    )
                    return 2
                else:
                    completed += 1
        except (FidelityHarnessError, OSError, yaml.YAMLError) as exc:
            print(
                f"HARNESS ERROR at trial {trial}; {completed} trials completed "
                f"({completed - mismatches} agreed, {mismatches} mismatches): {exc}",
                file=sys.stderr,
            )
            return 2

    agreed = completed - mismatches
    print(f"{agreed}/{completed} trials agree with promtool; {mismatches} mismatches")
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
