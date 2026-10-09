"""Helpers for deriving exam block assignments from LP solutions."""
from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import gurobipy as gp


_B_ASSIGNMENT_PATTERN = re.compile(r"^b\[(?P<block>\d+),(?P<slot>\d+)\]$")
_X_ASSIGNMENT_PATTERN = re.compile(r"^x\[(?P<i>\d+),(?P<j>\d+),(?P<k>\d+),(?P<slot>\d+)\]$")


def extract_block_assignments_from_model(model: "gp.Model", blocks: list[int]) -> dict[int, int]:
    solution: dict[int, int] = {}
    active_triplets: list[tuple[int, int, int, int]] = []
    block_set = {int(block) for block in blocks}

    for variable in model.getVars():
        if variable.X <= 0.5:
            continue

        assignment_match = _B_ASSIGNMENT_PATTERN.match(variable.VarName)
        if assignment_match is not None:
            block = int(assignment_match.group("block"))
            slot = int(assignment_match.group("slot"))
            if block in block_set:
                solution[block] = slot
            continue

        x_match = _X_ASSIGNMENT_PATTERN.match(variable.VarName)
        if x_match is None:
            continue

        triplet = (
            int(x_match.group("i")),
            int(x_match.group("j")),
            int(x_match.group("k")),
            int(x_match.group("slot")),
        )
        if all(block in block_set for block in triplet[:3]):
            active_triplets.append(triplet)

    if solution:
        return solution
    if active_triplets:
        return derive_block_assignments_from_triplets(active_triplets)
    return {}


def derive_block_assignments_from_triplets(
    active_triplets: list[tuple[int, int, int, int]]
) -> dict[int, int]:
    slot_count = max(slot for _, _, _, slot in active_triplets)
    slot_assignments: dict[int, int] = {}

    for block_i, block_j, block_k, slot in active_triplets:
        for assigned_slot, block in (
            (wrap_slot(slot, slot_count), block_i),
            (wrap_slot(slot + 1, slot_count), block_j),
            (wrap_slot(slot + 2, slot_count), block_k),
        ):
            current = slot_assignments.get(assigned_slot)
            if current is not None and current != block:
                raise ValueError(
                    "Inconsistent x[i,j,k,s] triplets: "
                    f"slot {assigned_slot} maps to both {current} and {block}"
                )
            slot_assignments[assigned_slot] = block

    block_assignments: dict[int, int] = {}
    for slot, block in slot_assignments.items():
        current = block_assignments.get(block)
        if current is not None and current != slot:
            raise ValueError(
                "Inconsistent x[i,j,k,s] triplets: "
                f"block {block} maps to both slots {current} and {slot}"
            )
        block_assignments[block] = slot
    return block_assignments


def wrap_slot(slot: int, slot_count: int) -> int:
    return ((int(slot) - 1) % slot_count) + 1


# Base artifacts: the saved Gurobi .sol file and log of a base solve.
SOL_OBJECTIVE_PATTERN = re.compile(r"^# Objective value = (?P<objective>[-+0-9.eE]+)$")
SOL_B_ASSIGNMENT_PATTERN = re.compile(
    r"^b\[(?P<block>\d+),(?P<slot>\d+)\]\s+(?P<value>[-+0-9.eE]+)$"
)
SOL_X_ASSIGNMENT_PATTERN = re.compile(
    r"^x\[(?P<i>\d+),(?P<j>\d+),(?P<k>\d+),(?P<slot>\d+)\]\s+(?P<value>[-+0-9.eE]+)$"
)
LOG_RUNTIME_PATTERN = re.compile(r"^Explored .* in (?P<runtime>[-+0-9.eE]+) seconds")
LOG_FINAL_SUMMARY_PATTERN = re.compile(
    r"^Best objective (?P<objective>[-+0-9.eE]+), best bound (?P<bound>[-+0-9.eE]+), gap (?P<gap>[-+0-9.eE]+)%$"
)
LOG_SOLUTION_COUNT_PATTERN = re.compile(r"^Solution count (?P<count>\d+):")
LOG_OPTIMAL_STATUS_PATTERN = re.compile(r"^Optimal solution found(?:\s+\(tolerance .*?\))?$")


def parse_base_solution_file(sol_path: Path) -> tuple[float, dict[int, int]]:
    if not sol_path.exists():
        raise FileNotFoundError(f"Base solution file not found: {sol_path}")

    objective: float | None = None
    solution: dict[int, int] = {}
    active_triplets: list[tuple[int, int, int, int]] = []
    with sol_path.open("r", encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line:
                continue
            objective_match = SOL_OBJECTIVE_PATTERN.match(line)
            if objective_match is not None:
                objective = float(objective_match.group("objective"))
                continue
            assignment_match = SOL_B_ASSIGNMENT_PATTERN.match(line)
            if assignment_match is not None:
                value = float(assignment_match.group("value"))
                if value <= 0.5:
                    continue
                block = int(assignment_match.group("block"))
                slot = int(assignment_match.group("slot"))
                solution[block] = slot
                continue

            x_match = SOL_X_ASSIGNMENT_PATTERN.match(line)
            if x_match is None:
                continue
            value = float(x_match.group("value"))
            if value <= 0.5:
                continue
            active_triplets.append(
                (
                    int(x_match.group("i")),
                    int(x_match.group("j")),
                    int(x_match.group("k")),
                    int(x_match.group("slot")),
                )
            )

    if not solution and active_triplets:
        solution = derive_block_assignments_from_triplets(active_triplets)

    if objective is None:
        raise ValueError(f"Missing objective header in base solution file: {sol_path}")
    if not solution:
        raise ValueError(f"Missing warm-start assignments in base solution file: {sol_path}")
    return objective, solution


def parse_base_log_file(log_path: Path) -> dict[str, Any]:
    if not log_path.exists():
        raise FileNotFoundError(f"Base log file not found: {log_path}")

    meta: dict[str, Any] = {}
    with log_path.open("r", encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line:
                continue
            if line == "Time limit reached":
                meta["status"] = 9
                continue
            if LOG_OPTIMAL_STATUS_PATTERN.match(line) is not None:
                meta["status"] = 2
                continue
            runtime_match = LOG_RUNTIME_PATTERN.match(line)
            if runtime_match is not None:
                meta["runtime"] = float(runtime_match.group("runtime"))
                continue
            summary_match = LOG_FINAL_SUMMARY_PATTERN.match(line)
            if summary_match is not None:
                meta["obj_bound"] = float(summary_match.group("bound"))
                meta["mip_gap"] = float(summary_match.group("gap")) / 100.0
                continue
            solution_count_match = LOG_SOLUTION_COUNT_PATTERN.match(line)
            if solution_count_match is not None:
                meta["solution_count"] = int(solution_count_match.group("count"))

    if "runtime" not in meta:
        raise ValueError(f"Missing runtime summary in base log file: {log_path}")
    if "status" not in meta:
        raise ValueError(f"Missing terminal solve status in base log file: {log_path}")
    return meta
