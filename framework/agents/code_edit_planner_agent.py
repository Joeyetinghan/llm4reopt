"""Aider-backed code-edit planner/executor agents."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from framework.core import (
    ClassifierSchema,
    ModelRepresentation,
    PlannerOutput,
    PlannedActionSet,
    ProblemSpec,
    PromptContext,
)
from framework.core.schemas import (
    DeltaRequest,
    _compact_component_lines,
    _compact_model_counts,
    _compact_solver_capabilities,
)
from framework.editing.aider import prepare_problem_edit_workspace, run_aider_edit
from framework.llm import DEFAULT_LLM_MODEL
from .prompt_utils import render_field_block


CODEEDIT_DEFAULT_MODEL = os.getenv("REOPT_CODEEDIT_DEFAULT_MODEL", "gpt-4.1-mini")


@dataclass(frozen=True)
class CodeEditPlannerSpec:
    substrate: str
    description: str


class CodeEditPlannerAgent:
    """Shared state and tracing hooks for code-edit planner backends."""

    spec = CodeEditPlannerSpec(
        substrate="generic",
        description="Generic code-edit planner.",
    )

    def __init__(
        self,
        *,
        problem_spec: ProblemSpec,
        model_name: str,
        api_key: str | None,
        prompt_context: PromptContext | None = None,
    ):
        self.problem_spec = problem_spec
        self.model_name = _resolve_codeedit_model_name(model_name)
        self.api_key = api_key
        self.prompt_context = prompt_context
        self._log_collector: dict[str, Any] | None = None
        self._trace_dir: Path | None = None
        self._repair_context: dict[str, Any] | None = None
        self.guidance = ClassifierSchema()

    def set_log_collector(self, collector: dict[str, Any]) -> None:
        self._log_collector = collector

    def set_prompt_context(self, prompt_context: PromptContext) -> None:
        self.prompt_context = prompt_context

    def set_trace_dir(self, trace_dir: str | Path) -> None:
        self._trace_dir = Path(trace_dir)

    def set_repair_context(self, repair_context: dict[str, Any] | None) -> None:
        self._repair_context = dict(repair_context) if repair_context else None

    def set_guidance(self, guidance: ClassifierSchema | None) -> None:
        self.guidance = guidance or ClassifierSchema()

    def set_model(self, model) -> None:
        del model

    def plan(self, delta_request: DeltaRequest, representation: ModelRepresentation) -> PlannerOutput:
        raise NotImplementedError(self.spec.description)

    def _record_prompt(self, *, system_msg: str, user_msg: str) -> None:
        if self._log_collector is None:
            return
        self._log_collector.setdefault("steps", []).append(
            {
                "agent": self.__class__.__name__,
                "prompt": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
            }
        )

    def _record_response(self, response: str) -> None:
        if self._log_collector is None or not self._log_collector.get("steps"):
            return
        self._log_collector["steps"][-1]["response"] = response


class AiderCodeEditPlannerAgent(CodeEditPlannerAgent):
    """Bounded code-edit planner that delegates source edits to Aider."""

    spec = CodeEditPlannerSpec(
        substrate="python_builder",
        description=(
            "Aider-backed code-edit planner for Python-backed packaged problems. "
            "The editable substrate is restricted to surfaced solver artifacts."
        ),
    )

    def plan(self, delta_request: DeltaRequest, representation: ModelRepresentation) -> PlannerOutput:
        workspace = prepare_problem_edit_workspace(
            self.problem_spec,
            trace_dir=self._trace_dir,
        )
        system_msg, user_msg = self._build_prompt(
            delta_request,
            representation,
            workspace.editable_file_labels,
            workspace.read_only_file_labels,
        )
        self._record_prompt(system_msg=system_msg, user_msg=user_msg)

        edit_result = run_aider_edit(
            workspace,
            prompt=user_msg,
            model_name=self.model_name,
            api_key=self.api_key,
        )
        response = edit_result.stdout.strip() or edit_result.stderr.strip() or json.dumps(
            {"changed_files": edit_result.changed_files}
        )
        self._record_response(response)

        action = edit_result.to_action_payload()
        annotations = {
            "planner_mode": "codeedit",
            "planner_model": self.model_name,
            "editable_files": workspace.editable_file_labels,
            "read_only_files": workspace.read_only_file_labels,
            "edited_files": list(edit_result.changed_files),
            "workspace_problem_root": str(workspace.problem_root),
            "unified_diff": edit_result.unified_diff,
            "planner_warnings": list(edit_result.warnings),
        }
        return PlannerOutput(
            edit_summary=delta_request.text.strip() or "Edit source files",
            affected_sets={},
            relevant_components=[],
            action_kind="codeedit",
            candidate_actions=[action],
            candidate_action_sets=[
                PlannedActionSet(
                    action_kind="codeedit",
                    actions=[action],
                    label="aider_edit",
                    metadata={"changed_files": list(edit_result.changed_files)},
                )
            ],
            planning_hints={
                "planner_mode": "codeedit",
                "planner_model": self.model_name,
                "editable_file_count": len(workspace.editable_file_labels),
                "changed_file_count": len(edit_result.changed_files),
                "planner_warning_count": len(edit_result.warnings),
                "planner_warnings": list(edit_result.warnings),
                "codeedit_noop": not edit_result.changed_files,
            },
            raw_response=response,
            annotations=annotations,
        )

    def _build_prompt(
        self,
        delta_request: DeltaRequest,
        representation: ModelRepresentation,
        editable_files: list[str],
        read_only_files: list[str],
    ) -> tuple[str, str]:
        system_lines = [
            "You are editing a packaged re-optimization problem through Aider.",
            "Modify only the editable files provided to you; use any read-only files only as context.",
            "Keep the code executable and minimal.",
            "Prefer changing the canonical solver implementation that is rebuilt and re-solved.",
            "Honor the natural-language request and any generic problem guidance provided below.",
        ]
        system_lines.extend(self._extra_system_lines(representation))
        system_msg = "\n".join(system_lines)
        target_lines = _render_preferred_targets(self.problem_spec, editable_files)
        grounding_text = _render_codeedit_grounding(representation)
        user_sections = [
            "Requested change:",
            delta_request.text,
            "",
            "Editable files:",
            "\n".join(f"- {path}" for path in editable_files),
        ]
        if read_only_files:
            user_sections.extend(["", "Read-only context files:", "\n".join(f"- {path}" for path in read_only_files)])
        if target_lines:
            user_sections.extend(["", "Preferred edit targets:", "\n".join(f"- {line}" for line in target_lines)])
        if grounding_text:
            user_sections.extend(["", "Code-edit grounding:", grounding_text])
        guidance_lines = _render_codeedit_guidance_lines(self.guidance)
        if guidance_lines:
            user_sections.extend(["", "Problem-specific guidance:", "\n".join(f"- {line}" for line in guidance_lines)])
        identifier_hints = _render_identifier_hints(representation)
        if identifier_hints:
            user_sections.extend(["", "Identifier hints:", "\n".join(f"- {line}" for line in identifier_hints)])
        problem_notes = _render_problem_specific_notes(self.problem_spec, delta_request.text)
        if problem_notes:
            user_sections.extend(["", "Problem-specific edit notes:", "\n".join(f"- {line}" for line in problem_notes)])
        extra_user_rules = self._extra_user_guidance_lines(representation)
        if extra_user_rules:
            user_sections.extend(["", "Additional code-edit rules:", "\n".join(f"- {line}" for line in extra_user_rules)])
        repair_lines = _render_codeedit_repair_lines(self._repair_context)
        if repair_lines:
            user_sections.extend(["", "Repair guidance:", "\n".join(f"- {line}" for line in repair_lines)])
        if representation.context_payload:
            user_sections.extend(["", "Problem context:", representation.context_payload])
        if self.prompt_context is not None and self.prompt_context.selected_examples:
            examples_text = []
            for example in self.prompt_context.selected_examples:
                examples_text.append(
                    f"Delta: {example.get('delta_text', '')}\n"
                    f"Chosen patch: {example.get('chosen_patch') or example.get('chosen_patches')}"
                )
            user_sections.extend(["", "Reference examples:", "\n\n".join(examples_text)])
        return system_msg, "\n".join(section for section in user_sections if section is not None)

    def _extra_system_lines(self, representation: ModelRepresentation) -> list[str]:
        del representation
        return []

    def _extra_user_guidance_lines(self, representation: ModelRepresentation) -> list[str]:
        del representation
        return []


class PythonCodeEditPlannerAgent(AiderCodeEditPlannerAgent):
    spec = CodeEditPlannerSpec(
        substrate="python_builder",
        description=(
            "Aider-backed code-edit planner for Python-backed problems. "
            "The editable substrate is the declared solver-side codeedit surface."
        ),
    )


class LPWrapperCodeEditPlannerAgent(AiderCodeEditPlannerAgent):
    spec = CodeEditPlannerSpec(
        substrate="lp_wrapper_python",
        description=(
            "Aider-backed code-edit planner for LP-backed packaged problems. "
            "The editable substrate is a thin generated Python wrapper over a copied LP file."
        ),
    )

    def _extra_system_lines(self, representation: ModelRepresentation) -> list[str]:
        del representation
        return [
            "For LP-wrapper editing, build_codeedit_model(...) must only mutate and return a Gurobi model.",
            "Do not solve the model or inspect solution values inside build_codeedit_model(...).",
        ]

    def _extra_user_guidance_lines(self, representation: ModelRepresentation) -> list[str]:
        del representation
        return [
            "Inside build_codeedit_model(...), mutate the model and return it; do not call model.optimize() or any solve routine there.",
            "Do not read solution attributes such as var.X, reduced costs, dual values, or objective values inside build_codeedit_model(...).",
            "Prefer using the existing generic helper functions when they fit the requested change.",
            "Choose patterns that match actual names shown in the read-only LP summaries; avoid edits that silently match nothing.",
            "If you use a generic helper, make sure it matches the intended rows or variables; helper calls that match zero targets are treated as failures.",
        ]


def _resolve_codeedit_model_name(model_name: str) -> str:
    normalized = model_name.strip()
    if not normalized or normalized == DEFAULT_LLM_MODEL:
        return CODEEDIT_DEFAULT_MODEL
    return normalized


def _render_preferred_targets(spec: ProblemSpec, editable_files: list[str]) -> list[str]:
    editable_set = set(editable_files)
    source_root = spec.metadata.package_root.expanduser().resolve()
    targets: list[str] = []

    for artifact in spec.codeedit_artifacts:
        artifact_path = Path(artifact.path).expanduser().resolve()
        try:
            relative = str(artifact_path.relative_to(source_root))
        except ValueError:
            continue
        if relative not in editable_set:
            continue
        target = relative
        if artifact.prompt_symbol:
            target += f" :: {artifact.prompt_symbol}"
        if artifact.description:
            target += f" ({artifact.description})"
        targets.append(target)

    if targets:
        return targets

    return list(editable_files)


def _render_codeedit_grounding(representation: ModelRepresentation) -> str:
    lines = [f"Representation kind: {representation.representation_kind}"]

    counts_line = _compact_model_counts(representation.model_summary, representation.component_descriptors)
    if counts_line:
        lines.append(f"Model counts: {counts_line}")

    solver_line = _compact_solver_capabilities(representation.solver_capabilities)
    if solver_line:
        lines.append(f"Solver capabilities: {solver_line}")

    parameter_lines = _compact_component_lines(
        descriptor
        for descriptor in representation.component_descriptors
        if descriptor.component_type == "parameter"
    )
    if parameter_lines:
        lines.append("Key parameters:")
        lines.extend(parameter_lines[:6])

    return "\n".join(lines)


def _render_identifier_hints(representation: ModelRepresentation) -> list[str]:
    hints: list[str] = []
    for descriptor in representation.component_descriptors:
        if descriptor.component_type != "parameter":
            continue
        if descriptor.name == "plants":
            hints.extend(_ordinal_identifier_hints(descriptor.sample_members, prefix="P", label="Plant"))
        elif descriptor.name == "customers":
            hints.extend(_ordinal_identifier_hints(descriptor.sample_members, prefix="C", label="Customer"))
    if hints:
        hints.append("Use these exact package identifiers in code edits; do not spell them out as natural-language names.")
    return hints


def _ordinal_identifier_hints(values: list[str], *, prefix: str, label: str) -> list[str]:
    hints: list[str] = []
    for value in values:
        stripped = str(value).strip()
        if not stripped.startswith(prefix):
            continue
        suffix = stripped[len(prefix):]
        if suffix.isdigit():
            hints.append(f'{label} {suffix} -> "{stripped}"')
    return hints


def _render_problem_specific_notes(spec: ProblemSpec, delta_text: str) -> list[str]:
    del delta_text

    if spec.metadata.problem_id == "exam_block_seq":
        return [
            'The main solver code surfaces are build_exam_gurobi_model(...) for the base Gurobi formulation and solve_exam_direct(...) for runtime data preparation, warm start application, and solve orchestration.',
            'In solve_exam_direct(...), data["blocks"] is the ordered slot-id list and data["slots_per_day"] is the day width; if data["slots"] is present it is a scalar count, not an iterable slot list.',
            'Do not invent new runtime_data or solver_params keys; only pass real Gurobi parameters through _apply_solver_params(...).',
            'Prefer editing weights, pair_counts, reserved_slots/large_blocks/early_slots derivation, or the direct model constraints in build_exam_gurobi_model(...).',
            "Preserve additive semantics from the request: wording like 'increase by' should usually modify existing values rather than replace them.",
            "When the request refers to a relationship between two blocks, keep the update symmetric unless the request explicitly distinguishes direction.",
            'Slot reservation requests should ground through slot_times and slots_per_day, then reserve the resolved slot via the virtual-block mechanism.',
            'Day-level load changes should use data["block_enrollment"] with first-index occupancy over the relevant slots.',
            "If a combined request specifies an order, preserve that order in the implementation.",
        ]

    return []


def _render_codeedit_guidance_lines(guidance: ClassifierSchema | None) -> list[str]:
    if guidance is None:
        return []

    lines: list[str] = []
    guidance_text = str(guidance.codeedit_guidance or guidance.guidance or "").strip()
    if guidance_text:
        lines.append(guidance_text)
    if guidance.optional_fields:
        lines.append("Helpful structured fields when the request or context makes them explicit:")
        lines.append(render_field_block(guidance.optional_fields))
    return lines


def _render_codeedit_repair_lines(repair_context: dict[str, Any] | None) -> list[str]:
    if not repair_context:
        return []

    lines = [
        "This is a fresh repair attempt for the same user request from a fresh copy of the source tree.",
        "Preserve the user's intent, not the previous implementation details.",
        "Use the runtime feedback below only to avoid the previous failure mode.",
        "The items below are runtime feedback from earlier attempts in this same run.",
    ]
    failure_kind = repair_context.get("failure_kind")
    if failure_kind:
        lines.append(f"Failure class: {failure_kind}")
    failure_message = str(repair_context.get("failure_message") or "").strip()
    if failure_message:
        lines.append(f"Observed failure: {failure_message}")
    repair_instruction = str(repair_context.get("repair_instruction") or "").strip()
    if repair_instruction:
        lines.append(repair_instruction)
    attempt_history = repair_context.get("attempt_history")
    if isinstance(attempt_history, list) and attempt_history:
        lines.append("Recent failed attempts:")
        for item in attempt_history:
            if not isinstance(item, dict):
                continue
            attempt = item.get("attempt")
            attempt_stage = str(item.get("failure_stage") or "").strip()
            attempt_kind = str(item.get("failure_kind") or "").strip()
            attempt_message = str(item.get("failure_message") or "").strip()
            attempt_label = f"attempt {attempt}" if attempt not in {None, ""} else "attempt"
            detail = " | ".join(part for part in (attempt_stage, attempt_kind, attempt_message) if part)
            if detail:
                lines.append(f"{attempt_label}: {detail}")
    return lines
