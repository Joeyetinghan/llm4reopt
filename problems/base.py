"""Shared helpers for packaged problem adapters."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from framework.core import (
    ClassifierSchema,
    DeltaRequest,
    EditArtifact,
    GroundTruthArtifact,
    GroundTruthBundle,
    GroundTruthCase,
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
from framework.agents import (
    LPWrapperCodeEditPlannerAgent,
    PythonCodeEditPlannerAgent,
    ReoptPatchPlannerAgent,
    SplitReoptPatchPlannerAgent,
)
from framework.core.schemas import ContextBundle, DataArtifact, DataBundle, EvaluationSummary
from framework.core.solver_utils import solve_with_metadata
from framework.evaluation import build_evaluation_summary
from framework.execution.reporter import FileRunReporter
from framework.grounding import DefaultCodeEditGrounder, DefaultLPModelGrounder, DefaultPythonModelGrounder
from framework.llm import create_llm_client
from framework.prompting import build_prompt_context, load_examples_context, read_context_file
from framework.registry import load_manifest


class BasePackagedProblemAdapter(ProblemAdapter):
    def load_problem(
        self,
        package_root: str,
        config_path: str | None = None,
        config_mapping: dict[str, Any] | None = None,
        examples_path: str | None = None,
        load_runtime_data: bool = True,
    ) -> ProblemSpec:
        root = Path(package_root).expanduser().resolve()
        manifest = load_manifest(root)

        metadata = ProblemMetadata(
            problem_id=str(manifest["id"]),
            name=str(manifest["name"]),
            description=str(manifest.get("description", "")),
            package_root=root,
        )

        if "model_artifacts" in manifest:
            raise ValueError(
                f"Manifest {root / 'problem.yaml'} still uses model_artifacts; rename it to patchedit_artifacts."
            )

        patchedit_artifacts = _load_edit_artifacts(root, manifest.get("patchedit_artifacts", []))
        codeedit_artifacts = _load_edit_artifacts(root, manifest.get("codeedit_artifacts", []))

        data_artifacts = [
            DataArtifact(
                name=str(item["name"]),
                kind=str(item["kind"]),
                path=_resolve_path(root, item["path"]),
                description=str(item.get("description", "")),
            )
            for item in manifest.get("data_artifacts", [])
        ]

        contexts = manifest.get("contexts", {})
        problem_context = read_context_file(
            _resolve_optional_path(root, contexts.get("problem_context")),
            name="problem_context",
            description="Problem formulation and modeling notes",
        )
        data_context = read_context_file(
            _resolve_optional_path(root, contexts.get("data_context")),
            name="data_context",
            description="Data schema and data-handling notes",
        )
        extras = []
        for item in contexts.get("extras", []):
            if isinstance(item, str):
                extras.append(read_context_file(_resolve_path(root, item), name=Path(item).stem))
            elif isinstance(item, dict):
                extras.append(
                    read_context_file(
                        _resolve_optional_path(root, item.get("path")),
                        name=str(item.get("name", "extra_context")),
                        description=str(item.get("description", "")),
                    )
                )

        configured_examples_path = examples_path or manifest.get("examples_context")
        example_records, example_store_path = load_examples_context(
            _resolve_optional_path(root, configured_examples_path) if configured_examples_path else None
        )

        selected_config_path = _select_config_path(root, manifest, config_path)
        config_payload = dict(config_mapping or _load_mapping(selected_config_path))
        ground_truth = _load_ground_truth_bundle(root, manifest)

        capabilities = dict(manifest.get("capabilities", {}))
        capabilities.update(self.capability_overrides())

        spec = ProblemSpec(
            metadata=metadata,
            manifest_path=root / "problem.yaml",
            adapter_path=str(manifest["adapter"]),
            patchedit_artifacts=patchedit_artifacts,
            data_bundle=DataBundle(artifacts=data_artifacts),
            context_bundle=ContextBundle(
                problem_context=problem_context,
                data_context=data_context,
                extras=extras,
                examples_context=example_records,
                example_store_path=example_store_path,
            ),
            ground_truth=ground_truth,
            config_metadata={
                "config": config_payload,
                "config_path": selected_config_path,
            },
            capabilities=capabilities,
            codeedit_artifacts=codeedit_artifacts,
        )
        return spec

    def capability_overrides(self) -> dict[str, Any]:
        return {}

    def classifier_schema(self, spec: ProblemSpec, prompt_context=None) -> ClassifierSchema:
        del spec, prompt_context
        return ClassifierSchema()

    def build_structured_model(self, spec: ProblemSpec):
        return self._build_env(spec).build_structured_model()

    def build_prompt_context(self, spec: ProblemSpec, model_or_representation, delta_request: DeltaRequest):
        representation = (
            model_or_representation
            if isinstance(model_or_representation, ModelRepresentation)
            else self._build_grounder(spec).ground(
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

    def build_runtime(
        self,
        spec: ProblemSpec,
        model_name: str,
        api_key: str | None,
        prompt_context,
    ) -> ProblemRuntime:
        planner_mode = _resolve_planner_mode(spec)
        guidance = self.classifier_schema(spec, prompt_context)
        selected_examples = list(prompt_context.selected_examples) if prompt_context is not None else []
        if planner_mode == CODEEDIT_MODE:
            primary_kind = spec.codeedit_artifacts[0].source_type if spec.codeedit_artifacts else "python_builder"
            planner_cls = (
                LPWrapperCodeEditPlannerAgent
                if primary_kind == "lp_file"
                else PythonCodeEditPlannerAgent
            )
            planner = planner_cls(
                problem_spec=spec,
                model_name=model_name,
                api_key=api_key,
                prompt_context=prompt_context,
            )
        elif planner_mode == PATCHEDIT_TWO_STAGE_MODE:
            llm_client = create_llm_client(model_name=model_name, api_key=api_key)
            planner = SplitReoptPatchPlannerAgent(
                llm_client,
                allowed_ops=self.supported_patch_ops(),
                guidance=guidance,
                prompt_context=prompt_context,
                selected_examples=selected_examples,
            )
        else:
            llm_client = create_llm_client(model_name=model_name, api_key=api_key)
            planner = ReoptPatchPlannerAgent(
                llm_client,
                allowed_ops=self.supported_patch_ops(),
                guidance=guidance,
                selected_examples=selected_examples,
            )
        return ProblemRuntime(
            env=self.build_env(spec),
            grounder=self._build_grounder(spec, planner_mode=planner_mode),
            planner=planner,
            validator=self._build_validator(spec),
            reporter=FileRunReporter(trace_root=self._trace_root(spec)),
        )

    def solve(
        self,
        spec: ProblemSpec,
        model,
        strategy: SolveStrategy,
        warm_start: Any | None = None,
    ) -> ReoptResult:
        env = self._build_env(spec)
        candidate_model = model.copy() if hasattr(model, "copy") else model
        if warm_start is not None and strategy in {SolveStrategy.WARM, SolveStrategy.WARM_TUNED}:
            candidate_model.parameters["warm_start"] = warm_start
        solve_context = self.solve_context(spec, strategy)
        if isinstance(solve_context, dict):
            for key, value in solve_context.items():
                candidate_model.parameters[key] = value
        objective, solution, solve_meta = solve_with_metadata(env, candidate_model)
        representation = self._build_grounder(spec).ground(spec, env, candidate_model)
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
                "strategy": reopt_result.strategy.value,
            },
        )

    def build_env(self, spec: ProblemSpec):
        return self._build_env(spec)

    def _build_env(self, spec: ProblemSpec):
        raise NotImplementedError

    def _build_validator(self, spec: ProblemSpec):
        raise NotImplementedError

    def _build_grounder(self, spec: ProblemSpec, *, planner_mode: str | None = None):
        mode = planner_mode or _resolve_planner_mode(spec)
        if mode == CODEEDIT_MODE:
            return DefaultCodeEditGrounder(self.supported_patch_ops())
        primary_kind = (
            spec.patchedit_artifacts[0].source_type
            if spec.patchedit_artifacts
            else "python_builder"
        )
        if primary_kind in {"python_builder", "python_model"}:
            return DefaultPythonModelGrounder(self.supported_patch_ops())
        return DefaultLPModelGrounder(self.supported_patch_ops())

    def _trace_root(self, spec: ProblemSpec) -> Path | None:
        configured = spec.config_metadata.get("trace_root")
        if configured:
            return Path(str(configured)).expanduser().resolve()
        return None


def _resolve_path(root: Path, path_value: str | Path) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return (root / path).resolve()


def _resolve_optional_path(root: Path, path_value: str | Path | None) -> Path | None:
    if path_value in {None, ""}:
        return None
    return _resolve_path(root, path_value)


def _resolve_planner_mode(spec: ProblemSpec) -> str:
    config = spec.config_metadata.get("config") or {}
    return normalize_planner_mode(
        spec.config_metadata.get("planner_mode")
        or config.get("planner_mode")
        or spec.capabilities.get("planner_mode")
        or PATCHEDIT_MODE
    )


def _select_config_path(root: Path, manifest: dict[str, Any], config_path: str | None) -> Path | None:
    if config_path:
        return Path(config_path).expanduser().resolve()
    default_config = manifest.get("default_config")
    if not default_config:
        return None
    return _resolve_path(root, default_config)


def _load_edit_artifacts(root: Path, items: list[Any]) -> list[EditArtifact]:
    return [
        EditArtifact(
            name=str(item["name"]),
            source_type=str(item["source_type"]),
            path=_resolve_path(root, item["path"]),
            description=str(item.get("description", "")),
            exported_artifacts=tuple(
                _resolve_path(root, extra_path) for extra_path in item.get("exported_artifacts", [])
            ),
            read_only_artifacts=tuple(
                _resolve_path(root, extra_path) for extra_path in item.get("read_only_artifacts", [])
            ),
            baseline_solution_path=_resolve_optional_path(root, item.get("baseline_solution_path")),
            prompt_symbol=str(item["prompt_symbol"]) if item.get("prompt_symbol") else None,
        )
        for item in items
    ]


def _load_mapping(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        if path.suffix.lower() in {".yaml", ".yml"}:
            payload = yaml.safe_load(fh) or {}
        else:
            payload = json.load(fh) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Expected mapping in config file: {path}")
    return dict(payload)


def _load_ground_truth_bundle(root: Path, manifest: dict[str, Any]) -> GroundTruthBundle | None:
    ground_truth_cfg = manifest.get("ground_truth")
    if not isinstance(ground_truth_cfg, dict):
        return None

    evaluator_path = ground_truth_cfg.get("evaluator")
    if not evaluator_path:
        raise ValueError(f"Ground truth config missing evaluator: {root / 'problem.yaml'}")

    cases_path = _resolve_optional_path(root, ground_truth_cfg.get("cases_path"))
    cases_payload: dict[str, Any] = {}
    if cases_path is not None and cases_path.exists():
        with cases_path.open("r", encoding="utf-8") as fh:
            cases_payload = yaml.safe_load(fh) or {}
        if not isinstance(cases_payload, dict):
            raise ValueError(f"Ground truth cases file must be a mapping: {cases_path}")

    cases: list[GroundTruthCase] = []
    for item in cases_payload.get("cases", []):
        if not isinstance(item, dict):
            raise ValueError(f"Ground truth case must be a mapping: {item!r}")
        cases.append(
            GroundTruthCase(
                case_id=str(item["case_id"]),
                description=str(item.get("description", "")),
                delta_text=str(item.get("delta_text", "")),
                expected_patch_ops=[str(op) for op in item.get("expected_patch_ops", [])],
                checker=str(item["checker"]) if item.get("checker") else None,
                reference_artifact=str(item["reference_artifact"]) if item.get("reference_artifact") else None,
                active=bool(item.get("active", True)),
                metadata=dict(item.get("metadata", {})),
            )
        )

    artifacts = [
        GroundTruthArtifact(
            name=str(item["name"]),
            artifact_type=str(item["type"]),
            path=_resolve_path(root, item["path"]),
            description=str(item.get("description", "")),
        )
        for item in ground_truth_cfg.get("artifacts", [])
    ]

    metadata = dict(ground_truth_cfg.get("metadata", {}))
    metadata.update(dict(cases_payload.get("metadata", {})))
    return GroundTruthBundle(
        evaluator_path=str(evaluator_path),
        cases_path=cases_path,
        cases=cases,
        artifacts=artifacts,
        status=str(cases_payload.get("status", ground_truth_cfg.get("status", "active"))),
        metadata=metadata,
    )
