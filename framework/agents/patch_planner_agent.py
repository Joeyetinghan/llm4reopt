"""LLM-powered patch planning agent."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from .patch_parsing import (
    normalize_constraint_rhs_fields,
    normalize_update_bound_fields,
    normalize_update_objective_coeff_fields,
    normalize_update_parameter_fields,
    parse_json_object,
    parse_patch_list,
)
from .patch_prompting import build_operator_guidance, build_schema_lines
from ..core.events import StructuredEvent
from ..core.interfaces import BaseLLMAgent
from ..core.model import StructuredModel
from ..core.patches import Patch, PatchOp


@dataclass
class PatchPlannerOutput:
    patches: List[Patch]


class PatchPlannerAgent(BaseLLMAgent):
    """Agent 2: generate candidate patches using LLM reasoning."""

    def __init__(
        self,
        llm,
        allowed_ops: List[PatchOp] | None = None,
        context_description: str | None = None,
        examples: List[Dict[str, Any]] | None = None,
    ):
        super().__init__(llm)
        self.allowed_ops = allowed_ops
        self.context_description = context_description
        self.examples = list(examples or [])
        self._repair_context: Dict[str, Any] | None = None

    def set_repair_context(self, repair_context: Dict[str, Any] | None) -> None:
        self._repair_context = dict(repair_context) if repair_context else None

    def plan_patches(
        self,
        event: StructuredEvent,
        relevant_components: List[str],
        model: StructuredModel,
    ) -> PatchPlannerOutput:
        examples = self._retrieve_examples(event)
        prompt = self._build_prompt(event, relevant_components, model, examples)
        raw = self._call_llm(prompt, label="PatchPlannerAgent")
        patches = self._parse(raw)
        if not patches:
            raise RuntimeError("LLM did not return any patches")
        return PatchPlannerOutput(patches=patches)

    # ------------------------------------------------------------------
    def _retrieve_examples(self, event: StructuredEvent) -> List[Dict[str, Any]]:
        del event
        return list(self.examples)

    def _build_prompt(
        self,
        event: StructuredEvent,
        components: List[str],
        model: StructuredModel,
        examples: List[Dict[str, Any]],
    ) -> List[Dict[str, str]]:
        examples_str = ""
        for ex in examples:
            examples_str += (
                f"Delta: {ex['delta_text']}\n"
                f"Event: {ex['event']}\n"
                f"Chosen patch: {ex['chosen_patch']}\n\n"
            )

        ops_pool = self.allowed_ops or list(PatchOp)
        allowed_ops_str = ", ".join(op.value for op in ops_pool)

        schema_lines = build_schema_lines(ops_pool)
        guidance_lines = build_operator_guidance(ops_pool)

        system_msg = (
            "You are a model-edit planner. Given (E_t, relevant components), output a JSON object"
            " with key 'candidate_action_sets' listing executable candidate plans."
            " Each candidate_action_sets item must be an object {'actions': [{op, target, scope, update}, ...]}."
            " Use only candidate_action_sets for planned edits; do not emit a top-level patches key."
            f" You MUST choose op from this list only: {allowed_ops_str}."
            " Use these operator-choice rules:\n"
            f"{guidance_lines}"
            " Use the following schemas exactly:\n"
            f"{schema_lines}"
            + " Scope is optional and should only include additional context dictionaries (e.g., {'entity': 'C2'})."
        )
        user_sections = [
            "Structured event E_t:\n"
            f"{event.describe()}",
            "Relevant components:\n"
            f"{components}",
        ]
        if self.context_description:
            user_sections.append(
                "Model representation:\n"
                f"{self.context_description}"
            )
        user_sections.append(
            "Few-shot examples:\n"
            f"{examples_str or 'None'}"
        )
        repair_lines = _render_patch_repair_lines(self._repair_context)
        if repair_lines:
            user_sections.append(
                "Repair guidance:\n"
                + "\n".join(f"- {line}" for line in repair_lines)
            )
        return [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": "\n\n".join(user_sections)},
        ]

    def _parse(self, raw: str) -> List[Patch]:
        data = parse_json_object(raw, label="PatchPlannerAgent")
        patches = _parse_candidate_action_sets(data)
        if patches:
            return patches
        return parse_patch_list(
            data.get("patches", []),
            normalize_patch_fields=_normalize_patch_fields,
        )


def _render_patch_repair_lines(repair_context: Dict[str, Any] | None) -> List[str]:
    if not isinstance(repair_context, dict):
        return []
    lines: List[str] = [
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


def _parse_candidate_action_sets(data: Dict[str, Any]) -> List[Patch]:
    patches: List[Patch] = []
    action_sets = data.get("candidate_action_sets") or []
    if not isinstance(action_sets, list):
        return patches
    for action_set_data in action_sets:
        if isinstance(action_set_data, dict):
            raw_actions = action_set_data.get("actions") or []
        elif isinstance(action_set_data, list):
            raw_actions = action_set_data
        else:
            continue
        patches.extend(
            parse_patch_list(
                raw_actions,
                normalize_patch_fields=_normalize_patch_fields,
            )
        )
    return patches


def _normalize_patch_fields(
    op: PatchOp,
    target: Dict[str, Any],
    scope: Dict[str, Any],
    update: Dict[str, Any],
) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    if op == PatchOp.UPDATE_CONSTRAINT_RHS:
        return normalize_constraint_rhs_fields(
            target,
            scope,
            update,
            scope_keys=["customers", "plants", "indices", "entity"],
            collapse_scope_list=True,
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
            strict_bound_type=False,
        )

    if op == PatchOp.UPDATE_OBJECTIVE_COEFF:
        return normalize_update_objective_coeff_fields(
            target,
            scope,
            update,
            objective_keys=["objective", "objective_id"],
            scope_keys=["arc", "flow", "lane"],
            require_index=True,
            parse_index=_parse_arc_index,
            index_error_message="UPDATE_OBJECTIVE_COEFF requires update['index'] (e.g., ['P1','C2'])",
        )

    if op == PatchOp.FIX_VARIABLES_BY_PATTERN:
        pattern = target.get("pattern")
        if not pattern:
            raise RuntimeError("FIX_VARIABLES_BY_PATTERN requires target['pattern']")
        filters = scope.get("filters") or {}
        lb = update.get("lb", 0.0)
        ub = update.get("ub", 0.0)
        target = {"pattern": pattern}
        scope = {"filters": filters}
        update = {"lb": float(lb), "ub": float(ub)}
        return target, scope, update

    if op == PatchOp.UPDATE_CONSTRAINT_RHS_BY_PATTERN:
        pattern = target.get("pattern")
        if not pattern:
            raise RuntimeError("UPDATE_CONSTRAINT_RHS_BY_PATTERN requires target['pattern']")
        if "value" not in update and "factor" not in update:
            raise RuntimeError("UPDATE_CONSTRAINT_RHS_BY_PATTERN requires update['value'] or update['factor']")
        target = {"pattern": pattern}
        return target, scope, update

    if op == PatchOp.UPDATE_COEFFICIENT:
        vp = target.get("variable_pattern")
        cp = target.get("constraint_pattern")
        if not vp or not cp:
            raise RuntimeError("UPDATE_COEFFICIENT requires target['variable_pattern'] and target['constraint_pattern']")
        if "delta" not in update and "value" not in update:
            raise RuntimeError("UPDATE_COEFFICIENT requires update['delta'] or update['value']")
        target = {"variable_pattern": vp, "constraint_pattern": cp}
        return target, scope, update

    return target, scope, update


def _coerce_parameter_value(name: str, value: Any) -> Any:
    name_lower = name.strip().lower()
    if name_lower in {"reserved_slots", "reserved_slot"}:
        if isinstance(value, (list, tuple, set)):
            cleaned: list[int] = []
            for v in value:
                if isinstance(v, (int, float)):
                    cleaned.append(int(v))
                elif isinstance(v, str) and v.strip().isdigit():
                    cleaned.append(int(v.strip()))
            return cleaned
        if isinstance(value, str):
            parts = [p.strip() for p in value.split(",") if p.strip()]
            if len(parts) > 1:
                return [int(p) for p in parts if p.isdigit()]
            if value.strip().isdigit():
                return [int(value.strip())]
        if isinstance(value, (int, float)):
            return [int(value)]
        return value
    return float(value)


def _parse_arc_index(value: Any) -> tuple[str, str] | None:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return value[0], value[1]
    if isinstance(value, dict):
        src = value.get("source") or value.get("plant")
        dst = value.get("target") or value.get("customer")
        if src and dst:
            return src, dst
    if isinstance(value, str):
        if "->" in value:
            parts = value.split("->", 1)
            return parts[0].strip(), parts[1].strip()
        if "," in value:
            parts = value.split(",", 1)
            return parts[0].strip(), parts[1].strip()
    return None
