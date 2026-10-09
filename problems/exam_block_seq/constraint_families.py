"""Shared semantic constraint-family helpers for exam block sequencing."""
from __future__ import annotations

from typing import Any, Iterable

import gurobipy as gp

from framework.core import ConstraintFamily


EXAM_X_AGGREGATE_KIND = "exam_x_aggregate"
FRONTLOAD_RULE_KIND = "frontload_family"
RESERVED_VIRTUAL_SLOT_RULE_KIND = "reserved_virtual_slot_family"
SLOT_LOAD_CAP_RULE_KIND = "slot_load_cap_family"


def canonical_early_slots(blocks: Iterable[int], cutoff_exclusive: int) -> list[int]:
    slot_ids = sorted(int(slot) for slot in blocks)
    return [slot for slot in slot_ids if slot < int(cutoff_exclusive)]


def deterministic_virtual_slot_assignments(
    virtual_blocks: Iterable[int],
    reserved_slots: Iterable[int],
) -> dict[int, int]:
    slots = sorted(int(slot) for slot in reserved_slots)
    dummies = sorted(int(block) for block in virtual_blocks)
    assignments: dict[int, int] = {}
    for slot, block in zip(slots, dummies, strict=False):
        assignments[slot] = block
    if len(assignments) != len(slots):
        raise RuntimeError("Reserved virtual slots require at least one virtual block per reserved slot.")
    return assignments


def empty_reserved_virtual_slot_family() -> ConstraintFamily:
    return ConstraintFamily(
        name="reserved_virtual_slot",
        index_set=[],
        lhs_spec={"kind": EXAM_X_AGGREGATE_KIND, "rows": {}},
        rhs_spec={},
        sense="=",
        desc="Reserve selected slots by fixing specific virtual blocks to those slots.",
        tags={"block_seq", "availability"},
        metadata={"rule_kind": RESERVED_VIRTUAL_SLOT_RULE_KIND},
    )


def empty_slot_load_cap_family() -> ConstraintFamily:
    return ConstraintFamily(
        name="slot_load_cap",
        index_set=[],
        lhs_spec={"kind": EXAM_X_AGGREGATE_KIND, "rows": {}},
        rhs_spec={},
        sense="<=",
        desc="Weighted enrollment cap across a selected slot set.",
        tags={"block_seq", "load_cap"},
        metadata={"rule_kind": SLOT_LOAD_CAP_RULE_KIND},
    )


def build_frontload_constraint_family(
    *,
    large_blocks: Iterable[int],
    early_slots: Iterable[int],
) -> ConstraintFamily:
    rows = {
        int(block): {
            "fixed_block": int(block),
            "slots": sorted(int(slot) for slot in early_slots),
        }
        for block in sorted(int(block) for block in large_blocks)
    }
    return ConstraintFamily(
        name="frontload",
        index_set=list(rows.keys()),
        lhs_spec={"kind": EXAM_X_AGGREGATE_KIND, "rows": rows},
        rhs_spec={row_index: 1.0 for row_index in rows},
        sense="=",
        desc="Large blocks must be assigned to the designated early slots.",
        tags={"block_seq", "frontload"},
        metadata={
            "rule_kind": FRONTLOAD_RULE_KIND,
            "lp_edit_mode": "rewrite_existing",
        },
    )


def build_reserved_virtual_slot_constraint_family(
    *,
    virtual_blocks: Iterable[int],
    reserved_slots: Iterable[int],
) -> ConstraintFamily:
    assignments = deterministic_virtual_slot_assignments(virtual_blocks, reserved_slots)
    rows = {
        int(slot): {
            "fixed_block": int(block),
            "slots": [int(slot)],
        }
        for slot, block in assignments.items()
    }
    return ConstraintFamily(
        name="reserved_virtual_slot",
        index_set=list(rows.keys()),
        lhs_spec={"kind": EXAM_X_AGGREGATE_KIND, "rows": rows},
        rhs_spec={row_index: 1.0 for row_index in rows},
        sense="=",
        desc="Reserve selected slots by assigning them to deterministic virtual blocks.",
        tags={"block_seq", "availability"},
        metadata={
            "rule_kind": RESERVED_VIRTUAL_SLOT_RULE_KIND,
        },
    )


def build_slot_load_cap_constraint_family(
    *,
    row_name: str,
    slots: Iterable[int],
    real_blocks: Iterable[int],
    block_enrollment: dict[int, float],
    cap: float,
) -> ConstraintFamily:
    weights = {
        int(block): float(block_enrollment.get(int(block), 0.0))
        for block in sorted(int(block) for block in real_blocks)
        if abs(float(block_enrollment.get(int(block), 0.0))) > 1e-12
    }
    row_id = str(row_name)
    return ConstraintFamily(
        name="slot_load_cap",
        index_set=[row_id],
        lhs_spec={
            "kind": EXAM_X_AGGREGATE_KIND,
            "rows": {
                row_id: {
                    "slots": sorted(int(slot) for slot in slots),
                    "block_weights": weights,
                }
            },
        },
        rhs_spec={row_id: float(cap)},
        sense="<=",
        desc="Cap weighted enrollment across a selected slot set.",
        tags={"block_seq", "load_cap"},
        metadata={"rule_kind": SLOT_LOAD_CAP_RULE_KIND},
    )


def is_exam_x_aggregate_family(lhs_spec: Any) -> bool:
    return isinstance(lhs_spec, dict) and str(lhs_spec.get("kind") or "").strip().lower() == EXAM_X_AGGREGATE_KIND


def apply_exam_x_aggregate_families(
    model: gp.Model,
    *,
    blocks: Iterable[int],
    families: Iterable[ConstraintFamily],
) -> list[str]:
    applied: list[str] = []
    block_ids = [int(block) for block in blocks]
    for family in families:
        lhs_spec = family.lhs_spec
        if not is_exam_x_aggregate_family(lhs_spec):
            continue
        rows = lhs_spec.get("rows") or {}
        if not isinstance(rows, dict):
            raise RuntimeError(f"Exam semantic family {family.name} must provide dict rows.")
        row_indices = list(family.index_set or rows.keys())

        existing_to_remove: list[gp.Constr] = []
        for row_index in row_indices:
            try:
                existing = model.getConstrByName(_constraint_name(family.name, row_index))
            except gp.GurobiError:
                existing = None
            if existing is not None:
                existing_to_remove.append(existing)
        if existing_to_remove:
            model.remove(existing_to_remove)
            model.update()

        for row_index in row_indices:
            row_spec = rows.get(row_index) or {}
            expr = _build_x_aggregate_expr(model, block_ids, row_spec)
            rhs = _rhs_for_row(family, row_index)
            name = _constraint_name(family.name, row_index)
            if family.sense == "<=":
                model.addConstr(expr <= rhs, name=name)
            elif family.sense == ">=":
                model.addConstr(expr >= rhs, name=name)
            elif family.sense == "=":
                model.addConstr(expr == rhs, name=name)
            else:
                raise RuntimeError(f"Unsupported sense for exam semantic family: {family.sense!r}")
            applied.append(name)

    if applied:
        model.update()
    return applied


def _build_x_aggregate_expr(
    model: gp.Model,
    blocks: list[int],
    row_spec: dict[str, Any],
) -> gp.LinExpr:
    expr = gp.LinExpr()
    slots = [int(slot) for slot in row_spec.get("slots", [])]
    if "fixed_block" in row_spec:
        block_weights = {int(row_spec["fixed_block"]): float(row_spec.get("coeff", 1.0))}
    else:
        raw_weights = dict(row_spec.get("block_weights") or {})
        block_weights = {
            int(block): float(weight)
            for block, weight in raw_weights.items()
            if abs(float(weight)) > 1e-12
        }

    for block, coeff in block_weights.items():
        for slot in slots:
            for j in blocks:
                for k in blocks:
                    variable = model.getVarByName(f"x[{block},{j},{k},{slot}]")
                    if variable is not None:
                        expr += float(coeff) * variable
    return expr


def _rhs_for_row(family: ConstraintFamily, row_index: Any) -> float:
    rhs_spec = family.rhs_spec
    if isinstance(rhs_spec, dict):
        if row_index not in rhs_spec:
            raise RuntimeError(f"Constraint family {family.name} is missing rhs for row {row_index!r}.")
        return float(rhs_spec[row_index])
    return float(rhs_spec)


def _constraint_name(family_name: str, row_index: Any) -> str:
    if row_index in {None, ""}:
        return family_name
    if isinstance(row_index, tuple):
        payload = ",".join(str(item) for item in row_index)
    elif isinstance(row_index, list):
        payload = ",".join(str(item) for item in row_index)
    else:
        payload = str(row_index)
    return f"{family_name}[{payload}]"
