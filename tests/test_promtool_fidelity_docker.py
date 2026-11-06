from __future__ import annotations

import os

import numpy as np
import pytest
import yaml

from hysteresis.sim import Rule
from tools.promtool_fidelity import (
    FidelityMismatch,
    get_promtool_version,
    run_promtool,
    write_case,
)


@pytest.mark.docker
def test_prometheus_v3151_executes_and_checks_expected_alerts(tmp_path):
    if os.environ.get("HYSTERESIS_RUN_DOCKER_TESTS") != "1":
        pytest.skip("set HYSTERESIS_RUN_DOCKER_TESTS=1 to run the Docker integration")

    image = "prom/prometheus:v3.15.1"
    version, _, _ = get_promtool_version([], image)
    assert version == "3.15.1"

    states = write_case(
        tmp_path,
        Rule(80, inclusive=True, pending_steps=1, keep_firing_steps=1),
        ">=",
        np.array([80.0, 80.0, 70.0, 70.0]),
        "80 80 70 70",
    )
    assert states.tolist() == [1, 2, 3, 0]
    assert run_promtool([], tmp_path, image).stdout.strip() == "SUCCESS"

    test_path = tmp_path / "test.yml"
    test_doc = yaml.safe_load(test_path.read_text(encoding="utf-8"))
    test_doc["tests"][0]["alert_rule_test"][1]["exp_alerts"] = [
        {"exp_labels": {"k": "deliberately-wrong"}}
    ]
    test_path.write_text(yaml.safe_dump(test_doc, sort_keys=False), encoding="utf-8")

    with pytest.raises(FidelityMismatch) as mismatch:
        run_promtool([], tmp_path, image)
    assert mismatch.value.result.returncode != 0
    assert "FAILED:" in mismatch.value.result.stderr
    assert "exp:[" in mismatch.value.result.stderr
    assert "got:[" in mismatch.value.result.stderr
