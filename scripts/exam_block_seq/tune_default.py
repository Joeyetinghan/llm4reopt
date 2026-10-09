#!/usr/bin/env python3
"""CLI entry point for tuning exam LP instances with Gurobi."""
from __future__ import annotations

import argparse
from pathlib import Path

from framework.utils.gurobi_tuning import discover_lp_files, select_lp_files, tune_lp_batch
from problems.exam_block_seq.paths import INSTANCES_ROOT, RUNS_ROOT


def main() -> None:
    parser = argparse.ArgumentParser(description="Tune exam LP instances with Gurobi")
    parser.add_argument(
        "--lp-dir",
        default=str(INSTANCES_ROOT),
        help="Directory containing .lp files",
    )
    parser.add_argument(
        "--lp-glob",
        default="*/model.lp",
        help="Glob pattern used to discover LP files inside --lp-dir",
    )
    parser.add_argument(
        "--lp-path",
        default=None,
        help="Tune one specific LP file",
    )
    parser.add_argument(
        "--index",
        type=int,
        default=None,
        help="Tune the Nth discovered LP file (0-based), useful for job arrays",
    )
    parser.add_argument(
        "--output-root",
        default=str(RUNS_ROOT / "tune"),
        help="Root directory for tuning artifacts",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=8,
        help="Gurobi Threads parameter",
    )
    parser.add_argument(
        "--mip-gap",
        type=float,
        default=1e-2,
        help="Target MIP gap used during tuning runs",
    )
    parser.add_argument(
        "--tune-time-limit",
        type=float,
        default=86_400,
        help="Gurobi TuneTimeLimit in seconds",
    )
    parser.add_argument(
        "--tune-results",
        type=int,
        default=1,
        help="Number of tuned parameter sets to keep",
    )
    parser.add_argument(
        "--tune-trials",
        type=int,
        default=None,
        help="Optional Gurobi TuneTrials parameter",
    )
    args = parser.parse_args()

    if args.lp_path is not None:
        selected = select_lp_files([], lp_path=args.lp_path, index=None)
        lp_files = list(selected)
    else:
        lp_files = discover_lp_files(args.lp_dir, args.lp_glob)
        if not lp_files:
            raise SystemExit(f"No LP files found in {Path(args.lp_dir).resolve()} matching {args.lp_glob!r}")
        selected = select_lp_files(lp_files, lp_path=None, index=args.index)

    print(f"Discovered {len(lp_files)} LP files")
    print(f"Tuning {len(selected)} LP file(s)")
    for path in selected:
        print(f"  - {path}")

    summaries = tune_lp_batch(
        selected,
        output_root=args.output_root,
        threads=args.threads,
        mip_gap=args.mip_gap,
        tune_time_limit=args.tune_time_limit,
        tune_results=args.tune_results,
        tune_trials=args.tune_trials,
    )

    failures = 0
    for summary in summaries:
        status = summary.get("status", "unknown")
        lp_path = summary.get("lp_path", "<unknown>")
        result_count = summary.get("tune_result_count", 0)
        print(f"{status.upper()}: {lp_path} (results={result_count})")
        if status != "ok":
            failures += 1
            error = summary.get("error")
            if error:
                print(f"  error: {error}")

    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
