# RobustEdgeBench

**RobustEdgeBench** is a reproducible Python/Jupyter analysis repository for the ETFA 2026 robustness benchmark on **ML-based container attack detection in industrial edge systems**.

The repository assumes that the data-generation campaign has already been executed. It does **not** generate telemetry, perturbations, or attacks. Instead, it provides reusable code for:

1. indexing generated campaign folders,
2. parsing run metadata and exported NDJSON files,
3. extracting detector-ready features from runtime, process, and controller logs,
4. training normal-only anomaly detection baselines,
5. evaluating clean and perturbed attack-detection performance,
6. quantifying false-alarm behavior under telemetry perturbations,
7. generating robustness profiles, heatmaps, timelines, and paper-ready figures.

The repository is designed for the current ABB/RUB campaign structure and supports folders such as:

```text
data/raw/logs/
  phase-phase1_clean_benign_perturbation-none_attackDuration-0_intensity-_20260424T191959Z/
    iteration-1/
      scenario.json
      config.json
      sysdig_logs.ndjson
      tep_signals.ndjson
      tep_controller_mv_commands.ndjson
      tep_alarm_events.ndjson
      annotations.ndjson
      attack_records.ndjson
      container_*.log
```

The public GitHub repository can host the analysis code and either the full dataset or a link to a separate citable dataset release.

---

## Repository scope

This repository covers the **analysis and robustness-evaluation pipeline** only.

It does **not** include:

- the TEP simulator implementation,
- the attack-agent implementation,
- the perturbation injection implementation,
- container orchestration for generating new data.

It does include:

- run discovery and metadata parsing,
- feature extraction,
- normal-only anomaly detection baselines,
- leave-one-clean-benign-out training/calibration splits,
- per-run and pooled-window evaluation,
- feature-view ablations,
- robustness summaries,
- visualization utilities.

---

## Current campaign phases

The current dataset naming convention is phase-aware:

| Phase | Meaning | Use in analysis |
|---|---|---|
| `phase1_clean_benign` | No attack, no perturbation | Training and validation/calibration |
| `phase2_clean_attacked` | Attack, no perturbation | Nominal attack-detection baseline |
| `phase3_perturbed_benign` | Perturbation, no attack | False-alarm robustness |
| `phase4_perturbed_attacked` | Perturbation and attack | Robustness under attack |

The parser extracts the following fields from folder names whenever possible:

- `phase`,
- `perturbation_family`, e.g. `P1`, `P2`, ...,
- `severity`, e.g. `lam0.50`,
- `attack_duration`,
- `attack_intensity`,
- scenario timestamp,
- iteration number.

The parser intentionally gives priority to folder-derived metadata when folder names are more specific than older or incomplete `scenario.json` fields.

---

## Current perturbation setting

The current dataset contains a selected set of perturbation severities rather than a dense severity grid:

- perturbation families: `P1`–`P5`,
- available nonzero severities: `lambda = 0.50` and `lambda = 1.00`,
- clean baseline: `lambda = 0.00`, represented by unperturbed clean runs,
- repetitions: three runs per generated condition.

The analysis therefore reports **discrete robustness profiles and heatmaps**, not smooth robustness curves. If future campaigns include more severity levels, the same code can be used to produce robustness curves.

---

## Feature extraction

The primary feature view follows the ABB zero-day container attack-detection setting: runtime features are extracted from a **bag-of-system-calls** representation of `sysdig_logs.ndjson`.

The repository additionally supports feature-view ablations using process and controller context.

### Supported feature views

| Feature view | Input streams | Purpose |
|---|---|---|
| `runtime` | `sysdig_logs.ndjson` | Primary container-runtime detection view |
| `runtime_process` | `sysdig_logs.ndjson` + `tep_signals.ndjson` | Tests whether process telemetry context changes robustness |
| `runtime_controller` | `sysdig_logs.ndjson` + `tep_controller_mv_commands.ndjson` | Tests whether controller-command context changes robustness |
| `fused` | runtime + process + controller | Full industrial-edge context view |

### Runtime features

Runtime features are extracted from `sysdig_logs.ndjson`.

Each sysdig aggregation window becomes one ML sample. The current campaign uses 4 s windows.

Typical runtime features are syscall-count features such as:

```text
rt_write
rt_read
rt_open
rt_close
rt_nanosleep
...
```

### Process features

Process features are extracted from `tep_signals.ndjson`.

The process stream is asynchronous, so values are aligned to the sysdig windows using a latest-available-value strategy. For each sysdig window, the code uses the most recent process value available at or before the runtime window.

Feature prefixes:

```text
proc_
```

Depending on configuration, the code can include:

- latest aligned process values,
- process deltas,
- update-count features.

### Controller features

Controller features are extracted from `tep_controller_mv_commands.ndjson`.

They are aligned to the same sysdig windows using the same latest-available-value strategy.

Feature prefixes:

```text
ctrl_
```

Depending on configuration, the code can include:

- latest aligned controller command values,
- controller command deltas.

### Alarm events

`tep_alarm_events.ndjson` is not included by default in the detector feature set.

Reason: alarm events may reflect legitimate abnormal process behavior and could make the interpretation of container attack detection less clean. Alarm events remain available for context, diagnostics, and future ablation studies.

---

## Scientific protocol

The default analysis protocol is:

1. train only on `phase1_clean_benign`,
2. calibrate thresholds only on held-out clean benign runs,
3. evaluate nominal clean attacks on `phase2_clean_attacked`,
4. evaluate false-alarm robustness on `phase3_perturbed_benign`,
5. evaluate attack-detection robustness on `phase4_perturbed_attacked`,
6. aggregate results over leave-one-clean-benign-out splits and campaign repetitions.

No attack labels, perturbed runs, or test runs are used for model fitting or threshold selection.

---

## Leave-one-clean-benign-out calibration

The current campaign contains three clean benign runs. The repository uses leave-one-clean-benign-out splits:

| Split | Training runs | Validation/calibration run |
|---|---|---|
| 1 | clean benign runs 1 + 2 | clean benign run 3 |
| 2 | clean benign runs 1 + 3 | clean benign run 2 |
| 3 | clean benign runs 2 + 3 | clean benign run 1 |

For each split:

1. preprocessing is fitted on the training runs,
2. the anomaly detector is fitted on the training runs,
3. the alarm threshold is calibrated on the validation run,
4. all other runs are evaluated,
5. metrics are later averaged across splits.

This avoids dependence on one arbitrary train/validation split and makes better use of the limited clean benign data.

---

## Anomaly detection baselines

The repository includes compact normal-only baselines:

- PCA reconstruction error,
- Gaussian mixture model negative log-likelihood,
- one-class SVM,
- isolation forest,
- shallow autoencoder reconstruction error.

All detectors output one anomaly score per time window. Higher scores mean more anomalous behavior.

Thresholds are calibrated on clean benign validation scores using a high empirical quantile.

---

## Metrics

The repository reports both threshold-free and threshold-based metrics.

### Primary operational metrics

| Metric | Meaning |
|---|---|
| `false_alarm_rate_percent` | Percentage of benign windows classified as anomalous |
| `false_alarms_per_hour` | False alarms normalized by benign runtime in hours |
| `event_recall` | Fraction of attack events detected at least once |
| `attack_window_recall_percent` | Percentage of attack windows classified as anomalous |
| `median_ttd_s` | Median time-to-detect for detected attack events |

The main false-alarm metric is now:

```text
false_alarm_rate_percent
```

This is easier to interpret than false alarms per hour because it directly states what fraction of benign windows triggered an alarm. `false_alarms_per_hour` is still retained as an operational diagnostic.

### Secondary diagnostics

The repository also reports:

- AUROC,
- AUPRC,
- precision,
- recall,
- F1,
- MCC,
- predicted alarm rate.

---

## Per-run and pooled-window evaluation

The repository computes two complementary evaluation views.

### 1. Per-run metrics

Per-run metrics preserve the campaign repetition structure.

They are useful for:

- mean ± standard deviation over repetitions,
- event recall,
- time-to-detect,
- run-level uncertainty.

Output file:

```text
metrics_by_run.csv
```

### 2. Pooled-window metrics

Pooled-window metrics aggregate all windows belonging to the same condition.

They are useful for:

- attack-window recall,
- false-alarm rate,
- AUROC/AUPRC across all windows in a condition,
- heatmaps over perturbation family and severity.

Output files:

```text
metrics_window_pooled_all_attacks.csv
metrics_window_pooled_by_duration.csv
```

The pooled “all attacks” file merges attack durations where appropriate. This supports compact heatmaps where all attacked windows are evaluated together.

---

## Robustness reporting

Because the current dataset only contains `lambda = 0.50` and `lambda = 1.00` as nonzero perturbation severities, the repository focuses on:

- robustness heatmaps,
- discrete robustness profiles,
- robustness summaries over the available severity points.

Clean unperturbed runs are used as the `lambda = 0.00` reference.

The repository computes:

| Summary | Meaning |
|---|---|
| `R_avg` | Average metric value over available severity points |
| `R_worst` | Worst observed severity-conditioned metric value |
| `R_prod` | Product/geometric-style robustness summary penalizing brittle behavior |

Output file:

```text
robustness_summary.csv
```

---

## Installation

Create and activate a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
```

On Windows:

```bash
.venv\Scripts\activate
```

Install the repository in editable mode:

```bash
python -m pip install --upgrade pip
pip install -e .[dev]
```

Alternatively:

```bash
pip install -r requirements.txt
```

---

## Quick start

Place raw generated logs under:

```text
data/raw/logs/
```

Then run:

```bash
python scripts/summarize_dataset.py --data-root data/raw/logs
python scripts/run_pipeline.py --data-root data/raw/logs --output-dir outputs/etfa_campaign
```

---

## Expected outputs

A full pipeline run writes:

```text
outputs/etfa_campaign/
  manifest.csv
  features.csv

  metrics_by_run.csv
  metrics_by_run_all_splits.csv

  scores_by_window.csv
  scores_by_window_all_splits.csv

  metrics_aggregated.csv
  metrics_window_pooled_all_attacks.csv
  metrics_window_pooled_by_duration.csv

  robustness_summary.csv

  models/
    runtime/
      ...
    runtime_process/
      ...
    runtime_controller/
      ...
    fused/
      ...

  figures/
    heatmaps_false_alarm_rate_percent/
    heatmaps_attack_window_recall_percent/
    heatmaps_auprc/
    metric_distributions/
    timelines_all_models/
```

The exact figure set depends on the configured feature views and available test conditions.

---

## Notebooks

Recommended notebook order:

1. `notebooks/00_dataset_overview.ipynb`
2. `notebooks/01_feature_extraction.ipynb`
3. `notebooks/02_train_baselines.ipynb`
4. `notebooks/03_evaluate_robustness.ipynb`
5. `notebooks/04_paper_figures.ipynb`

All notebooks call reusable Python code from:

```text
src/robustedge/
```

The notebooks are intended for transparent scientific analysis and figure development. The CLI pipeline is intended for reproducible batch execution.

---

## Configuration

The main configuration file is:

```text
configs/default.yaml
```

Important options include:

```yaml
features:
  include_runtime_features: true
  include_process_features: true
  include_controller_features: true
  include_alarm_features: false

  feature_views:
    - runtime
    - runtime_process
    - runtime_controller
    - fused

calibration:
  target_fpr_quantile: 0.995

figures:
  make_all_timelines: true
  max_timeline_runs: null
```

To run a runtime-only analysis, set:

```yaml
features:
  include_runtime_features: true
  include_process_features: false
  include_controller_features: false
  include_alarm_features: false

  feature_views:
    - runtime
```

---

## Data and code availability

The source code, benchmark configuration files, analysis pipeline, and generated dataset used in the ETFA paper will be made publicly available after acceptance in a citable repository with a persistent DOI. During review, the materials can be made available to reviewers upon request.

---

## Recommended citation

A formal citation will be added after publication. For now, please cite the corresponding ETFA 2026 paper draft:

```text
G. Manca et al.,
"Robustness Benchmarking of ML-Based Container Attack Detection with a Perturbation-Driven Industrial Edge Testbed,"
submitted to IEEE ETFA 2026.
```

---

## Notes for contributors

When extending the repository:

- keep data generation separate from analysis,
- avoid using metadata columns as ML features,
- keep train/validation/test split logic run-based,
- document any new feature view or perturbation interpretation,
- store all generated figures and metrics under `outputs/`,
- keep notebooks reproducible by calling functions from `src/robustedge/`.
