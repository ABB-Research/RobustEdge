# RobustEdgeTestbed

This repository contains code to generate the data used in the paper "Robustness Benchmarking of ML-Based Container Attack Detection with a Perturbation-Driven Industrial Edge Testbed", Manca et al., EFTA 2026.

RobustEdgeTestbed is an open research platform and testbed for generating labeled industrial control system (ICS) telemetry under cyberattack conditions. It combines a Tennessee Eastman Process (TEP) simulator, closed-loop controller, attack agent, InfluxDB, and Grafana to produce reproducible datasets for detection research and system analysis.

## Why RobustEdgeTestbed

- Generate realistic time-series telemetry for both normal and adversarial operation.
- Run repeatable campaigns with scenario metadata and structured NDJSON exports.
- Observe system dynamics live in Grafana while recording complete run artifacts.
- Extend attacks, perturbation profiles, and collection strategy from configuration.

## What It Runs

- TEP simulator: process dynamics, PV/MV telemetry, alarm generation.
- Controller: closed-loop MV control with direct simulator command channel.
- Attack runner: burst-write adversarial traffic into InfluxDB.
- InfluxDB: time-series storage for telemetry and attack records.
- Grafana: dashboards for run-time and post-run visualization.

## Quick Start

### 1) Prerequisites

- Linux host (tested on Ubuntu 20.04+)
- Docker Engine 24+ with `docker compose` (Compose v2)
- Python 3.8+
- `sysdig` (optional, for syscall trace collection)

### 2) Python setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3) Configure secrets

```bash
cp .env.example .env
# edit .env and set at minimum:
# INFLUXDB_ADMIN_PASSWORD
# INFLUXDB_USER_PASSWORD
```

### 4) Render Grafana datasource provisioning

```bash
python3 grafana/render_provisioning.py
```

This renders `grafana/provisioning/datasources/influxdb.yml` from the template using values from `.env`.

### 5) Build and run a campaign

```bash
docker compose build
source .venv/bin/activate
PYTHONPATH=. .venv/bin/python3 scripts/run_data_campaign.py \
  --config config.json \
  --phase all \
  --test-duration 300 \
  --iterations 1 \
  --no-cleanup
```

If you changed InfluxDB credentials in `.env` after the stack has already been initialized, recreate the volumes once before running the campaign:

```bash
docker compose down -v
docker compose up -d influxdb
```

This resets persisted InfluxDB auth state to match current `.env` values.

### 6) Open Grafana

Use:

- URL: `http://localhost:3000/d/tep-operations-overview/tep-operations-overviews`
- Default credentials are defined in `.env`.

## Campaign Outputs

Each run writes artifacts under the path configured in `config.json` (`campaign.output_root`, default `./output`):

```text
output/
  perturbation-<P>_attackDuration-<D>_intensity-<I>_<UTC>/
    iteration-1/
      scenario.json
      attack_records.ndjson
      annotations.ndjson
      sysdig_logs.ndjson
      tep_signals.ndjson                 # if campaign.collect.tep_signals
      tep_alarm_events.ndjson            # if campaign.collect.tep_alarm_events
      tep_controller_mv_commands.ndjson  # if campaign.collect.tep_controller_mv_commands
      container_*.log                    # if campaign.collect.container_logs
```

| File | Always written | Contents |
|---|---|---|
| `scenario.json` | Yes | Exact parameters for this run: perturbation profile, attack timing, seeds, durations |
| `attack_records.ndjson` | Yes | Attack data points burst-written to InfluxDB during the attack window |
| `annotations.ndjson` | Yes | Timestamped events: `run_start`, `attack_start`, `attack_end`, `run_end` |
| `sysdig_logs.ndjson` | Yes (may be empty) | Per-container syscall counts from the sysdig chisel; empty when sysdig is unavailable |
| `tep_signals.ndjson` | `collect.tep_signals` | All PV and MV telemetry records emitted by the simulator |
| `tep_alarm_events.ndjson` | `collect.tep_alarm_events` | Alarm state transitions per sensor (e.g. NORMAL → HI) |
| `tep_controller_mv_commands.ndjson` | `collect.tep_controller_mv_commands` | MV commands sent by the controller (for audit and tracing) |
| `container_*.log` | `collect.container_logs` | Raw Docker stdout/stderr for each service |

All NDJSON files are newline-delimited JSON — one record per line.

## Configuration

- Main runtime config: `config.json`
- Full config reference: [docs/CONFIG.md](docs/CONFIG.md)
- Simulator internals and architecture: [docs/SIMULATOR.md](docs/SIMULATOR.md)

Attack scenarios are defined in `campaign.attacks`. For each attack entry with `duration > 0`, include both `attack_start_delay_min` and `attack_start_delay_max`.

Example:

```json
"campaign": {
  "attacks": [
    { "duration": 0 },
    {
      "duration": 20,
      "intensities": ["medium"],
      "attack_start_delay_min": 5,
      "attack_start_delay_max": 15
    }
  ]
}
```

## Repository Layout

- `.chisels/`: sysdig chisel for syscall counting.
- `attack_agents/`: attack implementations.
- `docs/`: documentation.
- `grafana/`: provisioning helpers and dashboards.
- `scripts/`: campaign and attack launch scripts.
- `simulator/common/`: TEP simulator and controller code.
- `simulator/tep_controller/`: control loop and command clients.
- `simulator/tep_process/`: simulator runtime, perturbation logic, sink pipeline.
- `util/`: logging and annotation helpers.

## Sysdig Collection (Optional)

To collect syscall traces:

- Install `sysdig` on the host.
- Ensure `sudo sysdig` can run in your environment.
- Configure containers via `sysdig.containers` in `config.json`.
- Ensure the chisel exists at `.chisels/count_syscalls`.

## Security and Safety Notes

- This project is intended for controlled lab and research environments.
- Do not expose InfluxDB or Grafana publicly without hardening and credential rotation.
- Default passwords in examples are for local development only.

# Citation

If you use this project in your research, please cite:

```
@inproceedings{robustedge2026,
  title = {Robustness Benchmarking of ML-Based Container Attack Detection with a Perturbation-Driven Industrial Edge Testbed},
  author = {Manca, Gianluca and Maag, Balz and Sivanthi, Thanikesavan and Guo, Shuai and Sommer, Philipp and Fay, Alexander},
  booktitle = {2026 IEEE 31st International Conference on Emerging Technologies and Factory Automation (ETFA)},
  year = {2026}
}
```
