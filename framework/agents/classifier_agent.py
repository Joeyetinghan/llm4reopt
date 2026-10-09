"""LLM-powered component classifier agent."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, List, Mapping

from .prompt_utils import fix_leading_zero_numbers, render_field_block, strip_markdown_fences
from ..core.events import ClassifierSchema, EventType, IntentionType, StructuredEvent
from ..core.interfaces import BaseLLMAgent
from ..core.model import StructuredModel


@dataclass
class ClassifierOutput:
    event: StructuredEvent
    relevant_components: List[str]


class ClassifierAgent(BaseLLMAgent):
    """Agent 1: classify delta text into structured events."""

    def __init__(
        self,
        llm,
        classifier_schema: ClassifierSchema | None = None,
        context_description: str | None = None,
    ):
        super().__init__(llm)
        self.classifier_schema = classifier_schema or ClassifierSchema()
        self.context_description = context_description

    def classify(self, delta_text: str, model: StructuredModel) -> ClassifierOutput:
        prompt = self._build_prompt(delta_text, model)
        raw = self._call_llm(prompt, label="ClassifierAgent")
        return self._parse(raw, delta_text)

    # ------------------------------------------------------------------
    def _build_prompt(self, delta_text: str, model: StructuredModel) -> List[Dict[str, str]]:
        component_descriptions: List[str] = []
        for name, fam in model.variables.items():
            component_descriptions.append(
                f"[VariableFamily] {name}: {fam.desc} tags={sorted(fam.tags)}"
            )
        for name, cons in model.constraints.items():
            component_descriptions.append(
                f"[ConstraintFamily] {name}: {cons.desc} tags={sorted(cons.tags)}"
            )
        for name, obj in model.objectives.items():
            component_descriptions.append(
                f"[ObjectiveComponent] {name}: {obj.desc} tags={sorted(obj.tags)}"
            )
        if model.parameters:
            component_descriptions.append(
                f"[Parameters] {sorted(model.parameters.keys())}"
            )

        system_sections = [
            "You are an optimization-structure interpreter.",
            "Analyze the natural-language change and return a lightweight structured JSON description.",
            "Do not force the request into a fixed global taxonomy unless problem-specific guidance asks for one.",
            "Required JSON keys:",
            render_field_block(self.classifier_schema.required_fields),
        ]
        if self.classifier_schema.optional_fields:
            system_sections.extend(
                [
                    "Optional JSON keys (include only when clearly supported by the delta):",
                    render_field_block(self.classifier_schema.optional_fields),
                ]
            )
        if self.classifier_schema.guidance:
            system_sections.extend(
                [
                    "Problem-specific guidance:",
                    self.classifier_schema.guidance,
                ]
            )
        system_sections.append("Output JSON only.")
        system_msg = "\n\n".join(section for section in system_sections if section)
        user_msg = (
            "Natural-language change (delta_t):\n"
            f"{delta_text}\n\n"
            "Model components:\n"
            f"{chr(10).join(component_descriptions)}\n\n"
            "Instructions:\n"
            "1. Fill every required JSON key from the system message.\n"
            "2. Use model component names exactly when listing relevant_components.\n"
            "3. affected_sets should map natural entity/set labels to identifiers mentioned or strongly implied by the delta.\n"
            "4. edit_summary should be a short literal summary of the requested edit.\n"
            "5. Omit optional fields unless they are well supported by the request.\n"
            "6. Output JSON only."
        )
        if self.context_description:
            user_msg += (
                "\n\nModel representation:\n"
                f"{self.context_description}"
            )
        return [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ]

    # ------------------------------------------------------------------
    def _parse(self, raw: str, delta_text: str) -> ClassifierOutput:
        cleaned = strip_markdown_fences(raw)
        cleaned = fix_leading_zero_numbers(cleaned)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"ClassifierAgent could not parse JSON: {raw}") from exc

        event_type = _coerce_event_type(data.get("event_type"))
        intention = _coerce_intention(data.get("intention"))
        affected_sets = data.get("affected_sets") or {}
        if not isinstance(affected_sets, Mapping):
            affected_sets = {}
        else:
            affected_sets = dict(affected_sets)
        edit_summary = str(data.get("edit_summary") or data.get("summary") or delta_text).strip()
        relevant_components = data.get("relevant_components") or []
        if not isinstance(relevant_components, list):
            relevant_components = [str(relevant_components)]
        relevant_components = [str(component) for component in relevant_components if component is not None]

        event = StructuredEvent(
            event_type=event_type,
            affected_sets=affected_sets,
            edit_summary=edit_summary,
            intention=intention,
            raw_text=delta_text,
            annotations=data,
        )
        # [TODO]: add confidence score from LLM output to guide downstream logic.
        return ClassifierOutput(event=event, relevant_components=relevant_components)


def _coerce_event_type(raw: str | None) -> EventType:
    if not raw:
        return EventType.UNKNOWN
    raw_lower = raw.strip().lower()
    synonyms = {
        "parameter_update": EventType.PARAMETER_UPDATE,
        "update_parameter": EventType.PARAMETER_UPDATE,
        "change_capacity": EventType.CAPACITY_LOSS,
        "capacity_change": EventType.CAPACITY_LOSS,
        "cost_adjustment": EventType.COST_ADJUSTMENT,
        "cost_change": EventType.COST_ADJUSTMENT,
    }
    if raw_lower in synonyms:
        return synonyms[raw_lower]
    for candidate in EventType:
        if candidate.value == raw_lower:
            return candidate
    return EventType.UNKNOWN


def _coerce_intention(raw: str | None) -> IntentionType:
    if not raw:
        return IntentionType.UPDATE
    raw_lower = raw.strip().lower()
    for candidate in IntentionType:
        if candidate.value == raw_lower:
            return candidate
    return IntentionType.UPDATE
