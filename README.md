# Hysteresis

**Evaluate alert rules against labeled metric history before deploying them.** Hysteresis is an experimental, open-source alert-quality evaluation tool. It replays a deliberately small supported rule subset against a benchmark, scores detections and extra alert episodes, searches a few rule-timing settings, and can fail a CI job when a rule violates configured quality budgets.

> **Status: experimental v0.1.1.** Hysteresis is not a complete PromQL evaluator, a replacement for Prometheus, or a production alert-management system. Its current evaluation contract is intentionally narrow and documented below.

## What it does

1. Creates or loads a time-series benchmark with labeled incident windows.
2. Evaluates one threshold-style alert per metric series using a small explicit state machine.
3. Reports missed incidents, false and repeated alert episodes, detection delay, precision and recall.
4. Optionally searches for lower-loss pending / keep-firing settings.
5. Runs a strict CI gate: invalid or unsupported input is an evaluation error, not a passing result.

## Quickstart

Python 3.10+ is required. With [uv](https://docs.astral.sh/uv/):

```bash
uv sync --extra dev
uv run hysteresis bench --out bench/default.npz --seed 0 --days 14
uv run hysteresis score examples/rules.yml --bench bench/default.npz
uv run hysteresis tune examples/rules.yml --bench bench/default.npz --alert HighCPU
uv run hysteresis check examples/rules.yml --bench bench/default.npz \
  --alert HighCPUTypical --max-missed 2 --max-extra-episodes 10
```

Or with pip:

```bash
python -m pip install -e ".[dev]"
hysteresis bench --out bench/default.npz --seed 0 --days 14
hysteresis score examples/rules.yml --bench bench/default.npz
```

The example benchmark contains one metric named `cpu_usage`. `--metric-name` selects the series name stored in a newly generated benchmark:

```bash
hysteresis bench --out bench/cpu-idle.npz --metric-name cpu_idle --days 7
hysteresis score examples/rules_below.yml --bench bench/cpu-idle.npz
```

## Supported rule contract (v0.1)

Each benchmark contains **one one-dimensional metric series** and its incident windows. The supported expression form is exactly:

```promql
<metric_name> > 70
<metric_name> >= 70
<metric_name> < 20
<metric_name> <= 20
```

The metric name must exactly match the benchmark's `metric_name`. Hysteresis also reads `for` and `keep_firing_for` durations.

The following are intentionally unsupported and rejected rather than approximated:

- Label matchers such as `cpu_usage{job="api"}`.
- PromQL functions such as `rate()` or `histogram_quantile()`.
- Aggregations, joins, vector matching, compound expressions and multiple metrics.
- Recording rules as evaluation targets.

See `examples/unsupported_rules.yml` for a rule that is deliberately rejected. Do not interpret a rejected rule as a passing rule.

## CLI exit codes

| Exit code | Meaning                                                                                                                                                |
| --------: | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
|     `0` | Evaluation completed and, for `check`, all configured quality budgets passed.                                                                        |
|     `1` | Evaluation completed, but one or more quality budgets failed.                                                                                          |
|     `2` | Evaluation could not be trusted: invalid input, missing alert, metric mismatch, unsupported expression, corrupt benchmark, or no selected alert rules. |

This distinction is important for CI. Hysteresis never silently treats an unsupported or empty evaluation as a passing check.

## Metrics and scoring definitions

- **Detected incident:** at least one `FIRING` sample overlaps the labeled incident window.
- **Missed incident:** a labeled incident with no `FIRING` sample.
- **Alert episode:** one contiguous run of `FIRING` and/or `RECOVERING` states.
- **False episode:** an alert episode with no firing sample in any incident window.
- **Redundant episode:** a subsequent episode associated with an incident that already had a useful episode.
- **Extra alert episodes:** false plus redundant episodes. This is an episode count, not the number of notification messages.
- **Notifications:** modeled as two messages per episode (one firing and one resolved notification).
- **Detection delay:** time from incident start to its first firing sample; the mean is calculated over detected incidents.
- **Precision / recall:** reported as `n/a` when the corresponding denominator is zero; they are not presented as perfect scores for an empty result.

`episodes/day` is deliberately not called `flaps/day`: a true flap metric needs an explicit transition/window definition and is not implemented yet.

## Tuning

`tune` uses deterministic seeded random search. The current rule is always included as the baseline candidate, so the selected rule cannot have a worse training loss than the starting rule. This is **not** evidence of generalization. The CLI tunes on the first chronological partition and reports on the held-out second partition. Use multiple benchmark seeds and real, labeled data before drawing conclusions.

Loss weights can be changed explicitly:

```bash
hysteresis tune examples/rules.yml --bench bench/default.npz --alert HighCPU \
  --trials 500 --weight-missed 10 --weight-extra 1 --weight-delay 1 \
  --max-delay 300 --max-pending 1800 --max-keep 3600
```

## Validation

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run python tools/promtool_fidelity.py --trials 200 --docker prom/prometheus:v3.15.1
```

The Prometheus fidelity tool generates random state-machine cases and asks `promtool test rules` to validate the simulator's expected states. A fidelity pass is evidence only for the tested semantics and pinned Prometheus version; it does not establish conformance for the full PromQL language or Grafana-managed alerting.

## Local Prometheus + Grafana harness

The harness is for manual live comparison, not yet an automated end-to-end fidelity test. It plays one benchmark pass at the benchmark's sample interval and writes wall-clock incident labels.

```bash
uv sync --extra live
hysteresis bench --out bench/default.npz --seed 0 --days 0.1
cp harness/.env.example harness/.env
# Edit harness/.env and change GRAFANA_ADMIN_PASSWORD.
python harness/exporter.py --bench bench/default.npz
```

In another terminal:

```bash
cd harness
docker compose up
```

Prometheus is available at `http://localhost:9090`; Grafana is at `http://localhost:3000`. Both ports bind to localhost. The exporter runs on the host at port 8000 and plays the entire benchmark in real time (sample count × 15 seconds); start with a short benchmark. The current stack pins Prometheus `v3.15.1` and Grafana OSS `13.0.2` as starting versions; rerun the integration checks before changing them. The bundled Prometheus rule evaluation interval and exporter contract are both 15 seconds, so generate the live benchmark with the default `--step 15`. Docker is not required for the unit-test suite.

## Repository layout

```text
src/hysteresis/   simulator, benchmark, rule parser, scoring, tuning, CLI, reports
tests/            unit, validation and CLI regression tests
tools/            Prometheus differential fidelity and multi-seed experiments
harness/          one-pass exporter and local Prometheus/Grafana Compose stack
examples/         supported and deliberately unsupported rule examples
docs/             architecture, scoring contract and engineering plan
```

## Non-goals for v0.1

No full PromQL parser, database, web dashboard, chatbot, LLM integration, hosted SaaS, Kubernetes operator, multi-metric joins or learned anomaly detector. First establish that the narrow core is correct, reproducible and useful with real labeled histories.

## License

Apache-2.0. See [LICENSE](LICENSE).
