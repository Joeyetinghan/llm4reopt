"""Structured-model patch runtime for transport."""

from __future__ import annotations

from typing import Dict, Tuple

from framework.core import StructuredModel
from problems.transport.solver import _extract_solve_meta, build_transport_gurobi_model


def solve_transport_model(model: StructuredModel) -> Tuple[float, Dict[Tuple[str, str], float], Dict[str, float | int]]:
    plants = list(model.parameters.get("plants", []))
    customers = list(model.parameters.get("customers", []))
    grb, flow_vars = build_transport_gurobi_model(
        plants=plants,
        customers=customers,
        supply=dict(model.constraints["supply_constraints"].rhs_spec),
        demand=dict(model.constraints["demand_constraints"].rhs_spec),
        costs=model.parameters["costs"],
        warm_start=model.parameters.get("warm_start"),
        lower_bounds=dict(model.variables["flows"].lower_bounds),
        upper_bounds=dict(model.variables["flows"].upper_bounds),
    )

    grb.optimize()
    solve_meta = _extract_solve_meta(grb)

    if grb.Status != 2:
        raise RuntimeError(f"Transportation solve failed with status {grb.Status}")

    solution = {idx: float(var.X) for idx, var in flow_vars.items()}
    return float(grb.ObjVal), solution, solve_meta
