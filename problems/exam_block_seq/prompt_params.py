"""Instance-specific prompt parameters for exam block sequencing."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping


DEFAULT_P3_SLOT_CUTOFF_EXCLUSIVE = 15


def exam_prompt_params(prompt_id: str, *, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if str(prompt_id).strip().upper() != "P3":
        return {}
    data = _coerce_prompt_context(context)
    blocks = [int(slot) for slot in data.get("blocks", [])]
    large_blocks = [int(block) for block in data.get("large_blocks", [])]
    cutoff = p3_slot_cutoff_exclusive(blocks, large_blocks)
    return {
        "slot_cutoff_exclusive": cutoff,
        "slot_cutoff_ordinal": _ordinal(cutoff),
        "early_slot_count": max(cutoff - 1, 0),
    }


def p3_slot_cutoff_exclusive(
    blocks: Iterable[int],
    large_blocks: Iterable[int],
    *,
    minimum_cutoff_exclusive: int = DEFAULT_P3_SLOT_CUTOFF_EXCLUSIVE,
) -> int:
    slot_ids = sorted(int(slot) for slot in blocks)
    if not slot_ids:
        return int(minimum_cutoff_exclusive)
    required_cutoff = len([int(block) for block in large_blocks]) + 1
    cutoff = max(int(minimum_cutoff_exclusive), required_cutoff)
    return min(cutoff, max(slot_ids) + 1)


def _coerce_prompt_context(context: Mapping[str, Any] | None) -> dict[str, Any]:
    payload = dict(context or {})
    if payload.get("blocks") is not None and payload.get("large_blocks") is not None:
        return payload

    instance_dir = payload.get("instance_dir")
    lp_path = payload.get("lp_path")
    if instance_dir is None and lp_path is None:
        return payload

    from problems.exam_block_seq.dataloader import load_block_seq_data_from_mapping

    loader_mapping: dict[str, Any] = {}
    if instance_dir is not None:
        loader_mapping["instance_dir"] = str(instance_dir)
    if lp_path is not None:
        loader_mapping["lp_path"] = str(Path(lp_path))
    loaded = load_block_seq_data_from_mapping(loader_mapping)
    payload.setdefault("blocks", loaded.get("blocks", []))
    payload.setdefault("large_blocks", loaded.get("large_blocks", []))
    return payload


def _ordinal(value: int) -> str:
    n = int(value)
    suffix = "th"
    if n % 100 not in {11, 12, 13}:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"
