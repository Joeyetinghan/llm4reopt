"""Helpers for grouped planner action sets."""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from .patches import Patch
from .schemas import PlannedActionSet


def coerce_action_sets(
    items: Sequence[Any] | None = None,
    *,
    fallback_actions: Sequence[Any] | None = None,
    default_action_kind: str = "patch",
    group_fallback_actions: bool = False,
    include_grouped_fallback_singletons: bool = True,
) -> list[PlannedActionSet]:
    """Return normalized action sets from explicit sets or flat actions."""

    normalized: list[PlannedActionSet] = []
    source = list(items or [])
    if source:
        for item in source:
            if isinstance(item, PlannedActionSet):
                normalized.append(
                    PlannedActionSet(
                        action_kind=item.action_kind or default_action_kind,
                        actions=list(item.actions),
                        label=item.label,
                        metadata=dict(item.metadata),
                    )
                )
            else:
                normalized.append(_singleton_action_set(item, action_kind=default_action_kind))
        return normalized

    fallback = list(fallback_actions or [])
    if group_fallback_actions and len(fallback) > 1:
        normalized.append(
            PlannedActionSet(
                action_kind=default_action_kind,
                actions=list(fallback),
                label="fallback_group",
                metadata={"coerced_from": "candidate_actions"},
            )
        )
        if not include_grouped_fallback_singletons:
            return normalized

    for action in fallback:
        normalized.append(_singleton_action_set(action, action_kind=default_action_kind))
    return normalized


def flatten_patch_actions(action_sets: Iterable[PlannedActionSet]) -> list[Patch]:
    patches: list[Patch] = []
    for action_set in action_sets:
        if action_set.action_kind != "patch":
            continue
        for action in action_set.actions:
            if isinstance(action, Patch):
                patches.append(action)
    return patches


def _singleton_action_set(action: Any, *, action_kind: str) -> PlannedActionSet:
    label = ""
    if isinstance(action, Patch):
        label = action.op.value
    return PlannedActionSet(
        action_kind=action_kind,
        actions=[action],
        label=label,
    )
