# Engineering audit — release candidate

Audit record: 2026-11-06. This is a local source and packaging review, not a
production-readiness certification or a record of GitHub Actions results.

## Assessment

The current source passed the recorded unit, lint, formatting, compilation,
packaging, isolated-wheel CLI, and pinned Prometheus checks. Hysteresis remains
an experimental single-series evaluator with a deliberately narrow rule
contract; the fidelity results do not establish general PromQL conformance or
production usefulness.

The source is ready for the owner to complete release-specific review. A first
public release still requires the owner to inspect release contents and
history, confirm the version and release notes, and review any desired CI
results. No Git operation, tag, publication, Compose startup, or Grafana test
was performed as part of this audit.

## Current behavior and scope

```text
supported rule YAML + one-series labeled benchmark
    -> strict parser -> discrete simulator -> scoring -> report / CI gate
                                      \-> seeded tuning on train, scored on holdout
```

The accepted expression is a bare metric name followed by one of `>`, `>=`,
`<`, or `<=` and a finite number. `for` and `keep_firing_for` are translated
to discrete evaluation steps. A rule selector must match the benchmark's
single metric name. Unsupported PromQL is rejected rather than approximated.

The simulator has `NORMAL`, `PENDING`, `FIRING`, and `RECOVERING` states;
`NaN` is a missing sample. Scoring counts an incident as detected only when a
`FIRING` sample occurs inside its labeled interval. An alert episode is a
contiguous `FIRING`/`RECOVERING` run. Precision and recall are undefined when
their denominators are zero. `check` returns 0 for passed budgets, 1 for a
completed budget failure, and 2 when the evaluation cannot be trusted.

## Changes in this finalization pass

- Fixed a native Windows CLI failure: the tuning summary used a Unicode arrow
  that could not be encoded by a standard PowerShell console. It now prints
  the ASCII `->`, with a regression assertion and an isolated installed-wheel
  smoke test.
- Reworked README and development instructions to use Windows PowerShell
  commands and disposable benchmark paths; clarified that Docker Compose and
  Grafana are optional and not used by the fidelity test.
- Corrected the historical 0.1.1 review's stale CI matrix statement and marked
  its results as historical.
- Added `promtool-failures/` to `.gitignore` so generated reproduction data is
  not accidentally included in release contents.
- Updated the engineering plan and release checklist to distinguish current
  verification, owner checks, and deferred work.

## Verification actually run

| Check | Observed result |
|---|---|
| `uv sync --extra dev` | Succeeded; 16 packages resolved, 10 audited |
| `uv run --python 3.10 --extra dev pytest -q` | 113 passed, 1 Docker test skipped |
| Same pytest command on Python 3.11 | 113 passed, 1 Docker test skipped |
| Same pytest command on Python 3.12 | 113 passed, 1 Docker test skipped |
| Same pytest command on Python 3.14 | 113 passed, 1 Docker test skipped |
| `uv run ruff check .` | All checks passed |
| `uv run ruff format --check .` | 27 files already formatted |
| `uv run python -m compileall -q src tests tools harness` | Passed |
| `uv lock --check` | Passed; lock resolves 16 packages |
| Package build in isolated temporary source copy | Wheel and sdist built as `hysteresis-0.1.1` |
| Isolated wheel install on Python 3.10, run outside the repository | Passed; `--help`, `bench`, `score`, `tune`, and `check` all ran |
| Wheel/sdist contents | Wheel had 8 Python modules and a console entry point; sdist contained `pyproject.toml`, README, LICENSE, and package CLI source |
| Docker engine | Client/server 29.0.1; Docker Desktop 4.53.0 |
| Pinned image | `prom/prometheus:v3.15.1` was present locally |
| `uv run python tools\promtool_fidelity.py --trials 200 --docker prom/prometheus:v3.15.1` | 200/200 trials agree; 0 mismatches |
| Opt-in Docker pytest | 1 passed, including a deliberately incorrect expected-alert control |
| Fidelity container cleanup | No containers matching `hysteresis-promtool-` remained |
| Local repository text scan | No candidate secret patterns found; no local `.env` file was present |

The package smoke test exposed the Windows output-encoding defect described
above. The output was changed to ASCII and the package was rebuilt and retested
successfully. The Docker checks were completed before that unrelated CLI text
change; no fidelity code changed afterward.

The secret-pattern scan is heuristic and covers current text files only. It
does not inspect Git history, and no Git command was run. The available
workflow configuration was inspected, but no GitHub Actions run was observed.
CI currently tests Python 3.10; the additional Python versions above were
local test runs, not a CI matrix.

## Prometheus fidelity details

The tool probes and records the `promtool` version. The `--docker` argument
chooses the image; the harness does not force an arbitrary user-supplied image
to be v3.15.1. The release comparison and CI command explicitly select
`prom/prometheus:v3.15.1`.

Each case writes a generated `rules.yml` and `test.yml` in a temporary
directory, then runs `promtool test rules test.yml` in a one-shot container
with a read-only bind mount. The generated `alert_rule_test` asserts the
simulator's expected active-alert labels at every 15-second sample. Prometheus
3.15.1 returns exit 0 with `SUCCESS` on stdout and empty stderr for a passing
case; a semantic assertion failure returns nonzero with `FAILED`, `exp`, and
`got` details. Unrecognized or infrastructure failures are not counted as
agreements or mismatches.

The harness keeps the Prometheus image's configured non-root user. The
temporary test directory and synthetic YAML files are made readable to that
user; on the tested Windows host Python's `chmod` result was readable by Docker
Desktop, and both the integration test and 200-trial run passed. Normal
containers are removed with `--rm`; timeout cleanup targets only the harness's
unique container name. Mismatch reproductions and unverified trial diagnostics
are kept in separate directories and are now ignored by `.gitignore`.

The 200 generated trials sample four threshold operators around 80, 15-second
steps, `for` and `keep_firing_for` values from zero through four steps, one
labeled series, and stale/missing samples. They do not exhaust all possible
state sequences, durations, PromQL expressions, labels, or series counts.

## Packaging, CI, and repository hygiene

- `pyproject.toml` declares Python `>=3.10`, runtime dependencies NumPy and
  PyYAML, optional development dependencies pytest/Ruff, the optional
  Prometheus exporter dependency, and the `hysteresis` console script.
- The wheel and source distribution built from an isolated copy and the wheel
  ran from outside the source tree. No global Python installation was changed.
- The tested sdist includes `tests/` but does not include `docs/`, `tools/`, or
  `harness/`. This does not affect wheel installation; those developer and
  integration resources are available in the source repository, not this sdist.
- `.gitignore` excludes virtual environments, caches, build outputs, generated
  benchmark files, local environment files, package metadata, and fidelity
  reproduction artifacts. The existing `src/hysteresis.egg-info` and
  `bench/default.npz` were left untouched.
- The CI workflow tests Python 3.10, runs Ruff and pytest, builds a wheel, runs
  pinned Prometheus fidelity, checks the example rule gate, and validates
  Compose configuration. The Compose-config job does not start the stack.
- No local Docker Compose, Prometheus service, exporter, or Grafana service was
  started. Their live behavior is not verified by this audit.

## Known limitations and deferred work

- No full PromQL parser/evaluator, label matchers, aggregations, joins,
  vector matching, multiple metrics, or multiple series.
- No real-data/CSV/Prometheus `query_range` importer.
- No stored candidate baseline or candidate-versus-baseline regression
  comparison; `check` enforces absolute budgets.
- The optional recovery threshold is a simulator setting and is not emitted
  as a Prometheus rule field.
- Episode-to-incident matching is intentionally simple; see
  [SCORING.md](SCORING.md).
- The live exporter and optional Compose stack have not been exercised here;
  automated live Grafana state comparison is not implemented.
- Synthetic data and local fidelity checks do not establish production
  usefulness, full Prometheus compatibility, or user validation.

Future work is tracked in [PLAN.md](PLAN.md). The release-specific owner steps
are in [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md).
