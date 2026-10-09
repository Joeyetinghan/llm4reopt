"""Packaged adapter for the transportation problem."""

from __future__ import annotations

from typing import Any

from framework.core import (
    ClassifierSchema,
    CodeEditState,
    DeltaRequest,
    Patch,
    PatchOp,
    ProblemSpec,
    ReoptResult,
    SolveStrategy,
)
from framework.evaluation import build_evaluation_summary
from problems.base import BasePackagedProblemAdapter
from problems.transport.env import TransportationEnv
from problems.transport.patching import normalize_transport_patches
from problems.transport.solver import solve_transport_direct
from problems.transport.validator import TransportValidatorSolver


class TransportProblemAdapter(BasePackagedProblemAdapter):
    def classifier_schema(self, spec: ProblemSpec, prompt_context=None) -> ClassifierSchema:
        del spec, prompt_context
        return ClassifierSchema()

    def capability_overrides(self) -> dict[str, Any]:
        return {
            "supports_warm_start": True,
            "supports_tuned_solver": False,
            "legacy_env_name": "transportation",
            "example_aliases": ["transportation"],
        }

    def normalize_patches(
        self,
        spec: ProblemSpec,
        event,
        patches: list[Patch],
        model=None,
    ) -> list[Patch]:
        model = model or self.build_structured_model(spec)
        return normalize_transport_patches(patches, model, event)

    def supported_patch_ops(self) -> list[PatchOp]:
        return [
            PatchOp.UPDATE_PARAMETER,
            PatchOp.UPDATE_BOUND,
            PatchOp.UPDATE_CONSTRAINT_RHS,
            PatchOp.UPDATE_OBJECTIVE_COEFF,
            PatchOp.UPDATE_OBJECTIVE_WEIGHT,
        ]

    def extract_warm_start(self, reopt_result) -> Any | None:
        return reopt_result.solution

    def solve_codeedit(
        self,
        spec: ProblemSpec,
        state: CodeEditState,
        strategy: SolveStrategy,
        warm_start: Any | None = None,
        solve_context: dict[str, Any] | None = None,
    ) -> ReoptResult:
        del solve_context
        objective, solution, solve_meta = solve_transport_direct(
            state.runtime_data,
            warm_start=warm_start,
        )
        state.last_solve_meta = dict(solve_meta)
        representation = self._build_grounder(spec, planner_mode="codeedit").ground(
            spec,
            self.build_env(spec),
            state,
            delta_request=DeltaRequest(text=""),
        )
        prompt_context = self.build_prompt_context(spec, representation, DeltaRequest(text=""))
        result = ReoptResult(
            delta_request=DeltaRequest(text=""),
            prompt_context=prompt_context,
            strategy=strategy,
            strategy_selection=None,
            event={},
            relevant_components=[],
            candidate_patches=[],
            chosen_patches=[],
            objective=objective,
            solution=solution,
            solve_meta=solve_meta,
            evaluation=build_evaluation_summary(objective=objective, solve_meta=solve_meta),
            change_report="Direct code-edit solve",
            model_summary=state.describe(),
        )
        result.evaluation = self.evaluate(spec, result)
        return result

    def _build_env(self, spec: ProblemSpec):
        data = dict(spec.config_metadata.get("loaded_data") or spec.config_metadata.get("config") or {})
        return TransportationEnv(
            plants=data["plants"],
            customers=data["customers"],
            supply=data["supply"],
            demand=data["demand"],
            costs=data["costs"],
        )

    def _build_validator(self, spec: ProblemSpec):
        del spec
        return TransportValidatorSolver()
