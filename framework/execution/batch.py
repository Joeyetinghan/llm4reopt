"""Manifest-driven batch experiment execution."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from framework.core import (
    DeltaRequest,
    ProblemAdapter,
    ProblemRunResult,
    ProblemSpec,
    ReoptResult,
)
from framework.core.failure_taxonomy import classify_visible_failure, success_failure_summary
from framework.core.planner_modes import normalize_planner_mode
from framework.core.solver_utils import solve_with_metadata
from framework.evaluation import (
    build_evaluation_summary,
    evaluate_result_payload,
)
from framework.llm import DEFAULT_LLM_MODEL
from framework.prompting import PromptCatalogUnavailable, resolve_problem_prompt, resolve_problem_prompt_params
from framework.registry import load_manifest, load_problem, resolve_problem_root

from .step_runner import (
    build_change_report,
    execute_delta_step,
)
from .strategy import (
    canonical_strategy_policy_mode,
    default_requested_strategy_for_policy,
    resolve_strategy_policy,
)


PATHLIKE_CONFIG_KEYS = {"instances_dir"}
EXCLUDED_BASE_KEY_FIELDS = {"delta_text", "prompt_params"}


@dataclass(frozen=True)
class ExperimentRun:
    run_id: str
    problem: str | None
    problem_root: str | None
    problem_id: str
    config_path: str | None
    config_mapping: dict[str, Any]
    examples_path: str | None
    delta_text: str
    prompt_id: str | None
    case_id: str | None
    planner_mode: str | None
    model_name: str
    api_key: str | None
    strategy_name: str
    trace_root: str | None = None
    strategy_policy_mode: str | None = None
    strategy_selector_model: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    evaluate_ground_truth: bool = False
    reference_policy: str = "if_available"


@dataclass
class BaseSolveCacheEntry:
    base_model: Any
    base_objective: float
    base_solution: Any
    base_solve_meta: dict[str, Any]
    base_evaluation: Any


def run_experiment_manifest(
    manifest_path: str | Path,
    *,
    output_dir: str | Path | None = None,
    model_name: str | None = None,
    api_key: str | None = None,
    evaluate_ground_truth: bool | None = None,
    reference_policy: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    manifest_file = Path(manifest_path).expanduser().resolve()
    payload = _load_mapping_file(manifest_file)
    defaults = dict(payload.get("defaults", {}))
    runs = _normalize_runs(
        manifest_file,
        payload,
        model_name_override=model_name,
        api_key_override=api_key,
        evaluate_ground_truth_override=evaluate_ground_truth,
        reference_policy_override=reference_policy,
    )

    resolved_output_dir = _resolve_output_dir(
        manifest_file,
        output_dir=output_dir,
        manifest_defaults=defaults,
    )

    if dry_run:
        return {
            "manifest_path": str(manifest_file),
            "output_dir": str(resolved_output_dir),
            "total_runs": len(runs),
            "runs": [_describe_dry_run(run) for run in runs],
        }

    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    runs_dir = resolved_output_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    base_cache: dict[str, BaseSolveCacheEntry] = {}
    summary_rows: list[dict[str, Any]] = []
    ground_truth_rows: list[dict[str, Any]] = []

    for index, run in enumerate(runs, start=1):
        detail_path = runs_dir / f"{run.run_id}.json"
        spec: ProblemSpec | None = None
        try:
            spec, adapter = load_problem(
                problem=run.problem if run.problem_root is None else None,
                problem_root=run.problem_root,
                config_path=run.config_path,
                config_mapping=run.config_mapping,
                examples_path=run.examples_path,
            )
            base_key = _base_cache_key(spec)
            base_context = base_cache.get(base_key)
            if base_context is None:
                base_context = _build_base_context(adapter, spec)
                base_cache[base_key] = base_context

            detail_payload, summary_row, ground_truth_row = _run_single_experiment(
                run,
                spec,
                adapter,
                base_context,
            )
        except Exception as exc:
            detail_payload = _error_detail_payload(run, spec, exc)
            summary_row = _summary_row_from_detail(detail_payload, ground_truth=None)
            ground_truth_row = None

        detail_path.write_text(
            json.dumps(detail_payload, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
        summary_row["result_path"] = str(detail_path)
        summary_rows.append(summary_row)
        if ground_truth_row is not None:
            ground_truth_rows.append(ground_truth_row)

        print(
            f"[{index}/{len(runs)}] {run.run_id}: "
            f"{summary_row['status']} "
            f"problem={summary_row['problem']} "
            f"instance={summary_row['instance_id'] or '?'} "
            f"prompt={summary_row['prompt_id'] or '?'} "
            f"model={summary_row['model']}"
        )

    summary_csv_path = resolved_output_dir / "summary.csv"
    _write_csv(summary_rows, summary_csv_path)

    evaluation_csv_path: str | None = None
    if ground_truth_rows:
        evaluation_csv_path = str(
            write_ground_truth_csv_payloads(
                ground_truth_rows,
                resolved_output_dir / "evaluation.csv",
            )
        )

    summary_payload = {
        "manifest_path": str(manifest_file),
        "output_dir": str(resolved_output_dir),
        "total_runs": len(runs),
        "ok_runs": sum(1 for row in summary_rows if row["status"] == "ok"),
        "error_runs": sum(1 for row in summary_rows if row["status"] == "error"),
        "summary_csv": str(summary_csv_path),
        "evaluation_csv": evaluation_csv_path,
        "rows": summary_rows,
    }
    (resolved_output_dir / "summary.json").write_text(
        json.dumps(summary_payload, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    return summary_payload


def write_ground_truth_csv_payloads(rows: list[dict[str, Any]], path: str | Path) -> Path:
    output_path = Path(path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return output_path


def _normalize_runs(
    manifest_file: Path,
    payload: dict[str, Any],
    *,
    model_name_override: str | None,
    api_key_override: str | None,
    evaluate_ground_truth_override: bool | None,
    reference_policy_override: str | None,
) -> list[ExperimentRun]:
    raw_runs = payload.get("runs")
    if not isinstance(raw_runs, list) or not raw_runs:
        raise ValueError("Manifest must declare a non-empty 'runs' list")

    defaults = dict(payload.get("defaults", {}))
    manifest_dir = manifest_file.parent
    normalized: list[ExperimentRun] = []
    for index, item in enumerate(raw_runs, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Each manifest run must be a mapping: {item!r}")

        merged = _merge_defaults(defaults, item)
        raw_problem_root = merged.get("problem_root")
        resolved_problem_root = (
            str(_resolve_path(manifest_dir, raw_problem_root))
            if raw_problem_root not in {None, ""}
            else None
        )
        problem = str(merged["problem"]) if merged.get("problem") else None
        package_root = resolve_problem_root(problem=problem if resolved_problem_root is None else None, problem_root=resolved_problem_root)
        problem_manifest = load_manifest(package_root)
        problem_id = str(problem_manifest["id"])

        base_config_path = _select_base_config_path(
            package_root,
            problem_manifest,
            merged.get("config_path"),
            manifest_dir,
        )
        base_config = _load_mapping_file(base_config_path) if base_config_path is not None else {}
        config_overrides = _resolve_config_paths(
            dict(merged.get("config", {})),
            manifest_dir,
        )
        config_mapping = dict(base_config)
        config_mapping.update(config_overrides)

        prompt_id = str(merged["prompt_id"]) if merged.get("prompt_id") else None
        delta_text, prompt_metadata = _resolve_delta_text(
            problem_id,
            config_mapping,
            prompt_id=prompt_id,
            explicit_delta_text=merged.get("delta_text"),
        )

        run_id = str(merged.get("run_id") or _default_run_id(problem_id, prompt_id, index))
        examples_path = (
            str(_resolve_path(manifest_dir, merged["examples_path"]))
            if merged.get("examples_path") not in {None, ""}
            else None
        )
        if base_config_path is not None:
            config_path_arg = str(base_config_path)
        elif config_mapping:
            config_path_arg = str(manifest_file)
        else:
            config_path_arg = None
        trace_root = (
            str(_resolve_path(manifest_dir, merged["trace_root"]))
            if merged.get("trace_root") not in {None, ""}
            else None
        )
        strategy_policy_mode = (
            str(merged["strategy_policy"]) if merged.get("strategy_policy") else "llm"
        )
        strategy_name = str(
            merged.get(
                "strategy",
                default_requested_strategy_for_policy(
                    strategy_policy_mode,
                    default_policy="llm",
                ),
            )
        )
        metadata = dict(merged.get("metadata", {}))
        metadata.update(prompt_metadata)
        normalized.append(
            ExperimentRun(
                run_id=run_id,
                problem=problem,
                problem_root=resolved_problem_root,
                problem_id=problem_id,
                config_path=config_path_arg,
                config_mapping=config_mapping,
                examples_path=examples_path,
                delta_text=delta_text,
                prompt_id=prompt_id,
                case_id=str(merged["case_id"]) if merged.get("case_id") else prompt_id,
                planner_mode=(
                    normalize_planner_mode(str(merged["planner_mode"]))
                    if merged.get("planner_mode")
                    else None
                ),
                model_name=str(model_name_override or merged.get("model") or DEFAULT_LLM_MODEL),
                api_key=api_key_override or merged.get("api_key"),
                strategy_name=strategy_name,
                trace_root=trace_root,
                strategy_policy_mode=canonical_strategy_policy_mode(
                    strategy_policy_mode,
                    default="llm",
                ),
                strategy_selector_model=(
                    str(merged["strategy_selector_model"])
                    if merged.get("strategy_selector_model")
                    else None
                ),
                metadata=metadata,
                evaluate_ground_truth=bool(
                    evaluate_ground_truth_override
                    if evaluate_ground_truth_override is not None
                    else merged.get("evaluate_ground_truth", False)
                ),
                reference_policy=str(
                    reference_policy_override
                    or merged.get("reference_policy", "if_available")
                ),
            )
        )
    return normalized


def _merge_defaults(defaults: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    merged = dict(defaults)
    merged.update(item)
    merged["config"] = {
        **dict(defaults.get("config", {})),
        **dict(item.get("config", {})),
    }
    merged["metadata"] = {
        **dict(defaults.get("metadata", {})),
        **dict(item.get("metadata", {})),
    }
    return merged


def _select_base_config_path(
    package_root: Path,
    manifest: dict[str, Any],
    explicit_config_path: Any,
    manifest_dir: Path,
) -> Path | None:
    if explicit_config_path not in {None, ""}:
        return _resolve_path(manifest_dir, explicit_config_path)
    default_config = manifest.get("default_config")
    if not default_config:
        return None
    return (package_root / str(default_config)).resolve()


def _run_single_experiment(
    run: ExperimentRun,
    spec: ProblemSpec,
    adapter: ProblemAdapter,
    base_context: BaseSolveCacheEntry,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    delta_request = DeltaRequest(text=run.delta_text, metadata=dict(run.metadata))
    base_model = base_context.base_model.copy()
    if run.planner_mode:
        spec.config_metadata["planner_mode"] = run.planner_mode
    if run.trace_root:
        spec.config_metadata["trace_root"] = run.trace_root
    if run.strategy_policy_mode:
        spec.config_metadata["strategy_policy"] = run.strategy_policy_mode
    if run.strategy_selector_model:
        spec.config_metadata["strategy_selector_model"] = run.strategy_selector_model
    step_result = execute_delta_step(
        adapter=adapter,
        spec=spec,
        model=base_model,
        delta_request=delta_request,
        model_name=run.model_name,
        api_key=run.api_key,
        objective_before=base_context.base_objective,
        prior_result=None,
        warm_start=base_context.base_solution,
        warm_start_meta=base_context.base_solve_meta,
        requested_strategy=run.strategy_name,
        strategy_policy=resolve_strategy_policy(
            spec,
            model_name=run.model_name,
            api_key=run.api_key,
        ),
        trace_metadata={"run_id": run.run_id},
    )

    step = ReoptResult(
        delta_request=delta_request,
        prompt_context=step_result.prompt_context,
        strategy=step_result.strategy,
        strategy_selection=step_result.strategy_selection,
        event=step_result.event.describe(),
        relevant_components=list(step_result.relevant_components),
        candidate_patches=step_result.candidate_patches,
        chosen_patches=step_result.chosen_patches,
        objective=step_result.objective,
        solution=step_result.solution,
        solve_meta=step_result.solve_meta,
        evaluation=build_evaluation_summary(objective=step_result.objective, solve_meta=step_result.solve_meta),
        change_report=build_change_report(
            step_result.chosen_patches,
            base_context.base_objective,
            step_result.objective,
            edit_summary=step_result.planner_output.edit_summary,
        ),
        planner_output=step_result.planner_output.to_dict(),
        model_summary=step_result.model_after.describe() if hasattr(step_result.model_after, "describe") else {},
        artifacts=step_result.report_artifacts,
    )
    step.evaluation = step_result.adapter_after.evaluate(step_result.spec_after, step)

    run_result = ProblemRunResult(
        problem=spec.metadata,
        mode="single",
        base_objective=base_context.base_objective,
        base_solution=base_context.base_solution,
        base_solve_meta=base_context.base_solve_meta,
        base_evaluation=base_context.base_evaluation,
        steps=[step],
    )

    ground_truth_payload = _ground_truth_payload(run, spec, run_result, step_result.trace)
    ground_truth = None
    if run.evaluate_ground_truth and spec.ground_truth is not None:
        ground_truth = evaluate_result_payload(
            spec,
            ground_truth_payload,
            explicit_case_id=run.case_id,
            reference_policy=run.reference_policy,
        ).to_dict()

    detail_payload = {
        "run_id": run.run_id,
        "problem": spec.metadata.problem_id,
        "problem_name": spec.metadata.name,
        **_result_detail_fields(run, spec, run_result),
        "input": _run_input_payload(run),
        "metadata": dict(run.metadata),
        "result": run_result.to_dict(),
        "llm_trace": list(step_result.trace.get("steps", [])),
        "ground_truth": ground_truth,
    }
    summary_row = _summary_row_from_detail(detail_payload, ground_truth=ground_truth)
    return detail_payload, summary_row, ground_truth


def _build_base_context(adapter: ProblemAdapter, spec: ProblemSpec) -> BaseSolveCacheEntry:
    base_model = adapter.build_structured_model(spec)
    cached_base = adapter.load_base_context(spec, base_model)
    if cached_base is not None:
        objective, solution, solve_meta = cached_base
    else:
        env = adapter.build_env(spec)
        objective, solution, solve_meta = solve_with_metadata(env, base_model.copy())
    return BaseSolveCacheEntry(
        base_model=base_model,
        base_objective=objective,
        base_solution=solution,
        base_solve_meta=solve_meta,
        base_evaluation=build_evaluation_summary(
            objective=objective,
            solve_meta=solve_meta,
            details={"phase": "base"},
        ),
    )


def _resolve_output_dir(
    manifest_file: Path,
    *,
    output_dir: str | Path | None,
    manifest_defaults: dict[str, Any],
) -> Path:
    selected = output_dir if output_dir is not None else manifest_defaults.get("output_dir", "outputs/batch")
    return _resolve_path(manifest_file.parent, selected)


def _resolve_delta_text(
    problem_id: str,
    config_mapping: dict[str, Any],
    *,
    prompt_id: str | None,
    explicit_delta_text: Any,
) -> tuple[str, dict[str, Any]]:
    metadata: dict[str, Any] = {}
    if prompt_id:
        metadata["prompt_id"] = str(prompt_id)
    prompts = config_mapping.get("prompts")
    prompt_entry = prompts.get(prompt_id) if isinstance(prompts, dict) and prompt_id else None

    if prompt_entry is not None and isinstance(prompt_entry, dict):
        prompt_metadata = prompt_entry.get("metadata")
        if isinstance(prompt_metadata, dict):
            metadata.update(prompt_metadata)
        params = prompt_entry.get("params")
        if isinstance(params, dict) and params:
            metadata["prompt_params"] = dict(params)

    if prompt_id and "prompt_params" not in metadata:
        derived_params = resolve_problem_prompt_params(problem_id, prompt_id, context=config_mapping)
        if derived_params:
            metadata["prompt_params"] = dict(derived_params)

    if explicit_delta_text not in {None, ""}:
        return str(explicit_delta_text), metadata

    if prompt_entry is not None and isinstance(prompt_entry, dict):
        delta_text = prompt_entry.get("delta_text")
        if delta_text not in {None, ""}:
            metadata["prompt_source"] = "config"
            return str(delta_text).strip(), metadata

    if prompt_id:
        try:
            metadata["prompt_source"] = "catalog"
            prompt = resolve_problem_prompt(
                problem_id,
                prompt_id,
                params=metadata.get("prompt_params"),
            )
            resolved_metadata = dict(prompt.metadata)
            resolved_metadata.update(metadata)
            return prompt.text, resolved_metadata
        except PromptCatalogUnavailable:
            pass

    if config_mapping.get("delta_text") not in {None, ""}:
        return str(config_mapping["delta_text"]).strip(), metadata

    raise ValueError(
        f"Could not resolve delta text for problem '{problem_id}'"
        + (f" and prompt '{prompt_id}'" if prompt_id else "")
    )


def _resolve_config_paths(config: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for key, value in config.items():
        if isinstance(value, dict):
            resolved[key] = _resolve_config_paths(value, base_dir)
            continue
        if _is_pathlike_key(key) and isinstance(value, str) and value:
            resolved[key] = str(_resolve_path(base_dir, value))
            continue
        resolved[key] = value
    return resolved


def _is_pathlike_key(key: str) -> bool:
    return (
        key.endswith("_path")
        or key.endswith("_dir")
        or key.endswith("_root")
        or key in PATHLIKE_CONFIG_KEYS
    )


def _resolve_path(base_dir: Path, value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (base_dir / path).resolve()


def _load_mapping_file(path: str | Path) -> dict[str, Any]:
    resolved = Path(path).expanduser().resolve()
    with resolved.open("r", encoding="utf-8") as fh:
        if resolved.suffix.lower() in {".yaml", ".yml"}:
            payload = yaml.safe_load(fh) or {}
        else:
            payload = json.load(fh) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Expected mapping file: {resolved}")
    return dict(payload)


def _ground_truth_payload(
    run: ExperimentRun,
    spec: ProblemSpec,
    run_result: ProblemRunResult,
    log_collector: dict[str, Any],
) -> dict[str, Any]:
    final_result = run_result.final_result
    step = final_result.to_dict() if final_result is not None else {}
    solution = final_result.solution if final_result is not None else {}
    solve_meta = final_result.solve_meta if final_result is not None else {}
    chosen_patch = final_result.chosen_patches[0].describe() if final_result and final_result.chosen_patches else {}
    instance_id = _instance_id(spec)

    payload = {
        "problem_id": spec.metadata.problem_id,
        "instance": instance_id,
        "instance_id": instance_id,
        "prompt": run.prompt_id,
        "prompt_id": run.prompt_id,
        "case_id": run.case_id,
        "delta_text": run.delta_text,
        "delta_request": run.delta_text,
        "model": run.model_name,
        "llm_trace": list(log_collector.get("steps", [])),
        "base_obj": run_result.base_objective,
        "base_cost": run_result.base_objective,
        "new_obj": final_result.objective if final_result is not None else None,
        "new_cost": final_result.objective if final_result is not None else None,
        "objective": final_result.objective if final_result is not None else None,
        "feasible": final_result.evaluation.feasible if final_result is not None else None,
        "status": "ok" if final_result is not None else "error",
        "chosen_patch": chosen_patch,
        "chosen_patch_op": chosen_patch.get("op", ""),
        "chosen_patches": step.get("chosen_patches", []),
        "candidate_patches": step.get("candidate_patches", []),
        "planner_output": step.get("planner_output", {}),
        "event": step.get("event", {}),
        "new_solution": solution,
        "solution": solution,
        "new_solve_meta": solve_meta,
        "solve_meta": solve_meta,
        "planner_mode": _planner_mode_value(run, spec),
        "execution_label": _execution_label(final_result, fallback=run.strategy_name),
        "trace_dir": _trace_dir_from_artifacts(final_result.artifacts if final_result is not None else {}),
    }
    if isinstance(solution, dict) and "patch_logs" in solution:
        payload["patch_logs"] = solution.get("patch_logs", [])
    return payload


def _result_detail_fields(
    run: ExperimentRun,
    spec: ProblemSpec,
    run_result: ProblemRunResult,
) -> dict[str, Any]:
    final_result = run_result.final_result
    selection = final_result.strategy_selection if final_result is not None else None
    solve_meta = final_result.solve_meta if final_result is not None else {}
    chosen_patch_op = _chosen_patch_op(final_result)
    success_summary = success_failure_summary(strategy_selection=selection)
    return {
        "instance_id": _instance_id(spec),
        "prompt_id": run.prompt_id or "",
        "model": run.model_name,
        "planner_mode": _planner_mode_value(run, spec),
        "strategy": final_result.strategy.value if final_result is not None else run.strategy_name,
        "strategy_policy": (
            selection.policy_name
            if selection is not None
            else (run.strategy_policy_mode or "llm")
        ),
        "execution_label": _execution_label(final_result, fallback=run.strategy_name),
        "status": "ok",
        "base_objective": run_result.base_objective,
        "objective": final_result.objective if final_result is not None else None,
        "cost_delta": (
            final_result.objective - run_result.base_objective
            if final_result is not None
            else None
        ),
        "feasible": final_result.evaluation.feasible if final_result is not None else None,
        "runtime": final_result.evaluation.runtime if final_result is not None else None,
        "obj_bound": solve_meta.get("obj_bound"),
        "mip_gap": solve_meta.get("mip_gap", solve_meta.get("gap")),
        "chosen_patch_op": chosen_patch_op,
        "trace_dir": _trace_dir_from_artifacts(final_result.artifacts if final_result is not None else {}),
        "failure_stage": success_summary["failure_stage"],
        "failure_label": success_summary["failure_label"],
        "failure_detail": success_summary["failure_detail"],
        "selection_fallback_used": success_summary["selection_fallback_used"],
        "error": "",
    }


def _error_detail_payload(
    run: ExperimentRun,
    spec: ProblemSpec | None,
    exc: Exception,
) -> dict[str, Any]:
    selection = getattr(exc, "reopt_strategy_selection", None)
    planner_mode = _planner_mode_value(
        run,
        spec,
        fallback=getattr(exc, "reopt_planner_mode", None),
    )
    trace = getattr(exc, "reopt_trace", None)
    llm_trace = list(trace.get("steps", [])) if isinstance(trace, dict) else []
    report_artifacts = getattr(exc, "reopt_report_artifacts", None)
    trace_dir = _trace_dir_from_artifacts(report_artifacts if isinstance(report_artifacts, dict) else {})
    failure = classify_visible_failure(
        exc,
        strategy_selection=selection,
        planner_mode=planner_mode,
    )
    planner_output = getattr(exc, "reopt_planner_output", None)
    planning_hints = dict(getattr(planner_output, "planning_hints", {}) or {})

    return {
        "run_id": run.run_id,
        "problem": spec.metadata.problem_id if spec is not None else run.problem_id,
        "problem_name": spec.metadata.name if spec is not None else run.problem_id,
        "instance_id": _instance_id(spec) if spec is not None else "",
        "prompt_id": run.prompt_id or "",
        "model": run.model_name,
        "planner_mode": planner_mode,
        "strategy": run.strategy_name,
        "strategy_policy": run.strategy_policy_mode or "llm",
        "execution_label": _strategy_execution_label(selection, fallback=run.strategy_name),
        "status": "error",
        "base_objective": "",
        "objective": "",
        "cost_delta": "",
        "feasible": "",
        "runtime": "",
        "obj_bound": "",
        "mip_gap": "",
        "chosen_patch_op": "",
        "trace_dir": trace_dir,
        "failure_stage": failure["failure_stage"],
        "failure_label": failure["failure_label"],
        "failure_detail": failure["failure_detail"],
        "selection_fallback_used": failure["selection_fallback_used"],
        "error": str(exc),
        "input": _run_input_payload(run),
        "metadata": dict(run.metadata),
        "result": None,
        "llm_trace": llm_trace,
        "ground_truth": None,
        "codeedit_failure_kind": getattr(exc, "codeedit_failure_kind", None),
        "codeedit_failure_retryable": getattr(exc, "codeedit_failure_retryable", None),
        "codeedit_attempt_count": (
            getattr(exc, "codeedit_attempt_count", None)
            or planning_hints.get("codeedit_attempt_count")
        ),
        "codeedit_repair_count": (
            getattr(exc, "codeedit_repair_count", None)
            or planning_hints.get("codeedit_repair_count")
        ),
    }


def _summary_row_from_detail(
    detail_payload: dict[str, Any],
    *,
    ground_truth: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "run_id": detail_payload.get("run_id", ""),
        "problem": detail_payload.get("problem", ""),
        "instance_id": detail_payload.get("instance_id", ""),
        "prompt_id": detail_payload.get("prompt_id", ""),
        "model": detail_payload.get("model", ""),
        "planner_mode": detail_payload.get("planner_mode", ""),
        "strategy": detail_payload.get("strategy", ""),
        "strategy_policy": detail_payload.get("strategy_policy", ""),
        "execution_label": detail_payload.get("execution_label", ""),
        "status": detail_payload.get("status", ""),
        "base_objective": detail_payload.get("base_objective", ""),
        "objective": detail_payload.get("objective", ""),
        "cost_delta": detail_payload.get("cost_delta", ""),
        "feasible": detail_payload.get("feasible", ""),
        "runtime": detail_payload.get("runtime", ""),
        "obj_bound": detail_payload.get("obj_bound", ""),
        "mip_gap": detail_payload.get("mip_gap", ""),
        "chosen_patch_op": detail_payload.get("chosen_patch_op", ""),
        "failure_stage": detail_payload.get("failure_stage", ""),
        "failure_label": detail_payload.get("failure_label", ""),
        "failure_detail": detail_payload.get("failure_detail", ""),
        "selection_fallback_used": detail_payload.get("selection_fallback_used", ""),
        "trace_dir": detail_payload.get("trace_dir", ""),
        "ground_truth_status": ground_truth.get("status", "") if ground_truth else "",
        "matches_ground_truth": ground_truth.get("matches_ground_truth", "") if ground_truth else "",
        "semantic_verdict": ground_truth.get("semantic_verdict", "") if ground_truth else "",
        "reference_verdict": ground_truth.get("reference_verdict", "") if ground_truth else "",
        "overall_verdict": ground_truth.get("overall_verdict", "") if ground_truth else "",
        "schedule_match": ground_truth.get("schedule_match", "") if ground_truth else "",
        "candidate_obj_lb": ground_truth.get("candidate_obj_lb", "") if ground_truth else "",
        "candidate_obj_ub": ground_truth.get("candidate_obj_ub", "") if ground_truth else "",
        "reference_obj_lb": ground_truth.get("reference_obj_lb", "") if ground_truth else "",
        "reference_obj_ub": ground_truth.get("reference_obj_ub", "") if ground_truth else "",
        "result_path": "",
        "error": detail_payload.get("error", ""),
    }


def _planner_mode_value(
    run: ExperimentRun,
    spec: ProblemSpec | None,
    *,
    fallback: str | None = None,
) -> str:
    raw = (
        fallback
        or (spec.config_metadata.get("planner_mode") if spec is not None else None)
        or ((spec.config_metadata.get("config") or {}).get("planner_mode") if spec is not None else None)
        or run.planner_mode
    )
    if raw in {None, ""}:
        return ""
    try:
        return normalize_planner_mode(str(raw))
    except ValueError:
        return str(raw)


def _execution_label(final_result: ReoptResult | None, *, fallback: str) -> str:
    selection = final_result.strategy_selection if final_result is not None else None
    return _strategy_execution_label(selection, fallback=fallback)


def _strategy_execution_label(
    selection,
    *,
    fallback: str,
) -> str:
    if selection is None:
        return fallback
    label = str(getattr(selection, "execution_label", "") or "").strip()
    return label or fallback


def _chosen_patch_op(final_result: ReoptResult | None) -> str:
    if final_result is None or not final_result.chosen_patches:
        return ""
    return final_result.chosen_patches[0].op.value


def _trace_dir_from_artifacts(artifacts: dict[str, Any]) -> str:
    if not isinstance(artifacts, dict):
        return ""
    value = artifacts.get("trace_dir")
    return str(value) if value not in {None, ""} else ""


def _instance_id(spec: ProblemSpec) -> str:
    loaded = spec.config_metadata.get("loaded_data") or {}
    instance_id = loaded.get("instance_id")
    if instance_id not in {None, ""}:
        return str(instance_id)
    lp_path = loaded.get("lp_path")
    if lp_path not in {None, ""}:
        return Path(str(lp_path)).stem
    return ""


def _base_cache_key(spec: ProblemSpec) -> str:
    loaded = dict(spec.config_metadata.get("loaded_data") or {})
    for field in EXCLUDED_BASE_KEY_FIELDS:
        loaded.pop(field, None)
    return json.dumps(
        {
            "problem_id": spec.metadata.problem_id,
            "loaded_data": _json_safe(loaded),
        },
        sort_keys=True,
        default=str,
    )


def _run_input_payload(run: ExperimentRun) -> dict[str, Any]:
    return {
        "problem": run.problem,
        "problem_root": run.problem_root,
        "config_path": run.config_path,
        "config": dict(run.config_mapping),
        "examples_path": run.examples_path,
        "delta_text": run.delta_text,
        "prompt_id": run.prompt_id,
        "case_id": run.case_id,
        "planner_mode": run.planner_mode,
        "trace_root": run.trace_root,
        "model": run.model_name,
        "strategy": run.strategy_name,
        "strategy_policy": run.strategy_policy_mode,
        "strategy_selector_model": run.strategy_selector_model,
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, set):
        return sorted((_json_safe(item) for item in value), key=lambda item: str(item))
    return value


def _default_run_id(problem_id: str, prompt_id: str | None, index: int) -> str:
    suffix = prompt_id or f"{index:03d}"
    return f"{problem_id}_{suffix}"


def _describe_dry_run(run: ExperimentRun) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "problem": run.problem_id,
        "prompt_id": run.prompt_id,
        "case_id": run.case_id,
        "delta_text": run.delta_text,
        "planner_mode": run.planner_mode,
        "trace_root": run.trace_root,
        "model": run.model_name,
        "strategy": run.strategy_name,
        "strategy_policy": run.strategy_policy_mode,
        "evaluate_ground_truth": run.evaluate_ground_truth,
    }
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
