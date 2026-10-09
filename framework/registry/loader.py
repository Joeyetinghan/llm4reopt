"""Problem registry and manifest loading."""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from typing import Any

import yaml

from framework.core import ProblemAdapter, ProblemSpec


REPO_ROOT = Path(__file__).resolve().parents[2]
PROBLEMS_ROOT = REPO_ROOT / "problems"


def builtin_problem_roots() -> dict[str, Path]:
    return {
        path.name: path
        for path in PROBLEMS_ROOT.iterdir()
        if path.is_dir() and (path / "problem.yaml").exists()
    }


def resolve_problem_root(problem: str | None = None, problem_root: str | None = None) -> Path:
    if problem_root is not None:
        root = Path(problem_root).expanduser().resolve()
    elif problem is not None:
        builtins = builtin_problem_roots()
        if problem not in builtins:
            raise KeyError(f"Unknown problem '{problem}'. Available: {sorted(builtins)}")
        root = builtins[problem]
    else:
        raise ValueError("Either problem or problem_root must be provided")

    manifest_path = root / "problem.yaml"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing problem manifest: {manifest_path}")
    return root


def load_manifest(problem_root: Path) -> dict[str, Any]:
    manifest_path = problem_root / "problem.yaml"
    with manifest_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Problem manifest must be a mapping: {manifest_path}")
    return data


def load_adapter(problem_root: Path) -> ProblemAdapter:
    manifest = load_manifest(problem_root)
    adapter_path = manifest.get("adapter")
    if not adapter_path or ":" not in adapter_path:
        raise ValueError(f"Manifest missing adapter entry: {problem_root / 'problem.yaml'}")

    module_name, class_name = adapter_path.split(":", 1)
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        module = _load_local_module(problem_root, module_name, exc)
    adapter_cls = getattr(module, class_name)
    adapter = adapter_cls()
    if not isinstance(adapter, ProblemAdapter):
        raise TypeError(f"Adapter must implement ProblemAdapter: {adapter_path}")
    return adapter


def load_problem(
    *,
    problem: str | None = None,
    problem_root: str | None = None,
    config_path: str | None = None,
    config_mapping: dict[str, Any] | None = None,
    examples_path: str | None = None,
    load_runtime_data: bool = True,
) -> tuple[ProblemSpec, ProblemAdapter]:
    root = resolve_problem_root(problem=problem, problem_root=problem_root)
    adapter = load_adapter(root)
    spec = adapter.load_problem(
        str(root),
        config_path=config_path,
        config_mapping=config_mapping,
        examples_path=examples_path,
        load_runtime_data=load_runtime_data,
    )
    return spec, adapter


def _load_local_module(problem_root: Path, module_name: str, original_error: ModuleNotFoundError):
    file_candidate = problem_root.joinpath(*module_name.split(".")).with_suffix(".py")
    if file_candidate.exists():
        return _load_module_from_file(module_name, file_candidate)

    package_candidate = problem_root.joinpath(*module_name.split(".")) / "__init__.py"
    if package_candidate.exists():
        return _load_module_from_file(module_name, package_candidate)

    raise original_error


def _load_module_from_file(module_name: str, file_path: Path):
    synthetic_name = f"_reopt_problem_{abs(hash(file_path.resolve()))}_{module_name.replace('.', '_')}"
    search_locations = [str(file_path.parent)] if file_path.name == "__init__.py" else None
    spec = importlib.util.spec_from_file_location(
        synthetic_name,
        file_path,
        submodule_search_locations=search_locations,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module {module_name!r} from {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
