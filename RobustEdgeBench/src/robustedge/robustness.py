"""Aggregation of per-run and pooled window-level robustness profiles."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .metrics import evaluate_predictions


FAMILIES = ["P1", "P2", "P3", "P4", "P5"]


def aggregate_metrics(metrics: pd.DataFrame) -> pd.DataFrame:
    """Aggregate per-run metrics across repetitions and CV splits."""
    group_cols = [
        "feature_view",
        "detector",
        "phase",
        "perturbation_family",
        "severity",
        "attack_duration",
        "attack_intensity",
    ]
    numeric_cols = [
        c for c in metrics.columns
        if c not in group_cols + [
            "split_id", "split_role", "run_id", "run_dir", "threshold", "perturbation", "perturbation_profile",
            "train_run_ids", "validation_run_id",
        ]
        and pd.api.types.is_numeric_dtype(metrics[c])
    ]
    out = metrics.groupby(group_cols, dropna=False)[numeric_cols].agg(["mean", "std", "count"])
    out.columns = ["_".join(c).strip("_") for c in out.columns.to_flat_index()]
    return out.reset_index()


def aggregate_window_metrics(
    scores: pd.DataFrame,
    window_seconds: float,
    group_by_attack_duration: bool = False,
) -> pd.DataFrame:
    """Compute pooled condition metrics from individual window scores.

    This complements per-run metrics.  It pools all windows within a condition,
    which is useful for the user's requested view: all attack windows together
    and all non-attack windows together.
    """
    base_cols = ["feature_view", "detector", "phase", "perturbation_family", "severity", "attack_intensity"]
    if group_by_attack_duration:
        base_cols.append("attack_duration")

    rows = []
    for keys, g in scores.groupby(base_cols, dropna=False):
        y_true = g["label"].to_numpy(dtype=int)
        y_pred = g["prediction"].to_numpy(dtype=int)
        score = g["score"].to_numpy(dtype=float)
        row = dict(zip(base_cols, keys if isinstance(keys, tuple) else (keys,)))
        row.update(evaluate_predictions(y_true, score, y_pred, window_seconds))
        row["n_runs"] = int(g["run_id"].nunique())
        row["n_splits"] = int(g["split_id"].nunique()) if "split_id" in g else 1
        rows.append(row)
    return pd.DataFrame(rows)


def robustness_summary(agg: pd.DataFrame, metric: str, higher_is_better: bool = True, eps: float = 1e-9) -> pd.DataFrame:
    """Summarize discrete severity profiles for one metric.

    The current dataset contains only selected severity points (0.5 and 1.0)
    plus a clean baseline (0.0).  These summaries should therefore be
    interpreted as discrete robustness profiles, not as a dense robustness
    integral.
    """
    rows = []
    keys = ["feature_view", "detector", "perturbation_family", "attack_duration", "attack_intensity"]
    for group, g in agg.groupby(keys, dropna=False):
        vals = g.sort_values("severity")[metric].dropna().astype(float).values
        if len(vals) == 0:
            continue
        row = dict(zip(keys, group if isinstance(group, tuple) else (group,)))
        row.update({
            "metric": metric,
            "higher_is_better": higher_is_better,
            "R_avg": float(np.mean(vals)),
            "R_worst": float(np.min(vals) if higher_is_better else np.max(vals)),
            "R_prod": float(np.exp(np.mean(np.log(vals + eps)))) if higher_is_better else float("nan"),
            "n_severity_points": int(len(vals)),
        })
        rows.append(row)
    return pd.DataFrame(rows)


def add_lambda0_baseline(
    df: pd.DataFrame,
    metric_cols: list[str] | None = None,
    families: list[str] | None = None,
    benign: bool = False,
) -> pd.DataFrame:
    """Replicate clean baseline rows across perturbation families for plots.

    For perturbed attacked data, the clean baseline is phase2_clean_attacked
    with perturbation none and severity 0.  For perturbed benign false-alarm
    plots, the clean baseline is phase1_clean_benign.  This allows heatmaps to
    always show lambda = 0, 0.5, 1.0.
    """
    families = families or FAMILIES
    if df.empty:
        return df.copy()
    out = [df.copy()]
    phase = "phase1_clean_benign" if benign else "phase2_clean_attacked"
    base = df[(df["phase"] == phase) & (df["severity"].fillna(0.0) == 0.0)].copy()
    if base.empty:
        return df.copy()
    replicated = []
    for fam in families:
        b = base.copy()
        b["perturbation_family"] = fam
        b["severity"] = 0.0
        b["perturbation_profile"] = fam if "perturbation_profile" in b else fam
        replicated.append(b)
    if replicated:
        out.append(pd.concat(replicated, ignore_index=True))
    combined = pd.concat(out, ignore_index=True)
    # Prefer explicit perturbation rows over replicated baseline if duplicates exist.
    subset = [c for c in ["feature_view", "detector", "phase", "perturbation_family", "severity", "attack_duration", "attack_intensity"] if c in combined.columns]
    return combined.drop_duplicates(subset=subset, keep="first")
