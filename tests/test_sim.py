import numpy as np
import pytest

from hysteresis.sim import FIRING, NORMAL, PENDING, RECOVERING, Rule, episodes, simulate
import itertools

def test_pending_delays_firing():
    values = np.array([90, 90, 90, 90, 50])
    states = simulate(values, Rule(80, pending_steps=2))
    assert list(states) == [PENDING, PENDING, FIRING, FIRING, NORMAL]


def test_single_blip_never_fires_with_pending():
    states = simulate(np.array([50, 90, 50, 50]), Rule(80, pending_steps=1))
    assert FIRING not in states


def test_keep_firing_holds_then_resolves():
    values = np.array([90, 50, 50, 50, 50])
    states = simulate(values, Rule(80, keep_firing_steps=2))
    assert list(states) == [FIRING, RECOVERING, RECOVERING, NORMAL, NORMAL]


def test_recovery_threshold_holds_alert_in_band():
    values = np.array([90, 78, 78, 70])
    no_band = simulate(values, Rule(80))
    band = simulate(values, Rule(80, clear_threshold=75))
    assert list(no_band) == [FIRING, NORMAL, NORMAL, NORMAL]
    assert list(band) == [FIRING, FIRING, FIRING, NORMAL]


def test_below_direction():
    states = simulate(np.array([30, 10, 10, 30]), Rule(20, direction="below"))
    assert list(states) == [NORMAL, FIRING, FIRING, NORMAL]


def test_inclusive_comparison():
    assert simulate(np.array([80.0]), Rule(80))[0] == NORMAL
    assert simulate(np.array([80.0]), Rule(80, inclusive=True))[0] == FIRING


def test_nan_counts_as_condition_false():
    states = simulate(np.array([90, np.nan, 90]), Rule(80))
    assert list(states) == [FIRING, NORMAL, FIRING]


def test_episodes_merge_recovering_into_one():
    states = simulate(np.array([90, 50, 90, 50, 50, 50]), Rule(80, keep_firing_steps=1))
    assert episodes(states) == [(0, 4)]


def test_invalid_rules_rejected():
    with pytest.raises(ValueError):
        Rule(80, clear_threshold=90)
    with pytest.raises(ValueError):
        Rule(80, direction="sideways")
    with pytest.raises(ValueError):
        Rule(float("nan"))
    with pytest.raises(ValueError):
        Rule(80, pending_steps=1.5)


def test_simulate_rejects_invalid_signal_shape_and_infinity():
    with pytest.raises(ValueError, match="one-dimensional"):
        simulate(np.ones((2, 2)), Rule(80))
    with pytest.raises(ValueError, match="infinity"):
        simulate(np.array([1.0, np.inf]), Rule(80))


def test_band_reduces_flapping_on_noisy_signal():
    values = 80 + 6 * np.random.default_rng(0).standard_normal(2000)
    base = len(episodes(simulate(values, Rule(80))))
    tuned_rule = Rule(80, clear_threshold=72, pending_steps=2, keep_firing_steps=4)
    tuned = len(episodes(simulate(values, tuned_rule)))
    assert tuned < base


def test_seeded_random_signals_preserve_state_and_episode_invariants():
    allowed = {NORMAL, PENDING, FIRING, RECOVERING}
    for seed in range(25):
        rng = np.random.default_rng(seed)
        values = rng.normal(75, 15, size=500)
        values[rng.random(values.size) < 0.03] = np.nan
        rule = Rule(
            threshold=80,
            pending_steps=seed % 4,
            keep_firing_steps=seed % 5,
        )
        states = simulate(values, rule)
        assert set(np.unique(states)).issubset(allowed)
        alert_episodes = episodes(states)
        assert all(start < end for start, end in alert_episodes)
        assert all(left[1] <= right[0] for left, right in itertools.pairwise(alert_episodes, alert_episodes[1:]))
        assert all(states[start] == FIRING for start, _ in alert_episodes)
