"""Patch DSL primitives and utilities."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List

from .model import ConstraintFamily, ObjectiveComponent, StructuredModel, VariableFamily


class PatchOp(str, Enum):
    UPDATE_PARAMETER = "UPDATE_PARAMETER"
    UPDATE_BOUND = "UPDATE_BOUND"
    UPDATE_CONSTRAINT_RHS = "UPDATE_CONSTRAINT_RHS"
    UPDATE_CONSTRAINT_LHS = "UPDATE_CONSTRAINT_LHS"
    ADD_CONSTRAINT_FAMILY = "ADD_CONSTRAINT_FAMILY"
    REMOVE_CONSTRAINT_FAMILY = "REMOVE_CONSTRAINT_FAMILY"
    UPDATE_OBJECTIVE_WEIGHT = "UPDATE_OBJECTIVE_WEIGHT"
    UPDATE_OBJECTIVE_COEFF = "UPDATE_OBJECTIVE_COEFF"
    ADD_OBJECTIVE_COMPONENT = "ADD_OBJECTIVE_COMPONENT"
    ADD_VARIABLE_FAMILY = "ADD_VARIABLE_FAMILY"
    FIX_VARIABLES_BY_PATTERN = "FIX_VARIABLES_BY_PATTERN"
    UPDATE_CONSTRAINT_RHS_BY_PATTERN = "UPDATE_CONSTRAINT_RHS_BY_PATTERN"
    UPDATE_COEFFICIENT = "UPDATE_COEFFICIENT"


@dataclass
class Patch:
    op: PatchOp
    target: Dict[str, Any]
    scope: Dict[str, Any] = field(default_factory=dict)
    update: Dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    def describe(self) -> Dict[str, Any]:
        return {
            "op": self.op.value,
            "target": self.target,
            "scope": self.scope,
            "update": self.update,
            "notes": self.notes,
        }


PatchSequence = List[Patch]


def _clone_or_use(model: StructuredModel, in_place: bool) -> StructuredModel:
    return model if in_place else model.copy()


def _normalize_index(index: Any) -> Any:
    """Unwrap single-item lists or convert lists to tuples for hashable indices."""
    if isinstance(index, list):
        if len(index) == 1:
            return index[0]
        return tuple(index)
    return index


def _update_parameter(model: StructuredModel, patch: Patch) -> None:
    param = patch.update.get("name") or patch.target.get("name")
    if param is None:
        raise ValueError("UPDATE_PARAMETER requires parameter name")
    key = _normalize_index(patch.update.get("key"))
    current_value = _current_parameter_value(model, param, key)
    value = _resolve_absolute_or_delta_update(
        current=current_value,
        update=patch.update,
        op_name="UPDATE_PARAMETER",
        absolute_keys=("value",),
    )
    if key is None:
        model.parameters[param] = value
    else:
        container = dict(model.parameters.get(param, {}))
        container[key] = value
        model.parameters[param] = container
    _sync_parameter_side_effects(model, param, key, value)


def _update_bound(model: StructuredModel, patch: Patch) -> None:
    var_name = patch.target.get("variable")
    index = _normalize_index(patch.update.get("index"))
    bound_type = patch.update.get("bound", "lower")
    value = patch.update["value"]
    family = model.variables[var_name]
    if bound_type == "lower":
        family.lower_bounds[index] = value
    elif bound_type == "upper":
        family.upper_bounds[index] = value
    else:
        raise ValueError(f"Unknown bound type {bound_type}")


def _update_constraint_rhs(model: StructuredModel, patch: Patch) -> None:
    cons_name = patch.target.get("constraint")
    index = _normalize_index(patch.update.get("index"))
    cons = model.constraints[cons_name]
    current_value = cons.rhs_spec.get(index) if isinstance(cons.rhs_spec, dict) else cons.rhs_spec
    value = _resolve_absolute_or_delta_update(
        current=current_value,
        update=patch.update,
        op_name="UPDATE_CONSTRAINT_RHS",
        absolute_keys=("value",),
    )
    if isinstance(cons.rhs_spec, dict):
        cons.rhs_spec[index] = value
    else:
        cons.rhs_spec = value


def _update_constraint_lhs(model: StructuredModel, patch: Patch) -> None:
    cons_name = patch.target.get("constraint")
    cons = model.constraints[cons_name]
    cons.lhs_spec = patch.update.get("lhs_spec")


def _add_constraint_family(model: StructuredModel, patch: Patch) -> None:
    cons_obj = patch.update.get("constraint")
    if isinstance(cons_obj, dict):
        cons_obj = ConstraintFamily(
            name=str(cons_obj["name"]),
            index_set=list(cons_obj.get("index_set", [])),
            lhs_spec=cons_obj.get("lhs_spec"),
            rhs_spec=cons_obj.get("rhs_spec"),
            sense=str(cons_obj.get("sense", "<=")),
            desc=str(cons_obj.get("desc", "")),
            tags=set(cons_obj.get("tags", [])),
            aliases=set(cons_obj.get("aliases", [])),
            metadata=dict(cons_obj.get("metadata", {})),
        )
    if not isinstance(cons_obj, ConstraintFamily):
        raise ValueError("ADD_CONSTRAINT_FAMILY expects a ConstraintFamily instance or mapping")
    model.constraints[cons_obj.name] = cons_obj


def _remove_constraint_family(model: StructuredModel, patch: Patch) -> None:
    cons_name = patch.target.get("constraint")
    model.constraints.pop(cons_name, None)


def _update_objective_weight(model: StructuredModel, patch: Patch) -> None:
    obj_name = patch.target.get("objective")
    weight = _resolve_absolute_or_delta_update(
        current=model.objectives[obj_name].weight,
        update=patch.update,
        op_name="UPDATE_OBJECTIVE_WEIGHT",
        absolute_keys=("weight", "value"),
    )
    model.objectives[obj_name].weight = weight
    if obj_name in model.parameters:
        model.parameters[obj_name] = weight


def _update_objective_coeff(model: StructuredModel, patch: Patch) -> None:
    obj_name = patch.target.get("objective")
    index = _normalize_index(patch.update.get("index"))
    if obj_name is None or index is None:
        raise ValueError("UPDATE_OBJECTIVE_COEFF requires objective and index")
    coeffs = model.objectives[obj_name].spec.setdefault("coeffs", {})
    current_value = coeffs.get(index)
    value = _resolve_absolute_or_delta_update(
        current=current_value,
        update=patch.update,
        op_name="UPDATE_OBJECTIVE_COEFF",
        absolute_keys=("value",),
    )
    coeffs[index] = value
    model.objectives[obj_name].spec["coeffs"] = coeffs


def _add_objective_component(model: StructuredModel, patch: Patch) -> None:
    obj = patch.update.get("objective")
    if not isinstance(obj, ObjectiveComponent):
        raise ValueError("ADD_OBJECTIVE_COMPONENT expects an ObjectiveComponent instance")
    model.objectives[obj.name] = obj


def _add_variable_family(model: StructuredModel, patch: Patch) -> None:
    fam = patch.update.get("variable_family")
    if not isinstance(fam, VariableFamily):
        raise ValueError("ADD_VARIABLE_FAMILY expects a VariableFamily instance")
    model.variables[fam.name] = fam


def _noop_lp_only(_model: StructuredModel, _patch: Patch) -> None:
    """Placeholder for LP-level ops that are applied directly to Gurobi models."""


_PATCH_HANDLERS = {
    PatchOp.UPDATE_PARAMETER: _update_parameter,
    PatchOp.UPDATE_BOUND: _update_bound,
    PatchOp.UPDATE_CONSTRAINT_RHS: _update_constraint_rhs,
    PatchOp.UPDATE_CONSTRAINT_LHS: _update_constraint_lhs,
    PatchOp.ADD_CONSTRAINT_FAMILY: _add_constraint_family,
    PatchOp.REMOVE_CONSTRAINT_FAMILY: _remove_constraint_family,
    PatchOp.UPDATE_OBJECTIVE_WEIGHT: _update_objective_weight,
    PatchOp.UPDATE_OBJECTIVE_COEFF: _update_objective_coeff,
    PatchOp.ADD_OBJECTIVE_COMPONENT: _add_objective_component,
    PatchOp.ADD_VARIABLE_FAMILY: _add_variable_family,
    PatchOp.FIX_VARIABLES_BY_PATTERN: _noop_lp_only,
    PatchOp.UPDATE_CONSTRAINT_RHS_BY_PATTERN: _noop_lp_only,
    PatchOp.UPDATE_COEFFICIENT: _noop_lp_only,
}


def apply_patch(model: StructuredModel, patch: Patch, *, in_place: bool = False) -> StructuredModel:
    """Apply a patch to a structured model."""

    working_model = _clone_or_use(model, in_place)

    handler = _PATCH_HANDLERS.get(patch.op)
    if handler:
        handler(working_model, patch)
    else:
        raise ValueError(f"Unsupported patch op {patch.op}")

    return working_model


def apply_patch_sequence(model: StructuredModel, patches: Iterable[Patch]) -> StructuredModel:
    updated = model.copy()
    for patch in patches:
        updated = apply_patch(updated, patch, in_place=True)
    return updated


def _sync_parameter_side_effects(model: StructuredModel, param: str, key: Any, value: Any) -> None:
    if param == "demand" and key is not None:
        demand_cons = model.constraints.get("demand_constraints")
        if demand_cons and isinstance(demand_cons.rhs_spec, dict):
            demand_cons.rhs_spec[key] = value
    elif param == "supply" and key is not None:
        supply_cons = model.constraints.get("supply_constraints")
        if supply_cons and isinstance(supply_cons.rhs_spec, dict):
            supply_cons.rhs_spec[key] = value


def _current_parameter_value(model: StructuredModel, param: str, key: Any) -> Any:
    current = model.parameters.get(param)
    if key is None:
        return current
    if isinstance(current, dict):
        return current.get(key)
    return None


def _resolve_absolute_or_delta_update(
    *,
    current: Any,
    update: dict[str, Any],
    op_name: str,
    absolute_keys: tuple[str, ...],
) -> float | Any:
    for field in absolute_keys:
        if field in update and update[field] is not None:
            return update[field]
    if "delta" not in update or update["delta"] is None:
        key_text = " or ".join(f"update['{field}']" for field in absolute_keys)
        raise ValueError(f"{op_name} requires {key_text} or update['delta']")
    if not isinstance(current, (int, float)):
        raise ValueError(f"{op_name} delta requires an existing numeric target value")
    return float(current) + _coerce_numeric_update_operand(update["delta"], op_name, "delta")


def _coerce_numeric_update_operand(value: Any, op_name: str, field_name: str) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError as exc:
            raise ValueError(f"{op_name} requires numeric update['{field_name}']") from exc
    raise ValueError(f"{op_name} requires numeric update['{field_name}']")
