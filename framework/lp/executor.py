"""Apply patches directly to loaded Gurobi LP models."""

from __future__ import annotations

import re
from typing import Any

import gurobipy as gp

from framework.core import Patch, PatchOp


def apply_patch_to_gurobi(model: "gp.Model", patch: Patch) -> dict[str, Any]:
    handler = _LP_PATCH_HANDLERS.get(patch.op)
    if handler is None:
        raise ValueError(f"No LP-level handler for patch op {patch.op}")
    return handler(model, patch)


def apply_patches_to_gurobi(model: "gp.Model", patches: list[Patch]) -> list[dict[str, Any]]:
    logs: list[dict[str, Any]] = []
    for patch in patches:
        logs.append(apply_patch_to_gurobi(model, patch))
    model.update()
    return logs


def _handle_fix_variables_by_pattern(model: "gp.Model", patch: Patch) -> dict[str, Any]:
    pattern = re.compile(patch.target["pattern"])
    filters = patch.scope.get("filters", {})
    lb = float(patch.update.get("lb", 0.0))
    ub = float(patch.update.get("ub", 0.0))

    fixed: list[str] = []
    for variable in model.getVars():
        match = pattern.match(variable.VarName)
        if match is None or not _evaluate_filters(match, filters):
            continue
        variable.LB = lb
        variable.UB = ub
        fixed.append(variable.VarName)

    model.update()
    return {
        "op": PatchOp.FIX_VARIABLES_BY_PATTERN.value,
        "pattern": patch.target["pattern"],
        "filters": filters,
        "bounds": {"lb": lb, "ub": ub},
        "num_fixed": len(fixed),
        "sample": fixed[:10],
    }


def _handle_update_constraint_rhs_by_pattern(model: "gp.Model", patch: Patch) -> dict[str, Any]:
    pattern = re.compile(patch.target["pattern"])
    value = patch.update.get("value")
    factor = patch.update.get("factor")

    updated: list[tuple[str, float, float]] = []
    for constraint in model.getConstrs():
        if pattern.match(constraint.ConstrName) is None:
            continue
        old_rhs = constraint.RHS
        if value is not None:
            new_rhs = float(value)
        elif factor is not None:
            new_rhs = old_rhs * float(factor)
        else:
            continue
        constraint.RHS = new_rhs
        updated.append((constraint.ConstrName, old_rhs, new_rhs))

    model.update()
    return {
        "op": PatchOp.UPDATE_CONSTRAINT_RHS_BY_PATTERN.value,
        "pattern": patch.target["pattern"],
        "num_updated": len(updated),
        "changes": updated[:20],
    }


def _handle_update_coefficient(model: "gp.Model", patch: Patch) -> dict[str, Any]:
    variable_pattern = re.compile(patch.target["variable_pattern"])
    constraint_pattern = re.compile(patch.target["constraint_pattern"])
    delta = patch.update.get("delta")
    absolute = patch.update.get("value")

    matched_variables = [variable for variable in model.getVars() if variable_pattern.match(variable.VarName)]
    matched_constraints = [constraint for constraint in model.getConstrs() if constraint_pattern.match(constraint.ConstrName)]

    changes: list[tuple[str, str, float, float]] = []
    for constraint in matched_constraints:
        for variable in matched_variables:
            old_coeff = model.getCoeff(constraint, variable)
            if old_coeff == 0.0:
                continue
            if absolute is not None:
                new_coeff = float(absolute)
            elif delta is not None:
                new_coeff = old_coeff + float(delta)
            else:
                continue
            model.chgCoeff(constraint, variable, new_coeff)
            changes.append((constraint.ConstrName, variable.VarName, old_coeff, new_coeff))

    model.update()
    return {
        "op": PatchOp.UPDATE_COEFFICIENT.value,
        "variable_pattern": patch.target["variable_pattern"],
        "constraint_pattern": patch.target["constraint_pattern"],
        "num_changes": len(changes),
        "changes": changes[:20],
    }


def _evaluate_filters(match: re.Match, filters: dict[str, Any]) -> bool:
    for key, expected in filters.items():
        if key.startswith("range_contains_"):
            parts = key.split("_")
            try:
                group_start = int(parts[2])
                group_end = int(parts[3])
                value = int(expected)
                start = int(match.group(group_start))
                end = int(match.group(group_end))
            except (IndexError, TypeError, ValueError):
                return False
            if not (start <= value <= end):
                return False
            continue

        exact_match = re.match(r"^group_(\d+)$", key)
        if exact_match:
            group_index = int(exact_match.group(1))
            group_value = match.group(group_index)
            if str(group_value) != str(expected):
                try:
                    if int(group_value) != int(expected):
                        return False
                except (TypeError, ValueError):
                    return False
            continue

        membership_match = re.match(r"^group_(\d+)_in$", key)
        if membership_match:
            group_index = int(membership_match.group(1))
            group_value = match.group(group_index)
            allowed = [str(item) for item in expected] if isinstance(expected, list) else [str(expected)]
            if group_value not in allowed:
                return False
            continue

        lte_match = re.match(r"^group_(\d+)_lte$", key)
        if lte_match:
            group_index = int(lte_match.group(1))
            try:
                if int(match.group(group_index)) > int(expected):
                    return False
            except (TypeError, ValueError):
                return False
            continue

        gte_match = re.match(r"^group_(\d+)_gte$", key)
        if gte_match:
            group_index = int(gte_match.group(1))
            try:
                if int(match.group(group_index)) < int(expected):
                    return False
            except (TypeError, ValueError):
                return False
            continue

    return True


_LP_PATCH_HANDLERS = {
    PatchOp.FIX_VARIABLES_BY_PATTERN: _handle_fix_variables_by_pattern,
    PatchOp.UPDATE_CONSTRAINT_RHS_BY_PATTERN: _handle_update_constraint_rhs_by_pattern,
    PatchOp.UPDATE_COEFFICIENT: _handle_update_coefficient,
}
