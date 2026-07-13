"""Feature extraction for runtime, process and controller views.

The detector input is built on the sysdig runtime windows.  Sysdig provides
one row per monitoring window and is therefore used as the alignment anchor.
Process telemetry and controller commands are aligned to these windows using a
backward as-of join: each detector window receives the most recent process or
controller value available at or before the sysdig timestamp.

The default feature prefixes are:

``rt_``
    Runtime/sysdig bag-of-system-call features.
``proc_``
    TEP process telemetry features aligned to sysdig windows.
``ctrl_``
    Controller command/audit features aligned to sysdig windows.
``alarm_``
    Optional alarm-event diagnostic features. Disabled by default.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .constants import NON_FEATURE_COLUMNS
from .data import RunData
from .io import infer_time_column, parse_timestamps
from .labels import add_window_labels, attack_intervals_from_run


def _sanitize(name: str) -> str:
    """Convert arbitrary tag/field names into stable feature-name fragments."""
    name = str(name).strip().replace(" ", "_").replace("/", "_").replace("-", "_")
    name = name.replace(".", "_").replace(":", "_").replace("%", "pct")
    return "".join(ch for ch in name if ch.isalnum() or ch == "_")


def _asof_time(series: pd.Series) -> pd.Series:
    """Normalize timestamps for ``pandas.merge_asof``.

    ``merge_asof`` requires the left and right keys to have exactly the same
    dtype.  Depending on pandas version and the input schema, one stream may be
    parsed as ``datetime64[ns, UTC]`` and another as ``datetime64[us, UTC]``.
    This helper normalizes all alignment keys to timezone-naive UTC
    ``datetime64[ns]``.
    """
    ts = pd.to_datetime(series, utc=True, errors="coerce")
    if pd.api.types.is_datetime64tz_dtype(ts.dtype):
        ts = ts.dt.tz_convert("UTC").dt.tz_localize(None)
    return ts.astype("datetime64[ns]")


def _relative_times_from_df(df: pd.DataFrame, fallback_window_seconds: float) -> tuple[pd.Series, pd.Timestamp | None]:
    """Return normalized timestamps and run-start timestamp for a DataFrame."""
    time_col = infer_time_column(df)
    if time_col is None:
        ts = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")
        return ts, None
    ts = _asof_time(parse_timestamps(df[time_col]))
    start = ts.min() if not ts.isna().all() else None
    return ts, start


def _anchor_times(anchor: pd.DataFrame, fallback_start: pd.Timestamp) -> pd.Series:
    """Return merge-ready sysdig-window anchor timestamps.

    If the runtime table has valid timestamps, they are normalized and used.
    Otherwise, anchor timestamps are reconstructed from ``window_end`` relative
    to ``fallback_start``.
    """
    if "timestamp" in anchor.columns and not pd.isna(anchor["timestamp"]).all():
        ts = _asof_time(anchor["timestamp"])
        if ts.notna().any():
            return ts
    return pd.Series(
        fallback_start + pd.to_timedelta(anchor["window_end"].to_numpy(), unit="s"),
        index=anchor.index,
        dtype="datetime64[ns]",
    )


@dataclass
class RuntimeSysdigExtractor:
    """Extract bag-of-system-call features from ``sysdig_logs.ndjson``.

    This follows the feature idea of the ABB zero-day container paper: each
    fixed time window is represented by counts of system-call types.  In the
    current data, syscalls are typically stored in flattened columns such as
    ``fields.write`` after NDJSON normalization.
    """

    window_seconds: float = 4.0

    def transform(self, run: RunData) -> pd.DataFrame:
        df = run.sysdig.copy()
        if df.empty:
            return pd.DataFrame()

        ts, start = _relative_times_from_df(df, self.window_seconds)
        numeric = pd.DataFrame(index=df.index)

        # Preferred schema: flattened fields.<syscall> columns.
        field_cols = [c for c in df.columns if c.startswith("fields.")]
        if field_cols:
            for c in field_cols:
                feature = "rt_" + _sanitize(c.replace("fields.", ""))
                numeric[feature] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
        else:
            # Fallback: all numeric non-metadata columns.
            for c in df.columns:
                if c in NON_FEATURE_COLUMNS or c.startswith("tags.") or c == "measurement":
                    continue
                vals = pd.to_numeric(df[c], errors="coerce")
                if vals.notna().any():
                    numeric["rt_" + _sanitize(c)] = vals.fillna(0.0)

        if numeric.empty:
            numeric["rt_row_count"] = 1.0

        if start is not None:
            relative = (ts - start).dt.total_seconds()
            fallback = pd.Series(np.arange(len(df)) * self.window_seconds, index=df.index)
            relative = relative.fillna(fallback)
        else:
            relative = pd.Series(np.arange(len(df)) * self.window_seconds, index=df.index)
            ts = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")

        out = numeric.copy()
        out.insert(0, "relative_time_s", relative.astype(float).to_numpy())
        out.insert(1, "window_start", out["relative_time_s"])
        out.insert(2, "window_end", out["relative_time_s"] + self.window_seconds)
        out.insert(3, "timestamp", _asof_time(ts))
        return out.reset_index(drop=True)


@dataclass
class ProcessSignalExtractor:
    """Extract TEP signal features aligned to runtime windows.

    For each numeric field in the process stream (typically ``value``,
    ``command`` and ``feedback``), the latest available value per tag is carried
    forward to the sysdig window.  Optional deltas and update-count features
    preserve simple temporal/change information without requiring sequence
    models.
    """

    include_deltas: bool = True
    include_update_counts: bool = True

    def transform(self, run: RunData, anchor: pd.DataFrame) -> pd.DataFrame:
        df = run.signals.copy()
        if df.empty or anchor.empty:
            return pd.DataFrame(index=anchor.index)
        time_col = infer_time_column(df)
        if time_col is None or "name" not in df.columns:
            return pd.DataFrame(index=anchor.index)

        df["_timestamp"] = _asof_time(parse_timestamps(df[time_col]))
        df = df.dropna(subset=["_timestamp"]).sort_values("_timestamp")
        if df.empty:
            return pd.DataFrame(index=anchor.index)

        anchor_ts = _anchor_times(anchor, fallback_start=df["_timestamp"].min())
        anchor_df = pd.DataFrame({"_anchor_time": anchor_ts, "_anchor_idx": np.arange(len(anchor))}).sort_values("_anchor_time")

        pieces: list[pd.DataFrame] = []
        numeric_fields = [c for c in ["value", "command", "feedback"] if c in df.columns]

        for field in numeric_fields:
            temp = df[["_timestamp", "name", field]].copy()
            temp[field] = pd.to_numeric(temp[field], errors="coerce")
            temp = temp.dropna(subset=[field])
            if temp.empty:
                continue
            wide = temp.pivot_table(index="_timestamp", columns="name", values=field, aggfunc="last").sort_index()
            wide.columns = [f"proc_last_{_sanitize(c)}_{field}" for c in wide.columns]
            wide_reset = wide.reset_index()
            wide_reset["_timestamp"] = _asof_time(wide_reset["_timestamp"])
            wide_reset = wide_reset.dropna(subset=["_timestamp"]).sort_values("_timestamp")

            aligned = pd.merge_asof(
                anchor_df,
                wide_reset,
                left_on="_anchor_time",
                right_on="_timestamp",
                direction="backward",
            )
            aligned = aligned.sort_values("_anchor_idx").drop(columns=[c for c in ["_timestamp", "_anchor_time", "_anchor_idx"] if c in aligned.columns])
            aligned = aligned.ffill().fillna(0.0)
            if self.include_deltas:
                delta = aligned.diff().fillna(0.0)
                delta.columns = [c.replace("proc_last_", "proc_delta_") for c in delta.columns]
                aligned = pd.concat([aligned, delta], axis=1)
            pieces.append(aligned.reset_index(drop=True))

        if self.include_update_counts:
            counts = self._update_counts(df, anchor)
            if not counts.empty:
                pieces.append(counts.reset_index(drop=True))

        return pd.concat(pieces, axis=1) if pieces else pd.DataFrame(index=anchor.index)

    def _update_counts(self, df: pd.DataFrame, anchor: pd.DataFrame) -> pd.DataFrame:
        if "_timestamp" not in df.columns or anchor.empty:
            return pd.DataFrame(index=anchor.index)
        anchor_ts = _anchor_times(anchor, fallback_start=df["_timestamp"].min())
        if len(anchor_ts) < 2:
            return pd.DataFrame(index=anchor.index)

        start_ts = anchor_ts.min()
        start_edges = start_ts + pd.to_timedelta(anchor["window_start"], unit="s")
        end_edges = start_ts + pd.to_timedelta(anchor["window_end"], unit="s")

        rows = []
        for s, e in zip(start_edges, end_edges):
            mask = (df["_timestamp"] >= s) & (df["_timestamp"] < e)
            sub = df.loc[mask]
            row = {"proc_update_count_total": float(len(sub))}
            if "record_type" in sub.columns:
                for k, v in sub["record_type"].astype(str).value_counts().items():
                    row[f"proc_update_count_record_type_{_sanitize(k)}"] = float(v)
            if "category" in sub.columns:
                for k, v in sub["category"].astype(str).value_counts().items():
                    row[f"proc_update_count_category_{_sanitize(k)}"] = float(v)
            rows.append(row)
        return pd.DataFrame(rows).fillna(0.0)


@dataclass
class ControllerCommandExtractor:
    """Extract controller MV command features aligned to runtime windows."""

    include_deltas: bool = True

    def transform(self, run: RunData, anchor: pd.DataFrame) -> pd.DataFrame:
        df = run.controller.copy()
        if df.empty or anchor.empty:
            return pd.DataFrame(index=anchor.index)
        time_col = infer_time_column(df)
        if time_col is None or "name" not in df.columns or "command" not in df.columns:
            return pd.DataFrame(index=anchor.index)
        df["_timestamp"] = _asof_time(parse_timestamps(df[time_col]))
        df["command"] = pd.to_numeric(df["command"], errors="coerce")
        df = df.dropna(subset=["_timestamp", "command"]).sort_values("_timestamp")
        if df.empty:
            return pd.DataFrame(index=anchor.index)

        anchor_ts = _anchor_times(anchor, fallback_start=df["_timestamp"].min())
        anchor_df = pd.DataFrame({"_anchor_time": anchor_ts, "_anchor_idx": np.arange(len(anchor))}).sort_values("_anchor_time")

        wide = df.pivot_table(index="_timestamp", columns="name", values="command", aggfunc="last").sort_index()
        wide.columns = [f"ctrl_last_command_{_sanitize(c)}" for c in wide.columns]
        wide_reset = wide.reset_index()
        wide_reset["_timestamp"] = _asof_time(wide_reset["_timestamp"])
        wide_reset = wide_reset.dropna(subset=["_timestamp"]).sort_values("_timestamp")

        aligned = pd.merge_asof(
            anchor_df,
            wide_reset,
            left_on="_anchor_time",
            right_on="_timestamp",
            direction="backward",
        )
        aligned = aligned.sort_values("_anchor_idx").drop(columns=[c for c in ["_timestamp", "_anchor_time", "_anchor_idx"] if c in aligned.columns])
        aligned = aligned.ffill().fillna(0.0)
        if self.include_deltas:
            delta = aligned.diff().fillna(0.0)
            delta.columns = [c.replace("ctrl_last_", "ctrl_delta_") for c in delta.columns]
            aligned = pd.concat([aligned, delta], axis=1)

        start_ts = anchor_ts.min()
        rows = []
        for ws, we in zip(anchor["window_start"], anchor["window_end"]):
            s = start_ts + pd.to_timedelta(ws, unit="s")
            e = start_ts + pd.to_timedelta(we, unit="s")
            rows.append({"ctrl_update_count_total": float(((df["_timestamp"] >= s) & (df["_timestamp"] < e)).sum())})
        count_df = pd.DataFrame(rows)
        return pd.concat([aligned.reset_index(drop=True), count_df], axis=1)


@dataclass
class AlarmEventExtractor:
    """Optional diagnostic alarm-event features.

    Disabled by default because alarm activations may reflect genuine process
    abnormalities rather than container/runtime attacks.
    """

    def transform(self, run: RunData, anchor: pd.DataFrame) -> pd.DataFrame:
        df = run.alarms.copy()
        if df.empty or anchor.empty:
            return pd.DataFrame(index=anchor.index)
        time_col = infer_time_column(df)
        if time_col is None:
            return pd.DataFrame(index=anchor.index)
        df["_timestamp"] = _asof_time(parse_timestamps(df[time_col]))
        df = df.dropna(subset=["_timestamp"])
        if df.empty:
            return pd.DataFrame(index=anchor.index)
        start_ts = _anchor_times(anchor, fallback_start=df["_timestamp"].min()).min()
        rows = []
        for ws, we in zip(anchor["window_start"], anchor["window_end"]):
            s = start_ts + pd.to_timedelta(ws, unit="s")
            e = start_ts + pd.to_timedelta(we, unit="s")
            sub = df[(df["_timestamp"] >= s) & (df["_timestamp"] < e)]
            row = {"alarm_event_count_total": float(len(sub))}
            if "state" in sub.columns:
                for k, v in sub["state"].astype(str).value_counts().items():
                    row[f"alarm_event_state_{_sanitize(k)}"] = float(v)
            rows.append(row)
        return pd.DataFrame(rows).fillna(0.0)


@dataclass
class MultiViewFeatureBuilder:
    """Build detector-ready features from runtime, process and controller views."""

    window_seconds: float = 4.0
    include_runtime_features: bool = True
    include_process_features: bool = True
    include_controller_features: bool = True
    include_alarm_features: bool = False
    process_deltas: bool = True
    process_update_counts: bool = True
    controller_deltas: bool = True

    def transform_run(self, run: RunData) -> pd.DataFrame:
        runtime = RuntimeSysdigExtractor(self.window_seconds).transform(run)
        if runtime.empty:
            return pd.DataFrame()
        pieces: list[pd.DataFrame] = []
        if self.include_runtime_features:
            pieces.append(runtime.copy())
        else:
            pieces.append(runtime[["relative_time_s", "window_start", "window_end", "timestamp"]].copy())

        if self.include_process_features:
            proc = ProcessSignalExtractor(include_deltas=self.process_deltas, include_update_counts=self.process_update_counts).transform(run, runtime)
            if not proc.empty:
                pieces.append(proc)
        if self.include_controller_features:
            ctrl = ControllerCommandExtractor(include_deltas=self.controller_deltas).transform(run, runtime)
            if not ctrl.empty:
                pieces.append(ctrl)
        if self.include_alarm_features:
            alarms = AlarmEventExtractor().transform(run, runtime)
            if not alarms.empty:
                pieces.append(alarms)

        base = pieces[0].reset_index(drop=True)
        for extra in pieces[1:]:
            extra = extra.reset_index(drop=True)
            for c in list(extra.columns):
                if c in base.columns:
                    extra = extra.drop(columns=[c])
            base = pd.concat([base, extra], axis=1)

        base["run_id"] = run.run_id
        base["run_dir"] = str(run.run_dir)
        base["phase"] = run.scenario.phase
        base["phase_order"] = run.scenario.phase_order
        base["perturbation"] = run.scenario.perturbation
        base["perturbation_family"] = run.scenario.perturbation_family
        base["perturbation_profile"] = run.scenario.perturbation_profile
        base["severity"] = run.scenario.severity
        base["attack_duration"] = run.scenario.attack_duration
        base["attack_intensity"] = run.scenario.attack_intensity
        base["attack_start_delay"] = run.scenario.attack_start_delay
        base["test_duration"] = run.scenario.test_duration
        base["iteration"] = run.scenario.iteration
        reference_time = _asof_time(runtime["timestamp"]).min() if "timestamp" in runtime.columns and not pd.isna(runtime["timestamp"]).all() else None
        base = add_window_labels(base, attack_intervals_from_run(run, reference_time=reference_time))
        return base

    def transform_runs(self, runs: list[RunData]) -> pd.DataFrame:
        tables = []
        for run in runs:
            table = self.transform_run(run)
            if table.empty:
                print(f"[WARN] no features extracted for {run.run_id}")
            else:
                tables.append(table)
        if not tables:
            return pd.DataFrame()
        return pd.concat(tables, ignore_index=True).fillna(0.0)


def infer_feature_columns(df: pd.DataFrame, prefixes: tuple[str, ...] | None = None) -> list[str]:
    """Infer numeric detector feature columns.

    Parameters
    ----------
    df:
        Combined feature table.
    prefixes:
        Optional prefixes such as ``("rt_", "proc_", "ctrl_")`` for selecting
        a feature view.
    """
    cols: list[str] = []
    for c in df.columns:
        if c in NON_FEATURE_COLUMNS:
            continue
        if prefixes is not None and not c.startswith(prefixes):
            continue
        if pd.api.types.is_numeric_dtype(df[c]):
            cols.append(c)
    return cols
