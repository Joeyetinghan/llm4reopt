#!/usr/bin/env python3
"""Replay patchedit-auto patch plans through an exam execution route.

This is intentionally exam-campaign tooling, not a new framework execution
mode. Normal runs still plan end-to-end. The replay path reuses the normalized
patch action sets produced by a source patchedit-auto run and only varies the
execution route, which is useful for selector ablations in the full exam runs.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from framework.core import (
    DeltaRequest,
    Patch,
    PatchOp,
    PlannedActionSet,
    PlannerOutput,
    ProblemRunResult,
    ReoptResult,
)
from framework.core.patches import apply_patch_sequence
from framework.evaluation import build_evaluation_summary, evaluate_result_payload
from framework.execution.batch import (
    BaseSolveCacheEntry,
    ExperimentRun,
    _build_base_context,
    _error_detail_payload,
    _ground_truth_payload,
    _result_detail_fields,
    _run_input_payload,
    _summary_row_from_detail,
)
from framework.execution.reporter import FileRunReporter
from framework.execution.step_runner import (
    build_change_report,
    planner_output_to_event,
    select_strategy,
    selected_patches,
    validate_and_solve,
)
from framework.registry import load_problem


DEFAULT_REPLAY_STRATEGY = "scratch"
DEFAULT_REPLAY_POLICY = "rule"
SUMMARY_FIELDS = [
    "run_id",
    "problem",
    "instance_id",
    "prompt_id",
    "model",
    "planner_mode",
    "strategy",
    "strategy_policy",
    "execution_label",
    "status",
    "base_objective",
    "objective",
    "cost_delta",
    "feasible",
    "runtime",
    "obj_bound",
    "mip_gap",
    "chosen_patch_op",
    "failure_stage",
    "failure_label",
    "failure_detail",
    "selection_fallback_used",
    "trace_dir",
    "ground_truth_status",
    "matches_ground_truth",
    "semantic_verdict",
    "reference_verdict",
    "overall_verdict",
    "schedule_match",
    "candidate_obj_lb",
    "candidate_obj_ub",
    "reference_obj_lb",
    "reference_obj_ub",
    "result_path",
    "error",
]


def replay_source_result(
    source_result_path: str | Path,
    *,
    output_dir: str | Path,
    trace_root: str | Path | None = None,
    strategy: str = DEFAULT_REPLAY_STRATEGY,
    solver_time_limit: int | None = None,
    evaluate_ground_truth: bool = False,
    reference_policy: str = "if_available",
    overwrite: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_path = Path(source_result_path).expanduser().resolve()
    output_root = Path(output_dir).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    runs_dir = output_root / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    source_payload = _load_json(source_path)
    run = _replay_run_from_source(
        source_payload,
        source_path=source_path,
        trace_root=trace_root or output_root / "traces",
        strategy=strategy,
        solver_time_limit=solver_time_limit,
    )
    detail_path = runs_dir / f"{run.run_id}.json"
    if detail_path.exists() and not overwrite:
        raise FileExistsError(f"Replay result already exists: {detail_path}")

    spec = None
    try:
        if str(source_payload.get("status") or "").lower() != "ok":
            raise RuntimeError("Source patchedit-auto run is not ok; no patch plan is available to replay.")

        spec, adapter = load_problem(
            problem=run.problem if run.problem_root is None else None,
            problem_root=run.problem_root,
            config_path=run.config_path,
            config_mapping=run.config_mapping,
            examples_path=run.examples_path,
        )
        spec.config_metadata["planner_mode"] = "patchedit"
        spec.config_metadata["trace_root"] = run.trace_root
        spec.config_metadata["strategy_policy"] = DEFAULT_REPLAY_POLICY

        base_context = _base_context_from_source(source_payload, adapter, spec)
        detail_payload, ground_truth = _execute_replay(
            run,
            spec,
            adapter,
            base_context,
            source_payload=source_payload,
            source_path=source_path,
            evaluate_ground_truth=evaluate_ground_truth,
            reference_policy=reference_policy,
        )
    except Exception as exc:
        detail_payload = _error_detail_payload(run, spec, exc)
        ground_truth = None
        _attach_replay_metadata(detail_payload, source_payload=source_payload, source_path=source_path)

    detail_path.write_text(
        json.dumps(detail_payload, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    summary_row = _summary_row_from_detail(detail_payload, ground_truth=ground_truth)
    summary_row["result_path"] = str(detail_path)
    return detail_payload, summary_row


def replay_many(
    source_result_paths: list[str | Path],
    *,
    output_dir: str | Path,
    trace_root: str | Path | None = None,
    strategy: str = DEFAULT_REPLAY_STRATEGY,
    solver_time_limit: int | None = None,
    evaluate_ground_truth: bool = False,
    reference_policy: str = "if_available",
    overwrite: bool = False,
) -> dict[str, Any]:
    output_root = Path(output_dir).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    summary_rows: list[dict[str, Any]] = []
    hard_error_count = 0
    for index, source in enumerate(source_result_paths, start=1):
        try:
            detail, summary = replay_source_result(
                source,
                output_dir=output_root,
                trace_root=trace_root,
                strategy=strategy,
                solver_time_limit=solver_time_limit,
                evaluate_ground_truth=evaluate_ground_truth,
                reference_policy=reference_policy,
                overwrite=overwrite,
            )
        except Exception as exc:
            hard_error_count += 1
            print(f"[{index}/{len(source_result_paths)}] {source}: error {exc}")
            continue
        summary_rows.append(summary)
        print(
            f"[{index}/{len(source_result_paths)}] {detail.get('run_id')}: "
            f"{detail.get('status')} source={Path(source).name}"
        )

    summary_csv = output_root / "summary.csv"
    _write_csv(summary_rows, summary_csv, fieldnames=SUMMARY_FIELDS)
    summary_payload = {
        "output_dir": str(output_root),
        "total_runs": len(source_result_paths),
        "ok_runs": sum(1 for row in summary_rows if row.get("status") == "ok"),
        "error_runs": sum(1 for row in summary_rows if row.get("status") == "error") + hard_error_count,
        "summary_csv": str(summary_csv),
        "rows": summary_rows,
    }
    (output_root / "summary.json").write_text(
        json.dumps(summary_payload, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    return summary_payload


def _execute_replay(
    run: ExperimentRun,
    spec,
    adapter,
    base_context: BaseSolveCacheEntry,
    *,
    source_payload: dict[str, Any],
    source_path: Path,
    evaluate_ground_truth: bool,
    reference_policy: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    delta_request = DeltaRequest(text=run.delta_text, metadata=dict(run.metadata))
    base_model = base_context.base_model.copy()
    prompt_context = adapter.build_prompt_context(spec, base_model, delta_request)
    representation = prompt_context.model_representation
    reporter = FileRunReporter(trace_root=run.trace_root)
    trace = reporter.start_step(spec, delta_request, representation)
    trace.update(
        {
            "run_id": run.run_id,
            "replay_source_result_path": str(source_path),
            "replay_source_run_id": source_payload.get("run_id"),
            "steps": [],
        }
    )

    planner_output, action_sets = _load_replayed_plan(source_payload, source_path=source_path)
    event = planner_output_to_event(planner_output, delta_request)
    candidate_patches = [
        action
        for action_set in action_sets
        if action_set.action_kind == "patch"
        for action in action_set.actions
        if isinstance(action, Patch)
    ]
    planner_output.planning_hints["planner_parse_ok"] = True
    planner_output.planning_hints["planner_output_executable"] = bool(action_sets)
    planner_output.planning_hints["planner_failed_semantically"] = False
    planner_output.planning_hints["model_attempt_count"] = 0
    planner_output.planning_hints["model_retry_count"] = 0
    planner_output.annotations["replayed_from_result_path"] = str(source_path)
    planner_output.annotations["replayed_from_run_id"] = source_payload.get("run_id")

    reporter.record_planner_step(
        trace,
        planner_output=planner_output,
        normalized_action_sets=action_sets,
        normalized_patches=candidate_patches,
    )
    strategy_selection = select_strategy(
        spec,
        delta_request,
        planner_output,
        requested=run.strategy_name,
        prior_result=None,
        warm_start_available=False,
        strategy_policy=None,
    )
    strategy_obj = strategy_selection.strategy
    execution_options = adapter.resolve_execution_options(
        spec,
        strategy_selection,
        None,
        warm_start_meta=None,
        prior_result=None,
        delta_request=delta_request,
        planner_output=planner_output,
    )
    attempt_trace_dir = Path(trace["step_dir"]) / "replay_attempt_01"
    validator = adapter._build_validator(spec)
    try:
        chosen_patch, objective, solution, solve_meta = validate_and_solve(
            validator,
            action_sets,
            base_model.copy(),
            adapter.build_env(spec),
            warm_start=execution_options.warm_start,
            solve_context=execution_options.solve_context,
            trace_dir=attempt_trace_dir,
        )
    except Exception as exc:
        artifacts = reporter.finalize_failure_step(
            trace,
            prompt_context=prompt_context,
            planner_output=planner_output,
            normalized_action_sets=action_sets,
            normalized_patches=candidate_patches,
            strategy=strategy_obj,
            strategy_selection=strategy_selection,
            objective_before=base_context.base_objective,
            error=exc,
        )
        _attach_replay_exception_context(
            exc,
            trace=trace,
            artifacts=artifacts,
            planner_output=planner_output,
            strategy_selection=strategy_selection,
        )
        raise

    chosen_patches = selected_patches(validator, chosen_patch)
    model_after = apply_patch_sequence(base_model, chosen_patches)
    artifacts = reporter.finalize_step(
        trace,
        prompt_context=prompt_context,
        planner_output=planner_output,
        normalized_action_sets=action_sets,
        normalized_patches=candidate_patches,
        chosen_patches=chosen_patches,
        strategy=strategy_obj,
        strategy_selection=strategy_selection,
        objective_before=base_context.base_objective,
        objective_after=objective,
        solve_meta=solve_meta,
        solution=solution,
    )

    step = ReoptResult(
        delta_request=delta_request,
        prompt_context=prompt_context,
        strategy=strategy_obj,
        strategy_selection=strategy_selection,
        event=event.describe(),
        relevant_components=list(planner_output.relevant_components),
        candidate_patches=candidate_patches,
        chosen_patches=chosen_patches,
        objective=objective,
        solution=solution,
        solve_meta=solve_meta,
        evaluation=build_evaluation_summary(objective=objective, solve_meta=solve_meta),
        change_report=build_change_report(
            chosen_patches,
            base_context.base_objective,
            objective,
            edit_summary=planner_output.edit_summary,
        ),
        planner_output=planner_output.to_dict(),
        model_summary=model_after.describe() if hasattr(model_after, "describe") else {},
        artifacts=artifacts,
    )
    step.evaluation = adapter.evaluate(spec, step)
    run_result = ProblemRunResult(
        problem=spec.metadata,
        mode="single",
        base_objective=base_context.base_objective,
        base_solution=base_context.base_solution,
        base_solve_meta=base_context.base_solve_meta,
        base_evaluation=base_context.base_evaluation,
        steps=[step],
    )

    ground_truth_payload = _ground_truth_payload(run, spec, run_result, trace)
    ground_truth = None
    if evaluate_ground_truth and spec.ground_truth is not None:
        ground_truth = evaluate_result_payload(
            spec,
            ground_truth_payload,
            explicit_case_id=run.case_id,
            reference_policy=reference_policy,
        ).to_dict()

    detail_payload = {
        "run_id": run.run_id,
        "problem": spec.metadata.problem_id,
        "problem_name": spec.metadata.name,
        **_result_detail_fields(run, spec, run_result),
        "input": _run_input_payload(run),
        "metadata": dict(run.metadata),
        "result": run_result.to_dict(),
        "llm_trace": [],
        "ground_truth": ground_truth,
    }
    _attach_replay_metadata(detail_payload, source_payload=source_payload, source_path=source_path)
    return detail_payload, ground_truth


def _load_replayed_plan(
    source_payload: dict[str, Any],
    *,
    source_path: Path,
) -> tuple[PlannerOutput, list[PlannedActionSet]]:
    trace_dir = _source_trace_dir(source_payload)
    normalized_path = trace_dir / "normalized_actions.json"
    planner_path = trace_dir / "planner_output.json"
    if not normalized_path.exists():
        raise FileNotFoundError(f"Missing source normalized actions: {normalized_path}")
    if not planner_path.exists():
        raise FileNotFoundError(f"Missing source planner output: {planner_path}")

    action_sets = _action_sets_from_payload(_load_json(normalized_path))
    if not action_sets:
        raise RuntimeError(f"Source plan has no executable patch action sets: {normalized_path}")
    planner_payload = _load_json(planner_path)
    planner_output = PlannerOutput(
        edit_summary=str(planner_payload.get("edit_summary") or ""),
        affected_sets=dict(planner_payload.get("affected_sets") or {}),
        relevant_components=[
            str(item) for item in list(planner_payload.get("relevant_components") or [])
        ],
        action_kind="patch",
        candidate_actions=[],
        candidate_action_sets=action_sets,
        planning_hints=dict(planner_payload.get("planning_hints") or {}),
        raw_response=str(planner_payload.get("raw_response") or ""),
        annotations=dict(planner_payload.get("annotations") or {}),
    )
    planner_output.planning_hints["replay_source_trace_dir"] = str(trace_dir)
    planner_output.planning_hints["replay_source_result_path"] = str(source_path)
    return planner_output, action_sets


def _action_sets_from_payload(payload: Any) -> list[PlannedActionSet]:
    if not isinstance(payload, list):
        raise ValueError("normalized_actions.json must contain a list")
    if not payload:
        return []

    action_sets: list[PlannedActionSet] = []
    flat_patches: list[Patch] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        if "actions" in item:
            action_kind = str(item.get("action_kind") or "patch").strip().lower() or "patch"
            if action_kind != "patch":
                continue
            actions = [_patch_from_mapping(action) for action in list(item.get("actions") or [])]
            if actions:
                action_sets.append(
                    PlannedActionSet(
                        action_kind="patch",
                        actions=actions,
                        label=str(item.get("label") or ""),
                        metadata=dict(item.get("metadata") or {}),
                    )
                )
            continue
        if "op" in item:
            flat_patches.append(_patch_from_mapping(item))

    if flat_patches:
        action_sets.append(
            PlannedActionSet(
                action_kind="patch",
                actions=flat_patches,
                label="replayed_flat_actions",
                metadata={"coerced_from": "normalized_actions"},
            )
        )
    return action_sets


def _patch_from_mapping(payload: Any) -> Patch:
    if isinstance(payload, Patch):
        return payload
    if not isinstance(payload, dict):
        raise ValueError(f"Expected patch mapping, got {type(payload).__name__}")
    op = PatchOp(str(payload["op"]))
    target = payload.get("target") if isinstance(payload.get("target"), dict) else {}
    scope = payload.get("scope") if isinstance(payload.get("scope"), dict) else {}
    update = payload.get("update") if isinstance(payload.get("update"), dict) else {}
    return Patch(
        op=op,
        target=_restore_json_keys(dict(target)),
        scope=_restore_json_keys(dict(scope)),
        update=_restore_json_keys(dict(update)),
        notes=str(payload.get("notes") or ""),
    )


def _restore_json_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            _restore_json_key(key): _restore_json_keys(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_restore_json_keys(item) for item in value]
    return value


def _restore_json_key(key: Any) -> Any:
    if not isinstance(key, str):
        return key
    stripped = key.strip()
    if stripped and stripped.lstrip("-").isdigit():
        try:
            return int(stripped)
        except ValueError:
            return key
    return key


def _replay_run_from_source(
    source_payload: dict[str, Any],
    *,
    source_path: Path,
    trace_root: str | Path,
    strategy: str,
    solver_time_limit: int | None,
) -> ExperimentRun:
    source_input = dict(source_payload.get("input") or {})
    config_mapping = dict(source_input.get("config") or {})
    if solver_time_limit is not None:
        config_mapping["time_limit"] = int(solver_time_limit)
    metadata = dict(source_payload.get("metadata") or {})
    metadata.update(
        {
            "replayed_from_run_id": source_payload.get("run_id"),
            "replayed_from_result_path": str(source_path),
            "replay_source_trace_dir": str(_source_trace_dir(source_payload)) if _has_source_trace_dir(source_payload) else "",
            "run_mode": "patchedit-scratch-replay",
        }
    )
    run_id = _default_replay_run_id(str(source_payload.get("run_id") or source_path.stem))
    return ExperimentRun(
        run_id=run_id,
        problem=str(source_input.get("problem") or source_payload.get("problem") or "exam_block_seq"),
        problem_root=source_input.get("problem_root"),
        problem_id=str(source_payload.get("problem") or "exam_block_seq"),
        config_path=source_input.get("config_path"),
        config_mapping=config_mapping,
        examples_path=source_input.get("examples_path"),
        delta_text=str(source_input.get("delta_text") or ""),
        prompt_id=str(source_payload.get("prompt_id") or source_input.get("prompt_id") or ""),
        case_id=str(source_input.get("case_id") or source_payload.get("prompt_id") or ""),
        planner_mode="patchedit",
        model_name=str(source_payload.get("model") or source_input.get("model") or ""),
        api_key=None,
        strategy_name=strategy,
        trace_root=str(Path(trace_root).expanduser().resolve()),
        strategy_policy_mode=DEFAULT_REPLAY_POLICY,
        strategy_selector_model=None,
        metadata=metadata,
        evaluate_ground_truth=False,
        reference_policy="if_available",
    )


def _base_context_from_source(source_payload: dict[str, Any], adapter, spec) -> BaseSolveCacheEntry:
    run_result = source_payload.get("result")
    if not isinstance(run_result, dict) or "base_objective" not in run_result:
        return _build_base_context(adapter, spec)
    try:
        objective = float(run_result["base_objective"])
    except (TypeError, ValueError):
        return _build_base_context(adapter, spec)
    solve_meta = dict(run_result.get("base_solve_meta") or {})
    return BaseSolveCacheEntry(
        base_model=adapter.build_structured_model(spec),
        base_objective=objective,
        base_solution=run_result.get("base_solution"),
        base_solve_meta=solve_meta,
        base_evaluation=build_evaluation_summary(
            objective=objective,
            solve_meta=solve_meta,
            details={"phase": "base", "source": "replay_source_result"},
        ),
    )


def _attach_replay_exception_context(
    exc: Exception,
    *,
    trace: dict[str, Any],
    artifacts: dict[str, Any],
    planner_output: PlannerOutput,
    strategy_selection,
) -> None:
    for key, value in {
        "reopt_trace": trace,
        "reopt_report_artifacts": artifacts,
        "reopt_planner_output": planner_output,
        "reopt_planner_mode": "patchedit",
        "reopt_strategy_selection": strategy_selection,
    }.items():
        try:
            setattr(exc, key, value)
        except Exception:
            continue


def _attach_replay_metadata(
    detail_payload: dict[str, Any],
    *,
    source_payload: dict[str, Any],
    source_path: Path,
) -> None:
    detail_payload["replay_source_run_id"] = source_payload.get("run_id")
    detail_payload["replay_source_result_path"] = str(source_path)
    detail_payload["replay_source_trace_dir"] = (
        str(_source_trace_dir(source_payload)) if _has_source_trace_dir(source_payload) else ""
    )
    metadata = dict(detail_payload.get("metadata") or {})
    metadata.update(
        {
            "replay_source_run_id": source_payload.get("run_id"),
            "replay_source_result_path": str(source_path),
            "replay_source_trace_dir": detail_payload["replay_source_trace_dir"],
        }
    )
    detail_payload["metadata"] = metadata


def _default_replay_run_id(source_run_id: str) -> str:
    replacements = [
        ("patchedit_auto", "patchedit_scratch"),
        ("patchedit-auto", "patchedit-scratch"),
        ("patcheditauto", "patcheditscratch"),
    ]
    for old, new in replacements:
        if old in source_run_id:
            return source_run_id.replace(old, new)
    return f"{source_run_id}_patchedit_scratch_replay"


def _has_source_trace_dir(source_payload: dict[str, Any]) -> bool:
    try:
        _source_trace_dir(source_payload)
    except Exception:
        return False
    return True


def _source_trace_dir(source_payload: dict[str, Any]) -> Path:
    trace_dir = source_payload.get("trace_dir")
    if trace_dir in {None, ""}:
        result = source_payload.get("result")
        if isinstance(result, dict):
            steps = result.get("steps")
            if isinstance(steps, list) and steps:
                artifacts = steps[-1].get("artifacts") if isinstance(steps[-1], dict) else {}
                if isinstance(artifacts, dict):
                    trace_dir = artifacts.get("trace_dir")
    if trace_dir in {None, ""}:
        raise ValueError("Source result does not record a trace_dir")
    return Path(str(trace_dir)).expanduser().resolve()


def _load_json(path: str | Path) -> Any:
    with Path(path).expanduser().resolve().open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _write_csv(rows: list[dict[str, Any]], path: Path, *, fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _collect_sources(args: argparse.Namespace) -> list[Path]:
    paths: list[Path] = []
    for raw in args.source_results or []:
        paths.append(Path(raw).expanduser().resolve())
    for pattern in args.source_results_glob or []:
        for match in sorted(glob.glob(pattern)):
            paths.append(Path(match).expanduser().resolve())
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    if not unique:
        raise ValueError("No source result paths were provided")
    return unique


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-results",
        nargs="*",
        default=[],
        help="Source patchedit-auto result JSON paths to replay.",
    )
    parser.add_argument(
        "--source-results-glob",
        nargs="*",
        default=[],
        help="Glob patterns for source patchedit-auto result JSON paths.",
    )
    parser.add_argument("--output-dir", required=True, help="Replay output directory.")
    parser.add_argument("--trace-root", default=None, help="Optional replay trace root.")
    parser.add_argument("--strategy", default=DEFAULT_REPLAY_STRATEGY)
    parser.add_argument(
        "--solver-time-limit",
        type=int,
        default=None,
        help="Optional replay solve time limit override; useful for smoke tests.",
    )
    parser.add_argument("--evaluate-ground-truth", action="store_true")
    parser.add_argument(
        "--reference-policy",
        choices=["off", "if_available", "require"],
        default="if_available",
    )
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sources = _collect_sources(args)
    summary = replay_many(
        sources,
        output_dir=args.output_dir,
        trace_root=args.trace_root,
        strategy=args.strategy,
        solver_time_limit=args.solver_time_limit,
        evaluate_ground_truth=args.evaluate_ground_truth,
        reference_policy=args.reference_policy,
        overwrite=args.overwrite,
    )
    print(f"\nOutput directory: {summary['output_dir']}")
    print(f"Total replayed runs: {summary['total_runs']}")
    print(f"OK: {summary['ok_runs']}")
    print(f"Errors: {summary['error_runs']}")
    print(f"Summary CSV: {summary['summary_csv']}")
    if summary["error_runs"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
