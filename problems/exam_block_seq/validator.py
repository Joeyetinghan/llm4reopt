"""Validator/solver for exam block sequencing patches."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence, Tuple

from framework.core import BaseEnv, BaseValidatorSolver, Patch, StructuredModel, apply_patch
from framework.core.action_sets import coerce_action_sets
from framework.core.failure_taxonomy import attach_patchedit_failure
from framework.core.schemas import PlannedActionSet
from framework.core.patches import apply_patch_sequence
from framework.core.solver_utils import SolveFailureError, solve_with_metadata, summarize_candidate_failures


class ExamBlockSeqValidatorSolver(BaseValidatorSolver):
    def __init__(self):
        self.last_selected_patches: list[Patch] = []

    def validate_and_solve(
        self,
        patches: Sequence[Patch] | Sequence[PlannedActionSet],
        model: StructuredModel,
        env: BaseEnv,
        warm_start: Any | None = None,
        solve_context: dict[str, Any] | None = None,
        trace_dir: str | Path | None = None,
    ) -> Tuple[Patch, float, dict, dict]:
        best_patch: Patch | None = None
        best_cost: float | None = None
        best_solution = None
        best_meta: dict = {}
        failures: list[BaseException] = []
        trace_root = Path(trace_dir) if trace_dir is not None else None

        for idx, action_set in enumerate(coerce_action_sets(patches), start=1):
            if action_set.action_kind != "patch":
                continue
            patch_set = [action for action in action_set.actions if isinstance(action, Patch)]
            if not patch_set:
                continue
            try:
                candidate_model = apply_patch_sequence(model.copy(), patch_set)
            except Exception as exc:
                failures.append(attach_patchedit_failure(exc, kind="patch_application_failed"))
                print(f"Warning: Unexpected error validating patch set {patch_set}: {exc}")
                continue
            try:
                if warm_start is not None:
                    candidate_model.parameters["warm_start"] = warm_start
                if isinstance(solve_context, dict):
                    for key, value in solve_context.items():
                        candidate_model.parameters[key] = value
                if trace_root is not None:
                    solver_params = dict(candidate_model.parameters.get("solver_params") or {})
                    solver_params["OutputFlag"] = 1
                    solver_params["LogToConsole"] = 0
                    solver_params["LogFile"] = str(trace_root / f"candidate_{idx:02d}_solver.log")
                    candidate_model.parameters["solver_params"] = solver_params
                cost, solution, meta = solve_with_metadata(env, candidate_model)
            except SolveFailureError as exc:
                kind = "no_incumbent" if str(exc).startswith("No incumbent solution available") else "solve_failed"
                failures.append(attach_patchedit_failure(exc, kind=kind))
                continue
            except RuntimeError as exc:
                failures.append(attach_patchedit_failure(exc, kind="solve_failed"))
                continue
            except Exception as exc:
                failures.append(attach_patchedit_failure(exc, kind="solve_failed"))
                print(f"Warning: Unexpected error validating patch set {patch_set}: {exc}")
                continue

            if best_cost is None or cost < best_cost:
                best_patch = patch_set[0]
                best_cost = cost
                best_solution = solution
                best_meta = meta
                self.last_selected_patches = list(patch_set)

        if best_patch is None or best_cost is None:
            raise summarize_candidate_failures(failures)

        return best_patch, best_cost, best_solution, best_meta
