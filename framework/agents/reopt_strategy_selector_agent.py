"""LLM-based solve strategy selector."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .patch_parsing import parse_json_object
from framework.core import BaseLLMAgent, PlannerOutput, ProblemSpec
from framework.core.schemas import DeltaRequest


@dataclass
class ReoptStrategySelectorResult:
    strategy_label: str
    toolbox_plan: list[str]
    confidence: float | None
    rationale: str
    raw_response: str
    prompt: list[dict[str, str]]


class ReoptStrategySelectorAgent(BaseLLMAgent):
    """Pick a solve strategy from compact runtime context."""

    def select(
        self,
        *,
        spec: ProblemSpec,
        delta_request: DeltaRequest,
        planner_output: PlannerOutput,
        selection_context,
    ) -> ReoptStrategySelectorResult:
        prompt = self._build_prompt(
            spec=spec,
            delta_request=delta_request,
            planner_output=planner_output,
            selection_context=selection_context,
        )
        raw = self._call_llm(prompt, label="ReoptStrategySelectorAgent")
        try:
            return self._parse(raw, prompt)
        except Exception as exc:
            setattr(exc, "strategy_selector_prompt", list(prompt))
            setattr(exc, "strategy_selector_raw_response", raw)
            raise

    def _build_prompt(
        self,
        *,
        spec: ProblemSpec,
        delta_request: DeltaRequest,
        planner_output: PlannerOutput,
        selection_context,
    ) -> list[dict[str, str]]:
        system_msg = "\n".join(
            [
                "You choose the fastest safe reoptimization solve strategy.",
                "Return JSON only.",
                "Pick exactly one solve strategy from the allowed list.",
                "Do not invent toolbox items or unsupported strategies.",
                "Toolbox plans are executable in this runtime and must match the chosen solve strategy.",
                "Prefer warm+tuned over warm alone when both warm reuse and tuned solving are available and the edit looks reuse-friendly.",
                "Prefer warm reuse for local edits when a reusable solution exists but tuned solving is unavailable or unnecessary.",
                "Prefer tuned or scratch for structural edits when warm reuse looks fragile.",
                'Required JSON keys: "solve_strategy", "toolbox_plan", "rationale".',
                'Optional JSON key: "confidence" as a number in [0, 1].',
            ]
        )
        allowed_strategy_labels = list(
            getattr(
                selection_context,
                "allowed_strategy_labels",
                [item.value for item in selection_context.allowed_strategies],
            )
        )
        user_payload = {
            "problem_id": spec.metadata.problem_id,
            "delta_text": delta_request.text,
            "allowed_strategies": allowed_strategy_labels,
            "toolbox_inventory": list(selection_context.toolbox_inventory),
            "toolbox_placeholders": list(selection_context.toolbox_placeholders),
            "planner_summary": {
                "edit_summary": planner_output.edit_summary,
                "relevant_components": list(planner_output.relevant_components),
                "planning_hints": dict(planner_output.planning_hints),
            },
            "runtime_context": {
                "structural_edit": selection_context.structural_edit,
                "has_prior_result": selection_context.has_prior,
                "has_base_warm_start": selection_context.has_base_warm,
                "warm_start_available": selection_context.warm_start_available,
                "supports_warm_start": selection_context.supports_warm,
                "supports_tuned_solver": selection_context.supports_tuned,
                "patch_count": selection_context.patch_count,
                "planner_mode": getattr(selection_context, "planner_mode", ""),
                "default_execution_label": getattr(selection_context, "default_execution_label", ""),
                "heuristic_warm_start_available": getattr(selection_context, "heuristic_warm_start_available", False),
                "toolbox_execution_mode": "executable",
            },
        }
        return [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": json.dumps(user_payload, indent=2, sort_keys=True)},
        ]

    def _parse(
        self,
        raw: str,
        prompt: list[dict[str, str]],
    ) -> ReoptStrategySelectorResult:
        data = parse_json_object(raw, label="ReoptStrategySelectorAgent")
        raw_toolbox = data.get("toolbox_plan") or []
        if not isinstance(raw_toolbox, list):
            raise RuntimeError("ReoptStrategySelectorAgent expected toolbox_plan to be a list")
        confidence = data.get("confidence")
        if confidence is not None:
            try:
                confidence = float(confidence)
            except (TypeError, ValueError) as exc:
                raise RuntimeError("ReoptStrategySelectorAgent confidence must be numeric") from exc
        return ReoptStrategySelectorResult(
            strategy_label=str(data.get("solve_strategy") or "").strip(),
            toolbox_plan=[str(item) for item in raw_toolbox if item is not None],
            confidence=confidence,
            rationale=str(data.get("rationale") or "").strip(),
            raw_response=raw,
            prompt=list(prompt),
        )
