"""Transportation prompt catalog."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from framework.prompting import PromptSpec, load_prompt_catalog, render_prompt_entry


CATALOG_PATH = Path(__file__).with_name("catalog.yaml")
_PROMPT_ENTRIES = load_prompt_catalog(CATALOG_PATH)

PROMPT_ORDER = list(_PROMPT_ENTRIES)


def resolve_prompt(prompt_id: str, *, params: Mapping[str, Any] | None = None) -> PromptSpec:
    try:
        entry = _PROMPT_ENTRIES[prompt_id]
    except KeyError as exc:
        raise KeyError(
            f"Unknown transport prompt '{prompt_id}'. Available: {sorted(_PROMPT_ENTRIES)}"
        ) from exc
    return render_prompt_entry(prompt_id, entry, params=params)


__all__ = ["PROMPT_ORDER", "PromptSpec", "resolve_prompt"]
