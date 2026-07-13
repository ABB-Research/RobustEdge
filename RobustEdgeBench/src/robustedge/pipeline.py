"""End-to-end RobustEdgeBench pipeline.

The pipeline now supports the current campaign design:

* three clean benign phase-1 runs used in leave-one-validation splits;
* multiple feature views for ablation studies;
* per-run metrics and pooled window-level condition metrics;
* heatmaps over the available discrete severity levels (0, 0.5, 1.0);
* timeline plots for all test runs and all detectors.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import yaml
from sklearn.preprocessing import StandardScaler

from .calibration import QuantileCalibrator
from .data import DatasetIndex, RunData
from .features import MultiViewFeatureBuilder, infer_feature_columns
from .labels import intervals_from_binary_labels
from .metrics import evaluate_run
from .models import default_detectors
from .plotting import (
    DEFAULT_FAMILIES,
    plot_all_models_timeline,
    plot_detector_metric_bars,
    plot_metric_distribution,
    plot_metric_heatmap,
)
from .robustness import add_lambda0_baseline, aggregate_metrics, aggregate_window_metrics, robustness_summary


@dataclass(frozen=True)
class CleanBenignSplit:
    """One leave-one-clean-benign-out calibration split."""

    split_id: str
    train_run_ids: list[str]
    validation_run_id: str


def load_config(path: str | Path = "configs/default.yaml") -> dict[str, Any]:
    """Load YAML configuration."""
    with Path(path).open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)
    

def _write_csv(df: pd.DataFrame, path: str | Path, columns: list[str] | None = None) -> None:
    """Write a CSV file robustly.

    The function always creates the file. If the DataFrame is empty and
    expected columns are provided, it writes an empty CSV with headers. This
    avoids confusing FileNotFoundError / EmptyDataError situations in notebooks.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if df is None:
        df = pd.DataFrame(columns=columns or [])
    elif df.empty and columns is not None:
        df = df.reindex(columns=columns)

    df.to_csv(path, index=False)


def build_features(data_root: str | Path, config: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame, list[RunData]]:
    """Discover runs and build the full feature table.

    The feature table may contain all available views (runtime, process,
    controller).  Actual model training later selects a view by prefix.
    """
    profile_to_severity = config.get("scenario", {}).get("profile_to_severity")
    index = DatasetIndex.from_root(data_root, profile_to_severity=profile_to_severity)
    runs = index.load_runs()
    manifest = index.to_frame()
    fcfg = config.get("features", {})
    builder = MultiViewFeatureBuilder(
        window_seconds=float(fcfg.get("sysdig_window_seconds", 4.0)),
        include_runtime_features=bool(fcfg.get("include_runtime_features", True)),
        include_process_features=bool(fcfg.get("include_process_features", True)),
        include_controller_features=bool(fcfg.get("include_controller_features", True)),
        include_alarm_features=bool(fcfg.get("include_alarm_features", False)),
        process_deltas=bool(fcfg.get("process_deltas", True)),
        process_update_counts=bool(fcfg.get("process_update_counts", True)),
        controller_deltas=bool(fcfg.get("controller_deltas", True)),
    )
    features = builder.transform_runs(runs)
    return features, manifest, runs


def select_feature_prefixes(feature_view: str) -> tuple[str, ...]:
    """Map a named feature view to column prefixes."""
    if feature_view == "runtime":
        return ("rt_",)
    if feature_view == "runtime_process":
        return ("rt_", "proc_")
    if feature_view == "runtime_controller":
        return ("rt_", "ctrl_")
    if feature_view == "process_controller":
        return ("proc_", "ctrl_")
    if feature_view == "fused":
        return ("rt_", "proc_", "ctrl_")
    raise ValueError(f"Unknown feature_view: {feature_view}")


def configured_feature_views(config: dict[str, Any]) -> list[str]:
    """Return feature views requested in config.

    If ``feature_views`` is present, it is used.  Otherwise, the older single
    field ``feature_view`` is used for backward compatibility.
    """
    fcfg = config.get("features", {})
    views = fcfg.get("feature_views")
    if views:
        return list(views)
    return [fcfg.get("feature_view", "runtime")]


def make_clean_benign_cv_splits(features: pd.DataFrame) -> list[CleanBenignSplit]:
    """Create leave-one-clean-benign-out splits.

    With the current three phase-1 runs this produces exactly:

    * train 1+2, validate 3;
    * train 1+3, validate 2;
    * train 2+3, validate 1.

    The function generalizes to more than three clean benign runs by leaving
    each one out once as validation and training on the remaining clean benign
    runs.
    """
    clean = features[features["phase"] == "phase1_clean_benign"]
    run_ids = sorted(clean["run_id"].unique())
    if len(run_ids) < 2:
        raise ValueError("Need at least two phase1_clean_benign runs for leave-one-validation splitting.")

    splits: list[CleanBenignSplit] = []
    for i, val in enumerate(run_ids, start=1):
        train = [r for r in run_ids if r != val]
        split_id = f"cv{i:02d}_val_{_short_run_id(val)}"
        splits.append(CleanBenignSplit(split_id=split_id, train_run_ids=train, validation_run_id=val))
    return splits


def _short_run_id(run_id: str) -> str:
    if "__" in run_id:
        return run_id.split("__")[-1]
    return run_id[-24:]


def _split_role(run_id: str, split: CleanBenignSplit) -> str:
    if run_id in split.train_run_ids:
        return "train"
    if run_id == split.validation_run_id:
        return "validation"
    return "test"


def _fit_one_view_one_split(
    features: pd.DataFrame,
    runs: list[RunData],
    config: dict[str, Any],
    output_dir: Path,
    feature_view: str,
    split: CleanBenignSplit,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Train/evaluate all detectors for one feature view and one CV split."""
    prefixes = select_feature_prefixes(feature_view)
    feature_cols = infer_feature_columns(features, prefixes=prefixes)
    if not feature_cols:
        print(f"[WARN] No feature columns for view={feature_view}; skipping")
        return pd.DataFrame(), pd.DataFrame()

    view_dir = output_dir / "models" / feature_view / split.split_id
    view_dir.mkdir(parents=True, exist_ok=True)
    (view_dir / "feature_columns.json").write_text(json.dumps(feature_cols, indent=2), encoding="utf-8")

    X_train = features[features["run_id"].isin(split.train_run_ids)][feature_cols].to_numpy(float)
    X_val = features[features["run_id"] == split.validation_run_id][feature_cols].to_numpy(float)

    scaler = StandardScaler().fit(X_train)
    joblib.dump(scaler, view_dir / "scaler.joblib")
    X_train_s = scaler.transform(X_train)
    X_val_s = scaler.transform(X_val)

    random_state = int(config.get("models", {}).get("random_state", 42))
    detectors = default_detectors(random_state=random_state, include_autoencoder=bool(config.get("models", {}).get("include_autoencoder", True)))
    quantile = float(config.get("calibration", {}).get("target_fpr_quantile", 0.995))
    window_seconds = float(config.get("features", {}).get("sysdig_window_seconds", 4.0))

    metric_rows, score_tables = [], []
    for detector in detectors:
        print(f"[INFO] split={split.split_id} view={feature_view} training detector={detector.name}")
        detector.fit(X_train_s)
        val_scores = detector.score(X_val_s)
        cal = QuantileCalibrator(quantile=quantile).fit(val_scores)
        threshold = cal.threshold_
        joblib.dump(detector, view_dir / f"detector_{detector.name}.joblib")

        for run_id, g in features.groupby("run_id", sort=False):
            X = scaler.transform(g[feature_cols].to_numpy(float))
            scores = detector.score(X)
            preds = cal.predict(scores)
            y = g["label"].to_numpy(int)
            times = g["relative_time_s"].to_numpy(float)
            intervals = intervals_from_binary_labels(y, times, window_seconds)
            m = evaluate_run(y, scores, preds, times, intervals, window_seconds)
            first = g.iloc[0]
            role = _split_role(run_id, split)
            row = {
                "split_id": split.split_id,
                "train_run_ids": ";".join(split.train_run_ids),
                "validation_run_id": split.validation_run_id,
                "split_role": role,
                "feature_view": feature_view,
                "detector": detector.name,
                "run_id": run_id,
                "run_dir": first.get("run_dir", ""),
                "phase": first.get("phase", ""),
                "perturbation": first.get("perturbation", ""),
                "perturbation_family": first.get("perturbation_family", ""),
                "perturbation_profile": first.get("perturbation_profile", ""),
                "severity": first.get("severity", np.nan),
                "attack_duration": first.get("attack_duration", np.nan),
                "attack_intensity": first.get("attack_intensity", ""),
                "threshold": threshold,
            }
            row.update(m)
            metric_rows.append(row)

            score_df = g[[
                "run_id", "relative_time_s", "label", "phase", "perturbation_family", "severity",
                "attack_duration", "attack_intensity",
            ]].copy()
            score_df["split_id"] = split.split_id
            score_df["split_role"] = role
            score_df["feature_view"] = feature_view
            score_df["detector"] = detector.name
            score_df["score"] = scores
            score_df["prediction"] = preds
            score_df["threshold"] = threshold
            score_tables.append(score_df)

    metrics = pd.DataFrame(metric_rows)
    scores = pd.concat(score_tables, ignore_index=True) if score_tables else pd.DataFrame()
    return metrics, scores


def fit_evaluate(
    features: pd.DataFrame,
    runs: list[RunData],
    config: dict[str, Any],
    output_dir: str | Path,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Train/evaluate all configured feature views and CV splits.

    Canonical output files written by this function:

    - metrics_by_run_all_splits.csv
    - scores_by_window_all_splits.csv
    - metrics_by_run.csv
    - scores_by_window.csv
    - metrics_aggregated.csv
    - metrics_window_pooled_by_duration.csv
    - metrics_window_pooled_all_attacks.csv
    - robustness_summary.csv

    No legacy v2/v3 aliases are written.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "models").mkdir(exist_ok=True)

    splits = make_clean_benign_cv_splits(features)
    views = configured_feature_views(config)

    print(f"[INFO] using feature views: {views}")
    print(f"[INFO] using {len(splits)} leave-one-clean-benign-out splits")

    all_metrics: list[pd.DataFrame] = []
    all_scores: list[pd.DataFrame] = []

    for split in splits:
        for view in views:
            metrics, scores = _fit_one_view_one_split(
                features=features,
                runs=runs,
                config=config,
                output_dir=output_dir,
                feature_view=view,
                split=split,
            )

            if not metrics.empty:
                all_metrics.append(metrics)

            if not scores.empty:
                all_scores.append(scores)

    metrics_all = pd.concat(all_metrics, ignore_index=True) if all_metrics else pd.DataFrame()
    scores_all = pd.concat(all_scores, ignore_index=True) if all_scores else pd.DataFrame()

    # Main evaluation excludes training runs.
    #
    # Validation clean-benign runs are retained because they provide the
    # lambda=0 benign reference for each split. All phase2--phase4 runs are
    # test runs by construction.
    if not metrics_all.empty:
        metrics = metrics_all[metrics_all["split_role"] != "train"].copy()
    else:
        metrics = pd.DataFrame()

    if not scores_all.empty:
        scores = scores_all[scores_all["split_role"] != "train"].copy()
    else:
        scores = pd.DataFrame()

    # Aggregated metrics.
    agg = aggregate_metrics(metrics) if not metrics.empty else pd.DataFrame()

    window_by_duration = aggregate_window_metrics(
        scores,
        float(config.get("features", {}).get("sysdig_window_seconds", 4.0)),
        group_by_attack_duration=True,
    ) if not scores.empty else pd.DataFrame()

    window_pooled = aggregate_window_metrics(
        scores,
        float(config.get("features", {}).get("sysdig_window_seconds", 4.0)),
        group_by_attack_duration=False,
    ) if not scores.empty else pd.DataFrame()

    # Robustness summaries.
    summary_frames: list[pd.DataFrame] = []

    for metric, higher_is_better in [
        ("event_recall_mean", True),
        ("attack_window_recall_percent_mean", True),
        ("auroc_mean", True),
        ("auprc_mean", True),
        ("false_alarm_rate_percent_mean", False),
        ("median_ttd_s_mean", False),
    ]:
        if metric in agg.columns:
            tmp = robustness_summary(
                agg,
                metric=metric,
                higher_is_better=higher_is_better,
            )
            if not tmp.empty:
                summary_frames.append(tmp)

    summary = pd.concat(summary_frames, ignore_index=True) if summary_frames else pd.DataFrame()

    # ------------------------------------------------------------------
    # Canonical output files
    # ------------------------------------------------------------------

    _write_csv(metrics_all, output_dir / "metrics_by_run_all_splits.csv")
    _write_csv(scores_all, output_dir / "scores_by_window_all_splits.csv")

    _write_csv(metrics, output_dir / "metrics_by_run.csv")
    _write_csv(scores, output_dir / "scores_by_window.csv")

    _write_csv(agg, output_dir / "metrics_aggregated.csv")

    _write_csv(
        window_by_duration,
        output_dir / "metrics_window_pooled_by_duration.csv",
    )

    _write_csv(
        window_pooled,
        output_dir / "metrics_window_pooled_all_attacks.csv",
    )

    _write_csv(
        summary,
        output_dir / "robustness_summary.csv",
        columns=[
            "feature_view",
            "detector",
            "perturbation_family",
            "attack_duration",
            "attack_intensity",
            "metric",
            "higher_is_better",
            "R_avg",
            "R_worst",
            "R_prod",
            "n_severity_points",
        ],
    )

    return metrics, scores, agg, window_by_duration, window_pooled


def _print_metric_overview(metrics: pd.DataFrame, window_pooled: pd.DataFrame) -> None:
    """Print a compact text overview of all important metrics."""
    if metrics.empty:
        return
    print("\n[METRICS] Per-run metric columns:")
    print(", ".join([c for c in metrics.columns if c not in {"run_id", "run_dir", "train_run_ids"}]))
    print("\n[METRICS] Mean per-run metrics by phase:")
    cols = [c for c in ["event_recall", "false_alarm_rate_percent", "false_alarms_per_hour", "attack_window_recall_percent", "median_ttd_s", "auroc", "auprc"] if c in metrics]
    if cols:
        print(metrics.groupby(["feature_view", "detector", "phase"], dropna=False)[cols].mean(numeric_only=True).round(3).to_string())
    if not window_pooled.empty:
        print("\n[METRICS] Pooled window metrics by phase:")
        cols2 = [c for c in ["false_alarm_rate_percent", "attack_window_recall_percent", "predicted_alarm_rate_percent", "auroc", "auprc", "precision", "recall", "f1", "mcc"] if c in window_pooled]
        print(window_pooled.groupby(["feature_view", "detector", "phase"], dropna=False)[cols2].mean(numeric_only=True).round(3).to_string())


def make_figures(agg: pd.DataFrame, window_pooled: pd.DataFrame, scores: pd.DataFrame, output_dir: str | Path, config: dict[str, Any]) -> None:
    """Create paper-oriented figures for all detectors and feature views."""
    fig_dir = Path(output_dir) / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)

    # Add lambda=0 baselines for heatmap visualization.
    benign_heat = add_lambda0_baseline(window_pooled, benign=True)
    attack_heat = add_lambda0_baseline(window_pooled, benign=False)

    detectors = sorted(scores["detector"].dropna().unique()) if not scores.empty else []
    feature_views = sorted(scores["feature_view"].dropna().unique()) if not scores.empty else []

    # Pooled heatmaps: all benign windows for FA%, all attack windows for recall/AUPRC.
    for fv in feature_views:
        for det in detectors:
            # False-alarm heatmap on benign data (phase 1 + phase 3).
            benign = benign_heat[benign_heat["phase"].isin(["phase1_clean_benign", "phase3_perturbed_benign"])]
            if not benign.empty and "false_alarm_rate_percent" in benign.columns:
                plot_metric_heatmap(
                    benign,
                    metric_col="false_alarm_rate_percent",
                    detector=det,
                    feature_view=fv,
                    output_path=fig_dir / "heatmaps_false_alarm_rate_percent" / f"{fv}_{det}.png",
                    title=f"False-alarm rate [%] — {det}, {fv}",
                    value_format=".2f",
                    cmap="magma",
                )

            # Attack-window recall heatmap pooled over attack durations.
            attacked = attack_heat[attack_heat["phase"].isin(["phase2_clean_attacked", "phase4_perturbed_attacked"])]
            if not attacked.empty and "attack_window_recall_percent" in attacked.columns:
                plot_metric_heatmap(
                    attacked,
                    metric_col="attack_window_recall_percent",
                    detector=det,
                    feature_view=fv,
                    output_path=fig_dir / "heatmaps_attack_window_recall_percent" / f"{fv}_{det}.png",
                    title=f"Attack-window recall [%] — {det}, {fv}",
                    value_format=".1f",
                    cmap="viridis",
                )

            if not attacked.empty and "auprc" in attacked.columns:
                plot_metric_heatmap(
                    attacked,
                    metric_col="auprc",
                    detector=det,
                    feature_view=fv,
                    output_path=fig_dir / "heatmaps_auprc" / f"{fv}_{det}.png",
                    title=f"AUPRC — {det}, {fv}",
                    value_format=".2f",
                    cmap="viridis",
                )

    # Boxplots for key per-run metrics.
    for phase, metric_cols in {
        "phase3_perturbed_benign": ["false_alarm_rate_percent", "false_alarms_per_hour"],
        "phase4_perturbed_attacked": ["event_recall", "median_ttd_s", "auprc", "auroc"],
    }.items():
        sub = agg[agg["phase"] == phase]
        # Use raw per-run metrics for distributions, not agg.
    # Raw per-run distribution plots.
    # These are created below in run_end_to_end after metrics are available.

    # Timeline plots for all test runs and all models/views, as requested.
    fig_cfg = config.get("figures", {})
    make_all = bool(fig_cfg.get("make_all_timelines", True))
    max_runs = fig_cfg.get("max_timeline_runs", None)
    if make_all and not scores.empty:
        test_scores = scores[scores["split_role"].isin(["test", "validation"])].copy()
        run_ids = sorted(test_scores["run_id"].unique())
        if max_runs is not None:
            run_ids = run_ids[: int(max_runs)]
        for fv in feature_views:
            for run_id in run_ids:
                try:
                    plot_all_models_timeline(
                        test_scores,
                        run_id=run_id,
                        feature_view=fv,
                        output_path=fig_dir / "timelines_all_models" / fv / f"{run_id}.png",
                    )
                except Exception as exc:
                    print(f"[WARN] timeline plot failed for {run_id}, {fv}: {exc}")


def make_metric_distribution_figures(metrics: pd.DataFrame, output_dir: str | Path) -> None:
    """Create per-run metric distribution plots for all models/views."""
    fig_dir = Path(output_dir) / "figures" / "metric_distributions"
    fig_dir.mkdir(parents=True, exist_ok=True)
    for phase, metric_list in {
        "phase3_perturbed_benign": ["false_alarm_rate_percent", "false_alarms_per_hour", "predicted_alarm_rate_percent"],
        "phase4_perturbed_attacked": ["event_recall", "attack_window_recall_percent", "median_ttd_s", "auroc", "auprc"],
        "phase2_clean_attacked": ["event_recall", "attack_window_recall_percent", "median_ttd_s", "auroc", "auprc"],
    }.items():
        sub = metrics[metrics["phase"] == phase]
        for metric in metric_list:
            if metric in sub.columns and not sub[metric].dropna().empty:
                plot_metric_distribution(
                    sub,
                    metric_col=metric,
                    output_path=fig_dir / phase / f"{metric}.png",
                    title=f"{metric.replace('_', ' ')} — {phase}",
                )


def run_end_to_end(
    data_root: str | Path,
    output_dir: str | Path,
    config_path: str | Path = "configs/default.yaml",
) -> None:
    """Run the complete analysis workflow.

    This function writes one canonical set of output files:

    - manifest.csv
    - features.csv
    - metrics_by_run.csv
    - metrics_by_run_all_splits.csv
    - scores_by_window.csv
    - scores_by_window_all_splits.csv
    - metrics_aggregated.csv
    - metrics_window_pooled_all_attacks.csv
    - metrics_window_pooled_by_duration.csv
    - robustness_summary.csv
    """
    config = load_config(config_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("[INFO] building features")
    features, manifest, runs = build_features(data_root, config)

    _write_csv(manifest, output_dir / "manifest.csv")
    _write_csv(features, output_dir / "features.csv")

    print("[INFO] fitting models and evaluating")
    metrics, scores, agg, window_by_duration, window_pooled = fit_evaluate(
        features=features,
        runs=runs,
        config=config,
        output_dir=output_dir,
    )

    _print_metric_overview(metrics, window_pooled)

    print("[INFO] creating figures")
    make_figures(
        agg=agg,
        window_pooled=window_pooled,
        scores=scores,
        output_dir=output_dir,
        config=config,
    )

    make_metric_distribution_figures(
        metrics=metrics,
        output_dir=output_dir,
    )

    print(f"[INFO] wrote outputs to {output_dir}")
