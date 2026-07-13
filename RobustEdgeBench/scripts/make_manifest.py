#!/usr/bin/env python
from __future__ import annotations

import argparse
from pathlib import Path

from robustedge.data import DatasetIndex


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a manifest CSV for a RobustEdgeBench dataset.")
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--output", default="outputs/manifest.csv")
    args = parser.parse_args()

    df = DatasetIndex.from_root(args.data_root).to_frame()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"Wrote {len(df)} runs to {out}")


if __name__ == "__main__":
    main()
