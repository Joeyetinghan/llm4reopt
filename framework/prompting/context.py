"""Prompt-context assembly and example store helpers."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from framework.core.schemas import (
    ContextFile,
    DeltaRequest,
    ModelRepresentation,
    PatchSurfaceDescriptor,
    ProblemSpec,
    PromptContext,
)


def read_context_file(path: Path | None, *, name: str, description: str = "") -> ContextFile:
    content = ""
    if path is not None and path.exists():
        content = path.read_text(encoding="utf-8")
    return ContextFile(name=name, path=path, content=content, description=description)


def load_examples_context(path: str | Path | None) -> tuple[list[dict[str, Any]], Path | None]:
    if path is None:
        return [], None

    examples_path = Path(path).expanduser().resolve()
    if not examples_path.exists():
        return [], examples_path

    with examples_path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)

    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, Mapping)], examples_path

    if isinstance(payload, Mapping):
        episodes = payload.get("episodes")
        if isinstance(episodes, list):
            return [dict(item) for item in episodes if isinstance(item, Mapping)], examples_path

    raise ValueError(f"Unsupported examples payload: {examples_path}")


def select_examples(
    examples: Iterable[Mapping[str, Any]],
    *,
    problem_id: str,
    aliases: Iterable[str] = (),
    limit: int = 3,
) -> list[dict[str, Any]]:
    valid_keys = {problem_id, *aliases}
    filtered: list[dict[str, Any]] = []
    for example in examples:
        example_problem = example.get("problem_id")
        example_env = example.get("env_name")
        if example_problem in valid_keys or example_env in valid_keys or (
            example_problem is None and example_env is None
        ):
            filtered.append(dict(example))
    return filtered[-limit:]


def build_prompt_context(
    spec: ProblemSpec,
    model_or_representation,
    delta_request: DeltaRequest,
    *,
    supported_patch_ops: list[str],
) -> PromptContext:
    aliases = spec.capabilities.get("example_aliases", [])
    selected_examples = select_examples(
        spec.context_bundle.examples_context,
        problem_id=spec.metadata.problem_id,
        aliases=aliases,
        limit=int(spec.capabilities.get("max_prompt_examples", 3)),
    )
    if isinstance(model_or_representation, ModelRepresentation):
        representation = model_or_representation
    else:
        model_summary = (
            model_or_representation.describe()
            if hasattr(model_or_representation, "describe")
            else {}
        )
        representation = ModelRepresentation(
            problem=spec.metadata,
            representation_kind="legacy_structured_model",
            component_descriptors=[],
            patch_surface=PatchSurfaceDescriptor(supported_ops=list(supported_patch_ops)),
            solver_capabilities={"source_kind": spec.source_kind, **dict(spec.capabilities)},
            artifact_inventory={},
            context_payload="\n\n".join(
                document.content
                for document in spec.context_bundle.documents()
                if document.content
            ),
            source_payloads=[],
            model_summary=model_summary,
        )
    return PromptContext(
        problem=spec.metadata,
        delta_request=delta_request,
        model_representation=representation,
        supported_patch_ops=supported_patch_ops,
        selected_examples=selected_examples,
    )


def extract_python_symbol(source_text: str, symbol: str) -> str | None:
    try:
        module = ast.parse(source_text)
    except SyntaxError:
        return None

    lines = source_text.splitlines()
    for node in module.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if getattr(node, "name", None) != symbol:
            continue
        start_line = node.lineno
        decorators = getattr(node, "decorator_list", [])
        if decorators:
            start_line = min(start_line, *(decorator.lineno for decorator in decorators))
        end_line = getattr(node, "end_lineno", None)
        if end_line is None:
            return None
        return "\n".join(lines[start_line - 1:end_line])
    return None


def append_episode(context_bundle: ContextBundle, episode: Mapping[str, Any]) -> None:
    target = context_bundle.example_store_path
    if target is None:
        return
    append_episode_to_path(target, episode)


def append_episode_to_path(path: str | Path, episode: Mapping[str, Any]) -> None:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    existing: list[dict[str, Any]] = []
    if target.exists():
        with target.open("r", encoding="utf-8") as fh:
            try:
                payload = json.load(fh)
            except json.JSONDecodeError:
                payload = []
        if isinstance(payload, list):
            existing = [dict(item) for item in payload if isinstance(item, Mapping)]

    existing.append(_safe_json(dict(episode)))
    with target.open("w", encoding="utf-8") as fh:
        json.dump(existing, fh, indent=2)


def _safe_json(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        if isinstance(value, Mapping):
            return {str(key): _safe_json(val) for key, val in value.items()}
        if isinstance(value, (list, tuple)):
            return [_safe_json(item) for item in value]
        return repr(value)
