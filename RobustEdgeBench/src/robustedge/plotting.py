"""Plotting utilities for paper-grade robustness figures.

The functions avoid the overloaded line plot used in the first prototype.  The
current dataset has only a small number of non-zero severities, so heatmaps and
bar/point plots are more interpretable than dense curves.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


DEFAULT_FAMILIES = ["P1", "P2", "P3", "P4", "P5"]
DEFAULT_SEVERITIES = [0.0, 0.5, 1.0]


def _safe_filename(text: str) -> str:
    return str(text).replace("/", "_").replace(" ", "_").replace(":", "_").replace(".", "p")


def set_paper_style() -> None:
    """Use a clean matplotlib style suitable for IEEE-style figures."""
    plt.rcParams.update({
        "font.size": 9,
        "axes.titlesize": 10,
        "axes.labelsize": 9,
        "legend.fontsize": 8,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "figure.dpi": 120,
        "savefig.dpi": 300,
        "axes.grid": False,
    })


def plot_metric_heatmap(
    df: pd.DataFrame,
    metric_col: str,
    detector: str,
    feature_view: str,
    output_path: str | Path | None = None,
    title: str | None = None,
    families: list[str] | None = None,
    severities: list[float] | None = None,
    value_format: str = ".2f",
    cmap: str = "viridis",
) -> plt.Figure:
    """Plot perturbation-family by severity heatmap for one detector/view."""
    set_paper_style()
    families = families or DEFAULT_FAMILIES
    severities = severities or DEFAULT_SEVERITIES
    sub = df[(df["detector"] == detector) & (df["feature_view"] == feature_view)].copy()
    pivot = sub.pivot_table(index="perturbation_family", columns="severity", values=metric_col, aggfunc="mean")
    pivot = pivot.reindex(index=families, columns=severities)

    fig, ax = plt.subplots(figsize=(4.6, 3.0))
    im = ax.imshow(pivot.values.astype(float), aspect="auto", cmap=cmap)
    ax.set_xticks(np.arange(len(pivot.columns)))
    ax.set_xticklabels([f"{float(c):.1f}" for c in pivot.columns])
    ax.set_yticks(np.arange(len(pivot.index)))
    ax.set_yticklabels(pivot.index)
    ax.set_xlabel("Severity $\\lambda$")
    ax.set_ylabel("Perturbation family")
    ax.set_title(title or f"{metric_col}: {detector}, {feature_view}")

    for i in range(pivot.shape[0]):
        for j in range(pivot.shape[1]):
            val = pivot.values[i, j]
            if pd.notna(val):
                ax.text(j, i, f"{val:{value_format}}", ha="center", va="center", fontsize=8)

    cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.ax.set_ylabel(metric_col.replace("_", " "), rotation=90)
    fig.tight_layout()
    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path, bbox_inches="tight")
    return fig


def plot_detector_metric_bars(
    df: pd.DataFrame,
    metric_col: str,
    output_path: str | Path | None = None,
    title: str | None = None,
) -> plt.Figure:
    """Grouped bar plot over detectors for a single metric.

    This is useful for summary figures where severity/family have already been
    filtered or aggregated.
    """
    set_paper_style()
    d = df.copy()
    labels = d["detector"].astype(str) + "\n" + d["feature_view"].astype(str)
    fig, ax = plt.subplots(figsize=(max(5.0, 0.45 * len(labels)), 3.2))
    ax.bar(np.arange(len(labels)), d[metric_col].astype(float).values)
    ax.set_xticks(np.arange(len(labels)))
    ax.set_xticklabels(labels, rotation=45, ha="right")
    ax.set_ylabel(metric_col.replace("_", " "))
    ax.set_title(title or metric_col)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path, bbox_inches="tight")
    return fig


def plot_all_models_timeline(
    scores: pd.DataFrame,
    run_id: str,
    feature_view: str,
    output_path: str | Path | None = None,
    normalize_scores: bool = True,
) -> plt.Figure:
    """Plot all detector score timelines for one run and feature view.

    Scores from different detectors are not on the same scale.  By default they
    are min-max normalized per detector to make a compact visual comparison.
    Thresholds are not shown in the normalized plot because each detector has a
    different threshold scale; predictions are indicated by small markers.
    """
    set_paper_style()
    df = scores[(scores["run_id"] == run_id) & (scores["feature_view"] == feature_view)].copy()
    if df.empty:
        raise ValueError(f"No scores for run_id={run_id!r}, feature_view={feature_view!r}")

    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    for det, g in df.groupby("detector"):
        g = g.sort_values("relative_time_s")
        y = g["score"].astype(float).to_numpy()
        if normalize_scores:
            ymin, ymax = np.nanmin(y), np.nanmax(y)
            y = (y - ymin) / (ymax - ymin) if ymax > ymin else np.zeros_like(y)
        ax.plot(g["relative_time_s"], y, label=str(det), linewidth=1.2)
        alarms = g[g["prediction"] == 1]
        if not alarms.empty:
            yy = np.interp(alarms["relative_time_s"], g["relative_time_s"], y)
            ax.scatter(alarms["relative_time_s"], yy, s=8)

    # Attack interval shading from labels.
    any_view = df.sort_values("relative_time_s").drop_duplicates("relative_time_s")
    if "label" in any_view and any_view["label"].max() > 0:
        atk = any_view[any_view["label"] == 1]
        ax.axvspan(atk["relative_time_s"].min(), atk["relative_time_s"].max(), alpha=0.18, label="attack window")

    ax.set_xlabel("Time since run start [s]")
    ax.set_ylabel("Normalized anomaly score" if normalize_scores else "Anomaly score")
    ax.set_title(f"Model score timelines ({feature_view})\n{run_id}")
    ax.grid(True, alpha=0.25)
    ax.legend(fontsize=7, ncol=2)
    fig.tight_layout()
    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path, bbox_inches="tight")
    return fig


def plot_metric_distribution(
    metrics: pd.DataFrame,
    metric_col: str,
    output_path: str | Path | None = None,
    title: str | None = None,
) -> plt.Figure:
    """Boxplot distribution of per-run metrics by detector and feature view."""
    set_paper_style()
    labels, data = [], []
    for (fv, det), g in metrics.groupby(["feature_view", "detector"], dropna=False):
        vals = g[metric_col].dropna().astype(float).values
        if len(vals):
            labels.append(f"{det}\n{fv}")
            data.append(vals)
    fig, ax = plt.subplots(figsize=(max(5.5, 0.45 * len(labels)), 3.4))
    ax.boxplot(data, labels=labels, showmeans=True)
    ax.set_ylabel(metric_col.replace("_", " "))
    ax.set_title(title or metric_col)
    ax.tick_params(axis="x", rotation=45)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path, bbox_inches="tight")
    return fig
