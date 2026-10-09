#!/usr/bin/env python3
"""Run exact manifest-defined experiment batches across packaged problems."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from framework.execution.batch import run_experiment_manifest
from framework.llm import DEFAULT_LLM_MODEL


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", help="YAML or JSON manifest defining exact runs")
    parser.add_argument("--output-dir", default=None, help="Override manifest output directory")
    parser.add_argument(
        "--model",
        default=None,
        help=f"Override model for every run (default manifest setting or {DEFAULT_LLM_MODEL})",
    )
    parser.add_argument("--api-key", default=None, help="Optional API key override for every run")
    parser.add_argument(
        "--evaluate-ground-truth",
        action="store_true",
        help="Force packaged ground-truth evaluation on all runs",
    )
    parser.add_argument(
        "--reference-policy",
        choices=["off", "if_available", "require"],
        default=None,
        help="Reference-artifact policy for ground-truth evaluation",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print resolved runs without executing")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = run_experiment_manifest(
        args.manifest,
        output_dir=args.output_dir,
        model_name=args.model,
        api_key=args.api_key,
        evaluate_ground_truth=True if args.evaluate_ground_truth else None,
        reference_policy=args.reference_policy,
        dry_run=args.dry_run,
    )

    if args.dry_run:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return

    print(f"\nOutput directory: {summary['output_dir']}")
    print(f"Total runs: {summary['total_runs']}")
    print(f"OK: {summary['ok_runs']}")
    print(f"Errors: {summary['error_runs']}")
    print(f"Summary CSV: {summary['summary_csv']}")
    if summary.get("evaluation_csv"):
        print(f"Evaluation CSV: {summary['evaluation_csv']}")

    if summary["error_runs"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
