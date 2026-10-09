"""Structured-model patch runtime for exam block sequencing."""

from __future__ import annotations

from typing import Any, Dict, Tuple

import gurobipy as gp
from gurobipy import GRB

from framework.core import StructuredModel
from framework.core.solver_utils import SolveFailureError
from framework.editing.materialized_linear import apply_materialized_linear_constraint_families
from problems.exam_block_seq.constraint_families import (
    apply_exam_x_aggregate_families,
    is_exam_x_aggregate_family,
)
from problems.exam_block_seq.objective import refresh_exam_lp_objective_from_structured
from problems.exam_block_seq.solution import extract_block_assignments_from_model
from problems.exam_block_seq.solver import apply_solver_params, extract_solve_meta
from problems.exam_block_seq.warm_start import apply_exam_warm_start_payload, resolve_exam_warm_start_payload

_LP_VAR_FAMILY_NAMES = {
    "schedule": "slot_assignment",
}


def solve_exam_model(
    base_model: "gp.Model",
    structured: StructuredModel,
    *,
    base_weights: Dict[str, float],
    default_time_limit: int,
) -> Tuple[float, Dict[int, int], Dict[str, float | int]]:
    grb = base_model.copy()
    solver_params = structured.parameters.get("solver_params")
    time_limit = structured.parameters.get("time_limit", default_time_limit)
    if time_limit:
        grb.Params.TimeLimit = float(time_limit)
    apply_solver_params(grb, solver_params)

    _apply_constraint_rhs_overrides(grb, structured)
    _apply_variable_bound_overrides(grb, structured)
    _apply_exam_semantic_constraint_families(grb, structured)
    _apply_added_constraint_families(grb, structured)
    objective_nonzero_coeffs = refresh_exam_lp_objective_from_structured(
        grb,
        structured,
        default_weights=base_weights,
    )
    warm_start, warm_start_source = resolve_exam_warm_start_payload(structured)
    apply_exam_warm_start_payload(grb, warm_start)
    grb.update()

    grb.optimize()
    solve_meta = extract_solve_meta(grb)
    solve_meta["objective_nonzero_coeffs"] = objective_nonzero_coeffs
    solve_meta["warm_start_applied"] = warm_start_source != "none"
    solve_meta["warm_start_source"] = warm_start_source
    solve_meta["warm_start_mode"] = warm_start_source
    if grb.Status not in (GRB.OPTIMAL, GRB.TIME_LIMIT):
        raise SolveFailureError(
            f"Block sequencing solve failed with status {grb.Status}",
            solve_meta=solve_meta,
        )
    if grb.SolCount == 0:
        raise SolveFailureError(
            f"No incumbent solution available at status {grb.Status}",
            solve_meta=solve_meta,
        )

    solution = extract_block_assignments_from_model(grb, list(structured.parameters.get("blocks", [])))
    return float(grb.ObjVal), solution, solve_meta


def _apply_constraint_rhs_overrides(model: "gp.Model", structured: StructuredModel) -> None:
    for family in structured.constraints.values():
        rhs_spec = family.rhs_spec
        if isinstance(rhs_spec, dict):
            for index, value in rhs_spec.items():
                if not isinstance(value, (int, float)):
                    continue
                constraint = model.getConstrByName(f"{family.name}[{_format_index(index)}]")
                if constraint is not None:
                    constraint.RHS = float(value)
        elif isinstance(rhs_spec, (int, float)):
            if family.index_set:
                for index in family.index_set:
                    constraint = model.getConstrByName(f"{family.name}[{_format_index(index)}]")
                    if constraint is not None:
                        constraint.RHS = float(rhs_spec)
            else:
                constraint = model.getConstrByName(family.name)
                if constraint is not None:
                    constraint.RHS = float(rhs_spec)


def _apply_variable_bound_overrides(model: "gp.Model", structured: StructuredModel) -> None:
    for family in structured.variables.values():
        lp_name = _LP_VAR_FAMILY_NAMES.get(family.name, family.name)
        for index, value in family.lower_bounds.items():
            variable = model.getVarByName(_format_var_name(lp_name, index))
            if variable is not None:
                variable.LB = float(value)
        for index, value in family.upper_bounds.items():
            variable = model.getVarByName(_format_var_name(lp_name, index))
            if variable is not None:
                variable.UB = float(value)


def _apply_added_constraint_families(model: "gp.Model", structured: StructuredModel) -> None:
    blocks = [int(block) for block in structured.parameters.get("blocks", [])]
    apply_materialized_linear_constraint_families(
        model,
        structured,
        resolve_variable=lambda family_name, index: model.getVarByName(
            _format_var_name(_LP_VAR_FAMILY_NAMES.get(family_name, family_name), index)
        ),
        expand_term=lambda term: _expand_exam_term(model, blocks, term),
    )


def _apply_exam_semantic_constraint_families(model: "gp.Model", structured: StructuredModel) -> None:
    blocks = [int(block) for block in structured.parameters.get("blocks", [])]
    families = [family for family in structured.constraints.values() if is_exam_x_aggregate_family(family.lhs_spec)]
    apply_exam_x_aggregate_families(model, blocks=blocks, families=families)


def _expand_exam_term(
    model: "gp.Model",
    blocks: list[int],
    term: dict[str, Any],
) -> list[tuple["gp.Var", float]] | None:
    family_name = str(term.get("variable") or "")
    if family_name != "b":
        return None

    pair = _coerce_index_tuple(term.get("index"), 2)
    if pair is None:
        raise RuntimeError(f"Exam term expansion requires a block-slot index, got {term.get('index')!r}")

    block, slot = pair
    expanded: list[tuple["gp.Var", float]] = []
    for j in blocks:
        for k in blocks:
            variable = model.getVarByName(f"x[{block},{j},{k},{slot}]")
            if variable is not None:
                expanded.append((variable, 1.0))
    return expanded


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


def _format_var_name(family_name: str, index: Any) -> str:
    if index is None or index == "all":
        return family_name
    return f"{family_name}[{_format_index(index)}]"


def _format_index(index: Any) -> str:
    if isinstance(index, tuple):
        return ",".join(str(item) for item in index)
    if isinstance(index, list):
        return ",".join(str(item) for item in index)
    return str(index)
