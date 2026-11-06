# Release checklist

Status recorded 2026-11-06. This checklist describes the local audit only.
It does not publish a release or replace the project owner's source-control
review.

## Completed and independently verified

- [x] Confirmed package metadata, Python floor, declared dependencies, license,
  and `hysteresis` console entry point.
- [x] Ran the test suite on Python 3.10, 3.11, 3.12, and 3.14; each run
  reported 113 passed and 1 Docker test skipped.
- [x] Ran Ruff lint, Ruff formatting, Python compilation, and lock consistency
  checks.
- [x] Built a wheel and source distribution in an isolated temporary source
  copy; installed the wheel into a temporary Python 3.10 environment and ran
  `--help`, `bench`, `score`, `tune`, and `check` outside the repository.
- [x] Reviewed the supported expression contract, scoring definitions,
  invalid-input behavior, and CLI exit codes.
- [x] Ran the Prometheus 3.15.1 Docker integration test, including its
  deliberately incorrect expected-alert control.
- [x] Ran 200 differential trials against
  `prom/prometheus:v3.15.1`: 200 agreed, 0 mismatches.
- [x] Confirmed no container with the `hysteresis-promtool-` name remained
  after those tests.
- [x] Checked current repository text for common secret patterns without
  printing matched values; no candidate pattern or local `.env` file was
  found.
- [x] Confirmed generated fidelity reproductions are ignored by the repository
  ignore file.

The local Python 3.11, 3.12, and 3.14 runs are not CI matrix results. The
current GitHub Actions workflow tests Python 3.10.

## Completed but not independently verified

- [ ] GitHub Actions outcomes: workflow configuration was inspected, but no
  remote workflow run was observed.
- [ ] Git history and the owner's eventual release diff: no Git commands were
  run. The local text scan is heuristic and cannot establish whether a
  credential was committed in the past.
- [ ] A clean checkout by an independent user: packaging was built from an
  isolated source copy, but this was not a separate machine or checkout.

## Not applicable to this audit

- [x] Docker Compose, Grafana, and the live exporter are not required for the
  package or the `promtool` fidelity test. No local stack or service was
  started.
- [x] Tagging, publishing, and creating a GitHub release are owner-managed
  actions and were not performed.

If the release intends to claim the optional live Prometheus/Grafana
demonstration as verified functionality, validate it separately with a
disposable benchmark and record the versions and observations.

## Deferred engineering work

- [ ] Full PromQL evaluation, label matchers, aggregations, joins, vector
  matching, and multiple series.
- [ ] Real-data ingestion and versioned CSV/Prometheus query imports.
- [ ] Candidate-versus-baseline regression gates and stored evaluation
  baselines.
- [ ] Automated live Prometheus/Grafana state-history comparison.
- [ ] Broader deterministic/property-based state and scoring tests.

These are product roadmap items, not prerequisites for describing the current
experimental v0.1.1 scope accurately.

## Owner actions before publication

- [ ] Review the final source-control diff and decide which files belong in the
  release.
- [ ] Inspect relevant history and release artifacts for credentials,
  `.env` files, generated benchmark data, and unintended files. The local scan
  did not inspect Git history.
- [ ] Confirm the first-public-release version and reconcile the README,
  changelog, and release description.
- [ ] Confirm the sdist's scope is acceptable: it includes package source and
  tests but omits `docs/`, `tools/`, and `harness/`.
- [ ] Review any GitHub Actions results required by the project's release
  policy.
- [ ] Publish only after those owner checks; publication is not complete.
