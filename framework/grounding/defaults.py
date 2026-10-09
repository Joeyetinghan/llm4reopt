"""Default deterministic model-grounder implementations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from framework.core import (
    BaseEnv,
    BaseGrounder,
    CodeEditState,
    ComponentDescriptor,
    ModelRepresentation,
    PatchOp,
    PatchSurfaceDescriptor,
    ProblemSpec,
    SourceArtifact,
    StructuredModel,
)
from framework.lp import (
    render_inventory_markdown,
    render_lp_patch_examples_markdown,
    render_lp_snippets_markdown,
    summarize_lp_model,
)
from framework.prompting.context import extract_python_symbol


_PATCH_TARGET_SCHEMAS = {
    PatchOp.UPDATE_PARAMETER.value: "target={'name': <parameter_name>}, update={'value': <any>, 'key': <optional index>} or {'delta': <float>, 'key': <optional index>}",
    PatchOp.UPDATE_BOUND.value: "target={'variable': <family_name>}, update={'index': <idx>, 'bound': 'upper|lower', 'value': <float>}",
    PatchOp.UPDATE_CONSTRAINT_RHS.value: "target={'constraint': <family_name>}, update={'index': <idx>, 'value': <float>} or {'index': <idx>, 'delta': <float>}",
    PatchOp.UPDATE_CONSTRAINT_LHS.value: (
        "target={'constraint': <family_name>}, update={'lhs_spec': <replacement spec>} "
        "where lhs_spec may be materialized or a representation-supported semantic kind"
    ),
    PatchOp.UPDATE_OBJECTIVE_COEFF.value: "target={'objective': <name>}, update={'index': <idx>, 'value': <float>} or {'index': <idx>, 'delta': <float>}",
    PatchOp.UPDATE_OBJECTIVE_WEIGHT.value: "target={'objective': <name>}, update={'weight': <float>} or {'delta': <float>}",
    PatchOp.ADD_CONSTRAINT_FAMILY.value: (
        "update={'constraint': {'name': <family_name>, 'index_set': [...], "
        "'lhs_spec': {'kind': <lhs_kind>, ...}, "
        "'rhs_spec': <float or {row_idx: float}>, 'sense': '<=|=|>='}}"
    ),
    PatchOp.REMOVE_CONSTRAINT_FAMILY.value: "target={'constraint': <family_name>}",
    PatchOp.ADD_OBJECTIVE_COMPONENT.value: "update={'objective': <ObjectiveComponent>}",
    PatchOp.ADD_VARIABLE_FAMILY.value: "update={'variable_family': <VariableFamily>}",
    PatchOp.FIX_VARIABLES_BY_PATTERN.value: "target={'pattern': <regex>}, scope={'filters': {...}}, update={'lb': <float>, 'ub': <float>}",
    PatchOp.UPDATE_CONSTRAINT_RHS_BY_PATTERN.value: "target={'pattern': <regex>}, update={'value': <float>} or {'factor': <float>}",
    PatchOp.UPDATE_COEFFICIENT.value: "target={'variable_pattern': <regex>, 'constraint_pattern': <regex>}, update={'delta': <float>} or {'value': <float>}",
}

_STRUCTURAL_OPS = {
    PatchOp.UPDATE_CONSTRAINT_LHS.value,
    PatchOp.ADD_CONSTRAINT_FAMILY.value,
    PatchOp.REMOVE_CONSTRAINT_FAMILY.value,
    PatchOp.ADD_OBJECTIVE_COMPONENT.value,
    PatchOp.ADD_VARIABLE_FAMILY.value,
}


class DefaultPythonModelGrounder(BaseGrounder):
    def __init__(self, supported_ops: list[PatchOp]):
        self.supported_ops = list(supported_ops)

    def ground(
        self,
        spec: ProblemSpec,
        env: BaseEnv,
        model: StructuredModel,
        *,
        delta_request=None,
    ) -> ModelRepresentation:
        del env, delta_request
        source_payloads = _build_source_payloads(
            spec,
            artifacts=spec.patchedit_artifacts,
            include_python=True,
        )
        return ModelRepresentation(
            problem=spec.metadata,
            representation_kind="python_builder",
            component_descriptors=build_component_descriptors(model),
            patch_surface=build_patch_surface_descriptor(
                self.supported_ops,
                model=model,
                representation_kind="python_builder",
            ),
            solver_capabilities=_solver_capabilities(spec, model),
            artifact_inventory=_artifact_inventory(spec, model),
            context_payload=_context_payload(spec),
            source_payloads=source_payloads + _extra_source_payloads(model),
            model_summary=model.describe() if hasattr(model, "describe") else {},
        )


class DefaultCodeEditGrounder(BaseGrounder):
    def __init__(self, supported_ops: list[PatchOp]):
        self.supported_ops = list(supported_ops)

    def ground(
        self,
        spec: ProblemSpec,
        env: BaseEnv,
        model,
        *,
        delta_request=None,
    ) -> ModelRepresentation:
        state = model if isinstance(model, CodeEditState) else None
        source_payloads = _build_codeedit_source_payloads(spec, env, model, state=state)
        if state is not None:
            component_descriptors = build_summary_descriptors(state.summary_metadata)
            model_summary = _codeedit_model_summary(spec, env, state)
            solver_capabilities = _codeedit_solver_capabilities(spec, state)
        elif isinstance(model, StructuredModel):
            component_descriptors = build_component_descriptors(model)
            model_summary = model.describe() if hasattr(model, "describe") else {}
            solver_capabilities = _solver_capabilities(spec, model)
        else:
            component_descriptors = []
            model_summary = {}
            solver_capabilities = {
                "solver_backend": spec.capabilities.get("solver_backend"),
                "supports_warm_start": bool(spec.capabilities.get("supports_warm_start")),
                "supports_tuned_solver": bool(spec.capabilities.get("supports_tuned_solver")),
                "source_kind": spec.source_kind,
            }

        return ModelRepresentation(
            problem=spec.metadata,
            representation_kind=_codeedit_representation_kind(spec),
            component_descriptors=component_descriptors,
            patch_surface=PatchSurfaceDescriptor(
                supported_ops=[],
                description="Direct solver-code editing surface; patch operators are not used here.",
            ),
            solver_capabilities=solver_capabilities,
            artifact_inventory=_artifact_inventory(spec, model, mode="codeedit"),
            context_payload=_context_payload(spec),
            source_payloads=source_payloads,
            model_summary=model_summary,
        )


class DefaultLPModelGrounder(BaseGrounder):
    def __init__(self, supported_ops: list[PatchOp]):
        self.supported_ops = list(supported_ops)

    def ground(
        self,
        spec: ProblemSpec,
        env: BaseEnv,
        model: StructuredModel,
        *,
        delta_request=None,
    ) -> ModelRepresentation:
        del delta_request
        artifact_inventory = _artifact_inventory(spec, model)
        if hasattr(env, "load_gurobi_model"):
            try:
                lp_inventory = summarize_lp_model(env.load_gurobi_model())
            except Exception:
                lp_inventory = model.parameters.get("lp_inventory") or {}
        else:
            lp_inventory = model.parameters.get("lp_inventory") or {}
        artifact_inventory = {**artifact_inventory, "lp_inventory": lp_inventory}

        source_payloads: list[SourceArtifact] = []
        if lp_inventory:
            source_payloads.append(
                SourceArtifact(
                    name="lp_inventory",
                    kind="lp_inventory",
                    path=None,
                    description="Deterministic LP family inventory derived from the loaded model.",
                    content=render_inventory_markdown(lp_inventory),
                )
            )
        source_payloads.extend(_extra_source_payloads(model))

        return ModelRepresentation(
            problem=spec.metadata,
            representation_kind="lp_model",
            component_descriptors=build_component_descriptors(model),
            patch_surface=build_patch_surface_descriptor(
                self.supported_ops,
                model=model,
                representation_kind="lp_model",
            ),
            solver_capabilities=_solver_capabilities(spec, model),
            artifact_inventory=artifact_inventory,
            context_payload=_context_payload(spec),
            source_payloads=source_payloads,
            model_summary=model.describe() if hasattr(model, "describe") else {},
        )


def build_component_descriptors(model: StructuredModel) -> list[ComponentDescriptor]:
    descriptors: list[ComponentDescriptor] = []

    for name, family in model.variables.items():
        descriptors.append(
            ComponentDescriptor(
                name=name,
                component_type="variable_family",
                summary=family.desc or f"Variable family {name}",
                tags=sorted(family.tags),
                sample_members=[str(item) for item in family.index_set[:5]],
                metadata={"var_type": family.var_type.value},
            )
        )
        if family.aliases:
            descriptors[-1].metadata["aliases"] = sorted(family.aliases)
        if family.metadata:
            descriptors[-1].metadata.update(_safe_metadata(family.metadata))

    for name, family in model.constraints.items():
        sample_members = [str(item) for item in family.index_set[:5]]
        rhs_preview: Any = family.rhs_spec
        if isinstance(rhs_preview, dict):
            rhs_preview = {str(k): rhs_preview[k] for k in list(rhs_preview)[:5]}
        descriptors.append(
            ComponentDescriptor(
                name=name,
                component_type="constraint_family",
                summary=family.desc or f"Constraint family {name}",
                tags=sorted(family.tags),
                sample_members=sample_members,
                metadata={"sense": family.sense, "rhs_preview": rhs_preview},
            )
        )
        if isinstance(family.lhs_spec, dict) and family.lhs_spec.get("kind") is not None:
            descriptors[-1].metadata["lhs_kind"] = str(family.lhs_spec.get("kind"))
        if family.aliases:
            descriptors[-1].metadata["aliases"] = sorted(family.aliases)
        if family.metadata:
            descriptors[-1].metadata.update(_safe_metadata(family.metadata))

    for name, obj in model.objectives.items():
        descriptors.append(
            ComponentDescriptor(
                name=name,
                component_type="objective_component",
                summary=obj.desc or f"Objective component {name}",
                tags=sorted(obj.tags),
                metadata={"weight": obj.weight, "spec": obj.spec},
            )
        )
        if obj.aliases:
            descriptors[-1].metadata["aliases"] = sorted(obj.aliases)
        if obj.metadata:
            descriptors[-1].metadata.update(_safe_metadata(obj.metadata))

    if model.parameters:
        for key, value in model.parameters.items():
            info = model.parameter_info.get(str(key))
            descriptors.append(
                ComponentDescriptor(
                    name=str(key),
                    component_type="parameter",
                    summary=info.desc if info is not None and info.desc else _parameter_summary(key, value),
                    tags=sorted(info.tags) if info is not None else [],
                    sample_members=_sample_parameter_members(value),
                    metadata=_parameter_descriptor_metadata(value, info),
                )
            )

    return descriptors


def build_patch_surface_descriptor(
    supported_ops: list[PatchOp],
    *,
    model: StructuredModel,
    representation_kind: str,
) -> PatchSurfaceDescriptor:
    supported_op_names = [patch_op.value for patch_op in supported_ops]
    editable_concepts: list[str] = list(model.parameters.keys())
    editable_concepts.extend(list(model.constraints.keys()))
    editable_concepts.extend(list(model.objectives.keys()))
    editable_concepts.extend(list(model.variables.keys()))
    if representation_kind == "lp_model":
        editable_concepts.extend(
            str(name) for name in model.parameters.get("supported_lp_patch_ops", [])
        )
    seen = set()
    deduped_concepts = []
    for concept in editable_concepts:
        if concept in seen:
            continue
        seen.add(concept)
        deduped_concepts.append(concept)

    return PatchSurfaceDescriptor(
        supported_ops=supported_op_names,
        target_schemas={
            op_name: _PATCH_TARGET_SCHEMAS[op_name]
            for op_name in supported_op_names
            if op_name in _PATCH_TARGET_SCHEMAS
        },
        editable_concepts=deduped_concepts,
        structural_ops=[op_name for op_name in supported_op_names if op_name in _STRUCTURAL_OPS],
        description="Deterministic patch surface derived from adapter-supported operations.",
    )


def _build_source_payloads(
    spec: ProblemSpec,
    *,
    artifacts,
    include_python: bool,
) -> list[SourceArtifact]:
    payloads: list[SourceArtifact] = []
    seen_paths: set[Path] = set()

    def _append_payload(
        *,
        name: str,
        source_type: str,
        path: Path,
        description: str,
        prompt_symbol: str | None = None,
    ) -> None:
        if include_python and source_type not in {"python_builder", "python_model"}:
            return
        if path in seen_paths or not path.exists() or not path.is_file():
            return
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return
        prompt_content = content.rstrip()
        if prompt_symbol:
            extracted = extract_python_symbol(content, prompt_symbol)
            if extracted:
                prompt_content = extracted.rstrip()
        payloads.append(
            SourceArtifact(
                name=name,
                kind=source_type,
                path=path,
                description=description,
                content=f"```python\n{prompt_content}\n```",
            )
        )
        seen_paths.add(path)

    for artifact in artifacts:
        _append_payload(
            name=artifact.name,
            source_type=artifact.source_type,
            path=artifact.path,
            description=artifact.description,
            prompt_symbol=artifact.prompt_symbol,
        )
        for extra_path in artifact.exported_artifacts:
            _append_payload(
                name=f"{artifact.name}_{extra_path.stem}",
                source_type=artifact.source_type,
                path=extra_path,
                description=f"Supporting source context exported by {artifact.name}.",
            )
    return payloads


def _build_codeedit_source_payloads(
    spec: ProblemSpec,
    env: BaseEnv,
    model,
    *,
    state: CodeEditState | None,
) -> list[SourceArtifact]:
    primary_kind = spec.codeedit_artifacts[0].source_type if spec.codeedit_artifacts else None
    if primary_kind != "lp_file":
        return _build_source_payloads(
            spec,
            artifacts=spec.codeedit_artifacts,
            include_python=True,
        )

    payloads: list[SourceArtifact] = []
    artifact_paths = dict(state.artifact_paths) if state is not None else {}
    if not artifact_paths and isinstance(model, StructuredModel):
        payloads.extend(_lp_inventory_payloads(model.parameters.get("lp_inventory"), model.extras.get("lp_snippets")))
        return payloads

    payloads.extend(_workspace_artifact_payloads(artifact_paths))
    if payloads:
        return payloads

    lp_inventory = None
    lp_snippets = None
    if hasattr(env, "load_gurobi_model"):
        try:
            lp_inventory = summarize_lp_model(env.load_gurobi_model())
            lp_snippets = lp_inventory.get("snippets")
        except Exception:
            lp_inventory = None
            lp_snippets = None
    payloads.extend(_lp_inventory_payloads(lp_inventory, lp_snippets))
    return payloads


def _context_payload(spec: ProblemSpec) -> str:
    sections: list[str] = []
    for document in spec.context_bundle.documents():
        if document.content:
            sections.extend([document.name, document.content])
    return "\n\n".join(sections)


def _solver_capabilities(spec: ProblemSpec, model: StructuredModel) -> dict[str, Any]:
    capabilities = {
        "solver_backend": spec.capabilities.get("solver_backend"),
        "supports_warm_start": bool(spec.capabilities.get("supports_warm_start")),
        "supports_tuned_solver": bool(spec.capabilities.get("supports_tuned_solver")),
        "has_base_warm_start": bool(spec.capabilities.get("has_base_warm_start")),
    }
    capabilities.update(_safe_metadata(model.supports))
    return capabilities


def _artifact_inventory(spec: ProblemSpec, model, *, mode: str = "patchedit") -> dict[str, Any]:
    inventory = _artifact_inventory_base(spec)
    if isinstance(model, StructuredModel):
        inventory.update(_safe_metadata(model.artifacts))
        if "lp_inventory" not in inventory and "lp_inventory" in model.extras:
            inventory["lp_inventory"] = _safe_metadata(model.extras["lp_inventory"])
    if isinstance(model, CodeEditState) and model.artifact_paths:
        inventory["codeedit_workspace_artifacts"] = _safe_metadata(model.artifact_paths)
    if mode == "codeedit":
        inventory["codeedit_mode"] = True
    return inventory


def _artifact_inventory_base(spec: ProblemSpec) -> dict[str, Any]:
    read_only_artifacts: list[str] = []
    for artifact in spec.codeedit_artifacts:
        read_only_artifacts.extend(str(path) for path in artifact.read_only_artifacts)
    if spec.config_metadata.get("loaded_data"):
        read_only_artifacts.append("runtime_snapshot.json")
    inventory: dict[str, Any] = {
        "patchedit_artifacts": [str(artifact.path) for artifact in spec.patchedit_artifacts],
        "codeedit_artifacts": [str(artifact.path) for artifact in spec.codeedit_artifacts],
        "codeedit_read_only_artifacts": read_only_artifacts,
        "data_artifacts": [str(artifact.path) for artifact in spec.data_bundle.artifacts],
    }

    config_sources = [
        spec.config_metadata.get("config") or {},
        spec.config_metadata.get("loaded_data") or {},
    ]
    artifact_candidates: dict[str, list[str]] = {
        "base_solution_candidates": [],
        "base_log_candidates": [],
        "tuned_param_candidates": [],
    }
    for source in config_sources:
        if not isinstance(source, dict):
            continue
        for key, value in source.items():
            lower_key = str(key).lower()
            for path_value in _flatten_candidate_paths(value):
                path_text = str(path_value)
                if any(token in lower_key for token in {"sol", "solution"}):
                    artifact_candidates["base_solution_candidates"].append(path_text)
                if "log" in lower_key:
                    artifact_candidates["base_log_candidates"].append(path_text)
                if "prm" in lower_key or "param" in lower_key:
                    artifact_candidates["tuned_param_candidates"].append(path_text)
    inventory.update(artifact_candidates)
    return inventory


def _extra_source_payloads(model: StructuredModel) -> list[SourceArtifact]:
    payloads: list[SourceArtifact] = []
    lp_snippets = model.extras.get("lp_snippets")
    if isinstance(lp_snippets, dict) and lp_snippets:
        payloads.append(
            SourceArtifact(
                name="lp_snippets",
                kind="lp_snippets",
                path=None,
                description="Representative LP rows and columns for key families.",
                content=render_lp_snippets_markdown(lp_snippets),
            )
        )
    lp_patch_examples = model.extras.get("lp_patch_examples")
    if isinstance(lp_patch_examples, dict) and lp_patch_examples:
        payloads.append(
            SourceArtifact(
                name="lp_patch_examples",
                kind="lp_patch_examples",
                path=None,
                description="Deterministic Gurobi-oriented patch examples for LP-backed edits.",
                content=render_lp_patch_examples_markdown(lp_patch_examples),
            )
        )
    return payloads


def _safe_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _safe_metadata(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_metadata(item) for item in value]
    if isinstance(value, set):
        return sorted(str(item) for item in value)
    if hasattr(value, "value"):
        return getattr(value, "value")
    return value


def _flatten_candidate_paths(value: Any) -> list[str]:
    if isinstance(value, (str, Path)):
        return [str(value)]
    if isinstance(value, dict):
        results: list[str] = []
        for nested in value.values():
            results.extend(_flatten_candidate_paths(nested))
        return results
    if isinstance(value, (list, tuple, set)):
        results = []
        for item in value:
            results.extend(_flatten_candidate_paths(item))
        return results
    return []


def _parameter_summary(name: object, value: Any) -> str:
    key = str(name)
    if isinstance(value, dict):
        return f"Indexed parameter {key} with {len(value)} entries."
    if isinstance(value, list):
        return f"List parameter {key} with {len(value)} values."
    return f"Scalar parameter {key}."


def _sample_parameter_members(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(key) for key in list(value.keys())[:5]]
    if isinstance(value, list):
        return [str(item) for item in value[:5]]
    return []


def _parameter_preview(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): value[key] for key in list(value.keys())[:5]}
    if isinstance(value, list):
        return value[:5]
    return value


def _parameter_descriptor_metadata(value: Any, info: Any) -> dict[str, Any]:
    metadata: dict[str, Any] = {"value_preview": _parameter_preview(value)}
    if info is not None and info.aliases:
        metadata["aliases"] = sorted(info.aliases)
    if info is not None:
        metadata.update(_safe_metadata(info.metadata))

    if isinstance(value, list):
        metadata["value_count"] = len(value)
        if value and all(isinstance(item, (int, float)) for item in value):
            metadata["value_min"] = int(min(value))
            metadata["value_max"] = int(max(value))
    elif isinstance(value, dict):
        metadata["value_count"] = len(value)

    return metadata


def build_summary_descriptors(summary_metadata: dict[str, Any]) -> list[ComponentDescriptor]:
    descriptors: list[ComponentDescriptor] = []
    for key, value in summary_metadata.items():
        descriptors.append(
            ComponentDescriptor(
                name=str(key),
                component_type="parameter",
                summary=_parameter_summary(key, value),
                sample_members=_sample_parameter_members(value),
                metadata={"value_preview": _parameter_preview(value)},
            )
        )
    return descriptors


def _codeedit_solver_capabilities(spec: ProblemSpec, state: CodeEditState) -> dict[str, Any]:
    capabilities = {
        "solver_backend": spec.capabilities.get("solver_backend"),
        "supports_warm_start": bool(spec.capabilities.get("supports_warm_start")),
        "supports_tuned_solver": bool(spec.capabilities.get("supports_tuned_solver")),
        "source_kind": spec.source_kind,
        "edited_package_root": str(state.package_root),
        "codeedit_source_type": state.codeedit_source_type,
    }
    if state.last_solve_meta:
        capabilities["last_solve_meta"] = _safe_metadata(state.last_solve_meta)
    if state.artifact_paths:
        capabilities["workspace_artifacts"] = _safe_metadata(state.artifact_paths)
    return capabilities


def _codeedit_representation_kind(spec: ProblemSpec) -> str:
    primary_kind = spec.codeedit_artifacts[0].source_type if spec.codeedit_artifacts else None
    if primary_kind == "lp_file":
        return "lp_wrapper_codeedit"
    return "codeedit_solver"


def _codeedit_model_summary(
    spec: ProblemSpec,
    env: BaseEnv,
    state: CodeEditState,
) -> dict[str, Any]:
    summary = state.describe()
    if state.codeedit_source_type != "lp_file":
        return summary

    lp_inventory = None
    if hasattr(env, "load_gurobi_model"):
        try:
            lp_inventory = summarize_lp_model(env.load_gurobi_model())
        except Exception:
            lp_inventory = None
    if isinstance(lp_inventory, dict):
        summary.update(
            {
                "n_variables": int(lp_inventory.get("num_variables", 0)),
                "n_constraints": int(lp_inventory.get("num_constraints", 0)),
                "n_objectives": 1,
                "lp_objective_sense": lp_inventory.get("objective_sense"),
            }
        )
    return summary


def _workspace_artifact_payloads(artifact_paths: dict[str, Any]) -> list[SourceArtifact]:
    payloads: list[SourceArtifact] = []
    mapping = [
        ("wrapper_path", "lp_codeedit_wrapper", "python_builder", "Editable generated LP codeedit wrapper."),
        ("lp_inventory_path", "lp_inventory", "lp_inventory", "Compact LP family inventory for the copied active LP."),
        ("lp_snippets_path", "lp_snippets", "lp_snippets", "Representative LP rows and columns for the copied active LP."),
        ("runtime_snapshot_path", "runtime_snapshot", "runtime_snapshot", "Runtime snapshot for the active packaged LP problem."),
    ]
    for key, name, kind, description in mapping:
        raw_path = artifact_paths.get(key)
        if raw_path in {None, ""}:
            continue
        path = Path(str(raw_path)).expanduser().resolve()
        if not path.exists() or not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8").rstrip()
        except UnicodeDecodeError:
            continue
        if kind == "python_builder":
            content = f"```python\n{content}\n```"
        elif path.suffix == ".json":
            content = f"```json\n{content}\n```"
        payloads.append(
            SourceArtifact(
                name=name,
                kind=kind,
                path=path,
                description=description,
                content=content,
            )
        )
    return payloads


def _lp_inventory_payloads(
    lp_inventory: Any,
    lp_snippets: Any,
) -> list[SourceArtifact]:
    payloads: list[SourceArtifact] = []
    if isinstance(lp_inventory, dict) and lp_inventory:
        payloads.append(
            SourceArtifact(
                name="lp_inventory",
                kind="lp_inventory",
                path=None,
                description="Compact LP family inventory derived from the loaded model.",
                content=render_inventory_markdown(lp_inventory),
            )
        )
    if isinstance(lp_snippets, dict) and lp_snippets:
        payloads.append(
            SourceArtifact(
                name="lp_snippets",
                kind="lp_snippets",
                path=None,
                description="Representative LP rows and columns for key families.",
                content=render_lp_snippets_markdown(lp_snippets),
            )
        )
    return payloads
