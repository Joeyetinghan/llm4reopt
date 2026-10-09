"""Framework interfaces and compatibility exports."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from framework.llm.clients import BaseLLMClient

from .events import ClassifierSchema, StructuredEvent
from .model import StructuredModel
from .patches import Patch, PatchOp
from .schemas import (
    CodeEditState,
    DeltaRequest,
    EvaluationSummary,
    GroundTruthCase,
    GroundTruthCheckResult,
    GroundTruthModeResult,
    ModelRepresentation,
    PlannerOutput,
    PlannedActionSet,
    ProblemSpec,
    PromptContext,
    ReoptResult,
    SolveExecutionOptions,
    StrategySelectionDecision,
    SolveStrategy,
)


@dataclass
class ProblemRuntime:
    env: "BaseEnv"
    grounder: "BaseGrounder"
    planner: "PlannerBackend"
    validator: "BaseValidatorSolver"
    reporter: "BaseReporter"


class BaseEnv(ABC):
    name: str

    @abstractmethod
    def build_structured_model(self) -> StructuredModel:
        """Return the pre-change structured model."""

    @abstractmethod
    def solve(self, model: StructuredModel) -> tuple[float, Any]:
        """Solve and return (objective, solution) or (objective, solution, metadata)."""


class BaseGrounder(ABC):
    @abstractmethod
    def ground(
        self,
        spec: ProblemSpec,
        env: BaseEnv,
        model: Any,
        *,
        delta_request: DeltaRequest | None = None,
    ) -> ModelRepresentation:
        """Build a deterministic model representation for planner consumption."""


class PlannerBackend(ABC):
    @abstractmethod
    def plan(
        self,
        delta_request: DeltaRequest,
        representation: ModelRepresentation,
    ) -> PlannerOutput:
        """Return a planner output for the requested reoptimization change."""


class BaseValidatorSolver(ABC):
    @abstractmethod
    def validate_and_solve(
        self,
        patches: list[Patch] | list[PlannedActionSet],
        model: StructuredModel,
        env: BaseEnv,
        warm_start: Any | None = None,
        solve_context: dict[str, Any] | None = None,
    ) -> tuple[Patch, float, Any, dict]:
        """Pick the best patch, solve, and return (patch, objective, solution, solve_meta)."""


class BaseReporter(ABC):
    @abstractmethod
    def start_step(
        self,
        spec: ProblemSpec,
        delta_request: DeltaRequest,
        representation: ModelRepresentation,
    ) -> dict[str, Any]:
        """Initialize a trace collector for one run step and return mutable trace state."""

    @abstractmethod
    def finalize_step(
        self,
        trace: dict[str, Any],
        *,
        prompt_context: PromptContext,
        planner_output: PlannerOutput,
        normalized_action_sets: list[PlannedActionSet],
        normalized_patches: list[Patch],
        chosen_patches: list[Patch],
        strategy: SolveStrategy,
        strategy_selection: StrategySelectionDecision,
        objective_before: float,
        objective_after: float,
        solve_meta: dict[str, Any],
        solution: Any,
    ) -> dict[str, Any]:
        """Persist human-readable traces and return artifact metadata."""

    def record_planner_step(
        self,
        trace: dict[str, Any],
        *,
        planner_output: PlannerOutput,
        normalized_action_sets: list[PlannedActionSet],
        normalized_patches: list[Patch],
    ) -> None:
        """Persist planner prompt/response artifacts before validation."""

    def finalize_failure_step(
        self,
        trace: dict[str, Any],
        *,
        prompt_context: PromptContext,
        planner_output: PlannerOutput | None,
        normalized_action_sets: list[PlannedActionSet],
        normalized_patches: list[Patch],
        strategy: SolveStrategy | None,
        strategy_selection: StrategySelectionDecision | None,
        objective_before: float,
        error: Exception,
    ) -> dict[str, Any]:
        """Persist traces for a failed step and return artifact metadata."""
        return {}


class BaseLLMAgent(ABC):
    """Shared functionality for agents that delegate to an LLM client."""

    def __init__(self, llm_client: BaseLLMClient):
        self.llm = llm_client
        self._debug_prompts = os.getenv("REOPT_DEBUG_PROMPTS", "").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self._log_collector: dict[str, Any] | None = None

    def set_log_collector(self, collector: dict[str, Any]) -> None:
        self._log_collector = collector

    def _call_llm(self, prompt: list[dict[str, str]], *, label: str) -> str:
        if self._debug_prompts:
            print(f"[LLM DEBUG][{label}] Prompt:")
            for msg in prompt:
                role = msg.get("role", "?")
                content = msg.get("content", "")
                print(f"  {role}: {content}")

        step: dict[str, Any] | None = None
        if self._log_collector is not None:
            self._log_collector.setdefault("steps", [])
            step = {
                "agent": label,
                "prompt": prompt,
            }

        response = self.llm.chat(prompt)

        if self._debug_prompts:
            print(f"[LLM DEBUG][{label}] Response:\n{response}\n")

        if self._log_collector is not None and step is not None:
            step["response"] = response
            self._log_collector["steps"].append(step)

        return response


class SolveStrategyPolicy(ABC):
    @abstractmethod
    def select(
        self,
        spec: ProblemSpec,
        delta_request: DeltaRequest,
        planner_output: PlannerOutput,
        prior_result: ReoptResult | None = None,
        warm_start_available: bool = False,
    ) -> StrategySelectionDecision:
        """Choose the solve strategy for a delta step."""


class ProblemAdapter(ABC):
    @abstractmethod
    def load_problem(
        self,
        package_root: str,
        config_path: str | None = None,
        config_mapping: dict[str, Any] | None = None,
        examples_path: str | None = None,
        load_runtime_data: bool = True,
    ) -> ProblemSpec:
        """Load a packaged problem and return its standardized spec."""

    @abstractmethod
    def build_structured_model(self, spec: ProblemSpec):
        """Build the current structured model."""

    @abstractmethod
    def build_prompt_context(
        self,
        spec: ProblemSpec,
        model_or_representation,
        delta_request: DeltaRequest,
    ) -> PromptContext:
        """Assemble the LLM-facing planner context."""

    @abstractmethod
    def normalize_patches(
        self,
        spec: ProblemSpec,
        event,
        patches: list[Patch],
        model=None,
    ) -> list[Patch]:
        """Adapter-aware patch normalization."""

    def normalize_action_sets(
        self,
        spec: ProblemSpec,
        event,
        action_sets: list[PlannedActionSet],
        model=None,
    ) -> list[PlannedActionSet]:
        """Adapter-aware grouped action-set normalization."""
        normalized: list[PlannedActionSet] = []
        for action_set in action_sets:
            if action_set.action_kind != "patch":
                normalized.append(action_set)
                continue
            patches = [action for action in action_set.actions if isinstance(action, Patch)]
            normalized_patches = self.normalize_patches(
                spec,
                event,
                patches,
                model=model,
            )
            if not normalized_patches:
                continue
            normalized.append(
                PlannedActionSet(
                    action_kind="patch",
                    actions=normalized_patches,
                    label=action_set.label,
                    metadata=dict(action_set.metadata),
                )
            )
        return normalized

    def normalize_codeedit_action_sets(
        self,
        spec: ProblemSpec,
        event,
        action_sets: list[PlannedActionSet],
        model=None,
    ) -> list[PlannedActionSet]:
        """Adapter-aware codeedit action normalization.

        Code-edit execution depends on the planner preserving workspace payloads,
        so the default behavior is a strict passthrough.
        """
        del spec, event, model
        return list(action_sets)

    @abstractmethod
    def solve(
        self,
        spec: ProblemSpec,
        model,
        strategy: SolveStrategy,
        warm_start: Any | None = None,
    ) -> ReoptResult:
        """Solve a patched model under a chosen strategy."""

    def build_codeedit_state(self, spec: ProblemSpec) -> CodeEditState:
        active_root = spec.config_metadata.get("codeedit_active_problem_root")
        package_root = (
            Path(str(active_root)).expanduser().resolve()
            if active_root is not None
            else spec.metadata.package_root.expanduser().resolve()
        )
        primary_kind = spec.codeedit_artifacts[0].source_type if spec.codeedit_artifacts else "python_builder"
        config_mapping = dict(spec.config_metadata.get("config") or {})
        runtime_data = dict(spec.config_metadata.get("loaded_data") or config_mapping)
        artifact_paths = {
            str(key): str(value)
            for key, value in dict(spec.config_metadata.get("codeedit_workspace_artifacts") or {}).items()
        }
        summary_metadata = _codeedit_summary_metadata(runtime_data or config_mapping)
        return CodeEditState(
            package_root=package_root,
            codeedit_source_type=primary_kind,
            config_mapping=config_mapping,
            runtime_data=runtime_data,
            summary_metadata=summary_metadata,
            artifact_paths=artifact_paths,
        )

    def solve_codeedit(
        self,
        spec: ProblemSpec,
        state: CodeEditState,
        strategy: SolveStrategy,
        warm_start: Any | None = None,
        solve_context: dict[str, Any] | None = None,
    ) -> ReoptResult:
        model = self.build_structured_model(spec)
        result = self.solve(spec, model, strategy, warm_start=warm_start)
        state.last_solve_meta = dict(result.solve_meta or {})
        return result

    @abstractmethod
    def evaluate(self, spec: ProblemSpec, reopt_result: ReoptResult) -> EvaluationSummary:
        """Build a standardized evaluation summary."""

    @abstractmethod
    def extract_warm_start(self, reopt_result: ReoptResult) -> Any | None:
        """Extract a reusable warm start from a solve result."""

    @abstractmethod
    def supported_patch_ops(self) -> list[PatchOp]:
        """Return adapter-supported patch operators."""

    @abstractmethod
    def build_env(self, spec: ProblemSpec) -> BaseEnv:
        """Build the environment required to solve the packaged problem."""

    @abstractmethod
    def build_runtime(
        self,
        spec: ProblemSpec,
        model_name: str,
        api_key: str | None,
        prompt_context: PromptContext | None = None,
    ) -> ProblemRuntime:
        """Build runtime components for the generic execution pipeline."""

    def classifier_schema(
        self,
        spec: ProblemSpec,
        prompt_context: PromptContext | None = None,
    ) -> ClassifierSchema:
        """Return adapter-specific extraction/planning guidance.

        The standalone classifier is gone from the main runtime, but existing
        adapter guidance is still useful to shape the planner prompt.
        """
        del spec, prompt_context
        return ClassifierSchema()

    def load_base_context(
        self,
        spec: ProblemSpec,
        model: Any | None = None,
    ) -> tuple[float, Any, dict[str, Any]] | None:
        """Optionally reuse a saved base solve context instead of resolving it in-run."""
        del spec, model
        return None

    def prepare_warm_start(
        self,
        spec: ProblemSpec,
        strategy: SolveStrategy,
        warm_start: Any | None,
        *,
        warm_start_meta: dict[str, Any] | None = None,
        prior_result: ReoptResult | None = None,
    ) -> Any | None:
        """Return the runtime warm-start payload for one solve."""
        del spec, strategy, warm_start_meta, prior_result
        return warm_start

    def solve_context(
        self,
        spec: ProblemSpec,
        strategy: SolveStrategy,
    ) -> dict[str, Any]:
        """Return optional per-strategy solver hints such as tuned parameters."""
        del spec, strategy
        return {}

    def resolve_execution_options(
        self,
        spec: ProblemSpec,
        strategy_selection: StrategySelectionDecision,
        warm_start: Any | None,
        *,
        warm_start_meta: dict[str, Any] | None = None,
        prior_result: ReoptResult | None = None,
        delta_request: DeltaRequest | None = None,
        planner_output: PlannerOutput | None = None,
    ) -> SolveExecutionOptions:
        """Resolve the concrete warm-start and solve-context payloads for one solve."""
        del delta_request, planner_output
        toolbox = set(strategy_selection.toolbox_plan)
        warm_selected = (
            strategy_selection.strategy in {SolveStrategy.WARM, SolveStrategy.WARM_TUNED}
            or "warm_start" in toolbox
            or "direct_warm_start" in toolbox
        )
        resolved_warm_start = None
        if warm_selected:
            resolved_warm_start = self.prepare_warm_start(
                spec,
                strategy_selection.strategy,
                warm_start,
                warm_start_meta=warm_start_meta,
                prior_result=prior_result,
            )
        solve_context = self.solve_context(spec, strategy_selection.strategy)
        return SolveExecutionOptions(
            warm_start=resolved_warm_start,
            solve_context=dict(solve_context or {}),
        )

    def classify_codeedit_execution_metadata(
        self,
        spec: ProblemSpec,
        delta_request: DeltaRequest,
        planner_output: PlannerOutput,
        action_sets: list[PlannedActionSet],
    ) -> dict[str, Any]:
        """Return optional codeedit execution metadata before strategy selection."""
        del spec, delta_request, planner_output, action_sets
        return {}


def _codeedit_summary_metadata(values: dict[str, Any]) -> dict[str, Any]:
    return {
        str(key): value
        for key, value in values.items()
        if not _skip_codeedit_summary_key(str(key))
    }


def _skip_codeedit_summary_key(key: str) -> bool:
    lowered = key.strip().lower()
    if lowered in {"delta_text", "prompt_id", "prompt_params"}:
        return True
    return lowered.endswith(("_path", "_dir", "_root"))


class GroundTruthEvaluator(ABC):
    @abstractmethod
    def load_cases(self, spec: ProblemSpec) -> list[GroundTruthCase]:
        """Return all available ground-truth cases for a packaged problem."""

    @abstractmethod
    def match_case(
        self,
        spec: ProblemSpec,
        result_payload: dict[str, Any],
        explicit_case_id: str | None = None,
    ) -> GroundTruthCase | None:
        """Resolve a result payload to a ground-truth case."""

    def evaluate_patch_case(
        self,
        spec: ProblemSpec,
        case: GroundTruthCase,
        result_payload: dict[str, Any],
    ) -> GroundTruthModeResult | None:
        """Evaluate patch semantics for one result payload against one case."""
        return None

    def evaluate_reference_case(
        self,
        spec: ProblemSpec,
        case: GroundTruthCase,
        result_payload: dict[str, Any],
        *,
        reference_policy: str = "off",
    ) -> GroundTruthModeResult | None:
        """Evaluate optional reference-solve behavior for one payload."""
        del spec, case, result_payload, reference_policy
        return None

    def evaluate_case(
        self,
        spec: ProblemSpec,
        case: GroundTruthCase,
        result_payload: dict[str, Any],
        *,
        reference_policy: str = "off",
    ) -> GroundTruthCheckResult:
        """Evaluate one result payload against one ground-truth case."""
        mode_results: dict[str, GroundTruthModeResult] = {}
        patch_result = self.evaluate_patch_case(spec, case, result_payload)
        if patch_result is not None:
            mode_results[patch_result.mode] = patch_result

        reference_result = self.evaluate_reference_case(
            spec,
            case,
            result_payload,
            reference_policy=reference_policy,
        )
        if reference_result is not None:
            mode_results[reference_result.mode] = reference_result

        return self._combine_mode_results(spec, case, result_payload, mode_results)

    def _combine_mode_results(
        self,
        spec: ProblemSpec,
        case: GroundTruthCase,
        result_payload: dict[str, Any],
        mode_results: dict[str, GroundTruthModeResult],
    ) -> GroundTruthCheckResult:
        result_path = (
            str(result_payload.get("__result_path__"))
            if result_payload.get("__result_path__")
            else None
        )
        if not mode_results:
            return GroundTruthCheckResult(
                problem_id=spec.metadata.problem_id,
                case_id=case.case_id,
                result_path=result_path,
                status="pending",
                matched_case=True,
                matches_ground_truth=None,
                checks={},
                details={
                    "case_description": case.description,
                    "message": "No ground-truth checks were performed for this case.",
                },
                mode_results={},
            )

        statuses = {result.status for result in mode_results.values()}
        if "failed" in statuses:
            status = "failed"
        elif "pending" in statuses:
            status = "pending"
        else:
            status = "passed"

        concrete_matches = [
            result.matches for result in mode_results.values() if result.matches is not None
        ]
        matches_ground_truth = all(concrete_matches) if concrete_matches else None

        checks: dict[str, Any] = {}
        details: dict[str, Any] = {"case_description": case.description}
        for mode, result in mode_results.items():
            checks[mode] = result.matches
            checks.update(result.checks)
            details[mode] = result.to_dict()

        return GroundTruthCheckResult(
            problem_id=spec.metadata.problem_id,
            case_id=case.case_id,
            result_path=result_path,
            status=status,
            matched_case=True,
            matches_ground_truth=matches_ground_truth,
            checks=checks,
            details=details,
            mode_results=mode_results,
        )
