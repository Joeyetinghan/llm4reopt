"""Warm-start helpers for exam block sequencing."""

from __future__ import annotations

from collections import defaultdict
import os
from typing import Any

import gurobipy as gp

from framework.core import StructuredModel
from problems.exam_block_seq.constraint_families import (
    FRONTLOAD_RULE_KIND,
    RESERVED_VIRTUAL_SLOT_RULE_KIND,
    SLOT_LOAD_CAP_RULE_KIND,
    deterministic_virtual_slot_assignments,
    is_exam_x_aggregate_family,
)
from problems.exam_block_seq.solution import wrap_slot


def apply_exam_warm_start_payload(model: "gp.Model", warm_start: Any) -> None:
    if isinstance(warm_start, dict):
        raw_starts = warm_start.get("mip_starts")
        if isinstance(raw_starts, list):
            starts = [start for start in raw_starts if start is not None]
            for index, start in enumerate(starts):
                _apply_single_warm_start(model, start, append=index > 0)
            return
    _apply_single_warm_start(model, warm_start, append=False)


def resolve_exam_warm_start_payload(
    structured: StructuredModel,
) -> tuple[dict[str, Any] | dict[Any, Any] | None, str]:
    explicit_warm_start = structured.parameters.get("warm_start")
    default_warm_start = None
    if not bool(structured.parameters.get("disable_default_warm_start", False)):
        default_warm_start = build_exam_heuristic_warm_start(structured)

    if explicit_warm_start is not None:
        resolved_explicit = _expand_exam_warm_start(structured, explicit_warm_start)
        if _is_file_based_warm_start(resolved_explicit) and default_warm_start is not None:
            return _combine_warm_starts(resolved_explicit, default_warm_start), "base+heuristic"
        return resolved_explicit, "base" if _is_file_based_warm_start(resolved_explicit) else "provided"

    if default_warm_start is not None:
        return default_warm_start, "heuristic"
    return None, "none"


def build_exam_heuristic_warm_start(structured: StructuredModel) -> dict[str, dict[str, float]] | None:
    blocks = [int(block) for block in structured.parameters.get("blocks", [])]
    if not blocks:
        return None

    schedule = _build_exam_schedule(structured, blocks)
    if schedule is None:
        return None
    return _build_exam_warm_start_from_schedule(structured, schedule)


def _expand_exam_warm_start(
    structured: StructuredModel,
    warm_start: Any,
) -> Any:
    if not isinstance(warm_start, dict) or "var_starts" in warm_start or "sol_path" in warm_start:
        return warm_start

    schedule = _coerce_schedule_from_block_assignments(structured, warm_start)
    if schedule is None:
        return warm_start

    expanded = _build_exam_warm_start_from_schedule(structured, schedule)
    return expanded if expanded is not None else warm_start


def _apply_single_warm_start(model: "gp.Model", warm_start: Any, *, append: bool) -> None:
    if isinstance(warm_start, (str, os.PathLike)):
        model.read(str(warm_start))
        return
    if not isinstance(warm_start, dict):
        return

    sol_path = warm_start.get("sol_path")
    if isinstance(sol_path, (str, os.PathLike)):
        model.read(str(sol_path))
        return

    var_starts = warm_start.get("var_starts")
    if isinstance(var_starts, dict):
        _apply_named_var_starts(model, var_starts, append=append)
        return

    block_slot_starts = {
        (int(block), int(slot)): 1.0
        for block, slot in warm_start.items()
        if isinstance(block, (int, float)) and isinstance(slot, (int, float))
    }
    if not block_slot_starts:
        return
    _apply_block_slot_starts(model, block_slot_starts, append=append)


def _apply_named_var_starts(model: "gp.Model", var_starts: dict[Any, Any], *, append: bool) -> None:
    assignments: list[tuple["gp.Var", float]] = []
    for var_name, value in var_starts.items():
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            continue
        variable = model.getVarByName(str(var_name))
        if variable is not None:
            assignments.append((variable, numeric_value))
    if not assignments:
        return
    _select_start_slot(model, append=append)
    for variable, value in assignments:
        variable.Start = value


def _apply_block_slot_starts(
    model: "gp.Model",
    block_slot_starts: dict[tuple[int, int], float],
    *,
    append: bool,
) -> None:
    assignments: list[tuple["gp.Var", float]] = []
    for (block, slot), value in block_slot_starts.items():
        variable = model.getVarByName(f"b[{block},{slot}]")
        if variable is not None:
            assignments.append((variable, float(value)))
    if not assignments:
        return
    _select_start_slot(model, append=append)
    for variable, value in assignments:
        variable.Start = value


def _is_file_based_warm_start(warm_start: Any) -> bool:
    if isinstance(warm_start, (str, os.PathLike)):
        return True
    if not isinstance(warm_start, dict):
        return False
    return isinstance(warm_start.get("sol_path"), (str, os.PathLike))


def _combine_warm_starts(*payloads: Any) -> dict[str, list[Any]]:
    starts: list[Any] = []
    for payload in payloads:
        if payload is None:
            continue
        if isinstance(payload, dict):
            nested = payload.get("mip_starts")
            if isinstance(nested, list):
                starts.extend(start for start in nested if start is not None)
                continue
        starts.append(payload)
    return {"mip_starts": starts}


def _select_start_slot(model: "gp.Model", *, append: bool) -> None:
    current = max(int(getattr(model, "NumStart", 0) or 0), 0)
    if append:
        model.NumStart = current + 1
        model.Params.StartNumber = current
        return
    model.NumStart = max(current, 1)
    model.Params.StartNumber = 0


def _coerce_schedule_from_block_assignments(
    structured: StructuredModel,
    warm_start: dict[Any, Any],
) -> dict[int, int] | None:
    blocks = [int(block) for block in structured.parameters.get("blocks", [])]
    if not blocks:
        return None

    block_set = set(blocks)
    slot_count = len(blocks)
    schedule: dict[int, int] = {}
    seen_blocks: set[int] = set()

    for raw_block, raw_slot in warm_start.items():
        if not isinstance(raw_block, (int, float)) or not isinstance(raw_slot, (int, float)):
            return None
        block = int(raw_block)
        slot = int(raw_slot)
        if block not in block_set or slot < 1 or slot > slot_count:
            return None
        current_block = schedule.get(slot)
        if current_block is not None and current_block != block:
            return None
        if block in seen_blocks and schedule.get(slot) != block:
            return None
        schedule[slot] = block
        seen_blocks.add(block)

    if len(schedule) != slot_count:
        return None
    return schedule


def _build_exam_warm_start_from_schedule(
    structured: StructuredModel,
    schedule: dict[int, int],
) -> dict[str, dict[str, float]] | None:
    blocks = [int(block) for block in structured.parameters.get("blocks", [])]
    if not blocks:
        return None

    pair_counts = {
        _coerce_index_tuple(key, 2): float(value)
        for key, value in dict(structured.parameters.get("pair_counts") or {}).items()
        if _coerce_index_tuple(key, 2) is not None
    }
    pair_counts.update(
        {
            _coerce_index_tuple(key, 2): float(value)
            for key, value in dict(structured.parameters.get("p") or {}).items()
            if _coerce_index_tuple(key, 2) is not None
        }
    )
    triplet_counts = {
        _coerce_index_tuple(key, 3): float(value)
        for key, value in dict(structured.parameters.get("triplet_counts") or {}).items()
        if _coerce_index_tuple(key, 3) is not None
    }
    triplet_counts.update(
        {
            _coerce_index_tuple(key, 3): float(value)
            for key, value in dict(structured.parameters.get("t") or {}).items()
            if _coerce_index_tuple(key, 3) is not None
        }
    )

    triple_day_start = [int(slot) for slot in structured.parameters.get("triple_day_start", [])]
    triple_24_start = [int(slot) for slot in structured.parameters.get("triple_24_start", [])]
    eve_morn_start = [int(slot) for slot in structured.parameters.get("eve_morn_start", [])]
    other_b2b_start = [int(slot) for slot in structured.parameters.get("other_b2b_start", [])]

    slot_count = len(blocks)
    slot_of = {block: slot for slot, block in schedule.items()}
    triple_by_slot: dict[int, tuple[int, int, int]] = {}
    y_active: set[tuple[int, int, int]] = set()
    z_active: set[tuple[int, int, int, int]] = set()
    var_starts: dict[str, float] = {}

    for slot in blocks:
        i = schedule[wrap_slot(slot, slot_count)]
        j = schedule[wrap_slot(slot + 1, slot_count)]
        k = schedule[wrap_slot(slot + 2, slot_count)]
        triple = (i, j, k)
        triple_by_slot[slot] = triple
        var_starts[f"x[{i},{j},{k},{slot}]"] = 1.0
        var_starts[f"b[{i},{slot}]"] = 1.0
        var_starts[f"slot_assignment[{slot}]"] = float(i)

    for block, slot in slot_of.items():
        var_starts[f"block_assigned[{block}]"] = float(slot)

    triple_slots = set(triple_day_start) | set(triple_24_start)
    for slot, triple in triple_by_slot.items():
        if slot in triple_slots:
            y_active.add(triple)
            i, j, k = triple
            var_starts[f"y[{i},{j},{k}]"] = 1.0

    for slot in blocks:
        i, j, k = triple_by_slot[slot]
        next_triple = triple_by_slot[wrap_slot(slot + 1, slot_count)]
        if next_triple[:2] != (j, k):
            continue
        l = next_triple[2]
        if (i, j, k) in y_active and (j, k, l) in y_active:
            z_active.add((i, j, k, l))
            var_starts[f"z[{i},{j},{k},{l}]"] = 1.0

    c = 16
    for i in blocks:
        for j in blocks:
            diff = abs(slot_of[i] - slot_of[j])
            var_starts[f"block_diff[{i},{j}]"] = float(diff)
            var_starts[f"block_diff_large[{i},{j}]"] = 1.0 if diff >= c else 0.0

    triple_in_day = sum(float(triplet_counts.get(triple_by_slot[slot], 0.0)) for slot in triple_day_start)
    triple_in_24hr = sum(float(triplet_counts.get(triple_by_slot[slot], 0.0)) for slot in triple_24_start)
    three_exams_four_slots = 0.0
    for i, j, k, l in z_active:
        three_exams_four_slots += float(triplet_counts.get((i, j, k), 0.0))
        three_exams_four_slots += float(triplet_counts.get((i, k, l), 0.0))
    b2b_eve_morn = sum(float(pair_counts.get((triple_by_slot[slot][0], triple_by_slot[slot][1]), 0.0)) for slot in eve_morn_start)
    b2b_other = sum(float(pair_counts.get((triple_by_slot[slot][0], triple_by_slot[slot][1]), 0.0)) for slot in other_b2b_start)

    var_starts["triple_in_day"] = triple_in_day
    var_starts["triple_in_24hr"] = triple_in_24hr
    var_starts["three_exams_four_slots"] = three_exams_four_slots
    var_starts["b2b_eveMorn"] = b2b_eve_morn
    var_starts["b2b_other"] = b2b_other
    return {"var_starts": var_starts}


def _build_exam_schedule(structured: StructuredModel, blocks: list[int]) -> dict[int, int] | None:
    slot_count = len(blocks)
    all_slots = list(range(1, slot_count + 1))
    virtual_blocks = {int(block) for block in structured.parameters.get("virtual_blocks", [])}
    real_blocks = [block for block in blocks if block not in virtual_blocks]
    dummy_blocks = sorted(virtual_blocks)
    block_enrollment = {
        int(key): float(value)
        for key, value in dict(structured.parameters.get("block_enrollment") or {}).items()
    }

    forbidden_slots = _collect_forbidden_slots(structured)
    slot_caps = _collect_slot_caps(structured)
    required_assignments = _collect_required_assignments(structured, slot_count)
    capped_slots = {slot for slot_ids, _ in slot_caps for slot in slot_ids}

    schedule: dict[int, int] = {}
    remaining_dummy = list(dummy_blocks)
    remaining_real = list(real_blocks)

    for slot, block in sorted(required_assignments.items()):
        if slot in schedule:
            return None
        schedule[slot] = block
        if block in remaining_dummy:
            remaining_dummy.remove(block)
        elif block in remaining_real:
            remaining_real.remove(block)
        else:
            return None

    constrained_blocks = sorted(
        [block for block in remaining_real if forbidden_slots.get(block)],
        key=lambda item: (_allowed_slot_count(item, all_slots, forbidden_slots), -block_enrollment.get(item, 0.0), item),
    )
    for block in constrained_blocks:
        candidate_slot = next((
            slot
            for slot in all_slots
            if slot not in schedule
            and slot not in capped_slots
            and slot not in forbidden_slots.get(block, set())
        ), None)
        if candidate_slot is None:
            candidate_slot = next(
                (slot for slot in all_slots if slot not in schedule and slot not in forbidden_slots.get(block, set())),
                None,
            )
        if candidate_slot is None:
            return None
        schedule[candidate_slot] = block
        if block in remaining_real:
            remaining_real.remove(block)

    for slot_ids, cap in sorted(slot_caps, key=lambda item: (len(item[0]), item[1])):
        open_slots = [slot for slot in slot_ids if slot not in schedule]
        if not open_slots:
            continue
        candidate_blocks = [
            block
            for block in remaining_real
            if all(slot not in forbidden_slots.get(block, set()) for slot in open_slots)
        ]
        candidate_blocks.sort(key=lambda block: (block_enrollment.get(block, 0.0), block))
        running_cap = 0.0

        while open_slots and remaining_dummy:
            schedule[open_slots.pop(0)] = remaining_dummy.pop(0)

        while open_slots and candidate_blocks:
            block = candidate_blocks.pop(0)
            enrollment = block_enrollment.get(block, 0.0)
            if running_cap + enrollment > cap:
                continue
            slot = next(
                (candidate_slot for candidate_slot in list(open_slots) if candidate_slot not in forbidden_slots.get(block, set())),
                None,
            )
            if slot is None:
                continue
            schedule[slot] = block
            open_slots.remove(slot)
            remaining_real.remove(block)
            running_cap += enrollment

        while open_slots and remaining_dummy:
            schedule[open_slots.pop(0)] = remaining_dummy.pop(0)

    while len(schedule) < slot_count:
        unfilled_slots = [slot for slot in all_slots if slot not in schedule]
        if not unfilled_slots:
            break
        slot = min(unfilled_slots, key=lambda item: _candidate_priority(item, remaining_real, remaining_dummy, forbidden_slots))
        block = _select_block_for_slot(slot, remaining_real, remaining_dummy, forbidden_slots, block_enrollment)
        if block is None:
            return None
        schedule[slot] = block
        if block in remaining_real:
            remaining_real.remove(block)
        elif block in remaining_dummy:
            remaining_dummy.remove(block)

    if set(schedule.keys()) != set(all_slots):
        return None
    if not _schedule_satisfies_semantic_restrictions(
        schedule,
        forbidden_slots=forbidden_slots,
        required_assignments=required_assignments,
        slot_caps=slot_caps,
        block_enrollment=block_enrollment,
    ):
        return None
    return schedule


def _collect_forbidden_slots(structured: StructuredModel) -> dict[int, set[int]]:
    forbidden: dict[int, set[int]] = defaultdict(set)
    all_slots = {int(slot) for slot in structured.parameters.get("blocks", [])}
    for family in structured.constraints.values():
        lhs_spec = family.lhs_spec
        rule_kind = str(family.metadata.get("rule_kind") or "").strip().lower()
        if is_exam_x_aggregate_family(lhs_spec) and rule_kind == FRONTLOAD_RULE_KIND:
            rows = lhs_spec.get("rows") or {}
            for row_spec in rows.values():
                block = row_spec.get("fixed_block")
                if not isinstance(block, (int, float)):
                    continue
                allowed_slots = {int(slot) for slot in row_spec.get("slots", [])}
                forbidden[int(block)].update(all_slots - allowed_slots)
            continue
        if not isinstance(lhs_spec, dict):
            continue
        if str(lhs_spec.get("kind") or "").strip().lower() not in {"materialized_linear", "materialized_linear_family"}:
            continue
        if rule_kind != "assignment_forbid_family":
            continue
        rows = lhs_spec.get("rows") or {}
        for terms in rows.values():
            for term in list(terms or []):
                if str(term.get("variable") or "") != "b":
                    continue
                pair = _coerce_index_tuple(term.get("index"), 2)
                if pair is None:
                    continue
                block, slot = pair
                forbidden[int(block)].add(int(slot))
    return forbidden


def _collect_slot_caps(structured: StructuredModel) -> list[tuple[list[int], float]]:
    slot_caps: list[tuple[list[int], float]] = []
    for family in structured.constraints.values():
        lhs_spec = family.lhs_spec
        rule_kind = str(family.metadata.get("rule_kind") or "").strip().lower()
        if is_exam_x_aggregate_family(lhs_spec) and rule_kind == SLOT_LOAD_CAP_RULE_KIND:
            rows = lhs_spec.get("rows") or {}
            rhs_spec = family.rhs_spec if isinstance(family.rhs_spec, dict) else {}
            for row_index, row_spec in rows.items():
                slot_ids = sorted(int(slot) for slot in row_spec.get("slots", []))
                if not slot_ids:
                    continue
                rhs = rhs_spec.get(row_index, family.rhs_spec)
                slot_caps.append((slot_ids, float(rhs)))
            continue
        if not isinstance(lhs_spec, dict):
            continue
        if str(lhs_spec.get("kind") or "").strip().lower() not in {"materialized_linear", "materialized_linear_family"}:
            continue
        if rule_kind != "slot_load_cap_family":
            continue
        rows = lhs_spec.get("rows") or {}
        rhs_spec = family.rhs_spec if isinstance(family.rhs_spec, dict) else {}
        for row_index, terms in rows.items():
            slot_ids = sorted(
                {
                    int(pair[1])
                    for pair in (_coerce_index_tuple(term.get("index"), 2) for term in list(terms or []))
                    if pair is not None
                }
            )
            if not slot_ids:
                continue
            rhs = rhs_spec.get(row_index, family.rhs_spec)
            slot_caps.append((slot_ids, float(rhs)))
    return slot_caps


def _collect_required_assignments(structured: StructuredModel, slot_count: int) -> dict[int, int]:
    assignments: dict[int, int] = {}
    for family in structured.constraints.values():
        lhs_spec = family.lhs_spec
        if not is_exam_x_aggregate_family(lhs_spec):
            continue
        if str(family.metadata.get("rule_kind") or "").strip().lower() != RESERVED_VIRTUAL_SLOT_RULE_KIND:
            continue
        rows = lhs_spec.get("rows") or {}
        for row_index, row_spec in rows.items():
            slot = int(row_index)
            block = row_spec.get("fixed_block")
            if not isinstance(block, (int, float)):
                continue
            assignments[wrap_slot(slot, slot_count)] = int(block)

    if assignments:
        return assignments

    reserved_slots = {
        int(slot)
        for slot in (structured.parameters.get("reserved_slots") or [])
        if isinstance(slot, (int, float))
    }
    if not reserved_slots:
        return assignments

    virtual_blocks = [int(block) for block in structured.parameters.get("virtual_blocks", [])]
    derived = deterministic_virtual_slot_assignments(virtual_blocks, reserved_slots)
    return {wrap_slot(slot, slot_count): block for slot, block in derived.items()}


def _candidate_priority(
    slot: int,
    remaining_real: list[int],
    remaining_dummy: list[int],
    forbidden_slots: dict[int, set[int]],
) -> tuple[int, int]:
    allowed_real = sum(1 for block in remaining_real if slot not in forbidden_slots.get(block, set()))
    allowed_dummy = len(remaining_dummy)
    return allowed_real + allowed_dummy, slot


def _select_block_for_slot(
    slot: int,
    remaining_real: list[int],
    remaining_dummy: list[int],
    forbidden_slots: dict[int, set[int]],
    block_enrollment: dict[int, float],
) -> int | None:
    for block in sorted(remaining_real, key=lambda item: (block_enrollment.get(item, 0.0), item)):
        if slot not in forbidden_slots.get(block, set()):
            return block
    if remaining_dummy:
        return remaining_dummy[0]
    return None


def _allowed_slot_count(
    block: int,
    all_slots: list[int],
    forbidden_slots: dict[int, set[int]],
) -> int:
    return sum(1 for slot in all_slots if slot not in forbidden_slots.get(block, set()))


def _schedule_satisfies_semantic_restrictions(
    schedule: dict[int, int],
    *,
    forbidden_slots: dict[int, set[int]],
    required_assignments: dict[int, int],
    slot_caps: list[tuple[list[int], float]],
    block_enrollment: dict[int, float],
) -> bool:
    slot_of = {block: slot for slot, block in schedule.items()}
    for block, forbidden in forbidden_slots.items():
        assigned_slot = slot_of.get(block)
        if assigned_slot is not None and assigned_slot in forbidden:
            return False
    for slot, block in required_assignments.items():
        if schedule.get(slot) != block:
            return False
    for slot_ids, cap in slot_caps:
        total = sum(float(block_enrollment.get(schedule.get(slot, -1), 0.0)) for slot in slot_ids)
        if total > float(cap) + 1e-9:
            return False
    return True


def _coerce_index_tuple(value: Any, arity: int) -> tuple[int, ...] | None:
    if isinstance(value, tuple):
        items = list(value)
    elif isinstance(value, list):
        items = list(value)
    else:
        return None
    if len(items) != arity:
        return None
    coerced: list[int] = []
    for item in items:
        if not isinstance(item, (int, float)):
            return None
        coerced.append(int(item))
    return tuple(coerced)
