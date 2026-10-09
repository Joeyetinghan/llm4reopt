"""Generic validator for LP pattern-edit bundles."""

from __future__ import annotations

import time
from typing import Any, Sequence

from gurobipy import GRB

from framework.core import BaseEnv, BaseValidatorSolver, Patch, StructuredModel
from framework.core.action_sets import coerce_action_sets
from framework.core.schemas import PlannedActionSet
from framework.lp.executor import apply_patches_to_gurobi


def _count_changes(patch_logs: list[dict[str, Any]]) -> int:
    total = 0
    for log in patch_logs:
        total += int(log.get("num_changes", 0))
        total += int(log.get("num_updated", 0))
        total += int(log.get("num_fixed", 0))
    return total


class LPPatternValidatorSolver(BaseValidatorSolver):
    """Evaluate LP-safe pattern edits on copied Gurobi models and keep the best candidate."""

    def __init__(self):
        self.last_selected_patches: list[Patch] = []

    def validate_and_solve(
        self,
        patches: Sequence[Patch] | Sequence[PlannedActionSet],
        model: StructuredModel,
        env: BaseEnv,
        warm_start: Any | None = None,
        solve_context: dict[str, Any] | None = None,
    ):
        del model, warm_start, solve_context

        if not hasattr(env, "load_gurobi_model"):
            raise TypeError("LPPatternValidatorSolver requires an env with load_gurobi_model()")

        base_model = env.load_gurobi_model()
        sense = "maximize" if getattr(base_model, "ModelSense", 1) < 0 else "minimize"

        candidates: list[tuple[list[Patch], str]] = []
        for action_set in coerce_action_sets(patches):
            if action_set.action_kind != "patch":
                continue
            patch_set = [action for action in action_set.actions if isinstance(action, Patch)]
            if not patch_set:
                continue
            label = action_set.label or ", ".join(patch.op.value for patch in patch_set)
            candidates.append((patch_set, label))

        best_patch_set: list[Patch] | None = None
        best_cost: float | None = None
        best_solution: dict[str, Any] | None = None

        for patch_set, label in candidates:
            try:
                candidate = base_model.copy()
                candidate.Params.OutputFlag = 0
                t0 = time.time()
                patch_logs = apply_patches_to_gurobi(candidate, patch_set)
                total_changes = _count_changes(patch_logs)
                if total_changes == 0:
                    continue

                candidate.optimize()
                if candidate.Status == GRB.INF_OR_UNBD:
                    candidate.Params.Presolve = 0
                    candidate.optimize()

                solve_time = time.time() - t0
                solution: dict[str, Any] = {
                    "status": int(candidate.Status),
                    "patch_logs": patch_logs,
                    "solve_time": solve_time,
                    "candidate_label": label,
                    "total_changes": total_changes,
                }
                if candidate.Status in (GRB.OPTIMAL, GRB.TIME_LIMIT) and candidate.SolCount > 0:
                    objective = float(candidate.ObjVal)
                    solution["objective"] = objective
                    solution["gap"] = float(candidate.MIPGap) if candidate.IsMIP else 0.0
                else:
                    objective = float("-inf") if sense == "maximize" else float("inf")
                    solution["objective"] = objective

                is_better = best_cost is None or (
                    objective > best_cost if sense == "maximize" else objective < best_cost
                )
                if not is_better and best_cost is not None and abs(objective - best_cost) < 1e-6:
                    previous_changes = best_solution.get("total_changes", 0) if best_solution else 0
                    if total_changes > previous_changes:
                        is_better = True

                if is_better:
                    best_patch_set = patch_set
                    best_cost = objective
                    best_solution = solution
                    self.last_selected_patches = list(patch_set)
            except Exception as exc:
                print(f"Warning: LP patch validation failed ({label}): {exc}")
                continue

        if best_patch_set is None or best_cost is None or best_solution is None:
            raise RuntimeError("No feasible LP patches found")

        chosen_patch = best_patch_set[0]
        solve_meta = {
            "status": best_solution.get("status"),
            "solve_time": best_solution.get("solve_time"),
            "gap": best_solution.get("gap"),
            "candidate_label": best_solution.get("candidate_label"),
            "total_changes": best_solution.get("total_changes"),
        }
        if len(best_patch_set) > 1:
            best_solution["combined_patches"] = [patch.describe() for patch in best_patch_set]
        return chosen_patch, best_cost, best_solution, solve_meta
