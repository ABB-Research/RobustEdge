from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time
from typing import Any, Dict, Optional

from simulator.common.config_loader import load_config as _load_config
from simulator.tep_process.config import RateConfig
from simulator.tep_process.model import TEPSimulator
from simulator.tep_process.perturbations import (
    LambdaPerturbationSink,
    PerturbationConfig,
    PerturbationSink,
    PerturbationSpec,
)
import threading



from simulator.tep_process.sinks import InfluxDBSink, JsonlSink, MultiSink
from simulator.tep_process.mv_command_server import MVCommandServer
from simulator.tep_process.pv_telemetry_server import PVTelemetryServer


def _load_process_defaults() -> Dict[str, Any]:
    return _load_config("tep_process")


def build_arg_parser() -> argparse.ArgumentParser:
    defaults = _load_process_defaults()
    rates = defaults.get("rates", {}) if isinstance(defaults.get("rates"), dict) else {}
    influxdb = defaults.get("influxdb", {}) if isinstance(defaults.get("influxdb"), dict) else {}
    influx_conn = _load_config("influxdb")
    perturbations = defaults.get("perturbations", {}) if isinstance(defaults.get("perturbations"), dict) else {}

    parser = argparse.ArgumentParser(description="Tennessee Eastman style process simulator")
    #Comamnd line arguments for standalone simulator mode (no controller, just run the process and print PV/MV updates)
    parser.add_argument("--mv-server-unix", default=None, help="Path for UNIX socket for MV command server (overrides TCP)")
    parser.add_argument("--pv-telemetry-unix", default=None, help="Path for UNIX socket for PV telemetry server (overrides TCP)")
    parser.add_argument("--duration", type=float, default=float(defaults.get("duration", 900.0)), help="Simulation horizon in simulated seconds (ignored when --run-forever is set)")
    parser.add_argument("--run-forever", action="store_true", help="Run indefinitely until the process is killed (overrides --duration)")
    parser.add_argument("--seed", type=int, default=int(defaults.get("seed", 10871)), help="Random seed for simulator")
    parser.add_argument("--measurements-out", default="-", help="JSONL output for PV and MV samples, or - for stdout")
    parser.add_argument("--alarm-events-out", default=None, help="JSONL output for alarm events")
    parser.add_argument("--realtime", action="store_true", help="Sleep between simulation steps")
    parser.add_argument("--realtime-scale", type=float, default=float(defaults.get("realtimeScale", 1.0)), help="Simulated seconds per real second when realtime mode is enabled")
    parser.add_argument("--print-summary", action="store_true", help="Print simulator channel summary to stderr before starting")
    parser.add_argument("--pressure-period", type=float, default=float(rates.get("pressurePeriod", 1.0)))
    parser.add_argument("--flow-period-min", type=float, default=float(rates.get("flowPeriodMin", 1.0)))
    parser.add_argument("--flow-period-max", type=float, default=float(rates.get("flowPeriodMax", 2.0)))
    parser.add_argument("--level-period-min", type=float, default=float(rates.get("levelPeriodMin", 2.0)))
    parser.add_argument("--level-period-max", type=float, default=float(rates.get("levelPeriodMax", 5.0)))
    parser.add_argument("--temperature-period-min", type=float, default=float(rates.get("temperaturePeriodMin", 5.0)))
    parser.add_argument("--temperature-period-max", type=float, default=float(rates.get("temperaturePeriodMax", 10.0)))
    parser.add_argument("--mv-period", type=float, default=float(rates.get("mvPeriod", 1.0)))
    parser.add_argument("--composition-period-min", type=float, default=float(rates.get("compositionPeriodMin", 360.0)))
    parser.add_argument("--composition-period-max", type=float, default=float(rates.get("compositionPeriodMax", 900.0)))
    parser.add_argument("--composition-dead-time", type=float, default=float(rates.get("compositionDeadTime", 120.0)))
    parser.add_argument("--influxdb-output", action="store_true", help="Also write simulator records into InfluxDB")
    parser.add_argument("--influxdb-host", default=os.getenv("INFLUXDB_HOST", str(influxdb.get("host", influx_conn.get("host", "localhost")))))
    parser.add_argument("--influxdb-port", type=int, default=int(os.getenv("INFLUXDB_PORT", str(influxdb.get("port", influx_conn.get("port", 8086))))))
    parser.add_argument("--influxdb-database", default=os.getenv("INFLUXDB_DB", str(influxdb.get("database", influx_conn.get("database", "appdb")))))
    parser.add_argument("--influxdb-username", default=os.getenv("INFLUXDB_ADMIN_USER", str(influxdb.get("username", influx_conn.get("username", "admin")))))
    parser.add_argument("--influxdb-password", default=os.getenv("INFLUXDB_ADMIN_PASSWORD", str(influxdb.get("password", influx_conn.get("password", "change_me_admin_password")))))
    parser.add_argument("--influxdb-measurement", default=str(influxdb.get("measurement", "tep_signals")))
    parser.add_argument("--influxdb-event-measurement", default=str(influxdb.get("eventMeasurement", "tep_alarm_events")))
    parser.add_argument("--perturbation-profile", choices=["light", "moderate", "heavy"], default=None, help="Preset intensity for perturbations (default: moderate)")
    parser.add_argument("--silent-sensors", default=",".join(perturbations.get("silentSensors", [])), help="Comma-separated sensor names to silence completely")
    _pseed_env = os.getenv("TEP_PERTURBATION_SEED", "")
    parser.add_argument("--perturbation-seed", type=int, default=int(_pseed_env) if _pseed_env else None, help="Random seed for perturbation engine (overrides auto-derived value)")
    parser.add_argument("--perturbation-seed-offset", type=int, default=int(perturbations.get("seedOffset", 1000)), help="Default offset applied when perturbation seed is not explicitly set")
    parser.add_argument(
        "--perturbation-family",
        choices=["none", "P1", "P2", "P3", "P4", "P5"],
        default=os.getenv("TEP_PERTURBATION_FAMILY", "none"),
        help="ETFA 2026 perturbation family (P1–P5); 'none' disables perturbation injection",
    )
    parser.add_argument(
        "--perturbation-lambda",
        type=float,
        default=float(os.getenv("TEP_PERTURBATION_LAMBDA", "0.0")),
        help="Perturbation severity λ ∈ [0, 1] (0 = no perturbation, 1 = maximum)",
    )
    _p4_env = os.getenv("TEP_P4_OUTAGE_START", "")
    parser.add_argument(
        "--p4-outage-start",
        type=float,
        default=float(_p4_env) if _p4_env else None,
        help="P4: simulator time (seconds) when the outage window begins",
    )
    return parser


def rate_config_from_args(args: argparse.Namespace) -> RateConfig:
    return RateConfig(
        pressure_period=args.pressure_period,
        flow_period_min=args.flow_period_min,
        flow_period_max=args.flow_period_max,
        level_period_min=args.level_period_min,
        level_period_max=args.level_period_max,
        temperature_period_min=args.temperature_period_min,
        temperature_period_max=args.temperature_period_max,
        mv_period=args.mv_period,
        composition_period_min=args.composition_period_min,
        composition_period_max=args.composition_period_max,
        composition_dead_time=args.composition_dead_time,
    )


def build_sink(args: argparse.Namespace):
    sinks = [JsonlSink(args.measurements_out, args.alarm_events_out)]
    if args.influxdb_output:
        sinks.append(
            InfluxDBSink(
                host=args.influxdb_host,
                port=args.influxdb_port,
                username=args.influxdb_username,
                password=args.influxdb_password,
                database=args.influxdb_database,
                measurement_name=args.influxdb_measurement,
                event_measurement_name=args.influxdb_event_measurement,
            )
        )
    if len(sinks) == 1:
        sink = sinks[0]
    else:
        sink = MultiSink(sinks)

    pseed = (
        args.perturbation_seed
        if args.perturbation_seed is not None
        else args.seed + args.perturbation_seed_offset
    )

    # New lambda-based path (P1–P5)
    family = getattr(args, "perturbation_family", "none") or "none"
    if family in ("P1", "P2", "P3", "P4", "P5"):
        lam = max(0.0, min(1.0, float(getattr(args, "perturbation_lambda", 0.0) or 0.0)))
        outage_start_raw = getattr(args, "p4_outage_start", None)
        spec = PerturbationSpec(
            family=family,
            lambda_=lam,
            seed=pseed,
            outage_start=float(outage_start_raw) if outage_start_raw is not None else None,
        )
        return LambdaPerturbationSink(downstream=sink, spec=spec)

    # Legacy profile-based path (backward compatibility)
    silent = [s.strip() for s in args.silent_sensors.split(",") if s.strip()]
    defaults = _load_process_defaults()
    perturbations = defaults.get("perturbations", {}) if isinstance(defaults.get("perturbations"), dict) else {}
    downsample_pvs = perturbations.get("downsamplePVs", None)
    profile = getattr(args, "perturbation_profile", None)
    if profile is not None:
        cfg = PerturbationConfig.from_profile(
            profile,
            silent_sensors=silent,
            downsample_pvs=downsample_pvs,
        )
        return PerturbationSink(downstream=sink, cfg=cfg, seed=pseed)

    return sink


class TelemetrySinkWrapper:
    def __init__(self, downstream, pv_telemetry_server):
        self._downstream = downstream
        # store mapping name -> {"value": float, "timestamp": float}
        self._pv_values = {}
        self._pv_telemetry_server = pv_telemetry_server

    def emit_measurement(self, record):
        # Forward to the downstream sink
        self._downstream.emit_measurement(record)
        if record.get("record_type") == "pv":
            # preserve the simulator timestamp if present
            ts = record.get("timestamp")
            self._pv_values[record["name"]] = {"value": record["value"], "timestamp": ts}

    def flush_pvs(self):
        if self._pv_values:
            self._pv_telemetry_server.send_pv_values(self._pv_values)
            self._pv_values = {}

    def __getattr__(self, name):
        return getattr(self._downstream, name)


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    simulator = TEPSimulator(rates=rate_config_from_args(args), seed=args.seed)

    # Start PVTelemetryServer
    pv_telemetry_server = PVTelemetryServer(unix_socket=args.pv_telemetry_unix)
    pv_telemetry_server.start()

    if args.print_summary:
        print(json.dumps(simulator.summary(), indent=2), file=sys.stderr)

    sink = build_sink(args)
    mv_server = MVCommandServer(unix_socket=args.mv_server_unix)
    mv_server.start() 
    duration = None if args.run_forever else args.duration
    try:
        # Wrap the sink to also send PVs to the telemetry server
        telemetry_sink = TelemetrySinkWrapper(sink, pv_telemetry_server)
        # Custom run loop to flush PVs after each step
        import itertools
        step_iter = itertools.count() if duration is None else range(int(duration / simulator.base_step))
        for _ in step_iter:
            commands = mv_server.get_latest_commands() or {}
            simulator._update_mv(simulator.base_step, commands)
            simulator._update_hidden_states(simulator.base_step)
            simulator._emit_pv_updates(telemetry_sink)
            telemetry_sink.flush_pvs()
            simulator._emit_mv_updates(telemetry_sink)
            simulator.sim_time += simulator.base_step
            if args.realtime:
                time.sleep(simulator.base_step / max(args.realtime_scale, 1e-6))
    finally:
        sink.close()
        mv_server.stop()
        pv_telemetry_server.stop()
    return 0