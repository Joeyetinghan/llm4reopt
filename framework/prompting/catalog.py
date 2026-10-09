"""YAML-backed prompt catalog helpers."""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path
from string import Formatter
from typing import Any, Mapping

import yaml


class PromptCatalogUnavailable(Exception):
    """Raised when a problem package does not expose a prompt catalog."""


@dataclass(frozen=True)
class PromptSpec:
    prompt_id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


def load_prompt_catalog(path: str | Path) -> dict[str, dict[str, Any]]:
    catalog_path = Path(path).expanduser().resolve()
    with catalog_path.open("r", encoding="utf-8") as fh:
        payload = yaml.safe_load(fh) or {}

    prompts = payload.get("prompts", payload)
    if not isinstance(prompts, Mapping):
        raise ValueError(f"Prompt catalog must contain a 'prompts' mapping: {catalog_path}")

    catalog: dict[str, dict[str, Any]] = {}
    for prompt_id, raw_entry in prompts.items():
        if isinstance(raw_entry, str):
            catalog[str(prompt_id)] = {"text": raw_entry}
            continue
        if not isinstance(raw_entry, Mapping):
            raise ValueError(f"Prompt entry must be a string or mapping: {catalog_path}::{prompt_id}")
        catalog[str(prompt_id)] = dict(raw_entry)
    return catalog


def render_prompt_entry(
    prompt_id: str,
    entry: Mapping[str, Any],
    *,
    params: Mapping[str, Any] | None = None,
) -> PromptSpec:
    template = str(entry.get("text") or entry.get("template") or "").strip()
    if not template:
        raise KeyError(f"Prompt '{prompt_id}' has no text/template field.")

    merged_params = dict(entry.get("defaults", {}))
    if params:
        merged_params.update(dict(params))

    required_params = [str(name) for name in entry.get("required_params", [])]
    missing_required = [name for name in required_params if name not in merged_params]
    if missing_required:
        raise KeyError(
            f"Prompt '{prompt_id}' is missing required params: {', '.join(sorted(missing_required))}"
        )

    placeholder_names = _placeholder_names(template)
    missing_placeholders = [name for name in placeholder_names if name not in merged_params]
    if missing_placeholders:
        raise KeyError(
            f"Prompt '{prompt_id}' is missing template params: {', '.join(sorted(missing_placeholders))}"
        )

    text = template.format_map(merged_params) if placeholder_names else template
    metadata = dict(entry.get("metadata", {}))
    if merged_params:
        metadata["params"] = dict(merged_params)
    return PromptSpec(prompt_id=prompt_id, text=text.strip(), metadata=metadata)


def resolve_problem_prompt(
    problem_id: str,
    prompt_id: str,
    *,
    params: Mapping[str, Any] | None = None,
) -> PromptSpec:
    try:
        module = importlib.import_module(f"problems.{problem_id}.prompts")
    except ModuleNotFoundError as exc:
        raise PromptCatalogUnavailable(f"No prompt catalog module found for '{problem_id}'.") from exc

    resolver = getattr(module, "resolve_prompt", None)
    if resolver is None:
        raise PromptCatalogUnavailable(f"Problem '{problem_id}' does not expose resolve_prompt().")
    return resolver(prompt_id, params=params)


def resolve_problem_prompt_params(
    problem_id: str,
    prompt_id: str,
    *,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        module = importlib.import_module(f"problems.{problem_id}.prompts")
    except ModuleNotFoundError:
        return {}

    resolver = getattr(module, "default_prompt_params", None)
    if resolver is None:
        return {}
    resolved = resolver(prompt_id, context=context)
    return dict(resolved or {})


def _placeholder_names(template: str) -> list[str]:
    names: list[str] = []
    for _, field_name, _, _ in Formatter().parse(template):
        if field_name:
            names.append(field_name)
    return names
