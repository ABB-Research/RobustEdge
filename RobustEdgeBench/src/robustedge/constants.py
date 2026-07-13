"""Constants shared by the RobustEdgeBench analysis package."""

from __future__ import annotations

PROFILE_TO_SEVERITY = {
    "none": 0.0,
    "light": 0.25,
    "moderate": 0.50,
    "heavy": 1.0,
}

PHASE_ORDER = {
    "phase1_clean_benign": 1,
    "phase2_clean_attacked": 2,
    "phase3_perturbed_benign": 3,
    "phase4_perturbed_attacked": 4,
}

CORE_FILES = ["scenario.json", "sysdig_logs.ndjson"]

OPTIONAL_FILES = [
    "config.json",
    "annotations.ndjson",
    "attack_records.ndjson",
    "tep_signals.ndjson",
    "tep_alarm_events.ndjson",
    "tep_controller_mv_commands.ndjson",
]

TIME_COLUMN_CANDIDATES = [
    "time",
    "timestamp",
    "ts",
    "datetime",
    "window_start",
    "window_start_time",
    "start_time",
    "fields.time",
    "tags.time",
]

NON_FEATURE_COLUMNS = {
    "run_id",
    "run_dir",
    "scenario_dir",
    "phase",
    "phase_order",
    "iteration",
    "perturbation",
    "perturbation_family",
    "perturbation_profile",
    "severity",
    "attack_duration",
    "attack_intensity",
    "attack_start_delay",
    "test_duration",
    "window_start",
    "window_end",
    "timestamp",
    "time",
    "relative_time_s",
    "label",
}
