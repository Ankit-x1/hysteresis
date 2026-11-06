# Development setup (Windows PowerShell)

This guide covers native Windows 11 development with PowerShell, VS Code, `uv`,
and Docker Desktop. WSL is not required. Python 3.10 is the CI baseline; the
package declares Python 3.10 or later.

## 1. Prerequisites

Required for the Python workflow:

- Windows 11 and PowerShell.
- Python 3.10 or later, managed by `uv` or already installed.
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/).
- VS Code is optional but is the project owner's editor.

Docker Desktop is needed only for the Prometheus fidelity test or the separate
optional Prometheus/Grafana demonstration. Docker Compose and Grafana are not
needed for ordinary development, unit tests, package installation, or the
fidelity test.

## 2. Install development dependencies

Run these commands from the repository root:

```powershell
uv python install 3.10
uv sync --python 3.10 --extra dev
```

The `dev` extra installs pytest and Ruff. To develop the optional live exporter,
install its dependency as well:

```powershell
uv sync --python 3.10 --extra dev --extra live
```

## 3. Run checks and the CLI

```powershell
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run python -m compileall -q src tests tools harness
```

The Docker integration test is marked and skipped during ordinary pytest runs.
Use a unique disposable path for generated benchmark data so an existing
benchmark is never overwritten:

```powershell
$bench = Join-Path $env:TEMP "hysteresis-dev-$([guid]::NewGuid().ToString('N')).npz"
uv run hysteresis bench --out $bench --seed 0 --days 14
uv run hysteresis score examples\rules.yml --bench $bench
uv run hysteresis tune examples\rules.yml --bench $bench --alert HighCPU
uv run hysteresis check examples\rules.yml --bench $bench --alert HighCPUTypical --max-missed 2 --max-extra-episodes 10
```

`check` returns 0 when all configured budgets pass, 1 when evaluation
completes but a budget fails, and 2 when the evaluation cannot be trusted.
`tune` evaluates candidate settings on the first chronological partition and
prints scores on the held-out second partition; this is not evidence of
generalization beyond the benchmark.

## 4. Docker prerequisites

Start Docker Desktop yourself and wait until its engine is ready. This audit
does not start or stop Docker Desktop. Check both the CLI and engine:

```powershell
docker version
```

The output should contain both **Client** and **Server** sections. The pinned
image can be checked or pulled explicitly:

```powershell
docker image inspect --format '{{.Id}}' prom/prometheus:v3.15.1
if ($LASTEXITCODE -ne 0) {
    docker pull prom/prometheus:v3.15.1
}
```

Manual pulling is optional: Docker pulls the image automatically when the
fidelity command needs it. You do not need to create an image or container in
advance, use the Docker Desktop GUI after the engine is ready, run Compose, or
run Grafana.

## 5. Prometheus fidelity test

Run a short check first, then the full 200-trial comparison:

```powershell
uv run python tools\promtool_fidelity.py --trials 1 --docker prom/prometheus:v3.15.1
uv run python tools\promtool_fidelity.py --trials 200 --docker prom/prometheus:v3.15.1
```

A successful full run prints:

```text
200/200 trials agree with promtool; 0 mismatches
```

Each generated test contains expected alert assertions at every 15-second
evaluation point. The harness runs `promtool test rules`, probes and records
the engine version, and requires exit code 0 with exactly `SUCCESS` on stdout
and empty stderr. The image is selected by `--docker`; use the pinned image
above when reproducing the project's recorded comparison.

Each run uses one-shot temporary containers and read-only mounts of generated
test files. Docker's `--rm` removes normal containers; after a timeout, the
harness tries to remove only its uniquely named container. Verify no harness
containers remain with:

```powershell
docker ps -a --filter "name=hysteresis-promtool-"
```

The Docker integration test also checks a deliberately wrong expected alert.
Run it explicitly with a process-local environment variable that is restored
even if pytest fails:

```powershell
$previous = [Environment]::GetEnvironmentVariable("HYSTERESIS_RUN_DOCKER_TESTS", "Process")
try {
    $env:HYSTERESIS_RUN_DOCKER_TESTS = "1"
    uv run pytest -q -m docker tests\test_promtool_fidelity_docker.py
    if ($LASTEXITCODE -ne 0) {
        throw "Docker integration test failed with exit code $LASTEXITCODE"
    }
}
finally {
    if ($null -eq $previous) {
        Remove-Item Env:\HYSTERESIS_RUN_DOCKER_TESTS -ErrorAction SilentlyContinue
    }
    else {
        $env:HYSTERESIS_RUN_DOCKER_TESTS = $previous
    }
}
```

### Results and reproductions

- Exit 0: every generated test completed and all assertions agreed.
- Exit 1: Prometheus ran the assertions and rejected an expected alert state.
  Mismatch inputs and outputs are saved under
  `promtool-failures\trial-NNNN\`.
- Exit 2: the result was unverified, for example because Docker, a mount, YAML
  generation, permissions, timeout, or output validation failed. Such a trial
  is not counted as either an agreement or a mismatch. Available diagnostics
  are saved under `promtool-failures\harness-errors\trial-NNNN\`.

To reproduce a saved mismatch in PowerShell:

```powershell
Push-Location promtool-failures\trial-0000
try {
    $casePath = (Resolve-Path .).Path
    docker run --rm --mount "type=bind,source=$casePath,target=/t,readonly" --workdir /t --entrypoint promtool prom/prometheus:v3.15.1 test rules test.yml
}
finally {
    Pop-Location
}
```

Inspect `trial.json`, `rules.yml`, `test.yml`, `series.txt`,
`simulator_states.csv`, `stdout.txt`, `stderr.txt`, and the saved version
output. A mismatch is not a Docker or file-access error.

The sampled comparison covers `>`, `>=`, `<`, and `<=` around a threshold of
80, one labeled series, 15-second steps, `for` and `keep_firing_for` values
from zero through four steps, and missing/stale samples. It does not establish
full PromQL conformance and does not cover multiple series, label matchers,
annotations, recording rules, custom recovery thresholds, or
Grafana-managed alerting.

## 6. Optional local Prometheus/Grafana demonstration

This separate manual demonstration is not part of the fidelity test. It
requires Docker Compose, Grafana, and a local password file; it is not required
for development checks or package use. This audit did not start or validate
the stack.

Install the optional exporter dependency and create the local environment file
only if it does not already exist. Edit the placeholder password before
starting the stack, and do not publish `harness\.env`:

```powershell
uv sync --extra dev --extra live
if (-not (Test-Path harness\.env)) {
    Copy-Item harness\.env.example harness\.env
}
notepad harness\.env
```

The bundled Prometheus configuration and exporter require a 15-second sample
step. Playback takes one sample interval per sample; a 0.1-day benchmark takes
about 2 hours and 24 minutes. Run the exporter in one terminal:

```powershell
$liveBench = Join-Path $env:TEMP "hysteresis-live-$([guid]::NewGuid().ToString('N')).npz"
uv run hysteresis bench --out $liveBench --seed 0 --days 0.1 --step 15
uv run python harness\exporter.py --bench $liveBench
```

Only when you intentionally want to start the optional local services, use a
second terminal:

```powershell
Set-Location harness
docker compose up
```

Prometheus and Grafana bind to localhost on ports 9090 and 3000. Playback is
real-time; stop the exporter with Ctrl+C after it completes. No Compose or
Grafana command is needed for `promtool_fidelity.py`.

## Troubleshooting

- **`uv` selects the wrong Python:** check `uv run python --version` and
  synchronize with `uv sync --python 3.10 --extra dev`.
- **Docker CLI is missing:** install Docker Desktop and open a new PowerShell
  window, then retry `docker version`.
- **Docker Server is unavailable:** start Docker Desktop manually and wait for
  the engine; this project does not start it automatically.
- **Image pull fails:** check Docker Hub access and available disk space; try
  the explicit `docker pull` command above.
- **Bind-mount or permission error:** it is a harness error, not a simulator
  mismatch. Review the saved harness-error artifacts and confirm Docker Desktop
  can access the Windows temporary directory.
- **Rule rejected:** review the [supported rule contract](../README.md#supported-rule-contract-v01);
  general PromQL is intentionally unsupported.
- **Quality gate returns 1:** inspect missed incidents, extra alert episodes,
  and detection delay against the configured budgets.
