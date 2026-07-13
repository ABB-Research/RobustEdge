"""Create camera-ready result figures for the ETFA RobustEdgeBench paper.

Input files expected in --input-dir:
    metrics_by_run.csv
    metrics_window_pooled_all_attacks.csv
    metrics_window_pooled_by_duration.csv
    robustness_summary.csv

The figures focus on the current discrete severity design: clean baseline
(lambda=0) plus lambda=0.5 and lambda=1.0 for P1--P5.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

DET_ORDER = ["pca", "gmm", "ocsvm", "isolation_forest", "autoencoder"]
DET_LABELS = {
    "pca": "PCA",
    "gmm": "GMM",
    "ocsvm": "OCSVM",
    "isolation_forest": "IF",
    "autoencoder": "AE",
}
FAMILIES = ["P1", "P2", "P3", "P4", "P5"]
SEVERITIES = [0.5, 1.0]
COND_LABELS = ["clean"] + [f"{p}\nλ={s:g}" for p in FAMILIES for s in SEVERITIES]


def _heat_matrix(win_all: pd.DataFrame, view: str, metric_col: str, benign: bool) -> np.ndarray:
    rows = []
    for det in DET_ORDER:
        vals = []
        clean_phase = "phase1_clean_benign" if benign else "phase2_clean_attacked"
        clean = win_all[(win_all.phase == clean_phase) & (win_all.feature_view == view) & (win_all.detector == det)]
        vals.append(float(clean[metric_col].iloc[0]) if len(clean) else np.nan)
        for fam in FAMILIES:
            for sev in SEVERITIES:
                phase = "phase3_perturbed_benign" if benign else "phase4_perturbed_attacked"
                sub = win_all[
                    (win_all.phase == phase)
                    & (win_all.feature_view == view)
                    & (win_all.detector == det)
                    & (win_all.perturbation_family == fam)
                    & (win_all.severity == sev)
                ]
                vals.append(float(sub[metric_col].iloc[0]) if len(sub) else np.nan)
        rows.append(vals)
    return np.asarray(rows)


def _text_color(value: float, cmap: mpl.colors.Colormap, norm: mpl.colors.Normalize) -> str:
    if np.isnan(value):
        return "black"
    rgba = cmap(norm(value))
    luminance = 0.299 * rgba[0] + 0.587 * rgba[1] + 0.114 * rgba[2]
    return "black" if luminance > 0.55 else "white"


def _annotated_heatmap(ax, data, title, vmin, vmax, cmap_name, fmt):
    cmap = plt.get_cmap(cmap_name)
    norm = mpl.colors.Normalize(vmin=vmin, vmax=vmax)
    im = ax.imshow(data, aspect="auto", cmap=cmap, norm=norm)
    ax.set_title(title, fontsize=9, pad=5)
    ax.set_xticks(np.arange(len(COND_LABELS)))
    ax.set_xticklabels(COND_LABELS, fontsize=6.8, rotation=45, ha="right")
    ax.set_yticks(np.arange(len(DET_ORDER)))
    ax.set_yticklabels([DET_LABELS[d] for d in DET_ORDER], fontsize=8)
    ax.set_xticks(np.arange(-0.5, data.shape[1], 1), minor=True)
    ax.set_yticks(np.arange(-0.5, data.shape[0], 1), minor=True)
    ax.grid(which="minor", color="white", linestyle="-", linewidth=0.6)
    ax.tick_params(which="minor", bottom=False, left=False)
    for i in range(data.shape[0]):
        for j in range(data.shape[1]):
            value = data[i, j]
            text = "–" if np.isnan(value) else fmt.format(value)
            ax.text(j, i, text, ha="center", va="center", fontsize=6.2, color=_text_color(value, cmap, norm))
    return im


def create_false_alarm_heatmaps(win_all: pd.DataFrame, output_dir: Path) -> None:
    runtime = _heat_matrix(win_all, "runtime", "false_alarm_rate_percent", benign=True)
    fused = _heat_matrix(win_all, "fused", "false_alarm_rate_percent", benign=True)
    vmax = max(np.nanmax(runtime), np.nanmax(fused))
    fig, axs = plt.subplots(1, 2, figsize=(7.15, 3.35), constrained_layout=True)
    im = _annotated_heatmap(axs[0], runtime, "(a) Runtime-only", 0, vmax, "magma", "{:.1f}")
    _annotated_heatmap(axs[1], fused, "(b) Fused runtime+process+controller", 0, vmax, "magma", "{:.1f}")
    for ax in axs:
        ax.set_xlabel("Perturbation condition", fontsize=7.8)
    axs[0].set_ylabel("Detector", fontsize=8)
    fig.suptitle("False-alarm robustness on benign perturbation runs", fontsize=10.2)
    cbar = fig.colorbar(im, ax=axs, shrink=0.82, pad=0.015)
    cbar.set_label("False-alarm rate [%]", fontsize=7.8)
    cbar.ax.tick_params(labelsize=7)
    _save(fig, output_dir, "fig_false_alarm_heatmaps")


def create_auprc_heatmaps(win_all: pd.DataFrame, output_dir: Path) -> None:
    runtime = _heat_matrix(win_all, "runtime", "auprc", benign=False)
    fused = _heat_matrix(win_all, "fused", "auprc", benign=False)
    fig, axs = plt.subplots(1, 2, figsize=(7.15, 3.35), constrained_layout=True)
    im = _annotated_heatmap(axs[0], runtime, "(a) Runtime-only", 0, 1, "viridis", "{:.2f}")
    _annotated_heatmap(axs[1], fused, "(b) Fused runtime+process+controller", 0, 1, "viridis", "{:.2f}")
    for ax in axs:
        ax.set_xlabel("Perturbation condition", fontsize=7.8)
    axs[0].set_ylabel("Detector", fontsize=8)
    fig.suptitle("Attack-window ranking robustness on attacked runs (pooled durations)", fontsize=10.2)
    cbar = fig.colorbar(im, ax=axs, shrink=0.82, pad=0.015)
    cbar.set_label("AUPRC", fontsize=7.8)
    cbar.ax.tick_params(labelsize=7)
    _save(fig, output_dir, "fig_attack_auprc_heatmaps")

from matplotlib.lines import Line2D

def _pareto_mask(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """
    Pareto-optimal mask for:
      - x: lower is better
      - y: higher is better
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)

    mask = np.ones(len(x), dtype=bool)
    for i in range(len(x)):
        dominated = (x <= x[i]) & (y >= y[i]) & ((x < x[i]) | (y > y[i]))
        dominated[i] = False
        if dominated.any():
            mask[i] = False
    return mask


def _cluster_by_distance(points_xy: np.ndarray, threshold_px: float = 72.0) -> list[list[int]]:
    """
    Cluster points that are visually close in display coordinates.
    This is used so that nearby points with the same detector name
    get only one shared label.
    """
    n = len(points_xy)
    if n == 0:
        return []

    parent = list(range(n))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(n):
        for j in range(i + 1, n):
            if np.hypot(*(points_xy[i] - points_xy[j])) <= threshold_px:
                union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    return list(groups.values())


def _measure_label_bbox(ax, text: str, fontsize: float, renderer) -> tuple[float, float]:
    """
    Measure label box size in display coordinates (pixels).
    """
    tmp = ax.text(
        0,
        0,
        text,
        fontsize=fontsize,
        ha="center",
        va="center",
        bbox=dict(
            boxstyle="round,pad=0.18",
            facecolor="white",
            edgecolor="0.70",
            linewidth=0.45,
            alpha=0.95,
        ),
        visible=False,
    )
    bb = tmp.get_window_extent(renderer=renderer).expanded(1.08, 1.20)
    tmp.remove()
    return bb.width, bb.height


def create_tradeoff_plot(
    win_all: pd.DataFrame,
    output_dir: Path,
    show_title: bool = False,
) -> None:
    # ------------------------------------------------------------------
    # Aggregate
    # ------------------------------------------------------------------
    fa_avg = (
        win_all[win_all.phase == "phase3_perturbed_benign"]
        .groupby(["feature_view", "detector"])["false_alarm_rate_percent"]
        .mean()
        .rename("false_alarm_rate_percent")
    )

    rec_avg = (
        win_all[win_all.phase == "phase4_perturbed_attacked"]
        .groupby(["feature_view", "detector"])["attack_window_recall_percent"]
        .mean()
        .rename("attack_window_recall_percent")
    )

    auprc_avg = (
        win_all[win_all.phase == "phase4_perturbed_attacked"]
        .groupby(["feature_view", "detector"])["auprc"]
        .mean()
        .rename("auprc")
    )

    trade = pd.concat([fa_avg, rec_avg, auprc_avg], axis=1).dropna().reset_index()
    trade["det_label"] = trade["detector"].map(lambda d: DET_LABELS.get(d, d))
    trade["pareto_optimal"] = _pareto_mask(
        trade["false_alarm_rate_percent"].to_numpy(),
        trade["auprc"].to_numpy(),
    )
    trade.to_csv(output_dir / "operational_tradeoff_summary.csv", index=False)

    # ------------------------------------------------------------------
    # Style
    # ------------------------------------------------------------------
    markers = {
        "runtime": "o",
        "runtime_controller": "s",
        "runtime_process": "^",
        "fused": "D",
    }
    colors = {
        "runtime": "tab:blue",
        "runtime_controller": "tab:orange",
        "runtime_process": "tab:green",
        "fused": "tab:red",
    }
    view_labels = {
        "runtime": "runtime",
        "runtime_controller": "runtime+controller",
        "runtime_process": "runtime+process",
        "fused": "fused",
    }

    fig, ax = plt.subplots(figsize=(5.8, 3.9), constrained_layout=True)

    # Give a bit of extra space on the right/top for labels
    xmax = float(trade["false_alarm_rate_percent"].max())
    ax.set_xlim(0.0, xmax * 1.10 + 0.20)
    ax.set_ylim(0.0, 1.03)

    ax.grid(True, alpha=0.28, zorder=0)
    if show_title:
        ax.set_title("Operational robustness trade-off", fontsize=9.4)

    # ------------------------------------------------------------------
    # Pareto front
    # ------------------------------------------------------------------
    pareto = trade.loc[trade["pareto_optimal"]].sort_values("false_alarm_rate_percent")

    # Global Pareto front line
    ax.plot(
        pareto["false_alarm_rate_percent"],
        pareto["auprc"],
        linestyle="--",
        linewidth=1.05,
        color="0.20",
        alpha=0.85,
        zorder=1,
    )

    # Highlight Pareto-optimal points with a black outline ring
    ax.scatter(
        pareto["false_alarm_rate_percent"],
        pareto["auprc"],
        s=92,
        facecolors="none",
        edgecolors="0.20",
        linewidths=0.85,
        zorder=3.2,
    )

    # Draw order only affects stacking in the plot, not the legend order.
    plot_order = ["runtime_controller", "fused", "runtime_process", "runtime"]
    draw_zorder = {
        "runtime_controller": 3.0,
        "fused": 3.1,
        "runtime_process": 3.2,
        "runtime": 3.3,
    }

    for feature_view in plot_order:
        marker = markers[feature_view]
        sub = trade[trade.feature_view == feature_view]
        if sub.empty:
            continue

        ax.scatter(
            sub["false_alarm_rate_percent"],
            sub["auprc"],
            marker=marker,
            s=78,
            color=colors.get(feature_view, None),
            alpha=0.78,
            edgecolors="white",
            linewidths=0.75,
            label=view_labels.get(feature_view, feature_view.replace("_", "+")),
            zorder=draw_zorder[feature_view],
        )

    # # ------------------------------------------------------------------
    # # Scatter points
    # #   - semi-transparent to show overlap
    # #   - visible edges so stacked points remain distinguishable
    # # ------------------------------------------------------------------
    # for feature_view, marker in markers.items():
    #     sub = trade[trade.feature_view == feature_view]
    #     if sub.empty:
    #         continue

    #     ax.scatter(
    #         sub["false_alarm_rate_percent"],
    #         sub["auprc"],
    #         marker=marker,
    #         s=78,
    #         color=colors.get(feature_view, None),
    #         alpha=0.78,
    #         edgecolors="white",
    #         linewidths=0.75,
    #         label=view_labels.get(feature_view, feature_view.replace("_", "+")),
    #         zorder=3,
    #     )

    ax.set_xlabel("Mean false-alarm rate on perturbed benign runs [%]", fontsize=8.4)
    ax.set_ylabel("Mean AUPRC on perturbed attacked runs", fontsize=8.4)

    # Legend
    legend_handles = [
        Line2D(
            [0],
            [0],
            marker=markers[k],
            linestyle="None",
            markersize=7.5,
            markerfacecolor=colors[k],
            markeredgecolor="white",
            markeredgewidth=0.75,
            alpha=0.85,
            label=view_labels[k],
        )
        for k in markers
    ]
    legend_handles.append(
        Line2D([0], [0], color="0.20", linestyle="--", linewidth=1.05, label="Pareto front")
    )
    ax.legend(
        handles=legend_handles,
        fontsize=6.5,
        title="Feature view",
        title_fontsize=7.0,
        loc="lower right",
        framealpha=0.92,
    )

    # ------------------------------------------------------------------
    # Smart label placement
    # ------------------------------------------------------------------
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()

    # Point positions in display coordinates (pixels)
    point_disp = ax.transData.transform(
        trade[["false_alarm_rate_percent", "auprc"]].to_numpy()
    )
    axes_bbox = ax.get_window_extent(renderer=renderer)

    # Build detector-specific local clusters so that nearby points with the
    # same detector label share one annotation.
    clusters = []
    for det_label, sub in trade.groupby("det_label", sort=False):
        idx = sub.index.to_list()
        disp = point_disp[idx]
        local_clusters = _cluster_by_distance(disp, threshold_px=72.0)

        for c in local_clusters:
            global_idx = [idx[i] for i in c]
            center = point_disp[global_idx].mean(axis=0)
            clusters.append(
                {
                    "label": det_label,
                    "indices": global_idx,
                    "center": center,
                }
            )

    # Place top labels first
    clusters.sort(key=lambda item: (-item["center"][1], item["center"][0]))

    # Candidate offsets around the cluster center (in display pixels)
    candidate_offsets = np.array(
        [
            (0, 22),
            (22, 0),
            (-22, 0),
            (0, -22),
            (24, 18),
            (24, -18),
            (-24, 18),
            (-24, -18),
            (36, 0),
            (-36, 0),
            (0, 34),
            (0, -34),
            (40, 20),
            (40, -20),
            (-40, 20),
            (-40, -20),
            (54, 0),
            (-54, 0),
            (0, 46),
            (0, -46),
            (60, 24),
            (60, -24),
            (-60, 24),
            (-60, -24),
        ],
        dtype=float,
    )

    placed_boxes: list[np.ndarray] = []
    label_specs = []
    fontsize = 6.2

    for item in clusters:
        w, h = _measure_label_bbox(ax, item["label"], fontsize=fontsize, renderer=renderer)
        cx, cy = item["center"]

        best = None
        for dx, dy in candidate_offsets:
            lx, ly = cx + dx, cy + dy
            box = np.array([lx - w / 2, ly - h / 2, lx + w / 2, ly + h / 2])

            # Must stay inside the axes
            margin = 4.0
            if (
                box[0] < axes_bbox.x0 + margin
                or box[2] > axes_bbox.x1 - margin
                or box[1] < axes_bbox.y0 + margin
                or box[3] > axes_bbox.y1 - margin
            ):
                continue

            # Scoring: prefer short leaders, no overlap with points, no overlap with labels
            score = 0.02 * np.mean(
                np.hypot(
                    point_disp[item["indices"], 0] - lx,
                    point_disp[item["indices"], 1] - ly,
                )
            )

            # Penalize label boxes covering symbols
            for px, py in point_disp:
                if (box[0] - 8 <= px <= box[2] + 8) and (box[1] - 8 <= py <= box[3] + 8):
                    score += 125.0

            # Penalize overlap with already placed label boxes
            for ob in placed_boxes:
                inter_w = max(0.0, min(box[2], ob[2]) - max(box[0], ob[0]))
                inter_h = max(0.0, min(box[3], ob[3]) - max(box[1], ob[1]))
                if inter_w > 0 and inter_h > 0:
                    score += 2500.0 + 5.0 * inter_w * inter_h
                else:
                    gap_x = max(ob[0] - box[2], box[0] - ob[2], 0.0)
                    gap_y = max(ob[1] - box[3], box[1] - ob[3], 0.0)
                    if gap_x < 6.0 and gap_y < 6.0:
                        score += 80.0

            # Slight penalty for long total leader length
            score += 0.003 * np.sum(
                np.hypot(
                    point_disp[item["indices"], 0] - lx,
                    point_disp[item["indices"], 1] - ly,
                )
            )

            if best is None or score < best[0]:
                best = (score, box, (lx, ly))

        # Fallback if everything is crowded
        if best is None:
            lx = min(max(cx + 24, axes_bbox.x0 + w / 2 + 4), axes_bbox.x1 - w / 2 - 4)
            ly = min(max(cy + 24, axes_bbox.y0 + h / 2 + 4), axes_bbox.y1 - h / 2 - 4)
            box = np.array([lx - w / 2, ly - h / 2, lx + w / 2, ly + h / 2])
            best = (0.0, box, (lx, ly))

        placed_boxes.append(best[1])
        label_specs.append(
            {
                **item,
                "label_center_disp": best[2],
            }
        )

    # Create label boxes
    texts = []
    for spec in label_specs:
        tx, ty = ax.transData.inverted().transform(spec["label_center_disp"])
        txt = ax.text(
            tx,
            ty,
            spec["label"],
            ha="center",
            va="center",
            fontsize=fontsize,
            bbox=dict(
                boxstyle="round,pad=0.18",
                facecolor="white",
                edgecolor="0.70",
                linewidth=0.45,
                alpha=0.95,
            ),
            zorder=4,
            clip_on=True,
        )
        texts.append((txt, spec["indices"]))

    # Re-draw so we can access the actual label box extents
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()

    # Connect label boxes to all corresponding points with thin leader lines
    for txt, indices in texts:
        bbox = txt.get_window_extent(renderer=renderer)
        for idx in indices:
            px, py = point_disp[idx]

            # Nearest point on label box
            anchor_x = float(np.clip(px, bbox.x0, bbox.x1))
            anchor_y = float(np.clip(py, bbox.y0, bbox.y1))

            (x0, y0), (x1, y1) = ax.transData.inverted().transform(
                np.array([[anchor_x, anchor_y], [px, py]])
            )

            ax.plot(
                [x0, x1],
                [y0, y1],
                color="0.35",
                lw=0.50,
                alpha=0.85,
                zorder=2,
            )

    _save(fig, output_dir, "fig_operational_tradeoff")

# def create_tradeoff_plot(win_all: pd.DataFrame, output_dir: Path) -> None:
#     fa_avg = win_all[win_all.phase == "phase3_perturbed_benign"].groupby(["feature_view", "detector"])[
#         "false_alarm_rate_percent"
#     ].mean().rename("false_alarm_rate_percent")
#     rec_avg = win_all[win_all.phase == "phase4_perturbed_attacked"].groupby(["feature_view", "detector"])[
#         "attack_window_recall_percent"
#     ].mean().rename("attack_window_recall_percent")
#     auprc_avg = win_all[win_all.phase == "phase4_perturbed_attacked"].groupby(["feature_view", "detector"])[
#         "auprc"
#     ].mean().rename("auprc")
#     trade = pd.concat([fa_avg, rec_avg, auprc_avg], axis=1).reset_index()
#     trade.to_csv(output_dir / "operational_tradeoff_summary.csv", index=False)

#     markers = {"runtime": "o", "runtime_controller": "s", "runtime_process": "^", "fused": "D"}
#     fig, ax = plt.subplots(figsize=(5.3, 3.6), constrained_layout=True)
#     for feature_view, marker in markers.items():
#         sub = trade[trade.feature_view == feature_view]
#         ax.scatter(sub.false_alarm_rate_percent, sub.auprc, marker=marker, s=45, label=feature_view.replace("_", "+"))
#         for _, row in sub.iterrows():
#             ax.text(
#                 row.false_alarm_rate_percent + 0.12,
#                 row.auprc + 0.006,
#                 DET_LABELS.get(row.detector, row.detector),
#                 fontsize=6.2,
#             )
#     ax.set_xlabel("Mean false-alarm rate on perturbed benign runs [%]", fontsize=8.4)
#     ax.set_ylabel("Mean AUPRC on perturbed attacked runs", fontsize=8.4)
#     ax.set_title("Operational robustness trade-off", fontsize=9.4)
#     ax.set_xlim(left=0)
#     ax.set_ylim(0, 1.03)
#     ax.grid(True, alpha=0.28)
#     ax.legend(fontsize=6.5, title="Feature view", title_fontsize=7)
#     _save(fig, output_dir, "fig_operational_tradeoff")


def _save(fig, output_dir: Path, name: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_dir / f"{name}.pdf", bbox_inches="tight")
    fig.savefig(output_dir / f"{name}.png", dpi=450, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=Path("outputs/notebook_pipeline"))
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/paper_figures"))
    args = parser.parse_args()

    win_all = pd.read_csv(args.input_dir / "metrics_window_pooled_all_attacks.csv")
    # create_false_alarm_heatmaps(win_all, args.output_dir)
    # create_auprc_heatmaps(win_all, args.output_dir)
    create_tradeoff_plot(win_all, args.output_dir)
    print(f"Wrote figures to {args.output_dir}")


if __name__ == "__main__":
    main()
