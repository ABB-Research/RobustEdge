"""Detection, false-alarm, and robustness metrics.

The repository reports both per-run metrics and pooled window-level metrics.
Per-run metrics are useful because they preserve the repetition structure of the
campaign.  Pooled condition metrics are useful for paper figures where all
attack windows or all benign windows under a condition should be evaluated
together.
"""

from __future__ import annotations

import numpy as np
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    f1_score,
    matthews_corrcoef,
    precision_score,
    recall_score,
    roc_auc_score,
)


def safe_auroc(y_true: np.ndarray, scores: np.ndarray) -> float:
    """Area under ROC curve; NaN if only one class is present."""
    return float(roc_auc_score(y_true, scores)) if len(np.unique(y_true)) > 1 else float("nan")


def safe_auprc(y_true: np.ndarray, scores: np.ndarray) -> float:
    """Area under precision-recall curve; NaN if only one class is present."""
    return float(average_precision_score(y_true, scores)) if len(np.unique(y_true)) > 1 else float("nan")


def confusion_counts(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, int]:
    """Return TP/TN/FP/FN counts for binary predictions."""
    if len(y_true) == 0:
        return {"tp": 0, "tn": 0, "fp": 0, "fn": 0}
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
    return {"tp": int(tp), "tn": int(tn), "fp": int(fp), "fn": int(fn)}


def false_alarms_per_hour(y_true: np.ndarray, y_pred: np.ndarray, window_seconds: float) -> float:
    """False-alarm count normalized to one hour of benign windows."""
    benign = y_true == 0
    hours = benign.sum() * window_seconds / 3600.0
    return float(((y_pred == 1) & benign).sum() / hours) if hours > 0 else float("nan")


def false_alarm_rate_percent(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """False-alarm percentage among benign windows.

    This is often easier to interpret than a raw FA/h count.  With a 4 s window,
    one hour contains 900 windows; 1 false alarm per hour corresponds to about
    0.111% false-alarm rate.
    """
    benign = y_true == 0
    if benign.sum() == 0:
        return float("nan")
    return float(100.0 * ((y_pred == 1) & benign).sum() / benign.sum())


def predicted_alarm_rate_percent(y_pred: np.ndarray) -> float:
    """Percentage of all windows predicted as anomalous."""
    return float(100.0 * np.mean(y_pred == 1)) if len(y_pred) else float("nan")


def attack_window_recall_percent(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Recall percentage over attack-labelled windows."""
    attack = y_true == 1
    if attack.sum() == 0:
        return float("nan")
    return float(100.0 * ((y_pred == 1) & attack).sum() / attack.sum())


def event_recall_and_ttd(y_pred: np.ndarray, times_s: np.ndarray, intervals: list[tuple[float, float]]) -> tuple[float, list[float]]:
    """Compute event-level recall and time-to-detect values.

    An attack event is detected if at least one detector alarm is raised during
    the annotated attack interval.  TTD is the first alarm time after the event
    onset.
    """
    if not intervals:
        return float("nan"), []
    detected, ttds = 0, []
    for start, end in intervals:
        mask = (times_s >= start) & (times_s < end)
        alarm_times = times_s[mask & (y_pred == 1)]
        if len(alarm_times):
            detected += 1
            ttds.append(float(alarm_times.min() - start))
    return float(detected / len(intervals)), ttds


def window_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    """Thresholded window-level metrics."""
    if len(y_true) == 0:
        return {"precision": float("nan"), "recall": float("nan"), "f1": float("nan"), "mcc": float("nan")}
    return {
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
        "mcc": float(matthews_corrcoef(y_true, y_pred)) if len(np.unique(y_true)) > 1 else float("nan"),
    }


def evaluate_predictions(
    y_true: np.ndarray,
    scores: np.ndarray,
    y_pred: np.ndarray,
    window_seconds: float,
) -> dict[str, float]:
    """Evaluate threshold-free and thresholded window metrics."""
    counts = confusion_counts(y_true, y_pred)
    out = {
        "n_windows": int(len(y_true)),
        "n_attack_windows": int((y_true == 1).sum()),
        "n_benign_windows": int((y_true == 0).sum()),
        "auroc": safe_auroc(y_true, scores),
        "auprc": safe_auprc(y_true, scores),
        "false_alarms_per_hour": false_alarms_per_hour(y_true, y_pred, window_seconds),
        "false_alarm_rate_percent": false_alarm_rate_percent(y_true, y_pred),
        "predicted_alarm_rate_percent": predicted_alarm_rate_percent(y_pred),
        "attack_window_recall_percent": attack_window_recall_percent(y_true, y_pred),
    }
    out.update(counts)
    out.update(window_metrics(y_true, y_pred))
    return out


def evaluate_run(
    y_true: np.ndarray,
    scores: np.ndarray,
    y_pred: np.ndarray,
    times_s: np.ndarray,
    intervals: list[tuple[float, float]],
    window_seconds: float,
) -> dict[str, float]:
    """Evaluate all metrics for a single detector on a single run."""
    er, ttds = event_recall_and_ttd(y_pred, times_s, intervals)
    out = evaluate_predictions(y_true, scores, y_pred, window_seconds)
    out.update({
        "event_recall": er,
        "median_ttd_s": float(np.median(ttds)) if ttds else float("nan"),
        "mean_ttd_s": float(np.mean(ttds)) if ttds else float("nan"),
        "min_ttd_s": float(np.min(ttds)) if ttds else float("nan"),
        "max_ttd_s": float(np.max(ttds)) if ttds else float("nan"),
        "n_detected_events": int(len(ttds)),
        "n_attack_events": int(len(intervals)),
    })
    return out
