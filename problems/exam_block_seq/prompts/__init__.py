"""Exam block sequencing prompt catalog."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from framework.prompting import PromptSpec, load_prompt_catalog, render_prompt_entry
from problems.exam_block_seq.prompt_params import exam_prompt_params


CATALOG_PATH = Path(__file__).with_name("catalog.yaml")
_PROMPT_ENTRIES = load_prompt_catalog(CATALOG_PATH)

PROMPT_ORDER = list(_PROMPT_ENTRIES)


def _default_prompt_specs() -> dict[str, PromptSpec]:
    prompts: dict[str, PromptSpec] = {}
    for prompt_id, entry in _PROMPT_ENTRIES.items():
        prompts[prompt_id] = render_prompt_entry(
            prompt_id,
            entry,
            params=exam_prompt_params(prompt_id),
        )
    return prompts


PROMPTS = _default_prompt_specs()


def resolve_prompt(prompt_id: str, *, params: Mapping[str, Any] | None = None) -> PromptSpec:
    try:
        entry = _PROMPT_ENTRIES[prompt_id]
    except KeyError as exc:
        raise KeyError(
            f"Unknown exam prompt '{prompt_id}'. Available: {sorted(_PROMPT_ENTRIES)}"
        ) from exc
    return render_prompt_entry(prompt_id, entry, params=params)


def default_prompt_params(prompt_id: str, *, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return exam_prompt_params(prompt_id, context=context)


__all__ = ["PROMPTS", "PROMPT_ORDER", "PromptSpec", "default_prompt_params", "resolve_prompt"]
