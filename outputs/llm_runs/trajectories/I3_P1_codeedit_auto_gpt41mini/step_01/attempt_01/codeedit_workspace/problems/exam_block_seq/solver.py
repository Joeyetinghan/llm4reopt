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

    # Reserve the evening slot immediately before the final evening slot for auditorium setup
    # The final evening slot is the last slot with time "7pm"
    # We find all slots with time "7pm" and pick the last one, then reserve the previous slot cyclically
    # This reservation is done by excluding that slot from assignment (reserved slot)
    # We add this slot to reserved_slots and exclude it from the model by removing it from slots and related sets

    # Determine the evening slots from the slots list and slot_times
    # We need to get slot_times and slots_per_day from environment or parameters
    # Since we don't have slot_times here, we must rely on the global instance data or environment
    # Instead, we will assume the last evening slot is the max slot in eve_morn_start or other_b2b_start with time "7pm"
    # But these are slot subsets, so better to get the last slot with time "7pm" from the slots list and slot_times

    # We will get slot_times and slots_per_day from environment variables or from a global variable
    # But since this is not available, we will do a workaround:
    # The last evening slot is the max slot in slots where (slot - 1) % 3 == 2 (0-based indexing)
    # Because slots_per_day=3, evening slot is slot index mod 3 == 2 (0-based)
    # So evening slots are those where (slot - 1) % 3 == 2 (since slots are 1-based)
    # The final evening slot is max of these, the reserved slot is the previous slot cyclically

    # Compute evening slots
    evening_slots = [slot for slot in slots if (slot - 1) % 3 == 2]
    if evening_slots:
        final_evening_slot = max(evening_slots)
        # previous slot cyclically
        idx = slots.index(final_evening_slot)
        reserved_slot = slots[idx - 1] if idx > 0 else slots[-1]
        # Remove reserved_slot from slots and related sets
        slots = [s for s in slots if s != reserved_slot]
        # Also remove reserved_slot from triple_slots if present
        triple_slots = [s for s in triple_slots if s != reserved_slot]
        # Also remove reserved_slot from eve_morn_start and other_b2b_start if present
        eve_morn_start = [s for s in eve_morn_start if s != reserved_slot]
        other_b2b_start = [s for s in other_b2b_start if s != reserved_slot]

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

    # Reserve the evening slot immediately before the final evening slot for auditorium setup
    # We add this slot to reserved_slots to exclude it from assignment
    slot_times = data.get("slot_times", [])
    slots_per_day = int(data.get("slots_per_day", 3))
    slots = list(data.get("blocks", []))

    # Find all evening slots (time == "7pm")
    evening_slot_indices = [idx for idx, time in enumerate(slot_times) if time == "7pm"]
    # Compute all slot ids that correspond to evening slots
    evening_slots = []
    for idx in evening_slot_indices:
        # Slots are 1-based, so slot id = idx * slots_per_day + offset + 1
        # But slot_times is length slots_per_day, repeated over days
        # So evening slot offset is idx + 1 (1-based)
        # Slots are sequential, so evening slots are those slots where (slot - 1) % slots_per_day == idx
        evening_slots = [slot for slot in slots if (slot - 1) % slots_per_day == idx]
        if evening_slots:
            break  # Use the first found evening slot index with "7pm"

    if evening_slots:
        final_evening_slot = max(evening_slots)
        # previous slot cyclically
        idx = slots.index(final_evening_slot)
        reserved_slot = slots[idx - 1] if idx > 0 else slots[-1]
        # Add reserved_slot to reserved_slots in data to exclude it
        reserved_slots = set(data.get("reserved_slots", []))
        reserved_slots.add(reserved_slot)
        data["reserved_slots"] = list(sorted(reserved_slots))

    grb = build_exam_gurobi_model(
        blocks=[int(block) for block in data["blocks"]],
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
