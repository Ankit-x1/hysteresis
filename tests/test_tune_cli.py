import dataclasses

import numpy as np

from hysteresis.bench import Bench, Incident, generate
from hysteresis.cli import main
from hysteresis.sim import Rule
from hysteresis.tune import tune


def test_tune_never_worse_than_base_on_training_data():
    bench = generate(seed=0, days=3)
    result = tune(bench, Rule(75), trials=40, seed=0)
    assert result.loss <= result.base_loss


def test_tune_is_deterministic():
    bench = generate(seed=0, days=3)
    first = tune(bench, Rule(75), trials=30, seed=7)
    second = tune(bench, Rule(75), trials=30, seed=7)
    assert first.rule == second.rule and first.loss == second.loss


def test_band_only_tuned_when_allowed():
    bench = generate(seed=0, days=3)
    assert tune(bench, Rule(75), trials=30, seed=1).rule.clear_threshold is None
    result = tune(bench, Rule(75), trials=60, seed=1, allow_band=True)
    assert result.rule.clear_threshold is None or result.rule.clear_threshold <= 75
    assert dataclasses.is_dataclass(result.rule)


def test_cli_end_to_end(tmp_path, capsys):
    benchmark_path = str(tmp_path / "b.npz")
    assert main(["bench", "--out", benchmark_path, "--days", "3", "--seed", "1"]) == 0
    assert main(["score", "examples/rules.yml", "--bench", benchmark_path]) == 0
    output = capsys.readouterr().out
    assert "HighCPU" in output and "HighCPUTypical" in output
    assert "Episodes/day" in output
    assert (
        main(
            [
                "tune",
                "examples/rules.yml",
                "--bench",
                benchmark_path,
                "--trials",
                "20",
                "--alert",
                "HighCPU",
            ]
        )
        == 0
    )
    assert "Tuned" in capsys.readouterr().out


def test_check_exit_codes(tmp_path):
    benchmark_path = str(tmp_path / "b.npz")
    assert main(["bench", "--out", benchmark_path, "--days", "3", "--seed", "1"]) == 0
    args = ["check", "examples/rules.yml", "--bench", benchmark_path, "--alert", "HighCPU"]
    assert main(args + ["--max-extra-episodes", "100000", "--max-missed", "100"]) == 0
    assert main(args + ["--max-extra-episodes", "0"]) == 1
    assert main(args + ["--max-extra-pages", "0"]) == 1


def test_unsupported_rule_fails_closed(tmp_path, capsys):
    benchmark_path = str(tmp_path / "b.npz")
    assert main(["bench", "--out", benchmark_path, "--days", "2"]) == 0
    code = main(
        [
            "check",
            "examples/unsupported_rules.yml",
            "--bench",
            benchmark_path,
            "--max-missed",
            "100",
        ]
    )
    assert code == 2
    assert "unsupported alert" in capsys.readouterr().err


def test_unknown_alert_fails_closed(tmp_path, capsys):
    benchmark_path = str(tmp_path / "b.npz")
    main(["bench", "--out", benchmark_path, "--days", "2"])
    code = main(["check", "examples/rules.yml", "--bench", benchmark_path, "--alert", "missing"])
    assert code == 2
    assert "was not found" in capsys.readouterr().err


def test_metric_selector_must_match_benchmark(tmp_path, capsys):
    benchmark_path = str(tmp_path / "b.npz")
    Bench(np.array([50.0, 90.0]), 15, [], metric_name="memory_usage").save(benchmark_path)
    code = main(["check", "examples/rules.yml", "--bench", benchmark_path, "--alert", "HighCPU"])
    assert code == 2
    assert "does not match benchmark" in capsys.readouterr().err


def test_empty_or_invalid_rule_file_fails_closed(tmp_path, capsys):
    benchmark_path = str(tmp_path / "b.npz")
    main(["bench", "--out", benchmark_path, "--days", "2"])
    empty_rules = tmp_path / "empty.yml"
    empty_rules.write_text("groups: []\n")
    assert main(["check", str(empty_rules), "--bench", benchmark_path]) == 2
    assert "no alerting rules" in capsys.readouterr().err


def test_check_delay_does_not_treat_undefined_as_zero(tmp_path, capsys):
    benchmark_path = str(tmp_path / "b.npz")
    Bench(np.full(100, 50.0), 15, [Incident(10, 20)]).save(benchmark_path)
    rules_path = tmp_path / "r.yml"
    rules_path.write_text(
        "groups:\n- name: g\n  rules:\n  - alert: NeverFires\n    expr: cpu_usage > 1000\n"
    )
    code = main(
        [
            "check",
            str(rules_path),
            "--bench",
            benchmark_path,
            "--max-missed",
            "1",
            "--max-delay",
            "30",
        ]
    )
    assert code == 1
    assert "undefined" in capsys.readouterr().out


def test_bench_creates_missing_output_directory(tmp_path):
    output = str(tmp_path / "new" / "dir" / "b.npz")
    assert main(["bench", "--out", output, "--days", "1"]) == 0
    loaded = Bench.load(output)
    assert loaded.metric_name == "cpu_usage"
