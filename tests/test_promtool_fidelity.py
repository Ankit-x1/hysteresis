from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import numpy as np
import pytest
import yaml

from hysteresis.sim import Rule
from tools import promtool_fidelity as fidelity
from tools.promtool_fidelity import FidelityHarnessError, FidelityMismatch, run_promtool, write_case


def _success() -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["promtool"],
        returncode=0,
        stdout="  SUCCESS\n\n",
        stderr="",
    )


def _mock_version(monkeypatch, version="3.15.1"):
    result = subprocess.CompletedProcess(
        args=["docker", "--version"],
        returncode=0,
        stdout=f"promtool, version {version}\n",
        stderr="",
    )
    monkeypatch.setattr(
        fidelity,
        "get_promtool_version",
        lambda cmd, docker, timeout_s: (
            (
                version,
                ["docker", "run", "--rm", "--entrypoint", "promtool", docker, "--version"]
                if docker
                else [*cmd, "--version"],
                result,
            )
        ),
    )


def _write_valid_case(path):
    states = write_case(
        path,
        Rule(80, inclusive=True, pending_steps=1, keep_firing_steps=1),
        ">=",
        np.array([80.0, 80.0, 70.0, 70.0]),
        "80 80 70 70",
    )
    return states


def test_write_case_generates_valid_yaml_and_container_readable_permissions(tmp_path):
    states = _write_valid_case(tmp_path)

    rules = yaml.safe_load((tmp_path / "rules.yml").read_text(encoding="utf-8"))
    test = yaml.safe_load((tmp_path / "test.yml").read_text(encoding="utf-8"))

    assert rules["groups"][0]["rules"][0] == {
        "alert": "Fid",
        "expr": "x >= 80",
        "for": "15s",
        "keep_firing_for": "15s",
    }
    assert [item["exp_alerts"] for item in test["tests"][0]["alert_rule_test"]] == [
        [],
        [{"exp_labels": {"k": "v"}}],
        [{"exp_labels": {"k": "v"}}],
        [],
    ]
    assert states.tolist() == [1, 2, 3, 0]
    assert stat.S_IMODE(tmp_path.stat().st_mode) & (stat.S_IROTH | stat.S_IXOTH) == (
        stat.S_IROTH | stat.S_IXOTH
    )
    for filename in ("rules.yml", "test.yml"):
        assert stat.S_IMODE((tmp_path / filename).stat().st_mode) & stat.S_IROTH


def test_write_case_rejects_mismatched_series_length(tmp_path):
    with pytest.raises(FidelityHarnessError, match="one token per"):
        write_case(tmp_path, Rule(80), ">", np.array([90.0, 50.0]), "90")


def test_write_case_rejects_empty_trial_without_alert_assertions(tmp_path):
    with pytest.raises(FidelityHarnessError, match="must contain samples"):
        write_case(tmp_path, Rule(80), ">", np.array([], dtype=float), "")


def test_yaml_serialization_failure_is_a_harness_error(tmp_path, monkeypatch):
    monkeypatch.setattr(
        yaml,
        "safe_dump",
        lambda *args, **kwargs: (_ for _ in ()).throw(yaml.YAMLError("serialization failed")),
    )
    with pytest.raises(FidelityHarnessError, match="could not serialize"):
        _write_valid_case(tmp_path)


def test_docker_invocation_uses_read_only_bind_mount_and_safe_argument_list(tmp_path, monkeypatch):
    _write_valid_case(tmp_path)
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return _success()

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = run_promtool([], tmp_path, "prom/prometheus:v3.15.1")

    command, kwargs = calls[0]
    assert result.returncode == 0
    assert command[:3] == ["docker", "run", "--rm"]
    assert command[command.index("--name") + 1].startswith("hysteresis-promtool-")
    assert command[command.index("--mount") + 1] == (
        f"type=bind,source={tmp_path.resolve()},target=/t,readonly"
    )
    assert command[-4:] == ["prom/prometheus:v3.15.1", "test", "rules", "test.yml"]
    assert "--workdir" in command and "/t" in command
    assert kwargs["cwd"] is None
    assert kwargs["timeout"] == fidelity.DEFAULT_TIMEOUT_S
    assert kwargs["text"] is True
    assert "shell" not in kwargs


def test_local_promtool_invocation_uses_case_directory(tmp_path, monkeypatch):
    _write_valid_case(tmp_path)
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return _success()

    monkeypatch.setattr(subprocess, "run", fake_run)
    run_promtool(["/usr/bin/promtool"], tmp_path, None)

    command, kwargs = calls[0]
    assert command == ["/usr/bin/promtool", "test", "rules", "test.yml"]
    assert kwargs["cwd"] == tmp_path


def test_version_probe_returns_observed_version_and_exact_command(monkeypatch):
    observed = subprocess.CompletedProcess(
        args=["docker"],
        returncode=0,
        stdout="promtool, version 3.15.1 (branch: HEAD)\n",
        stderr="",
    )
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return observed

    monkeypatch.setattr(subprocess, "run", fake_run)
    version, command, result = fidelity.get_promtool_version([], "prom/prometheus:v3.15.1")

    assert version == "3.15.1"
    assert command == calls[0][0]
    assert command[-2:] == ["prom/prometheus:v3.15.1", "--version"]
    assert result is observed
    assert calls[0][1]["capture_output"] is True
    assert calls[0][1]["text"] is True


@pytest.mark.parametrize(
    "returncode,stdout",
    [(1, "promtool, version 3.15.1"), (0, "SUCCESS"), (0, "")],
)
def test_version_probe_rejects_failed_or_unrecognized_output(monkeypatch, returncode, stdout):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=["promtool"], returncode=returncode, stdout=stdout, stderr=""
        ),
    )
    with pytest.raises(FidelityHarnessError, match="version probe failed"):
        fidelity.get_promtool_version(["promtool"], None)


def test_docker_version_probe_timeout_removes_its_named_container(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[1] == "run":
            raise subprocess.TimeoutExpired(command, 1)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(FidelityHarnessError, match="version probe timed out"):
        fidelity.get_promtool_version([], "prom/prometheus:v3.15.1", timeout_s=1)

    version_command, cleanup_command = calls
    container_name = version_command[version_command.index("--name") + 1]
    assert cleanup_command == ["docker", "rm", "--force", container_name]


def test_missing_docker_is_an_execution_error_not_a_mismatch(tmp_path, monkeypatch):
    _write_valid_case(tmp_path)

    def missing_executable(*args, **kwargs):
        raise FileNotFoundError("docker missing")

    monkeypatch.setattr(subprocess, "run", missing_executable)
    with pytest.raises(FidelityHarnessError, match="Docker executable was not found"):
        run_promtool([], tmp_path, "prom/prometheus:v3.15.1")


def test_docker_invocation_oserror_is_reported_with_command(tmp_path, monkeypatch):
    _write_valid_case(tmp_path)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("daemon unavailable")),
    )

    with pytest.raises(FidelityHarnessError, match="command:.*daemon unavailable"):
        run_promtool([], tmp_path, "prom/prometheus:v3.15.1")


def test_promtool_timeout_is_an_execution_error(tmp_path, monkeypatch):
    _write_valid_case(tmp_path)

    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired("docker", 1)

    monkeypatch.setattr(subprocess, "run", timed_out)
    with pytest.raises(FidelityHarnessError, match="timed out"):
        run_promtool([], tmp_path, "prom/prometheus:v3.15.1", timeout_s=1)


def test_docker_timeout_removes_only_the_named_harness_container(tmp_path, monkeypatch):
    _write_valid_case(tmp_path)
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[1] == "run":
            raise subprocess.TimeoutExpired(command, 1, output=b"partial", stderr=b"timeout")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(FidelityHarnessError, match="partial output"):
        run_promtool([], tmp_path, "prom/prometheus:v3.15.1", timeout_s=1)

    run_command, cleanup_command = calls
    container_name = run_command[run_command.index("--name") + 1]
    assert cleanup_command == ["docker", "rm", "--force", container_name]


@pytest.mark.parametrize(
    "stdout,stderr",
    [
        ("", "promtool: error: stat test.yml: permission denied"),
        ("", "Error loading rule files: cannot open rules.yml"),
        ("", "Error parsing test YAML"),
    ],
)
def test_nonzero_execution_errors_are_not_counted_as_mismatches(
    tmp_path, monkeypatch, stdout, stderr
):
    _write_valid_case(tmp_path)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=["promtool"], returncode=1, stdout=stdout, stderr=stderr
        ),
    )
    with pytest.raises(FidelityHarnessError, match="execution failed"):
        run_promtool(["promtool"], tmp_path, None)


def test_nonzero_expected_alert_difference_is_a_genuine_mismatch(tmp_path, monkeypatch):
    _write_valid_case(tmp_path)
    result = subprocess.CompletedProcess(
        args=["promtool"],
        returncode=1,
        stdout="\n\n",
        stderr=(
            "  FAILED:\n"
            "    alertname: Fid, time: 0s, \n"
            "        exp:[\n"
            "            0:\n"
            '              Labels:{alertname="Fid", k="deliberately-wrong"}\n'
            "              Annotations:{}\n"
            "            ], \n"
            "        got:[]\n"
        ),
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: result)

    with pytest.raises(FidelityMismatch, match="rejected expected alert states") as exc:
        run_promtool(["promtool"], tmp_path, None)
    assert exc.value.result is result


def test_observed_success_output_is_accepted_only_after_zero_exit(tmp_path, monkeypatch):
    _write_valid_case(tmp_path)
    observed = _success()
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: observed)

    assert run_promtool(["promtool"], tmp_path, None) is observed


@pytest.mark.parametrize(
    "returncode,stdout",
    [
        (0, ""),
        (0, "finished without a test result"),
        (1, "unexpected unclassified output"),
    ],
)
def test_invalid_promtool_output_fails_closed(tmp_path, monkeypatch, returncode, stdout):
    _write_valid_case(tmp_path)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=["promtool"], returncode=returncode, stdout=stdout, stderr=""
        ),
    )
    with pytest.raises(FidelityHarnessError):
        run_promtool(["promtool"], tmp_path, None)


@pytest.mark.parametrize(
    "stdout,stderr",
    [("", ""), ("SUCCESS extra text", ""), ("SUCCESS", "warning: unexpected output")],
)
def test_empty_or_unexpected_success_output_is_unverified(tmp_path, monkeypatch, stdout, stderr):
    _write_valid_case(tmp_path)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=["promtool"], returncode=0, stdout=stdout, stderr=stderr
        ),
    )

    with pytest.raises(FidelityHarnessError, match="exact completed-test marker"):
        run_promtool(["promtool"], tmp_path, None)


def test_success_requires_completed_promtool_test_marker(tmp_path, monkeypatch):
    _write_valid_case(tmp_path)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: _success())
    assert run_promtool(["promtool"], tmp_path, None).returncode == 0


def test_main_reports_exact_trial_counts_and_saves_mismatch_reproduction(
    tmp_path, monkeypatch, capsys
):
    _mock_version(monkeypatch)
    success = _success()
    mismatch = subprocess.CompletedProcess(
        args=["docker"],
        returncode=1,
        stdout="\n\n",
        stderr="FAILED:\n  alertname: Fid, time: 0s,\n    exp:[]\n    got:[]\n",
    )
    outcomes = iter([success, FidelityMismatch("state mismatch", mismatch), success])

    def fake_run(*args, **kwargs):
        outcome = next(outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(fidelity, "run_promtool", fake_run)
    failures = tmp_path / "failures"
    code = fidelity.main(
        [
            "--promtool",
            "mock-promtool",
            "--trials",
            "3",
            "--length",
            "5",
            "--failure-dir",
            str(failures),
        ]
    )

    output = capsys.readouterr().out
    assert code == 1
    assert "2/3 trials agree with promtool; 1 mismatches" in output
    reproduction = failures / "trial-0001"
    assert (reproduction / "rules.yml").is_file()
    assert (reproduction / "test.yml").is_file()
    assert (reproduction / "series.txt").is_file()
    assert (reproduction / "simulator_states.csv").is_file()
    assert (reproduction / "stdout.txt").read_text(encoding="utf-8") == "\n\n"
    assert "FAILED:" in (reproduction / "stderr.txt").read_text(encoding="utf-8")
    metadata = fidelity.json.loads((reproduction / "trial.json").read_text(encoding="utf-8"))
    assert metadata["seed"] == 0
    assert metadata["promtool_version"] == "3.15.1"
    assert metadata["test_command"] == ["docker"]
    assert metadata["returncode"] == 1
    assert (reproduction / "promtool-version.stdout.txt").read_text(
        encoding="utf-8"
    ) == "promtool, version 3.15.1\n"


def test_main_stops_on_infrastructure_error_without_counting_a_mismatch(
    tmp_path, monkeypatch, capsys
):
    _mock_version(monkeypatch)
    execution_error = FidelityHarnessError(
        "permission denied",
        command=["docker", "run", "promtool", "test", "rules", "test.yml"],
        result=subprocess.CompletedProcess(
            args=["docker", "run"],
            returncode=1,
            stdout="",
            stderr="stat test.yml: permission denied",
        ),
    )
    outcomes = iter([_success(), execution_error])

    def fake_run(*args, **kwargs):
        outcome = next(outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(fidelity, "run_promtool", fake_run)
    code = fidelity.main(
        ["--promtool", "mock-promtool", "--trials", "3", "--failure-dir", str(tmp_path / "f")]
    )

    error = capsys.readouterr().err
    assert code == 2
    assert "1 trials completed (1 agreed, 0 mismatches)" in error
    assert "HARNESS ERROR at trial 1" in error
    unverified = tmp_path / "f" / "harness-errors" / "trial-0001"
    assert (unverified / "rules.yml").is_file()
    assert (unverified / "test.yml").is_file()
    assert "permission denied" in (unverified / "stderr.txt").read_text(encoding="utf-8")
    metadata = fidelity.json.loads((unverified / "trial.json").read_text(encoding="utf-8"))
    assert metadata["status"] == "unverified"
    assert metadata["test_command"] == execution_error.command
    assert metadata["returncode"] == 1


def test_main_does_not_count_a_trial_when_temporary_directory_creation_fails(monkeypatch, capsys):
    _mock_version(monkeypatch)

    def fail_tempdir(*args, **kwargs):
        raise OSError("temp path unavailable")

    monkeypatch.setattr(fidelity.tempfile, "TemporaryDirectory", fail_tempdir)
    code = fidelity.main(
        ["--promtool", "mock-promtool", "--trials", "2", "--failure-dir", "unused"]
    )

    error = capsys.readouterr().err
    assert code == 2
    assert "0 trials completed (0 agreed, 0 mismatches)" in error
    assert "temp path unavailable" in error


def test_main_reports_version_probe_failure_before_any_trials(monkeypatch, capsys):
    def fail_version(*args, **kwargs):
        raise FidelityHarnessError("version probe unavailable")

    monkeypatch.setattr(fidelity, "get_promtool_version", fail_version)
    code = fidelity.main(
        ["--promtool", "mock-promtool", "--trials", "2", "--failure-dir", "unused"]
    )

    error = capsys.readouterr().err
    assert code == 2
    assert "before trial 0; 0 trials completed" in error
    assert "version probe unavailable" in error


def test_write_case_file_creation_failure_is_a_harness_error(tmp_path, monkeypatch):
    original_write_text = Path.write_text

    def fail_rules_write(path, *args, **kwargs):
        if path.name == "rules.yml":
            raise PermissionError("fixture is read-only")
        return original_write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_rules_write)
    with pytest.raises(FidelityHarnessError, match="could not write generated"):
        _write_valid_case(tmp_path)


def test_main_rejects_nonpositive_trial_count():
    with pytest.raises(SystemExit) as exc:
        fidelity.main(["--docker", "prom/prometheus:v3.15.1", "--trials", "0"])
    assert exc.value.code == 2
