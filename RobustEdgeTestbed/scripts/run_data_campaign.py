from __future__ import annotations

import argparse
import itertools
import json
import os
import random
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from simulator.common.config_loader import load_config as _load_config
from simulator.tep_process.perturbations import PerturbationSpec
from simulator.tep_process.config import (
    FLOW_DEFS, PRESSURE_DEFS, TEMPERATURE_DEFS,
    LEVEL_DEFS, COMPOSITION_DEFS, MV_DEFS,
)
from util.local_annotations import LocalAnnotations

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CHISEL_DIR = _REPO_ROOT / ".chisels"

# All known eligible tag names for tag-scoped perturbation families (P1, P2, P3, P5).
# Derived from the static TEP model definition so affected-tag lists can be pre-computed
# on the host and stored in scenario.json before each run.
_ALL_PV_TAGS: List[str] = [
    d[0] for d in (FLOW_DEFS + PRESSURE_DEFS + TEMPERATURE_DEFS + LEVEL_DEFS + COMPOSITION_DEFS)
]
_ALL_MV_TAGS: List[str] = [d[0] for d in MV_DEFS]
_ALL_ELIGIBLE_TAGS: List[str] = _ALL_PV_TAGS + _ALL_MV_TAGS


def _get_container_ids(names: List[str]) -> Dict[str, str]:
    """Return {name: short_id} for each named container that is currently running."""
    result: Dict[str, str] = {}
    for name in names:
        try:
            out = subprocess.check_output(
                ["docker", "inspect", "--format", "{{.Id}}", name],
                stderr=subprocess.DEVNULL,
            )
            full_id = out.decode().strip()
            if full_id:
                result[name] = full_id[:12]
        except Exception:
            pass
    return result


def _sysdig_reader(proc: subprocess.Popen, out_path: Path, stop_event: threading.Event) -> None:
    with out_path.open("w", encoding="utf-8") as fh:
        while not stop_event.is_set():
            line = proc.stdout.readline()  # type: ignore[union-attr]
            if not line:
                break
            decoded = line.decode("utf-8", errors="replace").strip()
            if not decoded:
                continue
            try:
                data = json.loads(decoded)
                containers = data["containers"]
                for container in containers:
                    measurement = {
                        "time": int(data["timestamp"] * 1e9),
                        "measurement": "sysdig",
                        "tags": {"container_name": container["name"]},
                        "fields": container["stats"],
                    }
                    fh.write(json.dumps(measurement) + "\n")
                    fh.flush()
            except Exception as e:
                print(f"Exception while parsing sysdig line: '{decoded}': {e}", flush=True)


def _start_sysdig(
    container_dict: Dict[str, str],
    window_size: int,
    out_path: Path,
) -> Tuple[Optional[subprocess.Popen], Optional[Tuple[threading.Thread, threading.Event]]]:
    """Start sysdig in a background thread writing to out_path.

    Returns (proc, (thread, stop_event)) or (None, None) if sysdig is unavailable.
    """
    if not shutil.which("sysdig"):
        print("Warning: sysdig not found on PATH — syscall traces will not be collected", flush=True)
        return None, None
    if not container_dict:
        print("Warning: no running containers found — skipping sysdig", flush=True)
        return None, None

    ids = list(container_dict.values())
    if len(ids) == 1:
        container_filter = f"container.id in ('{ids[0]}')"
    else:
        container_filter = "container.id in " + str(tuple(ids))

    chisel_path = str(_CHISEL_DIR / "count_syscalls")
    cmd = [
        "sudo", "-n",
        "sysdig",
        "-c", chisel_path,
        str(window_size),
        container_filter,
    ]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError as exc:
        print(f"Warning: could not start sysdig: {exc}", flush=True)
        return None, None

    stop_event = threading.Event()
    t = threading.Thread(target=_sysdig_reader, args=(proc, out_path, stop_event), daemon=True)
    t.start()
    return proc, (t, stop_event)


def _stop_sysdig(
    proc: Optional[subprocess.Popen],
    reader: Optional[Tuple[threading.Thread, threading.Event]],
) -> None:
    if proc is None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    if reader is not None:
        t, stop_event = reader
        stop_event.set()
        t.join(timeout=5)


DEFAULT_MEASUREMENTS = [
    "tep_signals",
    "tep_alarm_events",
    "tep_controller_mv_commands",
    "attack_records",
]


def _influx_client(*, host: str, port: int, username: str, password: str, database: str | None = None):
    try:
        from influxdb import InfluxDBClient
    except ModuleNotFoundError as exc:
        raise RuntimeError("Missing dependency 'influxdb'. Install with: pip3 install -r requirements.txt") from exc
    return InfluxDBClient(host=host, port=port, username=username, password=password, database=database,
                          proxies={"http": None, "https": None})


@dataclass(frozen=True)
class Scenario:
    perturbation: str              # profile name used for directory naming
    perturbation_family: str       # "none" | "P1" | "P2" | "P3" | "P4" | "P5"
    perturbation_lambda: float     # severity λ ∈ [0, 1]
    attack_duration: int
    attack_intensity: str
    iteration: int
    attack_meta: Optional[Dict[str, Any]] = None


@dataclass
class CampaignConfig:
    compose_file: Path
    output_root: Path
    test_duration: int
    iterations: int
    perturbation_profiles: List[Dict[str, Any]]  # list of {name, family, lambda} dicts
    attacks: List[Dict[str, Any]]
    collect: Dict[str, bool]
    no_cleanup: bool
    # Phase system — populated by --phase handling; empty means run top-level config.
    phase_name: str = ""
    phase_seed_offset: int = 0
    selected_phases: List[Dict[str, Any]] = field(default_factory=list)


def parse_csv(values: str) -> List[str]:
    return [v.strip() for v in values.split(",") if v.strip()]


def parse_int_csv(values: str) -> List[int]:
    return [int(v.strip()) for v in values.split(",") if v.strip()]


def _list_of_str(raw) -> List[str]:
    if isinstance(raw, list):
        return [str(v).strip() for v in raw if str(v).strip()]
    if isinstance(raw, str):
        return parse_csv(raw)
    return []


def _list_of_int(raw) -> List[int]:
    if isinstance(raw, list):
        return [int(v) for v in raw]
    if isinstance(raw, str):
        return parse_int_csv(raw)
    return []


def _load_campaign_defaults(config_path: Path) -> dict:
    if not config_path.exists():
        return {}
    with config_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"Campaign config must be a JSON object: {config_path}")
    # Support both a top-level config.json (section-keyed) and a legacy campaign-only file
    return data.get("campaign", data)


def _resolve_selected_phases(defaults: dict, args: argparse.Namespace) -> List[Dict[str, Any]]:
    """Parse --phase / --list-phases into the list of phase dicts to run."""
    all_phases: List[Dict[str, Any]] = defaults.get("phases", [])

    if args.list_phases:
        if not all_phases:
            print("No phases defined in config.json campaign.phases", flush=True)
        else:
            print("Available campaign phases:", flush=True)
            for i, p in enumerate(all_phases, start=1):
                iters = p.get("iterations", "?")
                n_profiles = len(p.get("perturbation_profiles", []))
                n_attacks = len(p.get("attacks", []))
                desc = p.get("description", "")
                print(f"  {i}. {p.get('name')}  (iterations={iters}, profiles={n_profiles}, attacks={n_attacks})", flush=True)
                if desc:
                    print(f"       {desc}", flush=True)
        raise SystemExit(0)

    if args.phase is None:
        return []  # use top-level config (backward compat)

    if not all_phases:
        raise SystemExit("--phase requires campaign.phases to be defined in config.json")

    return _select_phases(all_phases, args.phase)


def parse_args() -> CampaignConfig:
    parser = argparse.ArgumentParser(description="Run containerized TEP experiment campaign")
    parser.add_argument("--config", default="config.json", help="Path to config.json (default: config.json)")
    parser.add_argument("--compose-file", default=None, help="Path to docker-compose.yml (default: from config.json or 'docker-compose.yml')")
    parser.add_argument("--output-root", default=None, help="Root directory for campaign output (default: from config.json or 'logs')")
    parser.add_argument("--test-duration", type=int, default=None, help="Duration of each test run in seconds (default: from config.json or 180)")
    parser.add_argument("--iterations", type=int, default=None, help="Number of iterations per phase (default: from config.json or 3)")
    parser.add_argument("--no-cleanup", action="store_true", default=False, help="Don't run 'docker compose down' after each run (preserve containers)")
    parser.add_argument(
        "--phase", default=None,
        help=(
            "Select campaign phase(s) to run. "
            "Accepts: 'all', a 1-based number (e.g. '1'), a phase name, "
            "or a comma-separated list (e.g. '1,2'). "
            "If omitted, falls back to the top-level perturbation_profiles/attacks in config.json. "
            "Use --list-phases to see available phases."
        ),
    )
    parser.add_argument("--list-phases", action="store_true", default=False, help="Print available phases and exit")
    # Legacy attack_durations/attack_intensities fallback removed; require structured `attacks` in config.json
    args = parser.parse_args()

    cfg_path = Path(args.config).resolve()
    defaults = _load_campaign_defaults(cfg_path)

    compose_file = args.compose_file or str(defaults.get("compose_file", "docker-compose.yml"))
    # Determine output root: prefer CLI; otherwise use campaign.output_root from config.json.
    # Resolve relative paths against the config file directory so values like "logs_test"
    # are interpreted relative to the config file location.
    raw_output_root = args.output_root if args.output_root is not None else str(defaults.get("output_root", "logs"))
    if os.path.isabs(raw_output_root):
        output_root = raw_output_root
    else:
        # cfg_path points to the config.json provided by --config (or default); resolve relative to it
        output_root = str(cfg_path.parent / raw_output_root)
    test_duration = args.test_duration if args.test_duration is not None else int(defaults.get("test_duration", 180))
    iterations = args.iterations if args.iterations is not None else int(defaults.get("iterations", 3))

    # New: perturbation_profiles list of {name, family, lambda} dicts
    if "perturbation_profiles" in defaults:
        perturbation_profiles = defaults["perturbation_profiles"]
    else:
        # Legacy fallback: convert old string list to minimal profile dicts
        legacy_names = _list_of_str(defaults.get("perturbations", ["none"]))
        perturbation_profiles = [
            {"name": n, "family": "none" if n == "none" else "_legacy", "lambda": 0.0}
            for n in legacy_names
        ]
    attacks_defaults = defaults.get("attacks")
    if attacks_defaults is None:
        raise SystemExit("config.json must include campaign.attacks (structured list). Legacy attack_durations/attack_intensities fallback is removed.")
    attacks = attacks_defaults if isinstance(attacks_defaults, list) else SystemExit("campaign.attacks must be a list")
    collect_cfg = defaults.get("collect", {})
    collect: Dict[str, bool] = {
        "tep_signals": bool(collect_cfg.get("tep_signals", False)),
        "tep_alarm_events": bool(collect_cfg.get("tep_alarm_events", False)),
        "tep_controller_mv_commands": bool(collect_cfg.get("tep_controller_mv_commands", False)),
        "container_logs": bool(collect_cfg.get("container_logs", False)),
    }

    # Determine selected phases: if --phase was provided, resolve it; otherwise
    # when the config defines `campaign.phases` default to running those phases.
    resolved_phases = _resolve_selected_phases(defaults, args)
    if args.phase is None and defaults.get("phases") and not resolved_phases:
        # No explicit --phase given but phases are defined: run all phases by default
        resolved_phases = list(defaults.get("phases"))

    # If we're running explicit phases, don't set a top-level phase name.
    default_phase_name = "" if resolved_phases else str(defaults.get("name", "")) if args.phase is None else ""

    return CampaignConfig(
        compose_file=Path(compose_file).resolve(),
        output_root=Path(output_root).resolve(),
        test_duration=test_duration,
        iterations=iterations,
        perturbation_profiles=perturbation_profiles,
        attacks=attacks,
        collect=collect,
        no_cleanup=bool(args.no_cleanup),
        phase_name=default_phase_name,
        selected_phases=resolved_phases,
    )


def run_cmd(cmd: List[str], env: Dict[str, str], cwd: Path, log_file: Path | None = None) -> int:
    if log_file is None:
        proc = subprocess.run(cmd, cwd=str(cwd), env=env)
        return proc.returncode
    with log_file.open("w", encoding="utf-8") as fh:
        proc = subprocess.run(cmd, cwd=str(cwd), env=env, stdout=fh, stderr=subprocess.STDOUT)
        return proc.returncode


def compose_cmd(cfg: CampaignConfig, *args: str) -> List[str]:
    return ["docker", "compose", "-f", str(cfg.compose_file), *args]


def wait_for_influx(host: str, port: int, user: str, password: str, timeout: int = 120) -> None:
    deadline = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            client = _influx_client(host=host, port=port, username=user, password=password)
            client.ping()
            client.close()
            return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(2)
    raise RuntimeError(f"InfluxDB not healthy after {timeout}s: {last_err}")


def reset_database(host: str, port: int, user: str, password: str, db: str) -> None:
    client = _influx_client(host=host, port=port, username=user, password=password)
    try:
        client.drop_database(db)
        client.create_database(db)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "401" in msg or "authorization failed" in msg.lower():
            raise RuntimeError(
                "InfluxDB authorization failed while resetting the database. "
                "This commonly happens when INFLUXDB_ADMIN_PASSWORD in .env was changed "
                "after InfluxDB data volume initialization. Run 'docker compose down -v' "
                "to recreate InfluxDB with current .env credentials, then retry the campaign."
            ) from exc
        raise
    finally:
        client.close()


def collect_influx_dump(host: str, port: int, user: str, password: str, db: str, start_ns: int, end_ns: int, out_dir: Path, measurements: List[str]) -> None:
    client = _influx_client(host=host, port=port, username=user, password=password, database=db)
    try:
        for measurement in measurements:
            query = f'SELECT * FROM "{measurement}" WHERE time >= {start_ns}ns and time <= {end_ns}ns'
            result = client.query(query)
            output_path = out_dir / f"{measurement}.ndjson"
            with output_path.open("w", encoding="utf-8") as fh:
                for series in result.raw.get("series", []):
                    cols = series.get("columns", [])
                    tags = series.get("tags", {})
                    for values in series.get("values", []):
                        row = {k: v for k, v in zip(cols, values)}
                        if tags:
                            row["tags"] = tags
                        fh.write(json.dumps(row) + "\n")
    finally:
        client.close()


def collect_container_logs(cfg: CampaignConfig, out_dir: Path, services: Iterable[str]) -> None:
    env = os.environ.copy()
    for service in services:
        log_file = out_dir / f"container_{service}.log"
        cmd = compose_cmd(cfg, "logs", "--no-color", service)
        run_cmd(cmd, env=env, cwd=cfg.compose_file.parent, log_file=log_file)


def build_run_group_name(s: Scenario, phase_name: str = "") -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    prefix = f"phase-{phase_name}_" if phase_name else ""
    return (
        f"{prefix}perturbation-{s.perturbation}_"
        f"attackDuration-{s.attack_duration}_intensity-{s.attack_intensity}_{ts}"
    )


def build_iteration_name(s: Scenario) -> str:
    return f"iteration-{s.iteration}"


def _select_phases(all_phases: List[Dict[str, Any]], phase_arg: str) -> List[Dict[str, Any]]:
    """Resolve --phase argument to a list of phase dicts.

    Accepts: 'all', a 1-based integer, a phase name, or a comma-separated
    combination of integers and names.
    """
    if phase_arg.strip().lower() == "all":
        return list(all_phases)
    selected: List[Dict[str, Any]] = []
    for token in phase_arg.split(","):
        token = token.strip()
        if not token:
            continue
        if token.isdigit():
            idx = int(token) - 1
            if not (0 <= idx < len(all_phases)):
                raise SystemExit(f"Phase number {token} out of range (1–{len(all_phases)})")
            selected.append(all_phases[idx])
        else:
            match = next((p for p in all_phases if p.get("name") == token), None)
            if match is None:
                names = [p.get("name") for p in all_phases]
                raise SystemExit(f"Unknown phase '{token}'. Available: {names}")
            selected.append(match)
    return selected


def _apply_phase(base: "CampaignConfig", phase: Dict[str, Any]) -> "CampaignConfig":
    """Return a new CampaignConfig with phase settings layered over the base config."""
    return CampaignConfig(
        compose_file=base.compose_file,
        output_root=base.output_root,
        test_duration=int(phase.get("test_duration", base.test_duration)),
        iterations=int(phase.get("iterations", base.iterations)),
        perturbation_profiles=phase.get("perturbation_profiles", base.perturbation_profiles),
        attacks=phase.get("attacks", base.attacks),
        collect=base.collect,
        no_cleanup=base.no_cleanup,
        phase_name=str(phase.get("name", "")),
        phase_seed_offset=int(phase.get("seed_offset", 0)),
        selected_phases=[],  # phases don't recurse
    )


def scenario_iter(cfg: CampaignConfig) -> Iterable[Scenario]:
    # Iterate the structured `attacks` list; legacy cross-product fallback removed.
    for attack in cfg.attacks:
        duration = int(attack.get("duration", 0))
        intensities = attack.get("intensities") or []
        attack_meta = {k: attack.get(k) for k in ("attack_start_delay_min", "attack_start_delay_max") if attack.get(k) is not None}
        if not intensities:
            for profile in cfg.perturbation_profiles:
                for iteration in range(1, cfg.iterations + 1):
                    yield Scenario(
                        perturbation=profile["name"],
                        perturbation_family=str(profile.get("family", "none")),
                        perturbation_lambda=float(profile.get("lambda", 0.0)),
                        attack_duration=duration,
                        attack_intensity="",
                        iteration=iteration,
                        attack_meta=attack_meta,
                    )
        else:
            for profile, intensity in itertools.product(cfg.perturbation_profiles, intensities):
                for iteration in range(1, cfg.iterations + 1):
                    yield Scenario(
                        perturbation=profile["name"],
                        perturbation_family=str(profile.get("family", "none")),
                        perturbation_lambda=float(profile.get("lambda", 0.0)),
                        attack_duration=duration,
                        attack_intensity=intensity,
                        iteration=iteration,
                        attack_meta=attack_meta,
                    )


def run_one(cfg: CampaignConfig, scenario: Scenario) -> None:
    run_dir = cfg.output_root / build_run_group_name(scenario, cfg.phase_name) / build_iteration_name(scenario)
    run_dir.mkdir(parents=True, exist_ok=True)

    influx_conn = _load_config("influxdb")
    # Always connect via localhost on the host side (published port).
    # The .env may contain INFLUXDB_HOST=influxdb (Docker service name) which
    # is only resolvable inside the Docker network, not from the host.
    host_export_host = "localhost"
    host_export_port = int(influx_conn.get("port", 8086))
    host_export_db = str(influx_conn.get("database", "appdb"))
    host_export_user = str(influx_conn.get("username", "admin"))
    host_export_password = str(influx_conn.get("password", "change_me_admin_password"))

    # Read config for metadata and archival (perturbations now controlled via env vars)
    config_path = Path(cfg.compose_file).parent / "config.json"
    with config_path.open("r", encoding="utf-8") as fh:
        config_data = json.load(fh)
    # Archive a verbatim copy of the config used for this run (for reproducibility)
    with (run_dir / "config.json").open("w", encoding="utf-8") as fh:
        json.dump(config_data, fh, indent=2)
    # Derive perturbation seed: tep_seed + offset + per-iteration shift for independent randomness
    _tep_seed = int(config_data.get("tep_process", {}).get("seed", 10871))
    _seed_offset = int(config_data.get("tep_process", {}).get("perturbations", {}).get("seedOffset", 1000))
    # cfg.phase_seed_offset ensures cross-phase seed independence (0 for clean phases 1/2)
    perturbation_seed = _tep_seed + _seed_offset + cfg.phase_seed_offset + (scenario.iteration - 1) * 31337
    env = os.environ.copy()
    env.update(
        {
            "ATTACK_DURATION": str(scenario.attack_duration),
            "ATTACK_INTENSITY": scenario.attack_intensity,
            "TEP_CONTROLLER_INTERVAL": "1.0",
            "INFLUXDB_DB": host_export_db,
            "INFLUXDB_HOST": "influxdb",
            "INFLUXDB_PORT": str(host_export_port),
            "INFLUXDB_ADMIN_USER": host_export_user,
            "INFLUXDB_ADMIN_PASSWORD": host_export_password,
            "TEP_PERTURBATION_FAMILY": scenario.perturbation_family,
            "TEP_PERTURBATION_LAMBDA": str(scenario.perturbation_lambda),
            "TEP_PERTURBATION_SEED": str(perturbation_seed),
        }
    )

    # Compute a safe simulator duration (simulated seconds) and pass via env var
    # Convert campaign real seconds to simulator simulated seconds using realtime scale
    try:
        realtime_scale = float(env.get("TEP_REALTIME_SCALE", os.environ.get("TEP_REALTIME_SCALE", "1.0")))
    except Exception:
        realtime_scale = 1.0
    # buffer (simulated seconds) to account for startup/teardown/jitter
    buffer_secs = 10
    sim_duration = int(cfg.test_duration * max(realtime_scale, 1e-6)) + int(buffer_secs)
    env["TEP_RUN_DURATION"] = str(sim_duration)

    # Ensure host-mounted sockets directory exists so Docker can mount it and
    # so processes that bind UNIX sockets have a place to create .sock files.
    sockets_dir = Path(cfg.compose_file).parent / "sockets"
    try:
        sockets_dir.mkdir(parents=True, exist_ok=True)
        try:
            sockets_dir.chmod(0o777)
        except Exception:
            # Non-fatal if chmod fails (e.g., on Windows / permission issues).
            pass
    except Exception:
        # If we cannot create the sockets directory, let Docker/docker-compose
        # behave normally and surface errors. We don't want to mask permission
        # issues, but creation here helps common Linux dev workflows.
        pass
    # Load sysdig config early so window size is available for scenario.json metadata
    sysdig_cfg = _load_config("sysdig")
    sysdig_window = int(sysdig_cfg.get("windowSize", 4))
    sysdig_targets = _list_of_str(sysdig_cfg.get("containers", ["influxdb"]))
    if not sysdig_targets:
        sysdig_targets = ["influxdb"]

    # Determine attack start delay. If this is a baseline run (duration==0), skip delay/attack.
    if scenario.attack_duration == 0:
        actual_delay = 0
    else:
        if not scenario.attack_meta or "attack_start_delay_min" not in scenario.attack_meta or "attack_start_delay_max" not in scenario.attack_meta:
            raise RuntimeError("Missing per-attack delay values for non-zero duration attack; these must be set in the `attacks` entry")
        delay_min = int(scenario.attack_meta["attack_start_delay_min"])
        delay_max = int(scenario.attack_meta["attack_start_delay_max"])
        max_safe_delay = max(0, cfg.test_duration - scenario.attack_duration)
        actual_delay = random.randint(delay_min, min(delay_max, max_safe_delay))

    # P4: compute outage start (sim time) so the flush burst aligns with the attack window.
    # For attacked runs: outage ends at attack onset (clamped to sim start).
    # For benign runs: use 25% of run duration as a synthetic reference time.
    p4_outage_start: Optional[float] = None
    if scenario.perturbation_family == "P4" and scenario.perturbation_lambda > 0.0:
        _d_out = scenario.perturbation_lambda * 600.0  # mirrors PerturbationSpec.d_max_p4
        if scenario.attack_duration > 0:
            p4_outage_start = max(0.0, actual_delay * realtime_scale - _d_out)
        else:
            p4_outage_start = max(0.0, cfg.test_duration * 0.25 * realtime_scale - _d_out)
    env["TEP_P4_OUTAGE_START"] = str(p4_outage_start) if p4_outage_start is not None else ""

    # Build PerturbationSpec to derive and record all concrete parameters
    _spec = PerturbationSpec(
        family=scenario.perturbation_family,
        lambda_=scenario.perturbation_lambda,
        seed=perturbation_seed,
        outage_start=p4_outage_start,
    )
    # Affected streams per family (matches spec §4 and sink behaviour):
    # P5 explicitly does NOT perturb alarm events (alarm pass-through enforced in sink).
    # P4 is global: all three streams are buffered during the outage.
    if scenario.perturbation_family == "none":
        _affected_streams: List[str] = []
    elif scenario.perturbation_family == "P5":
        _affected_streams = ["tep_signals", "tep_controller_mv_commands"]
    else:
        _affected_streams = ["tep_signals", "tep_alarm_events", "tep_controller_mv_commands"]
    # Compute actual affected tag list (with at-least-one guarantee) so scenario.json
    # stores exact tag names, not just the selection probability.
    # P4 is global (no tag-level scope); tag list is empty for "none".
    if scenario.perturbation_family in ("P1", "P2", "P3", "P5"):
        _affected_tags: List[str] = _spec.compute_affected_tags(_ALL_ELIGIBLE_TAGS)
    else:
        _affected_tags = []

    (run_dir / "scenario.json").write_text(
        json.dumps(
            {
                # campaign phase
                "campaign_phase": cfg.phase_name,
                # perturbation
                "perturbation_profile": scenario.perturbation,
                "perturbation_family": scenario.perturbation_family,
                "perturbation_lambda": scenario.perturbation_lambda,
                "perturbation_params": _spec.concrete_params(),
                "perturbation_affected_streams": _affected_streams,
                "perturbation_affected_tags": _affected_tags,
                # attack
                "attack_duration": scenario.attack_duration,
                "attack_intensity": scenario.attack_intensity,
                "attack_start_delay": actual_delay,
                # run
                "run_duration_seconds": cfg.test_duration,
                "iteration": scenario.iteration,
                "sysdig_aggregation_window": sysdig_window,
                # seeds
                "tep_process_seed": _tep_seed,
                "perturbation_seed": perturbation_seed,
                # flag: whether perturbation window overlaps attack window
                "perturbation_overlaps_attack": (
                    scenario.perturbation_family == "P4"
                    and scenario.attack_duration > 0
                    and p4_outage_start is not None
                ),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    annotations = LocalAnnotations(output_file=str(run_dir / "annotations.ndjson"))

    sysdig_proc: Optional[subprocess.Popen] = None
    sysdig_reader: Optional[Tuple[threading.Thread, threading.Event]] = None

    start_ns = time.time_ns()
    start_t = time.time()
    attack_start_t: float | None = None

    try:
        annotations.createEvent(
            timestamp=start_t,
            title="run_start",
            description=f" perturbation={scenario.perturbation} iteration={scenario.iteration}",
            tags={"perturbation": scenario.perturbation, "iteration": str(scenario.iteration)},
        )

        run_cmd(compose_cmd(cfg, "down"), env=env, cwd=cfg.compose_file.parent)
        if run_cmd(compose_cmd(cfg, "up", "-d", "influxdb"), env=env, cwd=cfg.compose_file.parent) != 0:
            raise RuntimeError("failed to start influxdb")

        wait_for_influx(host_export_host, host_export_port, host_export_user, host_export_password)
        reset_database(host_export_host, host_export_port, host_export_user, host_export_password, host_export_db)

        if run_cmd(compose_cmd(cfg, "up", "-d", "tep-simulator", "controller", "grafana"), env=env, cwd=cfg.compose_file.parent) != 0:
            raise RuntimeError("failed to start core services")

        # Start sysdig against configured running containers
        container_dict = _get_container_ids(sysdig_targets)
        sysdig_proc, sysdig_reader = _start_sysdig(
            container_dict, sysdig_window, run_dir / "sysdig_logs.ndjson"
        )

        if scenario.attack_duration == 0:
            print("baseline run (no attack)", flush=True)
            attack_end_t = time.time()
        else:
            print(f"attack start delay: {actual_delay}s (range {delay_min}–{delay_max})", flush=True)
            time.sleep(actual_delay)

            attack_start_t = time.time()
            annotations.createEvent(
                timestamp=attack_start_t,
                title="attack_start",
                description=f"intensity={scenario.attack_intensity} duration={scenario.attack_duration}s",
                tags={"intensity": scenario.attack_intensity, "attack_duration": str(scenario.attack_duration)},
            )

            attack_log = run_dir / "container_attack-runner.log"
            if run_cmd(compose_cmd(cfg, "run", "--rm", "attack-runner"), env=env, cwd=cfg.compose_file.parent, log_file=attack_log) != 0:
                raise RuntimeError("attack runner failed - cmd: {}".format(compose_cmd(cfg, "run", "--rm", "attack-runner")))

            attack_end_t = time.time()

            # Only record an attack region if an attack actually started
            if attack_start_t is not None:
                annotations.createRegion(
                    timestamp_start=attack_start_t,
                    timestamp_end=attack_end_t,
                    title="attack",
                    description=f"InfluxDBBurstAttack intensity={scenario.attack_intensity} duration={scenario.attack_duration}s",
                    tags={"intensity": scenario.attack_intensity, "attack_duration": str(scenario.attack_duration)},
                )

        elapsed = int((time.time_ns() - start_ns) / 1_000_000_000)
        if elapsed < cfg.test_duration:
            time.sleep(cfg.test_duration - elapsed)

        end_ns = time.time_ns()
        optional_measurements = [m for m in ["tep_signals", "tep_alarm_events", "tep_controller_mv_commands"] if cfg.collect.get(m)]
        collect_influx_dump(host_export_host, host_export_port, host_export_user, host_export_password, host_export_db, start_ns, end_ns, run_dir, ["attack_records"] + optional_measurements)
        if cfg.collect.get("container_logs"):
            collect_container_logs(cfg, run_dir, ["influxdb", "tep-simulator", "controller", "grafana"])

    finally:
        _stop_sysdig(sysdig_proc, sysdig_reader)
        annotations.createEvent(
            timestamp=time.time(),
            title="run_end",
            description=f" perturbation={scenario.perturbation} iteration={scenario.iteration}",
            tags={"perturbation": scenario.perturbation, "iteration": str(scenario.iteration)},
        )
        if not getattr(cfg, "no_cleanup", False):
            run_cmd(compose_cmd(cfg, "down"), env=env, cwd=cfg.compose_file.parent)


def _run_phase(cfg: CampaignConfig) -> None:
    """Validate attacks and run all scenarios for a single CampaignConfig (one phase)."""
    phase_label = f"[{cfg.phase_name}] " if cfg.phase_name else ""

    for i, attack in enumerate(getattr(cfg, "attacks", []) or []):
        duration = int(attack.get("duration", 0))
        if duration == 0:
            continue  # baseline — per-attack delays are ignored

        if "attack_start_delay_min" not in attack or "attack_start_delay_max" not in attack:
            raise SystemExit(
                f"{phase_label}attack index {i} (duration={duration}) missing "
                "attack_start_delay_min/attack_start_delay_max — these must be set per-attack for non-zero durations"
            )

        delay_min = int(attack.get("attack_start_delay_min"))
        delay_max = int(attack.get("attack_start_delay_max"))
        if delay_min > delay_max:
            raise SystemExit(f"{phase_label}Invalid attack delay range for attack index {i}: min {delay_min} > max {delay_max}")

        max_safe_delay = max(0, int(cfg.test_duration) - int(duration))
        if delay_min > max_safe_delay:
            print(
                f"Warning: {phase_label}attack index {i} delay_min {delay_min}s exceeds safe maximum {max_safe_delay}s; clamping",
                flush=True,
            )
            delay_min = max_safe_delay
        if delay_max > max_safe_delay:
            print(
                f"Warning: {phase_label}attack index {i} delay_max {delay_max}s exceeds safe maximum {max_safe_delay}s; clamping",
                flush=True,
            )
            delay_max = max_safe_delay

        attack["attack_start_delay_min"] = int(delay_min)
        attack["attack_start_delay_max"] = int(delay_max)

    scenarios = list(scenario_iter(cfg))
    total = len(scenarios)
    print(f"{phase_label}phase start: {total} runs", flush=True)

    for idx, scenario in enumerate(scenarios, start=1):
        print(
            f"{phase_label}[{idx}/{total}] perturbation={scenario.perturbation} "
            f"attack_duration={scenario.attack_duration} intensity={scenario.attack_intensity} "
            f"iteration={scenario.iteration}",
            flush=True,
        )
        run_one(cfg, scenario)

    print(f"{phase_label}phase complete", flush=True)


def main() -> int:
    cfg = parse_args()
    cfg.output_root.mkdir(parents=True, exist_ok=True)

    if cfg.selected_phases:
        print(f"Running {len(cfg.selected_phases)} phase(s): {[p.get('name') for p in cfg.selected_phases]}", flush=True)
        for phase in cfg.selected_phases:
            phase_cfg = _apply_phase(cfg, phase)
            _run_phase(phase_cfg)
    else:
        _run_phase(cfg)

    print("campaign complete", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
