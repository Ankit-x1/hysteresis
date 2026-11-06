# Changelog

## 0.1.1 — Engineering hardening

- Fail closed for unsupported alert expressions, missing alert names, empty rules and metric-name mismatches.
- Preserve each rule's metric selector and require it to match the single metric in the benchmark.
- Add benchmark, simulator, rule parser and tuner input validation.
- Validate loaded NumPy archives without enabling pickle loading; retain compatibility with earlier archives by defaulting the missing metric name to `cpu_usage`.
- Report undefined precision/recall as `n/a` rather than treating empty results as perfect.
- Replace misleading `flaps/day` output with `episodes/day`.
- Separate invalid-evaluation exit code `2` from quality-budget failure exit code `1`.
- Make the live exporter play the benchmark once at its exact sample interval and align incident labels with that playback.
- Pin the Prometheus and Grafana Compose image tags, bind local ports to localhost and require a configurable Grafana password.
- Add Apache-2.0 license, architecture/scoring docs and expanded regression tests.
- Keep tuning summary output ASCII-compatible with standard Windows PowerShell consoles.
