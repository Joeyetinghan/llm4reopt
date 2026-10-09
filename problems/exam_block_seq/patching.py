"""Exam block sequencing patch normalization helpers."""
from __future__ import annotations

import re
from typing import Iterable

from framework.core import ConstraintFamily, Patch, PatchOp, PlannedActionSet, StructuredEvent, StructuredModel, apply_patch
from problems.exam_block_seq.constraint_families import (
    build_frontload_constraint_family,
    build_reserved_virtual_slot_constraint_family,
    build_slot_load_cap_constraint_family,
    canonical_early_slots,
)


def normalize_exam_patches(
    patches: Iterable[Patch],
    model: StructuredModel,
    event: StructuredEvent,
) -> list[Patch]:
    blocks = set(model.parameters.get("blocks", []))
    normalized: list[Patch] = []
    for patch in patches:
        normalized.extend(_normalize_patch(patch, blocks, model, event))
    return normalized


def derive_exam_semantic_patches(
    patch: Patch,
    model: StructuredModel,
) -> list[Patch]:
    if patch.op != PatchOp.UPDATE_PARAMETER:
        return []

    name = str(patch.update.get("name") or patch.target.get("name") or "").strip()
    if name == "frontload_slot_cutoff":
        value = patch.update.get("value")
        if not isinstance(value, (int, float)):
            return []
        early_slots = canonical_early_slots(model.parameters.get("blocks", []), int(value) + 1)
        return [
            Patch(
                op=PatchOp.UPDATE_PARAMETER,
                target={"name": "early_slots"},
                update={"name": "early_slots", "value": early_slots},
            )
        ]

    if name == "early_slots":
        family = build_frontload_constraint_family(
            large_blocks=model.parameters.get("large_blocks", []),
            early_slots=model.parameters.get("early_slots", []),
        )
        return [
            Patch(
                op=PatchOp.UPDATE_CONSTRAINT_LHS,
                target={"constraint": "frontload"},
                update={"lhs_spec": family.lhs_spec},
            )
        ]

    if name == "reserved_slots":
        reserved_slots = [int(slot) for slot in (patch.update.get("value") or [])]
        if not reserved_slots:
            return []
        family = build_reserved_virtual_slot_constraint_family(
            virtual_blocks=model.parameters.get("virtual_blocks", []),
            reserved_slots=reserved_slots,
        )
        return [
            Patch(
                op=PatchOp.ADD_CONSTRAINT_FAMILY,
                target={"constraint": family.name},
                update={"constraint": family},
            )
        ]

    return []


def resolve_exam_composed_action_sets(
    event: StructuredEvent,
    model: StructuredModel,
) -> list[PlannedActionSet]:
    prompt_ids = _resolve_composed_prompt_ids(event)
    if not prompt_ids:
        return []

    action_set = _build_composed_action_set(prompt_ids, model, label=_composed_action_set_label(event, prompt_ids))
    return [] if action_set is None else [action_set]


def _normalize_patch(
    patch: Patch,
    blocks: set[int],
    model: StructuredModel,
    event: StructuredEvent,
) -> list[Patch]:
    if patch.op == PatchOp.UPDATE_PARAMETER:
        update = dict(patch.update)
        name = update.get("name") or patch.target.get("name")
        if name:
            name = _canonicalize_param_name(str(name))
            update["name"] = name
            if name == "reserved_slots":
                parsed = _parse_reserved_slots(
                    update.get("value") or update.get("key") or patch.scope.get("entity"),
                    model,
                    event,
                )
                if parsed is not None:
                    update["value"] = parsed
                update.pop("key", None)
            elif name == "frontload_slot_cutoff":
                if update.get("value") is not None:
                    update["value"] = int(update["value"])
                if update.get("delta") is not None:
                    update["delta"] = int(update["delta"])
                update.pop("key", None)
            elif name == "early_slots":
                parsed_slots = _parse_slot_list(update.get("value"), model)
                if parsed_slots is not None:
                    update["value"] = parsed_slots
                update.pop("key", None)
            elif name in {"p", "pair_counts"}:
                key = update.get("key") or patch.scope.get("entity")
                update["key"] = _coerce_index(key, 2, blocks)
                update["name"] = "p"
                _materialize_delta_override(
                    update,
                    base_values=dict(model.parameters.get("pair_counts") or {}),
                    override_values=dict(model.parameters.get("p") or {}),
                )
            elif name in {"t", "triplet_counts"}:
                key = update.get("key") or patch.scope.get("entity")
                update["key"] = _coerce_index(key, 3, blocks)
                update["name"] = "t"
                _materialize_delta_override(
                    update,
                    base_values=dict(model.parameters.get("triplet_counts") or {}),
                    override_values=dict(model.parameters.get("t") or {}),
                )
        return [Patch(op=patch.op, target=patch.target, scope=patch.scope, update=update)]

    if patch.op == PatchOp.UPDATE_BOUND:
        update = dict(patch.update)
        update["index"] = _coerce_index(update.get("index"), 4, blocks)
        return [Patch(op=patch.op, target=patch.target, scope=patch.scope, update=update)]

    if patch.op == PatchOp.UPDATE_CONSTRAINT_LHS:
        normalized = _normalize_semantic_constraint_patch(patch, model)
        if normalized is not None:
            return normalized
        return [patch]

    if patch.op == PatchOp.ADD_CONSTRAINT_FAMILY:
        normalized = _normalize_semantic_constraint_patch(patch, model)
        if normalized is not None:
            return normalized
        return [patch]

    if patch.op == PatchOp.UPDATE_OBJECTIVE_COEFF:
        update = dict(patch.update)
        update["index"] = _coerce_index(update.get("index"), 3, blocks)
        return [Patch(op=patch.op, target=patch.target, scope=patch.scope, update=update)]

    if patch.op == PatchOp.UPDATE_CONSTRAINT_RHS:
        update = dict(patch.update)
        update["index"] = _coerce_index(update.get("index"), 1, blocks)
        return [Patch(op=patch.op, target=patch.target, scope=patch.scope, update=update)]

    return [patch]


def _resolve_composed_prompt_ids(event: StructuredEvent) -> list[str]:
    delta_metadata = dict(event.annotations.get("delta_metadata") or {})
    raw_prompt_ids = delta_metadata.get("composed_prompt_ids")
    if isinstance(raw_prompt_ids, (list, tuple)):
        prompt_ids = [str(item).strip().upper() for item in raw_prompt_ids if str(item).strip()]
        if prompt_ids:
            return prompt_ids

    prompt_id = str(delta_metadata.get("prompt_id") or "").strip().upper()
    if prompt_id == "P6":
        return ["P4", "P2", "P1"]

    raw_text = str(event.raw_text or "").strip()
    if not raw_text:
        return []
    prompt_ids = re.findall(r"\bP\d+\b", raw_text.upper())
    if len(prompt_ids) >= 2 and "EXACT ORDER" in raw_text.upper():
        return prompt_ids
    return []


def _build_composed_action_set(
    prompt_ids: list[str],
    model: StructuredModel,
    *,
    label: str,
) -> PlannedActionSet | None:
    actions: list[Patch] = []
    working_model = model.copy()
    for prompt_id in prompt_ids:
        patches = _prompt_action_patches(prompt_id, working_model)
        if not patches:
            return None
        for patch in patches:
            actions.append(patch)
            working_model = apply_patch(working_model, patch, in_place=True)
            for derived in derive_exam_semantic_patches(patch, working_model):
                working_model = apply_patch(working_model, derived, in_place=True)
    return PlannedActionSet(
        action_kind="patch",
        actions=actions,
        label=label,
        metadata={
            "composed_prompt_ids": list(prompt_ids),
            "deterministic_composition": True,
        },
    )


def _prompt_action_patches(prompt_id: str, model: StructuredModel) -> list[Patch]:
    prompt_key = str(prompt_id).strip().upper()
    if prompt_key == "P1":
        reserved_slot = _penultimate_evening_slot(model)
        return [
            Patch(
                op=PatchOp.UPDATE_PARAMETER,
                target={"name": "reserved_slots"},
                update={"name": "reserved_slots", "value": [reserved_slot]},
            )
        ]

    if prompt_key == "P2":
        pair_49 = _current_count_override(model, "p", "pair_counts", (4, 9)) + 120.0
        pair_94 = _current_count_override(model, "p", "pair_counts", (9, 4)) + 120.0
        return [
            Patch(
                op=PatchOp.UPDATE_PARAMETER,
                target={"name": "pair_counts"},
                scope={"entity": [4, 9]},
                update={"name": "pair_counts", "key": [4, 9], "value": pair_49},
            ),
            Patch(
                op=PatchOp.UPDATE_PARAMETER,
                target={"name": "pair_counts"},
                scope={"entity": [9, 4]},
                update={"name": "pair_counts", "key": [9, 4], "value": pair_94},
            ),
        ]

    if prompt_key == "P4":
        gamma2_weight = _current_objective_weight(model, "gamma2")
        gamma2_base = _base_objective_weight(model, "gamma2", default=gamma2_weight)
        return [
            Patch(
                op=PatchOp.UPDATE_OBJECTIVE_WEIGHT,
                target={"objective": "gamma1"},
                update={"weight": gamma2_weight},
            ),
            Patch(
                op=PatchOp.UPDATE_OBJECTIVE_WEIGHT,
                target={"objective": "beta"},
                update={"weight": 20.0 * gamma2_base},
            ),
        ]

    return []


def _composed_action_set_label(event: StructuredEvent, prompt_ids: list[str]) -> str:
    delta_metadata = dict(event.annotations.get("delta_metadata") or {})
    prompt_id = str(delta_metadata.get("prompt_id") or "").strip()
    if prompt_id:
        return prompt_id
    return "compose:" + "->".join(prompt_ids)


def _current_objective_weight(model: StructuredModel, name: str) -> float:
    objective = model.objectives.get(name)
    if objective is not None:
        return float(objective.weight)
    return float(model.parameters.get(name, 0.0) or 0.0)


def _base_objective_weight(model: StructuredModel, name: str, *, default: float) -> float:
    base_parameters = dict(model.extras.get("base_parameters") or {})
    value = base_parameters.get(name)
    if isinstance(value, (int, float)):
        return float(value)
    return float(default)


def _current_count_override(
    model: StructuredModel,
    override_name: str,
    base_name: str,
    key: tuple[int, ...],
) -> float:
    override_values = dict(model.parameters.get(override_name) or {})
    if key in override_values and isinstance(override_values[key], (int, float)):
        return float(override_values[key])
    base_values = dict(model.parameters.get(base_name) or {})
    if key in base_values and isinstance(base_values[key], (int, float)):
        return float(base_values[key])
    return 0.0


def _penultimate_evening_slot(model: StructuredModel) -> int:
    slots = sorted(int(slot) for slot in model.parameters.get("blocks", []))
    slots_per_day = int(model.parameters.get("slots_per_day", 0) or 0)
    if not slots or slots_per_day <= 0:
        raise RuntimeError("Exam composed prompt is missing slot metadata.")
    evening_index = _evening_slot_index(
        [str(label).strip().lower() for label in (model.parameters.get("slot_times") or [])],
        slots_per_day,
    )
    evening_slots = [slot for slot in slots if (slot - 1) % slots_per_day == evening_index]
    if len(evening_slots) >= 2:
        return evening_slots[-2]
    if evening_slots:
        return evening_slots[-1]
    raise RuntimeError("Exam composed prompt could not resolve an evening slot.")


def _normalize_semantic_constraint_patch(
    patch: Patch,
    model: StructuredModel,
) -> list[Patch] | None:
    constraint_payload = patch.update.get("constraint")
    lhs_spec: object | None = None
    index_set: list[object] = []
    rhs_spec: object | None = None
    constraint_name = patch.target.get("constraint")

    if patch.op == PatchOp.UPDATE_CONSTRAINT_LHS:
        lhs_spec = patch.update.get("lhs_spec")
    elif isinstance(constraint_payload, ConstraintFamily):
        constraint_name = constraint_payload.name
        lhs_spec = constraint_payload.lhs_spec
        index_set = list(constraint_payload.index_set or [])
        rhs_spec = constraint_payload.rhs_spec
    elif isinstance(constraint_payload, dict):
        constraint_name = str(constraint_payload.get("name") or constraint_name or "").strip() or None
        lhs_spec = constraint_payload.get("lhs_spec")
        index_set = list(constraint_payload.get("index_set") or [])
        rhs_spec = constraint_payload.get("rhs_spec")

    if not isinstance(lhs_spec, dict):
        return None

    kind = str(lhs_spec.get("kind") or "").strip().lower()
    if kind == "frontload":
        slots = _parse_slot_list(
            lhs_spec.get("slots")
            or lhs_spec.get("slot_ids")
            or lhs_spec.get("early_slots"),
            model,
        ) or []
        return [
            Patch(
                op=PatchOp.UPDATE_PARAMETER,
                target={"name": "early_slots"},
                update={"name": "early_slots", "value": slots},
            )
        ]

    if kind == "reserved_virtual_slot":
        slot_value = lhs_spec.get("slot") or lhs_spec.get("slot_id")
        if slot_value is None and index_set:
            slot_value = index_set[0]
        slots = _parse_slot_list([slot_value], model) or []
        if not slots:
            return []
        family = build_reserved_virtual_slot_constraint_family(
            virtual_blocks=model.parameters.get("virtual_blocks", []),
            reserved_slots=slots,
        )
        return [
            Patch(
                op=PatchOp.ADD_CONSTRAINT_FAMILY,
                target={"constraint": family.name},
                update={"constraint": family},
            )
        ]

    if kind == "slot_load_cap":
        row_index = index_set[0] if index_set else "day_2"
        slots = _slot_load_cap_slots(lhs_spec, row_index=row_index, model=model)
        if not slots:
            return []
        cap = rhs_spec
        if isinstance(rhs_spec, dict):
            cap = rhs_spec.get(row_index)
            if cap is None and rhs_spec:
                cap = next(iter(rhs_spec.values()))
        if cap is None:
            return []
        family = build_slot_load_cap_constraint_family(
            row_name=_canonical_slot_load_cap_row_name(row_index, slots, model),
            slots=slots,
            real_blocks=[
                int(block)
                for block in model.parameters.get("blocks", [])
                if int(block) not in {int(v) for v in model.parameters.get("virtual_blocks", [])}
            ],
            block_enrollment=dict(model.parameters.get("block_enrollment") or {}),
            cap=float(cap),
        )
        return [
            Patch(
                op=PatchOp.ADD_CONSTRAINT_FAMILY,
                target={"constraint": family.name},
                update={"constraint": family},
            )
        ]

    return None


def _slot_load_cap_slots(
    lhs_spec: dict[str, object],
    *,
    row_index: object,
    model: StructuredModel,
) -> list[int]:
    slots = _parse_slot_list(
        lhs_spec.get("slots")
        or lhs_spec.get("slot_set")
        or lhs_spec.get("slot_ids")
        or _slots_for_day(lhs_spec.get("day"), model),
        model,
    )
    if slots:
        return slots

    rows = lhs_spec.get("rows")
    if not isinstance(rows, dict):
        return []
    row_spec = rows.get(row_index)
    if row_spec is None and rows:
        row_spec = next(iter(rows.values()))
    if isinstance(row_spec, dict):
        return _parse_slot_list(
            row_spec.get("slots")
            or row_spec.get("slot_set")
            or row_spec.get("slot_ids")
            or _slots_for_day(row_spec.get("day"), model),
            model,
        ) or []
    return _parse_slot_list(row_spec, model) or []


def _slots_for_day(day_value: object, model: StructuredModel) -> list[int] | None:
    if day_value is None:
        return None
    try:
        day_index = int(day_value)
    except (TypeError, ValueError):
        return None
    slots_per_day = int(model.parameters.get("slots_per_day", 0) or 0)
    blocks = [int(slot) for slot in (model.parameters.get("blocks") or [])]
    if day_index <= 0 or slots_per_day <= 0 or not blocks:
        return None
    max_slot = max(blocks)
    start = (day_index - 1) * slots_per_day + 1
    end = min(max_slot, start + slots_per_day - 1)
    if start > end:
        return None
    return list(range(start, end + 1))


def _canonical_slot_load_cap_row_name(
    row_index: object,
    slots: list[int],
    model: StructuredModel,
) -> str:
    slots_per_day = int(model.parameters.get("slots_per_day", 0) or 0)
    if slots_per_day > 0 and slots:
        sorted_slots = sorted(int(slot) for slot in slots)
        day_index = ((sorted_slots[0] - 1) // slots_per_day) + 1
        expected = _slots_for_day(day_index, model) or []
        if expected and sorted_slots == expected:
            return f"day_{day_index}"
    return str(row_index)


def _canonicalize_param_name(name: str) -> str:
    lowered = name.strip().lower()
    aliases = {
        "pair_counts": "p",
        "pairs": "p",
        "triplet_counts": "t",
        "triplets": "t",
        "reserved_slot": "reserved_slots",
        "reserved slots": "reserved_slots",
        "b2b_eve_morn_weight": "gamma1",
        "b2b_other_weight": "gamma2",
        "triple_day_weight": "alpha",
        "triple_24_weight": "beta",
        "three_in_four_weight": "delta",
    }
    return aliases.get(lowered, name)


def _parse_slot_list(value: object, model: StructuredModel) -> list[int] | None:
    blocks = sorted(int(slot) for slot in model.parameters.get("blocks", []))
    if not blocks or value is None:
        return None
    max_slot = blocks[-1]
    if isinstance(value, (list, tuple, set)):
        parsed: list[int] = []
        for item in value:
            if isinstance(item, (int, float)):
                parsed.append(int(item))
            elif isinstance(item, str) and item.strip().isdigit():
                parsed.append(int(item.strip()))
        return sorted({slot for slot in parsed if 1 <= slot <= max_slot}) or None
    if isinstance(value, str):
        text = value.strip().lower().replace("through", "..").replace("to", "..").replace("-", "..")
        if ".." in text:
            start_text, end_text = [part.strip() for part in text.split("..", 1)]
            if start_text.isdigit() and end_text.isdigit():
                start = int(start_text)
                end = int(end_text)
                if start <= end:
                    return [slot for slot in range(start, end + 1) if 1 <= slot <= max_slot]
        if text.isdigit():
            slot = int(text)
            if 1 <= slot <= max_slot:
                return [slot]
    return None


def _coerce_index(value: object, arity: int, blocks: set[int]) -> object:
    if value is None:
        return value
    if isinstance(value, (list, tuple)):
        items = list(value)
    elif isinstance(value, dict):
        items = list(value.values())
    elif isinstance(value, str) and "," in value:
        items = [part.strip() for part in value.split(",")]
    else:
        items = [value]

    if arity == 1:
        return _coerce_block(items[0], blocks)
    if len(items) < arity:
        return value
    return tuple(_coerce_block(item, blocks) for item in items[:arity])


def _coerce_block(value: object, blocks: set[int]) -> int | object:
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip().lower().replace("block", "").replace("slot", "").strip()
        if stripped.isdigit():
            candidate = int(stripped)
            if not blocks or candidate in blocks:
                return candidate
    return value


def _parse_reserved_slots(
    value: object,
    model: StructuredModel,
    event: StructuredEvent,
) -> list[int] | None:
    blocks = model.parameters.get("blocks") or []
    slots_per_day = int(model.parameters.get("slots_per_day", 0) or 0)
    slot_times = [str(t).strip().lower() for t in (model.parameters.get("slot_times") or [])]
    max_slot = max(blocks) if blocks else 0

    direct_slots = _parse_slot_list(value, model)
    if direct_slots:
        return direct_slots

    explicit_tokens: list[str] = []
    if isinstance(value, (list, tuple, set)):
        explicit_tokens.extend(str(v) for v in value)
    elif value is not None:
        explicit_tokens.append(str(value))

    explicit_candidates = _extract_reserved_slot_candidates(
        explicit_tokens,
        max_slot=max_slot,
        slots_per_day=slots_per_day,
        slot_times=slot_times,
    )
    if explicit_candidates:
        return sorted(explicit_candidates)

    if event.raw_text:
        event_candidates = _extract_reserved_slot_candidates(
            [event.raw_text],
            max_slot=max_slot,
            slots_per_day=slots_per_day,
            slot_times=slot_times,
        )
        if event_candidates:
            return sorted(event_candidates)
    return None


def _extract_reserved_slot_candidates(
    tokens: Iterable[str],
    *,
    max_slot: int,
    slots_per_day: int,
    slot_times: list[str],
) -> set[int]:
    candidates: set[int] = set()
    for token in tokens:
        lowered = token.lower()
        relative_slots = _match_relative_slot_request(lowered, max_slot, slots_per_day, slot_times)
        if relative_slots:
            candidates.update(relative_slots)
            continue
        if _looks_like_slot_expression(lowered):
            continue
        day_idx = _first_int_after_keyword(lowered, {"day"})
        time_idx = _match_time_index(lowered, slot_times)

        for num in _find_ints_after_keyword(lowered, {"slot", "timeslot", "time slot"}):
            if 1 <= num <= max_slot:
                candidates.add(num)

        if day_idx and slots_per_day:
            if time_idx is not None:
                slot_num = (day_idx - 1) * slots_per_day + time_idx + 1
                if 1 <= slot_num <= max_slot:
                    candidates.add(slot_num)
                continue
            day_start = (day_idx - 1) * slots_per_day + 1
            day_end = day_start + slots_per_day - 1
            for slot_num in range(day_start, day_end + 1):
                if 1 <= slot_num <= max_slot:
                    candidates.add(slot_num)
            continue

        if time_idx is not None and slots_per_day:
            for slot_num in range(1, max_slot + 1):
                if (slot_num - 1) % slots_per_day == time_idx:
                    candidates.add(slot_num)
    return candidates


def _materialize_delta_override(
    update: dict[str, object],
    *,
    base_values: dict[object, object],
    override_values: dict[object, object],
) -> None:
    if "delta" not in update or update["delta"] is None:
        return
    key = update.get("key")
    if key is None:
        return
    current = override_values.get(key, base_values.get(key))
    if not isinstance(current, (int, float)):
        current = 0.0
    update["value"] = float(current) + float(update["delta"])
    update.pop("delta", None)


def _match_relative_slot_request(
    text: str,
    max_slot: int,
    slots_per_day: int,
    slot_times: list[str],
) -> set[int]:
    if max_slot <= 0 or slots_per_day <= 0:
        return set()

    evening_index = _evening_slot_index(slot_times, slots_per_day)
    evening_slots = [
        slot_num
        for slot_num in range(1, max_slot + 1)
        if (slot_num - 1) % slots_per_day == evening_index
    ]
    if not evening_slots:
        return set()

    normalized = " ".join(text.split())
    asks_for_penultimate_evening = (
        "penultimate evening" in normalized
        or "penultimate night" in normalized
        or "before the final evening slot" in normalized
        or "before the last evening slot" in normalized
        or "before the final night slot" in normalized
        or "before the last night slot" in normalized
        or "one day before the final evening slot" in normalized
        or "one day before the last evening slot" in normalized
        or "one day before the final night slot" in normalized
        or "one day before the last night slot" in normalized
    )
    if asks_for_penultimate_evening:
        if len(evening_slots) >= 2:
            return {evening_slots[-2]}
        return {evening_slots[-1]}
    return set()


def _find_ints_after_keyword(text: str, keywords: set[str]) -> list[int]:
    results: list[int] = []
    for keyword in keywords:
        idx = text.find(keyword)
        if idx == -1:
            continue
        suffix = text[idx + len(keyword) :]
        for part in suffix.replace("#", " ").replace(":", " ").split():
            if part.isdigit():
                results.append(int(part))
                break
    return results


def _first_int_after_keyword(text: str, keywords: set[str]) -> int | None:
    nums = _find_ints_after_keyword(text, keywords)
    return nums[0] if nums else None


def _match_time_index(text: str, slot_times: list[str]) -> int | None:
    if not slot_times:
        slot_times = ["9am", "2pm", "7pm"]
    for idx, label in enumerate(slot_times):
        if label and label in text:
            return idx
    if "morning" in text:
        return 0
    if "afternoon" in text and len(slot_times) > 1:
        return 1
    if "evening" in text or "night" in text:
        return min(2, len(slot_times) - 1)
    return None


def _looks_like_slot_expression(text: str) -> bool:
    return "index(" in text or any(symbol in text for symbol in {" + ", " - ", " * ", " / ", "(", ")"})


def _evening_slot_index(slot_times: list[str], slots_per_day: int) -> int:
    if slot_times:
        for idx in range(len(slot_times) - 1, -1, -1):
            label = slot_times[idx]
            if "eve" in label or "night" in label or label.endswith("pm"):
                return idx
        return len(slot_times) - 1
    return max(0, slots_per_day - 1)
