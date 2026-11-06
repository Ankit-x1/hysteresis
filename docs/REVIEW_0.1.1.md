# Historical engineering review — 0.1.1

> This file preserves the earlier source-archive review. Its verification
> results are historical, not the current release audit. See
> [ENGINEERING_AUDIT.md](ENGINEERING_AUDIT.md) and
> [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) for the current status.

## Source reviewed

The 0.1.1 update was made from the uploaded `hysteresis.zip` source archive. It is a source-code update, not a production-readiness certification.

## Changes made

### Critical correctness

- Unsupported alert expressions now cause an evaluation error (exit code 2). They cannot be silently skipped while `check` returns success.
- Missing alert names, empty alert files and metric-name mismatches now fail closed.
- The parser preserves the metric selector. The single-series benchmark includes a metric name, and the selector must match it exactly.
- The expression contract is now explicit: one bare metric name, one comparison operator, and one finite numeric threshold. Label matchers and general PromQL are rejected.
- Quality-budget failures remain exit code 1; invalid or incomplete evaluations are exit code 2.
- A configured maximum detection delay no longer treats an undefined delay as zero.

### Data and metric correctness

- Benchmark generation validates durations, intervals, noise parameters, AR coefficients, incident ranges, sample counts and metric names.
- Benchmark archives are loaded with `allow_pickle=False`, validate their arrays and use a schema version. Legacy archives without a schema/metric name load as schema 0 with `cpu_usage` as the metric name.
- Simulator rules validate finite thresholds and integer step counts. Input signals must be one-dimensional; NaN represents missing data and infinity is rejected.
- Precision and recall are undefined when their denominators are zero and are rendered as `n/a`.
- `flaps/day` was replaced with `episodes/day`; a true flap metric is not implemented.
- `extra_pages` was clarified and renamed internally to `extra_episodes`; the previous property and CLI flag remain compatibility aliases.
- Duration formatting now preserves millisecond settings.

### Tuning and integration

- Tuning validates candidate counts, limits and weights; exposes loss weights and search limits through the CLI; and uses infinite delay for tie-breaking when no incident was detected.
- The tuning CLI requires labeled incidents in both training and held-out partitions.
- The live exporter plays a benchmark once, on the benchmark's sample clock, and writes matching wall-clock incident windows. It no longer loops a first-cycle label file over subsequent cycles.
- The bundled live harness explicitly requires 15-second benchmark steps to match its Prometheus evaluation interval.
- Prometheus and Grafana container tags are pinned; local ports bind to localhost; Grafana password is configurable and required.
- At the time of this historical review, CI was described as testing Python
  3.10–3.13. The current workflow checks Python 3.10 only; its test, fidelity,
  rule-gate and Compose-configuration jobs are described in the current audit.

## Verification recorded at the time of the historical review

| Check | Result |
|---|---|
| Unit / regression suite | 71 passed |
| Python compileall | Passed |
| CLI benchmark generation | Passed |
| CLI score | Passed |
| CLI tuning | Passed |
| CLI quality gate | Passed |
| Seeded multi-seed smoke experiment (3 seeds, 25 trials) | Completed |
| Wheel build | Passed |
| Install wheel into isolated target directory | Passed |
| Run CLI from installed wheel | Passed |
| YAML parse of workflow, Compose, Prometheus and rule files | Passed |
| Ruff check / Ruff format | Not run; Ruff is not installed in this environment |
| Prometheus fidelity test | Not run; `promtool` and Docker are not installed |
| Docker Compose startup / live Grafana comparison | Not run; Docker is not installed |

The local tests in that review ran on Python 3.13.5. The results above are
preserved as historical evidence and should not be substituted for checks on a
later source revision.

## Known limitations that remain

- The benchmark still contains one metric series. General PromQL evaluation, label matchers, joins, aggregations and multi-series alerts are not supported.
- The current `check` command enforces absolute quality budgets. Candidate-versus-baseline regression comparison and stored baselines are not implemented yet.
- Real metric ingestion (CSV/Prometheus `query_range`) is not implemented yet.
- The Docker exporter is a one-pass real-time player; automated live-state comparison is not implemented yet.
- The scoring policy associates an alert episode with the first incident window it overlaps. Revisit this before multi-series or complex incident correlation.
- No external user validation has been performed. Synthetic results do not establish production usefulness.
- The optional ML forecasting baseline has not been implemented.

## Current direction

Do not treat the optional Compose demonstration as a release prerequisite.
Remaining product work is tracked in [PLAN.md](PLAN.md); the current release
verification and owner actions are listed in
[ENGINEERING_AUDIT.md](ENGINEERING_AUDIT.md).
