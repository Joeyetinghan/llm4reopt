"""Readable per-step planner tracing and reporting."""

from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from framework.core import (
    BaseReporter,
    DeltaRequest,
    ModelRepresentation,
    Patch,
    PlannerOutput,
    PlannedActionSet,
    ProblemSpec,
    PromptContext,
    SolveStrategy,
    StrategySelectionDecision,
)


class FileRunReporter(BaseReporter):
    def __init__(self, *, trace_root: str | Path | None = None):
        self.trace_root = Path(trace_root) if trace_root is not None else None
        self._run_root: Path | None = None
        self._step_index = 0

    def start_step(
        self,
        spec: ProblemSpec,
        delta_request: DeltaRequest,
        representation: ModelRepresentation,
    ) -> dict[str, Any]:
        if self._run_root is None:
            base_root = (
                Path(self.trace_root)
                if self.trace_root is not None
                else spec.metadata.package_root.parents[1] / "tmp" / "planner_traces"
            )
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            self._run_root = base_root / spec.metadata.problem_id / timestamp
        self._step_index += 1
        step_dir = self._run_root / f"step_{self._step_index:02d}"
        step_dir.mkdir(parents=True, exist_ok=True)
        trace = {
            "step_dir": step_dir,
            "delta_text": delta_request.text,
            "representation": representation,
            "steps": [],
        }
        (step_dir / "model_representation.md").write_text(
            representation.render_markdown(),
            encoding="utf-8",
        )
        return trace

    def finalize_step(
        self,
        trace: dict[str, Any],
        *,
        prompt_context: PromptContext,
        planner_output: PlannerOutput,
        normalized_action_sets: list[PlannedActionSet],
        normalized_patches: list[Patch],
        chosen_patches: list[Patch],
        strategy: SolveStrategy,
        strategy_selection: StrategySelectionDecision,
        objective_before: float,
        objective_after: float,
        solve_meta: dict[str, Any],
        solution: Any,
    ) -> dict[str, Any]:
        step_dir = Path(trace["step_dir"])
        self.record_planner_step(
            trace,
            planner_output=planner_output,
            normalized_action_sets=normalized_action_sets,
            normalized_patches=normalized_patches,
        )
        _write_strategy_selection_artifacts(step_dir, strategy_selection)
        _write_json(
            step_dir / "validator_summary.json",
            {
                "chosen_patches": [patch.describe() for patch in chosen_patches],
                "planner_parse_ok": planner_output.planning_hints.get("planner_parse_ok"),
                "planner_output_executable": planner_output.planning_hints.get("planner_output_executable"),
                "planner_failed_semantically": planner_output.planning_hints.get("planner_failed_semantically"),
                "model_attempt_count": planner_output.planning_hints.get("model_attempt_count"),
                "model_retry_count": planner_output.planning_hints.get("model_retry_count"),
                "codeedit_attempt_count": planner_output.planning_hints.get("codeedit_attempt_count"),
                "codeedit_repair_count": planner_output.planning_hints.get("codeedit_repair_count"),
                "execution_ok": True,
                "strategy": strategy.value,
                "strategy_selection": strategy_selection.to_dict(),
                "objective_before": objective_before,
                "objective_after": objective_after,
                "solve_meta": solve_meta,
                "solution": _json_safe(solution),
            },
        )
        (step_dir / "result_summary.md").write_text(
            _render_result_summary(
                prompt_context=prompt_context,
                planner_output=planner_output,
                normalized_action_sets=normalized_action_sets,
                normalized_patches=normalized_patches,
                chosen_patches=chosen_patches,
                strategy=strategy,
                strategy_selection=strategy_selection,
                objective_before=objective_before,
                objective_after=objective_after,
                solve_meta=solve_meta,
            ),
            encoding="utf-8",
        )
        return {"trace_dir": str(step_dir)}

    def record_planner_step(
        self,
        trace: dict[str, Any],
        *,
        planner_output: PlannerOutput,
        normalized_action_sets: list[PlannedActionSet],
        normalized_patches: list[Patch],
    ) -> None:
        step_dir = Path(trace["step_dir"])
        system_msg, user_msg, response = _planner_trace_artifacts(trace, planner_output)
        (step_dir / "planner_system.txt").write_text(system_msg, encoding="utf-8")
        (step_dir / "planner_user.txt").write_text(user_msg, encoding="utf-8")
        (step_dir / "planner_response.txt").write_text(str(response), encoding="utf-8")
        _write_json(step_dir / "planner_output.json", planner_output.to_dict())
        edited_files = planner_output.annotations.get("edited_files")
        if isinstance(edited_files, list):
            _write_json(step_dir / "edited_files.json", edited_files)
        unified_diff = planner_output.annotations.get("unified_diff")
        if isinstance(unified_diff, str) and unified_diff:
            (step_dir / "edited_diff.patch").write_text(unified_diff, encoding="utf-8")
        _write_json(
            step_dir / "normalized_actions.json",
            [action_set.to_dict() for action_set in normalized_action_sets]
            if normalized_action_sets
            else [patch.describe() for patch in normalized_patches],
        )

    def finalize_failure_step(
        self,
        trace: dict[str, Any],
        *,
        prompt_context: PromptContext,
        planner_output: PlannerOutput | None,
        normalized_action_sets: list[PlannedActionSet],
        normalized_patches: list[Patch],
        strategy: SolveStrategy | None,
        strategy_selection: StrategySelectionDecision | None = None,
        objective_before: float,
        error: Exception,
    ) -> dict[str, Any]:
        step_dir = Path(trace["step_dir"])
        if planner_output is not None:
            self.record_planner_step(
                trace,
                planner_output=planner_output,
                normalized_action_sets=normalized_action_sets,
                normalized_patches=normalized_patches,
            )
        else:
            _write_raw_planner_trace(step_dir, trace)
            (step_dir / "planner_parse_error.txt").write_text(str(error), encoding="utf-8")
        _write_strategy_selection_artifacts(step_dir, strategy_selection)
        _write_json(
            step_dir / "validator_summary.json",
            {
                "chosen_patches": [],
                "planner_parse_ok": (
                    planner_output.planning_hints.get("planner_parse_ok")
                    if planner_output is not None
                    else False
                ),
                "planner_output_executable": (
                    planner_output.planning_hints.get("planner_output_executable")
                    if planner_output is not None
                    else False
                ),
                "planner_failed_semantically": (
                    planner_output.planning_hints.get("planner_failed_semantically")
                    if planner_output is not None
                    else False
                ),
                "reopt_failure_stage": getattr(error, "reopt_failure_stage", None),
                "reopt_failure_kind": getattr(error, "reopt_failure_kind", None),
                "reopt_failure_retryable": getattr(error, "reopt_failure_retryable", None),
                "model_attempt_count": getattr(error, "model_attempt_count", None),
                "model_retry_count": getattr(error, "model_retry_count", None),
                "codeedit_failure_kind": getattr(error, "codeedit_failure_kind", None),
                "codeedit_failure_retryable": getattr(error, "codeedit_failure_retryable", None),
                "codeedit_attempt_count": getattr(error, "codeedit_attempt_count", None),
                "codeedit_repair_count": getattr(error, "codeedit_repair_count", None),
                "execution_ok": False,
                "strategy": strategy.value if strategy is not None else None,
                "strategy_selection": _json_safe(strategy_selection),
                "objective_before": objective_before,
                "objective_after": None,
                "solve_meta": {"error": str(error)},
                "solution": None,
            },
        )
        (step_dir / "result_summary.md").write_text(
            (
                _render_failure_summary(
                    prompt_context=prompt_context,
                    planner_output=planner_output,
                    normalized_action_sets=normalized_action_sets,
                    normalized_patches=normalized_patches,
                    strategy=strategy,
                    strategy_selection=strategy_selection,
                    objective_before=objective_before,
                    error=error,
                )
                if planner_output is not None
                else _render_unparsed_failure_summary(
                    prompt_context=prompt_context,
                    strategy=strategy,
                    strategy_selection=strategy_selection,
                    objective_before=objective_before,
                    error=error,
                )
            ),
            encoding="utf-8",
        )
        return {"trace_dir": str(step_dir)}


def _render_result_summary(
    *,
    prompt_context: PromptContext,
    planner_output: PlannerOutput,
    normalized_action_sets: list[PlannedActionSet],
    normalized_patches: list[Patch],
    chosen_patches: list[Patch],
    strategy: SolveStrategy,
    strategy_selection: StrategySelectionDecision,
    objective_before: float,
    objective_after: float,
    solve_meta: dict[str, Any],
) -> str:
    edited_files = planner_output.annotations.get("edited_files")
    lines = [
        f"# {prompt_context.problem.name} planner summary",
        "",
        f"- Delta: {prompt_context.delta_request.text}",
        f"- Action kind: {planner_output.action_kind}",
        f"- Supported ops: {', '.join(prompt_context.supported_patch_ops) or 'None'}",
        f"- Relevant components: {planner_output.relevant_components}",
        f"- Edit summary: {planner_output.edit_summary}",
        f"- Planner parse ok: {planner_output.planning_hints.get('planner_parse_ok')}",
        f"- Planner output executable: {planner_output.planning_hints.get('planner_output_executable')}",
        f"- Planner failed semantically: {planner_output.planning_hints.get('planner_failed_semantically')}",
        f"- Model attempts: {planner_output.planning_hints.get('model_attempt_count')}",
        f"- Model retries: {planner_output.planning_hints.get('model_retry_count')}",
        f"- Strategy: {strategy.value}",
        f"- Execution label: {strategy_selection.execution_label or strategy.value}",
        f"- Strategy policy: {strategy_selection.policy_name}",
        f"- Toolbox plan: {strategy_selection.toolbox_plan}",
        f"- Strategy fallback used: {strategy_selection.fallback_used}",
        f"- Objective: {objective_before:.6f} -> {objective_after:.6f}",
        f"- Solve status: {solve_meta.get('status')}",
        "",
        "## Candidate actions",
        "",
    ]
    if planner_output.action_kind == "codeedit":
        lines[12:12] = [
            f"- Code-edit attempts: {planner_output.planning_hints.get('codeedit_attempt_count')}",
            f"- Code-edit repairs: {planner_output.planning_hints.get('codeedit_repair_count')}",
        ]
    if isinstance(edited_files, list) and edited_files:
        lines.insert(12, f"- Edited files: {', '.join(str(item) for item in edited_files)}")
    if normalized_action_sets:
        for action_set in normalized_action_sets:
            label = action_set.label or action_set.action_kind
            lines.append(f"- action_set `{label}`")
            for action in action_set.actions:
                if isinstance(action, Patch):
                    lines.append(f"  - `{action.op.value}` {action.describe()}")
                else:
                    lines.append(f"  - `{action_set.action_kind}` {_render_generic_action(action)}")
    else:
        for patch in normalized_patches:
            lines.append(f"- `{patch.op.value}` {patch.describe()}")
    lines.extend(["", "## Chosen actions", ""])
    if chosen_patches:
        for patch in chosen_patches:
            lines.append(f"- `{patch.op.value}` {patch.describe()}")
    else:
        lines.append("- Rebuilt and solved from edited source files.")
    return "\n".join(lines)


def _render_failure_summary(
    *,
    prompt_context: PromptContext,
    planner_output: PlannerOutput,
    normalized_action_sets: list[PlannedActionSet],
    normalized_patches: list[Patch],
    strategy: SolveStrategy | None,
    strategy_selection: StrategySelectionDecision | None,
    objective_before: float,
    error: Exception,
) -> str:
    edited_files = planner_output.annotations.get("edited_files")
    lines = [
        f"# {prompt_context.problem.name} planner summary",
        "",
        f"- Delta: {prompt_context.delta_request.text}",
        f"- Action kind: {planner_output.action_kind}",
        f"- Supported ops: {', '.join(prompt_context.supported_patch_ops) or 'None'}",
        f"- Relevant components: {planner_output.relevant_components}",
        f"- Edit summary: {planner_output.edit_summary}",
        f"- Planner parse ok: {planner_output.planning_hints.get('planner_parse_ok')}",
        f"- Planner output executable: {planner_output.planning_hints.get('planner_output_executable')}",
        f"- Planner failed semantically: {planner_output.planning_hints.get('planner_failed_semantically')}",
        f"- Failure stage: {getattr(error, 'reopt_failure_stage', None)}",
        f"- Failure class: {getattr(error, 'reopt_failure_kind', None)}",
        f"- Failure retryable: {getattr(error, 'reopt_failure_retryable', None)}",
        f"- Model attempts: {getattr(error, 'model_attempt_count', None)}",
        f"- Model retries: {getattr(error, 'model_retry_count', None)}",
        f"- Strategy: {strategy.value if strategy is not None else 'not selected'}",
        f"- Execution label: {strategy_selection.execution_label if strategy_selection is not None else 'not selected'}",
        f"- Strategy policy: {strategy_selection.policy_name if strategy_selection is not None else 'not selected'}",
        f"- Toolbox plan: {strategy_selection.toolbox_plan if strategy_selection is not None else []}",
        f"- Strategy fallback used: {strategy_selection.fallback_used if strategy_selection is not None else False}",
        f"- Objective before failure: {objective_before:.6f}",
        f"- Error: {error}",
        "",
        "## Candidate actions",
        "",
    ]
    if planner_output.action_kind == "codeedit" or getattr(error, "codeedit_failure_kind", None):
        lines[15:15] = [
            f"- Code-edit failure kind: {getattr(error, 'codeedit_failure_kind', None)}",
            f"- Code-edit failure retryable: {getattr(error, 'codeedit_failure_retryable', None)}",
            f"- Code-edit attempts: {getattr(error, 'codeedit_attempt_count', None)}",
            f"- Code-edit repairs: {getattr(error, 'codeedit_repair_count', None)}",
        ]
    if isinstance(edited_files, list) and edited_files:
        lines.insert(12, f"- Edited files: {', '.join(str(item) for item in edited_files)}")
    if normalized_action_sets:
        for action_set in normalized_action_sets:
            label = action_set.label or action_set.action_kind
            lines.append(f"- action_set `{label}`")
            for action in action_set.actions:
                if isinstance(action, Patch):
                    lines.append(f"  - `{action.op.value}` {action.describe()}")
                else:
                    lines.append(f"  - `{action_set.action_kind}` {_render_generic_action(action)}")
    else:
        for patch in normalized_patches:
            lines.append(f"- `{patch.op.value}` {patch.describe()}")
    lines.extend(["", "## Result", "", "- Validation/solve failed before a patch was chosen."])
    return "\n".join(lines)


def _render_unparsed_failure_summary(
    *,
    prompt_context: PromptContext,
    strategy: SolveStrategy | None,
    strategy_selection: StrategySelectionDecision | None,
    objective_before: float,
    error: Exception,
) -> str:
    lines = [
        f"# {prompt_context.problem.name} planner summary",
        "",
        f"- Delta: {prompt_context.delta_request.text}",
        f"- Supported ops: {', '.join(prompt_context.supported_patch_ops) or 'None'}",
        f"- Failure stage: {getattr(error, 'reopt_failure_stage', None)}",
        f"- Failure class: {getattr(error, 'reopt_failure_kind', None)}",
        f"- Failure retryable: {getattr(error, 'reopt_failure_retryable', None)}",
        f"- Model attempts: {getattr(error, 'model_attempt_count', None)}",
        f"- Model retries: {getattr(error, 'model_retry_count', None)}",
        f"- Strategy: {strategy.value if strategy is not None else 'not selected'}",
        f"- Execution label: {strategy_selection.execution_label if strategy_selection is not None else 'not selected'}",
        f"- Strategy policy: {strategy_selection.policy_name if strategy_selection is not None else 'not selected'}",
        f"- Objective before failure: {objective_before:.6f}",
        f"- Error: {error}",
        "",
        "## Result",
        "",
        "- Planner failed before a structured planner output could be parsed.",
    ]
    if getattr(error, "codeedit_failure_kind", None):
        lines[9:9] = [
            f"- Code-edit failure kind: {getattr(error, 'codeedit_failure_kind', None)}",
            f"- Code-edit failure retryable: {getattr(error, 'codeedit_failure_retryable', None)}",
            f"- Code-edit attempts: {getattr(error, 'codeedit_attempt_count', None)}",
            f"- Code-edit repairs: {getattr(error, 'codeedit_repair_count', None)}",
        ]
    return "\n".join(lines)


def _planner_trace_artifacts(
    trace: dict[str, Any],
    planner_output: PlannerOutput | None,
) -> tuple[str, str, str]:
    planner_steps = [
        entry
        for entry in trace.get("steps", [])
        if str(entry.get("agent", "")).endswith("PlannerAgent")
    ]
    planner_step = planner_steps[-1] if planner_steps else (trace.get("steps", [{}])[-1] if trace.get("steps") else {})
    prompt = planner_step.get("prompt") or []
    system_msg = next((item.get("content", "") for item in prompt if item.get("role") == "system"), "")
    user_msg = next((item.get("content", "") for item in prompt if item.get("role") == "user"), "")
    response = planner_step.get("response", planner_output.raw_response if planner_output is not None else "")
    return system_msg, user_msg, str(response)


def _write_raw_planner_trace(step_dir: Path, trace: dict[str, Any]) -> None:
    system_msg, user_msg, response = _planner_trace_artifacts(trace, None)
    if system_msg:
        (step_dir / "planner_system.txt").write_text(system_msg, encoding="utf-8")
    if user_msg:
        (step_dir / "planner_user.txt").write_text(user_msg, encoding="utf-8")
    if response:
        (step_dir / "planner_response.txt").write_text(str(response), encoding="utf-8")


def _write_strategy_selection_artifacts(
    step_dir: Path,
    strategy_selection: StrategySelectionDecision | None,
) -> None:
    if strategy_selection is None:
        return
    _write_json(step_dir / "strategy_selection.json", strategy_selection.to_dict())
    if strategy_selection.prompt:
        _write_json(step_dir / "strategy_selector_prompt.json", strategy_selection.prompt)
        system_msg = "\n\n".join(
            str(item.get("content", ""))
            for item in strategy_selection.prompt
            if item.get("role") == "system"
        ).strip()
        user_msg = "\n\n".join(
            str(item.get("content", ""))
            for item in strategy_selection.prompt
            if item.get("role") == "user"
        ).strip()
        if system_msg:
            (step_dir / "strategy_selector_system.txt").write_text(
                system_msg,
                encoding="utf-8",
            )
        if user_msg:
            (step_dir / "strategy_selector_user.txt").write_text(
                user_msg,
                encoding="utf-8",
            )
    if strategy_selection.raw_response:
        (step_dir / "strategy_selector_response.txt").write_text(
            strategy_selection.raw_response,
            encoding="utf-8",
        )


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(_json_safe(payload), indent=2, sort_keys=True), encoding="utf-8")


def _render_generic_action(action: Any) -> str:
    safe = _json_safe(action)
    if isinstance(safe, str):
        return safe
    return json.dumps(safe, sort_keys=True)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, set):
        return sorted((_json_safe(item) for item in value), key=lambda item: str(item))
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _json_safe(getattr(value, field.name)) for field in fields(value)}
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _json_safe(value.to_dict())
    if hasattr(value, "describe") and callable(value.describe):
        return _json_safe(value.describe())
    return value
