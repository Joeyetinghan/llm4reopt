"""Structured event and classifier-schema representations used by the planner agents."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List


class EventType(str, Enum):
    PARAMETER_UPDATE = "parameter_update"
    CAPACITY_LOSS = "capacity_loss"
    NEW_RULE = "new_rule"
    COST_ADJUSTMENT = "cost_adjustment"
    UNKNOWN = "unknown"


class IntentionType(str, Enum):
    TIGHTEN = "tighten"
    RELAX = "relax"
    FORBID = "forbid"
    PRIORITIZE = "prioritize"
    UPDATE = "update"


_DEFAULT_CLASSIFIER_REQUIRED_FIELDS = {
    "relevant_components": (
        "list[str] of the model component names most relevant to the requested change"
    ),
    "affected_sets": (
        "object mapping meaningful entity/set labels to identifiers mentioned or strongly implied by the delta; use {} if none"
    ),
    "edit_summary": "short free-form summary of the requested edit",
}


@dataclass(frozen=True)
class ClassifierSchema:
    """Problem-specific guidance for the shared classifier agent."""

    required_fields: Dict[str, str] = field(
        default_factory=lambda: dict(_DEFAULT_CLASSIFIER_REQUIRED_FIELDS)
    )
    optional_fields: Dict[str, str] = field(default_factory=dict)
    guidance: str = ""
    codeedit_guidance: str = ""


@dataclass
class StructuredEvent:
    """Lightweight classification payload passed into patch planning.

    ``event_type`` and ``intention`` are kept as optional legacy metadata. The
    core shared contract is now ``affected_sets`` plus ``edit_summary``.
    """

    event_type: EventType = EventType.UNKNOWN
    affected_sets: Dict[str, List[Any]] = field(default_factory=dict)
    edit_summary: str = ""
    intention: IntentionType = IntentionType.UPDATE
    raw_text: str = ""
    annotations: Dict[str, Any] = field(default_factory=dict)

    def describe(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type.value,
            "affected_sets": self.affected_sets,
            "edit_summary": self.edit_summary,
            "intention": self.intention.value,
            "raw_text": self.raw_text,
            "annotations": self.annotations,
        }
