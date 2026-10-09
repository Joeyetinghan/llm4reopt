"""Shared planner-mode naming and normalization helpers."""

from __future__ import annotations

import re


PATCHEDIT_MODE = "patchedit"
PATCHEDIT_TWO_STAGE_MODE = "patchedit-two-stage"
CODEEDIT_MODE = "codeedit"

_PATCHEDIT_ALIASES = {
    "integrated",
    "single",
    "single-agent",
    "one-agent",
    "reopt-patch",
    "patchedit",
    "patch-edit",
}

_PATCHEDIT_TWO_STAGE_ALIASES = {
    "split",
    "two-stage",
    "two-agent",
    "legacy",
    "patchedit-two-stage",
    "patchedit two-stage",
    "patchedit (two-stage)",
    "patch-edit-two-stage",
    "patch-edit two-stage",
    "patch-edit (two-stage)",
}

_CODEEDIT_ALIASES = {
    "codeedit",
    "code-edit",
    "aider",
}

PLANNER_MODE_CHOICES = (
    PATCHEDIT_MODE,
    PATCHEDIT_TWO_STAGE_MODE,
    CODEEDIT_MODE,
)


def normalize_planner_mode(raw_mode: str | None, *, default: str = PATCHEDIT_MODE) -> str:
    """Return the canonical planner-mode string."""
    if raw_mode in {None, ""}:
        return default

    mode = str(raw_mode).strip().lower().replace("_", "-")
    mode = re.sub(r"\s+", " ", mode)

    if mode in _PATCHEDIT_ALIASES:
        return PATCHEDIT_MODE
    if mode in _PATCHEDIT_TWO_STAGE_ALIASES:
        return PATCHEDIT_TWO_STAGE_MODE
    if mode in _CODEEDIT_ALIASES:
        return CODEEDIT_MODE

    raise ValueError(f"Unsupported planner mode: {raw_mode!r}")
