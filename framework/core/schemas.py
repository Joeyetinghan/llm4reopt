"""Typed framework schemas."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping

from .patches import Patch


def _serialize(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Patch):
        return _serialize(value.describe())
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _serialize(getattr(value, field.name)) for field in fields(value)}
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _serialize(value.to_dict())
    if hasattr(value, "describe") and callable(value.describe):
        return _serialize(value.describe())
    if isinstance(value, dict):
        return {str(key): _serialize(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    if isinstance(value, set):
        return sorted((_serialize(item) for item in value), key=lambda item: str(item))
    return value


def _is_flat_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


class SolveStrategy(str, Enum):
    SCRATCH = "scratch"
    WARM = "warm"
    TUNED = "tuned"
    WARM_TUNED = "warm+tuned"


@dataclass
class SolveExecutionOptions:
    warm_start: Any | None = None
    solve_context: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "warm_start": _serialize(self.warm_start),
            "solve_context": _serialize(self.solve_context),
            "metadata": _serialize(self.metadata),
        }


@dataclass
class StrategySelectionDecision:
    strategy: SolveStrategy
    policy_name: str
    execution_label: str = ""
    toolbox_plan: list[str] = field(default_factory=list)
    rationale: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    fallback_used: bool = False
    fallback_reason: str = ""
    raw_response: str = ""
    prompt: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy.value,
            "execution_label": self.execution_label or self.strategy.value,
            "policy_name": self.policy_name,
            "toolbox_plan": list(self.toolbox_plan),
            "rationale": self.rationale,
            "metadata": _serialize(self.metadata),
            "fallback_used": self.fallback_used,
            "fallback_reason": self.fallback_reason,
            "raw_response": self.raw_response,
            "prompt": _serialize(self.prompt),
        }


@dataclass(frozen=True)
class ProblemMetadata:
    problem_id: str
    name: str
    description: str
    package_root: Path


@dataclass(frozen=True)
class EditArtifact:
    name: str
    source_type: str
    path: Path
    description: str = ""
    exported_artifacts: tuple[Path, ...] = ()
    read_only_artifacts: tuple[Path, ...] = ()
    baseline_solution_path: Path | None = None
    prompt_symbol: str | None = None


@dataclass(frozen=True)
class DataArtifact:
    name: str
    kind: str
    path: Path
    description: str = ""


@dataclass(frozen=True)
class ContextFile:
    name: str
    path: Path | None
    content: str
    description: str = ""


@dataclass(frozen=True)
class SourceArtifact:
    name: str
    kind: str
    path: Path | None
    content: str
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "path": str(self.path) if self.path is not None else None,
            "content": self.content,
            "description": self.description,
        }


@dataclass(frozen=True)
class ComponentDescriptor:
    name: str
    component_type: str
    summary: str
    tags: list[str] = field(default_factory=list)
    sample_members: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "component_type": self.component_type,
            "summary": self.summary,
            "tags": list(self.tags),
            "sample_members": list(self.sample_members),
            "metadata": _serialize(self.metadata),
        }


@dataclass(frozen=True)
class PatchSurfaceDescriptor:
    supported_ops: list[str]
    target_schemas: dict[str, str] = field(default_factory=dict)
    editable_concepts: list[str] = field(default_factory=list)
    structural_ops: list[str] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "supported_ops": list(self.supported_ops),
            "target_schemas": dict(self.target_schemas),
            "editable_concepts": list(self.editable_concepts),
            "structural_ops": list(self.structural_ops),
            "description": self.description,
        }


@dataclass
class ModelRepresentation:
    problem: ProblemMetadata
    representation_kind: str
    component_descriptors: list[ComponentDescriptor]
    patch_surface: PatchSurfaceDescriptor
    solver_capabilities: dict[str, Any]
    artifact_inventory: dict[str, Any]
    context_payload: str
    source_payloads: list[SourceArtifact] = field(default_factory=list)
    model_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "problem": {
                "problem_id": self.problem.problem_id,
                "name": self.problem.name,
                "description": self.problem.description,
                "package_root": str(self.problem.package_root),
            },
            "representation_kind": self.representation_kind,
            "component_descriptors": [descriptor.to_dict() for descriptor in self.component_descriptors],
            "patch_surface": self.patch_surface.to_dict(),
            "solver_capabilities": _serialize(self.solver_capabilities),
            "artifact_inventory": _serialize(self.artifact_inventory),
            "context_payload": self.context_payload,
            "source_payloads": [payload.to_dict() for payload in self.source_payloads],
            "model_summary": _serialize(self.model_summary),
        }

    def render_for_llm(self) -> str:
        sections = [f"Representation kind: {self.representation_kind}"]

        counts_line = _compact_model_counts(self.model_summary, self.component_descriptors)
        if counts_line:
            sections.append(f"Model counts: {counts_line}")

        solver_line = _compact_solver_capabilities(self.solver_capabilities)
        if solver_line:
            sections.append(f"Solver capabilities: {solver_line}")

        component_lines = _compact_component_lines(self.component_descriptors)
        if component_lines:
            sections.append("Component summaries:\n" + "\n".join(component_lines))

        if self.source_payloads:
            source_sections: list[str] = []
            for payload in self.source_payloads:
                header = f"{payload.name} ({payload.kind})"
                source_sections.append("\n".join([header, payload.content]).strip())
            sections.append("Source artifacts:\n" + "\n\n".join(source_sections))

        if self.context_payload:
            sections.append("Context payload:\n" + self.context_payload)

        return "\n\n".join(section for section in sections if section)

    def render_debug_markdown(self) -> str:
        sections = [
            f"Representation kind: {self.representation_kind}",
            "Model summary:",
            str(self.model_summary),
            "Patch surface:",
            str(self.patch_surface.to_dict()),
            "Solver capabilities:",
            str(self.solver_capabilities),
            "Artifact inventory:",
            str(self.artifact_inventory),
        ]

        if self.component_descriptors:
            sections.append("Component descriptors:")
            for descriptor in self.component_descriptors:
                sample_suffix = (
                    f" samples={descriptor.sample_members}"
                    if descriptor.sample_members
                    else ""
                )
                tag_suffix = f" tags={descriptor.tags}" if descriptor.tags else ""
                sections.append(
                    f"- [{descriptor.component_type}] {descriptor.name}: {descriptor.summary}"
                    f"{tag_suffix}{sample_suffix}"
                )

        if self.source_payloads:
            sections.append("Source artifacts:")
            for payload in self.source_payloads:
                header = f"{payload.name} ({payload.kind})"
                if payload.path is not None:
                    header += f" @ {payload.path}"
                sections.extend([header, payload.content])

        if self.context_payload:
            sections.extend(["Context payload:", self.context_payload])

        return "\n\n".join(section for section in sections if section)

    def render_markdown(self) -> str:
        return self.render_debug_markdown()


@dataclass(frozen=True)
class GroundTruthArtifact:
    name: str
    artifact_type: str
    path: Path
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "artifact_type": self.artifact_type,
            "path": str(self.path),
            "description": self.description,
        }


@dataclass(frozen=True)
class GroundTruthCase:
    case_id: str
    description: str = ""
    delta_text: str = ""
    expected_patch_ops: list[str] = field(default_factory=list)
    checker: str | None = None
    reference_artifact: str | None = None
    active: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "description": self.description,
            "delta_text": self.delta_text,
            "expected_patch_ops": list(self.expected_patch_ops),
            "checker": self.checker,
            "reference_artifact": self.reference_artifact,
            "active": self.active,
            "metadata": _serialize(self.metadata),
        }


@dataclass
class GroundTruthBundle:
    evaluator_path: str
    cases_path: Path | None = None
    cases: list[GroundTruthCase] = field(default_factory=list)
    artifacts: list[GroundTruthArtifact] = field(default_factory=list)
    status: str = "active"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluator_path": self.evaluator_path,
            "cases_path": str(self.cases_path) if self.cases_path is not None else None,
            "cases": [case.to_dict() for case in self.cases],
            "artifacts": [_serialize(artifact) for artifact in self.artifacts],
            "status": self.status,
            "metadata": _serialize(self.metadata),
        }


@dataclass
class DataBundle:
    artifacts: list[DataArtifact] = field(default_factory=list)


@dataclass
class ContextBundle:
    problem_context: ContextFile
    data_context: ContextFile
    extras: list[ContextFile] = field(default_factory=list)
    examples_context: list[dict[str, Any]] = field(default_factory=list)
    example_store_path: Path | None = None

    def documents(self) -> list[ContextFile]:
        return [self.problem_context, self.data_context, *self.extras]


@dataclass
class DeltaRequest:
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    requested_edit_mode: str = "patch"


@dataclass
class PlannedActionSet:
    action_kind: str
    actions: list[Any]
    label: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_kind": self.action_kind,
            "actions": _serialize(self.actions),
            "label": self.label,
            "metadata": _serialize(self.metadata),
        }


@dataclass
class PlannerOutput:
    edit_summary: str
    affected_sets: dict[str, Any]
    relevant_components: list[str]
    action_kind: str
    candidate_actions: list[Any]
    candidate_action_sets: list[PlannedActionSet] = field(default_factory=list)
    planning_hints: dict[str, Any] = field(default_factory=dict)
    raw_response: str = ""
    annotations: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "edit_summary": self.edit_summary,
            "affected_sets": _serialize(self.affected_sets),
            "relevant_components": list(self.relevant_components),
            "action_kind": self.action_kind,
            "candidate_actions": _serialize(self.candidate_actions),
            "candidate_action_sets": _serialize(self.candidate_action_sets),
            "planning_hints": _serialize(self.planning_hints),
            "raw_response": self.raw_response,
            "annotations": _serialize(self.annotations),
        }


@dataclass
class PromptContext:
    problem: ProblemMetadata
    delta_request: DeltaRequest
    model_representation: ModelRepresentation
    supported_patch_ops: list[str]
    selected_examples: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ProblemSpec:
    metadata: ProblemMetadata
    manifest_path: Path | None
    adapter_path: str
    patchedit_artifacts: list[EditArtifact]
    data_bundle: DataBundle
    context_bundle: ContextBundle
    ground_truth: GroundTruthBundle | None = None
    config_metadata: dict[str, Any] = field(default_factory=dict)
    capabilities: dict[str, Any] = field(default_factory=dict)
    source_kind: str = "package"
    codeedit_artifacts: list[EditArtifact] = field(default_factory=list)

    def artifacts_for_mode(self, mode: str) -> list[EditArtifact]:
        normalized = mode.strip().lower().replace("_", "-")
        if normalized in {"codeedit", "code-edit", "aider"}:
            return list(self.codeedit_artifacts)
        return list(self.patchedit_artifacts)


@dataclass
class CodeEditState:
    package_root: Path
    codeedit_source_type: str = "python_builder"
    config_mapping: dict[str, Any] = field(default_factory=dict)
    runtime_data: dict[str, Any] = field(default_factory=dict)
    summary_metadata: dict[str, Any] = field(default_factory=dict)
    artifact_paths: dict[str, Any] = field(default_factory=dict)
    last_solve_meta: dict[str, Any] = field(default_factory=dict)

    def describe(self) -> dict[str, Any]:
        return {
            "state_kind": "codeedit_solver",
            "package_root": str(self.package_root),
            "codeedit_source_type": self.codeedit_source_type,
            "runtime_keys": sorted(str(key) for key in self.runtime_data.keys()),
            "summary_keys": sorted(str(key) for key in self.summary_metadata.keys()),
            "artifact_paths": _serialize(self.artifact_paths),
            "last_solve_meta": _serialize(self.last_solve_meta),
        }


@dataclass
class EvaluationSummary:
    status: Any | None
    runtime: float | None
    objective: float | None
    feasible: bool | None
    checks: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)
    ground_truth_case_id: str | None = None
    matches_ground_truth: bool | None = None
    ground_truth_checks: dict[str, Any] = field(default_factory=dict)
    ground_truth_details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": _serialize(self.status),
            "runtime": self.runtime,
            "objective": self.objective,
            "feasible": self.feasible,
            "checks": _serialize(self.checks),
            "details": _serialize(self.details),
            "ground_truth_case_id": self.ground_truth_case_id,
            "matches_ground_truth": self.matches_ground_truth,
            "ground_truth_checks": _serialize(self.ground_truth_checks),
            "ground_truth_details": _serialize(self.ground_truth_details),
        }


def _compact_model_counts(
    model_summary: Mapping[str, Any],
    descriptors: Iterable[ComponentDescriptor],
) -> str:
    descriptor_counts = {
        "variable_family": 0,
        "constraint_family": 0,
        "objective_component": 0,
        "parameter": 0,
    }
    for descriptor in descriptors:
        if descriptor.component_type in descriptor_counts:
            descriptor_counts[descriptor.component_type] += 1

    variables = model_summary.get("n_variables")
    constraints = model_summary.get("n_constraints")
    objectives = model_summary.get("n_objectives")
    parameters = model_summary.get("n_parameters")
    if not isinstance(variables, int):
        variables = descriptor_counts["variable_family"]
    if not isinstance(constraints, int):
        constraints = descriptor_counts["constraint_family"]
    if not isinstance(objectives, int):
        objectives = descriptor_counts["objective_component"]
    if not isinstance(parameters, int):
        parameters = descriptor_counts["parameter"]

    parts = [
        f"variables={variables}",
        f"constraints={constraints}",
        f"objectives={objectives}",
        f"parameters={parameters}",
    ]
    return ", ".join(parts)


def _compact_solver_capabilities(capabilities: Mapping[str, Any]) -> str:
    compact: dict[str, Any] = {}
    for key, value in capabilities.items():
        if value in (None, "", [], {}, False):
            continue
        if _looks_like_path_key(key):
            continue
        if isinstance(value, (str, int, float, bool)):
            compact[key] = value
    if not compact:
        return ""
    return json.dumps(compact, sort_keys=True)


def _compact_component_lines(descriptors: Iterable[ComponentDescriptor]) -> list[str]:
    lines: list[str] = []
    for descriptor in descriptors:
        if _omit_descriptor_from_compact_render(descriptor):
            continue

        summary = _truncate_text(" ".join(descriptor.summary.split()), 160)
        suffix_parts = _compact_descriptor_suffix_parts(descriptor)
        suffix = f" ({'; '.join(suffix_parts)})" if suffix_parts else ""
        lines.append(
            f"- [{descriptor.component_type}] {descriptor.name}: {summary}{suffix}"
        )
    return lines


def _compact_descriptor_suffix_parts(descriptor: ComponentDescriptor) -> list[str]:
    parts: list[str] = []
    metadata = descriptor.metadata or {}

    if descriptor.component_type == "variable_family":
        var_type = metadata.get("var_type")
        if isinstance(var_type, str) and var_type:
            parts.append(f"type={var_type}")
    elif descriptor.component_type == "constraint_family":
        sense = metadata.get("sense")
        if isinstance(sense, str) and sense:
            parts.append(f"sense={sense}")
    elif descriptor.component_type == "objective_component":
        weight = metadata.get("weight")
        if isinstance(weight, (int, float)):
            parts.append(f"weight={weight}")
    elif descriptor.component_type == "parameter":
        preview = metadata.get("value_preview")
        if isinstance(preview, (str, int, float, bool)) and not _looks_path_like_value(preview):
            parts.append(f"value={preview}")
        elif isinstance(preview, list) and preview and len(preview) <= 4:
            preview_text = ", ".join(str(item) for item in preview)
            if len(preview_text) <= 60:
                parts.append(f"preview={preview_text}")
        value_count = metadata.get("value_count")
        if isinstance(value_count, int) and value_count > 4:
            parts.append(f"count={value_count}")
        value_min = metadata.get("value_min")
        value_max = metadata.get("value_max")
        if isinstance(value_min, (int, float)) and isinstance(value_max, (int, float)):
            parts.append(f"range={value_min}..{value_max}")

    if descriptor.tags:
        tag_text = ",".join(sorted(descriptor.tags))
        if len(tag_text) <= 40:
            parts.append(f"tags={tag_text}")

    if descriptor.sample_members and len(descriptor.sample_members) <= 2:
        sample_text = ", ".join(str(member) for member in descriptor.sample_members)
        if len(sample_text) <= 40:
            parts.append(f"examples={sample_text}")

    return parts


def _omit_descriptor_from_compact_render(descriptor: ComponentDescriptor) -> bool:
    if descriptor.component_type != "parameter":
        return False
    name = descriptor.name.lower()
    if (
        name.endswith("_path")
        or name.endswith("_file")
        or name == "warm_start"
        or "artifact" in name
        or "inventory" in name
        or "snippet" in name
    ):
        return True
    if name.startswith("supported_") or name.startswith("base_"):
        return True
    preview = (descriptor.metadata or {}).get("value_preview")
    return _looks_path_like_value(preview)


def _looks_like_path_key(key: str) -> bool:
    lowered = key.lower()
    return lowered.endswith("_path") or lowered.endswith("_file") or "directory" in lowered


def _looks_path_like_value(value: Any) -> bool:
    if isinstance(value, str):
        lowered = value.lower()
        return "/" in value or lowered.endswith(
            (".lp", ".json", ".yaml", ".yml", ".csv", ".sol", ".log", ".prm", ".py")
        )
    return False


def _truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


@dataclass
class GroundTruthModeResult:
    mode: str
    status: str
    matches: bool | None
    checks: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "status": self.status,
            "matches": self.matches,
            "checks": _serialize(self.checks),
            "details": _serialize(self.details),
        }


@dataclass
class GroundTruthCheckResult:
    problem_id: str
    case_id: str | None
    result_path: str | None
    status: str
    matched_case: bool
    matches_ground_truth: bool | None
    checks: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)
    mode_results: dict[str, GroundTruthModeResult] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "problem_id": self.problem_id,
            "case_id": self.case_id,
            "result_path": self.result_path,
            "status": self.status,
            "matched_case": self.matched_case,
            "matches_ground_truth": self.matches_ground_truth,
            "checks": _serialize(self.checks),
            "details": _serialize(self.details),
            "mode_results": _serialize(self.mode_results),
        }
        for key, value in self.checks.items():
            if key in payload or not _is_flat_scalar(value):
                continue
            payload[str(key)] = _serialize(value)
        return payload


@dataclass
class ReoptResult:
    delta_request: DeltaRequest
    prompt_context: PromptContext
    strategy: SolveStrategy
    strategy_selection: StrategySelectionDecision | None
    event: dict[str, Any]
    relevant_components: list[str]
    candidate_patches: list[Patch]
    chosen_patches: list[Patch]
    objective: float
    solution: Any
    solve_meta: dict[str, Any]
    evaluation: EvaluationSummary
    change_report: str
    action_kind: str = "patch"
    planner_output: dict[str, Any] = field(default_factory=dict)
    model_summary: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "delta_request": _serialize(self.delta_request.text),
            "strategy": self.strategy.value,
            "strategy_selection": _serialize(self.strategy_selection),
            "event": _serialize(self.event),
            "relevant_components": list(self.relevant_components),
            "action_kind": self.action_kind,
            "planner_output": _serialize(self.planner_output),
            "candidate_patches": _serialize(self.candidate_patches),
            "chosen_patches": _serialize(self.chosen_patches),
            "objective": self.objective,
            "solution": _serialize(self.solution),
            "solve_meta": _serialize(self.solve_meta),
            "evaluation": self.evaluation.to_dict(),
            "change_report": self.change_report,
            "model_summary": _serialize(self.model_summary),
            "artifacts": _serialize(self.artifacts),
        }


@dataclass
class ProblemRunResult:
    problem: ProblemMetadata
    mode: str
    base_objective: float
    base_solution: Any
    base_solve_meta: dict[str, Any]
    base_evaluation: EvaluationSummary
    steps: list[ReoptResult] = field(default_factory=list)

    @property
    def final_result(self) -> ReoptResult | None:
        return self.steps[-1] if self.steps else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "problem": {
                "problem_id": self.problem.problem_id,
                "name": self.problem.name,
                "description": self.problem.description,
                "package_root": str(self.problem.package_root),
            },
            "mode": self.mode,
            "base_objective": self.base_objective,
            "base_solution": _serialize(self.base_solution),
            "base_solve_meta": _serialize(self.base_solve_meta),
            "base_evaluation": self.base_evaluation.to_dict(),
            "steps": [step.to_dict() for step in self.steps],
        }


def select_latest_examples(
    examples: Iterable[Mapping[str, Any]],
    *,
    limit: int = 3,
    problem_id: str | None = None,
) -> list[dict[str, Any]]:
    filtered = []
    for example in examples:
        if problem_id is None or example.get("problem_id") in {None, problem_id}:
            filtered.append(dict(example))
    return filtered[-limit:]
