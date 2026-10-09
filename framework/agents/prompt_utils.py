"""Small shared helpers for agent prompt construction and parsing."""

from __future__ import annotations

import re
from typing import Mapping

from framework.core import PatchOp


_LEADING_ZERO_RE = re.compile(r"(?<=[\s,:\[{])0(\d+)(?=[\s,\]}\n])")


def render_field_block(fields: Mapping[str, str]) -> str:
    return "\n".join(f"- {name}: {description}" for name, description in fields.items())


def strip_markdown_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return text


def fix_leading_zero_numbers(text: str) -> str:
    return _LEADING_ZERO_RE.sub(r"\1", text)


def coerce_patch_op(raw: str | None) -> PatchOp:
    if not raw:
        raise RuntimeError("Patch missing 'op' field")
    raw_upper = raw.strip().upper()
    for candidate in PatchOp:
        if candidate.value == raw_upper:
            return candidate
    raise RuntimeError(f"Unsupported patch op '{raw}'")
