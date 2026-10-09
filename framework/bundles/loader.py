"""Runtime bundle loading and validation."""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path

from framework.core import ProblemAdapter, ProblemSpec

from .adapters import BundleDescriptor, LPBundleAdapter, PythonBundleAdapter


def load_bundle(
    *,
    bundle_path: str,
    examples_path: str | None = None,
    load_runtime_data: bool = True,
) -> tuple[ProblemSpec, ProblemAdapter]:
    descriptor = resolve_bundle(bundle_path)
    if descriptor.bundle_kind == "python":
        adapter: ProblemAdapter = PythonBundleAdapter(descriptor)
    else:
        adapter = LPBundleAdapter(descriptor)
    spec = adapter.load_problem(
        str(descriptor.root),
        examples_path=examples_path,
        load_runtime_data=load_runtime_data,
    )
    return spec, adapter


def resolve_bundle(bundle_path: str | Path) -> BundleDescriptor:
    original = Path(bundle_path).expanduser().resolve()
    root = _materialize_bundle_root(original)
    context_path = root / "context.md"
    python_model = root / "model.py"
    lp_model = root / "model.lp"

    if not context_path.exists():
        raise FileNotFoundError(f"Runtime bundle is missing required context.md: {root}")
    if python_model.exists() and lp_model.exists():
        raise ValueError(f"Runtime bundle must contain exactly one of model.py or model.lp: {root}")
    if not python_model.exists() and not lp_model.exists():
        raise FileNotFoundError(f"Runtime bundle must contain model.py or model.lp: {root}")

    model_path = python_model if python_model.exists() else lp_model
    bundle_kind = "python" if python_model.exists() else "lp"
    data_dir = root / "data"
    artifacts_dir = root / "artifacts"
    return BundleDescriptor(
        root=root,
        original_path=original,
        bundle_kind=bundle_kind,
        model_path=model_path,
        context_path=context_path,
        data_dir=data_dir if data_dir.exists() else None,
        artifacts_dir=artifacts_dir if artifacts_dir.exists() else None,
    )


def _materialize_bundle_root(path: Path) -> Path:
    if path.is_dir():
        return path
    if path.suffix.lower() != ".zip":
        raise ValueError(f"Unsupported bundle path (expected directory or .zip): {path}")

    extract_root = Path(tempfile.mkdtemp(prefix="reopt_bundle_"))
    with zipfile.ZipFile(path, "r") as archive:
        archive.extractall(extract_root)
    return _normalize_extracted_root(extract_root)


def _normalize_extracted_root(root: Path) -> Path:
    children = [child for child in root.iterdir() if child.name != "__MACOSX"]
    if len(children) == 1 and children[0].is_dir():
        candidate = children[0]
        if (candidate / "context.md").exists():
            return candidate
    return root
