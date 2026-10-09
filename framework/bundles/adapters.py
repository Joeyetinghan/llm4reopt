"""Runtime bundle adapters for Python-builder and raw-LP inputs."""

from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from framework.agents import (
    PythonCodeEditPlannerAgent,
    ReoptPatchPlannerAgent,
    SplitReoptPatchPlannerAgent,
)
from framework.bundles.artifacts import load_standard_base_artifacts, load_tuned_prm
from framework.core import (
    ClassifierSchema,
    DeltaRequest,
    EditArtifact,
    ModelRepresentation,
    Patch,
    PatchOp,
    ProblemAdapter,
    ProblemMetadata,
    ProblemRuntime,
    ProblemSpec,
    ReoptResult,
    SolveStrategy,
)
from framework.core.planner_modes import (
    CODEEDIT_MODE,
    PATCHEDIT_MODE,
    PATCHEDIT_TWO_STAGE_MODE,
    normalize_planner_mode,
)
from framework.core.schemas import ContextBundle, ContextFile, DataArtifact, DataBundle, EvaluationSummary
from framework.core.solver_utils import solve_with_metadata
from framework.editing import BestImprovementValidatorSolver
from framework.evaluation import build_evaluation_summary
from framework.execution.reporter import FileRunReporter
from framework.grounding import DefaultLPModelGrounder, DefaultPythonModelGrounder
from framework.llm import create_llm_client
from framework.lp import GenericLPEnv, LP_PATTERN_OPS, LPPatternValidatorSolver, render_inventory_markdown
from framework.prompting import build_prompt_context, load_examples_context, read_context_file


DEFAULT_STRUCTURED_PATCH_OPS = [
    PatchOp.UPDATE_PARAMETER,
    PatchOp.UPDATE_BOUND,
    PatchOp.UPDATE_CONSTRAINT_RHS,
    PatchOp.UPDATE_OBJECTIVE_COEFF,
    PatchOp.UPDATE_OBJECTIVE_WEIGHT,
]


@dataclass(frozen=True)
class BundleDescriptor:
    root: Path
    original_path: Path
    bundle_kind: str
    model_path: Path
    context_path: Path
    data_dir: Path | None
    artifacts_dir: Path | None


class PythonBundleAdapter(ProblemAdapter):
    def __init__(self, descriptor: BundleDescriptor):
        self.descriptor = descriptor
        self._module = _load_module_from_path(descriptor.model_path)

    def load_problem(
        self,
        package_root: str,
        config_path: str | None = None,
        config_mapping: dict[str, Any] | None = None,
        examples_path: str | None = None,
        load_runtime_data: bool = True,
    ) -> ProblemSpec:
        del package_root, config_path, config_mapping, load_runtime_data

        problem_context = read_context_file(
            self.descriptor.context_path,
            name="problem_context",
            description="User-provided bundle context",
        )
        example_records, example_store_path = load_examples_context(examples_path)
        data_artifacts = _bundle_data_artifacts(self.descriptor)
        data_context = ContextFile(
            name="data_context",
            path=None,
            content=_bundle_inventory_text(self.descriptor, data_artifacts),
            description="Detected runtime bundle inventory",
        )
        capabilities = _python_bundle_capabilities(self._module, self.descriptor)
        metadata = ProblemMetadata(
            problem_id=self.descriptor.root.name,
            name=self.descriptor.root.name.replace("_", " ").title(),
            description=f"Runtime Python bundle loaded from {self.descriptor.original_path}",
            package_root=self.descriptor.root,
        )
        return ProblemSpec(
            metadata=metadata,
            manifest_path=None,
            adapter_path=f"{__name__}:PythonBundleAdapter",
            patchedit_artifacts=[
                EditArtifact(
                    name="model",
                    source_type="python_builder",
                    path=self.descriptor.model_path,
                    description="Runtime bundle Python builder entrypoint.",
                )
            ],
            data_bundle=DataBundle(artifacts=data_artifacts),
            context_bundle=ContextBundle(
                problem_context=problem_context,
                data_context=data_context,
                examples_context=example_records,
                example_store_path=example_store_path,
            ),
            config_metadata={
                "bundle_root": str(self.descriptor.root),
                "bundle_original_path": str(self.descriptor.original_path),
                "bundle_kind": self.descriptor.bundle_kind,
            },
            capabilities=capabilities,
            source_kind="bundle",
            codeedit_artifacts=[
                EditArtifact(
                    name="model",
                    source_type="python_builder",
                    path=self.descriptor.model_path,
                    description="Runtime bundle Python builder entrypoint.",
                )
            ],
        )

    def build_structured_model(self, spec: ProblemSpec):
        return self.build_env(spec).build_structured_model()

    def build_prompt_context(self, spec: ProblemSpec, model_or_representation, delta_request: DeltaRequest):
        representation = (
            model_or_representation
            if isinstance(model_or_representation, ModelRepresentation)
            else DefaultPythonModelGrounder(self.supported_patch_ops()).ground(
                spec,
                self.build_env(spec),
                model_or_representation,
                delta_request=delta_request,
            )
        )
        return build_prompt_context(
            spec,
            representation,
            delta_request,
            supported_patch_ops=[patch_op.value for patch_op in self.supported_patch_ops()],
        )

    def normalize_patches(self, spec: ProblemSpec, event, patches: list[Patch], model=None) -> list[Patch]:
        del spec, event, model
        return list(patches)

    def solve(
        self,
        spec: ProblemSpec,
        model,
        strategy: SolveStrategy,
        warm_start: Any | None = None,
    ) -> ReoptResult:
        candidate_model = model.copy() if hasattr(model, "copy") else model
        if warm_start is not None and strategy in {SolveStrategy.WARM, SolveStrategy.WARM_TUNED}:
            candidate_model.parameters["warm_start"] = warm_start
        solve_context = self.solve_context(spec, strategy)
        for key, value in solve_context.items():
            candidate_model.parameters[key] = value
        objective, solution, solve_meta = solve_with_metadata(self.build_env(spec), candidate_model)
        representation = DefaultPythonModelGrounder(self.supported_patch_ops()).ground(
            spec,
            self.build_env(spec),
            candidate_model,
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
            change_report="Direct solve",
            model_summary=candidate_model.describe() if hasattr(candidate_model, "describe") else {},
        )
        result.evaluation = self.evaluate(spec, result)
        return result

    def evaluate(self, spec: ProblemSpec, reopt_result: ReoptResult) -> EvaluationSummary:
        return build_evaluation_summary(
            objective=reopt_result.objective,
            solve_meta=reopt_result.solve_meta,
            details={
                "problem_id": spec.metadata.problem_id,
                "source_kind": spec.source_kind,
                "strategy": reopt_result.strategy.value,
            },
        )

    def extract_warm_start(self, reopt_result: ReoptResult) -> Any | None:
        if not self._capability_value("supports_warm_start"):
            return None
        return reopt_result.solution

    def supported_patch_ops(self) -> list[PatchOp]:
        hook = getattr(self._module, "supported_patch_ops", None)
        if callable(hook):
            raw_ops = hook()
        else:
            raw_ops = DEFAULT_STRUCTURED_PATCH_OPS
        return [_coerce_patch_op(item) for item in raw_ops]

    def build_env(self, spec: ProblemSpec):
        del spec
        builder = getattr(self._module, "build_model", None)
        if callable(builder):
            return builder(self.descriptor.root)
        builder = getattr(self._module, "build_env", None)
        if not callable(builder):
            raise ValueError(
                f"Python bundle is missing build_model(bundle_dir) or build_env(bundle_dir): {self.descriptor.model_path}"
            )
        return builder(self.descriptor.root)

    def classifier_schema(self, spec: ProblemSpec, prompt_context=None) -> ClassifierSchema:
        del spec, prompt_context
        return ClassifierSchema(
            guidance=(
                "This is a runtime Python bundle. Prefer affected_sets keys that match the domain entities "
                "named in the user context or model summary. relevant_components should use structured model "
                "family names exactly when possible."
            )
        )

    def build_runtime(
        self,
        spec: ProblemSpec,
        model_name: str,
        api_key: str | None,
        prompt_context,
    ) -> ProblemRuntime:
        planner_mode = _resolve_planner_mode(spec)
        llm_client = None if planner_mode == "codeedit" else create_llm_client(model_name=model_name, api_key=api_key)
        planner = _build_planner_backend(
            spec,
            llm_client,
            model_name=model_name,
            api_key=api_key,
            allowed_ops=self.supported_patch_ops(),
            guidance=self.classifier_schema(spec, prompt_context),
            prompt_context=prompt_context,
        )
        return ProblemRuntime(
            env=self.build_env(spec),
            grounder=DefaultPythonModelGrounder(self.supported_patch_ops()),
            planner=planner,
            validator=BestImprovementValidatorSolver(),
            reporter=FileRunReporter(),
        )

    def load_base_context(self, spec: ProblemSpec, model=None):
        del spec, model
        standard = load_standard_base_artifacts(self.descriptor.root)
        hook = getattr(self._module, "load_base_solution", None)
        if not callable(hook):
            return standard
        custom = hook(self.descriptor.root)
        if custom is None:
            return standard
        objective, warm_start, meta = custom
        merged_meta = dict(standard[2]) if standard is not None else {}
        merged_meta.update(dict(meta or {}))
        resolved_objective = float(objective) if objective is not None else (float(standard[0]) if standard is not None else None)
        resolved_warm_start = warm_start if warm_start is not None else (standard[1] if standard is not None else None)
        if resolved_objective is None:
            return None
        return resolved_objective, resolved_warm_start, merged_meta

    def solve_context(self, spec: ProblemSpec, strategy: SolveStrategy) -> dict[str, Any]:
        del spec
        if strategy not in {SolveStrategy.TUNED, SolveStrategy.WARM_TUNED}:
            return {}
        solver_params = load_tuned_prm(self.descriptor.root)
        return {"solver_params": solver_params} if solver_params else {}

    def _capability_value(self, name: str) -> Any:
        hook = getattr(self._module, "capabilities", None)
        if callable(hook):
            values = hook() or {}
            if isinstance(values, dict):
                return values.get(name)
        return None


class LPBundleAdapter(ProblemAdapter):
    def __init__(self, descriptor: BundleDescriptor):
        self.descriptor = descriptor

    def load_problem(
        self,
        package_root: str,
        config_path: str | None = None,
        config_mapping: dict[str, Any] | None = None,
        examples_path: str | None = None,
        load_runtime_data: bool = True,
    ) -> ProblemSpec:
        del package_root, config_path, config_mapping

        problem_context = read_context_file(
            self.descriptor.context_path,
            name="problem_context",
            description="User-provided bundle context",
        )
        data_artifacts = _bundle_data_artifacts(self.descriptor)
        data_context = ContextFile(
            name="data_context",
            path=None,
            content=_bundle_inventory_text(self.descriptor, data_artifacts),
            description="Detected runtime bundle inventory",
        )
        extras: list[ContextFile] = []
        if load_runtime_data:
            try:
                inventory = self.build_env(None).inventory()
            except Exception:
                inventory = None
            if isinstance(inventory, dict):
                extras.append(
                    ContextFile(
                        name="lp_inventory",
                        path=None,
                        content=render_inventory_markdown(inventory),
                        description="Auto-generated LP inventory summary",
                    )
                )
        example_records, example_store_path = load_examples_context(examples_path)
        metadata = ProblemMetadata(
            problem_id=self.descriptor.root.name,
            name=self.descriptor.root.name.replace("_", " ").title(),
            description=f"Runtime LP bundle loaded from {self.descriptor.original_path}",
            package_root=self.descriptor.root,
        )
        return ProblemSpec(
            metadata=metadata,
            manifest_path=None,
            adapter_path=f"{__name__}:LPBundleAdapter",
            patchedit_artifacts=[
                EditArtifact(
                    name="model",
                    source_type="lp_file",
                    path=self.descriptor.model_path,
                    description="Runtime bundle LP artifact.",
                )
            ],
            data_bundle=DataBundle(artifacts=data_artifacts),
            context_bundle=ContextBundle(
                problem_context=problem_context,
                data_context=data_context,
                extras=extras,
                examples_context=example_records,
                example_store_path=example_store_path,
            ),
            config_metadata={
                "bundle_root": str(self.descriptor.root),
                "bundle_original_path": str(self.descriptor.original_path),
                "bundle_kind": self.descriptor.bundle_kind,
                "loaded_data": {
                    "lp_path": str(self.descriptor.model_path),
                },
            },
            capabilities={
                "supports_warm_start": False,
                "supports_tuned_solver": load_tuned_prm(self.descriptor.root) is not None,
                "source_kind": "bundle",
                "bundle_mode": "raw_lp",
            },
            source_kind="bundle",
            codeedit_artifacts=[],
        )

    def build_structured_model(self, spec: ProblemSpec):
        return self.build_env(spec).build_structured_model()

    def build_prompt_context(self, spec: ProblemSpec, model_or_representation, delta_request: DeltaRequest):
        representation = (
            model_or_representation
            if isinstance(model_or_representation, ModelRepresentation)
            else DefaultLPModelGrounder(self.supported_patch_ops()).ground(
                spec,
                self.build_env(spec),
                model_or_representation,
                delta_request=delta_request,
            )
        )
        return build_prompt_context(
            spec,
            representation,
            delta_request,
            supported_patch_ops=[patch_op.value for patch_op in self.supported_patch_ops()],
        )

    def normalize_patches(self, spec: ProblemSpec, event, patches: list[Patch], model=None) -> list[Patch]:
        del spec, event, model
        allowed = set(self.supported_patch_ops())
        return [patch for patch in patches if patch.op in allowed]

    def solve(
        self,
        spec: ProblemSpec,
        model,
        strategy: SolveStrategy,
        warm_start: Any | None = None,
    ) -> ReoptResult:
        del warm_start
        candidate_model = model.copy() if hasattr(model, "copy") else model
        for key, value in self.solve_context(spec, strategy).items():
            candidate_model.parameters[key] = value
        objective, solution, solve_meta = solve_with_metadata(self.build_env(spec), candidate_model)
        representation = DefaultLPModelGrounder(self.supported_patch_ops()).ground(
            spec,
            self.build_env(spec),
            candidate_model,
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
            change_report="Direct solve",
            model_summary=candidate_model.describe() if hasattr(candidate_model, "describe") else {},
        )
        result.evaluation = self.evaluate(spec, result)
        return result

    def evaluate(self, spec: ProblemSpec, reopt_result: ReoptResult) -> EvaluationSummary:
        return build_evaluation_summary(
            objective=reopt_result.objective,
            solve_meta=reopt_result.solve_meta,
            details={
                "problem_id": spec.metadata.problem_id,
                "source_kind": spec.source_kind,
                "strategy": reopt_result.strategy.value,
            },
        )

    def extract_warm_start(self, reopt_result: ReoptResult) -> Any | None:
        del reopt_result
        return None

    def supported_patch_ops(self) -> list[PatchOp]:
        return [_coerce_patch_op(name) for name in LP_PATTERN_OPS]

    def build_env(self, spec: ProblemSpec | None):
        del spec
        return GenericLPEnv(
            lp_path=self.descriptor.model_path,
            name="runtime_lp_bundle",
            problem_id=self.descriptor.root.name,
        )

    def classifier_schema(self, spec: ProblemSpec, prompt_context=None) -> ClassifierSchema:
        del spec, prompt_context
        return ClassifierSchema(
            guidance=(
                "This is a raw LP runtime bundle. Prefer affected_sets keys for explicit identifiers named in "
                "the delta or context, such as item, unit, date, period, constraint_family, or "
                "variable_family. relevant_components should stay close to LP family names from the model "
                "summary or LP inventory and should not invent ungrounded semantic parameter names."
            )
        )

    def build_runtime(
        self,
        spec: ProblemSpec,
        model_name: str,
        api_key: str | None,
        prompt_context,
    ) -> ProblemRuntime:
        planner_mode = _resolve_planner_mode(spec)
        llm_client = None if planner_mode == "codeedit" else create_llm_client(model_name=model_name, api_key=api_key)
        planner = _build_planner_backend(
            spec,
            llm_client,
            model_name=model_name,
            api_key=api_key,
            allowed_ops=self.supported_patch_ops(),
            guidance=self.classifier_schema(spec, prompt_context),
            prompt_context=prompt_context,
        )
        return ProblemRuntime(
            env=self.build_env(spec),
            grounder=DefaultLPModelGrounder(self.supported_patch_ops()),
            planner=planner,
            validator=LPPatternValidatorSolver(),
            reporter=FileRunReporter(),
        )

    def load_base_context(self, spec: ProblemSpec, model=None):
        del spec, model
        return load_standard_base_artifacts(self.descriptor.root)

    def solve_context(self, spec: ProblemSpec, strategy: SolveStrategy) -> dict[str, Any]:
        del spec
        if strategy not in {SolveStrategy.TUNED, SolveStrategy.WARM_TUNED}:
            return {}
        solver_params = load_tuned_prm(self.descriptor.root)
        return {"solver_params": solver_params} if solver_params else {}


def _load_module_from_path(path: Path) -> ModuleType:
    module_name = f"_reopt_bundle_{abs(hash(path.resolve()))}_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load runtime bundle module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bundle_data_artifacts(descriptor: BundleDescriptor) -> list[DataArtifact]:
    artifacts: list[DataArtifact] = []
    for kind, directory in (("data_file", descriptor.data_dir), ("bundle_artifact", descriptor.artifacts_dir)):
        if directory is None or not directory.exists():
            continue
        for path in sorted(item for item in directory.rglob("*") if item.is_file()):
            relative = path.relative_to(descriptor.root)
            artifacts.append(
                DataArtifact(
                    name=str(relative),
                    kind=kind,
                    path=path,
                    description=f"Runtime bundle {kind.replace('_', ' ')}",
                )
            )
    return artifacts


def _bundle_inventory_text(descriptor: BundleDescriptor, data_artifacts: list[DataArtifact]) -> str:
    payload = {
        "bundle_kind": descriptor.bundle_kind,
        "bundle_root": str(descriptor.root),
        "model_artifact": str(descriptor.model_path.relative_to(descriptor.root)),
        "context_file": str(descriptor.context_path.relative_to(descriptor.root)),
        "data_files": [
            {
                "name": artifact.name,
                "kind": artifact.kind,
                "path": str(artifact.path.relative_to(descriptor.root)),
            }
            for artifact in data_artifacts
        ],
    }
    return "Runtime bundle inventory:\n" + json.dumps(payload, indent=2, sort_keys=True)


def _python_bundle_capabilities(module: ModuleType, descriptor: BundleDescriptor) -> dict[str, Any]:
    values: dict[str, Any] = {}
    hook = getattr(module, "capabilities", None)
    if callable(hook):
        returned = hook() or {}
        if isinstance(returned, dict):
            values.update(returned)
    if "supports_warm_start" not in values:
        values["supports_warm_start"] = (
            (descriptor.artifacts_dir / "base.sol").exists() if descriptor.artifacts_dir else False
        ) or callable(getattr(module, "load_base_solution", None))
    if "supports_tuned_solver" not in values:
        values["supports_tuned_solver"] = (
            (descriptor.artifacts_dir / "tuned.prm").exists() if descriptor.artifacts_dir else False
        )
    values.setdefault("source_kind", "bundle")
    values.setdefault("bundle_mode", "python_builder")
    return values


def _coerce_patch_op(value: PatchOp | str) -> PatchOp:
    if isinstance(value, PatchOp):
        return value
    return PatchOp(str(value))


def _build_planner_backend(
    spec: ProblemSpec,
    llm_client,
    *,
    model_name: str,
    api_key: str | None,
    allowed_ops: list[PatchOp],
    guidance: ClassifierSchema,
    prompt_context,
):
    mode = _resolve_planner_mode(spec)
    selected_examples = list(prompt_context.selected_examples) if prompt_context is not None else []
    if mode == CODEEDIT_MODE:
        return PythonCodeEditPlannerAgent(
            problem_spec=spec,
            model_name=model_name,
            api_key=api_key,
            prompt_context=prompt_context,
        )
    if mode == PATCHEDIT_TWO_STAGE_MODE:
        return SplitReoptPatchPlannerAgent(
            llm_client,
            allowed_ops=allowed_ops,
            guidance=guidance,
            prompt_context=prompt_context,
            selected_examples=selected_examples,
        )
    return ReoptPatchPlannerAgent(
        llm_client,
        allowed_ops=allowed_ops,
        guidance=guidance,
        selected_examples=selected_examples,
    )


def _resolve_planner_mode(spec: ProblemSpec) -> str:
    config = spec.config_metadata.get("config") or {}
    return normalize_planner_mode(
        spec.config_metadata.get("planner_mode")
        or config.get("planner_mode")
        or spec.capabilities.get("planner_mode")
        or PATCHEDIT_MODE
    )
