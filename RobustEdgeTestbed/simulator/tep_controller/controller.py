from __future__ import annotations

import os
import random
import time
import hashlib
import json
from dataclasses import dataclass
from typing import Dict, List


from influxdb import InfluxDBClient
from simulator.tep_controller.pv_telemetry_client import get_latest_pv_values

from simulator.common.config_loader import load_config as _load_config
from simulator.tep_controller.config import build_default_control_loops, build_default_override_rules

from simulator.tep_process.config import (
    ALARM_SEVERITY,
    RateConfig,
    build_default_mv_specs,
    build_default_pv_specs,
)


def _load_controller_defaults() -> Dict[str, object]:
    return _load_config("tep_controller")


@dataclass
class ControllerConfig:
    host: str
    port: int
    username: str
    password: str
    database: str
    telemetry_measurement: str
    alarm_state_measurement: str
    alarm_event_measurement: str
    alarm_config_measurement: str
    command_measurement: str
    interval_seconds: float


def _env_config() -> ControllerConfig:
    defaults = _load_controller_defaults()
    influx_conn = _load_config("influxdb")
    return ControllerConfig(
        host=os.getenv("INFLUXDB_HOST", str(influx_conn.get("host", "localhost"))),
        port=int(os.getenv("INFLUXDB_PORT", str(influx_conn.get("port", 8086)))),
        username=os.getenv("INFLUXDB_ADMIN_USER", str(influx_conn.get("username", "admin"))),
        password=os.getenv("INFLUXDB_ADMIN_PASSWORD", str(influx_conn.get("password", "change_me_admin_password"))),
        database=os.getenv("INFLUXDB_DB", str(influx_conn.get("database", "appdb"))),
        telemetry_measurement=os.getenv("TEP_TELEMETRY_MEASUREMENT", str(defaults.get("telemetryMeasurement", "tep_signals"))),
        alarm_state_measurement=os.getenv("TEP_ALARM_STATE_MEASUREMENT", str(defaults.get("alarmStateMeasurement", "tep_alarm_state"))),
        alarm_event_measurement=os.getenv("TEP_ALARM_EVENT_MEASUREMENT", str(defaults.get("alarmEventMeasurement", "tep_alarm_events"))),
        alarm_config_measurement=os.getenv("TEP_ALARM_CONFIG_MEASUREMENT", str(defaults.get("alarmConfigMeasurement", "tep_alarm_config"))),
        command_measurement=os.getenv("TEP_COMMAND_MEASUREMENT", str(defaults.get("commandMeasurement", "tep_controller_mv_commands"))),
        interval_seconds=float(os.getenv("TEP_CONTROLLER_INTERVAL", str(defaults.get("intervalSeconds", 1.0)))),
    )


def _alarm_state_for_value(value: float, thresholds) -> str:
    if value >= thresholds.hihi:
        return "HIHI"
    if value >= thresholds.hi:
        return "HI"
    if value <= thresholds.lolo:
        return "LOLO"
    if value <= thresholds.lo:
        return "LO"
    return "NORMAL"


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _alarm_meets_trigger(current_state: str, trigger_state: str) -> bool:
    if trigger_state in {"HI", "HIHI"}:
        return current_state in {trigger_state, "HIHI"}
    if trigger_state in {"LO", "LOLO"}:
        return current_state in {trigger_state, "LOLO"}
    return current_state == trigger_state


def _compute_pv_alarm_states(pv_values: Dict[str, float], pv_specs: List) -> Dict[str, str]:
    states: Dict[str, str] = {}
    for spec in pv_specs:
        value = pv_values.get(spec.name)
        if value is None:
            continue
        states[spec.name] = _alarm_state_for_value(value, spec.thresholds)
    return states


def _apply_overrides(
    mv_commands: Dict[str, float],
    pv_alarm_states: Dict[str, str],
    override_rules: List,
    mv_specs_by_name: Dict[str, object],
) -> None:
    for rule in override_rules:
        pv_state = pv_alarm_states.get(rule.pv_name)
        if pv_state is None:
            continue
        if not _alarm_meets_trigger(pv_state, rule.trigger_state):
            continue
        spec = mv_specs_by_name.get(rule.mv_name)
        if spec is None:
            continue
        current = mv_commands.get(rule.mv_name, spec.command)
        if rule.mode == "min":
            constrained = max(current, rule.value)
        elif rule.mode == "max":
            constrained = min(current, rule.value)
        else:
            constrained = current
        mv_commands[rule.mv_name] = _clamp(constrained, spec.min_value, spec.max_value)


def _build_alarm_config_rows(pv_specs: List, mv_specs: List) -> List[dict]:
    rows: List[dict] = []
    for spec in pv_specs:
        rows.append(
            {
                "source_type": "pv",
                "source": spec.name,
                "lolo": float(spec.thresholds.lolo),
                "lo": float(spec.thresholds.lo),
                "hi": float(spec.thresholds.hi),
                "hihi": float(spec.thresholds.hihi),
            }
        )
    for spec in mv_specs:
        if spec.thresholds is None:
            continue
        rows.append(
            {
                "source_type": "mv",
                "source": spec.name,
                "lolo": float(spec.thresholds.lolo),
                "lo": float(spec.thresholds.lo),
                "hi": float(spec.thresholds.hi),
                "hihi": float(spec.thresholds.hihi),
            }
        )
    rows.sort(key=lambda r: (r["source_type"], r["source"]))
    return rows


def _fingerprint_rows(rows: List[dict]) -> str:
    payload = json.dumps(rows, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _emit_alarm_config_if_changed(
    client: InfluxDBClient,
    cfg: ControllerConfig,
    timestamp_ns: int,
    rows: List[dict],
    previous_fingerprint: str | None,
) -> str:
    current_fingerprint = _fingerprint_rows(rows)
    if previous_fingerprint == current_fingerprint:
        return current_fingerprint

    reason = "startup" if previous_fingerprint is None else "changed"
    points = []
    for row in rows:
        points.append(
            {
                "measurement": cfg.alarm_config_measurement,
                "time": timestamp_ns,
                "tags": {
                    "source_type": row["source_type"],
                    "source": row["source"],
                },
                "fields": {
                    "lolo": row["lolo"],
                    "lo": row["lo"],
                    "hi": row["hi"],
                    "hihi": row["hihi"],
                    "config_hash": current_fingerprint,
                    "reason": reason,
                },
            }
        )
    if points:
        client.write_points(points, time_precision="n")
    return current_fingerprint

def _compute_mv_commands(
    pv_values: Dict[str, float],
    loop_integrals: Dict[str, float],
    dt: float,
    loops: List,
    mv_specs: Dict[str, object],
    override_rules: List,
    pv_alarm_states: Dict[str, str],
) -> Dict[str, float]:
    demands: Dict[str, List[float]] = {name: [] for name in mv_specs}

    for loop in loops:
        pv = pv_values.get(loop.pv_name)
        if pv is None:
            continue
        sign = -1.0 if loop.reverse_action else 1.0
        error = loop.setpoint - pv
        integral = loop_integrals.get(loop.name, 0.0)
        integral += sign * loop.gain * error * dt / max(loop.integral_time, 1e-6)
        integral = _clamp(integral, -100.0, 100.0)
        loop_integrals[loop.name] = integral
        raw = loop.output_bias + sign * loop.gain * error + integral
        mv = mv_specs[loop.mv_name]
        demands[loop.mv_name].append(_clamp(raw, mv.min_value, mv.max_value))

    commands: Dict[str, float] = {}
    for name, mv in mv_specs.items():
        if demands[name]:
            commands[name] = sum(demands[name]) / len(demands[name])
        else:
            commands[name] = mv.command
    _apply_overrides(commands, pv_alarm_states, override_rules, mv_specs)
    return commands


def _build_alarm_points(
    timestamp_ns: int,
    pv_values: Dict[str, float],
    mv_commands: Dict[str, float],
    pv_specs: List,
    mv_specs: List,
    previous_states: Dict[str, str],
    cfg: ControllerConfig,
):
    state_points = []
    event_points = []

    for spec in pv_specs:
        value = pv_values.get(spec.name)
        if value is None:
            continue
        state = _alarm_state_for_value(value, spec.thresholds)
        key = f"pv::{spec.name}"
        prev = previous_states.get(key, "NORMAL")
        previous_states[key] = state
        # Always use controller loop timestamp for alarm points (ignore PV timestamps)
        point_time_ns = timestamp_ns

        state_points.append(
            {
                "measurement": cfg.alarm_state_measurement,
                "time": point_time_ns,
                "tags": {"source_type": "pv", "source": spec.name, "state": state},
                "fields": {"severity": int(ALARM_SEVERITY[state]), "value": float(value)},
            }
        )

        if prev != state:
            event_points.append(
                {
                    "measurement": cfg.alarm_event_measurement,
                    "time": point_time_ns,
                    "tags": {"source_type": "pv", "source": spec.name},
                    "fields": {
                        "previous_state": prev,
                        "state": state,
                        "severity": int(ALARM_SEVERITY[state]),
                        "value": float(value),
                    },
                }
            )

    for spec in mv_specs:
        if spec.thresholds is None:
            continue
        value = mv_commands.get(spec.name, spec.command)
        state = _alarm_state_for_value(value, spec.thresholds)
        key = f"mv::{spec.name}"
        prev = previous_states.get(key, "NORMAL")
        previous_states[key] = state

        state_points.append(
            {
                "measurement": cfg.alarm_state_measurement,
                "time": timestamp_ns,
                "tags": {"source_type": "mv", "source": spec.name, "state": state},
                "fields": {"severity": int(ALARM_SEVERITY[state]), "value": float(value)},
            }
        )

        if prev != state:
            event_points.append(
                {
                    "measurement": cfg.alarm_event_measurement,
                    "time": timestamp_ns,
                    "tags": {"source_type": "mv", "source": spec.name},
                    "fields": {
                        "previous_state": prev,
                        "state": state,
                        "severity": int(ALARM_SEVERITY[state]),
                        "value": float(value),
                    },
                }
            )

    return state_points, event_points


def _build_command_points(timestamp_ns: int, mv_commands: Dict[str, float], cfg: ControllerConfig):
    """Build MV command audit points.

    If PV timestamps are available, use the most-recent PV timestamp as the point time
    so MV audit points align to simulator sim_time. Otherwise fall back to controller time.
    """
    # Ignore PV timestamps and use controller loop timestamp for command audit points
    point_time_ns = timestamp_ns
    points = []
    for mv_name, cmd in mv_commands.items():
        points.append(
            {
                "measurement": cfg.command_measurement,
                "time": point_time_ns,
                "tags": {"name": mv_name},
                "fields": {"command": float(cmd)},
            }
        )
    return points


def main() -> int:
    # Socket config for direct MV command delivery
    from simulator.tep_controller.mv_command_client import send_mv_commands

    cfg = _env_config()
    mv_socket_unix = os.getenv("MV_SERVER_UNIX", "/sockets/mv.sock")

    # PV Telemetry socket config
    pv_telemetry_unix = os.getenv("PV_TELEMETRY_UNIX", "/sockets/pv.sock")

    # Wait briefly for PV telemetry socket to appear so the controller
    # doesn't immediately spam timeout/file-not-found errors while the
    # telemetry server is still starting up.
    for _ in range(60):
        if os.path.exists(pv_telemetry_unix):
            break
        print(f"[WARN] PV telemetry socket {pv_telemetry_unix} not present, waiting...", flush=True)
        time.sleep(1.0)
    else:
        print(f"[WARN] PV telemetry socket {pv_telemetry_unix} did not appear; controller will continue and retry each loop", flush=True)
    # Try to receive an initial PV payload before entering the main loop.
    # This reduces the race where the controller reads before the simulator
    # has emitted its first PV snapshot. We'll wait up to 30s total.
    initial_pv_payload = None
    try:
        startup_deadline = time.time() + 30.0
        while time.time() < startup_deadline:
            try:
                pv = get_latest_pv_values(unix_socket=pv_telemetry_unix, timeout=2.0)
                if pv:
                    initial_pv_payload = pv
                    print("[INFO] Received initial PV payload from telemetry server", flush=True)
                    break
            except FileNotFoundError:
                # Socket not present yet, keep waiting
                pass
            time.sleep(0.5)
    except Exception:
        # Non-fatal; proceed to main loop which will retry each cycle
        pass
    client = InfluxDBClient(
        host=cfg.host,
        port=cfg.port,
        username=cfg.username,
        password=cfg.password,
        database=cfg.database,
    )

    for _ in range(60):
        try:
            client.create_database(cfg.database)
            client.switch_database(cfg.database)
            break
        except Exception:
            time.sleep(1.0)
    else:
        raise RuntimeError("Controller could not connect to InfluxDB")

    previous_states: Dict[str, str] = {}
    loop_integrals: Dict[str, float] = {}
    config_fingerprint: str | None = None

    rates = RateConfig()
    pv_specs = build_default_pv_specs(rates, random.Random(10871))
    mv_specs = build_default_mv_specs(rates)
    mv_specs_by_name = {mv.name: mv for mv in mv_specs}
    loops = build_default_control_loops()
    override_rules = build_default_override_rules()
    alarm_config_rows = _build_alarm_config_rows(pv_specs, mv_specs)

    while True:
        start = time.time()
        try:
            # Get PVs from telemetry socket. Use any initial payload we captured
            # during startup to avoid a spurious first-loop timeout.
            if initial_pv_payload is not None:
                pv_payload = initial_pv_payload
                initial_pv_payload = None
            else:
                pv_payload = get_latest_pv_values(unix_socket=pv_telemetry_unix, timeout=2.0)
            if not pv_payload:
                print("[WARN] No PV values received from telemetry server", flush=True)
                time.sleep(cfg.interval_seconds)
                continue
            # pv_payload: { name: {"value": float, "timestamp": float} }
            pv_values: Dict[str, float] = {}
            for name, item in pv_payload.items():
                if isinstance(item, dict):
                    val = item.get("value")
                else:
                    val = item
                try:
                    pv_values[name] = float(val)
                except Exception:
                   continue

            pv_alarm_states = _compute_pv_alarm_states(pv_values, pv_specs)
            mv_commands = _compute_mv_commands(
                pv_values,
                loop_integrals,
                cfg.interval_seconds,
                loops,
                mv_specs_by_name,
                override_rules,
                pv_alarm_states,
            )
            now_ns = int(start * 1e9)

            # Send MV commands to simulator via socket (direct channel)
            try:
                send_mv_commands(mv_commands, unix_socket=mv_socket_unix)
            except Exception as e:
                print(f"[WARN] Could not send MV commands to simulator socket: {e}", flush=True)

            config_fingerprint = _emit_alarm_config_if_changed(
                client,
                cfg,
                now_ns,
                alarm_config_rows,
                config_fingerprint,
            )

            command_points = _build_command_points(now_ns, mv_commands, cfg)
            if command_points:
                client.write_points(command_points, time_precision="n")

            state_points, event_points = _build_alarm_points(
                now_ns,
                pv_values,
                mv_commands,
                pv_specs,
                mv_specs,
                previous_states,
                cfg,
            )
            if state_points:
                client.write_points(state_points, time_precision="n")
            if event_points:
                client.write_points(event_points, time_precision="n")
        except Exception as exc:
            # Include exception type to make diagnosis easier (e.g. socket.timeout vs FileNotFoundError)
            print(f"controller loop error ({type(exc).__name__}): {exc}", flush=True)

        elapsed = time.time() - start
        time.sleep(max(0.0, cfg.interval_seconds - elapsed))


if __name__ == "__main__":
    raise SystemExit(main())
