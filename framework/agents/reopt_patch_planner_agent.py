"""Single-step LLM planner for patch-based reoptimization."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .patch_parsing import (
    coerce_numeric_or_preserve,
    normalize_add_constraint_family_fields,
    normalize_constraint_rhs_fields,
    normalize_update_bound_fields,
    normalize_update_objective_coeff_fields,
    normalize_update_objective_weight_fields,
    normalize_update_parameter_fields,
    parse_json_object,
    parse_patch_list,
)
from .patch_prompting import build_operator_guidance, build_schema_lines
from .prompt_utils import render_field_block
from framework.core import (
    BaseLLMAgent,
    ClassifierSchema,
    ModelRepresentation,
    PlannerOutput,
    PlannedActionSet,
    PatchOp,
)
from framework.core.schemas import DeltaRequest


@dataclass
class ReoptPatchPlannerResult:
    planner_output: PlannerOutput


class ReoptPatchPlannerAgent(BaseLLMAgent):
    """LLM planner that combines lightweight interpretation and patch planning."""

    def __init__(
        self,
        llm,
        *,
        allowed_ops: list[PatchOp] | None = None,
        guidance: ClassifierSchema | None = None,
        selected_examples: list[dict[str, Any]] | None = None,
    ):
        super().__init__(llm)
        self.allowed_ops = list(allowed_ops or [])
        self.guidance = guidance or ClassifierSchema()
        self.selected_examples = list(selected_examples or [])
        self._repair_context: dict[str, Any] | None = None

    def set_repair_context(self, repair_context: dict[str, Any] | None) -> None:
        self._repair_context = dict(repair_context) if repair_context else None

    def plan(
        self,
        delta_request: DeltaRequest,
        representation: ModelRepresentation,
    ) -> PlannerOutput:
        prompt = self._build_prompt(delta_request, representation)
        raw = self._call_llm(prompt, label="ReoptPatchPlannerAgent")
        try:
            return self._parse(raw, delta_request)
        except RuntimeError as exc:
            return PlannerOutput(
                edit_summary=delta_request.text.strip() or "Planner parse failed",
                affected_sets={},
                relevant_components=[],
                action_kind="patch",
                candidate_actions=[],
                candidate_action_sets=[],
                planning_hints={
                    "planner_parse_fallback": True,
                    "planner_parse_error": str(exc),
                },
                raw_response=raw,
                annotations={
                    "planner_parse_fallback": True,
                    "planner_parse_error": str(exc),
                },
            )

    def _build_prompt(
        self,
        delta_request: DeltaRequest,
        representation: ModelRepresentation,
    ) -> list[dict[str, str]]:
        examples_text = ""
        for example in self.selected_examples:
            examples_text += (
                f"Delta: {example.get('delta_text', '')}\n"
                f"candidate_action_sets: {_render_example_action_sets(example)}\n\n"
            )

        system_sections = [
            "You are a reoptimization planner.",
            "Use the deterministic model representation to interpret the requested change and propose candidate model edits.",
            "Return JSON only.",
            "Use candidate_action_sets as the canonical output format.",
            "Each candidate_action_set is one executable plan. Put all coordinated edits for a single plan in the same action set.",
            "Each candidate_action_sets item must be a JSON object with actions=[...].",
            "The patch list key inside each candidate_action_sets item must be actions; do not use alternate nested keys for the patch list.",
            "Each patch object must use the canonical keys op, target, scope, update, and optional notes.",
            "Return multiple candidate_action_sets only when they are genuinely different alternative plans.",
            "Do not duplicate the same edits in both grouped and flat forms.",
            "Patch payloads must be executable as written: use concrete ids and numeric literals for indices, row labels, and values whenever the model representation provides enough information.",
            "Do not emit pseudo-code, formulas, set names, or symbolic placeholders inside patch indices or values.",
            "For keyed parameter edits, place the concrete sub-index in update.key.",
            "For numeric requests phrased as 'increase by', 'decrease by', or other additive changes, use update.delta instead of overwriting with update.value.",
            "For materialized_linear constraint families, use matching concrete row ids in lhs_spec.rows and rhs_spec, and concrete executable variable indices in every term.",
            "If the representation explicitly exposes a compact problem-specific semantic lhs_spec.kind, you may use that semantic payload instead of materializing every row term.",
            "If you cannot express a valid executable patch, return empty candidate lists rather than a symbolic or guessed placeholder patch.",
            "Required JSON keys:",
            "- edit_summary: short free-form summary of the requested edit",
            "- affected_sets: object mapping relevant entity/set labels to identifiers mentioned or strongly implied by the delta; use {} if none",
            "- relevant_components: list[str] using model component names exactly when possible",
            "- candidate_action_sets: list of executable candidate plans, each {'actions': [{patch}, ...]}",
            "- planning_hints: object with optional planner hints such as edit_scope='local|structural' or expected_reuse='high|low'",
        ]
        if self.guidance.guidance:
            system_sections.extend(["Problem-specific guidance:", self.guidance.guidance])
        if self.guidance.optional_fields:
            system_sections.extend(
                [
                    "Optional extracted fields:",
                    render_field_block(self.guidance.optional_fields),
                ]
            )
        system_sections.extend(
            [
                "Allowed patch operators:",
                ", ".join(op.value for op in self.allowed_ops) or "None",
                "Operator guidance:",
                build_operator_guidance(self.allowed_ops),
                "Patch schemas:",
                build_schema_lines(self.allowed_ops),
            ]
        )
        system_msg = "\n\n".join(section for section in system_sections if section)
        user_sections = [
            "Delta request:",
            delta_request.text,
        ]
        if delta_request.metadata:
            user_sections.extend(["Delta metadata:", json.dumps(delta_request.metadata, indent=2, sort_keys=True)])
        user_sections.extend(
            [
            "Model representation:",
            representation.render_for_llm(),
            ]
        )
        if examples_text:
            user_sections.extend(["Few-shot examples:", examples_text])
        repair_lines = _render_patch_repair_lines(self._repair_context)
        if repair_lines:
            user_sections.extend(["Repair guidance:", "\n".join(f"- {line}" for line in repair_lines)])
        return [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": "\n\n".join(user_sections)},
        ]

    def _parse(self, raw: str, delta_request: DeltaRequest) -> PlannerOutput:
        data = parse_json_object(raw, label="ReoptPatchPlannerAgent")

        parsed_action_sets: list[PlannedActionSet] = []
        for action_set_data in data.get("candidate_action_sets") or []:
            if isinstance(action_set_data, list):
                parsed_action_sets.append(
                    PlannedActionSet(
                        action_kind="patch",
                        actions=parse_patch_list(
                            action_set_data,
                            normalize_patch_fields=_normalize_patch_fields,
                            allow_wrapped=True,
                            allow_inferred_op=True,
                            merge_extra_fields=True,
                        ),
                        label="",
                        metadata={},
                    )
                )
                continue
            if not isinstance(action_set_data, dict):
                continue
            action_kind = str(action_set_data.get("action_kind") or "patch").strip().lower() or "patch"
            if action_kind != "patch":
                raise RuntimeError(f"Unsupported planner action kind {action_kind!r}")
            parsed_action_sets.append(
                PlannedActionSet(
                    action_kind="patch",
                    actions=parse_patch_list(
                        action_set_data.get("actions") or [],
                        normalize_patch_fields=_normalize_patch_fields,
                        allow_wrapped=True,
                        allow_inferred_op=True,
                        merge_extra_fields=True,
                    ),
                    label=str(action_set_data.get("label") or "").strip(),
                    metadata=dict(action_set_data.get("metadata") or {}),
                )
            )

        planning_hints = dict(data.get("planning_hints") or {})
        if not parsed_action_sets:
            legacy_candidate_actions = data.get("candidate_actions")
            if legacy_candidate_actions is None:
                legacy_candidate_actions = data.get("patches", [])
            legacy_patches = parse_patch_list(
                legacy_candidate_actions or [],
                normalize_patch_fields=_normalize_patch_fields,
                allow_wrapped=True,
                allow_inferred_op=True,
                merge_extra_fields=True,
            )
            if legacy_patches:
                parsed_action_sets.append(
                    PlannedActionSet(
                        action_kind="patch",
                        actions=legacy_patches,
                        label="legacy_candidate_actions",
                        metadata={"coerced_from": "candidate_actions"},
                    )
                )
                planning_hints["legacy_candidate_actions_wrapped"] = True

        relevant_components = data.get("relevant_components") or []
        if not isinstance(relevant_components, list):
            relevant_components = [str(relevant_components)]

        affected_sets = data.get("affected_sets") or {}
        if not isinstance(affected_sets, dict):
            affected_sets = {}

        action_kind = str(data.get("action_kind") or "patch").strip().lower() or "patch"
        if action_kind != "patch":
            raise RuntimeError(f"Unsupported planner action kind {action_kind!r}")

        return PlannerOutput(
            edit_summary=str(data.get("edit_summary") or delta_request.text).strip(),
            affected_sets=affected_sets,
            relevant_components=[str(item) for item in relevant_components if item is not None],
            action_kind="patch",
            candidate_actions=[],
            candidate_action_sets=parsed_action_sets,
            planning_hints=planning_hints,
            raw_response=raw,
            annotations=data,
        )


def _render_patch_repair_lines(repair_context: dict[str, Any] | None) -> list[str]:
    if not isinstance(repair_context, dict):
        return []
    lines: list[str] = [
        "This is a fresh repair attempt for the same user request from a fresh planning pass.",
        "Preserve the user's intent, not the previous implementation details.",
        "Use the runtime feedback below only to avoid the previous failure mode.",
        "The items below are runtime feedback from earlier attempts in this same run.",
    ]
    failure_stage = str(repair_context.get("failure_stage") or "").strip()
    failure_kind = str(repair_context.get("failure_kind") or "").strip()
    failure_message = str(repair_context.get("failure_message") or "").strip()
    repair_instruction = str(repair_context.get("repair_instruction") or "").strip()
    if failure_stage:
        lines.append(f"Failure stage: {failure_stage}")
    if failure_kind:
        lines.append(f"Failure class: {failure_kind}")
    if failure_message:
        lines.append(f"Previous failure: {failure_message}")
    if repair_instruction:
        lines.append(f"Instruction: {repair_instruction}")
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


def _render_example_action_sets(example: dict[str, Any]) -> str:
    patch_payload = example.get("chosen_patches")
    if patch_payload is None or patch_payload == "":
        patch_payload = example.get("chosen_patch")
    if patch_payload is None or patch_payload == "":
        return "[]"

    if isinstance(patch_payload, list):
        patches = [_coerce_example_payload(item) for item in patch_payload]
    else:
        patches = [_coerce_example_payload(patch_payload)]
    return json.dumps([{"actions": patches}], sort_keys=True, default=str)


def _coerce_example_payload(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped:
        return value
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return value


def _normalize_patch_fields(
    op: PatchOp,
    target: dict[str, Any],
    scope: dict[str, Any],
    update: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if op == PatchOp.UPDATE_CONSTRAINT_RHS:
        return normalize_constraint_rhs_fields(
            target,
            scope,
            update,
            scope_keys=["index", "customers", "plants", "indices", "entity"],
        )

    if op == PatchOp.UPDATE_PARAMETER:
        return normalize_update_parameter_fields(
            target,
            scope,
            update,
            scope_keys=["entity", "customer", "plant"],
            coerce_value=_coerce_parameter_value,
            require_value=False,
            preserve_delta=True,
        )

    if op == PatchOp.UPDATE_BOUND:
        return normalize_update_bound_fields(
            target,
            scope,
            update,
            scope_keys=["entity", "arc", "flow", "lane"],
            default_variable="flows",
            strict_bound_type=True,
        )

    if op == PatchOp.UPDATE_OBJECTIVE_COEFF:
        return normalize_update_objective_coeff_fields(
            target,
            scope,
            update,
            objective_keys=["objective", "objective_name"],
            default_objective="transport_cost",
            scope_keys=["entity", "pair", "route"],
            require_index=False,
        )

    if op == PatchOp.UPDATE_OBJECTIVE_WEIGHT:
        return normalize_update_objective_weight_fields(
            target,
            scope,
            update,
            coerce_value=coerce_numeric_or_preserve,
        )

    if op == PatchOp.ADD_CONSTRAINT_FAMILY:
        return normalize_add_constraint_family_fields(target, scope, update)

    return target, scope, update


def _coerce_parameter_value(name: str, value: Any) -> Any:
    if name == "reserved_slots" and isinstance(value, (list, tuple, set)):
        return [_coerce_semantic_slot_value(item) for item in value]
    if isinstance(value, (int, float)):
        return float(value)
    return value


def _coerce_semantic_slot_value(value: Any) -> Any:
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
        return stripped
    return value
