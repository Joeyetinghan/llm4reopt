#!/usr/bin/env python3
"""Evaluate packaged experiment results against package-declared ground truth."""
from __future__ import annotations

import argparse
from pathlib import Path

from framework.evaluation import evaluate_results_dir, summarize_ground_truth, write_ground_truth_csv
from framework.registry import builtin_problem_roots, load_problem


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--problem", choices=sorted(builtin_problem_roots()), default=None)
    parser.add_argument("--problem-root", default=None)
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--case-id", default=None)
    parser.add_argument("--output-csv", default=None)
    parser.add_argument("--include-unmatched", action="store_true")
    parser.add_argument(
        "--reference-policy",
        choices=["off", "if_available", "require"],
        default="if_available",
        help="Whether to run reference-artifact checks in addition to patch checks.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.problem is None and args.problem_root is None:
        raise SystemExit("One of --problem or --problem-root is required")
    problem_name = args.problem
    spec, _ = load_problem(
        problem=problem_name if args.problem_root is None else None,
        problem_root=args.problem_root,
        load_runtime_data=False,
    )
    rows = evaluate_results_dir(
        spec,
        args.results_dir,
        explicit_case_id=args.case_id,
        include_unmatched=args.include_unmatched,
        reference_policy=args.reference_policy,
    )
    if not rows:
        print("No ground-truth evaluation rows produced.")
        return

    for row in rows:
        case_id = row.case_id or "?"
        verdict = row.status
        match = row.matches_ground_truth
        print(f"{case_id}: status={verdict}  match={match}")

    summary = summarize_ground_truth(rows)
    print(f"\nTotal rows: {summary['total']}")
    print(f"Matched cases: {summary['matched']}")
    print(f"Passed: {summary['passed']}")
    print(f"Failed: {summary['failed']}")
    print(f"Pending: {summary['pending']}")
    print(f"Partial: {summary['partial']}")
    print(f"Unmatched: {summary['unmatched']}")

    output_csv = args.output_csv or str(Path(args.results_dir).expanduser().resolve() / "evaluation.csv")
    csv_path = write_ground_truth_csv(rows, output_csv)
    print(f"\nEvaluation written to {csv_path}")


if __name__ == "__main__":
    main()
