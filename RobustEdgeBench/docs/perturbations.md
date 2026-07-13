# Perturbations

The current campaign includes perturbation families P1--P5 and severities 0.50 and 1.00 for most robustness settings.

Severity is not only a count of records. For tag-scoped perturbations, severity controls both:

1. perturbation magnitude for affected tags, and
2. affected-tag fraction.

Default affected-tag rule:

```text
f_aff(lambda) = lambda * 0.50
```

Thus, `lambda=1.0` can affect up to 50% of eligible tags.

## Families

- **P1 record loss:** affected tags + Bernoulli record dropping.
- **P2 duplicate records:** affected tags + repeated writes.
- **P3 timing disorder:** affected tags + timestamp jitter/out-of-order delivery.
- **P4 buffered delivery:** default path-level outage followed by burst flush.
- **P5 rate degradation:** affected tags + systematic downsampling/throttling.

The analysis code does not implement perturbations. It reads generated data and evaluates robustness based on the metadata extracted from folder names and `scenario.json`.
