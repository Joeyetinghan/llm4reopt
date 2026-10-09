"""Direct Gurobi build and solve helpers for transport."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Mapping, Tuple

import gurobipy as gp
from gurobipy import GRB


def build_transport_gurobi_model(
    *,
    plants: List[str],
    customers: List[str],
    supply: Dict[str, float],
    demand: Dict[str, float],
    costs: Dict[str, Dict[str, float]],
    warm_start: Dict[Tuple[str, str], float] | None = None,
    lower_bounds: Dict[Tuple[str, str], float] | None = None,
    upper_bounds: Dict[Tuple[str, str], float] | None = None,
) -> tuple["gp.Model", Dict[Tuple[str, str], "gp.Var"]]:
    """Build the transportation model directly in gurobipy."""
    indices = [(i, j) for i in plants for j in customers]
    grb = gp.Model("transport")
    grb.Params.OutputFlag = 1 if _show_solver_log() else 0

    flow_vars: Dict[Tuple[str, str], gp.Var] = {}
    for plant, customer in indices:
        lb = 0.0 if lower_bounds is None else float(lower_bounds.get((plant, customer), 0.0))
        ub_raw = float("inf") if upper_bounds is None else upper_bounds.get((plant, customer), float("inf"))
        ub = GRB.INFINITY if ub_raw is None or ub_raw == float("inf") else float(ub_raw)
        flow_vars[(plant, customer)] = grb.addVar(
            lb=lb,
            ub=ub,
            obj=float(costs[plant][customer]),
            name=f"flow_{plant}_{customer}",
        )

    grb.ModelSense = GRB.MINIMIZE
    grb.update()

    if isinstance(warm_start, dict):
        for index, value in warm_start.items():
            if index in flow_vars and isinstance(value, (int, float)):
                flow_vars[index].Start = float(value)

    for plant in plants:
        grb.addConstr(
            gp.quicksum(flow_vars[(plant, cust)] for cust in customers) <= float(supply[plant]),
            name=f"supply_{plant}",
        )

    for customer in customers:
        grb.addConstr(
            gp.quicksum(flow_vars[(plant, customer)] for plant in plants) >= float(demand[customer]),
            name=f"demand_{customer}",
        )

    grb.update()
    return grb, flow_vars


def solve_transport_direct(
    runtime_data: Mapping[str, Any],
    *,
    warm_start: Dict[Tuple[str, str], float] | None = None,
) -> Tuple[float, Dict[Tuple[str, str], float], Dict[str, float | int]]:
    grb, flow_vars = build_transport_gurobi_model(
        plants=list(runtime_data["plants"]),
        customers=list(runtime_data["customers"]),
        supply=dict(runtime_data["supply"]),
        demand=dict(runtime_data["demand"]),
        costs=dict(runtime_data["costs"]),
        warm_start=warm_start,
    )

    grb.optimize()
    solve_meta = _extract_solve_meta(grb)
    solve_meta["warm_start_applied"] = warm_start is not None

    if grb.Status != GRB.OPTIMAL:
        raise RuntimeError(f"Transportation solve failed with status {grb.Status}")

    solution = {idx: float(var.X) for idx, var in flow_vars.items()}
    return float(grb.ObjVal), solution, solve_meta


def _show_solver_log() -> bool:
    return os.getenv("REOPT_DEBUG_PROMPTS", "").strip().lower() in {"1", "true", "yes", "on"}


def _extract_solve_meta(model: gp.Model) -> Dict[str, float | int]:
    meta: Dict[str, float | int] = {
        "status": int(model.Status),
        "runtime": float(model.Runtime),
    }
    try:
        meta["mip_gap"] = float(model.MIPGap)
    except Exception:
        pass
    try:
        meta["obj_bound"] = float(model.ObjBound)
    except Exception:
        pass
    return meta
