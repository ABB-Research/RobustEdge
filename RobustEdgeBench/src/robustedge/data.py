"""Dataset indexing and run loading."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from .constants import OPTIONAL_FILES, PHASE_ORDER, PROFILE_TO_SEVERITY
from .io import discover_run_dirs, parse_scenario_name, read_json, read_ndjson


@dataclass
class Scenario:
    """Normalized run-level metadata."""

    phase: str = "unknown"
    perturbation: str = "unknown"
    perturbation_family: str = "unknown"
    perturbation_profile: str = "unknown"
    severity: float | None = None
    attack_duration: float = 0.0
    attack_intensity: str = ""
    iteration: int | None = None
    test_duration: float | None = None
    attack_start_delay: float | None = None
    scenario_timestamp: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_sources(
        cls,
        scenario_json: dict[str, Any],
        path_fields: dict[str, Any] | None = None,
        profile_to_severity: dict[str, float] | None = None,
    ) -> "Scenario":
        """Create normalized metadata.

        Folder-derived fields override JSON fields because the current campaign
        encodes the most specific perturbation information in the folder name
        (`P1_lam0.50`, etc.), while some example scenario files still contain
        legacy labels such as `moderate`.
        """
        profile_to_severity = profile_to_severity or PROFILE_TO_SEVERITY
        merged = dict(scenario_json or {})
        if path_fields:
            merged.update(path_fields)

        perturbation = str(merged.get("perturbation", "unknown"))
        family = str(merged.get("perturbation_family", perturbation))
        profile = str(merged.get("perturbation_profile", perturbation))
        severity = _safe_float(merged.get("severity"))
        if severity is None:
            severity = profile_to_severity.get(profile, profile_to_severity.get(perturbation))

        return cls(
            phase=str(merged.get("phase", infer_phase_from_attack_and_perturbation(merged))),
            perturbation=perturbation,
            perturbation_family=family,
            perturbation_profile=profile,
            severity=severity,
            attack_duration=float(merged.get("attack_duration", merged.get("attackDuration", 0.0)) or 0.0),
            attack_intensity=str(merged.get("attack_intensity", merged.get("intensity", "")) or ""),
            iteration=_safe_int(merged.get("iteration")),
            test_duration=_safe_float(merged.get("test_duration")),
            attack_start_delay=_safe_float(merged.get("attack_start_delay")),
            scenario_timestamp=merged.get("scenario_timestamp"),
            raw=merged,
        )

    @property
    def phase_order(self) -> int:
        return PHASE_ORDER.get(self.phase, 99)

    @property
    def has_attack(self) -> bool:
        return self.attack_duration > 0

    @property
    def is_clean_benign(self) -> bool:
        return self.phase == "phase1_clean_benign" or (self.attack_duration == 0 and (self.severity or 0) == 0)

    @property
    def attack_interval(self) -> tuple[float, float] | None:
        if not self.has_attack or self.attack_start_delay is None:
            return None
        return float(self.attack_start_delay), float(self.attack_start_delay + self.attack_duration)


def infer_phase_from_attack_and_perturbation(fields: dict[str, Any]) -> str:
    """Fallback phase inference for legacy folder names."""
    duration = float(fields.get("attack_duration", fields.get("attackDuration", 0.0)) or 0.0)
    perturb = str(fields.get("perturbation", "none"))
    sev = _safe_float(fields.get("severity"))
    is_perturbed = perturb not in ("none", "", "unknown") or (sev is not None and sev > 0)
    if duration <= 0 and not is_perturbed:
        return "phase1_clean_benign"
    if duration > 0 and not is_perturbed:
        return "phase2_clean_attacked"
    if duration <= 0 and is_perturbed:
        return "phase3_perturbed_benign"
    return "phase4_perturbed_attacked"


def _safe_float(x: Any) -> float | None:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except Exception:
        return None


def _safe_int(x: Any) -> int | None:
    if x is None or x == "":
        return None
    try:
        return int(x)
    except Exception:
        return None


@dataclass
class RunData:
    """All available data for a single iteration folder."""

    run_id: str
    run_dir: Path
    scenario: Scenario
    config: dict[str, Any]
    sysdig: pd.DataFrame
    signals: pd.DataFrame
    controller: pd.DataFrame
    alarms: pd.DataFrame
    annotations: pd.DataFrame
    attack_records: pd.DataFrame

    @classmethod
    def load(cls, run_dir: str | Path, profile_to_severity: dict[str, float] | None = None) -> "RunData":
        run_dir = Path(run_dir)
        path_fields = parse_scenario_name(run_dir)
        scenario = Scenario.from_sources(read_json(run_dir / "scenario.json"), path_fields, profile_to_severity)
        run_id = make_run_id(run_dir, scenario)
        return cls(
            run_id=run_id,
            run_dir=run_dir,
            scenario=scenario,
            config=read_json(run_dir / "config.json"),
            sysdig=read_ndjson(run_dir / "sysdig_logs.ndjson"),
            signals=read_ndjson(run_dir / "tep_signals.ndjson"),
            controller=read_ndjson(run_dir / "tep_controller_mv_commands.ndjson"),
            alarms=read_ndjson(run_dir / "tep_alarm_events.ndjson"),
            annotations=read_ndjson(run_dir / "annotations.ndjson"),
            attack_records=read_ndjson(run_dir / "attack_records.ndjson"),
        )

    def missing_optional_files(self) -> list[str]:
        return [f for f in OPTIONAL_FILES if not (self.run_dir / f).exists()]


def make_run_id(run_dir: Path, scenario: Scenario) -> str:
    scenario_name = run_dir.parent.name if run_dir.name.startswith("iteration") else run_dir.name
    iteration = run_dir.name if run_dir.name.startswith("iteration") else f"iteration-{scenario.iteration or 0}"
    return f"{scenario_name}__{iteration}"


@dataclass
class DatasetIndex:
    """Index of all discovered runs."""

    data_root: Path
    run_dirs: list[Path]
    profile_to_severity: dict[str, float] = field(default_factory=lambda: PROFILE_TO_SEVERITY.copy())

    @classmethod
    def from_root(cls, data_root: str | Path, profile_to_severity: dict[str, float] | None = None) -> "DatasetIndex":
        data_root = Path(data_root)
        return cls(data_root=data_root, run_dirs=discover_run_dirs(data_root), profile_to_severity=profile_to_severity or PROFILE_TO_SEVERITY.copy())

    def load_runs(self) -> list[RunData]:
        return [RunData.load(p, self.profile_to_severity) for p in self.run_dirs]

    def to_frame(self) -> pd.DataFrame:
        rows = []
        for p in self.run_dirs:
            run = RunData.load(p, self.profile_to_severity)
            rows.append({
                "run_id": run.run_id,
                "run_dir": str(run.run_dir),
                "scenario_dir": run.run_dir.parent.name if run.run_dir.name.startswith("iteration") else run.run_dir.name,
                "phase": run.scenario.phase,
                "phase_order": run.scenario.phase_order,
                "iteration": run.scenario.iteration,
                "perturbation": run.scenario.perturbation,
                "perturbation_family": run.scenario.perturbation_family,
                "perturbation_profile": run.scenario.perturbation_profile,
                "severity": run.scenario.severity,
                "attack_duration": run.scenario.attack_duration,
                "attack_intensity": run.scenario.attack_intensity,
                "attack_start_delay": run.scenario.attack_start_delay,
                "test_duration": run.scenario.test_duration,
                "scenario_timestamp": run.scenario.scenario_timestamp,
                "n_sysdig_rows": len(run.sysdig),
                "n_signals_rows": len(run.signals),
                "n_controller_rows": len(run.controller),
                "n_alarm_rows": len(run.alarms),
                "n_annotation_rows": len(run.annotations),
                "n_attack_record_rows": len(run.attack_records),
                "missing_optional_files": ",".join(run.missing_optional_files()),
            })
        return pd.DataFrame(rows)
