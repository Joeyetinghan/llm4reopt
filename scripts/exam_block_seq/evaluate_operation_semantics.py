#!/usr/bin/env python3
"""Shard and aggregate exam operation-level semantic evaluation."""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
import json
import math
import os
from pathlib import Path
import re
from typing import Any

from framework.evaluation.ground_truth import evaluate_result_payload, load_result_payload
from framework.registry import load_problem


DEFAULT_OBJECTIVE_ABS_TOL = 50.0
DEFAULT_OBJECTIVE_REL_TOL = 0.05
DEFAULT_MAIN_RUN_FAMILY = "patchedit_auto"
DEFAULT_MAIN_BASELINE_RUN_FAMILY = "codeedit_auto"
DEFAULT_SELECTOR_RUN_FAMILY = "patchedit_auto"
DEFAULT_SELECTOR_BASELINE_RUN_FAMILY = "patchedit_scratch"

CSV_FIELDS = [
    "run_family",
    "run_id",
    "model",
    "instance",
    "prompt",
    "run_status",
    "failure_stage",
    "failure_label",
    "failure_detail",
    "operation_status",
    "operation_verdict",
    "operation_correct",
    "operation_reason",
    "operation_projection_state",
    "operation_projected_semantics",
    "operation_projected_patch_ops",
    "reference_verdict",
    "reopt_objective",
    "ground_truth_objective",
    "exact_objective",
    "objective_abs_diff",
    "objective_rel_diff",
    "objective_close",
    "objective_close_threshold",
]
SELECTOR_IMPROVEMENT_FIELDS = [
    "run_family",
    "model",
    "selector_baseline_run_family",
    "paired_runs",
    "objective_comparable_pairs",
    "candidate_missing_objective_runs",
    "baseline_missing_objective_runs",
    "missing_objective_pairs",
    "candidate_operation_correct_pairs",
    "operation_correct_objective_pairs",
    "improved_runs",
    "tied_runs",
    "worsened_runs",
    "improvement_rate",
    "mean_rel_improvement_vs_baseline",
    "median_rel_improvement_vs_baseline",
    "operation_correct_improved_runs",
    "operation_correct_tied_runs",
    "operation_correct_worsened_runs",
    "operation_correct_improvement_rate",
    "operation_correct_mean_rel_improvement_vs_baseline",
    "operation_correct_median_rel_improvement_vs_baseline",
    "operation_correct_scratch_no_incumbent_wins",
    "operation_correct_assessable_pairs",
    "operation_correct_assessed_improvements",
    "operation_correct_assessed_improvement_rate",
]
PATCHEDIT_COMPARISON_FIELDS = [
    "model",
    "auto_total",
    "auto_ok",
    "auto_operation_match",
    "auto_operation_mismatch",
    "auto_operation_unavailable",
    "auto_operation_match_rate",
    "auto_objective_close",
    "auto_objective_close_rate_ok_only",
    "scratch_total",
    "scratch_ok",
    "scratch_operation_match",
    "scratch_operation_mismatch",
    "scratch_operation_unavailable",
    "scratch_operation_match_rate",
    "scratch_objective_close",
    "scratch_objective_close_rate_ok_only",
    "selector_pairs",
    "selector_operation_correct_assessable_pairs",
    "selector_operation_correct_assessed_improvements",
    "selector_operation_correct_assessed_improvement_rate",
    "selector_operation_correct_mean_rel_improvement_vs_baseline",
    "selector_operation_correct_median_rel_improvement_vs_baseline",
]
MAIN_COMPARISON_FIELDS = [
    "group_type",
    "group",
    "candidate_run_family",
    "baseline_run_family",
    "candidate_total",
    "candidate_ok",
    "candidate_objective_close",
    "candidate_objective_close_rate",
    "candidate_operation_match",
    "candidate_operation_match_rate",
    "baseline_total",
    "baseline_ok",
    "baseline_objective_close",
    "baseline_objective_close_rate",
    "baseline_operation_match",
    "baseline_operation_match_rate",
    "objective_close_rate_delta",
    "operation_match_rate_delta",
]
MANIFEST_FIELDS = [
    "task_id",
    "run_family",
    "run_id",
    "model",
    "instance",
    "prompt",
    "run_status",
    "failure_stage",
    "failure_label",
    "failure_detail",
    "result_path",
    "reference_verdict",
    "reopt_objective",
    "ground_truth_objective",
    "exact_objective",
]


def build_manifest(args: argparse.Namespace) -> None:
    report_dir = Path(args.report_dir)
    detail_path = report_dir / "detail.csv"
    baseline_path = report_dir / "operation_semantic_correctness.csv"
    detail_rows = _read_csv(detail_path)
    baseline_by_run_id = {
        row["run_id"]: row
        for row in _read_csv(baseline_path, required=False)
        if row.get("run_id")
    }
    run_families = _parse_csv_set(args.run_families)

    manifest_rows: list[dict[str, Any]] = []
    for detail_row in detail_rows:
        if run_families and detail_row.get("run_family") not in run_families:
            continue
        run_id = str(detail_row.get("run_id") or "").strip()
        if not run_id:
            continue
        baseline_row = baseline_by_run_id.get(run_id, {})
        reopt_objective = _finite_float(detail_row.get("reopt_objective"))
        ground_truth_objective = _finite_float(detail_row.get("ground_truth_objective"))
        manifest_rows.append(
            {
                "task_id": str(len(manifest_rows)),
                "run_family": detail_row.get("run_family") or "",
                "run_id": run_id,
                "model": detail_row.get("model") or baseline_row.get("model") or "",
                "instance": detail_row.get("instance") or baseline_row.get("instance") or "",
                "prompt": detail_row.get("prompt") or baseline_row.get("prompt") or "",
                "run_status": detail_row.get("run_status") or baseline_row.get("run_status") or "",
                "failure_stage": detail_row.get("failure_stage") or baseline_row.get("failure_stage") or "",
                "failure_label": detail_row.get("failure_label") or baseline_row.get("failure_label") or "",
                "failure_detail": detail_row.get("failure_detail") or baseline_row.get("failure_detail") or "",
                "result_path": detail_row.get("result_path") or "",
                "reference_verdict": baseline_row.get("reference_verdict")
                or detail_row.get("reference_verdict")
                or "",
                "reopt_objective": reopt_objective,
                "ground_truth_objective": ground_truth_objective,
                "exact_objective": baseline_row.get("exact_objective")
                or _exact_objective_from_detail(detail_row),
            }
        )

    output = Path(args.output)
    _write_csv(manifest_rows, output, MANIFEST_FIELDS)
    print(f"Wrote {len(manifest_rows)} manifest rows to {output}")


def eval_array_task(args: argparse.Namespace) -> None:
    manifest_rows = _read_csv(Path(args.manifest))
    task_id = args.task_id
    if task_id is None:
        task_id = 0
    num_shards = args.num_shards
    if num_shards is None:
        num_shards = int(
            os.environ.get("NUM_SHARDS")
            or str(len(manifest_rows))
        )
    if task_id < 0 or task_id >= num_shards:
        raise SystemExit(f"Invalid task id {task_id}; expected 0..{num_shards - 1}")

    spec, _ = load_problem(problem="exam_block_seq", load_runtime_data=False)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    shard_size = math.ceil(len(manifest_rows) / num_shards) if num_shards > 0 else len(manifest_rows)
    start = task_id * shard_size
    stop = min(start + shard_size, len(manifest_rows))
    written = 0
    for row_index in range(start, stop):
        manifest_row = manifest_rows[row_index]
        output_row = _evaluate_manifest_row(spec, manifest_row)
        _add_objective_fields(
            output_row,
            manifest_row,
            abs_tol=args.objective_abs_tol,
            rel_tol=args.objective_rel_tol,
        )
        output_path = _task_output_path(output_dir, int(manifest_row["task_id"]))
        output_path.write_text(json.dumps(output_row, indent=2, sort_keys=True), encoding="utf-8")
        written += 1
        print(f"Wrote {output_path}")
    print(f"Shard {task_id}/{num_shards} wrote {written} row(s) from rows {start}:{stop}")


def aggregate(args: argparse.Namespace) -> None:
    manifest_rows = _read_csv(Path(args.manifest))
    eval_dir = Path(args.eval_dir)
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for manifest_row in manifest_rows:
        task_id = int(manifest_row["task_id"])
        path = _task_output_path(eval_dir, task_id)
        if not path.exists():
            missing.append(str(path))
            continue
        row = _load_json(path)
        _add_objective_fields(
            row,
            manifest_row,
            abs_tol=args.objective_abs_tol,
            rel_tol=args.objective_rel_tol,
        )
        rows.append(row)

    if missing and not args.allow_missing:
        preview = "\n".join(missing[:10])
        raise SystemExit(f"Missing {len(missing)} task output(s):\n{preview}")

    rows.sort(key=lambda row: int(_manifest_task_id(manifest_rows, row["run_id"])))
    report_dir = Path(args.report_dir)
    _merge_detail_failure_fields(rows, report_dir)
    operation_csv = report_dir / "operation_semantic_correctness.csv"
    summary_json = report_dir / "operation_semantic_summary.json"
    main_comparison_csv = report_dir / "main_comparison_patchedit_auto_vs_codeedit_auto.csv"
    selector_improvement_csv = report_dir / "selector_operation_objective_improvement.csv"
    patchedit_comparison_csv = report_dir / "patchedit_auto_vs_scratch_by_model.csv"
    _write_csv(rows, operation_csv, CSV_FIELDS)
    main_comparison_rows = _summarize_main_family_comparison(rows)
    _write_csv(main_comparison_rows, main_comparison_csv, MAIN_COMPARISON_FIELDS)
    selector_improvement_rows = _summarize_selector_objective_improvement(rows)
    _write_csv(selector_improvement_rows, selector_improvement_csv, SELECTOR_IMPROVEMENT_FIELDS)
    patchedit_comparison_rows = _summarize_patchedit_auto_vs_scratch(
        rows,
        selector_improvement_rows=selector_improvement_rows,
    )
    _write_csv(patchedit_comparison_rows, patchedit_comparison_csv, PATCHEDIT_COMPARISON_FIELDS)

    summary = _build_summary(
        rows,
        csv_path=str(operation_csv),
        main_comparison_csv_path=str(main_comparison_csv),
        main_comparison_rows=main_comparison_rows,
        selector_improvement_csv_path=str(selector_improvement_csv),
        selector_improvement_rows=selector_improvement_rows,
        patchedit_comparison_csv_path=str(patchedit_comparison_csv),
        patchedit_comparison_rows=patchedit_comparison_rows,
        objective_abs_tol=args.objective_abs_tol,
        objective_rel_tol=args.objective_rel_tol,
    )
    summary_json.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    results_md = Path(args.results_md) if args.results_md else report_dir / "RESULTS.md"
    if results_md.exists():
        _update_results_markdown(results_md, summary)

    print(f"Wrote {operation_csv}")
    print(f"Wrote {main_comparison_csv}")
    print(f"Wrote {selector_improvement_csv}")
    print(f"Wrote {patchedit_comparison_csv}")
    print(f"Wrote {summary_json}")
    if results_md.exists():
        print(f"Updated {results_md}")


def _read_csv(path: Path, *, required: bool = True) -> list[dict[str, str]]:
    if not path.exists():
        if required:
            raise FileNotFoundError(path)
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _evaluate_manifest_row(spec: Any, manifest_row: dict[str, str]) -> dict[str, Any]:
    payload = load_result_payload(manifest_row["result_path"])
    eval_row = evaluate_result_payload(spec, payload, reference_policy="off")
    checks = dict(eval_row.checks or {})
    semantic_result = eval_row.mode_results.get("semantic") if eval_row.mode_results else None
    semantic_checks = dict(semantic_result.checks or {}) if semantic_result is not None else {}
    semantic_details = dict(semantic_result.details or {}) if semantic_result is not None else {}
    projection = semantic_details.get("projection")
    projection = dict(projection) if isinstance(projection, dict) else {}

    verdict = str(checks.get("semantic_verdict") or semantic_checks.get("semantic_verdict") or "")
    reference_verdict = str(checks.get("reference_verdict") or "").strip()
    if reference_verdict in {"", "unresolved"}:
        reference_verdict = str(manifest_row.get("reference_verdict") or reference_verdict)
    return {
        "run_family": manifest_row.get("run_family") or "",
        "run_id": manifest_row["run_id"],
        "model": manifest_row["model"],
        "instance": manifest_row["instance"],
        "prompt": manifest_row["prompt"],
        "run_status": manifest_row["run_status"],
        "failure_stage": manifest_row.get("failure_stage") or "",
        "failure_label": manifest_row.get("failure_label") or "",
        "failure_detail": manifest_row.get("failure_detail") or "",
        "operation_status": semantic_result.status if semantic_result is not None else eval_row.status,
        "operation_verdict": verdict,
        "operation_correct": _semantic_correct(verdict),
        "operation_reason": str(
            checks.get("semantic_reason")
            or semantic_checks.get("semantic_reason")
            or ""
        ),
        "operation_projection_state": str(projection.get("projection_state") or ""),
        "operation_projected_semantics": _join_values(projection.get("projected_semantics")),
        "operation_projected_patch_ops": _join_values(projection.get("projected_patch_ops")),
        "reference_verdict": reference_verdict,
        "reopt_objective": manifest_row.get("reopt_objective") or "",
        "ground_truth_objective": manifest_row.get("ground_truth_objective") or "",
        "exact_objective": manifest_row.get("exact_objective") or "",
    }


def _write_csv(rows: list[dict[str, Any]], path: Path, fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_value(row.get(field)) for field in fieldnames})


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _merge_detail_failure_fields(rows: list[dict[str, Any]], report_dir: Path) -> None:
    detail_by_run_id = {
        row["run_id"]: row
        for row in _read_csv(report_dir / "detail.csv", required=False)
        if row.get("run_id")
    }
    for row in rows:
        detail = detail_by_run_id.get(str(row.get("run_id") or ""))
        if not detail:
            continue
        for field in ["failure_stage", "failure_label", "failure_detail"]:
            if not row.get(field):
                row[field] = detail.get(field) or ""


def _parse_csv_set(value: str | None) -> set[str]:
    return {item.strip() for item in str(value or "").split(",") if item.strip()}


def _exact_objective_from_detail(row: dict[str, str]) -> str:
    candidate = _finite_float(row.get("reopt_objective"))
    reference = _finite_float(row.get("ground_truth_objective"))
    if candidate is None or reference is None:
        return ""
    return str(abs(candidate - reference) <= 1e-4)


def _finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _semantic_correct(verdict: str) -> bool | None:
    if verdict == "match":
        return True
    if verdict == "mismatch":
        return False
    return None


def _add_objective_fields(
    row: dict[str, Any],
    manifest_row: dict[str, Any],
    *,
    abs_tol: float,
    rel_tol: float,
) -> None:
    candidate = _finite_float(row.get("reopt_objective") or manifest_row.get("reopt_objective"))
    reference = _finite_float(row.get("ground_truth_objective") or manifest_row.get("ground_truth_objective"))
    if candidate is None or reference is None:
        row.setdefault("reopt_objective", manifest_row.get("reopt_objective") or "")
        row.setdefault("ground_truth_objective", manifest_row.get("ground_truth_objective") or "")
        row.setdefault("exact_objective", manifest_row.get("exact_objective") or "")
        row["objective_abs_diff"] = ""
        row["objective_rel_diff"] = ""
        row["objective_close"] = ""
        row["objective_close_threshold"] = ""
        return

    abs_diff = abs(candidate - reference)
    rel_diff = abs_diff / max(abs(reference), 1.0)
    close_threshold = max(float(abs_tol), float(rel_tol) * abs(reference))
    row["reopt_objective"] = candidate
    row["ground_truth_objective"] = reference
    row["exact_objective"] = row.get("exact_objective") or str(abs_diff <= 1e-4)
    row["objective_abs_diff"] = abs_diff
    row["objective_rel_diff"] = rel_diff
    row["objective_close"] = str(abs_diff <= close_threshold)
    row["objective_close_threshold"] = close_threshold


def _csv_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _join_values(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ",".join(str(item) for item in value)
    return str(value or "")


def _task_output_path(output_dir: Path, task_id: int) -> Path:
    return output_dir / f"task_{task_id:04d}.json"


def _manifest_task_id(manifest_rows: list[dict[str, str]], run_id: str) -> str:
    for row in manifest_rows:
        if row.get("run_id") == run_id:
            return row["task_id"]
    return "0"


def _build_summary(
    rows: list[dict[str, Any]],
    *,
    csv_path: str,
    main_comparison_csv_path: str = "",
    main_comparison_rows: list[dict[str, Any]] | None = None,
    selector_improvement_csv_path: str = "",
    selector_improvement_rows: list[dict[str, Any]] | None = None,
    patchedit_comparison_csv_path: str = "",
    patchedit_comparison_rows: list[dict[str, Any]] | None = None,
    objective_abs_tol: float = DEFAULT_OBJECTIVE_ABS_TOL,
    objective_rel_tol: float = DEFAULT_OBJECTIVE_REL_TOL,
) -> dict[str, Any]:
    if main_comparison_rows is None:
        main_comparison_rows = _summarize_main_family_comparison(rows)
    if selector_improvement_rows is None:
        selector_improvement_rows = _summarize_selector_objective_improvement(rows)
    if patchedit_comparison_rows is None:
        patchedit_comparison_rows = _summarize_patchedit_auto_vs_scratch(
            rows,
            selector_improvement_rows=selector_improvement_rows,
        )
    return {
        "by_model": _summarize(rows, "model"),
        "by_prompt": _summarize(rows, "prompt"),
        "by_run_family": _summarize(rows, "run_family"),
        "by_run_family_model": _summarize_multi(rows, ["run_family", "model"]),
        "csv": csv_path,
        "main_comparison_csv": main_comparison_csv_path,
        "main_comparison_rows": main_comparison_rows,
        "selector_objective_improvement_csv": selector_improvement_csv_path,
        "selector_objective_improvement_rows": selector_improvement_rows,
        "patchedit_auto_vs_scratch_csv": patchedit_comparison_csv_path,
        "patchedit_auto_vs_scratch_rows": patchedit_comparison_rows,
        "objective_tolerance": {
            "abs_tol": objective_abs_tol,
            "rel_tol": objective_rel_tol,
        },
        "total": {"all": _summarize_group(rows)},
    }


def _summarize_main_family_comparison(
    rows: list[dict[str, Any]],
    *,
    candidate_run_family: str = DEFAULT_MAIN_RUN_FAMILY,
    baseline_run_family: str = DEFAULT_MAIN_BASELINE_RUN_FAMILY,
) -> list[dict[str, Any]]:
    dimensions: list[tuple[str, list[str]]] = [
        ("overall", []),
        ("model", ["model"]),
        ("prompt", ["prompt"]),
        ("instance", ["instance"]),
    ]
    comparison_rows: list[dict[str, Any]] = []
    for group_type, fields in dimensions:
        if fields:
            groups = sorted(
                {
                    tuple(str(row.get(field) or "") for field in fields)
                    for row in rows
                    if row.get("run_family") in {candidate_run_family, baseline_run_family}
                }
            )
        else:
            groups = [("all",)]
        for group in groups:
            if fields:
                grouped_rows = [
                    row
                    for row in rows
                    if tuple(str(row.get(field) or "") for field in fields) == group
                ]
                group_label = " / ".join(group)
            else:
                grouped_rows = list(rows)
                group_label = "all"
            candidate = _summarize_group(
                [row for row in grouped_rows if row.get("run_family") == candidate_run_family]
            )
            baseline = _summarize_group(
                [row for row in grouped_rows if row.get("run_family") == baseline_run_family]
            )
            if candidate["total"] == 0 and baseline["total"] == 0:
                continue
            comparison_rows.append(
                {
                    "group_type": group_type,
                    "group": group_label,
                    "candidate_run_family": candidate_run_family,
                    "baseline_run_family": baseline_run_family,
                    "candidate_total": candidate["total"],
                    "candidate_ok": candidate["ok"],
                    "candidate_objective_close": candidate["objective_close"],
                    "candidate_objective_close_rate": candidate["objective_close_rate"],
                    "candidate_operation_match": candidate["operation_match"],
                    "candidate_operation_match_rate": candidate["operation_match_rate"],
                    "baseline_total": baseline["total"],
                    "baseline_ok": baseline["ok"],
                    "baseline_objective_close": baseline["objective_close"],
                    "baseline_objective_close_rate": baseline["objective_close_rate"],
                    "baseline_operation_match": baseline["operation_match"],
                    "baseline_operation_match_rate": baseline["operation_match_rate"],
                    "objective_close_rate_delta": _rate_delta(
                        candidate["objective_close_rate"],
                        baseline["objective_close_rate"],
                    ),
                    "operation_match_rate_delta": _rate_delta(
                        candidate["operation_match_rate"],
                        baseline["operation_match_rate"],
                    ),
                }
            )
    return comparison_rows


def _summarize_patchedit_auto_vs_scratch(
    rows: list[dict[str, Any]],
    *,
    selector_improvement_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    stats_by_family_model: dict[tuple[str, str], dict[str, Any]] = {}
    for row in _summarize_multi(rows, ["run_family", "model"]):
        stats_by_family_model[(str(row["run_family"]), str(row["model"]))] = row
    selector_by_model = {str(row.get("model") or ""): row for row in selector_improvement_rows}
    models = sorted(
        {
            str(row.get("model") or "")
            for row in rows
            if row.get("run_family") in {DEFAULT_SELECTOR_RUN_FAMILY, DEFAULT_SELECTOR_BASELINE_RUN_FAMILY}
        }
    )

    comparison_rows: list[dict[str, Any]] = []
    for model in models:
        auto = stats_by_family_model.get((DEFAULT_SELECTOR_RUN_FAMILY, model), {})
        scratch = stats_by_family_model.get((DEFAULT_SELECTOR_BASELINE_RUN_FAMILY, model), {})
        selector = selector_by_model.get(model, {})
        comparison_rows.append(
            {
                "model": model,
                "auto_total": auto.get("total", 0),
                "auto_ok": auto.get("ok", 0),
                "auto_operation_match": auto.get("operation_match", 0),
                "auto_operation_mismatch": auto.get("operation_mismatch", 0),
                "auto_operation_unavailable": auto.get("operation_unavailable", 0),
                "auto_operation_match_rate": auto.get("operation_match_rate"),
                "auto_objective_close": auto.get("objective_close", 0),
                "auto_objective_close_rate_ok_only": auto.get("objective_close_rate_ok_only"),
                "scratch_total": scratch.get("total", 0),
                "scratch_ok": scratch.get("ok", 0),
                "scratch_operation_match": scratch.get("operation_match", 0),
                "scratch_operation_mismatch": scratch.get("operation_mismatch", 0),
                "scratch_operation_unavailable": scratch.get("operation_unavailable", 0),
                "scratch_operation_match_rate": scratch.get("operation_match_rate"),
                "scratch_objective_close": scratch.get("objective_close", 0),
                "scratch_objective_close_rate_ok_only": scratch.get("objective_close_rate_ok_only"),
                "selector_pairs": selector.get("paired_runs", 0),
                "selector_operation_correct_assessable_pairs": selector.get(
                    "operation_correct_assessable_pairs",
                    0,
                ),
                "selector_operation_correct_assessed_improvements": selector.get(
                    "operation_correct_assessed_improvements",
                    0,
                ),
                "selector_operation_correct_assessed_improvement_rate": selector.get(
                    "operation_correct_assessed_improvement_rate",
                ),
                "selector_operation_correct_mean_rel_improvement_vs_baseline": selector.get(
                    "operation_correct_mean_rel_improvement_vs_baseline",
                ),
                "selector_operation_correct_median_rel_improvement_vs_baseline": selector.get(
                    "operation_correct_median_rel_improvement_vs_baseline",
                ),
            }
        )
    return comparison_rows


def _summarize_selector_objective_improvement(
    rows: list[dict[str, Any]],
    *,
    candidate_run_family: str = DEFAULT_SELECTOR_RUN_FAMILY,
    baseline_run_family: str = DEFAULT_SELECTOR_BASELINE_RUN_FAMILY,
) -> list[dict[str, Any]]:
    baseline_by_setting = {
        _selector_setting_key(row): row
        for row in rows
        if row.get("run_family") == baseline_run_family
    }
    grouped: dict[tuple[str, str], list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    for row in rows:
        if row.get("run_family") != candidate_run_family:
            continue
        baseline = baseline_by_setting.get(_selector_setting_key(row))
        if baseline is None:
            continue
        grouped[(candidate_run_family, str(row.get("model") or ""))].append((row, baseline))

    summary_rows: list[dict[str, Any]] = []
    for (run_family, model), pairs in sorted(grouped.items()):
        deltas: list[float] = []
        relative_improvements: list[float] = []
        operation_correct_deltas: list[float] = []
        operation_correct_relative_improvements: list[float] = []
        candidate_missing_objective_runs = 0
        baseline_missing_objective_runs = 0
        candidate_operation_correct_pairs = 0
        operation_correct_scratch_no_incumbent_wins = 0

        for candidate, baseline in pairs:
            candidate_objective = _finite_float(candidate.get("reopt_objective"))
            baseline_objective = _finite_float(baseline.get("reopt_objective"))
            candidate_operation_correct = _is_operation_correct(candidate)
            if candidate_operation_correct:
                candidate_operation_correct_pairs += 1
            if candidate_objective is None:
                candidate_missing_objective_runs += 1
            if baseline_objective is None:
                baseline_missing_objective_runs += 1
            if (
                candidate_operation_correct
                and candidate_objective is not None
                and baseline_objective is None
                and _is_operation_correct(baseline)
                and _is_no_incumbent_failure(baseline)
            ):
                operation_correct_scratch_no_incumbent_wins += 1
            if candidate_objective is None or baseline_objective is None:
                continue

            delta = candidate_objective - baseline_objective
            relative_improvement = _relative_objective_improvement(
                candidate_objective=candidate_objective,
                baseline_objective=baseline_objective,
            )
            deltas.append(delta)
            if relative_improvement is not None:
                relative_improvements.append(relative_improvement)
            if candidate_operation_correct:
                operation_correct_deltas.append(delta)
                if relative_improvement is not None:
                    operation_correct_relative_improvements.append(relative_improvement)

        paired_runs = len(pairs)
        objective_comparable_pairs = len(deltas)
        operation_correct_objective_pairs = len(operation_correct_deltas)
        operation_correct_improved_runs = sum(1 for delta in operation_correct_deltas if delta < -1e-4)
        operation_correct_assessable_pairs = (
            operation_correct_objective_pairs + operation_correct_scratch_no_incumbent_wins
        )
        operation_correct_assessed_improvements = (
            operation_correct_improved_runs + operation_correct_scratch_no_incumbent_wins
        )
        summary_rows.append(
            {
                "run_family": run_family,
                "model": model,
                "selector_baseline_run_family": baseline_run_family,
                "paired_runs": paired_runs,
                "objective_comparable_pairs": objective_comparable_pairs,
                "candidate_missing_objective_runs": candidate_missing_objective_runs,
                "baseline_missing_objective_runs": baseline_missing_objective_runs,
                "missing_objective_pairs": paired_runs - objective_comparable_pairs,
                "candidate_operation_correct_pairs": candidate_operation_correct_pairs,
                "operation_correct_objective_pairs": operation_correct_objective_pairs,
                "improved_runs": sum(1 for delta in deltas if delta < -1e-4),
                "tied_runs": sum(1 for delta in deltas if abs(delta) <= 1e-4),
                "worsened_runs": sum(1 for delta in deltas if delta > 1e-4),
                "improvement_rate": _rate(sum(1 for delta in deltas if delta < -1e-4), objective_comparable_pairs),
                "mean_rel_improvement_vs_baseline": _mean(relative_improvements),
                "median_rel_improvement_vs_baseline": _median(relative_improvements),
                "operation_correct_improved_runs": sum(
                    1 for delta in operation_correct_deltas if delta < -1e-4
                ),
                "operation_correct_tied_runs": sum(
                    1 for delta in operation_correct_deltas if abs(delta) <= 1e-4
                ),
                "operation_correct_worsened_runs": sum(
                    1 for delta in operation_correct_deltas if delta > 1e-4
                ),
                "operation_correct_improvement_rate": _rate(
                    operation_correct_improved_runs,
                    operation_correct_objective_pairs,
                ),
                "operation_correct_mean_rel_improvement_vs_baseline": _mean(
                    operation_correct_relative_improvements
                ),
                "operation_correct_median_rel_improvement_vs_baseline": _median(
                    operation_correct_relative_improvements
                ),
                "operation_correct_scratch_no_incumbent_wins": operation_correct_scratch_no_incumbent_wins,
                "operation_correct_assessable_pairs": operation_correct_assessable_pairs,
                "operation_correct_assessed_improvements": operation_correct_assessed_improvements,
                "operation_correct_assessed_improvement_rate": _rate(
                    operation_correct_assessed_improvements,
                    operation_correct_assessable_pairs,
                ),
            }
        )
    return summary_rows


def _selector_setting_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("model") or ""),
        str(row.get("instance") or ""),
        str(row.get("prompt") or ""),
    )


def _is_operation_correct(row: dict[str, Any]) -> bool:
    value = row.get("operation_correct")
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    return row.get("operation_verdict") == "match"


def _is_no_incumbent_failure(row: dict[str, Any]) -> bool:
    haystack = " ".join(
        str(row.get(field) or "")
        for field in ["failure_label", "failure_stage", "failure_detail"]
    ).lower()
    return "no incumbent" in haystack


def _relative_objective_improvement(
    *,
    candidate_objective: float,
    baseline_objective: float,
) -> float | None:
    denominator = abs(baseline_objective)
    if denominator <= 0:
        return None
    return (baseline_objective - candidate_objective) / denominator


def _summarize(rows: list[dict[str, Any]], field: str) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(field) or "")].append(row)
    return {key: _summarize_group(grouped[key]) for key in sorted(grouped)}


def _summarize_multi(rows: list[dict[str, Any]], fields: list[str]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[tuple(str(row.get(field) or "") for field in fields)].append(row)

    summary_rows: list[dict[str, Any]] = []
    for key in sorted(grouped):
        summary_row = {field: value for field, value in zip(fields, key)}
        summary_row.update(_summarize_group(grouped[key]))
        summary_rows.append(summary_row)
    return summary_rows


def _summarize_group(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    ok = sum(1 for row in rows if row.get("run_status") == "ok")
    match = sum(1 for row in rows if row.get("operation_verdict") == "match")
    match_ok = sum(
        1 for row in rows if row.get("run_status") == "ok" and row.get("operation_verdict") == "match"
    )
    mismatch = sum(1 for row in rows if row.get("operation_verdict") == "mismatch")
    mismatch_ok = sum(
        1 for row in rows if row.get("run_status") == "ok" and row.get("operation_verdict") == "mismatch"
    )
    unavailable = total - match - mismatch
    exact = sum(1 for row in rows if row.get("exact_objective") == "True")
    close = sum(1 for row in rows if row.get("objective_close") == "True")
    objective_evaluable = sum(1 for row in rows if row.get("exact_objective") in {"True", "False"})
    return {
        "objective_close": close,
        "objective_close_rate": _rate(close, total),
        "objective_close_rate_ok_only": _rate(close, ok),
        "objective_evaluable": objective_evaluable,
        "objective_exact": exact,
        "objective_exact_rate": _rate(exact, total),
        "objective_exact_rate_ok_only": _rate(exact, ok),
        "ok": ok,
        "operation_match": match,
        "operation_match_ok": match_ok,
        "operation_match_rate": _rate(match, total),
        "operation_match_rate_ok_only": _rate(match_ok, ok),
        "operation_mismatch": mismatch,
        "operation_mismatch_ok": mismatch_ok,
        "operation_unavailable": unavailable,
        "total": total,
    }


def _rate(count: int, denom: int) -> float | None:
    if denom <= 0:
        return None
    return count / denom


def _rate_delta(candidate_rate: Any, baseline_rate: Any) -> float | None:
    if candidate_rate is None or baseline_rate is None:
        return None
    try:
        return float(candidate_rate) - float(baseline_rate)
    except (TypeError, ValueError):
        return None


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    sorted_values = sorted(values)
    midpoint = len(sorted_values) // 2
    if len(sorted_values) % 2:
        return sorted_values[midpoint]
    return (sorted_values[midpoint - 1] + sorted_values[midpoint]) / 2.0


def _update_results_markdown(path: Path, summary: dict[str, Any]) -> None:
    text = path.read_text(encoding="utf-8")
    text = _clarify_interval_fallback_correctness(text)
    text = _replace_main_summary_tables(text, summary)
    text = _replace_or_insert_main_comparison_section(text, summary)
    text = _replace_or_insert_patchedit_comparison_section(text, summary)
    text = _remove_selector_section(text)
    text = _replace_or_insert_operation_section(text, summary)
    path.write_text(text, encoding="utf-8")


def _clarify_interval_fallback_correctness(text: str) -> str:
    diagnostic_sentence = (
        "Interval/objective compatibility is retained as a secondary bound-consistency diagnostic, "
        "not an exact operation match."
    )
    text = re.sub(
        r"Evaluation used cached 3600s ground-truth objective summaries and skipped the live semantic/reference evaluator\."
        r"(?: The top summary tables .*?\.)?",
        "Evaluation used cached 3600s ground-truth objective summaries and skipped the live semantic/reference evaluator. "
        f"The top summary tables prioritize the 5% objective-match rate. {diagnostic_sentence}",
        text,
    )
    text = re.sub(rf"(?: {re.escape(diagnostic_sentence)})+", "", text)
    text = text.replace(
        "The top summary tables prioritize the 5% objective-match rate.",
        f"The top summary tables prioritize the 5% objective-match rate. {diagnostic_sentence}",
        1,
    )
    replacements = {
        "correct runs": "interval-compatible runs",
        "correctness": "interval compatibility",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def _replace_main_summary_tables(text: str, summary: dict[str, Any]) -> str:
    interval = _extract_interval_summary(text)
    replacements = [
        ("Aggregate", _render_main_aggregate_section(summary, interval)),
        ("By Run Family", _render_main_group_section("By Run Family", ["run family"], summary["by_run_family"], interval)),
        ("By Model", _render_main_group_section("By Model", ["model"], summary["by_model"], interval)),
        (
            "By Run Family And Model",
            _render_main_group_rows_section(
                "By Run Family And Model",
                ["run family", "model"],
                summary["by_run_family_model"],
                interval,
            ),
        ),
    ]
    for heading, replacement in replacements:
        pattern = re.compile(rf"\n## {re.escape(heading)}\n.*?(?=\n## |\Z)", flags=re.DOTALL)
        if pattern.search(text):
            text = pattern.sub("\n" + replacement + "\n", text)
    return text


def _extract_interval_summary(text: str) -> dict[tuple[str, ...], tuple[str, str]]:
    extracted: dict[tuple[str, ...], tuple[str, str]] = {}
    section_patterns = {
        ("Aggregate",): r"\n## Aggregate\n(?P<body>.*?)(?=\n## |\Z)",
        ("By Run Family",): r"\n## By Run Family\n(?P<body>.*?)(?=\n## |\Z)",
        ("By Model",): r"\n## By Model\n(?P<body>.*?)(?=\n## |\Z)",
        ("By Run Family And Model",): r"\n## By Run Family And Model\n(?P<body>.*?)(?=\n## |\Z)",
    }
    for section_key, pattern in section_patterns.items():
        match = re.search(pattern, text, flags=re.DOTALL)
        if not match:
            continue
        for line in match.group("body").splitlines():
            cells = _markdown_cells(line)
            if not cells or cells[0] in {"---:", "---", ":---"}:
                continue
            if section_key == ("Aggregate",) and len(cells) >= 4 and cells[0].isdigit():
                extracted[section_key] = (cells[-2], cells[-1])
            elif section_key == ("By Run Family",) and len(cells) >= 5 and cells[0].startswith("`"):
                extracted[(section_key[0], _strip_ticks(cells[0]))] = (cells[-2], cells[-1])
            elif section_key == ("By Model",) and len(cells) >= 5 and cells[0].startswith("`"):
                extracted[(section_key[0], _strip_ticks(cells[0]))] = (cells[-2], cells[-1])
            elif section_key == ("By Run Family And Model",) and len(cells) >= 6 and cells[0].startswith("`"):
                extracted[(section_key[0], _strip_ticks(cells[0]), _strip_ticks(cells[1]))] = (
                    cells[-2],
                    cells[-1],
                )
    return extracted


def _render_main_aggregate_section(
    summary: dict[str, Any],
    interval: dict[tuple[str, ...], tuple[str, str]],
) -> str:
    stats = summary["total"]["all"]
    interval_count, interval_rate = interval.get(("Aggregate",), ("-", "-"))
    return "\n".join(
        [
            "## Aggregate",
            "",
            "| total runs | completed runs | 5% obj matches | 5% obj match rate | interval-compatible runs | interval compatibility |",
            "| ---: | ---: | ---: | ---: | ---: | ---: |",
            "| "
            + " | ".join(
                [
                    str(stats["total"]),
                    str(stats["ok"]),
                    str(stats["objective_close"]),
                    _percent(stats["objective_close"], stats["total"]),
                    interval_count,
                    interval_rate,
                ]
            )
            + " |",
        ]
    )


def _render_main_group_section(
    heading: str,
    labels: list[str],
    groups: dict[str, dict[str, Any]],
    interval: dict[tuple[str, ...], tuple[str, str]],
) -> str:
    lines = [
        f"## {heading}",
        "",
        "| "
        + " | ".join(
            labels
            + [
                "total runs",
                "completed runs",
                "5% obj matches",
                "5% obj match rate",
                "interval-compatible runs",
                "interval compatibility",
            ]
        )
        + " |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for key, stats in groups.items():
        interval_count, interval_rate = interval.get((heading, key), ("-", "-"))
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{key}`",
                    str(stats["total"]),
                    str(stats["ok"]),
                    str(stats["objective_close"]),
                    _percent(stats["objective_close"], stats["total"]),
                    interval_count,
                    interval_rate,
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def _render_main_group_rows_section(
    heading: str,
    labels: list[str],
    rows: list[dict[str, Any]],
    interval: dict[tuple[str, ...], tuple[str, str]],
) -> str:
    lines = [
        f"## {heading}",
        "",
        "| "
        + " | ".join(
            labels
            + [
                "total runs",
                "completed runs",
                "5% obj matches",
                "5% obj match rate",
                "interval-compatible runs",
                "interval compatibility",
            ]
        )
        + " |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        run_family = str(row["run_family"])
        model = str(row["model"])
        interval_count, interval_rate = interval.get((heading, run_family, model), ("-", "-"))
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{run_family}`",
                    f"`{model}`",
                    str(row["total"]),
                    str(row["ok"]),
                    str(row["objective_close"]),
                    _percent(row["objective_close"], row["total"]),
                    interval_count,
                    interval_rate,
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def _markdown_cells(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return []
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def _strip_ticks(value: str) -> str:
    return value.strip().strip("`")


def _replace_or_insert_main_comparison_section(text: str, summary: dict[str, Any]) -> str:
    section = _render_main_comparison_section(summary)
    if not section:
        return text
    pattern = re.compile(r"\n## Main Comparison: Patchedit Auto Vs Codeedit Auto\n.*?(?=\n## |\Z)", flags=re.DOTALL)
    if pattern.search(text):
        return pattern.sub("\n" + section + "\n", text)

    insert_at = (
        re.search(r"\n## Selector Ablation: Patchedit Auto Vs Scratch\b", text)
        or re.search(r"\n## Patchedit Auto Vs Scratch\b", text)
        or re.search(r"\n## Selector Improvement\b", text)
        or re.search(r"\n## Operation Semantics\b", text)
    )
    if insert_at:
        return text[: insert_at.start()] + "\n\n" + section + "\n" + text[insert_at.start() :]
    return text.rstrip() + "\n\n" + section + "\n"


def _render_main_comparison_section(summary: dict[str, Any]) -> str:
    rows = summary.get("main_comparison_rows") or []
    if not rows:
        return ""
    first = rows[0]
    candidate = first.get("candidate_run_family") or DEFAULT_MAIN_RUN_FAMILY
    baseline = first.get("baseline_run_family") or DEFAULT_MAIN_BASELINE_RUN_FAMILY
    lines = [
        "## Main Comparison: Patchedit Auto Vs Codeedit Auto",
        "",
        f"The primary method comparison is `{candidate}` against `{baseline}`. "
        "The table keeps completion, objective quality, and operation semantics separate. "
        "The objective metric is the priority 5% reference-objective match rate, counted over all runs.",
        "",
    ]
    section_titles = {
        "overall": "Overall",
        "model": "By Model",
        "prompt": "By Prompt",
        "instance": "By Instance",
    }
    for group_type in ("overall", "model", "prompt", "instance"):
        group_rows = [row for row in rows if row.get("group_type") == group_type]
        if not group_rows:
            continue
        lines.extend(
            [
                f"### {section_titles[group_type]}",
                "",
                "| group | patchedit completed | patchedit 5% obj | patchedit op | codeedit completed | codeedit 5% obj | codeedit op | 5% obj rate gap | op rate gap |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for row in group_rows:
            lines.append(_main_comparison_row(row))
        lines.append("")
    return "\n".join(lines).rstrip()


def _main_comparison_row(row: dict[str, Any]) -> str:
    candidate_total = int(row.get("candidate_total") or 0)
    baseline_total = int(row.get("baseline_total") or 0)
    cells = [
        f"`{row.get('group') or ''}`",
        f"{row.get('candidate_ok') or 0} / {candidate_total}",
        _count_rate_cell(row.get("candidate_objective_close"), candidate_total),
        _count_rate_cell(row.get("candidate_operation_match"), candidate_total),
        f"{row.get('baseline_ok') or 0} / {baseline_total}",
        _count_rate_cell(row.get("baseline_objective_close"), baseline_total),
        _count_rate_cell(row.get("baseline_operation_match"), baseline_total),
        _signed_percent(row.get("objective_close_rate_delta")),
        _signed_percent(row.get("operation_match_rate_delta")),
    ]
    return "| " + " | ".join(cells) + " |"


def _count_rate_cell(count: Any, total: int) -> str:
    int_count = int(count or 0)
    return f"{int_count} / {total} ({_percent(int_count, total)})"


def _replace_or_insert_patchedit_comparison_section(text: str, summary: dict[str, Any]) -> str:
    section = _render_patchedit_comparison_section(summary)
    if not section:
        return text
    pattern = re.compile(
        r"\n## (?:Selector Ablation: Patchedit Auto Vs Scratch|Patchedit Auto Vs Scratch)\n.*?(?=\n## |\Z)",
        flags=re.DOTALL,
    )
    if pattern.search(text):
        return pattern.sub("\n" + section + "\n", text)

    insert_at = re.search(r"\n## Selector Improvement\b", text) or re.search(r"\n## Operation Semantics\b", text)
    if insert_at:
        return text[: insert_at.start()] + "\n\n" + section + "\n" + text[insert_at.start() :]
    return text.rstrip() + "\n\n" + section + "\n"


def _render_patchedit_comparison_section(summary: dict[str, Any]) -> str:
    rows = summary.get("patchedit_auto_vs_scratch_rows") or []
    if not rows:
        return ""
    lines = [
        "## Selector Ablation: Patchedit Auto Vs Scratch",
        "",
        "`patchedit_auto` versus `patchedit_scratch` is an ablation on the effect of the LLM "
        "reoptimization technique selector. It is not the main method comparison. "
        "The ablation keeps completion, operation correctness, and objective quality separate: "
        "5% objective matches count completed runs within the objective tolerance, operation match "
        "rates are per run family, and assessed auto-better cases are counted pairwise and include "
        "operation-correct auto runs where scratch had a correct operation but no incumbent.",
        "",
        "Interpretation: the auto selector consistently beats scratch once we restrict to "
        "operation-correct pairs. Scratch can often identify the right operation, but it is weaker "
        "at turning that operation into a usable incumbent and a competitive objective. Relative "
        "objective improvement is computed only when both sides have finite objectives; scratch "
        "no-incumbent wins are counted in the assessed auto-better rate, not in the relative objective columns.",
        "",
        "| model | auto completed | auto 5% obj match | auto op match | scratch completed | scratch 5% obj match | scratch op match | operation-correct assessed pairs | assessed auto-better cases | assessed auto-better rate | mean relative obj improvement | median relative obj improvement |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{row.get('model') or ''}`",
                    f"{row.get('auto_ok') or 0} / {row.get('auto_total') or 0}",
                    f"{row.get('auto_objective_close') or 0} / {row.get('auto_ok') or 0}",
                    f"{row.get('auto_operation_match') or 0} / {row.get('auto_total') or 0}",
                    f"{row.get('scratch_ok') or 0} / {row.get('scratch_total') or 0}",
                    f"{row.get('scratch_objective_close') or 0} / {row.get('scratch_ok') or 0}",
                    f"{row.get('scratch_operation_match') or 0} / {row.get('scratch_total') or 0}",
                    str(row.get("selector_operation_correct_assessable_pairs") or 0),
                    str(row.get("selector_operation_correct_assessed_improvements") or 0),
                    _optional_percent(row.get("selector_operation_correct_assessed_improvement_rate")),
                    _optional_percent(row.get("selector_operation_correct_mean_rel_improvement_vs_baseline")),
                    _optional_percent(row.get("selector_operation_correct_median_rel_improvement_vs_baseline")),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def _remove_selector_section(text: str) -> str:
    pattern = re.compile(r"\n## Selector Improvement\n.*?(?=\n## |\Z)", flags=re.DOTALL)
    return pattern.sub("", text)


def _replace_or_insert_operation_section(text: str, summary: dict[str, Any]) -> str:
    section = _render_operation_section(summary)
    pattern = re.compile(r"\n## Operation Semantics\n.*?(?=\n## |\Z)", flags=re.DOTALL)
    if pattern.search(text):
        return pattern.sub("\n" + section + "\n", text)

    insert_at = re.search(r"\n## Main Failure Modes\b", text)
    if insert_at:
        return text[: insert_at.start()] + "\n\n" + section + "\n" + text[insert_at.start() :]
    return text.rstrip() + "\n\n" + section + "\n"


def _render_operation_section(summary: dict[str, Any]) -> str:
    total = summary["total"]["all"]
    tolerance = summary.get("objective_tolerance") or {}
    abs_tol = float(tolerance.get("abs_tol", DEFAULT_OBJECTIVE_ABS_TOL))
    rel_tol = float(tolerance.get("rel_tol", DEFAULT_OBJECTIVE_REL_TOL))
    lines = [
        "## Operation Semantics",
        "",
        "Operation-level semantic correctness is separate from objective/schedule matching. "
        "For `codeedit`, recognized solver diffs are projected into structured patches before "
        "the protected exam surface is compared against the expected prompt effect; reserved-slot "
        "projection uses the concrete slot grounded from the diff when available.",
        "",
        "| metric | count / total | rate | count / ok | ok-only rate |",
        "| --- | ---: | ---: | ---: | ---: |",
        _metric_row(
            "operation-level semantic match",
            total["operation_match"],
            total,
            ok_count=total.get("operation_match_ok"),
        ),
        _metric_row(
            "operation-level mismatch",
            total["operation_mismatch"],
            total,
            ok_count=total.get("operation_mismatch_ok"),
        ),
        _metric_row(
            "operation unavailable or nonprojectable",
            total["operation_unavailable"],
            total,
            ok_only=False,
        ),
        "",
        "### By Run Family",
        "",
        "| run family | total | ok | match | mismatch | unavailable | match rate | ok-only match rate |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for run_family, stats in summary.get("by_run_family", {}).items():
        lines.append(_operation_group_row([f"`{run_family}`"], stats))

    lines.extend(
        [
            "",
            "### By Run Family And Model",
            "",
            "| run family | model | total | ok | match | mismatch | unavailable | match rate | ok-only match rate |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in summary.get("by_run_family_model", []):
        lines.append(_operation_group_row([f"`{row['run_family']}`", f"`{row['model']}`"], row))
    lines.extend(
        [
            "",
            "### Objective Matching",
            "",
            "Exact objective uses `abs(candidate - reference) <= 1e-4`. "
            f"Close objective is the priority objective-quality rate and uses "
            f"`abs(candidate - reference) <= max({abs_tol:g}, "
            f"{100.0 * rel_tol:g}% * |reference|)`.",
            "",
            "| run family | model | total | completed | exact objective | within tolerance objective | exact completed-only | tolerance completed-only |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in summary.get("by_run_family_model", []):
        lines.append(_objective_group_row([f"`{row['run_family']}`", f"`{row['model']}`"], row))
    return "\n".join(lines)


def _operation_group_row(labels: list[str], stats: dict[str, Any]) -> str:
    cells = [
        *labels,
        str(stats["total"]),
        str(stats["ok"]),
        str(stats["operation_match"]),
        str(stats["operation_mismatch"]),
        str(stats["operation_unavailable"]),
        _percent(stats["operation_match"], stats["total"]),
        _percent(stats.get("operation_match_ok", 0), stats["ok"]),
    ]
    return "| " + " | ".join(cells) + " |"


def _objective_group_row(labels: list[str], stats: dict[str, Any]) -> str:
    cells = [
        *labels,
        str(stats["total"]),
        str(stats["ok"]),
        str(stats["objective_exact"]),
        str(stats["objective_close"]),
        _percent(stats["objective_exact"], stats["ok"]),
        _percent(stats["objective_close"], stats["ok"]),
    ]
    return "| " + " | ".join(cells) + " |"


def _metric_row(
    label: str,
    count: int,
    total: dict[str, Any],
    *,
    ok_only: bool = True,
    ok_count: int | None = None,
) -> str:
    total_count = int(total["total"])
    total_ok_count = int(total["ok"])
    total_rate = _percent(count, total_count)
    if ok_only:
        display_ok_count = count if ok_count is None else int(ok_count)
        ok_cell = f"{display_ok_count} / {total_ok_count}"
        ok_rate = _percent(display_ok_count, total_ok_count)
    else:
        ok_cell = "-"
        ok_rate = "-"
    return f"| {label} | {count} / {total_count} | {total_rate} | {ok_cell} | {ok_rate} |"


def _percent(count: int, denom: int) -> str:
    if denom <= 0:
        return "-"
    return f"{100.0 * count / denom:.2f}%"


def _optional_percent(value: Any) -> str:
    parsed = _finite_float(value)
    if parsed is None:
        return "-"
    return f"{100.0 * parsed:.2f}%"


def _signed_percent(value: Any) -> str:
    parsed = _finite_float(value)
    if parsed is None:
        return "-"
    return f"{100.0 * parsed:+.2f} pp"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build-manifest")
    build.add_argument("--report-dir", required=True)
    build.add_argument("--run-families", default="")
    build.add_argument("--output", required=True)
    build.set_defaults(func=build_manifest)

    eval_task = subparsers.add_parser("eval-array")
    eval_task.add_argument("--manifest", required=True)
    eval_task.add_argument("--output-dir", required=True)
    eval_task.add_argument("--task-id", type=int, default=None)
    eval_task.add_argument("--num-shards", type=int, default=None)
    eval_task.add_argument("--objective-abs-tol", type=float, default=DEFAULT_OBJECTIVE_ABS_TOL)
    eval_task.add_argument("--objective-rel-tol", type=float, default=DEFAULT_OBJECTIVE_REL_TOL)
    eval_task.set_defaults(func=eval_array_task)

    aggregate_parser = subparsers.add_parser("aggregate")
    aggregate_parser.add_argument("--manifest", required=True)
    aggregate_parser.add_argument("--eval-dir", required=True)
    aggregate_parser.add_argument("--report-dir", required=True)
    aggregate_parser.add_argument("--results-md", default="")
    aggregate_parser.add_argument("--allow-missing", action="store_true")
    aggregate_parser.add_argument("--objective-abs-tol", type=float, default=DEFAULT_OBJECTIVE_ABS_TOL)
    aggregate_parser.add_argument("--objective-rel-tol", type=float, default=DEFAULT_OBJECTIVE_REL_TOL)
    aggregate_parser.set_defaults(func=aggregate)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
