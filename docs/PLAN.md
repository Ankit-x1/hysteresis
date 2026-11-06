# Engineering plan

Work in small, reviewable commits. Do not build integrations on top of an unverified evaluation core.

The current release-candidate verification and owner-only publication checks
are recorded in [ENGINEERING_AUDIT.md](ENGINEERING_AUDIT.md) and
[RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md). Items below remain future work
unless explicitly checked; no release or publication action is implied.

## Milestone 0 — Establish a reproducible baseline (partially complete)

- [x] Run the test suite locally on Python 3.10, 3.11, 3.12, and 3.14; CI tests Python 3.10.
- [x] Run unit tests, Ruff, packaging and the CLI quickstart.
- [x] Record dependency and engine versions.
- [ ] Verify a clean checkout can reproduce the quickstart.

Done when a clean local environment can build and test the package.

## Milestone 1 — Correctness and fail-closed behavior (complete)

- [x] Reject unsupported PromQL expressions instead of guessing.
- [x] Preserve and verify the expression metric name against the benchmark metric name.
- [x] Return exit code 2 for unsupported rules, missing alerts, metric mismatches and invalid inputs.
- [x] Keep quality-budget failures separate with exit code 1.
- [x] Validate benchmark, rule, simulation and tuning parameters.
- [x] Report precision/recall as undefined when the denominator is zero.
- [x] Rename misleading `flaps/day` reporting to `episodes/day`.
- [x] Run current tests and close any regressions found.

Done when no invalid or partial evaluation can produce a green CI gate.

## Milestone 2 — Simulator fidelity (partially complete)

- [x] Run `tools/promtool_fidelity.py` against the exact Prometheus image pinned in CI.
- [ ] Add explicit property-based tests for state invariants and incident windows.
- [x] Keep minimal reproductions for every mismatch.
- [x] Verify the fidelity test catches an intentionally wrong expected alert.

Done when the pinned fidelity suite is green and the negative control fails as expected.

## Milestone 3 — Real-data ingestion

- [ ] Define a versioned benchmark schema and a documented CSV format.
- [ ] Support explicit timestamp and metric-name validation.
- [ ] Import labeled incident windows from a separate, auditable file.
- [ ] Add missing-data and sampling-gap tests.
- [ ] Test with controlled local fault injection before claiming production usefulness.

Done when a user can reproduce an evaluation from a documented real-data sample.

## Milestone 4 — Regression gates

- [ ] Define a baseline result format with schema version and tool version.
- [ ] Compare candidate rules against an accepted baseline, separately from absolute budgets.
- [ ] Add per-alert JSON output and deterministic exit behavior.
- [ ] Build a GitHub Action example and test it in a separate fixture repository.

Done when a change that worsens an agreed metric fails CI for an explicit, documented reason.

## Milestone 5 — Observability integrations

- [ ] Complete exporter -> Prometheus scrape -> alert evaluation -> state-history capture.
- [ ] Build an automated `query_range` comparison tool.
- [ ] Compare equivalent Grafana-managed alert configurations manually and record limitations.

Done when agreements and disagreements are reproducible on pinned versions.

## Milestone 6 — Optional ML experiment

- [ ] Add a seasonal forecasting baseline only after real labeled data exists.
- [ ] Use chronological train/validation/test splits.
- [ ] Compare against static thresholds using the same scoring contract.
- [ ] Report confidence intervals across independent seeds or time windows.

Done when the learned method demonstrates a measurable held-out improvement with trade-offs.

## Do not add yet

Full PromQL parsing, database, web UI, chatbot, LLM scoring, billing, hosted SaaS, Kubernetes operator, multi-vendor matrix, PPO tuner or differentiable tuning. Add complexity only in response to validated users and a concrete requirement.
