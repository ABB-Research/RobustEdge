# config.json Reference

All non-secret runtime configuration lives in `config.json` at the repository root. Secrets (passwords) go in `.env` — see `.env.example`.

---

## Credential precedence

For the `influxdb` section, values are applied in this order (last wins):

1. `config.json` → `influxdb` (committed defaults)
2. `.env` file
3. `os.environ`

This means Docker Compose environment variables always win.

---

## Loading in code

```python
from simulator.common.config_loader import load_config

influxdb = load_config("influxdb")   # returns the influxdb dict
campaign = load_config("campaign")   # returns the campaign dict
```

---

## Section reference

### `influxdb`

| Key | Default | Description |
|---|---|---|
| `host` | `localhost` | InfluxDB hostname. Override via `INFLUXDB_HOST` in `.env` or environment. Inside Docker Compose use the service name `influxdb`. |
| `port` | `8086` | InfluxDB port. Override via `INFLUXDB_PORT`. |
| `database` | `appdb` | Database name. Override via `INFLUXDB_DB`. |
| `username` | `admin` | Admin username. Override via `INFLUXDB_ADMIN_USER`. |
| `password` | `change_me_admin_password` | Admin password fallback — always override via `INFLUXDB_ADMIN_PASSWORD` in `.env`. The Grafana datasource provisioning reads the same variable so credentials stay in sync. If Grafana fails to authenticate, re-run `python3 grafana/render_provisioning.py` and restart Grafana. |

---

### `attack_agent`

| Key | Default | Description |
|---|---|---|
| `measurement` | `attack_records` | InfluxDB measurement to write attack points into |
| `tag` | `host` | Tag value on every written point |
| `burstDuration` | `10` | Duration of one burst in seconds |
| `burstPps` | `1000` | Points per second during a burst |
| `burstValue` | `0` | Fixed field value written to each attack point |
| `timeout` | `3` | Request timeout in seconds |

---

### `sysdig`

Controls optional host-side syscall collection. Requires `sysdig` installed on the host and passwordless `sudo`.

| Key | Default | Description |
|---|---|---|
| `windowSize` | `4` | Aggregation window in seconds passed to the chisel |
| `containers` | `["influxdb"]` | Container names to monitor |

The campaign runner runs: `sudo sysdig -c .chisels/count_syscalls <windowSize> container.name=<name>`

---

### `campaign`

Controls `scripts/run_data_campaign.py`. All values can be overridden with CLI flags.

| Key | Default | Description |
|---|---|---|
| `compose_file` | `docker-compose.yml` | Compose file to use |
| `output_root` | `./output` | Root folder for per-run output |
| `test_duration` | `180` | Seconds each run stays up (stack start → export) |
| `iterations` | `1` | Runs per scenario combination |
| `perturbation_profiles` | list of `{name, family, lambda}` | Campaign profile sweeps used by `scripts/run_data_campaign.py` |
| `attacks` | baseline plus 20/100/300s `medium` attack entries | Structured list of attack profiles. Each entry must include `duration` (seconds); for any entry with `duration > 0` include `attack_start_delay_min` and `attack_start_delay_max`. |

#### Example: Structured `attacks` configuration

To run both a baseline (no attack) and a 20s attack at `medium` intensity, set:

```json
"attacks": [
	{ "duration": 0 },
	{ "duration": 20, "intensities": ["medium"], "attack_start_delay_min": 10, "attack_start_delay_max": 300 }
]
```


Each item in the `attacks` list is an object that can contain the following fields. Fields marked **optional** can be omitted; sensible defaults are used when not present. For any attack entry with `duration > 0`, `attack_start_delay_min` and `attack_start_delay_max` are required and will be used to pick the randomized attack start. Campaign-level `attack_start_delay_*` keys are deprecated and ignored by the runner.

- `duration` (integer, seconds) — Required. Total length of the attack in seconds. Use `0` to indicate a baseline/no-attack run.
- `burstDuration` (integer, optional) — Override the internal burstDuration used by the attack agent for this attack entry.
- `burstValue` (number, optional) — Override the fixed value written by the attack agent for this entry. If omitted the agent defaults to `0.0`.
- `tag` (string, optional) — Tag to attach to attack points written by the attack agent (defaults to `campaign`/`attack_agent.tag`).
- `description` (string, optional) — Human-readable description to help identify the attack profile in logs and output folders.
 - `attack_start_delay_min` (integer) — Minimum seconds of baseline traffic before this attack starts. Required for `duration > 0`.
 - `attack_start_delay_max` (integer) — Maximum seconds of baseline traffic before this attack starts. Required for `duration > 0`.

Notes:

- When `duration` is `0` (baseline) the campaign runner will not wait for an attack delay and will not invoke the attack agent for that scenario.
- Fields not provided in an attack entry are inherited from campaign-level defaults where applicable.
- The structured `attacks` format makes it explicit which intensities belong to which durations and enables per-attack overrides for timing and attack parameters.

**`collect`** — which optional outputs to write per iteration (current committed config enables all of them):

| Key | Default | When enabled |
|---|---|---|
| `tep_signals` | `true` | Export `tep_signals.ndjson` (PV/MV telemetry) |
| `tep_alarm_events` | `true` | Export `tep_alarm_events.ndjson` |
| `tep_controller_mv_commands` | `true` | Export `tep_controller_mv_commands.ndjson` |
| `container_logs` | `true` | Save stdout/stderr from each Docker container |

`attack_records.ndjson`, `scenario.json`, `sysdig_logs.ndjson`, and `annotations.ndjson` are **always** written regardless of these flags.

Each iteration draws a random delay from the per-attack `[attack_start_delay_min, attack_start_delay_max]`. The actual value used is saved in `scenario.json`.


**Perturbation profiles** — the current campaign runner uses `campaign.perturbation_profiles`, a list of `{name, family, lambda}` objects. The `campaign.perturbations` string list remains only as a legacy fallback when `perturbation_profiles` is absent.

**Perturbation effect types explained:**

**PV drop**: Randomly skips (removes) individual data points, simulating sporadic sensor or network loss. The rest of the data stream continues as normal.
**Duplicates**: Randomly repeats some data points, simulating sensor or network glitches that cause the same value to be reported more than once.
**Timestamp jitter**: Randomly shifts the timestamp of each data point by a small amount (e.g., ±0.05s), simulating clock drift, network delays, or imprecise sampling.
**Out-of-order**: Delivers some data points out of their original sequence, so that timestamps may not be strictly increasing. This simulates buffering, network reordering, or delayed delivery.
**Outages**: Simulates short periods where no data is emitted at all (i.e., a sensor or network is completely silent for a while), creating longer gaps in the data.
**Downsample**: Systematically keeps only every Nth value (e.g., “every 2nd” or “every 3rd”), discarding the rest. This reduces the data frequency in a regular, predictable way, unlike random drops.

---


### `tep_controller`

Controls `simulator/tep_controller/controller.py`.

| Key | Default | Description |
|---|---|---|
| `telemetryMeasurement` | `tep_signals` | Measurement to read PV data from |
| `alarmStateMeasurement` | `tep_alarm_state` | Measurement to write continuous alarm states |
| `alarmEventMeasurement` | `tep_alarm_events` | Measurement to write alarm transition events |
| `alarmConfigMeasurement` | `tep_alarm_config` | Measurement to write alarm threshold config |
| `commandMeasurement` | `tep_controller_mv_commands` | Measurement to write MV commands (for audit/tracing only) |
| `intervalSeconds` | `1.0` | Controller polling interval (seconds) |

**MV Command Channel:**
The controller now sends MV commands directly to the simulator via UNIX sockets (see `MV_SERVER_UNIX` and `PV_TELEMETRY_UNIX`). This channel is used for real-time control and is independent of InfluxDB. InfluxDB is still used for logging/audit.

---


### `tep_process`

Controls `simulator/tep_process/`.

| Key | Default | Description |
|---|---|---|
| `duration` | `900.0` | Simulation horizon in simulated seconds. Only used for direct CLI runs. Docker Compose passes `--run-forever` so the simulator runs until stopped by `docker compose down`. |
| `seed` | `10871` | RNG seed for reproducibility |
| `realtimeScale` | `1.0` | Simulated seconds per real second |

**`rates`** — emission periods in simulated seconds:

| Key | Default | Description |
|---|---|---|
| `pressurePeriod` | `1.0` | Pressure PV emission period |
| `flowPeriodMin/Max` | `1.0 / 2.0` | Flow PV period range |
| `levelPeriodMin/Max` | `2.0 / 5.0` | Level PV period range |
| `temperaturePeriodMin/Max` | `5.0 / 10.0` | Temperature PV period range |
| `mvPeriod` | `1.0` | MV emission period |
| `compositionPeriodMin/Max` | `360.0 / 900.0` | Composition PV window range |
| `compositionDeadTime` | `120.0` | Dead time before a composition sample becomes visible |

**`influxdb`** (nested inside `tep_process`):

| Key | Default | Description |
|---|---|---|
| `measurement` | `tep_signals` | Measurement for PV/MV records |
| `eventMeasurement` | `tep_alarm_events` | Measurement for alarm events |

**`perturbations`** — simulator-local perturbation settings:

| Key | Default | Description |
|---|---|---|
| `silentSensors` | (list) | Channel names that are always silenced when a perturbation mode is active |
| `downsamplePVs` | (list) | Channel names to be downsampled in legacy profile mode (if empty or omitted, all PVs are eligible) |
| `seedOffset` | `1000` | Added to the main seed when deriving the perturbation RNG |

`silentSensors` is independent of the selected perturbation mode.
`downsamplePVs` restricts downsampling to only the listed PVs.

