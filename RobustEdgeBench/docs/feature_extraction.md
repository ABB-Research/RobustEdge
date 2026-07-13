# Feature extraction

The feature extraction follows the ABB zero-day container attack-detection idea of forming a bag-of-system-calls representation from runtime traces. Each sysdig row is interpreted as a monitoring window. Runtime features are extracted from `fields.*`.

## Runtime features

Example sysdig row:

```json
{
  "time": 1777178160120300032,
  "measurement": "sysdig",
  "tags": {"container_name": "influxdb"},
  "fields": {"read": 1426, "write": 1405, "futex": 4978}
}
```

Features:

```text
rt_read, rt_write, rt_futex, ...
```

## Process features

`tep_signals.ndjson` is converted to tag-specific last-observation-carried-forward features aligned to sysdig windows. For each signal tag and numeric field (`value`, `command`, `feedback`) the extractor computes:

- last value at the end of each sysdig window,
- optional delta relative to the previous window,
- update counts by record type/category.

## Controller features

`tep_controller_mv_commands.ndjson` is aligned to sysdig windows and converted into:

- last command value per MV,
- command delta per MV,
- command update count.

## Default feature view

The default `fused` view combines runtime + process + controller features. This is scientifically motivated because perturbations are applied to telemetry delivery and may affect both the workload observed in syscalls and the observed process/control streams.
