# Dataset schema

The current campaign uses folders such as:

```text
phase-phase4_perturbed_attacked_perturbation-P2_lam0.50_attackDuration-20_intensity-medium_20260426T043520Z/
  iteration-3/
    scenario.json
    config.json
    sysdig_logs.ndjson
    tep_signals.ndjson
    tep_controller_mv_commands.ndjson
    tep_alarm_events.ndjson
    annotations.ndjson
    attack_records.ndjson
    container_*.log
```

## Parsed folder fields

The parser extracts:

- `phase`: e.g. `phase1_clean_benign`, `phase2_clean_attacked`,
- `perturbation_family`: `none`, `P1`, `P2`, `P3`, `P4`, `P5`,
- `severity`: numeric value from `lam0.50`, if present,
- `attack_duration`: numeric seconds,
- `attack_intensity`: e.g. `medium` or empty string,
- timestamp,
- `iteration`: from `iteration-X`.

The parser gives priority to folder-derived fields because early `scenario.json` files may still contain older categorical perturbation labels such as `moderate`.

## Primary detector inputs

- Runtime view: `sysdig_logs.ndjson`
- Process view: `tep_signals.ndjson`
- Controller view: `tep_controller_mv_commands.ndjson`

Alarm events are not included by default but can be enabled for diagnostics.

## Timestamp handling

The code supports Unix nanoseconds, Unix milliseconds, Unix seconds, and ISO timestamps.
