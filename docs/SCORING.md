# Scoring contract

All interval bounds are zero-based and half-open: `[start, end)`. The benchmark owns the evaluation interval, so delays are reported in seconds by multiplying sample offsets by `step_s`.

| Metric | Definition |
|---|---|
| Detected incident | At least one `FIRING` sample occurs inside the incident window. |
| Missed incident | The incident contains no `FIRING` sample. |
| Alert episode | One contiguous run of `FIRING` or `RECOVERING`. |
| False episode | No firing sample in the episode overlaps any labeled incident. |
| Redundant episode | A subsequent episode associated with an incident that already had a useful episode. |
| Extra alert episodes | False episodes plus redundant episodes. |
| Detection delay | First firing sample index minus incident start, multiplied by `step_s`. |
| Notifications | Two modeled notifications per episode (fire and resolve). |
| Precision | `(episodes - extra_alert_episodes) / episodes`; undefined (`None`) when there are no episodes. |
| Recall | `detected_incidents / incidents`; undefined (`None`) when there are no labeled incidents. |
| Episodes/day | Number of episodes divided by benchmark duration in days. |

Precision and recall use `None` for undefined denominators. The CLI renders those values as `n/a`; it does not silently map an empty evaluation to 1.0. `episodes/day` is not a flapping metric. Hysteresis does not yet define a flap window or count transitions within one alert episode.

The false/redundant episode assignment is intentionally simple for the current one-series benchmark: an episode is associated with the first incident window containing one of its firing samples. This policy should be revisited before supporting overlapping/merged incidents or multi-series alert instances.
