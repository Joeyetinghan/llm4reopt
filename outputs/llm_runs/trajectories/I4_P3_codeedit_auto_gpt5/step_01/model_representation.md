Representation kind: codeedit_solver

Model summary:

{'state_kind': 'codeedit_solver', 'package_root': 'problems/exam_block_seq', 'codeedit_source_type': 'python_builder', 'runtime_keys': ['artifact_stem', 'block_enrollment', 'block_num_exams', 'block_summary_path', 'blockmap_path', 'blocks', 'delta_text', 'disable_default_warm_start', 'dummy_blocks', 'early_slots', 'eve_morn_start', 'frontload_block_size_cutoff', 'frontload_slot_cutoff', 'instance_dir', 'instance_id', 'instance_manifest_path', 'large_blocks', 'log_path', 'lp_path', 'lp_stem', 'other_b2b_start', 'pair_counts', 'pair_counts_path', 'prompt_id', 'prompt_params', 'real_blocks', 'reference_semester', 'reserved_slots', 'seed', 'size', 'slot_times', 'slots', 'slots_per_day', 'solution_path', 'threads', 'time_limit', 'triple_24_start', 'triple_day_start', 'triplet_counts', 'triplet_counts_path', 'tuned_param_path', 'virtual_blocks', 'weights'], 'summary_keys': ['artifact_stem', 'block_enrollment', 'block_num_exams', 'blocks', 'disable_default_warm_start', 'dummy_blocks', 'early_slots', 'eve_morn_start', 'frontload_block_size_cutoff', 'frontload_slot_cutoff', 'instance_id', 'large_blocks', 'lp_stem', 'other_b2b_start', 'pair_counts', 'real_blocks', 'reference_semester', 'reserved_slots', 'seed', 'size', 'slot_times', 'slots', 'slots_per_day', 'threads', 'time_limit', 'triple_24_start', 'triple_day_start', 'triplet_counts', 'virtual_blocks', 'weights'], 'artifact_paths': {}, 'last_solve_meta': {}}

Patch surface:

{'supported_ops': [], 'target_schemas': {}, 'editable_concepts': [], 'structural_ops': [], 'description': 'Direct solver-code editing surface; patch operators are not used here.'}

Solver capabilities:

{'solver_backend': 'gurobi', 'supports_warm_start': True, 'supports_tuned_solver': True, 'source_kind': 'package', 'edited_package_root': 'problems/exam_block_seq', 'codeedit_source_type': 'python_builder'}

Artifact inventory:

{'patchedit_artifacts': ['problems/exam_block_seq/structured.py'], 'codeedit_artifacts': ['problems/exam_block_seq/solver.py'], 'codeedit_read_only_artifacts': ['runtime_snapshot.json'], 'data_artifacts': ['problems/exam_block_seq/configs/default.yaml'], 'base_solution_candidates': ['outputs/solves/base', 'outputs/solves/base/blockseq_n588_blocks19_slots24_seed42.sol'], 'base_log_candidates': ['outputs/solves/base', 'outputs/solves/base/blockseq_n588_blocks19_slots24_seed42.log'], 'tuned_param_candidates': ['outputs/solves/tune/params', 'outputs/solves/tune/params/blockseq_n588_blocks19_slots24_seed42.prm'], 'codeedit_mode': True}

Component descriptors:

- [parameter] lp_stem: Scalar parameter lp_stem.

- [parameter] artifact_stem: Scalar parameter artifact_stem.

- [parameter] instance_id: Scalar parameter instance_id.

- [parameter] reference_semester: Scalar parameter reference_semester.

- [parameter] size: Scalar parameter size.

- [parameter] seed: Scalar parameter seed.

- [parameter] slots: Scalar parameter slots.

- [parameter] slots_per_day: Scalar parameter slots_per_day.

- [parameter] slot_times: List parameter slot_times with 3 values. samples=['9am', '2pm', '7pm']

- [parameter] blocks: List parameter blocks with 24 values. samples=['1', '2', '3', '4', '5']

- [parameter] real_blocks: Scalar parameter real_blocks.

- [parameter] dummy_blocks: List parameter dummy_blocks with 5 values. samples=['20', '21', '22', '23', '24']

- [parameter] virtual_blocks: List parameter virtual_blocks with 5 values. samples=['20', '21', '22', '23', '24']

- [parameter] large_blocks: List parameter large_blocks with 18 values. samples=['1', '2', '3', '4', '5']

- [parameter] early_slots: List parameter early_slots with 21 values. samples=['1', '2', '3', '4', '5']

- [parameter] triple_24_start: List parameter triple_24_start with 14 values. samples=['2', '3', '5', '6', '8']

- [parameter] triple_day_start: List parameter triple_day_start with 8 values. samples=['1', '4', '7', '10', '13']

- [parameter] eve_morn_start: List parameter eve_morn_start with 7 values. samples=['3', '6', '9', '12', '15']

- [parameter] other_b2b_start: List parameter other_b2b_start with 16 values. samples=['1', '2', '4', '5', '7']

- [parameter] weights: Indexed parameter weights with 12 entries. samples=['alpha', 'beta', 'gamma1', 'gamma2', 'delta']

- [parameter] reserved_slots: List parameter reserved_slots with 0 values.

- [parameter] block_enrollment: Indexed parameter block_enrollment with 24 entries. samples=['1', '2', '3', '4', '5']

- [parameter] block_num_exams: Indexed parameter block_num_exams with 24 entries. samples=['1', '2', '3', '4', '5']

- [parameter] pair_counts: Indexed parameter pair_counts with 360 entries. samples=['(1, 2)', '(1, 3)', '(1, 4)', '(1, 5)', '(1, 6)']

- [parameter] triplet_counts: Indexed parameter triplet_counts with 6726 entries. samples=['(1, 2, 2)', '(1, 2, 3)', '(1, 2, 4)', '(1, 2, 5)', '(1, 2, 6)']

- [parameter] frontload_block_size_cutoff: Scalar parameter frontload_block_size_cutoff.

- [parameter] frontload_slot_cutoff: Scalar parameter frontload_slot_cutoff.

- [parameter] time_limit: Scalar parameter time_limit.

- [parameter] threads: Scalar parameter threads.

- [parameter] disable_default_warm_start: Scalar parameter disable_default_warm_start.

Source artifacts:

exam_solver (python_builder) @ problems/exam_block_seq/solver.py

```python
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
    grb = build_exam_gurobi_model(
        blocks=[int(block) for block in data["blocks"]],
        slots_per_day=int(data["slots_per_day"]),
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
```

Context payload:

problem_context

# Exam Block Sequencing Context

You are helping the University Registrar evaluate changes to the final-exam schedule.

- This is the block-sequencing stage of a Group-then-Sequence workflow: exams have already been grouped into blocks, and this model places those blocks into exam slots.
- The model is about exam timing only; room assignment is handled separately.
- Delta requests usually describe policy, comfort, or operational changes to the exam calendar.

## Calendar And Basic Assumptions

- The exam period is a fixed ordered sequence of slots.
- The standard interpretation is three slots per day: morning, afternoon, and evening. In the packaged instances these are usually `9am`, `2pm`, and `7pm`.
- Requests such as “Day 2”, “morning”, “afternoon”, “evening”, or “the evening slot immediately before the final evening slot” should be grounded through `slots_per_day`, `slot_times`, and slot ids, not through guessed LP names.
- Some slots may be intentionally excluded from use. In practice, the Registrar may want to keep certain late slots empty for setup, cleanup, or special events.
- In final patch proposals, relative calendar phrases should be resolved to explicit slot ids when the instance data makes that possible.

## Objective

The sequencing objective penalizes stressful student exam patterns:

- `alpha`: triples within one day
- `beta`: triples within 24 hours
- `gamma1`: evening-to-morning back-to-backs
- `gamma2`: other back-to-backs
- `delta`: three exams in four consecutive slots

Here, a triple within one day (`alpha`) means three exams assigned to three consecutive slots that all fall on the same day. A triple within 24 hours (`beta`) means three exams assigned to three consecutive slots within a 24-hour window, which may cross an overnight boundary. A back-to-back means two exams assigned to consecutive slots; in this model, back-to-backs are split into evening-to-morning (`gamma1`) and other adjacent-slot pairs (`gamma2`). These events are not double-counted: exam pairs that are already part of a triple should not also be counted again as back-to-backs.

## Core Model View

The sequencing formulation is cyclic over the slot set.

- `x[i,j,k,s] = 1` means block `i` is placed at slot `s`, block `j` at slot `s+1`, and block `k` at slot `s+2`.
- The block occupying slot `s` is identified by the first index of `x[i,j,k,s]`, so assignment-like policy rules should be expressed by summing `x[i,*,*,s]` terms over the relevant slots.
- `y[...]` and `z[...]` are linkage variables used to score triple and four-slot patterns.

Assignment and continuity constraints enforce a valid cyclic schedule in which each block is placed once and each slot receives one block.

## Grounding Data

The rendered model representation already exposes the concrete instance data needed to ground requests, including `virtual_blocks`, `large_blocks`, `early_slots`, `reserved_slots`, `block_enrollment`, `pair_counts`, `triplet_counts`, `slots_per_day`, and `slot_times`.

Use those rendered values directly instead of inventing symbolic placeholders, and use the exact numeric slot cutoff stated in the prompt when front-loading requests are instance-specific.

## Canonical Interpretations

- Slot reservation requests should use `reserved_virtual_slot`; `reserved_slots` is the canonical slot list feeding that mechanism.
- Front-loading requests should be interpreted through `large_blocks` and `early_slots`.
- Day-load requests should use `block_enrollment` and the first-index occupancy view of `x[i,j,k,s]`.
- Co-enrollment changes usually affect `pair_counts` / `p` or `triplet_counts` / `t`; pairwise block relationships are unordered in the policy language, so update both ordered pair keys unless the request explicitly distinguishes direction.
- Weight or comfort tradeoff requests usually affect `alpha`, `beta`, `gamma1`, `gamma2`, or `delta`.
- If the instance data resolves a slot or day directly, emit explicit slot ids rather than symbolic formulas.

## Combined Requests

- Preserve the requested order when one request bundles several changes.
- Combined Registrar edits may mix weight changes, co-enrollment edits, front-loading, slot reservations, and day-load restrictions.
