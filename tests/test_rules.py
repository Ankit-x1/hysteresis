import pytest

from hysteresis.rules import format_duration, load_rules, parse_duration, steps_for


@pytest.mark.parametrize(
    "text,seconds",
    [("30s", 30), ("5m", 300), ("1h30m", 5400), ("0s", 0), (None, 0), ("500ms", 0.5)],
)
def test_parse_duration(text, seconds):
    assert parse_duration(text) == seconds


@pytest.mark.parametrize("bad", ["5", "m5", "5 minutes", "1sxyz", "-5s"])
def test_parse_duration_rejects(bad):
    with pytest.raises(ValueError):
        parse_duration(bad)


def test_parse_duration_requires_string():
    with pytest.raises(TypeError):
        parse_duration(30)


def test_format_duration():
    assert format_duration(90) == "1m30s"
    assert format_duration(600) == "10m"
    assert format_duration(0) == "0s"
    assert format_duration(0.5) == "500ms"
    assert format_duration(90.5) == "1m30s500ms"
    with pytest.raises(ValueError):
        format_duration(float("nan"))


def test_steps_for_rounds_up_and_validates_inputs():
    assert steps_for(20, 15) == 2
    assert steps_for(300, 15) == 20
    with pytest.raises(ValueError):
        steps_for(-1, 15)
    with pytest.raises(ValueError):
        steps_for(10, 0)


def test_load_example_rules_and_selector():
    rules = {rule.alert: rule for rule in load_rules("examples/rules.yml", 15)}
    assert rules["HighCPUTypical"].rule.pending_steps == 20
    assert rules["HighCPUTypical"].selector == "cpu_usage"
    below = {rule.alert: rule for rule in load_rules("examples/rules_below.yml", 15)}
    assert below["LowHeadroom"].rule.direction == "below"
    assert below["LowHeadroom"].rule.keep_firing_steps == 20


def test_unsupported_expression_is_explicit(tmp_path):
    path = tmp_path / "rules.yml"
    path.write_text(
        "groups:\n- name: g\n  rules:\n  - alert: A\n    expr: rate(errors_total[5m]) > 1\n"
    )
    parsed = load_rules(path, 15)[0]
    assert parsed.rule is None
    assert "bare metric selector" in parsed.reason


def test_label_matchers_and_compound_promql_are_rejected(tmp_path):
    path = tmp_path / "rules.yml"
    path.write_text(
        "groups:\n- name: g\n  rules:\n  - alert: A\n    expr: 'cpu_usage{job=\"api\"} > 3'\n"
    )
    assert load_rules(path, 15)[0].rule is None


def test_non_finite_number_is_not_supported(tmp_path):
    path = tmp_path / "rules.yml"
    path.write_text("groups:\n- name: g\n  rules:\n  - alert: A\n    expr: cpu_usage > 1e999\n")
    assert load_rules(path, 15)[0].rule is None


def test_pending_rounds_up_to_whole_evaluations(tmp_path):
    path = tmp_path / "rules.yml"
    path.write_text(
        "groups:\n- name: g\n  rules:\n  - alert: A\n    expr: cpu_usage > 1\n    for: 20s\n"
    )
    assert load_rules(path, 15)[0].rule.pending_steps == 2


def test_kubernetes_prometheusrule_wrapper(tmp_path):
    path = tmp_path / "rules.yml"
    path.write_text(
        "spec:\n  groups:\n  - name: g\n    rules:\n    - alert: A\n      expr: cpu_usage >= 1\n"
    )
    parsed = load_rules(path, 15)[0]
    assert parsed.rule.inclusive and parsed.rule.direction == "above"


def test_duplicate_alert_names_are_rejected(tmp_path):
    path = tmp_path / "rules.yml"
    path.write_text(
        "groups:\n- name: g\n  rules:\n  - alert: A\n    expr: cpu_usage > 1\n  - alert: A\n    expr: cpu_usage < 2\n"
    )
    with pytest.raises(ValueError, match="duplicate alert name"):
        load_rules(path, 15)


def test_invalid_yaml_root_and_duration_are_reported(tmp_path):
    path = tmp_path / "rules.yml"
    path.write_text("- not a mapping\n")
    with pytest.raises(TypeError, match="root"):
        load_rules(path, 15)
    path.write_text(
        "groups:\n- name: g\n  rules:\n  - alert: A\n    expr: cpu_usage > 1\n    for: 5 minutes\n"
    )
    with pytest.raises(ValueError, match="bad duration"):
        load_rules(path, 15)


@pytest.mark.parametrize(
    "content,message",
    [
        ("groups: 7\n", "'groups' must be a list"),
        ("groups:\n- 7\n", "group at index 0"),
        ("groups:\n- name: g\n  rules: A\n", "rules in group"),
        ("groups:\n- name: g\n  rules:\n  - 7\n", "rule at index 0"),
        (
            "groups:\n- name: g\n  rules:\n  - alert: A\n    expr: 7\n",
            "expression for alert",
        ),
        ("spec: 7\n", "'spec' must be a mapping"),
    ],
)
def test_rule_yaml_wrong_types_raise_type_error(tmp_path, content, message):
    path = tmp_path / "rules.yml"
    path.write_text(content)
    with pytest.raises(TypeError, match=message):
        load_rules(path, 15)
