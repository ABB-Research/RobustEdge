# Evaluation protocol

This repository evaluates robustness of normal-only anomaly detectors under the current four-phase campaign.

## Phases

| Phase | Meaning | Use |
|---|---|---|
| `phase1_clean_benign` | no attack, no perturbation | training, validation/calibration, lambda=0 benign reference |
| `phase2_clean_attacked` | attack, no perturbation | lambda=0 attack reference |
| `phase3_perturbed_benign` | perturbation, no attack | false-alarm robustness |
| `phase4_perturbed_attacked` | perturbation + attack | attack-detection robustness |

## Feature views

The pipeline now supports feature-view ablations:

| View | Included feature prefixes | Scientific question |
|---|---|---|
| `runtime` | `rt_` | closest to ABB zero-day syscall-based container detection |
| `runtime_process` | `rt_`, `proc_` | runtime detection with TEP telemetry context |
| `runtime_controller` | `rt_`, `ctrl_` | runtime detection with controller-command context |
| `fused` | `rt_`, `proc_`, `ctrl_` | full industrial edge context |

Alarm events are disabled by default because they can reflect real process abnormality and may confound pure container attack detection.

## Cross-validation / calibration splits

The current campaign has three clean benign runs. The default protocol uses leave-one-clean-benign-out calibration:

1. train runs 1+2, validate run 3,
2. train runs 1+3, validate run 2,
3. train runs 2+3, validate run 1.

For each split, models are trained only on the two clean benign training runs. The held-out clean benign run calibrates thresholds. All phase2--phase4 runs are evaluated as test runs. The clean validation run is retained as the lambda=0 benign reference for false-alarm plots.

## Metrics

The repository writes both per-run and pooled-window metrics.

### Per-run metrics

Per-run metrics preserve repetition structure and are useful for mean ± standard deviation across runs/splits:

- event recall,
- median/mean/min/max time-to-detect,
- false alarms per hour,
- false-alarm rate percent,
- predicted alarm rate percent,
- AUROC/AUPRC,
- precision/recall/F1/MCC,
- TP/TN/FP/FN counts.

### Pooled-window metrics

Pooled-window metrics answer the question: what happens if all attack windows or all benign windows under a condition are considered together? This is useful for heatmaps and avoids overemphasis on individual short runs.

The most important pooled metrics are:

- `false_alarm_rate_percent` for phase3 perturbed benign data,
- `attack_window_recall_percent` for phase4 perturbed attacked data,
- `auprc` and `auroc` as ranking diagnostics.

`false_alarm_rate_percent` is the percentage of benign windows predicted as anomalous. With 4 s windows, one hour contains 900 windows; 1 false alarm per hour corresponds to about 0.111% false-alarm rate.

## Plotting

Default figure generation now creates:

- heatmaps for false-alarm rate percent for all detectors and feature views,
- heatmaps for attack-window recall percent for all detectors and feature views,
- heatmaps for AUPRC for all detectors and feature views,
- per-run timelines with all detector scores for all test data,
- per-run metric distribution plots.

Because the current campaign contains only lambda values 0.5 and 1.0, the pipeline does not use overloaded line plots. Instead, lambda=0 clean baselines are replicated across perturbation families for visualization, and heatmaps show lambda = 0, 0.5, 1.0.
