import numpy as np
import pytest

from hysteresis.bench import Bench, Incident, generate
from hysteresis.score import score
from hysteresis.sim import Rule, simulate


def test_generate_is_deterministic():
    a, b = generate(seed=3, days=2), generate(seed=3, days=2)
    assert np.array_equal(a.values, b.values) and a.incidents == b.incidents
    assert not np.array_equal(a.values, generate(seed=4, days=2).values)


def test_incidents_do_not_overlap_and_fit():
    bench = generate(seed=1, days=7)
    for left, right in zip(bench.incidents, bench.incidents[1:]):
        assert left.end <= right.start
    assert all(0 <= item.start < item.end <= len(bench.values) for item in bench.incidents)


def test_save_load_roundtrip(tmp_path):
    bench = generate(seed=2, days=1, metric_name="api_latency_seconds")
    path = tmp_path / "b.npz"
    bench.save(str(path))
    loaded = Bench.load(str(path))
    assert np.array_equal(bench.values, loaded.values)
    assert bench.incidents == loaded.incidents
    assert loaded.metric_name == "api_latency_seconds"


def test_load_legacy_archive_defaults_to_cpu_usage(tmp_path):
    path = tmp_path / "legacy.npz"
    np.savez_compressed(
        path,
        values=np.array([1.0, 2.0]),
        step_s=15.0,
        starts=np.array([], dtype=np.int64),
        ends=np.array([], dtype=np.int64),
        seed=7,
    )
    assert Bench.load(str(path)).metric_name == "cpu_usage"


def test_load_rejects_unknown_schema_version(tmp_path):
    path = tmp_path / "future.npz"
    np.savez_compressed(
        path,
        values=np.array([1.0, 2.0]),
        step_s=15.0,
        starts=np.array([], dtype=np.int64),
        ends=np.array([], dtype=np.int64),
        seed=7,
        metric_name=np.array("cpu_usage"),
        schema_version=99,
    )
    with pytest.raises(ValueError, match="could not load benchmark"):
        Bench.load(str(path))


def test_load_rejects_corrupt_archive(tmp_path):
    path = tmp_path / "bad.npz"
    path.write_text("not a zip file")
    with pytest.raises(ValueError, match="could not load benchmark"):
        Bench.load(str(path))


def test_split_never_cuts_an_incident():
    bench = generate(seed=5, days=7)
    for frac in (0.3, 0.5, 0.7):
        train, test = bench.split(frac)
        assert len(train.values) + len(test.values) == len(bench.values)
        assert len(train.incidents) + len(test.incidents) == len(bench.incidents)
        assert train.metric_name == test.metric_name == bench.metric_name


def test_bench_rejects_invalid_values_and_incident_windows():
    with pytest.raises(ValueError, match="one-dimensional"):
        Bench(np.ones((2, 2)), 15, [])
    with pytest.raises(ValueError, match="infinity"):
        Bench(np.array([1.0, np.inf]), 15, [])
    with pytest.raises(ValueError, match="step_s"):
        Bench(np.array([1.0]), 0, [])
    with pytest.raises(ValueError, match="non-overlapping"):
        Bench(np.arange(10), 15, [Incident(1, 5), Incident(4, 7)])
    with pytest.raises(ValueError, match="metric_name"):
        Bench(np.arange(10), 15, [], metric_name="not a metric")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"days": 0},
        {"step_s": 0},
        {"step_s": float("nan")},
        {"ar": 1.2},
        {"ar": -1.0},
        {"noise_sd": -1},
        {"ramp_steps": 0},
        {"incidents_per_day": 0},
        {"duration_range_s": (0, 10)},
        {"shift_range": (10, 1)},
    ],
)
def test_generate_rejects_invalid_parameters(kwargs):
    with pytest.raises(ValueError):
        generate(**kwargs)


def test_split_handles_incident_touching_start_boundary():
    bench = Bench(np.arange(20, dtype=float), 15, [Incident(0, 5)])
    train, test = bench.split(0.25)
    assert len(train.values) == 5
    assert train.incidents == [Incident(0, 5)]
    assert test.incidents == []


def test_split_rejects_single_incident_spanning_entire_benchmark():
    bench = Bench(np.arange(20, dtype=float), 15, [Incident(0, 20)])
    with pytest.raises(ValueError, match="spans the full benchmark"):
        bench.split(0.5)


def test_split_requires_nonempty_fraction():
    bench = generate(seed=4, days=2)
    with pytest.raises(ValueError, match="strictly between"):
        bench.split(0)
    with pytest.raises(ValueError, match="strictly between"):
        bench.split(1)


def _bench(values, incidents):
    return Bench(np.array(values, dtype=float), 60.0, incidents)


def test_score_detection_delay_and_false_episode():
    values = [90, 50, 50, 50, 50, 50, 50, 90, 90, 90]
    bench = _bench(values, [Incident(7, 10)])
    result = score(simulate(bench.values, Rule(80)), bench)
    assert (result.incidents, result.detected, result.missed) == (1, 1, 0)
    assert result.false_episodes == 1 and result.redundant_episodes == 0
    assert result.mean_delay_s == 0.0 and result.notifications == 4
    assert result.episodes_per_day > 0


def test_score_redundant_episodes_for_flapping_inside_incident():
    values = [50, 90, 50, 90, 50, 90, 50, 50]
    bench = _bench(values, [Incident(1, 6)])
    result = score(simulate(bench.values, Rule(80)), bench)
    assert result.episodes == 3
    assert result.redundant_episodes == 2
    assert result.false_episodes == 0
    assert result.extra_pages == 2
    assert abs(result.precision - 1 / 3) < 1e-9


def test_score_missed_incident_has_undefined_delay_and_zero_recall():
    bench = _bench([50] * 10, [Incident(3, 6)])
    result = score(simulate(bench.values, Rule(80)), bench)
    assert result.missed == 1
    assert result.recall == 0.0
    assert result.mean_delay_s is None
    assert result.precision is None


def test_score_with_false_episodes_and_no_incidents_has_zero_precision():
    bench = _bench([90, 90, 50, 50], [])
    result = score(simulate(bench.values, Rule(80)), bench)
    assert result.episodes == 1
    assert result.precision == 0.0
    assert result.recall is None


def test_score_with_no_incidents_has_undefined_recall():
    bench = _bench([50] * 10, [])
    result = score(simulate(bench.values, Rule(80)), bench)
    assert result.incidents == 0
    assert result.recall is None
    assert result.precision is None


def test_score_rejects_state_array_length_and_values():
    bench = _bench([50] * 3, [])
    with pytest.raises(ValueError, match="match benchmark"):
        score(np.array([0, 0]), bench)
    with pytest.raises(ValueError, match="unknown alert state"):
        score(np.array([0, 99, 0]), bench)
