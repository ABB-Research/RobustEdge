# Data directory

Raw campaign logs should be placed in:

```text
data/raw/logs/
```

The repository intentionally does not track large raw datasets. Use Git LFS or a separate dataset release for public data.

Processed artifacts produced by notebooks/scripts are written to:

```text
data/processed/
```

or, preferably, to `outputs/<experiment-name>/`.
