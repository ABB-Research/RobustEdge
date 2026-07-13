#!/usr/bin/env python
"""Run the full RobustEdgeBench pipeline from the command line."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from robustedge.pipeline import run_end_to_end


def main() -> None:
    parser = argparse.ArgumentParser(description="Run RobustEdgeBench analysis pipeline.")
    parser.add_argument("--data-root", required=True, help="Path to data/raw/logs.")
    parser.add_argument("--output-dir", default="outputs/etfa_campaign", help="Output directory.")
    parser.add_argument("--config", default="configs/default.yaml", help="YAML configuration file.")
    parser.add_argument(
        "--feature-view",
        action="append",
        choices=["runtime", "runtime_process", "runtime_controller", "process_controller", "fused"],
        default=None,
        help="Feature view to evaluate. Can be passed multiple times. If omitted, config feature_views are used.",
    )
    parser.add_argument("--no-timelines", action="store_true", help="Disable generation of per-run timeline figures.")
    args = parser.parse_args()

    config_path = Path(args.config)
    if args.feature_view is not None or args.no_timelines:
        with config_path.open("r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        if args.feature_view is not None:
            cfg.setdefault("features", {})["feature_views"] = args.feature_view
        if args.no_timelines:
            cfg.setdefault("figures", {})["make_all_timelines"] = False
        out = Path(args.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        config_path = out / "effective_config.yaml"
        with config_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f)

    run_end_to_end(args.data_root, args.output_dir, config_path)


if __name__ == "__main__":
    main()
