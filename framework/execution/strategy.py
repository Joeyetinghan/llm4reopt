"""Solve strategy policies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from framework.agents.reopt_strategy_selector_agent import ReoptStrategySelectorAgent
from framework.core import (
    Patch,
    PatchOp,
    PlannerOutput,
    ProblemSpec,
    ReoptResult,
    SolveStrategy,
    SolveStrategyPolicy,
    StrategySelectionDecision,
)
from framework.core.action_sets import coerce_action_sets, flatten_patch_actions
from framework.core.planner_modes import PATCHEDIT_MODE, normalize_planner_mode
from framework.core.schemas import DeltaRequest
from framework.llm import create_llm_client
from problems.exam_block_seq.execution_labels import (
    DIRECT_WARM_START_TOOL,
    HEURISTIC_WARM_START_TOOL,
    TUNED_CONFIG_TOOL,
    allowed_exam_execution_labels,
    default_exam_execution_label_for_planner_mode,
    execution_spec_for_exam_label,
    is_exam_execution_label,
    normalize_exam_execution_label,
)


STRUCTURAL_PATCH_OPS = {
    PatchOp.ADD_CONSTRAINT_FAMILY,
    PatchOp.ADD_OBJECTIVE_COMPONENT,
    PatchOp.ADD_VARIABLE_FAMILY,
    PatchOp.REMOVE_CONSTRAINT_FAMILY,
}

TOOLBOX_ITEM_ALIASES = {
    "warm start": "warm_start",
    "warm_start": "warm_start",
    "direct warm start": DIRECT_WARM_START_TOOL,
    "direct_warm_start": DIRECT_WARM_START_TOOL,
    "prior solution": "prior_solution",
    "prior_solution": "prior_solution",
    "base solution": "base_solution",
    "base_solution": "base_solution",
    "tuned solver": "tuned_solver",
    "tuned_solver": "tuned_solver",
    "tuned config": TUNED_CONFIG_TOOL,
    "tuned_config": TUNED_CONFIG_TOOL,
    "heuristic warm start": "heuristic_warm_start",
    "heuristic_warm_start": "heuristic_warm_start",
    "fixing heuristics": "variable_fixing",
    "fixing_heuristics": "variable_fixing",
    "variable fixing": "variable_fixing",
    "variable_fixing": "variable_fixing",
    "valid inequality": "valid_inequalities",
    "valid inequalities": "valid_inequalities",
    "valid_inequality": "valid_inequalities",
    "valid_inequalities": "valid_inequalities",
    "solver configuration": "solver_configuration",
    "solver configurations": "solver_configuration",
    "solver_configuration": "solver_configuration",
    "solver_configurations": "solver_configuration",
}


@dataclass(frozen=True)
class StrategySelectionContext:
    allowed_strategies: list[SolveStrategy]
    allowed_strategy_labels: list[str]
    toolbox_inventory: list[str]
    toolbox_placeholders: list[str]
    structural_edit: bool
    has_prior: bool
    has_base_warm: bool
    warm_start_available: bool
    supports_warm: bool
    supports_tuned: bool
    patch_count: int
    planner_mode: str
    default_execution_label: str
    heuristic_warm_start_available: bool


class RuleBasedSolveStrategyPolicy(SolveStrategyPolicy):
    def select(
        self,
        spec: ProblemSpec,
        delta_request: DeltaRequest,
        planner_output: PlannerOutput,
        prior_result: ReoptResult | None = None,
        warm_start_available: bool = False,
    ) -> StrategySelectionDecision:
        del delta_request

        if _uses_exam_execution_labels(spec):
            context = _build_selection_context(
                spec,
                planner_output,
                prior_result=prior_result,
                warm_start_available=warm_start_available,
            )
            label = _rule_based_exam_execution_label(context)
            return _build_exam_decision(
                label,
                policy_name="rule",
                rationale="Deterministic exam execution-label selection.",
                metadata=_selection_metadata(spec, planner_output, prior_result=prior_result),
            )

        strategy = _rule_based_strategy(
            spec,
            planner_output,
            prior_result=prior_result,
        )
        return StrategySelectionDecision(
            strategy=strategy,
            policy_name="rule",
            execution_label=strategy.value,
            toolbox_plan=_toolbox_plan_for_strategy(strategy, warm_available=_supports_warm(spec), tuned_available=_supports_tuned(spec)),
            rationale="Deterministic capability-based strategy selection.",
            metadata=_selection_metadata(spec, planner_output, prior_result=prior_result),
        )


class LLMBasedSolveStrategyPolicy(SolveStrategyPolicy):
    def __init__(
        self,
        *,
        model_name: str,
        api_key: str | None = None,
        fallback_policy: SolveStrategyPolicy | None = None,
    ):
        self.model_name = model_name
        self.api_key = api_key
        self.fallback_policy = fallback_policy or RuleBasedSolveStrategyPolicy()
        self._selector = ReoptStrategySelectorAgent(
            create_llm_client(model_name=model_name, api_key=api_key)
        )

    def select(
        self,
        spec: ProblemSpec,
        delta_request: DeltaRequest,
        planner_output: PlannerOutput,
        prior_result: ReoptResult | None = None,
        warm_start_available: bool = False,
    ) -> StrategySelectionDecision:
        context = _build_selection_context(
            spec,
            planner_output,
            prior_result=prior_result,
            warm_start_available=warm_start_available,
        )
        fallback = self.fallback_policy.select(
            spec,
            delta_request,
            planner_output,
            prior_result=prior_result,
            warm_start_available=warm_start_available,
        )

        try:
            selector_result = self._selector.select(
                spec=spec,
                delta_request=delta_request,
                planner_output=planner_output,
                selection_context=context,
            )
            metadata = {
                **_selection_metadata(spec, planner_output, prior_result=prior_result),
                "selector_model": self.model_name,
                "selector_confidence": selector_result.confidence,
            }
            if _uses_exam_execution_labels(spec):
                decision = _build_exam_decision(
                    selector_result.strategy_label,
                    policy_name="llm",
                    rationale=selector_result.rationale,
                    metadata=metadata,
                    allowed_labels=context.allowed_strategy_labels,
                    raw_response=selector_result.raw_response,
                    prompt=list(selector_result.prompt),
                    raw_toolbox_plan=selector_result.toolbox_plan,
                )
            else:
                strategy = _parse_strategy_label(selector_result.strategy_label)
                if strategy not in context.allowed_strategies:
                    raise ValueError(
                        f"Unsupported strategy {selector_result.strategy_label!r}; "
                        f"allowed: {[item.value for item in context.allowed_strategies]}"
                    )
                toolbox_plan = _normalize_toolbox_plan(
                    selector_result.toolbox_plan,
                    allowed=context.toolbox_inventory,
                )
                decision = StrategySelectionDecision(
                    strategy=strategy,
                    policy_name="llm",
                    execution_label=strategy.value,
                    toolbox_plan=toolbox_plan,
                    rationale=selector_result.rationale,
                    metadata=metadata,
                    raw_response=selector_result.raw_response,
                    prompt=list(selector_result.prompt),
                )
            return decision
        except Exception as exc:
            return StrategySelectionDecision(
                strategy=fallback.strategy,
                policy_name="llm",
                execution_label=fallback.execution_label,
                toolbox_plan=list(fallback.toolbox_plan),
                rationale=fallback.rationale,
                metadata={
                    **dict(fallback.metadata),
                    "selector_model": self.model_name,
                    "selector_error": str(exc),
                    "fallback_policy_name": fallback.policy_name,
                },
                fallback_used=True,
                fallback_reason=str(exc),
                raw_response=str(getattr(exc, "strategy_selector_raw_response", "")),
                prompt=list(getattr(exc, "strategy_selector_prompt", [])),
            )


def resolve_strategy_policy(
    spec: ProblemSpec,
    *,
    model_name: str,
    api_key: str | None,
) -> SolveStrategyPolicy:
    config = spec.config_metadata.get("config") or {}
    raw_mode = (
        spec.config_metadata.get("strategy_policy")
        or config.get("strategy_policy")
        or spec.capabilities.get("strategy_policy")
        or "rule"
    )
    mode = canonical_strategy_policy_mode(raw_mode, default="rule")
    if mode in {"rule", "rule-based", "rules", "deterministic"}:
        return RuleBasedSolveStrategyPolicy()
    if mode in {"llm", "llm-based", "selector"}:
        selector_model = (
            spec.config_metadata.get("strategy_selector_model")
            or config.get("strategy_selector_model")
            or model_name
        )
        return LLMBasedSolveStrategyPolicy(
            model_name=str(selector_model),
            api_key=api_key,
        )
    raise ValueError(f"Unsupported strategy policy: {raw_mode!r}")


def canonical_strategy_policy_mode(raw_mode: str | None, *, default: str) -> str:
    mode = str(raw_mode or default).strip().lower()
    if mode in {"rule", "rule-based", "rules", "deterministic"}:
        return "rule"
    if mode in {"llm", "llm-based", "selector"}:
        return "llm"
    raise ValueError(f"Unsupported strategy policy: {raw_mode!r}")


def default_requested_strategy_for_policy(raw_mode: str | None, *, default_policy: str) -> str:
    return "scratch" if canonical_strategy_policy_mode(raw_mode, default=default_policy) == "rule" else "auto"


def build_selection_context(
    spec: ProblemSpec,
    planner_output: PlannerOutput,
    *,
    prior_result: ReoptResult | None,
    warm_start_available: bool,
) -> StrategySelectionContext:
    return _build_selection_context(
        spec,
        planner_output,
        prior_result=prior_result,
        warm_start_available=warm_start_available,
    )


def build_manual_strategy_selection(
    spec: ProblemSpec,
    planner_output: PlannerOutput,
    *,
    requested: str,
    prior_result: ReoptResult | None,
    warm_start_available: bool,
) -> StrategySelectionDecision:
    context = build_selection_context(
        spec,
        planner_output,
        prior_result=prior_result,
        warm_start_available=warm_start_available,
    )
    if _uses_exam_execution_labels(spec):
        label = _resolve_manual_exam_execution_label(
            requested,
            allowed_labels=context.allowed_strategy_labels,
            planner_mode=context.planner_mode,
        )
        decision = _build_exam_decision(
            label,
            policy_name="manual",
            rationale="User-requested exam execution-label override.",
            metadata=_selection_metadata(spec, planner_output, prior_result=prior_result),
            allowed_labels=context.allowed_strategy_labels,
        )
        return decision

    strategy = _parse_strategy_label(requested)
    supports_warm = bool(spec.capabilities.get("supports_warm_start"))
    supports_tuned = bool(spec.capabilities.get("supports_tuned_solver"))
    if strategy in {SolveStrategy.WARM, SolveStrategy.WARM_TUNED} and not supports_warm:
        raise ValueError(
            f"Problem '{spec.metadata.problem_id}' does not support warm-start strategies"
        )
    if strategy in {SolveStrategy.TUNED, SolveStrategy.WARM_TUNED} and not supports_tuned:
        raise ValueError(
            f"Problem '{spec.metadata.problem_id}' does not support tuned strategies"
        )
    if strategy in {SolveStrategy.WARM, SolveStrategy.WARM_TUNED} and not warm_start_available:
        raise ValueError("Warm-start strategy requested but no base solution is available")
    return StrategySelectionDecision(
        strategy=strategy,
        policy_name="manual",
        execution_label=strategy.value,
        toolbox_plan=[
            item
            for item, enabled in (
                ("warm_start", strategy in {SolveStrategy.WARM, SolveStrategy.WARM_TUNED}),
                ("tuned_solver", strategy in {SolveStrategy.TUNED, SolveStrategy.WARM_TUNED}),
            )
            if enabled
        ],
        rationale="User-requested solve strategy override.",
    )


def _rule_based_strategy(
    spec: ProblemSpec,
    planner_output: PlannerOutput,
    *,
    prior_result: ReoptResult | None,
) -> SolveStrategy:
    if planner_output.action_kind != "patch":
        return SolveStrategy.SCRATCH

    patches = _flatten_patches(planner_output)
    supports_warm = _supports_warm(spec)
    supports_tuned = _supports_tuned(spec)
    has_prior = prior_result is not None
    has_base_warm = bool(spec.capabilities.get("has_base_warm_start"))
    structural_edit = any(patch.op in STRUCTURAL_PATCH_OPS for patch in patches)

    if structural_edit:
        return SolveStrategy.TUNED if supports_tuned else SolveStrategy.SCRATCH
    if not has_prior and has_base_warm and supports_warm and supports_tuned:
        return SolveStrategy.WARM_TUNED
    if not has_prior and has_base_warm and supports_warm:
        return SolveStrategy.WARM
    if has_prior and supports_warm and supports_tuned:
        return SolveStrategy.WARM_TUNED
    if has_prior and supports_warm:
        return SolveStrategy.WARM
    if supports_tuned:
        return SolveStrategy.TUNED
    return SolveStrategy.SCRATCH


def _rule_based_exam_execution_label(context: StrategySelectionContext) -> str:
    if not context.allowed_strategy_labels:
        return "scratch"
    return default_exam_execution_label_for_planner_mode(
        context.planner_mode,
        allowed_labels=context.allowed_strategy_labels,
    )


def _build_selection_context(
    spec: ProblemSpec,
    planner_output: PlannerOutput,
    *,
    prior_result: ReoptResult | None,
    warm_start_available: bool,
) -> StrategySelectionContext:
    patches = _flatten_patches(planner_output)
    supports_warm = _supports_warm(spec)
    supports_tuned = _supports_tuned(spec)
    structural_edit = any(patch.op in STRUCTURAL_PATCH_OPS for patch in patches)
    planner_mode = _planner_mode_for_selection(spec, planner_output)
    heuristic_available = _heuristic_warm_start_available(spec, planner_output, planner_mode=planner_mode)
    allowed: list[SolveStrategy] = [SolveStrategy.SCRATCH]
    if supports_warm and warm_start_available:
        allowed.append(SolveStrategy.WARM)
    if supports_tuned:
        allowed.append(SolveStrategy.TUNED)
    if supports_warm and warm_start_available and supports_tuned:
        allowed.append(SolveStrategy.WARM_TUNED)
    if _uses_exam_execution_labels(spec):
        allowed_labels = allowed_exam_execution_labels(
            direct_available=supports_warm and warm_start_available,
            heuristic_available=heuristic_available,
            tuned_available=supports_tuned,
            include_scratch=True,
        )
        default_label = (
            default_exam_execution_label_for_planner_mode(
                planner_mode,
                allowed_labels=allowed_labels,
            )
            if allowed_labels
            else "scratch"
        )
    else:
        allowed_labels = [item.value for item in allowed]
        default_label = ""
    return StrategySelectionContext(
        allowed_strategies=allowed,
        allowed_strategy_labels=allowed_labels,
        toolbox_inventory=_toolbox_inventory(
            spec,
            prior_result=prior_result,
            warm_start_available=warm_start_available,
            heuristic_warm_start_available=heuristic_available,
            planner_mode=planner_mode,
        ),
        toolbox_placeholders=[] if _uses_exam_execution_labels(spec) else _configured_toolbox_items(spec),
        structural_edit=structural_edit,
        has_prior=prior_result is not None,
        has_base_warm=bool(spec.capabilities.get("has_base_warm_start")),
        warm_start_available=warm_start_available,
        supports_warm=supports_warm,
        supports_tuned=supports_tuned,
        patch_count=len(patches),
        planner_mode=planner_mode,
        default_execution_label=default_label,
        heuristic_warm_start_available=heuristic_available,
    )


def _toolbox_inventory(
    spec: ProblemSpec,
    *,
    prior_result: ReoptResult | None,
    warm_start_available: bool,
    heuristic_warm_start_available: bool,
    planner_mode: str,
) -> list[str]:
    if _uses_exam_execution_labels(spec):
        items: list[str] = []
        if warm_start_available and _supports_warm(spec):
            _append_toolbox_item(items, DIRECT_WARM_START_TOOL)
        if heuristic_warm_start_available:
            _append_toolbox_item(items, HEURISTIC_WARM_START_TOOL)
        if _supports_tuned(spec):
            _append_toolbox_item(items, TUNED_CONFIG_TOOL)
        del prior_result, planner_mode
        return items

    items: list[str] = []
    if warm_start_available and _supports_warm(spec):
        _append_toolbox_item(items, "warm_start")
        if prior_result is not None:
            _append_toolbox_item(items, "prior_solution")
        elif spec.capabilities.get("has_base_warm_start"):
            _append_toolbox_item(items, "base_solution")
    if _supports_tuned(spec):
        _append_toolbox_item(items, "tuned_solver")
    for item in _configured_toolbox_items(spec):
        _append_toolbox_item(items, item)
    return items


def _configured_toolbox_items(spec: ProblemSpec) -> list[str]:
    items: list[str] = []
    configured_values = [
        spec.capabilities.get("toolbox_items"),
        spec.capabilities.get("placeholder_toolbox_items"),
        spec.config_metadata.get("toolbox_items"),
        spec.config_metadata.get("strategy_toolbox_placeholders"),
        spec.config_metadata.get("strategy_toolbox"),
        (spec.config_metadata.get("config") or {}).get("toolbox_items"),
        (spec.config_metadata.get("config") or {}).get("strategy_toolbox_placeholders"),
        (spec.config_metadata.get("config") or {}).get("strategy_toolbox"),
    ]
    for configured in configured_values:
        if not isinstance(configured, list):
            continue
        for item in configured:
            if item is None:
                continue
            _append_toolbox_item(items, item)
    return items


def _selection_metadata(
    spec: ProblemSpec,
    planner_output: PlannerOutput,
    *,
    prior_result: ReoptResult | None,
) -> dict[str, Any]:
    patches = _flatten_patches(planner_output)
    placeholders = [] if _uses_exam_execution_labels(spec) else _configured_toolbox_items(spec)
    planner_mode = _planner_mode_for_selection(spec, planner_output)
    heuristic_available = _heuristic_warm_start_available(
        spec,
        planner_output,
        planner_mode=planner_mode,
    )
    return {
        "supports_warm_start": _supports_warm(spec),
        "supports_tuned_solver": _supports_tuned(spec),
        "has_base_warm_start": bool(spec.capabilities.get("has_base_warm_start")),
        "has_prior_result": prior_result is not None,
        "patch_count": len(patches),
        "planner_edit_summary": planner_output.edit_summary,
        "planner_relevant_components": list(planner_output.relevant_components),
        "planner_hints": dict(planner_output.planning_hints),
        "structural_edit": any(patch.op in STRUCTURAL_PATCH_OPS for patch in patches),
        "toolbox_placeholders": placeholders,
        "toolbox_trace_only": bool(placeholders) and not _uses_exam_execution_labels(spec),
        "planner_mode": planner_mode,
        "heuristic_warm_start_available": heuristic_available,
        "heuristic_prompt_ids": list(planner_output.annotations.get("heuristic_prompt_ids") or []),
        "heuristic_warm_start_reason": str(
            planner_output.annotations.get("heuristic_warm_start_reason") or ""
        ),
    }


def _toolbox_plan_for_strategy(
    strategy: SolveStrategy,
    *,
    warm_available: bool,
    tuned_available: bool,
) -> list[str]:
    toolbox: list[str] = []
    if strategy in {SolveStrategy.WARM, SolveStrategy.WARM_TUNED} and warm_available:
        toolbox.append("warm_start")
    if strategy in {SolveStrategy.TUNED, SolveStrategy.WARM_TUNED} and tuned_available:
        toolbox.append("tuned_solver")
    return toolbox


def _normalize_toolbox_plan(items: list[str], *, allowed: list[str]) -> list[str]:
    allowed_set = {_normalize_toolbox_item(item) for item in allowed}
    normalized: list[str] = []
    for item in items:
        candidate = _normalize_toolbox_item(item)
        if not candidate:
            continue
        if candidate not in allowed_set:
            raise ValueError(f"Unsupported toolbox item {item!r}")
        if candidate not in normalized:
            normalized.append(candidate)
    return normalized


def _append_toolbox_item(items: list[str], item: str) -> None:
    normalized = _normalize_toolbox_item(item)
    if normalized and normalized not in items:
        items.append(normalized)


def _normalize_toolbox_item(item: str) -> str:
    candidate = str(item).strip().lower().replace("-", "_")
    candidate = "_".join(part for part in candidate.replace("/", " ").split())
    return TOOLBOX_ITEM_ALIASES.get(candidate, candidate)


def _flatten_patches(planner_output: PlannerOutput) -> list[Patch]:
    return flatten_patch_actions(
        coerce_action_sets(
            list(planner_output.candidate_action_sets),
            fallback_actions=list(planner_output.candidate_actions),
        )
    )


def _parse_strategy_label(raw: str) -> SolveStrategy:
    normalized = str(raw).strip().lower().replace("_", "+")
    if normalized == "warm+tuned":
        return SolveStrategy.WARM_TUNED
    return SolveStrategy(normalized)


def _resolve_manual_exam_execution_label(
    requested: str,
    *,
    allowed_labels: list[str],
    planner_mode: str,
) -> str:
    normalized = str(requested).strip().lower().replace("_", "+")
    if normalized == "scratch":
        return "scratch"
    if is_exam_execution_label(normalized):
        return normalize_exam_execution_label(normalized)

    strategy = _parse_strategy_label(normalized)
    for candidate in _manual_exam_label_preferences(strategy, planner_mode=planner_mode):
        if candidate in allowed_labels:
            return candidate

    compatible = [
        label
        for label in allowed_labels
        if execution_spec_for_exam_label(label).coarse_strategy is strategy
    ]
    if compatible:
        return compatible[0]

    raise ValueError(
        f"Unsupported exam execution label {requested!r}; allowed: {allowed_labels}"
    )


def _manual_exam_label_preferences(
    strategy: SolveStrategy,
    *,
    planner_mode: str,
) -> tuple[str, ...]:
    if strategy is SolveStrategy.SCRATCH:
        return ("scratch",)

    mode = normalize_planner_mode(planner_mode)
    if strategy is SolveStrategy.WARM:
        if mode == "codeedit":
            return ("direct", "direct+heuristic")
        return ("direct+heuristic", "direct")
    if strategy is SolveStrategy.TUNED:
        if mode == "codeedit":
            return ("tuned", "heuristic+tuned")
        return ("heuristic+tuned", "tuned")
    if strategy is SolveStrategy.WARM_TUNED:
        if mode == "codeedit":
            return ("direct+tuned", "direct+heuristic+tuned")
        return ("direct+heuristic+tuned", "direct+tuned")
    return ()


def _build_exam_decision(
    label: str,
    *,
    policy_name: str,
    rationale: str,
    metadata: dict[str, Any] | None = None,
    allowed_labels: list[str] | None = None,
    raw_response: str = "",
    prompt: list[dict[str, str]] | None = None,
    raw_toolbox_plan: list[str] | None = None,
) -> StrategySelectionDecision:
    normalized = normalize_exam_execution_label(label)
    if allowed_labels is not None and normalized not in allowed_labels:
        raise ValueError(
            f"Unsupported exam execution label {label!r}; allowed: {allowed_labels}"
        )
    spec = execution_spec_for_exam_label(normalized)
    if raw_toolbox_plan:
        normalized_toolbox = _normalize_toolbox_plan(raw_toolbox_plan, allowed=list(spec.toolbox_plan))
        if normalized_toolbox != list(spec.toolbox_plan):
            raise ValueError(
                f"Exam execution label {normalized!r} requires toolbox plan {list(spec.toolbox_plan)}, "
                f"got {normalized_toolbox}"
            )
    return StrategySelectionDecision(
        strategy=spec.coarse_strategy,
        policy_name=policy_name,
        execution_label=spec.label,
        toolbox_plan=list(spec.toolbox_plan),
        rationale=rationale,
        metadata=dict(metadata or {}),
        raw_response=raw_response,
        prompt=list(prompt or []),
    )


def _uses_exam_execution_labels(spec: ProblemSpec) -> bool:
    return spec.metadata.problem_id == "exam_block_seq"


def _planner_mode_for_selection(spec: ProblemSpec, planner_output: PlannerOutput) -> str:
    config = spec.config_metadata.get("config") or {}
    raw_mode = (
        planner_output.annotations.get("planner_mode")
        or planner_output.planning_hints.get("planner_mode")
        or spec.config_metadata.get("planner_mode")
        or config.get("planner_mode")
        or spec.capabilities.get("planner_mode")
        or PATCHEDIT_MODE
    )
    return normalize_planner_mode(raw_mode)


def _heuristic_warm_start_available(
    spec: ProblemSpec,
    planner_output: PlannerOutput,
    *,
    planner_mode: str,
) -> bool:
    if not _uses_exam_execution_labels(spec):
        return "heuristic_warm_start" in _configured_toolbox_items(spec)
    if planner_mode == "codeedit":
        return bool(planner_output.annotations.get("heuristic_warm_start_available"))
    return True


def _supports_warm(spec: ProblemSpec) -> bool:
    return bool(spec.capabilities.get("supports_warm_start"))


def _supports_tuned(spec: ProblemSpec) -> bool:
    return bool(spec.capabilities.get("supports_tuned_solver"))
