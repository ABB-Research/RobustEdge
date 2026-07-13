# Methodology updates for the ETFA paper

The current campaign changes the paper methodology in four important ways:

1. **Phase-aware dataset.** The generated data are organized as clean benign, clean attacked, perturbed benign, and perturbed attacked phases. The methodology should describe these as the four evaluation phases.

2. **Selected severities, not dense curves.** The current robustness data contain `lambda=0.50` and `lambda=1.00` for P1--P5. Therefore, the paper should speak of discrete robustness profiles and heatmaps. Curves remain a possible visualization when more severities are generated.

3. **Fused feature view.** Feature extraction should not only use `sysdig_logs.ndjson`. The revised default uses runtime syscalls plus process telemetry (`tep_signals.ndjson`) and controller commands (`tep_controller_mv_commands.ndjson`). Alarm events are optional/diagnostic because alarms can also indicate process abnormality.

4. **Controller commands are not perturbed.** The current perturbation campaign does not perturb `tep_controller_mv_commands.ndjson`. This can be justified as representing a more hardened control-command path compared with general telemetry/alarm delivery. The feature extractor still uses controller commands because they provide useful context and can show indirect effects.
