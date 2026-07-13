#!/usr/bin/env python
from __future__ import annotations

import argparse

from robustedge.data import DatasetIndex


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize a RobustEdgeBench dataset.")
    parser.add_argument("--data-root", required=True)
    args = parser.parse_args()

    df = DatasetIndex.from_root(args.data_root).to_frame()
    print(f"Discovered {len(df)} runs")
    if df.empty:
        return
    print("\nCounts by phase:")
    print(df.groupby("phase").size().reset_index(name="n").to_string(index=False))
    print("\nCounts by phase/family/severity/attack duration:")
    print(df.groupby(["phase", "perturbation_family", "severity", "attack_duration"], dropna=False).size().reset_index(name="n").to_string(index=False))
    print("\nFirst rows:")
    print(df.head(20).to_string(index=False))


if __name__ == "__main__":
    main()
