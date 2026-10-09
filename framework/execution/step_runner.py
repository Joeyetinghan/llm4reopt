"""Shared single-delta execution helper for packaged runtimes."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

from framework.core import (
    CodeEditState,
    DeltaRequest,
    Patch,
    PlannedActionSet,
    ProblemAdapter,
    ProblemSpec,
    SolveStrategy,
    StrategySelectionDecision,
    StructuredEvent,
)
from framework.core.planner_modes import PATCHEDIT_MODE, normalize_planner_mode
from framework.core.action_sets import coerce_action_sets, flatten_patch_actions
from framework.core.failure_taxonomy import attach_patchedit_failure
from framework.core.patches import apply_patch_sequence
from framework.editing.aider import AiderInvocationError, CodeEditWorkspace, load_problem_from_edit_workspace

from .strategy import (
    build_manual_strategy_selection,
    resolve_strategy_policy,
)

_MAX_REPAIR_HISTORY = 3


@dataclass
class StepExecutionResult:
    prompt_context: Any
    planner_output: Any
    event: StructuredEvent
    relevant_components: list[str]
    candidate_action_sets: list[PlannedActionSet]
    candidate_patches: list[Patch]
    chosen_patch: Patch | None
    chosen_patches: list[Patch]
    strategy: SolveStrategy
    strategy_selection: StrategySelectionDecision
    objective: float
    solution: Any
    solve_meta: dict[str, Any]
    report_artifacts: dict[str, Any]
    model_after: Any
    adapter_after: ProblemAdapter
    spec_after: ProblemSpec
    trace: dict[str, Any]


@dataclass(frozen=True)
class ExecutionFailureInfo:
    stage: str
    kind: str
    retryable: bool
    message: str
    repair_instruction: str | None = None


def _attach_failure_context(
    exc: Exception,
    *,
    trace: dict[str, Any],
    report_artifacts: dict[str, Any],
    planner_output,
    planner_mode: str,
    strategy_selection: StrategySelectionDecision | None,
    event,
    relevant_components: list[str],
    candidate_action_sets: list[PlannedActionSet],
    candidate_patches: list[Patch],
) -> None:
    payload = {
        "reopt_trace": trace,
        "reopt_report_artifacts": report_artifacts,
        "reopt_planner_output": planner_output,
        "reopt_planner_mode": planner_mode,
        "reopt_strategy_selection": strategy_selection,
        "reopt_event": event,
        "reopt_relevant_components": list(relevant_components),
        "reopt_candidate_action_sets": list(candidate_action_sets),
        "reopt_candidate_patches": list(candidate_patches),
    }
    for key, value in payload.items():
        try:
            setattr(exc, key, value)
        except Exception:
            continue


def _planner_output_is_codeedit_noop(planner_output) -> bool:
    edited_files = planner_output.annotations.get("edited_files")
    if isinstance(edited_files, list):
        return len(edited_files) == 0
    return bool(planner_output.planning_hints.get("codeedit_noop"))


def _model_failure_retry_budget(spec: ProblemSpec) -> int:
    config = dict(spec.config_metadata.get("config") or {})
    raw_value = (
        config.get("model_failure_retry_budget")
        or spec.config_metadata.get("model_failure_retry_budget")
        or os.getenv("REOPT_MODEL_FAILURE_RETRY_BUDGET")
        or "3"
    )
    try:
        budget = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Invalid model failure retry budget: {raw_value!r}") from exc
    return max(1, budget)


def _codeedit_failure(
    kind: str,
    message: str,
    *,
    retryable: bool,
) -> ExecutionFailureInfo:
    return ExecutionFailureInfo(
        stage=_codeedit_failure_stage(kind),
        kind=kind,
        retryable=retryable,
        message=message,
        repair_instruction=_codeedit_repair_instruction(kind),
    )


def _codeedit_failure_stage(kind: str) -> str:
    if kind in {"planner_backend_failed", "edit_format_degraded", "no_edit", "no_model_effective_edit", "invalid_workspace_payload"}:
        return "Edit Generation"
    if kind in {"compile_failed", "reload_failed", "build_failed"}:
        return "Reload / Build"
    return "Edited Solve"


def _patch_failure(
    kind: str,
    message: str,
    *,
    retryable: bool,
) -> ExecutionFailureInfo:
    return ExecutionFailureInfo(
        stage=_patch_failure_stage(kind),
        kind=kind,
        retryable=retryable,
        message=message,
        repair_instruction=_patch_repair_instruction(kind),
    )


def _patch_failure_stage(kind: str) -> str:
    if kind in {"planner_backend_failed", "unparseable_patch_plan", "no_executable_patch"}:
        return "Interpretation"
    if kind == "patch_application_failed":
        return "Patch Validation"
    return "Patched Solve"


def _attach_codeedit_error(
    exc: Exception,
    *,
    failure: ExecutionFailureInfo,
    attempt_count: int,
    repair_count: int,
    prior_failures: list[dict[str, Any]],
) -> None:
    _attach_retry_error(
        exc,
        failure=failure,
        attempt_count=attempt_count,
        repair_count=repair_count,
        prior_failures=prior_failures,
        action_kind="codeedit",
    )


def _attach_retry_error(
    exc: Exception,
    *,
    failure: ExecutionFailureInfo,
    attempt_count: int,
    repair_count: int,
    prior_failures: list[dict[str, Any]],
    action_kind: str,
) -> None:
    payload = {
        "reopt_failure_stage": failure.stage,
        "reopt_failure_kind": failure.kind,
        "reopt_failure_retryable": failure.retryable,
        "reopt_failure_message": failure.message,
        "model_attempt_count": attempt_count,
        "model_retry_count": repair_count,
        "model_prior_failures": list(prior_failures),
    }
    if action_kind == "codeedit":
        payload.update(
            {
                "codeedit_failure_kind": failure.kind,
                "codeedit_failure_retryable": failure.retryable,
                "codeedit_failure_message": failure.message,
                "codeedit_attempt_count": attempt_count,
                "codeedit_repair_count": repair_count,
                "codeedit_prior_failures": list(prior_failures),
            }
        )
    elif action_kind == "patch" and failure.kind in {"patch_application_failed", "solve_failed", "no_incumbent"}:
        attach_patchedit_failure(exc, kind=failure.kind, detail=failure.message)
    for key, value in payload.items():
        try:
            setattr(exc, key, value)
        except Exception:
            continue


def _coerce_codeedit_failure(exc: Exception) -> ExecutionFailureInfo | None:
    existing_kind = getattr(exc, "codeedit_failure_kind", None)
    if existing_kind:
        return _codeedit_failure(
            str(existing_kind),
            str(getattr(exc, "codeedit_failure_message", str(exc))),
            retryable=bool(getattr(exc, "codeedit_failure_retryable", False)),
        )

    message = str(exc)
    if isinstance(exc, AiderInvocationError):
        lowered = message.lower()
        if (
            "could not produce a valid edit" in lowered
            or "did not conform to the edit format" in lowered
            or "searchreplacenoexactmatch" in lowered
        ):
            return _codeedit_failure("edit_format_degraded", message, retryable=True)
        return _codeedit_failure("planner_backend_failed", message, retryable=True)
    if message.startswith("No incumbent solution available"):
        return _codeedit_failure("no_incumbent", message, retryable=True)
    return _codeedit_failure("planner_backend_failed", message, retryable=True)


def _classify_terminal_codeedit_solve_failure(exc: Exception) -> ExecutionFailureInfo:
    existing_kind = getattr(exc, "codeedit_failure_kind", None)
    if existing_kind:
        return _codeedit_failure(
            str(existing_kind),
            str(getattr(exc, "codeedit_failure_message", str(exc))),
            retryable=bool(getattr(exc, "codeedit_failure_retryable", False)),
        )
    if isinstance(exc, SyntaxError):
        return _codeedit_failure("compile_failed", str(exc), retryable=True)
    message = str(exc)
    if message.startswith("No incumbent solution available"):
        return _codeedit_failure("no_incumbent", message, retryable=True)
    return _codeedit_failure("solve_failed", message, retryable=True)


def _coerce_patchedit_failure(
    exc: Exception,
    *,
    planner_output,
) -> ExecutionFailureInfo | None:
    message = str(exc)
    if planner_output is None:
        return _patch_failure("planner_backend_failed", message, retryable=True)

    hints = dict(getattr(planner_output, "planning_hints", {}) or {})
    if bool(hints.get("planner_parse_fallback")):
        return _patch_failure("unparseable_patch_plan", message, retryable=True)

    output_executable = hints.get("planner_output_executable")
    if output_executable is None:
        output_executable = bool(
            list(getattr(planner_output, "candidate_action_sets", []) or [])
            or list(getattr(planner_output, "candidate_actions", []) or [])
        )
    if not output_executable:
        return _patch_failure("no_executable_patch", message, retryable=True)

    kind = str(getattr(exc, "patchedit_failure_kind", "") or "").strip()
    if kind:
        return _patch_failure(
            kind,
            str(getattr(exc, "patchedit_failure_detail", message)),
            retryable=True,
        )
    if message.startswith("No incumbent solution available"):
        return _patch_failure("no_incumbent", message, retryable=True)
    return _patch_failure("solve_failed", message, retryable=True)


def _coerce_execution_failure(
    exc: Exception,
    *,
    planner_output,
    planner_mode: str,
) -> ExecutionFailureInfo | None:
    action_kind = str(getattr(planner_output, "action_kind", "") or "").strip().lower()
    if action_kind == "codeedit" or planner_mode == "codeedit":
        return _coerce_codeedit_failure(exc)
    return _coerce_patchedit_failure(exc, planner_output=planner_output)


def _build_model_repair_context(
    failure: ExecutionFailureInfo,
    *,
    attempt_history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    # Retries only receive runtime execution feedback from the failed attempt.
    # Do not thread through evaluator outputs, ground-truth labels, or any
    # richer artifacts that would change the benchmark contract.
    repair_context = {
        "failure_stage": failure.stage,
        "failure_kind": failure.kind,
        "failure_message": failure.message,
        "repair_instruction": failure.repair_instruction,
    }
    if attempt_history:
        repair_context["attempt_history"] = [
            {
                "attempt": int(item["attempt"]),
                "failure_stage": str(item["failure_stage"]),
                "failure_kind": str(item["failure_kind"]),
                "failure_message": str(item["failure_message"]),
            }
            for item in attempt_history[-_MAX_REPAIR_HISTORY:]
            if isinstance(item, dict)
        ]
    return repair_context


def _codeedit_repair_instruction(kind: str) -> str:
    if kind == "no_edit":
        return "Make at least one concrete change in the editable files. A no-op is not acceptable."
    if kind == "no_model_effective_edit":
        return "Keep the requested change, but ensure the edit changes the actual optimization model rather than only dead runtime preparation code."
    if kind == "edit_format_degraded":
        return "Produce a direct, minimal edit that matches the current file contents exactly."
    if kind == "invalid_workspace_payload":
        return "Keep the edit within the surfaced editable files and preserve a valid codeedit workspace payload."
    if kind == "compile_failed":
        return "Keep the requested change, but ensure the edited Python files parse and compile cleanly."
    if kind == "reload_failed":
        return "Keep the requested change, but preserve imports, module loading, and expected entrypoints."
    if kind == "build_failed":
        return "Keep the requested change, but ensure build_codeedit_model(...) returns a valid Gurobi model without raising."
    return "Keep the requested change, but avoid the previous failure mode with the smallest viable edit."


def _patch_repair_instruction(kind: str) -> str:
    if kind == "planner_backend_failed":
        return "Produce a valid patch plan for the same request; the previous attempt failed before a usable plan was available."
    if kind == "unparseable_patch_plan":
        return "Return valid JSON with candidate_action_sets as a list of objects, each with actions=[...]."
    if kind == "no_executable_patch":
        return "Return at least one executable candidate_action_set object whose actions list contains concrete normalized patch objects."
    if kind == "patch_application_failed":
        return "Keep the requested change, but make the patch apply cleanly to the surfaced structured model."
    if kind == "no_incumbent":
        return "Keep the requested change, but choose a patch that preserves a feasible solve with an incumbent."
    if kind == "solve_failed":
        return "Keep the requested change, but avoid patches that make the candidate model unsolvable."
    return "Keep the requested change, but avoid the previous failure mode with the smallest viable patch."


def _annotate_model_attempts(
    planner_output,
    *,
    attempt_count: int,
    repair_count: int,
    prior_failures: list[dict[str, Any]],
) -> None:
    planner_output.planning_hints["model_attempt_count"] = attempt_count
    planner_output.planning_hints["model_retry_count"] = repair_count
    planner_output.annotations["model_attempt_count"] = attempt_count
    planner_output.annotations["model_retry_count"] = repair_count
    if prior_failures:
        planner_output.annotations["model_prior_failures"] = list(prior_failures)

    if str(getattr(planner_output, "action_kind", "") or "").strip().lower() != "codeedit":
        return
    planner_output.planning_hints["codeedit_attempt_count"] = attempt_count
    planner_output.planning_hints["codeedit_repair_count"] = repair_count
    planner_output.annotations["codeedit_attempt_count"] = attempt_count
    planner_output.annotations["codeedit_repair_count"] = repair_count
    if prior_failures:
        planner_output.annotations["codeedit_prior_failures"] = list(prior_failures)


def execute_delta_step(
    *,
    adapter: ProblemAdapter,
    spec: ProblemSpec,
    model,
    delta_request: DeltaRequest,
    model_name: str,
    api_key: str | None,
    objective_before: float,
    prior_result: Any | None = None,
    warm_start: Any | None = None,
    warm_start_meta: dict[str, Any] | None = None,
    requested_strategy: str = "auto",
    strategy_policy=None,
    trace_metadata: dict[str, Any] | None = None,
) -> StepExecutionResult:
    strategy_policy = strategy_policy or resolve_strategy_policy(
        spec,
        model_name=model_name,
        api_key=api_key,
    )
    planner_mode = _resolve_planner_mode(spec)
    grounding_model = (
        model
        if planner_mode != "codeedit" or isinstance(model, CodeEditState)
        else adapter.build_codeedit_state(spec)
    )

    runtime = adapter.build_runtime(spec, model_name, api_key, None)
    representation = runtime.grounder.ground(
        spec,
        runtime.env,
        grounding_model,
        delta_request=delta_request,
    )
    prompt_context = adapter.build_prompt_context(spec, representation, delta_request)
    configure_planner(runtime.planner, adapter, spec, prompt_context, grounding_model)

    trace = runtime.reporter.start_step(spec, delta_request, representation)
    if trace_metadata:
        trace.update(dict(trace_metadata))
    if hasattr(runtime.planner, "set_trace_dir"):
        runtime.planner.set_trace_dir(trace["step_dir"])
    if hasattr(runtime.planner, "set_log_collector"):
        runtime.planner.set_log_collector(trace)

    planner_output = None
    candidate_patches: list[Patch] = []
    candidate_action_sets: list[PlannedActionSet] = []
    relevant_components: list[str] = []
    strategy = None
    event = None
    strategy_selection = None
    chosen_patch: Patch | None = None
    objective: float | None = None
    solution: Any = None
    solve_meta: dict[str, Any] = {}
    model_after = model
    adapter_after = adapter
    spec_after = spec
    try:
        attempt_failures: list[dict[str, Any]] = []
        max_model_attempts = _model_failure_retry_budget(spec)
        trace["model_attempt_budget"] = max_model_attempts
        repair_context = None
        attempt_count = 0
        while True:
            attempt_count += 1
            attempt_trace_dir = Path(trace["step_dir"]) / f"attempt_{attempt_count:02d}"
            candidate_patches = []
            candidate_action_sets = []
            relevant_components = []
            planner_output = None
            event = None
            strategy = None
            strategy_selection = None
            if hasattr(runtime.planner, "set_repair_context"):
                runtime.planner.set_repair_context(repair_context)
            if hasattr(runtime.planner, "set_trace_dir"):
                runtime.planner.set_trace_dir(attempt_trace_dir)
            try:
                planner_output = runtime.planner.plan(delta_request, representation)
                if planner_output.action_kind not in {"patch", "codeedit"}:
                    raise RuntimeError(f"Unsupported planner action kind {planner_output.action_kind!r}")
                event = planner_output_to_event(planner_output, delta_request)
                relevant_components = list(planner_output.relevant_components)
                raw_action_sets = coerce_action_sets(
                    list(planner_output.candidate_action_sets),
                    fallback_actions=list(planner_output.candidate_actions),
                    group_fallback_actions=True,
                    # Flat multi-edit outputs should validate as one coordinated candidate.
                    include_grouped_fallback_singletons=False,
                )
                if planner_output.action_kind == "codeedit":
                    candidate_action_sets = list(
                        adapter.normalize_codeedit_action_sets(
                            spec,
                            event,
                            raw_action_sets,
                            model=model,
                        )
                    )
                else:
                    candidate_action_sets = list(
                        adapter.normalize_action_sets(
                            spec,
                            event,
                            raw_action_sets,
                            model=model,
                        )
                    )
                candidate_patches = (
                    flatten_patch_actions(candidate_action_sets)
                    if planner_output.action_kind == "patch"
                    else []
                )
                _annotate_planner_outcome(
                    planner_output,
                    candidate_action_sets=candidate_action_sets,
                )
                runtime.reporter.record_planner_step(
                    trace,
                    planner_output=planner_output,
                    normalized_action_sets=candidate_action_sets,
                    normalized_patches=candidate_patches,
                )
                if not candidate_action_sets:
                    if planner_output.action_kind == "codeedit":
                        exc = RuntimeError("Code-edit planner did not return any candidate action sets")
                        _attach_codeedit_error(
                            exc,
                            failure=_codeedit_failure(
                                "invalid_workspace_payload",
                                str(exc),
                                retryable=True,
                            ),
                            attempt_count=attempt_count,
                            repair_count=len(attempt_failures),
                            prior_failures=attempt_failures,
                        )
                        raise exc
                    raise RuntimeError("Planner did not return any candidate action sets")
                if planner_output.action_kind == "codeedit" and not any(
                    action_set.action_kind == "codeedit" for action_set in candidate_action_sets
                ):
                    kinds = sorted({action_set.action_kind for action_set in candidate_action_sets})
                    exc = RuntimeError(
                        "Code-edit normalization did not preserve any codeedit action sets"
                        f" (normalized kinds: {kinds or ['<none>']})"
                    )
                    _attach_codeedit_error(
                        exc,
                        failure=_codeedit_failure(
                            "invalid_workspace_payload",
                            str(exc),
                            retryable=True,
                        ),
                        attempt_count=attempt_count,
                        repair_count=len(attempt_failures),
                        prior_failures=attempt_failures,
                    )
                    raise exc
                if planner_output.action_kind == "codeedit" and _planner_output_is_codeedit_noop(planner_output):
                    exc = RuntimeError("Code-edit planner produced no material file edits")
                    _attach_codeedit_error(
                        exc,
                        failure=_codeedit_failure(
                            "no_edit",
                            str(exc),
                            retryable=True,
                        ),
                        attempt_count=attempt_count,
                        repair_count=len(attempt_failures),
                            prior_failures=attempt_failures,
                        )
                    raise exc

                if planner_output.action_kind == "codeedit":
                    execution_metadata = adapter.classify_codeedit_execution_metadata(
                        spec,
                        delta_request,
                        planner_output,
                        candidate_action_sets,
                    ) or {}
                    if execution_metadata:
                        planner_output.annotations.update(dict(execution_metadata))
                        planner_output.planning_hints.update(
                            {
                                key: value
                                for key, value in dict(execution_metadata).items()
                                if isinstance(value, (str, int, float, bool, list, dict))
                            }
                        )
                    projection_state = str(execution_metadata.get("projection_state") or "").strip().lower()
                    if projection_state == "dead" or (
                        not projection_state and execution_metadata.get("model_effective_edit") is False
                    ):
                        reason = str(
                            execution_metadata.get("model_effective_edit_reason")
                            or "Code-edit diff did not change the optimization model."
                        ).strip()
                        exc = RuntimeError(reason)
                        _attach_codeedit_error(
                            exc,
                            failure=_codeedit_failure(
                                "no_model_effective_edit",
                                reason,
                                retryable=True,
                            ),
                            attempt_count=attempt_count,
                            repair_count=len(attempt_failures),
                            prior_failures=attempt_failures,
                        )
                        raise exc

                strategy_selection = select_strategy(
                    spec,
                    delta_request,
                    planner_output,
                    requested=requested_strategy,
                    prior_result=prior_result,
                    warm_start_available=_warm_start_available(warm_start, warm_start_meta),
                    strategy_policy=strategy_policy,
                )
                strategy = strategy_selection.strategy
                if planner_output.action_kind == "codeedit":
                    try:
                        edited_workspace = _extract_codeedit_workspace(candidate_action_sets)
                    except Exception as exc:
                        _attach_codeedit_error(
                            exc,
                            failure=_codeedit_failure(
                                "invalid_workspace_payload",
                                str(exc),
                                retryable=True,
                            ),
                            attempt_count=attempt_count,
                            repair_count=len(attempt_failures),
                            prior_failures=attempt_failures,
                        )
                        raise
                    try:
                        spec_after, adapter_after = load_problem_from_edit_workspace(spec, edited_workspace)
                    except SyntaxError as exc:
                        _attach_codeedit_error(
                            exc,
                            failure=_codeedit_failure(
                                "compile_failed",
                                str(exc),
                                retryable=True,
                            ),
                            attempt_count=attempt_count,
                            repair_count=len(attempt_failures),
                            prior_failures=attempt_failures,
                        )
                        raise
                    except Exception as exc:
                        _attach_codeedit_error(
                            exc,
                            failure=_codeedit_failure(
                                "reload_failed",
                                str(exc),
                                retryable=True,
                            ),
                            attempt_count=attempt_count,
                            repair_count=len(attempt_failures),
                            prior_failures=attempt_failures,
                        )
                        raise
                    try:
                        codeedit_state = adapter_after.build_codeedit_state(spec_after)
                        execution_options = adapter_after.resolve_execution_options(
                            spec_after,
                            strategy_selection,
                            warm_start,
                            warm_start_meta=warm_start_meta,
                            prior_result=prior_result,
                            delta_request=delta_request,
                            planner_output=planner_output,
                        )
                        solve_result = adapter_after.solve_codeedit(
                            spec_after,
                            codeedit_state,
                            strategy,
                            warm_start=execution_options.warm_start,
                            solve_context=execution_options.solve_context,
                        )
                    except Exception as exc:
                        _attach_codeedit_error(
                            exc,
                            failure=_classify_terminal_codeedit_solve_failure(exc),
                            attempt_count=attempt_count,
                            repair_count=len(attempt_failures),
                            prior_failures=attempt_failures,
                        )
                        raise
                    chosen_patch = None
                    objective = solve_result.objective
                    solution = solve_result.solution
                    solve_meta = dict(solve_result.solve_meta)
                    model_after = codeedit_state
                    _annotate_model_attempts(
                        planner_output,
                        attempt_count=attempt_count,
                        repair_count=len(attempt_failures),
                        prior_failures=attempt_failures,
                    )
                    break

                execution_options = adapter.resolve_execution_options(
                    spec,
                    strategy_selection,
                    warm_start,
                    warm_start_meta=warm_start_meta,
                    prior_result=prior_result,
                    delta_request=delta_request,
                    planner_output=planner_output,
                )
                chosen_patch, objective, solution, solve_meta = validate_and_solve(
                    runtime.validator,
                    candidate_action_sets,
                    model.copy() if hasattr(model, "copy") else model,
                    runtime.env,
                    warm_start=execution_options.warm_start,
                    solve_context=execution_options.solve_context,
                    trace_dir=attempt_trace_dir,
                )
                model_after = apply_patch_sequence(model, selected_patches(runtime.validator, chosen_patch))
                adapter_after = adapter
                spec_after = spec
                _annotate_model_attempts(
                    planner_output,
                    attempt_count=attempt_count,
                    repair_count=len(attempt_failures),
                    prior_failures=attempt_failures,
                )
                break
            except Exception as exc:
                if planner_output is not None:
                    _annotate_planner_outcome(
                        planner_output,
                        candidate_action_sets=candidate_action_sets,
                    )
                failure = _coerce_execution_failure(
                    exc,
                    planner_output=planner_output,
                    planner_mode=planner_mode,
                )
                if (
                    failure is not None
                    and failure.retryable
                    and attempt_count < max_model_attempts
                ):
                    attempt_failures.append(
                        {
                            "attempt": attempt_count,
                            "failure_stage": failure.stage,
                            "failure_kind": failure.kind,
                            "failure_message": failure.message,
                        }
                    )
                    trace["model_attempt_failures"] = list(attempt_failures)
                    if planner_output is not None and planner_output.action_kind == "codeedit":
                        trace["codeedit_attempt_failures"] = list(attempt_failures)
                    repair_context = _build_model_repair_context(
                        failure,
                        attempt_history=attempt_failures,
                    )
                    continue
                if failure is not None:
                    action_kind = str(getattr(planner_output, "action_kind", "") or "").strip().lower()
                    if not action_kind:
                        action_kind = "codeedit" if planner_mode == "codeedit" else "patch"
                    _attach_retry_error(
                        exc,
                        failure=failure,
                        attempt_count=attempt_count,
                        repair_count=len(attempt_failures),
                        prior_failures=attempt_failures,
                        action_kind=action_kind,
                    )
                if planner_output is not None:
                    _annotate_model_attempts(
                        planner_output,
                        attempt_count=attempt_count,
                        repair_count=len(attempt_failures),
                        prior_failures=attempt_failures,
                    )
                raise
    except Exception as exc:
        report_artifacts = runtime.reporter.finalize_failure_step(
            trace,
            prompt_context=prompt_context,
            planner_output=planner_output,
            normalized_action_sets=candidate_action_sets,
            normalized_patches=candidate_patches,
            strategy=strategy,
            strategy_selection=strategy_selection,
            objective_before=objective_before,
            error=exc,
        )
        _attach_failure_context(
            exc,
            trace=trace,
            report_artifacts=report_artifacts,
            planner_output=planner_output,
            planner_mode=planner_mode,
            strategy_selection=strategy_selection,
            event=event,
            relevant_components=relevant_components,
            candidate_action_sets=candidate_action_sets,
            candidate_patches=candidate_patches,
        )
        raise
    finally:
        if hasattr(runtime.planner, "set_repair_context"):
            runtime.planner.set_repair_context(None)

    chosen_patches = [] if chosen_patch is None else selected_patches(runtime.validator, chosen_patch)
    report_artifacts = runtime.reporter.finalize_step(
        trace,
        prompt_context=prompt_context,
        planner_output=planner_output,
        normalized_action_sets=candidate_action_sets,
        normalized_patches=candidate_patches,
        chosen_patches=chosen_patches,
        strategy=strategy,
        strategy_selection=strategy_selection,
        objective_before=objective_before,
        objective_after=objective,
        solve_meta=solve_meta,
        solution=solution,
    )
    return StepExecutionResult(
        prompt_context=prompt_context,
        planner_output=planner_output,
        event=event,
        relevant_components=relevant_components,
        candidate_action_sets=candidate_action_sets,
        candidate_patches=candidate_patches,
        chosen_patch=chosen_patch,
        chosen_patches=chosen_patches,
        strategy=strategy,
        strategy_selection=strategy_selection,
        objective=objective,
        solution=solution,
        solve_meta=solve_meta,
        report_artifacts=report_artifacts,
        model_after=model_after,
        adapter_after=adapter_after,
        spec_after=spec_after,
        trace=trace,
    )


def configure_planner(planner, adapter, spec, prompt_context, model) -> None:
    guidance = adapter.classifier_schema(spec, prompt_context)
    if hasattr(planner, "set_prompt_context"):
        planner.set_prompt_context(prompt_context)
    elif hasattr(planner, "selected_examples"):
        planner.selected_examples = list(prompt_context.selected_examples)
    if hasattr(planner, "set_guidance"):
        planner.set_guidance(guidance)
    elif hasattr(planner, "guidance"):
        planner.guidance = guidance
    if hasattr(planner, "set_model"):
        planner.set_model(model)


def planner_output_to_event(
    planner_output,
    delta_request: DeltaRequest,
) -> StructuredEvent:
    annotations = dict(planner_output.annotations)
    if planner_output.raw_response:
        annotations["planner_raw_response"] = planner_output.raw_response
    if delta_request.metadata:
        annotations["delta_metadata"] = dict(delta_request.metadata)
    return StructuredEvent(
        affected_sets=dict(planner_output.affected_sets),
        edit_summary=planner_output.edit_summary,
        raw_text=delta_request.text,
        annotations=annotations,
    )


def selected_patches(validator, chosen_patch: Patch) -> list[Patch]:
    selected = getattr(validator, "last_selected_patches", None)
    if isinstance(selected, list) and selected:
        return list(selected)
    return [chosen_patch]


def _extract_codeedit_workspace(action_sets: list[PlannedActionSet]) -> CodeEditWorkspace:
    for action_set in action_sets:
        if action_set.action_kind != "codeedit":
            continue
        for action in action_set.actions:
            if not isinstance(action, dict):
                continue
            workspace_problem_root = action.get("workspace_problem_root")
            source_problem_root = action.get("source_problem_root")
            editable_files = action.get("editable_files") or []
            read_only_files = action.get("read_only_files") or []
            artifact_paths = action.get("artifact_paths") or {}
            if not workspace_problem_root or not source_problem_root:
                continue
            problem_root = Path(str(workspace_problem_root)).expanduser().resolve()
            source_root = Path(str(source_problem_root)).expanduser().resolve()
            return CodeEditWorkspace(
                source_problem_root=source_root,
                workspace_root=problem_root.parents[1],
                problem_root=problem_root,
                editable_files=[problem_root / str(relative_path) for relative_path in editable_files],
                read_only_files=[problem_root / str(relative_path) for relative_path in read_only_files],
                artifact_paths={
                    str(name): Path(str(path_value)).expanduser().resolve()
                    for name, path_value in dict(artifact_paths).items()
                },
            )
    raise RuntimeError("Code-edit planner did not return a valid workspace payload")


def _warm_start_available(
    warm_start: Any | None,
    warm_start_meta: dict[str, Any] | None,
) -> bool:
    if warm_start is not None:
        return True
    if not isinstance(warm_start_meta, dict):
        return False
    return warm_start_meta.get("solution_path") not in {None, ""}


def _annotate_planner_outcome(
    planner_output,
    *,
    candidate_action_sets: list[PlannedActionSet],
) -> None:
    parse_fallback = bool(planner_output.planning_hints.get("planner_parse_fallback"))
    planner_output.planning_hints["planner_parse_ok"] = not parse_fallback
    planner_output.planning_hints["planner_output_executable"] = bool(candidate_action_sets)
    planner_output.planning_hints["planner_failed_semantically"] = (
        not parse_fallback and not candidate_action_sets
    )


def _resolve_planner_mode(spec: ProblemSpec) -> str:
    config = spec.config_metadata.get("config") or {}
    return normalize_planner_mode(
        spec.config_metadata.get("planner_mode")
        or config.get("planner_mode")
        or spec.capabilities.get("planner_mode")
        or PATCHEDIT_MODE
    )


def build_change_report(
    chosen_patches: list[Patch],
    previous: float,
    current: float,
    *,
    edit_summary: str | None = None,
) -> str:
    patch_summary = ", ".join(patch.op.value for patch in chosen_patches)
    if not patch_summary and edit_summary:
        patch_summary = edit_summary
    return f"Applied {patch_summary or 'no patches'}; objective {previous:.4f} -> {current:.4f}"


def select_strategy(
    spec: ProblemSpec,
    delta_request: DeltaRequest,
    planner_output,
    *,
    requested: str,
    prior_result,
    warm_start_available: bool,
    strategy_policy,
) -> StrategySelectionDecision:
    normalized = requested.strip().lower()
    if normalized in {"", "auto"}:
        return strategy_policy.select(
            spec,
            delta_request,
            planner_output,
            prior_result=prior_result,
            warm_start_available=warm_start_available,
        )

    return build_manual_strategy_selection(
        spec,
        planner_output,
        requested=requested,
        prior_result=prior_result,
        warm_start_available=warm_start_available,
    )


def validate_and_solve(
    validator,
    patches: list[Patch] | list[PlannedActionSet],
    model,
    env,
    *,
    warm_start: Any | None,
    solve_context: dict[str, Any] | None,
    trace_dir: str | Path | None = None,
):
    resolved_trace_dir = Path(trace_dir) if trace_dir is not None else None
    if resolved_trace_dir is not None:
        resolved_trace_dir.mkdir(parents=True, exist_ok=True)
    try:
        return validator.validate_and_solve(
            patches,
            model,
            env,
            warm_start=warm_start,
            solve_context=solve_context,
            trace_dir=resolved_trace_dir,
        )
    except TypeError as exc:
        message = str(exc)
        if "trace_dir" not in message and "solve_context" not in message:
            raise
        if "trace_dir" in message:
            try:
                return validator.validate_and_solve(
                    patches,
                    model,
                    env,
                    warm_start=warm_start,
                    solve_context=solve_context,
                )
            except TypeError as nested_exc:
                if "solve_context" not in str(nested_exc):
                    raise
        return validator.validate_and_solve(
            patches,
            model,
            env,
            warm_start=warm_start,
        )
