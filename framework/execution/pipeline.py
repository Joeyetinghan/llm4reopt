"""Generic execution pipeline built on problem adapters."""

from __future__ import annotations

from typing import Any, Sequence

from framework.core import (
    CodeEditState,
    DeltaRequest,
    ProblemAdapter,
    ProblemRunResult,
    ProblemSpec,
    ReoptResult,
    SolveStrategy,
)
from framework.core.planner_modes import PATCHEDIT_MODE, normalize_planner_mode
from framework.core.solver_utils import solve_with_metadata
from framework.evaluation import build_evaluation_summary
from framework.prompting import append_episode

from .step_runner import build_change_report, execute_delta_step
from .strategy import resolve_strategy_policy


def _base_warm_start_available(solution: Any, solve_meta: dict[str, Any]) -> bool:
    if solution is not None:
        return True
    return solve_meta.get("solution_path") not in {None, ""}


def run_problem(
    adapter: ProblemAdapter,
    spec: ProblemSpec,
    delta_requests: Sequence[DeltaRequest],
    *,
    model_name: str,
    api_key: str | None,
    strategy_policy=None,
    requested_strategy: str = "auto",
) -> ProblemRunResult:
    if not delta_requests:
        raise ValueError("At least one delta request is required")

    strategy_policy = strategy_policy or resolve_strategy_policy(
        spec,
        model_name=model_name,
        api_key=api_key,
    )

    adapter_curr = adapter
    spec_curr = spec
    planner_mode = _resolve_planner_mode(spec_curr)

    if planner_mode == "codeedit":
        model_curr = adapter_curr.build_codeedit_state(spec_curr)
        base_context = adapter_curr.load_base_context(spec_curr, model_curr)
        base_result = None
        if base_context is None:
            base_result = adapter_curr.solve_codeedit(
                spec_curr,
                model_curr,
                SolveStrategy.SCRATCH,
            )
            base_objective = base_result.objective
            base_solution = base_result.solution
            base_solve_meta = dict(base_result.solve_meta)
        else:
            base_objective, base_solution, base_solve_meta = base_context
            if isinstance(model_curr, CodeEditState):
                model_curr.last_solve_meta = dict(base_solve_meta)
        spec_curr.capabilities["has_base_warm_start"] = _base_warm_start_available(
            base_solution,
            base_solve_meta,
        )
        base_evaluation = (
            base_result.evaluation
            if base_result is not None
            else build_evaluation_summary(
                objective=base_objective,
                solve_meta=base_solve_meta,
                details={"phase": "base"},
            )
        )
    else:
        base_env = adapter_curr.build_env(spec_curr)
        model_curr = adapter_curr.build_structured_model(spec_curr)
        base_context = adapter_curr.load_base_context(spec_curr, model_curr)
        if base_context is None:
            base_objective, base_solution, base_solve_meta = solve_with_metadata(base_env, model_curr)
            spec_curr.capabilities["has_base_warm_start"] = False
        else:
            base_objective, base_solution, base_solve_meta = base_context
            spec_curr.capabilities["has_base_warm_start"] = _base_warm_start_available(
                base_solution,
                base_solve_meta,
            )
        base_evaluation = build_evaluation_summary(
            objective=base_objective,
            solve_meta=base_solve_meta,
            details={"phase": "base"},
        )

    steps: list[ReoptResult] = []
    prior_result: ReoptResult | None = None
    prior_solution: Any = base_solution
    prior_solve_meta = base_solve_meta
    prior_objective = base_objective

    for delta_request in delta_requests:
        step = execute_delta_step(
            adapter=adapter_curr,
            spec=spec_curr,
            model=model_curr,
            delta_request=delta_request,
            model_name=model_name,
            api_key=api_key,
            objective_before=prior_objective,
            prior_result=prior_result,
            warm_start=prior_solution,
            warm_start_meta=prior_solve_meta,
            requested_strategy=requested_strategy,
            strategy_policy=strategy_policy,
        )
        model_curr = step.model_after

        result = ReoptResult(
            delta_request=delta_request,
            prompt_context=step.prompt_context,
            strategy=step.strategy,
            strategy_selection=step.strategy_selection,
            event=step.event.describe(),
            relevant_components=list(step.relevant_components),
            candidate_patches=step.candidate_patches,
            chosen_patches=step.chosen_patches,
            objective=step.objective,
            solution=step.solution,
            solve_meta=step.solve_meta,
            evaluation=build_evaluation_summary(
                objective=step.objective,
                solve_meta=step.solve_meta,
            ),
            change_report=build_change_report(
                step.chosen_patches,
                prior_objective,
                step.objective,
                edit_summary=step.planner_output.edit_summary,
            ),
            planner_output=step.planner_output.to_dict(),
            model_summary=model_curr.describe() if hasattr(model_curr, "describe") else {},
            artifacts=step.report_artifacts,
        )
        result.evaluation = step.adapter_after.evaluate(step.spec_after, result)
        steps.append(result)

        append_episode(
            step.spec_after.context_bundle,
            {
                "problem_id": step.spec_after.metadata.problem_id,
                "env_name": step.spec_after.capabilities.get("legacy_env_name"),
                "delta_text": delta_request.text,
                "event": result.event,
                "relevant_components": list(result.relevant_components),
                "candidate_action_sets": [action_set.to_dict() for action_set in step.candidate_action_sets],
                "candidate_patches": [patch.describe() for patch in step.candidate_patches],
                "chosen_patch": (
                    step.chosen_patches[0].describe()
                    if step.chosen_patches
                    else None
                ),
                "chosen_patches": [patch.describe() for patch in step.chosen_patches],
                "base_cost": prior_objective,
                "base_solution": prior_solution,
                "new_cost": step.objective,
                "new_solution": step.solution,
                "base_solve_meta": steps[-2].solve_meta if len(steps) > 1 else base_solve_meta,
                "new_solve_meta": step.solve_meta,
            },
        )

        prior_result = result
        prior_solution = step.adapter_after.extract_warm_start(result)
        prior_solve_meta = step.solve_meta
        prior_objective = step.objective
        adapter_curr = step.adapter_after
        spec_curr = step.spec_after

    return ProblemRunResult(
        problem=spec.metadata,
        mode="sequence" if len(delta_requests) > 1 else "single",
        base_objective=base_objective,
        base_solution=base_solution,
        base_solve_meta=base_solve_meta,
        base_evaluation=base_evaluation,
        steps=steps,
    )


def _resolve_planner_mode(spec: ProblemSpec) -> str:
    config = spec.config_metadata.get("config") or {}
    return normalize_planner_mode(
        spec.config_metadata.get("planner_mode")
        or config.get("planner_mode")
        or spec.capabilities.get("planner_mode")
        or PATCHEDIT_MODE
    )
