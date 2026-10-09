"""Packaged adapter for exam block sequencing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from framework.core import (
    ClassifierSchema,
    CodeEditState,
    DeltaRequest,
    Patch,
    PatchOp,
    PlannedActionSet,
    ProblemSpec,
    ReoptResult,
    SolveExecutionOptions,
    SolveStrategy,
    StrategySelectionDecision,
    apply_patch,
)
from framework.evaluation import build_evaluation_summary
from framework.utils.gurobi_tuning import load_prm_params
from problems.base import BasePackagedProblemAdapter
from problems.exam_block_seq.dataloader import load_block_seq_data_from_mapping
from problems.exam_block_seq.env import ExamBlockSeqEnv
from problems.exam_block_seq.execution_labels import (
    DIRECT_WARM_START_TOOL,
    HEURISTIC_WARM_START_TOOL,
    TUNED_CONFIG_TOOL,
)
from problems.exam_block_seq.patching import (
    derive_exam_semantic_patches,
    normalize_exam_patches,
    resolve_exam_composed_action_sets,
)
from problems.exam_block_seq.solver import solve_exam_direct
from problems.exam_block_seq.validator import ExamBlockSeqValidatorSolver
from problems.exam_block_seq.warm_start import resolve_exam_warm_start_payload
from problems.exam_block_seq._codeedit_heuristic import (
    apply_exam_codeedit_heuristic_projection,
    classify_exam_codeedit_heuristic_support,
    project_exam_codeedit_heuristic_support,
)


class ExamBlockSeqProblemAdapter(BasePackagedProblemAdapter):
    def classifier_schema(self, spec: ProblemSpec, prompt_context=None) -> ClassifierSchema:
        del spec, prompt_context
        return ClassifierSchema(
            guidance=(
                "Use the canonical exam patch forms. "
                "Do not invent new constraint-family names or semantic lhs kinds when an existing exam family fits. "
                "For slot exclusion requests like the penultimate evening slot, emit "
                "ADD_CONSTRAINT_FAMILY with constraint.name='reserved_virtual_slot', lhs_spec.kind='reserved_virtual_slot', "
                "the explicit grounded slot id, and the smallest available virtual block id. "
                "For large-exam frontloading requests, emit UPDATE_PARAMETER(name='early_slots', value=[...]) "
                "with explicit slot ids; do not invent a new frontload family name. "
                "For pairwise co-enrollment changes between two blocks, emit keyed UPDATE_PARAMETER patches for both ordered pair keys "
                "[a,b] and [b,a] on the pair-count override surface unless the request explicitly distinguishes direction. "
                "For day-level load caps, emit ADD_CONSTRAINT_FAMILY with constraint.name='slot_load_cap', "
                "lhs_spec.kind='slot_load_cap', explicit slot ids, and a stable row id such as 'day_2'."
            ),
            codeedit_guidance=(
                "Use the existing exam solver mechanisms rather than inventing new interfaces or rewriting the model structure. "
                "For slot exclusion requests, resolve the concrete slot from slot_times and slots_per_day, update reserved_slots, "
                "and preserve the virtual-block reservation mechanism that materializes the slot exclusion. "
                "For pairwise co-enrollment changes between two blocks, keep pair-count updates symmetric across both ordered keys unless the request explicitly distinguishes direction. "
                "For large-exam frontloading requests, reuse early_slots and the existing large_blocks logic instead of adding a new frontload formulation. "
                "For day-level load caps, ground the limit with block_enrollment over the affected day slots and preserve the existing slot-load-cap semantics. "
                "For stress-penalty changes, update the existing objective weight parameters rather than rewriting the objective structure."
            ),
        )

    def load_problem(
        self,
        package_root: str,
        config_path: str | None = None,
        config_mapping: dict[str, Any] | None = None,
        examples_path: str | None = None,
        load_runtime_data: bool = True,
    ) -> ProblemSpec:
        spec = super().load_problem(
            package_root,
            config_path=config_path,
            config_mapping=config_mapping,
            examples_path=examples_path,
            load_runtime_data=load_runtime_data,
        )
        if load_runtime_data:
            config_base_dir = None
            config_file = spec.config_metadata.get("config_path")
            if config_file is not None:
                config_base_dir = str(config_file.parent)
            spec.config_metadata["loaded_data"] = load_block_seq_data_from_mapping(
                spec.config_metadata["config"],
                base_dir=config_base_dir,
            )
            tuned_param_path = spec.config_metadata["loaded_data"].get("tuned_param_path")
            spec.capabilities["supports_tuned_solver"] = bool(
                tuned_param_path and Path(str(tuned_param_path)).expanduser().exists()
            )
        return spec

    def capability_overrides(self) -> dict[str, Any]:
        return {
            "supports_warm_start": True,
            "supports_tuned_solver": False,
            "legacy_env_name": "exam_block_seq",
            "example_aliases": ["exam_block_seq"],
        }

    def classify_codeedit_execution_metadata(
        self,
        spec: ProblemSpec,
        delta_request: DeltaRequest,
        planner_output,
        action_sets: list[PlannedActionSet],
    ) -> dict[str, Any]:
        del action_sets
        return classify_exam_codeedit_heuristic_support(
            structured=self.build_structured_model(spec),
            delta_request=delta_request,
            planner_output=planner_output,
        )

    def normalize_patches(
        self,
        spec: ProblemSpec,
        event,
        patches: list[Patch],
        model=None,
    ) -> list[Patch]:
        model = model or self.build_structured_model(spec)
        return normalize_exam_patches(patches, model, event)

    def normalize_action_sets(
        self,
        spec: ProblemSpec,
        event,
        action_sets: list[PlannedActionSet],
        model=None,
    ) -> list[PlannedActionSet]:
        base_model = model or self.build_structured_model(spec)
        composed_action_sets = resolve_exam_composed_action_sets(event, base_model)
        if composed_action_sets:
            action_sets = composed_action_sets
        normalized_sets: list[PlannedActionSet] = []
        for action_set in action_sets:
            if action_set.action_kind != "patch":
                normalized_sets.append(action_set)
                continue
            working_model = base_model.copy()
            normalized_patches: list[Patch] = []
            pending = [action for action in action_set.actions if isinstance(action, Patch)]
            while pending:
                raw_patch = pending.pop(0)
                for patch in normalize_exam_patches([raw_patch], working_model, event):
                    normalized_patches.append(patch)
                    working_model = apply_patch(working_model, patch, in_place=True)
                    pending = list(derive_exam_semantic_patches(patch, working_model)) + pending
            if normalized_patches:
                normalized_sets.append(
                    PlannedActionSet(
                        action_kind="patch",
                        actions=normalized_patches,
                        label=action_set.label,
                        metadata=dict(action_set.metadata),
                    )
                )
        return normalized_sets

    def supported_patch_ops(self) -> list[PatchOp]:
        return [
            PatchOp.UPDATE_PARAMETER,
            PatchOp.UPDATE_BOUND,
            PatchOp.UPDATE_CONSTRAINT_RHS,
            PatchOp.UPDATE_CONSTRAINT_LHS,
            PatchOp.UPDATE_OBJECTIVE_COEFF,
            PatchOp.UPDATE_OBJECTIVE_WEIGHT,
            PatchOp.ADD_CONSTRAINT_FAMILY,
        ]

    def extract_warm_start(self, reopt_result) -> Any | None:
        return reopt_result.solution

    def prepare_warm_start(
        self,
        spec: ProblemSpec,
        strategy: SolveStrategy,
        warm_start: Any | None,
        *,
        warm_start_meta: dict[str, Any] | None = None,
        prior_result=None,
    ) -> Any | None:
        del spec, prior_result
        if strategy not in {SolveStrategy.WARM, SolveStrategy.WARM_TUNED}:
            return warm_start
        solution_path = (warm_start_meta or {}).get("solution_path")
        if solution_path in {None, ""}:
            return warm_start
        solution_file = Path(str(solution_path)).expanduser().resolve()
        if not solution_file.exists():
            return warm_start
        return {"sol_path": str(solution_file)}

    def load_base_context(self, spec: ProblemSpec, model=None):
        del model
        loaded = spec.config_metadata.get("loaded_data") or {}
        solution_path = loaded.get("solution_path")
        log_path = loaded.get("log_path")
        if solution_path in {None, ""} or log_path in {None, ""}:
            return None

        from problems.exam_block_seq.solution import parse_base_log_file, parse_base_solution_file

        solution_file = Path(str(solution_path)).expanduser().resolve()
        log_file = Path(str(log_path)).expanduser().resolve()
        if not solution_file.exists() or not log_file.exists():
            return None

        try:
            objective, solution = parse_base_solution_file(solution_file)
            solve_meta = parse_base_log_file(log_file)
        except (OSError, ValueError):
            return None
        solve_meta["source"] = "saved_base_artifacts"
        solve_meta["solution_path"] = str(solution_file)
        solve_meta["log_path"] = str(log_file)
        return objective, solution, solve_meta

    def solve_context(
        self,
        spec: ProblemSpec,
        strategy: SolveStrategy,
    ) -> dict[str, Any]:
        loaded = spec.config_metadata.get("loaded_data") or {}
        default_threads = loaded.get("threads")
        if strategy not in {SolveStrategy.TUNED, SolveStrategy.WARM_TUNED}:
            return {}
        tuned_param_path = loaded.get("tuned_param_path")
        if tuned_param_path in {None, ""}:
            return {}
        solver_params = load_prm_params(tuned_param_path)
        if default_threads is not None:
            solver_params["Threads"] = int(default_threads)
        return {"solver_params": solver_params}

    def resolve_execution_options(
        self,
        spec: ProblemSpec,
        strategy_selection: StrategySelectionDecision,
        warm_start: Any | None,
        *,
        warm_start_meta: dict[str, Any] | None = None,
        prior_result=None,
        delta_request: DeltaRequest | None = None,
        planner_output=None,
    ) -> SolveExecutionOptions:
        toolbox = set(strategy_selection.toolbox_plan)
        direct_enabled = DIRECT_WARM_START_TOOL in toolbox or "warm_start" in toolbox
        heuristic_enabled = HEURISTIC_WARM_START_TOOL in toolbox
        tuned_enabled = TUNED_CONFIG_TOOL in toolbox or "tuned_solver" in toolbox

        direct_warm_start = None
        if direct_enabled:
            direct_warm_start = self.prepare_warm_start(
                spec,
                SolveStrategy.WARM,
                warm_start,
                warm_start_meta=warm_start_meta,
                prior_result=prior_result,
            )
            if direct_warm_start is None:
                raise ValueError(
                    f"Execution label {strategy_selection.execution_label or strategy_selection.strategy.value!r} "
                    "requires a reusable direct warm start, but none was available."
                )

        solve_context: dict[str, Any] = {}
        if tuned_enabled:
            solve_context.update(self.solve_context(spec, SolveStrategy.TUNED))

        if getattr(planner_output, "action_kind", "") != "codeedit":
            solve_context["disable_default_warm_start"] = not heuristic_enabled
            return SolveExecutionOptions(
                warm_start=direct_warm_start,
                solve_context=solve_context,
                metadata={"execution_label": strategy_selection.execution_label or strategy_selection.strategy.value},
            )

        structured = self.build_structured_model(spec)
        projection = project_exam_codeedit_heuristic_support(
            structured=structured,
            delta_request=delta_request or DeltaRequest(text=""),
            planner_output=planner_output,
        )
        planner_output.annotations.update(projection.as_metadata())

        if not heuristic_enabled or not projection.heuristic_warm_start_available:
            solve_context["warm_start_source_override"] = "base" if direct_warm_start is not None else "none"
            return SolveExecutionOptions(
                warm_start=direct_warm_start,
                solve_context=solve_context,
                metadata={"execution_label": strategy_selection.execution_label or strategy_selection.strategy.value},
            )
        apply_exam_codeedit_heuristic_projection(structured, projection=projection)
        structured.parameters["warm_start"] = direct_warm_start
        structured.parameters["disable_default_warm_start"] = False
        resolved_warm_start, warm_start_source = resolve_exam_warm_start_payload(structured)
        solve_context["warm_start_source_override"] = warm_start_source
        return SolveExecutionOptions(
            warm_start=resolved_warm_start,
            solve_context=solve_context,
            metadata={
                "execution_label": strategy_selection.execution_label or strategy_selection.strategy.value,
                "warm_start_source": warm_start_source,
            },
        )

    def solve_codeedit(
        self,
        spec: ProblemSpec,
        state: CodeEditState,
        strategy: SolveStrategy,
        warm_start: Any | None = None,
        solve_context: dict[str, Any] | None = None,
    ) -> ReoptResult:
        solve_context = dict(solve_context or {})
        solver_params = dict(solve_context.get("solver_params") or {})
        default_threads = state.runtime_data.get("threads")
        if default_threads is not None:
            solver_params["Threads"] = int(default_threads)
        objective, solution, solve_meta = solve_exam_direct(
            state.runtime_data,
            warm_start=warm_start,
            solver_params=solver_params,
            time_limit=state.runtime_data.get("time_limit", 600),
        )
        warm_start_source_override = solve_context.get("warm_start_source_override")
        if isinstance(warm_start_source_override, str) and warm_start_source_override:
            solve_meta["warm_start_applied"] = warm_start_source_override != "none"
            solve_meta["warm_start_source"] = warm_start_source_override
            solve_meta["warm_start_mode"] = warm_start_source_override
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
        data = spec.config_metadata["loaded_data"]
        return ExamBlockSeqEnv(
            lp_path=data["lp_path"],
            blocks=data["blocks"],
            slots_per_day=data["slots_per_day"],
            slot_times=data.get("slot_times"),
            triple_24_start=data["triple_24_start"],
            triple_day_start=data["triple_day_start"],
            eve_morn_start=data["eve_morn_start"],
            other_b2b_start=data["other_b2b_start"],
            weights=data["weights"],
            reserved_slots=data.get("reserved_slots"),
            real_blocks=data.get("real_blocks"),
            virtual_blocks=data.get("virtual_blocks"),
            large_blocks=data.get("large_blocks"),
            early_slots=data.get("early_slots"),
            block_enrollment=data.get("block_enrollment"),
            block_num_exams=data.get("block_num_exams"),
            pair_counts=data.get("pair_counts"),
            triplet_counts=data.get("triplet_counts"),
            frontload_block_size_cutoff=data.get("frontload_block_size_cutoff"),
            frontload_slot_cutoff=data.get("frontload_slot_cutoff"),
            time_limit=data.get("time_limit", 600),
            threads=data.get("threads"),
            instance_id=data.get("instance_id"),
        )

    def _build_validator(self, spec: ProblemSpec):
        del spec
        return ExamBlockSeqValidatorSolver()
