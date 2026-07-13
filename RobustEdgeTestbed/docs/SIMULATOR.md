# Tennessee Eastman Process (TEP) Simulator

## Quick Summary

The TEP Simulator is a realistic industrial process emulator that models a chemical refining operation with 73 monitored sensors (Process Variables) and 12 controllable actuators (Manipulated Variables). It runs in Docker alongside InfluxDB, a closed-loop controller, and an attack agent to generate labeled cybersecurity datasets where system behavior is recorded during normal operation and cyberattacks.

## Overview

The TEP Simulator is a detailed process control system that emulates the Tennessee Eastman chemical process. It generates labeled telemetry datasets under both normal and cyberattack conditions, suitable for developing and testing attack detection algorithms, security analytics, and machine learning models.

The simulator runs in Docker alongside:
- **InfluxDB** for telemetry storage
- **Controller** for closed-loop process control
- **Attack Agent** for burst-write attacks on InfluxDB
- **Grafana** for real-time visualization

## Architecture

### Components

#### 1. Process Simulator (`simulator/tep_process/`)
Simulates a chemical refining process with realistic dynamics:
- **Process Variables (PVs)**: 73 monitored sensor readings (pressures, flows, levels, temperatures, compositions)
- **Manipulated Variables (MVs)**: 12 controllable valve positions
- **Alarm System**: Multi-level alarms (LOLO, LO, NORMAL, HI, HIHI) per sensor
- **Dynamics**: Realistic time constants, dead times, and lag for each variable

The simulator uses continuous-time differential equations to model:
- Reactor pressure, temperature, and level
- Feed flows A, B, C, D
- Separator operations
- Stripper operations
- Product composition

**Direct MV Command Channel:**
The simulator receives real-time MV commands directly from the controller via a UNIX socket server (`MVCommandServer`). This channel is independent of InfluxDB, ensuring that process control is robust even if InfluxDB is under attack or unavailable. The simulator updates actuator positions immediately upon receiving new commands.

#### 2. Closed-Loop Controller (`simulator/tep_controller/`)
Maintains process stability by:
- Continuously monitoring PV telemetry from the simulator's UNIX telemetry socket (default 1 Hz)
- Computing corrective actions via control loops
- Writing Manipulated Variable (MV) commands to both InfluxDB (for audit/tracing) and directly to the simulator via UNIX socket (for real-time control)
- Supporting override rules for manual intervention

**Direct MV Command Delivery:**
The controller sends each MV command directly to the simulator's UNIX socket server, bypassing InfluxDB for real-time control. This ensures that controller-to-simulator updates are not affected by InfluxDB performance or attacks. InfluxDB is still used for logging and audit.

#### 3. Attack Agent (`attack_agents/influxdb_burst_attack.py`)
Simulates a cyberattack that bursts malicious data points into InfluxDB:
- Configurable burst duration, packet rate, and intensity
- Starts after a randomized baseline collection period
- Writes to the `attack_records` measurement for labeling

#### 4. Data Sinks
Output formats supported:
- **JSONL**: Line-delimited JSON to local files or stdout
- **InfluxDB**: Direct writing to the time-series database
- **Multi-sink**: Simultaneously write to multiple destinations

---

## Implementation Details

This section maps directly to the simulator code and explains what executes at runtime.


### Code Structure

- `simulator/tep_process/cli.py`:
  - Parses CLI options and merges defaults from `config.json` (`tep_process` section)
  - Builds `RateConfig` and the output sink chain
  - Instantiates and starts `MVCommandServer` (TCP/UNIX socket server for MV commands)
  - Instantiates and runs `TEPSimulator`, passing a function that fetches the latest MV commands from the socket server
- `simulator/tep_process/model.py`:
  - Defines runtime data classes (`VariableRuntime`, `MVRuntime`)
  - Implements `TEPSimulator` (state update, alarms, emission loop)
- `simulator/tep_process/config.py`:
  - Defines PV/MV specs, alarm thresholds, and initial hidden states
- `simulator/tep_process/sinks.py`:
  - Implements `JsonlSink`, `InfluxDBSink`, and `MultiSink`
- `simulator/tep_process/perturbations.py`:
  - Implements `PerturbationSink` wrapper and profile-driven perturbation config
- `simulator/tep_process/mv_command_server.py`:
  - Implements the TCP/UNIX socket server for receiving MV commands from the controller

### Startup and Initialization Path

When started via `python -m simulator.tep_process`:

1. `cli.main()` builds parser defaults from `load_config("tep_process")`.
2. `rate_config_from_args()` creates a `RateConfig` object (sampling windows, composition dead time).
3. `PVTelemetryServer` and `MVCommandServer` are started (UNIX sockets, configurable via CLI).
4. `TEPSimulator(...)` initializes:
   - Random generator (`random.Random(seed)`)
   - PV specs (`build_default_pv_specs`) and MV specs (`build_default_mv_specs`)
   - Hidden states (`build_default_hidden_states`)
   - Runtime buffers for each PV/MV (next emit times, pending composition values, alarm state)
5. The main simulation loop fetches the latest MV commands from the socket server (non-blocking, always up-to-date).
6. `build_sink(args)` creates output pipeline:
   - Always a `JsonlSink`
   - Optional `InfluxDBSink` (when `--influxdb-output`)
  - Optional `PerturbationSink` wrapper (when a perturbation family or legacy profile is selected)


### Main Simulation Loop (Exact Order)

`TEPSimulator.run()` executes one fixed-step iteration (`base_step = 1.0`) in this order:

1. `commands = socket_command_reader() or {}` — fetch latest controller commands from the socket server (non-blocking, always up-to-date)
2. `_update_mv(dt, commands)` — apply commands to actuator positions
3. `_update_hidden_states(dt)`
4. `_emit_pv_updates(sink)`
5. `_emit_mv_updates(sink)`
6. `sim_time += dt`
7. Optional sleep if `--realtime`

If duration is finite, it runs `range(int(duration / base_step))`; with `--run-forever`, it uses an infinite iterator.

### Process Model Internals

The process model is a phenomenological first-order-lag system.

- MVs are represented as commands plus feedback positions.
- MV commands are driven by the closed-loop controller: each simulation step, `_update_mv()` applies the latest command received from `ControllerCommandReader`. An MV with no fresh command holds its last feedback position.
- MV feedback follows command with a rate limit (`MVSpec.rate_limit`).
- Hidden states (flows, pressures, temperatures, levels, compositions) are updated by lag equations with Gaussian noise.

The update primitive is effectively:

```text
state <- state + (target - state) * dt / tau + noise
```

where target values are built from MV feedback positions.

### PV Generation and Composition Dead Time

For each `VariableSpec`, the simulator computes a clamped noisy value:

```text
pv_value = clamp(hidden_state * scale + bias + noise, min, max)
```

Category behavior differs:

- Non-composition PVs:
  - Emit whenever `sim_time >= next_emit_at`
  - Use immediate measured/visible value
- Composition PVs:
  - Sample on `next_sample_at`
  - Store sample in `pending_value`
  - Release to visible/measured value only after `composition_dead_time`
  - Emit on their own period using delayed value

This delayed-release path is why composition channels look slower and phase-lagged versus fast pressure/flow channels.

### Alarm Evaluation and Emission

Alarm states use threshold bands per signal:

- `HIHI` if value >= `hihi`
- `HI` if value >= `hi`
- `LOLO` if value <= `lolo`
- `LO` if value <= `lo`
- else `NORMAL`

Emission behavior:

- `record_type = pv` or `mv` always emitted on sample
- `record_type = alarm_event` emitted on every state transition (e.g. `NORMAL` → `HI`)

Severity mapping comes from `ALARM_SEVERITY`:

- `LOLO=-2`, `LO=-1`, `NORMAL=0`, `HI=1`, `HIHI=2`

### Sink Pipeline and Record Shapes

The sink interface has two methods:

- `emit_measurement(record)`
- `emit_event(record)`

`JsonlSink` writes one JSON object per line. `InfluxDBSink` maps simulator records into line-protocol points with:

- Tags: `record_type`, `name`, plus type-specific tags (`category`, `unit`, `source`, ...)
- Fields: numeric/process fields (`value`, `command`, `feedback`, `state`, `severity`, ...)
- Time: nanosecond timestamp from simulator `timestamp`

`MultiSink` fans out each record to all configured sinks.

### Perturbation Wrapper (Optional)

When enabled, `PerturbationSink` sits in front of downstream sinks and mutates delivery semantics.

Implemented effects include:

- Random drop (global and per-record-type)
- Duplicate and burst duplicate
- Clock skew and timestamp jitter
- Latency jitter with delayed release queue
- Out-of-order delivery
- Temporary outage buffering + burst flush
- Tag swap, unit scale error
- Stuck values and quantization
- Downsampling
- Per-sensor hard silence (`silent_sensors`)

Profiles `light`, `moderate`, `heavy` are parameter presets in `PerturbationConfig.from_profile()`.

### Runtime Summary API

`TEPSimulator.summary()` reports:

- `pv_count` (current implementation defines 73 PV channels)
- `mv_count` (12 MV channels)
- Per-category PV counts

Use `--print-summary` to dump this as JSON before the run starts.

---

## How It Works


### Simulation Workflow

1. **Initialization**
  - Load configuration (rates, perturbations)
  - Build runtime state for all process variables
  - Initialize alarms and controller state
  - Start MVCommandServer (TCP/UNIX socket) to receive MV commands from controller

2. **Simulation Loop** (runs continuously)
  - Read latest controller MV commands from the socket server (non-blocking, always up-to-date)
  - Apply MV commands to actuator positions; hold last position if no fresh command
  - Execute differential equations to advance process state
  - Calculate alarm transitions
  - Emit telemetry at configured intervals
  - Write outputs to configured sinks (InfluxDB, JSONL, etc.)

3. **Controller Loop** (separate, ~1 Hz)
  - Read latest telemetry from InfluxDB
  - Compute control actions to maintain setpoints
  - Write MV commands to both InfluxDB (for audit/tracing) and directly to the simulator via socket (for real-time control)

4. **Attack Injection** (campaign level)
  - Wait for randomized baseline period (see per-attack `attack_start_delay_min` / `attack_start_delay_max` in `campaign.attacks`)
  - Launch attack agent to burst data into InfluxDB
  - Continue for configured attack duration
  - Label all events with attack timestamps

### Timing Model

The simulator advances in discrete **simulation steps** (configurable):
- **Base step size**: 1 second or less (microseconds to sub-second precision available)
- **Sampling periods**: Each process variable has its own sampling period
  - Fast variables (pressure, flows): 1–2 seconds
  - Medium variables (level, MV commands): 1–5 seconds
  - Slow variables (temperature, composition): 5–15 minutes

Example: A sensor with a 5-second period will emit telemetry every 5 simulated seconds.

---

## Configuration

### Config Files

Configuration is organized into three sources (applied in order, last wins):

1. **`config.json`** – Committed defaults for all subsystems
2. **`.env`** – Environment overrides for secrets
3. **`os.environ`** – Runtime environment variables

### Configuration Loading in Code

```python
from simulator.common.config_loader import load_config

# Load any section from config.json
tep_process_config = load_config("tep_process")
tep_controller_config = load_config("tep_controller")
influxdb_config = load_config("influxdb")
campaign_config = load_config("campaign")
```

### Configuration Sections

#### `tep_process`

Controls the process simulator behavior.

| Key | Type | Default | Description |
|---|---|---|---|
| `duration` | float | `900.0` | Total simulation duration in simulated seconds (ignored if `--run-forever` is set) |
| `seed` | int | `10871` | Random seed for reproducibility |
| `realtimeScale` | float | `1.0` | Simulated seconds per real second (1.0 = wall-clock speed) |
| `measurement` | string | `tep_signals` | InfluxDB measurement name for telemetry |
| `eventMeasurement` | string | `tep_alarm_events` | InfluxDB measurement name for alarm events |

**Sampling Periods** – `rates` sub-section controls how often each variable type emits telemetry:

| Key | Type | Default | Description |
|---|---|---|---|
| `pressurePeriod` | float | `1.0` | Pressure sensors (seconds) |
| `flowPeriodMin`, `flowPeriodMax` | float | `1.0`, `2.0` | Flow sensors (seconds, randomized range) |
| `levelPeriodMin`, `levelPeriodMax` | float | `2.0`, `5.0` | Level sensors (seconds, randomized range) |
| `temperaturePeriodMin`, `temperaturePeriodMax` | float | `5.0`, `10.0` | Temperature sensors (seconds, randomized range) |
| `mvPeriod` | float | `1.0` | Manipulated variable commands (seconds) |
| `compositionPeriodMin`, `compositionPeriodMax` | float | `360.0`, `900.0` | Composition analysis (seconds, randomized range) |
| `compositionDeadTime` | float | `120.0` | Composition measurement delay (seconds) |

**Perturbations** – `perturbations` sub-section (optional, for injecting realistic faults and measurement errors):

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `false` | Enable perturbation injection |
| `profile` | string | `moderate` | Intensity preset: `light`, `moderate`, or `heavy` |
| `silentSensors` | list | `[]` | Sensor names to completely silence (drop all readings) |
| `seedOffset` | int | `1000` | Random seed offset for perturbations (ensures different randomness from main simulator) |

**Closed-Loop Controller Coupling** – `closedLoop` sub-section:

| Key | Type | Default | Description |
|---|---|---|---|
| `measurement` | string | `tep_controller_mv_commands` | InfluxDB measurement to poll for MV commands |
| `stalenessSeconds` | float | `5.0` | Max age of a cached command; older commands are ignored and MVs hold their last position |

#### `tep_controller`

Controls the closed-loop controller.

| Key | Type | Default | Description |
|---|---|---|---|
| `telemetryMeasurement` | string | `tep_signals` | InfluxDB measurement to read telemetry from |
| `alarmStateMeasurement` | string | `tep_alarm_state` | InfluxDB measurement to read alarm state from |
| `alarmEventMeasurement` | string | `tep_alarm_events` | InfluxDB measurement to read alarm events from |
| `alarmConfigMeasurement` | string | `tep_alarm_config` | InfluxDB measurement for alarm thresholds |
| `commandMeasurement` | string | `tep_controller_mv_commands` | InfluxDB measurement to write MV commands to |
| `intervalSeconds` | float | `1.0` | Control loop update rate (seconds) |

#### `attack_agent`

Controls the burst-write attack.

| Key | Type | Default | Description |
|---|---|---|---|
| `measurement` | string | `attack_records` | InfluxDB measurement to write attack points into |
| `tag` | string | `host` | Tag value on every attack point (for labeling) |
| `burstDuration` | int | `10` | Duration of one burst (seconds) |
| `burstPps` | int | `1000` | Points per second during attack burst |
| `burstValue` | float | `0` | Field value written to each attack point (fixed value per write) |

#### `campaign`

Controls the data collection campaign runner (`scripts/run_data_campaign.py`).

| Key | Type | Default | Description |
|---|---|---|---|
| `compose_file` | string | `docker-compose.yml` | Docker Compose file to use |
| `output_root` | string | `logs` | Root folder for per-run output |
| `test_duration` | int | `600` | Duration of each run in seconds (from stack startup to export) |
| `iterations` | int | `3` | Number of runs per scenario combination |
| `perturbations` | list | `["none", "light", "moderate", "heavy"]` | Perturbation profiles to sweep |
| `attacks` | list | `[{'duration':0}, {'duration':20,'intensities':['medium']}]` | Structured list of attack profiles. Each entry must include `duration`; for `duration > 0` include `attack_start_delay_min` and `attack_start_delay_max`.

**Collection Options** – `collect` sub-section (enables optional outputs per iteration):

| Key | Type | Default | Enables |
|---|---|---|---|
| `tep_signals` | bool | `false` | Export `tep_signals.ndjson` (all telemetry) |
| `tep_alarm_events` | bool | `false` | Export `tep_alarm_events.ndjson` |
| `tep_controller_mv_commands` | bool | `false` | Export `tep_controller_mv_commands.ndjson` |
| `container_logs` | bool | `false` | Save Docker container stdout/stderr |

**Always exported (regardless of `collect` flags):**
- `attack_records.ndjson` – Attack burst points
- `scenario.json` – Exact parameters for this run
- `annotations.ndjson` – Run start/end and attack event timestamps
- `sysdig_logs.ndjson` – Syscall traces (if sysdig enabled)

---

## Running the Simulator


### Command Line Interface

The TEP Simulator and Controller now support direct MV command delivery via UNIX socket. Example CLI usage:

```bash
# Start the simulator with UNIX sockets for MV commands and PV telemetry
python3 -m simulator.tep_process \
  --duration 600 \
  --mv-server-unix /tmp/tep_mv.sock \
  --pv-telemetry-unix /tmp/tep_pv.sock \
  --measurements-out tep_output.ndjson

# Start the controller and point it to the simulator sockets
export MV_SERVER_UNIX=/tmp/tep_mv.sock
export PV_TELEMETRY_UNIX=/tmp/tep_pv.sock
python3 -m simulator.tep_controller
```

**Simulator MV Command Channel Flags:**

| Flag | Argument | Default | Description |
|---|---|---|---|
| `--mv-server-unix` | path | — | UNIX socket path for MV command server |
| `--pv-telemetry-unix` | path | — | UNIX socket path for PV telemetry server |

**Controller Environment Variables:**

| Variable | Default | Description |
|---|---|---|
| `MV_SERVER_UNIX` | /sockets/mv.sock | UNIX socket path for MV command server |
| `PV_TELEMETRY_UNIX` | /sockets/pv.sock | UNIX socket path for PV telemetry server |

**Other Common Flags:**

| Flag | Argument | Default | Description |
|---|---|---|---|
| `--duration` | seconds | 900 | Simulation duration (ignored with `--run-forever`) |
| `--run-forever` | — | off | Run indefinitely until killed |
| `--seed` | int | 10871 | Random seed for reproducibility |
| `--measurements-out` | path | `-` | Write PV/MV telemetry to file (or stdout) |
| `--alarm-events-out` | path | — | Write alarm events to file (optional) |
| `--realtime` | — | off | Sleep between steps to run at wall-clock speed |
| `--realtime-scale` | float | 1.0 | Simulated seconds per real second |
| `--print-summary` | — | off | Print diagnostic summary on startup |

**InfluxDB Output:**

| Flag | Argument | Default | Description |
|---|---|---|---|
| `--influxdb-output` | — | off | Enable InfluxDB writing |
| `--influxdb-host` | hostname | localhost | InfluxDB server hostname |
| `--influxdb-port` | int | 8086 | InfluxDB port |
| `--influxdb-database` | name | appdb | Database name |
| `--influxdb-username` | user | admin | Auth username |
| `--influxdb-password` | pwd | — | Auth password (from `.env` or env var) |
| `--influxdb-measurement` | name | tep_signals | Measurement for PV/MV |
| `--influxdb-event-measurement` | name | tep_alarm_events | Measurement for alarms |

**Perturbations:**

| Flag | Argument | Default | Description |
|---|---|---|---|
| `--perturbation-family` | family | none | Family-based perturbation mode (`none`, `P1`..`P5`) |
| `--perturbation-lambda` | float | 0.0 | Perturbation severity $\lambda$ in `[0, 1]` |
| `--perturbation-profile` | profile | moderate | `light`, `moderate`, or `heavy` |
| `--silent-sensors` | csv | — | Comma-separated sensor names to silence |
| `--perturbation-seed` | int | — | Random seed for perturbations |

### Examples

**Baseline collection (10 minutes, no attack):**
```bash
python3 -m simulator.tep_process \
  --duration 600 \
  --influxdb-output \
  --influxdb-host localhost \
  --measurements-out baseline.ndjson
```

**With perturbations (heavy, specific sensors silenced):**
```bash
python3 -m simulator.tep_process \
  --duration 600 \
  --perturbation-profile heavy \
  --silent-sensors 'Reactor Outlet Temp,Separator Temp' \
  --influxdb-output \
  --measurements-out perturbed.ndjson
```

**Capture alarm events to a file:**
```bash
python3 -m simulator.tep_process \
  --duration 600 \
  --alarm-events-out alarms.ndjson \
  --influxdb-output
```

---

## Output Formats

### JSONL Output (Line-Delimited JSON)

Each line is a complete JSON object for easy streaming and parsing.

#### Telemetry Record (`tep_signals.ndjson`)
```json
{
  "time": "2026-03-18T12:34:56.789012Z",
  "measurement": "tep_signals",
  "tags": {
    "sensor": "Reactor Outlet Temp",
    "host": "simulator"
  },
  "fields": {
    "true_value": 45.123,
    "measured_value": 45.087,
    "visible_value": 45.087,
    "alarm_state": "NORMAL"
  }
}
```

#### Event Record (`tep_alarm_events.ndjson`)
```json
{
  "time": "2026-03-18T12:34:56.789012Z",
  "sensor": "Reactor Pressure",
  "from_state": "NORMAL",
  "to_state": "HI",
  "severity": 1,
  "details": "Transition from NORMAL to HI alarm"
}
```

#### Attack Record (`attack_records.ndjson`)
```json
{
  "time": "2026-03-18T12:35:45.000000Z",
  "measurement": "attack_records",
  "tags": {
    "host": "attacker"
  },
  "fields": {
    "value": 0
  }
}
```

#### Scenario Record (`scenario.json`)
The campaign runner writes a richer metadata file with the resolved perturbation family/lambda, actual attack delay, affected streams/tags, seeds, and run metadata. The exact fields vary by phase, but the file always records the concrete values used for that run.

#### Annotation Record (`annotations.ndjson`)
```json
{
  "time": "2026-03-18T12:30:00.000000Z",
  "event_type": "run_start",
  "description": "Data collection run started"
}
{
  "time": "2026-03-18T12:35:45.000000Z",
  "event_type": "attack_start",
  "description": "Burst attack initiated"
}
{
  "time": "2026-03-18T12:36:05.000000Z",
  "event_type": "attack_end",
  "description": "Burst attack ended"
}
```

---

## Perturbations (Fault Injection)

The current campaign runner uses family/lambda perturbations (`P1` through `P5`) selected via `TEP_PERTURBATION_FAMILY` and `TEP_PERTURBATION_LAMBDA`. The legacy `light` / `moderate` / `heavy` profile path is still available for direct simulator runs, but it is backward-compatibility code rather than the primary campaign mode.

| Flag | Argument | Default | Description |
|---|---|---|---|
| `--perturbation-family` | family | none | Family-based perturbation mode (`none`, `P1`..`P5`) |
| `--perturbation-lambda` | float | 0.0 | Perturbation severity $\lambda$ in `[0, 1]` |
| `--perturbation-profile` | profile | moderate | Legacy profile mode (`light`, `moderate`, `heavy`) |
| `--silent-sensors` | csv | — | Comma-separated sensor names to silence |
| `--perturbation-seed` | int | — | Random seed for perturbations |

### Perturbation Types

| Type | Effect | Example |
|---|---|---|
| **Drop** | Entire measurement skipped | Sensor temporarily unavailable |
| **Duplicate** | Same value repeated N times | Network retransmission |
| **Clock Skew** | Systematic timestamp offset | NTP desynchronization |
| **Jitter** | Random timestamp variance | Variable network latency |
| **Out-of-Order** | Measurements arrive late | Buffering/queuing delay |
| **Outage** | Sensor silent for duration | Temporary sensor failure |
| **Silent Sensors** | Sensor never reports | Dead sensor (must name it) |
| **Stuck Values** | Value doesn't change | Sensor freeze |
| **Quantization** | Coarse value rounding | Low-resolution ADC |
| **Unit Scale Error** | All values × error factor | Wrong conversion factor |

---

## Process Variables (PVs)

The simulator generates 73 process variables across five categories:

### Pressures (12 total)
- Reactor Pressure
- Separator Pressure
- Stripper Pressure
- Feed Line A Pressure
- Feed Line C Pressure
- Compressor Output Pressure

### Flows (18 total)
- Feed Flow A, B, C, D
- Recycle Flow
- Product Flow (liquid)
- Purge Flow
- Separator Output Flow
- Stripper Output Flow

### Levels (10 total)
- Reactor Level
- Separator Level
- Stripper Level
- Flash Drum Level
- Intermediate Tank Level

### Temperatures (20 total)
- Reactor Inlet/Outlet Temperature
- Separator Inlet/Outlet Temperature
- Feed Heater Outlet Temperature
- Stripper Inlet/Outlet Temperature
- Cooler Outlet Temperature
- And 4 intermediate temperatures

### Compositions (13 total)
- Reactor Outlet A/B/C/D/E/G concentrations
- Separator Outlet A/D concentrations
- Recycle Stream A concentration

Each variable has:
- **Nominal setpoint**: Normal operating point
- **Alarm thresholds**: LOLO, LO, HI, HIHI levels
- **Dead time**: Measurement delay (especially for composition)
- **Sampling period**: How often it's measured
- **Noise**: Realistic measurement error

---

## Manipulated Variables (MVs)

The simulator controls 12 manipulated variables:

| Variable | Range | Control Target | Typical Period |
|---|---|---|---|
| Reactor Feed Rate | 0–1000 | Reactor inlet throughput | 1 sec |
| Reactor Cooling | 0–100% | Reactor outlet temperature | 1 sec |
| Stripper Feed Rate | 0–1000 | Stripper inlet | 1 sec |
| Stripper Pressure | 0–50 atm | Separation efficiency | 1 sec |
| Separator Level Control | 0–100% | Separator overhead | 1 sec |
| Stripper Level Control | 0–100% | Stripper bottoms | 1 sec |
| Feed Tank A Level | 0–100% | Tank A outlet pressure | 1 sec |
| Compressor Recycle Flow | 0–1000 | System recycle rate | 1 sec |
| Flash Drum Pressure | 0–50 atm | Flash separation | 1 sec |
| Purge Valve | 0–100% | System purge rate | 1 sec |
| Product Valve A | 0–100% | Product A withdrawal | 1 sec |
| Product Valve E | 0–100% | Product E withdrawal | 1 sec |

The controller adjusts these to maintain stability and meet production targets.

---

## Attack Scenarios

Three pre-defined attack intensities are available:

| Intensity | Points/Second | Burst Duration | Typical Effect |
|---|---|---|---|
| **Low** | 100 pps | 10–20 sec | ~1,000–2,000 fake points |
| **Medium** | 500 pps | 10–20 sec | ~5,000–10,000 fake points |
| **High** | 1000 pps | 10–20 sec | ~10,000–20,000 fake points |

The attack is triggered after a randomized baseline period defined per-attack via `attack_start_delay_min`/`attack_start_delay_max`. All attack points are labeled with the `attack_records` measurement and can be joined with telemetry for supervised learning.

---

## Examples: Common Configurations

### Example 1: Stress Testing (High Intensity)

```json
{
  "campaign": {
    "workloads": ["high"],
    "perturbations": ["none"],
    "attacks": [ { "duration": 20, "intensities": ["high"], "attack_start_delay_min": 10, "attack_start_delay_max": 60 } ],
    "test_duration": 300,
    "iterations": 5
  }
}
```

**Effect**: 5 runs of the high-workload scenario with maximal attack traffic, no perturbations, suitable for testing system capacity limits.

### Example 2: Realistic ICS Deployment

```json
{
  "campaign": {
    "workloads": ["normal"],
    "perturbations": ["moderate"],
    "attacks": [ { "duration": 10, "intensities": ["low", "medium"], "attack_start_delay_min": 30, "attack_start_delay_max": 120 } ],
    "test_duration": 600,
    "iterations": 10
  },
  "tep_process": {
    "perturbations": {
      "enabled": true,
      "profile": "moderate",
      "silentSensors": ["Reactor Outlet Temp"]  // simulate a failed sensor
    }
  }
}
```

**Effect**: 10 runs per intensity (20 total) of normal operations with moderate measurement errors and one permanently unavailable sensor, more realistic for production systems.

### Example 3: Fast Baseline Collection

```json
{
  "campaign": {
    "workloads": ["normal"],
    "perturbations": ["none"],
    "attacks": [ { "duration": 0 } ],
    "test_duration": 60,
    "iterations": 1
  },
  "tep_process": {
    "realtimeScale": 10.0  // Run 10x faster
  }
}
```

**Effect**: Single 10-minute simulation run (compressed into ~1 minute wall-clock) with no perturbations or attack, fast baseline generation.

---

## Troubleshooting

### Simulator exits immediately
**Cause**: Invalid configuration or missing required keys
**Fix**: Validate `config.json` against the reference in [CONFIG.md](CONFIG.md)

### No telemetry appears in InfluxDB
**Cause**: InfluxDB connection failed or database doesn't exist
**Fix**: 
1. Verify InfluxDB is running: `docker compose logs influxdb`
2. Check credentials in `.env` match the running instance
3. Ensure database exists: `influx -port 8086 -database appdb -execute 'SHOW DATABASES'`

### Alarms never transition
**Cause**: Workload too weak or thresholds too permissive
**Fix**: Increase workload profile to `high` or manually adjust `alarm_thresholds` in code

### Composition values stuck or very slow to change
**Expected**: Composition has 2–15 minute measurement delay by design
**Fix**: Normal behavior; test with shorter `compositionDeadTime` if needed

### Perturbations not applied
**Cause**: Neither `--perturbation-family` nor `--perturbation-profile` was set, or the selected mode is not supported by the current run
**Fix**: Add `--perturbation-family P1` (or another family) or `--perturbation-profile heavy` for the legacy path

---

## Performance Considerations

- **CPU**: Simulator is single-threaded; uses ~1 CPU core at normal speed, ~5 cores for 10x realtime scale
- **Memory**: ~200 MB baseline + 10 MB per 100k telemetry points buffered
- **InfluxDB I/O**: ~100 writes/sec baseline, ~5,000+ writes/sec during high-intensity attack
- **Disk**: ~1 MB per 100k telemetry points (JSONL), InfluxDB storage depends on retention policy

For long campaigns (1000+ runs), consider:
- Running multiple parallel instances with different seeds
- Disabling optional `collect` flags to reduce output volume
- Using local SSD for faster JSONL export
- Setting InfluxDB retention policies to auto-purge old data
