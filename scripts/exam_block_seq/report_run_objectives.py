#!/usr/bin/env python3
"""Generate per-run objective reports for exam campaigns against ground truth."""
from __future__ import annotations

from collections import defaultdict
import argparse
import csv
import json
import math
from pathlib import Path
import re
from typing import Any

from scripts.exam_block_seq.inspect_run_experiments import inspect_campaign


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GROUND_TRUTH_ROOT = REPO_ROOT / "outputs" / "solves" / "reference" / "3600s"
KNOWN_RUN_FAMILIES = (
    "patchedit_auto",
    "codeedit_auto",
    "patchedit_scratch",
)
DEFAULT_SELECTOR_BASELINE_RUN_FAMILY = "patchedit_scratch"
DEFAULT_SELECTOR_RUN_FAMILY = "patchedit_auto"
_CORRECT_REFERENCE_VERDICTS = {
    "exact_schedule_match",
    "exact_objective_match",
    "interval_consistent",
    "same_infeasibility",
}
BASE_SOL_OBJECTIVE_PATTERN = re.compile(r"^# Objective value = (?P<objective>[-+0-9.eE]+)$")
_GROUND_TRUTH_RUNTIME_UNSET = object()
_GROUND_TRUTH_RUNTIME: Any = _GROUND_TRUTH_RUNTIME_UNSET


def build_campaign_objective_report(
    campaign_root: str | Path,
    *,
    run_families: list[str] | None = None,
    ground_truth_root: str | Path = DEFAULT_GROUND_TRUTH_ROOT,
    output_dir: str | Path | None = None,
    include_semantic_ground_truth: bool = True,
    selector_run_family: str = DEFAULT_SELECTOR_RUN_FAMILY,
    selector_baseline_run_family: str = DEFAULT_SELECTOR_BASELINE_RUN_FAMILY,
) -> dict[str, Any]:
    root = _resolve_path(campaign_root)
    reports_root = root / "reports"
    merged_rows = _load_merged_rows(root)
    canonical_rows = _load_optional_csv(reports_root / "canonical_ground_truth_evaluation.csv", key_field="run_id")
    gt_root = _resolve_path(ground_truth_root)

    gt_cache: dict[tuple[str, str], dict[str, Any]] = {}
    base_objective_cache: dict[str, float | None] = {}
    detail_rows: list[dict[str, Any]] = []
    selected_run_families: set[str] = set()

    for merged_row in merged_rows:
        run_family = _infer_run_family(merged_row)
        if run_families and run_family not in run_families:
            continue
        selected_run_families.add(run_family)

        result_path = _resolve_result_path(root, merged_row.get("result_path"))
        payload = _load_json(result_path)
        instance = str(merged_row.get("instance") or payload.get("instance_id") or "").strip()
        prompt = str(merged_row.get("prompt") or payload.get("prompt_id") or "").strip()

        gt_key = (instance, prompt)
        if gt_key not in gt_cache:
            gt_cache[gt_key] = _load_ground_truth_summary_row(gt_root, instance, prompt)
        gt_row = gt_cache[gt_key]

        canonical_row = canonical_rows.get(str(merged_row.get("run_id") or "")) if canonical_rows else None
        semantic_eval = (
            _load_semantic_ground_truth_eval(
                result_payload=payload,
                result_path=result_path,
            )
            if include_semantic_ground_truth
            else {}
        )
        detail_rows.append(
            _build_detail_row(
                merged_row=merged_row,
                result_payload=payload,
                ground_truth_row=gt_row,
                canonical_row=canonical_row,
                semantic_eval=semantic_eval,
                run_family=run_family,
                result_path=result_path,
                base_objective_cache=base_objective_cache,
            )
        )

    detail_rows.sort(key=lambda row: (row["run_family"], row["model"], row["instance"], row["prompt"]))
    _annotate_selector_improvement(
        detail_rows,
        candidate_run_family=selector_run_family,
        baseline_run_family=selector_baseline_run_family,
    )
    selected = sorted(run_families or selected_run_families)
    prefix = "__".join(selected) if selected else "all_runs"
    report_root = _resolve_output_dir(root, output_dir, prefix)

    overall_rows = _summarize_correctness(detail_rows, group_fields=["run_family", "model"])
    by_prompt_rows = _summarize_correctness(detail_rows, group_fields=["run_family", "model", "prompt"])
    by_instance_rows = _summarize_correctness(detail_rows, group_fields=["run_family", "model", "instance"])
    semantic_overall_rows = _summarize_semantic_ground_truth(detail_rows, group_fields=["run_family", "model"])
    semantic_by_prompt_rows = _summarize_semantic_ground_truth(
        detail_rows,
        group_fields=["run_family", "model", "prompt"],
    )
    semantic_by_instance_rows = _summarize_semantic_ground_truth(
        detail_rows,
        group_fields=["run_family", "model", "instance"],
    )
    selector_improvement_rows = _summarize_selector_improvement(
        detail_rows,
        candidate_run_family=selector_run_family,
        baseline_run_family=selector_baseline_run_family,
        group_fields=["run_family", "model"],
    )
    failure_mode_rows = _summarize_failure_modes(detail_rows)

    report_paths = {
        "detail_csv": str(report_root / "detail.csv"),
        "overall_csv": str(report_root / "overall_correctness.csv"),
        "by_prompt_csv": str(report_root / "correctness_by_prompt.csv"),
        "by_instance_csv": str(report_root / "correctness_by_instance.csv"),
        "semantic_overall_csv": str(report_root / "semantic_correctness_overall.csv"),
        "semantic_by_prompt_csv": str(report_root / "semantic_correctness_by_prompt.csv"),
        "semantic_by_instance_csv": str(report_root / "semantic_correctness_by_instance.csv"),
        "selector_improvement_csv": str(report_root / "selector_improvement.csv"),
        "failure_modes_csv": str(report_root / "failure_modes.csv"),
        "summary_json": str(report_root / "summary.json"),
    }
    _write_csv(detail_rows, Path(report_paths["detail_csv"]))
    _write_csv(overall_rows, Path(report_paths["overall_csv"]))
    _write_csv(by_prompt_rows, Path(report_paths["by_prompt_csv"]))
    _write_csv(by_instance_rows, Path(report_paths["by_instance_csv"]))
    _write_csv(semantic_overall_rows, Path(report_paths["semantic_overall_csv"]))
    _write_csv(semantic_by_prompt_rows, Path(report_paths["semantic_by_prompt_csv"]))
    _write_csv(semantic_by_instance_rows, Path(report_paths["semantic_by_instance_csv"]))
    _write_csv(selector_improvement_rows, Path(report_paths["selector_improvement_csv"]))
    _write_csv(failure_mode_rows, Path(report_paths["failure_modes_csv"]))

    summary = {
        "campaign_root": str(root),
        "ground_truth_root": str(gt_root),
        "run_families": selected,
        "row_count": len(detail_rows),
        "correct_count": sum(1 for row in detail_rows if row["correct"] is True),
        "ok_count": sum(1 for row in detail_rows if row["run_status"] == "ok"),
        "canonical_eval_used": bool(canonical_rows),
        "semantic_ground_truth_used": include_semantic_ground_truth,
        "selector_run_family": selector_run_family,
        "selector_baseline_run_family": selector_baseline_run_family,
        "report_paths": report_paths,
        "overall_rows": overall_rows,
        "by_prompt_rows": by_prompt_rows,
        "by_instance_rows": by_instance_rows,
        "semantic_overall_rows": semantic_overall_rows,
        "semantic_by_prompt_rows": semantic_by_prompt_rows,
        "semantic_by_instance_rows": semantic_by_instance_rows,
        "selector_improvement_rows": selector_improvement_rows,
        "failure_mode_rows": failure_mode_rows,
    }
    Path(report_paths["summary_json"]).write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    summary["detail_rows"] = detail_rows
    return summary


def _build_detail_row(
    *,
    merged_row: dict[str, Any],
    result_payload: dict[str, Any],
    ground_truth_row: dict[str, Any],
    canonical_row: dict[str, Any] | None,
    semantic_eval: dict[str, Any],
    run_family: str,
    result_path: Path,
    base_objective_cache: dict[str, float | None],
) -> dict[str, Any]:
    run_status = str(merged_row.get("status") or result_payload.get("status") or "").strip()
    reopt_objective = _first_finite_number(result_payload.get("objective"), merged_row.get("objective"))
    reopt_obj_bound = _first_finite_number(result_payload.get("obj_bound"), merged_row.get("obj_bound"))
    reopt_mip_gap = _first_finite_number(result_payload.get("mip_gap"), merged_row.get("mip_gap"))
    base_objective = _resolve_base_objective(result_payload, cache=base_objective_cache)

    ground_truth_objective = _first_finite_number(ground_truth_row.get("objective"))
    ground_truth_obj_bound = _first_finite_number(ground_truth_row.get("obj_bound"))
    ground_truth_mip_gap = _first_finite_number(ground_truth_row.get("mip_gap"))

    candidate_lb, candidate_ub = _objective_interval(reopt_objective, reopt_obj_bound)
    reference_lb, reference_ub = _objective_interval(ground_truth_objective, ground_truth_obj_bound)
    correctness = _resolve_correctness(
        merged_row=merged_row,
        canonical_row=canonical_row,
        run_status=run_status,
        candidate_lb=candidate_lb,
        candidate_ub=candidate_ub,
        reference_lb=reference_lb,
        reference_ub=reference_ub,
    )
    semantic_verdict = str(semantic_eval.get("semantic_verdict") or "").strip()
    combined_overall_verdict = (
        _combine_overall_verdict(semantic_verdict=semantic_verdict, reference_verdict=correctness["reference_verdict"])
        if semantic_verdict
        else ""
    )

    return {
        "run_family": run_family,
        "run_family_label": run_family.replace("_", "-"),
        "model": str(merged_row.get("model") or result_payload.get("model") or ""),
        "planner_mode": str(merged_row.get("planner_mode") or result_payload.get("planner_mode") or ""),
        "instance": str(merged_row.get("instance") or result_payload.get("instance_id") or ""),
        "prompt": str(merged_row.get("prompt") or result_payload.get("prompt_id") or ""),
        "run_id": str(merged_row.get("run_id") or result_payload.get("run_id") or result_path.stem),
        "run_status": run_status,
        "reopt_objective": reopt_objective,
        "reopt_obj_bound": reopt_obj_bound,
        "reopt_mip_gap": reopt_mip_gap,
        "ground_truth_objective": ground_truth_objective,
        "ground_truth_obj_bound": ground_truth_obj_bound,
        "ground_truth_mip_gap": ground_truth_mip_gap,
        "base_objective": base_objective,
        "diff_vs_ground_truth_obj": _subtract_or_none(reopt_objective, ground_truth_objective),
        "diff_vs_base_obj": _subtract_or_none(reopt_objective, base_objective),
        "selector_baseline_run_family": "",
        "selector_baseline_run_id": "",
        "selector_baseline_status": "",
        "selector_baseline_objective": None,
        "selector_obj_delta_vs_baseline": None,
        "selector_rel_improvement_vs_baseline": None,
        "selector_improved_vs_baseline": None,
        "correct": correctness["correct"],
        "correctness_source": correctness["source"],
        "correctness_verdict": correctness["verdict"],
        "reference_verdict": correctness["reference_verdict"],
        "schedule_match": correctness["schedule_match"],
        "candidate_obj_lb": correctness["candidate_obj_lb"],
        "candidate_obj_ub": correctness["candidate_obj_ub"],
        "reference_obj_lb": correctness["reference_obj_lb"],
        "reference_obj_ub": correctness["reference_obj_ub"],
        "semantic_verdict": semantic_verdict,
        "semantic_reason": str(semantic_eval.get("semantic_reason") or ""),
        "structural_correct": _semantic_matches(semantic_verdict) if semantic_verdict else None,
        "ground_truth_eval_status": str(semantic_eval.get("ground_truth_eval_status") or ""),
        "combined_overall_verdict": combined_overall_verdict,
        "combined_matches_ground_truth": (
            _matches_from_combined_overall_verdict(combined_overall_verdict)
            if combined_overall_verdict
            else None
        ),
        "failure_stage": str(merged_row.get("failure_stage") or result_payload.get("failure_stage") or ""),
        "failure_label": str(merged_row.get("failure_label") or result_payload.get("failure_label") or ""),
        "failure_detail": str(merged_row.get("failure_detail") or result_payload.get("failure_detail") or ""),
        "result_path": str(result_path),
    }


def _resolve_correctness(
    *,
    merged_row: dict[str, Any],
    canonical_row: dict[str, Any] | None,
    run_status: str,
    candidate_lb: float | None,
    candidate_ub: float | None,
    reference_lb: float | None,
    reference_ub: float | None,
) -> dict[str, Any]:
    if canonical_row is not None:
        reference_verdict = str(canonical_row.get("reference_verdict") or "").strip() or "unresolved"
        return {
            "correct": _parse_bool(canonical_row.get("matches_canonical_ground_truth")),
            "source": "canonical_ground_truth_evaluation",
            "verdict": reference_verdict,
            "reference_verdict": reference_verdict,
            "schedule_match": _parse_optional_bool(canonical_row.get("schedule_match")),
            "candidate_obj_lb": _first_finite_number(canonical_row.get("candidate_obj_lb")),
            "candidate_obj_ub": _first_finite_number(canonical_row.get("candidate_obj_ub")),
            "reference_obj_lb": _first_finite_number(canonical_row.get("reference_obj_lb")),
            "reference_obj_ub": _first_finite_number(canonical_row.get("reference_obj_ub")),
        }

    reference_verdict = _fallback_reference_verdict(
        run_status=run_status,
        candidate_lb=candidate_lb,
        candidate_ub=candidate_ub,
        reference_lb=reference_lb,
        reference_ub=reference_ub,
    )
    return {
        "correct": reference_verdict in _CORRECT_REFERENCE_VERDICTS,
        "source": "interval_fallback",
        "verdict": reference_verdict,
        "reference_verdict": reference_verdict,
        "schedule_match": None,
        "candidate_obj_lb": candidate_lb,
        "candidate_obj_ub": candidate_ub,
        "reference_obj_lb": reference_lb,
        "reference_obj_ub": reference_ub,
    }


def _fallback_reference_verdict(
    *,
    run_status: str,
    candidate_lb: float | None,
    candidate_ub: float | None,
    reference_lb: float | None,
    reference_ub: float | None,
    objective_tol: float = 1e-4,
) -> str:
    if candidate_ub is not None and reference_ub is not None:
        if (
            candidate_lb is not None
            and reference_lb is not None
            and max(candidate_lb, reference_lb) <= min(candidate_ub, reference_ub) + objective_tol
        ):
            return "interval_consistent"
        return "contradicted"
    if candidate_ub is not None and reference_lb is not None and reference_lb > candidate_ub + objective_tol:
        return "contradicted"
    if reference_ub is not None and candidate_lb is not None and candidate_lb > reference_ub + objective_tol:
        return "contradicted"
    if run_status == "error":
        return "unresolved"
    return "unresolved"


def _objective_interval(objective: float | None, obj_bound: float | None) -> tuple[float | None, float | None]:
    if objective is None and obj_bound is None:
        return None, None
    if objective is None:
        return obj_bound, obj_bound
    if obj_bound is None:
        return objective, objective
    return min(objective, obj_bound), max(objective, obj_bound)


def _summarize_correctness(
    rows: list[dict[str, Any]],
    *,
    group_fields: list[str],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = tuple(row[field] for field in group_fields)
        grouped[key].append(row)

    summary_rows: list[dict[str, Any]] = []
    for key, group_rows in sorted(grouped.items()):
        correct_count = sum(1 for row in group_rows if row["correct"] is True)
        total_runs = len(group_rows)
        payload = {field: key[idx] for idx, field in enumerate(group_fields)}
        payload.update(
            {
                "total_runs": total_runs,
                "ok_runs": sum(1 for row in group_rows if row["run_status"] == "ok"),
                "correct_runs": correct_count,
                "correctness_rate": (correct_count / total_runs) if total_runs else 0.0,
            }
        )
        summary_rows.append(payload)
    return summary_rows


def _summarize_failure_modes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["run_status"] == "ok":
            continue
        key = (
            str(row["run_family"]),
            str(row["model"]),
            str(row["failure_stage"]),
            str(row["failure_label"]),
        )
        grouped[key].append(row)

    summary_rows: list[dict[str, Any]] = []
    for key, group_rows in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])):
        run_family, model, failure_stage, failure_label = key
        summary_rows.append(
            {
                "run_family": run_family,
                "run_family_label": run_family.replace("_", "-"),
                "model": model,
                "failure_stage": failure_stage,
                "failure_label": failure_label,
                "count": len(group_rows),
                "example_run_id": group_rows[0]["run_id"],
                "example_failure_detail": group_rows[0]["failure_detail"],
            }
        )
    return summary_rows


def _annotate_selector_improvement(
    rows: list[dict[str, Any]],
    *,
    candidate_run_family: str,
    baseline_run_family: str,
) -> None:
    baseline_by_setting: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        if row["run_family"] != baseline_run_family:
            continue
        key = _selector_setting_key(row)
        if key not in baseline_by_setting or row["run_status"] == "ok":
            baseline_by_setting[key] = row

    for row in rows:
        if row["run_family"] != candidate_run_family:
            continue
        baseline = baseline_by_setting.get(_selector_setting_key(row))
        if baseline is None:
            continue
        baseline_objective = _first_finite_number(baseline.get("reopt_objective"))
        candidate_objective = _first_finite_number(row.get("reopt_objective"))
        row["selector_baseline_run_family"] = baseline_run_family
        row["selector_baseline_run_id"] = baseline.get("run_id") or ""
        row["selector_baseline_status"] = baseline.get("run_status") or ""
        row["selector_baseline_objective"] = baseline_objective
        if candidate_objective is None or baseline_objective is None:
            continue
        delta = candidate_objective - baseline_objective
        relative_improvement = _relative_objective_improvement(
            candidate_objective=candidate_objective,
            baseline_objective=baseline_objective,
        )
        row["selector_obj_delta_vs_baseline"] = delta
        row["selector_rel_improvement_vs_baseline"] = relative_improvement
        row["selector_improved_vs_baseline"] = delta < -1e-4


def _selector_setting_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("model") or ""),
        str(row.get("instance") or ""),
        str(row.get("prompt") or ""),
    )


def _relative_objective_improvement(
    *,
    candidate_objective: float,
    baseline_objective: float,
) -> float | None:
    denominator = abs(baseline_objective)
    if denominator <= 0:
        return None
    return (baseline_objective - candidate_objective) / denominator


def _summarize_selector_improvement(
    rows: list[dict[str, Any]],
    *,
    candidate_run_family: str,
    baseline_run_family: str,
    group_fields: list[str],
) -> list[dict[str, Any]]:
    paired_rows = [
        row
        for row in rows
        if row["run_family"] == candidate_run_family and str(row.get("selector_baseline_run_id") or "")
    ]
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in paired_rows:
        key = tuple(row[field] for field in group_fields)
        grouped[key].append(row)

    summary_rows: list[dict[str, Any]] = []
    for key, group_rows in sorted(grouped.items()):
        comparable_rows = [
            row
            for row in group_rows
            if _first_finite_number(row.get("selector_obj_delta_vs_baseline")) is not None
        ]
        deltas = [
            float(row["selector_obj_delta_vs_baseline"])
            for row in comparable_rows
            if _first_finite_number(row.get("selector_obj_delta_vs_baseline")) is not None
        ]
        rel_improvements = [
            float(row["selector_rel_improvement_vs_baseline"])
            for row in comparable_rows
            if _first_finite_number(row.get("selector_rel_improvement_vs_baseline")) is not None
        ]
        payload = {field: key[idx] for idx, field in enumerate(group_fields)}
        paired_runs = len(group_rows)
        objective_comparable_pairs = len(deltas)
        improved_runs = sum(1 for delta in deltas if delta < -1e-4)
        tied_runs = sum(1 for delta in deltas if abs(delta) <= 1e-4)
        worsened_runs = sum(1 for delta in deltas if delta > 1e-4)
        payload.update(
            {
                "selector_baseline_run_family": baseline_run_family,
                "paired_runs": paired_runs,
                "objective_comparable_pairs": objective_comparable_pairs,
                "candidate_missing_objective_runs": sum(
                    1 for row in group_rows if _first_finite_number(row.get("reopt_objective")) is None
                ),
                "baseline_missing_objective_runs": sum(
                    1 for row in group_rows if _first_finite_number(row.get("selector_baseline_objective")) is None
                ),
                "missing_objective_pairs": paired_runs - objective_comparable_pairs,
                "improved_runs": improved_runs,
                "tied_runs": tied_runs,
                "worsened_runs": worsened_runs,
                "improvement_rate": (
                    improved_runs / objective_comparable_pairs if objective_comparable_pairs else 0.0
                ),
                "all_pair_improvement_rate": (improved_runs / paired_runs) if paired_runs else 0.0,
                "mean_obj_delta_vs_baseline": _mean(deltas),
                "mean_rel_improvement_vs_baseline": _mean(rel_improvements),
                "median_rel_improvement_vs_baseline": _median(rel_improvements),
            }
        )
        summary_rows.append(payload)
    return summary_rows


def _summarize_semantic_ground_truth(
    rows: list[dict[str, Any]],
    *,
    group_fields: list[str],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = tuple(row[field] for field in group_fields)
        grouped[key].append(row)

    summary_rows: list[dict[str, Any]] = []
    for key, group_rows in sorted(grouped.items()):
        total_runs = len(group_rows)
        payload = {field: key[idx] for idx, field in enumerate(group_fields)}
        structural_match_runs = sum(1 for row in group_rows if row["structural_correct"] is True)
        overall_match_runs = sum(1 for row in group_rows if row["combined_matches_ground_truth"] is True)
        payload.update(
            {
                "total_runs": total_runs,
                "structural_match_runs": structural_match_runs,
                "structural_mismatch_runs": sum(1 for row in group_rows if row["structural_correct"] is False),
                "structural_unavailable_runs": sum(1 for row in group_rows if row["semantic_verdict"] == "unavailable"),
                "structural_not_run_runs": sum(1 for row in group_rows if row["semantic_verdict"] == ""),
                "structural_match_rate": (structural_match_runs / total_runs) if total_runs else 0.0,
                "overall_match_runs": overall_match_runs,
                "overall_pass_runs": sum(1 for row in group_rows if row["combined_overall_verdict"] == "pass"),
                "overall_behavioral_pass_runs": sum(
                    1 for row in group_rows if row["combined_overall_verdict"] == "behavioral_pass"
                ),
                "overall_fail_runs": sum(1 for row in group_rows if row["combined_overall_verdict"] == "fail"),
                "overall_partial_runs": sum(1 for row in group_rows if row["combined_overall_verdict"] == "partial"),
                "overall_unresolved_runs": sum(
                    1 for row in group_rows if row["combined_overall_verdict"] == "unresolved"
                ),
                "overall_not_run_runs": sum(1 for row in group_rows if row["combined_overall_verdict"] == ""),
                "overall_match_rate": (overall_match_runs / total_runs) if total_runs else 0.0,
            }
        )
        summary_rows.append(payload)
    return summary_rows


def _infer_run_family(row: dict[str, Any]) -> str:
    haystacks = [
        str(row.get("run_id") or ""),
        str(row.get("result_path") or ""),
    ]
    for family in sorted(KNOWN_RUN_FAMILIES, key=len, reverse=True):
        for haystack in haystacks:
            if family in haystack:
                return family
    planner_mode = str(row.get("planner_mode") or "").strip()
    if planner_mode:
        return planner_mode
    return "unknown"


def _resolve_output_dir(campaign_root: Path, output_dir: str | Path | None, prefix: str) -> Path:
    if output_dir is None:
        path = campaign_root / "reports" / f"run_objective_report_{prefix}"
    else:
        path = _resolve_path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _load_merged_rows(campaign_root: Path) -> list[dict[str, Any]]:
    merged_csv = campaign_root / "reports" / "merged_runs.csv"
    if not merged_csv.exists():
        inspect_campaign(campaign_root)
    with merged_csv.open("r", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _load_optional_csv(path: Path, *, key_field: str) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    with path.open("r", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    return {str(row.get(key_field) or ""): row for row in rows if row.get(key_field)}


def _load_ground_truth_summary_row(ground_truth_root: Path, instance: str, prompt: str) -> dict[str, Any]:
    if not instance or not prompt:
        return {}
    summary_csv = ground_truth_root / instance / prompt / "summary.csv"
    if not summary_csv.exists():
        return {}
    with summary_csv.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        return next(reader, {}) or {}


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _resolve_base_objective(
    result_payload: dict[str, Any],
    *,
    cache: dict[str, float | None],
) -> float | None:
    direct = _first_finite_number(result_payload.get("base_objective"))
    if direct is not None:
        return direct

    input_payload = dict(result_payload.get("input") or {})
    config = dict(input_payload.get("config") or {})
    instance_dir_value = config.get("instance_dir")
    solution_dir_value = config.get("solution_dir")
    if instance_dir_value in {None, ""} or solution_dir_value in {None, ""}:
        return None

    instance_dir = Path(str(instance_dir_value)).expanduser()
    solution_dir = Path(str(solution_dir_value)).expanduser()
    solution_path = (solution_dir / f"{instance_dir.name}.sol").resolve()
    cache_key = str(solution_path)
    if cache_key not in cache:
        cache[cache_key] = _parse_base_solution_objective(solution_path)
    return cache[cache_key]


def _parse_base_solution_objective(solution_path: Path) -> float | None:
    if not solution_path.exists():
        return None
    with solution_path.open("r", encoding="utf-8") as fh:
        for raw_line in fh:
            match = BASE_SOL_OBJECTIVE_PATTERN.match(raw_line.strip())
            if match is not None:
                return float(match.group("objective"))
    return None


def _resolve_result_path(campaign_root: Path, result_path: Any) -> Path:
    path = Path(str(result_path or "")).expanduser()
    if not path.is_absolute():
        path = campaign_root / path
    return path.resolve()


def _resolve_path(path_value: str | Path) -> Path:
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def _first_finite_number(*values: Any) -> float | None:
    for value in values:
        if value in {None, ""}:
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(parsed):
            return parsed
    return None


def _subtract_or_none(lhs: float | None, rhs: float | None) -> float | None:
    if lhs is None or rhs is None:
        return None
    return lhs - rhs


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


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().lower()
    return normalized in {"1", "true", "yes", "y"}


def _parse_optional_bool(value: Any) -> bool | None:
    if value in {None, ""}:
        return None
    return _parse_bool(value)


def _semantic_matches(verdict: str) -> bool | None:
    if verdict == "match":
        return True
    if verdict == "mismatch":
        return False
    return None


def _combine_overall_verdict(*, semantic_verdict: str, reference_verdict: str) -> str:
    if semantic_verdict == "mismatch" or reference_verdict == "contradicted":
        return "fail"
    if semantic_verdict == "match" and reference_verdict in _CORRECT_REFERENCE_VERDICTS:
        return "pass"
    if semantic_verdict == "unavailable" and reference_verdict == "exact_schedule_match":
        return "behavioral_pass"
    if semantic_verdict == "match" and reference_verdict == "unresolved":
        return "partial"
    if semantic_verdict == "unavailable" and reference_verdict in {
        "exact_objective_match",
        "interval_consistent",
        "same_infeasibility",
    }:
        return "partial"
    return "unresolved"


def _matches_from_combined_overall_verdict(verdict: str) -> bool | None:
    if verdict in {"pass", "behavioral_pass"}:
        return True
    if verdict == "fail":
        return False
    return None


def _load_semantic_ground_truth_eval(
    *,
    result_payload: dict[str, Any],
    result_path: Path,
) -> dict[str, Any]:
    runtime = _resolve_ground_truth_runtime()
    if runtime is None:
        return {}

    payload = dict(result_payload)
    payload["__result_path__"] = str(result_path)
    payload["__result_name__"] = result_path.name
    payload["__result_stem__"] = result_path.stem
    try:
        row = runtime["evaluate_result_payload"](
            runtime["spec"],
            payload,
            reference_policy="off",
        )
    except Exception as exc:
        return {
            "ground_truth_eval_status": "error",
            "semantic_verdict": "",
            "semantic_reason": str(exc),
        }

    checks = dict(row.checks or {})
    return {
        "ground_truth_eval_status": row.status,
        "semantic_verdict": str(checks.get("semantic_verdict") or ""),
        "semantic_reason": str(checks.get("semantic_reason") or ""),
    }


def _resolve_ground_truth_runtime() -> dict[str, Any] | None:
    global _GROUND_TRUTH_RUNTIME
    if _GROUND_TRUTH_RUNTIME is not _GROUND_TRUTH_RUNTIME_UNSET:
        return _GROUND_TRUTH_RUNTIME

    try:
        from framework.evaluation.ground_truth import evaluate_result_payload
        from framework.registry import load_problem

        spec, _ = load_problem(problem="exam_block_seq", load_runtime_data=False)
    except Exception:
        _GROUND_TRUTH_RUNTIME = None
        return None

    _GROUND_TRUTH_RUNTIME = {
        "evaluate_result_payload": evaluate_result_payload,
        "spec": spec,
    }
    return _GROUND_TRUTH_RUNTIME


def _write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-root", required=True, help="Campaign root under runs/exam/")
    parser.add_argument(
        "--run-families",
        default="",
        help="Comma-separated run families to include, e.g. patchedit_auto,codeedit_auto",
    )
    parser.add_argument(
        "--ground-truth-root",
        default=str(DEFAULT_GROUND_TRUTH_ROOT),
        help="Ground-truth summary root, defaulting to outputs/solves/reference/3600s",
    )
    parser.add_argument(
        "--skip-semantic-ground-truth",
        action="store_true",
        help="Skip live semantic/reference ground-truth evaluator and use objective summary data only.",
    )
    parser.add_argument(
        "--selector-run-family",
        default=DEFAULT_SELECTOR_RUN_FAMILY,
        help="Run family to compare against the same-setting selector baseline.",
    )
    parser.add_argument(
        "--selector-baseline-run-family",
        default=DEFAULT_SELECTOR_BASELINE_RUN_FAMILY,
        help="Run family used as the same-setting objective baseline for selector improvement metrics.",
    )
    parser.add_argument("--output-dir", default=None, help="Optional explicit output directory")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_families = [item.strip() for item in str(args.run_families or "").split(",") if item.strip()] or None
    summary = build_campaign_objective_report(
        args.campaign_root,
        run_families=run_families,
        ground_truth_root=args.ground_truth_root,
        output_dir=args.output_dir,
        include_semantic_ground_truth=not args.skip_semantic_ground_truth,
        selector_run_family=args.selector_run_family,
        selector_baseline_run_family=args.selector_baseline_run_family,
    )
    print(f"Campaign root: {summary['campaign_root']}")
    print(f"Run families: {', '.join(summary['run_families']) if summary['run_families'] else 'all'}")
    print(f"Rows written: {summary['row_count']}")
    print(f"Correct rows: {summary['correct_count']}")
    print(f"Detail CSV: {summary['report_paths']['detail_csv']}")
    print(f"Overall CSV: {summary['report_paths']['overall_csv']}")
    print(f"By-prompt CSV: {summary['report_paths']['by_prompt_csv']}")
    print(f"By-instance CSV: {summary['report_paths']['by_instance_csv']}")
    print(f"Semantic overall CSV: {summary['report_paths']['semantic_overall_csv']}")
    print(f"Semantic by-prompt CSV: {summary['report_paths']['semantic_by_prompt_csv']}")
    print(f"Semantic by-instance CSV: {summary['report_paths']['semantic_by_instance_csv']}")
    print(f"Selector improvement CSV: {summary['report_paths']['selector_improvement_csv']}")
    print(f"Failure modes CSV: {summary['report_paths']['failure_modes_csv']}")
    print(f"Summary JSON: {summary['report_paths']['summary_json']}")


if __name__ == "__main__":
    main()
