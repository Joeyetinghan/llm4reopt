"""Direct Gurobi build and solve helpers for exam block sequencing."""

from __future__ import annotations

import os
from typing import Any, Dict, Mapping, Tuple

import gurobipy as gp
from gurobipy import GRB

from framework.core.solver_utils import SolveFailureError
from problems.exam_block_seq.solution import extract_block_assignments_from_model
from problems.exam_block_seq.warm_start import apply_exam_warm_start_payload


def build_exam_gurobi_model(
    *,
    blocks: list[int],
    slots_per_day: int,
    triple_24_start: list[int],
    triple_day_start: list[int],
    eve_morn_start: list[int],
    other_b2b_start: list[int],
    weights: Dict[str, float],
    p: Dict[tuple[int, int], float] | None = None,
    t: Dict[tuple[int, int, int], float] | None = None,
    large_blocks: list[int] | None = None,
    early_slots: list[int] | None = None,
    reserved_slots: list[int] | None = None,
    virtual_blocks: list[int] | None = None,
    time_limit: float | None = None,
) -> "gp.Model":
    """Build the upstream block-sequencing model directly in gurobipy."""
    del slots_per_day
    slots = list(blocks)
    block_sequence_trip = [(i, j, k) for i in blocks for j in blocks for k in blocks]
    block_sequence_quad = [(i, j, k, l) for i in blocks for j in blocks for k in blocks for l in blocks]
    block_sequence_slot = [(i, j, k, s) for i in blocks for j in blocks for k in blocks for s in slots]

    next_slot = {slot: slots[(idx + 1) % len(slots)] for idx, slot in enumerate(slots)}
    triple_slots = sorted(list(triple_day_start) + list(triple_24_start))
    pair_penalties = dict(p or {})
    triplet_penalties = dict(t or {})

    alpha = float(weights.get("alpha", 10.0))
    beta = float(weights.get("beta", 10.0))
    gamma1 = float(weights.get("gamma1", 1.0))
    gamma2 = float(weights.get("gamma2", 1.0))
    delta = float(weights.get("delta", 5.0))

    m = gp.Model("BlockSequencing")
    m.setParam("OutputFlag", 1 if _show_solver_log() else 0)
    if time_limit:
        m.setParam("TimeLimit", float(time_limit))

    x = m.addVars(block_sequence_slot, vtype=GRB.BINARY, name="x")
    y = m.addVars(block_sequence_trip, vtype=GRB.BINARY, name="y")
    z = m.addVars(block_sequence_quad, vtype=GRB.BINARY, name="z")

    m.addConstrs(
        (
            gp.quicksum(x[i, j, k, s] for j in blocks for k in blocks for s in slots) == 1
            for i in blocks
        ),
        name="each_i",
    )
    m.addConstrs(
        (
            gp.quicksum(x[i, j, k, s] for i in blocks for k in blocks for s in slots) == 1
            for j in blocks
        ),
        name="each_j",
    )
    m.addConstrs(
        (
            gp.quicksum(x[i, j, k, s] for i in blocks for j in blocks for s in slots) == 1
            for k in blocks
        ),
        name="each_k",
    )
    m.addConstrs(
        (
            gp.quicksum(x[i, j, k, s] for i in blocks for j in blocks for k in blocks) == 1
            for s in slots
        ),
        name="each_slot",
    )

    m.addConstrs((x[i, i, k, s] == 0 for i in blocks for k in blocks for s in slots), name="no_ii")
    m.addConstrs((x[i, j, i, s] == 0 for i in blocks for j in blocks for s in slots), name="no_ik")
    m.addConstrs((x[i, j, j, s] == 0 for i in blocks for j in blocks for s in slots), name="no_jj")

    m.addConstrs(
        (
            gp.quicksum(x[i, j, k, s] for i in blocks)
            == gp.quicksum(x[j, k, l, next_slot[s]] for l in blocks)
            for j in blocks
            for k in blocks
            for s in slots
        ),
        name="continuity",
    )

    m.addConstrs(
        (
            y[i, j, k] == gp.quicksum(x[i, j, k, s] for s in triple_slots)
            for i, j, k in block_sequence_trip
        ),
        name="define_y",
    )
    m.addConstrs(
        (
            z[i, j, k, l] >= y[i, j, k] + y[j, k, l] - 1
            for i, j, k, l in block_sequence_quad
        ),
        name="define_z",
    )

    frontload_blocks = sorted(
        {
            int(block)
            for block in (large_blocks or [])
            if isinstance(block, (int, float)) and int(block) in blocks
        }
    )
    early_slot_values = [int(slot) for slot in (early_slots or [])]
    if frontload_blocks:
        m.addConstrs(
            (
                gp.quicksum(
                    x[i, j, k, s]
                    for j in blocks
                    for k in blocks
                    for s in early_slot_values
                )
                == 1
                for i in frontload_blocks
            ),
            name="frontload",
        )

    # Reserve specific slots by assigning a virtual block to those slots.
    reserved_slot_values = sorted({int(s) for s in (reserved_slots or []) if int(s) in slots})
    virtual_block_values = [int(v) for v in (virtual_blocks or []) if int(v) in blocks]
    if reserved_slot_values and virtual_block_values:
        m.addConstrs(
            (
                gp.quicksum(
                    x[i, j, k, s]
                    for i in virtual_block_values
                    for j in blocks
                    for k in blocks
                )
                == 1
                for s in reserved_slot_values
            ),
            name="reserve_virtual_slots",
        )

    objective = (
        gp.quicksum(
            gamma1 * float(pair_penalties.get((i, j), 0.0)) * x[i, j, k, s]
            for i in blocks
            for j in blocks
            for k in blocks
            for s in eve_morn_start
        )
        + gp.quicksum(
            gamma2 * float(pair_penalties.get((i, j), 0.0)) * x[i, j, k, s]
            for i in blocks
            for j in blocks
            for k in blocks
            for s in other_b2b_start
        )
        + gp.quicksum(
            alpha * float(triplet_penalties.get((i, j, k), 0.0)) * x[i, j, k, s]
            for i in blocks
            for j in blocks
            for k in blocks
            for s in triple_day_start
        )
        + gp.quicksum(
            beta * float(triplet_penalties.get((i, j, k), 0.0)) * x[i, j, k, s]
            for i in blocks
            for j in blocks
            for k in blocks
            for s in triple_24_start
        )
        + gp.quicksum(
            delta
            * (
                float(triplet_penalties.get((i, j, k), 0.0))
                + float(triplet_penalties.get((i, k, l), 0.0))
            )
            * z[i, j, k, l]
            for i in blocks
            for j in blocks
            for k in blocks
            for l in blocks
        )
    )
    m.setObjective(objective, GRB.MINIMIZE)

    m.update()
    return m


def solve_exam_direct(
    runtime_data: Mapping[str, Any],
    *,
    warm_start: Any | None = None,
    solver_params: dict[str, Any] | None = None,
    time_limit: int | float | None = None,
) -> Tuple[float, Dict[int, int], Dict[str, float | int]]:
    """Solve the direct solver model used by the codeedit pipeline."""
    data = dict(runtime_data)

    # Resolve "evening slot immediately before the final evening slot" to a concrete slot id.
    blocks_list = [int(block) for block in data["blocks"]]
    slots_per_day = int(data["slots_per_day"])
    slot_times = list(data.get("slot_times") or [])
    # Determine the index of the evening slot within a day.
    # Prefer to find "7pm" if present; otherwise fall back to the last slot in the day.
    evening_idx_in_day = None
    for idx, label in enumerate(slot_times):
        if isinstance(label, str) and label.strip().lower() in {"7pm", "evening"}:
            evening_idx_in_day = idx
            break
    if evening_idx_in_day is None:
        evening_idx_in_day = max(0, slots_per_day - 1)

    total_days = max(1, len(blocks_list) // slots_per_day)
    # Evening slot of the day immediately before the final day.
    reserved_slot = (total_days - 2) * slots_per_day + (evening_idx_in_day + 1)
    reserved_slot_values = [reserved_slot]

    grb = build_exam_gurobi_model(
        blocks=blocks_list,
        slots_per_day=slots_per_day,
        triple_24_start=[int(slot) for slot in data["triple_24_start"]],
        triple_day_start=[int(slot) for slot in data["triple_day_start"]],
        eve_morn_start=[int(slot) for slot in data["eve_morn_start"]],
        other_b2b_start=[int(slot) for slot in data["other_b2b_start"]],
        weights=dict(data["weights"]),
        p=dict(data.get("pair_counts") or {}),
        t=dict(data.get("triplet_counts") or {}),
        large_blocks=[int(block) for block in data.get("large_blocks", [])],
        early_slots=[int(slot) for slot in data.get("early_slots", [])],
        reserved_slots=reserved_slot_values,
        virtual_blocks=[int(v) for v in data.get("virtual_blocks", [])],
        time_limit=float(time_limit if time_limit is not None else data.get("time_limit", 600)),
    )
    apply_solver_params(grb, solver_params)
    apply_exam_warm_start_payload(grb, warm_start)
    grb.update()
    grb.optimize()

    solve_meta = extract_solve_meta(grb)
    solve_meta["warm_start_applied"] = warm_start is not None
    solve_meta["warm_start_source"] = "provided" if warm_start is not None else "none"
    solve_meta["warm_start_mode"] = solve_meta["warm_start_source"]
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

    solution = extract_block_assignments_from_model(grb, [int(block) for block in data["blocks"]])
    return float(grb.ObjVal), solution, solve_meta


def _show_solver_log() -> bool:
    return os.getenv("REOPT_DEBUG_PROMPTS", "").strip().lower() in {"1", "true", "yes", "on"}


def apply_solver_params(model: gp.Model, solver_params: Any) -> None:
    if not isinstance(solver_params, dict):
        return
    for name, value in solver_params.items():
        if value is None:
            continue
        try:
            model.setParam(str(name), value)
        except Exception as exc:
            raise RuntimeError(f"Failed to set Gurobi parameter {name}={value!r}") from exc


def extract_solve_meta(model: gp.Model) -> Dict[str, float | int]:
    meta: Dict[str, float | int] = {
        "status": int(model.Status),
        "runtime": float(model.Runtime),
        "sol_count": int(model.SolCount),
    }
    try:
        meta["mip_gap"] = float(model.MIPGap)
    except Exception:
        pass
    try:
        meta["obj_bound"] = float(model.ObjBound)
    except Exception:
        pass
    if model.SolCount > 0:
        try:
            meta["objective"] = float(model.ObjVal)
        except Exception:
            pass
    return meta
