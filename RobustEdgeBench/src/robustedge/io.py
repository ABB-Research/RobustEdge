"""General I/O utilities for RobustEdgeBench."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd

from .constants import CORE_FILES, TIME_COLUMN_CANDIDATES


def read_json(path: str | Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read a JSON file. Missing files return an empty dict by default."""
    path = Path(path)
    if not path.exists():
        return {} if default is None else default
    with path.open("r", encoding="utf-8", errors="replace") as f:
        return json.load(f)


def read_ndjson(path: str | Path, max_rows: int | None = None) -> pd.DataFrame:
    """Read newline-delimited JSON into a normalized DataFrame.

    The parser is intentionally permissive. Empty or missing files return an
    empty DataFrame. Malformed lines are skipped with a warning.
    """
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()

    rows = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line_no, line in enumerate(f, start=1):
            if max_rows is not None and len(rows) >= max_rows:
                break
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                print(f"[WARN] malformed JSON in {path} at line {line_no}; skipping")
                continue
            if isinstance(obj, dict):
                rows.append(obj)
            else:
                rows.append({"value": obj})
    if not rows:
        return pd.DataFrame()
    return pd.json_normalize(rows, sep=".")


def parse_timestamps(series: pd.Series) -> pd.Series:
    """Parse timestamps from ISO strings or Unix numeric values.

    Numeric timestamps are interpreted by scale:
    nanoseconds if median > 1e14, milliseconds if median > 1e11, otherwise seconds.
    """
    if series.empty:
        return pd.to_datetime(series)
    if pd.api.types.is_numeric_dtype(series):
        s = pd.to_numeric(series, errors="coerce")
        med = s.dropna().median() if not s.dropna().empty else 0
        if med > 1e14:
            return pd.to_datetime(series, unit="ns", utc=True, errors="coerce")
        if med > 1e11:
            return pd.to_datetime(series, unit="ms", utc=True, errors="coerce")
        return pd.to_datetime(series, unit="s", utc=True, errors="coerce")
    return pd.to_datetime(series, utc=True, errors="coerce")


def infer_time_column(df: pd.DataFrame) -> str | None:
    """Infer a timestamp column from a DataFrame."""
    if df.empty:
        return None
    lower = {c.lower(): c for c in df.columns}
    for cand in TIME_COLUMN_CANDIDATES:
        if cand.lower() in lower:
            return lower[cand.lower()]
    for c in df.columns:
        cl = c.lower()
        if "timestamp" in cl or cl == "time" or cl.endswith(".time") or cl.endswith("_time"):
            return c
    return None


def discover_run_dirs(data_root: str | Path) -> list[Path]:
    """Discover iteration/run directories containing required files."""
    data_root = Path(data_root)
    if not data_root.exists():
        raise FileNotFoundError(f"Data root does not exist: {data_root}")

    run_dirs = []
    for scenario_file in data_root.rglob("scenario.json"):
        run_dir = scenario_file.parent
        if all((run_dir / f).exists() for f in CORE_FILES):
            run_dirs.append(run_dir)
    return sorted(set(run_dirs))


def parse_scenario_name(path: str | Path) -> dict[str, Any]:
    """Parse metadata from current and legacy scenario folder names."""
    p = Path(path)
    name = p.name
    if re.fullmatch(r"iteration[-_]\d+", name, flags=re.IGNORECASE):
        iteration = int(re.findall(r"\d+", name)[0])
        name = p.parent.name
    else:
        iteration = None

    out: dict[str, Any] = {}
    m = re.search(r"phase-(.*?)_perturbation-", name)
    if m:
        out["phase"] = m.group(1)
    m = re.search(r"perturbation-([^_]+)", name)
    if m:
        perturbation = m.group(1)
        out["perturbation"] = perturbation
        if perturbation.upper().startswith("P"):
            out["perturbation_family"] = perturbation.upper()
            out["perturbation_profile"] = perturbation.upper()
        else:
            out["perturbation_family"] = perturbation
            out["perturbation_profile"] = perturbation
    m = re.search(r"lam([0-9]+(?:\.[0-9]+)?)", name)
    if m:
        out["severity"] = float(m.group(1))
    m = re.search(r"attackDuration-([0-9.]+)", name)
    if m:
        val = float(m.group(1))
        out["attack_duration"] = int(val) if val.is_integer() else val
    m = re.search(r"intensity-([^_]*)(?:_|$)", name)
    if m:
        out["attack_intensity"] = m.group(1)
    m = re.search(r"_(\d{8}T\d{6}Z)$", name)
    if m:
        out["scenario_timestamp"] = m.group(1)
    if iteration is not None:
        out["iteration"] = iteration
    return out
