#!/usr/bin/env python3
"""Evaluate saved Cornell GPT-only exam-scheduling runs.

Usage:
  python scripts/exam_block_seq/evaluate_cornell_gpt.py \
    --runs-dir runs/exam/paper_full_gpt_3600s \
    --reference-dir outputs/solves/reference/3600s \
    --output-dir runs/evaluation

This script reads only saved run outputs and saved 3600-second reference summaries.
It does not call LLM APIs and does not run optimization.
Figure 4 is written to the configured figure directory, which defaults to
<output-dir>/figures. The paper's outputs are in outputs/llm_runs/; the default output
directory is the git-ignored runs/evaluation, so a re-evaluation does not replace them.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import itertools
import json
import math
from pathlib import Path
import re
import statistics
import subprocess
from typing import Any, Iterable


GPT_MODELS = ("gpt-4.1-mini", "gpt-4.1", "gpt-5")
PROMPTS = ("P1", "P2", "P3", "P4", "P5", "P6")
TIME_LIMIT_SEC = 3600
DEFAULT_MODEL = "gpt-5"
DEFAULT_EDIT_MODE = "patch-edit-auto"
DEFAULT_RUNS_DIR = (
    "runs/exam/paper_full_gpt_3600s"
)
DEFAULT_REFERENCE_DIR = "outputs/solves/reference/3600s"
STATUS_CODE_NAMES = {
    2: "OPTIMAL",
    3: "INFEASIBLE",
    4: "INF_OR_UNBD",
    5: "UNBOUNDED",
    9: "TIME_LIMIT",
}
RUN_FAMILY_LABELS = {
    "codeedit_auto": "coedit-auto",
    "patchedit_auto": "patch-edit-auto",
    "patchedit_scratch": "patch-edit-scratch",
}
METHOD_PAPER_LABELS = {
    "patch-edit-auto": "ReOpt-LLM-Patch",
    "coedit-auto": "Direct-Code Agent",
    "patch-edit-scratch": "ReOpt-LLM-Patch without Selector",
}
METHOD_SHORT_LABELS = {
    "patch-edit-auto": "Patch",
    "coedit-auto": "Code",
    "patch-edit-scratch": "Patch-NoSel",
}
REFERENCE_PAPER_LABEL = "3600-second hindsight reference incumbent"
REFERENCE_SHORT_LABEL = "Ref."
EXPECTED_COMPONENTS = {
    "P1": ("reserved_virtual_slot", "reserved_slots", "virtual_block", "slot reservation"),
    "P2": ("pair_counts", "pair-count", "(4,9)", "(9,4)", "coenrollment", "co-enrollment"),
    "P3": ("frontload", "early_slots", "large_blocks", "large exam"),
    "P4": ("objective_weight", "triple_in_24hr", "beta", "gamma1", "gamma2"),
    "P5": ("slot_load_cap", "day2", "day_2", "block_enrollment", "load cap"),
    "P6": (
        "objective_weight",
        "triple_in_24hr",
        "beta",
        "pair_counts",
        "(4,9)",
        "(9,4)",
        "reserved_virtual_slot",
        "reserved_slots",
        "virtual_block",
    ),
}
CSV_FIELDS = [
    "run_family",
    "edit_mode",
    "method_label",
    "method_short_label",
    "model",
    "planner_mode",
    "instance",
    "prompt",
    "run_id",
    "run_status",
    "feasible",
    "component_identification_correct",
    "patch_valid",
    "update_correct",
    "prompt_constraint_satisfied",
    "prompt_satisfied",
    "solver_status",
    "gpt_reopt_obj",
    "reference_obj",
    "obj_diff",
    "ref_gap_pct",
    "time_limit_sec",
    "solve_time_sec",
    "reference_solve_time_sec",
    "triple_count",
    "back_to_back_count",
    "two_in_24hr_count",
    "three_in_4_slots_count",
    "reference_triple_count",
    "reference_back_to_back_count",
    "reference_two_in_24hr_count",
    "reference_three_in_4_slots_count",
    "total_constraint_violations",
    "violated_constraint_families",
    "max_constraint_violation",
    "sum_constraint_violation",
    "integrality_violation_count",
    "max_integrality_violation",
    "reserved_slot_violations",
    "coenrollment_update_applied",
    "affected_pair_metric_change",
    "large_exam_late_violations",
    "objective_weight_update_applied",
    "three_in_24h_penalty_update_applied",
    "day_2_total_students",
    "day_student_capacity_violations",
    "retry_count",
    "max_retries",
    "success_on_attempt",
    "first_attempt_patch_valid",
    "first_attempt_update_correct",
    "final_attempt_patch_valid",
    "final_attempt_update_correct",
    "failure_stage",
    "failure_label",
    "failure_detail",
    "result_path",
]
FAILURE_TAXONOMY_RUN_FIELDS = [
    "run_family",
    "edit_mode",
    "method_label",
    "method_short_label",
    "model",
    "instance",
    "prompt",
    "run_id",
    "run_status",
    "clean_pass",
    "any_failure",
    "primary_category",
    "non_ok",
    "objective_mismatch",
    "objective_unavailable",
    "operation_mismatch",
    "operation_unavailable",
    "projection_failure",
    "objective_close",
    "operation_correct",
    "operation_projection_state",
    "failure_stage",
    "failure_label",
    "chosen_patch_op",
    "detailed_failure_modes",
]
FAILURE_TAXONOMY_DETAIL_FIELDS = [
    "run_family",
    "edit_mode",
    "method_label",
    "method_short_label",
    "model",
    "instance",
    "prompt",
    "run_id",
    "primary_category",
    "is_primary_record",
    "mode_group",
    "mode_code",
    "mode_label",
    "mode_detail",
    "run_status",
    "failure_stage",
    "failure_label",
]
FAILURE_TAXONOMY_SUMMARY_FIELDS = [
    "model",
    "edit_mode",
    "method_label",
    "method_short_label",
    "mode_group",
    "mode_code",
    "mode_label",
    "primary_records",
    "record_count",
    "run_count",
    "example_run_id",
    "example_detail",
]


@dataclass
class InstanceData:
    instance_id: str
    path: Path
    blocks: list[int]
    virtual_blocks: list[int]
    large_blocks: list[int]
    early_slots: list[int]
    triple_day_start: list[int]
    triple_24_start: list[int]
    eve_morn_start: list[int]
    other_b2b_start: list[int]
    slots_per_day: int
    slot_times: list[str]
    block_enrollment: dict[int, float]
    pair_counts: dict[tuple[int, int], float]
    triplet_counts: dict[tuple[int, int, int], float]


@dataclass
class ReferenceRow:
    instance: str
    prompt: str
    objective: float | None
    solver_status: str
    solve_time_sec: float | None
    schedule: dict[int, int] | None
    path: Path


def main() -> None:
    args = parse_args()
    runs_dir = Path(args.runs_dir)
    reference_dir = Path(args.reference_dir)
    output_dir = Path(args.output_dir)
    tables_dir = Path(args.tables_dir) if args.tables_dir else output_dir / "tables"
    figures_dir = Path(args.figures_dir) if args.figures_dir else output_dir / "figures"
    args.tables_dir = str(tables_dir)
    args.figures_dir = str(figures_dir)
    if not any((runs_dir / "results").glob("*/runs/*.json")):
        raise SystemExit(f"No run result JSON files found under {runs_dir / 'results'}")
    for directory in (output_dir, tables_dir, figures_dir):
        directory.mkdir(parents=True, exist_ok=True)
    clean_paper_artifacts(tables_dir, figures_dir)

    instances = load_instances(Path("benchmark/raw_instances"))
    references = load_references(reference_dir)
    operation_rows = load_operation_rows(runs_dir)
    all_rows, missing_notes = evaluate_runs(runs_dir, references, instances, operation_rows)
    all_rows = stable_sort_rows([row for row in all_rows if row["prompt"] in PROMPTS])
    config_rows = build_config_rows(all_rows)
    selected_model, selected_mode, selected_metrics = select_configuration(
        config_rows, args.selected_model, args.selected_edit_mode
    )
    selected_rows = [
        row
        for row in all_rows
        if row["model"] == selected_model and row["edit_mode"] == selected_mode
    ]
    selected_rows = stable_sort_rows(selected_rows)
    default_rows = stable_sort_rows(
        [
            row
            for row in all_rows
            if row["model"] == DEFAULT_MODEL and row["edit_mode"] == DEFAULT_EDIT_MODE
        ]
    )
    schedule_quality_rows = build_schedule_quality_rows(default_rows)
    failure_rows = build_failure_rows(all_rows)
    (
        failure_taxonomy_run_rows,
        failure_taxonomy_detail_rows,
        failure_taxonomy_summary_rows,
        failure_taxonomy_high_level_rows,
        failure_taxonomy_notes,
    ) = build_failure_taxonomy_outputs(runs_dir, all_rows)
    missing_notes.extend(failure_taxonomy_notes)

    all_csv = output_dir / "evaluation_all_gpt_runs.csv"
    selected_csv = output_dir / "evaluation_selected_gpt.csv"
    failure_taxonomy_runs_csv = output_dir / "evaluation_failure_taxonomy_runs.csv"
    failure_taxonomy_detailed_csv = output_dir / "evaluation_failure_taxonomy_detailed.csv"
    failure_taxonomy_summary_csv = output_dir / "evaluation_failure_taxonomy_summary.csv"
    write_csv(all_csv, all_rows, CSV_FIELDS)
    write_csv(selected_csv, selected_rows, CSV_FIELDS)
    write_csv(failure_taxonomy_runs_csv, failure_taxonomy_run_rows, FAILURE_TAXONOMY_RUN_FIELDS)
    write_csv(
        failure_taxonomy_detailed_csv,
        failure_taxonomy_detail_rows,
        FAILURE_TAXONOMY_DETAIL_FIELDS,
    )
    write_csv(
        failure_taxonomy_summary_csv,
        failure_taxonomy_summary_rows,
        FAILURE_TAXONOMY_SUMMARY_FIELDS,
    )

    write_exam_instances_table(tables_dir / "exam_instances.tex", instances)
    write_main_table(tables_dir / "cornell_main_results.tex", default_rows)
    write_schedule_quality_table(
        tables_dir / "cornell_schedule_quality.tex", schedule_quality_rows
    )
    write_patch_vs_code_table(tables_dir / "cornell_patch_vs_code.tex", all_rows)
    write_failure_table(tables_dir / "cornell_failure_modes.tex", failure_rows)
    write_selector_ablation_table(tables_dir / "cornell_selector_ablation.tex", all_rows)
    write_selector_ablation_paired_table(
        tables_dir / "cornell_selector_ablation_paired.tex", all_rows
    )

    plot_paths, plot_notes = write_reference_gap_plot(figures_dir, default_rows)
    missing_notes.extend(plot_notes)

    metadata = build_metadata(
        args=args,
        selected_model=selected_model,
        selected_mode=selected_mode,
        selected_metrics=selected_metrics,
        evaluated_rows=all_rows,
        missing_notes=missing_notes,
        plot_paths=plot_paths,
    )
    metadata_path = output_dir / "evaluation_metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(
        "Selected GPT configuration: "
        f"{selected_model} / {method_paper_label(selected_mode)} ({method_short_label(selected_mode)})"
    )
    print(f"Evaluated runs: {len(all_rows)}")
    print(f"Wrote {all_csv}")
    print(f"Wrote {selected_csv}")
    print(f"Wrote {failure_taxonomy_runs_csv}")
    print(f"Wrote {failure_taxonomy_detailed_csv}")
    print(f"Wrote {failure_taxonomy_summary_csv}")
    print(f"Wrote {metadata_path}")
    print(f"Wrote LaTeX tables under {tables_dir}")
    print(f"Wrote plots under {figures_dir}")
    if missing_notes:
        print("Missing metrics / assumptions:")
        for note in sorted(set(missing_notes)):
            print(f"- {note}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR)
    parser.add_argument("--reference-dir", default=DEFAULT_REFERENCE_DIR)
    parser.add_argument("--output-dir", default="runs/evaluation")
    parser.add_argument("--tables-dir", default=None, help="Defaults to <output-dir>/tables")
    parser.add_argument("--figures-dir", default=None, help="Defaults to <output-dir>/figures")
    parser.add_argument("--selected-model", default=None)
    parser.add_argument("--selected-edit-mode", default=None)
    return parser.parse_args()


def evaluate_runs(
    runs_dir: Path,
    references: dict[tuple[str, str], ReferenceRow],
    instances: dict[str, InstanceData],
    operation_rows: dict[str, dict[str, str]],
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    missing_notes: list[str] = []
    result_paths = sorted((runs_dir / "results").glob("*/runs/*.json"))
    if not result_paths:
        raise SystemExit(f"No run result JSON files found under {runs_dir / 'results'}")

    for path in result_paths:
        payload = load_json(path)
        model = normalize_model(payload.get("model") or nested_get(payload, "input", "model"))
        if model not in GPT_MODELS:
            continue
        prompt = normalize_prompt(payload.get("prompt_id") or nested_get(payload, "input", "prompt_id"))
        if prompt not in PROMPTS:
            continue
        instance = str(payload.get("instance_id") or "").strip()
        if not instance:
            instance = infer_instance_from_run_id(str(payload.get("run_id") or path.stem))
        run_family = infer_run_family(payload, path)
        edit_mode = RUN_FAMILY_LABELS.get(run_family, run_family.replace("_", "-"))
        run_id = str(payload.get("run_id") or path.stem)
        reference = references.get((instance, prompt))
        instance_data = instances.get(instance)
        operation = operation_rows.get(run_id, {})

        gpt_schedule = extract_schedule(payload)
        reference_schedule = reference.schedule if reference else None
        gpt_quality = (
            compute_schedule_quality(gpt_schedule, instance_data)
            if gpt_schedule and instance_data
            else empty_quality()
        )
        ref_quality = (
            compute_schedule_quality(reference_schedule, instance_data)
            if reference_schedule and instance_data
            else empty_quality()
        )
        general_violations = (
            compute_general_violations(gpt_schedule, instance_data)
            if gpt_schedule and instance_data
            else missing_general_violations()
        )
        prompt_violations = (
            compute_prompt_violations(prompt, gpt_schedule, instance_data, operation)
            if instance_data is not None
            else empty_prompt_violations()
        )

        feasible = coerce_bool(payload.get("feasible"))
        if feasible is None:
            feasible = bool(gpt_schedule) and str(payload.get("status") or "").lower() == "ok"
        patch_valid = compute_patch_valid(payload, operation)
        update_correct = compute_update_correct(operation)
        component_correct = compute_component_correct(payload, operation, prompt)
        prompt_constraint_satisfied = compute_prompt_constraint_satisfied(
            prompt, general_violations, prompt_violations, update_correct
        )
        prompt_satisfied = bool(feasible and update_correct is True and prompt_constraint_satisfied is True)
        retry_metrics, retry_notes = compute_retry_metrics(payload, patch_valid, update_correct, prompt_satisfied)
        missing_notes.extend(retry_notes)

        gpt_obj = finite_float(payload.get("objective"))
        if gpt_obj is None:
            gpt_obj = finite_float(nested_get(payload, "result", "steps", 0, "objective"))
        ref_obj = reference.objective if reference else finite_float(operation.get("ground_truth_objective"))
        obj_diff = gpt_obj - ref_obj if gpt_obj is not None and ref_obj is not None else None
        ref_gap = (
            100.0 * obj_diff / max(1.0, abs(ref_obj))
            if obj_diff is not None and ref_obj is not None
            else None
        )
        solve_time = finite_float(payload.get("runtime"))
        if solve_time is None:
            solve_time = finite_float(nested_get(payload, "result", "steps", 0, "evaluation", "runtime"))
        solver_status = solver_status_from_payload(payload)

        if reference is None:
            missing_notes.append(f"Missing 3600s reference for {instance}_{prompt}")
        if instance_data is None:
            missing_notes.append(f"Missing instance data for {instance}")
        if not gpt_schedule:
            missing_notes.append(f"Missing saved final schedule for {run_id}")
        if operation and operation.get("operation_correct", "") == "":
            missing_notes.append("Some update correctness values are unavailable in operation logs")

        row: dict[str, Any] = {
                "run_family": run_family,
                "edit_mode": edit_mode,
                "method_label": method_paper_label(edit_mode),
                "method_short_label": method_short_label(edit_mode),
                "model": model,
            "planner_mode": payload.get("planner_mode") or nested_get(payload, "input", "planner_mode") or "",
            "instance": instance,
            "prompt": prompt,
            "run_id": run_id,
            "run_status": payload.get("status") or "",
            "feasible": feasible,
            "component_identification_correct": component_correct,
            "patch_valid": patch_valid,
            "update_correct": update_correct,
            "prompt_constraint_satisfied": prompt_constraint_satisfied,
            "prompt_satisfied": prompt_satisfied,
            "solver_status": solver_status,
            "gpt_reopt_obj": gpt_obj,
            "reference_obj": ref_obj,
            "obj_diff": obj_diff,
            "ref_gap_pct": ref_gap,
            "time_limit_sec": TIME_LIMIT_SEC,
            "solve_time_sec": solve_time,
            "reference_solve_time_sec": reference.solve_time_sec if reference else None,
            "triple_count": gpt_quality["triple_count"],
            "back_to_back_count": gpt_quality["back_to_back_count"],
            "two_in_24hr_count": gpt_quality["two_in_24hr_count"],
            "three_in_4_slots_count": gpt_quality["three_in_4_slots_count"],
            "reference_triple_count": ref_quality["triple_count"],
            "reference_back_to_back_count": ref_quality["back_to_back_count"],
            "reference_two_in_24hr_count": ref_quality["two_in_24hr_count"],
            "reference_three_in_4_slots_count": ref_quality["three_in_4_slots_count"],
            "total_constraint_violations": general_violations["total_constraint_violations"],
            "violated_constraint_families": ";".join(general_violations["violated_constraint_families"]),
            "max_constraint_violation": general_violations["max_constraint_violation"],
            "sum_constraint_violation": general_violations["sum_constraint_violation"],
            "integrality_violation_count": general_violations["integrality_violation_count"],
            "max_integrality_violation": general_violations["max_integrality_violation"],
            "reserved_slot_violations": prompt_violations["reserved_slot_violations"],
            "coenrollment_update_applied": prompt_violations["coenrollment_update_applied"],
            "affected_pair_metric_change": prompt_violations["affected_pair_metric_change"],
            "large_exam_late_violations": prompt_violations["large_exam_late_violations"],
            "objective_weight_update_applied": prompt_violations["objective_weight_update_applied"],
            "three_in_24h_penalty_update_applied": prompt_violations[
                "three_in_24h_penalty_update_applied"
            ],
            "day_2_total_students": prompt_violations["day_2_total_students"],
            "day_student_capacity_violations": prompt_violations[
                "day_student_capacity_violations"
            ],
            "retry_count": retry_metrics["retry_count"],
            "max_retries": retry_metrics["max_retries"],
            "success_on_attempt": retry_metrics["success_on_attempt"],
            "first_attempt_patch_valid": retry_metrics["first_attempt_patch_valid"],
            "first_attempt_update_correct": retry_metrics["first_attempt_update_correct"],
            "final_attempt_patch_valid": retry_metrics["final_attempt_patch_valid"],
            "final_attempt_update_correct": retry_metrics["final_attempt_update_correct"],
            "failure_stage": payload.get("failure_stage") or operation.get("failure_stage") or "",
            "failure_label": payload.get("failure_label") or operation.get("failure_label") or "",
            "failure_detail": payload.get("failure_detail") or operation.get("failure_detail") or "",
            "result_path": str(path),
        }
        rows.append(row)
    return rows, missing_notes


def load_instances(root: Path) -> dict[str, InstanceData]:
    instances: dict[str, InstanceData] = {}
    for instance_dir in sorted(root.glob("blockseq_*")):
        manifest_path = instance_dir / "instance.json"
        if not manifest_path.exists():
            continue
        manifest = load_json(manifest_path)
        instance_id = parse_instance_id(instance_dir.name)
        block_summary = read_block_summary(instance_dir / "block_summary.csv")
        instances[instance_id] = InstanceData(
            instance_id=instance_id,
            path=instance_dir,
            blocks=[int(x) for x in manifest.get("all_blocks", [])],
            virtual_blocks=[int(x) for x in manifest.get("virtual_blocks", [])],
            large_blocks=[int(x) for x in manifest.get("large_blocks", [])],
            early_slots=[int(x) for x in manifest.get("early_slots", [])],
            triple_day_start=[int(x) for x in manifest.get("triple_day_start", [])],
            triple_24_start=[int(x) for x in manifest.get("triple_24_start", [])],
            eve_morn_start=[int(x) for x in manifest.get("eve_morn_start", [])],
            other_b2b_start=[int(x) for x in manifest.get("other_b2b_start", [])],
            slots_per_day=3,
            slot_times=["9am", "2pm", "7pm"],
            block_enrollment=block_summary,
            pair_counts=read_count_map(instance_dir / "pair_counts.csv", arity=2),
            triplet_counts=read_count_map(instance_dir / "triplet_counts.csv", arity=3),
        )
    return instances


def load_references(reference_dir: Path) -> dict[tuple[str, str], ReferenceRow]:
    candidates: dict[tuple[str, str], list[tuple[int, str, ReferenceRow]]] = defaultdict(list)
    for path in sorted(reference_dir.glob("**/summary.csv")):
        for row in read_csv(path):
            instance = str(row.get("instance_id") or "").strip()
            prompt = normalize_prompt(row.get("prompt_id"))
            if not instance or prompt not in PROMPTS:
                continue
            reference = ReferenceRow(
                instance=instance,
                prompt=prompt,
                objective=finite_float(row.get("objective")),
                solver_status=str(row.get("status") or ""),
                solve_time_sec=finite_float(row.get("runtime_attr_s"))
                or finite_float(row.get("wall_time_s")),
                schedule=parse_schedule(row.get("schedule")),
                path=path,
            )
            candidates[(instance, prompt)].append((len(path.parts), str(path), reference))
    references: dict[tuple[str, str], ReferenceRow] = {}
    for key, refs in candidates.items():
        refs.sort(key=lambda item: (item[0], item[1]))
        references[key] = refs[0][2]
    return references


def load_operation_rows(runs_dir: Path) -> dict[str, dict[str, str]]:
    report_dirs = sorted((runs_dir / "reports").glob("all_runs_complete*"))
    for report_dir in reversed(report_dirs):
        path = report_dir / "operation_semantic_correctness.csv"
        if path.exists():
            return {row["run_id"]: row for row in read_csv(path) if row.get("run_id")}
    print(
        f"note: no {runs_dir}/reports/all_runs_complete*/operation_semantic_correctness.csv; "
        "update_correct and the related semantic checks are blank, and the P2/P4 prompt checks count as not satisfied"
    )
    return {}


def compute_schedule_quality(
    block_to_slot: dict[int, int],
    instance: InstanceData,
) -> dict[str, float]:
    slot_to_blocks: dict[int, list[int]] = defaultdict(list)
    for block, slot in block_to_slot.items():
        slot_to_blocks[int(slot)].append(int(block))
    slot_to_block = {slot: blocks[0] for slot, blocks in slot_to_blocks.items() if blocks}
    max_slot = max(instance.blocks) if instance.blocks else 0

    triple_count = 0.0
    for slot in sorted(set(instance.triple_day_start) | set(instance.triple_24_start)):
        triple = blocks_for_slots(slot_to_block, slot, 3, max_slot)
        if triple is not None:
            triple_count += triplet_count(instance, triple)

    back_to_back_count = 0.0
    for slot in sorted(set(instance.eve_morn_start) | set(instance.other_b2b_start)):
        pair = blocks_for_slots(slot_to_block, slot, 2, max_slot)
        if pair is not None:
            back_to_back_count += pair_count(instance, pair[0], pair[1])

    two_in_24hr_count = 0.0
    for slot_a in sorted(slot_to_block):
        for distance in range(1, instance.slots_per_day + 1):
            slot_b = slot_a + distance
            if slot_b > max_slot or slot_b not in slot_to_block:
                continue
            two_in_24hr_count += pair_count(instance, slot_to_block[slot_a], slot_to_block[slot_b])

    three_in_4_slots_count = 0.0
    for start in range(1, max_slot - 2):
        window = blocks_for_slots(slot_to_block, start, 4, max_slot)
        if window is None:
            continue
        for idxs in itertools.combinations(range(4), 3):
            triple = tuple(window[idx] for idx in idxs)
            three_in_4_slots_count += triplet_count(instance, triple)

    return {
        "triple_count": triple_count,
        "back_to_back_count": back_to_back_count,
        "two_in_24hr_count": two_in_24hr_count,
        "three_in_4_slots_count": three_in_4_slots_count,
    }


def compute_general_violations(
    block_to_slot: dict[int, int],
    instance: InstanceData,
) -> dict[str, Any]:
    families: set[str] = set()
    magnitudes: list[float] = []
    expected_blocks = set(instance.blocks)
    expected_slots = set(instance.blocks)
    seen_blocks = [int(block) for block in block_to_slot]
    seen_slots = [int(slot) for slot in block_to_slot.values()]

    missing_blocks = expected_blocks - set(seen_blocks)
    extra_blocks = set(seen_blocks) - expected_blocks
    duplicate_blocks = len(seen_blocks) - len(set(seen_blocks))
    if missing_blocks or extra_blocks or duplicate_blocks:
        families.add("block_assignment")
        magnitudes.append(float(len(missing_blocks) + len(extra_blocks) + duplicate_blocks))

    missing_slots = expected_slots - set(seen_slots)
    extra_slots = set(seen_slots) - expected_slots
    duplicate_slots = len(seen_slots) - len(set(seen_slots))
    if missing_slots or extra_slots or duplicate_slots:
        families.add("slot_assignment")
        magnitudes.append(float(len(missing_slots) + len(extra_slots) + duplicate_slots))

    return {
        "total_constraint_violations": int(sum(magnitudes)),
        "violated_constraint_families": sorted(families),
        "max_constraint_violation": max(magnitudes) if magnitudes else 0.0,
        "sum_constraint_violation": sum(magnitudes),
        "integrality_violation_count": 0,
        "max_integrality_violation": 0.0,
    }


def missing_general_violations() -> dict[str, Any]:
    return {
        "total_constraint_violations": None,
        "violated_constraint_families": [],
        "max_constraint_violation": None,
        "sum_constraint_violation": None,
        "integrality_violation_count": None,
        "max_integrality_violation": None,
    }


def compute_prompt_violations(
    prompt: str,
    block_to_slot: dict[int, int] | None,
    instance: InstanceData,
    operation: dict[str, str],
) -> dict[str, Any]:
    values = empty_prompt_violations()
    slot_to_block = invert_schedule(block_to_slot or {})
    update_correct = compute_update_correct(operation)

    if prompt == "P1":
        reserved_slot = penultimate_evening_slot(instance)
        assigned = slot_to_block.get(reserved_slot)
        values["reserved_slot_violations"] = 0 if assigned in set(instance.virtual_blocks) else 1
    elif prompt == "P2":
        values["coenrollment_update_applied"] = update_correct
        values["affected_pair_metric_change"] = 120 if update_correct is True else 0
    elif prompt == "P3":
        cutoff = p3_cutoff_exclusive(instance)
        late = 0
        for block in instance.large_blocks:
            slot = (block_to_slot or {}).get(int(block))
            if slot is None or int(slot) >= cutoff:
                late += 1
        values["large_exam_late_violations"] = late
    elif prompt == "P4":
        values["objective_weight_update_applied"] = update_correct
        values["three_in_24h_penalty_update_applied"] = update_correct
    elif prompt == "P5":
        total = sum(float(instance.block_enrollment.get(slot_to_block.get(slot, -1), 0.0)) for slot in (4, 5, 6))
        values["day_2_total_students"] = total
        values["day_student_capacity_violations"] = max(0.0, total - 4000.0)
    elif prompt == "P6":
        reserved_slot = penultimate_evening_slot(instance)
        assigned = slot_to_block.get(reserved_slot)
        values["reserved_slot_violations"] = 0 if assigned in set(instance.virtual_blocks) else 1
        values["coenrollment_update_applied"] = update_correct
        values["affected_pair_metric_change"] = 120 if update_correct is True else 0
        values["objective_weight_update_applied"] = update_correct
        values["three_in_24h_penalty_update_applied"] = update_correct
    return values


def empty_prompt_violations() -> dict[str, Any]:
    return {
        "reserved_slot_violations": None,
        "coenrollment_update_applied": None,
        "affected_pair_metric_change": None,
        "large_exam_late_violations": None,
        "objective_weight_update_applied": None,
        "three_in_24h_penalty_update_applied": None,
        "day_2_total_students": None,
        "day_student_capacity_violations": None,
    }


def compute_prompt_constraint_satisfied(
    prompt: str,
    general_violations: dict[str, Any],
    prompt_violations: dict[str, Any],
    update_correct: bool | None,
) -> bool:
    general_total = general_violations.get("total_constraint_violations")
    if general_total is None or int(general_total) != 0:
        return False
    if prompt == "P1":
        return int(prompt_violations.get("reserved_slot_violations") or 0) == 0
    if prompt == "P2":
        return update_correct is True
    if prompt == "P3":
        return int(prompt_violations.get("large_exam_late_violations") or 0) == 0
    if prompt == "P4":
        return update_correct is True
    if prompt == "P5":
        return float(prompt_violations.get("day_student_capacity_violations") or 0.0) == 0.0
    if prompt == "P6":
        return (
            update_correct is True
            and int(prompt_violations.get("reserved_slot_violations") or 0) == 0
        )
    return False


def compute_patch_valid(payload: dict[str, Any], operation: dict[str, str]) -> bool | None:
    status = str(payload.get("status") or "").lower()
    if status == "error":
        if str(payload.get("failure_stage") or "").lower() in {"edit generation", "patch generation"}:
            return False
    for candidate in (
        nested_get(payload, "result", "steps", 0, "planner_output", "annotations", "planner_output_executable"),
        nested_get(payload, "result", "steps", 0, "planner_output", "planning_hints", "planner_output_executable"),
        nested_get(payload, "result", "steps", 0, "planner_output", "annotations", "model_effective_edit"),
        nested_get(payload, "result", "steps", 0, "planner_output", "planning_hints", "model_effective_edit"),
        nested_get(payload, "result", "steps", 0, "planner_output", "annotations", "planner_parse_ok"),
        nested_get(payload, "result", "steps", 0, "planner_output", "planning_hints", "planner_parse_ok"),
    ):
        bool_value = coerce_bool(candidate)
        if bool_value is not None:
            return bool_value
    if operation.get("operation_correct") in {"True", "False"}:
        return True
    if status == "ok":
        return True
    return None


def compute_update_correct(operation: dict[str, str]) -> bool | None:
    value = coerce_bool(operation.get("operation_correct"))
    if value is not None:
        return value
    return None


def compute_component_correct(
    payload: dict[str, Any],
    operation: dict[str, str],
    prompt: str,
) -> bool | None:
    haystack_items = [
        operation.get("operation_reason", ""),
        operation.get("operation_projected_semantics", ""),
        operation.get("operation_projected_patch_ops", ""),
        json.dumps(nested_get(payload, "result", "steps", 0, "planner_output") or {}, sort_keys=True),
        json.dumps(nested_get(payload, "result", "steps", 0, "chosen_patches") or {}, sort_keys=True),
    ]
    haystack = " ".join(str(item).lower() for item in haystack_items)
    expected = EXPECTED_COMPONENTS.get(prompt, ())
    if any(token.lower() in haystack for token in expected):
        return True
    if operation.get("operation_correct") == "True":
        return True
    if operation.get("operation_correct") == "False":
        return False
    return None


def compute_retry_metrics(
    payload: dict[str, Any],
    patch_valid: bool | None,
    update_correct: bool | None,
    prompt_satisfied: bool,
) -> tuple[dict[str, Any], list[str]]:
    notes: list[str] = []
    retry_count = first_finite_int(
        payload.get("model_retry_count"),
        payload.get("codeedit_repair_count"),
        nested_get(payload, "result", "steps", 0, "planner_output", "annotations", "model_retry_count"),
        nested_get(payload, "result", "steps", 0, "planner_output", "planning_hints", "model_retry_count"),
        nested_get(payload, "result", "steps", 0, "planner_output", "annotations", "codeedit_repair_count"),
        nested_get(payload, "result", "steps", 0, "planner_output", "planning_hints", "codeedit_repair_count"),
    )
    attempt_count = first_finite_int(
        payload.get("model_attempt_count"),
        payload.get("codeedit_attempt_count"),
        nested_get(payload, "result", "steps", 0, "planner_output", "annotations", "model_attempt_count"),
        nested_get(payload, "result", "steps", 0, "planner_output", "planning_hints", "model_attempt_count"),
        nested_get(payload, "result", "steps", 0, "planner_output", "annotations", "codeedit_attempt_count"),
        nested_get(payload, "result", "steps", 0, "planner_output", "planning_hints", "codeedit_attempt_count"),
    )
    max_retries = first_finite_int(nested_get(payload, "input", "config", "model_failure_retry_budget"))
    if retry_count is None:
        notes.append("Retry counts are missing for some runs")
    first_patch_valid: bool | None = None
    first_update_correct: bool | None = None
    success_on_attempt: int | None = None
    if attempt_count == 1 or retry_count == 0:
        first_patch_valid = patch_valid
        first_update_correct = update_correct
    elif attempt_count is not None or retry_count is not None:
        first_patch_valid = False
        first_update_correct = False if update_correct is True else update_correct
    if prompt_satisfied and attempt_count is not None:
        success_on_attempt = attempt_count
    return (
        {
            "retry_count": retry_count,
            "max_retries": max_retries,
            "success_on_attempt": success_on_attempt,
            "first_attempt_patch_valid": first_patch_valid,
            "first_attempt_update_correct": first_update_correct,
            "final_attempt_patch_valid": patch_valid,
            "final_attempt_update_correct": update_correct,
        },
        notes,
    )


def build_config_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["model"], row["edit_mode"])].append(row)
    config_rows: list[dict[str, Any]] = []
    for (model, edit_mode), group in sorted(grouped.items(), key=lambda item: (model_sort(item[0][0]), item[0][1])):
        successful = [row for row in group if row.get("prompt_satisfied") is True]
        config_rows.append(
            {
                "model": model,
                "edit_mode": edit_mode,
                "method_label": method_paper_label(edit_mode),
                "method_short_label": method_short_label(edit_mode),
                "runs": len(group),
                "component_identification_accuracy": mean_bool(row.get("component_identification_correct") for row in group),
                "patch_validity_rate": mean_bool(row.get("patch_valid") for row in group),
                "update_correctness_rate": mean_bool(row.get("update_correct") for row in group),
                "prompt_satisfaction_rate": mean_bool(row.get("prompt_satisfied") for row in group),
                "first_attempt_success_rate": mean_bool(
                    (row.get("prompt_satisfied") is True and finite_int(row.get("retry_count")) == 0)
                    if finite_int(row.get("retry_count")) is not None
                    else None
                    for row in group
                ),
                "final_success_rate": mean_bool(row.get("prompt_satisfied") for row in group),
                "mean_retry_count_for_successful_runs": mean_float(
                    row.get("retry_count") for row in successful
                ),
                "mean_obj_diff": mean_float(row.get("obj_diff") for row in group),
                "mean_ref_gap_pct": mean_float(row.get("ref_gap_pct") for row in group),
                "mean_triple_count": mean_float(row.get("triple_count") for row in group),
                "mean_back_to_back_count": mean_float(row.get("back_to_back_count") for row in group),
                "mean_two_in_24hr_count": mean_float(row.get("two_in_24hr_count") for row in group),
                "mean_three_in_4_slots_count": mean_float(
                    row.get("three_in_4_slots_count") for row in group
                ),
            }
        )
    return config_rows


def select_configuration(
    config_rows: list[dict[str, Any]],
    selected_model: str | None,
    selected_edit_mode: str | None,
) -> tuple[str, str, dict[str, Any]]:
    normalized_selected_edit_mode = normalize_edit_mode_arg(selected_edit_mode)
    if selected_model or selected_edit_mode:
        for row in config_rows:
            if selected_model and row["model"] != normalize_model(selected_model):
                continue
            if normalized_selected_edit_mode and row["edit_mode"] != normalized_selected_edit_mode:
                continue
            return row["model"], row["edit_mode"], row
        raise SystemExit(
            f"Requested selected configuration not found: model={selected_model}, edit_mode={selected_edit_mode}"
        )

    def key(row: dict[str, Any]) -> tuple[Any, ...]:
        return (
            -(row.get("prompt_satisfaction_rate") or 0.0),
            -(row.get("update_correctness_rate") or 0.0),
            -(row.get("patch_validity_rate") or 0.0),
            row.get("mean_ref_gap_pct") if row.get("mean_ref_gap_pct") is not None else float("inf"),
            row.get("mean_obj_diff") if row.get("mean_obj_diff") is not None else float("inf"),
            row.get("mean_triple_count") if row.get("mean_triple_count") is not None else float("inf"),
            row.get("mean_back_to_back_count") if row.get("mean_back_to_back_count") is not None else float("inf"),
            row.get("mean_two_in_24hr_count") if row.get("mean_two_in_24hr_count") is not None else float("inf"),
            row.get("mean_three_in_4_slots_count") if row.get("mean_three_in_4_slots_count") is not None else float("inf"),
            model_sort(row["model"]),
            row["edit_mode"],
        )

    best = sorted(config_rows, key=key)[0]
    return best["model"], best["edit_mode"], best


def build_schedule_quality_rows(selected_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    selected_method_short = (
        method_short_label(selected_rows[0]["edit_mode"]) if selected_rows else "GPT"
    )
    for label, group in [("overall", selected_rows)] + [
        (prompt, [row for row in selected_rows if row["prompt"] == prompt]) for prompt in PROMPTS
    ]:
        if not group:
            continue
        output = {"group": label, "method_short_label": selected_method_short}
        for metric, short in [
            ("triple_count", "triples"),
            ("back_to_back_count", "b2b"),
            ("two_in_24hr_count", "two_in_24hr"),
            ("three_in_4_slots_count", "three_in_4"),
        ]:
            gpt = mean_float(row.get(metric) for row in group)
            ref = mean_float(row.get(f"reference_{metric}") for row in group)
            output[f"{short}_gpt"] = gpt
            output[f"{short}_reference"] = ref
            output[f"{short}_delta"] = (gpt - ref) if gpt is not None and ref is not None else None
        rows.append(output)
    return rows


def build_failure_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["model"], row["edit_mode"])].append(row)
    output: list[dict[str, Any]] = []
    for (model, edit_mode), group in sorted(grouped.items(), key=lambda item: (model_sort(item[0][0]), item[0][1])):
        output.append(
            {
                "model": model,
                "edit_mode": edit_mode,
                "method_label": method_paper_label(edit_mode),
                "method_short_label": method_short_label(edit_mode),
                "wrong_component": count_flag(group, lambda row: row.get("component_identification_correct") is False),
                "invalid_patch": count_flag(group, lambda row: row.get("patch_valid") is False),
                "incorrect_update": count_flag(group, lambda row: row.get("update_correct") is not True),
                "infeasible_solution": count_flag(group, lambda row: row.get("feasible") is not True),
                "general_constraint_violation": count_flag(
                    group, lambda row: positive(row.get("total_constraint_violations"))
                ),
                "prompt_constraint_violation": count_flag(
                    group, lambda row: row.get("prompt_constraint_satisfied") is not True
                ),
                "missing_invalid_output": count_flag(
                    group,
                    lambda row: row.get("run_status") != "ok"
                    or row.get("gpt_reopt_obj") is None
                    or row.get("triple_count") is None,
                ),
                "exhausted_retry_budget": count_flag(
                    group,
                    lambda row: finite_int(row.get("retry_count")) is not None
                    and finite_int(row.get("max_retries")) is not None
                    and finite_int(row.get("retry_count")) >= finite_int(row.get("max_retries"))
                    and row.get("prompt_satisfied") is not True,
                ),
            }
        )
    return output


def build_failure_taxonomy_outputs(
    runs_dir: Path,
    evaluation_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    notes: list[str] = []
    row_by_run_id = {str(row["run_id"]): row for row in evaluation_rows}
    run_ids = set(row_by_run_id)
    reports_dir = runs_dir / "reports"
    run_path = reports_dir / "failure_analysis_all_runs.csv"
    detail_path = reports_dir / "failure_modes_detailed.csv"

    run_rows: list[dict[str, Any]] = []
    if run_path.exists():
        for raw in read_csv(run_path):
            run_id = str(raw.get("run_id") or "")
            eval_row = row_by_run_id.get(run_id)
            if eval_row is None:
                continue
            row = {field: raw.get(field, "") for field in FAILURE_TAXONOMY_RUN_FIELDS}
            row["edit_mode"] = eval_row["edit_mode"]
            row["method_label"] = method_paper_label(eval_row["edit_mode"])
            row["method_short_label"] = method_short_label(eval_row["edit_mode"])
            row["run_family"] = eval_row["run_family"]
            row["model"] = eval_row["model"]
            row["instance"] = eval_row["instance"]
            row["prompt"] = eval_row["prompt"]
            row["run_id"] = run_id
            run_rows.append(row)
    else:
        notes.append(f"Failure taxonomy run file missing: {run_path}")
    existing_taxonomy_run_ids = {row["run_id"] for row in run_rows}
    if existing_taxonomy_run_ids != run_ids:
        notes.append("Patch-edit-scratch is included only as an ablation to test the LLM selector.")
    for eval_row in evaluation_rows:
        run_id = str(eval_row["run_id"])
        if run_id in existing_taxonomy_run_ids:
            continue
        run_rows.append(derived_failure_taxonomy_run_row(eval_row))

    detail_rows: list[dict[str, Any]] = []
    if detail_path.exists():
        for raw in read_csv(detail_path):
            run_id = str(raw.get("run_id") or "")
            eval_row = row_by_run_id.get(run_id)
            if eval_row is None or run_id not in run_ids:
                continue
            row = {field: raw.get(field, "") for field in FAILURE_TAXONOMY_DETAIL_FIELDS}
            row["edit_mode"] = eval_row["edit_mode"]
            row["method_label"] = method_paper_label(eval_row["edit_mode"])
            row["method_short_label"] = method_short_label(eval_row["edit_mode"])
            row["run_family"] = eval_row["run_family"]
            row["model"] = eval_row["model"]
            row["instance"] = eval_row["instance"]
            row["prompt"] = eval_row["prompt"]
            row["run_id"] = run_id
            detail_rows.append(row)
    else:
        notes.append(f"Detailed failure taxonomy file missing: {detail_path}")
    detail_run_ids = {row["run_id"] for row in detail_rows}
    for eval_row in evaluation_rows:
        if str(eval_row["run_id"]) in detail_run_ids:
            continue
        derived_detail = derived_execution_failure_detail_row(eval_row)
        if derived_detail is not None:
            detail_rows.append(derived_detail)

    return (
        stable_sort_taxonomy_run_rows(run_rows),
        stable_sort_taxonomy_detail_rows(detail_rows),
        build_failure_taxonomy_summary_rows(detail_rows),
        build_failure_taxonomy_high_level_rows(run_rows),
        notes,
    )


def derived_failure_taxonomy_run_row(eval_row: dict[str, Any]) -> dict[str, Any]:
    non_ok = eval_row.get("run_status") != "ok" or eval_row.get("gpt_reopt_obj") is None
    operation_unavailable = eval_row.get("update_correct") is None
    operation_mismatch = eval_row.get("update_correct") is not True and not operation_unavailable
    objective_unavailable = eval_row.get("gpt_reopt_obj") is None
    objective_mismatch = (
        finite_float(eval_row.get("obj_diff")) is not None
        and abs(finite_float(eval_row.get("obj_diff")) or 0.0) > 1e-8
    )
    if non_ok:
        primary = "non_ok"
    elif operation_mismatch:
        primary = "operation_mismatch"
    elif objective_mismatch:
        primary = "objective_mismatch"
    elif eval_row.get("prompt_satisfied") is not True:
        primary = "prompt_constraint_failure"
    else:
        primary = "clean_pass"
    detailed_modes = ""
    derived_detail = derived_execution_failure_detail_row(eval_row)
    if derived_detail is not None:
        detailed_modes = derived_detail["mode_code"]
    return {
        "run_family": eval_row["run_family"],
        "edit_mode": eval_row["edit_mode"],
        "method_label": method_paper_label(eval_row["edit_mode"]),
        "method_short_label": method_short_label(eval_row["edit_mode"]),
        "model": eval_row["model"],
        "instance": eval_row["instance"],
        "prompt": eval_row["prompt"],
        "run_id": eval_row["run_id"],
        "run_status": eval_row.get("run_status") or "",
        "clean_pass": primary == "clean_pass",
        "any_failure": primary != "clean_pass",
        "primary_category": primary,
        "non_ok": non_ok,
        "objective_mismatch": objective_mismatch,
        "objective_unavailable": objective_unavailable,
        "operation_mismatch": operation_mismatch,
        "operation_unavailable": operation_unavailable,
        "projection_failure": False,
        "objective_close": not objective_mismatch and not objective_unavailable,
        "operation_correct": eval_row.get("update_correct"),
        "operation_projection_state": "",
        "failure_stage": eval_row.get("failure_stage") or "",
        "failure_label": eval_row.get("failure_label") or "",
        "chosen_patch_op": "",
        "detailed_failure_modes": detailed_modes,
    }


def derived_execution_failure_detail_row(eval_row: dict[str, Any]) -> dict[str, Any] | None:
    failure_label = str(eval_row.get("failure_label") or "").strip()
    failure_stage = str(eval_row.get("failure_stage") or "").strip()
    if not failure_label and eval_row.get("run_status") == "ok":
        return None
    if not failure_label:
        failure_label = "Missing or invalid output"
    mode_code = slugify_failure_mode(failure_label)
    return {
        "run_family": eval_row["run_family"],
        "edit_mode": eval_row["edit_mode"],
        "method_label": method_paper_label(eval_row["edit_mode"]),
        "method_short_label": method_short_label(eval_row["edit_mode"]),
        "model": eval_row["model"],
        "instance": eval_row["instance"],
        "prompt": eval_row["prompt"],
        "run_id": eval_row["run_id"],
        "primary_category": "non_ok",
        "is_primary_record": True,
        "mode_group": "execution",
        "mode_code": mode_code,
        "mode_label": failure_label,
        "mode_detail": eval_row.get("failure_detail") or failure_stage,
        "run_status": eval_row.get("run_status") or "",
        "failure_stage": failure_stage,
        "failure_label": failure_label,
    }


def slugify_failure_mode(label: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", label.strip().lower())
    return text.strip("_") or "unknown_failure"


def build_failure_taxonomy_high_level_rows(run_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in run_rows:
        grouped[(row["model"], row["edit_mode"])].append(row)
    output: list[dict[str, Any]] = []
    for (model, edit_mode), group in sorted(grouped.items(), key=lambda item: (model_sort(item[0][0]), item[0][1])):
        output.append(
            {
                "model": model,
                "edit_mode": edit_mode,
                "method_label": method_paper_label(edit_mode),
                "method_short_label": method_short_label(edit_mode),
                "total": len(group),
                "clean_pass": count_flag(group, lambda row: coerce_bool(row.get("clean_pass")) is True),
                "any_failure": count_flag(group, lambda row: coerce_bool(row.get("any_failure")) is True),
                "primary_non_ok": count_flag(group, lambda row: row.get("primary_category") == "non_ok"),
                "primary_projection_failure": count_flag(
                    group, lambda row: row.get("primary_category") == "projection_failure"
                ),
                "primary_operation_mismatch": count_flag(
                    group, lambda row: row.get("primary_category") == "operation_mismatch"
                ),
                "primary_objective_mismatch": count_flag(
                    group, lambda row: row.get("primary_category") == "objective_mismatch"
                ),
                "non_ok": count_flag(group, lambda row: coerce_bool(row.get("non_ok")) is True),
                "objective_mismatch": count_flag(
                    group, lambda row: coerce_bool(row.get("objective_mismatch")) is True
                ),
                "objective_unavailable": count_flag(
                    group, lambda row: coerce_bool(row.get("objective_unavailable")) is True
                ),
                "operation_mismatch": count_flag(
                    group, lambda row: coerce_bool(row.get("operation_mismatch")) is True
                ),
                "operation_unavailable": count_flag(
                    group, lambda row: coerce_bool(row.get("operation_unavailable")) is True
                ),
                "projection_failure": count_flag(
                    group, lambda row: coerce_bool(row.get("projection_failure")) is True
                ),
            }
        )
    return output


def build_failure_taxonomy_summary_rows(detail_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in detail_rows:
        grouped[
            (
                row["model"],
                row["edit_mode"],
                row.get("mode_group", ""),
                row.get("mode_code", ""),
                row.get("mode_label", ""),
            )
        ].append(row)
    output: list[dict[str, Any]] = []
    for key, group in grouped.items():
        model, edit_mode, mode_group, mode_code, mode_label = key
        example = next((row for row in group if row.get("mode_detail")), group[0])
        output.append(
            {
                "model": model,
                "edit_mode": edit_mode,
                "method_label": method_paper_label(edit_mode),
                "method_short_label": method_short_label(edit_mode),
                "mode_group": mode_group,
                "mode_code": mode_code,
                "mode_label": mode_label,
                "primary_records": count_flag(
                    group, lambda row: coerce_bool(row.get("is_primary_record")) is True
                ),
                "record_count": len(group),
                "run_count": len({row.get("run_id") for row in group}),
                "example_run_id": example.get("run_id", ""),
                "example_detail": example.get("mode_detail")
                or example.get("failure_label")
                or example.get("failure_stage")
                or "",
            }
        )
    return sorted(
        output,
        key=lambda row: (
            model_sort(row["model"]),
            row["edit_mode"],
            mode_group_sort(row["mode_group"]),
            -int(row["run_count"]),
            row["mode_code"],
        ),
    )


def stable_sort_taxonomy_run_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            model_sort(row["model"]),
            row["edit_mode"],
            instance_sort(row["instance"]),
            prompt_sort(row["prompt"]),
            row["run_id"],
        ),
    )


def stable_sort_taxonomy_detail_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            model_sort(row["model"]),
            row["edit_mode"],
            instance_sort(row["instance"]),
            prompt_sort(row["prompt"]),
            row["run_id"],
            mode_group_sort(row.get("mode_group", "")),
            row.get("mode_code", ""),
        ),
    )


def mode_group_sort(mode_group: str) -> int:
    order = {"execution": 0, "projection": 1, "operation": 2, "objective": 3}
    return order.get(str(mode_group), 99)


def clean_paper_artifacts(tables_dir: Path, figures_dir: Path) -> None:
    for path in tables_dir.glob("*.tex"):
        path.unlink()
    for pattern in ("cornell_*.pdf", "cornell_*.png"):
        for path in figures_dir.glob(pattern):
            path.unlink()


def exam_instance_metadata(instance: InstanceData) -> dict[str, Any]:
    match = re.search(
        r"blockseq_n(?P<exams>\d+)_blocks(?P<blocks>\d+)_slots(?P<slots>\d+)_seed(?P<seed>\d+)",
        instance.path.name,
    )
    if match is None:
        exams = None
        blocks = len([block for block in instance.blocks if block not in set(instance.virtual_blocks)])
        slots = len(instance.blocks)
    else:
        exams = int(match.group("exams"))
        blocks = int(match.group("blocks"))
        slots = int(match.group("slots"))
    binary, constraints = model_size_counts(instance)
    semesters = {
        "I1": "Spring 2024",
        "I2": "Fall 2023",
        "I3": "Spring 2023",
        "I4": "Fall 2022",
        "I5": "Spring 2022",
    }
    return {
        "instance": instance.instance_id,
        "semester": semesters.get(instance.instance_id, ""),
        "exams": exams,
        "slots": slots,
        "blocks": blocks,
        "binary": binary,
        "constraints": constraints,
    }


def model_size_counts(instance: InstanceData) -> tuple[int | None, int | None]:
    log_path = Path("outputs/solves/base") / f"{instance.path.name}.log"
    if log_path.exists():
        text = log_path.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"Optimize a model with (\d+) rows, (\d+) columns", text)
        if match is None:
            match = re.search(r": (\d+) rows, (\d+) columns", text)
        if match:
            return int(match.group(2)), int(match.group(1))
    n = len(instance.blocks)
    binary = 2 * (n**4) + (n**3)
    constraints = n**4 + 5 * (n**3) + 4 * n + len(instance.large_blocks)
    return binary, constraints


def rows_with_instance_spacing(
    rows: list[dict[str, Any]],
    row_builder,
) -> list[str]:
    output: list[str] = []
    previous_instance: str | None = None
    for row in rows:
        instance = str(row.get("instance") or "")
        if previous_instance is not None and instance != previous_instance:
            output.append("\\addlinespace")
        output.append(" & ".join(row_builder(row)) + " \\\\")
        previous_instance = instance
    return output


def count_true(values: Iterable[Any]) -> int:
    return sum(1 for value in values if coerce_bool(value) is True)


def median_float(values: Iterable[Any]) -> float | None:
    numeric = [finite_float(value) for value in values]
    numeric = [value for value in numeric if value is not None]
    if not numeric:
        return None
    return float(statistics.median(numeric))


def write_exam_instances_table(path: Path, instances: dict[str, InstanceData]) -> None:
    rows: list[dict[str, Any]] = []
    for instance_id in sorted(instances, key=instance_sort):
        instance = instances[instance_id]
        meta = exam_instance_metadata(instance)
        rows.append(meta)
    avg = {
        "exams": mean_float(row["exams"] for row in rows),
        "slots": mean_float(row["slots"] for row in rows),
        "blocks": mean_float(row["blocks"] for row in rows),
        "binary": mean_float(row["binary"] for row in rows),
        "constraints": mean_float(row["constraints"] for row in rows),
    }
    body = [
        " & ".join(
            [
                latex_instance_id(row["instance"], bold=True),
                latex_escape(row["semester"]),
                fmt_int(row["exams"]),
                fmt_int(row["slots"]),
                fmt_int(row["blocks"]),
                fmt_int(row["binary"]),
                fmt_int(row["constraints"]),
            ]
        )
        + " \\\\"
        for row in rows
    ]
    body.append("\\midrule")
    body.append(
        " & ".join(
            [
                "\\multicolumn{2}{c}{\\textbf{Avg}}",
                latex_bold(fmt_int(avg["exams"])),
                latex_bold(fmt_int(avg["slots"])),
                latex_bold(fmt_int(avg["blocks"])),
                latex_bold(fmt_int(avg["binary"])),
                latex_bold(fmt_int(avg["constraints"])),
            ]
        )
        + " \\\\"
    )
    caption = (
        "Calibrated synthetic Cornell exam-scheduling instances. "
        "\\emph{Note.} \\emph{\\# Exams} is the number of exams in the synthetic semester, "
        "\\emph{\\# Slots} is the number of available exam time slots, "
        "\\emph{\\# Blocks} is the number of exam blocks before virtual padding, "
        "\\emph{\\# Binary} is the number of binary decision variables in the generated MIP, "
        "and \\emph{\\# Constraints} is the number of generated MIP constraints."
    )
    write_latex_table_raw(
        path,
        label="exam_instances",
        caption=caption,
        colspec="llrrrrr",
        headers=["Instance", "Semester", "\\# Exams", "\\# Slots", "\\# Blocks", "\\# Binary", "\\# Constraints"],
        body_lines=body,
        size="small",
        resize=True,
    )


def write_main_table(path: Path, rows: list[dict[str, Any]]) -> None:
    headers = [
        "Instance",
        "Prompt",
        "Update correct",
        "Prompt satisfied",
        "ReOpt obj.",
        "Ref. obj.",
        "$\\Delta$obj",
    ]
    body = rows_with_instance_spacing(
        rows,
        lambda row: [
            latex_instance_id(row["instance"]),
            latex_escape(row["prompt"]),
            latex_bool(row.get("update_correct")),
            latex_bool(row.get("prompt_satisfied")),
            fmt_int(row.get("gpt_reopt_obj")),
            fmt_int(row.get("reference_obj")),
            fmt_int(row.get("obj_diff")),
        ],
    )
    caption = (
        "Cornell exam-scheduling results for the default \\emph{ReOpt-LLM-Patch} configuration. "
        "\\emph{Note.} Each row is one instance-prompt case for the default configuration. "
        "\\emph{Update correct} is marked when the encoded model update matches the user's intent. "
        "\\emph{Prompt satisfied} is marked when the final feasible schedule satisfies the update and all prompt-specific constraints. "
        "\\emph{ReOpt obj.} is the objective value of the GPT-selected reoptimization solution, "
        "\\emph{Ref. obj.} is the 3600-second hindsight reference incumbent, and "
        "$\\Delta$obj = ReOpt obj. $-$ Ref. obj.; exact matches have $\\Delta$obj = 0. "
        "Figure~\\ref{fig:cornell_reference_gap} gives the reference-relative percentage view, and "
        "Table~\\ref{tab:cornell_schedule_quality} aggregates schedule quality."
    )
    write_latex_table_raw(
        path,
        label="cornell_main_results",
        caption=caption,
        colspec="llccrrr",
        headers=headers,
        body_lines=body,
        size="small",
    )


def write_schedule_quality_table(path: Path, rows: list[dict[str, Any]]) -> None:
    headers = [
        "Metric",
        "Series",
        "Overall",
        "P1",
        "P2",
        "P3",
        "P4",
        "P5",
        "P6",
    ]
    rows_by_group = {row["group"]: row for row in rows}
    groups = ["overall", *PROMPTS]
    metric_specs = [
        ("Triples", "triples"),
        ("Back-to-back", "b2b"),
        ("Two in 24 hours", "two_in_24hr"),
        ("Three in four slots", "three_in_4"),
    ]
    series_specs = [("ReOpt", "gpt"), ("Ref.", "reference"), ("$\\Delta$", "delta")]
    body: list[str] = []
    for metric_idx, (metric_label, key) in enumerate(metric_specs):
        if metric_idx:
            body.append("\\addlinespace")
        for series_label, series_key in series_specs:
            cells = [fmt_float1(rows_by_group[group].get(f"{key}_{series_key}")) for group in groups]
            body.append(" & ".join([metric_label, series_label, *cells]) + " \\\\")
    caption = (
        "Schedule-quality comparison for the default \\emph{ReOpt-LLM-Patch} configuration. "
        "\\emph{Note.} Entries are means over all 30 default-config cases in the Overall column "
        "and over five instances in each prompt column. "
        "\\emph{ReOpt} reports the GPT-selected solution, \\emph{Ref.} reports the 3600-second hindsight reference incumbent, "
        "and $\\Delta$ is ReOpt $-$ Ref. "
        "\\emph{Triples} counts three exams in three consecutive slots, \\emph{Back-to-back} counts consecutive exams, "
        "\\emph{Two in 24 hours} counts exam pairs within 24 hours, and "
        "\\emph{Three in four slots} counts triples inside four-slot windows."
    )
    write_latex_table_raw(
        path,
        label="cornell_schedule_quality",
        caption=caption,
        colspec="llrrrrrrr",
        headers=headers,
        body_lines=body,
        resize=True,
    )


def write_patch_vs_code_table(path: Path, rows: list[dict[str, Any]]) -> None:
    headers = [
        "Model",
        "Method",
        "Update correctness",
        "Prompt satisfaction",
        "First-attempt success",
        "Final success",
    ]
    body: list[str] = []
    for model_idx, model in enumerate(GPT_MODELS):
        if model_idx:
            body.append("\\addlinespace")
        for edit_mode in ("coedit-auto", "patch-edit-auto"):
            group = [row for row in rows if row["model"] == model and row["edit_mode"] == edit_mode]
            n = len(group) or 1
            body.append(
                " & ".join(
                    [
                        latex_escape(model),
                        latex_method_label(edit_mode),
                        fmt_percent_value(count_true(row.get("update_correct") for row in group) / n),
                        fmt_percent_value(count_true(row.get("prompt_satisfied") for row in group) / n),
                        fmt_percent_value(
                            sum(
                                1
                                for row in group
                                if row.get("prompt_satisfied") is True
                                and finite_int(row.get("success_on_attempt")) == 1
                            )
                            / n
                        ),
                        fmt_percent_value(count_true(row.get("prompt_satisfied") for row in group) / n),
                    ]
                )
                + " \\\\"
            )
    caption = (
        "Baseline comparison between the \\emph{Direct-Code Agent} and \\emph{ReOpt-LLM-Patch} in the Cornell evaluation. "
        "\\emph{Note.} Each row aggregates 30 prompt-instance cases (five instances $\\times$ six prompts). "
        "Entries are the percentage of those 30 cases that satisfy the corresponding criterion. "
        "\\emph{Update correctness} requires that the model update encoded by the LLM match the user's intent. "
        "\\emph{Prompt satisfaction} additionally requires the final schedule to satisfy all prompt-specific constraints. "
        "\\emph{First-attempt success} requires both criteria on the first attempt, and "
        "\\emph{Final success} requires both criteria after the retry budget is applied. "
        "The criteria are nested, so the reported rates are non-increasing from left to right."
    )
    write_latex_table_raw(
        path,
        label="cornell_patch_vs_code",
        caption=caption,
        colspec="llrrrr",
        headers=headers,
        body_lines=body,
        size="small",
        resize=True,
    )


def write_failure_table(path: Path, rows: list[dict[str, Any]]) -> None:
    filtered = [
        row for row in rows if row["edit_mode"] in {"coedit-auto", "patch-edit-auto"}
    ]
    by_config = {(row["model"], row["edit_mode"]): row for row in filtered}
    headers = [
        "Model",
        "Variant",
        "Wrong comp.",
        "Invalid patch",
        "Bad update",
        "Infeasible",
        "Prompt viol.",
        "Missing output",
    ]
    body: list[str] = []
    for model_idx, model in enumerate(GPT_MODELS):
        if model_idx:
            body.append("\\addlinespace")
        for edit_mode in ("coedit-auto", "patch-edit-auto"):
            row = by_config[(model, edit_mode)]
            body.append(
                " & ".join(
                    [
                        latex_escape(model),
                        latex_method_label(edit_mode),
                        str(row["wrong_component"]),
                        str(row["invalid_patch"]),
                        str(row["incorrect_update"]),
                        str(row["infeasible_solution"]),
                        str(row["prompt_constraint_violation"]),
                        str(row["missing_invalid_output"]),
                    ]
                )
                + " \\\\"
            )
    caption = (
        "Failure modes for the \\emph{Direct-Code Agent} and \\emph{ReOpt-LLM-Patch} in the Cornell evaluation. "
        "\\emph{Note.} Entries are counts out of 30 prompt-instance cases per row, and the same case can appear in multiple columns. "
        "\\emph{Wrong comp.} means the edited component was not the component required by the prompt. "
        "\\emph{Invalid patch} means the patch failed structural validation. "
        "\\emph{Bad update} means the encoded update did not match the user's intent. "
        "\\emph{Infeasible} means no feasible final schedule was returned. "
        "\\emph{Prompt viol.} means at least one prompt-specific constraint was violated. "
        "\\emph{Missing output} means no usable solution was returned. "
        "General model violations and retry-exhausted cases are zero throughout."
    )
    write_latex_table_raw(
        path,
        label="cornell_failure_modes",
        caption=caption,
        colspec="llrrrrrr",
        headers=headers,
        body_lines=body,
        resize=True,
    )


def write_selector_ablation_table(path: Path, rows: list[dict[str, Any]]) -> None:
    headers = [
        "Variant",
        "Final success",
        "No incumbent",
        "Mean $\\Delta$obj",
        "Median $\\Delta$obj",
        "Mean ref. gap",
        "Median ref. gap",
    ]
    body: list[str] = []
    for edit_mode in ("patch-edit-auto", "patch-edit-scratch"):
        group = [
            row for row in rows if row["model"] == DEFAULT_MODEL and row["edit_mode"] == edit_mode
        ]
        diffs = [finite_float(row.get("obj_diff")) for row in group]
        gaps = [finite_float(row.get("ref_gap_pct")) for row in group]
        diffs = [value for value in diffs if value is not None]
        gaps = [value for value in gaps if value is not None]
        no_incumbent = sum(1 for row in group if finite_float(row.get("gpt_reopt_obj")) is None)
        body.append(
            " & ".join(
                [
                    latex_method_label(edit_mode),
                    fmt_percent(count_true(row.get("prompt_satisfied") for row in group) / (len(group) or 1)),
                    str(no_incumbent),
                    fmt_float1(mean_float(diffs)),
                    fmt_float1(median_float(diffs)),
                    fmt_pct(mean_float(gaps)),
                    fmt_pct(median_float(gaps)),
                ]
            )
            + " \\\\"
        )
    caption = (
        "Effect of the LLM-guided toolbox selector on the default exam-scheduling configuration. "
        "\\emph{Note.} Both rows use gpt-5 over 30 prompt-instance cases. "
        "\\emph{Final success} is the percentage of cases satisfying the prompt after retries. "
        "\\emph{No incumbent} counts cases with no feasible incumbent. "
        "\\emph{Mean $\\Delta$obj} and \\emph{Median $\\Delta$obj} summarize ReOpt obj. $-$ Ref. obj. "
        "\\emph{Mean ref. gap} and \\emph{Median ref. gap} summarize the reference-relative percentage gap. "
        "Objective and gap summaries exclude no-incumbent cases; lower is better in every column."
    )
    write_latex_table_raw(
        path,
        label="cornell_selector_ablation",
        caption=caption,
        colspec="lrrrrrr",
        headers=headers,
        body_lines=body,
        size="small",
        resize=True,
    )


def write_selector_ablation_paired_table(path: Path, rows: list[dict[str, Any]]) -> None:
    headers = [
        "Model",
        "Common valid cases",
        "Selector lower obj.",
        "Median obj. improvement",
        "Mean obj. improvement",
    ]
    body: list[str] = []
    for model in GPT_MODELS:
        auto = {
            (row["instance"], row["prompt"]): row
            for row in rows
            if row["model"] == model and row["edit_mode"] == "patch-edit-auto"
        }
        scratch = {
            (row["instance"], row["prompt"]): row
            for row in rows
            if row["model"] == model and row["edit_mode"] == "patch-edit-scratch"
        }
        improvements: list[float] = []
        for key in sorted(set(auto) & set(scratch), key=lambda item: (instance_sort(item[0]), prompt_sort(item[1]))):
            a = auto[key]
            s = scratch[key]
            if a.get("prompt_satisfied") is not True or s.get("prompt_satisfied") is not True:
                continue
            auto_obj = finite_float(a.get("gpt_reopt_obj"))
            scratch_obj = finite_float(s.get("gpt_reopt_obj"))
            if auto_obj is None or scratch_obj is None:
                continue
            improvements.append(scratch_obj - auto_obj)
        lower = sum(1 for value in improvements if value > 0)
        body.append(
            " & ".join(
                [
                    latex_escape(model),
                    str(len(improvements)),
                    f"{lower}/{len(improvements)}",
                    fmt_float1(median_float(improvements)),
                    fmt_float1(mean_float(improvements)),
                ]
            )
            + " \\\\"
        )
    caption = (
        "Paired objective improvement from LLM-guided toolbox selection in the Cornell evaluation. "
        "\\emph{Note.} \\emph{Common valid cases} counts instance-prompt pairs where both selector and no-selector variants satisfy the prompt. "
        "\\emph{Selector lower obj.} is the number of common valid cases where the selector objective is lower, reported over the common-case denominator. "
        "\\emph{Median obj. improvement} and \\emph{Mean obj. improvement} summarize no-selector obj. $-$ selector obj.; positive values favor the selector."
    )
    write_latex_table_raw(
        path,
        label="cornell_selector_ablation_paired",
        caption=caption,
        colspec="lrrrr",
        headers=headers,
        body_lines=body,
        size="small",
        resize=True,
    )


def build_metadata(
    *,
    args: argparse.Namespace,
    selected_model: str,
    selected_mode: str,
    selected_metrics: dict[str, Any],
    evaluated_rows: list[dict[str, Any]],
    missing_notes: list[str],
    plot_paths: list[str],
) -> dict[str, Any]:
    return {
        "input_paths": {
            "runs_dir": str(Path(args.runs_dir)),
            "reference_dir": str(Path(args.reference_dir)),
            "output_dir": str(Path(args.output_dir)),
            "tables_dir": str(Path(args.tables_dir)),
            "figures_dir": str(Path(args.figures_dir)),
        },
        "selected_model": selected_model,
        "selected_edit_mode": selected_mode,
        "selected_method_label": method_paper_label(selected_mode),
        "selected_method_short_label": method_short_label(selected_mode),
        "selection_metrics": clean_json(selected_metrics),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "git_commit_hash": git_commit_hash(),
        "number_of_evaluated_runs": len(evaluated_rows),
        "prompts_included": list(PROMPTS),
        "models_included": list(GPT_MODELS),
        "method_labels": METHOD_PAPER_LABELS,
        "method_short_labels": METHOD_SHORT_LABELS,
        "reference_label": REFERENCE_PAPER_LABEL,
        "reference_short_label": REFERENCE_SHORT_LABEL,
        "time_limit_sec": TIME_LIMIT_SEC,
        "missing_metrics_assumptions": sorted(set(missing_notes + default_assumptions())),
        "plot_paths": plot_paths,
    }


def default_assumptions() -> list[str]:
    return [
        "P1/P3/P5 prompt-specific violations are recovered from saved final block schedules.",
        "P2/P4 prompt-specific compliance is treated as an applied-update check from operation semantic logs.",
        "General model violations are schedule-level assignment violations recoverable from saved block schedules.",
        "Two-in-24h counts sum pair co-enrollments for slot separations up to slots_per_day.",
        "Three-in-4 counts sum all three-block combinations inside each four-slot window.",
        "Integrality metrics are zero when an integral block schedule is saved; raw solver variable integrality is not logged.",
        "P6 is evaluated as the composed P4 -> P2 -> P1 update; prompt-specific satisfaction requires operation correctness and the P1 reserved-slot check.",
        "Patch-edit-scratch is included only as an ablation to test the LLM selector.",
        "Failure taxonomy CSVs preserve prior high-level and lower-level taxonomy records when failure_analysis_all_runs.csv and failure_modes_detailed.csv are present.",
    ]


def solver_status_from_payload(payload: dict[str, Any]) -> str:
    status = nested_get(payload, "result", "steps", 0, "evaluation", "details", "status")
    if status is None:
        status = nested_get(payload, "result", "steps", 0, "evaluation", "status")
    code = finite_int(status)
    if code is not None:
        return STATUS_CODE_NAMES.get(code, str(code))
    if payload.get("status") == "error":
        return str(payload.get("failure_label") or payload.get("failure_stage") or "error")
    return str(payload.get("status") or "")


def extract_schedule(payload: dict[str, Any]) -> dict[int, int] | None:
    candidates = [
        nested_get(payload, "result", "steps", 0, "planner_output", "annotations", "solution"),
        nested_get(payload, "result", "steps", 0, "solution"),
        nested_get(payload, "result", "steps", 0, "evaluation", "details", "solution"),
        nested_get(payload, "result", "steps", 0, "validator_summary", "solution"),
        payload.get("solution"),
    ]
    for candidate in candidates:
        schedule = parse_schedule(candidate)
        if schedule:
            return schedule
    trace_dir = payload.get("trace_dir")
    if trace_dir:
        validator = Path(str(trace_dir)) / "validator_summary.json"
        if validator.exists():
            return parse_schedule(load_json(validator).get("solution"))
    return None


def parse_schedule(value: Any) -> dict[int, int] | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    if not isinstance(value, dict):
        return None
    schedule: dict[int, int] = {}
    for raw_block, raw_slot in value.items():
        try:
            block = int(raw_block)
            slot = int(raw_slot)
        except (TypeError, ValueError):
            return None
        schedule[block] = slot
    return schedule or None


def infer_run_family(payload: dict[str, Any], path: Path) -> str:
    run_mode = str(nested_get(payload, "metadata", "run_mode") or "").strip().lower()
    strategy = str(payload.get("strategy") or nested_get(payload, "input", "strategy") or "").strip().lower()
    planner = str(payload.get("planner_mode") or "").strip().lower()
    result_parent = path.parents[1].name.lower()
    combined = " ".join([run_mode, strategy, planner, result_parent])
    if "patchedit-scratch" in combined or "scratch" in result_parent:
        return "patchedit_scratch"
    if "codeedit" in combined:
        return "codeedit_auto"
    if "patchedit" in combined:
        return "patchedit_auto"
    return result_parent


def empty_quality() -> dict[str, Any]:
    return {
        "triple_count": None,
        "back_to_back_count": None,
        "two_in_24hr_count": None,
        "three_in_4_slots_count": None,
    }


def penultimate_evening_slot(instance: InstanceData) -> int:
    evening_index = instance.slots_per_day - 1
    evening_slots = [
        slot for slot in sorted(instance.blocks) if (slot - 1) % instance.slots_per_day == evening_index
    ]
    return evening_slots[-2] if len(evening_slots) >= 2 else evening_slots[-1]


def p3_cutoff_exclusive(instance: InstanceData) -> int:
    required_cutoff = len(instance.large_blocks) + 1
    cutoff = max(15, required_cutoff)
    return min(cutoff, max(instance.blocks) + 1)


def pair_count(instance: InstanceData, a: int, b: int) -> float:
    return float(instance.pair_counts.get((int(a), int(b)), instance.pair_counts.get((int(b), int(a)), 0.0)))


def triplet_count(instance: InstanceData, triple: Iterable[int]) -> float:
    values = tuple(int(x) for x in triple)
    if values in instance.triplet_counts:
        return float(instance.triplet_counts[values])
    for permutation in itertools.permutations(values):
        if permutation in instance.triplet_counts:
            return float(instance.triplet_counts[permutation])
    return 0.0


def blocks_for_slots(slot_to_block: dict[int, int], start: int, width: int, max_slot: int) -> tuple[int, ...] | None:
    slots = list(range(int(start), int(start) + int(width)))
    if slots[-1] > max_slot:
        return None
    if any(slot not in slot_to_block for slot in slots):
        return None
    return tuple(slot_to_block[slot] for slot in slots)


def invert_schedule(block_to_slot: dict[int, int]) -> dict[int, int]:
    return {int(slot): int(block) for block, slot in block_to_slot.items()}


def parse_instance_id(dirname: str) -> str:
    known = {
        "blockseq_n544_blocks20_slots24_seed42": "I1",
        "blockseq_n601_blocks18_slots24_seed42": "I2",
        "blockseq_n553_blocks17_slots25_seed42": "I3",
        "blockseq_n588_blocks19_slots24_seed42": "I4",
        "blockseq_n539_blocks16_slots24_seed42": "I5",
    }
    return known.get(dirname, dirname)


def infer_instance_from_run_id(run_id: str) -> str:
    match = re.match(r"^(I\d+)_", run_id)
    return match.group(1) if match else ""


def normalize_model(value: Any) -> str:
    text = str(value or "").strip()
    aliases = {
        "gpt41mini": "gpt-4.1-mini",
        "gpt-4.1-mini": "gpt-4.1-mini",
        "gpt41": "gpt-4.1",
        "gpt-4.1": "gpt-4.1",
        "gpt5": "gpt-5",
        "gpt-5": "gpt-5",
    }
    return aliases.get(text, aliases.get(text.lower(), text))


def normalize_prompt(value: Any) -> str:
    text = str(value or "").strip().upper()
    match = re.search(r"P\d+", text)
    return match.group(0) if match else text


def read_block_summary(path: Path) -> dict[int, float]:
    rows = read_csv(path)
    return {int(row["block"]): float(row["block_enrollment"]) for row in rows}


def read_count_map(path: Path, *, arity: int) -> dict[tuple[int, ...], float]:
    counts: dict[tuple[int, ...], float] = {}
    for row in read_csv(path):
        if arity == 2:
            key = (int(row["block_i"]), int(row["block_j"]))
        else:
            key = (int(row["block_i"]), int(row["block_j"]), int(row["block_k"]))
        counts[key] = float(row["count"])
    return counts


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: csv_value(row.get(field)) for field in fields})


def write_latex_table_raw(
    path: Path,
    *,
    label: str,
    caption: str,
    colspec: str,
    headers: list[str],
    body_lines: list[str],
    size: str = "scriptsize",
    resize: bool = False,
) -> None:
    lines = [
        f"\\begin{{tabular}}{{{colspec}}}",
        "\\toprule",
        " & ".join(latex_header_cell(header) for header in headers) + " \\\\",
        "\\midrule",
    ]
    lines.extend(body_lines)
    lines.extend(["\\bottomrule", "\\end{tabular}"])
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_reference_gap_plot(
    figures_dir: Path,
    selected_rows: list[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    notes: list[str] = []
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        try:
            import pandas as pd
            import seaborn as sns

            sns.set_theme(style="whitegrid", context="paper", font_scale=1.05)
        except Exception as exc:  # pragma: no cover - optional style dependency
            pd = None
            sns = None
            notes.append(f"Seaborn styling unavailable; used matplotlib defaults: {exc}")
    except Exception as exc:  # pragma: no cover - environment dependent
        return [], [f"Reference-gap plot not generated because matplotlib is unavailable: {exc}"]

    instances = sorted({row["instance"] for row in selected_rows}, key=instance_sort)
    prompts = list(PROMPTS)
    gap_matrix = [
        [
            finite_float(find_row(selected_rows, instance, prompt).get("ref_gap_pct")) or 0.0
            for prompt in prompts
        ]
        for instance in instances
    ]
    max_abs_gap = max([abs(value) for row in gap_matrix for value in row] + [1.0])

    fig, ax = plt.subplots(figsize=(6.7, 3.9))
    if sns is not None and pd is not None:
        sns.heatmap(
            pd.DataFrame(gap_matrix, index=[plot_instance_id(instance) for instance in instances], columns=prompts),
            ax=ax,
            annot=True,
            fmt=".1f",
            cmap="vlag",
            center=0,
            vmin=-max_abs_gap,
            vmax=max_abs_gap,
            linewidths=0.8,
            linecolor="white",
            cbar_kws={"label": "Ref. gap (%)"},
        )
        ax.set_yticklabels(ax.get_yticklabels(), rotation=0)
    else:
        image = ax.imshow(gap_matrix, cmap="coolwarm", vmin=-max_abs_gap, vmax=max_abs_gap)
        ax.set_xticks(range(len(prompts)), prompts)
        ax.set_yticks(range(len(instances)), [plot_instance_id(instance) for instance in instances])
        for i, _instance in enumerate(instances):
            for j, _prompt in enumerate(prompts):
                ax.text(j, i, f"{gap_matrix[i][j]:.1f}", ha="center", va="center", color="black")
        fig.colorbar(image, ax=ax, fraction=0.046, pad=0.04, label="Ref. gap (%)")
    ax.set_title("Reference-relative objective gap")
    ax.set_xlabel("Prompt")
    ax.set_ylabel("Instance")
    fig.tight_layout()

    source_path = figures_dir / "cornell_reference_gap.pdf"
    fig.savefig(source_path, bbox_inches="tight")
    plt.close(fig)
    return [str(source_path)], notes


def find_row(rows: list[dict[str, Any]], instance: str, prompt: str) -> dict[str, Any]:
    for row in rows:
        if row["instance"] == instance and row["prompt"] == prompt:
            return row
    return {}


def stable_sort_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: (instance_sort(row.get("instance")), prompt_sort(row.get("prompt")), model_sort(row.get("model")), row.get("edit_mode", ""), row.get("run_id", "")))


def instance_sort(value: Any) -> int:
    match = re.search(r"\d+", str(value or ""))
    return int(match.group(0)) if match else 9999


def prompt_sort(value: Any) -> int:
    match = re.search(r"\d+", str(value or ""))
    return int(match.group(0)) if match else 9999


def model_sort(value: Any) -> int:
    order = {model: idx for idx, model in enumerate(GPT_MODELS)}
    return order.get(str(value), 999)


def method_paper_label(edit_mode: Any) -> str:
    return METHOD_PAPER_LABELS.get(str(edit_mode), str(edit_mode))


def method_short_label(edit_mode: Any) -> str:
    return METHOD_SHORT_LABELS.get(str(edit_mode), str(edit_mode))


def latex_instance_id(value: Any, *, bold: bool = False) -> str:
    match = re.search(r"\d+", str(value or ""))
    text = f"EXAM$_{match.group(0)}$" if match else latex_escape(value)
    return f"\\textbf{{{text}}}" if bold else text


def plot_instance_id(value: Any) -> str:
    match = re.search(r"\d+", str(value or ""))
    return f"EXAM$_{match.group(0)}$" if match else str(value)


def latex_method_label(edit_mode: Any) -> str:
    return f"\\emph{{{latex_escape(method_paper_label(edit_mode))}}}"


def latex_bold(value: str) -> str:
    return f"\\textbf{{{value}}}"


def normalize_edit_mode_arg(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    normalized_text = text.lower()
    for edit_mode in RUN_FAMILY_LABELS.values():
        candidates = {
            edit_mode.lower(),
            method_paper_label(edit_mode).lower(),
            method_short_label(edit_mode).lower(),
        }
        if normalized_text in candidates:
            return edit_mode
    return text


def latex_escape(value: Any) -> str:
    text = str(value)
    return (
        text.replace("\\", "\\textbackslash{}")
        .replace("_", "\\_")
        .replace("%", "\\%")
        .replace("&", "\\&")
        .replace("#", "\\#")
    )


def latex_bool(value: Any) -> str:
    bool_value = coerce_bool(value)
    if bool_value is True:
        return "\\checkmark"
    if bool_value is False:
        return "$\\times$"
    return "--"


def latex_header_cell(value: str) -> str:
    return f"\\textbf{{{value}}}"


def fmt_pct(value: Any) -> str:
    numeric = finite_float(value)
    return "--" if numeric is None else f"{format_numeric(numeric, digits=1)}\\%"


def fmt_percent(value: Any) -> str:
    numeric = finite_float(value)
    return "--" if numeric is None else f"{format_numeric(100 * numeric, digits=1)}\\%"


def fmt_percent_value(value: Any) -> str:
    numeric = finite_float(value)
    return "--" if numeric is None else format_numeric(100 * numeric, digits=1)


def fmt_int(value: Any) -> str:
    numeric = finite_float(value)
    return "--" if numeric is None else format_numeric(numeric, integer=True)


def fmt_float1(value: Any) -> str:
    numeric = finite_float(value)
    return "--" if numeric is None else format_numeric(numeric, digits=1)


def format_numeric(value: float, *, integer: bool = False, digits: int = 1) -> str:
    if integer:
        rounded = int(round(value))
        text = f"{rounded:,}" if abs(rounded) >= 10000 else str(rounded)
        return latex_number(text)
    text = f"{value:,.{digits}f}" if abs(value) >= 10000 else f"{value:.{digits}f}"
    return latex_number(text)


def latex_number(text: str) -> str:
    return text.replace(",", "{,}")


def csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return ""
        return f"{value:.12g}"
    return value


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def nested_get(payload: Any, *keys: Any) -> Any:
    current = payload
    for key in keys:
        if isinstance(key, int):
            if not isinstance(current, list) or len(current) <= key:
                return None
            current = current[key]
        else:
            if not isinstance(current, dict):
                return None
            current = current.get(key)
    return current


def coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return None


def finite_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric) or math.isinf(numeric):
        return None
    return numeric


def finite_int(value: Any) -> int | None:
    numeric = finite_float(value)
    if numeric is None:
        return None
    return int(numeric)


def first_finite_int(*values: Any) -> int | None:
    for value in values:
        numeric = finite_int(value)
        if numeric is not None:
            return numeric
    return None


def mean_bool(values: Iterable[Any]) -> float | None:
    bools = [coerce_bool(value) for value in values]
    present = [value for value in bools if value is not None]
    if not present:
        return None
    return sum(1 for value in present if value) / len(present)


def mean_float(values: Iterable[Any]) -> float | None:
    present = [finite_float(value) for value in values]
    present = [value for value in present if value is not None]
    if not present:
        return None
    return sum(present) / len(present)


def positive(value: Any) -> bool:
    numeric = finite_float(value)
    return numeric is not None and numeric > 0


def count_flag(rows: list[dict[str, Any]], predicate: Any) -> int:
    return sum(1 for row in rows if predicate(row))


def clean_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): clean_json(val) for key, val in value.items()}
    if isinstance(value, list):
        return [clean_json(item) for item in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def git_commit_hash() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except Exception:
        return None
    return result.stdout.strip() or None


if __name__ == "__main__":
    main()
