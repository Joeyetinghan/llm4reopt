"""Exam-only execution labels built from primitive reoptimization tools."""

from __future__ import annotations

from dataclasses import dataclass

from framework.core import SolveStrategy

DIRECT_WARM_START_TOOL = "direct_warm_start"
HEURISTIC_WARM_START_TOOL = "heuristic_warm_start"
TUNED_CONFIG_TOOL = "tuned_config"

SCRATCH_EXECUTION_LABEL = "scratch"

_TOOL_ORDER = (
    DIRECT_WARM_START_TOOL,
    HEURISTIC_WARM_START_TOOL,
    TUNED_CONFIG_TOOL,
)
_TOKEN_TO_TOOL = {
    "direct": DIRECT_WARM_START_TOOL,
    "heuristic": HEURISTIC_WARM_START_TOOL,
    "tuned": TUNED_CONFIG_TOOL,
}
_TOOL_TO_TOKEN = {tool: token for token, tool in _TOKEN_TO_TOOL.items()}


@dataclass(frozen=True)
class ExamExecutionLabelSpec:
    label: str
    toolbox_plan: tuple[str, ...]
    coarse_strategy: SolveStrategy


def normalize_exam_execution_label(raw: str) -> str:
    candidate = str(raw or "").strip().lower().replace("_", "+")
    if candidate == SCRATCH_EXECUTION_LABEL:
        return candidate
    tokens = [token.strip() for token in candidate.split("+") if token.strip()]
    if not tokens:
        raise ValueError("Exam execution label cannot be empty")
    tools: list[str] = []
    for token in tokens:
        tool = _TOKEN_TO_TOOL.get(token)
        if tool is None:
            raise ValueError(f"Unsupported exam execution label token: {token!r}")
        if tool not in tools:
            tools.append(tool)
    if not tools:
        raise ValueError(f"Unsupported exam execution label: {raw!r}")
    return "+".join(_TOOL_TO_TOKEN[tool] for tool in _TOOL_ORDER if tool in tools)


def is_exam_execution_label(raw: str) -> bool:
    try:
        normalize_exam_execution_label(raw)
    except ValueError:
        return False
    return True


def toolbox_plan_for_exam_execution_label(label: str) -> list[str]:
    normalized = normalize_exam_execution_label(label)
    if normalized == SCRATCH_EXECUTION_LABEL:
        return []
    tokens = normalized.split("+")
    return [_TOKEN_TO_TOOL[token] for token in tokens]


def coarse_strategy_for_exam_execution_label(label: str) -> SolveStrategy:
    toolbox = set(toolbox_plan_for_exam_execution_label(label))
    has_direct = DIRECT_WARM_START_TOOL in toolbox
    has_tuned = TUNED_CONFIG_TOOL in toolbox
    if has_direct and has_tuned:
        return SolveStrategy.WARM_TUNED
    if has_direct:
        return SolveStrategy.WARM
    if has_tuned:
        return SolveStrategy.TUNED
    return SolveStrategy.SCRATCH


def execution_spec_for_exam_label(label: str) -> ExamExecutionLabelSpec:
    normalized = normalize_exam_execution_label(label)
    return ExamExecutionLabelSpec(
        label=normalized,
        toolbox_plan=tuple(toolbox_plan_for_exam_execution_label(normalized)),
        coarse_strategy=coarse_strategy_for_exam_execution_label(normalized),
    )


def allowed_exam_execution_labels(
    *,
    direct_available: bool,
    heuristic_available: bool,
    tuned_available: bool,
    include_scratch: bool = False,
) -> list[str]:
    available_tools = {
        tool
        for tool, enabled in (
            (DIRECT_WARM_START_TOOL, direct_available),
            (HEURISTIC_WARM_START_TOOL, heuristic_available),
            (TUNED_CONFIG_TOOL, tuned_available),
        )
        if enabled
    }
    labels: list[str] = []
    for label in (
        "direct",
        "heuristic",
        "tuned",
        "direct+heuristic",
        "direct+tuned",
        "heuristic+tuned",
        "direct+heuristic+tuned",
    ):
        tools = set(toolbox_plan_for_exam_execution_label(label))
        if tools.issubset(available_tools):
            labels.append(label)
    if include_scratch:
        labels.append(SCRATCH_EXECUTION_LABEL)
    return labels


def default_exam_execution_label_for_planner_mode(
    planner_mode: str,
    *,
    allowed_labels: list[str],
) -> str:
    preferred = (
        "direct+tuned"
        if str(planner_mode).strip().lower() == "codeedit"
        else "direct+heuristic+tuned"
    )
    if preferred in allowed_labels:
        return preferred
    fallback_order = [
        "direct+heuristic+tuned",
        "direct+tuned",
        "heuristic+tuned",
        "direct+heuristic",
        "direct",
        "heuristic",
        "tuned",
        SCRATCH_EXECUTION_LABEL,
    ]
    for label in fallback_order:
        if label in allowed_labels:
            return label
    raise ValueError("No allowed exam execution labels were available")


