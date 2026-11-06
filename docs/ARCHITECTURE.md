# Architecture and v0.1 input contract

## Data flow

```text
Prometheus-style rule file ──> narrow rule parser ──┐
                                                    ├─> simulator ─> score ─> report / CI gate
Benchmark (.npz) ──> one named metric + incidents ─┘
```

A `Bench` contains one one-dimensional numeric series, an evaluation interval, a metric name, and zero or more non-overlapping labeled incident windows. `NaN` is allowed as a missing sample; positive and negative infinity are rejected. Saved archives use schema version 1; earlier archives without a schema field load as legacy version 0, while unknown future versions are rejected.

The parser accepts one expression form: a bare Prometheus metric name, a comparison operator (`>`, `>=`, `<`, `<=`), and a finite numeric literal. The metric name must equal the benchmark's metric name. This restriction is deliberate: scoring `memory_usage > 70` against a `cpu_usage` array would produce a syntactically plausible but meaningless result.

Label matchers and general PromQL are not parsed or approximated. They return an unsupported-rule error. A future multi-series interface should pass the evaluated expression's actual time series into the simulator rather than silently ignoring the selector.

## State machine

Each array element is one evaluation point. The states are `NORMAL`, `PENDING`, `FIRING`, and `RECOVERING`. A true condition increments the pending counter; an alert fires after the configured number of additional evaluation steps. Once firing, `keep_firing_for` controls how many false evaluations remain in `RECOVERING` before resolution. `NaN` compares false and is treated as a missing sample at that evaluation point.

This is a discrete state model, not a full Prometheus engine. The `tools/promtool_fidelity.py` test checks selected `for`, `keep_firing_for`, comparison and staleness cases against a pinned Prometheus version.

## CI exit contract

- `0`: all selected rules evaluated and quality budgets passed.
- `1`: evaluation completed, one or more budgets failed.
- `2`: the result is not trustworthy because input is invalid or evaluation was incomplete.

Unsupported rules, missing alert names, no alert rules, metric-name mismatch, invalid benchmark input and empty selection are errors. Hysteresis must not convert them to a green quality gate.

## Boundaries

The v0.1 CLI scores a single series per benchmark. A rule file can contain multiple alert rules only when every selected rule refers to that same metric. To evaluate another metric, generate or ingest a benchmark with that metric name. Multi-metric vector matching and PromQL expression evaluation are future work.
