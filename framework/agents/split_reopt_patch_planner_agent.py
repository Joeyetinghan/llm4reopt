"""Two-stage classifier + patch-planner backend for ablation studies."""

from __future__ import annotations

import json
from typing import Any

from framework.core import ClassifierSchema, ModelRepresentation, PlannerOutput, StructuredModel
from framework.core.planner_modes import PATCHEDIT_TWO_STAGE_MODE
from framework.core.schemas import DeltaRequest

from .classifier_agent import ClassifierAgent
from .patch_planner_agent import PatchPlannerAgent


class SplitReoptPatchPlannerAgent:
    """Planner backend that preserves the legacy two-call LLM workflow."""

    def __init__(
        self,
        llm,
        *,
        allowed_ops=None,
        guidance: ClassifierSchema | None = None,
        prompt_context=None,
        selected_examples: list[dict[str, Any]] | None = None,
    ):
        self.guidance = guidance or ClassifierSchema()
        self.prompt_context = prompt_context
        self.selected_examples = list(selected_examples or [])
        self._repair_context: dict[str, Any] | None = None
        self._model: StructuredModel | None = None
        self.classifier = ClassifierAgent(
            llm,
            classifier_schema=self.guidance,
        )
        self.patch_planner = PatchPlannerAgent(
            llm,
            allowed_ops=list(allowed_ops or []),
            examples=self.selected_examples,
        )

    def set_log_collector(self, collector: dict[str, Any]) -> None:
        self.classifier.set_log_collector(collector)
        self.patch_planner.set_log_collector(collector)

    def set_model(self, model: StructuredModel) -> None:
        self._model = model

    def set_prompt_context(self, prompt_context) -> None:
        self.prompt_context = prompt_context
        self.selected_examples = list(getattr(prompt_context, "selected_examples", []) or [])
        self.patch_planner.examples = list(self.selected_examples)

    def set_guidance(self, guidance: ClassifierSchema | None) -> None:
        self.guidance = guidance or ClassifierSchema()
        self.classifier.classifier_schema = self.guidance

    def set_repair_context(self, repair_context: dict[str, Any] | None) -> None:
        self._repair_context = dict(repair_context) if repair_context else None
        if hasattr(self.patch_planner, "set_repair_context"):
            self.patch_planner.set_repair_context(self._repair_context)

    def plan(
        self,
        delta_request: DeltaRequest,
        representation: ModelRepresentation,
    ) -> PlannerOutput:
        if self._model is None:
            raise RuntimeError("SplitReoptPatchPlannerAgent requires set_model(...) before plan(...)")

        context_description = representation.render_for_llm()
        self.classifier.context_description = context_description
        self.patch_planner.context_description = context_description
        classifier_output = self.classifier.classify(delta_request.text, self._model)
        patch_plan = self.patch_planner.plan_patches(
            classifier_output.event,
            classifier_output.relevant_components,
            self._model,
        )

        annotations = dict(classifier_output.event.annotations)
        annotations["planner_mode"] = PATCHEDIT_TWO_STAGE_MODE
        annotations["classifier_event"] = classifier_output.event.describe()
        raw_response = json.dumps(
            {
                "classifier_event": classifier_output.event.describe(),
                "candidate_actions": [patch.describe() for patch in patch_plan.patches],
            },
            indent=2,
            sort_keys=True,
        )
        return PlannerOutput(
            edit_summary=classifier_output.event.edit_summary or delta_request.text,
            affected_sets=dict(classifier_output.event.affected_sets),
            relevant_components=list(classifier_output.relevant_components),
            action_kind="patch",
            candidate_actions=list(patch_plan.patches),
            candidate_action_sets=[],
            planning_hints={
                "planner_mode": PATCHEDIT_TWO_STAGE_MODE,
                "event_type": classifier_output.event.event_type.value,
                "intention": classifier_output.event.intention.value,
            },
            raw_response=raw_response,
            annotations=annotations,
        )
