"""Expose one benchmark pass as a live Prometheus gauge.

The exporter advances at exactly ``bench.step_s`` and never loops the series.
That keeps the generated wall-clock incident labels aligned with the offline
benchmark. After the final sample, the gauge is set to NaN (missing sample).

    pip install -e '.[live]'
    python harness/exporter.py --bench bench/default.npz
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from prometheus_client import Gauge, start_http_server

from hysteresis.bench import Bench


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bench", required=True)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--labels", default="bench/live_incidents.json")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    bench = Bench.load(args.bench)
    if bench.step_s != 15.0:
        parser.error(
            "the bundled Prometheus configuration evaluates every 15s; "
            "generate this live-comparison benchmark with --step 15"
        )
    label_path = Path(args.labels)
    label_path.parent.mkdir(parents=True, exist_ok=True)

    gauge = Gauge(bench.metric_name, "Hysteresis benchmark signal")
    start_http_server(args.port)
    wall_start = time.time()
    monotonic_start = time.monotonic()
    windows = [
        {
            "start": wall_start + incident.start * bench.step_s,
            "end": wall_start + incident.end * bench.step_s,
        }
        for incident in bench.incidents
    ]
    label_path.write_text(
        json.dumps(
            {
                "metric_name": bench.metric_name,
                "t0_unix_seconds": wall_start,
                "step_seconds": bench.step_s,
                "sample_count": len(bench.values),
                "incidents": windows,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        f"serving {bench.metric_name} on :{args.port}; {len(bench.values)} samples at "
        f"{bench.step_s:g}s; incident labels written to {label_path}"
    )
    for index, value in enumerate(bench.values):
        target = monotonic_start + index * bench.step_s
        delay = target - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        gauge.set(float(value))
    end_target = monotonic_start + len(bench.values) * bench.step_s
    delay = end_target - time.monotonic()
    if delay > 0:
        time.sleep(delay)
    gauge.set(float("nan"))
    print("benchmark playback complete; metric is now NaN (missing)")

    # Keep the exporter alive so Prometheus can observe the terminal missing sample.
    while True:
        time.sleep(60)


if __name__ == "__main__":
    raise SystemExit(main())
